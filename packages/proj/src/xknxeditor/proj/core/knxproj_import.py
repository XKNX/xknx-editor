"""Import a ``.knxproj`` archive into a fresh project SQLite document.

Parsing is delegated to the ``xknxproject`` library. A ``.knxproj`` bundles its application
program XMLs, so ``xknxproject`` resolves every device's com-object/parameter/module refs and its
group-address links straight from the archive — the catalog is *not* needed. That keeps this
importer inside ``xknxeditor-proj`` (which depends only on ``xknxeditor-namespaces``) without pulling in the
catalog/product layers.

The public ``parse()`` dict drops the .knxproj reference ids (``product_ref`` / ``hardware_program_ref``)
that a project :class:`~xknxeditor.proj.models.Device` requires, so we drive the internal
``XMLParser`` and read its populated ``devices`` / ``areas`` / ``group_ranges`` /
``group_addresses`` attributes instead.

The whole project is written directly through the ORM in one commit (like
:func:`~xknxeditor.proj.core.skeleton.seed_new_project`): an import is initial state, not a sequence
of undoable edits, so it produces a valid zero-event project — far faster than one event per row for
a large installation, and it preserves the original group-range names.
"""

from __future__ import annotations

import base64
import io
import logging
import zipfile
import zlib
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import IO, Any, Protocol
from uuid import uuid4
from xml.etree import ElementTree as ET
from zipfile import BadZipFile

from sqlalchemy.orm import Session
from xknxproject.exceptions import InvalidPasswordException, UnexpectedFileContent
from xknxproject.models.knxproject import DPTType
from xknxproject.models.models import (
    ComObjectInstanceRef,
    DeviceInstance,
    XMLGroupRange,
    XMLLine,
    XMLSpace,
)
from xknxproject.xml import XMLParser
from xknxproject.zip.extractor import extract

from xknxeditor.proj.core.addressing import GroupAddressStyle
from xknxeditor.proj.core.import_notes import (
    DROPPED_DUPLICATE_LINES,
    MULTI_SEGMENT,
    MULTIPLE_INSTALLATIONS,
    ImportLoss,
    dumps,
)
from xknxeditor.proj.db import make_engine, url_for
from xknxeditor.proj.models import (
    Area,
    ComObject,
    ComObjectLink,
    Device,
    DeviceAdditionalAddress,
    DeviceBinaryData,
    Function,
    FunctionGroupAddress,
    GroupAddress,
    GroupRange,
    Installation,
    Line,
    ModuleInstance,
    Parameter,
    Project,
    ProjectTrace,
    Segment,
    Space,
    Trade,
    TradeDevice,
)

logger = logging.getLogger(__name__)

_INSTALLATION_INDEX = 0


def _best_effort[T](label: str, fn: Callable[[], T], default: T) -> T:
    """Run an optional raw-XML reader, degrading to ``default`` (with a visible warning) on any
    failure instead of aborting the whole import.

    The raw-XML readers only *enrich* the import (binary data, module args, comments, coupler
    routing, project metadata, loss detection). The topology, group addresses and devices are built
    independently of them, so a single malformed element must never take the whole import down and
    leave the user with an empty project (see issue #17)."""
    try:
        return fn()
    except Exception as e:
        logger.warning(
            "could not read '%s' from the raw project XML; import continues without it: %s: %s",
            label,
            type(e).__name__,
            e,
        )
        return default


def import_knxproj(
    source: Path | str,
    dest: Path | str,
    *,
    password: str | None = None,
    language: str | None = None,
    project_id: str | None = None,
) -> str:
    """Parse ``source`` (a ``.knxproj``) and write it as a new project at ``dest``.

    ``password`` is the project password (for encrypted archives); ``language`` picks the
    translation (e.g. ``"de-DE"``), falling back to the project default. Returns the project id.

    Raises :class:`~xknxproject.exceptions.InvalidPasswordException` when the archive is protected
    and no/a wrong password was given (standard-zip encryption reports a wrong password only as a
    decompression error, so any parse failure with a password set is treated as a wrong password),
    and :class:`~xknxproject.exceptions.UnexpectedFileContent` when the file is not a readable
    ``.knxproj`` archive. An existing ``dest`` is overwritten (an import is a fresh project).
    """
    logger.debug(
        "knxproj import entry",
        extra={
            "source": str(source),
            "dest": str(dest),
            "encrypted": password is not None,
        },
    )
    parser, extras = _parse_checked(source, password, language)
    # Capture the archive's own product data (signed master + manufacturer members) so a later export
    # can re-emit it verbatim; ETS re-issues product ids on import, so the catalog cannot resolve them.
    master_xml, product_members = _capture_product_provenance(source)
    # Project IDs are "P-" + 4 hex digits (spec Project Scheme §4.2.3, e.g. "P-02D7"); a longer
    # id yields a non-conformant P-XXXXXXXX folder that the importer can refuse to import.
    pid = project_id or f"P-{uuid4().hex[:4].upper()}"
    logger.debug(
        "import_knxproj: source=%s dest=%s pid=%s language=%s encrypted=%s",
        source,
        dest,
        pid,
        language,
        password is not None,
    )
    # An import always produces a new project; start from a clean file so re-importing over an
    # existing target does not clash with its rows. Done only after a successful parse, so a failed
    # import (e.g. wrong password) never destroys an existing project.
    dest_path = Path(dest)
    dest_path.unlink(missing_ok=True)
    engine = make_engine(url_for(dest_path))
    try:
        with Session(engine) as session:
            _build(
                session,
                parser,
                pid,
                extras,
                knx_master_xml=master_xml,
                product_members=product_members,
            )
            session.commit()
    finally:
        engine.dispose()
    logger.debug("import_knxproj done: pid=%s -> %s", pid, dest)
    return pid


def _parse_checked(
    source: Path | str, password: str | None, language: str | None
) -> tuple[XMLParser, _RawExtras]:
    """Parse, normalising the assorted low-level failure modes into a clear, typed error."""
    try:
        return _parse(source, password, language)
    except InvalidPasswordException:
        raise  # protected + no password (extractor reports this eagerly)
    except (BadZipFile, zlib.error, RuntimeError) as e:
        # Standard-zip encryption has only a one-byte password check: a wrong password may
        # fail the header check (RuntimeError) or slip through and fail decompression later
        # (zlib.error). With a password set, treat any such failure as a wrong password.
        if password is not None:
            raise InvalidPasswordException("Invalid password.") from e
        raise UnexpectedFileContent(f"Not a readable .knxproj archive: {e}") from e


