"""Gruvbox Dark (Medium) palette and status-color helpers.

The whole UI is Gruvbox only: no default terminal colors anywhere. This module
exposes the palette, the CSS variables handed to Textual, an optional Textual
``Theme`` object and small helpers that map game state to colors / block bars.
"""

from __future__ import annotations

from typing import Any, Literal

from engine.dice_engine import Outcome

GRUVBOX: dict[str, str] = {
    "bg0_hard": "#1d2021",
    "bg0": "#282828",   # main background
    "bg1": "#3c3836",   # panel background
    "bg2": "#504945",   # borders, hover
    "bg3": "#665c54",
    "bg4": "#7c6f64",
    "fg0": "#fbf1c7",   # primary text
    "fg1": "#ebdbb2",
    "fg2": "#d5c4a1",
    "fg3": "#bdae93",
    "fg4": "#a89984",
    # Saturated Gruvbox-bright palette (a touch more vivid than medium).
    "red": "#ff5c57",    # HP low / danger
    "green": "#c6dc4f",  # HP high / success
    "yellow": "#ffce4a", # warning / currency
    "blue": "#6fc3e2",   # info / mana
    "purple": "#e57db0", # magic / crit
    "aqua": "#8ee88a",   # heal / success
    "orange": "#ff8a2b", # accent / fire / initiative
    "black": "#1c1c1c",  # critical failure (used as text on a bright background)
}

#: CSS variables exposed to ``gruvbox.tcss`` via ``App.get_css_variables``.
CSS_VARIABLES: dict[str, str] = {
    "bg": GRUVBOX["bg0"],
    "bgdark": GRUVBOX["bg0_hard"],
    "panel": GRUVBOX["bg1"],
    "surface": GRUVBOX["bg2"],
    "hover": GRUVBOX["bg3"],
    "muted": GRUVBOX["fg4"],
    "fg": GRUVBOX["fg1"],
    "fgbright": GRUVBOX["fg0"],
    "fg2": GRUVBOX["fg2"],
    "fg3": GRUVBOX["fg3"],
    "fg4": GRUVBOX["fg4"],
    "accent": GRUVBOX["orange"],
    "red": GRUVBOX["red"],
    "green": GRUVBOX["green"],
    "yellow": GRUVBOX["yellow"],
    "blue": GRUVBOX["blue"],
    "purple": GRUVBOX["purple"],
    "aqua": GRUVBOX["aqua"],
    "orange": GRUVBOX["orange"],
}

HP_HIGH = 0.66
HP_LOW = 0.33

BLOCK_FULL = "\u2588"  # █
BLOCK_EMPTY = "\u2591"  # ░

ColorKey = Literal["red", "green", "yellow", "blue", "purple", "aqua", "orange"]


def color(name: ColorKey) -> str:
    return GRUVBOX[name]


def hp_color(current: int, maximum: int) -> str:
    """>66% green, 33–66% yellow, <33% red."""
    ratio = 1.0 if maximum <= 0 else current / maximum
    if ratio > HP_HIGH:
        return GRUVBOX["green"]
    if ratio >= HP_LOW:
        return GRUVBOX["yellow"]
    return GRUVBOX["red"]


def mp_color(current: int, maximum: int) -> str:
    return GRUVBOX["blue"]


def currency_color() -> str:
    return GRUVBOX["yellow"]


def outcome_color(outcome: Outcome | None) -> str:
    """Crit success → purple, success → green, failure → red, crit failure → black."""
    return {
        "crit_success": GRUVBOX["purple"],
        "success": GRUVBOX["green"],
        "failure": GRUVBOX["red"],
        "crit_failure": GRUVBOX["black"],
    }.get(outcome or "", GRUVBOX["fg2"])


def danger_color(level: int) -> str:
    if level <= 1:
        return GRUVBOX["green"]
    if level <= 3:
        return GRUVBOX["yellow"]
    return GRUVBOX["red"]


def block_bar(current: int, maximum: int, width: int = 10) -> str:
    """Render a block progress bar such as ``████████░░``."""
    if width <= 0:
        return ""
    ratio = 0.0 if maximum <= 0 else max(0.0, min(1.0, current / maximum))
    filled = int(round(ratio * width))
    return BLOCK_FULL * filled + BLOCK_EMPTY * (width - filled)


def hp_bar(current: int, maximum: int, width: int = 10) -> str:
    return block_bar(current, maximum, width)


def currency_label(gold: int, silver: int, copper: int) -> str:
    """Format money the way the header shows it, e.g. ``6с · 32м``."""
    parts: list[str] = []
    if gold:
        parts.append(f"{gold}з")
    if silver:
        parts.append(f"{silver}с")
    if copper or not parts:
        parts.append(f"{copper}м")
    return " · ".join(parts)


def get_css_variables() -> dict[str, str]:
    """Return the mapping Textual should register for ``$name`` references."""
    return dict(CSS_VARIABLES)


def gruvbox_theme() -> Any | None:
    """Build a Textual ``Theme`` for Gruvbox Dark, or ``None`` if unsupported."""
    try:
        from textual.theme import Theme
    except Exception:  # pragma: no cover - very old textual
        return None
    try:
        return Theme(
            name="gruvbox-dark",
            primary=GRUVBOX["orange"],
            secondary=GRUVBOX["blue"],
            warning=GRUVBOX["yellow"],
            error=GRUVBOX["red"],
            success=GRUVBOX["green"],
            accent=GRUVBOX["aqua"],
            foreground=GRUVBOX["fg1"],
            background=GRUVBOX["bg0"],
            surface=GRUVBOX["bg1"],
            panel=GRUVBOX["bg2"],
            dark=True,
            variables=dict(CSS_VARIABLES),
        )
    except Exception:  # pragma: no cover - defensive
        return None
