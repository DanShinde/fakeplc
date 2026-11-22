"""A small Modbus TCP server that reads and writes the tag table.

Supported function codes: 1, 2, 3, 4, 5, 6, 15 and 16. Every request is
checked completely before anything is written, so a multi-register write
either lands as a whole or not at all.
"""

from __future__ import annotations

import asyncio
import logging
import struct
from collections.abc import Awaitable, Callable

from .config import ModbusSpec
from .tags import TagTable
from .types import from_registers, to_registers

__all__ = ["ModbusServer"]

log = logging.getLogger(__name__)

ILLEGAL_FUNCTION = 0x01
ILLEGAL_ADDRESS = 0x02
ILLEGAL_VALUE = 0x03
DEVICE_FAILURE = 0x04

_READ_TABLE = {1: "coil", 2: "discrete", 3: "holding", 4: "input"}


class _ModbusError(Exception):
    def __init__(self, code: int) -> None:
        super().__init__(code)
        self.code = code


class ModbusServer:
    def __init__(
        self,
        table: TagTable,
        spec: ModbusSpec,
        on_change: Callable[[], Awaitable[None]] | None = None,
    ) -> None:
        self.table = table
        self.spec = spec
        self.on_change = on_change
        self._server: asyncio.base_events.Server | None = None
        self._clients: set[asyncio.StreamWriter] = set()
        self.port: int = spec.port
        # (table, address) -> (tag name, word index within the tag)
        self._map: dict[tuple[str, int], tuple[str, int]] = {}
        for name, tag in table.specs.items():
            if tag.modbus is None:
                continue
            for offset in range(tag.register_count):
                self._map[(tag.modbus.table, tag.modbus.address + offset)] = (name, offset)

    async def start(self) -> None:
        self._server = await asyncio.start_server(self._handle, self.spec.host, self.spec.port)
        self.port = self._server.sockets[0].getsockname()[1]
        log.info("Modbus TCP listening on %s:%s", self.spec.host, self.port)

    async def stop(self) -> None:
        if self._server is None:
            return
        self._server.close()
        for writer in list(self._clients):
            writer.close()
        await self._server.wait_closed()
        self._server = None

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        self._clients.add(writer)
        try:
            while True:
                header = await reader.readexactly(7)
                transaction, protocol, length, unit = struct.unpack(">HHHB", header)
                if length < 2 or length > 254:
                    break
                pdu = await reader.readexactly(length - 1)
                if protocol != 0:
                    continue
                response = await self._process(pdu)
                writer.write(struct.pack(">HHHB", transaction, 0, len(response) + 1, unit) + response)
                await writer.drain()
        except (asyncio.IncompleteReadError, ConnectionError):
            pass
        finally:
            self._clients.discard(writer)
            writer.close()

    async def _process(self, pdu: bytes) -> bytes:
        function = pdu[0]
        try:
            response, changed = self._dispatch(function, pdu[1:])
        except _ModbusError as exc:
            return bytes([function | 0x80, exc.code])
        except (struct.error, IndexError):
            return bytes([function | 0x80, ILLEGAL_VALUE])
        except Exception:
            log.exception("Modbus request failed")
            return bytes([function | 0x80, DEVICE_FAILURE])
        if changed and self.on_change is not None:
            await self.on_change()
        return response

    def _dispatch(self, function: int, data: bytes) -> tuple[bytes, bool]:
        if function in (1, 2):
            address, count = struct.unpack(">HH", data[:4])
            if not 1 <= count <= 2000:
                raise _ModbusError(ILLEGAL_VALUE)
            bits = self._read(_READ_TABLE[function], address, count)
            packed = bytearray((count + 7) // 8)
            for i, bit in enumerate(bits):
                if bit:
                    packed[i // 8] |= 1 << (i % 8)
            return bytes([function, len(packed)]) + bytes(packed), False
        if function in (3, 4):
            address, count = struct.unpack(">HH", data[:4])
            if not 1 <= count <= 125:
                raise _ModbusError(ILLEGAL_VALUE)
            registers = self._read(_READ_TABLE[function], address, count)
            return bytes([function, count * 2]) + struct.pack(f">{count}H", *registers), False
        if function == 5:
            address, raw = struct.unpack(">HH", data[:4])
            if raw not in (0x0000, 0xFF00):
                raise _ModbusError(ILLEGAL_VALUE)
            self._write("coil", address, [raw == 0xFF00])
            return bytes([function]) + data[:4], True
        if function == 6:
            address, value = struct.unpack(">HH", data[:4])
            self._write("holding", address, [value])
            return bytes([function]) + data[:4], True
        if function == 15:
            address, count, byte_count = struct.unpack(">HHB", data[:5])
            if not 1 <= count <= 1968 or byte_count != (count + 7) // 8 or len(data) < 5 + byte_count:
                raise _ModbusError(ILLEGAL_VALUE)
            payload = data[5 : 5 + byte_count]
            bits = [bool(payload[i // 8] >> (i % 8) & 1) for i in range(count)]
            self._write("coil", address, bits)
            return struct.pack(">BHH", function, address, count), True
        if function == 16:
            address, count, byte_count = struct.unpack(">HHB", data[:5])
            if not 1 <= count <= 123 or byte_count != count * 2 or len(data) < 5 + byte_count:
                raise _ModbusError(ILLEGAL_VALUE)
            registers = list(struct.unpack(f">{count}H", data[5 : 5 + byte_count]))
            self._write("holding", address, registers)
            return struct.pack(">BHH", function, address, count), True
        raise _ModbusError(ILLEGAL_FUNCTION)

    def _read(self, table: str, address: int, count: int) -> list[int]:
        if address + count > 65536:
            raise _ModbusError(ILLEGAL_ADDRESS)
        values = self.table.snapshot()
        out: list[int] = []
        cache: dict[str, list[int]] = {}
        word_order = self.spec.word_order
        for addr in range(address, address + count):
            entry = self._map.get((table, addr))
            if entry is None:
                if self.spec.unmapped == "error":
                    raise _ModbusError(ILLEGAL_ADDRESS)
                out.append(0)
                continue
            name, offset = entry
            tag = self.table.specs[name]
            if tag.type == "bool":
                out.append(1 if values[name] else 0)
                continue
            if name not in cache:
                cache[name] = to_registers(tag.type, values[name], word_order)
            out.append(cache[name][offset])
        return out

    def _write(self, table: str, address: int, values: list[int] | list[bool]) -> None:
        if address + len(values) > 65536:
            raise _ModbusError(ILLEGAL_ADDRESS)
        updates: dict[str, dict[int, int]] = {}
        for i, value in enumerate(values):
            entry = self._map.get((table, address + i))
            if entry is None:
                raise _ModbusError(ILLEGAL_ADDRESS)
            name, offset = entry
            if self.table.specs[name].readonly:
                raise _ModbusError(ILLEGAL_ADDRESS)
            updates.setdefault(name, {})[offset] = int(value)
        current = self.table.snapshot()
        new_values = {}
        for name, words in updates.items():
            tag = self.table.specs[name]
            if tag.type == "bool":
                new_values[name] = bool(words[0])
                continue
            registers = to_registers(tag.type, current[name], self.spec.word_order)
            for offset, word in words.items():
                registers[offset] = word
            new_values[name] = from_registers(tag.type, registers, self.spec.word_order)
        self.table.set_many(new_values, protocol="modbus")
