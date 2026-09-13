from __future__ import annotations

import re
from collections import defaultdict
from urllib.parse import urldefrag, urljoin, urlparse

from bs4 import BeautifulSoup, Tag

from .fetch import Fetcher
from .tables import table_to_text
from .util import clean_text, normalized_name, slugify, utc_now


D20_BASE = "https://www.d20srd.org"
ROMANS = {"I": 1, "II": 2, "III": 3, "IV": 4, "V": 5, "VI": 6, "VII": 7, "VIII": 8, "IX": 9}
TEMPLATE_PAGES = {"celestialcreature.htm", "fiendishcreature.htm"}
# The level-9 Nature's Ally table uniquely omits the "(any)" qualifier used by
# every lower elemental entry, while still linking the generic elemental page.
MULTI_VARIANT_ALIASES = {"elemental elder"}


class SrdParseError(ValueError):
    pass


def summon_urls() -> list[tuple[str, int, str]]:
    values: list[tuple[str, int, str]] = []
    for roman, level in ROMANS.items():
        values.append(("summon_monster", level, f"{D20_BASE}/srd/spells/summonMonster{roman}.htm"))
        values.append(("summon_natures_ally", level, f"{D20_BASE}/srd/spells/summonNaturesAlly{roman}.htm"))
    return values


def _footnotes(table: Tag) -> dict[str, str]:
    result: dict[str, str] = {}
    footer = table.find("tfoot")
    if not footer:
        return result
    for index, item in enumerate(footer.find_all("li"), 1):
        result[str(index)] = clean_text(item.get_text(" ", strip=True))
    return result


def parse_summon_page(html: str, source_url: str, family: str, level: int) -> dict:
    soup = BeautifulSoup(html, "html.parser")
    heading = soup.find("h1")
    if not heading:
        raise SrdParseError(f"Summon page has no h1: {source_url}")
    table = heading.find_next("table", class_="right")
    if not table:
        raise SrdParseError(f"Summon page has no creature table: {source_url}")
    notes_by_number = _footnotes(table)
    entries: list[dict] = []
    rows = table.find("tbody").find_all("tr") if table.find("tbody") else table.find_all("tr")
    for row in rows:
        cells = row.find_all(["td", "th"], recursive=False)
        if not cells or cells[0].name == "th":
            continue
        creature_cell = cells[0]
        supers = [clean_text(sup.get_text(" ")) for sup in creature_cell.find_all("sup")]
        clone = BeautifulSoup(str(creature_cell), "html.parser")
        for sup in clone.find_all("sup"):
            sup.decompose()
        display_name = clean_text(clone.get_text(" ", strip=True))
        if not display_name:
            continue
        template = next((word for word in ("Celestial", "Fiendish") if normalized_name(display_name).startswith(word.casefold())), None)
        base_name = re.sub(r"^(celestial|fiendish)\s+", "", display_name, flags=re.I)
        base_name = re.sub(r"\s*\((?:animal|aquatic)[^)]*\)\s*$", "", base_name, flags=re.I)
        links = []
        for link in creature_cell.find_all("a", href=True):
            href = urljoin(source_url, str(link["href"]))
            if "/srd/monsters/" in href:
                links.append(href)
        target = next((href for href in reversed(links) if urlparse(urldefrag(href).url).path.rsplit("/", 1)[-1].casefold() not in TEMPLATE_PAGES), None)
        entry_notes = [notes_by_number[number] for number in supers if number in notes_by_number]
        qualifier_match = re.findall(r"\(([^)]+)\)", display_name)
        entry_notes.extend(qualifier_match)
        bracket_qualifiers = re.findall(r"\[([^]]+)\]", display_name)
        alignment = clean_text(cells[1].get_text(" ", strip=True)) if len(cells) > 1 else None
        for qualifier in bracket_qualifiers:
            parts = [clean_text(part) for part in qualifier.split(";") if clean_text(part)]
            if parts and not alignment and re.fullmatch(r"[LNC][GNE]", parts[0], re.I):
                alignment = parts.pop(0).upper()
            entry_notes.extend(parts)
        entries.append(
            {
                "display_name": display_name,
                "base_creature_name": clean_text(base_name),
                "template": template,
                "alignment": alignment,
                "notes": list(dict.fromkeys(entry_notes)),
                "source_links": links,
                "target_url": target,
                "target_fragment": urldefrag(target).fragment if target else None,
                "monster_ref": None,
                "monster_refs": [],
                "resolution": None,
            }
        )
    if not entries:
        raise SrdParseError(f"No summon entries parsed from {source_url}")
    return {
        "schema_version": 1,
        "spell_family": family,
        "spell_level": level,
        "spell_name": clean_text(heading.get_text(" ")),
        "source_url": source_url,
        "entries": entries,
        "table_text": table_to_text(table),
        "fetched_at": utc_now(),
    }


