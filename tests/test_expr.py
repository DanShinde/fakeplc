from __future__ import annotations

import random

import pytest

from fakeplc.expr import Context, Expression, ExpressionError


def ev(source: str, t: float = 0.0, **values: object) -> object:
    names = {k.replace("__", "."): v for k, v in values.items()}
    return Expression(source).evaluate(Context(names, t, random.Random(0)))


def test_arithmetic_and_dotted_names() -> None:
    assert ev("Tank.Level * 2 + 1", Tank__Level=10) == 21
    assert ev("A.B.C - 1", A__B__C=5) == 4


def test_boolean_logic_and_conditionals() -> None:
    assert ev("Start and not Fault", Start=True, Fault=False) is True
    assert ev("Start and not Fault", Start=True, Fault=True) is False
    assert ev("1 if Run else 0", Run=True) == 1
    assert ev("0 < x <= 10", x=10) is True


def test_signal_functions_use_time() -> None:
    assert ev("ramp(0, 100, 10)", t=2.5) == 25
    assert ev("square(0, 1, 10)", t=1) == 1
    assert ev("square(0, 1, 10)", t=6) == 0
    assert abs(ev("sine(10, 4)", t=1) - 10) < 1e-9
    assert ev("counter(2, 0.5)", t=1.5) == 6
    assert ev("t") == 0.0


def test_noise_is_repeatable() -> None:
    a = Expression("noise(20, 1)").evaluate(Context({}, 0, random.Random(5)))
    b = Expression("noise(20, 1)").evaluate(Context({}, 0, random.Random(5)))
    assert a == b
    assert 19 <= a <= 21


def test_keyword_arguments_and_helpers() -> None:
    assert ev("ramp(low=10, high=20, period=10)", t=5) == 15
    assert ev("limit(x, 0, 5)", x=9) == 5
    assert ev("max(a, b)", a=1, b=2) == 2


@pytest.mark.parametrize(
    "source",
    [
        "__import__('os')",
        "open('x')",
        "x.__class__",
        "(lambda: 1)()",
        "[1, 2]",
        "x[0]",
        "a := 1",
        "f(x).y",
    ],
)
def test_rejects_unsafe_syntax(source: str) -> None:
    with pytest.raises(ExpressionError):
        Expression(source)


def test_unknown_names_are_reported() -> None:
    expr = Expression("Motor.Start and Missing.Tag")
    with pytest.raises(ExpressionError, match="Missing.Tag"):
        expr.check_names(["Motor.Start"])


def test_runtime_errors_name_the_expression() -> None:
    with pytest.raises(ExpressionError, match="1 / x"):
        ev("1 / x", x=0)


def test_string_constants() -> None:
    assert ev('"Clean" if Mode == 2 else Name', Mode=2, Name="Old") == "Clean"
    assert ev('"Clean" if Mode == 2 else Name', Mode=1, Name="Old") == "Old"
    assert ev('Name + "!"', Name="Hi") == "Hi!"
    with pytest.raises(ExpressionError, match="not allowed"):
        Expression("[1, 2]")
