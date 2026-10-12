"""Tests for world prototypes and player-saved rulesets."""

from __future__ import annotations

from pathlib import Path

import pytest

from engine import rules_engine as rules
from engine import worlds
from engine.catalogs import build_catalogs
from engine.config import Settings
from engine.state_manager import StateManager

DATA_DIR = Path(__file__).resolve().parents[1] / "data"
WORLD_IDS = ("black_marsh", "witcher_dark", "hobbit_classic", "arcane_school", "neon_sprawl", "ashen_wastes")


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(
        save_dir=tmp_path / "saves", log_dir=tmp_path / "logs", data_dir=DATA_DIR, offline_mode="force"
    )


def test_list_worlds_includes_prototypes() -> None:
    ids = {world.id for world in worlds.list_worlds(DATA_DIR)}
    assert set(WORLD_IDS) <= ids


@pytest.mark.parametrize("world_id", WORLD_IDS)
def test_every_world_loads(world_id: str) -> None:
    world = worlds.load_world(world_id, DATA_DIR)
    assert world.meta.id == world_id
    assert world.catalogs.locations, "world has no locations"
    assert world.meta.start in world.catalogs.locations or world_id == "black_marsh"


@pytest.mark.parametrize("world_id", ("witcher_dark", "hobbit_classic", "arcane_school", "neon_sprawl", "ashen_wastes"))
def test_build_state_uses_world(world_id: str) -> None:
    world = worlds.load_world(world_id, DATA_DIR)
    state = worlds.build_state(world, DATA_DIR)

    start = world.meta.start
    assert state.world.current_location == start
    assert state.world.locations[start].discovered
    # everything except the start begins fogged
    assert sum(1 for loc in state.world.locations.values() if loc.discovered) == 1
    assert state.player.name == world.meta.player["name"]
    assert state.flags["world_id"] == world_id
    if world.meta.starting_quests:
        assert state.quests


def test_arcane_school_is_hogwarts_like() -> None:
    world = worlds.load_world("arcane_school", DATA_DIR)
    state = worlds.build_state(world, DATA_DIR)
    assert "great_hall" in state.world.locations
    assert any(loc.terrain == "library" for loc in world.catalogs.locations.values())
    assert state.player.stats["INT"] == 4


def test_world_carries_lore_without_a_fixed_adventure() -> None:
    world = worlds.load_world("hobbit_classic", DATA_DIR)
    state = worlds.build_state(world, DATA_DIR)
    lore = state.flags["world_lore"]
    assert lore["era"] and lore["magic"] and lore["peoples"]
    assert lore["premise"]
    # the opening quest is NOT hard-coded — the narrator improvises it
    assert state.quests == {}


def test_witcher_world_is_grim() -> None:
    world = worlds.load_world("witcher_dark", DATA_DIR)
    state = worlds.build_state(world, DATA_DIR)
    assert state.rules.lethality == "hardcore"
    assert state.rules.world_attitude == -10
    assert any("Серебро" in law for law in state.rules.laws)
    assert "знаки ведьмака" in state.flags["world_lore"]["magic"]


def test_arcane_school_is_near_modern() -> None:
    world = worlds.load_world("arcane_school", DATA_DIR)
    state = worlds.build_state(world, DATA_DIR)
    era = state.flags["world_lore"]["era"]
    assert "современност" in era


def test_cyberpunk_world_has_no_magic_lore_but_tech() -> None:
    world = worlds.load_world("neon_sprawl", DATA_DIR)
    state = worlds.build_state(world, DATA_DIR)
    assert "имплант" in state.flags["world_lore"]["magic"]
    assert any("Магии не существует" in law for law in state.rules.laws)


def test_kenshi_like_world_disables_magic() -> None:
    world = worlds.load_world("ashen_wastes", DATA_DIR)
    state = worlds.build_state(world, DATA_DIR)
    assert state.rules.toggles.magic_enabled is False
    assert state.rules.lethality == "hardcore"


def test_new_game_with_world(settings: Settings, tmp_path: Path) -> None:
    world = worlds.load_world("hobbit_classic", DATA_DIR)
    manager = StateManager.new_game("hobbit", settings=settings, world=world)
    assert manager.state.world.current_location == "shire_hamlet"
    assert manager.state.flags["world_id"] == "hobbit_classic"

    reloaded = StateManager.load("hobbit", settings=settings)
    assert reloaded.state.player.name == "Бильбо"


def test_catalog_overlay_keeps_base_items() -> None:
    base = build_catalogs(DATA_DIR)
    witcher = build_catalogs(DATA_DIR, DATA_DIR / "worlds" / "witcher_dark")
    # world adds its own item...
    assert "silver_sword" in witcher.items
    # ...and still inherits the base catalog
    assert "healing_potion" in witcher.items and "healing_potion" in base.items


# ---------------------------------------------------------------------------
# Player-saved rulesets
# ---------------------------------------------------------------------------


def test_ruleset_save_load_roundtrip(tmp_path: Path) -> None:
    ruleset = rules.Ruleset(name="my world")
    rules.set_rule(ruleset, "carry_base", "30")
    rules.set_rule(ruleset, "world_attitude", "25")
    rules.add_law(ruleset, "Гравитация слабее на рассвете")

    directory = rules.rulesets_dir(tmp_path)
    path = rules.save_ruleset(ruleset, directory, "my world")
    assert path.exists()
    assert "my_world" in path.name

    loaded = rules.load_ruleset(path)
    assert loaded.carry_base == 30
    assert loaded.world_attitude == 25
    assert "Гравитация слабее на рассвете" in loaded.laws
    assert "my_world" in rules.list_rulesets(directory)


def test_list_rulesets_empty_when_missing(tmp_path: Path) -> None:
    assert rules.list_rulesets(tmp_path / "nope") == []
