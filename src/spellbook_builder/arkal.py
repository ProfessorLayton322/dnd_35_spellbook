from __future__ import annotations

import re
from collections.abc import Callable
from urllib.parse import parse_qsl, parse_qs, urlencode, urljoin, urlparse, urlunparse

from bs4 import BeautifulSoup, Tag

from .fetch import Fetcher
from .tables import table_to_text
from .util import clean_text, normalized_name, slugify, utc_now


class ArkalParseError(ValueError):
    pass


ARKALSEIF_STATIC_HOSTS = {"arkalseif.info", "dnd.arkalseif.info"}
ARKALSEIF_DYNAMIC_HOST = "dndtools.org"


def _content(html: str) -> Tag:
    soup = BeautifulSoup(html, "html.parser")
    content = soup.find(id="content")
    if not content:
        raise ArkalParseError("Page has no #content region; site markup may have changed")
    return content


def parse_class_page(html: str, source_url: str) -> dict:
    content = _content(html)
    spell_heading = content.find(lambda tag: isinstance(tag, Tag) and tag.name in {"h2", "h3", "h4"} and "spells for" in normalized_name(tag.get_text(" ")))
    if spell_heading is None:
        raise ArkalParseError("Could not find the class spell-list heading")
    heading = content.find("h2")
    class_name = clean_text(heading.get_text(" ")) if heading else clean_text(spell_heading.get_text(" ")).removeprefix("Spells for ")
    levels: dict[int, str] = {}
    for link in spell_heading.find_all_next("a", href=True):
        if link.find_previous(id="comments"):
            break
        href = str(link["href"])
        match = re.search(r"spells-level-(\d+)", href, re.I) or re.search(r"\b(?:level\s*)?(\d+)\b", clean_text(link.get_text(" ")), re.I)
        if match and "spells-level" in href:
            levels[int(match.group(1))] = urljoin(source_url, href)
    if not levels:
        raise ArkalParseError(f"No spell-level links found for {class_name}")
    return {"class_name": class_name, "source_url": source_url, "levels": dict(sorted(levels.items()))}


def parse_level_page(html: str, source_url: str) -> list[dict]:
    content = _content(html)
    links: list[dict] = []
    seen: set[str] = set()
    for table in content.find_all("table"):
        headers = normalized_name(" ".join(th.get_text(" ", strip=True) for th in table.find_all("th")))
        if "spell name" not in headers:
            continue
        for row in table.find_all("tr"):
            link = row.find("a", href=re.compile(r"/spells/"))
            if not link:
                continue
            url = urljoin(source_url, str(link["href"]))
            if url not in seen:
                seen.add(url)
                links.append({"name": clean_text(link.get_text(" ")), "url": url})
    if not links:
        raise ArkalParseError(f"No spell links found on level page {source_url}")
    return links


def _complete_level_url(source_url: str) -> str:
    """Return a listing URL that asks the live database for every level item.

    Arkalseif's public class URLs are a static mirror.  Their pager links still
    contain ``?page=N``, but the static host serves page one for every query.
    Arkalseif links its working/filter version at dndtools.org, whose paths are
    compatible after dropping the static ``index.html`` suffix.
    """

    parsed = urlparse(source_url)
    host = (parsed.hostname or "").casefold()
    path = parsed.path
    netloc = parsed.netloc
    scheme = parsed.scheme
    if host in ARKALSEIF_STATIC_HOSTS:
        if path.endswith("/index.html"):
            path = path[: -len("index.html")]
        scheme = "https"
        netloc = ARKALSEIF_DYNAMIC_HOST
    query = [(key, value) for key, value in parse_qsl(parsed.query, keep_blank_values=True) if key not in {"page", "page_size"}]
    query.append(("page_size", "1000"))
    return urlunparse((scheme, netloc, path, parsed.params, urlencode(query), ""))


def _level_total(html: str) -> int | None:
    content = _content(html)
    pagination = content.find(class_="pagination")
    if pagination is None:
        return None
    match = re.search(r"\btotal\s+([\d,]+)\s+items?\b", clean_text(pagination.get_text(" ")), re.I)
    return int(match.group(1).replace(",", "")) if match else None


