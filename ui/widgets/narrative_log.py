"""Central narrative log: a scrollable column of selectable text entries.

Each entry is its own :class:`Static`, so text can be selected (across entries,
too) and copied with Ctrl+C, scrolling works, and individual lines can carry
their own colour (e.g. a dice result coloured by its outcome).
"""

from __future__ import annotations

import re

from rich.markdown import Markdown
from rich.text import Text
from textual.containers import VerticalScroll
from textual.widgets import Static

from ui.theme_gruvbox import GRUVBOX

#: Anything in the emoji / pictograph ranges is stripped from player-facing text.
_EMOJI_RE = re.compile(
    "["
    "\U0001f000-\U0001faff"   # emoji & pictographs
    "\U00002600-\U000027bf"   # misc symbols & dingbats (⚔ ✅ ❌ ❤ ♠ …)
    "\U0001f1e6-\U0001f1ff"   # regional indicators
    "\u2b00-\u2bff"           # arrows / stars
    "\u2190-\u21ff"           # arrows
    "\ufe0f\u200d"            # variation selectors / ZWJ
    "]+"
)


def strip_emojis(text: str) -> str:
    """Remove emojis and pictographs, then tidy leftover double spaces."""
    cleaned = _EMOJI_RE.sub("", text)
    cleaned = re.sub(r"[ \t]{2,}", " ", cleaned)
    cleaned = re.sub(r" +([,.;:!?])", r"\1", cleaned)
    return cleaned.strip()


_KIND_STYLE = {
    "gm": GRUVBOX["fg1"],
    "player": GRUVBOX["blue"],
    "system": GRUVBOX["fg4"],
    "location": GRUVBOX["aqua"],
    "crime": GRUVBOX["red"],
    "dice": GRUVBOX["fg2"],
    "gain": GRUVBOX["green"],
    "loss": GRUVBOX["red"],
}

_PREFIX = {
    "gm": "\u25c8 ",       # ◈
    "player": "> ",
    "system": "\u00b7 ",   # ·
    "location": "\u2691 ",  # ⚑
    "crime": "\u2020 ",    # †
    "dice": "\u25cb ",     # ○
    "gain": "+ ",
    "loss": "- ",
}


def format_entry(text: str, kind: str = "gm", *, style: str | None = None) -> Text:
    """Prefix and color a log entry. Player-facing text drops emojis."""
    return Text(
        _PREFIX.get(kind, "") + strip_emojis(text),
        style=style or _KIND_STYLE.get(kind, GRUVBOX["fg1"]),
    )


class NarrativeLog(VerticalScroll):
    """Scrollable, selectable story log (one Static per entry)."""

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._lines: list[str] = []

    @property
    def lines(self) -> list[str]:
        """Plain-text lines (used by tests and by ``/log`` export)."""
        return list(self._lines)

    def add(self, text: str, kind: str = "gm", *, style: str | None = None, markdown: bool = False) -> None:
        plain = strip_emojis(text)
        self._lines.append(plain)
        renderable = Markdown(plain) if markdown else format_entry(text, kind, style=style)
        self.mount(Static(renderable))
        self.call_after_refresh(self.scroll_end, animate=False)

    def add_location(self, location: str, time_line: str) -> None:
        line = Text()
        line.append(f"\u2691 {location}", style=f"bold {GRUVBOX['aqua']}")
        line.append(f"   {time_line}", style=GRUVBOX["fg3"])
        self._lines.append(strip_emojis(location))
        self.mount(Static(line))
        self.call_after_refresh(self.scroll_end, animate=False)

    def clear(self) -> None:
        self._lines.clear()
        for child in list(self.children):
            child.remove()
