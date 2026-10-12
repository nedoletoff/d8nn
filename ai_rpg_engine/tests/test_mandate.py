"""Tests for the player mandate: edits that outrank the engine."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from engine import combat_engine as combat
from engine import inventory_engine as inv
from engine import mandate as mandate_mod
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
    manager = StateManager.new_game("mandate", settings=settings)
    manager.state.rng.seed = "seed"
    return manager


class FakeLLM:
    def __init__(self, response, *, offline: bool = False) -> None:
        self._response = response
        self.offline = offline
        self.settings = SimpleNamespace(intent_model="m", narrator_model="m")

    async def complete(self, messages, **kwargs):
        return None if self.offline else self._response


# ---------------------------------------------------------------------------
# Deterministic parsing
# ---------------------------------------------------------------------------


def test_rule_based_detects_no_magic() -> None:
    mandate = mandate_mod.rule_based_mandate("в этом мире магии не существует")
    assert mandate.capabilities["may_describe_magic"] is False
    assert mandate.toggles["magic_enabled"] is False
    assert mandate.laws


def test_rule_based_detects_no_death() -> None:
    mandate = mandate_mod.rule_based_mandate("никто не умирает, все выживают")
    assert mandate.toggles["death_enabled"] is False
    assert mandate.capabilities["may_kill"] is False


def test_rule_based_reads_numbers() -> None:
    mandate = mandate_mod.rule_based_mandate("хочу carry_base = 40 и world_attitude 50")
    assert mandate.engine_overrides["carry_base"] == "40"
    assert mandate.engine_overrides["world_attitude"] == "50"


def test_rule_based_tone() -> None:
    assert mandate_mod.rule_based_mandate("сделай тон уютный").capabilities["tone"] == "cozy"


# ---------------------------------------------------------------------------
# Apply: edits outrank the engine (force beyond bounds)
# ---------------------------------------------------------------------------


def test_apply_forces_out_of_bounds_override(manager: StateManager) -> None:
    mandate = mandate_mod.Mandate(engine_overrides={"world_attitude": "500"}, raw="слишком добрый мир")
    mandate_mod.apply_mandate(manager.state.rules, mandate)
    assert manager.state.rules.world_attitude == 500  # engine's own bound was 100
    assert manager.state.rules.overrides_enabled is True


def test_apply_sets_mechanics_and_capabilities(manager: StateManager) -> None:
    mandate = mandate_mod.rule_based_mandate("нет магии; никто не умирает")
    mandate_mod.apply_mandate(manager.state.rules, mandate)
    assert manager.state.rules.toggles.magic_enabled is False
    assert manager.state.rules.toggles.death_enabled is False
    assert manager.state.rules.capabilities.may_describe_magic is False


def test_narrator_constraints_are_explicit(manager: StateManager) -> None:
    mandate_mod.apply_mandate(
        manager.state.rules, mandate_mod.rule_based_mandate("нет магии и без насилия")
    )
    text = mandate_mod.narrator_constraints(manager.state.rules)
    assert "ЗАПРЕЩЕНО" in text
    assert "описывать магию" in text
    assert "Неизменяемое" in text  # canon invariant can never be edited away


def test_no_constraints_when_no_mandate(manager: StateManager) -> None:
    assert mandate_mod.narrator_constraints(manager.state.rules) == ""


# ---------------------------------------------------------------------------
# Engine obeys the toggles
# ---------------------------------------------------------------------------


def test_death_toggle_prevents_kills(manager: StateManager, catalogs) -> None:
    from engine import npc_engine as npc

    vlad = npc.get_or_create_npc(manager.state, catalogs, "rotten_vlad")
    vlad.hp = 3
    mandate_mod.apply_mandate(manager.state.rules, mandate_mod.rule_based_mandate("никто не умирает"))
    for _ in range(30):
        combat.attack(manager, catalogs, "player", "npc:rotten_vlad")
    assert vlad.hp >= 1
    assert vlad.status == "alive"


def test_encumbrance_toggle(manager: StateManager, catalogs) -> None:
    inv.add_item(manager.state, catalogs, manager.state.player, "chain_mail")
    assert inv.is_overloaded(manager.state, catalogs, manager.state.player)
    mandate_mod.apply_mandate(
        manager.state.rules, mandate_mod.Mandate(toggles={"encumbrance_enabled": False})
    )
    assert not inv.is_overloaded(manager.state, catalogs, manager.state.player)


def test_encounters_toggle(manager: StateManager) -> None:
    mandate_mod.apply_mandate(
        manager.state.rules, mandate_mod.Mandate(toggles={"encounters_enabled": False})
    )
    assert world.roll_encounter(manager, 5) is False


def test_magic_toggle_blocks_casting(manager: StateManager, catalogs) -> None:
    from engine import npc_engine as npc

    npc.get_or_create_npc(manager.state, catalogs, "rotten_vlad")
    mandate_mod.apply_mandate(manager.state.rules, mandate_mod.rule_based_mandate("магии нет"))
    with pytest.raises(combat.CombatError):
        combat.cast_spell(manager, catalogs, "player", "spark", "npc:rotten_vlad")


# ---------------------------------------------------------------------------
# LLM-refined mandate
# ---------------------------------------------------------------------------


async def test_llm_refines_mandate(manager: StateManager) -> None:
    client = FakeLLM(
        '{"laws":["Магия — это технология"],"engine_overrides":{"carry_base":"99"},'
        '"toggles":{"magic_enabled":false},"capabilities":{"may_kill":false},"notes":"ok"}'
    )
    mandate = await mandate_mod.parse_mandate(client, "переделай магию")
    assert mandate.source == "llm"
    mandate_mod.apply_mandate(manager.state.rules, mandate)
    assert manager.state.rules.carry_base == 99
    assert manager.state.rules.capabilities.may_kill is False
    assert any("технология" in law for law in manager.state.rules.laws)


async def test_offline_mandate_uses_rules(manager: StateManager) -> None:
    mandate = await mandate_mod.parse_mandate(FakeLLM(None, offline=True), "никто не умирает")
    assert mandate.source == "rule"