def _capture_product_provenance(
    source: Path | str,
) -> tuple[bytes | None, bytes | None]:
    """Capture the imported archive's verbatim product data for a byte-faithful re-export.

    Returns ``(knx_master_xml, product_members)``: the archive's signed ``knx_master.xml`` and a ZIP
    of its manufacturer members (``M-XXXX/*`` and ``M-XXXX.signature``), both exactly as the source
    carried them. ETS re-issues product ids on import, so resolving them through the catalog on export
    misses most folders; only these original members keep a re-exported project importable. The
    manufacturer data is never password protected, so it reads without the project password.
    Best-effort: returns ``None`` for either part on a read failure (the export then falls back to the
    catalog bundle / fetched master).
    """
    try:
        with zipfile.ZipFile(source) as zf:
            names = zf.namelist()
            master = zf.read("knx_master.xml") if "knx_master.xml" in names else None
            members = [
                (name, zf.read(name))
                for name in names
                if not name.endswith("/") and name.split("/", 1)[0].startswith("M-")
            ]
    except (OSError, zipfile.BadZipFile, RuntimeError, zlib.error) as exc:
        logger.warning("could not capture product data from %s (%s)", source, exc)
        return None, None
    if not members:
        return master, None
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as out:
        for name, data in members:
            out.writestr(name, data)
    logger.debug(
        "captured product data: master=%s members=%d",
        "yes" if master else "no",
        len(members),
    )
    return master, buffer.getvalue()


@dataclass(frozen=True, slots=True)
class _BinaryEntry:
    """A DeviceInstance ``<BinaryData>``: metadata + the raw payload from its ``.dat`` file."""

    name: str
    data: bytes
    ref_id: str = ""
    do_not_copy: bool = False


@dataclass(frozen=True, slots=True)
class _TraceEntry:
    """A ``ProjectInformation/ProjectTraces/ProjectTrace`` entry, captured verbatim.

    ``comment`` is stored exactly as written by ETS (encrypted); we neither decrypt nor re-encrypt.
    """

    date: str
    user_name: str
    comment: str


@dataclass(frozen=True, slots=True)
class _AddrEntry:
    """A DeviceInstance ``<AdditionalAddresses>/<Address>``: an extra individual address a coupler or
    interface reserves. ``address`` is the 1-255 octet (``None`` only for the schema-optional case)."""

    address: int | None
    name: str = ""
    description: str = ""
    comment: str = ""


@dataclass(frozen=True, slots=True)
class _SegmentDesc:
    """A raw ``<Segment>`` (project/22+23) of a line: its ``Number``, ``MediumTypeRefId``,
    ``DomainAddress`` and the ``@Id`` of every ``DeviceInstance`` nested under it. Used to rebuild
    one ``Segment`` per raw segment (xknxproject collapses them, reassigning devices and losing the
    per-segment domain)."""

    number: int
    medium_type_ref: str | None
    domain_address: int | None
    device_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class _TradeEntry:
    """A ``Trades/Trade`` node (Gewerke), captured verbatim. ``device_ids`` are the referenced
    DeviceInstance ``@Id``s (``DeviceInstanceRef/@RefId``); ``children`` are the nested trades."""

    name: str
    number: str
    comment: str
    description: str
    completion_status: str
    context: str
    device_ids: list[str]
    children: list[_TradeEntry]


# Per-device data read straight from the raw project XML because xknxproject drops it. Keyed by
# DeviceInstance @Id (== xknxproject's ``DeviceInstance.identifier``).
BinaryDataMap = dict[str, list[_BinaryEntry]]
# instance_id -> (RepeatIndex, [{"ref_id", "value"}]) for that ModuleInstance's <Arguments>.
ModuleArgs = dict[str, tuple[str, list[dict[str, str]]]]
ModuleArgsMap = dict[str, ModuleArgs]


class _ProjectContents(Protocol):
    """The slice of xknxproject's ``KNXProjContents`` that ``_read_device_extras`` needs."""

    def open_project_0(self) -> IO[bytes]: ...


class _RawExtras:
    """Per-device data captured from the raw project XML (not surfaced by xknxproject)."""

    def __init__(self) -> None:
        # DeviceInstance @Id -> [(BinaryData Name, decoded bytes)]
        self.binary_data: BinaryDataMap = {}
        # DeviceInstance @Id -> {ModuleInstance @Id -> (RepeatIndex, argument list)}
        self.module_args: ModuleArgsMap = {}
        # ETS project-log entries, in document order (captured verbatim; comment stays encrypted).
        self.traces: list[_TraceEntry] = []
        # DeviceInstance @Id -> the device's <GroupObjectTree> as a namespace-stripped XML string.
        self.group_object_trees: dict[str, str] = {}
        # DeviceInstance @Id -> the device's <IPConfig> attributes (IP interface/router config).
        self.ip_config: dict[str, dict[str, str]] = {}
        # <Topology>/<UnassignedDevices> devices as namespace-stripped <DeviceInstance> XML strings,
        # in document order (empty when the source had no such container).
        self.unassigned_devices: list[str] = []
        # ComObjectInstanceRef @Id -> (raw @Text, raw @FunctionText, raw @Description) per-instance
        # overrides. Read from the raw XML because xknxproject backfills these from the app default
        # post-parse, so the parsed object can no longer tell an instance override from an inherited
        # default.
        self.com_object_text_overrides: dict[
            str, tuple[str | None, str | None, str | None]
        ] = {}
        # --- coupler data xknxproject does not surface (read straight from the raw XML) ---
        # (Area @Address, Line @Address) -> coupler pass-through group addresses
        # (``AdditionalGroupAddresses``; on the Segment in project/22+23, on the Line in 14+20).
        self.line_pass_through: dict[tuple[int, int], list[int]] = {}
        # (Area @Address, Line @Address) -> RF/PL ``DomainAddress`` (Line in 14+20, Segment in 22+23).
        self.domain_address: dict[tuple[int, int], int] = {}
        # (Area @Address, Line @Address) -> the line's raw ``<Segment>`` descriptors (project/22+23),
        # in document order. Empty for project/14+20 (devices sit directly under the Line).
        self.segments: dict[tuple[int, int], list[_SegmentDesc]] = {}
        # Raw group-address values whose <GroupAddress> carries ``Unfiltered="true"``.
        self.unfiltered_ga: set[int] = set()
        # (RangeStart, RangeEnd) of every <GroupRange> carrying ``Unfiltered="true"``.
        self.unfiltered_ranges: set[tuple[int, int]] = set()
        # DeviceInstance @Id -> extra individual addresses (``AdditionalAddresses/Address``).
        self.additional_addresses: dict[str, list[_AddrEntry]] = {}
        # --- descriptive metadata xknxproject does not surface (read from the raw XML) ---
        # The ProjectInformation @Comment (project.xml).
        self.project_comment: str = ""
        # Area @Address -> Area @Comment.
        self.area_comment: dict[int, str] = {}
        # (Area @Address, Line @Address) -> Line @Comment.
        self.line_comment: dict[tuple[int, int], str] = {}
        # (RangeStart, RangeEnd) -> GroupRange @Description.
        self.grouprange_description: dict[tuple[int, int], str] = {}
        # Raw group-address values whose <GroupAddress> carries ``Central="true"``.
        self.central_ga: set[int] = set()
        # Raw group-address values whose <GroupAddress> carries ``Global="true"``.
        self.global_ga: set[int] = set()
        # DeviceInstance @Id -> DeviceInstance @Comment.
        self.device_comment: dict[str, str] = {}
        # Space @Id -> Space @Comment.
        self.space_comment: dict[str, str] = {}
        # Function @Id -> (Comment, Description).
        self.function_meta: dict[str, tuple[str, str]] = {}
        # Installation <Trades> tree (Gewerke), captured verbatim.
        self.trades: list[_TradeEntry] = []
        # Import-loss notes detected from the raw XML (see core.import_notes). Build-time losses
        # (dropped duplicate lines) are appended during topology construction.
        self.losses: list[ImportLoss] = []


