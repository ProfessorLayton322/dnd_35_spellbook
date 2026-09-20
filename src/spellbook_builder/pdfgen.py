from __future__ import annotations

import os
import tempfile
from pathlib import Path
from xml.sax.saxutils import escape

from pypdf import PdfReader, PdfWriter
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_RIGHT
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import Flowable, HRFlowable, KeepTogether, Paragraph, SimpleDocTemplate, Spacer


# 1: every entity started on a new page.
# 2: entities flow continuously, separated by a black rule; only a batch starts a new page.
# 3: feat-derived summon statblocks and feat-granted summon spell entries.
RENDERER_VERSION = 3


def _safe(value) -> str:
    return escape(str(value if value is not None else ""))


class EntityMarker(Flowable):
    def __init__(self, entity_index: int, title: str, anchors: list[dict], logical_offset: int):
        super().__init__()
        self.entity_index = entity_index
        self.title = title
        self.anchors = anchors
        self.logical_offset = logical_offset
        self.width = 0
        self.height = 0

    def wrap(self, _available_width, _available_height):
        return (0, 0)

    def draw(self):
        self.anchors.append(
            {
                "entity_index": self.entity_index,
                "title": self.title,
                "page": self.canv.getPageNumber() + self.logical_offset,
            }
        )


def _styles():
    styles = getSampleStyleSheet()
    styles.add(ParagraphStyle(name="EntityTitle", parent=styles["Title"], fontName="Helvetica-Bold", fontSize=17, leading=20, spaceAfter=8))
    styles.add(ParagraphStyle(name="Section", parent=styles["Heading3"], fontName="Helvetica-Bold", fontSize=10.5, leading=13, spaceBefore=7, spaceAfter=3))
    styles.add(ParagraphStyle(name="BodySmall", parent=styles["BodyText"], fontName="Helvetica", fontSize=8.7, leading=11, spaceAfter=5))
    styles.add(ParagraphStyle(name="Meta", parent=styles["BodyText"], fontName="Helvetica", fontSize=7.5, leading=9, textColor=colors.HexColor("#555555"), spaceAfter=4))
    styles.add(ParagraphStyle(name="TOCTitle", parent=styles["Title"], alignment=TA_CENTER, fontSize=20, leading=24, spaceAfter=18))
    styles.add(ParagraphStyle(name="TOCEntry", parent=styles["BodyText"], fontSize=9, leading=11, leftIndent=6, rightIndent=6, spaceAfter=2))
    return styles


def _line(label: str, value, styles) -> Paragraph | None:
    if value is None or value == "" or value == []:
        return None
    if isinstance(value, list):
        value = ", ".join(str(item) for item in value)
    return Paragraph(f"<b>{_safe(label)}:</b> {_safe(value)}", styles["BodySmall"])


def _block_flowables(blocks: list[dict], styles) -> list:
    story: list = []
    for block in blocks:
        text = block.get("text", "")
        if not text:
            continue
        if block["type"] == "heading":
            story.append(Paragraph(_safe(text), styles["Section"]))
        elif block["type"] == "table":
            story.append(Paragraph(_safe(text).replace("\n", "<br/>"), styles["BodySmall"]))
        else:
            story.append(Paragraph(_safe(text), styles["BodySmall"]))
    return story


def entity_separator() -> Flowable:
    return HRFlowable(width="100%", thickness=1, lineCap="butt", color=colors.black, spaceBefore=10, spaceAfter=10)


def spell_flowables(spell: dict, styles) -> tuple[list, list]:
    """Return ``(heading, body)`` so the heading can be kept on one page."""

    school = spell.get("school") or ""
    if spell.get("subschool"):
        school += f" ({spell['subschool']})"
    if spell.get("descriptors"):
        school += " [" + ", ".join(spell["descriptors"]) + "]"
    levels = ", ".join(
        f"{entry['class']} {entry['level']}" + (f" ({entry['notes']})" if entry.get("notes") else "")
        for entry in spell.get("levels", [])
    )
    source = spell.get("source_book") or ""
    if spell.get("source_page"):
        source += f", p. {spell['source_page']}"
    heading = [Paragraph(_safe(spell["name"]), styles["EntityTitle"])]
    if school:
        heading.append(Paragraph(_safe(school), styles["BodySmall"]))
    body: list = []
    for label, value in (
        ("Level", levels),
        ("Components", spell.get("components")),
        ("Casting Time", spell.get("casting_time")),
        ("Range", spell.get("range")),
        ("Target", spell.get("target")),
        ("Area", spell.get("area")),
        ("Effect", spell.get("effect")),
        ("Duration", spell.get("duration")),
        ("Saving Throw", spell.get("saving_throw")),
        ("Spell Resistance", spell.get("spell_resistance")),
        ("Source", source),
    ):
        flowable = _line(label, value, styles)
        if flowable:
            body.append(flowable)
    body.append(Spacer(1, 5))
    body.extend(_block_flowables(spell.get("content_blocks", []), styles))
    return heading, body


