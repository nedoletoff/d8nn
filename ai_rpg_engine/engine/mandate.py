"""The player mandate: free-text edits that outrank the engine.

At setup (and any time via ``/mandate``) the player writes free-text "edits" —
house rules, world overrides, restrictions. This module:

* **parses** those edits into a structured :class:`Mandate`
  (an LLM classifies them when online; a deterministic keyword parser does it
  offline);
* **applies** the mechanical parts to the ruleset with ``force=True`` (the
  player's numbers win over the engine's bounds) and flips mechanic toggles;
* **derives the narrator's rights** from the mandate and renders a constraints
  block that is injected into the narration prompt, so the model is explicitly
  told which of its own permissions are revoked.

Hard invariants (immutable canon, no direct writes to state) cannot be edited
away — everything else is fair game.
"""

from __future__ import annotations

import json
import re
from typing import Any, Literal

from pydantic import BaseModel, Field

from engine import rules_engine as rules_engine
from engine.rules_engine import (
    CAPABILITY_LABELS,
    TOGGLE_LABELS,
    NarratorCapabilities,
    MechanicsToggles,
    Ruleset,
)

MANDATE_PROMPT = """\
Ты — редактор мира. Игрок написал свои правки к игре на свободном языке.
Разбери их в JSON. Правки игрока ВАЖНЕЕ движка, поэтому смело меняй правила.

Верни строго JSON:
{
  "laws": ["переформулированные законы мира, строки"],
  "engine_overrides": {"ключ_правила": "значение"},   // см. список ниже
  "toggles": {"death_enabled": false, ...},
  "capabilities": {"may_describe_magic": false, ...},
  "notes": "короткая заметка"
}

Ключи правил (engine_overrides): max_hp_cap, carry_base, carry_per_str,
overload_dex_penalty, death_threshold, world_attitude, encounter_rate.

Переключатели (toggles): death_enabled, encumbrance_enabled, encounters_enabled,
magic_enabled, initiative_enabled.

Права нарратора (capabilities): may_describe_magic, may_kill, may_resurrect,
may_invent_npcs, may_invent_locations, may_grant_items, may_time_skip.
Также можно задать строки: tone ("grimdark"/"heroic"/"cozy"/...),
violence ("none"/"mild"/"graphic"), language ("ru"/"en").

Что не упомянуто — не выдумывай. Нельзя разрешать менять неизменяемый канон."""


class Mandate(BaseModel):
    laws: list[str] = Field(default_factory=list)
    engine_overrides: dict[str, str] = Field(default_factory=dict)
    toggles: dict[str, bool] = Field(default_factory=dict)
    capabilities: dict[str, Any] = Field(default_factory=dict)
    notes: str = ""
    raw: str = ""
    source: str = "rule"  # "rule" | "llm"


# ---------------------------------------------------------------------------
# Deterministic parser (offline)
# ---------------------------------------------------------------------------

_STRINGS = {
    "tone": Literal["grimdark", "heroic", "cozy", "noir", "hopeful"],
}

_NUMBER_RE = re.compile(
    r"(max_hp_cap|carry_base|carry_per_str|overload_dex_penalty|death_threshold|"
    r"world_attitude|encounter_rate)\s*[:=]?\s*(-?\d+(?:[.,]\d+)?)",
    re.IGNORECASE,
)

_NO_MAGIC = ("нет маги", "маги не", "магии не", "магии нет", "магию запрещ", "без маги", "магия запрещ", "no magic")
_NO_DEATH = ("никто не умира", "не умира", "бессмерт", "без смерт", "no death", "регенерац")
_RESURRECT = ("воскреш", "resurrect", "возвращать из мертв")
_NO_NEW_NPC = ("нет новых персонаж", "без новых npc", "только существующие")
_NO_TIME = ("время не ид", "без времени", "не пропуска")
_NO_VIOLENCE = ("без насили", "no violence", "без крови")
_COZY = ("уютн", "cozy", "добр", "нежно")
_HEROIC = ("героич", "heroic", "эпичн")


def rule_based_mandate(text: str) -> Mandate:
    """Best-effort deterministic parse; always keeps the raw text as a law."""
    lowered = text.lower()
    mandate = Mandate(raw=text, source="rule")
    if text.strip():
        mandate.laws.append(text.strip())

    for match in _NUMBER_RE.finditer(text):
        key, value = match.group(1).lower(), match.group(2).replace(",", ".")
        mandate.engine_overrides[key] = value

    if any(token in lowered for token in _NO_MAGIC):
        mandate.capabilities["may_describe_magic"] = False
        mandate.toggles["magic_enabled"] = False
    if any(token in lowered for token in _NO_DEATH):
        mandate.toggles["death_enabled"] = False
        mandate.capabilities["may_kill"] = False
    if any(token in lowered for token in _RESURRECT):
        mandate.capabilities["may_resurrect"] = True
    if any(token in lowered for token in _NO_NEW_NPC):
        mandate.capabilities["may_invent_npcs"] = False
    if any(token in lowered for token in _NO_TIME):
        mandate.capabilities["may_time_skip"] = False
    if any(token in lowered for token in _NO_VIOLENCE):
        mandate.capabilities["violence"] = "none"
    if any(token in lowered for token in _COZY):
        mandate.capabilities["tone"] = "cozy"
    elif any(token in lowered for token in _HEROIC):
        mandate.capabilities["tone"] = "heroic"
    return mandate


