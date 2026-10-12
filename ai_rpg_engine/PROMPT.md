# PROMPT — AI RPG Engine (project brief)

A console text RPG sandbox with an AI game master.

**Core contract:** every mechanic (dice, HP, inventory, map, quests, time,
economy, crime) is deterministic Python. The LLM is a narrator only: it parses
player intent into JSON and describes a result the engine already computed. It
can never change a number, a fact, or the past.

## Non-negotiable principles

1. Deterministic-first — the LLM never decides an outcome.
2. Immutable canon — `canon_log.jsonl` is append-only, protected by a sha256
   hash-chain. Tampering breaks the chain and loading fails.
3. Append-only history — the LLM only sees a canon snapshot + a recent window.
4. Single source of truth — `game_state.json` is authoritative; the LLM gets a
   sanitized view only.
5. Failure-safe — with no LLM the engine keeps playing in dry mode.
6. No macOS — on `darwin` print `open your mind` and exit 1.
7. Gruvbox everywhere — Gruvbox Dark only.

## Stack

Python 3.11+, `textual` + `rich`, `pydantic` v2, `httpx`, `openai`/`litellm`,
`jsonlines`, `pydantic-settings`, `pytest`.

## Build order (implemented)

1. `requirements.txt`, `.env.example`, `README.md`
2. `main.py` (OS gate + bootstrap)
3. `engine/dice_engine.py` + `tests/test_dice.py`
4. `engine/canon_log.py` (hash-chain) + `tests/test_canon.py`
5. `engine/state_manager.py` + `data/default_state.json` + `tests/test_state.py`
6. rule engines: time, combat, inventory, quest, npc, world_map (+ catalogs)
7. `engine/llm_client.py`, `intent_parser.py`, `narrator.py`
8. `ui/theme_gruvbox.py`, `ui/css/gruvbox.tcss`
9. `ui/widgets/*`
10. `ui/tui_app.py`
11. `pytest -q`

## Extensions beyond the original brief

- **Crime layer** (`crime_engine.py`): a killing does not automatically damage
  faction relations. Witnesses (perception vs stealth), willingness to report,
  body disposal and physical evidence decide whether the world learns *and can
  name* the killer. A time-delayed investigation may expose it later. An
  adjudicator (LLM or rule-based) proposes `{exposure, who_knows}`; the engine
  validates it against the facts (it cannot claim an identification when no one
  could identify and no body was found). Facts passed to the narrator include
  explicit `body_found` / `body_disposed` flags.
- **Read-only handbook** (`rules_handbook.py`): `/rule <topic>` answers "how does
  it work?" questions. It never writes to the canon, mutates state or saves.
