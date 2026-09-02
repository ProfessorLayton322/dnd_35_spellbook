from __future__ import annotations

from dataclasses import dataclass

from bs4 import BeautifulSoup, Tag

from .util import clean_text


@dataclass(frozen=True)
class GridCell:
    text: str
    header: bool = False


def table_matrix(table: Tag) -> list[list[GridCell]]:
    """Expand an HTML table into a rectangular matrix, including spans."""
    rows: list[list[GridCell]] = []
    spans: dict[int, tuple[int, GridCell]] = {}
    for tr in table.find_all("tr"):
        if tr.find_parent("tfoot") is not None:
            continue
        row: list[GridCell] = []
        col = 0

        def consume_spans() -> None:
            nonlocal col
            while col in spans:
                remaining, cell = spans[col]
                row.append(cell)
                if remaining <= 1:
                    del spans[col]
                else:
                    spans[col] = (remaining - 1, cell)
                col += 1

        consume_spans()
        for node in tr.find_all(["th", "td"], recursive=False):
            consume_spans()
            cell = GridCell(clean_text(node.get_text(" ", strip=True)), node.name == "th")
            colspan = max(1, int(node.get("colspan", 1) or 1))
            rowspan = max(1, int(node.get("rowspan", 1) or 1))
            for offset in range(colspan):
                row.append(cell)
                if rowspan > 1:
                    spans[col + offset] = (rowspan - 1, cell)
            col += colspan
        consume_spans()
        if row:
            rows.append(row)
    width = max((len(row) for row in rows), default=0)
    return [row + [GridCell("")] * (width - len(row)) for row in rows]


def table_to_text(table_or_html: Tag | str) -> str:
    """Convert a table to deterministic nested plain-text bullets."""
    if isinstance(table_or_html, str):
        table = BeautifulSoup(table_or_html, "html.parser").find("table")
        if table is None:
            return ""
    else:
        table = table_or_html
    matrix = table_matrix(table)
    if not matrix:
        return ""
    first = matrix[0]
    has_header_row = any(cell.header for cell in first) and len(matrix) > 1
    headers = [cell.text or f"Column {index + 1}" for index, cell in enumerate(first)] if has_header_row else []
    body = matrix[1:] if has_header_row else matrix
    lines: list[str] = []
    for row_index, row in enumerate(body, 1):
        values = [cell.text for cell in row]
        if headers:
            first_value = values[0] if values else ""
            lines.append(f"- {headers[0]}: {first_value}")
            for header, value in zip(headers[1:], values[1:]):
                lines.append(f"  - {header}: {value}")
        else:
            lines.append(f"- Row {row_index}: {values[0] if values else ''}")
            for column, value in enumerate(values[1:], 2):
                lines.append(f"  - Column {column}: {value}")
    # Footers often contain table footnotes not represented by the primary rows.
    footer = table.find("tfoot")
    if footer:
        for index, item in enumerate(footer.find_all("li"), 1):
            lines.append(f"- Footnote {index}: {clean_text(item.get_text(' ', strip=True))}")
    return "\n".join(lines)
