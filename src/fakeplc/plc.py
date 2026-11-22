"""The :class:`FakePLC`: tag table, scan engine and protocol servers."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import random
import socket
import threading
from collections.abc import Callable, Coroutine
from pathlib import Path
from typing import Any, TypeVar

from .config import PLCConfig, load_config
from .expr import Context, ExpressionError
from .modbus import ModbusServer
from .tags import TagTable, Write

__all__ = ["FakePLC"]

T = TypeVar("T")

log = logging.getLogger(__name__)

_MISSING = object()


def _free_port(host: str) -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind((host if host not in ("", "0.0.0.0") else "127.0.0.1", 0))
        return int(sock.getsockname()[1])


class FakePLC:
    """A simulated PLC that serves one tag table over OPC UA and Modbus TCP.

    ``config`` is a path to a YAML file, a YAML string, a dict, or a
    :class:`~fakeplc.config.PLCConfig`. The servers run on a background
    thread, so the methods below can be called from ordinary sync code::

        with FakePLC("line1.yaml") as plc:
            plc.set("Tank.Level", 80.0)
            plc.scan(10)
            assert plc.writes("Pump.Setpoint") == []
    """

    def __init__(
        self,
        config: str | Path | dict[str, Any] | PLCConfig,
        *,
        autoscan: bool | None = None,
        opcua_port: int | None = None,
        modbus_port: int | None = None,
        host: str | None = None,
    ) -> None:
        self.config = load_config(config)
        if autoscan is not None:
            self.config.autoscan = autoscan
        if opcua_port is not None:
            self.config.opcua.port = opcua_port
        if modbus_port is not None:
            self.config.modbus.port = modbus_port
        if host is not None:
            self.config.opcua.host = host
            self.config.modbus.host = host
        self.table = TagTable(self.config)
        self._rng = random.Random(self.config.seed)
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._scan_task: asyncio.Task[None] | None = None
        self._opcua: Any = None
        self._modbus: ModbusServer | None = None
        self._scan_lock: asyncio.Lock | None = None
        self.running = False

    # lifecycle -----------------------------------------------------------

    def start(self) -> FakePLC:
        """Start the servers on a background thread and return ``self``."""
        if self.running:
            return self
        ready = threading.Event()

        def run() -> None:
            loop = asyncio.new_event_loop()
            self._loop = loop
            asyncio.set_event_loop(loop)
            ready.set()
            loop.run_forever()
            loop.run_until_complete(loop.shutdown_asyncgens())
            loop.close()

        self._thread = threading.Thread(target=run, name=f"fakeplc-{self.config.name}", daemon=True)
        self._thread.start()
        ready.wait()
        try:
            self._call(self._astart(), timeout=60)
        except BaseException:
            self._shutdown_loop()
            raise
        self.running = True
        return self

    def stop(self) -> None:
        """Stop the servers and the background thread."""
        if self._loop is None:
            return
        try:
            self._call(self._astop(), timeout=30)
        finally:
            self._shutdown_loop()
            self.running = False

    def __enter__(self) -> FakePLC:
        return self.start()

    def __exit__(self, *exc: object) -> None:
        self.stop()

    def __repr__(self) -> str:
        state = "running" if self.running else "stopped"
        return f"<FakePLC {self.config.name!r} {state}, {len(self.config.tags)} tags>"

    def _shutdown_loop(self) -> None:
        loop, thread = self._loop, self._thread
        if loop is not None:
            loop.call_soon_threadsafe(loop.stop)
        if thread is not None:
            thread.join(timeout=10)
        self._loop = None
        self._thread = None

    def _call(self, coro: Coroutine[Any, Any, T], timeout: float = 30) -> T:
        if self._loop is None:
            coro.close()
            raise RuntimeError("the PLC is not running; call start() first")
        if threading.current_thread() is self._thread:
            coro.close()
            raise RuntimeError("cannot call a blocking FakePLC method from its own event loop")
        return asyncio.run_coroutine_threadsafe(coro, self._loop).result(timeout)

    async def _astart(self) -> None:
        self._scan_lock = asyncio.Lock()
        cfg = self.config
        if cfg.modbus.enabled:
            self._modbus = ModbusServer(self.table, cfg.modbus, on_change=self._flush)
            await self._modbus.start()
        if cfg.opcua.enabled:
            from .opcua import OpcUaServer

            port = cfg.opcua.port or _free_port(cfg.opcua.host)
            self._opcua = OpcUaServer(self.table, cfg.opcua, cfg.name)
            try:
                await self._opcua.start(port)
            except BaseException:
                if self._modbus is not None:
                    await self._modbus.stop()
                raise
        self.table.take_dirty()
        if cfg.autoscan:
            self._scan_task = asyncio.get_running_loop().create_task(self._autoscan())

    async def _astop(self) -> None:
        if self._scan_task is not None:
            self._scan_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._scan_task
            self._scan_task = None
        if self._modbus is not None:
            await self._modbus.stop()
            self._modbus = None
        if self._opcua is not None:
            await self._opcua.stop()
            self._opcua = None

    async def _flush(self) -> None:
        dirty = self.table.take_dirty()
        if self._opcua is not None and dirty:
            await self._opcua.push(dirty)

    # scan engine ---------------------------------------------------------

    async def _autoscan(self) -> None:
        period = self.config.scan_ms / 1000
        loop = asyncio.get_running_loop()
        next_run = loop.time() + period
        while True:
            await asyncio.sleep(max(0.0, next_run - loop.time()))
            next_run += period
            try:
                await self._ascan(1)
            except ExpressionError as exc:
                log.error("scan stopped: %s", exc)
                return

    async def _ascan(self, count: int) -> None:
        assert self._scan_lock is not None
        async with self._scan_lock:
            for _ in range(count):
                self._scan_once()
            await self._flush()

    def _scan_once(self) -> None:
        table = self.table
        table.scan_count += 1
        values = table.snapshot()
        ctx = Context(values, self.time, self._rng)
        for name, spec in table.specs.items():
            if spec.sim is not None:
                try:
                    values[name] = table.set(name, spec.sim.evaluate(ctx))
                except (ExpressionError, TypeError, ValueError) as exc:
                    raise ExpressionError(f"sim of {name}: {exc}") from None
        for rule in self.config.logic:
            try:
                values[rule.target] = table.set(rule.target, rule.expression.evaluate(ctx))
            except (ExpressionError, TypeError, ValueError) as exc:
                raise ExpressionError(f"logic rule '{rule}': {exc}") from None

    # public API ----------------------------------------------------------

    @property
    def name(self) -> str:
        return self.config.name

    @property
    def time(self) -> float:
        """Simulated time in seconds: scans done times the scan period."""
        return self.table.scan_count * self.config.scan_ms / 1000

    @property
    def scans(self) -> int:
        return self.table.scan_count

    @property
    def tags(self) -> list[str]:
        return list(self.config.tags)

    @property
    def opcua_url(self) -> str:
        """Endpoint URL such as ``opc.tcp://127.0.0.1:4840``."""
        if self._opcua is None:
            raise RuntimeError("OPC UA is not running")
        return str(self._opcua.url)

    @property
    def opcua_port(self) -> int:
        if self._opcua is None:
            raise RuntimeError("OPC UA is not running")
        return int(self._opcua.port)

    @property
    def opcua_namespace_index(self) -> int:
        if self._opcua is None:
            raise RuntimeError("OPC UA is not running")
        return int(self._opcua.namespace_index)

    def node_id(self, tag: str) -> str:
        """OPC UA node id of a tag, for example ``ns=2;s=Motor.Speed``."""
        self.table.spec(tag)
        if self._opcua is None:
            raise RuntimeError("OPC UA is not running")
        return str(self._opcua.node_id(tag))

    @property
    def modbus_host(self) -> str:
        return self.config.modbus.host

    @property
    def modbus_port(self) -> int:
        if self._modbus is None:
            raise RuntimeError("Modbus is not running")
        return self._modbus.port

    def get(self, tag: str) -> Any:
        """Current value of a tag."""
        return self.table.get(tag)

    def values(self) -> dict[str, Any]:
        """Current value of every tag."""
        return self.table.snapshot()

    def set(self, tag: str, value: Any) -> Any:
        """Force a tag to a value, as if the PLC program wrote it.

        Returns the stored value after type conversion. This is not
        recorded in the write journal; that holds client writes only.
        """
        self.table.spec(tag)
        return self._call(self._aset({tag: value}))[tag]

    def set_many(self, values: dict[str, Any]) -> None:
        self._call(self._aset(values))

    async def _aset(self, values: dict[str, Any]) -> dict[str, Any]:
        stored = {name: self.table.set(name, value) for name, value in values.items()}
        await self._flush()
        return stored

    def scan(self, count: int = 1) -> None:
        """Run ``count`` PLC scans now: sim signals first, then logic rules."""
        if count < 0:
            raise ValueError("count must not be negative")
        self._call(self._ascan(count))

    def reset(self) -> None:
        """Restore start values, clear the journal and restart simulated time."""
        self._call(self._areset())

    async def _areset(self) -> None:
        assert self._scan_lock is not None
        async with self._scan_lock:
            self.table.reset()
            self._rng.seed(self.config.seed)
            await self._flush()

    def journal(self, tag: str | None = None, protocol: str | None = None) -> list[Write]:
        """Client writes, oldest first, optionally for one tag or protocol."""
        return self.table.journal(tag, protocol)

    def writes(self, tag: str, protocol: str | None = None) -> list[Any]:
        """Values clients wrote to ``tag``, oldest first."""
        return [w.value for w in self.table.journal(tag, protocol)]

    def clear_writes(self) -> None:
        self.table.clear_journal()

    def wait_for(
        self,
        tag: str,
        expected: Any = _MISSING,
        timeout: float = 5.0,
        *,
        written: bool = False,
    ) -> Any:
        """Wait until ``tag`` has a value and return it.

        ``expected`` is a value or a predicate such as ``lambda v: v > 10``.
        With ``written=True`` the wait is for a client write to the tag (any
        value if ``expected`` is left out). Raises :class:`TimeoutError`.
        """
        self.table.spec(tag)
        check: Callable[[Any], bool]
        if expected is _MISSING:
            check = lambda _value: True  # noqa: E731
        elif callable(expected):
            check = expected
        else:
            check = lambda value: value == expected  # noqa: E731

        if written:

            def ready() -> bool:
                return any(check(w.value) for w in self.table.journal(tag))

        else:

            def ready() -> bool:
                return bool(check(self.table.get(tag)))

        if not self.table.wait(ready, timeout):
            what = "a client write to" if written else "a value for"
            want = "" if expected is _MISSING else f" matching {expected!r}"
            raise TimeoutError(
                f"no {what} {tag}{want} within {timeout}s; current value is {self.table.get(tag)!r}, "
                f"writes so far {self.writes(tag)!r}"
            )
        if written:
            return next(w.value for w in reversed(self.table.journal(tag)) if check(w.value))
        return self.table.get(tag)

    async def wait_for_async(self, tag: str, expected: Any = _MISSING, timeout: float = 5.0, **kwargs: Any) -> Any:
        """:meth:`wait_for` for async code; does not block the caller's event loop."""
        return await asyncio.to_thread(self.wait_for, tag, expected, timeout, **kwargs)
