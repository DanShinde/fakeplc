"""Load and validate a fakeplc YAML file."""

from __future__ import annotations

import copy
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from .expr import Expression, ExpressionError
from .types import REGISTER_COUNT, TYPES, coerce, default_value

__all__ = ["ConfigError", "ModbusAddress", "ModbusSpec", "OpcUaSpec", "PLCConfig", "Rule", "TagSpec", "load_config"]

_TAG_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*(\.[A-Za-z_][A-Za-z0-9_]*)*$")
_RULE = re.compile(r"^\s*([A-Za-z_][A-Za-z0-9_.]*)\s*=(?!=)\s*(.+?)\s*$", re.S)
_TABLES = ("coil", "discrete", "holding", "input")
_TABLE_ALIASES = {
    "coil": "coil",
    "coils": "coil",
    "co": "coil",
    "discrete": "discrete",
    "di": "discrete",
    "discrete_input": "discrete",
    "holding": "holding",
    "hr": "holding",
    "holding_register": "holding",
    "input": "input",
    "ir": "input",
    "input_register": "input",
}
_TOP_KEYS = {"plc", "scan_ms", "autoscan", "seed", "opcua", "modbus", "tags", "logic"}
_TAG_KEYS = {"type", "value", "modbus", "readonly", "sim", "unit", "description"}


class ConfigError(ValueError):
    """Raised when a configuration is not valid."""


@dataclass(frozen=True)
class ModbusAddress:
    table: str
    address: int

    def __str__(self) -> str:
        return f"{self.table} {self.address}"


@dataclass
class TagSpec:
    name: str
    type: str
    value: Any
    modbus: ModbusAddress | None = None
    readonly: bool = False
    sim: Expression | None = None
    unit: str | None = None
    description: str | None = None

    @property
    def register_count(self) -> int:
        return 1 if self.type == "bool" else REGISTER_COUNT[self.type]


@dataclass
class Rule:
    target: str
    expression: Expression

    def __str__(self) -> str:
        return f"{self.target} = {self.expression.source}"


@dataclass
class OpcUaSpec:
    enabled: bool = True
    host: str = "127.0.0.1"
    port: int = 4840
    namespace: str = "urn:fakeplc"


@dataclass
class ModbusSpec:
    enabled: bool = True
    host: str = "127.0.0.1"
    port: int = 5020
    word_order: str = "big"
    unmapped: str = "error"


@dataclass
class PLCConfig:
    name: str = "plc"
    scan_ms: int = 100
    autoscan: bool = True
    seed: int = 0
    opcua: OpcUaSpec = field(default_factory=OpcUaSpec)
    modbus: ModbusSpec = field(default_factory=ModbusSpec)
    tags: dict[str, TagSpec] = field(default_factory=dict)
    logic: list[Rule] = field(default_factory=list)
    source: Path | None = None

    def copy(self) -> PLCConfig:
        return copy.deepcopy(self)


def _fail(where: str, message: str) -> ConfigError:
    return ConfigError(f"{where}: {message}")


def _parse_address(where: str, raw: Any) -> ModbusAddress:
    if isinstance(raw, dict):
        unknown = set(raw) - {"table", "address"}
        if unknown:
            raise _fail(where, f"unknown key {sorted(unknown)[0]!r} in modbus address")
        table_raw, address_raw = raw.get("table"), raw.get("address")
    elif isinstance(raw, str):
        parts = raw.replace(":", " ").split()
        if len(parts) != 2:
            raise _fail(where, f"modbus address {raw!r} should look like 'holding 10'")
        table_raw, address_raw = parts
    else:
        raise _fail(where, f"modbus address {raw!r} should look like 'holding 10'")
    table = _TABLE_ALIASES.get(str(table_raw).lower())
    if table is None:
        raise _fail(where, f"unknown modbus table {table_raw!r}; use one of {', '.join(_TABLES)}")
    try:
        address = int(address_raw)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        raise _fail(where, f"modbus address {address_raw!r} is not a number") from None
    if not 0 <= address <= 65535:
        raise _fail(where, f"modbus address {address} is outside 0..65535")
    return ModbusAddress(table, address)


