"""Tests for the LLM narrative layer: client, intent parsing, narration.

Everything is offline / scripted; no network is touched.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from engine import crime_engine, narrator
from engine.catalogs import load_catalogs
from engine.config import Settings
from engine.intent_parser import Intent, heuristic_intent, parse_intent
from engine.llm_client import LLMClient, _extract_content
from engine.state_manager import StateManager

DATA_DIR = Path(__file__).resolve().parents[1] / "data"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class FakeLLM:
    """Minimal stand-in for LLMClient driven by a list of responses."""

    def __init__(self, responses=(), *, offline: bool = False, model: str = "fake") -> None:
        self._responses = list(responses)
        self.offline = offline
        self.settings = SimpleNamespace(intent_model=model, narrator_model=model)
        self.calls: list[dict] = []

    async def complete(self, messages, **kwargs):
        self.calls.append({"messages": messages, **kwargs})
        if self.offline or not self._responses:
            return None
        return self._responses.pop(0)


class ScriptedClient(LLMClient):
    """LLMClient whose transport returns a fixed script of (status, data)."""

    def __init__(self, script, **kwargs) -> None:
        super().__init__(**kwargs)
        self._script = list(script)
        self.post_calls = 0

    async def _post(self, payload):
        self.post_calls += 1
        return self._script.pop(0) if self._script else (500, {})


def make_settings(tmp_path: Path, **overrides) -> Settings:
    values = {
        "save_dir": tmp_path / "saves",
        "log_dir": tmp_path / "logs",
        "data_dir": DATA_DIR,
        "openrouter_api_key": "test-key",
        "offline_mode": "never",
    }
    values.update(overrides)
    return Settings(**values)


@pytest.fixture(autouse=True)
def _no_backoff(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make retries instant so tests never actually sleep."""
    async def _instant(_seconds):
        return None

    monkeypatch.setattr("engine.llm_client.asyncio.sleep", _instant)


def _ok(content: str):
    return (200, {"choices": [{"message": {"content": content}}]})


# ---------------------------------------------------------------------------
# LLMClient
# ---------------------------------------------------------------------------


def test_extract_content_shapes() -> None:
    assert _extract_content({"choices": [{"message": {"content": " hi "}}]}) == "hi"
    assert _extract_content({"choices": []}) is None
    assert _extract_content({}) is None


async def test_offline_client_returns_none(tmp_path: Path) -> None:
    client = ScriptedClient([], settings=make_settings(tmp_path, offline_mode="force"))
    assert client.offline is True
    assert await client.complete([{"role": "user", "content": "hi"}]) is None
    assert client.post_calls == 0


async def test_successful_completion_and_cache(tmp_path: Path) -> None:
    client = ScriptedClient([_ok("привет")], settings=make_settings(tmp_path))
    messages = [{"role": "user", "content": "hi"}]

    assert await client.complete(messages) == "привет"
    assert await client.complete(messages) == "привет"  # served from cache
    assert client.post_calls == 1


async def test_retry_on_5xx_then_success(tmp_path: Path) -> None:
    client = ScriptedClient(
        [(500, {}), _ok("ok")], settings=make_settings(tmp_path, llm_retries=3)
    )
    assert await client.complete([{"role": "user", "content": "x"}]) == "ok"
    assert client.post_calls == 2


async def test_hard_failure_returns_none(tmp_path: Path) -> None:
    client = ScriptedClient([(400, {"error": "bad"})], settings=make_settings(tmp_path))
    assert await client.complete([{"role": "user", "content": "x"}]) is None


async def test_logging_writes_daily_file(tmp_path: Path) -> None:
    client = ScriptedClient([_ok("x")], settings=make_settings(tmp_path))
    await client.complete([{"role": "user", "content": "hi"}])
    logs = list((tmp_path / "logs").glob("llm_*.log"))
    assert logs and "ok" in logs[0].read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# Intent parsing
# ---------------------------------------------------------------------------


