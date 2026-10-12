"""Quests & NPC log tab."""

from __future__ import annotations

from rich.text import Text
from textual.widgets import Static

from engine.catalogs import Catalogs
from engine.state_manager import GameState
from ui.theme_gruvbox import GRUVBOX

_STATUS_RU = {
    "active": "активен",
    "completed": "выполнен",
    "failed": "провален",
    "abandoned": "брошен",
}
_STATUS_STYLE = {
    "active": GRUVBOX["yellow"],
    "completed": GRUVBOX["green"],
    "failed": GRUVBOX["red"],
    "abandoned": GRUVBOX["fg4"],
}


def render_quests(state: GameState, catalogs: Catalogs) -> Text:
    out = Text()
    out.append("\u25c8 КВЕСТЫ", style=f"bold {GRUVBOX['aqua']}")
    out.append("\n")

    if not state.quests:
        out.append("(пока никаких дел)\n", style=GRUVBOX["fg4"])
    for quest in state.quests.values():
        definition = catalogs.quests.get(quest.id)
        title = definition.title if definition else quest.id
        out.append(f"\u2022 {title} ", style=f"bold {GRUVBOX['fg0']}")
        out.append(
            f"[{_STATUS_RU.get(quest.status, quest.status)}]\n",
            style=_STATUS_STYLE.get(quest.status, GRUVBOX["fg2"]),
        )
        out.append(f"    стадия: {quest.stage}\n", style=GRUVBOX["fg3"])

    out.append("\n\u25c8 ЛОГ NPC\n", style=f"bold {GRUVBOX['aqua']}")
    if not state.npcs:
        out.append("(никого не встречено)\n", style=GRUVBOX["fg4"])
    for npc_id, npc in state.npcs.items():
        definition = catalogs.npcs.get(npc_id)
        name = definition.name if definition else npc_id
        style = GRUVBOX["red"] if npc.status == "dead" else GRUVBOX["fg2"]
        out.append(f"\u2022 {name} [{npc.status}] rep {npc.rep:+d}\n", style=style)
    return out


class QuestTab(Static):
    def update_from(self, state: GameState, catalogs: Catalogs) -> None:
        self.update(render_quests(state, catalogs))
