from __future__ import annotations

import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest
from pymodbus.client import ModbusTcpClient

from fakeplc.cli import main


def test_check_prints_tags(example_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["check", str(example_path)]) == 0
    out = capsys.readouterr().out
    assert "OK, PLC 'line1', 8 tags, 3 logic rules" in out
    assert "Pump.Setpoint  real  holding 2" in out


def test_check_reports_errors(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    bad = tmp_path / "bad.yaml"
    bad.write_text("tags:\n  A: {type: bool, modbus: holding 1}\n")
    assert main(["check", str(bad)]) == 2
    assert "bool tags go on a coil" in capsys.readouterr().err


@pytest.mark.skipif(sys.platform == "win32", reason="uses SIGINT")
def test_serve_runs_until_interrupted(example_path: Path) -> None:
    proc = subprocess.Popen(
        [sys.executable, "-m", "fakeplc", "serve", str(example_path), "--opcua-port", "0", "--modbus-port", "0"],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    try:
        assert proc.stdout is not None
        lines = []
        port = None
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            line = proc.stdout.readline()
            lines.append(line)
            if "Modbus TCP" in line:
                port = int(line.split()[2].rsplit(":", 1)[1])
            if "Ctrl+C" in line:
                break
        assert port, "".join(lines)
        client = ModbusTcpClient("127.0.0.1", port=port)
        client.connect()
        client.write_coil(0, True)
        client.close()
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            line = proc.stdout.readline()
            if "wrote Motor.Start = True" in line:
                break
        else:
            pytest.fail("write was not printed")
    finally:
        proc.send_signal(signal.SIGINT)
        assert proc.wait(timeout=20) == 0
