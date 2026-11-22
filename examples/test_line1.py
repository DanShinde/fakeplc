"""Example tests against examples/line1.yaml.

The "app" here is a few small functions that talk to the PLC the way a
SCADA or Django app would: Modbus for commands, OPC UA for values.
"""

import asyncio

import pytest
from asyncua import Client
from pymodbus.client import ModbusTcpClient

pytestmark = pytest.mark.fakeplc("line1.yaml")


# --- the code under test -------------------------------------------------


def start_line(host: str, port: int) -> None:
    client = ModbusTcpClient(host, port=port)
    client.connect()
    try:
        client.write_coil(0, True)
    finally:
        client.close()


def set_pump(host: str, port: int, litres_per_minute: float) -> None:
    client = ModbusTcpClient(host, port=port)
    client.connect()
    try:
        regs = client.convert_to_registers(litres_per_minute, data_type=client.DATATYPE.FLOAT32)
        client.write_registers(2, regs)
    finally:
        client.close()


async def read_dashboard(url: str) -> dict[str, float]:
    async with Client(url) as client:
        idx = await client.get_namespace_index("urn:fakeplc")
        level = await client.get_node(f"ns={idx};s=Tank.Level").read_value()
        speed = await client.get_node(f"ns={idx};s=Motor.Speed").read_value()
        return {"level": level, "speed": speed}


# --- the tests -------------------------------------------------------------


def test_start_button_writes_the_start_coil(fake_plc):
    start_line(fake_plc.modbus_host, fake_plc.modbus_port)
    assert fake_plc.writes("Motor.Start") == [True]


def test_motor_ramps_up_after_start(fake_plc):
    start_line(fake_plc.modbus_host, fake_plc.modbus_port)
    fake_plc.scan(5)  # five PLC scans, no sleeping
    assert fake_plc.get("Motor.Running") is True
    assert fake_plc.get("Motor.Speed") == 750.0


def test_pump_setpoint_is_sent_as_float(fake_plc):
    set_pump(fake_plc.modbus_host, fake_plc.modbus_port, 12.5)
    assert fake_plc.writes("Pump.Setpoint") == [12.5]


def test_dashboard_reads_live_values(fake_plc):
    fake_plc.set("Tank.Level", 87.5)
    start_line(fake_plc.modbus_host, fake_plc.modbus_port)
    fake_plc.scan(10)
    assert asyncio.run(read_dashboard(fake_plc.opcua_url)) == {"level": 87.5, "speed": 1500.0}