def monster_page_id(source_url: str) -> str:
    filename = urlparse(source_url).path.rsplit("/", 1)[-1].rsplit(".", 1)[0]
    # Insert spaces before capitals so camelCase URLs keep human-stable IDs.
    return slugify(re.sub(r"(?<=[a-z])(?=[A-Z])", " ", filename))


def _table_variant_names(table: Tag, fallback: str) -> list[str]:
    head = table.find("tr", class_=lambda value: value and "colHead" in value)
    if head:
        names = [clean_text(cell.get_text(" ", strip=True)) for cell in head.find_all(["th", "td"], recursive=False)]
        names = [name for name in names[1:] if name]
        if names:
            return names
    return [fallback]


def _table_fields(table: Tag, names: list[str]) -> list[dict[str, str]]:
    fields: list[dict[str, str]] = [dict() for _ in names]
    for row in table.find_all("tr"):
        if "colHead" in (row.get("class") or []):
            continue
        cells = row.find_all(["th", "td"], recursive=False)
        if not cells or cells[0].name != "th":
            continue
        label = clean_text(cells[0].get_text(" ", strip=True)).rstrip(":").strip()
        values: list[str] = []
        for cell in cells[1:]:
            value = clean_text(cell.get_text(" ", strip=True))
            values.extend([value] * max(1, int(cell.get("colspan", 1) or 1)))
        if not values:
            continue
        if len(values) == 1 and len(names) > 1:
            values *= len(names)
        for index, target in enumerate(fields):
            target[label] = values[index] if index < len(values) else values[-1]
    return fields


def _content_blocks(heading: Tag, boundaries: list[Tag] | None = None) -> list[dict]:
    blocks: list[dict] = []
    for node in heading.next_siblings:
        if isinstance(node, Tag) and "footer" in (node.get("class") or []):
            break
        if not isinstance(node, Tag):
            continue
        if boundaries and any(node is boundary for boundary in boundaries):
            break
        if node.name in {"h2", "h3", "h4", "h5", "h6"}:
            text = clean_text(node.get_text(" ", strip=True))
            if text:
                blocks.append({"type": "heading", "text": text})
        elif node.name in {"p", "ul", "ol"}:
            text = clean_text(node.get_text(" ", strip=True))
            if text:
                blocks.append({"type": "paragraph", "text": text})
        elif node.name == "table":
            if "statBlock" not in (node.get("class") or []):
                blocks.append({"type": "table", "text": table_to_text(node)})
    return blocks


