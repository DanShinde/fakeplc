"""OPC UA front end built on asyncua.

Each tag becomes a variable with the string node id ``ns=<idx>;s=<tag name>``,
placed in folders that follow the dotted name: ``Motor.Speed`` is the
variable ``Speed`` in the folder ``Objects/<plc>/Motor``.
"""

from __future__ import annotations

import logging
from typing import Any

from asyncua import Server, ua
from asyncua.common.callback import CallbackType, ServerItemCallback
from asyncua.common.node import Node

from .config import OpcUaSpec
from .tags import TagTable

__all__ = ["OpcUaServer"]

log = logging.getLogger(__name__)

VARIANT_TYPES: dict[str, ua.VariantType] = {
    "bool": ua.VariantType.Boolean,
    "int": ua.VariantType.Int16,
    "uint": ua.VariantType.UInt16,
    "dint": ua.VariantType.Int32,
    "udint": ua.VariantType.UInt32,
    "real": ua.VariantType.Float,
    "lreal": ua.VariantType.Double,
    "string": ua.VariantType.String,
}


class OpcUaServer:
    def __init__(self, table: TagTable, spec: OpcUaSpec, plc_name: str) -> None:
        self.table = table
        self.spec = spec
        self.plc_name = plc_name
        self.server = Server()
        self.namespace_index = 0
        self.port = spec.port
        self._nodes: dict[str, Node] = {}
        self._names: dict[ua.NodeId, str] = {}

    @property
    def url(self) -> str:
        return f"opc.tcp://{self.spec.host}:{self.port}"

    def node_id(self, tag: str) -> str:
        return f"ns={self.namespace_index};s={tag}"

    async def start(self, port: int) -> None:
        self.port = port
        server = self.server
        await server.init()
        server.set_endpoint(f"opc.tcp://{self.spec.host}:{port}")
        server.set_server_name(f"fakeplc {self.plc_name}")
        server.set_security_policy([ua.SecurityPolicyType.NoSecurity])
        idx = await server.register_namespace(self.spec.namespace)
        self.namespace_index = idx
        root = await server.nodes.objects.add_folder(idx, self.plc_name)
        folders: dict[str, Node] = {"": root}
        values = self.table.snapshot()
        for name, tag in self.table.specs.items():
            parts = name.split(".")
            parent_path = ""
            parent = root
            for part in parts[:-1]:
                path = f"{parent_path}.{part}" if parent_path else part
                if path not in folders:
                    folders[path] = await parent.add_folder(idx, part)
                parent, parent_path = folders[path], path
            node_id = ua.NodeId(name, idx)  # type: ignore[arg-type]
            variant = ua.Variant(values[name], VARIANT_TYPES[tag.type])
            node = await parent.add_variable(node_id, ua.QualifiedName(parts[-1], idx), variant)
            if not tag.readonly:
                await node.set_writable()
            if tag.description:
                await node.write_attribute(
                    ua.AttributeIds.Description,
                    ua.DataValue(ua.Variant(ua.LocalizedText(tag.description), ua.VariantType.LocalizedText)),
                )
            self._nodes[name] = node
            self._names[node_id] = name
        server.subscribe_server_callback(CallbackType.PostWrite, self._on_write)
        await server.start()
        log.info("OPC UA listening on %s", self.url)

    async def stop(self) -> None:
        await self.server.stop()

    async def push(self, names: set[str]) -> None:
        """Copy current tag values into their OPC UA variables."""
        if not names:
            return
        values = self.table.snapshot()
        for name in names:
            node = self._nodes.get(name)
            if node is None:
                continue
            variant = ua.Variant(values[name], VARIANT_TYPES[self.table.specs[name].type])
            await self.server.write_attribute_value(node.nodeid, ua.DataValue(variant))

    async def _on_write(self, event: ServerItemCallback, _dispatcher: Any) -> None:
        if not event.is_external:
            return
        changed: dict[str, Any] = {}
        for write, status in zip(event.request_params.NodesToWrite, event.response_params):
            if write.AttributeId != ua.AttributeIds.Value or not status.is_good():
                continue
            name = self._names.get(write.NodeId)
            if name is None:
                continue
            changed[name] = write.Value.Value.Value
        for name, value in changed.items():
            try:
                stored = self.table.set(name, value, protocol="opcua", dirty=False)
            except (TypeError, ValueError) as exc:
                log.warning("OPC UA write to %s rejected: %s", name, exc)
                await self.push({name})
                continue
            if stored != value:
                await self.push({name})
