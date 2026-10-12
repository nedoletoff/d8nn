"""Deterministic game engine for AI RPG Engine.

Everything in this package is deterministic Python. The only modules that talk
to an LLM are ``intent_parser`` and ``narrator`` (via ``llm_client``), and they
operate strictly on a sanitized, read-only view of the game state.
"""

from __future__ import annotations

__all__ = ["__version__"]

__version__ = "0.1.0"
