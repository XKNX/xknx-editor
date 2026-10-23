"""Topology plugin: a layered Area -> Line -> Device map docked in the main area."""

from editor_gui.plugins.base import PanelDefinition, PluginAPI
from editor_gui.plugins.topology.strings import S
from editor_gui.plugins.topology.ui import TopologyPanel


class TopologyPlugin:
    name = "topology"

    def __init__(self, api: PluginAPI) -> None:
        self._api = api
        # Group-address hubs for the link view and the group names, cached per project and
        # revision (rebuilt only on an edit or when another project opens).
        self._links_key: tuple[int, int] | None = None
        self._links_cache: list[
            tuple[int, str, str, tuple[tuple[int, bool, bool], ...]]
        ] = []
        self._names_key: tuple[int, int] | None = None
        self._names_cache: dict[str, str] = {}
        self._panel = TopologyPanel(
            get_devices=lambda: api.project.devices,
            get_space_tree=api.project.get_space_tree,
            on_select=self._select,
            is_open=lambda: api.project.is_open,
            get_link_graph=self._link_graph,
            get_group_names=self._group_names,
        )
        self._panels = [
            PanelDefinition(
                name="topology",
                label=S.PANEL_TOPOLOGY,
                dock="MainDockSpace",
                render=self._panel.render,
            ),
        ]

    def _select(self, node_id: int) -> None:
        device = self._api.project.find_device_by_node_id(node_id)
        if device is not None:
            self._api.project.selected_device = device
            self._api.project.focus_editor()

    def _link_graph(
        self,
    ) -> list[tuple[int, str, str, tuple[tuple[int, bool, bool], ...]]]:
        key = self._source_key()
        if key is None:
            return []
        if self._links_key != key:
            self._links_cache = self._build_link_graph()
            self._links_key = key
        return self._links_cache

    def _source_key(self) -> tuple[int, int] | None:
        """What the cached reads depend on: the revision and the device list (a new one for every
        project opened); ``None`` while a project is still loading."""
        project = self._api.project
        if project.loading:
            return None
        return (project.revision, id(project.devices))

    def _build_link_graph(
        self,
    ) -> list[tuple[int, str, str, tuple[tuple[int, bool, bool], ...]]]:
        """One hub per group address: ``(ga_id, address, name, participants)`` where each
        participant is ``(device node id, sends, receives)``.

        Direction follows the com-object's flags, gated by Communication: a device SENDS on a group
        address when it transmits there (Transmit on its sending link); it RECEIVES when it is
        written to (Write) or takes updates (Update). Answering read requests is not sending. Every
        group address with at least one participant is kept; the view decides what to show."""
        part: dict[int, dict[int, list[bool]]] = {}
        for link in self._api.project.device_links():
            if not link.communication:
                continue
            sends = link.transmit and link.is_sending
            receives = link.write or link.update
            if not (sends or receives):
                continue
            bits = part.setdefault(link.group_address_id, {}).setdefault(
                link.device_id, [False, False]
            )
            bits[0] = bits[0] or sends
            bits[1] = bits[1] or receives
        gas = {ga.id: ga for ga in self._api.project.group_addresses}
        hubs: list[tuple[int, str, str, tuple[tuple[int, bool, bool], ...]]] = []
        for ga_id, devs in part.items():
            ga = gas.get(ga_id)
            address = ga.address if ga is not None else str(ga_id)
            name = ga.name if ga is not None else ""
            participants = tuple((nid, bits[0], bits[1]) for nid, bits in devs.items())
            hubs.append((ga_id, address, name, participants))
        return hubs

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
