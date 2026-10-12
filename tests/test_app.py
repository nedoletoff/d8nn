"""Headless tests for the assembled Textual app (spec step 10)."""

from __future__ import annotations

from pathlib import Path

import pytest

from engine.config import Settings
from engine.state_manager import StateManager
from ui.tui_app import RpgApp
from ui.widgets import NarrativeLog

DATA_DIR = Path(__file__).resolve().parents[1] / "data"


def make_settings(tmp_path: Path) -> Settings:
    return Settings(
        save_dir=tmp_path / "saves",
        log_dir=tmp_path / "logs",
        data_dir=DATA_DIR,
        offline_mode="force",
    )


@pytest.fixture
def app_settings(tmp_path: Path) -> Settings:
    return make_settings(tmp_path)


async def test_app_boots_and_creates_slot(app_settings: Settings) -> None:
    app = RpgApp(settings=app_settings, slot="t", skip_setup=True)
    async with app.run_test() as pilot:
        await pilot.pause()
        assert app.manager is not None
        assert StateManager.exists("t", settings=app_settings)
        # All spec panels are on screen.
        for widget_id in ("header", "character-card", "party-panel", "map-panel",
                          "dialogue-panel", "inventory-tab", "quest-tab",
                          "narrative-log", "command-bar"):
            assert app.query_one(f"#{widget_id}") is not None


async def test_plain_action_writes_canon_and_narration(app_settings: Settings) -> None:
    app = RpgApp(settings=app_settings, slot="t", skip_setup=True)
    async with app.run_test() as pilot:
        await pilot.pause()
        before = len(app.manager.canon)
        await app.handle_input("осматриваю топи")
        await pilot.pause()

        assert len(app.manager.canon) == before + 1
        log = app.query_one(NarrativeLog)
        assert len(log.lines) >= 3  # greeting + input + narration

        session_log = app.manager.slot_dir / "session.log"
        assert session_log.exists()
        assert "игрок" in session_log.read_text(encoding="utf-8")


async def test_rule_command_is_read_only(app_settings: Settings) -> None:
    app = RpgApp(settings=app_settings, slot="t", skip_setup=True)
    async with app.run_test() as pilot:
        await pilot.pause()
        before = len(app.manager.canon)
        await app.handle_input("/rule свидетели")
        await pilot.pause()
        assert len(app.manager.canon) == before  # nothing recorded


async def test_roll_command_does_not_write_canon(app_settings: Settings) -> None:
    app = RpgApp(settings=app_settings, slot="t", skip_setup=True)
    async with app.run_test() as pilot:
        await pilot.pause()
        before = len(app.manager.canon)
        await app.handle_input("/roll 2d6+3 проверка")
        await pilot.pause()
        assert len(app.manager.canon) == before


async def test_log_command_writes_history(app_settings: Settings) -> None:
    app = RpgApp(settings=app_settings, slot="t", skip_setup=True)
    async with app.run_test() as pilot:
        await pilot.pause()
        await app.handle_input("осматриваюсь")
        await pilot.pause()
        await app.handle_input("/log")
        await pilot.pause()
        target = app.manager.slot_dir / "narrative.txt"
        assert target.exists()
        assert "осматриваюсь" in target.read_text(encoding="utf-8")


async def test_export_creates_markdown(app_settings: Settings) -> None:
    app = RpgApp(settings=app_settings, slot="t", skip_setup=True)
    async with app.run_test() as pilot:
        await pilot.pause()
        await app.handle_input("осматриваюсь")
        await app.handle_input("/export")
        await pilot.pause()
        chronicle = app.manager.slot_dir / "chronicle.md"
        assert chronicle.exists()
        assert "Хроника" in chronicle.read_text(encoding="utf-8")


async def test_unknown_command_is_reported(app_settings: Settings) -> None:
    app = RpgApp(settings=app_settings, slot="t", skip_setup=True)
    async with app.run_test() as pilot:
        await pilot.pause()
        await app.handle_input("/nonsense")
        await pilot.pause()
        assert app.manager is not None  # app survived


async def test_setup_screen_creates_world(app_settings: Settings) -> None:
    from textual.widgets import Input, Select

    from ui.widgets.setup_screen import SetupScreen

    app = RpgApp(settings=app_settings, slot="wizard")  # no skip_setup -> wizard
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        screen = app.screen
        assert isinstance(screen, SetupScreen)

        screen.query_one("#world", Select).value = "hobbit_classic"
        screen.query_one("#name", Input).value = "Бильбо"
        screen.query_one("#char_class", Select).value = "rogue"
        screen.query_one("#lethality", Select).value = "soft"
        await pilot.click("#start")
        await pilot.pause()

        assert app.mode == "play"
        assert app.manager is not None
        assert app.manager.state.player.name == "Бильбо"
        assert app.manager.state.flags["world_id"] == "hobbit_classic"
        assert app.manager.state.rules.lethality == "soft"
        # the world's own start location is used
        assert app.manager.state.world.current_location == "shire_hamlet"


