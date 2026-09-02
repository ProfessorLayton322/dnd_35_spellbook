# Codex 5.6 Sol xhigh Implementation Prompt — Local D&D 3.5 Spellbook Builder

You are Codex 5.6 Sol running at xhigh reasoning effort. Build the complete project described below in the current repository.

Do not stop at an architectural sketch or partial prototype. Implement the project end-to-end, run the required dry runs/tests, fix failures you encounter, and commit all changes to git.

## Primary goal

Build a local-first browser application for creating printable D&D 3.5 spellbooks from data scraped from:

1. `https://dnd.arkalseif.info/` for class spell lists and full spell data.
2. `https://www.d20srd.org/srd/` for Summon Monster / Summon Nature's Ally creature lists and monster statblocks.

The application must preserve spellbooks as **append-only batches** so previously printed spell pages never change when a new batch is added.

The implementation consists of three logical subsystems:

1. Arkalself class/spell parser.
2. d20srd summon/monster parser.
3. Localhost browser app, persistent local JSON state, and PDF generation/export.

All user-generated/runtime data must be stored locally outside tracked repository data and must be ignored by git.

---

# 1. General engineering requirements

## 1.1 Technology choice

Choose a simple, maintainable stack appropriate for a local-only application.

Prefer Python unless there is a compelling reason not to, because the project requires:
- HTML fetching/parsing,
- structured local JSON storage,
- PDF generation,
- a lightweight localhost server,
- deterministic CLI dry runs/tests.

A reasonable default is:
- Python 3.12+
- FastAPI or Flask
- BeautifulSoup/lxml
- requests/httpx
- Jinja2 or minimal server-rendered HTML
- lightweight vanilla JavaScript only where useful
- a deterministic PDF library such as ReportLab, WeasyPrint, or another suitable library

Avoid a heavy SPA framework unless it materially simplifies the task.

## 1.2 Repository layout

Use a clean structure such as:

```text
src/
  app/
  parsers/
  models/
  storage/
  pdf/
  services/
templates/
static/
tests/
scripts/
runtime/            # ignored by git
README.md
pyproject.toml
.gitignore
```

The exact structure may differ, but keep parsing, storage, application, and PDF logic cleanly separated.

## 1.3 Runtime/user data

All imported/generated user data must live in a configurable local runtime/data directory, for example:

```text
runtime/
  state.json
  indexes/
    spells.json
    monsters.json
    summon_lists.json
  spellbooks/
    <spellbook-id>/
      spellbook.json
      full.pdf
      exports/
        ...
```

Requirements:
- The runtime directory MUST be gitignored.
- Do not commit scraped spell data, user state, generated PDFs, or generated monster indexes.
- Provide an environment variable or CLI option to override the runtime directory.
- Use atomic JSON writes where practical: write to a temporary file then rename.
- Store UTF-8 JSON with human-readable indentation.
- Keep schemas versioned so future migrations are possible.

## 1.4 Reliability

The scrapers must:
- use a descriptive User-Agent;
- have configurable request timeouts;
- have limited retries/backoff for transient failures;
- not hammer remote sites;
- optionally sleep between page fetches during large imports;
- resolve relative URLs correctly;
- normalize whitespace without destroying meaningful paragraph/list structure;
- produce actionable errors rather than silently dropping content;
- support re-running imports without duplicating identical records;
- retain source URLs and fetch timestamps in parsed records.

Do not make brittle assumptions based only on exact CSS classes. Prefer semantic structure, headings, links, labels, tables, and robust fallbacks.

## 1.5 Testing

Add automated tests for:
- table-to-plain-text conversion;
- spell-page parsing using saved HTML fixtures;
- class-level spell-list discovery using fixtures;
- d20srd monster table parsing;
- multi-column/multi-variant statblocks;
- summon-list parsing;
- name normalization and summon-to-statblock resolution;
- batch append-only behavior;
- PDF page numbering;
- TOC generation;
- diff export invariants.

Network-dependent integration tests should be separate from unit tests and should not make normal test runs flaky.

---

# 2. Arkalself spell parser

Base site:

`https://dnd.arkalseif.info/`

Example class page:

`https://dnd.arkalseif.info/classes/warmage/index.html`

Example level page:

`https://dnd.arkalseif.info/classes/warmage/spells-level-2/index.html`

Example spell page:

