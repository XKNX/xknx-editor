"""The effective KNX Data Secure state of group addresses and devices.

A device is commissioned securely ("Secure Commissioning" activated) when the project holds a Tool
Key for it (``<Security @ToolKey>``). A group address with Security ``On`` is secured and one with
``Off`` is not. In the default ``Auto`` mode it is secured when it is linked and every device linked
to it is commissioned securely: a device commissioned in the clear could not take part in its
secured communication. The state is derived from the links whenever it is needed, so a change of a
link or of a device's secure commissioning counts everywhere at once.

A main or middle group with Security ``On`` or ``Off`` forces it on every group address and group
below it: the nearest such group above an address decides, the address's own mode only counts
while none does. The own modes stay stored, so they count again once the group is back on ``Auto``.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from collections import defaultdict
from collections.abc import Callable, Iterable

from sqlalchemy import event
from sqlalchemy.orm import Session

from ..models import (
    Area,
    ComObject,
    ComObjectLink,
    Device,
    GroupAddress,
    GroupRange,
    Line,
    Segment,
)
from . import ip_secure
from .medium import MEDIUM_IP
from .security_domains import (
    SECURE,
    Domains,
    GroupSecurity,
    InterfaceLink,
    ObjectLink,
    SecurityContradiction,
    SegmentInfo,
)

#: The ``@Security`` values of a group address that let its links decide (``None`` = default).
AUTO_MODES: frozenset[str | None] = frozenset({None, "Auto"})
#: The ``@Security`` values of a group that force it on everything below it.
FORCED_MODES: frozenset[str | None] = frozenset({"On", "Off"})


def forcing_range(group_range: GroupRange | None) -> GroupRange | None:
    """The nearest group from ``group_range`` up (itself included) that forces its Security;
    None when none does."""
    while group_range is not None:
        if group_range.security_mode in FORCED_MODES:
            return group_range
        group_range = group_range.parent
    return None


def effective_mode(group_address: GroupAddress) -> str | None:
    """The Security mode that counts for a group address: the one of the nearest group above it
    that forces one, else its own."""
    forcing = forcing_range(group_address.group_range)
    return forcing.security_mode if forcing is not None else group_address.security_mode


def effective_modes(session: Session) -> dict[int, str | None]:
    """The Security mode that counts for every group address (see :func:`effective_mode`)."""
    ranges = {
        range_id: (parent_id, mode)
        for range_id, parent_id, mode in session.query(
            GroupRange.id, GroupRange.parent_id, GroupRange.security_mode
        )
    }
    forced: dict[int, str | None] = {}

    def forced_by(range_id: int | None) -> str | None:
        if range_id is None or range_id not in ranges:
            return None
        if range_id not in forced:
            parent_id, mode = ranges[range_id]
            forced[range_id] = mode if mode in FORCED_MODES else forced_by(parent_id)
        return forced[range_id]

    return {
        ga_id: forced_by(range_id) or mode
        for ga_id, range_id, mode in session.query(
            GroupAddress.id, GroupAddress.group_range_id, GroupAddress.security_mode
        )
    }


def security_attributes(device: Device) -> dict[str, str]:
    """The attributes of the device's stored ``<Security>`` (empty when it has none)."""
    raw = (device.extra_subtrees or {}).get("Security")
    if not raw:
        return {}
    try:
        return dict(ET.fromstring(raw).attrib)
    except ET.ParseError:
        return {}


def secure_commissioned(device: Device) -> bool:
    """Whether the device is commissioned securely: the project holds a Tool Key for it."""
    return bool(security_attributes(device).get("ToolKey"))


def is_secured(
    mode: str | None, devices: Iterable[int], commissioned: set[int]
) -> bool:
    """Whether a group address with Security ``mode``, linked to ``devices``, is secured, given
    the securely commissioned devices."""
    if mode == "On":
        return True
    if mode not in AUTO_MODES:
        return False
    linked = set(devices)
    return bool(linked) and linked <= commissioned


def commissioned_devices(session: Session) -> set[int]:
    """The ids of the devices commissioned securely."""
    return {
        device_id
        for device_id, subtrees in session.query(Device.id, Device.extra_subtrees)
        if subtrees and _has_tool_key(subtrees.get("Security"))
    }


def linked_devices(session: Session) -> dict[int, set[int]]:
    """Each linked group address's id -> the ids of the devices linked to it."""
    linked: dict[int, set[int]] = defaultdict(set)
    rows = session.query(ComObjectLink.group_address_id, ComObject.device_id).join(
        ComObject, ComObject.id == ComObjectLink.com_object_id
    )
    for group_address_id, device_id in rows:
        linked[group_address_id].add(device_id)
    return linked


def secured_group_addresses(session: Session) -> set[int]:
    """The ids of the group addresses that are secured now, in at least one security domain
    (see :func:`project_security`)."""
    return set(project_security(session).secured_anywhere)


# -- per security domain ------------------------------------------------------------------------

#: ``session.info`` key of what the project data alone cannot tell (see :class:`SecurityContext`).
CONTEXT_KEY = "security_context"
_CACHE_KEY = "_project_security"
_LISTENS_KEY = "_project_security_listens"


