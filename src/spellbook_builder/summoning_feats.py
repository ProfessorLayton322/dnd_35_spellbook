from __future__ import annotations

import math
import re
from copy import deepcopy
from fractions import Fraction

from .util import normalized_name


SUMMONING_FEATS = (
    {
        "id": "augment_summoning",
        "name": "Augment Summoning",
        "source": "Player's Handbook v.3.5, p. 89",
        "source_url": "https://dndtools.org/feats/players-handbook-v35--6/augment-summoning--141/",
        "summary": "+4 enhancement bonus to Strength and Constitution for every summoned creature.",
    },
    {
        "id": "beckon_the_frozen",
        "name": "Beckon the Frozen",
        "source": "Frostburn, p. 47",
        "source_url": "https://www.dndtools.org/feats/frostburn--68/beckon-the-frozen--201/",
        "summary": "Adds the cold subtype and +1d6 cold damage to natural attacks; cannot affect fire creatures.",
    },
    {
        "id": "rashemi_elemental_summoning",
        "name": "Rashemi Elemental Summoning",
        "source": "Unapproachable East, p. 45",
        "source_url": "https://www.dndtools.org/feats/unapproachable-east--33/rashemi-elemental-summoning--2383/",
        "summary": "Adds an orglash option beside each air elemental and a thomil option beside each earth elemental.",
    },
    {
        "id": "greenbound_summoning",
        "name": "Greenbound Summoning",
        "source": "Lost Empires of Faerun, p. 8",
        "source_url": "https://dndtools.org/feats/lost-empires-of-faerun--30/greenbound-summoning--1317/",
        "summary": "Applies the greenbound template to animals summoned with summon nature's ally.",
    },
    {
        "id": "nightbringer_initiate",
        "name": "Nightbringer Initiate",
        "source": "Faiths of Eberron, p. 147",
        "source_url": "https://www.dndtools.org/feats/faiths-of-eberron--8/nightbringer-initiate--3266/",
        "summary": "Adds druid 5th-level summon monster V, restricted to a shadow mastiff.",
    },
)
FEATS_BY_ID = {feat["id"]: feat for feat in SUMMONING_FEATS}

_ABILITY_NAMES = ("Str", "Dex", "Con", "Int", "Wis", "Cha")
_SIZE_MODIFIERS = {
    "fine": 8,
    "diminutive": 4,
    "tiny": 2,
    "small": 1,
    "medium": 0,
    "large": -1,
    "huge": -2,
    "gargantuan": -4,
    "colossal": -8,
}
_SLAM_DAMAGE = {
    "fine": "1",
    "diminutive": "1d2",
    "tiny": "1d3",
    "small": "1d4",
    "medium": "1d6",
    "large": "1d8",
    "huge": "2d6",
    "gargantuan": "2d8",
    "colossal": "4d6",
}
_ENGULF_DAMAGE = {
    "small": "1d4",
    "medium": "1d6",
    "large": "1d8",
    "huge": "2d6",
    "gargantuan": "2d8",
    "colossal": "4d6",
}
_SKILL_ABILITIES = {
    "Balance": "Dex",
    "Bluff": "Cha",
    "Climb": "Str",
    "Concentration": "Con",
    "Diplomacy": "Cha",
    "Disguise": "Cha",
    "Escape Artist": "Dex",
    "Gather Information": "Cha",
    "Handle Animal": "Cha",
    "Hide": "Dex",
    "Intimidate": "Cha",
    "Jump": "Str",
    "Move Silently": "Dex",
    "Open Lock": "Dex",
    "Perform": "Cha",
    "Ride": "Dex",
    "Sleight of Hand": "Dex",
    "Swim": "Str",
    "Tumble": "Dex",
    "Use Magic Device": "Cha",
    "Use Rope": "Dex",
}