`https://dnd.arkalseif.info/spells/players-handbook-v35--6/flaming-sphere--2615/index.html`

## 2.1 Class page import

The parser accepts an Arkalself class-page URL.

Example:

```text
https://dnd.arkalseif.info/classes/warmage/index.html
```

From that page:
1. identify the class name;
2. find the class spell-list section;
3. discover every spell-level link exposed for that class;
4. derive the numeric spell level from either anchor text or URL;
5. fetch every discovered level page;
6. parse all spell links from each level page;
7. fetch each spell page;
8. parse the complete spell data into the spell index;
9. record which imported class and spell level contains each spell.

The implementation must work for classes other than Warmage when their page follows the same site conventions.

Do not hard-code Warmage-specific spell URLs.

## 2.2 Spell record requirements

A spell JSON record must preserve all meaningful spell information found on the page.

Use an explicit schema. A suggested shape:

```json
{
  "id": "stable-normalized-id",
  "name": "Flaming Sphere",
  "source_url": "...",
  "source_book": "Player's Handbook v.3.5",
  "source_page": 232,
  "school": "Evocation",
  "subschool": null,
  "descriptors": ["Fire"],
  "levels": [
    {"class": "Druid", "level": 2, "notes": null},
    {"class": "Sorcerer", "level": 2, "notes": null},
    {"class": "Wizard", "level": 2, "notes": null},
    {"class": "Warmage", "level": 2, "notes": null}
  ],
  "components": ["V", "S", "M", "DF"],
  "casting_time": "1 standard action",
  "range": "Medium (100 ft. + 10 ft./level)",
  "target": null,
  "area": null,
  "effect": "5-ft.-diameter sphere",
  "duration": "1 round/level",
  "saving_throw": "Reflex negates",
  "spell_resistance": "Yes",
  "body_sections": [
    {
      "heading": null,
      "paragraphs": ["..."]
    },
    {
      "heading": "Arcane Material Component",
      "paragraphs": ["..."]
    }
  ],
  "tables": [],
  "raw_labels": {},
  "imported_from_classes": [
    {"class_name": "Warmage", "spell_level": 2}
  ],
  "fetched_at": "ISO-8601 timestamp"
}
```

This is a suggested schema, not a requirement to use these exact keys. The final schema must be explicit, documented, stable, and lossless for the relevant page information.

### Accuracy requirement

The spell index must be refined and accurate:
- strip navigation, footer, comments, social widgets, ads, and other site chrome;
- do not accidentally mix unrelated class-page content into spell descriptions;
- preserve paragraph boundaries;
- preserve section headings;
- correctly distinguish target, area, and effect if present;
- preserve component details and material/focus text;
- preserve book/page metadata where present;
- preserve class-level mappings from the spell page;
- preserve imported class-level membership separately from global spell-level mappings.

## 2.3 HTML table conversion

Any table found inside content that belongs to a spell must be converted into **plain text bullet-list form before being stored in JSON**.

Do not store HTML tables.

Do not save Markdown pipe tables.

Example transformation:

HTML conceptually equivalent to:

```text
Damage | Result
1-5    | A
6-10   | B
```

should become something like:

```text
- Damage: 1-5
  - Result: A
- Damage: 6-10
  - Result: B
```

or another deterministic multiline bullet representation.

Requirements:
- retain headers;
- retain all rows/cells;
- preserve row relationships;
- handle colspan/rowspan reasonably;
- normalize nested links to text;
- retain footnotes where relevant;
- unit-test this conversion.

If a spell page has multiple tables, preserve them as distinct ordered table/list blocks.

---

# 3. d20srd summon and monster parser

Base monster index:

`https://www.d20srd.org/srd/monsters/`

Example pages:

`https://www.d20srd.org/srd/monsters/direBat.htm`

`https://www.d20srd.org/srd/monsters/direRat.htm`

Summon spell examples:

`https://www.d20srd.org/srd/spells/summonMonsterI.htm`

`https://www.d20srd.org/srd/spells/summonNaturesAllyI.htm`

## 3.1 Purpose

Create a local index containing exactly the monster/statblock data needed by:

- Summon Monster I–IX
- Summon Nature's Ally I–IX

The parser should derive the summonable creature lists from d20srd summon spell pages rather than relying on a manually typed list.

Parse all nine levels of each spell family.

The resulting summon index must be able to answer:

```text
Which creatures/statblocks must be appended when this particular summon spell is put in a spellbook?
```