def _parse(
    source: Path | str, password: str | None, language: str | None
) -> tuple[XMLParser, _RawExtras]:
    with extract(Path(source), password) as contents:
        parser = XMLParser(contents)
        parser.parse(
            language
        )  # populates parser.* ; the returned dict drops the ref ids we need
        # xknxproject exposes neither per-device <BinaryData> (where DCAs persist state, e.g. the MDT
        # DALI "DaliGC16-Backup-Store") nor the <ModuleInstances> Arguments/RepeatIndex needed to
        # re-emit module-based devices on export, so read both from the raw installation XML while the
        # archive is still open.
        extras = _read_device_extras(contents)
        extras.project_comment, extras.traces = _best_effort(
            "project metadata", lambda: _read_project_meta(contents), ("", [])
        )
    logger.info(
        "parsed knxproj '%s': %d devices, %d group addresses, %d w/ binary data, %d w/ modules, %d traces",
        parser.project_info.name,
        len(parser.devices),
        len(parser.group_addresses),
        len(extras.binary_data),
        len(extras.module_args),
        len(extras.traces),
    )
    return parser, extras


def _read_device_extras(contents: _ProjectContents) -> _RawExtras:
    """Extract from the raw ``0.xml`` what xknxproject drops: per-device ``<BinaryData>``,
    ``<ModuleInstances>``, ``<GroupObjectTree>`` and ``<AdditionalAddresses>``, plus the coupler
    routing data (``AdditionalGroupAddresses`` pass-through and ``Unfiltered`` flags on the
    group-address tree).

    ``contents`` is xknxproject's ``KNXProjContents``; ``open_project_0`` yields the installation
    XML. Best-effort: any parse failure leaves the result empty rather than aborting the import.
    """
    extras = _RawExtras()
    try:
        with contents.open_project_0() as handle:
            root = ET.parse(handle).getroot()
    except Exception as e:  # these extras are optional; never fail the import for them
        logger.debug("could not read raw 0.xml for device extras: %s", e)
        return extras
    # Namespace-agnostic: match on the local tag name so we don't depend on the schema version.
    device_count = 0
    for device in root.iter():
        if _localname(device.tag) != "DeviceInstance":
            continue
        device_id = device.get("Id")
        if not device_id:
            continue
        device_count += 1
        # Best-effort per device: a single malformed device's optional extras must not abort the
        # whole import (which would leave an empty project); the device itself is still built from
        # the parser's data.
        try:
            _read_one_device_extras(device, device_id, contents, extras)
        except Exception as e:
            logger.warning(
                "could not read raw extras for device %s; import continues without them: %s: %s",
                device_id,
                type(e).__name__,
                e,
            )
    extras.unassigned_devices = _best_effort(
        "unassigned devices", lambda: _read_unassigned_devices(root), []
    )
    _best_effort("coupler extras", lambda: _read_coupler_extras(root, extras), None)
    _best_effort("locations extras", lambda: _read_locations_extras(root, extras), None)
    extras.trades = _best_effort("trades", lambda: _read_trades(root), [])
    extras.losses = _best_effort(
        "import losses", lambda: _detect_import_losses(root, extras), []
    )
    logger.debug("read raw device extras for %d device instances", device_count)
    return extras


def _read_one_device_extras(
    device: ET.Element, device_id: str, contents: _ProjectContents, extras: _RawExtras
) -> None:
    """Collect one ``<DeviceInstance>``'s optional raw-XML extras into ``extras`` (keyed by id).

    Every field here is enrichment xknxproject drops; wrapped per device by the caller so one bad
    device never aborts the import.
    """
    binary = _read_binary_data(device, device_id, contents)
    if binary:
        extras.binary_data[device_id] = binary
    modules = _read_module_args(device)
    if modules:
        extras.module_args[device_id] = modules
    tree = _read_group_object_tree(device)
    if tree:
        extras.group_object_trees[device_id] = tree
    ip_config = _read_ip_config(device)
    if ip_config is not None:
        extras.ip_config[device_id] = ip_config
    additional = _read_additional_addresses(device)
    if additional:
        extras.additional_addresses[device_id] = additional
    comment = device.get("Comment")
    if comment:
        extras.device_comment[device_id] = comment
    for cid, override in _read_com_object_text_overrides(device):
        extras.com_object_text_overrides[cid] = override


def _int_attr(elem: ET.Element, name: str) -> int | None:
    """The named attribute parsed as an int, or ``None`` when absent or non-numeric."""
    value = elem.get(name)
    if value is None:
        return None
    try:
        return int(value)
    except ValueError:
        return None


def _is_true(value: str | None) -> bool:
    """XML-schema boolean truthiness (``"true"``/``"1"``)."""
    return value is not None and value.strip().lower() in ("true", "1")


def _read_additional_addresses(device: ET.Element) -> list[_AddrEntry]:
    """The device's ``<AdditionalAddresses>/<Address>`` entries (extra individual addresses)."""
    for child in device:
        if _localname(child.tag) != "AdditionalAddresses":
            continue
        return [
            _AddrEntry(
                address=_int_attr(addr, "Address"),
                name=addr.get("Name", "") or "",
                description=addr.get("Description", "") or "",
                comment=addr.get("Comment", "") or "",
            )
            for addr in child
            if _localname(addr.tag) == "Address"
        ]
    return []


def _read_ip_config(device: ET.Element) -> dict[str, str] | None:
    """The device's ``<IPConfig>`` attributes (IP interface/router config), or ``None`` if absent.

    ``IPConfig_t`` carries only attributes (Assign, IPAddress, SubnetMask, DefaultGateway,
    MACAddress) and no children, so the local-name attribute dict is a lossless capture that the
    exporter re-emits verbatim. xknxproject does not surface it.
    """
    for child in device:
        if _localname(child.tag) == "IPConfig":
            return {_localname(k): v for k, v in child.attrib.items()}
    return None


def _read_aga_addresses(parent: ET.Element) -> list[int]:
    """The ``<AdditionalGroupAddresses>/<GroupAddress @Address>`` values directly under ``parent``."""
    out: list[int] = []
    for child in parent:
        if _localname(child.tag) != "AdditionalGroupAddresses":
            continue
        for ga in child:
            if _localname(ga.tag) != "GroupAddress":
                continue
            addr = _int_attr(ga, "Address")
            if addr is not None:
                out.append(addr)
    return out


