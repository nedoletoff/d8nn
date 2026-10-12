"""Turn a player's free-text input into a validated, structured intent.

This is **LLM call #1**. The model only classifies the player's words into a
small JSON schema; it never decides an outcome. If the LLM is unavailable or
returns garbage, we fall back to a deterministic heuristic (``freeform``), and
the engine asks the player for a stat/DC interactively where needed.
"""

from __future__ import annotations

import json
from typing import Any, Literal

from pydantic import BaseModel, Field, ValidationError

IntentAction = Literal[
    "check",
    "attack",
    "cast",
    "move",
    "travel",
    "talk",
    "say",
    "use",
    "equip",
    "unequip",
    "look",
    "rest",
    "freeform",
]

_ALLOWED_ACTIONS = set(IntentAction.__args__)  # type: ignore[attr-defined]

INTENT_SYSTEM_PROMPT = """\
Ты — парсер намерений игрока в настольной RPG. Твоя единственная задача —
превратить слова игрока в JSON-объект (intent). Ты НЕ решаешь исход, НЕ
придумываешь числа и НЕ описываешь мир.

Верни строго один JSON-объект без пояснений:
{
  "action_type": один из ["check","attack","cast","move","travel","talk","say",
                           "use","equip","unequip","look","rest","freeform"],
  "stat": "STR|DEX|INT|WIS|CHA|LUK" или null,
  "dc": целое число или null,
  "target": строка или null,
  "item": id предмета или null,
  "spell": id заклинания или null,
  "destination": id локации или null,
  "npc": id собеседника или null,
  "reason": краткая причина/описание, если уместно
}

Правила:
- попытка сделать что-то рискованное → "check" со stat и разумным dc;
- удар по врагу → "attack"; заклинание → "cast";
- перемещение между локациями → "travel";
- реплика в диалоге → "say"; попытка убедить/запугать → "talk";
- если непонятно — "freeform".
Никаких событий истории, только разбор одной фразы."""


class Intent(BaseModel):
    action_type: IntentAction = "freeform"
    stat: str | None = None
    dc: int | None = None
    target: str | None = None
    item: str | None = None
    spell: str | None = None
    destination: str | None = None
    npc: str | None = None
    reason: str | None = None
    raw: str = ""
    source: str = "llm"  # "llm" | "heuristic" | "fallback"


def heuristic_intent(text: str) -> Intent:
    """Deterministic last-resort classifier (no LLM involved)."""
    lowered = text.strip().lower()
    if not lowered:
        return Intent(action_type="freeform", raw=text, source="heuristic")

    if lowered.startswith("/"):
        return Intent(action_type="freeform", raw=text, source="heuristic")
    if any(word in lowered for word in ("атак", "бью", "удару", "напада", "attack")):
        return Intent(action_type="attack", reason=text, raw=text, source="heuristic")
    if any(word in lowered for word in ("иду", "идём", "переход", "направля", "travel")):
        return Intent(action_type="travel", reason=text, raw=text, source="heuristic")
    if any(word in lowered for word in ("говорю", "спрашив", "убеж", "запуг", "say", "talk")):
        return Intent(action_type="talk", stat="CHA", raw=text, source="heuristic")
    if any(word in lowered for word in ("осмотр", "смотр", "осмат", "огляд", "гля", "look")):
        return Intent(action_type="look", raw=text, source="heuristic")
    return Intent(action_type="freeform", raw=text, source="heuristic")


def _coerce(payload: dict[str, Any], text: str, source: str) -> Intent:
    action = payload.get("action_type", "freeform")
    if action not in _ALLOWED_ACTIONS:
        action = "freeform"
    data = {**payload, "action_type": action, "raw": text, "source": source}
    try:
        return Intent(**data)
    except ValidationError:
        return Intent(action_type="freeform", raw=text, source="fallback")


async def parse_intent(
    client: Any,
    text: str,
    sanitized_state: dict[str, Any] | None = None,
) -> Intent:
    """Classify ``text`` into an :class:`Intent`.

    Bad or missing JSON is retried once, then we fall back to the heuristic.
    """
    if client is None or getattr(client, "offline", False):
        return heuristic_intent(text)

    user = json.dumps(
        {
            "state": sanitized_state or {},
            "player_input": text,
        },
        ensure_ascii=False,
    )
    messages = [
        {"role": "system", "content": INTENT_SYSTEM_PROMPT},
        {"role": "user", "content": user},
    ]

    for attempt in range(2):
        content = await client.complete(
            messages,
            model=getattr(client.settings, "intent_model", None),
            response_format={"type": "json_object"},
            temperature=0.0,
        )
        if content:
            intent = _try_parse(content, text)
            if intent is not None:
                return intent
        # One stricter retry before giving up.
        messages = messages + [
            {
                "role": "user",
                "content": "Ответ был невалидным. Верни ТОЛЬКО корректный JSON-объект.",
            }
        ]

    return heuristic_intent(text)


def _try_parse(content: str, text: str) -> Intent | None:
    try:
        payload = json.loads(content)
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, dict):
        return None
    return _coerce(payload, text, source="llm")