## 3.2 Summon-list records

Create explicit structured records for summon lists, for example:

```json
{
  "spell_family": "summon_monster",
  "spell_level": 1,
  "source_url": "...",
  "entries": [
    {
      "display_name": "Fiendish dire rat",
      "base_creature_name": "Dire Rat",
      "template": "Fiendish",
      "alignment": "LE",
      "notes": [],
      "monster_ref": "dire-rat::fiendish-dire-rat"
    }
  ]
}
```

For Summon Nature's Ally retain qualifiers such as:
- `(animal)`;
- aquatic-only footnotes;
- alignment/notes if present;
- size/type variants.

For Summon Monster retain:
- celestial/fiendish or other template qualifiers;
- alignment;
- aquatic-only footnotes;
- exact summon table display name.

## 3.3 Link resolution

Prefer links already present in the summon tables to locate monster pages.

Do not rely solely on fuzzy string guessing.

However, some summon entries may point into a monster page that contains several creatures/variants. Implement deterministic resolution using:
1. the summon-table link target;
2. anchor fragments where available;
3. normalized display name;
4. template name such as Celestial/Fiendish;
5. statblock column headings or section headings;
6. an explicit alias/resolution table only for genuine exceptional cases.

Any unresolved summon entry must be reported in a validation report and should cause the summon-index verification command to fail unless explicitly whitelisted with a documented reason.

## 3.4 Monster statblock parsing

Parse meaningful monster content from d20srd pages:
- creature/statblock name;
- source URL;
- all top-level stat fields;
- combat sections;
- special attacks;
- special qualities;
- abilities;
- saves;
- skills;
- feats;
- environment;
- organization;
- CR;
- treasure;
- alignment;
- advancement;
- level adjustment;
- descriptive paragraphs;
- subheadings and ability descriptions;
- any other relevant SRD statblock information on the page.

Exclude site navigation, footer, unrelated widgets, and licensing boilerplate from the printable statblock body, while retaining the source URL in metadata.

## 3.5 Multiple tables and multi-variant pages

If a page contains multiple relevant tables, each table MUST be:
- transformed independently into multiline plain-text bullet-list form;
- stored as a different ordered list/table block;
- retained inside the same monster JSON record/file.

Do not concatenate unrelated tables into one flat list.

Additionally, monster pages can contain a single comparison table with multiple named statblock columns.

The Dire Rat page is an important example: the main table can represent both:
- Dire Rat
- Fiendish Dire Rat

The parser must preserve these variants as separately addressable statblocks even when they originate from the same HTML table/page.

A good internal representation might be:

```json
{
  "page_id": "dire-rat",
  "source_url": ".../direRat.htm",
  "statblocks": [
    {
      "id": "dire-rat::dire-rat",
      "name": "Dire Rat",
      "fields": {...},
      "tables": [...]
    },
    {
      "id": "dire-rat::fiendish-dire-rat",
      "name": "Fiendish Dire Rat",
      "fields": {...},
      "tables": [...]
    }
  ],
  "shared_sections": [...]
}
```

Use a better schema if appropriate, but variants must not overwrite one another.

## 3.6 Template correctness

Summon Monster commonly summons templated creatures such as "Celestial dog" or "Fiendish dire rat".

Do not silently print only the untemplated base animal if d20srd provides a corresponding templated statblock or applicable templated column.

Use the statblock version that corresponds to the summon-table entry whenever the SRD page provides one.

When a page supplies shared prose but separate statblock columns, preserve both the variant-specific fields and shared prose necessary to understand the creature.

---

# 4. Browser localhost application

Build a plain, simple, utilitarian UI.

No need for elaborate visual design.

The application should run locally with a documented command such as:

```bash
python -m ...
```

or:

```bash
uv run ...
```

Then open at a localhost URL.

## 4.1 Persistent app state

Store application state in local JSON.

At minimum, the state should track:
- schema version;
- imported Arkalself classes;
- class → level → spell IDs;
- spell index metadata;
- summon/monster index metadata;
- spellbooks;
- active/in-progress batch where appropriate;
- generated PDF/export metadata.

Do not rely on browser localStorage as the primary source of truth.

The server-side local JSON state is authoritative.

## 4.2 Import class spell lists

The UI must contain a way to:
1. paste an Arkalself class page URL;
2. start import;
3. show success/failure;
4. show discovered class name;
5. show spell counts grouped by spell level.