def _read_coupler_extras(root: ET.Element, extras: _RawExtras) -> None:
    """Read the topology data xknxproject drops from ``root``: per-line ``AdditionalGroupAddresses``
    pass-through (on the Segment in project/22+23, on the Line in 14+20), the ``Unfiltered`` flags on
    the group-address tree, plus the descriptive metadata it also omits — Area/Line ``@Comment``,
    GroupRange ``@Description`` and the group-address ``Central`` flag."""
    for area in root.iter():
        if _localname(area.tag) != "Area":
            continue
        a_addr = _int_attr(area, "Address")
        if a_addr is not None and (a_comment := area.get("Comment")):
            extras.area_comment[a_addr] = a_comment
        for line in area:
            if _localname(line.tag) != "Line":
                continue
            l_addr = _int_attr(line, "Address")
            if (
                a_addr is not None
                and l_addr is not None
                and (l_comment := line.get("Comment"))
            ):
                extras.line_comment[(a_addr, l_addr)] = l_comment
            addrs = _read_aga_addresses(line)
            # ``DomainAddress`` (RF/PL) sits on the Line in project/14+20 and on the Segment in
            # project/22+23; xknxproject drops it either way. Prefer the Segment value when present.
            domain = _int_attr(line, "DomainAddress")
            seg_descs: list[_SegmentDesc] = []
            for seg in line:
                if _localname(seg.tag) == "Segment":
                    addrs.extend(_read_aga_addresses(seg))
                    seg_domain = _int_attr(seg, "DomainAddress")
                    if seg_domain is not None:
                        domain = seg_domain
                    device_ids = tuple(
                        did
                        for d in seg.iter()
                        if _localname(d.tag) == "DeviceInstance"
                        and (did := d.get("Id"))
                    )
                    seg_descs.append(
                        _SegmentDesc(
                            number=_int_attr(seg, "Number") or 0,
                            medium_type_ref=seg.get("MediumTypeRefId"),
                            domain_address=seg_domain,
                            device_ids=device_ids,
                        )
                    )
            if seg_descs and a_addr is not None and l_addr is not None:
                extras.segments[(a_addr, l_addr)] = seg_descs
            if domain is not None and a_addr is not None and l_addr is not None:
                extras.domain_address[(a_addr, l_addr)] = domain
            if addrs and a_addr is not None and l_addr is not None:
                extras.line_pass_through[(a_addr, l_addr)] = addrs
    for elem in root.iter():
        name = _localname(elem.tag)
        if name == "GroupRange":
            start, end = _int_attr(elem, "RangeStart"), _int_attr(elem, "RangeEnd")
            if start is not None and end is not None:
                if _is_true(elem.get("Unfiltered")):
                    extras.unfiltered_ranges.add((start, end))
                if description := elem.get("Description"):
                    extras.grouprange_description[(start, end)] = description
        elif name == "GroupAddress":
            addr = _int_attr(elem, "Address")
            if addr is not None:
                if _is_true(elem.get("Unfiltered")):
                    extras.unfiltered_ga.add(addr)
                if _is_true(elem.get("Central")):
                    extras.central_ga.add(addr)
                if _is_true(elem.get("Global")):
                    extras.global_ga.add(addr)


def _detect_import_losses(root: ET.Element, extras: _RawExtras) -> list[ImportLoss]:
    """Detect, from the raw ``0.xml``, data classes ETS carries that our store cannot fully
    represent, so the user can be told what a round-trip export will merge or drop. Best-effort and
    read-only; the dropped-duplicate-line loss is added later during topology construction."""
    losses: list[ImportLoss] = []

    installations = [e for e in root.iter() if _localname(e.tag) == "Installation"]
    if len(installations) > 1:
        losses.append(ImportLoss(MULTIPLE_INSTALLATIONS, len(installations), detail=""))

    # <UnassignedDevices> (devices not placed on a line) and per-device <IPConfig> (IP interface/
    # router config) are now preserved verbatim through import/export (see Installation
    # .unassigned_devices_xml and Device.ip_config), so neither is reported as a loss here.

    # Per-instance com-object overrides (@Text/@FunctionText/@Description) are now preserved through
    # import/export (see _build_com_object), so they are no longer a loss and not reported here.

    multi_segment_lines = sum(1 for segs in extras.segments.values() if len(segs) > 1)
    if multi_segment_lines:
        losses.append(ImportLoss(MULTI_SEGMENT, multi_segment_lines, detail=""))

    return losses


def _read_locations_extras(root: ET.Element, extras: _RawExtras) -> None:
    """Read the descriptive metadata xknxproject drops from the Locations tree: each ``Space``
    ``@Comment`` (keyed by ``@Id``) and each ``Function`` ``@Comment``/``@Description``.

    Space metadata keys by the raw ``@Id`` because xknxproject exposes ``XMLSpace.identifier``
    verbatim. Function metadata must key by the *stripped* identifier: xknxproject derives
    ``XMLFunction.identifier`` as ``Id.split("_", 1)[1]`` (dropping the leading project-prefix
    segment), and the builder looks these up by that identifier, so a full-``@Id`` key would never
    match and the comment/description would be silently lost on import."""
    for elem in root.iter():
        name = _localname(elem.tag)
        if name == "Space":
            sid = elem.get("Id")
            if sid and (comment := elem.get("Comment")):
                extras.space_comment[sid] = comment
        elif name == "Function":
            fid = elem.get("Id")
            comment = elem.get("Comment") or ""
            description = elem.get("Description") or ""
            if fid and (comment or description):
                key = fid.split("_", 1)[1] if "_" in fid else fid
                extras.function_meta[key] = (comment, description)


def _read_trades(root: ET.Element) -> list[_TradeEntry]:
    """Read the Installation ``<Trades>`` tree (Gewerke), which xknxproject does not surface.

    Only the first ``<Trades>`` container is read; each ``Trade`` keeps its nested trades and the
    ``@RefId`` of every ``DeviceInstanceRef`` so the tree round-trips on export."""
    container = next((e for e in root.iter() if _localname(e.tag) == "Trades"), None)
    if container is None:
        return []
    return [
        _read_trade(child) for child in container if _localname(child.tag) == "Trade"
    ]


def _read_trade(elem: ET.Element) -> _TradeEntry:
    device_ids = [
        ref
        for child in elem
        if _localname(child.tag) == "DeviceInstanceRef" and (ref := child.get("RefId"))
    ]
    children = [
        _read_trade(child) for child in elem if _localname(child.tag) == "Trade"
    ]
    return _TradeEntry(
        name=elem.get("Name") or "",
        number=elem.get("Number") or "",
        comment=elem.get("Comment") or "",
        description=elem.get("Description") or "",
        completion_status=elem.get("CompletionStatus") or "",
        context=elem.get("Context") or "",
        device_ids=device_ids,
        children=children,
    )


def _strip_ns(elem: ET.Element) -> ET.Element:
    """A deep copy of ``elem`` with every tag and attribute reduced to its local name.

    The raw ``.knxproj`` carries the schema namespace on every element; storing the local-name-only
    form keeps the captured tree independent of the source schema so the exporter can re-emit it
    under whatever target namespace it writes.
    """
    out = ET.Element(_localname(elem.tag))
    for key, value in elem.attrib.items():
        out.set(_localname(key), value)
    for child in elem:
        out.append(_strip_ns(child))
    return out


def _read_group_object_tree(device: ET.Element) -> str:
    """The device's ``<GroupObjectTree>`` as a namespace-stripped XML string, or ``""`` if absent.

    xknxproject flattens this tree into ``ComObject.channel_id`` and drops the folder nodes, nesting
    and order (see issue #14), so capture the whole subtree verbatim for a lossless re-export. The
    referenced ids (com-object instance refs, channel ids, module-instance segments) round-trip
    unchanged, so the exporter can re-emit the tree as-is.
    """
    for child in device:
        if _localname(child.tag) == "GroupObjectTree":
            return ET.tostring(_strip_ns(child), encoding="unicode")
    return ""