async def test_setup_screen_demo_button(app_settings: Settings) -> None:
    from ui.widgets.setup_screen import SetupScreen

    app = RpgApp(settings=app_settings, slot="wizard")
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        assert isinstance(app.screen, SetupScreen)
        await pilot.click("#demo")
        await pilot.pause()
        assert app.mode == "play"
        assert app.slot == "test"
        assert app.manager is not None


async def test_world_command_creates_and_accepts(seeded_data: Path, tmp_path: Path) -> None:
    settings = Settings(
        save_dir=tmp_path / "saves", log_dir=tmp_path / "logs", data_dir=seeded_data, offline_mode="force"
    )
    app = RpgApp(settings=settings, slot="t", skip_setup=True)
    async with app.run_test() as pilot:
        await pilot.pause()
        await app.handle_input("/world new мрачный мир из стекла и пепла")
        await pilot.pause()
        assert app._draft_dir is not None and app._draft_dir.exists()

        await app.handle_input("/world accept glass_ash")
        await pilot.pause()
        assert (seeded_data / "worlds" / "glass_ash" / "world.json").exists()


async def test_setup_screen_roles_and_custom_role(app_settings: Settings) -> None:
    from textual.widgets import Input, Select, Static

    from ui.widgets.setup_screen import CLASS_LABELS, SetupScreen

    app = RpgApp(settings=app_settings, slot="wizard")
    async with app.run_test(size=(120, 44)) as pilot:
        await pilot.pause()
        screen = app.screen
        assert isinstance(screen, SetupScreen)

        # world options are plain names (no emoji that clip the first letter)
        assert all(all(ord(ch) < 0x2500 for ch in label) for label, _ in screen._world_options)
        # roles are translated to Russian
        assert CLASS_LABELS["warrior"] == "Воин"
        assert ("Маг", "mage") in screen._class_options

        # "pick a character with the LLM" button exists; offline -> friendly note
        assert screen.query_one("#gen_char") is not None
        await screen._generate_character()
        status = screen.query_one("#char_status", Static)
        assert "оффлайн" in str(status.renderable).lower()

        # a custom role overrides the preset and lands in the flags
        screen.query_one("#custom_role", Input).value = "алхимик-изгнанник"
        screen.query_one("#name", Input).value = "Рен"
        await pilot.click("#start")
        await pilot.pause()
        assert app.manager.state.flags["role"] == "алхимик-изгнанник"
        assert app.manager.state.player.name == "Рен"


async def test_mandate_command_edits_game(app_settings: Settings) -> None:
    app = RpgApp(settings=app_settings, slot="t", skip_setup=True)
    async with app.run_test() as pilot:
        await pilot.pause()
        await app.handle_input("/mandate нет магии и никто не умирает")
        await pilot.pause()
        assert app.manager.state.rules.toggles.magic_enabled is False
        assert app.manager.state.rules.toggles.death_enabled is False
        assert app.manager.state.rules.capabilities.may_describe_magic is False


async def test_resume_world_save_uses_world_catalogs(app_settings: Settings) -> None:
    from engine.worlds import load_world

    world = load_world("neon_sprawl", DATA_DIR)
    StateManager.new_game("neon", settings=app_settings, world=world)

    app = RpgApp(settings=app_settings, slot="neon")
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        assert app.manager is not None
        # the world's own catalogs are loaded, so its start location resolves
        assert app.catalogs.location("neon_market").name == "Неоновый рынок"


async def test_autosave_after_turn(app_settings: Settings) -> None:
    app = RpgApp(settings=app_settings, slot="t", skip_setup=True)
    async with app.run_test() as pilot:
        await pilot.pause()
        await app.handle_input("/travel Мост")
        await pilot.pause()
        reloaded = StateManager.load("t", settings=app_settings)
        assert reloaded.state.world.current_location == "suspension_bridge"


async def test_games_tab_lists_and_loads(app_settings: Settings) -> None:
    from ui.widgets.setup_screen import SetupScreen

    StateManager.new_game("past", settings=app_settings)

    app = RpgApp(settings=app_settings, slot="brand_new")
    async with app.run_test(size=(120, 44)) as pilot:
        await pilot.pause()
        screen = app.screen
        assert isinstance(screen, SetupScreen)
        assert screen.games and screen.games[0]["slot"] == "past"

        from textual.widgets import Button

        screen.query_one("#game-0", Button).press()
        await pilot.pause()
        assert app.slot == "past"
        assert app.mode == "play"


