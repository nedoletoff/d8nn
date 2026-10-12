"""Inventory tab: equipment, backpack, weight and encumbrance."""

from __future__ import annotations

from rich.text import Text
from textual.widgets import Static

from engine import inventory_engine as inv
from engine.catalogs import Catalogs
from engine.state_manager import Character, GameState
from ui import glyphs
from ui.theme_gruvbox import GRUVBOX, currency_label


def render_inventory(state: GameState, catalogs: Catalogs, character: Character) -> Text:
    out = Text()
    out.append("\u25c8 ИНВЕНТАРЬ", style=f"bold {GRUVBOX['aqua']}")
    out.append("\n")

    weight = inv.total_weight(state, catalogs, character)
    capacity = inv.carry_capacity(character, state.rules)
    over = weight > capacity
    style = GRUVBOX["red"] if over else GRUVBOX["fg2"]
    weight_mark = glyphs.pick("\U0001f9f3 ", "")
    out.append(f"{weight_mark}вес {weight:g}/{capacity}", style=style)
    if over:
        warn = glyphs.pick("\u26a0\ufe0f ", "! ")
        out.append(
            f"  {warn}ПЕРЕГРУЗ (DEX -{state.rules.overload_dex_penalty})",
            style=f"bold {GRUVBOX['red']}",
        )
    out.append("\n\n")

    out.append("Надето:\n", style=GRUVBOX["fg3"])
    for slot, instance_id in sorted(character.equipped.items()):
        if not instance_id:
            continue
        instance = state.items.get(instance_id)
        if instance is None:
            continue
        item = catalogs.item(instance.item_id)
        out.append(f"  {slot}: {item.name}\n", style=GRUVBOX["orange"])

    out.append("Сумка:\n", style=GRUVBOX["fg3"])
    if not character.inventory:
        out.append("  (пусто)\n", style=GRUVBOX["fg4"])
    for instance_id in character.inventory:
        instance = state.items.get(instance_id)
        if instance is None:
            continue
        item = catalogs.item(instance.item_id)
        out.append(f"  {item.name} x{instance.quantity}\n", style=GRUVBOX["fg2"])

    money = character.currency
    money_mark = glyphs.pick("\U0001f4b0 ", "")
    out.append(
        f"\n{money_mark}{currency_label(money.gold, money.silver, money.copper)}",
        style=GRUVBOX["yellow"],
    )
    return out


class InventoryTab(Static):
    def update_from(self, state: GameState, catalogs: Catalogs, *, character: Character | None = None) -> None:
        self.update(render_inventory(state, catalogs, character or state.player))