def parse_monster_page(html: str, source_url: str, fetched_at: str | None = None) -> dict:
    soup = BeautifulSoup(html, "html.parser")
    heading = soup.find("h1")
    if not heading:
        raise SrdParseError(f"Monster page has no h1: {source_url}")
    page_name = clean_text(heading.get_text(" "))
    page_id = monster_page_id(source_url)
    statblocks: list[dict] = []
    table_texts: list[str] = []
    table_groups: list[Tag] = []
    seen_ids: defaultdict[str, int] = defaultdict(int)
    for table_index, table in enumerate(heading.find_all_next("table", class_="statBlock"), 1):
        prior_heading = table.find_previous(["h1", "h2", "h3"])
        fallback = clean_text(prior_heading.get_text(" ")) if prior_heading else page_name
        names = _table_variant_names(table, fallback)
        field_sets = _table_fields(table, names)
        table_text = table_to_text(table)
        table_texts.append(table_text)
        table_groups.append(table.find_previous(["h2", "h1"]) or heading)
        for name, fields in zip(names, field_sets):
            base_id = f"{page_id}::{slugify(name)}"
            seen_ids[base_id] += 1
            variant_id = base_id if seen_ids[base_id] == 1 else f"{base_id}-{seen_ids[base_id]}"
            statblocks.append(
                {
                    "id": variant_id,
                    "name": name,
                    "normalized_name": normalized_name(name),
                    "fields": fields,
                    "table_index": table_index - 1,
                    "table_text": table_text,
                }
            )
    if not statblocks:
        raise SrdParseError(f"No statblock tables parsed from {source_url}")
    variant_names = [block["normalized_name"] for block in statblocks]
    direct_h2s = list(heading.find_all_next("h2"))
    footer = heading.find_next("div", class_="footer")
    if footer:
        direct_h2s = [node for node in direct_h2s if node.find_previous("div", class_="footer") is not footer]
    group_h2s = [group for group in table_groups if group.name == "h2"]
    variant_h2s: list[Tag] = []
    for node in direct_h2s:
        node_name = normalized_name(node.get_text(" ", strip=True))
        if any(node_name == name or (len(node_name) >= 4 and (name.startswith(node_name) or node_name.startswith(name))) for name in variant_names):
            variant_h2s.append(node)
    boundaries: list[Tag] = list(dict.fromkeys([*group_h2s, *variant_h2s]))
    intro_blocks = _content_blocks(heading, boundaries) if boundaries else []
    sections_by_table: dict[int, list[dict]] = {}
    for index, group in enumerate(table_groups):
        group_blocks = _content_blocks(group, [node for node in boundaries if node is not group])
        sections_by_table[index] = group_blocks if group is heading else intro_blocks + group_blocks
    for block in statblocks:
        sections = list(sections_by_table.get(block["table_index"], intro_blocks))
        for node in variant_h2s:
            node_name = normalized_name(node.get_text(" ", strip=True))
            candidate_name = block["normalized_name"]
            group = table_groups[block["table_index"]]
            if node is not group and (node_name == candidate_name or (len(node_name) >= 4 and candidate_name.startswith(node_name))):
                sections.extend(_content_blocks(node, [boundary for boundary in boundaries if boundary is not node]))
                break
        block["sections"] = sections
    page_sections = _content_blocks(heading)
    return {
        "schema_version": 1,
        "page_id": page_id,
        "name": page_name,
        "source_url": urldefrag(source_url).url,
        "statblocks": statblocks,
        "tables": table_texts,
        "shared_sections": intro_blocks,
        "page_sections": page_sections,
        "fetched_at": fetched_at or utc_now(),
    }


def _reference_name(value: str) -> str:
    value = re.sub(r"\[[^]]*]", " ", value)
    value = re.sub(r"\((?:any|animal|aquatic|demon|devil|dinosaur|eladrin|genie|guardinal|sprite)\)", " ", value, flags=re.I)
    return normalized_name(value)


def _tokens(value: str) -> set[str]:
    ignored = {"animal", "aquatic", "any"}
    return set(_reference_name(value).split()) - ignored


def resolve_entry_refs(entry: dict, page: dict) -> tuple[list[str], str]:
    """Resolve one table entry to one or more variants; '(any)' intentionally expands."""
    display = _reference_name(entry["display_name"])
    template = normalized_name(entry.get("template") or "")
    candidates = page["statblocks"]
    for candidate in candidates:
        if candidate["normalized_name"] == display:
            return [candidate["id"]], "exact display-name variant"
    wanted_tokens = _tokens(entry["display_name"])
    wanted_compact = "".join(display.split())
    scored: list[tuple[int, str]] = []
    fragment = normalized_name(entry.get("target_fragment") or "")
    for candidate in candidates:
        name = candidate["normalized_name"]
        tokens = _tokens(name)
        score = 0
        if tokens == wanted_tokens:
            score = 95
        elif "".join(name.split()) == wanted_compact:
            score = 92
        elif template and template in tokens and _tokens(entry["base_creature_name"]) <= tokens:
            score = 88
        elif wanted_tokens and wanted_tokens <= tokens:
            score = 78
        elif tokens and tokens <= wanted_tokens:
            score = 72 + len(tokens)
        else:
            overlap = len(tokens & wanted_tokens)
            score = overlap * 10
        if fragment and (fragment == name or fragment in name):
            score += 100
        scored.append((score, candidate["id"]))
    scored.sort(key=lambda item: (-item[0], item[1]))
    if not scored or scored[0][0] < 20:
        return [], "no statblock variant matched"
    top_score = scored[0][0]
    top = [candidate_id for score, candidate_id in scored if score == top_score]
    any_variant = bool(re.search(r"\(any\)", entry["display_name"], re.I)) or display in MULTI_VARIANT_ALIASES
    if len(top) > 1 and not any_variant:
        return [], "no unique statblock variant matched"
    resolution = "deterministic normalized-name resolution"
    if len(top) > 1:
        resolution += f"; '(any)' expanded to {len(top)} variants"
    elif template and template not in normalized_name(top[0]):
        resolution += "; SRD page supplies base statblock only (summon template retained in display metadata)"
    return top, resolution


