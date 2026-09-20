# Local D&D 3.5 Spellbook Builder

A local-first browser application and CLI for importing D&D 3.5 spells, assembling them in append-only print batches, expanding Summon Monster / Summon Nature's Ally into usable SRD statblocks, and exporting stable PDFs.

The server binds to `127.0.0.1` by default. It has no accounts and is intended for one local user. Scraped indexes, state, spellbooks, immutable PDF segments, and exports live under the ignored `runtime/` directory; none of that user/runtime data belongs in Git.

## Windows and Android apps

Ready-made apps need nothing else installed (no Python). Both run the same web UI on `127.0.0.1` (port 8735, or a free port when that one is taken), and the UI adapts to phone screens.

**Windows 10 or 11 (64-bit):** run `DnD35Spellbook-<version>-Setup.exe`. It installs for the current user without administrator rights and adds **D&D 3.5 Spellbook** (app window) and **D&D 3.5 Spellbook (web browser)** to the Start menu, plus a desktop shortcut. The installer is not code-signed, so SmartScreen may show "Windows protected your PC"; click **More info**, then **Run anyway**.

- The app window uses Microsoft Edge WebView2, which Windows 11 and updated Windows 10 include. Without it, the app opens in the default browser and shows a message box; clicking OK quits.
- The window's **Spellbook** menu has **Open in web browser**, **Show in this window**, **Open data folder**, and **Quit**. While the app is used in a browser, the window shows a notice; closing the window quits the app. PDF links open in the default browser.
- Starting the app again brings the running window forward instead of starting a second copy.
- Data is kept in `%LOCALAPPDATA%\DnD35Spellbook\runtime` and logs in `%LOCALAPPDATA%\DnD35Spellbook\logs`. Uninstalling asks whether to delete them.

**Android 7.0 or newer:** open `DnD35Spellbook-<version>.apk` on the phone and allow installing apps from that source when asked. The **⋮** menu has **Open in web browser**, **Reload**, and **Quit Spellbook**. Tapping an export offers **Open**, **Save to device…**, and **Share…**.

- The server runs as a foreground service with a notification, so a long class import continues while you use other apps. Back leaves the app running; swiping it away from recent apps quits it, unless it has been opened in a browser, in which case use **Stop** in the notification.
- Data is kept in the app's private storage and is removed when the app is uninstalled.

## Architecture

- Python 3.12, FastAPI, Jinja, and small vanilla JavaScript for the localhost UI.
- Requests with a descriptive user agent, retry/backoff, timeout, and configurable inter-request delay.
- Beautiful Soup semantic parsers for Arkalself and d20srd.
- Versioned UTF-8 JSON indexes and authoritative server state, written through atomic temporary-file replacement. Stored file references use portable `/` separators, while readers also accept state created on Windows with `\` separators.
- ReportLab for deterministic content/TOC rendering and pypdf for lossless segment assembly.
- Immutable per-batch content PDF segments. A full book is always a fresh TOC followed by the original segment files.

The main modules are:

```text
src/spellbook_builder/
  arkal.py       Arkalself class, level-list, and complete spell parsing
  srd.py         d20srd summon-table, monster page, and variant resolution
  summoning_feats.py  composable summon-feat rules and derived statblocks
  tables.py      deterministic HTML table-to-plain-text conversion
  store.py       versioned indexes/state and atomic JSON persistence
  service.py     batch workflow, summon expansion, exports, verification
  pdfgen.py      batch, TOC, and full-PDF rendering/assembly
  web.py         localhost FastAPI application
  cli.py         production CLI paths
  server.py      background-thread server for the packaged apps
  desktop.py     desktop app window / web browser launcher (Windows build entry point)
  mobile.py      Android entry points called through Chaquopy
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

