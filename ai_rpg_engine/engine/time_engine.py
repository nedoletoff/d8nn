"""In-game time: days, hours, minutes. It never flows backwards.

Every player action costs in-game minutes (spec section 4). Day/night drives
visibility, encounter odds and NPC moods. ``/sleep`` fast-forwards to the next
dawn and restores the party.
"""

from __future__ import annotations

from typing import Literal

from engine.canon_log import TimeStamp
from engine.state_manager import GameState

MINUTES_PER_HOUR = 60
MINUTES_PER_DAY = 24 * MINUTES_PER_HOUR

DAWN_HOUR = 6
DAY_HOUR = 8
DUSK_HOUR = 18
NIGHT_HOUR = 20

LightLevel = Literal["night", "dawn", "day", "dusk"]

LIGHT_LABEL: dict[LightLevel, str] = {
    "night": "Ночное",
    "dawn": "Рассвет",
    "day": "Дневное",
    "dusk": "Закат",
}

# Cost of common actions, in minutes.
COST_OBSERVE = 1
COST_COMBAT_ROUND = 1
COST_TALK = 5
COST_SEARCH = 10
COST_SHORT_REST = 30

_TRAVEL_MIN_PER_DISTANCE = 30
_TRAVEL_MIN = 30
_TRAVEL_MAX = 480


class TimeError(ValueError):
    """Raised when time would be moved backwards."""


def absolute_minutes(stamp: TimeStamp) -> int:
    return (stamp.day - 1) * MINUTES_PER_DAY + stamp.hour * MINUTES_PER_HOUR + stamp.minute


def from_minutes(total: int) -> TimeStamp:
    total = max(0, int(total))
    day, remainder = divmod(total, MINUTES_PER_DAY)
    hour, minute = divmod(remainder, MINUTES_PER_HOUR)
    return TimeStamp(day=day + 1, hour=hour, minute=minute)


def advance(state: GameState, minutes: int) -> TimeStamp:
    """Move time forward by ``minutes`` (must be >= 0)."""
    if minutes < 0:
        raise TimeError("time cannot go backwards")
    state.time = from_minutes(absolute_minutes(state.time) + minutes)
    return state.time


def advance_hours(state: GameState, hours: int) -> TimeStamp:
    return advance(state, hours * MINUTES_PER_HOUR)


def advance_days(state: GameState, days: int) -> TimeStamp:
    return advance(state, days * MINUTES_PER_DAY)


def advance_to_hour(state: GameState, hour: int, *, include_current: bool = False) -> TimeStamp:
    """Advance to the next occurrence of ``hour``:00.

    If the clock is already at ``hour``:00 the jump goes to the next day unless
    ``include_current`` is set.
    """
    current = absolute_minutes(state.time)
    target = (state.time.day - 1) * MINUTES_PER_DAY + hour * MINUTES_PER_HOUR
    if target < current or (target == current and not include_current):
        target += MINUTES_PER_DAY
    state.time = from_minutes(target)
    return state.time


def light_level(state: GameState) -> LightLevel:
    hour = state.time.hour
    if DAWN_HOUR <= hour < DAY_HOUR:
        return "dawn"
    if DAY_HOUR <= hour < DUSK_HOUR:
        return "day"
    if DUSK_HOUR <= hour < NIGHT_HOUR:
        return "dusk"
    return "night"


def is_night(state: GameState) -> bool:
    return light_level(state) == "night"


def describe(state: GameState) -> str:
    """Short status line for the header bar."""
    weather = state.flags.get("weather")
    parts = [
        f"День {state.time.day}",
        f"{state.time.hour:02d}:{state.time.minute:02d}",
    ]
    if weather:
        parts.append(str(weather))
    parts.append(f"Освещение: {LIGHT_LABEL[light_level(state)]}")
    return " · ".join(parts)


def travel_minutes(
    origin: tuple[int, int],
    destination: tuple[int, int],
    danger_level: int = 0,
) -> int:
    """Manhattan distance in map units -> travel minutes (30..480)."""
    distance = abs(origin[0] - destination[0]) + abs(origin[1] - destination[1])
    minutes = max(1, distance) * _TRAVEL_MIN_PER_DISTANCE + danger_level * 15
    return max(_TRAVEL_MIN, min(_TRAVEL_MAX, minutes))


def long_rest(state: GameState) -> int:
    """Sleep until the next dawn. Restores HP/MP and clears minor statuses.

    Returns the number of in-game minutes spent.
    """
    before = absolute_minutes(state.time)
    advance_to_hour(state, DAWN_HOUR)
    spent = absolute_minutes(state.time) - before

    for character in [state.player, *state.party]:
        if not character.alive:
            continue
        character.hp = character.max_hp
        character.mp = character.max_mp
        for status in ("poisoned", "burning", "stunned"):
            if status in character.statuses:
                character.statuses.remove(status)
    return spent