def _read_unassigned_devices(root: ET.Element) -> list[str]:
    """The ``<Topology>/<UnassignedDevices>`` devices as namespace-stripped ``<DeviceInstance>`` XML
    strings, in document order (empty when there is no such container).

    ETS keeps devices that are not yet placed on a line here. xknxproject does not expose them (they
    are absent from ``parser.areas``), and they have no line/segment to attach to, so the whole
    subtree is captured verbatim for a lossless re-export as opaque provenance.
    """
    container = next(
        (e for e in root.iter() if _localname(e.tag) == "UnassignedDevices"), None
    )
    if container is None:
        return []
    return [
        ET.tostring(_strip_ns(child), encoding="unicode")
        for child in container
        if _localname(child.tag) == "DeviceInstance"
    ]


def _read_com_object_text_overrides(
    device: ET.Element,
) -> list[tuple[str, tuple[str | None, str | None, str | None]]]:
    """Per-instance ``@Text``/``@FunctionText``/``@Description`` overrides, keyed by
    ``ComObjectInstanceRef @Id``.

    xknxproject backfills the text/function-text from the application-program default after parsing, so
    the raw XML is the only place the genuine user override survives. Yields only refs carrying at
    least one of the three attributes; an absent attribute stays ``None`` (inherit the app default).
    """
    result: list[tuple[str, tuple[str | None, str | None, str | None]]] = []
    for elem in device.iter():
        if _localname(elem.tag) != "ComObjectInstanceRef":
            continue
        cid = elem.get("Id")
        if not cid:
            continue
        text = elem.get("Text")
        function_text = elem.get("FunctionText")
        description = elem.get("Description")
        if text is not None or function_text is not None or description is not None:
            result.append((cid, (text, function_text, description)))
    return result


def _read_project_meta(contents: _ProjectContents) -> tuple[str, list[_TraceEntry]]:
    """The ``ProjectInformation`` ``@Comment`` and every ``ProjectTraces/ProjectTrace`` from the raw
    ``project.xml``.

    xknxproject surfaces neither, so read them from the raw project metadata while the archive is
    open. Best-effort: any failure yields ``("", [])`` rather than aborting the import. The trace
    ``Comment`` is kept verbatim (ETS encrypts it); order follows the document.
    """
    raw = _read_project_file(contents, "project.xml")
    if raw is None:
        return "", []
    try:
        root = ET.fromstring(raw)
    except ET.ParseError as e:
        logger.debug("could not parse project.xml for project metadata: %s", e)
        return "", []
    comment = ""
    for elem in root.iter():
        if _localname(elem.tag) == "ProjectInformation":
            comment = elem.get("Comment") or ""
            break
    traces: list[_TraceEntry] = []
    for elem in root.iter():
        if _localname(elem.tag) != "ProjectTrace":
            continue
        traces.append(
            _TraceEntry(
                date=elem.get("Date") or "",
                user_name=elem.get("UserName") or "",
                comment=elem.get("Comment") or "",
            )
        )
    return comment, traces


def _read_binary_data(
    device: ET.Element, device_id: str, contents: _ProjectContents
) -> list[_BinaryEntry]:
    """Every ``<BinaryData>`` entry on ``device`` with its payload.

    ETS stores the payload in ``P-XXXX/BinaryData/{Id}.dat`` (raw bytes), with the ``0.xml`` element
    carrying only metadata. Older/other writers may inline it as ``<Data>`` base64 — read that too.
    """
    entries: list[_BinaryEntry] = []
    for elem in device.iter():
        if _localname(elem.tag) != "BinaryData":
            continue
        name = elem.get("Name")
        entry_id = elem.get("Id")
        if name is None and entry_id is None:
            continue  # the wrapper element carries neither; only the entries do
        data = _binary_payload(elem, entry_id, contents)
        entries.append(
            _BinaryEntry(
                name=name or "",
                data=data,
                ref_id=elem.get("RefId") or "",
                # Copy flag: "DoNotCopy" (project/23) or "AutoCopy" (project/20; same meaning).
                do_not_copy=(
                    elem.get("DoNotCopy") or elem.get("AutoCopy") or ""
                ).lower()
                == "true",
            )
        )
    return entries


def _binary_payload(
    elem: ET.Element, entry_id: str | None, contents: _ProjectContents
) -> bytes:
    """The raw bytes for a ``<BinaryData>``: its external ``.dat`` file (authoritative in ETS 6.4),
    falling back to an inline ``<Data>`` (legacy/other writers)."""
    if entry_id:
        raw = _read_project_file(contents, f"BinaryData/{entry_id}.dat")
        if raw is not None:
            return raw
    data_elem = next((c for c in elem if _localname(c.tag) == "Data"), None)
    if data_elem is not None and data_elem.text:
        try:
            return base64.b64decode(data_elem.text)
        except (ValueError, TypeError):
            logger.debug("skipping malformed inline BinaryData Data on %s", entry_id)
    return b""  # a RefId-only entry (content borrowed from the application program) has no file


def _read_project_file(contents: _ProjectContents, relpath: str) -> bytes | None:
    """Read a file from the project folder of the (possibly encrypted) archive; None if absent.

    Uses xknxproject's already-opened project archive (private attrs) so encrypted projects work
    too. Best-effort: any failure returns None rather than aborting the import.
    """
    archive: Any = getattr(contents, "_project_archive", None)
    base: str = getattr(contents, "_project_relative_path", "") or ""
    if archive is None:
        return None
    try:
        with archive.open(f"{base}{relpath}") as handle:
            data: bytes = handle.read()
            return data
    except (KeyError, OSError, RuntimeError):
        return None


def _read_module_args(device: ET.Element) -> ModuleArgs:
    """The ``RepeatIndex`` + ``<Arguments>`` of every ``<ModuleInstance>``, keyed by its ``@Id``."""
    result: ModuleArgs = {}
    for mi in device.iter():
        if _localname(mi.tag) != "ModuleInstance":
            continue
        mi_id = mi.get("Id")
        if not mi_id:
            continue
        args = [
            {"ref_id": ref, "value": arg.get("Value", "")}
            for arg in mi.iter()
            if _localname(arg.tag) == "Argument" and (ref := arg.get("RefId"))
        ]
        result[mi_id] = (mi.get("RepeatIndex", ""), args)
    return result


def _localname(tag: str) -> str:
    """The local part of a possibly namespaced ElementTree tag (``{ns}Name`` -> ``Name``)."""
    return tag.rsplit("}", 1)[-1]


