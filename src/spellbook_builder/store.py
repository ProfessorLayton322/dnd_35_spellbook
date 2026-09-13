from __future__ import annotations

import os
import shutil
import tempfile
from copy import deepcopy
from pathlib import Path

from .arkal import collapse_spell_printings, spell_id_from_name, spell_printing_rank
from .pdfgen import RENDERER_VERSION
from .util import atomic_json_write, read_json, slugify, utc_now


class StoreError(RuntimeError):
    pass


class RuntimeStore:
    def __init__(self, root: Path):
        self.root = root
        self.indexes = root / "indexes"
        self.spellbooks_dir = root / "spellbooks"
        self.root.mkdir(parents=True, exist_ok=True)
        self.indexes.mkdir(parents=True, exist_ok=True)
        self.spellbooks_dir.mkdir(parents=True, exist_ok=True)
        self._remove_stale_atomic_files()
        if not self.state_path.exists():
            self.save_state(self.empty_state())
        self._finish_interrupted_spellbook_deletes()
        self._migrate_url_spell_ids()
        self.reconcile_spell_index()

    @property
    def state_path(self) -> Path:
        return self.root / "state.json"

    @staticmethod
    def empty_state() -> dict:
        return {
            "schema_version": 1,
            "imported_classes": {},
            "index_metadata": {"spells": None, "summons": None},
            "spellbooks": {},
            "updated_at": utc_now(),
        }

    def load_state(self) -> dict:
        return read_json(self.state_path, self.empty_state())

    def save_state(self, state: dict) -> None:
        state["updated_at"] = utc_now()
        atomic_json_write(self.state_path, state)

    def load_spells(self) -> dict[str, dict]:
        return read_json(self.indexes / "spells.json", {"schema_version": 1, "records": {}})["records"]

    def save_spells(self, records: dict[str, dict]) -> None:
        atomic_json_write(self.indexes / "spells.json", {"schema_version": 1, "updated_at": utc_now(), "records": records})

    def _remove_stale_atomic_files(self) -> None:
        """Remove temporary writes left behind if an older process was killed."""

        for directory, name in ((self.root, "state.json"), (self.indexes, "spells.json")):
            for path in directory.glob(f".{name}.*.tmp"):
                if path.is_file():
                    path.unlink(missing_ok=True)

    def _finish_interrupted_spellbook_deletes(self) -> None:
        """Resolve spellbook folders left in ``.deleted-*`` by a killed process.

        A delete moves the book folder aside before removing the book from
        state. A folder whose book is still in state was never deleted and is
        moved back; any other folder belongs to a committed delete.
        """

        spellbooks = self.load_state()["spellbooks"]
        for trash in self.spellbooks_dir.glob(".deleted-*"):
            if not trash.is_dir():
                continue
            for moved in trash.iterdir():
                if moved.name in spellbooks and not self.spellbook_dir(moved.name).exists():
                    os.replace(moved, self.spellbook_dir(moved.name))
            shutil.rmtree(trash, ignore_errors=True)

    @staticmethod
    def _spellbook_spell_ids(state: dict) -> set[str]:
        referenced: set[str] = set()
        for book in state.get("spellbooks", {}).values():
            batches = list(book.get("batches", []))
            if book.get("open_batch"):
                batches.append(book["open_batch"])
            for batch in batches:
                referenced.update(
                    entity["id"]
                    for entity in batch.get("entities", [])
                    if entity.get("kind") == "spell" and entity.get("id")
                )
        return referenced

    @classmethod
    def _reconcile_spells(cls, state: dict, spells: dict[str, dict]) -> tuple[dict[str, dict], int, bool]:
        """Drop records and memberships not backed by a completed import.

        Class records in state are the commit marker for an import. Records
        without import membership are retained because they may come from a
        future or manually managed source. Spellbook references are retained so
        an open batch never loses data and historical IDs remain inspectable.
        A retained record with no valid membership keeps its stale memberships,
        so it is dropped once the last spellbook referencing it is deleted.
        """

        completed_refs: set[tuple[str, str, str]] = set()
        completed_spell_ids: set[str] = set()
        for class_name, class_record in state.get("imported_classes", {}).items():
            for level, spell_ids in class_record.get("levels", {}).items():
                for spell_id in spell_ids:
                    completed_refs.add((class_name, str(level), spell_id))
                    completed_spell_ids.add(spell_id)

        protected_ids = completed_spell_ids | cls._spellbook_spell_ids(state)
        reconciled: dict[str, dict] = {}
        removed = 0
        changed = False
        for spell_id, source_record in spells.items():
            memberships = source_record.get("imported_from_classes", [])
            valid_memberships = [
                membership
                for membership in memberships
                if (
                    membership.get("class_name"),
                    str(membership.get("spell_level")),
                    spell_id,
                )
                in completed_refs
            ]
            if memberships and not valid_memberships:
                if spell_id in protected_ids:
                    reconciled[spell_id] = source_record
                else:
                    removed += 1
                    changed = True
                continue
            if valid_memberships != memberships:
                record = deepcopy(source_record)
                record["imported_from_classes"] = valid_memberships
                reconciled[spell_id] = record
                changed = True
            else:
                reconciled[spell_id] = source_record
        return reconciled, removed, changed

    def reconcile_spell_index(self) -> int:
        """Reclaim records no completed import or spellbook still needs.

        This also repairs imports and deletes interrupted before their index
        rewrite finished. Returns the number of removed records.
        """

        spells_path = self.indexes / "spells.json"
        if not spells_path.exists():
            return 0
        state = self.load_state()
        spells = self.load_spells()
        reconciled, removed, changed = self._reconcile_spells(state, spells)
        if changed:
            self.save_spells(reconciled)
            state.setdefault("index_metadata", {})["spells"] = {
                "record_count": len(reconciled),
                "updated_at": utc_now(),
            }
            self.save_state(state)
        return removed

    @staticmethod
    def _merge_printings(existing: dict | None, incoming: dict) -> dict:
        """Combine two records of one spell, keeping the preferred printing.

        Class memberships are combined. An equally ranked incoming record wins
        so a re-import refreshes the stored copy.
        """

        if existing is None:
            return deepcopy(incoming)
        kept = existing if spell_printing_rank(existing) < spell_printing_rank(incoming) else incoming
        merged = deepcopy(kept)
        memberships = existing.get("imported_from_classes", []) + incoming.get("imported_from_classes", [])
        merged["imported_from_classes"] = list({(m["class_name"], m["spell_level"]): m for m in memberships}.values())
        return merged

    def _migrate_url_spell_ids(self) -> None:
        """Merge spells imported under per-printing URL IDs into name IDs.

        Older imports keyed each rulebook printing of a spell separately, so a
        reprinted spell was suggested and added once per printing. Class lists
        keep each spell at its kept printing's levels, as a fresh import does,
        and spellbook references move to the merged record. The old records
        stay in the index until state is saved, so a migration killed part-way
        runs again on the next startup.
        """

        spells = self.load_spells()
        renamed = {
            spell_id: spell_id_from_name(record["name"])
            for spell_id, record in spells.items()
            if spell_id.startswith("arkal-") and spell_id != spell_id_from_name(record["name"])
        }
        if not renamed:
            return
        state = self.load_state()
        for class_record in state["imported_classes"].values():
            listed = {
                level: [spells[spell_id] for spell_id in spell_ids if spell_id in spells]
                for level, spell_ids in class_record["levels"].items()
            }
            class_record["levels"] = collapse_spell_printings(listed)[0]
        for book in state["spellbooks"].values():
            open_batch = book.get("open_batch")
            for batch in [*book.get("batches", []), *([open_batch] if open_batch else [])]:
                for entity in batch.get("entities", []):
                    if entity.get("kind") == "spell" and entity.get("id") in renamed:
                        entity["id"] = renamed[entity["id"]]
            if open_batch:
                # Printings of one spell become a single open-batch item.
                open_batch["entities"] = list({(entity["kind"], entity["id"]): entity for entity in open_batch["entities"]}.values())
        merged: dict[str, dict] = {}
        for spell_id, record in spells.items():
            spell_id = renamed.get(spell_id, spell_id)
            merged[spell_id] = self._merge_printings(merged.get(spell_id), {**record, "id": spell_id})
        merged, _removed, _changed = self._reconcile_spells(state, merged)
        state.setdefault("index_metadata", {})["spells"] = {
            "record_count": len(merged),
            "updated_at": utc_now(),
        }
        self.save_spells({**spells, **merged})
        self.save_state(state)
        self.save_spells(merged)

    def merge_class_import(self, class_record: dict, new_spells: dict[str, dict]) -> None:
        previous_spells = self.load_spells()
        spells = deepcopy(previous_spells)
        for spell_id, incoming in new_spells.items():
            spells[spell_id] = self._merge_printings(spells.get(spell_id), incoming)
        state = self.load_state()
        state["imported_classes"][class_record["class_name"]] = class_record
        spells, _removed, _changed = self._reconcile_spells(state, spells)
        state.setdefault("index_metadata", {})["spells"] = {
            "record_count": len(spells),
            "updated_at": utc_now(),
        }
        try:
            self.save_spells(spells)
            self.save_state(state)
        except BaseException:
            # Keep the pre-import index if the second half of the commit fails.
            # A hard process kill is repaired by reconcile_spell_index on the
            # next startup.
            try:
                self.save_spells(previous_spells)
            except Exception:
                pass
            raise

    def delete_imported_class(self, class_name: str) -> int:
        """Delete an imported class spell list and return the removed spell count.

        A spell stays while another imported class lists it or a spellbook
        references it.
        """

        state = self.load_state()
        if class_name not in state["imported_classes"]:
            raise StoreError(f"Unknown imported class: {class_name}")
        del state["imported_classes"][class_name]
        # State is the commit marker; startup reconciliation finishes an
        # interrupted index rewrite.
        self.save_state(state)
        return self.reconcile_spell_index()

    def save_summon_indexes(self, lists: dict, monsters: dict, metadata: dict) -> None:
        atomic_json_write(self.indexes / "summon_lists.json", {"schema_version": 1, "records": lists})
        atomic_json_write(self.indexes / "monsters.json", {"schema_version": 1, "records": monsters})
        atomic_json_write(self.indexes / "summon_validation.json", metadata)
        state = self.load_state()
        state["index_metadata"]["summons"] = metadata
        self.save_state(state)

    def load_summon_lists(self) -> dict[str, dict]:
        return read_json(self.indexes / "summon_lists.json", {"records": {}})["records"]

    def load_monsters(self) -> dict[str, dict]:
        return read_json(self.indexes / "monsters.json", {"records": {}})["records"]

    def spellbook_dir(self, spellbook_id: str) -> Path:
        return self.spellbooks_dir / spellbook_id

    def create_spellbook(self, name: str) -> dict:
        name = " ".join(name.split()).strip()
        if not (1 <= len(name) <= 80):
            raise StoreError("Spellbook name must be between 1 and 80 characters")
        if any(ord(char) < 32 for char in name):
            raise StoreError("Spellbook name contains control characters")
        state = self.load_state()
        base = slugify(name)
        spellbook_id = base
        counter = 2
        while spellbook_id in state["spellbooks"]:
            spellbook_id = f"{base}-{counter}"
            counter += 1
        book = {
            "schema_version": 1,
            "id": spellbook_id,
            "name": name,
            "created_at": utc_now(),
            "renderer_version": RENDERER_VERSION,
            "batches": [],
            "open_batch": None,
            "content_page_count": 0,
            "exports": [],
        }
        state["spellbooks"][spellbook_id] = book
        self.spellbook_dir(spellbook_id).mkdir(parents=True, exist_ok=False)
        self.save_state(state)
        return deepcopy(book)

    def get_spellbook(self, spellbook_id: str) -> dict:
        state = self.load_state()
        try:
            return state["spellbooks"][spellbook_id]
        except KeyError as exc:
            raise StoreError(f"Unknown spellbook: {spellbook_id}") from exc

    def update_spellbook(self, book: dict) -> None:
        state = self.load_state()
        if book["id"] not in state["spellbooks"]:
            raise StoreError(f"Unknown spellbook: {book['id']}")
        state["spellbooks"][book["id"]] = book
        self.save_state(state)

    def delete_spellbook(self, spellbook_id: str) -> dict:
        """Delete a spellbook with its open batch, immutable segments, and exports."""

        state = self.load_state()
        book = state["spellbooks"].pop(spellbook_id, None)
        if book is None:
            raise StoreError(f"Unknown spellbook: {spellbook_id}")
        # Move the folder aside first so a locked file fails the delete before
        # anything changes. Saving state commits the delete; if the process
        # dies before that, startup moves the folder back.
        book_dir = self.spellbook_dir(spellbook_id)
        trash = Path(tempfile.mkdtemp(prefix=".deleted-", dir=self.spellbooks_dir))
        moved = trash / spellbook_id
        try:
            if book_dir.exists():
                os.replace(book_dir, moved)
            self.save_state(state)
        except BaseException:
            if moved.exists():
                os.replace(moved, book_dir)
            trash.rmdir()
            raise
        shutil.rmtree(trash, ignore_errors=True)
        # Spells retained only for this book's references can now be reclaimed.
        self.reconcile_spell_index()
        return book

    def begin_batch(self, spellbook_id: str) -> dict:
        book = self.get_spellbook(spellbook_id)
        if book["open_batch"] is not None:
            raise StoreError("This spellbook already has an open batch")
        batch_id = f"batch-{len(book['batches']) + 1:04d}"
        book["open_batch"] = {"id": batch_id, "created_at": utc_now(), "entities": []}
        self.update_spellbook(book)
        return book

    def add_open_entity(self, spellbook_id: str, entity: dict) -> dict:
        book = self.get_spellbook(spellbook_id)
        batch = book.get("open_batch")
        if not batch:
            raise StoreError("Begin a batch first")
        key = (entity["kind"], entity["id"])
        existing = {(item["kind"], item["id"]) for item in batch["entities"]}
        if key not in existing:
            batch["entities"].append(entity)
            self.update_spellbook(book)
        return book

    def remove_open_entity(self, spellbook_id: str, index: int) -> dict:
        book = self.get_spellbook(spellbook_id)
        batch = book.get("open_batch")
        if not batch:
            raise StoreError("There is no open batch")
        if not 0 <= index < len(batch["entities"]):
            raise StoreError("Invalid batch item")
        del batch["entities"][index]
        self.update_spellbook(book)
        return book
