"""Deterministic dice engine.

This module is the *only* place where randomness for game mechanics lives. The
LLM never generates a number: it may only narrate a :class:`RollResult` that
this engine already produced.

Determinism model
-----------------
The engine holds a hidden ``seed`` and a monotonically growing ``counter``.
Each individual roll derives a fresh ``random.Random`` from
``sha256(f"{seed}:{counter}")`` and then increments the counter. Given the same
seed and counter a roll is fully reproducible, which is what lets the immutable
canon verify past outcomes. The seed is stored in the game state and is **never**
sent to the LLM.

When ``seed is None`` the engine falls back to OS entropy and results are not
reproducible (useful for throwaway rolls / tests).

Fudging
-------
Dice fudging is **off by default**: canon must remain a pure function of inputs.
When explicitly enabled, the engine may rule a *near miss* (within
``FUDGE_MARGIN``) a success, but the raw dice, the total and the DC are recorded
truthfully and the result is flagged ``fudged=True`` so the canon stays honest.
"""

from __future__ import annotations

import hashlib
import random
import re
from dataclasses import dataclass
from typing import Literal, Protocol

from pydantic import BaseModel, Field

# ---------------------------------------------------------------------------
# Types
# ---------------------------------------------------------------------------

Advantage = Literal["normal", "advantage", "disadvantage"]
Outcome = Literal["crit_success", "success", "failure", "crit_failure"]

#: A near miss within this many points may be ruled a success when fudging is on.
FUDGE_MARGIN = 2

MIN_SIDES = 2
MAX_SIDES = 1000
MAX_COUNT = 100

# "d20", "2d6", "1d8-2", "4D10 + 3" (case-insensitive)
_DICE_RE = re.compile(
    r"""^\s*
        (?:(?P<count>\d+)\s*)?[dD]\s*(?P<sides>\d+)
        (?:\s*(?P<sign>[+-])\s*(?P<mod>\d+))?
        \s*$""",
    re.VERBOSE,
)


class RngLike(Protocol):
    """Minimal random source interface (lets tests inject a fixed sequence)."""

    def randint(self, a: int, b: int) -> int:  # pragma: no cover - protocol
        ...


# ---------------------------------------------------------------------------
# Dice notation
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class DiceSpec:
    """A parsed dice expression such as ``2d6+3``."""

    count: int
    sides: int
    modifier: int = 0

    def __post_init__(self) -> None:
        if not (1 <= self.count <= MAX_COUNT):
            raise ValueError(f"dice count must be in 1..{MAX_COUNT}, got {self.count}")
        if not (MIN_SIDES <= self.sides <= MAX_SIDES):
            raise ValueError(f"dice sides must be in {MIN_SIDES}..{MAX_SIDES}, got {self.sides}")

    @property
    def notation(self) -> str:
        text = f"{self.count}d{self.sides}"
        if self.modifier:
            text += f"{self.modifier:+d}"
        return text

    @classmethod
    def parse(cls, text: str) -> "DiceSpec":
        """Parse ``NdM±k`` notation. Raises :class:`ValueError` on bad input."""
        match = _DICE_RE.match(text or "")
        if not match:
            raise ValueError(f"invalid dice notation: {text!r}")
        count = int(match.group("count") or 1)
        sides = int(match.group("sides"))
        mod = int(match.group("mod") or 0)
        if match.group("sign") == "-":
            mod = -mod
        return cls(count=count, sides=sides, modifier=mod)


# ---------------------------------------------------------------------------
# Result
# ---------------------------------------------------------------------------


class RollResult(BaseModel):
    """Immutable-ish record of a single roll, safe to embed in a canon event."""

    notation: str
    dice: str
    count: int
    sides: int
    rolls: list[int]
    dropped: list[int] = Field(default_factory=list)
    modifier: int = 0
    total: int
    dc: int | None = None
    outcome: Outcome | None = None
    natural: int | None = None
    advantage: Advantage = "normal"
    fudged: bool = False

    @property
    def success(self) -> bool:
        return self.outcome in ("success", "crit_success")


# ---------------------------------------------------------------------------
# Engine
# ---------------------------------------------------------------------------


