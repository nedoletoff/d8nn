"""Inventory, equipment, weight and encumbrance.

Items are referenced by catalog id; owned copies are :class:`ItemInstance`
records inside the game state. Weight affects a character's DEX when the load
exceeds ``carry_capacity = 10 + STR * 5``.
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass

from engine.catalogs import Catalogs, ItemDef
from engine.rules_engine import Ruleset
from engine.state_manager import Character, GameState, ItemInstance, StateManager

#: item type -> equipment slot
SLOT_BY_TYPE: dict[str, str] = {
    "weapon": "weapon",
    "armor": "armor",
    "spellbook": "focus",
}

#: fallback penalty when no ruleset is supplied
OVERLOAD_DEX_PENALTY = 2


class InventoryError(ValueError):
    """Raised on invalid inventory operations."""


@dataclass
class UseResult:
    character: str
    item_id: str
    effects: list[dict[str, object]]


def carry_capacity(character: Character, rules: Ruleset | None = None) -> int:
    if rules is not None:
        return int(rules.carry_base + character.stat("STR") * rules.carry_per_str)
    return 10 + character.stat("STR") * 5


def total_weight(state: GameState, catalogs: Catalogs, character: Character) -> float:
    def weight_of(instance_id: str) -> float:
        instance = state.items.get(instance_id)
        if instance is None:
            return 0.0
        return catalogs.item(instance.item_id).weight * instance.quantity

    carried = sum(weight_of(instance) for instance in character.inventory)
    equipped = sum(
        weight_of(instance) for instance in character.equipped.values() if instance
    )
    return round(carried + equipped, 2)


def is_overloaded(state: GameState, catalogs: Catalogs, character: Character) -> bool:
    if not state.rules.toggles.encumbrance_enabled:
        return False
    return total_weight(state, catalogs, character) > carry_capacity(character, state.rules)


def dex_penalty(state: GameState, catalogs: Catalogs, character: Character) -> int:
    """Effective DEX penalty from encumbrance (0 when within capacity)."""
    return state.rules.overload_dex_penalty if is_overloaded(state, catalogs, character) else 0


def _new_instance_id(state: GameState) -> str:
    while True:
        candidate = f"itm_{secrets.token_hex(3)}"
        if candidate not in state.items:
            return candidate


def add_item(
    state: GameState,
    catalogs: Catalogs,
    character: Character,
    item_id: str,
    quantity: int = 1,
) -> ItemInstance:
    """Create a new owned instance of ``item_id`` in ``character``'s backpack."""
    catalogs.item(item_id)  # validates existence
    if quantity <= 0:
        raise InventoryError("quantity must be positive")
    instance = ItemInstance(
        instance_id=_new_instance_id(state),
        item_id=item_id,
        quantity=quantity,
    )
    state.items[instance.instance_id] = instance
    character.inventory.append(instance.instance_id)
    return instance


def remove_item(
    state: GameState,
    character: Character,
    instance_id: str,
    quantity: int = 1,
) -> bool:
    """Remove ``quantity`` of an instance. Returns True when fully removed."""
    if instance_id not in character.inventory and instance_id not in character.equipped.values():
        return False
    instance = state.items.get(instance_id)
    if instance is None:
        return False
    instance.quantity -= quantity
    if instance.quantity <= 0:
        _forget_instance(state, character, instance_id)
        return True
    return False


def _forget_instance(state: GameState, character: Character, instance_id: str) -> None:
    state.items.pop(instance_id, None)
    if instance_id in character.inventory:
        character.inventory.remove(instance_id)
    for slot, equipped in list(character.equipped.items()):
        if equipped == instance_id:
            character.equipped[slot] = None


def equipped_def(
    state: GameState, catalogs: Catalogs, character: Character, slot: str
) -> ItemDef | None:
    instance_id = character.equipped.get(slot)
    if not instance_id:
        return None
    instance = state.items.get(instance_id)
    if instance is None:
        return None
    return catalogs.item(instance.item_id)


def equip(state: GameState, catalogs: Catalogs, character: Character, instance_id: str) -> str:
    """Equip an instance; returns the slot it went into."""
    instance = state.items.get(instance_id)
    if instance is None:
        raise InventoryError(f"unknown item instance: {instance_id}")
    item = catalogs.item(instance.item_id)
    slot = SLOT_BY_TYPE.get(item.type)
    if slot is None:
        raise InventoryError(f"item {item.name!r} cannot be equipped")
    if instance_id in character.inventory:
        character.inventory.remove(instance_id)
    character.equipped[slot] = instance_id
    return slot


def unequip(state: GameState, character: Character, instance_id: str) -> bool:
    for slot, equipped in list(character.equipped.items()):
        if equipped == instance_id:
            character.equipped[slot] = None
            if instance_id not in character.inventory:
                character.inventory.append(instance_id)
            return True
    return False


def _apply_effect_to(character: Character, effect: dict[str, object]) -> None:
    kind = str(effect.get("kind"))
    value = effect.get("value")
    if kind == "hp_delta":
        character.hp = max(0, min(character.max_hp, character.hp + int(value or 0)))
    elif kind == "mp_delta":
        character.mp = max(0, min(character.max_mp, character.mp + int(value or 0)))
    elif kind == "status_add" and value:
        status = str(value)
        if status not in character.statuses:
            character.statuses.append(status)
    elif kind == "status_remove" and value:
        status = str(value)
        if status in character.statuses:
            character.statuses.remove(status)


def use_item(
    manager: StateManager,
    catalogs: Catalogs,
    character: Character,
    instance_id: str,
) -> UseResult:
    """Consume a consumable, apply its effects and log an ``item_use`` event."""
    instance = manager.state.items.get(instance_id)
    if instance is None:
        raise InventoryError(f"unknown item instance: {instance_id}")
    item = catalogs.item(instance.item_id)
    if item.type != "consumable":
        raise InventoryError(f"item {item.name!r} is not consumable")

    effects: list[dict[str, object]] = []
    for effect in item.effects:
        payload = {"kind": effect.kind, "target": character.id, "value": effect.value}
        _apply_effect_to(character, payload)
        effects.append(payload)

    remove_item(manager.state, character, instance_id, quantity=1)

    manager.append_event(
        type="item_use",
        t=manager.state.time,
        actor=character.id,
        input=f"use {item.id}",
        intent={"action_type": "item_use", "item_id": item.id},
        effects=effects,
        narration=None,
    )
    return UseResult(character=character.id, item_id=item.id, effects=effects)
