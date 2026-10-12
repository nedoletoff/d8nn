"""Quests: stages, completion and rewards.

A quest's static definition lives in ``quests_catalog.json``; its runtime stage
lives in :class:`~engine.state_manager.QuestState`. Closing a quest grants
rewards through the inventory/economy rules and logs a ``quest_update`` event.
"""

from __future__ import annotations

from dataclasses import dataclass

from engine import inventory_engine as inv
from engine.catalogs import Catalogs, QuestDef
from engine.state_manager import QuestState, StateManager


class QuestError(ValueError):
    """Raised on invalid quest operations."""


@dataclass
class QuestResult:
    quest_id: str
    stage: str
    status: str
    rewards: dict[str, object]


def start_quest(manager: StateManager, catalogs: Catalogs, quest_id: str) -> QuestState:
    definition = catalogs.quest(quest_id)
    state = manager.state
    first_stage = definition.stages[0] if definition.stages else "start"
    quest = QuestState(id=quest_id, stage=first_stage, status="active")
    state.quests[quest_id] = quest
    manager.append_event(
        type="quest_update",
        t=state.time,
        actor="player",
        input=f"quest {quest_id}",
        intent={"action_type": "quest_start", "quest": quest_id},
        effects=[{"kind": "quest_stage", "target": quest_id, "value": first_stage}],
    )
    return quest


def advance_stage(
    manager: StateManager, catalogs: Catalogs, quest_id: str, stage: str
) -> QuestState:
    definition = catalogs.quest(quest_id)
    if stage not in definition.stages:
        raise QuestError(f"unknown stage {stage!r} for quest {quest_id!r}")
    quest = manager.state.quests.get(quest_id)
    if quest is None:
        raise QuestError(f"quest {quest_id!r} is not active")
    if quest.status != "active":
        raise QuestError(f"quest {quest_id!r} is already {quest.status}")
    quest.stage = stage
    manager.append_event(
        type="quest_update",
        t=manager.state.time,
        actor="player",
        input=f"quest {quest_id} -> {stage}",
        intent={"action_type": "quest_stage", "quest": quest_id, "stage": stage},
        effects=[{"kind": "quest_stage", "target": quest_id, "value": stage}],
    )
    return quest


def complete_quest(manager: StateManager, catalogs: Catalogs, quest_id: str) -> QuestResult:
    """Mark a quest complete and grant its rewards to the player."""
    definition = catalogs.quest(quest_id)
    quest = manager.state.quests.get(quest_id)
    if quest is None:
        raise QuestError(f"quest {quest_id!r} is not active")
    if quest.status != "active":
        raise QuestError(f"quest {quest_id!r} is already {quest.status}")

    quest.status = "completed"
    quest.stage = definition.stages[-1] if definition.stages else quest.stage

    rewards = _grant_rewards(manager, catalogs, definition)
    effects: list[dict[str, object]] = [
        {"kind": "quest_stage", "target": quest_id, "value": "completed"}
    ]
    effects.extend(rewards["effects"])  # type: ignore[arg-type]

    manager.append_event(
        type="quest_update",
        t=manager.state.time,
        actor="player",
        input=f"quest {quest_id} completed",
        intent={"action_type": "quest_complete", "quest": quest_id},
        effects=effects,
    )
    return QuestResult(
        quest_id=quest_id, stage=quest.stage, status=quest.status, rewards=rewards
    )


def _grant_rewards(
    manager: StateManager, catalogs: Catalogs, definition: QuestDef
) -> dict[str, object]:
    state = manager.state
    player = state.player
    reward = definition.rewards
    effects: list[dict[str, object]] = []

    if reward.xp:
        player.xp += reward.xp
        effects.append({"kind": "xp_delta", "target": "player", "value": reward.xp})
    if reward.currency.to_copper():
        player.currency = player.currency.plus(
            gold=reward.currency.gold,
            silver=reward.currency.silver,
            copper=reward.currency.copper,
        )
        effects.append(
            {"kind": "currency_delta", "target": "player", "value": reward.currency.to_copper()}
        )
    for item_id in reward.items:
        instance = inv.add_item(state, catalogs, player, item_id)
        effects.append({"kind": "item_add", "target": "player", "value": instance.instance_id})

    return {
        "xp": reward.xp,
        "currency": reward.currency.model_dump(),
        "items": list(reward.items),
        "effects": effects,
    }


def fail_quest(manager: StateManager, quest_id: str) -> QuestState:
    quest = manager.state.quests.get(quest_id)
    if quest is None:
        raise QuestError(f"quest {quest_id!r} is not active")
    quest.status = "failed"
    manager.append_event(
        type="quest_update",
        t=manager.state.time,
        actor="player",
        intent={"action_type": "quest_fail", "quest": quest_id},
        effects=[{"kind": "quest_stage", "target": quest_id, "value": "failed"}],
    )
    return quest
