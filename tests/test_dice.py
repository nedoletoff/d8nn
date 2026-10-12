"""Smoke tests for the deterministic dice engine (spec step 3)."""

from __future__ import annotations

import pytest

from engine.dice_engine import DiceEngine, DiceSpec, RollResult, quick_roll


class SeqRandom:
    """Injectable RNG returning a fixed, clamped sequence of values."""

    def __init__(self, *values: int) -> None:
        assert values, "need at least one value"
        self._values = list(values)
        self._i = 0

    def randint(self, a: int, b: int) -> int:
        value = self._values[self._i % len(self._values)]
        self._i += 1
        return max(a, min(b, value))


# ---------------------------------------------------------------------------
# Notation parsing
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "count", "sides", "modifier"),
    [
        ("d20", 1, 20, 0),
        ("D20", 1, 20, 0),
        ("1d20", 1, 20, 0),
        ("2d6+3", 2, 6, 3),
        ("1d8-2", 1, 8, -2),
        ("4D10 + 5", 4, 10, 5),
    ],
)
def test_parse_valid(text: str, count: int, sides: int, modifier: int) -> None:
    spec = DiceSpec.parse(text)
    assert (spec.count, spec.sides, spec.modifier) == (count, sides, modifier)


@pytest.mark.parametrize("text", ["", "d", "abc", "2x6", "d0", "0d6", "1d1", "3", "d20++1"])
def test_parse_invalid(text: str) -> None:
    with pytest.raises(ValueError):
        DiceSpec.parse(text)


def test_notation_roundtrip() -> None:
    assert DiceSpec(2, 6, 3).notation == "2d6+3"
    assert DiceSpec(1, 20, 0).notation == "1d20"
    assert DiceSpec(1, 8, -2).notation == "1d8-2"


def test_spec_validation() -> None:
    with pytest.raises(ValueError):
        DiceSpec(0, 6)
    with pytest.raises(ValueError):
        DiceSpec(1, 1)
    with pytest.raises(ValueError):
        DiceSpec(1000, 6)


# ---------------------------------------------------------------------------
# Determinism
# ---------------------------------------------------------------------------


def test_same_seed_reproduces_sequence() -> None:
    first = DiceEngine(seed="canon-42")
    second = DiceEngine(seed="canon-42")
    rolls_a = [first.roll("1d20").total for _ in range(8)]
    rolls_b = [second.roll("1d20").total for _ in range(8)]
    assert rolls_a == rolls_b
    assert first.counter == second.counter == 8


def test_counter_advances_each_roll() -> None:
    engine = DiceEngine(seed="s")
    assert engine.counter == 0
    engine.roll("1d6")
    engine.roll("2d6")
    assert engine.counter == 2


def test_unseeded_rolls_stay_in_range() -> None:
    engine = DiceEngine(seed=None)
    for _ in range(50):
        result = engine.roll("3d8")
        assert len(result.rolls) == 3
        assert all(1 <= value <= 8 for value in result.rolls)


def test_snapshot_roundtrip_continues_sequence() -> None:
    engine = DiceEngine(seed="branch-1")
    engine.roll("1d20")  # consume counter 0
    snapshot = engine.snapshot()

    restored = DiceEngine.from_snapshot(snapshot)
    assert restored.counter == engine.counter == 1
    assert restored.roll("1d20").total == engine.roll("1d20").total
    assert restored.seed == "branch-1"


# ---------------------------------------------------------------------------
# Modifiers and DC resolution
# ---------------------------------------------------------------------------


def test_modifier_is_applied() -> None:
    engine = DiceEngine(rng=SeqRandom(4))
    result = engine.roll("1d6+2", modifier=3)
    assert result.total == 4 + 2 + 3
    assert result.modifier == 5


