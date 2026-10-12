"""Tests for the TUI widgets: pure renderers plus a headless app smoke test."""

from __future__ import annotations

from pathlib import Path

import pytest
from textual.app import App, ComposeResult

from engine import inventory_engine as inv
from engine.catalogs import load_catalogs
from engine.config import Settings
from engine.state_manager import StateManager
from ui.widgets import (
    CharacterCard,
    CommandBar,
    DialoguePanel,
    InventoryTab,
    MapPanel,
    NarrativeLog,
    PartyPanel,
    QuestTab,
    format_entry,
    render_card,
    render_dialogue,
    render_header,
    render_inventory,
    render_map,
    render_party,
    render_quests,
    render_roll,
)
from ui.widgets.command_bar import CommandInput, SlashSuggester
from ui.widgets.dice_overlay import RollDisplay, animation_frame

DATA_DIR = Path(__file__).resolve().parents[1] / "data"


@pytest.fixture
def catalogs():
    return load_catalogs(DATA_DIR)


@pytest.fixture
def manager(tmp_path: Path) -> StateManager:
    settings = Settings(
        save_dir=tmp_path / "saves",
        log_dir=tmp_path / "logs",
        data_dir=DATA_DIR,
        offline_mode="force",
    )
    return StateManager.new_game("ui", settings=settings)


# ---------------------------------------------------------------------------
# Pure renderers
# ---------------------------------------------------------------------------


def test_render_header() -> None:
    text = str(render_header(model="m", offline=True, canon_broken=True))
    assert "d8nn" in text
    assert "offline" in text
    assert "КАНОН ПОВРЕЖДЁН" in text

    saved = str(render_header(model="m", offline=False, saved=True))
    assert "[СОХРАНЕНО]" in saved

    busy = str(render_header(model="m", offline=False, busy=True))
    assert "думает" in busy


def test_render_card(manager: StateManager, catalogs) -> None:
    text = str(render_card(manager.state.player, manager.state, catalogs))
    assert "Стасян Горелый" in text
    assert "\u2588" in text  # a block bar is present
    assert "Тяжёлый топор" in text
    assert "Кожаный доспех" in text
    assert "6с \u00b7 32м" in text


def test_render_party(manager: StateManager, catalogs) -> None:
    text = str(render_party(manager.state, catalogs))
    assert "Бобс" in text


def test_render_map_has_current_marker_and_fog(manager: StateManager, catalogs) -> None:
    text = str(render_map(manager.state, catalogs))
    assert "ты здесь" in text  # position label
    assert "\u2593\u2593" in text  # fog cell present in the board
    assert "\u250c" in text  # chessboard border
    assert "масштаб" in text  # scale line
    assert "a" in text  # column labels
    revealed = str(render_map(manager.state, catalogs, reveal=True))
    assert revealed.count("\u2593\u2593") < text.count("\u2593\u2593")


def test_render_dialogue_empty_and_filled(manager: StateManager, catalogs) -> None:
    empty = str(render_dialogue(None, None))
    assert "вне диалога" in empty

    npc = manager.state.npcs["old_mira"]
    filled = str(render_dialogue(npc, catalogs.npc("old_mira")))
    assert "Старая Мира" in filled
    assert "репутация" in filled


def test_render_inventory_and_overload(manager: StateManager, catalogs) -> None:
    text = str(render_inventory(manager.state, catalogs, manager.state.player))
    assert "вес 11/25" in text
    assert "Надето" in text

    inv.add_item(manager.state, catalogs, manager.state.player, "chain_mail")
    overloaded = str(render_inventory(manager.state, catalogs, manager.state.player))
    assert "ПЕРЕГРУЗ" in overloaded


def test_render_quests(manager: StateManager, catalogs) -> None:
    text = str(render_quests(manager.state, catalogs))
    assert "Священный источник" in text
    assert "Старая Мира" in text


def test_format_entry_prefix() -> None:
    assert str(format_entry("привет", "player")).startswith("> ")
    assert str(format_entry("беда", "crime")).startswith("\u2020 ")


def test_format_entry_strips_emojis() -> None:
    cleaned = str(format_entry("Ты победил \U0001f409 дракона!", "gm"))
    assert "\U0001f409" not in cleaned
    assert "дракона" in cleaned


def test_emoji_toggle_ascii(manager, catalogs) -> None:
    from ui import glyphs

    try:
        glyphs.set_emoji(False)
        map_text = str(render_map(manager.state, catalogs))
        assert "VI" in map_text or "MA" in map_text  # terrain codes, not emoji
        assert "\U0001f332" not in map_text
        card = str(render_card(manager.state.player, manager.state, catalogs))
        assert "\U0001f4aa" not in card and "\u2764\ufe0f" not in card
        assert "Деньги" in card
    finally:
        glyphs.set_emoji(True)


def test_render_roll_and_animation() -> None:
    display = RollDisplay(
        dice="d20", value=17, modifier=1, total=18, dc=14, outcome="success"
    )
    text = str(render_roll(display, "прыжок через пропасть"))
    assert "d20: [ 17 ]" in text
    assert "=  18" in text
    assert "PASS" in text

    crit = RollDisplay(dice="d20", value=20, modifier=0, total=20, dc=25, outcome="crit_success")
    assert "CRIT" in str(render_roll(crit))

    assert "d20:" in str(animation_frame(0))


def test_widgets_are_instantiable() -> None:
    # Class-level construction must not require a running app.
    for widget_cls in (CharacterCard, PartyPanel, MapPanel, DialoguePanel,
                       InventoryTab, QuestTab, NarrativeLog):
        assert widget_cls() is not None


# ---------------------------------------------------------------------------
# Headless app: autocomplete + history + logging
# ---------------------------------------------------------------------------


class _WidgetsApp(App):
    def compose(self) -> ComposeResult:
        yield CommandBar()
        yield NarrativeLog(id="log")


async def test_autocomplete_suggester() -> None:
    suggester = SlashSuggester()
    assert await suggester.get_suggestion("/sa") == "/save"
    assert await suggester.get_suggestion("/zzz") is None
    assert await suggester.get_suggestion("не команда") is None


async def test_command_bar_history_and_log() -> None:
    async with _WidgetsApp().run_test() as pilot:
        bar = pilot.app.query_one(CommandBar)
        command_input = bar.input

        command_input.remember("первый")
        command_input.remember("второй")
        assert command_input.history == ["первый", "второй"]

        await command_input.key_up()
        assert command_input.value == "второй"
        await command_input.key_up()
        assert command_input.value == "первый"
        await command_input.key_down()
        assert command_input.value == "второй"

        log = pilot.app.query_one("#log", NarrativeLog)
        log.add("Ты стоишь на краю пропасти.", "gm")
        await pilot.pause()
        assert len(log.lines) >= 1


def test_command_input_class_exists() -> None:
    assert issubclass(CommandInput, object)
