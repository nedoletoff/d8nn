"""Game state: the single authoritative source of numbers.

Everything the LLM is *not* allowed to touch lives here. The manager handles:

* atomic persistence (``.tmp`` -> ``os.replace``) of ``state.json``;
* ``meta.json`` / ``ui_prefs.json`` / ``canon_snapshot.json`` next to a slot;
* schema versioning with a migration registry;
* the sanctioned dice engine (seeded from the hidden ``rng`` block);
* a **sanitized** view (:meth:`GameState.sanitized_view`) that is the only thing
  ever handed to the LLM, and a canon summary for the narrative layer.

The hidden RNG seed and fudge settings never leave this module.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Literal

from pydantic import BaseModel, Field

from engine.canon_log import (
    CANON_ENCODING_VERSION,
    DEFAULT_CANON_FILENAME,
    CanonEvent,
    CanonLog,
    TimeStamp,
)
from engine.config import Settings, get_settings
from engine.dice_engine import DiceEngine
from engine.rules_engine import Ruleset

SCHEMA_VERSION = 3

#: version -> function upgrading a raw dict by exactly one version.
MIGRATIONS: dict[int, Callable[[dict[str, Any]], dict[str, Any]]] = {}


def _migrate_v1_to_v2(data: dict[str, Any]) -> dict[str, Any]:
    """v2 adds the crime/investigation journals (see ``crime_engine``)."""
    data.setdefault("crimes", {})
    data.setdefault("investigations", {})
    return data


def _migrate_v2_to_v3(data: dict[str, Any]) -> dict[str, Any]:
    """v3 adds the player-editable world rules (see ``rules_engine``)."""
    data.setdefault("rules", {})
    return data


MIGRATIONS[1] = _migrate_v1_to_v2
MIGRATIONS[2] = _migrate_v2_to_v3

#: Effect kinds understood by the engines (used by the snapshot builder).
EFFECT_HP = "hp_delta"
EFFECT_MP = "mp_delta"
EFFECT_CURRENCY = "currency_delta"
EFFECT_ITEM_ADD = "item_add"
EFFECT_ITEM_REMOVE = "item_remove"
EFFECT_REP = "rep_delta"
EFFECT_DISCOVER = "discover"
EFFECT_STATUS_ADD = "status_add"
EFFECT_STATUS_REMOVE = "status_remove"
EFFECT_XP = "xp_delta"
EFFECT_QUEST_STAGE = "quest_stage"
EFFECT_DEATH = "death"

_OUTCOME_MARK = {
    "crit_success": "\u2705 crit",
    "success": "\u2705",
    "failure": "\u274c",
    "crit_failure": "\U0001f480 crit",
}


class StateError(Exception):
    """Base error for state persistence."""


class StateSchemaError(StateError):
    """Raised when a save cannot be migrated to the current schema."""


class StateNotFoundError(StateError):
    """Raised when a slot does not exist."""


# ---------------------------------------------------------------------------
# Sub-models
# ---------------------------------------------------------------------------


class Currency(BaseModel):
    """Money. Exchange rate is 1 gold : 10 silver : 100 copper."""

    gold: int = 0
    silver: int = 0
    copper: int = 0

    def to_copper(self) -> int:
        return self.gold * 100 + self.silver * 10 + self.copper

    @classmethod
    def from_copper(cls, total: int) -> "Currency":
        total = max(0, int(total))
        gold, remainder = divmod(total, 100)
        silver, copper = divmod(remainder, 10)
        return cls(gold=gold, silver=silver, copper=copper)

    def plus(self, *, gold: int = 0, silver: int = 0, copper: int = 0) -> "Currency":
        return Currency.from_copper(self.to_copper() + gold * 100 + silver * 10 + copper)


class ItemInstance(BaseModel):
    """An owned copy of a catalog item."""

    instance_id: str
    item_id: str
    quantity: int = 1
    charges: int | None = None


class Character(BaseModel):
    """A player character, companion or (for reference) notable actor."""

    id: str
    name: str
    kind: Literal["player", "companion", "npc"] = "player"
    level: int = 1
    xp: int = 0
    hp: int = 1
    max_hp: int = 1
    mp: int = 0
    max_mp: int = 0
    ac: int = 10
    stats: dict[str, int] = Field(default_factory=dict)
    statuses: list[str] = Field(default_factory=list)
    #: slot -> item instance id (e.g. {"weapon": "itm_a1b2", "armor": None})
    equipped: dict[str, str | None] = Field(default_factory=dict)
    #: item instance ids carried but not equipped
    inventory: list[str] = Field(default_factory=list)
    currency: Currency = Field(default_factory=Currency)
    alive: bool = True
    permanently_dead: bool = False

    def stat(self, name: str) -> int:
        return int(self.stats.get(name, 0))

    @property
    def hp_ratio(self) -> float:
        return 0.0 if self.max_hp <= 0 else self.hp / self.max_hp


class LocationState(BaseModel):
    """Dynamic per-location state (static data lives in ``world_regions.json``)."""

    id: str
    discovered: bool = False
    visited: bool = False


class WorldState(BaseModel):
    locations: dict[str, LocationState] = Field(default_factory=dict)
    current_location: str = ""

    def is_discovered(self, location_id: str) -> bool:
        entry = self.locations.get(location_id)
        return bool(entry and entry.discovered)


class QuestState(BaseModel):
    id: str
    stage: str = "start"
    status: Literal["active", "completed", "failed", "abandoned"] = "active"
    progress: dict[str, int] = Field(default_factory=dict)


class NPCState(BaseModel):
    id: str
    status: Literal["alive", "dead", "missing"] = "alive"
    rep: int = 0
    disposition: Literal["hostile", "neutral", "friendly"] = "neutral"
    dialogue_state: dict[str, Any] = Field(default_factory=dict)
    permanently_dead: bool = False
    # Combat stats (populated from the NPC catalog when the NPC is created).
    hp: int = 1
    max_hp: int = 1
    ac: int = 10
    stats: dict[str, int] = Field(default_factory=dict)
    statuses: list[str] = Field(default_factory=list)

    def stat(self, name: str) -> int:
        return int(self.stats.get(name, 0))


class CombatState(BaseModel):
    active: bool = False
    round: int = 0
    turn_index: int = 0
    order: list[str] = Field(default_factory=list)
    encounter_id: str | None = None


class RngState(BaseModel):
    """Hidden random state. Never exposed to the LLM."""

    seed: str | None = None
    counter: int = 0


class WitnessReport(BaseModel):
    """What a single potential witness perceived during a crime."""

    npc_id: str
    faction: str = ""
    noticed: bool = False
    can_identify: bool = False
    will_report: bool = False
    perception_total: int = 0


class CrimeRecord(BaseModel):
    """A killing and everything needed to adjudicate who learns about it.

    Whether a faction turns against the killer is decided later, from witnesses
    and evidence, not from the mere fact of a death.
    """

    id: str
    killer: str
    victim: str
    location: str
    method: str = "weapon"
    t: TimeStamp = Field(default_factory=TimeStamp)
    witnesses: list[WitnessReport] = Field(default_factory=list)
    body_disposed: bool = False
    body_found: bool = True
    evidence: list[dict[str, Any]] = Field(default_factory=list)
    exposure: Literal["none", "suspicion", "accusation"] = "none"
    who_knows: list[str] = Field(default_factory=list)
    adjudicated: bool = False
    adjudication_source: str = "engine"
    #: net faction-reputation applied so far (lets re-adjudication correct it)
    rep_applied_delta: int = 0
    notes: str | None = None


class Investigation(BaseModel):
    """A scheduled, time-delayed inquiry into a crime."""

    id: str
    crime_id: str
    location: str
    due_time: TimeStamp = Field(default_factory=TimeStamp)
    resolved: bool = False
    result: str | None = None


class DmSettings(BaseModel):
    fudge_dice: bool = False
    model: str | None = None


# ---------------------------------------------------------------------------
# Root state
# ---------------------------------------------------------------------------


class GameState(BaseModel):
    schema_version: int = SCHEMA_VERSION
    rng: RngState = Field(default_factory=RngState)
    time: TimeStamp = Field(default_factory=TimeStamp)
    player: Character
    party: list[Character] = Field(default_factory=list)
    items: dict[str, ItemInstance] = Field(default_factory=dict)
    world: WorldState = Field(default_factory=WorldState)
    quests: dict[str, QuestState] = Field(default_factory=dict)
    npcs: dict[str, NPCState] = Field(default_factory=dict)
    crimes: dict[str, CrimeRecord] = Field(default_factory=dict)
    investigations: dict[str, Investigation] = Field(default_factory=dict)
    combat: CombatState | None = None
    flags: dict[str, Any] = Field(default_factory=dict)
    dm_settings: DmSettings = Field(default_factory=DmSettings)
    rules: Ruleset = Field(default_factory=Ruleset)
    game_over: bool = False
    active_branch: str = DEFAULT_CANON_FILENAME
    canon_encoding_version: int = CANON_ENCODING_VERSION

    # -- lookups -------------------------------------------------------------

    def resolve_item(self, instance_id: str) -> ItemInstance | None:
        return self.items.get(instance_id)

    def character(self, ref: str) -> Character | None:
        if ref == "player":
            return self.player
        for companion in self.party:
            if companion.id == ref:
                return companion
        return None

    def active_quests(self) -> list[QuestState]:
        return [quest for quest in self.quests.values() if quest.status == "active"]

    def living_npcs(self) -> list[NPCState]:
        return [npc for npc in self.npcs.values() if npc.status == "alive"]

    # -- LLM sanitization ----------------------------------------------------

    def sanitized_view(
        self,
        *,
        locations: dict[str, dict[str, Any]] | None = None,
        npc_names: dict[str, str] | None = None,
        canon_facts: list[str] | None = None,
        recent_events: list[str] | None = None,
        intent_request: str | None = None,
        visible_npc_ids: list[str] | None = None,
        map_grid: str | None = None,
    ) -> dict[str, Any]:
        """The **only** projection of state the LLM is ever allowed to see.

        Deliberately omits: the RNG seed/counter, fudge settings, item
        mechanics, undiscovered locations, quest triggers and the full log.
        """
        locations = locations or {}
        npc_names = npc_names or {}

        def character_card(character: Character) -> dict[str, Any]:
            equipped = [
                self.items[instance].item_id
                for instance in character.equipped.values()
                if instance and instance in self.items
            ]
            carried = [
                self.items[instance].item_id
                for instance in character.inventory
                if instance in self.items
            ]
            return {
                "name": character.name,
                "hp": character.hp,
                "max_hp": character.max_hp,
                "mp": character.mp,
                "max_mp": character.max_mp,
                "level": character.level,
                "stats": dict(character.stats),
                "statuses": list(character.statuses),
                "equipped": equipped,
                "carried": carried,
                "currency": character.currency.model_dump(),
            }

        current = self.world.current_location
        location_data = locations.get(current, {})
        exits = [
            location_id
            for location_id in location_data.get("connections", [])
            if self.world.is_discovered(location_id)
        ]

        npc_ids = visible_npc_ids if visible_npc_ids is not None else [
            npc.id for npc in self.living_npcs()
        ]
        visible_npcs = [
            {
                "id": npc_id,
                "name": npc_names.get(npc_id, npc_id),
                "status": npc.status,
                "rep": npc.rep,
                "disposition": npc.disposition,
            }
            for npc_id in npc_ids
            if (npc := self.npcs.get(npc_id)) is not None
        ]

        return {
            "player": character_card(self.player),
            "party": [character_card(member) for member in self.party],
            "location": {
                "id": current,
                "name": location_data.get("name", current),
                "desc": location_data.get("description", ""),
                "exits": exits,
            },
            "time": self.time.model_dump(),
            "active_quests": [
                {"id": quest.id, "stage": quest.stage} for quest in self.active_quests()
            ],
            "visible_npcs": visible_npcs,
            "setting": self.flags.get("setting", ""),
            "world_rules": self.rules.model_dump(),
            "world_laws": list(self.rules.laws),
            "map_grid": map_grid,
            "canon_facts": list(canon_facts or []),
            "recent_events": list(recent_events or []),
            "intent_request": intent_request,
        }


# ---------------------------------------------------------------------------
# Migration
# ---------------------------------------------------------------------------


def migrate_state(data: dict[str, Any]) -> dict[str, Any]:
    """Upgrade a raw state dict to :data:`SCHEMA_VERSION` in place (copy)."""
    data = dict(data)
    version = int(data.get("schema_version", 0))
    if version > SCHEMA_VERSION:
        raise StateSchemaError(
            f"save was written by a newer schema ({version} > {SCHEMA_VERSION})"
        )
    while version < SCHEMA_VERSION:
        migration = MIGRATIONS.get(version)
        if migration is None:
            raise StateSchemaError(f"no migration registered from schema {version}")
        data = migration(data)
        version += 1
        data["schema_version"] = version
    return data


# ---------------------------------------------------------------------------
# Atomic IO
# ---------------------------------------------------------------------------


def atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def atomic_write_json(path: Path, payload: Any) -> None:
    text = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)
    atomic_write_text(path, text + "\n")


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# Manager
# ---------------------------------------------------------------------------


class StateManager:
    """Binds a :class:`GameState` and its :class:`CanonLog` to one save slot."""

    def __init__(
        self,
        slot_dir: Path,
        state: GameState,
        canon: CanonLog,
        *,
        settings: Settings | None = None,
    ) -> None:
        self.slot_dir = Path(slot_dir)
        self.state = state
        self.canon = canon
        self.settings = settings or get_settings()

    # -- paths ---------------------------------------------------------------

    @property
    def state_path(self) -> Path:
        return self.slot_dir / "state.json"

    @property
    def meta_path(self) -> Path:
        return self.slot_dir / "meta.json"

    @property
    def snapshot_path(self) -> Path:
        return self.slot_dir / "canon_snapshot.json"

    @property
    def ui_prefs_path(self) -> Path:
        return self.slot_dir / "ui_prefs.json"

    @property
    def canon_path(self) -> Path:
        return self.slot_dir / self.state.active_branch

    # -- construction --------------------------------------------------------

    @classmethod
    def new_game(
        cls,
        slot: str,
        *,
        settings: Settings | None = None,
        default_state_path: Path | None = None,
        world: Any | None = None,
    ) -> "StateManager":
        settings = settings or get_settings()
        if world is not None:
            from engine.worlds import build_state

            state = build_state(world, settings.data_path)
        else:
            default_path = default_state_path or (settings.data_path / "default_state.json")
            raw = read_json(default_path)
            state = GameState(**migrate_state(raw))

        slot_dir = settings.saves_path / slot
        slot_dir.mkdir(parents=True, exist_ok=True)
        canon = CanonLog.create(slot_dir / state.active_branch)

        manager = cls(slot_dir, state, canon, settings=settings)
        manager.save()
        return manager

    @classmethod
    def load(cls, slot: str, *, settings: Settings | None = None) -> "StateManager":
        settings = settings or get_settings()
        slot_dir = settings.saves_path / slot
        state_path = slot_dir / "state.json"
        if not state_path.exists():
            raise StateNotFoundError(f"no such slot: {slot}")
        state = GameState(**migrate_state(read_json(state_path)))
        # A missing canon file is created empty; a *corrupted* one still raises.
        canon = CanonLog.load_or_create(slot_dir / state.active_branch)
        return cls(slot_dir, state, canon, settings=settings)

    @classmethod
    def exists(cls, slot: str, *, settings: Settings | None = None) -> bool:
        settings = settings or get_settings()
        return (settings.saves_path / slot / "state.json").exists()

    @classmethod
    def list_slots(cls, *, settings: Settings | None = None) -> list[str]:
        settings = settings or get_settings()
        root = settings.saves_path
        if not root.exists():
            return []
        return sorted(
            child.name
            for child in root.iterdir()
            if child.is_dir() and (child / "state.json").exists()
        )

    @classmethod
    def saves_info(cls, *, settings: Settings | None = None) -> list[dict[str, Any]]:
        """Slot metadata (from ``meta.json``) for the 'past games' list."""
        settings = settings or get_settings()
        root = settings.saves_path
        if not root.exists():
            return []
        infos: list[dict[str, Any]] = []
        for slot in cls.list_slots(settings=settings):
            meta: dict[str, Any] = {}
            meta_path = root / slot / "meta.json"
            if meta_path.exists():
                try:
                    meta = read_json(meta_path)
                except Exception:
                    meta = {}
            infos.append({"slot": slot, **meta})
        # newest first
        infos.sort(key=lambda info: str(info.get("saved_at", "")), reverse=True)
        return infos

    # -- persistence ---------------------------------------------------------

    def save(self) -> None:
        """Atomically persist state, meta, snapshot and UI prefs."""
        self.state.schema_version = SCHEMA_VERSION
        atomic_write_json(self.state_path, self.state.model_dump(mode="json"))
        atomic_write_json(self.ui_prefs_path, self._load_ui_prefs())
        self.build_snapshot()
        atomic_write_json(self.meta_path, self.meta())

    def _load_ui_prefs(self) -> dict[str, Any]:
        if self.ui_prefs_path.exists():
            return read_json(self.ui_prefs_path)
        return {}

    def meta(self) -> dict[str, Any]:
        return {
            "slot": self.slot_dir.name,
            "saved_at": datetime.now(timezone.utc).isoformat(),
            "game_time": self.state.time.model_dump(),
            "hp": self.state.player.hp,
            "max_hp": self.state.player.max_hp,
            "location": self.state.world.current_location,
            "schema_version": self.state.schema_version,
            "active_branch": self.state.active_branch,
            "canon_encoding_version": self.state.canon_encoding_version,
            "events": len(self.canon),
        }

    # -- dice ----------------------------------------------------------------

    def dice_engine(self) -> DiceEngine:
        return DiceEngine(
            seed=self.state.rng.seed,
            counter=self.state.rng.counter,
            fudge=self.state.dm_settings.fudge_dice,
        )

    def store_dice(self, engine: DiceEngine) -> None:
        """Write the hidden RNG counter back into the state."""
        self.state.rng.seed = engine.seed
        self.state.rng.counter = engine.counter
        self.state.dm_settings.fudge_dice = engine.fudge

    # -- canon ---------------------------------------------------------------

    def append_event(self, **fields: Any) -> CanonEvent:
        return self.canon.append(**fields)

    def recent_window(self, count: int = 15) -> list[str]:
        """Compact one-line summaries of the last ``count`` events (for the LLM)."""
        events = self.canon.events[-max(1, count):]
        window: list[str] = []
        for event in events:
            stamp = f"[{event.t.hour:02d}:{event.t.minute:02d}]"
            line = f"{stamp} {event.type} {event.actor}"
            roll = event.roll or {}
            outcome = roll.get("outcome")
            if outcome:
                line += f" {roll.get('dice', '?')} DC{roll.get('dc', '?')} {_OUTCOME_MARK.get(outcome, outcome)}"
            elif event.input:
                line += f" \u2192 {event.input}"
            window.append(line)
        return window

    # -- snapshot ------------------------------------------------------------

    def build_snapshot(self) -> dict[str, Any]:
        """Rebuild ``canon_snapshot.json`` from the log (never from the LLM)."""
        snapshot = derive_snapshot(self.canon.events, self.state)
        atomic_write_json(self.snapshot_path, snapshot)
        return snapshot

    def read_snapshot(self) -> dict[str, Any]:
        if self.snapshot_path.exists():
            return read_json(self.snapshot_path)
        return derive_snapshot(self.canon.events, self.state)


# ---------------------------------------------------------------------------
# Snapshot derivation (pure, testable)
# ---------------------------------------------------------------------------


def derive_snapshot(events: list[CanonEvent], state: GameState) -> dict[str, Any]:
    """Deterministic canon summary. No LLM is involved at any point."""
    deaths: list[str] = []
    discoveries: list[str] = []
    quests_closed: list[str] = []
    world_facts: list[str] = []

    for event in events:
        if event.type == "npc_death" or any(
            effect.get("kind") == EFFECT_DEATH for effect in event.effects
        ):
            victim = event.actor
            if victim not in deaths:
                deaths.append(victim)
                world_facts.append(f"{victim} is dead")

        for effect in event.effects:
            kind = effect.get("kind")
            if kind == EFFECT_DISCOVER:
                place = str(effect.get("value") or effect.get("target"))
                if place not in discoveries:
                    discoveries.append(place)
                    world_facts.append(f"{place} discovered")
            elif kind == EFFECT_QUEST_STAGE and effect.get("value") == "completed":
                quest_id = str(effect.get("target"))
                if quest_id not in quests_closed:
                    quests_closed.append(quest_id)
                    world_facts.append(f"quest {quest_id} completed")

    reputations = {
        npc_id: npc.rep for npc_id, npc in sorted(state.npcs.items()) if npc.rep != 0
    }

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "event_count": len(events),
        "last_event_id": events[-1].id if events else None,
        "last_hash": events[-1].hash if events else None,
        "world_facts": world_facts[:30],
        "deaths": deaths,
        "discoveries": discoveries,
        "quests_closed": quests_closed,
        "reputations": reputations,
    }
