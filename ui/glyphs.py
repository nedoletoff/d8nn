"""Glyph selection: emoji or ASCII fallbacks.

Emoji have a display width that many terminals/server fonts disagree on, which
breaks column alignment. Users can disable them (``EMOJI=false`` or
``/emoji off``); this module then returns ASCII replacements everywhere.
"""

from __future__ import annotations

EMOJI_ENABLED = True


def set_emoji(enabled: bool) -> None:
    global EMOJI_ENABLED
    EMOJI_ENABLED = bool(enabled)


def emoji_on() -> bool:
    return EMOJI_ENABLED


def pick(emoji_text: str, ascii_text: str = "") -> str:
    """Return the emoji when enabled, otherwise the ASCII fallback."""
    return emoji_text if EMOJI_ENABLED else ascii_text
