from __future__ import annotations

import threading
import time
from pathlib import Path

import pytest

from fakeplc import ExpressionError, FakePLC, UnknownTagError

from .conftest import base_config


def modbus_only(**overrides: object) -> dict[str, object]:
    return base_config(opcua=False, **overrides)


def test_logic_runs_on_scan() -> None:
    with FakePLC(modbus_only(), autoscan=False) as plc:
        plc.set("Motor.Start", True)
        assert plc.get("Motor.Running") is False
        plc.scan()
        assert plc.get("Motor.Running") is True
        plc.scan(4)
        assert plc.get("Batch.Count") == 5
        plc.set("Motor.Fault", True)
        plc.scan()
        assert plc.get("Motor.Running") is False
        assert plc.get("Batch.Count") == 5
        assert plc.scans == 6
        assert plc.time == pytest.approx(0.6)


def test_sim_signals_follow_simulated_time() -> None:
    config = modbus_only(
        tags={
            "Ramp": {"type": "real", "sim": "ramp(0, 100, 10)"},
            "Noise": {"type": "real", "sim": "noise(20, 1)"},
            "Count": {"type": "dint", "sim": "counter()"},
        },
        logic=[],
    )
    with FakePLC(config, autoscan=False) as plc:
        plc.scan(25)
        assert plc.get("Ramp") == pytest.approx(25.0)
        assert plc.get("Count") == 2
        first = plc.get("Noise")
        plc.reset()
        plc.scan(25)
        assert plc.get("Noise") == first


def test_autoscan_runs_by_itself(example_path: Path) -> None:
    with FakePLC(example_path, opcua_port=0, modbus_port=0) as plc:
        plc.set("Motor.Start", True)
        plc.wait_for("Motor.Speed", 1500.0, timeout=10)
        assert plc.get("Batch.Count") > 5


def test_set_converts_and_validates() -> None:
    with FakePLC(modbus_only(), autoscan=False) as plc:
        assert plc.set("Mode", 4.0) == 4
        with pytest.raises(ValueError):
            plc.set("Mode", 100000)
        with pytest.raises(UnknownTagError):
            plc.set("Nope", 1)
        with pytest.raises(UnknownTagError):
            plc.get("Nope")
        plc.set_many({"Tank.Level": 1, "Pump.Setpoint": 2})
        assert plc.values()["Pump.Setpoint"] == 2.0
        assert plc.journal() == []  # set() is not a client write


def test_wait_for_value_and_timeout() -> None:
    with FakePLC(modbus_only(), autoscan=False) as plc:
        threading.Timer(0.1, lambda: plc.set("Tank.Level", 80)).start()
        assert plc.wait_for("Tank.Level", lambda v: v > 50, timeout=5) == 80.0
        start = time.monotonic()
        with pytest.raises(TimeoutError, match="Pump.Setpoint"):
            plc.wait_for("Pump.Setpoint", 1.0, timeout=0.2)
        assert time.monotonic() - start < 2
        with pytest.raises(TimeoutError, match="client write"):
            plc.wait_for("Pump.Setpoint", timeout=0.1, written=True)


def test_runtime_logic_error_is_reported() -> None:
    config = modbus_only(
        tags={"A": {"type": "int"}, "B": {"type": "int"}},
        logic=["B = 10 / A"],
    )
    with FakePLC(config, autoscan=False) as plc, pytest.raises(ExpressionError, match="B = 10 / A"):
        plc.scan()


def test_calls_before_start_fail_clearly() -> None:
    plc = FakePLC(modbus_only())
    with pytest.raises(RuntimeError, match="start"):
        plc.scan()
    assert "stopped" in repr(plc)


def test_restartable_and_ports_free() -> None:
    plc = FakePLC(modbus_only())
    plc.start()
    port = plc.modbus_port
    plc.stop()
    plc2 = FakePLC(modbus_only(modbus={"port": port}))
    plc2.start()
    plc2.stop()
