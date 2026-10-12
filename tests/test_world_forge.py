"""Tests for the world forge: describe -> structure -> edit -> accept."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from engine import world_forge as forge
from engine.worlds import build_state, list_worlds, load_world


class FakeLLM:
    def __init__(self, response, *, offline: bool = False) -> None:
        self._response = response
        self.offline = offline
        self.settings = SimpleNamespace(intent_model="m", narrator_model="m")

    async def complete(self, messages, **kwargs):
        return None if self.offline else self._response


def test_templates_exist() -> None:
    from conftest import DATA_DIR

    for name in (
        "world_template.json",
        "world_regions_template.json",
        "items_template.json",
        "npcs_template.json",
        "spells_template.json",
        "quests_template.json",
        "rules_template.json",
    ):
        assert (DATA_DIR / "templates" / name).exists(), name


async def test_offline_draft_creates_valid_pack(seeded_data: Path) -> None:
    pack = await forge.structurize(None, "мир из стекла и пепла", seeded_data)
    directory = forge.create_draft("мир из стекла и пепла", seeded_data, pack=pack)

    assert directory.name.startswith(forge.DRAFT_PREFIX)
    assert (directory / "world.json").exists()
    assert (directory / "world_regions.json").exists()

    fixed, fixes = forge.validate_pack(forge.read_pack(directory), seeded_data, directory)
    assert fixed.world["start"] in fixed.locations


async def test_llm_structures_description(seeded_data: Path) -> None:
    response = json.dumps(
        {
            "world": {
                "id": "glass_ash",
                "name": "Стеклянный Пепел",
                "subtitle": "Город на костях",
                "emoji": "🌋",
                "tone": "мрачное",
                "era": "бронза",
                "magic": "нет магии",
                "peoples": ["стеклодувы"],
                "laws": ["Стекло помнит огонь"],
                "start": "glass_city",
                "rules": {"lethality": "hardcore"},
                "player": {"name": "Подмастерье", "weapon": "", "armor": ""},
                "party": [],
                "starting_items": [],
            },
            "locations": {
                "glass_city": {
                    "id": "glass_city",
                    "name": "Стеклянный город",
                    "description": "…",
                    "coords": {"x": 0, "y": 0},
                    "connections": ["ash_fields"],
                    "terrain": "city",
                },
                "ash_fields": {
                    "id": "ash_fields",
                    "name": "Пепельные поля",
                    "description": "…",
                    "coords": {"x": 1, "y": 0},
                    "connections": ["glass_city"],
                    "terrain": "desert",
                },
            },
            "items": {},
            "npcs": {},
            "spells": {},
            "quests": {},
        }
    )
    pack = await forge.structurize(FakeLLM(response), "город из стекла", seeded_data)
    assert pack.world["name"] == "Стеклянный Пепел"
    assert "glass_city" in pack.locations

    directory = forge.create_draft("город из стекла", seeded_data, pack=pack)
    assert any("Стекло помнит огонь" == law for law in forge.read_pack(directory).world["laws"])


async def test_review_refines_draft(seeded_data: Path) -> None:
    pack = await forge.structurize(None, "черновик", seeded_data)
    directory = forge.create_draft("черновик", seeded_data, pack=pack)

    response = json.dumps(
        {
            "world": {**pack.world, "name": "Исправленный Мир", "laws": ["Новый закон"]},
            "locations": pack.locations,
            "items": {},
            "npcs": {},
            "spells": {},
            "quests": {},
        }
    )
    reviewed = await forge.review(FakeLLM(response), directory, seeded_data, feedback="сделай мрачнее")
    forge.write_pack(directory, reviewed)
    assert forge.read_pack(directory).world["name"] == "Исправленный Мир"


def test_validate_fixes_references(seeded_data: Path) -> None:
    pack = forge.Pack(
        world={"name": "Хрень", "start": "missing", "player": {"name": "x", "weapon": "nope", "armor": ""}},
        locations={
            "a": {"id": "a", "coords": {"x": 0, "y": 0}, "connections": ["ghost"], "terrain": "unknown"},
        },
        items={},
        npcs={},
        spells={},
        quests={},
    )
    directory = seeded_data / "worlds" / "_draft_test"
    directory.mkdir(parents=True)
    forge.write_pack(directory, pack)
    fixed, fixes = forge.validate_pack(pack, seeded_data, directory)

    assert fixed.world["start"] == "a"
    assert fixed.locations["a"]["connections"] == []
    assert fixed.locations["a"]["terrain"] == "default"
    assert fixed.world["player"]["weapon"] == ""
    assert fixes


def test_finalize_promotes_and_is_playable(seeded_data: Path) -> None:
    pack = forge.rule_based_pack("Мир Тестов", seeded_data)
    directory = forge.create_draft("Мир Тестов", seeded_data, pack=pack)

    target = forge.finalize(directory, seeded_data, world_id="test_world")
    assert target.name == "test_world"
    assert not directory.exists()
    assert (target / "world.json").exists()

    assert any(w.id == "test_world" for w in list_worlds(seeded_data))
    world = load_world("test_world", seeded_data)
    state = build_state(world, seeded_data)
    assert state.world.current_location == world.meta.start


def test_slugify() -> None:
    assert forge.slugify("Мир из Стекла!") == "мир_из_стекла"
    assert forge.slugify("   ") == "world"


def test_list_drafts(seeded_data: Path) -> None:
    assert forge.list_drafts(seeded_data) == []
    pack = forge.rule_based_pack("черновик", seeded_data)
    forge.create_draft("черновик", seeded_data, pack=pack)
    assert len(forge.list_drafts(seeded_data)) == 1
