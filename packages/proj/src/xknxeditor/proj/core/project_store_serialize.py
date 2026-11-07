"""Serialize a project document into the ETS6 internal project-store streams.

Companion to :mod:`project_store` (the integrity sidecars). A ``.knxproj`` is the interchange format;
the local project store is a different, internal shape: one XML stream per object-model section --
``P`` (project), ``B`` (buildings), ``I`` (installation), ``U`` (unassigned devices), ``G`` (group
addresses), ``PT`` (traces) and one ``P-XXXX-0_A-N`` per topology area (devices live there).

The streams carry the *same* object model the ``.knxproj`` export reads, in a compact shape
(``DI``/``PI``/``CI``/``ModInst``/``GrpObjTree``) that shares the exporter's id scheme (``GA-n`` link
tokens, ``P-XXXX-0_DI-n`` device ids) and its write gate for com-object instances. The ref/id logic
is therefore reused from :mod:`knxproj_export` rather than duplicated.

Scope of this writer (v1): topology, devices (parameter/com-object instances, module instances,
the group-object tree), group addresses, buildings with their device assignments, and the project
header. Building *functions* and per-device ``BinaryData`` are not emitted yet. A device's
``IPConfig`` and extra individual addresses are, under the store's short names ``IP`` and ``AA``.
"""

from __future__ import annotations

import datetime
import re
import shutil
import xml.etree.ElementTree as ET
import zipfile
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path, PurePosixPath
from uuid import uuid4

from sqlalchemy.orm import object_session

from xknxeditor.proj.core.dpt import normalize_datapoint_type
from xknxeditor.proj.core.ga_security import secured_group_addresses
from xknxeditor.proj.core.knxproj_export import (
    assign_ids,
    enable,
    group_object_tree_instances,
    has_override,
    localname,
    relidref,
    tree_covers,
)
from xknxeditor.proj.core.project_store import (
    StoreFormat,
    project_mask,
    write_store_descriptor,
    write_store_stream,
    write_store_version,
)
from xknxeditor.proj.models import (
    UNDEFINED_STATUS,
    ComObject,
    Device,
    GroupRange,
    Installation,
    Project,
    Space,
)

#: A fixed BCU key value, as written by a genuine store (verified against two reference stores).
_BCU_KEY = "3989547399"
_IP_ROUTING_LATENCY_TOLERANCE = "2000"

#: A valid ETS project id (``P-`` + hex). Guards any filesystem path derived from a project id.
_PID_RE = re.compile(r"P-[0-9A-Fa-f]+\Z")

#: Medium type for a line whose segments did not survive the model (TP, as a genuine ETS 5 store
#: writes on every line of a TP installation).
_DEFAULT_MEDIUM_TYPE = "MT-0"

#: Sections an ETS 5 store always accounts for, none of which this writer has content for: addin
#: data, bus access, trades ("Gewerke"), history entries, IP-secure data, split (``S``), todo items
#: and user files. Written as empty (hash sidecar only), matching a genuine ETS 5 ProjectStore.
_ETS5_LEGACY_STREAMS = ("AI", "BA", "D", "HE", "IPSD", "S", "T", "UF")


def _is_safe_member(member: str) -> bool:
    """Whether a product-member path stays inside the store folder (no traversal / absolute path)."""
    pure = PurePosixPath(member)
    return not pure.is_absolute() and ".." not in pure.parts


@dataclass
class StoreStreams:
    """The serialized store streams plus the identifiers the integrity sidecars key off."""

    pid: str
    guid: str
    #: Stream name -> file bytes (incl. BOM), e.g. ``{"P": b"...", "P-0273-0_A-1": b"..."}``.
    streams: dict[str, bytes]


def _attr(parent: ET.Element, tag: str, **attrs: object) -> ET.Element:
    """Create a child element, skipping ``None`` attributes and stringifying the rest."""
    el = ET.SubElement(parent, tag)
    for key, value in attrs.items():
        if value is not None:
            el.set(key, str(value))
    return el


def _serialize(root: ET.Element) -> bytes:
    """Render ``root`` the way a genuine store writes a stream: UTF-8 with BOM, 2-space indent."""
    ET.indent(root, space="  ")
    body = ET.tostring(root, encoding="unicode")
    return (
        b"\xef\xbb\xbf"
        + b'<?xml version="1.0" encoding="utf-8"?>\n'
        + body.encode("utf-8")
    )


