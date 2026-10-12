"""Party panel: compact cards for companions."""

from __future__ import annotations

from rich.text import Text
from textual.widgets import Static

from engine.catalogs import Catalogs
from engine.state_manager import GameState
from ui import glyphs
from ui.theme_gruvbox import GRUVBOX, hp_bar, hp_color


def render_party(state: GameState, catalogs: Catalogs) -> Text:
    out = Text()
    out.append("\u25c8 ОТРЯД", style=f"bold {GRUVBOX['aqua']}")
    if not state.party:
        out.append("\n(один в этой тьме)", style=GRUVBOX["fg4"])
        return out

    for member in state.party:
        out.append("\n")
        dragon = glyphs.pick("\U0001f409", "~")
        name = Text(f"{dragon} {member.name}", style=f"bold {GRUVBOX['fg0']}")
        if not member.alive:
            name.stylize(GRUVBOX["red"])
            name.append("  (пал)", style=GRUVBOX["red"])
        out.append_text(name)
        out.append("\n")
        bar = Text()
        bar.append("HP ", style=GRUVBOX["fg3"])
        bar.append(hp_bar(member.hp, member.max_hp, 10), style=hp_color(member.hp, member.max_hp))
        bar.append(f" {member.hp}/{member.max_hp}", style=GRUVBOX["fg2"])
        out.append_text(bar)
        out.append("\n")
        out.append(" ".join(f"{k}{v:+d}" for k, v in member.stats.items()), style=GRUVBOX["fg3"])
    return out


class PartyPanel(Static):
    def update_from(self, state: GameState, catalogs: Catalogs) -> None:
        self.update(render_party(state, catalogs))
