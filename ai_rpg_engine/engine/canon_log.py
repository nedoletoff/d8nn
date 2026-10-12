"""Immutable, append-only canon log with a sha256 hash-chain.

The canon log is the project's source of truth *about the past*. It is never
edited in place:

* Each event is one JSON line in ``canon_log.jsonl``.
* ``hash = sha256(prev_hash + canonical_json(event_without_hash))`` forms a
  chain, blockchain-style. Tampering with any older record breaks every
  subsequent hash, and :meth:`CanonLog.load` raises :class:`CanonIntegrityError`.
* ``/retcon undo`` never deletes history. It *renames* the active branch to
  ``canon_log.rejected.<ts>.jsonl`` and copies the surviving prefix into a new
  ``canon_log.branch.<ts>.jsonl``. The old file keeps its bytes forever.
* ``/retcon edit`` is expressed as a brand-new ``canon_amendment`` event that
  points at the original. Only narrative fields (``narration``, ``flavor``) may
  be amended; mechanics (``roll``, ``effects``, ``outcome``) never can.

Canonical serialization is pinned (``sort_keys``, tight separators, UTF-8) and
versioned so a chain written by one version verifies on another.
"""

from __future__ import annotations

import hashlib
import json
import os
import secrets
from datetime import datetime
from pathlib import Path
from typing import Any, Iterator

from pydantic import BaseModel, Field

#: Bump only if the canonical JSON encoding ever changes shape.
CANON_ENCODING_VERSION = 1

#: The canonical branch filename inside a save slot.
DEFAULT_CANON_FILENAME = "canon_log.jsonl"

#: The "previous hash" of the very first event in any branch.
GENESIS_HASH = "sha256:" + "0" * 64

#: Fields an amendment may touch. Mechanics are deliberately excluded.
AMENDABLE_FIELDS: frozenset[str] = frozenset({"narration", "flavor"})

#: Recognised event types (kept as ``str`` so new types need no code change).
KNOWN_EVENT_TYPES: frozenset[str] = frozenset(
    {
        "dice_check",
        "check",
        "dialogue",
        "combat",
        "move",
        "travel",
        "item_use",
        "quest_update",
        "npc_death",
        "narration",
        "canon_amendment",
        "crime",
        "game_over",
        "sleep",
        "freeform",
    }
)


class CanonIntegrityError(ValueError):
    """Raised when the on-disk hash-chain does not verify."""

    def __init__(
        self,
        reason: str,
        *,
        index: int | None = None,
        event_id: str | None = None,
        expected: str | None = None,
        actual: str | None = None,
    ) -> None:
        self.reason = reason
        self.index = index
        self.event_id = event_id
        self.expected = expected
        self.actual = actual
        location = f" at line {index}" if index is not None else ""
        detail = f" (expected {expected!r}, got {actual!r})" if expected is not None else ""
        super().__init__(f"canon integrity error{location}: {reason}{detail}")


# ---------------------------------------------------------------------------
# Event model
# ---------------------------------------------------------------------------


class TimeStamp(BaseModel):
    """In-game time (see ``time_engine``). Never goes backwards."""

    day: int = 1
    hour: int = 0
    minute: int = 0


class CanonEvent(BaseModel):
    """A single immutable record in the canon."""

    id: str
    t: TimeStamp
    type: str
    actor: str
    input: str | None = None
    intent: dict[str, Any] | None = None
    roll: dict[str, Any] | None = None
    effects: list[dict[str, Any]] = Field(default_factory=list)
    narration: str | None = None
    flavor: str | None = None
    prev_hash: str
    hash: str


# ---------------------------------------------------------------------------
# Canonical encoding
# ---------------------------------------------------------------------------


def canonical_json(payload: dict[str, Any]) -> str:
    """Deterministic JSON encoding used inside the hash-chain."""
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def compute_hash(prev_hash: str, payload: dict[str, Any]) -> str:
    """``sha256(prev_hash + canonical_json(payload))`` as a ``sha256:<hex>`` string."""
    digest = hashlib.sha256((prev_hash + canonical_json(payload)).encode("utf-8")).hexdigest()
    return f"sha256:{digest}"


def _payload_of(record: dict[str, Any]) -> dict[str, Any]:
    """The event body that gets hashed (everything except ``hash``)."""
    return {key: value for key, value in record.items() if key != "hash"}


# ---------------------------------------------------------------------------
# Canon log
# ---------------------------------------------------------------------------