class DiceEngine:
    """Deterministic dice roller with optional hidden seeding."""

    def __init__(
        self,
        seed: str | None = None,
        counter: int = 0,
        *,
        fudge: bool = False,
        rng: RngLike | None = None,
    ) -> None:
        self._seed = seed
        self._counter = int(counter)
        self.fudge = bool(fudge)
        self._rng = rng

    # -- state ---------------------------------------------------------------

    @property
    def seed(self) -> str | None:
        return self._seed

    @property
    def counter(self) -> int:
        return self._counter

    def snapshot(self) -> dict[str, object]:
        """Serializable state for persistence / canon verification."""
        return {"seed": self._seed, "counter": self._counter, "fudge": self.fudge}

    @classmethod
    def from_snapshot(cls, data: dict[str, object], *, rng: RngLike | None = None) -> "DiceEngine":
        return cls(
            seed=data.get("seed"),  # type: ignore[arg-type]
            counter=int(data.get("counter", 0)),  # type: ignore[arg-type]
            fudge=bool(data.get("fudge", False)),
            rng=rng,
        )

    # -- randomness ----------------------------------------------------------

    def _derive_rng(self) -> random.Random:
        if self._rng is not None:
            return self._rng  # type: ignore[return-value]
        if self._seed is None:
            return random.Random()
        key = f"{self._seed}:{self._counter}".encode("utf-8")
        derived = int.from_bytes(hashlib.sha256(key).digest()[:8], "big")
        return random.Random(derived)

    def _draw(self, count: int, sides: int) -> list[int]:
        rng = self._derive_rng()
        self._counter += 1
        return [rng.randint(1, sides) for _ in range(count)]

    # -- rolls ---------------------------------------------------------------

    def roll(
        self,
        dice: str | DiceSpec,
        *,
        modifier: int = 0,
        dc: int | None = None,
        advantage: Advantage = "normal",
        fudge: bool | None = None,
    ) -> RollResult:
        """Roll ``dice`` and, if ``dc`` is given, resolve an outcome.

        ``modifier`` is an extra flat bonus (e.g. a stat modifier); it is added
        on top of any modifier embedded in the dice notation.
        """
        spec = dice if isinstance(dice, DiceSpec) else DiceSpec.parse(dice)

        use_advantage = advantage in ("advantage", "disadvantage") and spec.count == 1
        draw_count = 2 if use_advantage else spec.count
        raw = self._draw(draw_count, spec.sides)

        dropped: list[int] = []
        if use_advantage:
            keep_high = advantage == "advantage"
            kept = max(raw) if keep_high else min(raw)
            dropped = [value for value in raw if value != kept][:1]
            if not dropped:  # both dice equal
                dropped = [kept]
            rolls = [kept]
        else:
            rolls = raw

        total = sum(rolls) + spec.modifier + modifier
        natural = rolls[0] if (spec.sides == 20 and spec.count == 1) else None

        outcome = self._resolve_outcome(natural, total, dc)

        fudged = False
        effective_fudge = self.fudge if fudge is None else fudge
        if (
            effective_fudge
            and dc is not None
            and outcome == "failure"
            and 0 < (dc - total) <= FUDGE_MARGIN
        ):
            outcome = "success"
            fudged = True

        return RollResult(
            notation=spec.notation,
            dice=f"{spec.count}d{spec.sides}",
            count=spec.count,
            sides=spec.sides,
            rolls=rolls,
            dropped=dropped,
            modifier=spec.modifier + modifier,
            total=total,
            dc=dc,
            outcome=outcome,
            natural=natural,
            advantage=advantage,
            fudged=fudged,
        )

    @staticmethod
    def _resolve_outcome(natural: int | None, total: int, dc: int | None) -> Outcome | None:
        if dc is None:
            return None
        if natural == 20:
            return "crit_success"
        if natural == 1:
            return "crit_failure"
        return "success" if total >= dc else "failure"

    def roll_damage(
        self,
        dice: str | DiceSpec,
        *,
        modifier: int = 0,
        crit: bool = False,
    ) -> RollResult:
        """Roll damage. A crit doubles the dice (not the flat modifier)."""
        spec = dice if isinstance(dice, DiceSpec) else DiceSpec.parse(dice)
        if crit:
            spec = DiceSpec(count=spec.count * 2, sides=spec.sides, modifier=spec.modifier)
        return self.roll(spec, modifier=modifier)

    def roll_check(
        self,
        *,
        stat_modifier: int = 0,
        dc: int,
        advantage: Advantage = "normal",
        fudge: bool | None = None,
    ) -> RollResult:
        """Standard ``d20 + stat`` skill/ability check against a DC."""
        return self.roll(
            DiceSpec(1, 20),
            modifier=stat_modifier,
            dc=dc,
            advantage=advantage,
            fudge=fudge,
        )


def quick_roll(notation: str, *, seed: str | None = None) -> RollResult:
    """Convenience one-shot roll (used by ``/roll`` in offline mode)."""
    return DiceEngine(seed=seed).roll(notation)