def monster_flowables(monster: dict, display_name: str, styles) -> tuple[list, list]:
    """Return ``(heading, body)`` so the heading can be kept on one page."""

    title = display_name or monster["name"]
    heading = [Paragraph(_safe(title), styles["EntityTitle"])]
    if display_name and display_name != monster["name"]:
        heading.append(Paragraph(f"SRD statblock variant: {_safe(monster['name'])}", styles["Meta"]))
    body: list = []
    for label, value in monster.get("fields", {}).items():
        flowable = _line(label, value, styles)
        if flowable:
            body.append(flowable)
    body.append(Spacer(1, 5))
    body.extend(_block_flowables(monster.get("shared_sections", []), styles))
    body.append(Paragraph(f"Source: {_safe(monster.get('source_url', ''))}", styles["Meta"]))
    return heading, body


def feat_spell_flowables(entity: dict, styles) -> tuple[list, list]:
    """Render the summon spell access granted by a selected character feat."""

    heading = [Paragraph(_safe(entity["title"]), styles["EntityTitle"])]
    body = [
        _line("Level", "Druid 5", styles),
        _line("Restriction", "Can only summon a shadow mastiff", styles),
        _line("Source", "Nightbringer Initiate — Faiths of Eberron, p. 147", styles),
        Spacer(1, 5),
        Paragraph(
            "Nightbringer Initiate adds <i>summon monster V</i> to the druid spell list at 5th level. "
            "This granted version can summon only a shadow mastiff; otherwise use the normal spell rules.",
            styles["BodySmall"],
        ),
    ]
    return heading, [item for item in body if item is not None]


def _atomic_pdf_target(path: Path) -> tuple[Path, Path]:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    os.close(fd)
    return Path(name), path


def render_batch_segment(
    path: Path,
    entities: list[dict],
    spells: dict,
    monsters: dict,
    logical_page_start: int,
    *,
    rendered_monsters: dict[int, dict] | None = None,
) -> tuple[int, list[dict]]:
    if not entities:
        raise ValueError("Cannot render an empty batch")
    styles = _styles()
    anchors: list[dict] = []
    story: list = []
    offset = logical_page_start - 1
    for index, entity in enumerate(entities):
        if entity["kind"] == "spell":
            heading, body = spell_flowables(spells[entity["id"]], styles)
        elif entity["kind"] == "summon_statblock":
            monster = (rendered_monsters or {}).get(index, monsters[entity["id"]])
            heading, body = monster_flowables(monster, entity["title"], styles)
        elif entity["kind"] == "feat_spell":
            heading, body = feat_spell_flowables(entity, styles)
        else:
            raise ValueError(f"Unknown printable entity kind: {entity['kind']}")
        # Entities flow directly after one another; only a new batch segment starts a new page.
        # The rule, TOC anchor, heading, and first body line change pages together, so the
        # anchor always records the page that shows the title.
        group = [entity_separator()] if index else []
        group += [EntityMarker(index, entity["title"], anchors, offset), *heading, *body[:1]]
        story.append(KeepTogether(group))
        story.extend(body[1:])

    temp, target = _atomic_pdf_target(path)
    doc = SimpleDocTemplate(str(temp), pagesize=letter, rightMargin=0.62 * inch, leftMargin=0.62 * inch, topMargin=0.58 * inch, bottomMargin=0.58 * inch)

    def number_page(canvas, _doc):
        logical = canvas.getPageNumber() + offset
        canvas.saveState()
        canvas.setFont("Helvetica", 8)
        canvas.drawRightString(letter[0] - 0.5 * inch, 0.33 * inch, str(logical))
        canvas.setFillColor(colors.HexColor("#777777"))
        canvas.drawString(0.5 * inch, 0.33 * inch, f"D&D 3.5 Spellbook · renderer {RENDERER_VERSION}")
        canvas.restoreState()

    try:
        doc.build(story, onFirstPage=number_page, onLaterPages=number_page)
        os.replace(temp, target)
    finally:
        temp.unlink(missing_ok=True)
    page_count = len(PdfReader(str(target)).pages)
    anchors.sort(key=lambda entry: entry["entity_index"])
    return page_count, anchors


def render_toc(path: Path, spellbook_name: str, entries: list[dict]) -> int:
    styles = _styles()
    story: list = [Paragraph(f"{_safe(spellbook_name)} — Table of Contents", styles["TOCTitle"])]
    if not entries:
        story.append(Paragraph("No committed content.", styles["BodySmall"]))
    for entry in entries:
        title = _safe(entry["title"])
        page = _safe(entry["page"])
        dots = "." * max(3, 82 - min(70, len(entry["title"])))
        story.append(Paragraph(f"{title} {dots} <b>{page}</b>", styles["TOCEntry"]))
    temp, target = _atomic_pdf_target(path)
    try:
        SimpleDocTemplate(str(temp), pagesize=letter, rightMargin=0.65 * inch, leftMargin=0.65 * inch, topMargin=0.65 * inch, bottomMargin=0.55 * inch).build(story)
        os.replace(temp, target)
    finally:
        temp.unlink(missing_ok=True)
    return len(PdfReader(str(target)).pages)


def concatenate_pdfs(path: Path, parts: list[Path]) -> None:
    temp, target = _atomic_pdf_target(path)
    writer = PdfWriter()
    try:
        for part in parts:
            writer.append(str(part))
        with temp.open("wb") as handle:
            writer.write(handle)
        os.replace(temp, target)
    finally:
        temp.unlink(missing_ok=True)


def pdf_content_hashes(path: Path) -> list[str]:
    import hashlib

    hashes: list[str] = []
    for page in PdfReader(str(path)).pages:
        content = page.get_contents()
        data = content.get_data() if content else b""
        hashes.append(hashlib.sha256(data).hexdigest())
    return hashes
