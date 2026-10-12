"""The narrative layer: prose and verdicts for already-computed results.

Three independent, read-only responsibilities:

* :func:`describe_event` — **LLM call #2**. Turn an engine result into prose.
  The model sees only a sanitized state and the event; it cannot change numbers.
* :func:`explain_rules` — a read-only "rules referee" for ``/rule``. It may
  rephrase the deterministic handbook but must not invent mechanics.
* :func:`crime_verdict` — propose who learns of a killing. The engine clamps the
  verdict against the facts, so an eager model cannot invent an identification.

Every function degrades to a deterministic template when the LLM is offline.
"""

from __future__ import annotations

import json
from typing import Any, Callable

from engine import crime_engine
from engine.state_manager import CrimeRecord

NARRATOR_SYSTEM_PROMPT = """\
ТЫ — ГОЛОС МИРА. Ты описываешь только то, что уже произошло.

Ты НЕ имеешь права:
- придумывать факты, которых нет в CANON;
- воскрешать мёртвых NPC;
- возвращать потраченные предметы;
- менять локацию игрока без команды move;
- отменять броски и их исходы;
- называть числа, правила или характеристики.

Ты можешь ТОЛЬКО художественно описать то, что перечислено в events_block.
Стиль: тёмный, лаконичный, grimdark & hardcore. Не более 3 абзацев.
Если описывать нечего — опиши атмосферу места, но не выдумывай события.

Мир живёт по законам игрока (world_laws и world_rules). Считай их физикой
этого мира и соблюдай их в описаниях. Их нельзя менять, отменять или
переписывать — можно только показать, как они проявляются.
НИКОГДА не используй эмодзи, смайлики и пиктограммы в тексте — только слова."""

RULES_REFEREE_PROMPT = """\
Ты — СПРАВКА ПО ПРАВИЛАМ, а не рассказчик. Отвечай на вопрос игрока о механике
по переданному тексту правил. Тебе НЕЛЬЗЯ выдумывать новые механики, менять
числа или заявлять, что что-то в игре изменилось. Ты только объясняешь.
Если в тексте правил нет ответа — так и скажи. Отвечай кратко, на русском."""

CRIME_ADJUDICATOR_PROMPT = """\
Ты определяешь, кто в мире узнал об убийстве и может ли кто-то НАЗВАТЬ убийцу.
Тебе даны жёсткие факты: свидетели (noticed/can_identify/will_report), способ,
улики, и явные флаги body_found и body_disposed.

Ограничения (нарушать нельзя):
- если никто не может опознать убийцу (can_identify=false у всех) и тело не
  найдено (body_found=false) — ты НЕ можешь объявить "accusation";
- "accusation" допустим только если есть опознавший свидетель либо тело
  найдено и улика идентифицируема (identifiable=true);
- "suspicion" требует донесшего свидетеля или найденного тела; иначе "none";
- в who_knows перечисляй только id из списка свидетелей.

Верни строго JSON: {"exposure":"none|suspicion|accusation","who_knows":[...],"reason":"..."}"""


# ---------------------------------------------------------------------------
# Narration
# ---------------------------------------------------------------------------


def _as_dict(event: Any) -> dict[str, Any]:
    if isinstance(event, dict):
        return event
    if hasattr(event, "model_dump"):
        return event.model_dump(mode="json")
    return {}


_OUTCOME_TEXT = {
    "crit_success": "Критический успех.",
    "success": "Успех.",
    "failure": "Провал.",
    "crit_failure": "Критический провал.",
}