async def test_main_menu_opens_by_default(app_settings: Settings) -> None:
    from ui.widgets.setup_screen import SetupScreen

    StateManager.new_game("default", settings=app_settings)
    # No explicit slot -> always the main menu, even though a save exists.
    app = RpgApp(settings=app_settings)
    async with app.run_test(size=(120, 44)) as pilot:
        await pilot.pause()
        assert isinstance(app.screen, SetupScreen)
        assert app.manager is None

    # Explicit slot -> resume it directly.
    resumed = RpgApp(settings=app_settings, slot="default")
    async with resumed.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        assert resumed.manager is not None
        assert resumed.mode == "play"


async def test_main_command_opens_menu(app_settings: Settings) -> None:
    from ui.widgets.setup_screen import SetupScreen

    app = RpgApp(settings=app_settings, slot="t", skip_setup=True)
    async with app.run_test(size=(120, 44)) as pilot:
        await pilot.pause()
        await app.handle_input("/main")
        await pilot.pause()
        assert isinstance(app.screen, SetupScreen)


async def test_emoji_command(app_settings: Settings) -> None:
    from ui import glyphs

    app = RpgApp(settings=app_settings, slot="t", skip_setup=True)
    async with app.run_test() as pilot:
        await pilot.pause()
        await app.handle_input("/emoji off")
        await pilot.pause()
        assert app.settings.emoji is False
        assert glyphs.emoji_on() is False
        await app.handle_input("/emoji on")
        await pilot.pause()
        assert glyphs.emoji_on() is True


def test_default_emoji_is_off() -> None:
    from engine.config import Settings

    assert Settings().emoji is False


async def test_custom_world_option_toggles_prompt(app_settings: Settings) -> None:
    from textual.containers import Vertical
    from textual.widgets import Select

    from ui.widgets.setup_screen import CUSTOM_WORLD, STAT_KEYS, SetupScreen

    app = RpgApp(settings=app_settings, slot="wizard")
    async with app.run_test(size=(120, 46)) as pilot:
        await pilot.pause()
        screen = app.screen
        assert isinstance(screen, SetupScreen)

        assert any(value == CUSTOM_WORLD for _, value in screen._world_options)
        assert len(screen.query(".stat-label")) == len(STAT_KEYS)  # stat labels present
        assert screen.query_one("#world-custom", Vertical).display is False

        screen.query_one("#world", Select).value = CUSTOM_WORLD
        await pilot.pause()
        assert screen.query_one("#world-custom", Vertical).display is True


async def test_setup_applies_stats_background_items(app_settings: Settings) -> None:
    from textual.widgets import Button, Input, TextArea

    app = RpgApp(settings=app_settings, slot="wiz2")
    async with app.run_test(size=(120, 46)) as pilot:
        await pilot.pause()
        screen = app.screen
        screen.query_one("#name", Input).value = "Рен"
        screen.query_one("#stat_STR", Input).value = "5"
        screen.query_one("#background", TextArea).text = "вырос в шахтах"
        screen.query_one("#motivation", Input).value = "найти брата"
        screen.query_one("#items", TextArea).text = "healing_potion"
        screen.query_one("#start", Button).press()
        await pilot.pause()

        state = app.manager.state
        assert state.player.stats["STR"] == 5
        assert state.flags["background"] == "вырос в шахтах"
        assert state.flags["motivation"] == "найти брата"
        assert any(inst.item_id == "healing_potion" for inst in state.items.values())


async def test_trade_commands(app_settings: Settings) -> None:
    app = RpgApp(settings=app_settings, slot="t", skip_setup=True)
    async with app.run_test() as pilot:
        await pilot.pause()
        await app.handle_input("/shop old_mira")
        await pilot.pause()
        await app.handle_input("/ask зелье")
        await pilot.pause()
        await app.handle_input("/buy зелье")
        await pilot.pause()
        assert any(inst.item_id == "healing_potion" for inst in app.manager.state.items.values())


async def test_rules_command_edits_world(app_settings: Settings) -> None:
    app = RpgApp(settings=app_settings, slot="t", skip_setup=True)
    async with app.run_test() as pilot:
        await pilot.pause()
        await app.handle_input("/rules set carry_base 30")
        await pilot.pause()
        assert app.manager.state.rules.carry_base == 30

        await app.handle_input("/rules lethality hardcore")
        await app.handle_input("/rules law add Мертвые не возвращаются")
        await pilot.pause()
        assert app.manager.state.rules.lethality == "hardcore"
        assert "Мертвые не возвращаются" in app.manager.state.rules.laws

        before = len(app.manager.canon)
        await app.handle_input("/rules")  # just a view
        await pilot.pause()
        assert len(app.manager.canon) == before
