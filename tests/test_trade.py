"""Tests for trading with NPCs."""

from __future__ import annotations

from pathlib import Path

import pytest

from engine import trade_engine as trade
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
    manager = StateManager.new_game("trade", settings=settings)
    manager.state.rng.seed = "trade"
    return manager


def test_stock_and_merchant_detection(manager, catalogs) -> None:
    assert trade.is_merchant(catalogs, "old_mira")
    lines = trade.stock(catalogs, "old_mira")
    assert any(line.item_id == "healing_potion" for line in lines)
    assert not trade.is_merchant(catalogs, "bobs")


def test_ask_about_item(manager, catalogs) -> None:
    answer = trade.answer_about(catalogs, "old_mira", "зелье")
    assert "Да" in answer and "Зелье лечения" in answer
    missing = trade.answer_about(catalogs, "old_mira", "ракета")
    assert "нет" in missing.lower()


def test_buy_success_and_failure(manager, catalogs) -> None:
    before = manager.state.player.currency.to_copper()
    message = trade.buy(manager, catalogs, "old_mira", "зелье")
    assert "Куплено" in message
    assert manager.state.player.currency.to_copper() == before - 20
    assert any(inst.item_id == "healing_potion" for inst in manager.state.items.values())

    # not enough money
    manager.state.player.currency = manager.state.player.currency.__class__(copper=0)
    assert "Не хватает" in trade.buy(manager, catalogs, "old_mira", "зелье")


def test_sell_returns_half(manager, catalogs) -> None:
    from engine import inventory_engine as inv

    instance = inv.add_item(manager.state, catalogs, manager.state.player, "healing_potion")
    before = manager.state.player.currency.to_copper()
    message = trade.sell(manager, catalogs, "old_mira", "зелье")
    assert "Продано" in message
    assert manager.state.player.currency.to_copper() == before + 10  # half of 20
    assert instance.instance_id not in manager.state.items


def test_buy_logs_to_canon(manager, catalogs) -> None:
    before = len(manager.canon)
    trade.buy(manager, catalogs, "old_mira", "antidote")
    assert len(manager.canon) == before + 1
    assert manager.canon.events[-1].type == "item_use"
