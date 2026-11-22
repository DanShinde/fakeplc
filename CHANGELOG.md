# Changelog

## Unreleased

### Added
- `string` tag type: ASCII text with a configurable `length` (default 32, required on
  Modbus), stored as two bytes per register (`ceil(length / 2)` registers, NUL padded),
  exposed as `ua.VariantType.String` over OPC UA, and allowed as string constants in
  `sim`/`logic` expressions.

## 0.1.0 (2026-11-22)

First release.

- YAML tag table with `bool`, `int`, `uint`, `dint`, `udint`, `real` and `lreal` tags
- OPC UA server (asyncua) with folders from dotted tag names
- Built-in Modbus TCP server: function codes 1 to 6, 15 and 16, configurable word order
- Scan engine with simulated signals and one-line logic rules
- Write journal, `wait_for`, `scan`, `reset`
- pytest plugin with `fake_plc` and `fake_plc_factory` fixtures
- `fakeplc serve` and `fakeplc check` commands
