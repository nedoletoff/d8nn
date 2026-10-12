"""Smoke tests for the six rule engines (spec step 6)."""

from __future__ import annotations

from pathlib import Path

import pytest

from engine import combat_engine as combat
from engine import inventory_engine as inv
from engine import npc_engine as npc
from engine import quest_engine as quest
from engine import time_engine as tm
from engine import world_map as world
from engine.canon_log import TimeStamp
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
        save_dir=tmp_path / "saves",
        log_dir=tmp_path / "logs",
        data_dir=DATA_DIR,
        offline_mode="force",
    )
    manager = StateManager.new_game("test", settings=settings)
    manager.state.rng.seed = "test-seed"
    return manager


# ---------------------------------------------------------------------------
# time_engine
# ---------------------------------------------------------------------------


def test_advance_rolls_over_days() -> None:
    from engine.state_manager import GameState

    state = GameState(player={"id": "player", "name": "x"})
    state.time = TimeStamp(day=1, hour=23, minute=30)
    tm.advance(state, 45)
    assert state.time == TimeStamp(day=2, hour=0, minute=15)


def test_advance_rejects_backwards_time() -> None:
    from engine.state_manager import GameState

    state = GameState(player={"id": "player", "name": "x"})
    with pytest.raises(tm.TimeError):
        tm.advance(state, -1)


@pytest.mark.parametrize(
    ("hour", "expected"),
    [(3, "night"), (6, "dawn"), (12, "day"), (19, "dusk"), (22, "night")],
)
def test_light_level(hour: int, expected: str) -> None:
    from engine.state_manager import GameState

    state = GameState(player={"id": "player", "name": "x"})
    state.time = TimeStamp(day=1, hour=hour, minute=0)
    assert tm.light_level(state) == expected


def test_long_rest_heals_and_reaches_dawn(manager: StateManager) -> None:
    manager.state.player.hp = 3
    manager.state.player.mp = 0
    manager.state.player.statuses.append("poisoned")

    spent = tm.long_rest(manager.state)

    assert manager.state.time.hour == tm.DAWN_HOUR
    assert spent > 0
    assert manager.state.player.hp == manager.state.player.max_hp
    assert "poisoned" not in manager.state.player.statuses


def test_travel_minutes_clamped() -> None:
    assert tm.travel_minutes((0, 0), (0, 0)) == 30
    assert tm.travel_minutes((0, 0), (30, 30)) == 480


# ---------------------------------------------------------------------------
# combat_engine
# ---------------------------------------------------------------------------


def test_start_combat_builds_initiative_order(manager, catalogs) -> None:
    npc.get_or_create_npc(manager.state, catalogs, "rotten_vlad")
    state = combat.start_combat(manager, ["player", "npc:rotten_vlad"])
    assert state.active
    assert set(state.order) == {"player", "npc:rotten_vlad"}
    assert len(state.order) == 2
    assert manager.canon.events[-1].type == "combat"


def test_next_turn_wraps_and_advances_round(manager, catalogs) -> None:
    npc.get_or_create_npc(manager.state, catalogs, "rotten_vlad")
    combat.start_combat(manager, ["player", "npc:rotten_vlad"])
    assert manager.state.combat.round == 1
    combat.next_turn(manager)
    ref = combat.next_turn(manager)
    assert manager.state.combat.round == 2
    assert ref == combat.current_combatant(manager.state)


def test_attack_eventually_kills_npc(manager, catalogs) -> None:
    vlad = npc.get_or_create_npc(manager.state, catalogs, "rotten_vlad")
    vlad.hp = 5
    for _ in range(50):
        if not combat.is_alive(vlad):
            break
        combat.attack(manager, catalogs, "player", "npc:rotten_vlad")
    assert vlad.hp == 0
    assert vlad.status == "dead"
    assert vlad.permanently_dead
    # The death is recorded as a crime; faction reputation is decided later.
    assert manager.state.crimes
    crime = next(iter(manager.state.crimes.values()))
    assert crime.victim == "rotten_vlad"
    assert manager.canon.events[-1].type in ("combat", "crime")


def test_attack_records_hp_delta_effect(manager, catalogs) -> None:
    npc.get_or_create_npc(manager.state, catalogs, "rotten_vlad")
    result = combat.attack(manager, catalogs, "player", "npc:rotten_vlad")
    assert any(effect["kind"] == "hp_delta" for effect in result.effects)
    assert result.target_hp == 14 - result.damage


