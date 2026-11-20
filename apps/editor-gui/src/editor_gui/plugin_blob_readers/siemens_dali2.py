"""Display Siemens DALI-2 cached inventory without changing its metadata."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any, cast

from editor_gui.plugins.project.strings import S
from editor_gui.widgets.dpt_value import title_case

if TYPE_CHECKING:
    from editor_gui.plugin_params import PluginSectionView


def read(data: bytes, name: str = "") -> list[PluginSectionView]:
    """Return read-only cached gear and sensor metadata from its named JSON blob."""
    from editor_gui.plugin_params import PluginParameter, PluginSectionView

    if name != "Dali2DcaBinaryData":
        return []
    try:
        raw: object = json.loads(data)
    except (UnicodeDecodeError, ValueError):
        return []
    if not isinstance(raw, dict):
        return []
    root = cast(dict[str, Any], raw)
    sections: list[PluginSectionView] = []

    def append(section: str, key: str, fields: dict[str, Any]) -> None:
        rows: list[PluginParameter] = []

        def walk(value: object, path: str, label: str) -> None:
            if isinstance(value, dict):
                for field, child in cast(dict[str, Any], value).items():
                    walk(child, path + "/" + field, label + " / " + title_case(field))
                return
            rendered = (
                json.dumps(value, ensure_ascii=False)
                if not isinstance(value, str)
                else value
            )
            rows.append(
                PluginParameter(
                    ref_id="siemens_dali2:" + path,
                    name=path,
                    label=label,
                    value=rendered,
                    default_value="",
                    section=section,
                    display_order=len(rows),
                    writable=False,
                )
            )

        for field, value in fields.items():
            walk(value, key + "/" + field, title_case(field))
        sections.append(PluginSectionView(section=section, parameters=rows))

    for line in (1, 2):
        line_label = S.DALI_CHANNEL_N.format(n=line)
        key = f"RuntimeDataEcgLine{line}"
        ecgs = root.get(key)
        if isinstance(ecgs, dict):
            for address, values in cast(dict[str, Any], ecgs).items():
                if isinstance(values, dict):
                    append(
                        f"{line_label} / {S.E64_ECG} {address}",
                        key + "/" + address,
                        cast(dict[str, Any], values),
                    )
        key = f"RuntimeDataSensorLine{line}"
        sensor_data = root.get(key)
        if not isinstance(sensor_data, dict):
            continue
        sensor_data = cast(dict[str, Any], sensor_data)
        sensors = sensor_data.get("FoundSensorsWithInstancesSortedByShortAddress")
        if not isinstance(sensors, dict):
            continue
        for address, values in cast(dict[str, Any], sensors).items():
            if isinstance(values, dict):
                append(
                    f"{line_label} / {S.E64_INPUT_DEVICES} / {address}",
                    key + "/FoundSensorsWithInstancesSortedByShortAddress/" + address,
                    cast(dict[str, Any], values),
                )
    return sections
