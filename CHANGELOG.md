# Changelog

## Unreleased

- New `examples/fastapi/` worked example: a small FastAPI app (`GET /tank` reads `Tank.Level` over
  OPC UA, `POST /pump` writes `Pump.Setpoint` over Modbus, addresses from env/settings), tested
  with `TestClient` and `@pytest.mark.fakeplc("../line1.yaml")`; CI runs `pytest examples/fastapi`.

## 0.1.0 (2026-11-22)

First release.

- YAML tag table with `bool`, `int`, `uint`, `dint`, `udint`, `real` and `lreal` tags
- OPC UA server (asyncua) with folders from dotted tag names
- Built-in Modbus TCP server: function codes 1 to 6, 15 and 16, configurable word order
- Scan engine with simulated signals and one-line logic rules
- Write journal, `wait_for`, `scan`, `reset`
- pytest plugin with `fake_plc` and `fake_plc_factory` fixtures
- `fakeplc serve` and `fakeplc check` commands
