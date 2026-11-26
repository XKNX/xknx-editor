"""Configured connections (Gateway menu > Configured Connections…): the KNXnet/IP tunnelling
interfaces entered by their address, listed, added, changed, deleted, imported and exported as
the dialog does; one can be the open project's connection (its backbone's Bus Connection), and
the editor can connect through one."""

from __future__ import annotations

import time
from dataclasses import replace
from typing import TYPE_CHECKING, Any, Literal

from fastmcp.exceptions import ToolError

from editor_gui.plugins.mcp.context import McpContext, make_tool

if TYPE_CHECKING:
    from fastmcp import FastMCP

    from editor_gui.plugins.connection.configured import ConfiguredConnection

ProtocolName = Literal["Auto", "Udp", "Tcp"]


def _dict(
    connection: ConfiguredConnection, index: int, project: ConfiguredConnection | None
) -> dict[str, Any]:
    return {
        "index": index,
        "name": connection.name,
        "host": connection.host,
        "port": connection.port,
        "protocol": connection.protocol,
        "use_nat": connection.use_nat,
        "parameter": connection.to_parameter(),
        "project_connection": project is not None
        and connection.same_interface(project),
    }


def register(mcp: FastMCP, ctx: McpContext) -> None:
    tool = make_tool(mcp, ctx)
    conn = ctx.connection_plugin

    def _list() -> dict[str, Any]:
        project = conn.project_connection()
        connections = conn.configured_connections
        return {
            "connections": [_dict(c, i, project) for i, c in enumerate(connections)],
            "project_connection": project.to_parameter()
            if project is not None
            else None,
        }

    def _find(index: int | None, name: str | None) -> int:
        connections = conn.configured_connections
        if (index is None) == (name is None):
            raise ToolError("pass exactly one of index or name")
        if index is not None:
            if not 0 <= index < len(connections):
                raise ToolError(f"no configured connection {index}")
            return index
        matches = [i for i, c in enumerate(connections) if c.name == name]
        if not matches:
            raise ToolError(f"no configured connection named {name!r}")
        if len(matches) > 1:
            raise ToolError(f"several connections are named {name!r}; pass index")
        return matches[0]

    @tool("read")
    def connection_list_configured() -> dict[str, Any]:
        """The configured connections (Gateway menu > Configured Connections…), in order, and
        the open project's connection (its backbone's Bus Connection, when a KNXnet/IP tunnel;
        null without one or without a project).

        Returns {connections: [{index, name, host, port, protocol ("Auto": TCP when the interface
        offers it, else UDP; "Udp"; "Tcp"), use_nat, parameter (the connection string a Bus
        Connection stores), project_connection (the same interface as the project's)}],
        project_connection}."""
        return ctx.run_locked(_list)

    @tool("write")
    def connection_add_configured(
        host: str,
        name: str = "",
        port: int = 3671,
        protocol: ProtocolName = "Auto",
        use_nat: bool = False,
    ) -> dict[str, Any]:
        """Add a configured connection: ``host`` its IP address or host name, ``name`` (empty:
        the next free default name), ``port``, ``protocol`` and ``use_nat`` (UDP only). Kept for
        the next start. Returns the list as connection_list_configured does."""
        from editor_gui.plugins.connection.configured import ConfiguredConnection

        if not 0 < port < 65536:
            raise ToolError("port must be 1 to 65535")
        connection = ConfiguredConnection(
            name, host.strip(), port, protocol, use_nat and protocol != "Tcp"
        )

        def add() -> dict[str, Any]:
            try:
                conn.add_configured_connection(connection)
            except ValueError as error:
                raise ToolError(str(error)) from error
            return _list()

        return ctx.run_locked(add)

    @tool("write")
    def connection_update_configured(
        index: int | None = None,
        name: str | None = None,
        new_name: str | None = None,
        host: str | None = None,
        port: int | None = None,
        protocol: ProtocolName | None = None,
        use_nat: bool | None = None,
    ) -> dict[str, Any]:
        """Change a configured connection, found by ``index`` OR ``name`` (pass exactly one):
        the fields given (``new_name``, ``host``, ``port``, ``protocol``, ``use_nat``). A
        connection that is the open project's connection stays its connection (the project's
        Bus Connection takes the change, one undo step). Returns the list as
        connection_list_configured does."""
        from editor_gui.plugins.connection.configured import host_valid

        def update() -> dict[str, Any]:
            position = _find(index, name)
            connections = conn.configured_connections
            old = connections[position]
            changes: dict[str, Any] = {}
            if new_name is not None:
                changes["name"] = new_name
            if host is not None:
                if not host_valid(host):
                    from editor_gui.plugins.connection.strings import S

                    raise ToolError(S.CONFIGURED_HOST_INVALID)
                changes["host"] = host.strip()
            if port is not None:
                if not 0 < port < 65536:
                    raise ToolError("port must be 1 to 65535")
                changes["port"] = port
            if protocol is not None:
                changes["protocol"] = protocol
            if use_nat is not None:
                changes["use_nat"] = use_nat
            new = replace(old, **changes)
            if new.protocol == "Tcp":
                new = replace(new, use_nat=False)
            connections[position] = new
            project = conn.project_connection()
            conn.set_configured_connections(connections)
            if project is not None and old.same_interface(project):
                conn.set_project_connection(new)
            return _list()

        return ctx.run_locked(update)

    @tool("destructive")
    def connection_delete_configured(
        index: int | None = None, name: str | None = None
    ) -> dict[str, Any]:
        """Delete a configured connection, found by ``index`` OR ``name`` (pass exactly one).
        The open project's Bus Connection stays as it is. Returns the list as
        connection_list_configured does."""

        def delete() -> dict[str, Any]:
            position = _find(index, name)
            connections = conn.configured_connections
            del connections[position]
            conn.set_configured_connections(connections)
            return _list()

        return ctx.run_locked(delete)

    @tool("write")
    def connection_set_project_connection(
        index: int | None = None, name: str | None = None, automatic: bool = False
    ) -> dict[str, Any]:
        """Make a configured connection, found by ``index`` OR ``name``, the open project's
        connection: its backbone's Bus Connection, which "Use the Project's Bus Connection"
        connects through (one undo step); ``automatic`` true sets it to Automatic (none) instead.
        Returns the list as connection_list_configured does."""

        def set_it() -> dict[str, Any]:
            if not ctx.api.project.is_open:
                raise ToolError("no project is open")
            if automatic:
                if index is not None or name is not None:
                    raise ToolError("pass automatic or a connection, not both")
                conn.set_project_connection(None)
            else:
                conn.set_project_connection(
                    conn.configured_connections[_find(index, name)]
                )
            return _list()

        return ctx.run_locked(set_it)

    @tool("write")
    def connection_import_configured(xml: str) -> dict[str, Any]:
        """Add the connections of a BusConnections XML file (the text of an exported
        BusConnections.xml): one with the name and the interface (address and port) of a
        configured one is left out, one with only its name gets a new name. Returns
        {added, ...the list as connection_list_configured does}."""
        from editor_gui.plugins.connection.configured import import_xml, merge_imported

        def add() -> dict[str, Any]:
            try:
                imported = import_xml(xml.encode("utf-8"))
            except ValueError as error:
                raise ToolError(f"no BusConnections XML: {error}") from error
            merged, added = merge_imported(conn.configured_connections, imported)
            conn.set_configured_connections(merged)
            return {"added": added, **_list()}

        return ctx.run_locked(add)

    @tool("read")
    def connection_export_configured() -> dict[str, Any]:
        """The configured connections as a BusConnections XML file (as Export… writes it).
        Returns {xml}."""
        from editor_gui.plugins.connection.configured import export_xml

        return {
            "xml": export_xml(conn.configured_connections).decode("utf-8"),
        }

    @tool("write", open_world=True)
    def connection_connect_configured(
        index: int | None = None, name: str | None = None, timeout: float = 15.0
    ) -> dict[str, Any]:
        """Connect through a configured connection, found by ``index`` OR ``name``, as the
        Gateway menu does (with its protocol; Automatic: TCP when a scanned gateway at that
        address offers it, else UDP). Waits until connected or failed."""
        from editor_gui.plugins.connection.plugin import ConnectionState

        connection = ctx.run_on_ui(
            lambda: conn.configured_connections[_find(index, name)]
        )

        def state() -> ConnectionState:
            return conn.state

        if state() in (ConnectionState.CONNECTING, ConnectionState.CONNECTED):
            raise ToolError("disconnect first (connection_disconnect)")
        ctx.run_on_ui(lambda: conn.connect_to_configured(connection))
        deadline = time.monotonic() + timeout
        while state() == ConnectionState.CONNECTING and time.monotonic() < deadline:
            time.sleep(0.1)
        if state() != ConnectionState.CONNECTED:
            raise ToolError(f"connect failed (state: {state().value})")
        return {"state": state().value, "connected": True}
