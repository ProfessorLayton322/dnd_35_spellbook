from pathlib import Path

from spellbook_builder.arkal import parse_class_page, parse_level_page, parse_spell_page
from spellbook_builder.srd import parse_monster_page, parse_summon_page, resolve_entry
from spellbook_builder.tables import table_to_text


FIXTURES = Path(__file__).parent / "fixtures"


def fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


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
