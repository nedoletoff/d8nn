"""Crime, witnesses, evidence and the delayed fallout of a killing.

Killing someone does **not** by itself damage relations with a faction. That
only happens once enough of the world knows *and can name* the killer. This
module:

* records a :class:`~engine.state_manager.CrimeRecord` at the moment of death;
* gathers witnesses present at the scene and rolls **perception vs stealth**
  (loud methods such as magic are witnessed automatically);
* decides who is willing to talk;
* models the body (disposed / found) and the physical evidence left behind;
* asks an *adjudicator* for a verdict on awareness and then **validates** that
  verdict against the deterministic flags, so a narrator can never claim an
  identification that the facts do not support (e.g. no body, no witness who
  saw a face);
* applies the faction-reputation consequence only when the killer is actually
  named;
* schedules a **time-delayed investigation** that may expose the killer later.

The default adjudicator is a pure-Python rule set (offline mode). The narrative
layer can supply an LLM-backed one; whatever it returns is clamped here.
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass
from typing import Any, Callable, Literal

from engine import npc_engine as npc
from engine import time_engine as tm
from engine.catalogs import Catalogs
from engine.dice_engine import DiceEngine
from engine.state_manager import (
    CrimeRecord,
    Investigation,
    StateManager,
    TimeStamp,
    WitnessReport,
)

Method = Literal["weapon", "spell", "poison", "accident", "unarmed"]
Exposure = Literal["none", "suspicion", "accusation"]

#: A verdict callable: context in, ``{"exposure", "who_knows", "reason"}`` out.
Adjudicator = Callable[[dict[str, Any]], dict[str, Any]]

INVESTIGATION_DELAY_MINUTES = 6 * 60  # six in-game hours before anyone looks
WILL_REPORT_DC = 13
IDENTIFY_MARGIN = 5
NIGHT_IDENTIFY_PENALTY = 5

#: Faction-reputation hit applied per exposure level (accusation only).
FACTION_REP_PENALTY: dict[str, int] = {"none": 0, "suspicion": 0, "accusation": -25}


@dataclass(frozen=True)
class MethodProfile:
    name: str
    stealth_mod: int
    evidence: str
    evidence_strength: int
    loud: bool


METHOD_PROFILES: dict[str, MethodProfile] = {
    "weapon": MethodProfile("weapon", -2, "wounds", 2, False),
    "spell": MethodProfile("spell", -5, "arcane_burns", 3, True),
    "poison": MethodProfile("poison", 3, "toxin", 1, False),
    "accident": MethodProfile("accident", 1, "staged_scene", 1, False),
    "unarmed": MethodProfile("unarmed", 0, "blunt_force", 2, False),
}

_METHOD_ALIASES = {"melee": "weapon", "bow": "weapon", "magic": "spell", "push": "accident"}


class CrimeError(ValueError):
    """Raised on invalid crime operations."""


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------


def _new_id(prefix: str) -> str:
    return f"{prefix}_{secrets.token_hex(4)}"


def _light_for_hour(hour: int) -> str:
    if 6 <= hour < 8:
        return "dawn"
    if 8 <= hour < 18:
        return "day"
    if 18 <= hour < 20:
        return "dusk"
    return "night"


def _killer_stat(state, killer_ref: str, stat: str) -> int:  # type: ignore[no-untyped-def]
    if killer_ref == "player":
        return state.player.stat(stat)
    character = state.character(killer_ref)
    if character is not None:
        return character.stat(stat)
    key = killer_ref[4:] if killer_ref.startswith("npc:") else killer_ref
    npc_state = state.npcs.get(key)
    return npc_state.stat(stat) if npc_state else 0


def _profile(method: str) -> MethodProfile:
    method = _METHOD_ALIASES.get(method, method)
    return METHOD_PROFILES.get(method, METHOD_PROFILES["unarmed"])


# ---------------------------------------------------------------------------
# Fact gathering (deterministic)
# ---------------------------------------------------------------------------


def _candidates_at(
    manager: StateManager, catalogs: Catalogs, location: str, victim: str, killer: str
) -> list[tuple[str, str]]:
    """(npc_id, faction) pairs of everyone present at the scene."""
    state = manager.state
    present: list[tuple[str, str]] = []
    for npc_id, definition in catalogs.npcs.items():
        if npc_id == victim or npc_id == killer:
            continue
        if definition.location != location:
            continue
        if state.character(npc_id) is not None:
            continue  # a companion; handled by the party loop below
        runtime = npc.get_or_create_npc(state, catalogs, npc_id)
        if runtime.status != "alive":
            continue
        present.append((npc_id, definition.faction))
    for member in state.party:
        if member.alive and member.id != killer:
            present.append((member.id, "party"))
    return present


def _will_report(
    engine: DiceEngine, faction: str, victim_faction: str
) -> bool:
    if faction == "party":
        return False  # allies keep quiet
    if faction == victim_faction:
        return True  # loyalty to the victim's own faction
    return engine.roll("d20").total >= WILL_REPORT_DC


def _gather_witnesses(
    manager: StateManager,
    catalogs: Catalogs,
    engine: DiceEngine,
    location: str,
    victim: str,
    victim_faction: str,
    killer: str,
    stealth_score: int,
    profile: MethodProfile,
) -> list[WitnessReport]:
    state = manager.state
    night = _light_for_hour(state.time.hour) == "night"
    reports: list[WitnessReport] = []

    for npc_id, faction in _candidates_at(manager, catalogs, location, victim, killer):
        actor = state.npcs.get(npc_id) or state.character(npc_id)
        wisdom = actor.stat("WIS") if actor is not None else 0
        perception_total = 99 if profile.loud else engine.roll("d20", modifier=wisdom).total
        noticed = profile.loud or perception_total >= stealth_score

        can_identify = False
        if noticed:
            if profile.loud:
                can_identify = True
            else:
                margin = IDENTIFY_MARGIN + (NIGHT_IDENTIFY_PENALTY if night else 0)
                can_identify = (perception_total - stealth_score) >= margin

        will_report = noticed and _will_report(engine, faction, victim_faction)
        reports.append(
            WitnessReport(
                npc_id=npc_id,
                faction=faction,
                noticed=noticed,
                can_identify=can_identify,
                will_report=will_report,
                perception_total=perception_total,
            )
        )
    return reports


def _evidence_for(profile: MethodProfile, body_found: bool) -> list[dict[str, Any]]:
    if not body_found:
        return []
    return [
        {
            "kind": profile.evidence,
            "strength": profile.evidence_strength,
            "identifiable": profile.evidence_strength >= 3,
        }
    ]


# ---------------------------------------------------------------------------
# Context for the adjudicator / narration
# ---------------------------------------------------------------------------


def build_crime_context(
    state,  # type: ignore[no-untyped-def]
    catalogs: Catalogs,
    crime: CrimeRecord,
    *,
    investigation_success: bool = False,
) -> dict[str, Any]:
    """Everything an adjudicator (or narrator) needs, with explicit body flags.

    ``body_found`` and ``body_disposed`` are passed separately on purpose so a
    model cannot reason "they identified the killer from the body" when there is
    no body to find.
    """
    factions: dict[str, list[dict[str, str]]] = {}
    for npc_id, definition in catalogs.npcs.items():
        factions.setdefault(definition.faction, []).append(
            {"id": npc_id, "name": definition.name}
        )

    victim_def = catalogs.npcs.get(crime.victim)
    location_def = catalogs.locations.get(crime.location)
    return {
        "crime_id": crime.id,
        "killer": crime.killer,
        "victim": {
            "id": crime.victim,
            "name": victim_def.name if victim_def else crime.victim,
            "faction": victim_def.faction if victim_def else "",
        },
        "location": {
            "id": crime.location,
            "name": location_def.name if location_def else crime.location,
            "description": location_def.description if location_def else "",
            "danger_level": location_def.danger_level if location_def else 0,
        },
        "time": crime.t.model_dump(),
        "light": _light_for_hour(crime.t.hour),
        "method": crime.method,
        "body_found": crime.body_found,
        "body_disposed": crime.body_disposed,
        "witnesses": [witness.model_dump() for witness in crime.witnesses],
        "evidence": list(crime.evidence),
        "factions": factions,
        "investigation_success": investigation_success,
    }


# ---------------------------------------------------------------------------
# Adjudication
# ---------------------------------------------------------------------------


def _reporters(crime: CrimeRecord) -> set[str]:
    return {w.npc_id for w in crime.witnesses if w.will_report}


def _identifiers(crime: CrimeRecord) -> set[str]:
    return {w.npc_id for w in crime.witnesses if w.will_report and w.can_identify}


def _evidence_identified(crime: CrimeRecord) -> bool:
    return crime.body_found and any(e.get("identifiable") for e in crime.evidence)


def rule_based_exposure(crime: CrimeRecord, *, investigation_success: bool = False) -> Exposure:
    if _identifiers(crime):
        return "accusation"
    if investigation_success and _evidence_identified(crime):
        return "accusation"
    if _reporters(crime) or crime.body_found:
        return "suspicion"
    return "none"


def rule_based_adjudicator(context: dict[str, Any]) -> dict[str, Any]:
    """Offline default: derive the verdict purely from the gathered facts."""
    witnesses = context["witnesses"]
    reporters = [w["npc_id"] for w in witnesses if w["will_report"]]
    identifiers = [w["npc_id"] for w in witnesses if w["will_report"] and w["can_identify"]]
    body_found = context["body_found"]
    evidence_identified = body_found and any(
        e.get("identifiable") for e in context["evidence"]
    )
    investigation_success = context.get("investigation_success", False)

    if identifiers or (investigation_success and evidence_identified):
        exposure: Exposure = "accusation"
        who = identifiers or []
    elif reporters or body_found:
        exposure = "suspicion"
        who = reporters
    else:
        exposure = "none"
        who = []
    return {"exposure": exposure, "who_knows": who, "reason": "rule-based"}


def validate_verdict(
    crime: CrimeRecord,
    catalogs: Catalogs,
    context: dict[str, Any],
    verdict: dict[str, Any],
) -> dict[str, Any]:
    """Clamp a (possibly LLM-authored) verdict to what the facts allow."""
    exposure = str(verdict.get("exposure", "none"))
    if exposure not in ("none", "suspicion", "accusation"):
        exposure = "none"

    reporters = _reporters(crime)
    identifiers = _identifiers(crime)
    evidence_identified = _evidence_identified(crime)
    investigation_success = bool(context.get("investigation_success"))

    if exposure == "accusation" and not identifiers and not evidence_identified:
        if not (investigation_success and (identifiers or evidence_identified)):
            exposure = "suspicion"
    if exposure == "suspicion" and not reporters and not crime.body_found:
        exposure = "none"

    victim_faction = catalogs.npcs[crime.victim].faction if crime.victim in catalogs.npcs else ""
    faction_members = {
        npc_id
        for npc_id, definition in catalogs.npcs.items()
        if definition.faction == victim_faction
    }

    allowed = set(reporters)
    if exposure == "accusation":
        allowed |= identifiers
        allowed |= faction_members  # word spreads within the victim's faction

    who = {str(x) for x in (verdict.get("who_knows") or [])} & allowed
    if exposure == "accusation" and not who:
        who = identifiers or faction_members
    if exposure == "suspicion" and not who:
        who = reporters

    return {
        "exposure": exposure,
        "who_knows": sorted(who),
        "reason": str(verdict.get("reason", "")),
    }


def _apply_faction_rep(manager: StateManager, catalogs: Catalogs, crime: CrimeRecord) -> int:
    """Apply (or correct) the faction-reputation hit for the current exposure."""
    state = manager.state
    victim_def = catalogs.npcs.get(crime.victim)
    if victim_def is None:
        return 0
    key = f"faction_rep:{victim_def.faction}"
    new_delta = FACTION_REP_PENALTY.get(crime.exposure, 0)
    difference = new_delta - crime.rep_applied_delta
    if difference:
        state.flags[key] = int(state.flags.get(key, 0)) + difference
        crime.rep_applied_delta = new_delta
    return difference


def adjudicate(
    manager: StateManager,
    catalogs: Catalogs,
    crime_id: str,
    *,
    adjudicator: Adjudicator | None = None,
    investigation_success: bool = False,
) -> CrimeRecord:
    """Ask the adjudicator for a verdict, validate it and log the fallout."""
    crime = manager.state.crimes.get(crime_id)
    if crime is None:
        raise CrimeError(f"unknown crime: {crime_id}")

    context = build_crime_context(
        manager.state, catalogs, crime, investigation_success=investigation_success
    )
    raw = (adjudicator or rule_based_adjudicator)(context)
    verdict = validate_verdict(crime, catalogs, context, raw)

    crime.exposure = verdict["exposure"]
    crime.who_knows = verdict["who_knows"]
    crime.adjudicated = True
    crime.adjudication_source = "llm" if adjudicator else "engine"
    crime.notes = verdict.get("reason") or None

    rep_delta = _apply_faction_rep(manager, catalogs, crime)

    effects: list[dict[str, Any]] = [
        {"kind": "exposure", "target": crime.victim, "value": crime.exposure}
    ]
    if rep_delta:
        effects.append({"kind": "rep_delta", "target": crime.victim, "value": rep_delta})

    manager.append_event(
        type="crime",
        t=manager.state.time,
        actor=f"npc:{crime.victim}",
        input=f"crime {crime.id}",
        intent={
            "action_type": "crime",
            "crime": crime.id,
            "exposure": crime.exposure,
            "who_knows": crime.who_knows,
            "source": crime.adjudication_source,
        },
        effects=effects,
    )
    return crime


# ---------------------------------------------------------------------------
# Recording a kill
# ---------------------------------------------------------------------------


def record_kill(
    manager: StateManager,
    catalogs: Catalogs,
    *,
    killer: str,
    victim: str,
    method: str = "weapon",
    adjudicator: Adjudicator | None = None,
) -> CrimeRecord:
    """Record a killing and immediately adjudicate the initial fallout."""
    state = manager.state
    if victim in {crime.victim for crime in state.crimes.values()}:
        existing = next(c for c in state.crimes.values() if c.victim == victim)
        return existing

    if victim not in state.npcs:
        npc.get_or_create_npc(state, catalogs, victim)

    profile = _profile(method)
    victim_def = catalogs.npcs.get(victim)
    victim_faction = victim_def.faction if victim_def else ""
    location = state.world.current_location

    engine = manager.dice_engine()
    stealth_score = engine.roll(
        "d20", modifier=_killer_stat(state, killer, "DEX") + profile.stealth_mod
    ).total
    witnesses = _gather_witnesses(
        manager,
        catalogs,
        engine,
        location,
        victim,
        victim_faction,
        killer,
        stealth_score,
        profile,
    )
    manager.store_dice(engine)

    crime = CrimeRecord(
        id=_new_id("crime"),
        killer=killer,
        victim=victim,
        location=location,
        method=profile.name,
        t=state.time,
        witnesses=witnesses,
        body_disposed=False,
        body_found=True,
        evidence=_evidence_for(profile, body_found=True),
    )
    state.crimes[crime.id] = crime
    schedule_investigation(manager, crime)
    return adjudicate(manager, catalogs, crime.id, adjudicator=adjudicator)


# ---------------------------------------------------------------------------
# Body disposal
# ---------------------------------------------------------------------------


def dispose_body(
    manager: StateManager,
    catalogs: Catalogs,
    crime_id: str,
    *,
    stat: str = "DEX",
    dc: int | None = None,
) -> bool:
    """Try to hide the body. On success no physical evidence can be found."""
    state = manager.state
    crime = state.crimes.get(crime_id)
    if crime is None:
        raise CrimeError(f"unknown crime: {crime_id}")
    if crime.body_disposed:
        return True

    location_def = catalogs.locations.get(crime.location)
    difficulty = dc if dc is not None else 12 + (location_def.danger_level if location_def else 0)

    engine = manager.dice_engine()
    roll = engine.roll("d20", modifier=state.player.stat(stat), dc=difficulty)
    manager.store_dice(engine)
    success = roll.outcome in ("success", "crit_success")

    if success:
        crime.body_disposed = True
        crime.body_found = False
        crime.evidence = []
        for investigation in state.investigations.values():
            if investigation.crime_id == crime_id and not investigation.resolved:
                investigation.resolved = True
                investigation.result = "body_hidden"
        tm.advance(state, 30)
    else:
        tm.advance(state, 15)

    manager.append_event(
        type="crime",
        t=state.time,
        actor=crime.killer,
        input=f"dispose body {crime_id}",
        intent={"action_type": "dispose_body", "crime": crime_id, "stat": stat},
        roll=roll.model_dump(mode="json"),
        effects=[{"kind": "body_disposed", "target": crime_id, "value": success}],
    )
    if success:
        # With the body gone, prior suspicion may dissolve entirely.
        adjudicate(manager, catalogs, crime_id)
    return success


# ---------------------------------------------------------------------------
# Delayed investigation
# ---------------------------------------------------------------------------


def schedule_investigation(manager: StateManager, crime: CrimeRecord) -> Investigation | None:
    if not crime.body_found:
        return None
    state = manager.state
    due = tm.from_minutes(
        tm.absolute_minutes(state.time) + INVESTIGATION_DELAY_MINUTES
    )
    investigation = Investigation(
        id=_new_id("inv"),
        crime_id=crime.id,
        location=crime.location,
        due_time=due,
    )
    state.investigations[investigation.id] = investigation
    return investigation


def run_due_investigations(
    manager: StateManager,
    catalogs: Catalogs,
    *,
    adjudicator: Adjudicator | None = None,
) -> list[Investigation]:
    """Resolve every investigation whose due time has passed."""
    state = manager.state
    now = tm.absolute_minutes(state.time)
    resolved: list[Investigation] = []

    for investigation in list(state.investigations.values()):
        if investigation.resolved or tm.absolute_minutes(investigation.due_time) > now:
            continue
        crime = state.crimes.get(investigation.crime_id)
        if crime is None:
            investigation.resolved = True
            investigation.result = "crime_missing"
            resolved.append(investigation)
            continue
        if not crime.body_found:
            investigation.resolved = True
            investigation.result = "body_not_found"
            resolved.append(investigation)
            continue

        strength = sum(int(e.get("strength", 0)) for e in crime.evidence)
        dc = max(8, 14 - strength)
        engine = manager.dice_engine()
        roll = engine.roll("d20", modifier=2, dc=dc)
        manager.store_dice(engine)
        investigation.resolved = True

        if roll.outcome in ("success", "crit_success"):
            investigation.result = "leads_found"
            adjudicate(manager, catalogs, crime.id, adjudicator=adjudicator, investigation_success=True)
        else:
            investigation.result = "dead_end"
        resolved.append(investigation)

    return resolved


def due_investigations(state) -> list[Investigation]:  # type: ignore[no-untyped-def]
    now = tm.absolute_minutes(state.time)
    return [
        investigation
        for investigation in state.investigations.values()
        if not investigation.resolved and tm.absolute_minutes(investigation.due_time) <= now
    ]
