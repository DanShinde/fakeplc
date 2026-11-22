# Contributing to fakeplc

Thanks for helping. Bug reports, new examples and protocol work are all welcome.

## Set up

```bash
git clone https://github.com/DanShinde/fakeplc
cd fakeplc
python -m venv .venv && . .venv/bin/activate
pip install -e ".[test]" ruff mypy types-PyYAML
```

Python 3.10 or newer.

## Check your change

```bash
pytest                       # about 20 seconds; starts real OPC UA and Modbus servers
ruff check . && ruff format --check .
mypy                         # strict mode
```

CI runs the same commands on Linux (Python 3.10 to 3.14), Windows and macOS.

## Where things live

| File | What it does |
| --- | --- |
| `src/fakeplc/config.py` | Loads and validates the YAML file |
| `src/fakeplc/types.py` | Tag types and their Modbus register encoding |
| `src/fakeplc/expr.py` | The safe expression language for `sim` and `logic` |
| `src/fakeplc/tags.py` | The tag table and write journal |
| `src/fakeplc/modbus.py` | The Modbus TCP server |
| `src/fakeplc/opcua.py` | The OPC UA server (asyncua) |
| `src/fakeplc/plc.py` | `FakePLC`: lifecycle, scan engine, public API |
| `src/fakeplc/pytest_plugin.py` | The `fake_plc` fixtures |
| `src/fakeplc/cli.py` | `fakeplc serve` and `fakeplc check` |

## Guidelines

- Every change comes with a test. Protocol tests talk to the server through a
  real client (pymodbus or asyncua), not through internals.
- Keep the install light: new runtime dependencies need a good reason.
- Config errors should name the tag or rule and say how to fix it.
- Update `CHANGELOG.md` under an "Unreleased" heading.

## Proposing a new protocol

Open an issue first with the protocol, the client library you would test
against, and how tags map to its addresses (as `modbus: holding 4` does for
Modbus). A protocol is a server class like `ModbusServer`: it reads values
with `TagTable.snapshot()`, writes client values with
`TagTable.set(name, value, protocol="<name>")` so they reach the journal,
and asks the PLC to push changes to the other protocols through the
`on_change` callback.

## Releases

Maintainers bump `__version__` in `src/fakeplc/__init__.py`, update the
changelog, and publish a GitHub release; the release workflow uploads to PyPI.
