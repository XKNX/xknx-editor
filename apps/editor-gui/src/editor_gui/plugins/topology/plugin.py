"""Topology plugin: a layered Area -> Line -> Device map docked in the main area."""

from editor_gui.plugins.base import PanelDefinition, PluginAPI
from editor_gui.plugins.topology.strings import S
from editor_gui.plugins.topology.ui import TopologyPanel


class TopologyPlugin:
    name = "topology"

    def __init__(self, api: PluginAPI) -> None:
        self._api = api
        # Group-address hubs for the link view, cached by project revision (rebuilt only on edit).
        self._links_key: int | None = None
        self._links_cache: list[
            tuple[int, str, str, tuple[tuple[int, bool, bool], ...]]
        ] = []
        self._panel = TopologyPanel(
            get_devices=lambda: api.project.devices,
            get_space_tree=api.project.get_space_tree,
            on_select=self._select,
            is_open=lambda: api.project.is_open,
            get_link_graph=self._link_graph,
            get_main_names=self._main_group_names,
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
        revision = self._api.project.revision
        if self._links_key != revision:
            self._links_cache = self._build_link_graph()
            self._links_key = revision
        return self._links_cache

    def _build_link_graph(
        self,
    ) -> list[tuple[int, str, str, tuple[tuple[int, bool, bool], ...]]]:
        """One hub per group address: ``(ga_id, address, name, participants)`` where each
        participant is ``(device node id, sends, receives)``.

        Direction is per the com-object's flags, gated by Communication: a device SENDS on a group
        address if it responds to reads (Read) or spontaneously transmits on its sending association
        (Transmit on the sending link); it RECEIVES if it is written to (Write) or takes updates
        (Update). Every group address with at least one participant is kept (a lone participant is a
        clue to an incomplete association); the view decides what to show."""
        part: dict[int, dict[int, list[bool]]] = {}
        for device in self._api.project.devices:
            for com_object in device.com_objects:
                if com_object.db_id is None:
                    continue
                flags = com_object.flags
                if not flags.communication:
                    continue
                receives = flags.write or flags.update
                for link in self._api.project.get_links_for_com_object(
                    com_object.db_id
                ):
                    sends = flags.read or (flags.transmit and link.is_sending)
                    if not (sends or receives):
                        continue
                    bits = part.setdefault(link.group_address_id, {}).setdefault(
                        device.node_id, [False, False]
                    )
                    bits[0] = bits[0] or sends
                    bits[1] = bits[1] or receives
        hubs: list[tuple[int, str, str, tuple[tuple[int, bool, bool], ...]]] = []
        for ga_id, devs in part.items():
            ga = self._api.project.get_group_address(ga_id)
            address = ga.address if ga is not None else str(ga_id)
            name = ga.name if ga is not None else ""
            participants = tuple((nid, bits[0], bits[1]) for nid, bits in devs.items())
            hubs.append((ga_id, address, name, participants))
        return hubs

    def _main_group_names(self) -> dict[int, str]:
        """Main group number -> its name, from the project's group-address range tree (so the hub
        blocks can be titled "1/*  Lighting" rather than just "1/*")."""

        def first_address(rng: object) -> str | None:
            # Range-tree group addresses carry the formatted string in ``text`` (``address`` is the
            # raw integer), unlike project.get_group_address whose ``address`` is already formatted.
            for ga in getattr(rng, "group_addresses", []):
                text = getattr(ga, "text", "") or ""
                if text:
                    return text
            for child in getattr(rng, "children", []):
                found = first_address(child)
                if found:
                    return found
            return None

        names: dict[int, str] = {}
        for top in self._api.project.get_group_range_tree():
            address = first_address(top)
            if address is None or not top.name:
                continue
            try:
                main = int(address.split("/", 1)[0])
            except ValueError:
                continue
            names.setdefault(main, top.name)
        return names

    @property
    def panels(self) -> list[PanelDefinition]:
        return self._panels

    def on_load(self) -> None:
        pass

    def on_unload(self) -> None:
        pass
