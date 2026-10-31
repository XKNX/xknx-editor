# The @mcp.tool decorator registers each tool via its side effect, not a direct call.
# pyright: reportUnusedFunction=false
"""A batch tool: apply many project mutations in one call, with dry-run validation and atomic apply.

Lets an LLM parametrise-and-link a device in one round-trip instead of a dozen calls (fewer lost ids,
one place to fail). Only fast, undoable *project* mutations are allowed — no bus/programming ops.
Atomic apply rolls back everything on the first failure by rewinding the undo cursor.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, Any, get_args

from editor_gui.device_plugins import parameters_in_plugin
from editor_gui.plugins.mcp.context import McpContext, make_tool, require_project
from editor_gui.plugins.mcp.tools.details import (
    CommentKind,
    Status,
    StatusKind,
    comment_rich_text,
    set_comment,
    set_description,
    status_target,
)
from editor_gui.plugins.mcp.tools.project import (
    PLUGIN_PARAMETERS_ERROR,
    apply_parameter,
    com_object_hidden,
    link_id_between,
    perform_link,
    resolve_in_scope,
    resolve_parameter_id,
)
from editor_gui.plugins.project.service import DeviceProduct

if TYPE_CHECKING:
    from fastmcp import FastMCP

_SLOW = 300.0
_FLAGS = ("communication", "read", "write", "transmit", "update", "read_on_init")
_COMMENT_KINDS: tuple[str, ...] = get_args(CommentKind)
_STATUS_KINDS: tuple[str, ...] = get_args(StatusKind)
_STATUSES: tuple[str, ...] = get_args(Status)


def _placeholder(p: dict[str, Any], name: str) -> str:
    """The hint for a param named like the op list's placeholder ``name`` ("device", "ga")."""
    if name not in p:
        return ""
    return (
        f"; {name!r} in the op list stands for that pair, it is not a param of its own"
    )


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
                + _placeholder(p, "device")
            )
        if device_address is not None:
            matches = [
                d for d in project.devices if d.individual_address == device_address
            ]
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
                + _placeholder(p, "ga")
            )
        if g is not None:
            matches = [a for a in project.group_addresses if a.address == g]
            if not matches:
                raise ValueError(f"no group address {g!r}")
            if len(matches) > 1:
                ids = sorted(a.id for a in matches)
                raise ValueError(f"group_address {g!r} is ambiguous (ids: {ids})")
            return matches[0].id
        assert gid is not None  # proven by the exactly-one check above
        if project.get_group_address(int(gid)) is None:
            raise ValueError(f"no group address with id {gid}")
        return int(gid)

    def _on_group_address(
        apply: Callable[[int, dict[str, Any]], object],
    ) -> Callable[[dict[str, Any]], dict[str, Any]]:
        """An op on one group address: resolved once, before ``apply`` changes or removes it."""

        def run(p: dict[str, Any]) -> dict[str, Any]:
            ga_id = _group_address_id(p)
            apply(ga_id, p)
            return {"group_address_id": ga_id}

        return run

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
        if product is None:
            raise ValueError(f"unknown product {p['product_ref_id']!r}")
        # A product without an application program (e.g. a power supply) has none to load.
        app = (
            ctx.api.catalog.get_application(product.application_id)
            if product.application_id is not None
            else None
        )
        if product.application_id is not None and app is None:
            raise ValueError(f"application for {p['product_ref_id']!r} not in catalog")
        space_id = p.get("space_id")
        if space_id is not None and project.find_space(space_id) is None:
            raise ValueError(f"no space with id {space_id}")
        node_id = project.add_device(
            product_ref_id=product.product_ref_id,
            hardware2program_ref_id=product.hardware2program_ref_id,
            name=p.get("name") or "",
            app=app,
            product=DeviceProduct.of(product),
            space_id=space_id,
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
        return {"node_id": node_id, "address": p["address"]}

    def _op_set_parameter(p: dict[str, Any]) -> dict[str, Any]:
        # As project_set_parameter (apply_parameter): one undo step, no silent retry of a rejected
        # set, the effective read-back value + side effects + what the edit showed or hid.
        device = _parameters_device(p)
        ref = p.get("parameter_ref_id")
        if not ref and p.get("parameter_id"):
            # The parameter's ref NOW: an earlier op of the batch may show it under another.
            ref = resolve_parameter_id(device, p["parameter_id"])
        value = p.get("value")
        # Apply-time semantic addressing: resolve {label, subtree_id, value_label} NOW, against the
        # device state AFTER any earlier ops in this batch, so a child a prior op just activated
        # resolves (closes the parent->child dependency the plain ref form cannot express up front).
        if not ref:
            label = p.get("label")
            if not label:
                raise ValueError(
                    "set_parameter needs parameter_ref_id, parameter_id, or a label to resolve"
                )
            resolved = resolve_in_scope(
                list(device.get_ui()),
                p.get("subtree_id"),
                label,
                p.get("value_label"),
                node_id=device.node_id,
                tree_token=p.get("tree_token"),
            )
            ref = resolved["ref_id"]
            if value is None:
                value = resolved.get("resolved_value")
        if value is None:
            raise ValueError("set_parameter needs value (or value_label with a label)")
        # The editable check runs AFTER resolution: a label op whose scope an earlier batch op just
        # reshaped is checked against the current access, same as the standalone setter.
        return {
            "node_id": device.node_id,
            **apply_parameter(project, device, ref, value),
        }

    def _op_link_com_object(p: dict[str, Any]) -> dict[str, Any]:
        # Same feedback as the standalone project_link_com_object (idempotent dedup, dpt_mismatch,
        # sending). None means the service refused the link; surface it as an error.
        device = _device(p)
        ref = p["com_object_ref_id"]
        co = device.find_com_object(ref)
        if co is None:
            raise ValueError(f"no com-object {ref!r} on device {device.node_id}")
        if co.db_id is None:
            raise ValueError(f"com-object {ref!r} is not linkable (not instantiated)")
        ga_id = _group_address_id(p)
        result = perform_link(
            project,
            device.node_id,
            ref,
            co,
            ga_id,
            p.get("is_sending", False),
            hidden=com_object_hidden(device, ref),
        )
        if result is None:
            raise ValueError(
                f"could not link com-object to group address {ga_id} "
                "(check the group address id exists; a Security-On group address only takes "
                "securely commissioned devices)"
            )
        return result

    def _op_unlink_com_object(p: dict[str, Any]) -> dict[str, Any]:
        # By the link's id, or by the linked pair (device, com_object_ref_id, ga).
        if "assignment_id" in p:
            link_id = int(p["assignment_id"])
        else:
            link_id = link_id_between(
                project, _device(p), p["com_object_ref_id"], _group_address_id(p)
            )
        project.unlink_com_object_from_ga(link_id)
        return {"assignment_id": link_id}

    def _op_rename_device(p: dict[str, Any]) -> dict[str, Any]:
        if "name" not in p and "description" not in p:
            raise ValueError("rename_device needs name, description or both")
        device = _device(p)
        if "name" in p:
            project.set_device_name(device.node_id, device.name, p["name"])
        if "description" in p:
            project.set_device_description(
                device.node_id, device.description or "", p["description"]
            )
        return {"node_id": device.node_id}

    def _op_move_device_to_segment(p: dict[str, Any]) -> dict[str, Any]:
        device = _device(p)
        project.move_device_to_segment(device.node_id, int(p["segment_id"]))
        return {"node_id": device.node_id}

    def _op_remove_device(p: dict[str, Any]) -> dict[str, Any]:
        node_id = _device(p).node_id
        project.remove_device(node_id)
        if node_id in project.selected_node_ids:
            project.set_multi_selection(None, [])
        return {"node_id": node_id}

    def _op_set_comment(p: dict[str, Any]) -> dict[str, Any]:
        kind = p["kind"]
        if kind not in _COMMENT_KINDS:
            raise ValueError(f"kind must be one of: {', '.join(_COMMENT_KINDS)}")
        element_id = int(p.get("element_id", 0))
        field = p.get("field", "comment")
        if field not in ("comment", "installation_hints"):
            raise ValueError("field must be comment or installation_hints")
        text = comment_rich_text(p.get("text"), p.get("paragraphs"))
        set_comment(project, kind, element_id, field, text)
        return {"kind": kind, "element_id": element_id, "field": field}

    def _op_set_status(p: dict[str, Any]) -> dict[str, Any]:
        kind = p["kind"]
        if kind not in _STATUS_KINDS:
            raise ValueError(f"kind must be one of: {', '.join(_STATUS_KINDS)}")
        if p["status"] not in _STATUSES:
            raise ValueError(f"status must be one of: {', '.join(_STATUSES)}")
        element_id = p.get("element_id", p.get("node_id"))
        target = status_target(
            project,
            kind,
            None if element_id is None else int(element_id),
            p.get("device_address"),
        )
        project.set_completion_status(kind, target, p["status"])
        return {"kind": kind, "element_id": target, "status": p["status"]}

    def _op_set_com_object_flag(p: dict[str, Any]) -> dict[str, Any]:
        device = _device(p)
        project.set_flag(device, p["com_object_ref_id"], p["flag"], p["value"])
        return {"node_id": device.node_id, "com_object_ref_id": p["com_object_ref_id"]}

    def _op_create_group_address(p: dict[str, Any]) -> dict[str, Any]:
        address = p.get("address")
        ga_id = project.create_group_address(address, p.get("name", ""), p.get("dpt"))
        if ga_id is None:
            raise ValueError(
                f"could not create group address {address!r} (invalid for the project's style)"
            )
        return {"group_address_id": ga_id}

    # op name -> (required params, handler). Handlers raise ValueError on bad input. The device
    # identifier (node_id | device_address) is NOT in `required` because the op itself picks the
    # right form and errors clearly; same for group_address_id | group_address on link.
    handlers: dict[
        str, tuple[tuple[str, ...], Callable[[dict[str, Any]], dict[str, Any]]]
    ] = {
        "add_device": (("product_ref_id",), _op_add_device),
        "rename_device": ((), _op_rename_device),
        "move_device_to_segment": (("segment_id",), _op_move_device_to_segment),
        "remove_device": ((), _op_remove_device),
        "set_individual_address": (("address",), _op_set_individual_address),
        "set_parameter": ((), _op_set_parameter),
        "set_com_object_flag": (
            ("com_object_ref_id", "flag", "value"),
            _op_set_com_object_flag,
        ),
        "create_group_address": ((), _op_create_group_address),
        "rename_group_address": (
            ("name",),
            _on_group_address(
                lambda ga_id, p: project.rename_group_address(ga_id, p["name"])
            ),
        ),
        "set_group_address_central": (
            ("central",),
            _on_group_address(
                lambda ga_id, p: project.set_group_address_central(
                    ga_id, bool(p["central"])
                )
            ),
        ),
        "rename_group_range": (
            ("group_range_id", "name"),
            lambda p: (
                project.rename_group_range(int(p["group_range_id"]), p["name"].strip()),
                {"group_range_id": int(p["group_range_id"])},
            )[1],
        ),
        "rename_function": (
            ("function_id", "name"),
            lambda p: (
                project.rename_function(int(p["function_id"]), p["name"].strip()),
                {"function_id": int(p["function_id"])},
            )[1],
        ),
        "set_group_address_dpt": (
            (),
            _on_group_address(
                lambda ga_id, p: project.set_group_address_dpt(ga_id, p.get("dpt"))
            ),
        ),
        "remove_group_address": (
            (),
            _on_group_address(lambda ga_id, _p: project.remove_group_address(ga_id)),
        ),
        "link_com_object": (
            ("com_object_ref_id",),
            _op_link_com_object,
        ),
        "unlink_com_object": ((), _op_unlink_com_object),
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
        "set_comment": (("kind",), _op_set_comment),
        "set_status": (("kind", "status"), _op_set_status),
        "set_description": (
            ("kind", "element_id", "description"),
            lambda p: (
                set_description(
                    project, p["kind"], int(p["element_id"]), p["description"]
                ),
                {"kind": p["kind"], "element_id": int(p["element_id"])},
            )[1],
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
        if missing:
            return {
                "index": index,
                "op": name,
                "valid": False,
                "issue": f"missing params: {', '.join(missing)}",
            }
        if (
            name == "unlink_com_object"
            and "assignment_id" not in params
            and "com_object_ref_id" not in params
        ):
            return {
                "index": index,
                "op": name,
                "valid": False,
                "issue": "missing params: assignment_id, or com_object_ref_id with device and ga",
            }
        if name == "set_com_object_flag" and params.get("flag") not in _FLAGS:
            return {
                "index": index,
                "op": name,
                "valid": False,
                "issue": f"flag must be one of: {', '.join(_FLAGS)}",
            }
        return {"index": index, "op": name, "valid": True}

    @tool("destructive")
    def project_batch(
        operations: list[dict[str, Any]],
        atomic: bool = True,
        dry_run: bool = False,
    ) -> dict[str, Any]:
        """Apply project edits in order. Each operations entry is {"op": name, "params": {...}}.
        These are undoable project steps; applying changes to hardware requires connection tools.
        A batch may require multiple project_undo calls. IDs must be known before the call; later
        operations cannot reference earlier results — but a printed ``group_address`` is resolved
        when its op runs, so it can name a group address an earlier op of the batch created.

        Device-scoped ops accept either ``node_id`` (int) OR ``device_address`` (str, e.g. "1.0.30");
        GA-scoped ops accept either ``group_address_id`` (int) OR ``group_address`` (str, e.g.
        "10/1/2"). Pass exactly one of each pair. In the list below, "device" and "ga" are
        placeholders for these pairs, not param names: write e.g. {"device_address": "1.0.30"},
        never {"device": "1.0.30"}.

        Supported ops with required params (optional params in brackets) and success result fields:
        - add_device: product_ref_id [name, space_id] -> {node_id}; uses the first matching
          catalog product; with space_id it is added to that space, on its Current Line.
        - rename_device: device, [name] [description] (at least one) -> {node_id}.
        - move_device_to_segment: device, segment_id -> {node_id}; as
          project_move_device_to_segment (several devices, also from different lines, as one
          "Move to" of a multi-selection does).
        - remove_device: device -> {node_id}; as project_remove_device (several devices in one
          batch, as "Delete" of a multi-selection does).
        - set_individual_address: device, address -> {node_id, address}.
        - set_parameter: device, (parameter_ref_id | parameter_id | label [subtree_id tree_token]
          [value_label]), value? -> {node_id, ref_id, requested, value, active, tree_token,
          changed?, lost_links?} (tree_token as for project_set_parameter: the tree after this op).
          Address the parameter by parameter_ref_id; by parameter_id (the ref_id without its
          trailing "_R-…", resolved AT THIS OP'S TURN to the ref the device shows the parameter
          with, so a value after an op that switched e.g. its time base reaches the parameter's
          new ref); or by label (resolved AT THIS OP'S TURN within subtree_id,
          so a child a prior op in the batch just activated resolves; a SCOPED label requires
          tree_token from the project_parameter_tree response its subtree_id came from, so an op
          whose scope an earlier batch op invalidated fails explicitly instead of silently retargeting;
          value_label gives an enum's value). value is read back after the edit; changed lists
          calculated side effects; lost_links lists links a function/mode change dropped;
          shown_parameters, hidden_parameters, shown_com_objects and hidden_com_objects (when not
          empty) are what the edit showed or hid, as for project_set_parameter.
        - set_com_object_flag: device, com_object_ref_id, flag, value -> {node_id, com_object_ref_id}.
        - create_group_address: [address, name, dpt] -> {group_address_id}; dpt as for
          set_group_address_dpt, set in the same step.
        - rename_group_address: ga, name -> {group_address_id}.
        - set_group_address_central: ga, central (bool) -> {group_address_id}.
        - rename_group_range: group_range_id, name -> {group_range_id}.
        - rename_function: function_id, name -> {function_id}.
        - set_group_address_dpt: ga [dpt; omitted/null clears] -> {group_address_id}.
        - remove_group_address: ga -> {group_address_id}.
        - link_com_object: device, com_object_ref_id, ga [is_sending=false] ->
          {link_id, node_id, com_object_ref_id, com_object_db_id, group_address_id, is_sending,
          already_linked, dpt_mismatch?, other_sending_group_address_ids?, hidden?} (same feedback
          as the standalone tool; idempotent).
        - unlink_com_object: assignment_id, or device, com_object_ref_id, ga (the linked pair) ->
          {assignment_id}.
        - create_area: area_number [name] -> {area_id}.
        - create_line: area_id, line_number [name] -> {line_id}.
        - set_comment: kind [element_id] [field="comment"], text | paragraphs -> {kind, element_id,
          field}; kind, field, text and paragraphs as for project_set_comment.
        - set_description: kind, element_id, description -> {kind, element_id}; kind is area, line,
          group_range, group_address, space or function.
        - set_status: kind, status, element_id (a device: node_id or device_address; the project:
          none) -> {kind, element_id, status}; kind and status as for project_set_status.

        Use node_id from project_list_devices, product_ref_id from catalog_list_products, parameter
        ref_id from project_list_parameters as parameter_ref_id, and object ref_id from
        project_list_com_objects as com_object_ref_id. Use id from project_list_group_addresses as
        group_address_id; use id from project_list_group_ranges as group_range_id and a function
        id from project_spaces as function_id; use area id from project_topology as area_id. For
        assignment_id, use id
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