def _pagination_urls(html: str, fetched_url: str) -> list[str]:
    content = _content(html)
    fetched = urlparse(fetched_url)
    pages: dict[int, str] = {}
    for link in content.select(".pagination a[href]"):
        candidate = urljoin(fetched_url, str(link["href"]))
        parsed = urlparse(candidate)
        if parsed.scheme != fetched.scheme or parsed.netloc != fetched.netloc or parsed.path != fetched.path:
            continue
        values = parse_qs(parsed.query).get("page", [])
        if len(values) == 1 and values[0].isdigit() and int(values[0]) > 1:
            pages[int(values[0])] = candidate
    return [pages[page] for page in sorted(pages)]


def fetch_level_links(level_url: str, fetcher: Fetcher) -> list[dict]:
    """Fetch and de-duplicate all spell links across a level listing."""

    queue = [_complete_level_url(level_url)]
    queued = set(queue)
    visited: set[str] = set()
    links: list[dict] = []
    seen: set[str] = set()
    expected_total: int | None = None
    while queue:
        page_url = queue.pop(0)
        visited.add(page_url)
        html = fetcher.get(page_url)
        for link in parse_level_page(html, page_url):
            if link["url"] not in seen:
                seen.add(link["url"])
                links.append(link)
        page_total = _level_total(html)
        if page_total is not None:
            expected_total = page_total if expected_total is None else max(expected_total, page_total)
        if expected_total is not None and len(links) >= expected_total:
            break
        for candidate in _pagination_urls(html, page_url):
            if candidate not in visited and candidate not in queued:
                queued.add(candidate)
                queue.append(candidate)
    if expected_total is not None and len(links) < expected_total:
        raise ArkalParseError(
            f"Spell-list pagination is incomplete for {level_url}: "
            f"found {len(links)} of {expected_total} advertised items"
        )
    return links


def _label_values(content: Tag) -> dict[str, str]:
    values: dict[str, str] = {}
    for strong in content.find_all("strong"):
        label = clean_text(strong.get_text(" ")).rstrip(":")
        if not label:
            continue
        fragments: list[str] = []
        for sibling in strong.next_siblings:
            if isinstance(sibling, Tag) and sibling.name == "br":
                break
            if isinstance(sibling, Tag) and sibling.name == "strong":
                break
            fragments.append(sibling.get_text(" ", strip=True) if isinstance(sibling, Tag) else str(sibling))
        values[label] = clean_text(" ".join(fragments)).strip(" ,")
    return values


def _body_blocks(body: Tag | None) -> tuple[list[dict], list[str], list[dict]]:
    blocks: list[dict] = []
    tables: list[str] = []
    sections: list[dict] = []
    current = {"heading": None, "paragraphs": []}
    if body is None:
        return blocks, tables, sections
    for node in body.find_all(["h2", "h3", "h4", "h5", "h6", "p", "ul", "ol", "table"], recursive=False):
        if node.name == "table":
            text = table_to_text(node)
            tables.append(text)
            blocks.append({"type": "table", "text": text})
            continue
        text = clean_text(node.get_text(" ", strip=True))
        if not text:
            continue
        if node.name.startswith("h"):
            if current["paragraphs"] or current["heading"]:
                sections.append(current)
            current = {"heading": text, "paragraphs": []}
            blocks.append({"type": "heading", "text": text})
        else:
            heading = None
            emphasized = node.find(["em", "strong"], recursive=False)
            if emphasized:
                candidate = clean_text(emphasized.get_text(" ")).rstrip(":")
                if text.startswith(clean_text(emphasized.get_text(" "))) and ":" in text[: len(candidate) + 2]:
                    heading = candidate
                    text = text.split(":", 1)[1].strip()
            if heading:
                if current["paragraphs"] or current["heading"]:
                    sections.append(current)
                current = {"heading": heading, "paragraphs": [text] if text else []}
                blocks.append({"type": "heading", "text": heading})
            else:
                current["paragraphs"].append(text)
            blocks.append({"type": "paragraph", "text": text})
    if current["paragraphs"] or current["heading"]:
        sections.append(current)
    return blocks, tables, sections