def test_status_tick_deals_damage(manager, catalogs) -> None:
    vlad = npc.get_or_create_npc(manager.state, catalogs, "rotten_vlad")
    vlad.statuses.append(combat.STATUS_BURNING)
    before = vlad.hp
    effects = combat.tick_statuses(manager, "npc:rotten_vlad")
    assert vlad.hp < before
    assert effects and effects[0]["kind"] == "hp_delta"


def test_player_death_ends_the_game(manager, catalogs) -> None:
    npc.get_or_create_npc(manager.state, catalogs, "rotten_vlad")
    manager.state.player.hp = 1
    for _ in range(200):
        if manager.state.game_over:
            break
        combat.attack(manager, catalogs, "npc:rotten_vlad", "player")
    assert manager.state.game_over
    assert manager.state.player.alive is False


def test_cast_spell_spends_mp(manager, catalogs) -> None:
    npc.get_or_create_npc(manager.state, catalogs, "rotten_vlad")
    mp_before = manager.state.player.mp
    target = manager.state.npcs["rotten_vlad"]
    hp_before = target.hp
    result = combat.cast_spell(manager, catalogs, "player", "spark", "npc:rotten_vlad")
    assert manager.state.player.mp == mp_before - 1
    assert result.damage >= 0
    assert target.hp == hp_before - result.damage
    assert manager.canon.events[-1].type == "combat"


# ---------------------------------------------------------------------------
# inventory_engine
# ---------------------------------------------------------------------------


def test_carry_capacity_and_weight(manager, catalogs) -> None:
    assert inv.carry_capacity(manager.state.player) == 25  # 10 + STR(3) * 5
    weight = inv.total_weight(manager.state, catalogs, manager.state.player)
    assert weight == pytest.approx(11.0)  # heavy_axe 6 + leather_armor 5


def test_overload_applies_dex_penalty(manager, catalogs) -> None:
    inv.add_item(manager.state, catalogs, manager.state.player, "chain_mail")
    assert inv.is_overloaded(manager.state, catalogs, manager.state.player)
    assert inv.dex_penalty(manager.state, catalogs, manager.state.player) == 2


def test_equip_and_unequip(manager, catalogs) -> None:
    instance = inv.add_item(manager.state, catalogs, manager.state.player, "dagger")
    slot = inv.equip(manager.state, catalogs, manager.state.player, instance.instance_id)
    assert slot == "weapon"
    assert manager.state.player.equipped["weapon"] == instance.instance_id
    assert inv.unequip(manager.state, manager.state.player, instance.instance_id)
    assert instance.instance_id in manager.state.player.inventory


def test_use_consumable_heals_and_consumes(manager, catalogs) -> None:
    player = manager.state.player
    player.hp = 5
    instance = inv.add_item(manager.state, catalogs, player, "healing_potion")
    result = inv.use_item(manager, catalogs, player, instance.instance_id)
    assert player.hp == 13
    assert instance.instance_id not in manager.state.items
    assert result.item_id == "healing_potion"
    assert manager.canon.events[-1].type == "item_use"


def test_non_consumable_use_raises(manager, catalogs) -> None:
    instance = inv.add_item(manager.state, catalogs, manager.state.player, "torch")
    with pytest.raises(inv.InventoryError):
        inv.use_item(manager, catalogs, manager.state.player, instance.instance_id)


# ---------------------------------------------------------------------------
# quest_engine
# ---------------------------------------------------------------------------


def test_quest_lifecycle_and_rewards(manager, catalogs) -> None:
    quest.start_quest(manager, catalogs, "sacred_spring")
    assert manager.state.quests["sacred_spring"].stage == "reach_spring"

    quest.advance_stage(manager, catalogs, "sacred_spring", "cleanse_spring")
    xp_before = manager.state.player.xp
    copper_before = manager.state.player.currency.to_copper()

    result = quest.complete_quest(manager, catalogs, "sacred_spring")

    assert manager.state.quests["sacred_spring"].status == "completed"
    assert manager.state.player.xp == xp_before + 50
    assert manager.state.player.currency.to_copper() == copper_before + 50
    assert result.rewards["items"] == ["healing_potion"]
    assert any(item.item_id == "healing_potion" for item in manager.state.items.values())


def test_invalid_quest_stage_raises(manager, catalogs) -> None:
    quest.start_quest(manager, catalogs, "sacred_spring")
    with pytest.raises(quest.QuestError):
        quest.advance_stage(manager, catalogs, "sacred_spring", "nonsense")


