"""Typed loaders for the JSON catalogs under ``data/``.

Static content (items, spells, NPC templates, quests, the world graph) lives in
JSON. Dynamic per-save state lives in :class:`GameState`. This module is the
only bridge between the two.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

from engine.state_manager import Currency

ItemType = Literal["weapon", "armor", "consumable", "quest", "misc", "spellbook"]


class Coords(BaseModel):
    x: int = 0
    y: int = 0


class EffectDef(BaseModel):
    kind: str
    target: str = "self"
    value: object = None


class ItemDef(BaseModel):
    id: str
    name: str
    type: ItemType = "misc"
    weight: float = 0.0
    value: Currency = Field(default_factory=Currency)
    description: str = ""
    # weapon
    damage_dice: str | None = None
    damage_stat: str = "STR"
    # armor
    armor_bonus: int = 0
    # spellbook
    spell_id: str | None = None
    # consumable
    effects: list[EffectDef] = Field(default_factory=list)


class SpellDef(BaseModel):
    id: str
    name: str
    mp_cost: int = 1
    damage_dice: str | None = None
    school: str = "arcane"
    description: str = ""


class NpcDef(BaseModel):
    id: str
    name: str
    faction: str = "wilds"
    description: str = ""
    disposition: Literal["hostile", "neutral", "friendly"] = "neutral"
    rep: int = 0
    portrait: str | None = None
    location: str | None = None
    hp: int = 8
    max_hp: int = 8
    ac: int = 10
    stats: dict[str, int] = Field(default_factory=dict)
    unarmed_dice: str = "1d4"
    #: item ids this NPC sells (empty = not a merchant)
    stock: list[str] = Field(default_factory=list)
    shop: str = ""


class RewardDef(BaseModel):
    xp: int = 0
    currency: Currency = Field(default_factory=Currency)
    items: list[str] = Field(default_factory=list)


class QuestDef(BaseModel):
    id: str
    title: str
    description: str = ""
    stages: list[str] = Field(default_factory=lambda: ["start"])
    rewards: RewardDef = Field(default_factory=RewardDef)


class LocationDef(BaseModel):
    id: str
    name: str
    description: str = ""
    coords: Coords = Field(default_factory=Coords)
    connections: list[str] = Field(default_factory=list)
    danger_level: int = 0
    encounters: list[str] = Field(default_factory=list)
    npcs: list[str] = Field(default_factory=list)
    region: str = ""
    terrain: str = "default"


class Catalogs(BaseModel):
    """All static content, already validated."""

    items: dict[str, ItemDef] = Field(default_factory=dict)
    spells: dict[str, SpellDef] = Field(default_factory=dict)
    npcs: dict[str, NpcDef] = Field(default_factory=dict)
    quests: dict[str, QuestDef] = Field(default_factory=dict)
    locations: dict[str, LocationDef] = Field(default_factory=dict)
    start: str = ""

    # -- lookups -------------------------------------------------------------

    def item(self, item_id: str) -> ItemDef:
        try:
            return self.items[item_id]
        except KeyError as exc:  # pragma: no cover - defensive
            raise KeyError(f"unknown item id: {item_id}") from exc

    def spell(self, spell_id: str) -> SpellDef:
        return self.spells[spell_id]

    def npc(self, npc_id: str) -> NpcDef:
        return self.npcs[npc_id]

    def quest(self, quest_id: str) -> QuestDef:
        return self.quests[quest_id]

    def location(self, location_id: str) -> LocationDef:
        try:
            return self.locations[location_id]
        except KeyError as exc:
            raise KeyError(f"unknown location id: {location_id}") from exc

    def locations_for_llm(self) -> dict[str, dict[str, object]]:
        """Catalog view safe to embed in a sanitized prompt."""
        return {
            key: {
                "name": value.name,
                "description": value.description,
                "connections": list(value.connections),
            }
            for key, value in self.locations.items()
        }

    def npc_names(self) -> dict[str, str]:
        return {key: value.name for key, value in self.npcs.items()}


def _read(path: Path) -> dict:
    import json

    return json.loads(path.read_text(encoding="utf-8"))


@lru_cache(maxsize=None)
def load_catalogs(data_dir: Path) -> Catalogs:
    """Load and cache all catalogs from ``data_dir``."""
    data_dir = Path(data_dir)
    items_raw = _read(data_dir / "items_catalog.json")
    spells_raw = _read(data_dir / "spells_catalog.json")
    npcs_raw = _read(data_dir / "npcs_catalog.json")
    quests_raw = _read(data_dir / "quests_catalog.json")
    world_raw = _read(data_dir / "world_regions.json")

    return Catalogs(
        items={key: ItemDef(**value) for key, value in items_raw["items"].items()},
        spells={key: SpellDef(**value) for key, value in spells_raw["spells"].items()},
        npcs={key: NpcDef(**value) for key, value in npcs_raw["npcs"].items()},
        quests={key: QuestDef(**value) for key, value in quests_raw["quests"].items()},
        locations={key: LocationDef(**value) for key, value in world_raw["locations"].items()},
        start=world_raw.get("start", ""),
    )


#: filename -> top-level key inside the JSON file
CATALOG_FILES: tuple[tuple[str, str], ...] = (
    ("items_catalog.json", "items"),
    ("spells_catalog.json", "spells"),
    ("npcs_catalog.json", "npcs"),
    ("quests_catalog.json", "quests"),
    ("world_regions.json", "locations"),
)


def _merged_entries(base_dir: Path, overlay_dir: Path | None, filename: str, key: str) -> dict:
    """Merge a catalog: base entries overridden by the world's own entries."""
    merged = dict(_read(base_dir / filename).get(key, {}))
    if overlay_dir is not None:
        overlay = Path(overlay_dir) / filename
        if overlay.exists():
            raw = _read(overlay)
            merged.update(raw.get(key, raw))
    return merged


def build_catalogs(base_dir: Path, overlay_dir: Path | None = None) -> Catalogs:
    """Build catalogs from ``base_dir`` with optional per-world overrides."""
    base_dir = Path(base_dir)
    start = _read(base_dir / "world_regions.json").get("start", "")
    if overlay_dir is not None:
        overlay = Path(overlay_dir) / "world_regions.json"
        if overlay.exists():
            raw = _read(overlay)
            start = raw.get("start", start)

    return Catalogs(
        items={k: ItemDef(**v) for k, v in _merged_entries(base_dir, overlay_dir, "items_catalog.json", "items").items()},
        spells={k: SpellDef(**v) for k, v in _merged_entries(base_dir, overlay_dir, "spells_catalog.json", "spells").items()},
        npcs={k: NpcDef(**v) for k, v in _merged_entries(base_dir, overlay_dir, "npcs_catalog.json", "npcs").items()},
        quests={k: QuestDef(**v) for k, v in _merged_entries(base_dir, overlay_dir, "quests_catalog.json", "quests").items()},
        locations={k: LocationDef(**v) for k, v in _merged_entries(base_dir, overlay_dir, "world_regions.json", "locations").items()},
        start=start,
    )
