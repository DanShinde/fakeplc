"""Command line: ``fakeplc serve plc.yaml`` and ``fakeplc check plc.yaml``."""

from __future__ import annotations

import argparse
import logging
import signal
import sys
import threading
from collections.abc import Sequence

from . import __version__
from .config import ConfigError, PLCConfig, load_config
from .expr import ExpressionError

__all__ = ["main"]


def _table(config: PLCConfig) -> str:
    rows = [("TAG", "TYPE", "MODBUS", "ACCESS", "START", "SOURCE")]
    driven = {rule.target for rule in config.logic}
    for name, spec in config.tags.items():
        if spec.sim is not None:
            origin = f"sim: {spec.sim.source}"
        elif name in driven:
            origin = "logic"
        else:
            origin = ""
        rows.append(
            (
                name,
                spec.type,
                str(spec.modbus) if spec.modbus else "-",
                "read" if spec.readonly else "read/write",
                repr(spec.value),
                origin,
            )
        )
    widths = [max(len(row[i]) for row in rows) for i in range(len(rows[0]) - 1)]
    lines = []
    for row in rows:
        cells = [cell.ljust(width) for cell, width in zip(row, widths)]
        lines.append("  ".join([*cells, row[-1]]).rstrip())
    return "\n".join(lines)


def _check(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    print(f"{args.config}: OK, PLC {config.name!r}, {len(config.tags)} tags, {len(config.logic)} logic rules")
    print()
    print(_table(config))
    return 0


def _serve(args: argparse.Namespace) -> int:
    from .plc import FakePLC

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.WARNING, format="%(levelname)s %(name)s: %(message)s"
    )
    plc = FakePLC(
        args.config,
        host=args.host,
        opcua_port=args.opcua_port,
        modbus_port=args.modbus_port,
        autoscan=False if args.no_scan else None,
    )
    if args.no_opcua:
        plc.config.opcua.enabled = False
    if args.no_modbus:
        plc.config.modbus.enabled = False
    plc.start()
    cfg = plc.config
    print(f"fakeplc {__version__}: PLC {cfg.name!r} with {len(cfg.tags)} tags")
    if cfg.opcua.enabled:
        print(f"  OPC UA      {plc.opcua_url}   node ids ns={plc.opcua_namespace_index};s=<tag>")
    if cfg.modbus.enabled:
        print(f"  Modbus TCP  {plc.modbus_host}:{plc.modbus_port}   word order {cfg.modbus.word_order}")
    scan = f"every {cfg.scan_ms} ms" if cfg.autoscan else "off (--no-scan)"
    print(f"  Scan        {scan}")
    print()
    print(_table(cfg))
    print()
    print("Press Ctrl+C to stop.")
    sys.stdout.flush()

    stop = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    last = len(plc.journal())
    try:
        while not stop.wait(0.2):
            journal = plc.journal()
            for write in journal[last:]:
                print(
                    f"{write.timestamp:%H:%M:%S.%f}"[:-3] + f"  {write.protocol:<6} wrote {write.tag} = {write.value!r}"
                )
                sys.stdout.flush()
            last = len(journal)
    except KeyboardInterrupt:
        pass
    finally:
        plc.stop()
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="fakeplc", description="A fake PLC over OPC UA and Modbus TCP.")
    parser.add_argument("--version", action="version", version=f"fakeplc {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    serve = sub.add_parser("serve", help="run the PLC until Ctrl+C")
    serve.add_argument("config", help="YAML file")
    serve.add_argument("--host", help="address to listen on (default from the file, else 127.0.0.1)")
    serve.add_argument("--opcua-port", type=int, help="OPC UA port, 0 for a free one")
    serve.add_argument("--modbus-port", type=int, help="Modbus TCP port, 0 for a free one")
    serve.add_argument("--no-opcua", action="store_true", help="do not start OPC UA")
    serve.add_argument("--no-modbus", action="store_true", help="do not start Modbus TCP")
    serve.add_argument("--no-scan", action="store_true", help="do not run scans (values only change by writes)")
    serve.add_argument("-v", "--verbose", action="store_true", help="debug logging")
    serve.set_defaults(func=_serve)

    check = sub.add_parser("check", help="validate a YAML file and print its tags")
    check.add_argument("config", help="YAML file")
    check.set_defaults(func=_check)

    args = parser.parse_args(argv)
    try:
        return int(args.func(args))
    except (ConfigError, ExpressionError) as exc:
        print(f"fakeplc: {exc}", file=sys.stderr)
        return 2
    except OSError as exc:
        print(f"fakeplc: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