To run the desktop app from a checkout, install the `desktop` extra and run `.venv/bin/spellbook-desktop` (add `--browser` for the web browser). Its data defaults to the per-user folder described above (`~/.local/share/DnD35Spellbook` on Linux, `~/Library/Application Support/DnD35Spellbook` on macOS). When pywebview cannot open a window, it uses the browser.

## Data import workflow

In the app's Data section, paste an Arkalself class URL and choose **Import class**. Then choose **Import summons** once to download the Summon Monster and Summon Nature's Ally creature statblocks. Both imports show live progress. From the CLI, run:

```bash
.venv/bin/spellbook import-class https://dnd.arkalseif.info/classes/warmage/index.html
.venv/bin/spellbook build-summon-index
.venv/bin/spellbook validate-indexes
```

Class import discovers every linked level rather than assuming levels 0–9, follows each level's spell table, fetches every spell, and merges name-derived spell IDs on re-import. Global spell class/level mappings and the imported class-list membership are separate.

Many classes link spell levels that list no spells. Ranger, for example, links levels 0–9 but has spells only at levels 1–4. A level whose spell table is empty is skipped, and the import continues with the next level. Skipped levels are not saved in the class list. After the spell lists are scanned, the level progress counts only the levels that list spells. A class that lists no spells at any level fails with an error and nothing is saved. Sublime Chord is one such class: it gets its spells from other classes' lists, and every level link on Arkalseif is empty. A level page without a spell table still stops the import, because the page markup has probably changed.

The `dnd.arkalseif.info` pages are a static mirror: their visible `?page=N` links currently return page 1 again. For level listings and spell content, the importer automatically uses Arkalseif's linked working/filter database at `dndtools.org` and requests up to 1,000 rows. If a live spell record is incomplete, its complete static-mirror page is used instead. Individual spell pages that return 404 are reported and skipped; other fetch failures still stop the import. If a listing is still paginated, every same-list page is followed and de-duplicated. The advertised total is checked so a partial import fails explicitly instead of silently stopping at 20 spells.

