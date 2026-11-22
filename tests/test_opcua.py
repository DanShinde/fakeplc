from __future__ import annotations

import asyncio
from collections.abc import Iterator
from typing import Any

import pytest
from asyncua import Client, ua
from pymodbus.client import ModbusTcpClient

from fakeplc import FakePLC

from .conftest import base_config


@pytest.fixture(scope="module")
def plc() -> Iterator[FakePLC]:
    with FakePLC(base_config(), autoscan=False) as plc:
        yield plc


@pytest.fixture(autouse=True)
def _reset(plc: FakePLC) -> None:
    plc.reset()


def run(coro: Any) -> Any:
    return asyncio.run(coro)


async def read(plc: FakePLC, *tags: str) -> list[Any]:
    async with Client(plc.opcua_url) as client:
        return [await client.get_node(plc.node_id(tag)).read_value() for tag in tags]


async def write(plc: FakePLC, tag: str, value: Any, variant: ua.VariantType) -> None:
    async with Client(plc.opcua_url) as client:
        await client.get_node(plc.node_id(tag)).write_value(ua.DataValue(ua.Variant(value, variant)))


def test_values_and_types(plc: FakePLC) -> None:
    values = run(read(plc, "Tank.Level", "Mode", "Energy", "Status", "Motor.Start"))
    assert values == [42.5, -3, 1234.5678, 7, False]


def test_browse_path_follows_dotted_names(plc: FakePLC) -> None:
    async def browse() -> float:
        async with Client(plc.opcua_url) as client:
            idx = await client.get_namespace_index("urn:fakeplc")
            node = await client.nodes.objects.get_child([f"{idx}:test", f"{idx}:Tank", f"{idx}:Level"])
            return float(await node.read_value())

    assert run(browse()) == 42.5


def test_client_write_is_journaled(plc: FakePLC) -> None:
    run(write(plc, "Pump.Setpoint", 12.5, ua.VariantType.Float))
    assert plc.get("Pump.Setpoint") == 12.5
    assert plc.writes("Pump.Setpoint") == [12.5]
    assert plc.journal()[0].protocol == "opcua"


def test_opcua_write_visible_over_modbus(plc: FakePLC) -> None:
    run(write(plc, "Pump.Setpoint", 7.75, ua.VariantType.Float))
    client = ModbusTcpClient("127.0.0.1", port=plc.modbus_port)
    client.connect()
    try:
        regs = client.read_holding_registers(2, count=2).registers
        assert client.convert_from_registers(regs, data_type=client.DATATYPE.FLOAT32) == 7.75
    finally:
        client.close()


def test_modbus_write_visible_over_opcua(plc: FakePLC) -> None:
    client = ModbusTcpClient("127.0.0.1", port=plc.modbus_port)
    client.connect()
    try:
        client.write_coil(0, True)
    finally:
        client.close()
    assert run(read(plc, "Motor.Start")) == [True]
    plc.scan(3)
    assert run(read(plc, "Motor.Running", "Batch.Count")) == [True, 3]


def test_set_and_reset_reach_opcua(plc: FakePLC) -> None:
    plc.set("Tank.Level", 99)
    assert run(read(plc, "Tank.Level")) == [99.0]
    plc.reset()
    assert run(read(plc, "Tank.Level")) == [42.5]
    assert plc.journal() == []


def test_readonly_tag_rejects_writes(plc: FakePLC) -> None:
    with pytest.raises(ua.UaStatusCodeError):
        run(write(plc, "Batch.Count", 5, ua.VariantType.Int32))
    assert plc.get("Batch.Count") == 0
    assert plc.journal() == []


def test_wrong_type_is_rejected(plc: FakePLC) -> None:
    with pytest.raises(ua.UaStatusCodeError):
        run(write(plc, "Mode", 1.5, ua.VariantType.Double))
    assert plc.get("Mode") == -3


def test_wait_for_written(plc: FakePLC) -> None:
    import threading

    threading.Timer(0.2, lambda: run(write(plc, "Pump.Setpoint", 30.0, ua.VariantType.Float))).start()
    assert plc.wait_for("Pump.Setpoint", 30.0, timeout=10, written=True) == 30.0


def test_node_id(plc: FakePLC) -> None:
    assert plc.node_id("Tank.Level") == f"ns={plc.opcua_namespace_index};s=Tank.Level"