Re-import should update/merge safely without multiplying duplicates.

## 4.3 Create a spellbook

Allow the user to create a new empty named spellbook.

Validate names.

Use stable internal IDs separate from human-readable names.

A spellbook is NOT a flat list of spells.

It is an ordered list of immutable committed batches.

Example conceptual state:

```json
{
  "id": "my-warmage-book",
  "name": "My Warmage Book",
  "batches": [
    {
      "id": "batch-0001",
      "created_at": "...",
      "entities": [
        {"kind": "spell", "id": "..."},
        {"kind": "spell", "id": "..."},
        {"kind": "summon_statblock", "id": "..."}
      ],
      "page_start": 1,
      "page_end": 8
    },
    {
      "id": "batch-0002",
      "created_at": "...",
      "entities": [...]
    }
  ]
}
```

The exact shape may differ.

The essential invariant is that a spellbook contains committed ordered BATCHES and historical committed batch content/pages are append-only.

## 4.4 Batch workflow

Provide the following actions/buttons:

### Begin batch

Creates an in-progress batch for the selected spellbook.

Only one open batch per spellbook unless you deliberately implement a safe equivalent.

### Add a spell by name

Provide:
- text input;
- dropdown search hints;
- case-insensitive prefix matching;
- prefix matching from the beginning of the normalized spell name only;
- deterministic ordering;
- no broad substring/fuzzy matching for this feature.

Example:
- entering `fire` may suggest `Fireball`, `Fire Shield`, etc.
- entering `ball` must not suggest `Fireball` solely because "ball" occurs later.

Selecting a suggestion adds the spell to the current batch.

Avoid duplicate entities in one batch unless there is a clearly documented reason to permit them.

### Add a whole spell list for a certain class and spell level

Provide controls to select:
- imported class;
- spell level.

Examples:
- Warmage 4
- Wizard 2

Add every indexed spell associated with that imported class/level to the current batch.

Show how many spells will be/are added.

### End batch

Ending a batch commits it.

This must:
1. resolve any required summon statblocks;
2. finalize batch entity ordering;
3. paginate the batch starting on a fresh spell page;
4. append the batch to the spellbook state;
5. regenerate/update the complete PDF;
6. create two DIFF PDFs as described below;
7. persist export metadata.

Do not mutate older committed batches.

---

# 5. Summon spell expansion inside spellbooks

When a committed batch contains any spell corresponding to:

- Summon Monster I
- Summon Monster II
- ...
- Summon Monster IX

or:

- Summon Nature's Ally I
- ...
- Summon Nature's Ally IX

the spell's printable representation must be followed by printable statblocks for **all creatures corresponding to that summon spell's relevant summon list**.

This summon expansion must be based on the local d20srd summon/monster indexes.

Keep the actual spell entity followed immediately by its summon statblocks in deterministic order.

Summon statblocks count as table-of-contents entries and occupy normal spell-page-numbered pages.

Do not globally deduplicate a summon statblock out of its required location merely because the same creature appeared earlier in the spellbook. The printable semantics of "summon spell followed by all corresponding summons" take priority.

State may internally reference the same canonical monster record more than once, but the batch's printable entity sequence must remain explicit and deterministic.

---

# 6. PDF generation

PDF correctness and append-only page stability are core requirements.

## 6.1 Two logical page regions

A complete spellbook PDF is:

```text
[Table of Contents pages]
[Spell/content pages]
```

The two regions have different pagination semantics.

### Table of Contents pages

- TOC comes first.
- TOC includes ALL spell/content entities in the entire spellbook.
- Include spells.
- Include summoned monster/statblock entries.
- Show the corresponding spell-page number beside every entry.
- TOC can span multiple physical PDF pages.
- TOC page count may change after batches are added.
- TOC pages must NOT affect spell-page numbering.

### Spell/content pages

- Content page numbering starts at **1**.
- Printed page numbers for spell/content pages are independent from physical PDF page indices.
- Each new committed batch MUST begin on a new content page.
- No newly added batch may cause any older content batch's content-page numbers or page layout to change.

This last invariant is fundamental.

## 6.2 Spell rendering

Each spell representation must contain all relevant data saved in the spell JSON index.

