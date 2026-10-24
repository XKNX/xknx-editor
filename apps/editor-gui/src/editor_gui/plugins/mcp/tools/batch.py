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

from editor_gui.device import collect_ui_parameters as _collect_ui_parameters
from editor_gui.device_plugins import parameters_in_plugin
from editor_gui.plugins.mcp.context import McpContext, make_tool, require_project
from editor_gui.plugins.mcp.tools.project import (
    PLUGIN_PARAMETERS_ERROR,
    find_activation_path,
    param_change_result,
    perform_link,
    require_editable_param,
    resolve_enum_value,
    resolve_in_scope,
)
from editor_gui.plugins.project.service import DeviceProduct

if TYPE_CHECKING:
    from fastmcp import FastMCP

_SLOW = 300.0
_FLAGS = ("communication", "read", "write", "transmit", "update", "read_on_init")


def register(mcp: FastMCP, ctx: McpContext) -> None:
    tool = make_tool(mcp, ctx)
    project = ctx.api.project

    def _device(p: dict[str, Any]) -> Any:
        """Resolve the batch op's device by node_id OR device_address (e.g. '1.0.30'). Exactly one
        must be present in ``p``, so a caller can address a device by its individual address and
        skip the project_list_devices round-trip."""
        node_id = p.get("node_id")
        device_address = p.get("device_address")
        if (node_id is None) == (device_address is None):
            raise ValueError(
                "pass exactly one of node_id (int) or device_address (str, e.g. '1.0.30')"
            )
        if device_address is not None:
            target = str(device_address).strip()
            matches = [d for d in project.devices if d.individual_address == target]
            if not matches:
                raise ValueError(
                    f"no device with individual_address {device_address!r}"
                )
            if len(matches) > 1:
                nids = sorted(d.node_id for d in matches)
                raise ValueError(
                    f"individual_address {device_address!r} is ambiguous (node_ids: {nids})"
                )
            return matches[0]
        assert node_id is not None  # proven by the exactly-one check above
        device = project.find_device_by_node_id(node_id)
        if device is None:
            raise ValueError(f"no device with node_id {node_id}")
        return device

    def _group_address_id(p: dict[str, Any]) -> int:
        """Resolve a GA id by group_address_id OR group_address (printed form). Exactly one."""
        gid = p.get("group_address_id")
        g = p.get("group_address")
        if (gid is None) == (g is None):
            raise ValueError(
                "pass exactly one of group_address_id (int) or group_address (str, e.g. '10/1/2')"
            )
        if g is not None:
            target = str(g).strip()
            matches = [a for a in project.group_addresses if a.address == target]
            if not matches:
                raise ValueError(f"no group address {g!r}")
            if len(matches) > 1:
                ids = sorted(a.id for a in matches)
                raise ValueError(f"group_address {g!r} is ambiguous (ids: {ids})")
            return matches[0].id
        assert gid is not None  # proven by the exactly-one check above
        return int(gid)

    def _ga(p: dict[str, Any]) -> Any:
        ga_id = _group_address_id(p)
        ga = project.get_group_address(ga_id)
        if ga is None:
            raise ValueError(f"no group address with id {ga_id}")
        return ga

    def _parameters_device(p: dict[str, Any]) -> Any:
        device = _device(p)
        if parameters_in_plugin(device):
            raise ValueError(PLUGIN_PARAMETERS_ERROR.format(node_id=device.node_id))
        return device

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

    def _op_set_individual_address(p: dict[str, Any]) -> dict[str, Any]:
        # set_device_individual_address returns False on reject; verify the device actually
        # carries the new address (mirrors the standalone project_set_individual_address) so a
        # rejected address is reported as an error, not a false "ok".
        device = _device(p)
        node_id = device.node_id
        project.set_device_individual_address(
            node_id, device.individual_address, p["address"]
        )
        if _device({"node_id": node_id}).individual_address != p["address"]:
            raise ValueError(
                f"could not set individual address {p['address']!r} (invalid or already in use)"
            )
        return {
            "node_id": node_id,
            "device_address": p["address"],
            "address": p["address"],
        }

    def _op_set_parameter(p: dict[str, Any]) -> dict[str, Any]:
        # edit_params (not set_param): applies and reconciles as one undo step and raises on reject
        # WITHOUT the GUI's stash-and-retry, so a rejected batch set never silently takes effect on a
        # later edit. Returns the effective read-back value + calculated side effects.
        device = _parameters_device(p)
        node_id = device.node_id
        ref = p.get("parameter_ref_id")
        value = p.get("value")
        # Apply-time semantic addressing: resolve {label, subtree_id, value_label} NOW, against the
        # device state AFTER any earlier ops in this batch, so a child a prior op just activated
        # resolves (closes the parent->child dependency the plain ref form cannot express up front).
        if not ref:
            label = p.get("label")
            path = p.get("path")
            if not label and not path:
                raise ValueError(
                    "set_parameter needs parameter_ref_id, label, or path to resolve"
                )
            resolved = resolve_in_scope(
                list(device.get_ui()),
                p.get("subtree_id"),
                label=label,
                value_label=p.get("value_label"),
                channel=p.get("channel"),
                path=path,
                node_id=node_id,
                tree_token=p.get("tree_token"),
            )
            ref = resolved["ref_id"]
            if value is None:
                value = resolved.get("resolved_value")
        if value is None and p.get("value_label") is not None and ref:
            for param_item in _collect_ui_parameters(device.get_ui()):
                if param_item.ref_id == ref:
                    value = resolve_enum_value(
                        param_item.widget, str(p["value_label"]), param_item.label
                    )
                    break
        if value is None:
            raise ValueError(
                "set_parameter needs value (or value_label with a label or parameter_ref_id)"
            )
        # Reject NON-editable targets AFTER resolution: a label op whose scope an earlier batch op
        # just reshaped is checked against the current access, same as the standalone setter.
        require_editable_param(device, ref)
        prior_co_ids = {co.id for co in device.get_visible_com_objects()}
        prior_param_ids = {p.ref_id for p in _collect_ui_parameters(device.get_ui())}
        changes = project.edit_params(device, [(ref, value)], mode="edit")
        current_device = project.find_device_by_node_id(node_id) or device
        if hasattr(device, "last_lost_links"):
            current_device.last_lost_links = device.last_lost_links
        return {
            "node_id": node_id,
            "device_address": current_device.individual_address,
            **param_change_result(
                current_device,
                ref,
                value,
                changes,
                prior_visible_co_ids=prior_co_ids,
                prior_active_param_ids=prior_param_ids,
            ),
        }

    def _op_link_com_object(p: dict[str, Any]) -> dict[str, Any]:
        # Same feedback as the standalone project_link_com_object (idempotent dedup, dpt_mismatch,
        # sending). None means the service refused the link; surface it as an error.
        device = _device(p)
        node_id = device.node_id
        ref = p["com_object_ref_id"]
        co = device.find_com_object(ref)
        if co is None:
            raise ValueError(f"no com-object {ref!r} on device {device.node_id}")
        if co.db_id is None:
            raise ValueError(f"com-object {ref!r} is not linkable (not instantiated)")
        ga_id = _group_address_id(p)
        ga = project.get_group_address(ga_id)
        result = perform_link(
            project, node_id, ref, co, ga_id, p.get("is_sending", False)
        )
        if result is None:
            raise ValueError(
                f"could not link com-object to group address {ga_id} "
                "(check the group address exists; a Security-On group address only takes "
                "securely commissioned devices)"
            )
        result["device_address"] = device.individual_address
        if ga:
            result["group_address"] = ga.address
        return result

    def _op_unlink_com_object(p: dict[str, Any]) -> dict[str, Any]:
        target_id = p.get("assignment_id")
        if target_id is None:
            device = _device(p)
            ref = p.get("com_object_ref_id")
            if not ref:
                raise ValueError(
                    "unlink needs assignment_id, or com_object_ref_id with group_address"
                )
            co = device.find_com_object(ref)
            if co is None or co.db_id is None:
                raise ValueError(
                    f"no instantiated com-object {ref!r} on device {device.node_id}"
                )
            ga = _ga(p)
            links = list(project.get_links_for_com_object(co.db_id))
            found = next(
                (link for link in links if link.group_address_id == ga.id), None
            )
            if found is None:
                raise ValueError(
                    f"no link found between com-object {ref!r} and group address {ga.address!r}"
                )
            target_id = found.id
        project.unlink_com_object_from_ga(target_id)
        return {"assignment_id": target_id}

    def _op_setup_parameters(p: dict[str, Any]) -> dict[str, Any]:
        from fastmcp.exceptions import ToolError

        device = _parameters_device(p)
        node_id = device.node_id
        items = p.get("parameters", [])
        if not items:
            return {"node_id": node_id, "applied": False, "planned_edits": []}

        all_edits: list[tuple[str, str, str]] = []
        seen_refs: set[str] = set()
        for item in items:
            target_ref = item.get("parameter_ref_id") or item.get("ref_id")
            label = item.get("label")
            path = item.get("path")
            val = item.get("value")
            val_str = str(val) if val is not None else None
            val_label = item.get("value_label")

            try:
                edits = find_activation_path(
                    device,
                    target_ref_id=target_ref,
                    label=label,
                    path=path,
                    value=val_str,
                    value_label=val_label,
                )
            except ToolError as exc:
                raise ValueError(str(exc)) from exc

            for ref, value_to_set, reason in edits:
                if ref in seen_refs:
                    all_edits = [
                        (r, value_to_set if r == ref else v, desc)
                        for r, v, desc in all_edits
                    ]
                else:
                    seen_refs.add(ref)
                    all_edits.append((ref, value_to_set, reason))

        planned = [
            {"ref_id": ref, "value": val, "reason": reason}
            for ref, val, reason in all_edits
        ]
        if not all_edits:
            return {"node_id": node_id, "applied": False, "planned_edits": []}

        edits_for_engine = [(ref, val) for ref, val, _ in all_edits]
        changes = project.edit_params(device, edits_for_engine, mode="edit")
        side_effects = [
            {"ref_id": ref, "old": old, "new": new}
            for ref, (old, new) in sorted(changes.items())
            if ref not in seen_refs and old != new
        ]
        return {
            "node_id": node_id,
            "device_address": device.individual_address,
            "applied": True,
            "planned_edits": planned,
            "changed": side_effects,
        }

    _DEVICE_OPS = {
        "rename_device",
        "set_individual_address",
        "set_parameter",
        "setup_parameters",
        "set_com_object_flag",
        "link_com_object",
    }
    _GA_OPS = {
        "rename_group_address",
        "set_group_address_dpt",
        "remove_group_address",
        "link_com_object",
    }

    # op name -> (required params, handler). Handlers raise ValueError on bad input. The device
    # identifier (node_id | device_address) is NOT in `required` because the op itself picks the
    # right form and errors clearly; same for group_address_id | group_address on link.
    handlers: dict[
        str, tuple[tuple[str, ...], Callable[[dict[str, Any]], dict[str, Any]]]
    ] = {
        "add_device": (("product_ref_id",), _op_add_device),
        "rename_device": (
            ("name",),
            lambda p: (
                (dev := _device(p)),
                project.set_device_name(dev.node_id, dev.name, p["name"]),
                {
                    "node_id": dev.node_id,
                    "device_address": dev.individual_address,
                    "name": p["name"],
                },
            )[2],
        ),
        "set_individual_address": (("address",), _op_set_individual_address),
        "set_parameter": ((), _op_set_parameter),
        "setup_parameters": (("parameters",), _op_setup_parameters),
        "set_com_object_flag": (
            ("com_object_ref_id", "flag", "value"),
            lambda p: (
                (dev := _device(p)),
                project.set_flag(dev, p["com_object_ref_id"], p["flag"], p["value"]),
                {
                    "node_id": dev.node_id,
                    "device_address": dev.individual_address,
                    "com_object_ref_id": p["com_object_ref_id"],
                },
            )[2],
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
            ("name",),
            lambda p: (
                (g := _ga(p)),
                project.rename_group_address(g.id, p["name"]),
                {
                    "group_address_id": g.id,
                    "group_address": g.address,
                    "name": p["name"],
                },
            )[2],
        ),
        "set_group_address_dpt": (
            (),
            lambda p: (
                (g := _ga(p)),
                project.set_group_address_dpt(g.id, p.get("dpt")),
                {
                    "group_address_id": g.id,
                    "group_address": g.address,
                    "dpt": p.get("dpt"),
                },
            )[2],
        ),
        "remove_group_address": (
            (),
            lambda p: (
                (g := _ga(p)),
                project.remove_group_address(g.id),
                {"group_address_id": g.id, "group_address": g.address},
            )[2],
        ),
        "link_com_object": (
            ("com_object_ref_id",),
            _op_link_com_object,
        ),
        "unlink_com_object": (
            (),
            _op_unlink_com_object,
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
        params: dict[str, Any] = op.get("params", {}) or {}
        if name not in handlers:
            return {
                "index": index,
                "op": name,
                "valid": False,
                "issue": f"unknown op {name!r}; allowed: {', '.join(sorted(handlers))}",
            }
        required, _ = handlers[name]
        missing = [k for k in required if k not in params]
        if (
            name in _DEVICE_OPS
            and "node_id" not in params
            and "device_address" not in params
        ):
            missing.append("node_id or device_address")
        if (
            name in _GA_OPS
            and "group_address_id" not in params
            and "group_address" not in params
        ):
            missing.append("group_address_id or group_address")
        if name == "unlink_com_object":
            has_id = "assignment_id" in params
            has_explicit = (
                ("node_id" in params or "device_address" in params)
                and "com_object_ref_id" in params
                and ("group_address_id" in params or "group_address" in params)
            )
            if not has_id and not has_explicit:
                missing.append(
                    "assignment_id (or com_object_ref_id with group_address and device)"
                )
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
        deferred: bool = True,
    ) -> dict[str, Any]:
        """Apply project edits in order. Each operations entry is {"op": name, "params": {...}}.
        These are undoable project steps; applying changes to hardware requires connection tools.
        A batch may require multiple project_undo calls. IDs must be known before the call; later
        operations cannot reference earlier results.

        Device-scoped ops accept either ``node_id`` (int) OR ``device_address`` (str, e.g. "1.0.30");
        GA-scoped ops accept either ``group_address_id`` (int) OR ``group_address`` (str, e.g.
        "10/1/2"). Pass exactly one of each pair. Below "device" / "ga" stand for either form.

        Supported ops with required params (optional params in brackets) and success result fields:
        - add_device: product_ref_id [name] -> {node_id}; uses the first matching catalog product.
        - rename_device: (node_id | device_address), name -> {node_id, device_address, name}.
        - set_individual_address: (node_id | device_address), address -> {node_id, device_address, address}.
        - set_parameter: (node_id | device_address), (parameter_ref_id | label [channel] [subtree_id tree_token] [value_label]),
          value? -> {node_id, device_address, ref_id, requested, value, active, changed?, lost_links?, activated_com_objects, unlocked_parameters}.
          Address the parameter by parameter_ref_id, or semantically by label (resolved AT THIS OP'S TURN within channel or subtree_id,
          so a child a prior op in the batch just activated resolves; channel scopes by name e.g. 'A' without needing tree_token;
          value_label gives an enum's value). value is read back after the edit; changed lists
          calculated side effects; lost_links lists links a function/mode change dropped.
        - set_com_object_flag: (node_id | device_address), com_object_ref_id, flag, value -> {node_id, device_address, com_object_ref_id}.
        - create_group_address: [address, name] -> {group_address_id}.
        - rename_group_address: (group_address_id | group_address), name -> {group_address_id, group_address, name}.
        - set_group_address_dpt: (group_address_id | group_address) [dpt; omitted/null clears] -> {group_address_id, group_address, dpt}.
        - remove_group_address: (group_address_id | group_address) -> {group_address_id, group_address}.
        - link_com_object: (node_id | device_address), com_object_ref_id, (group_address_id | group_address) [is_sending=false] ->
          {link_id, node_id, device_address, com_object_ref_id, com_object_db_id, group_address_id, group_address, is_sending,
          already_linked, dpt_mismatch?, other_sending_group_address_ids?} (same feedback as the
          standalone tool; idempotent).
        - unlink_com_object: assignment_id | ((node_id | device_address), com_object_ref_id, (group_address_id | group_address)) -> {assignment_id}.
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

        deferred=true (default) enables multi-pass deferred validation: operations that fail because
        a prerequisite (e.g. parent switch activating a child parameter, or a function activating a
        com-object) has not yet run in the batch are deferred and retried in subsequent passes once
        other operations succeed. Setting deferred=false enforces strict single-pass order.

        With dry_run=false, returns {applied, atomic, rolled_back, ok, failed, results}. Each result
        has {index, op, status}, plus result on "ok" or error on "error"; "skipped" has neither.
        atomic=true (default) stops on the first unresolvable error and rewinds applied project edits;
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
            # Pre-validate schemas and required parameters
            validated: list[tuple[int, dict[str, Any]]] = []
            results: dict[int, dict[str, Any]] = {}
            for i, op in enumerate(operations):
                check = _validate(op, i)
                if not check["valid"]:
                    results[i] = {
                        "index": i,
                        "op": op.get("op"),
                        "status": "error",
                        "error": check["issue"],
                    }
                else:
                    validated.append((i, op))

            # If any schema validation failed and atomic, skip remaining and return immediately
            if results and atomic:
                for i, op in validated:
                    results[i] = {
                        "index": i,
                        "op": op.get("op"),
                        "status": "skipped",
                    }
                return {
                    "applied": False,
                    "atomic": atomic,
                    "rolled_back": False,
                    "ok": 0,
                    "failed": sum(
                        1 for r in results.values() if r["status"] == "error"
                    ),
                    "results": [results[i] for i in range(len(operations))],
                }

            # Multi-pass execution for validated operations
            pending = list(validated)
            last_errors: dict[int, str] = {}

            while pending:
                next_pending: list[tuple[int, dict[str, Any]]] = []
                progress = False

                for i, op in pending:
                    handler = handlers[op["op"]][1]
                    try:
                        result = handler(op.get("params", {}) or {})
                        results[i] = {
                            "index": i,
                            "op": op["op"],
                            "status": "ok",
                            "result": result,
                        }
                        progress = True
                    except Exception as exc:
                        last_errors[i] = str(exc)
                        if deferred:
                            next_pending.append((i, op))
                        else:
                            results[i] = {
                                "index": i,
                                "op": op["op"],
                                "status": "error",
                                "error": str(exc),
                            }
                            if atomic:
                                break

                if not deferred:
                    if atomic and any(r["status"] == "error" for r in results.values()):
                        for j, unhandled_op in validated:
                            if j not in results:
                                results[j] = {
                                    "index": j,
                                    "op": unhandled_op.get("op"),
                                    "status": "skipped",
                                }
                    break

                if not next_pending:
                    break

                if not progress:
                    # Stalled: a full pass made zero progress. Remaining ops cannot be resolved.
                    for i, op in next_pending:
                        results[i] = {
                            "index": i,
                            "op": op["op"],
                            "status": "error",
                            "error": last_errors.get(i, "operation failed"),
                        }
                    break

                pending = next_pending

            failed = any(r["status"] == "error" for r in results.values())
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
                "ok": sum(1 for r in results.values() if r["status"] == "ok"),
                "failed": sum(1 for r in results.values() if r["status"] == "error"),
                "results": [results[i] for i in range(len(operations))],
            }

        return ctx.run_locked(_apply, timeout=_SLOW)
