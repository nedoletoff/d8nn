"""World forge: author your own worlds.

Flow (see ``docs/world_format.md``):

1. the player describes a world in free text;
2. the forge drafts a pack (an LLM structures/augments/fixes it when online, a
   deterministic skeleton otherwise);
3. the player may edit the files by hand — a final pass with **no LLM check**;
4. ``/world check`` optionally asks the LLM to review and fix errors;
5. ``/world accept`` validates references and promotes the draft to a real world.

Drafts live in ``data/worlds/_draft_<id>/`` so they never show up as playable
worlds until accepted.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from engine.catalogs import load_catalogs

DRAFT_PREFIX = "_draft_"
TERRAIN_KNOWN = {
    "village", "forest", "marsh", "bridge", "cave", "water", "ruin", "road",
    "mountain", "school", "library", "garden", "tower", "city", "market",
    "desert", "oasis", "camp", "default",
}
FILENAME_KEYS: tuple[tuple[str, str], ...] = (
    ("world.json", "world"),
    ("world_regions.json", "locations"),
    ("items_catalog.json", "items"),
    ("npcs_catalog.json", "npcs"),
    ("spells_catalog.json", "spells"),
    ("quests_catalog.json", "quests"),
)


class ForgeError(Exception):
    """Raised on invalid world authoring operations."""


@dataclass
class Pack:
    """A world pack as plain data, mirroring the on-disk JSON files."""

    world: dict[str, Any]
    locations: dict[str, Any]
    items: dict[str, Any]
    npcs: dict[str, Any]
    spells: dict[str, Any]
    quests: dict[str, Any]

    def to_files(self) -> dict[str, dict[str, Any]]:
        return {
            "world.json": self.world,
            "world_regions.json": {"start": self.world.get("start", ""), "locations": self.locations},
            "items_catalog.json": {"items": self.items},
            "npcs_catalog.json": {"npcs": self.npcs},
            "spells_catalog.json": {"spells": self.spells},
            "quests_catalog.json": {"quests": self.quests},
        }


# ---------------------------------------------------------------------------
# Templates
# ---------------------------------------------------------------------------


def templates_dir(data_dir: Path) -> Path:
    return Path(data_dir) / "templates"


def load_template(data_dir: Path, name: str) -> dict[str, Any]:
    path = templates_dir(data_dir) / name
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def _templates_brief(data_dir: Path) -> str:
    wanted = (
        "world_template.json",
        "world_regions_template.json",
        "items_template.json",
        "npcs_template.json",
        "spells_template.json",
        "rules_template.json",
    )
    chunks = []
    for name in wanted:
        template = load_template(data_dir, name)
        if template:
            chunks.append(f"# {name}\n{json.dumps(template, ensure_ascii=False, indent=2)}")
    return "\n\n".join(chunks)


# ---------------------------------------------------------------------------
# Pack IO
# ---------------------------------------------------------------------------


def world_dir(data_dir: Path, world_id: str) -> Path:
    return Path(data_dir) / "worlds" / world_id


def is_draft(world_id: str) -> bool:
    return world_id.startswith(DRAFT_PREFIX)


def list_drafts(data_dir: Path) -> list[str]:
    root = Path(data_dir) / "worlds"
    if not root.exists():
        return []
    return sorted(child.name for child in root.iterdir() if child.is_dir() and is_draft(child.name))


def slugify(text: str, *, fallback: str = "world") -> str:
    text = text.strip().lower()
    text = re.sub(r"[^0-9a-zа-яё_ ]+", "", text)
    text = re.sub(r"\s+", "_", text)[:40].strip("_")
    return text or fallback


def write_pack(directory: Path, pack: Pack) -> Path:
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    for filename, payload in pack.to_files().items():
        (directory / filename).write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
    return directory


def read_pack(directory: Path) -> Pack:
    directory = Path(directory)

    def read(filename: str, key: str) -> dict[str, Any]:
        path = directory / filename
        if not path.exists():
            return {}
        raw = json.loads(path.read_text(encoding="utf-8"))
        return raw.get(key, raw) if isinstance(raw, dict) else {}

    return Pack(
        world=read("world.json", "world"),
        locations=read("world_regions.json", "locations"),
        items=read("items_catalog.json", "items"),
        npcs=read("npcs_catalog.json", "npcs"),
        spells=read("spells_catalog.json", "spells"),
        quests=read("quests_catalog.json", "quests"),
    )


# ---------------------------------------------------------------------------
# Drafting
# ---------------------------------------------------------------------------


def _default_player(name: str = "Безымянный") -> dict[str, Any]:
    return {
        "name": name,
        "class": "wanderer",
        "stats": {"STR": 2, "DEX": 2, "INT": 2, "WIS": 1, "CHA": 1, "LUK": 1},
        "hp": 22,
        "mp": 4,
        "ac": 12,
        "weapon": "",
        "armor": "",
        "currency": {"gold": 0, "silver": 2, "copper": 0},
    }


def rule_based_pack(text: str, data_dir: Path) -> Pack:
    """Deterministic minimal skeleton used offline."""
    world_id = slugify(text) or "my_world"
    world = {
        "id": world_id,
        "name": text.strip().splitlines()[0][:48] or "Мой Мир",
        "subtitle": "",
        "emoji": "🌍",
        "tone": text.strip()[:160],
        "description": text.strip(),
        "premise": text.strip(),
        "era": "",
        "magic": "",
        "peoples": [],
        "laws": [text.strip()] if text.strip() else [],
        "start": "camp",
        "rules": {"lethality": "standard"},
        "player": _default_player(),
        "party": [],
        "starting_items": [],
    }
    locations = {
        "camp": {
            "id": "camp",
            "name": "Лагерь",
            "description": "Точка старта.",
            "coords": {"x": 0, "y": 0},
            "connections": ["nearby"],
            "danger_level": 0,
            "encounters": [],
            "npcs": [],
            "region": "start",
            "terrain": "camp",
        },
        "nearby": {
            "id": "nearby",
            "name": "Рядом",
            "description": "Куда ведёт дорога.",
            "coords": {"x": 1, "y": 0},
            "connections": ["camp"],
            "danger_level": 1,
            "encounters": [],
            "npcs": [],
            "region": "start",
            "terrain": "default",
        },
    }
    return Pack(world=world, locations=locations, items={}, npcs={}, spells={}, quests={})


STRUCTURIZE_PROMPT = """\
Ты — редактор и составитель миров для текстовой RPG. Игрок описал мир на
свободном языке. Приведи его к структуре, дополни пробелы (магия, эпоха, народы,
законы, локации, предметы, NPC), исправь ошибки и несостыковки. НЕ фиксируй
конкретный квест — завязку придумает рассказчик на игре.

