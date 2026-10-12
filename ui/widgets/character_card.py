"""Character card: HP/MP bars, a vertical stat list, gear and clear money.

Emojis are optional (see :mod:`ui.glyphs`); when disabled, ASCII fallbacks keep
the columns perfectly aligned.
"""

from __future__ import annotations

from typing import Any

from rich.text import Text
from textual.widgets import Static

from engine.catalogs import Catalogs
from engine.state_manager import Character, GameState
from ui import glyphs
from ui.theme_gruvbox import GRUVBOX, currency_label, hp_bar, hp_color, mp_color, block_bar

BAR_WIDTH = 10

STAT_EMOJI: dict[str, str] = {
    "STR": "\U0001f4aa", "DEX": "\U0001f3c3", "INT": "\U0001f9e0",
    "WIS": "\U0001f441\ufe0f", "CHA": "\U0001f5e3\ufe0f", "LUK": "\U0001f340",
    "MAG": "\u2728",
}

COIN_EMOJI = {"gold": "\U0001f947", "silver": "\U0001f948", "copper": "\U0001f7e4"}
COIN_ASCII = {"gold": "G", "silver": "S", "copper": "C"}
COIN_RU = {"gold": "золото", "silver": "серебро", "copper": "медь"}


def _bar_line(emoji: str, ascii_label: str, label: str, current: int, maximum: int, color: str, width: int = BAR_WIDTH) -> Text:
    line = Text()
    line.append(f"{glyphs.pick(emoji, ascii_label)} {label} ", style=GRUVBOX["fg3"])
    line.append(block_bar(current, maximum, width), style=color)
    line.append(f" {current}/{maximum}", style=GRUVBOX["fg2"])
    return line


def _item_name(state: GameState, catalogs: Catalogs, instance_id: str | None) -> str | None:
    if not instance_id:
        return None
    instance = state.items.get(instance_id)
    if instance is None:
        return None
    return catalogs.item(instance.item_id).name


def _money_lines(character: Character) -> list[Text]:
    money = character.currency
    rows: list[Text] = [Text(glyphs.pick("\U0001f4b0 Деньги", "Деньги"), style=f"bold {GRUVBOX['yellow']}")]
    for key, label in COIN_RU.items():
        amount = getattr(money, key)
        line = Text("   ")
        line.append(f"{glyphs.pick(COIN_EMOJI[key], COIN_ASCII[key])} {label:<8}", style=GRUVBOX["fg2"])
        line.append(f"{amount:>4}", style=GRUVBOX["yellow"])
        rows.append(line)
    total = Text("   ")
    total.append("= всего      ", style=GRUVBOX["fg3"])
    total.append(currency_label(money.gold, money.silver, money.copper), style=GRUVBOX["yellow"])
    total.append(f"  ({money.to_copper()} меди)", style=GRUVBOX["fg4"])
    rows.append(total)
    return rows


def render_card(character: Character, state: GameState, catalogs: Catalogs) -> Text:
    """Render a character card as a multi-line rich Text."""
    lines: list[Text] = []

    header = Text()
    suit = glyphs.pick("\u2660", "#")
    header.append(f"{suit} {character.name}", style=f"bold {GRUVBOX['fg0']}")
    header.append(f"  Lv.{character.level}", style=GRUVBOX["yellow"])
    if not character.alive:
        header.append(glyphs.pick("  \U0001f480", "  (мертв)"), style=GRUVBOX["red"])
    lines.append(header)

    lines.append(_bar_line("\u2764\ufe0f", "", "HP", character.hp, character.max_hp, hp_color(character.hp, character.max_hp)))
    lines.append(_bar_line("\U0001f499", "", "MP", character.mp, character.max_mp, mp_color(character.mp, character.max_mp)))
    if character.ac:
        ac_mark = glyphs.pick("\U0001f6e1 ", "")
        lines.append(Text(f"{ac_mark}AC {character.ac}", style=GRUVBOX["blue"]))

    lines.append(Text(glyphs.pick("\U0001f4ca Характеристики", "Характеристики"), style=f"bold {GRUVBOX['aqua']}"))
    for name, value in character.stats.items():
        marker = glyphs.pick(STAT_EMOJI.get(name, "\u2022"), "\u2022")
        row = Text("   ")
        row.append(f"{marker} {name:<4}", style=GRUVBOX["fg3"])
        row.append(f"{value:+d}", style=GRUVBOX["fg0"])
        lines.append(row)

    lines.append(Text(glyphs.pick("\U0001f9f0 Снаряжение", "Снаряжение"), style=f"bold {GRUVBOX['aqua']}"))
    weapon = _item_name(state, catalogs, character.equipped.get("weapon"))
    w_mark = glyphs.pick("\u2694\ufe0f", "[w]")
    lines.append(Text(f"   {w_mark} {weapon or '—'}", style=GRUVBOX["orange"]))
    armor = _item_name(state, catalogs, character.equipped.get("armor"))
    a_mark = glyphs.pick("\U0001f6e1\ufe0f", "[a]")
    lines.append(Text(f"   {a_mark} {armor or '—'}", style=GRUVBOX["blue"]))

    lines.extend(_money_lines(character))

    if character.statuses:
        lines.append(Text(glyphs.pick("\U0001f4a5 ", "! ") + " ".join(character.statuses), style=GRUVBOX["red"]))

    return _join(lines)


def _join(lines: list[Text]) -> Text:
    out = Text()
    for index, line in enumerate(lines):
        if index:
            out.append("\n")
        out.append_text(line)
    return out


class CharacterCard(Static):
    def update_from(self, state: GameState, catalogs: Catalogs, *, companion: Any = None) -> None:
        self.update(render_card(companion or state.player, state, catalogs))
