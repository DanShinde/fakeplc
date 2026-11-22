"""Tests for the FastAPI example, run against the fake PLC over real protocols."""

import pytest
from app import app, settings
from fastapi.testclient import TestClient

pytestmark = pytest.mark.fakeplc("../line1.yaml")


@pytest.fixture(autouse=True)
def point_app_at_fake_plc(fake_plc):
    """Give the app the fake PLC's addresses, like the Django settings example."""
    settings.opcua_url = fake_plc.opcua_url
    settings.modbus_host = fake_plc.modbus_host
    settings.modbus_port = fake_plc.modbus_port


def test_post_pump_writes_the_setpoint(fake_plc):
    with TestClient(app) as client:
        response = client.post("/pump", json={"litres_per_minute": 12.5})
    assert response.status_code == 202
    assert response.json() == {"setpoint": 12.5}
    assert fake_plc.writes("Pump.Setpoint") == [12.5]


def test_get_tank_reads_the_level(fake_plc):
    fake_plc.set("Tank.Level", 87.5)
    with TestClient(app) as client:
        response = client.get("/tank")
    assert response.status_code == 200
    assert response.json() == {"level": 87.5}


def test_post_pump_rejects_a_missing_field(fake_plc):
    with TestClient(app) as client:
        response = client.post("/pump", json={})
    assert response.status_code == 422
    assert fake_plc.writes("Pump.Setpoint") == []
