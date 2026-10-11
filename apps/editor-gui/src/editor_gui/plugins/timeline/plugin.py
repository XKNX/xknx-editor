"""Timeline plugin: plot decoded numeric group-value telegrams over time (BottomSpace)."""

from typing import Any

from editor_gui.plugins.base import PanelDefinition, PluginAPI
from editor_gui.plugins.timeline.strings import S
from editor_gui.plugins.timeline.ui import GaInfo, TimelinePanel


class TimelinePlugin:
    name = "timeline"

    def __init__(self, api: PluginAPI, get_telegrams: Any) -> None:
        self._api = api
        self._panel = TimelinePanel(
            get_telegrams=get_telegrams,
            get_ga_info=self._ga_info,
            is_open=lambda: api.project.is_open,
        )
        self._panels = [
            PanelDefinition(
                name="timeline",
                label=S.PANEL_TIMELINE,
                dock="BottomSpace",
                render=self._panel.render,
            ),
        ]

    def _ga_info(self) -> list[GaInfo]:
        """Group addresses that carry a DPT (the plottable ones), as (raw, label, dpt)."""
        gas: list[Any] = self._api.project.group_addresses
        return [
            (ga.raw, f"{ga.address}  {ga.name}", ga.datapoint_type)
            for ga in gas
            if ga.datapoint_type
        ]

    @property
    def panels(self) -> list[PanelDefinition]:
        return self._panels

    def on_load(self) -> None:
        pass

    def on_unload(self) -> None:
        pass