def spell_id_from_url(source_url: str, name: str) -> str:
    parts = [part for part in urlparse(source_url).path.split("/") if part]
    if "spells" in parts:
        spell_parts = parts[parts.index("spells") + 1 :]
        if spell_parts and spell_parts[-1].casefold() == "index.html":
            spell_parts.pop()
        if len(spell_parts) >= 2:
            return "arkal-" + slugify("--".join(spell_parts[:2]))
    return "arkal-" + slugify(name)


def _static_spell_url(source_url: str) -> str | None:
    """Return the equivalent static-mirror URL for a live spell page."""

    parsed = urlparse(source_url)
    if (parsed.hostname or "").casefold() != ARKALSEIF_DYNAMIC_HOST:
        return None
    path = parsed.path.rstrip("/")
    if not path.startswith("/spells/"):
        return None
    return urlunparse(("https", "dnd.arkalseif.info", f"{path}/index.html", "", "", ""))


def parse_spell_page(html: str, source_url: str, fetched_at: str | None = None) -> dict:
    content = _content(html)
    heading = content.find("h2")
    if not heading:
        raise ArkalParseError("Spell page has no spell-name heading")
    name = clean_text(heading.get_text(" "))
    source_link = content.find("a", href=re.compile(r"/rulebooks/"))
    source_book = clean_text(source_link.get_text(" ")) if source_link else None
    prefix_text = clean_text(" ".join(str(node) for node in heading.next_siblings if not (isinstance(node, Tag) and node.name == "strong")))
    page_match = re.search(r"\bp\.\s*(\d+)", BeautifulSoup(prefix_text, "html.parser").get_text(" "), re.I)
    school_link = content.find("a", href=re.compile(r"/schools/"))
    school = clean_text(school_link.get_text(" ")) if school_link else None
    subschool = None
    if school_link:
        school_tail: list[str] = []
        for sibling in school_link.next_siblings:
            if isinstance(sibling, Tag) and sibling.name in {"br", "strong"}:
                break
            school_tail.append(sibling.get_text(" ", strip=True) if isinstance(sibling, Tag) else str(sibling))
        tail_text = clean_text(" ".join(school_tail))
        match = re.search(r"\(([^)]+)\)", tail_text)
        if match:
            subschool = clean_text(match.group(1))
    descriptors = [clean_text(a.get_text(" ")) for a in content.find_all("a", href=re.compile(r"/descriptors/"))]
    labels = _label_values(content)
    levels: list[dict] = []
    for link in content.find_all("a", href=re.compile(r"/classes/.*/spells-level-\d+")):
        text = clean_text(link.get_text(" "))
        match = re.match(r"(.+?)\s+(\d+)$", text)
        if match:
            note_text = ""
            for sibling in link.next_siblings:
                if isinstance(sibling, Tag) and sibling.name in {"a", "br"}:
                    break
                note_text += sibling.get_text(" ", strip=True) if isinstance(sibling, Tag) else str(sibling)
            note_match = re.search(r"\(([^)]+)\)", note_text)
            value = {"class": match.group(1), "level": int(match.group(2)), "notes": clean_text(note_match.group(1)) if note_match else None}
            if value not in levels:
                levels.append(value)
    body = content.find(class_="nice-textile")
    blocks, tables, sections = _body_blocks(body)
    components = [clean_text(abbr.get_text(" ")) for abbr in content.find_all("abbr")]
    if not components and labels.get("Components"):
        components = [part.strip() for part in labels["Components"].split(",")]
    record = {
        "schema_version": 1,
        "id": spell_id_from_url(source_url, name),
        "name": name,
        "normalized_name": normalized_name(name),
        "source_url": source_url,
        "source_book": source_book,
        "source_page": int(page_match.group(1)) if page_match else None,
        "school": school,
        "subschool": subschool,
        "descriptors": descriptors,
        "levels": levels,
        "components": components,
        "casting_time": labels.get("Casting Time"),
        "range": labels.get("Range"),
        "target": labels.get("Target"),
        "area": labels.get("Area"),
        "effect": labels.get("Effect"),
        "duration": labels.get("Duration"),
        "saving_throw": labels.get("Saving Throw"),
        "spell_resistance": labels.get("Spell Resistance"),
        "body_sections": sections,
        "tables": tables,
        "content_blocks": blocks,
        "raw_labels": labels,
        "imported_from_classes": [],
        "fetched_at": fetched_at or utc_now(),
    }
    if not record["name"] or not blocks:
        raise ArkalParseError(f"Spell record from {source_url} is missing name or description")
    return record