def test_dc_success_and_failure() -> None:
    engine = DiceEngine(rng=SeqRandom(12))
    success = engine.roll("1d20", modifier=3, dc=14)
    assert success.outcome == "success"
    assert success.total == 15

    engine = DiceEngine(rng=SeqRandom(5))
    failure = engine.roll("1d20", modifier=0, dc=14)
    assert failure.outcome == "failure"


def test_dc_boundary_is_inclusive() -> None:
    engine = DiceEngine(rng=SeqRandom(10))
    result = engine.roll("1d20", dc=10, modifier=0)
    assert result.total == 10
    assert result.outcome == "success"


def test_no_dc_means_no_outcome() -> None:
    engine = DiceEngine(rng=SeqRandom(7))
    result = engine.roll("2d6")
    assert result.outcome is None


# ---------------------------------------------------------------------------
# Criticals
# ---------------------------------------------------------------------------


def test_natural_twenty_is_crit_success_even_above_dc() -> None:
    engine = DiceEngine(rng=SeqRandom(20))
    result = engine.roll("1d20", dc=30)
    assert result.natural == 20
    assert result.outcome == "crit_success"
    assert result.success


def test_natural_one_is_crit_failure_even_below_dc() -> None:
    engine = DiceEngine(rng=SeqRandom(1))
    result = engine.roll("1d20", dc=1)
    assert result.natural == 1
    assert result.outcome == "crit_failure"
    assert not result.success


def test_crit_doubles_damage_dice_only() -> None:
    normal = DiceEngine(rng=SeqRandom(2)).roll_damage("2d6+3")
    critical = DiceEngine(rng=SeqRandom(2)).roll_damage("2d6+3", crit=True)
    assert normal.total == 2 + 2 + 3
    assert critical.total == 2 + 2 + 2 + 2 + 3  # 4d6 + 3 (modifier once)


# ---------------------------------------------------------------------------
# Advantage / disadvantage
# ---------------------------------------------------------------------------


def test_advantage_keeps_highest() -> None:
    engine = DiceEngine(rng=SeqRandom(5, 18))
    result = engine.roll("1d20", advantage="advantage")
    assert result.rolls == [18]
    assert result.dropped == [5]
    assert result.advantage == "advantage"


def test_disadvantage_keeps_lowest() -> None:
    engine = DiceEngine(rng=SeqRandom(5, 18))
    result = engine.roll("1d20", advantage="disadvantage")
    assert result.rolls == [5]
    assert result.dropped == [18]


# ---------------------------------------------------------------------------
# Fudging
# ---------------------------------------------------------------------------


def test_fudge_is_off_by_default() -> None:
    engine = DiceEngine(rng=SeqRandom(14))
    result = engine.roll("1d20", dc=15)  # miss by 1
    assert result.outcome == "failure"
    assert result.fudged is False


def test_fudge_turns_near_miss_into_flagged_success() -> None:
    engine = DiceEngine(rng=SeqRandom(14), fudge=True)
    result = engine.roll("1d20", dc=15)
    assert result.outcome == "success"
    assert result.fudged is True
    assert result.total == 14  # the raw number is never rewritten
    assert result.dc == 15


def test_fudge_leaves_far_misses_alone() -> None:
    engine = DiceEngine(rng=SeqRandom(5), fudge=True)
    result = engine.roll("1d20", dc=20)
    assert result.outcome == "failure"
    assert result.fudged is False


# ---------------------------------------------------------------------------
# Convenience
# ---------------------------------------------------------------------------


def test_roll_check_uses_d20_and_stat_modifier() -> None:
    engine = DiceEngine(rng=SeqRandom(11))
    result = engine.roll_check(stat_modifier=2, dc=13)
    assert result.dice == "1d20"
    assert result.total == 13
    assert result.outcome == "success"


def test_quick_roll_returns_result() -> None:
    result = quick_roll("2d6+1", seed="x")
    assert isinstance(result, RollResult)
    assert len(result.rolls) == 2
    assert result.notation == "2d6+1"
