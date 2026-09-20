from copy import deepcopy

import pytest

from spellbook_builder.service import expand_entities
from spellbook_builder.summoning_feats import materialize_summon_statblock, normalize_summoning_feats


def monster(monster_id: str, name: str, size_type: str = "Medium Animal") -> dict:
    return {
        "id": monster_id,
        "name": name,
        "fields": {
            "Size/Type": size_type,
            "Hit Dice": "2d8+4 (13 hp)",
            "Initiative": "+2",
            "Armor Class": "14 (+2 Dex, +2 natural), touch 12, flat-footed 12",
            "Base Attack/Grapple": "+1 / +2",
            "Attack": "Bite +3 melee ( 1d6+1 )",
            "Full Attack": "Bite +3 melee ( 1d6+1 )",
            "Special Attacks": "Trip",
            "Special Qualities": "Low-light vision, scent",
            "Saves": "Fort +5, Ref +5, Will +1",
            "Abilities": "Str 13, Dex 15, Con 15, Int 2, Wis 12, Cha 6",
            "Skills": "Hide +2, Listen +3, Move Silently +3, Spot +3, Survival +1",
            "Feats": "Track, Weapon Focus (bite)",
            "Environment": "Temperate forests",
            "Organization": "Solitary or pack",
            "Challenge Rating": "1",
            "Level Adjustment": "—",
        },
        "shared_sections": [{"type": "paragraph", "text": "Fixture creature."}],
        "source_url": "https://example.test/creature",
    }


def spell(spell_id: str, name: str) -> dict:
    return {"id": spell_id, "name": name}


def entry(display_name: str, ref: str, resolved_name: str | None = None) -> dict:
    return {
        "display_name": display_name,
        "monster_refs": [ref],
        "resolved_names": [resolved_name or display_name],
        "notes": [],
        "alignment": None,
    }


def test_feat_selection_is_validated_deduplicated_and_canonical_order():
    assert normalize_summoning_feats(
        ["greenbound_summoning", "augment_summoning", "greenbound_summoning"]
    ) == ["augment_summoning", "greenbound_summoning"]
    with pytest.raises(ValueError, match="Unknown summoning feat"):
        normalize_summoning_feats(["invented_feat"])


def test_augment_summoning_updates_effective_stats_without_mutating_canonical_monster():
    wolf = monster("wolf::wolf", "Wolf")
    canonical = deepcopy(wolf)

    effective, active = materialize_summon_statblock(
        wolf, ["augment_summoning"], "summon_natures_ally:1"
    )

    assert active == ["augment_summoning"]
    assert effective["fields"]["Abilities"] == "Str 17, Dex 15, Con 19, Int 2, Wis 12, Cha 6"
    assert effective["fields"]["Hit Dice"] == "2d8+8 (17 hp)"
    assert effective["fields"]["Base Attack/Grapple"] == "+1 / +4"
    assert effective["fields"]["Attack"] == "Bite +5 melee ( 1d6+3 )"
    assert effective["fields"]["Saves"] == "Fort +7, Ref +5, Will +1"
    assert wolf == canonical


def test_greenbound_augment_and_frozen_effects_stack_on_natures_ally_animals():
    effective, active = materialize_summon_statblock(
        monster("wolf::wolf", "Wolf"),
        ["beckon_the_frozen", "greenbound_summoning", "augment_summoning"],
        "summon_natures_ally:2",
    )
    fields = effective["fields"]

    assert active == ["augment_summoning", "beckon_the_frozen", "greenbound_summoning"]
    assert fields["Size/Type"] == "Medium Plant (Augmented Animal, Cold)"
    assert fields["Abilities"] == "Str 23, Dex 17, Con 23, Int 2, Wis 12, Cha 10"
    assert fields["Hit Dice"] == "2d8+12 (21 hp)"
    assert fields["Armor Class"].startswith("21 ")
    assert fields["Base Attack/Grapple"] == "+1 / +11"
    assert "Bite +8 melee ( 1d6+6 )" in fields["Attack"]
    assert "slam +7 melee (1d6+6)" in fields["Attack"]
    assert fields["Saves"] == "Fort +9, Ref +6, Will +1"
    assert fields["Beckon the Frozen Natural Attacks"] == "+1d6 cold damage on every natural attack"
    assert "wall of thorns" in fields["Greenbound Spell-Like Abilities"]
    assert "DR 10/magic and slashing" in fields["Special Qualities"]


