from __future__ import annotations

import pytest

from fakeplc.types import coerce, from_registers, to_registers


@pytest.mark.parametrize(
    ("type_", "value", "words"),
    [
        ("int", -2, [0xFFFE]),
        ("uint", 65535, [0xFFFF]),
        ("dint", 70000, [0x0001, 0x1170]),
        ("udint", 4000000000, [0xEE6B, 0x2800]),
        ("real", 1.5, [0x3FC0, 0x0000]),
        ("lreal", 1.0, [0x3FF0, 0, 0, 0]),
    ],
)
def test_register_round_trip(type_: str, value: object, words: list[int]) -> None:
    assert to_registers(type_, value) == words
    assert from_registers(type_, words) == value
    assert to_registers(type_, value, "little") == list(reversed(words))
    assert from_registers(type_, list(reversed(words)), "little") == value


def test_coerce_rounds_real_to_float32() -> None:
    assert coerce("real", 0.1) == pytest.approx(0.1)
    assert coerce("real", 0.1) != 0.1
    assert coerce("lreal", 0.1) == 0.1


def test_coerce_checks_integer_ranges() -> None:
    assert coerce("int", 3.0) == 3
    assert coerce("int", 2.6) == 3
    with pytest.raises(ValueError, match="out of range"):
        coerce("int", 40000)
    with pytest.raises(ValueError):
        coerce("uint", -1)


def test_coerce_bool_from_strings() -> None:
    assert coerce("bool", "on") is True
    assert coerce("bool", "false") is False
    with pytest.raises(ValueError):
        coerce("bool", "maybe")
