# Changelog

## 0.1.0 (unreleased)

First release.

- YAML tag table with `bool`, `int`, `uint`, `dint`, `udint`, `real` and `lreal` tags
- OPC UA server (asyncua) with folders from dotted tag names
- Built-in Modbus TCP server: function codes 1 to 6, 15 and 16, configurable word order
- Scan engine with simulated signals and one-line logic rules
- Write journal, `wait_for`, `scan`, `reset`
- pytest plugin with `fake_plc` and `fake_plc_factory` fixtures
- `fakeplc serve` and `fakeplc check` commands