def test_beckon_the_frozen_marks_fire_subtype_as_ineligible():
    fire = monster("elemental::fire", "Fire Elemental", "Medium Elemental (Fire, Extraplanar)")

    effective, active = materialize_summon_statblock(
        fire, ["beckon_the_frozen"], "summon_monster:5"
    )

    assert active == []
    assert effective["fields"]["Size/Type"] == "Medium Elemental (Fire, Extraplanar)"
    assert "not applied" in effective["fields"]["Applied Summoning Feats"]
    assert any("cannot gain the cold subtype" in block["text"] for block in effective["shared_sections"])


def test_ability_based_dcs_and_explicit_dexterity_skills_are_recalculated():
    rat = monster("dire-rat::dire-rat", "Dire Rat", "Small Animal")
    rat["fields"]["Skills"] = "Climb +11, Hide +8, Move Silently +4, Swim +11"
    rat["shared_sections"] = [
        {"type": "heading", "text": "Disease (Ex)"},
        {
            "type": "paragraph",
            "text": "Bite, Fortitude DC 11. The save DC is Constitution-based.",
        },
        {
            "type": "paragraph",
            "text": "Dire rats use their Dexterity modifier for Climb and Swim checks.",
        },
    ]

    effective, _active = materialize_summon_statblock(
        rat,
        ["augment_summoning", "greenbound_summoning"],
        "summon_natures_ally:1",
    )

    assert effective["fields"]["Skills"] == "Climb +12, Hide +9, Move Silently +5, Swim +12"
    assert "Fortitude DC 15" in effective["shared_sections"][1]["text"]


@pytest.mark.parametrize(
    ("subtype", "variant", "expected"),
    [("Air", "orglash", "Orglash"), ("Earth", "thomil", "Thomil")],
)
def test_rashemi_expansion_keeps_base_elemental_and_adds_alternative(subtype, variant, expected):
    elemental = monster(
        f"elemental::{subtype.casefold()}",
        f"Medium {subtype} Elemental",
        f"Medium Elemental ({subtype}, Extraplanar)",
    )
    ref = elemental["id"]
    spells = {"summon": spell("summon", "Summon Monster V")}
    lists = {
        "summon_monster:5": {
            "spell_name": "Summon Monster V",
            "entries": [entry(f"Elemental, Medium ({subtype})", ref, elemental["name"])],
        }
    }

    expanded = expand_entities(
        [{"kind": "spell", "id": "summon"}],
        spells,
        lists,
        {ref: elemental},
        ["rashemi_elemental_summoning", "augment_summoning"],
    )

    assert [item["kind"] for item in expanded] == ["spell", "summon_statblock", "summon_statblock"]
    assert [item["summon_variant"] for item in expanded[1:]] == [None, variant]
    assert expected in expanded[2]["title"]
    assert all("Augment Summoning" in item["title"] for item in expanded[1:])
    alternative, active = materialize_summon_statblock(
        elemental,
        expanded[2]["selected_summoning_feats"],
        "summon_monster:5",
        variant,
    )
    assert active == ["augment_summoning", "rashemi_elemental_summoning"]
    assert "Con 23" in alternative["fields"]["Abilities"]


def test_nightbringer_adds_restricted_spell_and_only_shadow_mastiff_after_sna_v():
    wolf = monster("wolf::wolf", "Wolf")
    shadow = monster("shadow-mastiff::shadow-mastiff", "Shadow Mastiff", "Medium Outsider (Extraplanar)")
    spells = {"sna-v": spell("sna-v", "Summon Nature's Ally V")}
    lists = {
        "summon_natures_ally:5": {
            "spell_name": "Summon Nature's Ally V",
            "entries": [entry("Wolf", wolf["id"], "Wolf")],
        },
        "summon_monster:5": {
            "spell_name": "Summon Monster V",
            "entries": [
                entry("Celestial brown bear", "bear::bear", "Brown Bear"),
                entry("Shadow mastiff", shadow["id"], "Shadow Mastiff"),
            ],
        },
    }

    expanded = expand_entities(
        [{"kind": "spell", "id": "sna-v"}],
        spells,
        lists,
        {wolf["id"]: wolf, shadow["id"]: shadow},
        ["nightbringer_initiate", "augment_summoning"],
    )

    assert [item["kind"] for item in expanded] == ["spell", "summon_statblock", "feat_spell", "summon_statblock"]
    assert expanded[2]["title"] == "Summon Monster V — Nightbringer Initiate"
    assert expanded[3]["id"] == shadow["id"]
    assert "Nightbringer Initiate" in expanded[3]["title"]
    assert "Augment Summoning" in expanded[3]["title"]