def resolve_entry(entry: dict, page: dict) -> tuple[str | None, str]:
    refs, reason = resolve_entry_refs(entry, page)
    return (refs[0] if len(refs) == 1 else None), reason


def build_summon_index(fetcher: Fetcher, on_progress=None, on_event=None) -> tuple[dict, dict, dict]:
    progress = on_progress or (lambda _message: None)
    emit = on_event or (lambda _event: None)
    lists: dict[str, dict] = {}
    target_urls: dict[str, str] = {}
    table_urls = summon_urls()
    for parsed_tables, (family, level, url) in enumerate(table_urls, 1):
        record = parse_summon_page(fetcher.get(url), url, family, level)
        lists[f"{family}:{level}"] = record
        for entry in record["entries"]:
            if entry["target_url"]:
                target_urls[urldefrag(entry["target_url"]).url] = entry["target_url"]
        progress(f"parsed {record['spell_name']}: {len(record['entries'])} entries")
        # The creature page total is unknown until every table has been read.
        emit(
            {
                "type": "summon_table_parsed",
                "spell_name": record["spell_name"],
                "entries": len(record["entries"]),
                "parsed_tables": parsed_tables,
                "total_tables": len(table_urls),
                "downloaded_monster_pages": 0,
                "total_monster_pages": None,
            }
        )
    emit(
        {
            "type": "monster_download_started",
            "parsed_tables": len(table_urls),
            "total_tables": len(table_urls),
            "downloaded_monster_pages": 0,
            "total_monster_pages": len(target_urls),
        }
    )
    pages: dict[str, dict] = {}
    statblocks: dict[str, dict] = {}
    for index, url in enumerate(sorted(target_urls), 1):
        page = parse_monster_page(fetcher.get(url), url)
        pages[page["page_id"]] = page
        for block in page["statblocks"]:
            canonical = dict(block)
            canonical.update(
                {
                    "page_id": page["page_id"],
                    "source_url": page["source_url"],
                    "shared_sections": block["sections"],
                    "page_shared_sections": page["shared_sections"],
                    "all_page_tables": page["tables"],
                    "fetched_at": page["fetched_at"],
                }
            )
            statblocks[block["id"]] = canonical
        progress(f"monster page {index}/{len(target_urls)}: {page['name']} ({len(page['statblocks'])} variants)")
        emit(
            {
                "type": "monster_page_downloaded",
                "monster_name": page["name"],
                "variants": len(page["statblocks"]),
                "parsed_tables": len(table_urls),
                "total_tables": len(table_urls),
                "downloaded_monster_pages": index,
                "total_monster_pages": len(target_urls),
            }
        )
    unresolved: list[dict] = []
    for key, summon_list in lists.items():
        for entry in summon_list["entries"]:
            target = urldefrag(entry["target_url"]).url if entry["target_url"] else None
            page = next((value for value in pages.values() if value["source_url"] == target), None)
            if page:
                entry["monster_refs"], entry["resolution"] = resolve_entry_refs(entry, page)
                entry["monster_ref"] = entry["monster_refs"][0] if len(entry["monster_refs"]) == 1 else None
                entry["resolved_names"] = [statblocks[ref]["name"] for ref in entry["monster_refs"]]
            if not entry["monster_refs"]:
                unresolved.append({"list": key, "display_name": entry["display_name"], "target_url": target, "reason": entry["resolution"] or "no monster link"})
    metadata = {
        "schema_version": 1,
        "built_at": utc_now(),
        "summon_page_count": len(lists),
        "monster_page_count": len(pages),
        "statblock_count": len(statblocks),
        "entry_count": sum(len(value["entries"]) for value in lists.values()),
        "unresolved": unresolved,
    }
    return lists, statblocks, metadata
