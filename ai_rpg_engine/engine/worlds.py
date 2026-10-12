"""World prototypes (packs) and fresh-game construction.

A world lives in ``data/worlds/<id>/`` and may override any of the base JSON
catalogs (items, spells, npcs, quests, world graph) plus a ``world.json`` meta
file. The base ``data/`` catalogs double as the built-in default world.

``build_state`` turns a world into a fresh :class:`GameState`: player, party,
starting quests, rules and a fogged map.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from engine import rules_engine
from engine.canon_log import TimeStamp
from engine.catalogs import Catalogs, build_catalogs
from engine.state_manager import (
    Character,
    Currency,
    GameState,
    ItemInstance,
    LocationState,
    QuestState,
    WorldState,
)

DEFAULT_WORLD_ID = "black_marsh"
WORLDS_DIRNAME = "worlds"


class WorldMeta(BaseModel):
    id: str
    name: str
    subtitle: str = ""
    tone: str = ""
    description: str = ""
    #: one-line hook; the concrete adventure is improvised by the narrator
    premise: str = ""
    #: what era / technology level this world sits in
    era: str = ""
    #: how magic works here (or that it does not)
    magic: str = ""
    #: peoples, factions, notable kinds of folk
    peoples: list[str] = Field(default_factory=list)
    #: world laws (physics / morals) handed to the narrator
    laws: list[str] = Field(default_factory=list)
    emoji: str = "\U0001f30d"
    start: str = ""
    rules: dict[str, Any] = Field(default_factory=dict)
    player: dict[str, Any] = Field(default_factory=dict)
    party: list[dict[str, Any]] = Field(default_factory=list)
    starting_quests: list[str] = Field(default_factory=list)
    starting_items: list[str] = Field(default_factory=list)


@dataclass
class World:
    meta: WorldMeta
    catalogs: Catalogs
    overlay_dir: Path | None


DEFAULT_WORLD = WorldMeta(
    id=DEFAULT_WORLD_ID,
    name="Топь Забытых Огней",
    subtitle="Болотный фронтир",
    tone="мрачное болотное фэнтези; гниль, туман и сделки без свидетелей",
    description="Край на сваях посреди бесконечной топи: деревня, топь, пещеры и канатный мост.",
    premise="Ты пришёл в край, где каждый огонь кем-то оплачен. Что ты здесь ищешь — реши сам.",
    era="позднее средневековье, порох уже есть, но редко",
    magic="магия редкая и дорогая: знаки, травы и кровь; за неё платят телом",
    peoples=["люди болотных деревень", "охотники за головами", "болотные твари"],
    laws=["Свет костра приманивает не только людей"],
    emoji="\U0001f32b\ufe0f",
    start="black_marsh_sector_3",
)


def worlds_dir(data_dir: Path) -> Path:
    return Path(data_dir) / WORLDS_DIRNAME


@lru_cache(maxsize=None)
def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def list_worlds(data_dir: Path) -> list[WorldMeta]:
    """All available worlds: the built-in default plus any ``data/worlds/*``."""
    data_dir = Path(data_dir)
    worlds: list[WorldMeta] = [DEFAULT_WORLD]
    root = worlds_dir(data_dir)
    if root.exists():
        for child in sorted(root.iterdir()):
            meta_file = child / "world.json"
            if child.is_dir() and meta_file.exists():
                try:
                    worlds.append(WorldMeta(**_read_json(meta_file)))
                except Exception:  # pragma: no cover - skip a malformed pack
                    continue
    return worlds


def load_world(world_id: str | None, data_dir: Path) -> World:
    """Load a world pack (its meta + merged catalogs)."""
    data_dir = Path(data_dir)
    if not world_id or world_id == DEFAULT_WORLD_ID:
        return World(meta=DEFAULT_WORLD, catalogs=build_catalogs(data_dir), overlay_dir=None)

    overlay = worlds_dir(data_dir) / world_id
    meta_file = overlay / "world.json"
    if not meta_file.exists():
        return load_world(DEFAULT_WORLD_ID, data_dir)

    meta = WorldMeta(**_read_json(meta_file))
    catalogs = build_catalogs(data_dir, overlay)
    return World(meta=meta, catalogs=catalogs, overlay_dir=overlay)


# ---------------------------------------------------------------------------
# Fresh state
# ---------------------------------------------------------------------------


def _character_from(template: dict[str, Any], *, kind: str) -> Character:
    return Character(
        id=str(template.get("id", kind)),
        name=str(template.get("name", kind)),
        kind=kind,  # type: ignore[arg-type]
        level=int(template.get("level", 1)),
        hp=int(template.get("hp", 10)),
        max_hp=int(template.get("hp", 10)),
        mp=int(template.get("mp", 0)),
        max_mp=int(template.get("mp", 0)),
        ac=int(template.get("ac", 10)),
        stats={k: int(v) for k, v in (template.get("stats") or {}).items()},
        currency=Currency(**(template.get("currency") or {})),
    )


def build_state(world: World, data_dir: Path) -> GameState:
    """Construct a fresh :class:`GameState` for ``world``."""
    del data_dir  # catalogs already resolved in the World
    meta = world.meta
    catalogs = world.catalogs

    ruleset = rules_engine.Ruleset()
    for key, value in (meta.rules or {}).items():
        if key == "laws" and isinstance(value, list):
            for law in value:
                rules_engine.add_law(ruleset, str(law))
            continue
        if key == "capabilities" and isinstance(value, dict):
            for cap_key, cap_value in value.items():
                if hasattr(ruleset.capabilities, cap_key):
                    setattr(ruleset.capabilities, cap_key, cap_value)
            continue
        if key == "toggles" and isinstance(value, dict):
            for toggle_key, toggle_value in value.items():
                if hasattr(ruleset.toggles, toggle_key):
                    setattr(ruleset.toggles, toggle_key, bool(toggle_value))
            continue
        try:
            rules_engine.set_rule(ruleset, key, str(value))
        except rules_engine.RuleError:
            continue

    start = meta.start or catalogs.start or next(iter(catalogs.locations), "")

    player_template = dict(meta.player or {})
    player = _character_from(player_template, kind="player")
    player.max_hp = rules_engine.effective_max_hp(ruleset, int(player_template.get("hp", player.max_hp)))
    player.hp = player.max_hp
    player.max_mp = int(player_template.get("mp", player.max_mp))
    player.mp = player.max_mp

    items: dict[str, ItemInstance] = {}
    counter = {"n": 0}

    def grant(character: Character, item_id: str, *, equip_slot: str | None = None) -> None:
        if not item_id or item_id not in catalogs.items:
            return
        counter["n"] += 1
        instance_id = f"itm_start_{counter['n']}"
        items[instance_id] = ItemInstance(instance_id=instance_id, item_id=item_id)
        if equip_slot:
            character.equipped[equip_slot] = instance_id
        else:
            character.inventory.append(instance_id)

    grant(player, str(player_template.get("weapon", "")), equip_slot="weapon")
    grant(player, str(player_template.get("armor", "")), equip_slot="armor")
    for item_id in meta.starting_items:
        grant(player, item_id)

    party: list[Character] = []
    for template in meta.party:
        member = _character_from(template, kind="companion")
        member.max_hp = rules_engine.effective_max_hp(ruleset, member.max_hp)
        member.hp = member.max_hp
        party.append(member)

    locations = {
        location_id: LocationState(
            id=location_id,
            discovered=location_id == start,
            visited=location_id == start,
        )
        for location_id in catalogs.locations
    }

    quests: dict[str, QuestState] = {}
    for quest_id in meta.starting_quests:
        definition = catalogs.quests.get(quest_id)
        if definition is not None:
            quests[quest_id] = QuestState(
                id=quest_id,
                stage=definition.stages[0] if definition.stages else "start",
                status="active",
            )

    return GameState(
        time=TimeStamp(day=1, hour=8, minute=0),
        player=player,
        party=party,
        items=items,
        world=WorldState(locations=locations, current_location=start),
        quests=quests,
        rules=ruleset,
        flags={
            "setting": meta.premise or meta.tone or meta.description,
            "world_id": meta.id,
            "world_lore": {
                "name": meta.name,
                "subtitle": meta.subtitle,
                "tone": meta.tone,
                "premise": meta.premise,
                "era": meta.era,
                "magic": meta.magic,
                "peoples": list(meta.peoples),
                "laws": list(meta.laws),
            },
        },
    )
