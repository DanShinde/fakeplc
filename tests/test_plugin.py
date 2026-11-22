from __future__ import annotations

from pathlib import Path

import pytest

PLC_YAML = """
plc: demo
tags:
  Start: {type: bool, modbus: coil 0}
  Running: {type: bool, modbus: discrete 0}
  Level: {type: real, modbus: holding 0, value: 10}
logic:
  - Running = Start
"""


def test_marker_fixture_and_reset(pytester: pytest.Pytester) -> None:
    pytester.makefile(".yaml", plc=PLC_YAML)
    pytester.makepyfile(
        """
        import pytest
        from pymodbus.client import ModbusTcpClient

        pytestmark = pytest.mark.fakeplc("plc.yaml")

        def test_first(fake_plc):
            client = ModbusTcpClient("127.0.0.1", port=fake_plc.modbus_port)
            client.connect()
            client.write_coil(0, True)
            client.close()
            assert fake_plc.writes("Start") == [True]
            fake_plc.scan()
            assert fake_plc.get("Running") is True
            fake_plc.set("Level", 99)

        def test_second_is_reset(fake_plc):
            assert fake_plc.get("Level") == 10.0
            assert fake_plc.get("Running") is False
            assert fake_plc.journal() == []
            assert fake_plc.scans == 0

        def test_same_instance(fake_plc, request):
            assert fake_plc.opcua_url.startswith("opc.tcp://127.0.0.1:")
            assert fake_plc.opcua_port != 4840
        """
    )
    result = pytester.runpytest("-p", "no:cacheprovider")
    result.assert_outcomes(passed=3)


def test_ini_option(pytester: pytest.Pytester) -> None:
    pytester.makefile(".yaml", plc=PLC_YAML)
    pytester.makeini("[pytest]\nfakeplc_config = plc.yaml\n")
    pytester.makepyfile(
        """
        def test_it(fake_plc):
            assert fake_plc.name == "demo"
        """
    )
    pytester.runpytest().assert_outcomes(passed=1)


def test_command_line_option(pytester: pytest.Pytester) -> None:
    pytester.makefile(".yaml", other=PLC_YAML.replace("demo", "other"))
    pytester.makepyfile(
        """
        def test_it(fake_plc):
            assert fake_plc.name == "other"
        """
    )
    pytester.runpytest("--fakeplc", "other.yaml").assert_outcomes(passed=1)


def test_missing_config_is_a_clear_error(pytester: pytest.Pytester) -> None:
    pytester.makepyfile(
        """
        def test_it(fake_plc):
            pass
        """
    )
    result = pytester.runpytest()
    result.assert_outcomes(errors=1)
    result.stdout.fnmatch_lines(["*fake_plc needs a configuration*"])


def test_factory_with_dict(pytester: pytest.Pytester) -> None:
    pytester.makepyfile(
        """
        def test_two(fake_plc_factory):
            a = fake_plc_factory({"plc": "a", "opcua": False, "tags": {"X": "int"}})
            b = fake_plc_factory({"plc": "b", "opcua": False, "tags": {"X": "int"}})
            a.set("X", 1)
            assert b.get("X") == 0
            assert a.modbus_port != b.modbus_port
        """
    )
    pytester.runpytest().assert_outcomes(passed=1)


def test_dict_marker_and_autoscan(pytester: pytest.Pytester) -> None:
    pytester.makepyfile(
        """
        import pytest

        CONFIG = {"opcua": False, "scan_ms": 10, "tags": {"N": {"type": "dint", "sim": "counter()"}}}

        @pytest.mark.fakeplc(CONFIG, autoscan=True)
        def test_it(fake_plc):
            fake_plc.wait_for("N", lambda n: n >= 1, timeout=5)

        @pytest.mark.fakeplc(CONFIG, colour="red")
        def test_bad_option(fake_plc):
            pass
        """
    )
    result = pytester.runpytest()
    result.assert_outcomes(passed=1, errors=1)


def test_example_suite_passes(pytester: pytest.Pytester) -> None:
    root = Path(__file__).resolve().parent.parent / "examples"
    pytester.makefile(".yaml", line1=(root / "line1.yaml").read_text())
    pytester.makepyfile(test_example=(root / "test_line1.py").read_text())
    pytester.runpytest().assert_outcomes(passed=4)
