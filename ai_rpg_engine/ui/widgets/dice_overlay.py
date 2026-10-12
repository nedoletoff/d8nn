"""Dice overlay: a short roll animation followed by the result."""

from __future__ import annotations

from dataclasses import dataclass

from rich.text import Text
from textual.widgets import Static

from ui.theme_gruvbox import GRUVBOX, outcome_color

#: animation frames kept deliberately short (<= 300ms total in the app).
ANIMATION_FRAMES = ("[ .. ]", "[ .: ]", "[ ::: ]", "[:::::]")


def animation_frame(step: int, dice: str = "d20") -> Text:
    frame = ANIMATION_FRAMES[min(step, len(ANIMATION_FRAMES) - 1)]
    return Text(f"  {dice}: {frame}", style=GRUVBOX["fg3"])


@dataclass
class RollDisplay:
    dice: str
    value: int
    modifier: int
    total: int
    dc: int | None
    outcome: str | None
    crit: bool = False


def render_roll(display: RollDisplay, reason: str | None = None) -> Text:
    """Render a resolved roll, e.g. ``d20: [ 17 ] + DEX(1) = 18  ✅ PASS``."""
    out = Text()
    if reason:
        out.append(f"  \U0001f3b2 {reason}\n", style=GRUVBOX["fg3"])

    out.append(f"  {display.dice}: [ {display.value} ]", style=GRUVBOX['fg0'])
    if display.modifier:
        sign = "+" if display.modifier >= 0 else "-"
        out.append(f" {sign} {abs(display.modifier)}", style=GRUVBOX["fg2"])
    out.append("  =  ", style=GRUVBOX["fg3"])
    out.append(str(display.total), style=f"bold {GRUVBOX['fg0']}")

    if display.dc is not None:
        out.append(f"   vs DC {display.dc}", style=GRUVBOX["fg3"])
    if display.outcome:
        label = {
            "crit_success": "\u2705 CRIT",
            "success": "\u2705 PASS",
            "failure": "\u274c FAIL",
            "crit_failure": "\U0001f480 CRIT FAIL",
        }.get(display.outcome, display.outcome)
        out.append(f"   {label}", style=f"bold {outcome_color(display.outcome)}")
    return out


class DiceOverlay(Static):
    def show_animation(self, step: int, dice: str = "d20") -> None:
        self.update(animation_frame(step, dice))

    def show_result(self, roll: object, reason: str | None = None) -> None:
        """Accepts any object exposing the RollResult fields."""
        display = RollDisplay(
            dice=getattr(roll, "dice", "d20"),
            value=(getattr(roll, "rolls", [0]) or [0])[0],
            modifier=getattr(roll, "modifier", 0),
            total=getattr(roll, "total", 0),
            dc=getattr(roll, "dc", None),
            outcome=getattr(roll, "outcome", None),
            crit=getattr(roll, "outcome", None) == "crit_success",
        )
        self.update(render_roll(display, reason))
