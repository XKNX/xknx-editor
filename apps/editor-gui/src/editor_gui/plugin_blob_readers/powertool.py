"""Present the native push-button function and dependent parameter tree."""

from editor_gui.plugin_params import PluginParameter, PluginSectionView
from editor_gui.plugins.project.strings import S
from xknxeditor.vendor.powertool import BLOB_NAME
from xknxeditor.vendor.powertool.family import FamilyConfig
from xknxeditor.vendor.powertool.model import (
    EvaluatedChannel,
    channel_exists,
    channel_functions,
    functions,
)
from xknxeditor.vendor.powertool.native import load as from_json_bytes
from xknxeditor.vendor.powertool.project_blob import (
    ORIGINAL_BLOB_NAME,
    SavedParameter,
    read_project_blob,
)


def read_original(data: bytes) -> list[PluginSectionView]:
    """Expose stored values without granting editing or image capabilities."""
    project = read_project_blob(data)
    section = ORIGINAL_BLOB_NAME
    metadata = {
        "database": project.database,
        "database_version": project.database_version,
        "product_type": project.product_type,
        "database_code": str(project.code),
        "metadata_verified": str(project.metadata_verified),
    }
    sections = [
        PluginSectionView(
            section,
            [
                PluginParameter(
                    f"powertool-original:{key}",
                    key,
                    S.PLUGIN_ORIGINAL_FIELDS[key],
                    value,
                    "",
                    section,
                    index,
                    False,
                )
                for index, (key, value) in enumerate(metadata.items())
            ],
            collapsed=True,
        )
    ]

    def rows(
        parameters: tuple[SavedParameter, ...], group: str
    ) -> list[PluginParameter]:
        def display(parameter: SavedParameter, value: object) -> str:
            if value is None:
                return ""
            if type(value) is int and 0 <= value < len(parameter.options):
                return parameter.options[value]
            return str(value)

        return [
            PluginParameter(
                f"powertool-original:{p.path}",
                p.path,
                p.label,
                display(p, p.display_value),
                display(p, p.default_value),
                group,
                index,
                False,
                tuple((str(i), label) for i, label in enumerate(p.options)),
                p.group,
                p.group_key,
                str(p.value),
            )
            for index, p in enumerate(parameters)
            if p.value is not None and not p.internal and p.kind != "V_PAGE"
        ]

    technical: list[PluginSectionView] = []

    def append_parameters(
        parameters: tuple[SavedParameter, ...], group: str
    ) -> list[PluginParameter]:
        internal = [p for p in parameters if p.internal and p.value is not None]
        if internal:
            title = f"{group}: {S.PLUGIN_INTERNAL_VALUES}"
            technical.append(
                PluginSectionView(
                    title,
                    [
                        PluginParameter(
                            f"powertool-original:{p.path}",
                            p.path,
                            p.label,
                            str(p.value),
                            "",
                            title,
                            index,
                            False,
                        )
                        for index, p in enumerate(internal)
                    ],
                    collapsed=True,
                )
            )
        return rows(parameters, group)

    if project.parameters:
        group = f"{section}: {S.CONFIGURE_PARAMETERS}"
        sections.append(
            PluginSectionView(group, append_parameters(project.parameters, group))
        )
    for index, channel in enumerate(project.channels):
        group = f"{index + 1}: {channel.label}"
        parameters = append_parameters(channel.parameters, group)
        if channel.application:
            parameters.insert(
                0,
                PluginParameter(
                    f"powertool-original:{channel.path}",
                    "application",
                    S.CONFIGURE_APPLICATION,
                    channel.application,
                    "",
                    group,
                    -1,
                    False,
                ),
            )
        if parameters:
            sections.append(PluginSectionView(group, parameters))
    sections.extend(technical)
    return sections


def read(data: bytes, name: str = BLOB_NAME) -> list[PluginSectionView]:
    if name == ORIGINAL_BLOB_NAME:
        return read_original(data)
    if name != BLOB_NAME:
        return []
    try:
        config = from_json_bytes(data)
    except ValueError:
        return []
    if isinstance(config, FamilyConfig):
        return read_family(config)
    sections: list[PluginSectionView] = []
    for channel, settings in enumerate(config.channels):
        if not channel_exists(config, channel):
            continue
        group = (
            "LED"
            if config.variant == "01" and channel == 2
            else f"{channel // 2 + 1}: {'Upper / Rocker' if channel % 2 == 0 else 'Lower'}"
        )
        evaluated = EvaluatedChannel(settings)
        rows = [
            PluginParameter(
                ref_id=f"powertool:{channel}:function",
                name="function",
                label="Function",
                value=settings.function,
                default_value="",
                section=group,
                display_order=0,
                writable=True,
                choices=tuple(
                    (k, functions()[k].label)
                    for k in channel_functions(config.variant, channel)
                ),
            )
        ]
        for index, f in enumerate(evaluated.function.fields):
            if not f.visible or f.kind == "page" or not evaluated.exists(index):
                continue
            rows.append(
                PluginParameter(
                    ref_id=f"powertool:{channel}:{f.key}",
                    name=f.key,
                    label=f.label,
                    value=str(evaluated.value(index)),
                    default_value=str(f.default),
                    section=group,
                    display_order=index + 1,
                    writable=True,
                    choices=tuple(
                        (str(i), label) for i, (label, _value) in enumerate(f.options)
                    ),
                )
            )
        sections.append(PluginSectionView(group, rows))
    return sections


