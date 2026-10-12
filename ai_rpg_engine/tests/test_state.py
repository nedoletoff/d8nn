"""Smoke tests for game state persistence and sanitization (spec step 5)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from engine import state_manager as sm
from engine.canon_log import CanonIntegrityError
from engine.config import Settings
from engine.state_manager import (
    Currency,
    GameState,
    StateManager,
    StateSchemaError,
    derive_snapshot,
    migrate_state,
)

DATA_DIR = Path(__file__).resolve().parents[1] / "data"


def make_settings(tmp_path: Path) -> Settings:
    return Settings(
        save_dir=tmp_path / "saves",
        log_dir=tmp_path / "logs",
        data_dir=DATA_DIR,
        offline_mode="force",
    )


# ---------------------------------------------------------------------------
# default_state.json
# ---------------------------------------------------------------------------


def test_default_state_is_valid() -> None:
    raw = json.loads((DATA_DIR / "default_state.json").read_text(encoding="utf-8"))
    state = GameState(**migrate_state(raw))
    assert state.schema_version == 3
    assert state.player.name == "Стасян Горелый"
    assert state.player.hp == state.player.max_hp == 25
    assert [member.id for member in state.party] == ["bobs"]
    assert state.world.current_location == "black_marsh_sector_3"


# ---------------------------------------------------------------------------
# new game / load / save
# ---------------------------------------------------------------------------


def test_new_game_creates_all_slot_files(tmp_path: Path) -> None:
    manager = StateManager.new_game("alpha", settings=make_settings(tmp_path))
    for name in ("state.json", "meta.json", "canon_snapshot.json", "canon_log.jsonl"):
        assert (manager.slot_dir / name).exists(), name
    assert StateManager.exists("alpha", settings=make_settings(tmp_path))


def test_save_load_roundtrip(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    manager = StateManager.new_game("alpha", settings=settings)
    manager.state.player.hp = 17
    manager.state.player.currency = Currency(silver=3, copper=5)
    manager.save()

    reloaded = StateManager.load("alpha", settings=settings)
    assert reloaded.state.player.hp == 17
    assert reloaded.state.player.currency == Currency(silver=3, copper=5)


def test_atomic_write_leaves_no_tmp(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    manager = StateManager.new_game("alpha", settings=settings)
    manager.save()
    assert not list(manager.slot_dir.glob("*.tmp"))


def test_meta_fields(tmp_path: Path) -> None:
    manager = StateManager.new_game("alpha", settings=make_settings(tmp_path))
    meta = json.loads(manager.meta_path.read_text(encoding="utf-8"))
    assert meta["slot"] == "alpha"
    assert meta["hp"] == 25
    assert meta["location"] == "black_marsh_sector_3"
    assert meta["active_branch"] == "canon_log.jsonl"
    assert meta["schema_version"] == 3


def test_list_slots_and_missing(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    StateManager.new_game("one", settings=settings)
    StateManager.new_game("two", settings=settings)
    assert StateManager.list_slots(settings=settings) == ["one", "two"]
    with pytest.raises(sm.StateNotFoundError):
        StateManager.load("nope", settings=settings)


# ---------------------------------------------------------------------------
# Canon integration
# ---------------------------------------------------------------------------


def test_canon_roundtrip_and_verification(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    manager = StateManager.new_game("alpha", settings=settings)
    manager.append_event(
        type="move",
        t=manager.state.time,
        actor="player",
        input="Иду в топь",
        effects=[{"kind": "discover", "target": "black_marsh_sector_3", "value": "black_marsh_sector_3"}],
    )
    manager.save()

    reloaded = StateManager.load("alpha", settings=settings)
    assert len(reloaded.canon) == 1
    reloaded.canon.verify()


def test_tampered_canon_blocks_load(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    manager = StateManager.new_game("alpha", settings=settings)
    manager.append_event(type="narration", t=manager.state.time, actor="world", narration="ok")
    manager.save()

    canon_file = manager.slot_dir / "canon_log.jsonl"
    record = json.loads(canon_file.read_text(encoding="utf-8").splitlines()[0])
    record["narration"] = "lies"
    canon_file.write_text(json.dumps(record, ensure_ascii=False) + "\n", encoding="utf-8")

    with pytest.raises(CanonIntegrityError):
        StateManager.load("alpha", settings=settings)


# ---------------------------------------------------------------------------
# Migrations
# ---------------------------------------------------------------------------


def test_migration_path_is_applied(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    settings = make_settings(tmp_path)
    default = json.loads((DATA_DIR / "default_state.json").read_text(encoding="utf-8"))

    def v0_to_v1(data: dict) -> dict:
        upgraded = json.loads(json.dumps(default))
        upgraded["player"]["name"] = data["legacy_name"]
        upgraded["schema_version"] = 1
        return upgraded

    monkeypatch.setitem(sm.MIGRATIONS, 0, v0_to_v1)

    slot_dir = settings.saves_path / "legacy"
    slot_dir.mkdir(parents=True)
    (slot_dir / "state.json").write_text(
        json.dumps({"schema_version": 0, "legacy_name": "Древний"}),
        encoding="utf-8",
    )

    manager = StateManager.load("legacy", settings=settings)
    assert manager.state.schema_version == 3  # v0 -> v1 -> v2 -> v3
    assert manager.state.player.name == "Древний"


def test_future_schema_is_rejected() -> None:
    with pytest.raises(StateSchemaError):
        migrate_state({"schema_version": 999})


def test_missing_migration_is_rejected() -> None:
    with pytest.raises(StateSchemaError):
        migrate_state({"schema_version": 0})


# ---------------------------------------------------------------------------
# Sanitization (spec section 6)
# ---------------------------------------------------------------------------


def test_sanitized_view_hides_secrets(tmp_path: Path) -> None:
    manager = StateManager.new_game("alpha", settings=make_settings(tmp_path))
    manager.state.rng.seed = "SUPER-SECRET-SEED"
    manager.state.dm_settings.fudge_dice = True

    view = manager.state.sanitized_view()
    blob = json.dumps(view)

    assert "SUPER-SECRET-SEED" not in blob
    assert "rng" not in view
    assert "fudge" not in blob
    assert view["player"]["hp"] == 25
    assert view["player"]["equipped"] == ["heavy_axe", "leather_armor"]
    assert view["time"] == {"day": 1, "hour": 8, "minute": 0}


def test_sanitized_view_includes_context(tmp_path: Path) -> None:
    manager = StateManager.new_game("alpha", settings=make_settings(tmp_path))
    view = manager.state.sanitized_view(
        locations={
            "black_marsh_sector_3": {
                "name": "Серные Топи",
                "description": "Туман ест сапоги.",
                "connections": ["suspension_bridge", "underground_caves"],
            }
        },
        npc_names={"old_mira": "Старая Мира"},
        canon_facts=["Рваный Влад мёртв"],
        recent_events=["[14:10] move → Серные Топи"],
        intent_request="Прыгаю через пропасть",
    )
    assert view["location"]["name"] == "Серные Топи"
    assert view["location"]["exits"] == []  # undiscovered locations stay hidden
    assert view["visible_npcs"][0]["name"] == "Старая Мира"
    assert view["intent_request"] == "Прыгаю через пропасть"


# ---------------------------------------------------------------------------
# Dice integration
# ---------------------------------------------------------------------------


def test_dice_engine_reads_and_writes_hidden_state(tmp_path: Path) -> None:
    manager = StateManager.new_game("alpha", settings=make_settings(tmp_path))
    manager.state.rng.seed = "seed-1"
    manager.state.dm_settings.fudge_dice = True

    engine = manager.dice_engine()
    engine.roll("1d20")
    engine.roll("1d20")
    manager.store_dice(engine)

    assert manager.state.rng.counter == 2
    assert manager.state.rng.seed == "seed-1"
    assert manager.state.dm_settings.fudge_dice is True


# ---------------------------------------------------------------------------
# Snapshot derivation
# ---------------------------------------------------------------------------


def test_derive_snapshot_rules(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    manager = StateManager.new_game("alpha", settings=settings)
    manager.append_event(
        type="npc_death",
        t=manager.state.time,
        actor="npc:rotten_vlad",
        effects=[{"kind": "death", "target": "rotten_vlad"}],
    )
    manager.append_event(
        type="move",
        t=manager.state.time,
        actor="player",
        effects=[{"kind": "discover", "target": "underground_caves", "value": "underground_caves"}],
    )
    manager.append_event(
        type="quest_update",
        t=manager.state.time,
        actor="player",
        effects=[{"kind": "quest_stage", "target": "sacred_spring", "value": "completed"}],
    )

    snapshot = derive_snapshot(manager.canon.events, manager.state)
    assert "npc:rotten_vlad is dead" in snapshot["world_facts"]
    assert "underground_caves discovered" in snapshot["world_facts"]
    assert "quest sacred_spring completed" in snapshot["world_facts"]
    assert snapshot["deaths"] == ["npc:rotten_vlad"]
    assert snapshot["event_count"] == 3
    assert snapshot["last_hash"] == manager.canon.last_hash


def test_recent_window_is_compact(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    manager = StateManager.new_game("alpha", settings=settings)
    manager.append_event(
        type="dice_check",
        t=manager.state.time,
        actor="player",
        roll={"dice": "d20", "dc": 14, "outcome": "success"},
    )
    window = manager.recent_window()
    assert len(window) == 1
    assert "d20" in window[0] and "DC14" in window[0]


# ---------------------------------------------------------------------------
# Currency
# ---------------------------------------------------------------------------


def test_currency_conversion() -> None:
    assert Currency(gold=1, silver=2, copper=3).to_copper() == 123
    assert Currency.from_copper(123) == Currency(gold=1, silver=2, copper=3)
    assert Currency(silver=6, copper=32).plus(silver=1).to_copper() == 6 * 10 + 32 + 10
