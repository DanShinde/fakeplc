from __future__ import annotations

from collections.abc import Iterator

import pytest
from pymodbus.client import ModbusTcpClient

from fakeplc import FakePLC

from .conftest import base_config


@pytest.fixture(scope="module")
def plc() -> Iterator[FakePLC]:
    config = base_config(opcua=False)
    with FakePLC(config, autoscan=False) as plc:
        yield plc


@pytest.fixture
def client(plc: FakePLC) -> Iterator[ModbusTcpClient]:
    plc.reset()
    client = ModbusTcpClient(plc.modbus_host, port=plc.modbus_port)
    assert client.connect()
    yield client
    client.close()


def f32(client: ModbusTcpClient, registers: list[int], word_order: str = "big") -> float:
    return client.convert_from_registers(registers, data_type=client.DATATYPE.FLOAT32, word_order=word_order)


def test_read_holding_float(client: ModbusTcpClient) -> None:
    result = client.read_holding_registers(0, count=2)
    assert not result.isError()
    assert f32(client, result.registers) == 42.5


def test_read_mixed_block(client: ModbusTcpClient) -> None:
    result = client.read_holding_registers(0, count=7)
    regs = result.registers
    assert f32(client, regs[0:2]) == 42.5
    assert client.convert_from_registers(regs[6:7], data_type=client.DATATYPE.INT16) == -3


def test_read_input_lreal_and_uint(client: ModbusTcpClient) -> None:
    regs = client.read_input_registers(0, count=5).registers
    assert client.convert_from_registers(regs[0:4], data_type=client.DATATYPE.FLOAT64) == 1234.5678
    assert regs[4] == 7


def test_write_float_is_journaled(plc: FakePLC, client: ModbusTcpClient) -> None:
    regs = client.convert_to_registers(55.25, data_type=client.DATATYPE.FLOAT32)
    assert not client.write_registers(2, regs).isError()
    assert plc.get("Pump.Setpoint") == 55.25
    assert plc.writes("Pump.Setpoint") == [55.25]
    write = plc.journal("Pump.Setpoint")[0]
    assert write.protocol == "modbus"


def test_multi_register_write_spans_tags(plc: FakePLC, client: ModbusTcpClient) -> None:
    regs = client.convert_to_registers(10.0, data_type=client.DATATYPE.FLOAT32)
    regs += client.convert_to_registers(20.0, data_type=client.DATATYPE.FLOAT32)
    assert not client.write_registers(0, regs).isError()
    assert plc.get("Tank.Level") == 10.0
    assert plc.get("Pump.Setpoint") == 20.0
    assert [w.tag for w in plc.journal()] == ["Tank.Level", "Pump.Setpoint"]


def test_single_register_write_updates_half_of_a_float(plc: FakePLC, client: ModbusTcpClient) -> None:
    high, low = client.convert_to_registers(3.0, data_type=client.DATATYPE.FLOAT32)
    assert not client.write_register(2, high).isError()
    assert not client.write_register(3, low).isError()
    assert plc.get("Pump.Setpoint") == 3.0
    assert plc.writes("Pump.Setpoint")[-1] == 3.0


def test_coils(plc: FakePLC, client: ModbusTcpClient) -> None:
    assert client.read_coils(0, count=2).bits[:2] == [False, False]
    assert not client.write_coil(0, True).isError()
    assert plc.get("Motor.Start") is True
    assert not client.write_coils(0, [False, True]).isError()
    assert plc.values()["Motor.Start"] is False
    assert plc.get("Motor.Fault") is True
    assert plc.writes("Motor.Start") == [True, False]


def test_discrete_inputs_follow_logic(plc: FakePLC, client: ModbusTcpClient) -> None:
    client.write_coil(0, True)
    assert client.read_discrete_inputs(0, count=1).bits[0] is False
    plc.scan()
    assert client.read_discrete_inputs(0, count=1).bits[0] is True


