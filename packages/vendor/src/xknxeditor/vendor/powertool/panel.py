"""Panel element identities, parent choices and control placement."""

from __future__ import annotations

from dataclasses import replace

from .family import FamilyConfig, FamilyEvaluation, Node


def _fields(application: Node) -> dict[str, Node]:
    result: dict[str, Node] = {}

    def walk(node: Node) -> None:
        for child in node.parameters:
            result[child.key.rsplit("+", 1)[-1].removeprefix("Par_")] = child
            if child.spec["extension"] != "None":
                result[child.spec["extension"]] = child
            walk(child)

    walk(application)
    return result


def _number(state: FamilyEvaluation, fields: dict[str, Node], key: str) -> int:
    node = fields.get(key)
    return int(state.stored(node) or 0) if node is not None else 0


def _size(state: FamilyEvaluation, app: Node) -> tuple[int, int]:
    fields = _fields(app)
    identifier = _number(state, fields, "ApplicationId")
    rows = 4 if identifier == 49725 else 2 if identifier in (49720, 49723) else 1
    columns = (2 if identifier in (49715, 49717) else 1) + _number(
        state, fields, "ControlSize"
    )
    extra = fields.get("ControlSizeRow")
    if (
        app.key in ("App_RGBW_Control", "App_Display")
        and extra is not None
        and state.property(extra, "Exists")
    ):
        rows = max(rows, 2)
    return rows, columns


def _cells(position: int, rows: int, columns: int) -> set[int]:
    if position < 0 or position // 4 + rows > 4 or position % 4 + columns > 4:
        return set()
    return {
        position + row * 4 + column for row in range(rows) for column in range(columns)
    }


def reset_dependents(
    config: FamilyConfig, state: FamilyEvaluation, channel: Node
) -> FamilyConfig:
    if config.database_code not in (20, 21) or channel.application is None:
        return config
    kind = channel.application.spec.get("application_type")
    if kind not in ("Floor", "Room", "Page"):
        return config
    removed = {channel.path}
    parents = {state.channel_number(channel)}
    for child_kind in ("Room", "Page") if kind == "Floor" else ("Page",):
        for child in state.channels:
            app = child.application
            if (
                app is not None
                and state.active(child)
                and app.spec.get("application_type") == child_kind
                and _number(state, _fields(app), "ParentId") in parents
            ):
                removed.add(child.path)
                parents.add(state.channel_number(child))
    prefixes = tuple(p + "/" for p in removed)
    options = {k: v for k, v in config.options.items() if not k.startswith(prefixes)}
    for path in removed - {channel.path}:
        options[path] = 0
    return replace(
        config,
        options=options,
        values={k: v for k, v in config.values.items() if not k.startswith(prefixes)},
    )