class _StoreBuilder:
    """Builds the store streams for one project from its model, mirroring the .knxproj exporter."""

    def __init__(
        self,
        project: Project,
        installation: Installation | None,
        pid: str,
        *,
        name: str | None = None,
        store_format: StoreFormat = StoreFormat.ETS6,
        group_object_trees: Mapping[int, str] | None = None,
    ) -> None:
        self._project = project
        self._installation = installation
        self._pid = pid
        self._format = store_format
        self._group_object_trees = group_object_trees or {}
        self._name = name if name is not None else project.name
        # Persistent-unique-id per object, assigned in the order a genuine store does: the whole
        # topology skeleton, then spaces, then devices, then the group-address tree. Keyed by
        # (kind, model-id). ``LastUsedPuid`` in the project header is the final counter value.
        self._puid: dict[tuple[str, int], int] = {}
        self._counter = 0
        # Element id sequences are per-type 1-based counters (``L-1``, ``S-1``, ``GR-1``, ``BP-1``),
        # independent of the Puid, bumped in the same traversal.
        self._idseq: dict[tuple[str, int], int] = {}
        self._seqcount: dict[str, int] = {}
        self._ga_link_id, self._di_id = assign_ids(pid, installation)
        session = object_session(installation) if installation is not None else None
        self._secured = (
            secured_group_addresses(session) if session is not None else set[int]()
        )
        self._assign_puids()

    # -- Puid allocation -------------------------------------------------------------------

    def _next(self, kind: str, oid: int) -> int:
        self._counter += 1
        self._puid[(kind, oid)] = self._counter
        seq = self._seqcount.get(kind, 0) + 1
        self._seqcount[kind] = seq
        self._idseq[(kind, oid)] = seq
        return self._counter

    def _assign_puids(self) -> None:
        inst = self._installation
        if inst is None:
            return
        for area in sorted(inst.areas, key=lambda a: a.address):
            self._next("A", area.id)
            for line in sorted(area.lines, key=lambda line_: line_.address):
                self._next("L", line.id)
                for segment in line.segments:
                    self._next("S", segment.id)
        for space in sorted(
            (s for s in inst.spaces if s.parent_id is None),
            key=lambda s: (s.order, s.id),
        ):
            self._assign_space_puids(space)
        for area in sorted(inst.areas, key=lambda a: a.address):
            for line in sorted(area.lines, key=lambda line_: line_.address):
                for segment in line.segments:
                    for device in segment.devices:
                        self._next("DI", device.id)
        for group_range in sorted(
            (gr for gr in inst.group_ranges if gr.parent_id is None),
            key=lambda gr: gr.range_start,
        ):
            self._assign_range_puids(group_range)

    def _assign_space_puids(self, space: Space) -> None:
        self._next("SP", space.id)
        for child in sorted(space.children, key=lambda s: (s.order, s.id)):
            self._assign_space_puids(child)

    def _assign_range_puids(self, group_range: GroupRange) -> None:
        self._next("GR", group_range.id)
        for child in sorted(group_range.children, key=lambda gr: gr.range_start):
            self._assign_range_puids(child)
        for ga in sorted(group_range.group_addresses, key=lambda g: g.address):
            self._next("GA", ga.id)

    def _p(self, kind: str, oid: int) -> int:
        return self._puid[(kind, oid)]

    def _s(self, kind: str, oid: int) -> int:
        return self._idseq[(kind, oid)]

    def _line_ref(self, line_id: int | None) -> str | None:
        """The id this store gives the line ``line_id``; None for no line."""
        if line_id is None or ("L", line_id) not in self._idseq:
            return None
        return f"{self._pid}-0_L-{self._s('L', line_id)}"

    # -- Streams ---------------------------------------------------------------------------

    def build(self) -> dict[str, bytes]:
        streams: dict[str, bytes] = {
            "P": self._build_project(),
            "I": self._build_installation(),
            "U": self._build_unassigned(),
            "B": self._build_buildings(),
            "G": self._build_group_addresses(),
            "PT": self._build_traces(),
        }
        if self._format is StoreFormat.ETS5:
            # Sections ETS 5 always accounts for and ETS 6 dropped. A genuine ETS 5 store carries a
            # hash-of-empty sidecar for each (no base file, see write_store_stream); a genuine ETS 6
            # store has no trace of them at all, which is why they are not written for both.
            streams.update(dict.fromkeys(_ETS5_LEGACY_STREAMS, b""))
        streams.update(self._build_areas())
        return streams

    def _build_project(self) -> bytes:
        project = self._project
        inst = self._installation
        now = datetime.datetime.now(datetime.UTC).strftime("%Y-%m-%dT%H:%M:%S.%f0Z")
        extra = project.extra_attributes or {}
        root = ET.Element("Proj", Id=self._pid)
        line_count = sum(len(a.lines) for a in inst.areas) if inst else 0
        device_count = (
            sum(
                len(s.devices)
                for a in inst.areas
                for line in a.lines
                for s in line.segments
            )
            if inst
            else 0
        )
        room_count = (
            sum(1 for s in inst.spaces if (s.space_type or "") == "Room") if inst else 0
        )
        function_count = sum(len(s.functions) for s in inst.spaces) if inst else 0
        # The project list reads its "Status" column from CompletionStatus; without it the project
        # is listed as "Unknown". Write the project's own status, else "Editing", the status of a
        # project that is simply being worked on.
        counts: dict[str, object] = (
            {"Birdy-DeviceCount": device_count}
            if self._format is StoreFormat.ETS5
            else {
                "Birdy-RoomCount": room_count,
                "Birdy-DeviceCount": device_count,
                "Birdy-FunctionCount": function_count,
                "Birdy-LineCount": line_count,
                "Birdy-AssignedCertificateCount": 0,
                "Birdy-UnassignedCertificateCount": 0,
            }
        )
        _attr(
            root,
            "ProjInfo",
            Name=self._name,
            GroupAddressStyle=project.group_address_style,
            LastModified=project.last_modified or now,
            ProjectStart=extra.get("ProjectStart") or now,
            Comment=project.comment or "",
            CompletionStatus=project.completion_status
            if project.completion_status not in ("", UNDEFINED_STATUS)
            else "Editing",
            LastUsedPuid=self._counter,
            Guid=self.guid,
            **counts,
        )
        return _serialize(root)

    @property
    def guid(self) -> str:
        if not getattr(self, "_guid", ""):
            self._guid = self._project.guid or str(uuid4())
        return self._guid

    def _build_installation(self) -> bytes:
        name = self._installation.name if self._installation else ""
        if self._format is StoreFormat.ETS5:
            # ETS 5 stores the single installation as the root element itself; the ``<Is>`` wrapper
            # (and with it more than one installation) arrived with ETS 6.
            root = ET.Element(
                "I",
                Name=name,
                BCUKey=_BCU_KEY,
                DefaultLine="",
                IPRoutingLatencyTolerance=_IP_ROUTING_LATENCY_TOLERANCE,
            )
            return _serialize(root)
        root = ET.Element("Is")
        _attr(
            root,
            "I",
            Name=name,
            BCUKey=_BCU_KEY,
            IPRoutingLatencyTolerance=_IP_ROUTING_LATENCY_TOLERANCE,
        )
        return _serialize(root)

    def _build_unassigned(self) -> bytes:
        # xknxeditor's model has no concept of a device outside the Area/Line/Segment topology (an
        # imported device with no line home is dropped at import, see the module docstring), so this
        # section is always empty. A genuine ProjectStore represents an unused ``U`` section as no
        # base file at all (see write_store_stream), not a serialized empty ``<UDI/>`` element -- so
        # this returns the same "empty" ``b""`` rather than serializing a root element nothing reads.
        return b""

    def _build_buildings(self) -> bytes:
        root = ET.Element("Ls")
        inst = self._installation
        if inst is not None:
            for space in sorted(
                (s for s in inst.spaces if s.parent_id is None),
                key=lambda s: (s.order, s.id),
            ):
                self._build_space(root, space)
        return _serialize(root)

    def _build_space(self, parent: ET.Element, space: Space) -> None:
        sp = _attr(
            parent,
            "Sp",
            Type=space.space_type or "Room",
            Id=f"{self._pid}-0_BP-{self._s('SP', space.id)}",
            Name=space.name,
            Number=space.number or None,
            Usage=space.usage or None,
            Description=space.description or None,
            Comment=space.comment or None,
            DefaultLine=self._line_ref(space.default_line_id),
            Context=space.context or None,
            Puid=self._p("SP", space.id),
        )
        for child in sorted(space.children, key=lambda s: (s.order, s.id)):
            self._build_space(sp, child)
        for device in space.devices:
            if device.id in self._di_id:
                _attr(sp, "DIR", RefId=self._di_id[device.id])

    def _build_group_addresses(self) -> bytes:
        root = ET.Element("GAs")
        inst = self._installation
        if inst is None:
            return _serialize(root)
        ranges = _attr(root, "GRs")
        for group_range in sorted(
            (gr for gr in inst.group_ranges if gr.parent_id is None),
            key=lambda gr: gr.range_start,
        ):
            self._build_range(ranges, group_range)
        return _serialize(root)

    def _build_range(self, parent: ET.Element, group_range: GroupRange) -> None:
        gr_el = _attr(
            parent,
            "GR",
            Id=f"{self._pid}-0_GR-{self._s('GR', group_range.id)}",
            RangeStart=group_range.range_start,
            RangeEnd=group_range.range_end,
            Name=group_range.name,
            Comment=group_range.comment or None,
            Description=group_range.description or None,
            Unfiltered="true" if group_range.unfiltered else None,
            Security=group_range.security_mode or None,
            Puid=self._p("GR", group_range.id),
        )
        for child in sorted(group_range.children, key=lambda gr: gr.range_start):
            self._build_range(gr_el, child)
        for ga in sorted(group_range.group_addresses, key=lambda g: g.address):
            try:
                datapoint_type = normalize_datapoint_type(ga.datapoint_type)
            except ValueError as exc:
                raise ValueError(f"group address {ga.name!r}: {exc}") from exc
            _attr(
                gr_el,
                "GA",
                Id=f"{self._pid}-0_{self._ga_link_id[ga.id]}",
                Address=ga.address,
                Name=ga.name,
                Description=ga.description or None,
                Comment=ga.comment or None,
                DatapointType=datapoint_type,
                Central="true" if ga.central else None,
                Unfiltered="true" if ga.unfiltered else None,
                Global="true" if ga.is_global else None,
                Security=ga.security_mode or None,
                Key=ga.security_key or None,
                Context=ga.context or None,
                Puid=self._p("GA", ga.id),
            )

    def _build_traces(self) -> bytes:
        # A project with no recorded traces yet (e.g. one never opened/saved in real ETS) has no
        # base ``PT`` file in a genuine store either -- same "empty means absent" shape as ``U``
        # (see write_store_stream and _build_unassigned). Only serialize a ``<PTrcs>`` element once
        # there is at least one trace to carry.
        if not self._project.traces:
            return b""
        root = ET.Element("PTrcs")
        for trace in self._project.traces:
            _attr(
                root,
                "PTrc",
                Date=trace.date or None,
                UserName=trace.user_name or None,
                Comment=trace.comment or "",
            )
        return _serialize(root)

    def _build_areas(self) -> dict[str, bytes]:
        inst = self._installation
        if inst is None:
            return {}
        out: dict[str, bytes] = {}
        for a_seq, area in enumerate(
            sorted(inst.areas, key=lambda a: a.address), start=1
        ):
            # ``Name`` is mandatory on a topology element, and it must be *present* even when
            # empty: ETS 5.7 refuses an <Area> without it with "required property 'Name' does not
            # exist", drops the whole area -- and with it every device in it -- and then rewrites
            # the store without the area streams. The project still opens, but comes up with no
            # devices and no group-address associations, which is what made this hard to spot.
            # (ETS 6 happens to tolerate a missing Name here; ETS 5 does not.)
            area_el = ET.Element(
                "Area",
                Id=f"{self._pid}-0_A-{a_seq}",
                Address=str(area.address),
                Name=area.name or "",
                Puid=str(self._p("A", area.id)),
            )
            for line in sorted(area.lines, key=lambda line_: line_.address):
                segments = list(line.segments)
                if self._format is StoreFormat.ETS5:
                    # ETS 5 has no segment layer: the medium type sits on the line and the devices
                    # are its direct children. Writing an ETS 6 ``<Segment>`` here costs the whole
                    # line -- ETS 5 fails it with "required property 'MediumTypeRefId' does not
                    # exist" and drops it, taking every device on it with it.
                    line_el = _attr(
                        area_el,
                        "Line",
                        Id=f"{self._pid}-0_L-{self._s('L', line.id)}",
                        Address=line.address,
                        Name=line.name or "",
                        MediumTypeRefId=(
                            segments[0].medium_type
                            if segments
                            else _DEFAULT_MEDIUM_TYPE
                        ),
                        Puid=self._p("L", line.id),
                    )
                    # A line with several segments cannot be represented; the devices still all
                    # belong to the line, so they are flattened onto it (import already notes the
                    # segment loss, see core.import_notes).
                    for segment in segments:
                        for device in segment.devices:
                            self._build_device(line_el, device)
                    continue
                line_el = _attr(
                    area_el,
                    "Line",
                    Id=f"{self._pid}-0_L-{self._s('L', line.id)}",
                    Address=line.address,
                    Name=line.name or "",
                    Puid=self._p("L", line.id),
                )
                for segment in segments:
                    seg_el = _attr(
                        line_el,
                        "Segment",
                        Id=f"{self._pid}-0_S-{self._s('S', segment.id)}",
                        Number=segment.number,
                        MediumTypeRefId=segment.medium_type,
                        Puid=self._p("S", segment.id),
                    )
                    for device in segment.devices:
                        self._build_device(seg_el, device)
            out[f"{self._pid}-0_A-{a_seq}"] = _serialize(area_el)
        return out

    def _build_device(self, parent: ET.Element, device: Device) -> None:
        di = _attr(
            parent,
            "DI",
            Id=self._di_id[device.id],
            Address=device.address,
            Name=device.name,
            ProductRefId=device.product_ref_id,
            Hardware2ProgramRefId=device.hardware2program_ref_id,
            Comment=device.comment or "",
            Description=device.description or "",
            # The objects the group object tree lists count as the active ones when the activity
            # counts as calculated; a tree that misses one has it calculated again.
            IsActivityCalculated="true"
            if tree_covers(self._device_tree(device), device.com_objects)
            else "false",
            LastModified=device.last_modified or None,
            Puid=self._p("DI", device.id),
        )
        if device.parameters:
            pis = _attr(di, "PIs")
            for param in device.parameters:
                _attr(
                    pis,
                    "PI",
                    RefId=param.ref_id,
                    Value=param.value,
                    CustomizedText=param.customized_text or None,
                    GrantUseByCustomer=param.grant_use_by_customer or None,
                )
        self._build_com_objects(di, device)
        self._build_module_instances(di, device)
        self._build_group_object_tree(di, device)
        self._build_additional_addresses(di, device)
        self._build_ip_config(di, device)

    def _build_additional_addresses(self, di: ET.Element, device: Device) -> None:
        """Extra individual addresses a coupler or IP interface reserves.

        The store shortens ``<AdditionalAddresses>/<Address>`` to ``<AA>/<Addr>``. An entry without
        an address cannot be represented there and is skipped.
        """
        entries = [
            addr for addr in device.additional_addresses if addr.address is not None
        ]
        if not entries:
            return
        container = _attr(di, "AA")
        for addr in entries:
            _attr(container, "Addr", Address=addr.address, Name=addr.name or "")

    def _build_ip_config(self, di: ET.Element, device: Device) -> None:
        """The device's IP interface config, captured at import.

        The store names the element ``IP``; the attributes (``Assign``, ``IPAddress``,
        ``SubnetMask``, ``DefaultGateway``, ``MACAddress``) are the ones the source carried.
        Without this an IP interface comes back with no address to program against.
        """
        if device.ip_config:
            _attr(di, "IP", **device.ip_config)

    def _build_com_objects(self, di: ET.Element, device: Device) -> None:
        # Same write gate as the .knxproj export: emit an instance only for a linked or overridden
        # object; an active-but-unlinked object is carried by the group-object tree instead.
        tree_instances = group_object_tree_instances(self._device_tree(device))
        pending: list[tuple[ComObject, str, str, bool]] = []
        for co in device.com_objects:
            ordered = sorted(co.links, key=lambda link: not link.is_sending)
            links = " ".join(
                self._ga_link_id[link.group_address_id]
                for link in ordered
                if link.group_address_id in self._ga_link_id
            )
            secure = any(link.group_address_id in self._secured for link in co.links)
            ref = relidref(co.instance_ref_id or co.ref_id)
            if not (co.links or has_override(co) or ref not in tree_instances):
                continue
            pending.append((co, ref, links, secure))
        if not pending:
            return
        cis = _attr(di, "CIs")
        for co, ref, links, secure in pending:
            _attr(
                cis,
                "CI",
                RefId=ref,
                ChannelId=co.channel_id,
                Text=co.text_override or None,
                FunctionText=co.function_text_override or None,
                Description=co.description_override or None,
                Priority=co.priority or None,
                DatapointType=co.datapoint_type_override or None,
                Acknowledges=co.acknowledges or None,
                Links=links or None,
                ReadFlag=enable(co.read_flag_override),
                WriteFlag=enable(co.write_flag_override),
                CommunicationFlag=enable(co.communication_flag_override),
                TransmitFlag=enable(co.transmit_flag_override),
                UpdateFlag=enable(co.update_flag_override),
                ReadOnInitFlag=enable(co.read_on_init_flag_override),
                # ETS 6 annotates each link with whether its group address is secured. A genuine
                # ETS 5 store never carries ``Birdy-SecurityState`` (the property is absent from
                # its object model), so the attribute is ETS 6 only.
                **(
                    {"Birdy-SecurityState": "true" if secure else "false"}
                    if self._format is StoreFormat.ETS6
                    else {}
                ),
            )

    def _build_module_instances(self, di: ET.Element, device: Device) -> None:
        if not device.module_instances:
            return
        container = _attr(di, "ModInsts")
        for mi in device.module_instances:
            module = _attr(
                container,
                "ModInst",
                Id=relidref(mi.instance_id),
                RefId=relidref(mi.ref_id),
                RepeatIndex=mi.repeat_index or None,
            )
            if mi.arguments:
                args = _attr(module, "Arguments")
                for arg in mi.arguments:
                    _attr(
                        args,
                        "Argument",
                        RefId=arg.get("ref_id"),
                        Value=arg.get("value"),
                    )

    def _device_tree(self, device: Device) -> str:
        """The device's ``<GroupObjectTree>``: the one given for it, else the stored one."""
        return self._group_object_trees.get(device.id) or device.group_object_tree

    def _build_group_object_tree(self, di: ET.Element, device: Device) -> None:
        # The stored tree is the .knxproj <GroupObjectTree> verbatim; the store renames the root to
        # <GrpObjTree> and keeps the <Nodes>/<Node> children. Drop @GroupObjectInstances tokens no
        # longer active, as the exporter does.
        raw = self._device_tree(device)
        if not raw:
            return
        try:
            src = ET.fromstring(raw)
        except ET.ParseError:
            return
        active = {
            relidref(co.instance_ref_id or co.ref_id) for co in device.com_objects
        }
        self._graft_tree(di, src, active, rename_root="GrpObjTree")

    def _graft_tree(
        self,
        parent: ET.Element,
        src: ET.Element,
        active: set[str],
        rename_root: str | None = None,
    ) -> None:
        tag = rename_root or localname(src.tag)
        el = ET.SubElement(parent, tag)
        for key, value in src.attrib.items():
            name = localname(key)
            if name == "GroupObjectInstances":
                kept = " ".join(tok for tok in value.split() if tok in active)
                if not kept:
                    continue
                value = kept
            el.set(name, value)
        for child in src:
            self._graft_tree(el, child, active)