def _build(
    session: Session,
    parser: XMLParser,
    pid: str,
    extras: _RawExtras,
    *,
    knx_master_xml: bytes | None = None,
    product_members: bytes | None = None,
) -> None:
    # Raw parser inventory at INFO (not DEBUG) so a default remote log shows what xknxproject
    # handed us. parser.devices is the flat concatenation of every line's devices across all areas
    # (topology-derived: it excludes <UnassignedDevices> and any device xknxproject itself dropped,
    # e.g. one without an individual address). Comparing it to the built device count below isolates
    # losses our importer adds (duplicate-line drops, per-device build failures) from what xknxproject
    # delivered. A 0 here means xknxproject placed no device on any line, which can be an empty
    # topology, an all-unassigned project, or an upstream parse issue, not necessarily an empty file.
    logger.info(
        "import parsed archive: %d devices, %d areas, %d group addresses, %d spaces, %d functions",
        len(parser.devices),
        len(parser.areas),
        len(parser.group_addresses),
        len(parser.spaces),
        len(parser.functions),
    )
    info = parser.project_info
    project = Project(
        id=pid,
        name=info.name,
        group_address_style=_style(info.group_address_style.value),
        guid=info.guid,
        created_by=info.created_by,
        last_modified=info.last_modified or "",
        schema_version=info.schema_version,
        tool_version=info.tool_version,
        comment=extras.project_comment,
        knx_master_xml=knx_master_xml,
        imported_product_members=product_members,
        traces=[
            ProjectTrace(
                project_id=pid,
                date=t.date,
                user_name=t.user_name,
                comment=t.comment,
            )
            for t in extras.traces
        ],
    )
    session.add(project)
    logger.debug(
        "build: project row added (name=%r style=%s schema=%s traces=%d comment=%s)",
        info.name,
        project.group_address_style,
        info.schema_version,
        len(extras.traces),
        bool(extras.project_comment),
    )

    installation = Installation(index=_INSTALLATION_INDEX, name="")
    session.add(installation)

    state = _ImportState(extras)
    logger.debug("build: topology (%d areas)", len(parser.areas))
    _build_topology(installation, parser, state)
    # <UnassignedDevices> devices (not placed on any line): kept verbatim as opaque provenance.
    installation.unassigned_devices_xml = extras.unassigned_devices or None
    areas = len(installation.areas)
    lines = sum(len(a.lines) for a in installation.areas)
    segments = sum(len(line.segments) for a in installation.areas for line in a.lines)
    devices = sum(
        len(s.devices)
        for a in installation.areas
        for line in a.lines
        for s in line.segments
    )
    logger.debug(
        "build: topology done (%d areas, %d lines, %d segments, %d devices, %d unassigned)",
        areas,
        lines,
        segments,
        devices,
        len(extras.unassigned_devices),
    )
    logger.debug("build: group addresses (%d)", len(parser.group_addresses))
    _build_group_addresses(installation, parser, state)
    logger.debug(
        "build: group addresses done (%d ranges, %d addresses)",
        len(installation.group_ranges),
        len(state.group_addresses),
    )
    _build_links(state)
    logger.debug("build: links done")
    _build_spaces(installation, parser, state)
    logger.debug("build: spaces done (%d spaces)", len(installation.spaces))
    _build_trades(installation, state)
    logger.debug("build: trades done (%d)", len(extras.trades))
    # Import-loss notes (raw-XML detection + any build-time dropped lines), echoed at export.
    project.import_notes = dumps(state.extras.losses)
    logger.info(
        "built project %s: %d/%d devices placed (%d skipped), %d areas, %d lines, "
        "%d group addresses, %d unassigned, %d losses",
        pid,
        devices,
        len(parser.devices),
        state.skipped_devices,
        areas,
        lines,
        len(state.group_addresses),
        len(extras.unassigned_devices),
        len(state.extras.losses),
    )


def _style(value: str) -> GroupAddressStyle:
    try:
        return GroupAddressStyle(value)
    except ValueError:
        return GroupAddressStyle.THREE_LEVEL


# --- topology + devices ---------------------------------------------------


def _build_topology(
    installation: Installation, parser: XMLParser, state: _ImportState
) -> None:
    # xknxproject flattens every <Installation>/Topology into one areas list, discarding the
    # installation boundary, so two installations that each define area 1 arrive as two XMLArea
    # objects with the same address. Merge by address (reuse the existing Area/Line) so we never
    # create duplicate topology rows; a colliding line is dropped and counted as an import loss.
    areas_by_addr: dict[int, Area] = {}
    lines_by_area: dict[int, dict[int, Line]] = {}
    dropped_lines = 0
    for xarea in parser.areas:
        area = areas_by_addr.get(xarea.address)
        if area is None:
            area = Area(
                address=xarea.address,
                name=xarea.name,
                description=getattr(xarea, "description", "") or "",
                comment=state.extras.area_comment.get(xarea.address, ""),
            )
            installation.areas.append(area)
            areas_by_addr[xarea.address] = area
            lines_by_area[xarea.address] = {}
        area_lines = lines_by_area[xarea.address]
        for xline in xarea.lines:
            if xline.address in area_lines:
                dropped_lines += 1
                continue
            line = Line(
                address=xline.address,
                name=xline.name,
                description=getattr(xline, "description", "") or "",
                comment=state.extras.line_comment.get(
                    (xarea.address, xline.address), ""
                ),
                # Coupler pass-through addresses (``AdditionalGroupAddresses``) read from the raw XML
                # (keyed by area/line address), stored comma-separated. Empty when the line has none.
                additional_group_addresses=",".join(
                    str(a)
                    for a in state.extras.line_pass_through.get(
                        (xarea.address, xline.address), []
                    )
                ),
            )
            area.lines.append(line)
            area_lines[xline.address] = line
            _build_line_segments(line, xarea.address, xline, state)
    if dropped_lines:
        state.extras.losses.append(
            ImportLoss(DROPPED_DUPLICATE_LINES, dropped_lines, detail="")
        )


def _build_line_segments(
    line: Line, area_address: int, xline: XMLLine, state: _ImportState
) -> None:
    """Rebuild the line's segments. project/14+20 lines have no ``<Segment>`` (one default segment
    holding all devices); project/22+23 lines carry one ``<Segment>`` per descriptor, and devices
    are placed on their own segment by ``@Id`` membership so multi-segment lines survive intact."""
    key = (area_address, xline.address)
    descs = state.extras.segments.get(key, [])
    default_domain = state.extras.domain_address.get(key)
    if not descs:
        segment = Segment(
            number=0, medium_type=xline.medium_type, domain_address=default_domain
        )
        line.segments.append(segment)
        for xdevice in xline.devices:
            _append_device(segment, xdevice, state)
        return

    device_by_id = {xd.identifier: xd for xd in xline.devices}
    placed: set[str] = set()
    segments: list[Segment] = []
    for desc in descs:
        segment = Segment(
            number=desc.number,
            medium_type=desc.medium_type_ref or xline.medium_type,
            domain_address=(
                desc.domain_address
                if desc.domain_address is not None or len(descs) > 1
                else default_domain
            ),
        )
        line.segments.append(segment)
        segments.append(segment)
        for did in desc.device_ids:
            xdevice = device_by_id.get(did)
            if xdevice is not None:
                _append_device(segment, xdevice, state)
                placed.add(did)
    # Devices xknxproject listed on the line but not matched to any raw segment fall back to the
    # first segment rather than being dropped.
    for xdevice in xline.devices:
        if xdevice.identifier not in placed:
            _append_device(segments[0], xdevice, state)


