"""Trading with NPCs: stock, prices, buy/sell and text enquiries.

A merchant's stock is the ``stock`` list of item ids on its catalog entry.
Prices come from the item's value. Buyers pay full copper value, sellers get
half. Everything is deterministic and logged to the canon.
"""

from __future__ import annotations

from dataclasses import dataclass

from engine import inventory_engine as inv
from engine.catalogs import Catalogs, ItemDef
from engine.state_manager import Currency, StateManager

#: fraction of value returned when the player sells
SELL_RATE = 0.5


class TradeError(ValueError):
    """Raised on invalid trade operations."""


@dataclass
class StockLine:
    item_id: str
    name: str
    price: int


def _total_copper(currency: Currency) -> int:
    return currency.to_copper()


def is_merchant(catalogs: Catalogs, npc_id: str) -> bool:
    definition = catalogs.npcs.get(npc_id)
    return bool(definition and definition.stock)


def price_of(catalogs: Catalogs, item_id: str) -> int:
    item: ItemDef = catalogs.item(item_id)
    return max(1, item.value.to_copper())


def stock(catalogs: Catalogs, npc_id: str) -> list[StockLine]:
    definition = catalogs.npcs.get(npc_id)
    if definition is None:
        return []
    lines: list[StockLine] = []
    for item_id in definition.stock:
        if item_id in catalogs.items:
            lines.append(StockLine(item_id, catalogs.items[item_id].name, price_of(catalogs, item_id)))
    return lines


def _match(candidates: list[tuple[str, str]], query: str) -> str | None:
    needle = query.strip().lower()
    if not needle:
        return None
    for item_id, name in candidates:
        if item_id.lower() == needle or name.lower() == needle:
            return item_id
    for item_id, name in candidates:
        if needle in name.lower() or needle in item_id.lower():
            return item_id
    return None


def find_in_stock(catalogs: Catalogs, npc_id: str, query: str) -> str | None:
    candidates = [(line.item_id, line.name) for line in stock(catalogs, npc_id)]
    return _match(candidates, query)


def answer_about(catalogs: Catalogs, npc_id: str, query: str) -> str:
    """Deterministic reply to "do you have X?" — used offline and as a base."""
    definition = catalogs.npcs.get(npc_id)
    if definition is None:
        return "Тут не с кем торговать."
    if not definition.stock:
        return f"{definition.name} не торгует."
    match = find_in_stock(catalogs, npc_id, query)
    if match:
        price = price_of(catalogs, match)
        return f"Да, есть: {catalogs.item(match).name} — {price} меди."
    lines = ", ".join(f"{line.name} ({line.price})" for line in stock(catalogs, npc_id))
    return f"Такого у него нет. Есть: {lines}."


def buy(manager: StateManager, catalogs: Catalogs, npc_id: str, query: str) -> str:
    item_id = find_in_stock(catalogs, npc_id, query)
    if item_id is None:
        return answer_about(catalogs, npc_id, query)

    state = manager.state
    player = state.player
    cost = price_of(catalogs, item_id)
    if _total_copper(player.currency) < cost:
        return f"Не хватает денег: нужно {cost} меди."

    player.currency = Currency.from_copper(_total_copper(player.currency) - cost)
    instance = inv.add_item(state, catalogs, player, item_id)
    manager.append_event(
        type="item_use",
        t=state.time,
        actor="player",
        input=f"buy {item_id}",
        intent={"action_type": "trade", "action": "buy", "npc": npc_id, "item": item_id},
        effects=[
            {"kind": "currency_delta", "target": "player", "value": -cost},
            {"kind": "item_add", "target": "player", "value": instance.instance_id},
        ],
    )
    return f"Куплено: {catalogs.item(item_id).name} за {cost} меди."


def sell(manager: StateManager, catalogs: Catalogs, npc_id: str, query: str) -> str:
    state = manager.state
    player = state.player
    candidates: list[tuple[str, str]] = []
    for instance_id in player.inventory:
        instance = state.items.get(instance_id)
        if instance is not None:
            candidates.append((instance_id, catalogs.item(instance.item_id).name))
    instance_id = _match(candidates, query)
    if instance_id is None:
        return "У тебя нет такого предмета."

    instance = state.items[instance_id]
    item = catalogs.item(instance.item_id)
    gain = max(1, int(price_of(catalogs, item.id) * SELL_RATE))
    inv.remove_item(state, player, instance_id, quantity=1)
    player.currency = Currency.from_copper(_total_copper(player.currency) + gain)
    manager.append_event(
        type="item_use",
        t=state.time,
        actor="player",
        input=f"sell {item.id}",
        intent={"action_type": "trade", "action": "sell", "npc": npc_id, "item": item.id},
        effects=[
            {"kind": "item_remove", "target": "player", "value": instance_id},
            {"kind": "currency_delta", "target": "player", "value": gain},
        ],
    )
    return f"Продано: {item.name} за {gain} меди."


def stock_lines(catalogs: Catalogs, npc_id: str) -> list[str]:
    definition = catalogs.npcs.get(npc_id)
    title = definition.name if definition else npc_id
    lines = [f"{title} предлагает:"]
    for line in stock(catalogs, npc_id):
        lines.append(f"  {line.name} — {line.price} меди")
    if len(lines) == 1:
        lines.append("  (пусто)")
    return lines