async def test_parse_intent_valid_json() -> None:
    client = FakeLLM(['{"action_type":"check","stat":"DEX","dc":14,"target":"пропасть"}'])
    intent = await parse_intent(client, "прыгаю через пропасть", {"player": {"name": "x"}})
    assert intent.action_type == "check"
    assert intent.stat == "DEX"
    assert intent.dc == 14
    assert intent.source == "llm"


async def test_parse_intent_retries_then_succeeds() -> None:
    client = FakeLLM(["not json at all", '{"action_type":"attack","target":"vlad"}'])
    intent = await parse_intent(client, "бью влада")
    assert intent.action_type == "attack"
    assert intent.source == "llm"
    assert len(client.calls) == 2


async def test_parse_intent_falls_back_to_heuristic() -> None:
    client = FakeLLM(["garbage", "still garbage"])
    intent = await parse_intent(client, "иду к мосту")
    assert intent.source == "heuristic"
    assert intent.action_type == "travel"


async def test_parse_intent_offline_uses_heuristic() -> None:
    client = FakeLLM(offline=True)
    intent = await parse_intent(client, "осматриваюсь")
    assert intent.action_type == "look"
    assert intent.source == "heuristic"


def test_unknown_action_becomes_freeform() -> None:
    intent = heuristic_intent("совершенно непонятная фраза")
    assert intent.action_type == "freeform"


async def test_intent_unknown_action_clamped() -> None:
    client = FakeLLM(['{"action_type":"explode"}'])
    intent = await parse_intent(client, "делаю странное")
    assert intent.action_type == "freeform"


# ---------------------------------------------------------------------------
# Narration
# ---------------------------------------------------------------------------


def test_template_narration_offline() -> None:
    event = {"type": "combat", "roll": {"outcome": "success"}}
    assert "Успех" in narrator.template_narration(event)
    crime_event = {
        "type": "crime",
        "effects": [{"kind": "exposure", "value": "none"}],
    }
    assert "Никто" in narrator.template_narration(crime_event)


async def test_describe_event_uses_llm_then_falls_back() -> None:
    client = FakeLLM(["Ты прыгаешь и хватаешься за корень."])
    text = await narrator.describe_event(client, {"type": "narration", "narration": "x"})
    assert text == "Ты прыгаешь и хватаешься за корень."

    offline = FakeLLM(offline=True)
    fallback = await narrator.describe_event(offline, {"type": "combat", "roll": {"outcome": "failure"}})
    assert "Провал" in fallback


async def test_explain_rules_read_only_fallback() -> None:
    text = await narrator.explain_rules(FakeLLM(offline=True), "бой", "СПРАВКА-ТЕКСТ")
    assert text == "СПРАВКА-ТЕКСТ"


async def test_crime_verdict_fallback_and_llm() -> None:
    context = {
        "witnesses": [
            {"npc_id": "old_mira", "will_report": True, "can_identify": True}
        ],
        "body_found": True,
        "evidence": [],
        "investigation_success": False,
    }
    offline = await narrator.crime_verdict(FakeLLM(offline=True), context)
    assert offline["exposure"] == "accusation"

    scripted = FakeLLM(['{"exposure":"suspicion","who_knows":[],"reason":"hmm"}'])
    verdict = await narrator.crime_verdict(scripted, context)
    assert verdict["exposure"] == "suspicion"


async def test_build_crime_verdict_integration(tmp_path: Path) -> None:
    settings = Settings(
        save_dir=tmp_path / "saves", log_dir=tmp_path / "logs", data_dir=DATA_DIR,
        offline_mode="force",
    )
    manager = StateManager.new_game("llm", settings=settings)
    catalogs = load_catalogs(DATA_DIR)
    crime = crime_engine.record_kill(
        manager, catalogs, killer="player", victim="rotten_vlad", method="poison"
    )

    verdict = await narrator.build_crime_verdict(
        FakeLLM(offline=True), manager.state, catalogs, crime
    )
    assert verdict["exposure"] in ("none", "suspicion", "accusation")

    # The engine still validates whatever the adjudicator returns.
    record = crime_engine.adjudicate(
        manager, catalogs, crime.id, adjudicator=narrator.adjudicator_from(verdict)
    )
    assert record.exposure in ("none", "suspicion", "accusation")
