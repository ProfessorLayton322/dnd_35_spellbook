from pathlib import Path

import pytest

from spellbook_builder.arkal import fetch_level_links, fetch_spell_record, import_class, parse_class_page, parse_level_page, parse_spell_page
from spellbook_builder.fetch import FetchError
from spellbook_builder.srd import build_summon_index, parse_monster_page, parse_summon_page, resolve_entry, summon_urls
from spellbook_builder.tables import table_to_text


FIXTURES = Path(__file__).parent / "fixtures"


def fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def spell_page(name: str, book: str = "Player's Handbook v.3.5") -> str:
    return fixture("flaming_sphere.html").replace("Flaming Sphere", name).replace("Player's Handbook v.3.5", book)


def test_table_to_plain_text_expands_spans_and_links():
    html = """<table><tr><th>Band</th><th>Effect</th></tr><tr><td rowspan='2'><a href='/x'>Low</a></td><td>A</td></tr><tr><td>B</td></tr><tfoot><tr><td colspan='2'><ol><li>Foot note.</li></ol></td></tr></tfoot></table>"""
    text = table_to_text(html)
    assert "- Band: Low" in text
    assert text.count("- Band: Low") == 2
    assert "  - Effect: B" in text
    assert "- Footnote 1: Foot note." in text
    assert "<" not in text and "|" not in text


def test_class_level_discovery_and_spell_links():
    root = "https://dnd.arkalseif.info/classes/fixture-mage/index.html"
    parsed = parse_class_page(fixture("arkal_class.html"), root)
    assert parsed["class_name"] == "Fixture Mage"
    assert list(parsed["levels"]) == [0, 2]
    links = parse_level_page(fixture("arkal_level.html"), parsed["levels"][2])
    assert [item["name"] for item in links] == ["Flame Orb", "Frost Ray"]
    assert links[0]["url"].startswith("https://dnd.arkalseif.info/spells/")


class FixtureFetcher:
    def __init__(self, pages: dict[str, str]):
        self.pages = pages
        self.requested: list[str] = []

    def get(self, url: str) -> str:
        self.requested.append(url)
        result = self.pages[url]
        if isinstance(result, Exception):
            raise result
        return result


def test_arkalseif_static_level_uses_complete_working_listing():
    source = "https://dnd.arkalseif.info/classes/fixture-mage/spells-level-2/index.html"
    complete = "https://dndtools.org/classes/fixture-mage/spells-level-2/?page_size=1000"
    fetcher = FixtureFetcher({complete: fixture("arkal_level_complete.html")})
    links = fetch_level_links(source, fetcher)
    assert fetcher.requested == [complete]
    assert [item["name"] for item in links] == ["Flame Orb", "Frost Ray", "Storm Bolt"]
    assert all(item["url"].startswith("https://dndtools.org/spells/") for item in links)


def test_level_pagination_is_followed_and_deduplicated():
    source = "https://example.test/classes/fixture-mage/spells-level-2/index.html"
    first = source + "?page_size=1000"
    second = source + "?page=2"
    fetcher = FixtureFetcher(
        {
            first: fixture("arkal_level_page_1.html"),
            second: fixture("arkal_level_page_2.html"),
        }
    )
    links = fetch_level_links(source, fetcher)
    assert fetcher.requested == [first, second]
    assert [item["name"] for item in links] == ["Flame Orb", "Frost Ray", "Storm Bolt"]


def test_every_printing_of_a_spell_name_shares_one_id():
    core = parse_spell_page(spell_page("Acid Splash"), "https://dnd.arkalseif.info/spells/players-handbook-v35--6/acid-splash--2373/index.html")
    reprint = parse_spell_page(spell_page("Acid splash*", "Magic of Faerun"), "https://dndtools.org/spells/magic-of-faerun--20/acid-splash--1604/")
    assert core["id"] == reprint["id"] == "arkal-acid-splash"


def test_incomplete_working_spell_falls_back_to_static_mirror():
    dynamic = "https://dndtools.org/spells/players-handbook-v35--6/detect-magic--2489/"
    static = "https://dnd.arkalseif.info/spells/players-handbook-v35--6/detect-magic--2489/index.html"
    fetcher = FixtureFetcher(
        {
            dynamic: fixture("detect_magic_incomplete.html"),
            static: fixture("detect_magic_complete.html"),
        }
    )

    record = fetch_spell_record(dynamic, fetcher)

    assert fetcher.requested == [dynamic, static]
    assert record["id"] == "arkal-detect-magic"
    assert record["source_url"] == static
    assert record["content_blocks"][0]["text"].startswith("You detect magical auras")