class SecurityContext:
    """What deciding the security domains needs from the products, for a project's session
    (``session.info[CONTEXT_KEY]``). The defaults know no product: no device is a coupler, every
    bus interface is on the device's own address."""

    def coupler(self, device: Device) -> tuple[bool, bool]:
        """Whether the device is a coupler, and whether it is one with the security extensions
        that can be used as a Security Proxy."""
        return False, False

    def interface_index(self, device: Device, ref_id: str) -> int:
        """The address index of the device's bus interface (0: the device's own address)."""
        return 0


class ProjectSecurity:
    """The security domains of a project and where its group addresses are secured in them."""

    def __init__(self, session: Session) -> None:
        context = session.info.get(CONTEXT_KEY)
        self.context = (
            context if isinstance(context, SecurityContext) else SecurityContext()
        )
        self.modes = effective_modes(session)
        self.commissioned = commissioned_devices(session)
        devices = session.query(Device).all()
        self.devices = {d.id: d for d in devices}
        self.domains = Domains(self._segments(session, devices))
        self._objects: dict[int, list[ObjectLink]] = defaultdict(list)
        self._linked: dict[int, set[int]] = defaultdict(set)
        # Every link: (com object id, device id, group address id, sending).
        self.links: list[tuple[int, int, int, bool]] = [
            (com_object_id, device_id, ga_id, sending)
            for com_object_id, device_id, ga_id, sending in session.query(
                ComObjectLink.com_object_id,
                ComObject.device_id,
                ComObjectLink.group_address_id,
                ComObjectLink.is_sending,
            ).join(ComObject, ComObject.id == ComObjectLink.com_object_id)
        ]
        by_object: dict[int, list[int]] = defaultdict(list)
        for com_object_id, _device_id, ga_id, _sending in self.links:
            by_object[com_object_id].append(ga_id)
        for com_object_id, device_id, ga_id, _sending in self.links:
            self._linked[ga_id].add(device_id)
            device = self.devices.get(device_id)
            if device is None or device.address is None or device.segment_id is None:
                continue
            self._objects[ga_id].append(
                ObjectLink(
                    segment=device.segment_id,
                    secure_device=device_id in self.commissioned,
                    others=tuple(g for g in by_object[com_object_id] if g != ga_id),
                )
            )
        self.interfaces = self._interface_links(session, devices)
        self.security = GroupSecurity(
            self.domains,
            self.modes.get,
            lambda ga: self._objects.get(ga, []),
            lambda ga: [link for link, _device in self.interfaces.get(ga, [])],
        )
        self._fallback: dict[int, bool] = {}
        self.secured_anywhere = {
            ga_id for ga_id in self.modes if self._secured_anywhere(ga_id)
        }

    def _segments(self, session: Session, devices: list[Device]) -> list[SegmentInfo]:
        on_segment: dict[int, list[Device]] = defaultdict(list)
        for device in sorted(devices, key=lambda d: d.id):
            if device.segment_id is not None:
                on_segment[device.segment_id].append(device)
        infos: list[SegmentInfo] = []
        rows = (
            session.query(Segment, Line.address, Area.address)
            .join(Line, Line.id == Segment.line_id)
            .join(Area, Area.id == Line.area_id)
            .all()
        )
        for segment, line, area in rows:
            ip = segment.medium_type == MEDIUM_IP
            members = on_segment.get(segment.id, [])
            if segment.number == 0:
                # The device numbered 0 feeds a line's main segment, whatever it is.
                feeding = next((d for d in members if d.address == 0), None)
                coupler = (
                    feeding
                    if feeding is not None and self.context.coupler(feeding)[0]
                    else None
                )
            else:
                feeding = next(
                    (
                        d
                        for d in members
                        if d.address is not None and self.context.coupler(d)[0]
                    ),
                    None,
                )
                coupler = feeding
            if feeding is None:
                proxied = not ip
            else:
                proxied = feeding.id in self.commissioned and all(
                    self.context.coupler(feeding)
                )
            infos.append(
                SegmentInfo(
                    id=segment.id,
                    area=area,
                    line=line,
                    number=segment.number,
                    ip=ip,
                    proxied=proxied,
                    coupler=coupler.id if coupler is not None else None,
                )
            )
        return infos

    def _interface_links(
        self, session: Session, devices: list[Device]
    ) -> dict[int, list[tuple[InterfaceLink, Device]]]:
        """The bus interfaces in use each group address is assigned to: those of addressed
        devices, on the device's own address or on an additional address that is set."""
        by_source = {
            source.rsplit("_", 1)[-1]: ga_id
            for ga_id, source in session.query(GroupAddress.id, GroupAddress.source_id)
            if source
        }
        links: dict[int, list[tuple[InterfaceLink, Device]]] = defaultdict(list)
        for device in devices:
            raw = (device.extra_subtrees or {}).get("BusInterfaces")
            if not raw or device.address is None or device.segment_id is None:
                continue
            addressed = {
                index
                for index, extra in enumerate(device.additional_addresses, start=1)
                if extra.address is not None
            }
            for element in ip_secure.bus_interfaces(raw).iter("BusInterface"):
                index = self.context.interface_index(device, element.get("RefId", ""))
                if index != 0 and index not in addressed:
                    continue
                secure = element.get("IsSecureEnabled", "true").lower() not in (
                    "false",
                    "0",
                )
                for connector in element.iter("Connector"):
                    ref = connector.get("GroupAddressRefId")
                    ga_id = by_source.get(ref.rsplit("_", 1)[-1]) if ref else None
                    if ga_id is not None:
                        links[ga_id].append(
                            (InterfaceLink(device.segment_id, secure), device)
                        )
        return links

    def _secured_anywhere(self, ga_id: int) -> bool:
        mode = self.modes.get(ga_id)
        if mode == "On":
            return True
        if mode not in AUTO_MODES:
            return False
        try:
            return SECURE in self.security.states(ga_id).values()
        except SecurityContradiction:
            return self._contradicted(ga_id)

    def _contradicted(self, ga_id: int) -> bool:
        """Links that contradict each other: the group address is decided as if the project
        had a single domain."""
        if ga_id not in self._fallback:
            self._fallback[ga_id] = is_secured(
                self.modes.get(ga_id), self._linked.get(ga_id, ()), self.commissioned
            )
        return self._fallback[ga_id]

    def domain_of(self, device_id: int) -> int | None:
        """The security domain of a device (None for one on no segment)."""
        device = self.devices.get(device_id)
        if device is None or device.segment_id is None:
            return None
        return self.domains.of_segment.get(device.segment_id)

    def secured_in(self, ga_id: int, domain: int | None) -> bool:
        """Whether the group address is secured in the domain."""
        mode = self.modes.get(ga_id)
        if mode == "On":
            return True
        if mode not in AUTO_MODES or domain is None:
            return False
        try:
            return self.security.secured(ga_id, domain) is True
        except SecurityContradiction:
            return self._contradicted(ga_id)

    def secured_for(self, device_id: int, ga_id: int) -> bool:
        """Whether the group address is secured in the device's domain."""
        return self.secured_in(ga_id, self.domain_of(device_id))

    def senders(
        self,
        ga_id: int,
        domain: int,
        can_send: Callable[[int], bool] | None = None,
    ) -> tuple[set[int], set[int], list[int]]:
        """Who may send on a group address secured in the domain: the devices (ids) with a
        group object sending on it that may send and are commissioned securely, the devices
        whose secured bus interfaces it is assigned to, both within the domains it reaches
        without being translated, and the Security Proxies that translate it into them."""
        try:
            reached, proxies = self.security.extent(ga_id, domain)
        except SecurityContradiction:
            reached, proxies = set(range(len(self.domains.domains))), []
        segments = {
            segment for d in reached for segment in self.domains.domains[d].segments
        }
        devices: set[int] = set()
        for com_object_id, device_id, linked_ga, sending in self.links:
            if linked_ga != ga_id or not sending or device_id not in self.commissioned:
                continue
            device = self.devices.get(device_id)
            if device is None or device.address is None:
                continue
            if device.segment_id not in segments:
                continue
            if can_send is None or can_send(com_object_id):
                devices.add(device_id)
        interfaces = {
            device.id
            for link, device in self.interfaces.get(ga_id, [])
            if link.secure and link.segment in segments
        }
        return devices, interfaces, proxies


