"""Tests for the read-only rules handbook.

The handbook must never write to the canon log, mutate state or create files.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from engine import rules_handbook as rules
from engine.config import Settings
from engine.state_manager import StateManager

DATA_DIR = Path(__file__).resolve().parents[1] / "data"


@pytest.fixture
def manager(tmp_path: Path) -> StateManager:
    settings = Settings(
        save_dir=tmp_path / "saves",
        log_dir=tmp_path / "logs",
        data_dir=DATA_DIR,
        offline_mode="force",
    )
    return StateManager.new_game("rules", settings=settings)


def test_topics_are_exposed() -> None:
    for key in ("checks", "combat", "crime", "canon", "llm", "commands"):
        assert key in rules.topics()


def test_explain_matches_by_keyword() -> None:
    result = rules.explain("как работает урон в бою")
    assert result.matched == "combat"
    assert "Инициатива" in result.text


def test_explain_matches_by_key() -> None:
    assert rules.explain("crime").matched == "crime"
    assert rules.explain("CANON").matched == "canon"


def test_unknown_topic_is_suggested() -> None:
    result = rules.explain("совершенно нерелевантный запрос про котов")
    assert result.matched is None
    assert result.suggested
    assert "/rule" in result.text


def test_explain_can_include_current_numbers(manager: StateManager) -> None:
    result = rules.explain("инвентарь", state=manager.state)
    assert "грузоподъёмность 25" in result.text  # 10 + STR(3) * 5


def test_handbook_is_read_only(manager: StateManager) -> None:
    before_canon = len(manager.canon)
    before_state = manager.state.model_dump()
    before_counter = manager.state.rng.counter
    files_before = {p.name for p in manager.slot_dir.iterdir()}

    for topic in rules.topics():
        rules.explain(topic, state=manager.state)

    assert len(manager.canon) == before_canon
    assert manager.state.model_dump() == before_state
    assert manager.state.rng.counter == before_counter
    assert {p.name for p in manager.slot_dir.iterdir()} == files_before


def test_crime_topic_reflects_real_constants() -> None:
    text = rules.explain("свидетели и расследование").text
    assert "accusation -25" in text
    assert "6 игровых часов" in text
