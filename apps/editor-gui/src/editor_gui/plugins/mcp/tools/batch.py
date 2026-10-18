# The @mcp.tool decorator registers each tool via its side effect, not a direct call.
# pyright: reportUnusedFunction=false
"""A batch tool: apply many project mutations in one call, with dry-run validation and atomic apply.

Lets an LLM parametrise-and-link a device in one round-trip instead of a dozen calls (fewer lost ids,
one place to fail). Only fast, undoable *project* mutations are allowed — no bus/programming ops.
Atomic apply rolls back everything on the first failure by rewinding the undo cursor.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from editor_gui.device_plugins import parameters_in_plugin
from editor_gui.plugins.mcp.context import McpContext, make_tool, require_project
from editor_gui.plugins.mcp.tools.project import PLUGIN_PARAMETERS_ERROR
from editor_gui.plugins.project.service import DeviceProduct

if TYPE_CHECKING:
    from fastmcp import FastMCP

_SLOW = 300.0
_FLAGS = ("communication", "read", "write", "transmit", "update", "read_on_init")


def register(mcp: FastMCP, ctx: McpContext) -> None:
    tool = make_tool(mcp, ctx)
    project = ctx.api.project

    def _device(node_id: Any) -> Any:
        device = project.find_device_by_node_id(node_id)
        if device is None:
            raise ValueError(f"no device with node_id {node_id}")
        return device

    def _parameters_device(node_id: Any) -> Any:
        device = _device(node_id)
        if parameters_in_plugin(device):
            raise ValueError(PLUGIN_PARAMETERS_ERROR.format(node_id=node_id))
        return device

    def _resolve_co_db_id(node_id: Any, ref_id: str) -> int:
        co = _device(node_id).find_com_object(ref_id)
        if co is None:
            raise ValueError(f"no com-object {ref_id!r} on device {node_id}")
        if co.db_id is None:
            raise ValueError(
                f"com-object {ref_id!r} is not linkable (not instantiated)"
            )
        return co.db_id

    def _op_add_device(p: dict[str, Any]) -> dict[str, Any]:
        product = next(
            (
                x
                for x in ctx.api.catalog.get_products()
                if x.product_ref_id == p["product_ref_id"]
            ),
            None,
        )
        if product is None or product.application_id is None:
            raise ValueError(f"unknown/addable product {p['product_ref_id']!r}")
        app = ctx.api.catalog.get_application(product.application_id)
        if app is None:
            raise ValueError(f"application for {p['product_ref_id']!r} not in catalog")
        node_id = project.add_device(
            product_ref_id=product.product_ref_id,
            hardware2program_ref_id=product.hardware2program_ref_id,
            name=p.get("name") or "",
            app=app,
            product=DeviceProduct.of(product),
        )
        return {"node_id": node_id}

    # op name -> (required params, handler). Handlers raise ValueError on bad input.
    handlers: dict[
        str, tuple[tuple[str, ...], Callable[[dict[str, Any]], dict[str, Any]]]
    ] = {
        "add_device": (("product_ref_id",), _op_add_device),
        "rename_device": (
            ("node_id", "name"),
            lambda p: (
                project.set_device_name(
                    p["node_id"], _device(p["node_id"]).name, p["name"]
                ),
                {"node_id": p["node_id"]},
            )[1],
        ),
        "set_individual_address": (
            ("node_id", "address"),
            lambda p: (
                project.set_device_individual_address(
                    p["node_id"], _device(p["node_id"]).individual_address, p["address"]
                ),
                {"node_id": p["node_id"], "address": p["address"]},
            )[1],
        ),
        "set_parameter": (
            ("node_id", "parameter_ref_id", "value"),
            lambda p: (
                project.set_param(
                    _parameters_device(p["node_id"]), p["parameter_ref_id"], p["value"]
                ),
                {"node_id": p["node_id"], "parameter_ref_id": p["parameter_ref_id"]},
            )[1],
        ),
        "set_com_object_flag": (
            ("node_id", "com_object_ref_id", "flag", "value"),
            lambda p: (
                project.set_flag(
                    _device(p["node_id"]), p["com_object_ref_id"], p["flag"], p["value"]
                ),
                {"node_id": p["node_id"], "com_object_ref_id": p["com_object_ref_id"]},
            )[1],
        ),
        "create_group_address": (
            (),
            lambda p: {
                "group_address_id": project.create_group_address(
                    p.get("address"), p.get("name", "")
                )
            },
        ),
        "rename_group_address": (
            ("group_address_id", "name"),
            lambda p: (
                project.rename_group_address(p["group_address_id"], p["name"]),
                {"group_address_id": p["group_address_id"]},
            )[1],
        ),
        "set_group_address_dpt": (
            ("group_address_id",),
            lambda p: (
                project.set_group_address_dpt(p["group_address_id"], p.get("dpt")),
                {"group_address_id": p["group_address_id"]},
            )[1],
        ),
        "remove_group_address": (
            ("group_address_id",),
            lambda p: (
                project.remove_group_address(p["group_address_id"]),
                {"group_address_id": p["group_address_id"]},
            )[1],
        ),
        "link_com_object": (
            ("node_id", "com_object_ref_id", "group_address_id"),
            lambda p: {
                "link_id": project.link_com_object_to_ga(
                    _resolve_co_db_id(p["node_id"], p["com_object_ref_id"]),
                    p["group_address_id"],
                    p.get("is_sending", False),
                )
            },
        ),
        "unlink_com_object": (
            ("assignment_id",),
            lambda p: (
                project.unlink_com_object_from_ga(p["assignment_id"]),
                {"assignment_id": p["assignment_id"]},
            )[1],
        ),
        "create_area": (
            ("area_number",),
            lambda p: {
                "area_id": project.create_area(p["area_number"], p.get("name", ""))
            },
        ),
        "create_line": (
            ("area_id", "line_number"),
            lambda p: {
                "line_id": project.create_line(
                    p["area_id"], p["line_number"], p.get("name", "")
                )
            },
        ),
    }

    def _validate(op: dict[str, Any], index: int) -> dict[str, Any]:
        name = op.get("op")
        params = op.get("params", {}) or {}
        if name not in handlers:
            return {
                "index": index,
                "op": name,
                "valid": False,
                "issue": f"unknown op {name!r}; allowed: {', '.join(sorted(handlers))}",
            }
        required, _ = handlers[name]
        missing = [k for k in required if k not in params]
        if missing:
            return {
                "index": index,
                "op": name,
                "valid": False,
                "issue": f"missing params: {', '.join(missing)}",
            }
        if name == "set_com_object_flag" and params.get("flag") not in _FLAGS:
            return {
                "index": index,
                "op": name,
                "valid": False,
                "issue": f"flag must be one of: {', '.join(_FLAGS)}",
            }
        return {"index": index, "op": name, "valid": True}

    @tool
    def project_batch(
        operations: list[dict[str, Any]],
        atomic: bool = True,
        dry_run: bool = False,
    ) -> dict[str, Any]:
        """Apply project edits in order. Each operations entry is {"op": name, "params": {...}}.
        These are undoable project steps; applying changes to hardware requires connection tools.
        A batch may require multiple project_undo calls. IDs must be known before the call; later
        operations cannot reference earlier results.

        Supported ops with required params (optional params in brackets) and success result fields:
        - add_device: product_ref_id [name] -> {node_id}; uses the first matching catalog product.
        - rename_device: node_id, name -> {node_id}.
        - set_individual_address: node_id, address -> {node_id, address}.
        - set_parameter: node_id, parameter_ref_id, value -> {node_id, parameter_ref_id}.
        - set_com_object_flag: node_id, com_object_ref_id, flag, value -> {node_id, com_object_ref_id}.
        - create_group_address: [address, name] -> {group_address_id}.
        - rename_group_address: group_address_id, name -> {group_address_id}.
        - set_group_address_dpt: group_address_id [dpt; omitted/null clears] -> {group_address_id}.
        - remove_group_address: group_address_id -> {group_address_id}.
        - link_com_object: node_id, com_object_ref_id, group_address_id [is_sending=false] -> {link_id}.
        - unlink_com_object: assignment_id -> {assignment_id}.
        - create_area: area_number [name] -> {area_id}.
        - create_line: area_id, line_number [name] -> {line_id}.

        Use node_id from project_list_devices, product_ref_id from catalog_list_products, parameter
        ref_id from project_list_parameters as parameter_ref_id, and object ref_id from
        project_list_com_objects as com_object_ref_id. Use id from project_list_group_addresses as
        group_address_id; use area id from project_topology as area_id. For assignment_id, use id
        from project_get_ga_assignments, assignment_id from project_get_com_object_links or a returned
        link_id. Linkable objects are required for linking; set is_sending=true for a transmitting
        link. flag is communication/read/write/transmit/update/read_on_init.

        dry_run=true checks only operation names, required params and flag names, without applying
        edits or validating referenced entities/values. Returns {dry_run, valid, results}; each result
        has {index, op, valid}, plus issue when invalid. index is zero-based.

        With dry_run=false, returns {applied, atomic, rolled_back, ok, failed, results}. Each result
        has {index, op, status}, plus result on "ok" or error on "error"; "skipped" has neither.
        atomic=true (default) stops on the first reported error and rewinds applied project edits;
        later operations are skipped. atomic=false continues, so applied=true can coexist with
        failures. ok counts successful handler returns even after rollback; inspect rolled_back and
        each result. Handlers can return without changing anything, so re-read critical edits."""
        require_project(ctx)

        if dry_run:
            checks = [_validate(op, i) for i, op in enumerate(operations)]
            return {
                "dry_run": True,
                "valid": all(c["valid"] for c in checks),
                "results": checks,
            }

        def _apply() -> dict[str, Any]:
            start_cursor = project.cursor
            results: list[dict[str, Any]] = []
            failed = False
            for i, op in enumerate(operations):
                if failed and atomic:
                    results.append(
                        {"index": i, "op": op.get("op"), "status": "skipped"}
                    )
                    continue
                check = _validate(op, i)
                if not check["valid"]:
                    results.append(
                        {
                            "index": i,
                            "op": op.get("op"),
                            "status": "error",
                            "error": check["issue"],
                        }
                    )
                    failed = True
                    continue
                handler = handlers[op["op"]][1]
                try:
                    result = handler(op.get("params", {}) or {})
                    results.append(
                        {"index": i, "op": op["op"], "status": "ok", "result": result}
                    )
                except Exception as exc:
                    results.append(
                        {
                            "index": i,
                            "op": op["op"],
                            "status": "error",
                            "error": str(exc),
                        }
                    )
                    failed = True

            rolled_back = False
            if failed and atomic:
                # Rewind every step this batch applied.
                while project.cursor > start_cursor and project.undo():
                    pass
                rolled_back = True

            return {
                "applied": not (failed and atomic),
                "atomic": atomic,
                "rolled_back": rolled_back,
                "ok": sum(1 for r in results if r["status"] == "ok"),
                "failed": sum(1 for r in results if r["status"] == "error"),
                "results": results,
            }

        return ctx.run_locked(_apply, timeout=_SLOW)