def test_readonly_and_unmapped_writes_fail(plc: FakePLC, client: ModbusTcpClient) -> None:
    result = client.write_registers(4, [0, 1])  # Batch.Count is driven by logic
    assert result.isError()
    assert result.exception_code == 2
    assert client.write_register(100, 1).isError()
    assert client.read_holding_registers(100, count=1).isError()
    assert plc.journal() == []


def test_failed_write_changes_nothing(plc: FakePLC, client: ModbusTcpClient) -> None:
    regs = client.convert_to_registers(99.0, data_type=client.DATATYPE.FLOAT32)
    # Tank.Level and Pump.Setpoint are writable, Batch.Count (4) is not
    assert client.write_registers(2, [*regs, 0]).isError()
    assert plc.get("Pump.Setpoint") == 0.0


def test_unmapped_zero_mode() -> None:
    config = base_config(opcua=False, modbus={"port": 0, "unmapped": "zero"})
    with FakePLC(config) as plc:
        client = ModbusTcpClient("127.0.0.1", port=plc.modbus_port)
        client.connect()
        try:
            regs = client.read_holding_registers(6, count=4).registers
            assert regs[1:] == [0, 0, 0]
        finally:
            client.close()


def test_little_word_order() -> None:
    config = base_config(opcua=False, modbus={"port": 0, "word_order": "little"})
    with FakePLC(config) as plc:
        client = ModbusTcpClient("127.0.0.1", port=plc.modbus_port)
        client.connect()
        try:
            regs = client.read_holding_registers(0, count=2).registers
            assert f32(client, regs, word_order="little") == 42.5
        finally:
            client.close()


def _string_config() -> dict:
    config = base_config()
    config["tags"]["Recipe.Name"] = {"type": "string", "length": 20, "modbus": "holding 8", "value": "Default"}
    return config


@pytest.fixture(scope="module")
def string_plc() -> Iterator[FakePLC]:
    with FakePLC(_string_config(), autoscan=False) as plc:
        yield plc


def test_string_default_served_on_modbus(string_plc: FakePLC) -> None:
    string_plc.reset()
    client = ModbusTcpClient("127.0.0.1", port=string_plc.modbus_port)
    assert client.connect()
    try:
        regs = client.read_holding_registers(8, count=10).registers  # ceil(20/2) registers
        # "Default" = 44 65 66 61 75 6C 74, NUL padded to 20 chars
        assert regs == [0x4465, 0x6661, 0x756C, 0x7400] + [0] * 6
    finally:
        client.close()


def test_modbus_string_write_read_back_over_opcua(string_plc: FakePLC) -> None:
    import asyncio

    from asyncua import Client

    string_plc.reset()
    client = ModbusTcpClient("127.0.0.1", port=string_plc.modbus_port)
    assert client.connect()
    try:
        assert not client.write_registers(8, [0x4142, 0x4300] + [0] * 8).isError()  # "ABC"
    finally:
        client.close()
    assert string_plc.get("Recipe.Name") == "ABC"
    assert string_plc.writes("Recipe.Name") == ["ABC"]
    assert string_plc.journal("Recipe.Name")[0].protocol == "modbus"

    async def read_name() -> object:
        async with Client(string_plc.opcua_url) as ua_client:
            return await ua_client.get_node(string_plc.node_id("Recipe.Name")).read_value()

    assert asyncio.run(read_name()) == "ABC"


def test_opcua_string_write_visible_over_modbus(string_plc: FakePLC) -> None:
    import asyncio

    from asyncua import Client, ua

    string_plc.reset()

    async def write_name() -> None:
        async with Client(string_plc.opcua_url) as ua_client:
            node = ua_client.get_node(string_plc.node_id("Recipe.Name"))
            await node.write_value(ua.DataValue(ua.Variant("Clean", ua.VariantType.String)))

    asyncio.run(write_name())
    assert string_plc.get("Recipe.Name") == "Clean"
    client = ModbusTcpClient("127.0.0.1", port=string_plc.modbus_port)
    client.connect()
    try:
        regs = client.read_holding_registers(8, count=3).registers
        assert regs == [0x436C, 0x6561, 0x6E00]  # "Cle" "an\0"
    finally:
        client.close()