def choices(state: FamilyEvaluation, node: Node) -> tuple[tuple[int, str], ...] | None:
    app = node.application
    channel = node.channel
    if app is None or channel is None or state.config.database_code not in (20, 21):
        return None
    extension = node.spec["extension"]
    kind = app.spec.get("application_type")
    if extension == "Linked":
        result: list[tuple[int, str]] = []
        for candidate in state.channels:
            other = candidate.application
            if other is None:
                continue
            number = state.channel_number(candidate)
            fields = _fields(other)
            if (
                (
                    state.active(candidate)
                    and other.spec.get("application_type") == "Page"
                    and channel.parent is not None
                    and candidate.parent is channel.parent.parent
                )
                or number in (4300, 4400)
                or (number == 4200 and _number(state, fields, "Udc") == 1)
                or (number == 5200 and _number(state, fields, "UseAlarmmessages") == 1)
            ):
                name = fields.get("Name")
                result.append(
                    (
                        number,
                        str(state.stored(name)) if name else candidate.spec["label"],
                    )
                )
        return tuple(result)
    if extension == "Position" and kind == "Control":
        occupied: set[int] = set()
        for sibling in state.channels:
            other = sibling.application
            if (
                sibling is channel
                or sibling.parent is not channel.parent
                or not state.active(sibling)
                or other is None
                or other.spec.get("application_type") != "Control"
            ):
                continue
            occupied.update(
                _cells(_number(state, _fields(other), "Position"), *_size(state, other))
            )
        rows, columns = _size(state, app)
        return tuple(
            (i, str(i))
            for i in range(16)
            if (cells := _cells(i, rows, columns)) and not cells & occupied
        )
    if extension == "ParentId" and kind in ("Page", "Room"):
        parent_kind = "Room" if kind == "Page" else "Floor"
        candidates = [(1000, "Home")]
        candidates.extend(
            (
                state.channel_number(c),
                str(state.stored(_fields(c.application)["Name"]))
                if "Name" in _fields(c.application)
                else c.spec["label"],
            )
            for c in state.channels
            if state.active(c)
            and c.application is not None
            and c.application.spec.get("application_type") == parent_kind
        )
        if kind == "Page" and state.config.database_code == 20:
            counts: dict[int, int] = {}
            for sibling in state.channels:
                other = sibling.application
                if (
                    sibling is channel
                    or not state.active(sibling)
                    or other is None
                    or other.spec.get("application_type") != "Page"
                ):
                    continue
                parent = _number(state, _fields(other), "ParentId")
                counts[parent] = counts.get(parent, 0) + 1
            candidates = [
                (value, label)
                for value, label in candidates
                if counts.get(value, 0) < (10 if value == 1000 else 3)
            ]
        return tuple(candidates)
    return None


def initialize_application(
    config: FamilyConfig, state: FamilyEvaluation, path: str
) -> FamilyConfig:
    if config.database_code not in (20, 21):
        return config
    channel = state.nodes[path]
    app = channel.application
    if app is None or app.spec["inactive"]:
        return config
    kind = app.spec.get("application_type")
    if kind not in ("Floor", "Room", "Page", "Control", "StApplication"):
        return config
    fields = _fields(app)
    values = dict(config.values)
    identifier = fields.get("ControlId")
    if identifier is not None:
        values[identifier.path] = state.channel_number(channel)
    parent = fields.get("ParentId")
    if kind == "Control" and parent is not None and channel.parent is not None:
        values[parent.path] = state.channel_number(channel.parent)
    for key in ("ParentId", "Position"):
        field = fields.get(key)
        available = choices(state, field) if field is not None else None
        if available is not None:
            if not available:
                raise ValueError("No available panel position or parent")
            assert field is not None
            values[field.path] = available[0][0]
    for field in fields.values():
        if field.spec["extension"] == "Linked":
            available = choices(state, field)
            if available and state.stored(field) not in dict(available):
                values[field.path] = available[0][0]
    result = replace(config, values=values)
    validate(FamilyEvaluation(result))
    return result


def validate(state: FamilyEvaluation) -> None:
    if state.config.database_code not in (20, 21):
        return
    controls = 0
    for channel in state.channels:
        app = channel.application
        if not state.active(channel) or app is None:
            continue
        kind = app.spec.get("application_type")
        if kind not in ("Floor", "Room", "Page", "Control", "StApplication"):
            continue
        fields = _fields(app)
        if "ControlId" in fields and _number(
            state, fields, "ControlId"
        ) != state.channel_number(channel):
            raise ValueError("Panel element identity does not match its channel")
        if kind == "Control":
            controls += 1
            if channel.parent is None or _number(
                state, fields, "ParentId"
            ) != state.channel_number(channel.parent):
                raise ValueError("Panel control has an invalid parent")
        for key in ("ParentId", "Position", "Linked"):
            field = fields.get(key)
            available = (
                choices(state, field)
                if field is not None and state.property(field, "Exists")
                else None
            )
            if available is not None and _number(state, fields, key) not in dict(
                available
            ):
                raise ValueError("Panel position or parent is unavailable")
    if state.config.database_code == 21 and controls > 64:
        raise ValueError("Panel control count exceeds 64")
