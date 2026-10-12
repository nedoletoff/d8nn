"""Turn orchestrator: the pipeline from spec section 7.

    input -> intent -> deterministic effect -> canon event -> narration

The LLM is only asked to (1) classify intent and (2) describe the result the
engine already produced. All mechanics are applied by the rule engines and
recorded in the canonical log. If the LLM is offline every step degrades to a
deterministic fallback.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from engine import combat_engine as combat
from engine import inventory_engine as inv
from engine import npc_engine as npc
from engine import time_engine as tm
from engine import world_map as world
from engine.canon_log import CanonEvent
from engine.catalogs import Catalogs, NpcDef
from engine.dice_engine import RollResult
from engine.intent_parser import Intent, parse_intent
from engine.narrator import describe_event
from engine.state_manager import StateManager


@dataclass
class TurnResult:
    input: str
    action: str
    narration: str
    event: CanonEvent | None = None
    roll: RollResult | None = None
    message: str | None = None
    effects: list[dict[str, Any]] = field(default_factory=list)
    ok: bool = True


class GameLoop:
    """Applies one player turn at a time against a :class:`StateManager`."""

    def __init__(self, manager: StateManager, catalogs: Catalogs, client: Any | None = None) -> None:
        self.manager = manager
        self.catalogs = catalogs
        self.client = client

    # -- helpers -------------------------------------------------------------

    def sanitized_state(self, *, intent_request: str | None = None) -> dict[str, Any]:
        from engine import mandate as mandate_mod

        snapshot = self.manager.read_snapshot()
        state = self.manager.state
        view = state.sanitized_view(
            locations=self.catalogs.locations_for_llm(),
            npc_names=self.catalogs.npc_names(),
            canon_facts=snapshot.get("world_facts", []),
            recent_events=self.manager.recent_window(15),
            intent_request=intent_request,
            map_grid=world.grid_ascii(state, self.catalogs, reveal=False),
        )
        view["world_lore"] = state.flags.get("world_lore", {})
        view["narrator_constraints"] = mandate_mod.narrator_constraints(state.rules)
        return view

    def _last_event(self) -> CanonEvent | None:
        events = self.manager.canon.events
        return events[-1] if events else None

    def resolve_npc(self, reference: str | None) -> str | None:
        if not reference:
            return None
        needle = reference.strip().lower()
        known = set(self.manager.state.npcs) | set(self.catalogs.npcs)
        for npc_id in known:
            definition: NpcDef | None = self.catalogs.npcs.get(npc_id)
            name = definition.name.lower() if definition else ""
            if npc_id.lower() == needle or name == needle or needle in name or needle in npc_id.lower():
                return npc_id
        return None

    def resolve_location(self, reference: str | None) -> str | None:
        if not reference:
            return None
        needle = reference.strip().lower()
        for location_id, definition in self.catalogs.locations.items():
            if location_id.lower() == needle or needle in definition.name.lower():
                return location_id
        return None

    def resolve_item(self, reference: str | None) -> str | None:
        if not reference:
            return None
        needle = reference.strip().lower()
        player = self.manager.state.player
        candidates = list(player.inventory) + [
            i for i in player.equipped.values() if i
        ]
        for instance_id in candidates:
            instance = self.manager.state.items.get(instance_id)
            if instance is None:
                continue
            item = self.catalogs.item(instance.item_id)
            if instance.item_id.lower() == needle or needle in item.name.lower():
                return instance_id
        return None

    def _append(self, **kwargs: Any) -> CanonEvent:
        state = self.manager.state
        kwargs.setdefault("t", state.time)
        return self.manager.append_event(**kwargs)

    # -- pipeline ------------------------------------------------------------

    async def process(self, text: str) -> TurnResult:
        intent = await parse_intent(self.client, text, self.sanitized_state(intent_request=text))
        handler = getattr(self, f"_do_{intent.action_type}", self._do_freeform)
        result = handler(intent, text)

        event = result.get("event") or self._last_event()
        narration = await describe_event(
            self.client,
            event.model_dump(mode="json") if event is not None else {"type": "narration", "input": text},
            self.sanitized_state(),
        )
        if result.get("message"):
            narration = f"{result['message']}\n{narration}".strip()
        return TurnResult(
            input=text,
            action=intent.action_type,
            narration=narration,
            event=event,
            roll=result.get("roll"),
            message=result.get("message"),
            effects=list(event.effects) if event is not None else [],
            ok=result.get("ok", True),
        )

    # -- action handlers -----------------------------------------------------

    def _do_check(self, intent: Intent, text: str) -> dict[str, Any]:
        player = self.manager.state.player
        stat = (intent.stat or "DEX").upper()
        dc = intent.dc if intent.dc is not None else 12
        engine = self.manager.dice_engine()
        roll = engine.roll_check(stat_modifier=player.stat(stat), dc=dc)
        self.manager.store_dice(engine)
        event = self._append(
            type="dice_check",
            actor="player",
            input=text,
            intent={**intent.model_dump(), "stat": stat, "dc": dc},
            roll=roll.model_dump(mode="json"),
        )
        return {"event": event, "roll": roll}

    def _do_attack(self, intent: Intent, text: str) -> dict[str, Any]:
        npc_id = self.resolve_npc(intent.target)
        if npc_id is None:
            return {"ok": False, "message": "Цели не видно.", "event": self._append(
                type="narration", actor="player", input=text, narration=None)}
        result = combat.attack(self.manager, self.catalogs, "player", f"npc:{npc_id}")
        return {"event": self._last_event(), "roll": result.roll}

    def _do_cast(self, intent: Intent, text: str) -> dict[str, Any]:
        npc_id = self.resolve_npc(intent.target)
        spell = intent.spell or "spark"
        if npc_id is None:
            return {"ok": False, "message": "Заклинание уходит в пустоту.", "event": self._append(
                type="narration", actor="player", input=text)}
        result = combat.cast_spell(self.manager, self.catalogs, "player", spell, f"npc:{npc_id}")
        return {"event": self._last_event(), "roll": result.roll}

    def _do_travel(self, intent: Intent, text: str) -> dict[str, Any]:
        destination = self.resolve_location(intent.destination or intent.target)
        if destination is None:
            return {"ok": False, "message": "Ты не знаешь дороги туда.", "event": self._append(
                type="narration", actor="player", input=text)}
        try:
            result = world.travel(self.manager, self.catalogs, destination)
        except world.WorldError as exc:
            return {"ok": False, "message": f"Путь закрыт: {exc}", "event": self._append(
                type="narration", actor="player", input=text)}
        return {"event": self._last_event(), "message": (
            "По дороге что-то движется в тумане." if result.encounter else None
        )}

    def _do_move(self, intent: Intent, text: str) -> dict[str, Any]:
        return self._do_travel(intent, text)

    def _do_talk(self, intent: Intent, text: str) -> dict[str, Any]:
        npc_id = self.resolve_npc(intent.npc or intent.target)
        if npc_id is None:
            return {"ok": False, "message": "Здесь не с кем говорить.", "event": self._append(
                type="narration", actor="player", input=text)}
        try:
            result = npc.social_check(
                self.manager, self.catalogs, npc_id,
                intent=intent.reason or "ask", stat=(intent.stat or "CHA"), dc=intent.dc or 12,
            )
        except npc.NpcError as exc:
            return {"ok": False, "message": str(exc), "event": self._append(
                type="narration", actor="player", input=text)}
        return {"event": self._last_event(), "roll": result.roll}

    def _do_say(self, intent: Intent, text: str) -> dict[str, Any]:
        npc_id = self.resolve_npc(intent.npc or intent.target)
        event = self._append(
            type="dialogue",
            actor=f"npc:{npc_id}" if npc_id else "player",
            input=text,
            intent={**intent.model_dump(), "action": "say"},
        )
        return {"event": event}

    def _do_use(self, intent: Intent, text: str) -> dict[str, Any]:
        instance_id = self.resolve_item(intent.item or intent.target)
        if instance_id is None:
            return {"ok": False, "message": "Такого предмета нет в сумке.", "event": self._append(
                type="narration", actor="player", input=text)}
        try:
            result = inv.use_item(self.manager, self.catalogs, self.manager.state.player, instance_id)
        except inv.InventoryError as exc:
            return {"ok": False, "message": str(exc), "event": self._append(
                type="narration", actor="player", input=text)}
        return {"event": self._last_event(), "effects": result.effects}

    def _do_equip(self, intent: Intent, text: str) -> dict[str, Any]:
        instance_id = self.resolve_item(intent.item or intent.target)
        if instance_id is None:
            return {"ok": False, "message": "Нечего надеть.", "event": self._append(
                type="narration", actor="player", input=text)}
        try:
            inv.equip(self.manager.state, self.catalogs, self.manager.state.player, instance_id)
        except inv.InventoryError as exc:
            return {"ok": False, "message": str(exc), "event": self._append(
                type="narration", actor="player", input=text)}
        event = self._append(
            type="item_use", actor="player", input=text,
            intent={**intent.model_dump(), "action": "equip"},
        )
        return {"event": event}

    def _do_unequip(self, intent: Intent, text: str) -> dict[str, Any]:
        instance_id = self.resolve_item(intent.item or intent.target)
        if instance_id is None or not inv.unequip(self.manager.state, self.manager.state.player, instance_id):
            return {"ok": False, "message": "Это не надето.", "event": self._append(
                type="narration", actor="player", input=text)}
        event = self._append(
            type="item_use", actor="player", input=text,
            intent={**intent.model_dump(), "action": "unequip"},
        )
        return {"event": event}

    def _do_look(self, intent: Intent, text: str) -> dict[str, Any]:
        event = self._append(type="narration", actor="player", input=text,
                             intent={**intent.model_dump(), "action": "look"})
        return {"event": event}

    def _do_rest(self, intent: Intent, text: str) -> dict[str, Any]:
        spent = tm.long_rest(self.manager.state)
        event = self._append(
            type="sleep", actor="player", input=text,
            intent={**intent.model_dump(), "action": "rest", "minutes": spent},
            effects=[{"kind": "rest", "target": "player", "value": spent}],
        )
        return {"event": event}

    def _do_freeform(self, intent: Intent, text: str) -> dict[str, Any]:
        event = self._append(
            type="freeform", actor="player", input=text,
            intent=intent.model_dump(),
        )
        return {"event": event}


# ---------------------------------------------------------------------------
# Export
# ---------------------------------------------------------------------------


def export_canon_markdown(manager: StateManager, catalogs: Catalogs) -> str:
    """Read-only Markdown chronicle of the canonical log."""
    lines = [f"# Хроника — {manager.slot_dir.name}", ""]
    for event in manager.canon.events:
        stamp = f"Д{event.t.day} {event.t.hour:02d}:{event.t.minute:02d}"
        lines.append(f"## {stamp} · {event.type} · {event.actor}")
        if event.input:
            lines.append(f"> {event.input}")
        roll = event.roll or {}
        if roll.get("outcome"):
            lines.append(
                f"**Бросок:** {roll.get('dice', '?')} → {roll.get('total', '?')}"
                f" vs DC {roll.get('dc', '?')} — {roll.get('outcome')}"
            )
        for effect in event.effects:
            lines.append(f"- `{effect.get('kind')}` → {effect.get('target')} = {effect.get('value')}")
        if event.narration:
            lines.append("")
            lines.append(event.narration)
        lines.append("")
    return "\n".join(lines)