def store_pid_exists(store_root: Path, project_id: str) -> bool:
    """Whether a ``P-XXXX`` folder for ``project_id`` already exists in the store."""
    return (store_root / project_id).exists()


def next_free_pid(store_root: Path) -> str:
    """Return the lowest unused ``P-XXXX`` folder name in ``store_root`` (for a new copy)."""
    existing: set[str] = (
        {p.name for p in store_root.iterdir()} if store_root.is_dir() else set()
    )
    n = 1
    while True:
        candidate = f"P-{n:04X}"
        if candidate not in existing:
            return candidate
        n += 1


def build_store_streams(
    project: Project,
    installation: Installation | None,
    *,
    pid: str | None = None,
    name: str | None = None,
    store_format: StoreFormat = StoreFormat.ETS6,
    group_object_trees: Mapping[int, str] | None = None,
) -> StoreStreams:
    """Serialize ``project`` into the ETS store streams (no files written).

    ``name`` overrides the stored project name (e.g. to tag a new copy); ``None`` keeps it.
    ``store_format`` selects the target ETS generation's stream shapes -- the two are not
    interchangeable, see :class:`xknxeditor.proj.core.project_store.StoreFormat`.
    ``group_object_trees`` are trees of devices whose stored one is out of date, as for the
    ``.knxproj`` export (:func:`xknxeditor.proj.core.knxproj_export.export_knxproj`).
    """
    resolved_pid = pid or project.id
    builder = _StoreBuilder(
        project,
        installation,
        resolved_pid,
        name=name,
        store_format=store_format,
        group_object_trees=group_object_trees,
    )
    streams = builder.build()
    return StoreStreams(pid=resolved_pid, guid=builder.guid, streams=streams)


