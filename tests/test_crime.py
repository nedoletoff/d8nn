"""Tests for the crime / witness / evidence / investigation system.

These cover the rule that faction reputation only suffers once the world can
actually name the killer, plus the explicit body flags handed to the narrator.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from engine import crime_engine as crime
from engine import time_engine as tm
from engine.catalogs import load_catalogs
from engine.config import Settings
from engine.state_manager import (
    CrimeRecord,
    StateManager,
    TimeStamp,
    WitnessReport,
)

DATA_DIR = Path(__file__).resolve().parents[1] / "data"
BANDITS = "faction_rep:bandits"


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
    manager = StateManager.new_game("crime", settings=settings)
    manager.state.rng.seed = "crime-seed"
    return manager


def _crime_with_witnesses(manager: StateManager, witnesses, **overrides) -> CrimeRecord:
    record = CrimeRecord(
        id="crime_test",
        killer="player",
        victim="rotten_vlad",
        location="black_marsh_sector_3",
        method=overrides.get("method", "poison"),
        t=manager.state.time,
        witnesses=witnesses,
        body_found=overrides.get("body_found", True),
        body_disposed=overrides.get("body_disposed", False),
        evidence=overrides.get("evidence", []),
    )
    manager.state.crimes[record.id] = record
    return record


def _force_dispose(manager: StateManager, catalogs, crime_id: str) -> bool:
    """Retry body disposal until it succeeds (rolls are deterministic per seed)."""
    for _ in range(60):
        if crime.dispose_body(manager, catalogs, crime_id, dc=-100):
            return True
    return False


# ---------------------------------------------------------------------------
# Killing quietly leaves relations intact
# ---------------------------------------------------------------------------


def test_kill_alone_does_not_touch_faction_rep(manager, catalogs) -> None:
    # Nobody but the (silent) companion is around, so no one can name the killer.
    manager.state.world.current_location = "underground_caves"
    crime.record_kill(manager, catalogs, killer="player", victim="rotten_vlad", method="poison")

    record = next(iter(manager.state.crimes.values()))
    assert record.exposure in ("none", "suspicion")
    assert manager.state.flags.get(BANDITS) in (None, 0)
    # Only allies witnessed it, and they do not talk.
    assert all(not w.will_report or w.faction != "party" for w in record.witnesses)


def test_disposing_the_body_dissolves_suspicion(manager, catalogs) -> None:
    record = crime.record_kill(
        manager, catalogs, killer="player", victim="rotten_vlad", method="poison"
    )
    assert record.body_found

    assert _force_dispose(manager, catalogs, record.id)

    assert record.body_disposed
    assert not record.body_found
    assert record.evidence == []
    assert record.exposure == "none"
    assert manager.state.flags.get(BANDITS) in (None, 0)


# ---------------------------------------------------------------------------
# A witness who can identify the killer -> accusation
# ---------------------------------------------------------------------------


def test_identifying_witness_triggers_accusation_and_rep_loss(manager, catalogs) -> None:
    _crime_with_witnesses(
        manager,
        [
            WitnessReport(
                npc_id="old_mira",
                faction="village",
                noticed=True,
                can_identify=True,
                will_report=True,
            ),
            WitnessReport(
                npc_id="bobs",
                faction="party",
                noticed=True,
                can_identify=True,
                will_report=False,
            ),
        ],
    )
    record = crime.adjudicate(manager, catalogs, "crime_test")

    assert record.exposure == "accusation"
    assert "old_mira" in record.who_knows
    assert manager.state.flags[BANDITS] == -25
    assert manager.canon.events[-1].type == "crime"


def test_loud_method_is_automatically_witnessed(manager, catalogs) -> None:
    record = crime.record_kill(
        manager, catalogs, killer="player", victim="rotten_vlad", method="spell"
    )
    assert record.method == "spell"
    bobs = [w for w in record.witnesses if w.npc_id == "bobs"]
    assert bobs and bobs[0].noticed and bobs[0].can_identify


# ---------------------------------------------------------------------------
# The engine clamps an over-eager verdict (this is what the LLM must obey)
# ---------------------------------------------------------------------------


def test_llm_accusation_without_identifier_is_downgraded(manager, catalogs) -> None:
    def liar(context):
        return {"exposure": "accusation", "who_knows": ["old_mira"], "reason": "drama"}

    _crime_with_witnesses(
        manager,
        [
            WitnessReport(
                npc_id="old_mira",
                faction="village",
                noticed=True,
                can_identify=False,
                will_report=True,
            )
        ],
        body_found=False,
        body_disposed=True,
        evidence=[],
    )
    record = crime.adjudicate(manager, catalogs, "crime_test", adjudicator=liar)

    assert record.exposure == "suspicion"  # downgraded: nobody could name the killer
    assert record.adjudication_source == "llm"
    assert manager.state.flags.get(BANDITS) in (None, 0)


def test_llm_accusation_with_no_evidence_at_all_is_downgraded_to_none(manager, catalogs) -> None:
    def liar(context):
        return {"exposure": "accusation", "who_knows": [], "reason": "drama"}

    _crime_with_witnesses(
        manager,
        [
            WitnessReport(
                npc_id="old_mira",
                faction="village",
                noticed=False,
                can_identify=False,
                will_report=False,
            )
        ],
        body_found=False,
        body_disposed=True,
        evidence=[],
    )
    record = crime.adjudicate(manager, catalogs, "crime_test", adjudicator=liar)
    assert record.exposure == "none"
    assert record.who_knows == []


def test_secret_is_narrated_with_explicit_body_flags(manager, catalogs) -> None:
    record = _crime_with_witnesses(manager, [], body_found=False, body_disposed=True)
    context = crime.build_crime_context(manager.state, catalogs, record)

    assert context["body_found"] is False
    assert context["body_disposed"] is True
    assert "factions" in context
    assert context["location"]["id"] == "black_marsh_sector_3"
    assert context["method"] == "poison"


# ---------------------------------------------------------------------------
# Adjudication is idempotent about reputation
# ---------------------------------------------------------------------------


def test_re_adjudication_does_not_double_charge(manager, catalogs) -> None:
    _crime_with_witnesses(
        manager,
        [
            WitnessReport(
                npc_id="old_mira",
                faction="village",
                noticed=True,
                can_identify=True,
                will_report=True,
            )
        ],
    )
    crime.adjudicate(manager, catalogs, "crime_test")
    assert manager.state.flags[BANDITS] == -25
    crime.adjudicate(manager, catalogs, "crime_test")
    assert manager.state.flags[BANDITS] == -25  # corrected, not accumulated


# ---------------------------------------------------------------------------
# Delayed investigation
# ---------------------------------------------------------------------------


def test_investigation_scheduled_only_when_body_present(manager, catalogs) -> None:
    record = crime.record_kill(manager, catalogs, killer="player", victim="rotten_vlad")
    investigations = [i for i in manager.state.investigations.values() if i.crime_id == record.id]
    assert investigations
    assert not investigations[0].resolved


def test_disposal_cancels_investigation(manager, catalogs) -> None:
    record = crime.record_kill(manager, catalogs, killer="player", victim="rotten_vlad")
    _force_dispose(manager, catalogs, record.id)
    investigations = [i for i in manager.state.investigations.values() if i.crime_id == record.id]
    assert investigations[0].resolved
    assert investigations[0].result == "body_hidden"


def test_investigation_runs_after_delay(manager, catalogs) -> None:
    _crime_with_witnesses(
        manager,
        [],  # nobody saw, but strong identifiable evidence on the body
        method="spell",
        body_found=True,
        evidence=[{"kind": "arcane_burns", "strength": 3, "identifiable": True}],
    )
    original = crime.schedule_investigation(manager, manager.state.crimes["crime_test"])
    assert original is not None

    # Not yet due.
    assert crime.run_due_investigations(manager, catalogs) == []

    tm.advance(manager.state, crime.INVESTIGATION_DELAY_MINUTES + 1)
    resolved = crime.run_due_investigations(manager, catalogs)

    assert len(resolved) == 1
    assert resolved[0].resolved
    assert resolved[0].result in ("leads_found", "dead_end")
    if resolved[0].result == "leads_found":
        assert manager.state.crimes["crime_test"].exposure == "accusation"


def test_no_investigation_after_body_hidden(manager, catalogs) -> None:
    record = crime.record_kill(manager, catalogs, killer="player", victim="rotten_vlad")
    _force_dispose(manager, catalogs, record.id)
    tm.advance(manager.state, crime.INVESTIGATION_DELAY_MINUTES + 1)
    resolved = crime.run_due_investigations(manager, catalogs)
    assert resolved == []
