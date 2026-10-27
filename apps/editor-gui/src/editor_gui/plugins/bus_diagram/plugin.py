"""Bus Diagram plugin: a layered Area -> Line -> Device map docked in the main area."""

from editor_gui.plugins.base import PanelDefinition, PluginAPI
from editor_gui.plugins.bus_diagram.flow import (
    GroupObject,
    Hub,
    device_group_objects,
    project_hubs,
)
from editor_gui.plugins.bus_diagram.strings import S
from editor_gui.plugins.bus_diagram.ui import BusDiagramPanel


class BusDiagramPlugin:
    name = "bus_diagram"

    def __init__(self, api: PluginAPI) -> None:
        self._api = api
        # Group-address hubs for the link view and the group names, cached per project and
        # revision (rebuilt only on an edit or when another project opens).
        self._links_key: tuple[int, int] | None = None
        self._links_cache: list[Hub] = []
        self._names_key: tuple[int, int] | None = None
        self._names_cache: dict[str, str] = {}
        # The group objects of the devices opened in the link view, cached like the hubs; each
        # with whether its channels are known (its application was loaded).
        self._objects_key: tuple[int, int] | None = None
        self._objects_cache: dict[int, tuple[bool, dict[int, GroupObject]]] = {}
        self._panel = BusDiagramPanel(
            get_devices=lambda: api.project.devices,
            get_space_tree=api.project.get_space_tree,
            on_select=self._select,
            is_open=lambda: api.project.is_open,
            get_link_graph=self._link_graph,
            get_group_names=self._group_names,
            get_group_objects=self._group_objects,
            take_request=api.project.take_bus_diagram_request,
        )
        self._panels = [
            PanelDefinition(
                name="bus_diagram",
                label=S.PANEL_BUS_DIAGRAM,
                dock="MainDockSpace",
                render=self._panel.render,
            ),
        ]

    def _select(self, node_id: int) -> None:
        device = self._api.project.find_device_by_node_id(node_id)
        if device is not None:
            self._api.project.selected_device = device
            self._api.project.focus_editor()

    def _link_graph(self) -> list[Hub]:
        key = self._source_key()
        if key is None:
            return []
        if self._links_key != key:
            self._links_cache = project_hubs(self._api.project)
            self._links_key = key
        return self._links_cache

    def _group_objects(self, node_id: int) -> dict[int, GroupObject]:
        """The group objects of a device the link view lists. Their channels come from the
        device's parameters, once its application is loaded (in the background, as for the
        Editor); until then they are listed without."""
        key = self._source_key()
        if key is None:
            return {}
        if self._objects_key != key:
            self._objects_cache.clear()
            self._objects_key = key
        cached = self._objects_cache.get(node_id)
        if cached is not None and cached[0]:
            return cached[1]
        project = self._api.project
        device = project.find_device_by_node_id(node_id)
        if device is None:
            return {}
        ready = project.application_ready(device)
        if cached is not None and not ready:
            return cached[1]
        objects = device_group_objects(device, with_channels=ready)
        self._objects_cache[node_id] = (ready, objects)
        return objects

    def _source_key(self) -> tuple[int, int] | None:
        """What the cached reads depend on: the revision and the device list (a new one for every
        project opened); ``None`` while a project is still loading."""
        project = self._api.project
        if project.loading:
            return None
        return (project.revision, id(project.devices))

    def _group_names(self) -> dict[str, str]:
        """The names of the main groups ("1") and middle groups ("1/2") by their number, from the
        group-address range tree; cached like the link graph."""
        key = self._source_key()
        if key is None:
            return {}
        if self._names_key != key:
            names: dict[str, str] = {}
            stack = list(self._api.project.get_group_range_tree())
            while stack:
                node = stack.pop()
                if node.address and node.name:
                    names.setdefault(node.address, node.name)
                stack.extend(node.children)
            self._names_cache = names
            self._names_key = key
        return self._names_cache

    @property
    def panels(self) -> list[PanelDefinition]:
        return self._panels

    def on_load(self) -> None:
        pass

    def on_unload(self) -> None:
        pass