def write_project_store(
    dest_dir: Path,
    project: Project,
    installation: Installation | None,
    installer_guid: str,
    *,
    pid: str | None = None,
    project_name: str | None = None,
    product_members: Mapping[str, bytes] | None = None,
    overwrite: bool = True,
    reveal: Callable[[bytes], bytes] | None = None,
    store_format: StoreFormat = StoreFormat.ETS6,
    group_object_trees: Mapping[int, str] | None = None,
) -> StoreStreams:
    """Write a complete ``P-XXXX`` store directory the target ETS can open.

    Writes each stream with its ``.ets5hash`` sidecar, the ``storeVersion`` marker and the
    ``{pid}.store`` descriptor bound to ``installer_guid`` (the target machine's ETS installation
    GUID). Manufacturer product data (``M-XXXX/*`` members) is written loose so device product/
    application refs resolve, each folder getting its own ``M-XXXX.store`` descriptor the same way
    the project folder does (see :func:`xknxeditor.proj.core.project_store.store_descriptor` -- verified
    against a genuine ProjectStore, a manufacturer folder without one is treated as missing/untrusted
    even though its files and hashes are otherwise correct); empty ``BinaryData``/``UserFiles``/
    ``AddinData`` folders complete the layout. ``dest_dir`` is the ``P-XXXX`` folder itself (use
    :func:`store_pid_exists` and :func:`next_free_pid` to decide the id and avoid clobbering an
    unrelated project).

    The folder is built in a sibling temp directory and only installed into ``dest_dir`` once
    complete, so a failed write never destroys an existing store. ``overwrite`` controls what
    happens when ``dest_dir`` already exists: ``True`` **merges** the freshly built folder into it
    (see :func:`_install_into_existing` -- this writer only owns the streams/descriptors/folders it
    knows how to build; anything else already in ``dest_dir``, such as a real ``BinaryData``
    attachment, a stream type this writer does not model yet, or a manufacturer folder it could not
    resolve, is left exactly as ETS wrote it rather than being deleted along with the rest).
    ``False`` (for a new copy) refuses when the slot is already taken, so a slot occupied between
    picking the id and writing is never clobbered.

    ``project_name`` overrides the stored project name (e.g. to tag a new copy); ``None`` keeps it.
    ``reveal`` turns a stream with the project's encrypted secrets into one with them in the clear
    (see :meth:`ProjectService.reveal`). ``store_format`` must match the ETS generation owning
    ``dest_dir``: a store written for the other one opens but comes up empty.
    ``group_object_trees`` see :func:`build_store_streams`.
    """
    built = build_store_streams(
        project,
        installation,
        pid=pid,
        name=project_name,
        store_format=store_format,
        group_object_trees=group_object_trees,
    )
    if reveal is not None:
        built.streams.update(
            {name: reveal(data) for name, data in built.streams.items()}
        )
    # Guard every path derived from the id: a project id from an imported archive is untrusted and
    # a traversal value (e.g. "../victim") would otherwise escape the store and misdirect rmtree.
    if not _PID_RE.fullmatch(built.pid):
        raise ValueError(f"unsafe project id for a store folder: {built.pid!r}")
    mask = project_mask(built.guid, built.pid)

    staging = dest_dir.with_name(f".{dest_dir.name}.xknxtmp")
    if staging.exists():
        shutil.rmtree(staging)
    staging.mkdir(parents=True)
    for name, data in built.streams.items():
        write_store_stream(staging, name, data, mask)
    write_store_version(staging, store_format)
    write_store_descriptor(staging, built.pid, installer_guid)

    manufacturer_folders: set[str] = set()
    for member, data in (product_members or {}).items():
        if not _is_safe_member(member):
            continue  # drop traversal/absolute members from an untrusted archive
        target = staging / member
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        top = PurePosixPath(member).parts[0]
        if top.startswith("M-") and "/" in member:
            manufacturer_folders.add(top)
    for folder in manufacturer_folders:
        write_store_descriptor(staging / folder, folder, installer_guid)

    for folder in ("BinaryData", "UserFiles", "AddinData"):
        (staging / folder).mkdir(exist_ok=True)

    if dest_dir.exists():
        if not overwrite:
            shutil.rmtree(staging)
            raise FileExistsError(f"store folder already exists: {dest_dir}")
        _install_into_existing(staging, dest_dir, built.pid, manufacturer_folders)
        shutil.rmtree(staging)
    else:
        staging.rename(dest_dir)
    return built