Верни строго один JSON со схемой:
{
  "world": { ...поля как в world_template.json... },
  "locations": { "id": {...как в world_regions_template.json...} },
  "items": { "id": {...} },
  "npcs": { "id": {...} },
  "spells": { "id": {...} },
  "quests": { ... }
}

Требования:
- координаты локаций образуют связную сетку; connections ссылаются на реальные id;
- стартовая локация указана в world.start и существует в locations;
- weapon/armor у player существуют в items (или пустые строки);
- terrain — из набора: village, forest, marsh, bridge, cave, water, ruin, road,
  mountain, school, library, garden, tower, city, market, desert, oasis, camp.
Ниже — примеры формата:

{templates}
"""


async def structurize(client: Any, text: str, data_dir: Path) -> Pack:
    """Turn a free-text description into a structured pack."""
    if client is None or getattr(client, "offline", False) or not text.strip():
        return rule_based_pack(text, data_dir)

    prompt = STRUCTURIZE_PROMPT.replace("{templates}", _templates_brief(data_dir))
    content = await client.complete(
        [
            {"role": "system", "content": prompt},
            {"role": "user", "content": text},
        ],
        response_format={"type": "json_object"},
        temperature=0.6,
    )
    if not content:
        return rule_based_pack(text, data_dir)
    try:
        payload = json.loads(content)
    except json.JSONDecodeError:
        return rule_based_pack(text, data_dir)
    if not isinstance(payload, dict) or "world" not in payload:
        return rule_based_pack(text, data_dir)

    fallback = rule_based_pack(text, data_dir)
    world = payload.get("world") or fallback.world
    world.setdefault("start", next(iter(payload.get("locations") or fallback.locations)))
    world.setdefault("id", slugify(str(world.get("name", text))))
    return Pack(
        world=world,
        locations=payload.get("locations") or fallback.locations,
        items=payload.get("items") or {},
        npcs=payload.get("npcs") or {},
        spells=payload.get("spells") or {},
        quests=payload.get("quests") or {},
    )


async def review(client: Any, directory: Path, data_dir: Path, *, feedback: str | None = None) -> Pack:
    """Ask the LLM to review/repair an on-disk pack (optional check step)."""
    pack = read_pack(directory)
    if client is None or getattr(client, "offline", False):
        return pack
    user = json.dumps(pack.to_files(), ensure_ascii=False)
    if feedback:
        user += f"\n\nПожелания игрока:\n{feedback}"
    prompt = STRUCTURIZE_PROMPT.replace("{templates}", _templates_brief(data_dir))
    content = await client.complete(
        [
            {"role": "system", "content": prompt},
            {"role": "user", "content": "Проверь, исправь и дополни этот черновик мира:\n" + user},
        ],
        response_format={"type": "json_object"},
        temperature=0.4,
    )
    if not content:
        return pack
    try:
        payload = json.loads(content)
    except json.JSONDecodeError:
        return pack
    if not isinstance(payload, dict) or "world" not in payload:
        return pack
    return Pack(
        world=payload.get("world") or pack.world,
        locations=payload.get("locations") or pack.locations,
        items=payload.get("items") or pack.items,
        npcs=payload.get("npcs") or pack.npcs,
        spells=payload.get("spells") or pack.spells,
        quests=payload.get("quests") or pack.quests,
    )


# ---------------------------------------------------------------------------
# Validation & finalize
# ---------------------------------------------------------------------------


def validate_pack(pack: Pack, data_dir: Path, directory: Path) -> tuple[Pack, list[str]]:
    """Repair references and return (fixed_pack, list_of_fixes)."""
    fixes: list[str] = []
    locations = dict(pack.locations)
    world = dict(pack.world)

    if not locations:
        fallback = rule_based_pack(str(world.get("name", "world")), data_dir)
        locations = fallback.locations
        fixes.append("локации отсутствовали — создан стартовый лагерь")

    if world.get("start") not in locations:
        first = next(iter(locations))
        world["start"] = first
        fixes.append(f"старт переназначен на {first!r}")

    for loc_id, loc in locations.items():
        loc.setdefault("id", loc_id)
        loc.setdefault("name", loc_id)
        loc.setdefault("description", "")
        loc.setdefault("coords", {"x": 0, "y": 0})
        if loc.get("terrain") not in TERRAIN_KNOWN:
            loc["terrain"] = "default"
        connections = [c for c in (loc.get("connections") or []) if c in locations]
        if connections != list(loc.get("connections") or []):
            fixes.append(f"{loc_id}: убраны битые связи")
        loc["connections"] = connections
        loc.setdefault("danger_level", 0)
        loc.setdefault("encounters", [])
        loc.setdefault("npcs", [])

    known_items = set(load_catalogs(Path(data_dir)).items) | set(pack.items)
    player = dict(world.get("player") or _default_player())
    for slot in ("weapon", "armor"):
        item_id = player.get(slot)
        if item_id and item_id not in known_items:
            player[slot] = ""
            fixes.append(f"{slot}: неизвестный предмет отброшен")
    player.setdefault("name", "Безымянный")
    world["player"] = player

    world.setdefault("name", "Мой Мир")
    world.setdefault("laws", [])
    world.setdefault("peoples", [])
    return Pack(world, locations, pack.items, pack.npcs, pack.spells, pack.quests), fixes


def finalize(directory: Path, data_dir: Path, *, world_id: str | None = None) -> Path:
    """Validate a draft and promote it to a real world directory."""
    directory = Path(directory)
    pack = read_pack(directory)
    pack, _fixes = validate_pack(pack, data_dir, directory)

    raw_id = world_id or directory.name
    clean = slugify(raw_id.replace(DRAFT_PREFIX, ""))
    target = world_dir(data_dir, clean)
    counter = 1
    while target.exists() and target != directory:
        counter += 1
        target = world_dir(data_dir, f"{clean}_{counter}")

    pack.world["id"] = clean
    write_pack(directory, pack)
    if target != directory:
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            import shutil

            shutil.rmtree(target)
        directory.rename(target)
    return target


def create_draft(text: str, data_dir: Path, *, pack: Pack) -> Path:
    """Write a draft pack under ``data/worlds/_draft_<slug>/``."""
    draft_id = DRAFT_PREFIX + slugify(str(pack.world.get("name", text)))
    directory = world_dir(data_dir, draft_id)
    counter = 1
    while directory.exists():
        counter += 1
        directory = world_dir(data_dir, f"{draft_id}_{counter}")
    pack.world.setdefault("id", draft_id)
    return write_pack(directory, pack)
