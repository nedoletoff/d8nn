"""Header bar: title, world/location/time, model, save state and busy flag."""

from __future__ import annotations

from rich.text import Text
from textual.widgets import Static

from ui.theme_gruvbox import GRUVBOX

TITLE = "d8nn"
EXPANSION = "dungeon and neutron network"


def render_header(
    *,
    model: str,
    offline: bool,
    saved: bool = False,
    canon_broken: bool = False,
    busy: bool = False,
    world: str | None = None,
    location: str | None = None,
    time_line: str | None = None,
) -> Text:
    """Build the header. Line 1: identity/model; line 2: world/location/time."""
    line = Text()
    line.append(TITLE, style=f"bold {GRUVBOX['yellow']}")
    line.append("  ")
    line.append(f"({EXPANSION})", style=GRUVBOX["fg4"])
    line.append("   \u00b7   ")
    line.append(model, style=GRUVBOX["blue"])
    if offline:
        line.append("   \u00b7   ")
        line.append("offline", style=GRUVBOX["red"])
    if busy:
        line.append("   \u00b7   ")
        line.append("модель думает", style=GRUVBOX["orange"])
    if canon_broken:
        line.append("   \u00b7   ")
        line.append("КАНОН ПОВРЕЖДЁН", style=f"bold {GRUVBOX['red']}")
    elif saved:
        line.append("   \u00b7   ")
        line.append("[СОХРАНЕНО]", style=f"bold {GRUVBOX['green']}")

    out = Text()
    out.append_text(line)
    if world or location or time_line:
        out.append("\n")
        if world:
            out.append(world, style=f"bold {GRUVBOX['aqua']}")
        if location:
            out.append("  \u00b7  " if world else "")
            out.append(location, style=GRUVBOX["orange"])
        if time_line:
            out.append("  \u00b7  ")
            out.append(time_line, style=GRUVBOX["fg3"])
    return out


class HeaderBar(Static):
    """Always-visible top bar (two lines: identity + world/location/time)."""

    def update_header(
        self,
        *,
        model: str,
        offline: bool,
        saved: bool = False,
        canon_broken: bool = False,
        busy: bool = False,
        world: str | None = None,
        location: str | None = None,
        time_line: str | None = None,
    ) -> None:
        self.update(
            render_header(
                model=model,
                offline=offline,
                saved=saved,
                canon_broken=canon_broken,
                busy=busy,
                world=world,
                location=location,
                time_line=time_line,
            )
        )