def _append_device(
    segment: Segment, xdevice: DeviceInstance, state: _ImportState
) -> None:
    try:
        segment.devices.append(_build_device(xdevice, state))
        # Verbose per-device trace so the import is not a silent gap.
        logger.debug(
            "imported device %s (id=%s)",
            getattr(xdevice, "individual_address", "?"),
            xdevice.identifier,
        )
    except Exception as e:
        # Partial load: a single malformed device must not abort the whole import.
        state.skipped_devices += 1
        logger.warning(
            "skipping malformed device on import (address=%s id=%s): %s: %s",
            getattr(xdevice, "individual_address", "?"),
            xdevice.identifier,
            type(e).__name__,
            e,
        )
        logger.debug("device import error detail", exc_info=e)


def _build_device(xdevice: DeviceInstance, state: _ImportState) -> Device:
    module_args = state.extras.module_args.get(xdevice.identifier, {})
    com_objects: list[ComObject] = []
    for coir in xdevice.com_object_instance_refs:
        override = state.extras.com_object_text_overrides.get(
            getattr(coir, "identifier", None) or ""
        )
        row = _build_com_object(coir, override)
        com_objects.append(row)
        state.register_com_object(coir, row)
    group_object_tree = state.extras.group_object_trees.get(xdevice.identifier, "")
    com_objects.extend(
        _unlinked_com_objects_from_tree(
            group_object_tree,
            {co.instance_ref_id for co in com_objects},
            getattr(xdevice, "application_program_ref", None) or "",
        )
    )
    device = Device(
        address=xdevice.address,
        name=xdevice.name,
        product_ref_id=xdevice.product_ref,
        hardware2program_ref_id=xdevice.hardware_program_ref,
        description=xdevice.description,
        comment=state.extras.device_comment.get(xdevice.identifier, ""),
        last_modified=getattr(xdevice, "last_modified", None),
        order_number=xdevice.order_number,
        hardware_name=xdevice.hardware_name,
        product_name=xdevice.product_name,
        manufacturer_name=xdevice.manufacturer_name,
        # Commissioning state (getattr with defaults: optional, and only present on newer
        # xknxproject; a missing attribute must not fail the whole device import).
        serial_number=getattr(xdevice, "serial_number", "") or "",
        last_download=getattr(xdevice, "last_download", None),
        individual_address_loaded=getattr(xdevice, "individual_address_loaded", False),
        application_program_loaded=getattr(
            xdevice, "application_program_loaded", False
        ),
        communication_part_loaded=getattr(xdevice, "communication_part_loaded", False),
        medium_config_loaded=getattr(xdevice, "medium_config_loaded", False),
        parameters_loaded=getattr(xdevice, "parameters_loaded", False),
        com_objects=com_objects,
        parameters=[
            Parameter(ref_id=ref, value=p.value or "")
            for ref, p in xdevice.parameter_instance_refs.items()
        ],
        module_instances=[
            _build_module_instance(mi, module_args) for mi in xdevice.module_instances
        ],
        # Per-device <BinaryData> captured from the raw XML (keyed by DeviceInstance @Id), e.g. a
        # DCA's persisted state. Preserved verbatim so import -> export does not drop it.
        binary_data=[
            DeviceBinaryData(
                name=e.name,
                data=e.data,
                ref_id=e.ref_id,
                do_not_copy=e.do_not_copy,
            )
            for e in state.extras.binary_data.get(xdevice.identifier, [])
        ],
        # The <GroupObjectTree> (channel/folder grouping), captured verbatim from the raw XML and
        # re-emitted on export; xknxproject does not surface it (issue #14).
        group_object_tree=group_object_tree,
        # The <IPConfig> (IP interface/router config), captured verbatim from the raw XML and
        # re-emitted on export; xknxproject does not surface it. None when the device had none.
        ip_config=state.extras.ip_config.get(xdevice.identifier),
        # Extra individual addresses a coupler/interface reserves (<AdditionalAddresses>), read from
        # the raw XML (xknxproject drops them) and re-emitted verbatim on export.
        additional_addresses=[
            DeviceAdditionalAddress(
                address=e.address,
                name=e.name,
                description=e.description,
                comment=e.comment,
            )
            for e in state.extras.additional_addresses.get(xdevice.identifier, [])
        ],
    )
    state.register_device(xdevice.individual_address, xdevice.identifier, device)
    return device


def _build_module_instance(mi: object, module_args: ModuleArgs) -> ModuleInstance:
    """Build a ModuleInstance row, enriching it with the raw ``RepeatIndex``/``<Arguments>``.

    ``xknxproject`` only gives ``identifier``/``module_def_id``; the allocator arguments and repeat
    index come from ``module_args`` (captured from the raw XML, keyed by the on-disk ``@Id`` which
    equals ``identifier``), so a module-based device survives export.
    """
    repeat_index, arguments = module_args.get(mi.identifier, ("", []))  # type: ignore[attr-defined]
    return ModuleInstance(
        instance_id=mi.identifier,  # type: ignore[attr-defined]
        ref_id=mi.module_def_id,  # type: ignore[attr-defined]
        repeat_index=repeat_index,
        arguments=arguments,
    )


def _unlinked_com_objects_from_tree(
    tree_xml: str, known_instance_ref_ids: set[str], application_program_ref: str
) -> list[ComObject]:
    """Com-object rows for instances the device activated but ETS did not write out.

    ETS only emits a ``<ComObjectInstanceRef>`` for objects carrying links or overrides; the
    authoritative active set is ``<GroupObjectTree>/Node/@GroupObjectInstances``. Without these
    rows an active-but-unlinked channel (a free rocker, a spare actuator channel) is pruned from
    the UI together with its parameters. Flags/overrides stay ``None`` (= application default).
    """
    if not tree_xml:
        return []
    from xknxproject.util import strip_module_instance

    rows: list[ComObject] = []
    root = ET.fromstring(tree_xml)
    # Root-level instances are channel-less; nodes carry theirs under their channel.
    for node in [root, *root.iter("Node")]:
        channel = node.get("RefId") if node.get("Type") == "Channel" else None
        for inst in (node.get("GroupObjectInstances") or "").split():
            if inst in known_instance_ref_ids:
                continue
            known_instance_ref_ids.add(inst)
            if application_program_ref and not inst.startswith(application_program_ref):
                ref_id = f"{application_program_ref}_{strip_module_instance(inst, 'O')}"
            else:
                ref_id = inst  # ETS4: already fully prefixed
            rows.append(ComObject(ref_id=ref_id, instance_ref_id=inst, channel_id=channel))
    return rows


def _build_com_object(
    coir: ComObjectInstanceRef,
    text_override: tuple[str | None, str | None, str | None] | None = None,
) -> ComObject:
    # ``com_object_ref_id`` is xknxproject's resolved *definition* id: it runs the ref through
    # strip_module_instance(), so every instance of a module shares one value. Keep the instance's
    # own ``ref_id`` alongside it, or the export cannot tell the instances apart again.
    text, function_text, description = text_override or (None, None, None)
    return ComObject(
        ref_id=coir.com_object_ref_id or coir.ref_id,
        instance_ref_id=coir.ref_id or "",
        channel_id=coir.channel,
        read_flag=coir.read_flag,
        write_flag=coir.write_flag,
        communication_flag=coir.communication_flag,
        transmit_flag=coir.transmit_flag,
        update_flag=coir.update_flag,
        read_on_init_flag=coir.read_on_init_flag,
        text_override=text,
        function_text_override=function_text,
        description_override=description,
    )


# --- group ranges + addresses + links -------------------------------------