def _install_into_existing(
    staging: Path, dest_dir: Path, pid: str, manufacturer_folders: set[str]
) -> None:
    """Install a freshly built store folder into an already-existing ``dest_dir``, without
    destroying content this writer does not own.

    Earlier versions of this function unconditionally ``rmtree``'d ``dest_dir`` and swapped
    ``staging`` in. That deletes every stream type this writer does not emit yet (the legacy
    ``AI``/``BA``/``D``/``HE``/``IPSD``/``S``/``T``/``UF`` streams a genuine ETS install may have
    created), every manufacturer folder this export could not resolve, and any real
    ``BinaryData``/``UserFiles``/``AddinData`` content -- then ETS reports the project as damaged.
    This merges instead, replacing only what the writer actually (re)built:

    - the per-area streams of *this* pid (a stale ``{pid}-0_A-N`` this writer wrote on an earlier
      export but no longer emits, e.g. because an area was deleted, is removed -- this writer is the
      sole owner of that naming scheme, so cleaning it up is safe);
    - each manufacturer folder in ``manufacturer_folders`` is replaced wholesale (so stale files
      from an earlier, different version of that manufacturer's data do not linger) -- but a
      manufacturer folder *not* in that set (one this export could not resolve) is left untouched;
    - everything else staging provides (``P``/``I``/``U``/``B``/``G``/``PT``, the current areas,
      ``storeVersion``, ``{pid}.store``, each manufacturer ``.store``, the three placeholder
      folders) is copied in, overwriting same-named entries.

    Anything already in ``dest_dir`` that staging does not mention at all -- an unmodelled stream,
    an unresolved manufacturer, a real binary attachment -- is never touched.
    """
    area_re = re.compile(rf"^{re.escape(pid)}-0_A-\d+(\.ets5hash)?$")
    for existing in dest_dir.iterdir():
        if area_re.match(existing.name) and not (staging / existing.name).exists():
            existing.unlink()
    for folder in manufacturer_folders:
        target = dest_dir / folder
        if target.exists():
            shutil.rmtree(target)
    _merge_tree(staging, dest_dir)


def _merge_tree(src: Path, dst: Path) -> None:
    """Copy ``src``'s tree into ``dst``, overwriting same-named files but never deleting anything
    in ``dst`` that ``src`` does not provide."""
    dst.mkdir(parents=True, exist_ok=True)
    for item in src.iterdir():
        target = dst / item.name
        if item.is_dir():
            _merge_tree(item, target)
        else:
            shutil.copy2(item, target)


def unzip_members(blob: bytes | None) -> dict[str, bytes]:
    """Unpack a stored ZIP blob (e.g. ``Project.imported_product_members``) into a member map."""
    if not blob:
        return {}
    with zipfile.ZipFile(BytesIO(blob)) as zf:
        return {
            info.filename: zf.read(info.filename)
            for info in zf.infolist()
            if not info.is_dir()
        }
