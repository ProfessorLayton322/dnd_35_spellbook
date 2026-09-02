import pytest

from spellbook_builder.arkal import fetch_level_links, parse_class_page, parse_spell_page
from spellbook_builder.fetch import Fetcher
from spellbook_builder.srd import parse_monster_page, parse_summon_page


pytestmark = pytest.mark.network


def test_live_arkal_examples():
    fetcher = Fetcher(delay=0.2)
    class_url = "https://dnd.arkalseif.info/classes/warmage/index.html"
    parsed = parse_class_page(fetcher.get(class_url), class_url)
    assert parsed["class_name"] == "Warmage" and len(parsed["levels"]) > 1
    spell_url = "https://dnd.arkalseif.info/spells/players-handbook-v35--6/flaming-sphere--2615/index.html"
    spell = parse_spell_page(fetcher.get(spell_url), spell_url)
    assert spell["name"] == "Flaming Sphere" and spell["content_blocks"]


def test_live_wizard_lists_are_not_truncated_to_one_page():
    fetcher = Fetcher(delay=0.05)
    class_url = "https://dnd.arkalseif.info/classes/wizard/index.html"
    parsed = parse_class_page(fetcher.get(class_url), class_url)
    counts = {level: len(fetch_level_links(url, fetcher)) for level, url in parsed["levels"].items()}
    assert set(counts) == set(range(10))
    assert all(count > 20 for count in counts.values()), counts


def test_live_d20srd_examples():
    fetcher = Fetcher(delay=0.2)
    rat_url = "https://www.d20srd.org/srd/monsters/direRat.htm"
    rat = parse_monster_page(fetcher.get(rat_url), rat_url)
    assert {item["name"] for item in rat["statblocks"]} == {"Dire Rat", "Fiendish Dire Rat"}
    summon_url = "https://www.d20srd.org/srd/spells/summonMonsterI.htm"
    summon = parse_summon_page(fetcher.get(summon_url), summon_url, "summon_monster", 1)
    assert any(entry["display_name"] == "Fiendish dire rat" for entry in summon["entries"])