def normalize_summoning_feats(feat_ids: list[str] | tuple[str, ...] | None) -> list[str]:
    requested = set(feat_ids or [])
    unknown = sorted(requested - FEATS_BY_ID.keys())
    if unknown:
        raise ValueError(f"Unknown summoning feat(s): {', '.join(unknown)}")
    return [feat["id"] for feat in SUMMONING_FEATS if feat["id"] in requested]


def rashemi_variant(monster: dict) -> str | None:
    size_type = normalized_name(monster.get("fields", {}).get("Size/Type", ""))
    if "elemental" not in size_type:
        return None
    if re.search(r"\bair\b", size_type):
        return "orglash"
    if re.search(r"\bearth\b", size_type):
        return "thomil"
    return None


def is_greenbound_eligible(monster: dict, summon_list: str) -> bool:
    return summon_list.startswith("summon_natures_ally:") and bool(
        re.search(r"\banimal\b", monster.get("fields", {}).get("Size/Type", ""), re.I)
    )


def _scores(value: str) -> dict[str, int]:
    return {
        name: int(score)
        for name, score in re.findall(r"\b(Str|Dex|Con|Int|Wis|Cha)\s+(\d+)", value or "")
    }


def _format_scores(original: str, scores: dict[str, int]) -> str:
    def replace(match: re.Match) -> str:
        name = match.group(1)
        return f"{name} {scores.get(name, int(match.group(2)))}"

    return re.sub(r"\b(Str|Dex|Con|Int|Wis|Cha)\s+(\d+)", replace, original)


def _modifier(score: int) -> int:
    return (score - 10) // 2


def _signed(value: int) -> str:
    return f"{value:+d}"


def _hit_dice_count(value: str) -> Fraction:
    match = re.search(r"(?:(\d+|[¼½¾])\s*)?d\d+", value or "")
    if not match:
        return Fraction(0)
    token = match.group(1) or "1"
    fractions = {"¼": Fraction(1, 4), "½": Fraction(1, 2), "¾": Fraction(3, 4)}
    return fractions[token] if token in fractions else Fraction(int(token))


def _integer_hit_dice(value: str) -> int:
    count = _hit_dice_count(value)
    return max(1, math.ceil(count))


def _adjust_hit_points(value: str, con_modifier_delta: int) -> str:
    if not value or not con_modifier_delta:
        return value
    count = _hit_dice_count(value)
    if not count:
        return value
    hp_delta = math.floor(count * con_modifier_delta)
    if con_modifier_delta > 0:
        hp_delta = max(1, hp_delta)

    dice = re.search(r"(?P<dice>(?:\d+|[¼½¾])?\s*d\d+)(?P<bonus>[+-]\d+)?", value)
    if not dice:
        return value
    old_bonus = int(dice.group("bonus") or 0)
    new_bonus = old_bonus + hp_delta
    bonus_text = _signed(new_bonus) if new_bonus else ""
    result = value[: dice.start()] + dice.group("dice") + bonus_text + value[dice.end() :]
    return re.sub(
        r"\((\d+)\s*hp\)",
        lambda match: f"({max(1, int(match.group(1)) + hp_delta)} hp)",
        result,
        count=1,
    )


def _adjust_armor_class(value: str, dex_delta: int, natural_delta: int) -> str:
    if not value or (not dex_delta and not natural_delta):
        return value
    result = re.sub(r"^\s*(\d+)", lambda match: str(int(match.group(1)) + dex_delta + natural_delta), value, count=1)
    result = re.sub(
        r"\btouch\s+(\d+)",
        lambda match: f"touch {int(match.group(1)) + dex_delta}",
        result,
        count=1,
        flags=re.I,
    )
    result = re.sub(
        r"\bflat-footed\s+(\d+)",
        lambda match: f"flat-footed {int(match.group(1)) + natural_delta}",
        result,
        count=1,
        flags=re.I,
    )
    additions = []
    if dex_delta:
        additions.append(f"{_signed(dex_delta)} feat/template Dex")
    if natural_delta:
        additions.append(f"{_signed(natural_delta)} template natural")
    if additions:
        explanation = ", ".join(additions)
        if ")" in result:
            result = result.replace(")", f", {explanation})", 1)
        else:
            result += f" ({explanation})"
    return result


