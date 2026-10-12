"""NPCs: creation, reputation, dispositions, social checks, death.

Reputation is a signed value in ``[-100, 100]`` and maps to a disposition band.
Social interactions resolve as deterministic checks and record ``dialogue``
events with their reputation deltas. A dead NPC is permanent.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from engine.catalogs import Catalogs, NpcDef
from engine.dice_engine import Advantage, DiceEngine, DiceSpec, RollResult
from engine.rules_engine import effective_disposition
from engine.state_manager import NPCState, StateManager

REP_MIN, REP_MAX = -100, 100

FRIENDLY_THRESHOLD = 30
HOSTILE_THRESHOLD = -30

REP_DELTA = {
    "crit_success": 8,
    "success": 3,
    "failure": -2,
    "crit_failure": -6,
}


class NpcError(ValueError):
    """Raised on invalid NPC operations."""


@dataclass
class SocialResult:
    npc_id: str
    intent: str
    roll: RollResult
    rep_before: int
    rep_after: int
    disposition: str


def disposition_for(rep: int) -> str:
    if rep >= FRIENDLY_THRESHOLD:
        return "friendly"
    if rep <= HOSTILE_THRESHOLD:
        return "hostile"
    return "neutral"


def clamp_rep(rep: int) -> int:
    return max(REP_MIN, min(REP_MAX, int(rep)))


def get_or_create_npc(state, catalogs: Catalogs, npc_id: str) -> NPCState:  # type: ignore[no-untyped-def]
    """Return the runtime NPC, instantiating it from the catalog on first use."""
    existing = state.npcs.get(npc_id)
    if existing is not None:
        return existing
    definition: NpcDef = catalogs.npc(npc_id)
    npc = NPCState(
        id=definition.id,
        status="alive",
        rep=clamp_rep(definition.rep),
        disposition=effective_disposition(state.rules, definition.rep),  # type: ignore[arg-type]
        hp=definition.hp,
        max_hp=definition.max_hp,
        ac=definition.ac,
        stats=dict(definition.stats),
    )
    state.npcs[npc_id] = npc
    return npc


def change_rep(manager: StateManager, npc_id: str, delta: int) -> NPCState:
    npc = manager.state.npcs[npc_id]
    npc.rep = clamp_rep(npc.rep + delta)
    npc.disposition = effective_disposition(manager.state.rules, npc.rep)  # type: ignore[assignment]
    return npc


def social_check(
    manager: StateManager,
    catalogs: Catalogs,
    npc_id: str,
    *,
    intent: str,
    stat: str = "CHA",
    dc: int = 12,
    advantage: Advantage = "normal",
) -> SocialResult:
    """Resolve a persuade/intimidate/deceive attempt against an NPC."""
    state = manager.state
    npc = get_or_create_npc(state, catalogs, npc_id)
    if npc.status != "alive":
        raise NpcError(f"{npc_id} cannot talk (status={npc.status})")

    engine = manager.dice_engine()
    roll = engine.roll(
        DiceSpec(1, 20),
        modifier=state.player.stat(stat),
        dc=dc,
        advantage=advantage,
    )
    manager.store_dice(engine)

    rep_before = npc.rep
    delta = REP_DELTA.get(roll.outcome or "failure", 0)
    npc.rep = clamp_rep(npc.rep + delta)
    npc.disposition = effective_disposition(state.rules, npc.rep)  # type: ignore[assignment]
    npc.dialogue_state["last_intent"] = intent

    manager.append_event(
        type="dialogue",
        t=state.time,
        actor=f"npc:{npc_id}",
        input=f"{intent} ({stat})",
        intent={"action_type": "dialogue", "intent": intent, "stat": stat, "dc": dc},
        roll=roll.model_dump(mode="json"),
        effects=[{"kind": "rep_delta", "target": npc_id, "value": delta}],
    )
    return SocialResult(
        npc_id=npc_id,
        intent=intent,
        roll=roll,
        rep_before=rep_before,
        rep_after=npc.rep,
        disposition=npc.disposition,
    )


def kill_npc(
    manager: StateManager,
    catalogs: Catalogs,
    npc_id: str,
    *,
    killer: str = "player",
    method: str = "weapon",
    force: bool = False,
    adjudicator: Any | None = None,
) -> NPCState:
    """Kill an NPC permanently and record the crime.

    The killer's faction reputation is **not** changed here: it depends on
    whether anyone witnessed the act and can name the killer, which is handled
    by ``crime_engine``. A previously killed NPC cannot be resurrected unless
    ``force`` is set (the only sanctioned override, used by ``/retcon --force``).
    """
    state = manager.state
    npc = get_or_create_npc(state, catalogs, npc_id)
    if npc.permanently_dead and not force:
        raise NpcError(f"{npc_id} is permanently dead; use force=True to override")
    if npc.status == "dead" and not force:
        raise NpcError(f"{npc_id} is already dead")

    definition = catalogs.npc(npc_id)
    npc.status = "dead"
    npc.permanently_dead = True
    npc.rep = REP_MIN
    npc.disposition = "hostile"
    npc.hp = 0
    npc.statuses = []

    manager.append_event(
        type="npc_death",
        t=state.time,
        actor=f"npc:{npc_id}",
        input=f"slain {npc_id}",
        intent={"action_type": "kill", "target": npc_id, "faction": definition.faction},
        effects=[{"kind": "death", "target": npc_id, "value": None}],
    )

    from engine import crime_engine  # local import avoids a cycle

    crime_engine.record_kill(
        manager,
        catalogs,
        killer=killer,
        victim=npc_id,
        method=method,
        adjudicator=adjudicator,
    )
    return npc
