"""A tiny FastAPI app on top of the fake PLC.

`GET /tank` reads ``Tank.Level`` over OPC UA (asyncua); `POST /pump`` writes
``Pump.Setpoint`` over Modbus (pymodbus). The PLC addresses come from the
environment so tests can point the app at the fake PLC's free ports:

    PLC_OPCUA_URL=opc.tcp://localhost:4840 PLC_MODBUS_HOST=localhost PLC_MODBUS_PORT=5020 \
        uvicorn app:app

Tested against the fake PLC in test_app.py (see the README "In pytest" section).
"""

import asyncio
import os
from typing import Any

from asyncua import Client
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from pymodbus.client import ModbusTcpClient


class Settings:
    """PLC addresses, from the environment; tests override them per test."""

    def __init__(self) -> None:
        self.opcua_url = os.environ.get("PLC_OPCUA_URL", "opc.tcp://localhost:4840")
        self.modbus_host = os.environ.get("PLC_MODBUS_HOST", "localhost")
        self.modbus_port = int(os.environ.get("PLC_MODBUS_PORT", "5020"))


settings = Settings()

# line1.yaml maps Pump.Setpoint to holding register 2 (float32 = two registers).
PUMP_SETPOINT_REGISTER = 2
TANK_LEVEL_NODE = "Tank.Level"


class PumpCommand(BaseModel):
    litres_per_minute: float


async def _read_tank_level() -> float:
    async with Client(settings.opcua_url) as client:
        index = await client.get_namespace_index("urn:fakeplc")
        value: Any = await client.get_node(f"ns={index};s={TANK_LEVEL_NODE}").read_value()
        return float(value)


def read_tank_level() -> float:
    """Read Tank.Level over OPC UA."""
    return asyncio.run(_read_tank_level())


def write_pump_setpoint(litres_per_minute: float) -> None:
    """Write Pump.Setpoint over Modbus TCP as a float32 in two registers."""
    client = ModbusTcpClient(settings.modbus_host, port=settings.modbus_port)
    if not client.connect():
        raise RuntimeError(f"cannot connect to Modbus PLC at {settings.modbus_host}:{settings.modbus_port}")
    try:
        registers = client.convert_to_registers(litres_per_minute, data_type=client.DATATYPE.FLOAT32)
        client.write_registers(PUMP_SETPOINT_REGISTER, registers)
    finally:
        client.close()


app = FastAPI(title="fakeplc dashboard")


@app.get("/tank")
def tank() -> dict[str, float]:
    """The current tank level, read live from the PLC."""
    try:
        return {"level": read_tank_level()}
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"PLC read failed: {exc}") from exc


@app.post("/pump", status_code=202)
def pump(command: PumpCommand) -> dict[str, float]:
    """Set the pump setpoint; the PLC journals the write."""
    try:
        write_pump_setpoint(command.litres_per_minute)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"PLC write failed: {exc}") from exc
    return {"setpoint": command.litres_per_minute}
