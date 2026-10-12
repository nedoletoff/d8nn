"""Dialogue panel: the current NPC (name, status, reputation, portrait)."""

from __future__ import annotations

from rich.text import Text
from textual.widgets import Static

from engine.catalogs import Catalogs, NpcDef
from engine.state_manager import NPCState
from ui.theme_gruvbox import GRUVBOX

_STATUS_STYLE = {
    "alive": GRUVBOX["green"],
    "dead": GRUVBOX["red"],
    "missing": GRUVBOX["yellow"],
}

_DISPOSITION_RU = {"hostile": "враждебен", "neutral": "нейтрален", "friendly": "дружелюбен"}


def render_dialogue(npc: NPCState | None, definition: NpcDef | None) -> Text:
    out = Text()
    out.append("\u25c8 ДИАЛОГ / NPC", style=f"bold {GRUVBOX['aqua']}")
    if npc is None or definition is None:
        out.append("\n(пусто — вне диалога)", style=GRUVBOX["fg4"])
        return out

    out.append("\n")
    out.append(definition.name, style=f"bold {GRUVBOX['fg0']}")
    out.append(f"  [{npc.status}]", style=_STATUS_STYLE.get(npc.status, GRUVBOX["fg2"]))
    out.append("\n")
    out.append(
        f"репутация {npc.rep:+d} · {_DISPOSITION_RU.get(npc.disposition, npc.disposition)}",
        style=GRUVBOX["yellow"],
    )
    if npc.permanently_dead:
        out.append("\nмертв навсегда", style=f"bold {GRUVBOX['red']}")
    if definition.portrait:
        out.append("\n")
        out.append(definition.portrait, style=GRUVBOX["purple"])
    out.append("\n")
    out.append(definition.description, style=GRUVBOX["fg2"])
    return out


class DialoguePanel(Static):
    def update_from(self, npc: NPCState | None, definition: NpcDef | None) -> None:
        self.update(render_dialogue(npc, definition))
