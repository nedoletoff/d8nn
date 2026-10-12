"""Smoke tests for the immutable canon log (spec step 4)."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import pytest

from engine.canon_log import (
    GENESIS_HASH,
    CanonIntegrityError,
    CanonLog,
    canonical_json,
    compute_hash,
    make_event_id,
)

FIXED_NOW = datetime(2026, 4, 12, 14, 20, 33)


def _seed_log(path: Path, count: int = 3) -> CanonLog:
    log = CanonLog.create(path)
    for step in range(count):
        log.append(
            type="move",
            t={"day": 1, "hour": 9 + step, "minute": 0},
            actor="player",
            input=f"move {step}",
            narration=f"step {step}",
            now=FIXED_NOW,
        )
    return log


# ---------------------------------------------------------------------------
# Hash chain
# ---------------------------------------------------------------------------


def test_first_event_links_to_genesis(tmp_path: Path) -> None:
    log = CanonLog.create(tmp_path / "canon_log.jsonl")
    event = log.append(type="narration", t={"day": 1}, actor="world", narration="hi")
    assert event.prev_hash == GENESIS_HASH
    assert event.hash.startswith("sha256:")


def test_chain_links_events(tmp_path: Path) -> None:
    log = _seed_log(tmp_path / "canon_log.jsonl", 3)
    events = log.events
    assert events[0].prev_hash == GENESIS_HASH
    assert events[1].prev_hash == events[0].hash
    assert events[2].prev_hash == events[1].hash
    assert log.last_hash == events[2].hash


def test_load_verifies_valid_chain(tmp_path: Path) -> None:
    path = tmp_path / "canon_log.jsonl"
    _seed_log(path, 4)
    reloaded = CanonLog.load(path)
    assert len(reloaded) == 4
    reloaded.verify()  # must not raise


def test_compute_hash_is_order_independent() -> None:
    left = {"a": 1, "b": [2, 3], "c": {"x": "y"}}
    right = {"c": {"x": "y"}, "b": [2, 3], "a": 1}
    assert canonical_json(left) == canonical_json(right)
    assert compute_hash(GENESIS_HASH, left) == compute_hash(GENESIS_HASH, right)


def test_empty_file_loads() -> None:
    import tempfile

    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "canon_log.jsonl"
        path.write_text("", encoding="utf-8")
        assert len(CanonLog.load(path)) == 0


# ---------------------------------------------------------------------------
# Tamper detection
# ---------------------------------------------------------------------------


def test_tampering_with_body_is_detected(tmp_path: Path) -> None:
    path = tmp_path / "canon_log.jsonl"
    _seed_log(path, 3)

    lines = path.read_text(encoding="utf-8").splitlines()
    record = json.loads(lines[0])
    record["narration"] = "REWRITTEN HISTORY"
    lines[0] = json.dumps(record, ensure_ascii=False)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    with pytest.raises(CanonIntegrityError) as caught:
        CanonLog.load(path)
    assert "hash" in caught.value.reason.lower()


def test_broken_linkage_is_detected(tmp_path: Path) -> None:
    path = tmp_path / "canon_log.jsonl"
    _seed_log(path, 3)

    lines = path.read_text(encoding="utf-8").splitlines()
    record = json.loads(lines[1])
    record["prev_hash"] = "sha256:" + "f" * 64
    lines[1] = json.dumps(record, ensure_ascii=False)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    with pytest.raises(CanonIntegrityError) as caught:
        CanonLog.load(path)
    assert caught.value.index == 2


def test_deleted_event_is_detected(tmp_path: Path) -> None:
    path = tmp_path / "canon_log.jsonl"
    _seed_log(path, 3)
    lines = path.read_text(encoding="utf-8").splitlines()
    path.write_text("\n".join(lines[:1] + lines[2:]) + "\n", encoding="utf-8")
    with pytest.raises(CanonIntegrityError):
        CanonLog.load(path)


# ---------------------------------------------------------------------------
# Append-only behaviour
# ---------------------------------------------------------------------------


def test_append_never_rewrites_earlier_lines(tmp_path: Path) -> None:
    path = tmp_path / "canon_log.jsonl"
    log = _seed_log(path, 2)
    before = path.read_text(encoding="utf-8")

    log.append(type="narration", t={"day": 1}, actor="world", narration="later")

    after = path.read_text(encoding="utf-8")
    assert after.startswith(before), "existing bytes must be untouched"


# ---------------------------------------------------------------------------
# Amendments (narration only)
# ---------------------------------------------------------------------------


def test_amend_narration_appends_new_event(tmp_path: Path) -> None:
    log = _seed_log(tmp_path / "canon_log.jsonl", 1)
    original_id = log.events[0].id

    amendment = log.amend(original_id, "narration", "corrected prose", now=FIXED_NOW)

    assert amendment.type == "canon_amendment"
    assert amendment.intent is not None
    assert amendment.intent["amends"] == original_id
    assert amendment.intent["old"] == "step 0"
    assert amendment.narration == "corrected prose"
    # Original event is untouched and the log only grew.
    assert log.events[0].narration == "step 0"
    assert len(log) == 2


@pytest.mark.parametrize("field", ["roll", "effects", "outcome", "type", "actor"])
def test_amend_forbidden_fields_raise(tmp_path: Path, field: str) -> None:
    log = _seed_log(tmp_path / "canon_log.jsonl", 1)
    with pytest.raises(ValueError):
        log.amend(log.events[0].id, field, "nope")


def test_amend_unknown_event_raises(tmp_path: Path) -> None:
    log = _seed_log(tmp_path / "canon_log.jsonl", 1)
    with pytest.raises(KeyError):
        log.amend("evt_does_not_exist", "narration", "x")


# ---------------------------------------------------------------------------
# Branches (/retcon undo)
# ---------------------------------------------------------------------------


def test_undo_forks_branch_and_preserves_old_file(tmp_path: Path) -> None:
    path = tmp_path / "canon_log.jsonl"
    log = _seed_log(path, 4)
    original_text = path.read_text(encoding="utf-8")
    target = log.events[2].id  # keep events 0 and 1, drop 2 and 3

    branch = log.undo(target, now=FIXED_NOW)

    # New branch keeps only the surviving prefix and still verifies.
    assert len(branch) == 2
    assert [e.id for e in branch] == [e.id for e in log.events[:2]]
    branch.verify()

    # Old branch is preserved byte-for-byte, not deleted.
    rejected = tmp_path / "canon_log.rejected.20260412_142033.jsonl"
    assert rejected.exists()
    assert rejected.read_text(encoding="utf-8") == original_text

    # Active file moved to the branch path.
    assert not path.exists()
    assert branch.path == tmp_path / "canon_log.branch.20260412_142033.jsonl"
    assert branch.path.exists()


def test_undo_first_event_yields_empty_branch(tmp_path: Path) -> None:
    path = tmp_path / "canon_log.jsonl"
    log = _seed_log(path, 3)
    branch = log.undo(log.events[0].id, now=FIXED_NOW)
    assert len(branch) == 0
    assert branch.last_hash == GENESIS_HASH


def test_undo_unknown_event_raises(tmp_path: Path) -> None:
    log = _seed_log(tmp_path / "canon_log.jsonl", 2)
    with pytest.raises(KeyError):
        log.undo("evt_missing")


# ---------------------------------------------------------------------------
# Misc
# ---------------------------------------------------------------------------


def test_find_and_index(tmp_path: Path) -> None:
    log = _seed_log(tmp_path / "canon_log.jsonl", 3)
    assert log.find("nope") is None
    assert log.index_of(log.events[1].id) == 1


def test_event_id_shape() -> None:
    event_id = make_event_id(now=FIXED_NOW)
    assert event_id.startswith("evt_20260412_142033_")
    assert len(event_id) == len("evt_20260412_142033_") + 6
