"""Combat: initiative, attacks, damage, criticals and statuses.

All rolls go through :class:`~engine.dice_engine.DiceEngine`. Results are
applied to the game state by this module and recorded as ``combat`` canon
events. Nothing here is delegated to the LLM.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from engine import inventory_engine as inv
from engine.catalogs import Catalogs, ItemDef
from engine.dice_engine import Advantage, DiceEngine, DiceSpec, RollResult
from engine.state_manager import Character, CombatState, NPCState, StateManager

STATUS_POISONED = "poisoned"
STATUS_BURNING = "burning"
STATUS_STUNNED = "stunned"
STATUS_BLESSED = "blessed"

TICK_DAMAGE: dict[str, str] = {
    STATUS_POISONED: "1",
    STATUS_BURNING: "1d4",
}

UNARMED = ItemDef(id="unarmed", name="Кулаки", type="weapon", damage_dice="1d2", damage_stat="STR")


class CombatError(ValueError):
    """Raised on invalid combat operations."""


Actor = Character | NPCState


@dataclass
class AttackResult:
    attacker: str
    target: str
    hit: bool
    crit: bool
    roll: RollResult
    damage: int
    target_hp: int
    dead: bool
    effects: list[dict[str, object]] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Actor resolution
# ---------------------------------------------------------------------------


def actor_ref(ref: str) -> str:
    """Normalise an NPC reference to the ``npc:<id>`` canon form."""
    return ref


def resolve_actor(state, ref: str) -> Actor:  # type: ignore[no-untyped-def]
    if ref == "player":
        return state.player
    character = state.character(ref)
    if character is not None:
        return character
    key = ref[4:] if ref.startswith("npc:") else ref
    if key in state.npcs:
        return state.npcs[key]
    raise CombatError(f"unknown combatant: {ref}")


def _canon_ref(state, ref: str) -> str:  # type: ignore[no-untyped-def]
    if ref == "player":
        return "player"
    if state.character(ref) is not None:
        return ref
    key = ref[4:] if ref.startswith("npc:") else ref
    if key in state.npcs:
        return f"npc:{key}"
    return ref


# ---------------------------------------------------------------------------
# Initiative & turns
# ---------------------------------------------------------------------------


def roll_initiative(engine: DiceEngine, dex_modifier: int) -> int:
    return engine.roll(DiceSpec(1, 20), modifier=dex_modifier).total


def start_combat(manager: StateManager, refs: list[str]) -> CombatState:
    """Roll initiative for every combatant and open combat."""
    if len(refs) < 2:
        raise CombatError("combat needs at least two combatants")
    state = manager.state
    engine = manager.dice_engine()
    scored: list[tuple[int, str]] = []
    for ref in refs:
        actor = resolve_actor(state, ref)
        scored.append((roll_initiative(engine, actor.stat("DEX")), _canon_ref(state, ref)))
    scored.sort(key=lambda pair: (-pair[0], pair[1]))
    manager.store_dice(engine)

    state.combat = CombatState(
        active=True,
        round=1,
        turn_index=0,
        order=[ref for _, ref in scored],
        encounter_id=state.flags.get("encounter_id"),  # type: ignore[arg-type]
    )
    manager.append_event(
        type="combat",
        t=state.time,
        actor="world",
        input="initiative",
        intent={"action_type": "combat", "action": "start"},
        roll={"order": [{"ref": ref, "initiative": score} for score, ref in scored]},
        effects=[{"kind": "combat_start", "target": "world", "value": None}],
    )
    return state.combat


def current_combatant(state) -> str | None:  # type: ignore[no-untyped-def]
    if state.combat is None or not state.combat.order:
        return None
    return state.combat.order[state.combat.turn_index % len(state.combat.order)]


def next_turn(manager: StateManager) -> str | None:
    combat = manager.state.combat
    if combat is None or not combat.active:
        return None
    combat.turn_index += 1
    if combat.turn_index >= len(combat.order):
        combat.turn_index = 0
        combat.round += 1
    return current_combatant(manager.state)


def end_combat(manager: StateManager) -> None:
    if manager.state.combat is not None:
        manager.state.combat.active = False


# ---------------------------------------------------------------------------
# Statuses
# ---------------------------------------------------------------------------


def tick_statuses(manager: StateManager, ref: str) -> list[dict[str, object]]:
    """Apply start-of-turn status damage for one actor."""
    state = manager.state
    actor = resolve_actor(state, ref)
    if not is_alive(actor):
        return []
    effects: list[dict[str, object]] = []
    engine = manager.dice_engine()
    for status in list(actor.statuses):
        dice = TICK_DAMAGE.get(status)
        if not dice:
            continue
        result = engine.roll(dice)
        actor.hp = max(0, actor.hp - result.total)
        effect = {"kind": "hp_delta", "target": _canon_ref(state, ref), "value": -result.total}
        effects.append(effect)
    manager.store_dice(engine)
    if effects:
        manager.append_event(
            type="combat",
            t=state.time,
            actor=_canon_ref(state, ref),
            intent={"action_type": "status_tick"},
            effects=effects,
        )
    return effects


def is_stunned(actor: Actor) -> bool:
    return STATUS_STUNNED in actor.statuses


def is_alive(actor: Actor) -> bool:
    return actor.alive if isinstance(actor, Character) else actor.status == "alive"


# ---------------------------------------------------------------------------
# Attacks
# ---------------------------------------------------------------------------


def _weapon_for(state, catalogs: Catalogs, attacker_ref: str) -> ItemDef:  # type: ignore[no-untyped-def]
    character = state.character(attacker_ref)
    if character is not None:
        weapon = inv.equipped_def(state, catalogs, character, "weapon")
        if weapon is not None:
            return weapon
    return UNARMED


def _armor_class(state, catalogs: Catalogs, actor: Actor) -> int:  # type: ignore[no-untyped-def]
    base = actor.ac
    if isinstance(actor, Character):
        armor = inv.equipped_def(state, catalogs, actor, "armor")
        if armor is not None:
            base += armor.armor_bonus
    return base


def attack(
    manager: StateManager,
    catalogs: Catalogs,
    attacker_ref: str,
    target_ref: str,
    *,
    advantage: Advantage = "normal",
) -> AttackResult:
    """Resolve a weapon attack: ``d20 + stat vs AC``; crit on a natural 20."""
    state = manager.state
    attacker = resolve_actor(state, attacker_ref)
    target = resolve_actor(state, target_ref)
    if not is_alive(attacker):
        raise CombatError(f"{attacker_ref} cannot act")
    if not is_alive(target):
        raise CombatError(f"{target_ref} is already down")

    weapon = _weapon_for(state, catalogs, attacker_ref)
    stat_name = weapon.damage_stat or "STR"
    stat_mod = attacker.stat(stat_name)
    if stat_name == "DEX":
        character = state.character(attacker_ref)
        if character is not None:
            stat_mod -= inv.dex_penalty(state, catalogs, character)

    ac = _armor_class(state, catalogs, target)
    engine = manager.dice_engine()
    roll = engine.roll(DiceSpec(1, 20), modifier=stat_mod, dc=ac, advantage=advantage)
    crit = roll.natural == 20
    hit = roll.outcome in ("success", "crit_success")

    damage = 0
    if hit:
        dice = weapon.damage_dice or "1d2"
        flat = attacker.stat("STR") if weapon is UNARMED else stat_mod
        damage_roll = engine.roll_damage(dice, modifier=flat, crit=crit)
        damage = max(0, damage_roll.total)
    manager.store_dice(engine)

    effects: list[dict[str, object]] = [
        {"kind": "hp_delta", "target": _canon_ref(state, target_ref), "value": -damage}
    ]
    if state.rules.toggles.death_enabled:
        target.hp = max(state.rules.death_threshold, target.hp - damage)
        dead = target.hp <= state.rules.death_threshold
    else:
        target.hp = max(1, target.hp - damage)  # nobody dies in this world
        dead = False
    death_method = "unarmed" if weapon is UNARMED else "weapon"
    if dead:
        effects.extend(
            _handle_death(
                manager, catalogs, killer=attacker_ref, target_ref=target_ref,
                actor=target, method=death_method,
            )
        )

    manager.append_event(
        type="combat",
        t=state.time,
        actor=_canon_ref(state, attacker_ref),
        input=f"attack {weapon.id}",
        intent={
            "action_type": "attack",
            "target": _canon_ref(state, target_ref),
            "weapon": weapon.id,
        },
        roll=roll.model_dump(mode="json"),
        effects=effects,
    )
    return AttackResult(
        attacker=_canon_ref(state, attacker_ref),
        target=_canon_ref(state, target_ref),
        hit=hit,
        crit=crit,
        roll=roll,
        damage=damage,
        target_hp=target.hp,
        dead=dead,
        effects=effects,
    )


def _handle_death(
    manager: StateManager,
    catalogs: Catalogs,
    *,
    killer: str,
    target_ref: str,
    actor: Actor,
    method: str = "weapon",
) -> list[dict[str, object]]:
    """Mark a combatant dead. A killed NPC's crime is recorded separately.

    Note: the killer's faction reputation is **not** touched here. That is
    decided by ``crime_engine`` only once the world learns who did it.
    """
    state = manager.state
    effects: list[dict[str, object]] = [
        {"kind": "death", "target": _canon_ref(state, target_ref), "value": None}
    ]
    if target_ref == "player" or actor is state.player:
        state.game_over = True
        state.player.alive = False
        return effects
    if isinstance(actor, Character):
        actor.alive = False
        return effects

    assert isinstance(actor, NPCState)
    actor.status = "dead"
    actor.permanently_dead = True
    actor.rep = -100
    actor.disposition = "hostile"
    actor.hp = 0
    actor.statuses = []

    from engine import crime_engine  # local import avoids a cycle

    crime_engine.record_kill(
        manager, catalogs, killer=killer, victim=actor.id, method=method
    )
    effects.append({"kind": "rep_delta", "target": actor.id, "value": -100})
    return effects


def cast_spell(
    manager: StateManager,
    catalogs: Catalogs,
    caster_ref: str,
    spell_id: str,
    target_ref: str,
) -> AttackResult:
    """Cast a damaging spell: spends MP, ``d20 + INT vs AC``, no crit fumble."""
    state = manager.state
    if not state.rules.toggles.magic_enabled:
        raise CombatError("в этом мире магия не действует")
    caster = resolve_actor(state, caster_ref)
    target = resolve_actor(state, target_ref)
    if not isinstance(caster, Character):
        raise CombatError("only characters can cast spells")
    spell = catalogs.spell(spell_id)
    if caster.mp < spell.mp_cost:
        raise CombatError(f"not enough MP for {spell.name}")
    caster.mp -= spell.mp_cost

    ac = _armor_class(state, catalogs, target)
    engine = manager.dice_engine()
    roll = engine.roll(DiceSpec(1, 20), modifier=caster.stat("INT"), dc=ac)
    crit = roll.natural == 20
    hit = roll.outcome in ("success", "crit_success")
    damage = 0
    if hit and spell.damage_dice:
        damage = max(0, engine.roll_damage(spell.damage_dice, modifier=caster.stat("INT"), crit=crit).total)
    manager.store_dice(engine)

    if state.rules.toggles.death_enabled:
        target.hp = max(state.rules.death_threshold, target.hp - damage)
        dead = target.hp <= state.rules.death_threshold
    else:
        target.hp = max(1, target.hp - damage)
        dead = False
    effects: list[dict[str, object]] = [
        {"kind": "mp_delta", "target": _canon_ref(state, caster_ref), "value": -spell.mp_cost},
        {"kind": "hp_delta", "target": _canon_ref(state, target_ref), "value": -damage},
    ]
    if dead:
        effects.extend(
            _handle_death(
                manager, catalogs, killer=caster_ref, target_ref=target_ref,
                actor=target, method="spell",
            )
        )

    manager.append_event(
        type="combat",
        t=state.time,
        actor=_canon_ref(state, caster_ref),
        input=f"cast {spell_id}",
        intent={"action_type": "cast", "spell": spell_id, "target": _canon_ref(state, target_ref)},
        roll=roll.model_dump(mode="json"),
        effects=effects,
    )
    return AttackResult(
        attacker=_canon_ref(state, caster_ref),
        target=_canon_ref(state, target_ref),
        hit=hit,
        crit=crit,
        roll=roll,
        damage=damage,
        target_hp=target.hp,
        dead=dead,
        effects=effects,
    )