class CanonLog:
    """An append-only sequence of :class:`CanonEvent` bound to one file."""

    def __init__(self, path: Path, events: list[CanonEvent] | None = None) -> None:
        self.path = Path(path)
        self._events: list[CanonEvent] = list(events or [])

    # -- construction --------------------------------------------------------

    @classmethod
    def create(cls, path: Path) -> "CanonLog":
        """Create a new, empty canon file (parent dirs created as needed)."""
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("", encoding="utf-8")
        return cls(path, [])

    @classmethod
    def load(cls, path: Path) -> "CanonLog":
        """Load and *fully verify* a canon file. Raises on any inconsistency."""
        path = Path(path)
        raw_records = _read_records(path)
        events = _verify_chain(raw_records)
        return cls(path, events)

    @classmethod
    def load_or_create(cls, path: Path) -> "CanonLog":
        path = Path(path)
        return cls.load(path) if path.exists() else cls.create(path)

    # -- introspection -------------------------------------------------------

    @property
    def events(self) -> list[CanonEvent]:
        return list(self._events)

    def __len__(self) -> int:
        return len(self._events)

    def __iter__(self) -> Iterator[CanonEvent]:
        return iter(self._events)

    @property
    def last_hash(self) -> str:
        return self._events[-1].hash if self._events else GENESIS_HASH

    def find(self, event_id: str) -> CanonEvent | None:
        """Return the event with ``event_id`` or ``None``."""
        for event in self._events:
            if event.id == event_id:
                return event
        return None

    def index_of(self, event_id: str) -> int:
        for position, event in enumerate(self._events):
            if event.id == event_id:
                return position
        raise KeyError(f"unknown event id: {event_id}")

    # -- append --------------------------------------------------------------

    def append(
        self,
        *,
        type: str,
        t: TimeStamp | dict[str, Any],
        actor: str,
        input: str | None = None,  # noqa: A002 - matches the canon schema
        intent: dict[str, Any] | None = None,
        roll: dict[str, Any] | None = None,
        effects: list[dict[str, Any]] | None = None,
        narration: str | None = None,
        flavor: str | None = None,
        event_id: str | None = None,
        now: datetime | None = None,
    ) -> CanonEvent:
        """Append one event, chaining it to the current tail, and persist it."""
        stamp = t if isinstance(t, TimeStamp) else TimeStamp(**t)
        event = CanonEvent(
            id=event_id or make_event_id(now=now),
            t=stamp,
            type=type,
            actor=actor,
            input=input,
            intent=intent,
            roll=roll,
            effects=effects or [],
            narration=narration,
            flavor=flavor,
            prev_hash=self.last_hash,
            hash="",
        )
        record = event.model_dump(mode="json", exclude_none=True)
        record["hash"] = compute_hash(event.prev_hash, _payload_of(record))

        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
            handle.flush()
            os.fsync(handle.fileno())

        stored = CanonEvent(**record)
        self._events.append(stored)
        return stored

    # -- amendments (narration only) -----------------------------------------

    def amend(
        self,
        target_id: str,
        field: str,
        value: str,
        *,
        actor: str = "world",
        event_id: str | None = None,
        now: datetime | None = None,
    ) -> CanonEvent:
        """Append a ``canon_amendment`` referencing ``target_id``.

        Only :data:`AMENDABLE_FIELDS` are allowed. The original event is never
        touched; a new event records the correction.
        """
        if field not in AMENDABLE_FIELDS:
            raise ValueError(
                f"field {field!r} is not amendable; allowed: {sorted(AMENDABLE_FIELDS)}"
            )
        original = self.find(target_id)
        if original is None:
            raise KeyError(f"unknown event id: {target_id}")
        old_value = getattr(original, field, None)
        return self.append(
            type="canon_amendment",
            t=original.t,
            actor=actor,
            input=f"amend {field} of {target_id}",
            intent={"amends": target_id, "field": field, "old": old_value},
            narration=value,
            event_id=event_id,
            now=now,
        )

    # -- branches (/retcon undo) ---------------------------------------------

    def undo(self, target_id: str, *, now: datetime | None = None) -> "CanonLog":
        """Fork history: keep everything *before* ``target_id`` in a new branch.

        The current file is renamed to ``canon_log.rejected.<ts>.jsonl`` (its
        bytes are preserved) and the surviving prefix is copied verbatim into
        ``canon_log.branch.<ts>.jsonl``. Returns the new :class:`CanonLog`.
        """
        index = self.index_of(target_id)  # raises KeyError when missing
        stamp = (now or datetime.now()).strftime("%Y%m%d_%H%M%S")
        rejected = self.path.with_name(f"canon_log.rejected.{stamp}.jsonl")
        branch = self.path.with_name(f"canon_log.branch.{stamp}.jsonl")

        # Preserve the old branch wholesale, then build the new one atomically.
        os.replace(self.path, rejected)
        tmp = branch.with_suffix(branch.suffix + ".tmp")
        with rejected.open("r", encoding="utf-8") as source, tmp.open("w", encoding="utf-8") as sink:
            for position, line in enumerate(source):
                if position >= index:
                    break
                sink.write(line)
        os.replace(tmp, branch)

        return CanonLog.load(branch)

    # -- verification --------------------------------------------------------

    def verify(self) -> None:
        """Re-read the file from disk and verify the whole chain."""
        _verify_chain(_read_records(self.path))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_event_id(*, now: datetime | None = None) -> str:
    """Generate an event id: ``evt_YYYYMMDD_HHMMSS_<6 hex>``."""
    moment = now or datetime.now()
    return f"evt_{moment.strftime('%Y%m%d_%H%M%S')}_{secrets.token_hex(3)}"


def _read_records(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        raise FileNotFoundError(f"canon file not found: {path}")
    records: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            text = line.strip()
            if not text:
                continue
            try:
                record = json.loads(text)
            except json.JSONDecodeError as exc:
                raise CanonIntegrityError(
                    "malformed JSON", index=line_number, actual=str(exc)
                ) from exc
            if not isinstance(record, dict):
                raise CanonIntegrityError("record is not an object", index=line_number)
            records.append(record)
    return records


def _verify_chain(records: list[dict[str, Any]]) -> list[CanonEvent]:
    """Recompute every hash and linkage; return parsed events on success."""
    events: list[CanonEvent] = []
    prev = GENESIS_HASH
    for position, record in enumerate(records, start=1):
        stored_id = record.get("id")
        if record.get("prev_hash") != prev:
            raise CanonIntegrityError(
                "broken hash-chain linkage",
                index=position,
                event_id=stored_id,
                expected=prev,
                actual=record.get("prev_hash"),
            )
        expected_hash = compute_hash(prev, _payload_of(record))
        if record.get("hash") != expected_hash:
            raise CanonIntegrityError(
                "event hash mismatch (record was tampered with)",
                index=position,
                event_id=stored_id,
                expected=expected_hash,
                actual=record.get("hash"),
            )
        prev = expected_hash
        events.append(CanonEvent(**record))
    return events