def project_security(session: Session) -> ProjectSecurity:
    """The project's security domains and group address security (see :class:`ProjectSecurity`),
    computed once until the session's next flush or rollback."""
    cached = session.info.get(_CACHE_KEY)
    if isinstance(cached, ProjectSecurity) and not (
        session.new or session.dirty or session.deleted
    ):
        return cached
    if not session.info.get(_LISTENS_KEY):
        session.info[_LISTENS_KEY] = True
        for name in ("after_flush", "after_rollback", "after_soft_rollback"):
            event.listen(session, name, _forget)
    result = ProjectSecurity(session)
    session.info[_CACHE_KEY] = result
    return result


def set_security_context(session: Session, context: SecurityContext | None) -> None:
    """Give the session what the products tell about couplers, group objects and bus
    interfaces (see :class:`SecurityContext`); None forgets it."""
    if context is None:
        session.info.pop(CONTEXT_KEY, None)
    else:
        session.info[CONTEXT_KEY] = context
    session.info.pop(_CACHE_KEY, None)


def _forget(session: Session, *_args: object) -> None:
    session.info.pop(_CACHE_KEY, None)


def exports_key(group_address: GroupAddress, secured: bool) -> bool:
    """Whether the group address's key is exported: while it is secured, or unlinked in ``Auto``
    mode. One that communicates in the clear because of its links or its mode is exported without
    a key; the project keeps it, so securing the address again brings back the same key."""
    if secured:
        return True
    return effective_mode(group_address) in AUTO_MODES and not group_address.links


def _has_tool_key(raw: str | None) -> bool:
    if not raw:
        return False
    try:
        return bool(ET.fromstring(raw).get("ToolKey"))
    except ET.ParseError:
        return False
