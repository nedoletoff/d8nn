"""Player-editable world rules.

The player invents how the world works; the engine obeys the numeric knobs and
hands the free-text *laws* to the narrator so the prose matches. Nothing here is
decided by the LLM — the numbers are enforced in Python, the laws are described.

Rules live in :class:`~engine.state_manager.GameState.rules` and are edited with
``/rules`` (see ``ui/tui_app.py``).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from pydantic import BaseModel, Field

Lethality = Literal["soft", "standard", "hardcore"]

#: Narrator permissions, derived from the player's mandate. The system prompt is
#: assembled from these, so the model is told exactly which of its rights are
#: revoked. Defaults already forbid resurrection and free gifts.
CAPABILITY_LABELS: dict[str, str] = {
    "may_describe_magic": "описывать магию",
    "may_kill": "убивать персонажей и NPC",
    "may_resurrect": "воскрешать мёртвых",
    "may_invent_npcs": "выдумывать новых NPC",
    "may_invent_locations": "выдумывать новые локации",
    "may_grant_items": "дарить предметы от лица мира",
    "may_time_skip": "пропускать время вперёд",
}

#: Mechanic switches a mandate may flip. These override the engine defaults.
TOGGLE_LABELS: dict[str, str] = {
    "death_enabled": "смерть",
    "encumbrance_enabled": "перегруз/вес",
    "encounters_enabled": "случайные встречи",
    "magic_enabled": "магия в механике",
    "initiative_enabled": "инициатива в бою",
}


class NarratorCapabilities(BaseModel):
    may_describe_magic: bool = True
    may_kill: bool = True
    may_resurrect: bool = False
    may_invent_npcs: bool = True
    may_invent_locations: bool = True
    may_grant_items: bool = False
    may_time_skip: bool = True
    violence: Literal["none", "mild", "graphic"] = "graphic"
    tone: str = "grimdark"
    language: str = "ru"


class MechanicsToggles(BaseModel):
    death_enabled: bool = True
    encumbrance_enabled: bool = True
    encounters_enabled: bool = True
    magic_enabled: bool = True
    initiative_enabled: bool = True

#: Preset lethality profiles (applied as a bundle by ``/rules lethality X``).
LETHALITY_PRESETS: dict[str, dict[str, Any]] = {
    "soft": {"death_threshold": -10, "max_hp_cap": 999, "encounter_rate": 0.5},
    "standard": {"death_threshold": 0, "max_hp_cap": 999, "encounter_rate": 1.0},
    "hardcore": {"death_threshold": 0, "max_hp_cap": 40, "encounter_rate": 1.5},
}


class Ruleset(BaseModel):
    """The mutable laws of this particular game."""

    name: str = "standard"
    #: absolute ceiling for a character's max HP
    max_hp_cap: int = 999
    #: carry capacity = carry_base + STR * carry_per_str
    carry_base: int = 10
    carry_per_str: int = 5
    #: DEX penalty applied while overloaded
    overload_dex_penalty: int = 2
    #: a character dies at or below this HP (enemy and player alike)
    death_threshold: int = 0
    #: constant bias on every NPC's reputation toward the player
    world_attitude: int = 0
    #: multiplier on the encounter probability
    encounter_rate: float = 1.0
    lethality: Lethality = "standard"
    #: free-text world laws the player invented (handed to the narrator)
    laws: list[str] = Field(default_factory=list)
    #: arbitrary player-defined key/values
    custom: dict[str, str] = Field(default_factory=dict)
    #: player-mandate layer ("edits are stronger than the engine")
    overrides_enabled: bool = False
    mandate_raw: str = ""
    mandate_notes: str = ""
    capabilities: NarratorCapabilities = Field(default_factory=NarratorCapabilities)
    toggles: MechanicsToggles = Field(default_factory=MechanicsToggles)


@dataclass(frozen=True)
class RuleField:
    key: str
    kind: Literal["int", "float"]
    minimum: float
    maximum: float
    description: str


EDITABLE_RULES: dict[str, RuleField] = {
    "max_hp_cap": RuleField("max_hp_cap", "int", 1, 100000, "потолок max HP"),
    "carry_base": RuleField("carry_base", "int", 0, 1000, "база грузоподъёмности"),
    "carry_per_str": RuleField("carry_per_str", "int", 0, 100, "грузоподъёмность за единицу STR"),
    "overload_dex_penalty": RuleField("overload_dex_penalty", "int", 0, 20, "штраф DEX при перегрузе"),
    "death_threshold": RuleField("death_threshold", "int", -100, 100, "смерть при HP <= X"),
    "world_attitude": RuleField("world_attitude", "int", -100, 100, "сдвиг отношения мира к игроку"),
    "encounter_rate": RuleField("encounter_rate", "float", 0.0, 5.0, "множитель частоты встреч"),
}

RULESET_KEYS: frozenset[str] = frozenset(EDITABLE_RULES) | {"lethality", "name"}


class RuleError(ValueError):
    """Raised on an invalid rule edit."""


# ---------------------------------------------------------------------------
# Derived values enforced by the engine
# ---------------------------------------------------------------------------


def carry_capacity(rules: Ruleset, strength: int) -> int:
    return int(rules.carry_base + strength * rules.carry_per_str)


def effective_max_hp(rules: Ruleset, raw_max_hp: int) -> int:
    return max(1, min(int(raw_max_hp), int(rules.max_hp_cap)))


def encounter_chance(rules: Ruleset, danger_level: int, *, step: int = 10) -> int:
    base = max(0, min(100, danger_level * step))
    scaled = int(round(base * float(rules.encounter_rate)))
    return max(0, min(100, scaled))


def effective_disposition(rules: Ruleset, rep: int) -> str:
    """Disposition band with the global world-attitude bias applied."""
    biased = rep + rules.world_attitude
    if biased >= 30:
        return "friendly"
    if biased <= -30:
        return "hostile"
    return "neutral"


# ---------------------------------------------------------------------------
# Editing
# ---------------------------------------------------------------------------


def _coerce(field: RuleField, raw: str) -> Any:
    try:
        return int(raw) if field.kind == "int" else float(raw)
    except (TypeError, ValueError) as exc:
        raise RuleError(f"{field.key}: ожидалось число ({field.kind})") from exc


def parse_value(field: RuleField, raw: str) -> Any:
    value = _coerce(field, raw)
    if not (field.minimum <= value <= field.maximum):
        raise RuleError(f"{field.key}: допустимо {field.minimum}..{field.maximum}")
    return value


def set_rule(rules: Ruleset, key: str, raw: str, *, force: bool = False) -> Ruleset:
    """Return an updated copy of ``rules`` after validating ``key = raw``.

    ``force=True`` (used by the player mandate) bypasses the bounds checks, so
    the player's edits really do win over the engine defaults.
    """
    key = key.strip().lower()
    if key == "lethality":
        value = raw.strip().lower()
        if value not in LETHALITY_PRESETS:
            raise RuleError(f"lethality: одно из {sorted(LETHALITY_PRESETS)}")
        rules.lethality = value  # type: ignore[assignment]
        for preset_key, preset_value in LETHALITY_PRESETS[value].items():
            setattr(rules, preset_key, preset_value)
        return rules
    if key == "name":
        rules.name = raw.strip() or "custom"
        return rules
    field = EDITABLE_RULES.get(key)
    if field is None:
        raise RuleError(f"неизвестное правило: {key}")
    setattr(rules, key, _coerce(field, raw) if force else parse_value(field, raw))
    return rules


def add_law(rules: Ruleset, text: str) -> None:
    text = text.strip()
    if text and text not in rules.laws:
        rules.laws.append(text)


def remove_law(rules: Ruleset, index: int) -> bool:
    if 0 <= index < len(rules.laws):
        rules.laws.pop(index)
        return True
    return False


def reset(rules: Ruleset) -> Ruleset:
    fresh = Ruleset()
    rules.__dict__.update(fresh.__dict__)
    return rules


def describe(rules: Ruleset) -> list[tuple[str, str, str]]:
    """(key, human value, description) rows for the ``/rules`` view."""
    rows: list[tuple[str, str, str]] = [("name", rules.name, "имя набора")]
    rows.append(("lethality", rules.lethality, "профиль летальности"))
    for key, field in EDITABLE_RULES.items():
        rows.append((key, str(getattr(rules, key)), field.description))
    return rows


# ---------------------------------------------------------------------------
# Save / load player-authored rulesets
# ---------------------------------------------------------------------------


def rulesets_dir(data_dir: "Path | str") -> "Path":
    from pathlib import Path as _Path

    return _Path(data_dir) / "rulesets"


def save_ruleset(rules: Ruleset, directory: "Path | str", name: str) -> "Path":
    """Persist a ruleset to ``<directory>/<name>.json`` (atomic). Returns path."""
    import json as _json
    import os as _os
    from pathlib import Path as _Path

    safe = "".join(c for c in name if c.isalnum() or c in "-_ ").strip() or "ruleset"
    directory = _Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / f"{safe.replace(' ', '_')}.json"
    tmp = target.with_suffix(".json.tmp")
    tmp.write_text(_json.dumps(rules.model_dump(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    _os.replace(tmp, target)
    return target


def load_ruleset(path: "Path | str") -> Ruleset:
    import json as _json
    from pathlib import Path as _Path

    return Ruleset(**_json.loads(_Path(path).read_text(encoding="utf-8")))


def list_rulesets(directory: "Path | str") -> list[str]:
    from pathlib import Path as _Path

    directory = _Path(directory)
    if not directory.exists():
        return []
    return sorted(p.stem for p in directory.glob("*.json"))
