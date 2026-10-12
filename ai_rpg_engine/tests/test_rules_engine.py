"""Tests for the player-editable rules and the chessboard grid map."""

from __future__ import annotations

from pathlib import Path

import pytest

from engine import rules_engine as rules
from engine import world_map as world
from engine.catalogs import load_catalogs
from engine.config import Settings
from engine.state_manager import StateManager

DATA_DIR = Path(__file__).resolve().parents[1] / "data"


@pytest.fixture
def catalogs():
    return load_catalogs(DATA_DIR)


@pytest.fixture
def manager(tmp_path: Path) -> StateManager:
    settings = Settings(
        save_dir=tmp_path / "saves", log_dir=tmp_path / "logs", data_dir=DATA_DIR, offline_mode="force"
    )
    return StateManager.new_game("rules", settings=settings)


# ---------------------------------------------------------------------------
# Ruleset
# ---------------------------------------------------------------------------


def test_default_ruleset_matches_engine_defaults() -> None:
    ruleset = rules.Ruleset()
    assert rules.carry_capacity(ruleset, 3) == 25
    assert rules.effective_max_hp(ruleset, 25) == 25


def test_set_numeric_rule(manager: StateManager) -> None:
    rules.set_rule(manager.state.rules, "carry_base", "20")
    assert manager.state.rules.carry_base == 20
    assert rules.carry_capacity(manager.state.rules, 3) == 20 + 3 * 5


def test_set_rule_out_of_bounds(manager: StateManager) -> None:
    with pytest.raises(rules.RuleError):
        rules.set_rule(manager.state.rules, "overload_dex_penalty", "999")


def test_set_unknown_rule(manager: StateManager) -> None:
    with pytest.raises(rules.RuleError):
        rules.set_rule(manager.state.rules, "cheat_mode", "on")


def test_lethality_preset_bundle(manager: StateManager) -> None:
    rules.set_rule(manager.state.rules, "lethality", "hardcore")
    assert manager.state.rules.max_hp_cap == 40
    assert manager.state.rules.encounter_rate == 1.5
    assert rules.effective_max_hp(manager.state.rules, 999) == 40


def test_world_attitude_shifts_disposition(manager: StateManager) -> None:
    rules.set_rule(manager.state.rules, "world_attitude", "40")
    assert rules.effective_disposition(manager.state.rules, 0) == "friendly"
    assert rules.effective_disposition(manager.state.rules, -90) == "hostile"


def test_encounter_rate_scales_chance() -> None:
    ruleset = rules.Ruleset()
    assert rules.encounter_chance(ruleset, 3) == 30
    rules.set_rule(ruleset, "encounter_rate", "2")
    assert rules.encounter_chance(ruleset, 3) == 60


def test_laws_add_remove(manager: StateManager) -> None:
    rules.add_law(manager.state.rules, "Магия запрещена под страхом смерти")
    rules.add_law(manager.state.rules, "Мертвые не возвращаются")
    assert len(manager.state.rules.laws) == 2
    assert rules.remove_law(manager.state.rules, 0)
    assert manager.state.rules.laws == ["Мертвые не возвращаются"]


def test_carry_capacity_follows_rules_and_state(manager: StateManager) -> None:
    from engine import inventory_engine as inv

    rules.set_rule(manager.state.rules, "carry_per_str", "10")
    assert inv.carry_capacity(manager.state.player, manager.state.rules) == 10 + 3 * 10


def test_rules_roundtrip_through_save(manager: StateManager) -> None:
    rules.set_rule(manager.state.rules, "death_threshold", "-5")
    rules.add_law(manager.state.rules, "Кровь приманивает духов")
    manager.save()
    reloaded = StateManager.load("rules", settings=manager.settings)
    assert reloaded.state.rules.death_threshold == -5
    assert reloaded.state.rules.laws == ["Кровь приманивает духов"]


# ---------------------------------------------------------------------------
# Grid / chessboard map
# ---------------------------------------------------------------------------


def test_grid_has_columns_and_rows(manager: StateManager, catalogs) -> None:
    grid = world.grid_ascii(manager.state, catalogs)
    assert " a" in grid and " b" in grid
    assert world.position_label(manager.state, catalogs) == "a1"


def test_grid_fog_and_reveal(manager: StateManager, catalogs) -> None:
    foggy = world.grid_tokens(manager.state, catalogs)
    assert any(world.FOG_CODE in cell for row in foggy for cell in row)
    revealed = world.grid_tokens(manager.state, catalogs, reveal=True)
    assert not any(world.FOG_CODE in cell for row in revealed for cell in row)


def test_grid_marks_current_cell(manager: StateManager, catalogs) -> None:
    tokens = world.grid_tokens(manager.state, catalogs)
    flat = [cell for row in tokens for cell in row]
    assert any(cell.endswith("*") for cell in flat)


def test_grid_coordinates_are_stable(manager: StateManager, catalogs) -> None:
    min_x, max_x, min_y, max_y = world.grid_bounds(catalogs)
    assert world.column_label(min_x, catalogs) == "a"
    assert world.cell_at(catalogs, 0, 1).id == "black_marsh_sector_3"


def test_grid_in_llm_context(manager: StateManager, catalogs) -> None:
    from engine.game_loop import GameLoop

    loop = GameLoop(manager, catalogs, client=None)
    view = loop.sanitized_state()
    assert "map_grid" in view and "a" in view["map_grid"]
    assert "world_rules" in view and "laws" in view["world_rules"]