def _build_group_addresses(
    installation: Installation, parser: XMLParser, state: _ImportState
) -> None:
    ranges: list[GroupRange] = []
    _build_ranges(installation, parser.group_ranges, None, ranges, state)

    for xga in parser.group_addresses:
        group_range = _range_for(ranges, xga.raw_address)
        if group_range is None:
            continue  # a group address outside every range would violate the schema; skip it
        ga = GroupAddress(
            address=xga.raw_address,
            name=xga.name,
            datapoint_type=_dpt(xga.dpt),
            description=xga.description,
            comment=xga.comment,
            data_secure=bool(xga.data_secure_key),
            # ``Central`` read from the raw XML by address (xknxproject does not surface it).
            central=xga.raw_address in state.extras.central_ga,
            # ``Global`` read from the raw XML by address (xknxproject does not surface it).
            is_global=xga.raw_address in state.extras.global_ga,
            # ``Unfiltered`` (route through couplers unconditionally) read from the raw XML by
            # address, since xknxproject does not surface it.
            unfiltered=xga.raw_address in state.extras.unfiltered_ga,
        )
        group_range.group_addresses.append(ga)
        state.register_group_address(xga.identifier, ga)


def _build_ranges(
    installation: Installation,
    xranges: list[XMLGroupRange],
    parent: GroupRange | None,
    collected: list[GroupRange],
    state: _ImportState,
) -> None:
    for xr in xranges:
        gr = GroupRange(
            range_start=xr.range_start,
            range_end=xr.range_end,
            name=xr.name,
            comment=getattr(xr, "comment", "") or "",
            # ``Description`` read from the raw XML by (start, end); xknxproject does not surface it.
            description=state.extras.grouprange_description.get(
                (xr.range_start, xr.range_end), ""
            ),
            parent=parent,
            # ``Unfiltered`` read from the raw XML by (start, end); xknxproject does not surface it.
            unfiltered=(xr.range_start, xr.range_end) in state.extras.unfiltered_ranges,
        )
        installation.group_ranges.append(gr)
        collected.append(gr)
        _build_ranges(installation, xr.group_ranges, gr, collected, state)


def _range_for(ranges: list[GroupRange], address: int) -> GroupRange | None:
    """The smallest (leaf) range that contains ``address``."""
    best: GroupRange | None = None
    for gr in ranges:
        if gr.range_start <= address <= gr.range_end and (
            best is None
            or (gr.range_end - gr.range_start) < (best.range_end - best.range_start)
        ):
            best = gr
    return best


def _build_links(state: _ImportState) -> None:
    for coir, com_object in state.com_objects:
        for i, link in enumerate(coir.links or []):
            ga = state.group_addresses.get(link)
            if ga is None:
                continue  # link to a group address not present in the project; drop it
            com_object.links.append(
                ComObjectLink(group_address=ga, is_sending=(i == 0))
            )


# --- spaces (buildings/rooms) + functions ---------------------------------


def _build_spaces(
    installation: Installation, parser: XMLParser, state: _ImportState
) -> None:
    space_by_identifier: dict[str, Space] = {}
    for order, xspace in enumerate(parser.spaces):
        _build_space(installation, xspace, None, order, state, space_by_identifier)

    for order, func in enumerate(parser.functions):
        space = space_by_identifier.get(func.space_id)
        if space is None:
            continue  # function references a space that was not present
        comment, description = state.extras.function_meta.get(func.identifier, ("", ""))
        function = Function(
            function_type=func.function_type,
            name=func.name,
            usage_text=func.usage_text,
            comment=comment,
            description=description,
            order=order,
        )
        for ref in func.group_addresses:
            ga = state.group_addresses.get(ref.ref_id)
            if ga is not None:
                function.group_addresses.append(
                    FunctionGroupAddress(group_address=ga, role=ref.role)
                )
        space.functions.append(function)


def _build_space(
    installation: Installation,
    xspace: XMLSpace,
    parent: Space | None,
    order: int,
    state: _ImportState,
    space_by_identifier: dict[str, Space],
) -> None:
    space = Space(
        space_type=xspace.space_type.value,
        name=xspace.name,
        number=xspace.number,
        usage=getattr(xspace, "usage_id", None) or "",
        usage_text=xspace.usage_text,
        description=getattr(xspace, "description", "") or "",
        comment=state.extras.space_comment.get(xspace.identifier, ""),
        order=order,
        parent=parent,
    )
    installation.spaces.append(space)
    space_by_identifier[xspace.identifier] = space
    for ia in xspace.devices:
        device = state.device_by_ia.get(ia)
        if device is not None:
            device.space = space
    for child_order, child in enumerate(xspace.spaces):
        _build_space(
            installation, child, space, child_order, state, space_by_identifier
        )


def _build_trades(installation: Installation, state: _ImportState) -> None:
    """Rebuild the Installation ``<Trades>`` tree (Gewerke) from the captured entries, resolving each
    ``DeviceInstanceRef`` back to its :class:`Device` row via the on-disk ``@Id``."""
    for order, entry in enumerate(state.extras.trades):
        _build_trade(installation, entry, None, order, state)


def _build_trade(
    installation: Installation,
    entry: _TradeEntry,
    parent: Trade | None,
    order: int,
    state: _ImportState,
) -> None:
    trade = Trade(
        name=entry.name,
        number=entry.number,
        comment=entry.comment,
        description=entry.description,
        completion_status=entry.completion_status,
        context=entry.context,
        order=order,
        parent=parent,
    )
    installation.trades.append(trade)
    for dev_order, device_id in enumerate(entry.device_ids):
        device = state.device_by_identifier.get(device_id)
        if device is not None:
            trade.devices.append(TradeDevice(device=device, order=dev_order))
    for child_order, child in enumerate(entry.children):
        _build_trade(installation, child, trade, child_order, state)


def _dpt(dpt: DPTType | None) -> str | None:
    if dpt is None:
        return None
    main = dpt["main"]
    sub = dpt["sub"]
    return f"DPST-{main}-{sub}" if sub is not None else f"DPT-{main}"


class _ImportState:
    """Cross-references built while walking devices, used to wire links once addresses exist."""

    def __init__(self, extras: _RawExtras | None = None) -> None:
        self.com_objects: list[tuple[ComObjectInstanceRef, ComObject]] = []
        self.group_addresses: dict[str, GroupAddress] = {}
        self.device_by_ia: dict[str, Device] = {}
        self.device_by_identifier: dict[str, Device] = {}
        # Per-device BinaryData / module Arguments captured from the raw project XML.
        self.extras: _RawExtras = extras or _RawExtras()
        # Devices xknxproject parsed but that failed to build (see _append_device); surfaced in the
        # import summary so a remote log shows whether devices were dropped rather than never parsed.
        self.skipped_devices: int = 0

    def register_com_object(self, coir: ComObjectInstanceRef, row: ComObject) -> None:
        self.com_objects.append((coir, row))

    def register_group_address(self, identifier: str, row: GroupAddress) -> None:
        self.group_addresses[identifier] = row

    def register_device(
        self, individual_address: str, identifier: str, row: Device
    ) -> None:
        self.device_by_ia[individual_address] = row
        self.device_by_identifier[identifier] = row
