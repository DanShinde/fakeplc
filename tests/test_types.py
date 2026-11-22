from __future__ import annotations

import pytest

from fakeplc.types import coerce, default_value, from_registers, string_register_count, to_registers


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


@pytest.mark.parametrize(
    ("value", "length", "words"),
    [
        ("AB", 20, [0x4142] + [0] * 9),
        ("ABC", 20, [0x4142, 0x4300] + [0] * 8),  # odd length: last byte NUL padded
        ("", 4, [0, 0]),
        ("Hi", 2, [0x4869]),
        ("Default", 7, [0x4465, 0x6661, 0x756C, 0x7400]),  # ceil(7/2) = 4 registers
    ],
)
def test_string_register_round_trip(value: str, length: int, words: list[int]) -> None:
    assert to_registers("string", value, length=length) == words
    assert from_registers("string", words, length=length) == value
    assert to_registers("string", value, "little", length) == list(reversed(words))
    assert from_registers("string", list(reversed(words)), "little", length) == value


def test_string_register_count() -> None:
    assert string_register_count(1) == 1
    assert string_register_count(2) == 1
    assert string_register_count(3) == 2
    assert string_register_count(20) == 10


def test_string_too_long_has_a_clear_error() -> None:
    with pytest.raises(ValueError, match="longer than 3 characters"):
        to_registers("string", "abcd", length=3)
    with pytest.raises(ValueError, match="longer than 4 characters"):
        coerce("string", "too long for 4", 4)


def test_string_must_be_ascii() -> None:
    with pytest.raises(ValueError, match="not ASCII"):
        coerce("string", "héllo", 10)
    with pytest.raises(ValueError, match="not ASCII"):
        to_registers("string", "héllo", length=10)


def test_coerce_and_default_string() -> None:
    assert coerce("string", "AB", 20) == "AB"
    assert coerce("string", 42, 10) == "42"
    assert default_value("string") == ""
    with pytest.raises(ValueError, match="cannot convert"):
        coerce("string", ["x"], 10)
