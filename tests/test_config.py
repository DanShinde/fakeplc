from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from fakeplc import ConfigError, load_config

from .conftest import base_config


def test_loads_example(example_path: Path) -> None:
    config = load_config(example_path)
    assert config.name == "line1"
    assert config.tags["Tank.Level"].value == 42.5
    assert str(config.tags["Batch.Count"].modbus) == "holding 4"
    assert config.source == example_path


def test_readonly_defaults() -> None:
    config = load_config(base_config())
    tags = config.tags
    assert not tags["Motor.Start"].readonly
    assert tags["Motor.Running"].readonly  # discrete input and logic target
    assert tags["Batch.Count"].readonly  # logic target
    assert tags["Energy"].readonly  # input register
    assert not tags["Tank.Level"].readonly


def test_yaml_string() -> None:
    config = load_config("tags:\n  A: {type: int, modbus: hr 3}\n")
    assert str(config.tags["A"].modbus) == "holding 3"
    assert config.opcua.port == 4840 and config.modbus.port == 5020


def test_protocols_can_be_disabled() -> None:
    config = load_config({"opcua": False, "modbus": {"port": False}, "tags": {"A": "bool"}})
    assert not config.opcua.enabled
    assert not config.modbus.enabled


def _tags(**tags: Any) -> dict[str, Any]:
    return {"tags": tags}


@pytest.mark.parametrize(
    ("data", "message"),
    [
        ({}, "'tags' must be"),
        ({"tags": {"A": "bool"}, "extra": 1}, "unknown top-level key 'extra'"),
        (_tags(A={"type": "str"}), "type must be one of"),
        (_tags(A={"type": "bool", "modbus": "holding 0"}), "bool tags go on a coil"),
        (_tags(A={"type": "real", "modbus": "coil 0"}), "real tags go on a holding"),
        (
            _tags(A={"type": "real", "modbus": "holding 0"}, B={"type": "int", "modbus": "holding 1"}),
            "overlaps tag 'A'",
        ),
        (_tags(A={"type": "int", "modbus": "register 0"}), "unknown modbus table"),
        (_tags(A={"type": "int", "modbus": "holding"}), "should look like"),
        (_tags(A={"type": "int", "value": 100000}), "bad start value"),
        (_tags(A={"type": "int", "colour": "red"}), "unknown key 'colour'"),
        (_tags(**{"Motor": "bool", "Motor.Speed": "real"}), "cannot also be a folder"),
        (_tags(**{"bad-name": "bool"}), "names use letters"),
        ({"tags": {"A": "bool"}, "logic": ["B = 1"]}, "unknown tag 'B'"),
        ({"tags": {"A": "bool"}, "logic": ["A == 1"]}, "should look like"),
        ({"tags": {"A": "bool"}, "logic": ["A = B"]}, "unknown tag 'B'"),
        ({"tags": {"A": {"type": "real", "sim": "ramp(0, 1, 2)"}}, "logic": ["A = 1"]}, "already has a sim"),
        ({"tags": {"A": {"type": "real", "sim": "open('x')"}}}, "unknown function"),
        ({"tags": {"A": "bool"}, "scan_ms": 0}, "scan_ms"),
        ({"tags": {"A": "bool"}, "modbus": {"word_order": "middle"}}, "word_order"),
        ({"tags": {"A": "bool"}, "opcua": {"port": 70000}}, "port must be"),
    ],
)
def test_errors(data: dict[str, Any], message: str) -> None:
    with pytest.raises(ConfigError, match=message):
        load_config(data)


def test_file_errors_name_the_file(tmp_path: Path) -> None:
    path = tmp_path / "bad.yaml"
    path.write_text("tags:\n  A: {type: nope}\n")
    with pytest.raises(ConfigError, match="bad.yaml: tag 'A'"):
        load_config(path)
    with pytest.raises(ConfigError, match="not found"):
        load_config(tmp_path / "missing.yaml")


def test_string_tag_defaults_to_length_32_without_modbus() -> None:
    config = load_config({"tags": {"Recipe.Name": {"type": "string", "value": "Default"}}})
    spec = config.tags["Recipe.Name"]
    assert spec.length == 32
    assert spec.register_count == 16  # ceil(32 / 2)
    assert spec.value == "Default"


def test_string_tag_on_modbus_requires_explicit_length() -> None:
    with pytest.raises(ConfigError, match="need an explicit length"):
        load_config({"tags": {"Recipe.Name": {"type": "string", "modbus": "holding 10"}}})
    config = load_config(
        {"tags": {"Recipe.Name": {"type": "string", "length": 20, "modbus": "holding 10", "value": "Default"}}}
    )
    spec = config.tags["Recipe.Name"]
    assert spec.length == 20
    assert spec.register_count == 10
    assert str(spec.modbus) == "holding 10"


def test_string_start_value_longer_than_length_names_the_tag() -> None:
    with pytest.raises(ConfigError, match="tag 'Recipe.Name'.*longer than 4 characters"):
        load_config({"tags": {"Recipe.Name": {"type": "string", "length": 4, "value": "Default"}}})


def test_string_tags_cannot_go_on_coils() -> None:
    with pytest.raises(ConfigError, match="holding or an input register"):
        load_config({"tags": {"Recipe.Name": {"type": "string", "length": 4, "modbus": "coil 0"}}})


def test_string_tags_participate_in_overlap_check() -> None:
    with pytest.raises(ConfigError, match="overlaps tag 'A'"):
        load_config(
            {
                "tags": {
                    "A": {"type": "string", "length": 4, "modbus": "holding 0"},  # registers 0..1
                    "B": {"type": "uint", "modbus": "holding 1"},
                }
            }
        )


def test_bad_length_is_rejected() -> None:
    for bad in (0, -1, 2.5, True):
        with pytest.raises(ConfigError, match="length must be"):
            load_config({"tags": {"A": {"type": "string", "length": bad}}})


def test_logic_rule_with_string_constant() -> None:
    config = load_config(
        {
            "tags": {"Mode": {"type": "int"}, "Recipe.Name": {"type": "string", "value": "Old"}},
            "logic": ['Recipe.Name = "Clean" if Mode == 2 else Recipe.Name'],
        }
    )
    assert config.logic[0].target == "Recipe.Name"
    assert config.logic[0].expression.names == {"Mode", "Recipe.Name"}