def _adjust_leading_bonus(value: str, delta: int) -> str:
    if not value or not delta:
        return value
    return re.sub(r"^\s*([+-]\d+)", lambda match: _signed(int(match.group(1)) + delta), value, count=1)


def _adjust_base_attack_grapple(value: str, delta: int) -> str:
    if not value or not delta:
        return value
    return re.sub(
        r"([+-]\d+)\s*/\s*([+-]\d+)",
        lambda match: f"{match.group(1)} / {_signed(int(match.group(2)) + delta)}",
        value,
        count=1,
    )


def _adjust_saves(value: str, fort_delta: int, ref_delta: int) -> str:
    result = value
    for label, delta in (("Fort", fort_delta), ("Ref", ref_delta)):
        if delta:
            result = re.sub(
                rf"\b{label}\s+([+-]\d+)",
                lambda match, label=label, delta=delta: f"{label} {_signed(int(match.group(1)) + delta)}",
                result,
                count=1,
                flags=re.I,
            )
    return result


def _adjust_attack_rolls(value: str, melee_delta: int, ranged_delta: int) -> str:
    def replace(match: re.Match) -> str:
        delta = melee_delta if match.group(2).casefold() == "melee" else ranged_delta
        return f"{_signed(int(match.group(1)) + delta)} {match.group(2)}"

    return re.sub(r"([+-]\d+)\s+(melee|ranged)\b", replace, value or "", flags=re.I)


def _strength_multiplier(old_bonus: int, old_modifier: int, group_index: int) -> float:
    candidates = (1.0, 0.5, 1.5)
    exact = [candidate for candidate in candidates if math.floor(old_modifier * candidate) == old_bonus]
    if exact:
        return exact[0]
    return 1.0 if group_index == 0 else 0.5


def _adjust_attack_damage(value: str, old_modifier: int, new_modifier: int) -> str:
    group_index = 0

    def replace_group(match: re.Match) -> str:
        nonlocal group_index
        content = match.group(1)
        damage = re.search(r"(?<![+\-\w])(\d+d\d+)([+-]\d+)?", content)
        if not damage:
            group_index += 1
            return match.group(0)
        old_bonus = int(damage.group(2) or 0)
        multiplier = _strength_multiplier(old_bonus, old_modifier, group_index)
        delta = math.floor(new_modifier * multiplier) - math.floor(old_modifier * multiplier)
        new_bonus = old_bonus + delta
        bonus_text = _signed(new_bonus) if new_bonus else ""
        adjusted = content[: damage.start()] + damage.group(1) + bonus_text + content[damage.end() :]
        group_index += 1
        return f"({adjusted})"

    return re.sub(r"\(([^)]*)\)", replace_group, value or "")


def _skill_ability_overrides(monster: dict) -> dict[str, str]:
    prose = " ".join(block.get("text", "") for block in monster.get("shared_sections", []))
    overrides: dict[str, str] = {}
    if re.search(r"Dexterity modifier (?:instead of .*? )?for Climb", prose, re.I):
        overrides["Climb"] = "Dex"
    if re.search(r"Dexterity modifier (?:instead of .*? )?for (?:Climb and )?Swim", prose, re.I):
        overrides["Swim"] = "Dex"
    return overrides


def _adjust_skills(value: str, modifier_deltas: dict[str, int], overrides: dict[str, str]) -> str:
    result = value
    for skill, ability in _SKILL_ABILITIES.items():
        ability = overrides.get(skill, ability)
        delta = modifier_deltas.get(ability, 0)
        if not delta:
            continue
        result = re.sub(
            rf"\b({re.escape(skill)})\s+([+-]\d+)",
            lambda match, delta=delta: f"{match.group(1)} {_signed(int(match.group(2)) + delta)}",
            result,
            count=1,
        )
    return result


