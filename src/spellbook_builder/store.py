from __future__ import annotations

from copy import deepcopy
from pathlib import Path

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
        if not self.state_path.exists():
            self.save_state(self.empty_state())

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

    def merge_class_import(self, class_record: dict, new_spells: dict[str, dict]) -> None:
        spells = self.load_spells()
        for spell_id, incoming in new_spells.items():
            existing = spells.get(spell_id)
            if existing:
                memberships = existing.get("imported_from_classes", []) + incoming.get("imported_from_classes", [])
                incoming["imported_from_classes"] = list({(m["class_name"], m["spell_level"]): m for m in memberships}.values())
            spells[spell_id] = incoming
        self.save_spells(spells)
        state = self.load_state()
        state["imported_classes"][class_record["class_name"]] = class_record
        state["index_metadata"]["spells"] = {"record_count": len(spells), "updated_at": utc_now()}
        self.save_state(state)

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
            "renderer_version": 1,
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