def _parse_tag(name: str, raw: Any) -> tuple[TagSpec, bool]:
    """Parse one tag; the flag says whether readonly was left to its default."""
    where = f"tag {name!r}"
    if not isinstance(name, str) or not _TAG_NAME.match(name):
        raise _fail(where, "names use letters, digits and underscores, with dots between parts")
    if isinstance(raw, str):
        raw = {"type": raw}
    if not isinstance(raw, dict):
        raise _fail(where, "expected a mapping such as {type: real, modbus: holding 0}")
    unknown = set(raw) - _TAG_KEYS
    if unknown:
        raise _fail(where, f"unknown key {sorted(unknown)[0]!r}")
    type_ = str(raw.get("type", "")).lower()
    if type_ not in TYPES:
        raise _fail(where, f"type must be one of {', '.join(TYPES)}")
    try:
        value = coerce(type_, raw["value"]) if "value" in raw else default_value(type_)
    except (TypeError, ValueError) as exc:
        raise _fail(where, f"bad start value: {exc}") from None
    modbus = _parse_address(where, raw["modbus"]) if raw.get("modbus") is not None else None
    if modbus is not None:
        if type_ == "bool" and modbus.table not in ("coil", "discrete"):
            raise _fail(where, "bool tags go on a coil or a discrete input")
        if type_ != "bool" and modbus.table not in ("holding", "input"):
            raise _fail(where, f"{type_} tags go on a holding or an input register")
    sim = None
    if raw.get("sim") is not None:
        try:
            sim = Expression(str(raw["sim"]))
        except ExpressionError as exc:
            raise _fail(where, str(exc)) from None
    readonly = raw.get("readonly")
    if readonly is not None and not isinstance(readonly, bool):
        raise _fail(where, "readonly must be true or false")
    spec = TagSpec(
        name=name,
        type=type_,
        value=value,
        modbus=modbus,
        readonly=bool(readonly),
        sim=sim,
        unit=None if raw.get("unit") is None else str(raw["unit"]),
        description=None if raw.get("description") is None else str(raw["description"]),
    )
    return spec, readonly is None


def _parse_port(where: str, raw: Any, default: int) -> tuple[bool, int]:
    if raw is False:
        return False, default
    if raw is None or raw is True:
        return True, default
    if isinstance(raw, int) and 0 <= raw <= 65535:
        return True, raw
    raise _fail(where, f"port must be 0..65535 or false, not {raw!r}")


def _parse_section(name: str, raw: Any, allowed: set[str]) -> dict[str, Any]:
    if raw is None or raw is True:
        return {}
    if raw is False:
        return {"port": False}
    if not isinstance(raw, dict):
        raise _fail(name, "expected a mapping or false")
    unknown = set(raw) - allowed
    if unknown:
        raise _fail(name, f"unknown key {sorted(unknown)[0]!r}")
    return raw


def _check_overlaps(tags: dict[str, TagSpec]) -> None:
    used: dict[tuple[str, int], str] = {}
    for spec in tags.values():
        if spec.modbus is None:
            continue
        for offset in range(spec.register_count):
            key = (spec.modbus.table, spec.modbus.address + offset)
            if key[1] > 65535:
                raise _fail(f"tag {spec.name!r}", "runs past modbus address 65535")
            other = used.get(key)
            if other is not None:
                raise _fail(f"tag {spec.name!r}", f"overlaps tag {other!r} at {key[0]} {key[1]}")
            used[key] = spec.name


def _check_prefixes(tags: dict[str, TagSpec]) -> None:
    names = set(tags)
    for name in names:
        parts = name.split(".")
        for i in range(1, len(parts)):
            prefix = ".".join(parts[:i])
            if prefix in names:
                raise _fail(f"tag {name!r}", f"{prefix!r} is a tag, so it cannot also be a folder")