def _adjust_ability_based_sections(sections: list[dict], modifier_deltas: dict[str, int]) -> list[dict]:
    """Update numeric DCs explicitly identified as ability-based by SRD prose."""

    result = deepcopy(sections)
    ability_names = {
        "Strength": "Str",
        "Dexterity": "Dex",
        "Constitution": "Con",
        "Intelligence": "Int",
        "Wisdom": "Wis",
        "Charisma": "Cha",
    }
    for index, block in enumerate(result):
        text = block.get("text", "")
        match = re.search(
            r"\b(?:save\s+|check\s+)?DCs?\b.*?\b(Strength|Dexterity|Constitution|Intelligence|Wisdom|Charisma)-based",
            text,
            re.I,
        )
        if not match:
            continue
        canonical_name = next(name for name in ability_names if name.casefold() == match.group(1).casefold())
        delta = modifier_deltas.get(ability_names[canonical_name], 0)
        if not delta:
            continue
        target_index = index
        if not re.search(r"\bDC\s+\d+", text):
            for prior in range(index - 1, -1, -1):
                if result[prior].get("type") == "heading":
                    break
                if re.search(r"\bDC\s+\d+", result[prior].get("text", "")):
                    target_index = prior
                    break
        result[target_index]["text"] = re.sub(
            r"\bDC\s+(\d+)",
            lambda dc_match: f"DC {int(dc_match.group(1)) + delta}",
            result[target_index].get("text", ""),
        )
    return result


def _append_field(value: str | None, addition: str) -> str:
    if not value or value.strip() == "—":
        return addition
    return value.rstrip() + ", " + addition


def _size(value: str) -> str:
    match = re.match(r"\s*(Fine|Diminutive|Tiny|Small|Medium|Large|Huge|Gargantuan|Colossal)\b", value or "", re.I)
    return match.group(1).casefold() if match else "medium"


def _greenbound_type(value: str, cold: bool) -> str:
    size = _size(value).title()
    subtypes = "Augmented Animal, Cold" if cold else "Augmented Animal"
    return f"{size} Plant ({subtypes})"


def _add_cold_subtype(value: str) -> str:
    if re.search(r"\bcold\b", value or "", re.I):
        return value
    if value.rstrip().endswith(")"):
        return value.rstrip()[:-1] + ", Cold)"
    return value.rstrip() + " (Cold)"


def _base_attack(value: str) -> int:
    match = re.search(r"([+-]\d+)\s*/", value or "")
    return int(match.group(1)) if match else 0


def _greenbound_slam(fields: dict, strength: int) -> str:
    size = _size(fields.get("Size/Type", ""))
    attack = _base_attack(fields.get("Base Attack/Grapple", "")) + _SIZE_MODIFIERS[size] + _modifier(strength)
    damage_bonus = _modifier(strength)
    damage = _SLAM_DAMAGE[size] + (_signed(damage_bonus) if damage_bonus else "")
    return f"slam {_signed(attack)} melee ({damage})"


def _apply_cr_note(value: str | None, addition: int, template_name: str) -> str:
    base = value or "—"
    return f"{base} (base; {template_name} +{addition})"


def _apply_level_adjustment_note(value: str | None, addition: int, template_name: str) -> str:
    base = value or "—"
    return f"{base} (base; {template_name} +{addition})"


