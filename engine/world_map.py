"""The world graph: locations, travel, fog-of-war and an ASCII map.

Movement is only allowed between connected locations. Travel costs in-game time
and may trigger an encounter (``d100 <= danger_level * 10``). Undiscovered
locations are rendered as ``[??]`` until the party reaches them.
"""

from __future__ import annotations

from dataclasses import dataclass

from engine import time_engine as tm
from engine.catalogs import Catalogs, LocationDef
from engine.rules_engine import encounter_chance as _rules_encounter_chance
from engine.state_manager import GameState, LocationState, StateManager

ENCOUNTER_DANGER_STEP = 10

FOG_LABEL = "[??]"

TERRAIN_EMOJI: dict[str, str] = {
    "village": "\U0001f3d8\ufe0f",   # 🏘️
    "forest": "\U0001f332",           # 🌲
    "marsh": "\U0001f32b\ufe0f",      # 🌫️
    "bridge": "\U0001f309",           # 🌉
    "cave": "\U0001f573\ufe0f",       # 🕳️
    "water": "\U0001f30a",            # 🌊
    "ruin": "\U0001f3da\ufe0f",       # 🏚️
    "road": "\U0001f6e4\ufe0f",       # 🛤️
    "mountain": "\u26f0\ufe0f",       # ⛰️
    "school": "\U0001f3f0",           # 🏰
    "library": "\U0001f4da",          # 📚
    "garden": "\U0001f33f",           # 🌿
    "tower": "\U0001f5fc",            # 🗼
    "city": "\U0001f3d9\ufe0f",       # 🏙️
    "market": "\U0001f3ec",           # 🏬
    "desert": "\U0001f3dc\ufe0f",     # 🏜️
    "oasis": "\U0001f334",            # 🌴
    "camp": "\u26fa",                 # ⛺
    "default": "\u2b1c",              # ⬜
}

TERRAIN_CODE: dict[str, str] = {
    "village": "VI", "forest": "FR", "marsh": "MA", "bridge": "BR",
    "cave": "CA", "water": "WA", "ruin": "RU", "road": "RD",
    "mountain": "MT", "school": "SC", "library": "LB", "garden": "GR",
    "tower": "TW", "city": "CI", "market": "MK", "desert": "DE",
    "oasis": "OA", "camp": "CP", "default": "..",
}

FOG_CELL = "\u2593\u2593"  # ▓▓
FOG_CODE = "···"


class WorldError(ValueError):
    """Raised on invalid movement."""


@dataclass
class TravelResult:
    origin: str
    destination: str
    minutes: int
    encounter: bool
    newly_discovered: bool


# ---------------------------------------------------------------------------
# Fog of war & graph queries
# ---------------------------------------------------------------------------


def is_discovered(state: GameState, location_id: str) -> bool:
    return state.world.is_discovered(location_id)


def discover(state: GameState, location_id: str) -> bool:
    """Reveal a location. Returns True if it was newly discovered."""
    entry = state.world.locations.get(location_id)
    if entry is None:
        entry = LocationState(id=location_id)
        state.world.locations[location_id] = entry
    if entry.discovered:
        return False
    entry.discovered = True
    return True


def connected(catalogs: Catalogs, origin_id: str, destination_id: str) -> bool:
    origin = catalogs.location(origin_id)
    return destination_id in origin.connections


def neighbors(catalogs: Catalogs, location_id: str) -> list[str]:
    return list(catalogs.location(location_id).connections)


def visible_exits(state: GameState, catalogs: Catalogs) -> list[str]:
    """Discovered neighbours of the current location (safe for the LLM)."""
    current = state.world.current_location
    exits = []
    for location_id in neighbors(catalogs, current):
        if is_discovered(state, location_id):
            exits.append(location_id)
    return exits


# ---------------------------------------------------------------------------
# Encounters
# ---------------------------------------------------------------------------


def encounter_chance(state: GameState, danger_level: int) -> int:
    """Encounter probability in percent, honouring the world rules."""
    return _rules_encounter_chance(state.rules, danger_level, step=ENCOUNTER_DANGER_STEP)


def roll_encounter(manager: StateManager, danger_level: int) -> bool:
    if danger_level <= 0 or not manager.state.rules.toggles.encounters_enabled:
        return False
    engine = manager.dice_engine()
    roll = engine.roll("d100")
    manager.store_dice(engine)
    return roll.total <= encounter_chance(manager.state, danger_level)


# ---------------------------------------------------------------------------
# Travel
# ---------------------------------------------------------------------------


