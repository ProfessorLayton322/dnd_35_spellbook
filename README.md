# Local D&D 3.5 Spellbook Builder

A local-first browser application and CLI for importing D&D 3.5 spells, assembling them in append-only print batches, expanding Summon Monster / Summon Nature's Ally into usable SRD statblocks, and exporting stable PDFs.

The server binds to `127.0.0.1` by default. It has no accounts and is intended for one local user. Scraped indexes, state, spellbooks, immutable PDF segments, and exports live under the ignored `runtime/` directory; none of that user/runtime data belongs in Git.

## Architecture

- Python 3.12, FastAPI, Jinja, and small vanilla JavaScript for the localhost UI.
- Requests with a descriptive user agent, retry/backoff, timeout, and configurable inter-request delay.
- Beautiful Soup semantic parsers for Arkalself and d20srd.
- Versioned UTF-8 JSON indexes and authoritative server state, written through atomic temporary-file replacement.
- ReportLab for deterministic content/TOC rendering and pypdf for lossless segment assembly.
- Immutable per-batch content PDF segments. A full book is always a fresh TOC followed by the original segment files.

The main modules are:

```text
src/spellbook_builder/
  arkal.py       Arkalself class, level-list, and complete spell parsing
  srd.py         d20srd summon-table, monster page, and variant resolution
  tables.py      deterministic HTML table-to-plain-text conversion
  store.py       versioned indexes/state and atomic JSON persistence
  service.py     batch workflow, summon expansion, exports, verification
  pdfgen.py      batch, TOC, and full-PDF rendering/assembly
  web.py         localhost FastAPI application
  cli.py         production CLI paths
```

## Setup and launch

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -e '.[test]'
.venv/bin/spellbook serve
```

Open <http://127.0.0.1:8000>. On Windows PowerShell, use `.venv\Scripts\python.exe` and `.venv\Scripts\spellbook.exe` instead.

Override the data location with `DND_SPELLBOOK_RUNTIME=/absolute/path` or the global `--runtime` option:

```bash
.venv/bin/spellbook --runtime /path/to/private-data serve
```

Global options (`--runtime`, `--timeout`, and `--delay`) go before the subcommand.

## Data import workflow

Paste an Arkalself class URL into the UI, or run:

```bash
.venv/bin/spellbook import-class https://dnd.arkalseif.info/classes/warmage/index.html
.venv/bin/spellbook build-summon-index
.venv/bin/spellbook validate-indexes
```

Class import discovers every linked level rather than assuming levels 0–9, follows each level's spell table, fetches every spell, and merges stable URL-derived IDs on re-import. Global spell class/level mappings and the imported class-list membership are separate.

The summon build reads all Summon Monster I–IX and Summon Nature's Ally I–IX tables. It prefers linked monster pages and anchors, then resolves named statblock columns deterministically. Qualified `(any)` entries intentionally expand to all applicable variants. An unresolved entry is written to `summon_validation.json` and makes the command fail.

## Building a spellbook

In the browser:

1. Create and select a named empty spellbook.
2. Begin a batch.
3. Add individual spells with beginning-only, normalized, case-insensitive prefix suggestions, or add an imported class/level in one operation.
4. Remove mistakes while the batch is open.
5. End the batch to resolve summons, render its immutable segment, update the full PDF, and create the two diff exports.

Only one open batch exists per book. Duplicate spell entities in one open batch are ignored. A committed batch cannot be edited through the application.

For a summon spell, its spell entity is immediately followed by every relevant summon statblock in table order. A statblock is not globally deduplicated away from that required location.

## Append-only PDF design

Every commit writes:

```text
runtime/spellbooks/<book-id>/
  batches/batch-0001.pdf       immutable logical content pages
  full.pdf                     fresh TOC + all immutable segments
  exports/batch-0001/
    toc.pdf                    complete current TOC only
    append.pdf                 exact copy of the newest segment only
    manifest.json
