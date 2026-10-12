"""Tests for the Gruvbox theme helpers and the stylesheet variables."""

from __future__ import annotations

import re
from pathlib import Path

from ui import theme_gruvbox as theme

CSS_PATH = Path(__file__).resolve().parents[1] / "ui" / "css" / "gruvbox.tcss"


def test_palette_is_gruvbox_dark() -> None:
    assert theme.GRUVBOX["bg0"] == "#282828"
    assert theme.GRUVBOX["red"].startswith("#")
    assert theme.GRUVBOX["green"].startswith("#")
    # saturated variants differ from the stock medium values
    assert theme.GRUVBOX["red"] != "#fb4934"
    assert theme.GRUVBOX["blue"] != "#83a598"


def test_hp_color_bands() -> None:
    assert theme.hp_color(20, 25) == theme.GRUVBOX["green"]
    assert theme.hp_color(12, 25) == theme.GRUVBOX["yellow"]
    assert theme.hp_color(5, 25) == theme.GRUVBOX["red"]


def test_outcome_colors() -> None:
    assert theme.outcome_color("crit_success") == theme.GRUVBOX["purple"]
    assert theme.outcome_color("success") == theme.GRUVBOX["green"]
    assert theme.outcome_color("failure") == theme.GRUVBOX["red"]
    assert theme.outcome_color("crit_failure") == theme.GRUVBOX["black"]


def test_block_bar_rendering() -> None:
    assert theme.block_bar(25, 25, 10) == "\u2588" * 10
    assert theme.block_bar(0, 25, 10) == "\u2591" * 10
    bar = theme.block_bar(20, 25, 10)
    assert bar.count("\u2588") == 8
    assert len(bar) == 10


def test_block_bar_handles_zero_max() -> None:
    assert theme.block_bar(0, 0, 5) == "\u2591" * 5


def test_currency_label() -> None:
    assert theme.currency_label(0, 6, 32) == "6с · 32м"
    assert theme.currency_label(1, 0, 0) == "1з"


def test_danger_color() -> None:
    assert theme.danger_color(0) == theme.GRUVBOX["green"]
    assert theme.danger_color(3) == theme.GRUVBOX["yellow"]
    assert theme.danger_color(5) == theme.GRUVBOX["red"]


def test_css_variables_cover_stylesheet() -> None:
    css = CSS_PATH.read_text(encoding="utf-8")
    used = set(re.findall(r"\$([a-z0-9]+)", css))
    defined = set(theme.CSS_VARIABLES)
    missing = used - defined
    assert not missing, f"stylesheet uses undefined variables: {sorted(missing)}"


def test_theme_object_or_none() -> None:
    built = theme.gruvbox_theme()
    # Either a Theme named gruvbox-dark, or None on unsupported Textual builds.
    assert built is None or getattr(built, "name", None) == "gruvbox-dark"