def _effect_paragraphs(active: list[str], blocked: list[str], variant: str | None) -> list[dict]:
    paragraphs: list[dict] = [{"type": "heading", "text": "Summoning Feat Effects"}]
    if "augment_summoning" in active:
        paragraphs.append({"type": "paragraph", "text": "Augment Summoning: +4 enhancement bonus to Strength and Constitution is included in the effective statistics above."})
    if "beckon_the_frozen" in active:
        paragraphs.append({"type": "paragraph", "text": "Beckon the Frozen: This frostfell version has the cold subtype (cold immunity and fire vulnerability) and its natural attacks deal +1d6 cold damage."})
    if "greenbound_summoning" in active:
        paragraphs.append({"type": "paragraph", "text": "Greenbound Summoning: The temporary greenbound template, including its ability, defense, attack, healing, resistance, senses, skill, and spell-like ability changes, is included above."})
    if variant == "orglash":
        paragraphs.append({"type": "paragraph", "text": "Rashemi Elemental Summoning option: Orglash template applied to the base air elemental; the normal air elemental remains a separate option."})
    elif variant == "thomil":
        paragraphs.append({"type": "paragraph", "text": "Rashemi Elemental Summoning option: Thomil template applied to the base earth elemental; the normal earth elemental remains a separate option."})
    paragraphs.extend({"type": "paragraph", "text": message} for message in blocked)
    return paragraphs