Spell IDs come from the spell name, so a spell is listed, suggested, and added once. Many spells are printed in several rulebooks (Acid Splash is in both *Player's Handbook v.3.5* and *Magic of Faerun*), and class listings name every printing. One printing is kept: *Player's Handbook v.3.5*, then *Spell Compendium*, then the alphabetically first other rulebook. When printings list a spell at different levels, the class list keeps it only at the kept printing's levels. Data imported with the older per-printing IDs is merged the same way on startup, and spellbook references move to the merged spell.

Class imports are committed only after every non-missing spell has been fetched. A failed commit restores the previous spell index, and startup removes orphan records and atomic-write temporary files left by interrupted imports, including imports interrupted by older versions. Records referenced by completed class imports or spellbooks are preserved.

The summon build reads all Summon Monster I–IX and Summon Nature's Ally I–IX tables. It prefers linked monster pages and anchors, then resolves named statblock columns deterministically. Qualified `(any)` entries intentionally expand to all applicable variants. An unresolved entry is written to `summon_validation.json` and makes the command fail. In the app, the import is still saved and the unresolved entries are listed in an error message.

Each base monster is imported once into the canonical index. Feat-adjusted and templated versions are derived in memory when a batch is committed; they do not create duplicate downloads or index records. Re-import summons only when the remote base data needs refreshing.

## Building a spellbook

In the browser:

1. Create and select a named empty spellbook.
2. Select every summoning feat the character has and save the selection.
3. Begin a batch.
4. Add individual spells with beginning-only, normalized, case-insensitive prefix suggestions, or add an imported class/level in one operation.
5. Remove mistakes while the batch is open.
6. End the batch to resolve summons, apply the selected feats, render its immutable segment, update the full PDF, and create the two diff exports.

Only one open batch exists per book. Duplicate spell entities in one open batch are ignored. A committed batch cannot be edited through the application.

A whole spellbook can be deleted from its heading. This removes its open batch, immutable segments, and exports. An imported class spell list can be deleted from the Data section. Its spells leave the index unless another imported class lists them or a spellbook references them. Referenced spells are removed once the last spellbook using them is deleted. Both actions ask for confirmation and cannot be undone. A delete interrupted by a crash is finished, or rolled back if it never reached `state.json`, on the next startup.

Committing a batch that contains a Summon Monster or Summon Nature's Ally spell requires the summon creatures (**Import summons** in the Data section). For a summon spell, its spell entity is immediately followed by every relevant summon statblock in table order. A statblock is not globally deduplicated away from that required location.

### Summoning feats

The spellbook-level **Character summoning feats** control supports multiple selections. The selected set is snapshotted into each committed batch and its manifest. Changing it affects the current and future open batches, never immutable batches already committed.

- **Augment Summoning** applies its +4 enhancement bonuses to Strength and Constitution and recalculates affected hit points, attacks, damage, grapple, saves, abilities, and skills.
- **Beckon the Frozen** adds the cold subtype and the +1d6 cold natural-attack rider. Fire-subtype creatures remain unchanged and their statblocks explain why the feat cannot apply.
- **Rashemi Elemental Summoning** keeps every normal air or earth elemental statblock and adds an orglash or thomil statblock beside it. The complete template adjustments and abilities are included.
- **Greenbound Summoning** applies only to animals summoned with Summon Nature's Ally. Its type, abilities, defenses, slam, spell-like abilities, healing, resistances, senses, skills, and other template changes are included.
- **Nightbringer Initiate** adds its restricted Druid 5 `summon monster V` entry after Summon Nature's Ally V and includes only the shadow mastiff. Other selected feats also apply to that shadow mastiff.

Effects compose on one derived statblock. For example, an animal with Greenbound Summoning, Beckon the Frozen, and Augment Summoning receives the summed ability changes, the greenbound template, and the frostfell changes. Rules and source links in the UI follow [D&D Tools: Augment Summoning](https://dndtools.org/feats/players-handbook-v35--6/augment-summoning--141/), [Beckon the Frozen](https://www.dndtools.org/feats/frostburn--68/beckon-the-frozen--201/), [Rashemi Elemental Summoning](https://www.dndtools.org/feats/unapproachable-east--33/rashemi-elemental-summoning--2383/), [Greenbound Summoning](https://dndtools.org/feats/lost-empires-of-faerun--30/greenbound-summoning--1317/), and [Nightbringer Initiate](https://www.dndtools.org/feats/faiths-of-eberron--8/nightbringer-initiate--3266/). The Greenbound, Orglash, and Thomil mechanics are applied from their referenced creature templates.

## PDF page layout

Content pages follow these rules (renderer 3; renderer 2 has the same flowing layout but no feat-derived entities):

- **Only a new batch starts a new page.** Each batch is rendered as its own segment, and every segment begins on a fresh content page. Nothing else forces a page break.
- **Spells and summon statblocks flow continuously.** Each printable entity begins directly after the previous one ends, on the same page when there is room. Summon statblocks follow exactly the same rule as spells, both after their summon spell and after one another.
- **Adjacent entities are separated by a horizontal black line.** The line is drawn between every two consecutive entities in a batch, whether spell or statblock. No line is drawn before a batch's first entity or after its last one.
- A long entity continues onto the next page. An entity's separator line, title, subtitle (spell school or SRD variant), and first details line are kept together; if they do not fit at the bottom of a page they move to the next page as a unit, so a line or title is never stranded at the bottom of a page.
- An entity's TOC page is the page where its title is printed.

Renderer 1 started every spell and statblock on its own page. Committed segments are immutable, so batches already rendered by renderer 1 keep that layout. A spellbook created under renderer 1 is not blocked: its next batch, and every batch after it, uses the current renderer. Each batch records the renderer that produced it in `renderer_version`, and the spellbook's `renderer_version` is updated on every commit. Committing is refused only when a spellbook records a renderer newer than the installed application.

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

Content numbering begins at 1 and is embedded while a segment is first rendered. TOC physical pages do not enter that numbering. Each later segment starts at the prior `page_end + 1`, so a new batch always starts on a fresh content page (see [PDF page layout](#pdf-page-layout)). Historical content is never re-rendered when a TOC grows.

`manifest.json` records logical ranges, page counts, entity titles/IDs, renderer version, and SHA-256 hashes. `verify-append-only` checks:

- stored immutable-segment hashes;
- contiguous logical batch ranges;
- append PDFs against their segments;
- each full-PDF content stream against the corresponding segment page.

```bash
.venv/bin/spellbook verify-append-only my-spellbook-id
.venv/bin/spellbook render-spellbook my-spellbook-id
```

`render-spellbook` only refreshes the TOC/full assembly; it does not rewrite content segments.

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

`state.json` contains `imported_classes`, index metadata, and `spellbooks`. An imported class maps string spell levels to ordered canonical spell IDs. A spellbook contains a stable ID, name, selected `summoning_feats`, renderer version (updated to the application's renderer on each commit), cumulative content-page count, an optional `open_batch`, ordered immutable `batches`, and export manifests. Each committed batch and manifest retains the exact feat-selection snapshot used to render it.

`indexes/spells.json` maps name-derived spell IDs (one per spell name) to records containing source URL/book/page, school/subschool/descriptors, global class-level mappings, components and all standard spell labels, ordered `content_blocks`, section-preserving body data, ordered plain-text tables, imported class memberships, and fetch time.

`indexes/monsters.json` maps `<page-id>::<variant-id>` to separately addressable statblocks. Each has variant fields, only the relevant/shared prose sections, its own table text, the page's distinct table list, source URL, and fetch time. Multi-column pages therefore cannot overwrite one variant with another.

`indexes/summon_lists.json` maps `<family>:<level>` to the source table and ordered entries. Each entry retains exact display text, base name, template, alignment, footnotes/qualifiers, source links/fragment, resolution explanation, and one or more `monster_refs`. Multiple refs represent a real “any” choice, not fuzzy ambiguity.

Historical batch state stores the exact explicit printable entity sequence plus each entity's logical starting page. Canonical records remain separate; their later re-import cannot change an already rendered segment.

Runtime state is relocatable as a directory between Windows, Linux, and macOS. Copy the whole `runtime/` directory, then point `--runtime` (or `DND_SPELLBOOK_RUNTIME`) at its new location. State and manifests never persist an absolute runtime path. Generated files are standard PDFs with embedded core-font references and are validated by reopening them with pypdf; they do not depend on the platform that rendered them.

## Building the Windows and Android packages

Both builds package the code in `src/` as it is and write to the ignored `dist/` directory. Bump `version` in `pyproject.toml` first; both packages read it, and the Android `versionCode` is derived from it.

### Windows installer

`packaging/windows/build.py` downloads the official Windows embeddable Python (checksum-pinned) and the wheels pinned in `packaging/windows/requirements.txt`, adds the app, and packs everything into a per-user NSIS installer. It runs on Windows, macOS, or Linux with Python 3.11+ (with pip) and NSIS 3 (`apt install nsis`, `brew install makensis`, or <https://nsis.sourceforge.io>). Running it with Python 3.13 also precompiles bytecode, which shortens the first start:

```bash
python3.13 packaging/windows/build.py    # dist/DnD35Spellbook-<version>-Setup.exe
```

The wheels are downloaded for Windows without dependency resolution, so `requirements.txt` pins every package, including indirect dependencies. The Start menu shortcuts run `python\pythonw.exe -m spellbook_builder.desktop` from the install directory.

### Android APK

`packaging/android` is a Gradle project that embeds Python 3.13 and the app with [Chaquopy](https://chaquo.com/chaquopy/). A small Java shell starts the server in a foreground service and shows it in a WebView. It needs JDK 17, the Android SDK (`ANDROID_HOME`), and `python3.13` on `PATH`:

```bash
packaging/android/build.sh    # dist/DnD35Spellbook-<version>.apk
```

The first build creates `packaging/android/release.keystore` and `keystore.properties`, which Git ignores. Back them up: Android installs an update over an existing install only when both are signed with the same key. The project also opens in Android Studio.

`pydantic-core` is compiled Rust with no Android build, so `packaging/android/app/requirements.txt` pins FastAPI 0.125.0, the last release that supports pure-Python pydantic 1.10, and Pillow 11.0.0, the newest Android build Chaquopy provides. The app does not use pydantic directly, and the test suite passes with both pinned dependency sets. Google publishes Android build tools for x86-64 Linux only; on an ARM64 Linux host, pass an ARM64 `aapt2` with `-Pandroid.aapt2FromMavenOverride=/path/to/aapt2`.

## Tests and verification

Normal tests are entirely fixture/local and do not depend on either remote site:

```bash
.venv/bin/pytest
```

Compact sanitized fixtures cover class/level discovery, complete spell parsing, table spans and footnotes, summon qualifiers, Dire Bat, the Dire Rat multi-column base/fiendish split, multi-table retention, and resolution. Summoning-feat tests cover selection validation, canonical-index immutability, recalculated Augment Summoning statistics, Greenbound/Beckon/Augment stacking, the Beckon fire exclusion, both Rashemi choices, and Nightbringer's shadow-mastiff restriction. The PDF tests commit two batches and prove TOC coverage, numbering continuation, exact append bytes, summon placement, feat snapshots, and historical content-stream stability. They also prove continuous entity flow, separator lines between adjacent entities, a fresh page for each batch, and renderer upgrades for older spellbooks.

Live verification performed on 2026-09-02 produced:

- Warmage levels 0–9: `4, 18, 14, 11, 13, 10, 8, 8, 7, 6` links/records; 99 unique records, zero missing names or descriptions, and no detected chrome leakage.
- Wizard complete level listings 0–9: `46, 291, 422, 385, 344, 308, 229, 164, 138, 136` links. Every level exceeds the static mirror's 20-row first page.
- Summons: 18 pages, 208 table entries, 87 linked monster pages, 202 canonical statblocks, 253 resolved printable references after legitimate “any” expansion, and zero unresolved entries.
- Dire Bat: one statblock; Dire Rat: separate `dire-rat` and `fiendish-dire-rat` records. Summon Monster I selects the fiendish record.
- PDF live-data dry run (renderer 1, one page per entity): batch 1 pages 1–4; batch 2 pages 5–27 with 14 Summon Monster I statblocks; 27 content streams verified, unchanged batch-1 hash, and byte-identical batch-2 append/segment files.

These counts are observations, not hard-coded expectations. Remote content can change.

## Troubleshooting and limitations

- If a remote host times out or returns a transient 5xx/429 response, retry later or increase `--timeout`; retry/backoff is already limited and automatic.
- A semantic markup change may produce an explicit “heading/table/content region not found” failure. Add a compact regression fixture before adjusting the parser.
- Rebuilding an index updates canonical records but never rewrites committed PDFs. Begin a new batch to print revised canonical content.
- Where d20srd supplies a celestial/fiendish column, resolution selects it. If the linked SRD page supplies only the base statblock, the exact summon display/template remains in metadata and the resolution report says that the base SRD statblock was used.
- Browser class imports stream live counts for scanned and completed spell levels and downloaded spells. Keep the page open until the import is saved; the CLI prints each discovered level list.
- Core PDF fonts are deliberately used for reproducible local rendering. The supported source punctuation is preserved, but this is a utilitarian layout rather than a typography system.

Do not commit `runtime/`, virtual environments, generated PDFs/indexes, or scraped source data. `.gitignore` covers the standard paths.