Render:
- title;
- school/subschool/descriptors;
- class-level mappings;
- components;
- casting time;
- range;
- target/area/effect;
- duration;
- saving throw;
- spell resistance;
- source book/page if available;
- all body paragraphs/sections;
- converted table/list content;
- material/focus sections and other spell-specific details.

Do not truncate long descriptions.

Allow a spell to flow across multiple pages.

## 6.3 Monster rendering

Render the full selected summon statblock data required to use the creature at the table.

Include:
- summon display name if it differs from statblock name;
- template/variant;
- core statblock fields;
- abilities and combat text;
- all parsed relevant sections;
- converted table/list content;
- source URL in unobtrusive metadata/footer if useful.

Allow statblocks to span pages.

## 6.4 Stable committed-batch rendering

The system must guarantee:

> Once batch N is committed, adding batch N+1 does not alter the PDF bytes/layout/page boundaries of batch N's content-page segment, except that those bytes may appear at a later physical PDF index if the TOC grows. Their logical content pages and rendering must remain identical.

Design for this explicitly.

Recommended approach:
- render each committed batch's content pages as its own immutable PDF segment;
- store the rendered batch segment locally;
- assign and persist its logical start/end content page numbers at commit time;
- generate a new full PDF by concatenating:
  1. freshly rendered TOC PDF
  2. immutable batch segment PDFs in order

Do NOT regenerate historical batch content from current templates every time if doing so could change pagination.

If you choose another approach, it must enforce the same invariant.

Changing PDF template/layout settings after batches exist must not silently rewrite old segments. Either:
- version the renderer and preserve historical renderer output, or
- refuse incompatible renderer changes.

## 6.5 Page number placement

Content-page number printed on each spell/statblock page must reflect logical content numbering starting at 1.

TOC pages should use either:
- no page number, or
- separate Roman/TOC numbering,

but in all cases TOC physical pages must not shift the logical numbers shown for content entries.

---

# 7. Append-only export and DIFF PDFs

Every time a batch is committed, create:

1. Updated full spellbook PDF.
2. TOC DIFF PDF.
3. Append DIFF PDF.

Use unambiguous timestamp/batch-based filenames.

Example:

```text
exports/
  batch-0003/
    toc.pdf
    append.pdf
    manifest.json
full.pdf
```

## 7.1 TOC DIFF PDF

The first DIFF file must contain the complete newly updated Table of Contents only.

It is intended to replace/reprint the old TOC, because TOC pages can change as the spellbook grows.

Therefore:
- include ALL spell/statblock TOC entries from all committed batches;
- include their stable logical content page numbers;
- contain no spell/content pages.

## 7.2 Append DIFF PDF

The second DIFF file must contain ONLY the new content pages introduced by the just-committed batch.

It must NOT contain:
- TOC pages;
- any historical content page.

Its page artwork should be byte-for-byte or render-equivalent to the corresponding new content pages in the full spellbook.

The printed logical page numbers in this append PDF must continue from the previous spellbook page count, not restart at 1.

Example:
- before batch: content pages 1–17;
- newly committed batch produces 6 pages;
- append diff contains six PDF pages printed as 18–23;
- updated full PDF contains TOC + historical content pages 1–17 + identical new pages 18–23.

## 7.3 Export manifest

Write a JSON manifest for every committed batch/export containing at least:
- spellbook ID;
- batch ID;
- commit timestamp;
- TOC PDF filename;
- append PDF filename;
- full PDF filename;
- logical content page start/end;
- number of new pages;
- cumulative content page count;
- entity IDs and display titles;
- hashes of batch segment/append PDF if practical;
- renderer/schema version.

This helps validate append-only behavior.

---

# 8. State and data model invariants

Document and enforce these invariants:

1. Canonical spell records are indexed separately from spellbooks.
2. Canonical monster/statblock records are indexed separately from spellbooks.
3. Spellbooks reference canonical entities but preserve immutable committed batch sequences.
4. Imported class spell lists map class+level to canonical spell IDs.
5. Historical batch PDF segments are immutable.
6. New batch content always begins on a new content page.
7. Logical content page numbers never change after commit.
8. TOC may change after every batch.
9. The full PDF is reconstructed from fresh TOC + immutable historical batch segments.
10. DIFF append contains only the newest batch segment.
11. No user/runtime data is checked into git.
12. A state write interrupted halfway must not corrupt the last valid state.
13. Spell and monster IDs must be stable across re-imports.

---

# 9. User interface details

Keep the UI plain and simple.