async def parse_mandate(client: Any, text: str) -> Mandate:
    """Classify the player's edits. Falls back to the keyword parser offline."""
    if client is None or getattr(client, "offline", False) or not text.strip():
        return rule_based_mandate(text)

    content = await client.complete(
        [
            {"role": "system", "content": MANDATE_PROMPT},
            {"role": "user", "content": text},
        ],
        response_format={"type": "json_object"},
        temperature=0.0,
    )
    if not content:
        return rule_based_mandate(text)
    try:
        payload = json.loads(content)
    except json.JSONDecodeError:
        return rule_based_mandate(text)
    if not isinstance(payload, dict):
        return rule_based_mandate(text)

    fallback = rule_based_mandate(text)
    mandate = Mandate(
        laws=[str(x) for x in payload.get("laws", [])] or fallback.laws,
        engine_overrides={str(k): str(v) for k, v in (payload.get("engine_overrides") or {}).items()},
        toggles={str(k): bool(v) for k, v in (payload.get("toggles") or {}).items()},
        capabilities=dict(payload.get("capabilities") or {}),
        notes=str(payload.get("notes", "")),
        raw=text,
        source="llm",
    )
    if not mandate.laws:
        mandate.laws = fallback.laws
    return mandate


# ---------------------------------------------------------------------------
# Apply
# ---------------------------------------------------------------------------


def apply_mandate(rules: Ruleset, mandate: Mandate, *, enabled: bool = True) -> list[str]:
    """Apply a mandate to the ruleset. Returns a list of human-readable changes."""
    changes: list[str] = []

    for law in mandate.laws:
        if law.strip():
            rules_engine.add_law(rules, law)
            changes.append(f"закон: {law.strip()[:60]}")

    for key, value in mandate.engine_overrides.items():
        try:
            rules_engine.set_rule(rules, key, value, force=True)
            changes.append(f"правило {key} = {value}")
        except rules_engine.RuleError:
            continue

    for key, value in mandate.toggles.items():
        if hasattr(rules.toggles, key):
            setattr(rules.toggles, key, bool(value))
            changes.append(f"механика {TOGGLE_LABELS.get(key, key)} = {'вкл' if value else 'выкл'}")

    for key, value in mandate.capabilities.items():
        if key in CAPABILITY_LABELS:
            setattr(rules.capabilities, key, bool(value))
            changes.append(f"право «{CAPABILITY_LABELS[key]}» = {'да' if value else 'нет'}")
        elif key in ("tone", "violence", "language") and value is not None:
            setattr(rules.capabilities, key, str(value))
            changes.append(f"{key} = {value}")

    if enabled:
        rules.overrides_enabled = True
        if mandate.raw:
            rules.mandate_raw = mandate.raw
        rules.mandate_notes = mandate.notes
    return changes


# ---------------------------------------------------------------------------
# Narrator constraints
# ---------------------------------------------------------------------------


def capability_rows(rules: Ruleset) -> list[tuple[str, bool]]:
    return [(label, bool(getattr(rules.capabilities, key))) for key, label in CAPABILITY_LABELS.items()]


def toggle_rows(rules: Ruleset) -> list[tuple[str, bool]]:
    return [(label, bool(getattr(rules.toggles, key))) for key, label in TOGGLE_LABELS.items()]


def narrator_constraints(rules: Ruleset) -> str:
    """Render the mandate as an explicit permission list for the narrator."""
    if not rules.overrides_enabled and not rules.laws:
        return ""

    lines = ["ПРАВКИ ИГРОКА (ВАЖНЕЕ ДВИЖКА — соблюдай их строго):"]

    forbidden = [label for label, ok in capability_rows(rules) if not ok]
    allowed = [label for label, ok in capability_rows(rules) if ok]
    lines.append("Тебе РАЗРЕШЕНО: " + ", ".join(allowed) + ".")
    if forbidden:
        lines.append("Тебе ЗАПРЕЩЕНО: " + ", ".join(forbidden) + ".")

    lines.append(
        f"Тон: {rules.capabilities.tone}. Насилие: {rules.capabilities.violence}. "
        f"Язык: {rules.capabilities.language}."
    )

    disabled = [label for label, ok in toggle_rows(rules) if not ok]
    if disabled:
        lines.append("Отключённые механики (в мире их нет): " + ", ".join(disabled) + ".")

    if rules.laws:
        lines.append("Законы мира:")
        lines.extend(f"  • {law}" for law in rules.laws)

    lines.append(
        "Неизменяемое (никогда): нельзя переписывать канон и прошлые события, "
        "нельзя менять числа состояния напрямую."
    )
    return "\n".join(lines)