def test_completing_twice_raises(manager, catalogs) -> None:
    quest.start_quest(manager, catalogs, "sacred_spring")
    quest.complete_quest(manager, catalogs, "sacred_spring")
    with pytest.raises(quest.QuestError):
        quest.complete_quest(manager, catalogs, "sacred_spring")


# ---------------------------------------------------------------------------
# npc_engine
# ---------------------------------------------------------------------------


def test_get_or_create_npc_from_catalog(manager, catalogs) -> None:
    vlad = npc.get_or_create_npc(manager.state, catalogs, "rotten_vlad")
    assert vlad.hp == 14
    assert vlad.max_hp == 14
    assert vlad.ac == 12
    assert vlad.stat("STR") == 3
    assert npc.get_or_create_npc(manager.state, catalogs, "rotten_vlad") is vlad


@pytest.mark.parametrize(
    ("rep", "disposition"),
    [(50, "friendly"), (0, "neutral"), (-50, "hostile")],
)
def test_disposition_bands(rep: int, disposition: str) -> None:
    assert npc.disposition_for(rep) == disposition


def test_change_rep_clamps(manager, catalogs) -> None:
    npc.get_or_create_npc(manager.state, catalogs, "old_mira")
    npc.change_rep(manager, "old_mira", 1000)
    assert manager.state.npcs["old_mira"].rep == 100
    assert manager.state.npcs["old_mira"].disposition == "friendly"


def test_social_check_updates_rep_and_logs(manager, catalogs) -> None:
    result = npc.social_check(manager, catalogs, "old_mira", intent="persuade", dc=12)
    expected = npc.clamp_rep(result.rep_before + npc.REP_DELTA[result.roll.outcome or "failure"])
    assert result.rep_after == expected
    assert result.disposition == npc.disposition_for(result.rep_after)
    assert manager.canon.events[-1].type == "dialogue"


def test_kill_npc_is_permanent(manager, catalogs) -> None:
    npc.kill_npc(manager, catalogs, "rotten_vlad")
    vlad = manager.state.npcs["rotten_vlad"]
    assert vlad.status == "dead"
    assert vlad.permanently_dead
    # The kill is logged, and a crime record is opened for the witness logic.
    assert any(event.type == "npc_death" for event in manager.canon.events)
    assert manager.state.crimes
    with pytest.raises(npc.NpcError):
        npc.kill_npc(manager, catalogs, "rotten_vlad")
    npc.kill_npc(manager, catalogs, "rotten_vlad", force=True)  # explicit override


def test_social_check_with_dead_npc_raises(manager, catalogs) -> None:
    npc.kill_npc(manager, catalogs, "rotten_vlad")
    with pytest.raises(npc.NpcError):
        npc.social_check(manager, catalogs, "rotten_vlad", intent="ask")


# ---------------------------------------------------------------------------
# world_map
# ---------------------------------------------------------------------------


def test_graph_queries(manager, catalogs) -> None:
    assert world.connected(catalogs, "black_marsh_sector_3", "suspension_bridge")
    assert not world.connected(catalogs, "wildwood_hamlet", "underground_caves")
    assert world.visible_exits(manager.state, catalogs) == ["wildwood_hamlet"]


def test_travel_moves_advances_time_and_discovers(manager, catalogs) -> None:
    before = manager.state.time
    result = world.travel(manager, catalogs, "suspension_bridge", force_encounter=False)
    assert result.destination == "suspension_bridge"
    assert result.newly_discovered
    assert manager.state.world.current_location == "suspension_bridge"
    assert (manager.state.time.day, manager.state.time.hour) >= (before.day, before.hour)
    assert manager.canon.events[-1].type == "move"


def test_travel_to_unconnected_raises(manager, catalogs) -> None:
    # From the hamlet the only exit is the marsh; the bridge is not adjacent.
    manager.state.world.current_location = "wildwood_hamlet"
    with pytest.raises(world.WorldError):
        world.travel(manager, catalogs, "suspension_bridge")


def test_encounter_flag(manager, catalogs) -> None:
    result = world.travel(manager, catalogs, "suspension_bridge", force_encounter=True)
    assert result.encounter
    assert manager.state.flags["encounter_id"] == "enc_suspension_bridge"


def test_ascii_map_fog_and_current_marker(manager, catalogs) -> None:
    rendered = world.ascii_map(manager.state, catalogs)
    assert "\u25ba" in rendered and "\u25c4" in rendered  # current-location markers
    assert world.FOG_LABEL in rendered  # undiscovered locations are hidden
    revealed = world.ascii_map(manager.state, catalogs, reveal=True)
    assert world.FOG_LABEL not in revealed
    assert "Пещеры" in revealed