Suggested screen:

```text
Spellbook Builder

[Data]
Arkalself class URL: [________________________] [Import]
Imported classes:
- Warmage: levels 0-9, 123 spells
...

[Spellbooks]
[Create new spellbook]
Name: [____________] [Create]

Selected: My Warmage Book
Committed batches: 2
Content pages: 17

[Batch]
[Begin batch]

Spell:
[fir_________________]  <- prefix autocomplete dropdown
[Add selected spell]

Class: [Warmage v]
Level: [4 v]
[Add entire class-level list]

Current batch:
1. Fireball
2. ...
[remove]
...

[End batch]

[Exports]
Full PDF: ...
Latest TOC diff: ...
Latest append diff: ...
```

Disable or hide controls when their action is invalid.

Show clear validation/error messages.

No authentication is needed; it is localhost-only.

Bind to `127.0.0.1` by default, not all interfaces.

---

# 10. CLI/admin commands

In addition to the browser app, provide CLI commands or scripts useful for debugging and verification.

At minimum:

```text
import-class <arkalself-class-url>
build-summon-index
validate-indexes
list-spells [--prefix ...]
render-spellbook <id>
verify-append-only <id>
```

Names may differ.

Commands should use the same production code paths as the app.

---

# 11. Mandatory dry-run verification

Before considering the project complete, perform real network dry runs against the live example sites.

Network access may occasionally fail transiently. Retry responsibly. If a particular page is temporarily unavailable, use another level page plus saved fixtures and clearly document the transient failure, but make a serious attempt to execute the requested live verification.

## 11.1 Warmage parser dry run

Run a live import starting from:

`https://dnd.arkalseif.info/classes/warmage/index.html`

Verify that:
- the class is recognized as Warmage;
- level links are discovered from the class page;
- multiple spell levels are parsed;
- a substantial number of spells are parsed;
- spells are grouped by level;
- individual spell pages are fetched and parsed;
- representative spell metadata/body is non-empty and accurate;
- no obvious site navigation/footer text leaked into descriptions.

Explicitly inspect at least:
- one low-level spell;
- one middle-level spell;
- one high-level spell;
- `Flaming Sphere` if it appears in the imported Warmage list.

Print a concise dry-run summary similar to:

```text
Warmage:
  level 0: N spells
  level 1: N spells
  level 2: N spells
  ...
  total unique: N
Validation:
  missing names: 0
  missing descriptions: 0
  unresolved spell links: 0
```

Do not hard-code expected exact counts unless you first derive them from the current site.

## 11.2 Monster/summon parser dry run

Build the live summon index for Summon Monster I–IX and Summon Nature's Ally I–IX.

Verify:
- all 18 summon spell pages are processed;
- each summon level produces entries;
- summon-table links resolve to monster/statblock records;
- multiple tables remain distinct;
- multi-variant pages are handled;
- templated summons resolve to the proper statblock variant where d20srd supplies one;
- unresolved entries are listed explicitly.

Specifically inspect:
- `https://www.d20srd.org/srd/monsters/direBat.htm`
- `https://www.d20srd.org/srd/monsters/direRat.htm`

For Dire Rat, verify that the parser does not collapse the Dire Rat and Fiendish Dire Rat statblocks into one overwritten object.

## 11.3 PDF dry run

Create a temporary spellbook using live/saved parsed data.

Commit at least two batches.

Batch 1:
- multiple ordinary spells;
- preferably enough content to make several pages.

Batch 2:
- multiple spells;
- include at least one Summon Monster or Summon Nature's Ally spell so statblocks are expanded.

Verify:
- batch 2 begins on a new content page;
- logical content numbering starts at 1 after TOC;
- TOC lists all spells and all expanded summon statblocks;
- page numbers in TOC match actual logical content pages;
- full PDF is regenerated;
- TOC diff contains only current TOC;
- append diff contains only batch 2 content pages;
- historical batch 1 content-page segment is unchanged after batch 2 commit.

Add an automated or scripted hash/render comparison proving historical segment stability.

---

# 12. Fixtures

Because remote websites can change, save a minimal set of representative HTML fixtures under `tests/fixtures/`.

Fixtures may include HTML source for:
- Warmage class page;
- a Warmage spell-level list page;
- Flaming Sphere spell page;
- Dire Bat;
- Dire Rat;
- Summon Monster I;
- Summon Nature's Ally I.