def read_family(config: "FamilyConfig") -> list[PluginSectionView]:
    from xknxeditor.vendor.powertool.family import FamilyEvaluation, Node
    from xknxeditor.vendor.powertool.panel import choices as panel_choices

    evaluated = FamilyEvaluation(config)
    sections: list[PluginSectionView] = []
    details: list[PluginSectionView] = []

    def append_fields(node: Node, title: str, rows: list[PluginParameter]) -> None:
        internal: list[PluginParameter] = []

        def walk(parent: Node, group: str = "", group_key: str = "") -> None:
            for parameter in parent.parameters:
                if not evaluated.property(parameter, "Exists"):
                    continue
                spec = parameter.spec
                if spec["value_kind"] == "V_PAGE":
                    walk(
                        parameter,
                        " / ".join(filter(None, (group, spec["label"]))),
                        parameter.path,
                    )
                    continue
                visible = bool(evaluated.property(parameter, "Visible"))
                dynamic_choices = panel_choices(evaluated, parameter)
                row = PluginParameter(
                    ref_id=f"powertool:family:{parameter.path}",
                    name=parameter.path,
                    label=spec["label"],
                    value=str(evaluated.display(parameter)),
                    default_value=str(spec["default"]),
                    section=title,
                    display_order=len(rows) + len(internal),
                    writable=visible,
                    choices=tuple(
                        (str(value), label) for value, label in dynamic_choices
                    )
                    if dynamic_choices is not None
                    else tuple(
                        (str(i), o["label"])
                        for i, o in enumerate(spec.get("options", []))
                    ),
                    group=group,
                    group_key=group_key,
                    raw_value=str(evaluated.stored(parameter)),
                )
                (rows if visible else internal).append(row)
                walk(parameter, group, group_key)

        walk(node)
        if internal:
            details.append(
                PluginSectionView(
                    f"{title}: {S.PLUGIN_INTERNAL_VALUES}", internal, collapsed=True
                )
            )

    for device in evaluated.devices:
        if (
            config.database_code is not None
            and device.path in evaluated.product_choices
        ):
            options = evaluated.product_choices[device.path]
            title = (
                device.parent.spec["label"] if device.parent else device.spec["label"]
            )
            label = S.CONFIGURE_HARDWARE
            if device.path.rsplit("/", 2)[-2] == "subsystems" and device.parent:
                label = device.parent.spec["subsystem_labels"][
                    int(device.path.rsplit("/", 1)[-1])
                ]
            sections.append(
                PluginSectionView(
                    title,
                    [
                        PluginParameter(
                            ref_id=f"powertool:family:{device.path}",
                            name=device.path,
                            label=label,
                            value=str(options.index(device.key)),
                            default_value="0",
                            section=title,
                            display_order=0,
                            writable=not device.spec.get("disabled", False),
                            choices=tuple(
                                (str(i), evaluated.schema["nodes"][key]["label"])
                                for i, key in enumerate(options)
                            ),
                        )
                    ],
                )
            )
        if config.database_code is not None and device.path in evaluated.count_limits:
            title = device.spec["label"]
            sections.append(
                PluginSectionView(
                    title,
                    [
                        PluginParameter(
                            ref_id=f"powertool:family:{device.path}/channel_count",
                            name="channel_count",
                            label=title,
                            value=str(len(device.channels)),
                            default_value="1",
                            section=title,
                            display_order=0,
                            writable=True,
                            choices=tuple(
                                (str(i), str(i))
                                for i in range(
                                    1, evaluated.count_limits[device.path] + 1
                                )
                            ),
                        )
                    ],
                )
            )
        if device.parameters:
            title = device.spec["label"]
            rows: list[PluginParameter] = []
            append_fields(device, title, rows)
            sections.append(PluginSectionView(title, rows))
    for index, channel in enumerate(evaluated.channels):
        if not evaluated.channel_exists(channel):
            continue
        title = f"{index + 1}: {channel.spec['label']}"
        rows = []
        options = channel.spec["applications"]
        if len(options) > 1:
            rows.append(
                PluginParameter(
                    ref_id=f"powertool:family:{channel.path}",
                    name="function",
                    label=S.CONFIGURE_APPLICATION,
                    value=str(config.options.get(channel.path, 0)),
                    default_value="0",
                    section=title,
                    display_order=-1,
                    writable=True,
                    choices=tuple(
                        (str(i), evaluated.schema["nodes"][key]["label"])
                        for i, key in enumerate(options)
                    ),
                )
            )
        append_fields(channel, title, rows)
        if evaluated.active(channel):
            assert channel.application is not None
            append_fields(channel.application, title, rows)
        if rows:
            sections.append(PluginSectionView(title, rows))
    return sections + details