def travel(
    manager: StateManager,
    catalogs: Catalogs,
    destination_id: str,
    *,
    force_encounter: bool | None = None,
) -> TravelResult:
    """Move the party to an adjacent, connected location."""
    state = manager.state
    origin_id = state.world.current_location
    if not connected(catalogs, origin_id, destination_id):
        raise WorldError(f"{origin_id} is not connected to {destination_id}")

    origin = catalogs.location(origin_id)
    destination = catalogs.location(destination_id)

    minutes = tm.travel_minutes(
        (origin.coords.x, origin.coords.y),
        (destination.coords.x, destination.coords.y),
        destination.danger_level,
    )
    tm.advance(state, minutes)

    newly_discovered = discover(state, destination_id)
    entry = state.world.locations[destination_id]
    entry.visited = True
    state.world.current_location = destination_id

    if force_encounter is None:
        encounter = roll_encounter(manager, destination.danger_level)
    else:
        encounter = force_encounter

    effects: list[dict[str, object]] = [
        {"kind": "discover", "target": destination_id, "value": destination_id}
    ]
    if encounter:
        state.flags["encounter_id"] = f"enc_{destination_id}"

    manager.append_event(
        type="move",
        t=state.time,
        actor="player",
        input=f"travel {destination_id}",
        intent={"action_type": "move", "destination": destination_id},
        roll={"encounter": encounter, "danger": destination.danger_level},
        effects=effects,
    )
    return TravelResult(
        origin=origin_id,
        destination=destination_id,
        minutes=minutes,
        encounter=encounter,
        newly_discovered=newly_discovered,
    )


# ---------------------------------------------------------------------------
# Grid / chessboard
# ---------------------------------------------------------------------------


def grid_bounds(catalogs: Catalogs) -> tuple[int, int, int, int]:
    """(min_x, max_x, min_y, max_y) across all known locations."""
    xs = [loc.coords.x for loc in catalogs.locations.values()] or [0]
    ys = [loc.coords.y for loc in catalogs.locations.values()] or [0]
    return min(xs), max(xs), min(ys), max(ys)


def column_label(x: int, catalogs: Catalogs) -> str:
    """Map an x coordinate to a chess-style column letter (a, b, c...)."""
    min_x, _, _, _ = grid_bounds(catalogs)
    return chr(ord("a") + (x - min_x))


def cell_at(catalogs: Catalogs, x: int, y: int) -> LocationDef | None:
    for location in catalogs.locations.values():
        if location.coords.x == x and location.coords.y == y:
            return location
    return None


def position_label(state: GameState, catalogs: Catalogs) -> str:
    current = catalogs.location(state.world.current_location)
    return f"{column_label(current.coords.x, catalogs)}{current.coords.y}"


def grid_tokens(state: GameState, catalogs: Catalogs, *, reveal: bool = False) -> list[list[str]]:
    """Rows (top row first) of fixed 3-char cell tokens, for the LLM and UI."""
    min_x, max_x, min_y, max_y = grid_bounds(catalogs)
    rows: list[list[str]] = []
    for y in range(max_y, min_y - 1, -1):
        row: list[str] = []
        for x in range(min_x, max_x + 1):
            location = cell_at(catalogs, x, y)
            if location is None:
                row.append(FOG_CODE)
                continue
            known = reveal or state.world.is_discovered(location.id)
            if not known:
                row.append(FOG_CODE)
                continue
            code = TERRAIN_CODE.get(location.terrain, "..")[:2]
            current = state.world.current_location == location.id
            row.append(f"{code}{'*' if current else ' '}")
        rows.append(row)
    return rows


def grid_ascii(state: GameState, catalogs: Catalogs, *, reveal: bool = False) -> str:
    """Plain-text chessboard (safe for LLM context)."""
    min_x, max_x, min_y, max_y = grid_bounds(catalogs)
    letters = [column_label(x, catalogs) for x in range(min_x, max_x + 1)]
    header = "    " + " ".join(f"{letter:>3}" for letter in letters)
    lines = [header]
    tokens = grid_tokens(state, catalogs, reveal=reveal)
    for index, y in enumerate(range(max_y, min_y - 1, -1)):
        cells = " ".join(f"{token:>3}" for token in tokens[index])
        lines.append(f"{y:>3} {cells}")
    lines.append(grid_legend(catalogs))
    return "\n".join(lines)


def grid_legend(catalogs: Catalogs) -> str:
    parts = [f"{TERRAIN_EMOJI.get(terrain, '')}{code}" for terrain, code in TERRAIN_CODE.items()]
    return "     легенда: " + " ".join(parts) + "   ···=туман  * =ты здесь"


# ---------------------------------------------------------------------------
# ASCII map (legacy, used in tests)
# ---------------------------------------------------------------------------


def _cell_label(state: GameState, location: LocationDef, reveal: bool) -> str:
    known = reveal or is_discovered(state, location.id)
    if not known:
        return FOG_LABEL
    if state.world.current_location == location.id:
        return f"\u25ba{location.name}\u25c4"
    return location.name


def ascii_map(state: GameState, catalogs: Catalogs, *, reveal: bool = False) -> str:
    """Render a minimal fog-of-war ASCII map of known locations."""
    locations = list(catalogs.locations.values())
    if not locations:
        return "(карта пуста)"

    xs = [loc.coords.x for loc in locations]
    ys = [loc.coords.y for loc in locations]
    cell_width = max(len(_cell_label(state, loc, reveal)) for loc in locations) + 2
    by_coord = {(loc.coords.x, loc.coords.y): loc for loc in locations}

    rows: list[str] = []
    for y in range(max(ys), min(ys) - 1, -1):
        row = ""
        for x in range(min(xs), max(xs) + 1):
            location = by_coord.get((x, y))
            label = _cell_label(state, location, reveal) if location else ""
            row += label.ljust(cell_width)
        rows.append(row.rstrip())
    return "\n".join(rows)