```

Content numbering begins at 1 and is embedded while a segment is first rendered. TOC physical pages do not enter that numbering. Each later segment starts at the prior `page_end + 1`, so a new batch always starts on a fresh content page. Historical content is never re-rendered when a TOC grows.

`manifest.json` records logical ranges, page counts, entity titles/IDs, renderer version, and SHA-256 hashes. `verify-append-only` checks:

- stored immutable-segment hashes;
- contiguous logical batch ranges;
- append PDFs against their segments;
- each full-PDF content stream against the corresponding segment page.

```bash
.venv/bin/spellbook verify-append-only my-spellbook-id
.venv/bin/spellbook render-spellbook my-spellbook-id
```

`render-spellbook` only refreshes the TOC/full assembly; it does not rewrite content segments. A renderer-version mismatch is refused once a spellbook has committed content.

## Other CLI commands

```bash
.venv/bin/spellbook list-spells
.venv/bin/spellbook list-spells --prefix fire --limit 25
.venv/bin/spellbook validate-indexes
.venv/bin/spellbook serve --port 8080
```

Import and summon commands print progress and actionable parse/fetch errors. Network requests default to a 25-second timeout, three limited retries with backoff, and a 0.15-second minimum spacing.

## Runtime JSON schemas

Every top-level document and canonical record has `schema_version: 1`.

`state.json` contains `imported_classes`, index metadata, and `spellbooks`. An imported class maps string spell levels to ordered canonical spell IDs. A spellbook contains a stable ID, name, renderer version, cumulative content-page count, an optional `open_batch`, ordered immutable `batches`, and export manifests.

`indexes/spells.json` maps stable spell IDs to records containing source URL/book/page, school/subschool/descriptors, global class-level mappings, components and all standard spell labels, ordered `content_blocks`, section-preserving body data, ordered plain-text tables, imported class memberships, and fetch time.

`indexes/monsters.json` maps `<page-id>::<variant-id>` to separately addressable statblocks. Each has variant fields, only the relevant/shared prose sections, its own table text, the page's distinct table list, source URL, and fetch time. Multi-column pages therefore cannot overwrite one variant with another.

`indexes/summon_lists.json` maps `<family>:<level>` to the source table and ordered entries. Each entry retains exact display text, base name, template, alignment, footnotes/qualifiers, source links/fragment, resolution explanation, and one or more `monster_refs`. Multiple refs represent a real “any” choice, not fuzzy ambiguity.

Historical batch state stores the exact explicit printable entity sequence plus each entity's logical starting page. Canonical records remain separate; their later re-import cannot change an already rendered segment.

## Tests and verification

Normal tests are entirely fixture/local and do not depend on either remote site:

```bash
.venv/bin/pytest
```

Compact sanitized fixtures cover class/level discovery, complete spell parsing, table spans and footnotes, summon qualifiers, Dire Bat, the Dire Rat multi-column base/fiendish split, multi-table retention, and resolution. The PDF test commits two batches and proves TOC coverage, numbering continuation, exact append bytes, summon placement, and historical content-stream stability.

Live verification performed on 2026-09-02 produced:

- Warmage levels 0–9: `4, 18, 14, 11, 13, 10, 8, 8, 7, 6` links/records; 99 unique records, zero missing names or descriptions, and no detected chrome leakage.
- Summons: 18 pages, 208 table entries, 87 linked monster pages, 202 canonical statblocks, 253 resolved printable references after legitimate “any” expansion, and zero unresolved entries.
- Dire Bat: one statblock; Dire Rat: separate `dire-rat` and `fiendish-dire-rat` records. Summon Monster I selects the fiendish record.
- PDF live-data dry run: batch 1 pages 1–4; batch 2 pages 5–27 with 14 Summon Monster I statblocks; 27 content streams verified, unchanged batch-1 hash, and byte-identical batch-2 append/segment files.

These counts are observations, not hard-coded expectations. Remote content can change.

## Troubleshooting and limitations

- If a remote host times out or returns a transient 5xx/429 response, retry later or increase `--timeout`; retry/backoff is already limited and automatic.
- A semantic markup change may produce an explicit “heading/table/content region not found” failure. Add a compact regression fixture before adjusting the parser.
- Rebuilding an index updates canonical records but never rewrites committed PDFs. Begin a new batch to print revised canonical content.
- Where d20srd supplies a celestial/fiendish column, resolution selects it. If the linked SRD page supplies only the base statblock, the exact summon display/template remains in metadata and the resolution report says that the base SRD statblock was used.
- Imports run synchronously in the local server process. The page waits during large class imports; the CLI gives clearer progress for those jobs.
- Core PDF fonts are deliberately used for reproducible local rendering. The supported source punctuation is preserved, but this is a utilitarian layout rather than a typography system.

Do not commit `runtime/`, virtual environments, generated PDFs/indexes, or scraped source data. `.gitignore` covers the standard paths.