def test_class_import_reports_spell_and_completed_level_progress():
    root = "https://dnd.arkalseif.info/classes/fixture-mage/index.html"
    level_urls = [
        f"https://dndtools.org/classes/fixture-mage/spells-level-{level}/?page_size=1000"
        for level in (0, 2)
    ]
    spell_urls = {
        f"https://dndtools.org/spells/book--1/{slug}--{number}/": name
        for slug, number, name in (("flame-orb", 10, "Flame Orb"), ("frost-ray", 11, "Frost Ray"), ("storm-bolt", 12, "Storm Bolt"))
    }
    pages = {root: fixture("arkal_class.html")}
    pages.update({url: fixture("arkal_level_complete.html") for url in level_urls})
    pages.update({url: spell_page(name) for url, name in spell_urls.items()})
    events: list[dict] = []

    class_record, spells = import_class(root, FixtureFetcher(pages), on_event=events.append)

    assert list(class_record["levels"]) == ["0", "2"]
    assert len(spells) == 3
    assert events[0] == {
        "type": "class_discovered",
        "class_name": "Fixture Mage",
        "completed_levels": 0,
        "total_levels": 2,
        "downloaded_spells": 0,
        "total_spells": None,
    }
    downloads = [event for event in events if event["type"] == "spell_downloaded"]
    completions = [event for event in events if event["type"] == "level_completed"]
    assert [event["downloaded_spells"] for event in downloads] == list(range(1, 7))
    assert [event["completed_levels"] for event in completions] == [1, 2]
    assert completions[-1]["total_spells"] == 6


def test_class_import_skips_only_missing_spell_pages():
    root = "https://dnd.arkalseif.info/classes/fixture-mage/index.html"
    level_urls = [
        f"https://dndtools.org/classes/fixture-mage/spells-level-{level}/?page_size=1000"
        for level in (0, 2)
    ]
    spell_urls = {
        slug: f"https://dndtools.org/spells/book--1/{slug}--{number}/"
        for slug, number in (("flame-orb", 10), ("frost-ray", 11), ("storm-bolt", 12))
    }
    pages = {root: fixture("arkal_class.html")}
    pages.update({url: fixture("arkal_level_complete.html") for url in level_urls})
    pages[spell_urls["flame-orb"]] = spell_page("Flame Orb")
    pages[spell_urls["frost-ray"]] = FetchError("missing", status_code=404)
    pages[spell_urls["storm-bolt"]] = spell_page("Storm Bolt")
    events: list[dict] = []

    class_record, spells = import_class(root, FixtureFetcher(pages), on_event=events.append)

    assert all(len(ids) == 2 for ids in class_record["levels"].values())
    assert len(spells) == 2
    skipped = [event for event in events if event["type"] == "spell_skipped"]
    assert len(skipped) == 2
    assert skipped[-1]["skipped_spells"] == 2
    assert events[-1]["processed_spells"] == events[-1]["total_spells"] == 6


def test_class_import_keeps_one_printing_per_spell_name():
    root = "https://dnd.arkalseif.info/classes/fixture-mage/index.html"
    level_zero, level_two = (
        f"https://dndtools.org/classes/fixture-mage/spells-level-{level}/?page_size=1000"
        for level in (0, 2)
    )
    listing = fixture("arkal_level_complete.html")
    frost_ray_row = '<tr><td><a href="/spells/book--1/frost-ray--11/">Frost Ray</a></td><td>Evocation</td></tr>'
    spell_url = "https://dndtools.org/spells/book--1/{}/"
    pages = {
        root: fixture("arkal_class.html"),
        level_zero: listing,
        # Level 2 lists only the reprint, not the preferred core printing.
        level_two: listing.replace(frost_ray_row, "").replace("total 3 items", "total 2 items"),
        spell_url.format("flame-orb--10"): spell_page("Acid Splash", "Magic of Faerun"),
        spell_url.format("frost-ray--11"): spell_page("Acid Splash"),
        spell_url.format("storm-bolt--12"): spell_page("Storm Bolt"),
    }

    class_record, spells = import_class(root, FixtureFetcher(pages))

    assert class_record["levels"] == {"0": ["arkal-acid-splash", "arkal-storm-bolt"], "2": ["arkal-storm-bolt"]}
    assert set(spells) == {"arkal-acid-splash", "arkal-storm-bolt"}
    assert spells["arkal-acid-splash"]["source_book"] == "Player's Handbook v.3.5"
    assert spells["arkal-acid-splash"]["imported_from_classes"] == [{"class_name": "Fixture Mage", "spell_level": 0}]
    assert [item["spell_level"] for item in spells["arkal-storm-bolt"]["imported_from_classes"]] == [0, 2]