def materialize_summon_statblock(
    monster: dict,
    feat_ids: list[str] | tuple[str, ...] | None,
    summon_list: str,
    variant: str | None = None,
) -> tuple[dict, list[str]]:
    """Return a derived printable statblock without changing the canonical index."""

    selected = normalize_summoning_feats(feat_ids)
    result = deepcopy(monster)
    fields = deepcopy(monster.get("fields", {}))
    base_scores = _scores(fields.get("Abilities", ""))
    score_deltas = {name: 0 for name in _ABILITY_NAMES}
    minimum_scores: dict[str, int] = {}
    natural_armor_delta = 0
    grapple_misc_delta = 0
    active: list[str] = []
    blocked: list[str] = []
    special_fields: list[tuple[str, str]] = []

    if variant not in {None, "orglash", "thomil"}:
        raise ValueError(f"Unknown summon variant: {variant}")
    if variant and variant != rashemi_variant(monster):
        raise ValueError(f"{variant} cannot be applied to {monster.get('name', 'this creature')}")

    if "greenbound_summoning" in selected and is_greenbound_eligible(monster, summon_list):
        active.append("greenbound_summoning")
        score_deltas.update({"Str": 6, "Dex": 2, "Con": 4, "Cha": 4})
        natural_armor_delta += 6
        grapple_misc_delta += 4

    fire_subtype = bool(re.search(r"\bfire\b", fields.get("Size/Type", ""), re.I))
    if "beckon_the_frozen" in selected:
        if fire_subtype:
            blocked.append("Beckon the Frozen: unavailable because a creature with the fire subtype cannot gain the cold subtype this way.")
        else:
            active.append("beckon_the_frozen")

    if variant == "orglash":
        active.append("rashemi_elemental_summoning")
        score_deltas["Con"] += 4
        minimum_scores["Int"] = 10
        natural_armor_delta += 2
    elif variant == "thomil":
        active.append("rashemi_elemental_summoning")
        score_deltas["Con"] += 4
        minimum_scores["Int"] = 10
        natural_armor_delta += 2

    if "augment_summoning" in selected:
        active.append("augment_summoning")
        score_deltas["Str"] += 4
        score_deltas["Con"] += 4

    new_scores = dict(base_scores)
    for ability, old_score in base_scores.items():
        new_scores[ability] = max(old_score + score_deltas[ability], minimum_scores.get(ability, -10**9))
    modifier_deltas = {
        ability: _modifier(new_scores[ability]) - _modifier(old_score)
        for ability, old_score in base_scores.items()
    }

    cold_applied = "beckon_the_frozen" in active or variant == "orglash"
    if "greenbound_summoning" in active:
        fields["Size/Type"] = _greenbound_type(fields.get("Size/Type", ""), cold_applied)
    elif cold_applied:
        fields["Size/Type"] = _add_cold_subtype(fields.get("Size/Type", ""))

    fields["Abilities"] = _format_scores(fields.get("Abilities", ""), new_scores)
    fields["Hit Dice"] = _adjust_hit_points(fields.get("Hit Dice", ""), modifier_deltas.get("Con", 0))
    fields["Initiative"] = _adjust_leading_bonus(fields.get("Initiative", ""), modifier_deltas.get("Dex", 0))
    fields["Armor Class"] = _adjust_armor_class(
        fields.get("Armor Class", ""), modifier_deltas.get("Dex", 0), natural_armor_delta
    )
    fields["Base Attack/Grapple"] = _adjust_base_attack_grapple(
        fields.get("Base Attack/Grapple", ""), modifier_deltas.get("Str", 0) + grapple_misc_delta
    )
    finesse = bool(re.search(r"Weapon Finesse", fields.get("Feats", ""), re.I))
    if finesse and {"Str", "Dex"} <= base_scores.keys():
        melee_delta = max(_modifier(new_scores["Str"]), _modifier(new_scores["Dex"])) - max(
            _modifier(base_scores["Str"]), _modifier(base_scores["Dex"])
        )
    else:
        melee_delta = modifier_deltas.get("Str", 0)
    old_str_modifier = _modifier(base_scores["Str"]) if "Str" in base_scores else 0
    new_str_modifier = _modifier(new_scores["Str"]) if "Str" in new_scores else old_str_modifier
    for key in ("Attack", "Full Attack"):
        value = _adjust_attack_rolls(fields.get(key, ""), melee_delta, modifier_deltas.get("Dex", 0))
        fields[key] = _adjust_attack_damage(value, old_str_modifier, new_str_modifier)
    fields["Saves"] = _adjust_saves(fields.get("Saves", ""), modifier_deltas.get("Con", 0), modifier_deltas.get("Dex", 0))
    if fields.get("Skills"):
        fields["Skills"] = _adjust_skills(fields["Skills"], modifier_deltas, _skill_ability_overrides(monster))
    result["shared_sections"] = _adjust_ability_based_sections(result.get("shared_sections", []), modifier_deltas)

    if "greenbound_summoning" in active:
        slam = _greenbound_slam(fields, new_scores.get("Str", 10))
        fields["Attack"] = _append_field(fields.get("Attack"), f"or {slam}")
        fields["Full Attack"] = _append_field(fields.get("Full Attack"), f"or {slam}")
        fields["Special Attacks"] = _append_field(fields.get("Special Attacks"), "spell-like abilities")
        fields["Special Qualities"] = _append_field(
            fields.get("Special Qualities"),
            "plant traits, DR 10/magic and slashing, fast healing 3, natural weapons count as magic, cold resistance 10, electricity resistance 10, tremorsense 60 ft.",
        )
        fields["Greenbound Spell-Like Abilities"] = "At will—entangle, pass without trace, speak with plants; 1/day—wall of thorns. Caster level equals Hit Dice; save DC 10 + spell level + Cha modifier."
        fields["Plant Traits"] = "Low-light vision; immunity to mind-affecting effects, poison, sleep, paralysis, polymorph, and stunning; not subject to critical hits."
        fields["Greenbound Skills"] = "+16 racial bonus on Hide and Move Silently checks in forested areas (in addition to effective skill totals above)."
        fields["Environment"] = "Any forest"
        fields["Treasure"] = "Standard"
        fields["Challenge Rating"] = _apply_cr_note(fields.get("Challenge Rating"), 2, "greenbound")
        fields["Level Adjustment"] = _apply_level_adjustment_note(fields.get("Level Adjustment"), 8, "greenbound")

    if "beckon_the_frozen" in active:
        special_fields.append(("Beckon the Frozen Natural Attacks", "+1d6 cold damage on every natural attack"))
        fields["Special Qualities"] = _append_field(fields.get("Special Qualities"), "cold subtype (cold immunity; fire vulnerability)")

    hit_dice = _integer_hit_dice(fields.get("Hit Dice", ""))
    if variant == "orglash":
        special_fields.extend(
            [
                ("Orglash Natural Attack Cold Damage", "Add half the attack's damage dice as cold damage; for a one-die attack, use a die two sizes smaller."),
                ("Orglash Red Wizard Saves", "+2 morale bonus on saves against spells from recognized Red Wizards"),
            ]
        )
        cone_dc = 15 + _modifier(new_scores.get("Cha", 10))
        fields["Special Attacks"] = _append_field(
            fields.get("Special Attacks"), f"cone of cold 3/day (caster level {hit_dice}, DC {cone_dc})"
        )
        fields["Special Qualities"] = _append_field(
            fields.get("Special Qualities"),
            "cold subtype, fast healing 3 in cold or extremely cold weather, native elemental",
        )
        fields["Environment"] = "Cold forest and plains"
        fields["Organization"] = "Solitary"
        fields["Challenge Rating"] = _apply_cr_note(fields.get("Challenge Rating"), 1, "orglash")
        fields["Level Adjustment"] = _apply_level_adjustment_note(fields.get("Level Adjustment"), 1, "orglash")
        fields["Alignment"] = "Usually chaotic neutral"
    elif variant == "thomil":
        size = _size(fields.get("Size/Type", ""))
        if size in _ENGULF_DAMAGE:
            con_modifier = _modifier(new_scores.get("Con", 10))
            str_modifier = _modifier(new_scores.get("Str", 10))
            reflex_dc = 10 + hit_dice // 2 + con_modifier
            escape_dc = 15 + hit_dice // 2 + con_modifier
            special_fields.append(
                (
                    "Thomil Engulf",
                    f"Standard action; creatures at least one size smaller. Reflex DC {reflex_dc}; trapped creatures take {_ENGULF_DAMAGE[size]}{_signed(str_modifier) if str_modifier else ''} crushing damage/round. Escape Artist DC {escape_dc} or Strength DC {20 + str_modifier}.",
                )
            )
            fields["Special Attacks"] = _append_field(fields.get("Special Attacks"), "engulf")
        special_fields.append(("Thomil Red Wizard Saves", "+2 morale bonus on saves against spells from recognized Red Wizards"))
        fields["Special Qualities"] = _append_field(
            fields.get("Special Qualities"),
            f"boulder defense, DR 10/+1, cold resistance 5, native elemental, spell resistance {5 + hit_dice}",
        )
        fields["Boulder Defense"] = "Standard action to become an immobile smooth boulder: DR 15/— and spell resistance +5; return to normal as a free action."
        fields["Environment"] = "Cold mountains"
        fields["Organization"] = "Solitary or patrol (5–20)"
        fields["Challenge Rating"] = _apply_cr_note(fields.get("Challenge Rating"), 2, "thomil")
        fields["Level Adjustment"] = _apply_level_adjustment_note(fields.get("Level Adjustment"), 1, "thomil")
        fields["Alignment"] = "Usually chaotic neutral"

    for label, value in special_fields:
        fields[label] = value

    active = [feat_id for feat_id in selected if feat_id in active]
    labels = [FEATS_BY_ID[feat_id]["name"] for feat_id in active]
    if blocked:
        labels.append("Beckon the Frozen (fire subtype: not applied)")
    result["fields"] = {"Applied Summoning Feats": ", ".join(labels), **fields}
    result["shared_sections"] = list(result.get("shared_sections", [])) + _effect_paragraphs(active, blocked, variant)
    result["applied_summoning_feats"] = active
    result["summon_variant"] = variant
    return result, active


def summon_display_title(base_title: str, active: list[str], variant: str | None) -> str:
    labels: list[str] = []
    if variant == "orglash":
        labels.append("Orglash")
    elif variant == "thomil":
        labels.append("Thomil")
    if "greenbound_summoning" in active:
        labels.append("Greenbound")
    if "beckon_the_frozen" in active:
        labels.append("Frostfell")
    if "augment_summoning" in active:
        labels.append("Augment Summoning")
    return base_title if not labels else f"{base_title} — {', '.join(labels)}"