def fetch_spell_record(source_url: str, fetcher: Fetcher) -> dict:
    """Fetch a spell, recovering incomplete live records from the static mirror."""

    html = fetcher.get(source_url)
    try:
        return parse_spell_page(html, source_url)
    except ArkalParseError:
        mirror_url = _static_spell_url(source_url)
        if mirror_url is None:
            raise
        return parse_spell_page(fetcher.get(mirror_url), mirror_url)


def import_class(
    class_url: str,
    fetcher: Fetcher,
    on_progress: Callable[[str], None] | None = None,
    on_event: Callable[[dict], None] | None = None,
) -> tuple[dict, dict[str, dict]]:
    progress = on_progress or (lambda _message: None)
    emit = on_event or (lambda _event: None)
    class_info = parse_class_page(fetcher.get(class_url), class_url)
    total_levels = len(class_info["levels"])
    emit(
        {
            "type": "class_discovered",
            "class_name": class_info["class_name"],
            "completed_levels": 0,
            "total_levels": total_levels,
            "downloaded_spells": 0,
            "total_spells": None,
        }
    )

    level_links: dict[int, list[dict]] = {}
    discovered_spells = 0
    for level, level_url in class_info["levels"].items():
        emit(
            {
                "type": "level_scan_started",
                "class_name": class_info["class_name"],
                "level": level,
                "completed_levels": 0,
                "total_levels": total_levels,
                "downloaded_spells": 0,
                "total_spells": None,
            }
        )
        links = fetch_level_links(level_url, fetcher)
        level_links[level] = links
        discovered_spells += len(links)
        progress(f"{class_info['class_name']} level {level}: {len(links)} links")
        emit(
            {
                "type": "level_scanned",
                "class_name": class_info["class_name"],
                "level": level,
                "spells_in_level": len(links),
                "completed_levels": 0,
                "total_levels": total_levels,
                "downloaded_spells": 0,
                "total_spells": None,
                "discovered_spells": discovered_spells,
            }
        )

    imported: dict[str, dict] = {}
    level_ids: dict[str, list[str]] = {}
    completed_levels = 0
    downloaded_spells = 0
    emit(
        {
            "type": "download_started",
            "class_name": class_info["class_name"],
            "completed_levels": completed_levels,
            "total_levels": total_levels,
            "downloaded_spells": downloaded_spells,
            "total_spells": discovered_spells,
        }
    )
    for level, links in level_links.items():
        ids: list[str] = []
        for link in links:
            record = fetch_spell_record(link["url"], fetcher)
            membership = {"class_name": class_info["class_name"], "spell_level": level}
            record["imported_from_classes"] = [membership]
            imported[record["id"]] = record
            ids.append(record["id"])
            downloaded_spells += 1
            emit(
                {
                    "type": "spell_downloaded",
                    "class_name": class_info["class_name"],
                    "level": level,
                    "spell_name": record["name"],
                    "completed_levels": completed_levels,
                    "total_levels": total_levels,
                    "downloaded_spells": downloaded_spells,
                    "total_spells": discovered_spells,
                }
            )
        level_ids[str(level)] = list(dict.fromkeys(ids))
        completed_levels += 1
        emit(
            {
                "type": "level_completed",
                "class_name": class_info["class_name"],
                "level": level,
                "spells_in_level": len(level_ids[str(level)]),
                "completed_levels": completed_levels,
                "total_levels": total_levels,
                "downloaded_spells": downloaded_spells,
                "total_spells": discovered_spells,
            }
        )
    result = {
        "schema_version": 1,
        "class_name": class_info["class_name"],
        "source_url": class_url,
        "levels": level_ids,
        "imported_at": utc_now(),
    }
    return result, imported