def test_class_import_does_not_skip_non_404_fetch_errors():
    root = "https://dnd.arkalseif.info/classes/fixture-mage/index.html"
    level_urls = [
        f"https://dndtools.org/classes/fixture-mage/spells-level-{level}/?page_size=1000"
        for level in (0, 2)
    ]
    first_spell = "https://dndtools.org/spells/book--1/flame-orb--10/"
    pages = {root: fixture("arkal_class.html"), first_spell: FetchError("unavailable", status_code=503)}
    pages.update({url: fixture("arkal_level_complete.html") for url in level_urls})

    with pytest.raises(FetchError, match="unavailable"):
        import_class(root, FixtureFetcher(pages))


def test_spell_page_is_refined_lossless_and_tables_are_text():
    record = parse_spell_page(fixture("flaming_sphere.html"), "https://dnd.arkalseif.info/spells/book--1/flaming-sphere--10/index.html")
    assert record["name"] == "Flaming Sphere"
    assert record["source_page"] == 232
    assert record["school"] == "Evocation" and record["subschool"] == "Creation" and record["descriptors"] == ["Fire"]
    assert record["effect"] == "5-ft. sphere" and record["target"] is None
    assert record["levels"][1] == {"class": "Warmage", "level": 2, "notes": "Fire"}
    assert record["body_sections"][-1]["heading"] == "Arcane Material Component"
    assert record["tables"] == ["- Damage: 1-5\n  - Result: A"]
    assert all("Navigation" not in block["text"] for block in record["content_blocks"])


def test_monster_multi_variant_and_multiple_tables():
    page = parse_monster_page(fixture("dire_rat.html"), "https://www.d20srd.org/srd/monsters/direRat.htm")
    assert [block["id"] for block in page["statblocks"]] == ["dire-rat::dire-rat", "dire-rat::fiendish-dire-rat"]
    assert page["statblocks"][0]["fields"]["Organization"] == "Solitary or pack"
    assert page["statblocks"][1]["fields"]["Organization"] == "Solitary or pack"
    assert page["statblocks"][1]["fields"]["Special Attacks"] == "Disease, smite good"
    auxiliary = [block for block in page["page_sections"] if block["type"] == "table"]
    assert len(auxiliary) == 1 and "Effect: Example" in auxiliary[0]["text"]
    assert "Commonly summoned." not in " ".join(block["text"] for block in page["statblocks"][0]["sections"])
    assert "Commonly summoned." in " ".join(block["text"] for block in page["statblocks"][1]["sections"])
    bat = parse_monster_page(fixture("dire_bat.html"), "https://www.d20srd.org/srd/monsters/direBat.htm")
    assert bat["statblocks"][0]["fields"]["Hit Dice"] == "4d8+12 (30 hp)"


def test_summon_index_build_reports_table_and_creature_page_progress():
    creature = fixture("dire_rat.html")
    pages = {url: fixture("summon_monster_i.html") for _family, _level, url in summon_urls()}
    pages["https://www.d20srd.org/srd/monsters/direRat.htm"] = creature
    pages["https://www.d20srd.org/srd/monsters/dog.htm"] = creature
    events: list[dict] = []

    build_summon_index(FixtureFetcher(pages), on_event=events.append)

    tables = [event for event in events if event["type"] == "summon_table_parsed"]
    assert [event["parsed_tables"] for event in tables] == list(range(1, 19))
    assert all(event["total_tables"] == 18 and event["total_monster_pages"] is None for event in tables)
    assert events[len(tables)]["type"] == "monster_download_started"
    assert events[len(tables)]["total_monster_pages"] == 2
    assert [(event["type"], event["downloaded_monster_pages"], event["total_monster_pages"]) for event in events[len(tables) + 1 :]] == [
        ("monster_page_downloaded", 1, 2),
        ("monster_page_downloaded", 2, 2),
    ]


def test_summon_lists_and_name_resolution():
    source = "https://www.d20srd.org/srd/spells/summonMonsterI.htm"
    summon = parse_summon_page(fixture("summon_monster_i.html"), source, "summon_monster", 1)
    assert len(summon["entries"]) == 2
    fiendish = summon["entries"][1]
    assert fiendish["template"] == "Fiendish" and fiendish["alignment"] == "LE"
    assert fiendish["notes"] == ["Aquatic environments only."]
    page = parse_monster_page(fixture("dire_rat.html"), "https://www.d20srd.org/srd/monsters/direRat.htm")
    ref, reason = resolve_entry(fiendish, page)
    assert ref == "dire-rat::fiendish-dire-rat" and reason.startswith("exact")
    nature = parse_summon_page(fixture("summon_natures_ally_i.html"), "https://www.d20srd.org/srd/spells/summonNaturesAllyI.htm", "summon_natures_ally", 1)
    assert nature["entries"][0]["notes"] == ["animal"]
    assert nature["entries"][1]["alignment"] == "NG" and nature["entries"][1]["notes"] == ["trained"]