Keep fixtures only for testing if legally/appropriately usable; otherwise construct compact sanitized HTML fixtures reproducing the required DOM structures.

Do NOT place the full scraped runtime indexes in the repository.

---

# 13. Documentation

Write a useful `README.md` covering:
- what the project does;
- dependencies;
- setup;
- how to run locally;
- runtime data directory;
- import workflow;
- how to build/rebuild summon index;
- how spellbooks and batches work;
- PDF append-only design;
- export files;
- CLI commands;
- testing;
- known parser limitations;
- troubleshooting remote fetch failures.

Also document the JSON schemas either in README or separate docs.

---

# 14. Git requirements

You are responsible for git hygiene.

Before finishing:
1. inspect `git status`;
2. ensure runtime data, generated indexes, generated PDFs, caches, virtual environments, and secrets are ignored;
3. run tests;
4. run mandatory verification/dry runs;
5. update docs with results or known limitations;
6. commit **all project changes**.

Use one or more sensible commits.

The final repository must end with a clean working tree, except for intentionally ignored runtime files.

Do not commit user/runtime data.

---

# 15. Definition of done

The task is done only when all of the following are true:

- [ ] Arkalself class parser accepts a class URL and discovers spell-level pages.
- [ ] It parses spells from multiple levels.
- [ ] It parses complete, refined individual spell records.
- [ ] Spell tables become ordered multiline plain-text bullet lists in JSON.
- [ ] d20srd parser derives Summon Monster I–IX lists.
- [ ] d20srd parser derives Summon Nature's Ally I–IX lists.
- [ ] Required monster/statblock pages are indexed.
- [ ] Monster tables become ordered multiline plain-text bullet lists.
- [ ] Multiple tables remain distinct.
- [ ] Multi-variant statblock pages such as Dire Rat are handled correctly.
- [ ] Templated summon entries resolve correctly where d20srd provides templated statblocks.
- [ ] Local browser app starts on localhost.
- [ ] App state persists in local JSON outside tracked repo data.
- [ ] Class spell lists can be imported through the UI.
- [ ] Empty named spellbooks can be created.
- [ ] Spellbooks are ordered lists of batches, not flat spell arrays.
- [ ] Begin Batch works.
- [ ] Spell name autocomplete uses exact normalized prefix matching.
- [ ] Individual spell can be added to a batch.
- [ ] Entire imported class+spell-level list can be added to a batch.
- [ ] End Batch commits an immutable batch.
- [ ] Summon spells are followed by all corresponding summon statblocks.
- [ ] Every committed batch starts on a fresh content page.
- [ ] Logical content page numbering starts at 1 and excludes TOC pages.
- [ ] TOC includes every spell and summon statblock with correct logical page number.
- [ ] Historical committed content pages remain stable after later batches.
- [ ] Full spellbook PDF is updated after batch commit.
- [ ] TOC DIFF PDF is produced after every batch.
- [ ] Append DIFF PDF contains only new content pages.
- [ ] Append DIFF printed page numbers continue from previous content page count.
- [ ] Export manifest is written.
- [ ] Automated tests pass.
- [ ] Warmage live dry run succeeds or any genuinely transient network failure is clearly documented after retries.
- [ ] Monster/summon live dry run succeeds or any genuinely transient network failure is clearly documented after retries.
- [ ] Two-batch PDF append-only verification succeeds.
- [ ] README is complete.
- [ ] Runtime data is gitignored.
- [ ] All implementation changes are committed.
- [ ] Final `git status` is clean aside from ignored runtime artifacts.

---

# 16. Execution style

Work autonomously.

Inspect the existing repository before choosing architecture so you do not unnecessarily replace useful existing code.

Make reasonable implementation decisions without asking me questions unless an issue is truly impossible to resolve from the requirements.

Prefer correctness, deterministic behavior, debuggability, and append-only PDF stability over visual polish.

When a parser assumption proves wrong against the live site, inspect the actual HTML, adapt the parser robustly, add a regression fixture/test, and continue.

Do not declare success merely because code was written. Run it.

At the end, report:
- architecture chosen;
- important files added/changed;
- test result summary;
- Warmage live parse summary by level;
- summon-index validation summary;
- Dire Bat/Dire Rat verification result;
- PDF two-batch append-only verification result;
- generated runtime paths;
- git commit hash(es);
- any unresolved issues.
