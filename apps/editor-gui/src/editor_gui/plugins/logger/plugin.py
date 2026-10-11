from editor_gui.plugins.base import PanelDefinition
from editor_gui.plugins.logger.service import LogService
from editor_gui.plugins.logger.strings import S
from editor_gui.plugins.logger.ui import LogPanel


class LoggerPlugin:
    name = "logger"

    def __init__(self, service: LogService) -> None:
        self._service = service
        self._panel = LogPanel(
            get_records=service.get_records,
            on_clear=service.clear,
        )
        self._panels = [
            PanelDefinition(
                name="logger",
                label=S.PANEL_LOGGER,
                dock="BottomSpace",
                render=self._panel.render,
            )
        ]

    @property
    def panels(self) -> list[PanelDefinition]:
        return self._panels

    def on_load(self) -> None:
        pass

    def on_unload(self) -> None:
        pass