def template_narration(event: Any) -> str:
    """Deterministic prose used in offline mode."""
    data = _as_dict(event)
    kind = data.get("type", "narration")
    roll = data.get("roll") or {}
    outcome = roll.get("outcome")

    if kind == "combat":
        base = _OUTCOME_TEXT.get(outcome, "")
        return f"Бой. {base}".strip()
    if kind == "dialogue":
        base = _OUTCOME_TEXT.get(outcome, "")
        return f"Разговор. {base}".strip()
    if kind == "move":
        return f"Ты добираешься до {data.get('intent', {}).get('destination', 'нового места')}."
    if kind == "crime":
        exposure = ""
        effects = data.get("effects") or []
        for effect in effects:
            if effect.get("kind") == "exposure":
                exposure = str(effect.get("value"))
        mapping = {
            "accusation": "Кто-то видел тебя и уже шепчется с нужными людьми.",
            "suspicion": "Смерть заметили. Пока — только слухи.",
            "none": "Никто ничего не видел. Могила молчит.",
        }
        return mapping.get(exposure, "Тело найдено. Тишина звенит.")
    if kind == "item_use":
        return "Ты используешь предмет. Пальцы помнят движение."
    if kind == "npc_death":
        return "Он падает. Топь принимает ещё одно."
    if kind == "narration":
        return data.get("narration") or "Мир молчит."
    if outcome:
        return f"Ты делаешь: {data.get('input') or 'действие'}. {_OUTCOME_TEXT.get(outcome, '')}".strip()
    return f"Ты делаешь: {data.get('input') or 'нечто'}."


async def describe_event(
    client: Any,
    event: Any,
    sanitized_state: dict[str, Any] | None = None,
) -> str:
    """Describe a computed result in prose (LLM call #2)."""
    fallback = template_narration(event)
    if client is None or getattr(client, "offline", False):
        return fallback

    state = sanitized_state or {}
    system = NARRATOR_SYSTEM_PROMPT
    lore = state.get("world_lore")
    if lore:
        system += "\n\nМИР (лор, соблюдай):\n" + json.dumps(lore, ensure_ascii=False)
    constraints = state.get("narrator_constraints")
    if constraints:
        system += "\n\n" + str(constraints)

    user = json.dumps(
        {
            "events_block": [_as_dict(event)],
            "state": state,
        },
        ensure_ascii=False,
    )
    content = await client.complete(
        [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        model=getattr(client.settings, "narrator_model", None),
        temperature=0.9,
    )
    return content or fallback


# ---------------------------------------------------------------------------
# Rules referee (read-only)
# ---------------------------------------------------------------------------


async def explain_rules(client: Any, query: str, handbook_text: str) -> str:
    """Rephrase the deterministic handbook. Never invents mechanics."""
    if client is None or getattr(client, "offline", False):
        return handbook_text
    user = json.dumps({"rules": handbook_text, "question": query}, ensure_ascii=False)
    content = await client.complete(
        [
            {"role": "system", "content": RULES_REFEREE_PROMPT},
            {"role": "user", "content": user},
        ],
        temperature=0.3,
    )
    return content or handbook_text


# ---------------------------------------------------------------------------
# Crime verdict (proposal; the engine validates it)
# ---------------------------------------------------------------------------


async def crime_verdict(client: Any, context: dict[str, Any]) -> dict[str, Any]:
    """Ask the LLM who learns of a crime. Falls back to the rule set offline."""
    if client is None or getattr(client, "offline", False):
        return crime_engine.rule_based_adjudicator(context)

    content = await client.complete(
        [
            {"role": "system", "content": CRIME_ADJUDICATOR_PROMPT},
            {"role": "user", "content": json.dumps(context, ensure_ascii=False)},
        ],
        response_format={"type": "json_object"},
        temperature=0.2,
    )
    if not content:
        return crime_engine.rule_based_adjudicator(context)
    try:
        verdict = json.loads(content)
        if isinstance(verdict, dict):
            return verdict
    except json.JSONDecodeError:
        pass
    return crime_engine.rule_based_adjudicator(context)


def adjudicator_from(verdict: dict[str, Any]) -> Callable[[dict[str, Any]], dict[str, Any]]:
    """Wrap a pre-fetched verdict so ``crime_engine.adjudicate`` can use it."""
    return lambda _context: verdict


async def build_crime_verdict(
    client: Any,
    state: Any,
    catalogs: Any,
    crime: CrimeRecord,
    *,
    investigation_success: bool = False,
) -> dict[str, Any]:
    """Convenience: build the context and fetch a (validated-later) verdict."""
    context = crime_engine.build_crime_context(
        state, catalogs, crime, investigation_success=investigation_success
    )
    return await crime_verdict(client, context)
