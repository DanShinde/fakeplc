from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

pytest_plugins = ["pytester"]

EXAMPLE = Path(__file__).resolve().parent.parent / "examples" / "line1.yaml"


def base_config(**overrides: Any) -> dict[str, Any]:
    config: dict[str, Any] = {
        "plc": "test",
        "scan_ms": 100,
        "opcua": {"port": 0},
        "modbus": {"port": 0},
        "tags": {
            "Motor.Start": {"type": "bool", "modbus": "coil 0"},
            "Motor.Fault": {"type": "bool", "modbus": "coil 1"},
            "Motor.Running": {"type": "bool", "modbus": "discrete 0"},
            "Tank.Level": {"type": "real", "modbus": "holding 0", "value": 42.5},
            "Pump.Setpoint": {"type": "real", "modbus": "holding 2"},
            "Batch.Count": {"type": "dint", "modbus": "holding 4"},
            "Mode": {"type": "int", "modbus": "holding 6", "value": -3},
            "Energy": {"type": "lreal", "modbus": "input 0", "value": 1234.5678},
            "Status": {"type": "uint", "modbus": "input 4", "readonly": True, "value": 7},
        },
        "logic": [
            "Motor.Running = Motor.Start and not Motor.Fault",
            "Batch.Count = Batch.Count + 1 if Motor.Running else Batch.Count",
        ],
    }
    for key, value in overrides.items():
        config[key] = value
    return config


@pytest.fixture
def example_path() -> Path:
    return EXAMPLE
