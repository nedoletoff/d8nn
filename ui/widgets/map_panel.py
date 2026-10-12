"""Map panel: a chessboard-style grid with axes and a scale.

Cell padding is computed with :func:`rich.cells.cell_len`, so wide emoji and
CJK glyphs do not break column alignment. When emojis are disabled the cells use
2-letter terrain codes instead.
"""

from __future__ import annotations

from rich.cells import cell_len
from rich.text import Text
from textual.widgets import Static

from engine.catalogs import Catalogs
from engine.state_manager import GameState
from engine.world_map import (
    FOG_CELL,
    TERRAIN_CODE,
    TERRAIN_EMOJI,
    column_label,
    grid_bounds,
    grid_tokens,
    position_label,
)
from ui import glyphs
from ui.theme_gruvbox import GRUVBOX

CELL_WIDTH = 6
MINUTES_PER_CELL = 30
FOG_MARK = "\u00b7"  # ·


def _center(text: str, width: int) -> str:
    pad = max(0, width - cell_len(text))
    left = pad // 2
    return " " * left + text + " " * (pad - left)


def _cell_symbol(terrain: str | None, fog: bool) -> str:
    terrain = terrain or "default"
    if fog:
        return FOG_CELL
    return glyphs.pick(TERRAIN_EMOJI.get(terrain, TERRAIN_EMOJI["default"]), TERRAIN_CODE.get(terrain, ".."))


def render_map(state: GameState, catalogs: Catalogs, *, reveal: bool = False) -> Text:
    min_x, max_x, min_y, max_y = grid_bounds(catalogs)
    letters = [column_label(x, catalogs) for x in range(min_x, max_x + 1)]
    tokens = grid_tokens(state, catalogs, reveal=reveal)

    out = Text()
    out.append("\u25c8 КАРТА", style=f"bold {GRUVBOX['aqua']}")
    out.append(f"   ты здесь: {position_label(state, catalogs)}\n", style=f"bold {GRUVBOX['orange']}")

    pad = "      "
    out.append(pad + " ".join(_center(letter, CELL_WIDTH) for letter in letters) + "\n", style=GRUVBOX["fg3"])

    top = pad + "\u250c" + "\u252c".join("\u2500" * CELL_WIDTH for _ in letters) + "\u2510"
    mid = pad + "\u251c" + "\u253c".join("\u2500" * CELL_WIDTH for _ in letters) + "\u2524"
    bot = pad + "\u2514" + "\u2534".join("\u2500" * CELL_WIDTH for _ in letters) + "\u2518"

    for index, row_y in enumerate(range(max_y, min_y - 1, -1)):
        out.append((top if index == 0 else mid) + "\n", style=GRUVBOX["bg3"])
        out.append(f"{row_y:>4}  ", style=GRUVBOX["fg3"])
        out.append("\u2502", style=GRUVBOX["bg3"])
        for x_index, token in enumerate(tokens[index]):
            cell_x = min_x + x_index
            location = next(
                (loc for loc in catalogs.locations.values() if loc.coords.x == cell_x and loc.coords.y == row_y),
                None,
            )
            fog = token.startswith(FOG_MARK)
            symbol = _cell_symbol(location.terrain if location else None, fog)
            if fog:
                style = GRUVBOX["fg4"]
            elif state.world.current_location == (location.id if location else ""):
                style = f"bold {GRUVBOX['orange']}"
            else:
                style = GRUVBOX["fg2"]
            out.append(_center(symbol, CELL_WIDTH), style=style)
            out.append("\u2502", style=GRUVBOX["bg3"])
        out.append(f"  {row_y:<4}", style=GRUVBOX["fg3"])
        out.append("\n")

    out.append(bot + "\n", style=GRUVBOX["bg3"])
    out.append(pad + " ".join(_center(letter, CELL_WIDTH) for letter in letters) + "\n", style=GRUVBOX["fg3"])
    present = sorted({loc.terrain for loc in catalogs.locations.values()})
    if glyphs.emoji_on():
        legend = " ".join(f"{_cell_symbol(t, False)}={TERRAIN_CODE.get(t, '..')}" for t in present)
    else:
        legend = "коды: " + " ".join(TERRAIN_CODE.get(t, "..") for t in present)
    out.append(f"   {legend}\n", style=GRUVBOX["fg4"])
    out.append(
        f"   масштаб: 1 клетка \u2248 {MINUTES_PER_CELL} мин пути   ({FOG_CELL}=туман)",
        style=GRUVBOX["fg4"],
    )
    return out


class MapPanel(Static):
    def update_from(self, state: GameState, catalogs: Catalogs, *, reveal: bool = False) -> None:
        self.update(render_map(state, catalogs, reveal=reveal))
