"""Read-only rules handbook.

This module answers "how does X work?" questions **without touching anything**:
it never writes to the canon log, never mutates the game state and never saves.
It is pure by construction — it does not even import ``StateManager``.

The answers are generated from the engine's own constants where possible, so
they stay in sync with the real rules instead of drifting into documentation.

An optional read-only LLM layer may *rephrase* these answers, but it is never
allowed to invent mechanics or claim that anything changed.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from engine import crime_engine as crime
from engine import inventory_engine as inv
from engine import time_engine as tm
from engine.dice_engine import FUDGE_MARGIN

DEFAULT_COMMAND = "/rule"


@dataclass(frozen=True)
class RuleTopic:
    key: str
    title: str
    keywords: tuple[str, ...]
    body: str


@dataclass
class HandbookResult:
    query: str
    matched: str | None
    text: str
    suggested: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Topic catalog
# ---------------------------------------------------------------------------

_TOPICS: tuple[RuleTopic, ...] = (
    RuleTopic(
        "checks",
        "Броски и проверки",
        ("бросок", "куб", "проверк", "d20", "dc", "сложность", "check", "стат"),
        (
            "Исход решает Python, не нейросеть.\n"
            "Проверка: d20 + модификатор стата против DC (сложности).\n"
            "• нат 20 — критический успех, даже если DC выше;\n"
            "• нат 1 — критический провал, даже если DC ниже;\n"
            "• иначе успех при total >= DC.\n"
            "Преимущество/помеха: два d20, берётся лучший/худший."
        ),
    ),
    RuleTopic(
        "combat",
        "Бой",
        ("бой", "атак", "урон", "инициатив", "крит", "combat", "hp", "состоян", "статус"),
        (
            "Инициатива: d20 + DEX, порядок по убыванию.\n"
            "Атака: d20 + STR/DEX против AC цели. Попадание → куб урона + мод.\n"
            "Крит (нат 20): удваиваются только кубы урона, не модификатор.\n"
            "Состояния тикают в начале хода: poisoned (1), burning (1d4), stunned (пропуск), blessed."
        ),
    ),
    RuleTopic(
        "time",
        "Время",
        ("время", "час", "день", "сон", "ночь", "рассвет", "time", "sleep"),
        (
            "Действия стоят игровых минут: осмотр 1, раунд боя 1, разговор 5, поиск 10.\n"
            f"Время не идёт назад. /sleep тянет до следующего рассвета ({tm.DAWN_HOUR:02d}:00) "
            "и восстанавливает HP/MP, снимая poisoned/burning/stunned.\n"
            "Освещение (день/сумерки/ночь) влияет на видимость и опознание."
        ),
    ),
    RuleTopic(
        "travel",
        "Карта и перемещение",
        ("карт", "локац", "перемещ", "travel", "туман", "fog", "дорога"),
        (
            "Мир — граф локаций; двигаться можно только по связям.\n"
            "Дорога тратит 30–480 минут (зависит от расстояния и опасности).\n"
            "Энкаунтер: d100 <= danger_level * 10.\n"
            "Туман войны: неоткрытые локации показаны как [??]."
        ),
    ),
    RuleTopic(
        "inventory",
        "Инвентарь и вес",
        ("инвентар", "вес", "перегруз", "экип", "предмет", "inventory", "carry", "equip"),
        (
            f"Грузоподъёмность = 10 + STR*5.\n"
            f"Перегруз даёт штраф к DEX (-{inv.OVERLOAD_DEX_PENALTY}).\n"
            "Типы: weapon, armor, consumable, quest, misc, spellbook.\n"
            "Валюты: gold, silver, copper (1:10:100)."
        ),
    ),
    RuleTopic(
        "npc",
        "NPC и репутация",
        ("npc", "репутац", "диалог", "фракц", "отношен", "rep", "говор"),
        (
            "Репутация NPC: -100..100. Бэнды: friendly >= 30, hostile <= -30, иначе neutral.\n"
            "Соцпроверка (persuade/intimidate/deceive): d20 + CHA против DC.\n"
            "Нат 20 → +8 репутации, провал → -2, нат 1 → -6.\n"
            "Убийство NPC необратимо без явного --force."
        ),
    ),
    RuleTopic(
        "crime",
        "Убийства, свидетели, расследование",
        ("убий", "свидетел", "улик", "расслед", "тело", "crime", "witness", "прят", "донес"),
        (
            "Репутация фракции падает НЕ от факта убийства, а когда об этом узнают и назовут убийцу.\n"
            "• Свидетели: скрытность убийцы d20+DEX+мод способа; свидетель d20+WIS — заметил ли;\n"
            "  опознание — отдельный порог (+5, ночью +10). Громкие способы (магия) видны всем.\n"
            f"• Донести готовы: своя фракция жертвы — да; союзники — нет; прочие — по кубу (d20 >= {crime.WILL_REPORT_DC}).\n"
            "• Тело: успешная уборка (d20+DEX vs DC) убирает улики и отменяет расследование.\n"
            f"• Расследование: через {crime.INVESTIGATION_DELAY_MINUTES // 60} игровых часов, если тело найдено.\n"
            f"• Штраф фракции: accusation {crime.FACTION_REP_PENALTY['accusation']}, suspicion/none — 0."
        ),
    ),
    RuleTopic(
        "canon",
        "Канон, сохранения, retcon",
        ("канон", "сохран", "retcon", "истори", "save", "load", "хеш", "ветк"),
        (
            "canon_log.jsonl — только дописывание, защищён цепочкой sha256.\n"
            "Правка старой записи ломает цепочку → загрузка падает с CanonIntegrityError.\n"
            "/retcon undo создаёт новую ветку; старый файл сохраняется как *.rejected.*.\n"
            "/retcon edit меняет только narration/flavor новым событием, не механики."
        ),
    ),
    RuleTopic(
        "llm",
        "Роль ИИ",
        ("ии", "нейрос", "llm", "нарратор", "модель", "narrator"),
        (
            "LLM — только рассказчик. Он (1) парсит интент игрока в JSON и (2) описывает\n"
            "уже посчитанный движком результат. Он не меняет числа, состояние и историю.\n"
            "Он видит лишь санитизированную выжимку состояния, не весь лог и не seed RNG.\n"
            "Без ключа игра работает в сухом режиме: механика полностью доступна."
        ),
    ),
    RuleTopic(
        "commands",
        "Команды",
        ("команд", "/", "help", "слэш", "slash"),
        (
            "/rule <тема> — эта справка (ничего не записывает).\n"
            "/roll <NdM+k> [причина] · /save [слот] · /load [слот] · /slots · /map ·\n"
            "/inv · /stats · /quests · /time · /party · /travel <id> · /talk <id> ·\n"
            "/say <текст> · /use <id> · /equip <id> · /unequip <id> · /snapshot ·\n"
            "/retcon ... · /model <name> · /fudge on|off · /export · /help · /exit"
        ),
    ),
)


_INDEX: dict[str, RuleTopic] = {topic.key: topic for topic in _TOPICS}


# ---------------------------------------------------------------------------
# Lookup
# ---------------------------------------------------------------------------


def topics() -> list[str]:
    return [topic.key for topic in _TOPICS]


def topic_titles() -> dict[str, str]:
    return {topic.key: topic.title for topic in _TOPICS}


def _match(query: str) -> RuleTopic | None:
    normalized = query.strip().lower()
    if not normalized:
        return None
    if normalized in _INDEX:
        return _INDEX[normalized]
    scored: list[tuple[int, RuleTopic]] = []
    for topic in _TOPICS:
        score = sum(1 for keyword in topic.keywords if keyword in normalized)
        if topic.key in normalized:
            score += 2
        if score:
            scored.append((score, topic))
    if not scored:
        return None
    scored.sort(key=lambda pair: pair[0], reverse=True)
    return scored[0][1]


def _state_extras(topic_key: str, state: Any) -> str:
    """Current read-only numbers relevant to a topic (never mutated)."""
    if state is None:
        return ""
    player = getattr(state, "player", None)
    if player is None:
        return ""
    if topic_key == "inventory":
        capacity = inv.carry_capacity(player)
        return f"\n\nТвои текущие значения: грузоподъёмность {capacity}."
    if topic_key == "checks":
        stats = ", ".join(f"{name} {value:+d}" for name, value in sorted(player.stats.items()))
        return f"\n\nТвои модификаторы: {stats}."
    if topic_key == "combat":
        return f"\n\nТвой HP {player.hp}/{player.max_hp}, AC {player.ac}."
    return ""


def explain(query: str, *, state: Any | None = None) -> HandbookResult:
    """Answer a rules question. Read-only: nothing is recorded or changed."""
    topic = _match(query)
    if topic is None:
        lines = ["Темы справки: " + ", ".join(topics())]
        lines.append(f"Спроси так: {DEFAULT_COMMAND} <тема>, например {DEFAULT_COMMAND} crime")
        text = "\n".join(lines)
        return HandbookResult(query=query, matched=None, text=text, suggested=topics())

    text = f"{topic.title}\n{topic.body}{_state_extras(topic.key, state)}"
    suggested = [key for key in topics() if key != topic.key][:3]
    return HandbookResult(query=query, matched=topic.key, text=text, suggested=suggested)