def parse_config(data: dict[str, Any], source: Path | None = None) -> PLCConfig:
    """Build a :class:`PLCConfig` from already loaded YAML data."""
    if not isinstance(data, dict):
        raise ConfigError("the file should contain a mapping with at least a 'tags' key")
    unknown = set(data) - _TOP_KEYS
    if unknown:
        raise ConfigError(f"unknown top-level key {sorted(unknown)[0]!r}")
    config = PLCConfig(source=source)
    config.name = str(data.get("plc", "plc"))
    scan_ms = data.get("scan_ms", 100)
    if not isinstance(scan_ms, int) or isinstance(scan_ms, bool) or scan_ms <= 0:
        raise ConfigError("scan_ms must be a positive whole number of milliseconds")
    config.scan_ms = scan_ms
    config.autoscan = bool(data.get("autoscan", True))
    seed = data.get("seed", 0)
    if not isinstance(seed, int):
        raise ConfigError("seed must be a whole number")
    config.seed = seed

    opcua = _parse_section("opcua", data.get("opcua"), {"host", "port", "namespace"})
    config.opcua.enabled, config.opcua.port = _parse_port("opcua", opcua.get("port"), 4840)
    config.opcua.host = str(opcua.get("host", config.opcua.host))
    config.opcua.namespace = str(opcua.get("namespace", config.opcua.namespace))

    modbus = _parse_section("modbus", data.get("modbus"), {"host", "port", "word_order", "unmapped"})
    config.modbus.enabled, config.modbus.port = _parse_port("modbus", modbus.get("port"), 5020)
    config.modbus.host = str(modbus.get("host", config.modbus.host))
    word_order = str(modbus.get("word_order", "big")).lower()
    if word_order not in ("big", "little"):
        raise ConfigError("modbus: word_order must be big or little")
    config.modbus.word_order = word_order
    unmapped = str(modbus.get("unmapped", "error")).lower()
    if unmapped not in ("error", "zero"):
        raise ConfigError("modbus: unmapped must be error or zero")
    config.modbus.unmapped = unmapped

    raw_tags = data.get("tags")
    if not isinstance(raw_tags, dict) or not raw_tags:
        raise ConfigError("'tags' must be a mapping with at least one tag")
    implicit: set[str] = set()
    for name, raw in raw_tags.items():
        spec, readonly_implicit = _parse_tag(name, raw)
        config.tags[name] = spec
        if readonly_implicit:
            implicit.add(name)
    _check_prefixes(config.tags)
    _check_overlaps(config.tags)

    raw_logic = data.get("logic") or []
    if not isinstance(raw_logic, list):
        raise ConfigError("'logic' must be a list of rules such as 'Motor.Running = Motor.Start'")
    for index, line in enumerate(raw_logic, start=1):
        where = f"logic rule {index}"
        match = _RULE.match(str(line))
        if not match:
            raise _fail(where, f"{line!r} should look like 'Tag.Name = expression'")
        target, source_text = match.groups()
        if target not in config.tags:
            raise _fail(where, f"unknown tag {target!r}")
        if config.tags[target].sim is not None:
            raise _fail(where, f"{target!r} already has a sim expression")
        try:
            config.logic.append(Rule(target, Expression(source_text)))
        except ExpressionError as exc:
            raise _fail(where, str(exc)) from None

    for spec in config.tags.values():
        if spec.sim is not None:
            try:
                spec.sim.check_names(config.tags)
            except ExpressionError as exc:
                raise _fail(f"tag {spec.name!r}", str(exc)) from None
    for index, rule in enumerate(config.logic, start=1):
        try:
            rule.expression.check_names(config.tags)
        except ExpressionError as exc:
            raise _fail(f"logic rule {index}", str(exc)) from None

    driven = {rule.target for rule in config.logic}
    for name in implicit:
        spec = config.tags[name]
        table = spec.modbus.table if spec.modbus else None
        spec.readonly = table in ("discrete", "input") or spec.sim is not None or name in driven
    return config


def load_config(source: str | Path | dict[str, Any] | PLCConfig) -> PLCConfig:
    """Load a configuration from a YAML file path, a YAML string, or a dict."""
    if isinstance(source, PLCConfig):
        return source.copy()
    if isinstance(source, dict):
        return parse_config(copy.deepcopy(source))
    text: str
    path: Path | None = None
    if isinstance(source, Path) or ("\n" not in str(source) and str(source).endswith((".yaml", ".yml"))):
        path = Path(source)
        try:
            text = path.read_text(encoding="utf-8")
        except FileNotFoundError:
            raise ConfigError(f"config file not found: {path}") from None
    else:
        text = str(source)
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise ConfigError(f"{path or 'config'}: invalid YAML: {exc}") from None
    try:
        return parse_config(data, path)
    except ConfigError as exc:
        if path is not None:
            raise ConfigError(f"{path}: {exc}") from None
        raise
