"""PLC data types and how they map to Modbus registers and OPC UA variants."""

from __future__ import annotations

import struct
from typing import Any

__all__ = ["REGISTER_COUNT", "TYPES", "coerce", "default_value", "from_registers", "to_registers"]

# name -> (struct format, minimum, maximum); None limits mean a float type
_NUMERIC: dict[str, tuple[str, float | None, float | None]] = {
    "int": ("h", -(2**15), 2**15 - 1),
    "uint": ("H", 0, 2**16 - 1),
    "dint": ("i", -(2**31), 2**31 - 1),
    "udint": ("I", 0, 2**32 - 1),
    "real": ("f", None, None),
    "lreal": ("d", None, None),
}

TYPES: tuple[str, ...] = ("bool", *_NUMERIC)

#: Number of 16-bit registers a value of each numeric type occupies.
REGISTER_COUNT: dict[str, int] = {name: struct.calcsize(fmt) // 2 for name, (fmt, _, _) in _NUMERIC.items()}


def default_value(type_: str) -> Any:
    if type_ == "bool":
        return False
    if type_ in ("real", "lreal"):
        return 0.0
    return 0


def coerce(type_: str, value: Any) -> Any:
    """Convert ``value`` to the Python value stored for a tag of ``type_``.

    Integers are range checked, ``real`` values are rounded to 32-bit float
    precision so that OPC UA and Modbus clients see the same number.
    """
    if type_ == "bool":
        if isinstance(value, str):
            lowered = value.strip().lower()
            if lowered in ("true", "1", "on", "yes"):
                return True
            if lowered in ("false", "0", "off", "no"):
                return False
            raise ValueError(f"cannot convert {value!r} to bool")
        return bool(value)
    _fmt, low, high = _NUMERIC[type_]
    if low is None:
        number = float(value)
        if type_ == "real":
            number = struct.unpack("<f", struct.pack("<f", number))[0]
        return number
    if isinstance(value, float) and not value.is_integer():
        value = round(value)
    number = int(value)
    assert high is not None
    if not low <= number <= high:
        raise ValueError(f"{number} is out of range for {type_} ({low}..{high})")
    return number


def to_registers(type_: str, value: Any, word_order: str = "big") -> list[int]:
    """Encode a value as big-endian 16-bit registers."""
    fmt = _NUMERIC[type_][0]
    raw = struct.pack(">" + fmt, value)
    words = [int.from_bytes(raw[i : i + 2], "big") for i in range(0, len(raw), 2)]
    if word_order == "little":
        words.reverse()
    return words


def from_registers(type_: str, registers: list[int], word_order: str = "big") -> Any:
    """Decode 16-bit registers into a value."""
    fmt = _NUMERIC[type_][0]
    words = list(registers)
    if word_order == "little":
        words.reverse()
    raw = b"".join(word.to_bytes(2, "big") for word in words)
    return struct.unpack(">" + fmt, raw)[0]
