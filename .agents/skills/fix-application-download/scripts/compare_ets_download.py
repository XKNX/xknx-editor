"""Compare an ETS telegram dump with the download image xknx-editor would write.

Run from the repository root:

  uv run python .agents/skills/fix-application-download/scripts/compare_ets_download.py \
    --ets /path/to/ets-group-monitor.xml \
    --project /path/to/project.xknx \
    --device 1.1.4
"""

from __future__ import annotations

import argparse
import sys
import xml.etree.ElementTree as ET
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from xknx.cemi.cemi_frame import CEMIFrame, CEMILData
from xknx.exceptions import ConversionError
from xknx.telegram.address import IndividualAddress
from xknx.telegram.apci import (
    APCI,
    MemoryExtendedRead,
    MemoryExtendedReadResponse,
    MemoryExtendedWrite,
    MemoryRead,
    MemoryResponse,
    MemoryWrite,
    PropertyValueWrite,
    Restart,
)

_LOAD_EVENTS = {1: "LOAD", 2: "COMPLETE", 3: "ADDITIONAL", 4: "UNLOAD"}
_SEGMENTS = {
    0: "ABS_DATA",
    1: "ABS_STACK",
    2: "ABS_TASK",
    3: "TASK_PTR",
    4: "TASK_CTRL_1",
    5: "TASK_CTRL_2",
    0x0A: "RELATIVE",
    0x0B: "DATA_RELATIVE",
}
_COMMUNICATION_BIT = 0x04
_GROUP_OBJECT_HEADER = 5
_GROUP_OBJECT_RECORD = 4


@dataclass(frozen=True)
class ManagementOp:
    """One management telegram the tool sent or the device answered with."""

    direction: str
    service: str
    address: int = 0
    data: bytes = b""
    count: int = 0
    object_index: int = 0
    property_id: int = 0


@dataclass
class ControlRecord:
    """One decoded load-control write."""

    where: str
    lsm: int | None
    event: str
    segment: str | None = None
    address: int | None = None
    length: int | None = None
    memory_type: int | None = None
    attributes: int | None = None
    pei: int | None = None
    application_id: str | None = None
    raw: str = ""


@dataclass
class EtsTrace:
    """What a successful ETS programming did to one device."""

    device: str
    tool: str
    telegrams: int
    undecoded: int
    acks: int = 0
    ops: list[ManagementOp] = field(default_factory=list)
    written: dict[int, int] = field(default_factory=dict)
    final: dict[int, int] = field(default_factory=dict)
    controls: list[ControlRecord] = field(default_factory=list)
    control_address: int | None = None


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    trace = decode_ets(Path(args.ets), args.device)
    _print_trace(trace)
    if not args.project:
        print("\nNo project given; memory bytes were not compared.")
        return 0
    try:
        ours, meta = editor_image(
            Path(args.project),
            Path(args.catalog) if args.catalog else _default_catalog(),
            trace.device,
            args.password,
        )
    except (OSError, ValueError, KeyError) as exc:
        print(f"\nCould not build the editor image: {exc}", file=sys.stderr)
        return 2
    _print_meta(meta)
    return _print_compare(trace, ours, meta)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ets", required=True, help="ETS group-monitor XML dump")
    parser.add_argument("--project", help="Saved .xknx project that was programmed")
    parser.add_argument("--device", help="Individual address, if the dump has several")
    parser.add_argument("--catalog", help="catalog.xknxcatalog (default: the editor's)")
    parser.add_argument(
        "--password", help="Project password, when the file is protected"
    )
    return parser


def _default_catalog() -> Path:
    import platformdirs

    return Path(platformdirs.user_data_dir("xknx-editor")) / "catalog.xknxcatalog"


def decode_ets(path: Path, device: str | None) -> EtsTrace:
    """Rebuild the memory ETS wrote and the load-control records it sent."""
    telegrams = _telegrams(path)
    chosen, tool = _choose_device(telegrams, device)
    trace = EtsTrace(device=chosen, tool=tool, telegrams=len(telegrams), undecoded=0)
    for src, dst, payload in telegrams:
        if payload is None:
            trace.acks += 1
            continue
        if str(dst) != chosen and str(src) != chosen:
            continue
        if not isinstance(dst, IndividualAddress) and not isinstance(
            src, IndividualAddress
        ):
            continue
        _absorb(trace, str(src) == chosen, payload)
    trace.controls = _control_records(trace)
    return trace


def _telegrams(path: Path) -> list[tuple[object, object, APCI | None]]:
    out: list[tuple[object, object, APCI | None]] = []
    for _event, element in ET.iterparse(path, events=("end",)):
        if element.tag.split("}")[-1] != "Telegram":
            continue
        raw = element.attrib.get("RawData")
        element.clear()
        if not raw:
            continue
        try:
            frame = CEMIFrame.from_knx(bytes.fromhex(raw))
        except (ConversionError, ValueError):
            out.append(("", "", None))
            continue
        data = frame.data
        if not isinstance(data, CEMILData):
            continue
        out.append((data.src_addr, data.dst_addr, data.payload))
    if not out:
        raise ValueError(f"{path} has no Telegram RawData elements")
    return out


def _choose_device(
    telegrams: list[tuple[object, object, APCI | None]], requested: str | None
) -> tuple[str, str]:
    counts: Counter[str] = Counter()
    tools: dict[str, Counter[str]] = {}
    for src, dst, payload in telegrams:
        if not isinstance(
            payload, (MemoryWrite, MemoryExtendedWrite, PropertyValueWrite)
        ):
            continue
        if not isinstance(dst, IndividualAddress):
            continue
        address = str(dst)
        counts[address] += 1
        tools.setdefault(address, Counter())[str(src)] += 1
    if requested:
        if requested not in counts:
            known = ", ".join(counts) or "none"
            raise ValueError(f"{requested} received no management writes (saw {known})")
        tool = tools[requested].most_common(1)[0][0]
        return requested, tool
    if not counts:
        raise ValueError("the dump has no memory or property writes to a device")
    address = counts.most_common(1)[0][0]
    tool = tools[address].most_common(1)[0][0]
    return address, tool


def _absorb(trace: EtsTrace, from_device: bool, payload: APCI) -> None:
    if isinstance(payload, (MemoryWrite, MemoryExtendedWrite)) and not from_device:
        for offset, byte in enumerate(payload.data):
            trace.written[payload.address + offset] = byte
            trace.final[payload.address + offset] = byte
        trace.ops.append(
            ManagementOp("to-device", "memory-write", payload.address, payload.data)
        )
        return
    if (
        isinstance(payload, (MemoryResponse, MemoryExtendedReadResponse))
        and from_device
    ):
        for offset, byte in enumerate(payload.data):
            trace.final.setdefault(payload.address + offset, byte)
        trace.ops.append(
            ManagementOp(
                "from-device", "memory-response", payload.address, payload.data
            )
        )
        return
    if isinstance(payload, (MemoryRead, MemoryExtendedRead)) and not from_device:
        trace.ops.append(
            ManagementOp(
                "to-device", "memory-read", payload.address, count=payload.count
            )
        )
        return
    if isinstance(payload, PropertyValueWrite) and not from_device:
        trace.ops.append(
            ManagementOp(
                "to-device",
                "property-write",
                data=payload.data,
                object_index=payload.object_index,
                property_id=payload.property_id,
            )
        )
        return
    if isinstance(payload, Restart) and not from_device:
        trace.ops.append(ManagementOp("to-device", "restart"))


def _control_records(trace: EtsTrace) -> list[ControlRecord]:
    candidates: Counter[int] = Counter()
    for op in trace.ops:
        if op.service != "memory-write" or len(op.data) not in (10, 11):
            continue
        if (op.data[0] & 0x0F) in _LOAD_EVENTS:
            candidates[op.address] += 1
    if candidates:
        trace.control_address = candidates.most_common(1)[0][0]
    records: list[ControlRecord] = []
    for op in trace.ops:
        if (
            op.service == "memory-write"
            and op.address == trace.control_address
            and len(op.data) in (10, 11)
        ):
            decoded = _decode_memory_control(op.data)
            if decoded is not None:
                records.append(decoded)
        elif op.service == "property-write" and op.property_id == 5:
            decoded = _decode_property_control(op)
            if decoded is not None:
                records.append(decoded)
    return records


def _decode_memory_control(data: bytes) -> ControlRecord | None:
    event = _LOAD_EVENTS.get(data[0] & 0x0F)
    if event is None:
        return None
    record = ControlRecord(
        where="memory",
        lsm=data[0] >> 4,
        event=event,
        raw=data.hex(),
    )
    if event != "ADDITIONAL" or len(data) < 3:
        return record
    # An 11-octet M112 record inserts a reserved 0 after the segment type.
    payload = data[3:] if len(data) == 11 else data[2:]
    _fill_segment(record, data[1], payload)
    return record


def _decode_property_control(op: ManagementOp) -> ControlRecord | None:
    event = _LOAD_EVENTS.get(op.data[0] if op.data else -1)
    if event is None:
        return None
    record = ControlRecord(
        where=f"property obj {op.object_index}",
        lsm=None,
        event=event,
        raw=op.data.hex(),
    )
    if event == "ADDITIONAL" and len(op.data) >= 2:
        _fill_segment(record, op.data[1], op.data[2:])
    return record


def _fill_segment(record: ControlRecord, segment_type: int, payload: bytes) -> None:
    record.segment = _SEGMENTS.get(segment_type, f"0x{segment_type:02x}")
    if segment_type in (0, 1) and len(payload) >= 7:
        record.address = int.from_bytes(payload[0:2], "big")
        record.length = int.from_bytes(payload[2:4], "big")
        record.memory_type = payload[5]
        record.attributes = payload[6]
    elif segment_type == 2 and len(payload) >= 8:
        record.address = int.from_bytes(payload[0:2], "big")
        record.pei = payload[2]
        record.application_id = payload[3:8].hex()


def editor_image(
    project_path: Path,
    catalog_path: Path,
    device_address: str,
    password: str | None,
) -> tuple[dict[int, int], dict[str, object]]:
    """The bytes ``build_image`` would write for the saved device."""
    from xknxeditor.catalog.core.service import CatalogService
    from xknxeditor.download.image import GroupCommunication, build_image
    from xknxeditor.download.project_data import GroupObjectLink
    from xknxeditor.namespaces.intermediate.com_object_instance_ref_t import (
        ComObjectInstanceRef,
    )
    from xknxeditor.namespaces.intermediate.enable_t import Enable
    from xknxeditor.namespaces.intermediate.module_instance_t import ModuleInstance
    from xknxeditor.namespaces.intermediate.parameter_instance_ref_t import (
        ParameterInstanceRef,
    )
    from xknxeditor.prod.parser_v2.dynamic import DynamicUI
    from xknxeditor.proj.core.addressing import format_ia
    from xknxeditor.proj.core.identity import qualified_com_object_ref
    from xknxeditor.proj.core.service import ProjectService

    if not catalog_path.is_file():
        raise ValueError(f"catalog not found: {catalog_path}")
    catalog = CatalogService(catalog_path)
    projects = ProjectService()
    project_id = projects.open(project_path, password)
    try:
        device = _project_device(projects, project_id, device_address, format_ia)
        application_id = device.application_program_ref_id
        if device.hardware2program_ref_id:
            application_id = (
                catalog.get_program_application_id(device.hardware2program_ref_id)
                or application_id
            )
        if not application_id:
            raise ValueError(f"{device_address} has no application program")
        application = catalog.get_application(application_id)
        if application is None:
            raise ValueError(f"application {application_id} is not in the catalog")
        program, tree = application.resolved()

        def enable(value: bool | None) -> Enable | None:
            if value is None:
                return None
            return Enable.ENABLED if value else Enable.DISABLED

        parameters = [
            ParameterInstanceRef(ref_id=row.ref_id, value=row.value)
            for row in device.parameters
        ]
        modules = [
            ModuleInstance(id=row.instance_id, ref_id=row.ref_id)
            for row in device.module_instances
        ]
        com_objects = []
        links: list[GroupObjectLink] = []
        for row in device.com_objects:
            ref_id = qualified_com_object_ref(
                row.ref_id, row.instance_ref_id or "", application.id
            )
            flags = (
                row.communication_flag,
                row.read_flag,
                row.write_flag,
                row.transmit_flag,
                row.update_flag,
                row.read_on_init_flag,
            )
            if all(flag is None for flag in flags):
                com_objects.append(ComObjectInstanceRef(ref_id=ref_id))
            else:
                com_objects.append(
                    ComObjectInstanceRef(
                        ref_id=ref_id,
                        communication_flag=enable(row.communication_flag),
                        read_flag=enable(row.read_flag),
                        write_flag=enable(row.write_flag),
                        transmit_flag=enable(row.transmit_flag),
                        update_flag=enable(row.update_flag),
                        read_on_init_flag=enable(row.read_on_init_flag),
                    )
                )
            for link in row.links:
                links.append(
                    GroupObjectLink(
                        com_object_ref_id=ref_id,
                        group_address=link.group_address.address,
                        sending=link.is_sending,
                    )
                )
        ui = DynamicUI(
            program,
            parameter_instance_refs=parameters,
            module_instances=modules,
            com_object_instance_refs=com_objects,
            tree_builder=tree,
        )
        area, line, octet = (int(part) for part in device_address.split("."))
        image = build_image(
            application,
            ui=ui,
            group_communication=GroupCommunication(
                device_address=(area << 12) | (line << 8) | octet,
                links=links,
            ),
        )
    finally:
        projects.close(project_id)

    written: dict[int, int] = {}
    for segment in image.segments:
        for address, data in segment.masked_runs(include_initialized=True):
            for offset, byte in enumerate(data):
                written[address + offset] = byte
    static = application.program.static
    options = getattr(static, "options", None) if static is not None else None
    patch = bool(
        options is not None and options.legacy_patch_manufacturer_id_in_task_segment
    )
    manufacturer = int(application.manufacturer_id.split("-")[-1], 16)
    application_bytes = (
        manufacturer.to_bytes(2, "big")
        + application.program.application_number.to_bytes(2, "big")
        + bytes([application.program.application_version & 0xFF])
    )
    if patch:
        application_bytes = b"\x00\x02" + application_bytes[2:]
    meta: dict[str, object] = {
        "application": application.id,
        "mask": application.program.mask_version,
        "load_procedure_style": str(application.load_procedure_style),
        "task_application_id": application_bytes.hex(),
        "legacy_manufacturer_patch": patch,
    }
    return written, meta


def _project_device(
    projects: object,
    project_id: str,
    device_address: str,
    format_ia: object,
) -> object:
    rows = projects.devices(project_id, with_contents=True)  # type: ignore[attr-defined]
    found = []
    for device in rows:
        if device.address is None:
            continue
        line = device.segment.line
        address = format_ia(line.area.address, line.address, device.address)  # type: ignore[operator]
        found.append(f"{address} {device.name}")
        if address == device_address:
            return device
    raise ValueError(
        f"{device_address} is not in the project. Devices: {', '.join(found)}"
    )


def _print_trace(trace: EtsTrace) -> None:
    print(f"device {trace.device}  tool {trace.tool}")
    print(
        f"telegrams {trace.telegrams}  acks {trace.acks}  undecoded {trace.undecoded}  "
        f"memory writes {sum(1 for op in trace.ops if op.service == 'memory-write')}"
    )
    if trace.control_address is not None:
        print(f"load control memory 0x{trace.control_address:04x}")
    for record in trace.controls:
        _print_control(record)
    print("management sequence:")
    _print_sequence(trace)


def _print_control(record: ControlRecord) -> None:
    parts = [
        f"  {record.where} LSM{record.lsm} {record.event}"
        if record.lsm is not None
        else f"  {record.where} {record.event}"
    ]
    if record.segment:
        parts.append(record.segment)
    if record.address is not None:
        parts.append(f"@0x{record.address:04x}")
    if record.length is not None:
        parts.append(f"len {record.length}")
    if record.memory_type is not None:
        parts.append(f"mem {record.memory_type}")
    if record.attributes is not None:
        parts.append(f"attr 0x{record.attributes:02x}")
    if record.pei is not None:
        parts.append(f"pei {record.pei}")
    if record.application_id:
        parts.append(f"app {record.application_id}")
    parts.append(record.raw)
    print(" ".join(parts))


def _print_sequence(trace: EtsTrace) -> None:
    pending: list[ManagementOp] = []

    def flush() -> None:
        if not pending:
            return
        start = min(op.address for op in pending)
        end = max(op.address + len(op.data) for op in pending)
        total = sum(len(op.data) for op in pending)
        print(
            f"  memory 0x{start:04x}..0x{end:04x} ({total} bytes, {len(pending)} writes)"
        )
        pending.clear()

    for op in trace.ops:
        if op.service == "memory-write" and op.address != trace.control_address:
            pending.append(op)
            continue
        flush()
        if op.service == "memory-read":
            print(f"  read 0x{op.address:04x} count {op.count}")
        elif op.service == "property-write" and op.property_id != 5:
            print(
                f"  property obj {op.object_index} pid {op.property_id} "
                f"{op.data.hex() or '(empty)'}"
            )
        elif op.service == "restart":
            print("  restart")
    flush()


def _print_meta(meta: dict[str, object]) -> None:
    print(
        f"\neditor application {meta['application']}  mask {meta['mask']}  "
        f"style {meta['load_procedure_style']}"
    )
    print(
        f"editor task application id {meta['task_application_id']}  "
        f"legacy manufacturer patch {meta['legacy_manufacturer_patch']}"
    )


def _print_compare(
    trace: EtsTrace, ours: dict[int, int], meta: dict[str, object]
) -> int:
    segments = [
        record
        for record in trace.controls
        if record.segment in ("ABS_DATA", "ABS_STACK")
        and record.address is not None
        and record.length is not None
    ]
    diffs: list[tuple[int, int, int, str]] = []
    uncovered: list[int] = []
    for address, ets in sorted(trace.written.items()):
        if trace.control_address is not None and address == trace.control_address:
            continue
        if (
            trace.control_address is not None
            and trace.control_address <= address < trace.control_address + 11
        ):
            continue
        if address not in ours:
            uncovered.append(address)
            continue
        if ours[address] != ets:
            diffs.append(
                (
                    address,
                    ours[address],
                    ets,
                    _classify(address, ours[address], ets, segments, trace.final),
                )
            )
    clobber = [
        address
        for address, ets in trace.final.items()
        if address not in trace.written and address in ours and ours[address] != ets
    ]
    task_mismatches = [
        record
        for record in trace.controls
        if record.application_id
        and record.application_id != meta["task_application_id"]
    ]
    print(
        f"\nbytes ETS wrote {len(trace.written)}  "
        f"compared {len(trace.written) - len(uncovered)}  "
        f"diffs {len(diffs)}  uncovered {len(uncovered)}  "
        f"would overwrite bytes ETS kept {len(clobber)}"
    )
    _print_diffs(diffs)
    if uncovered:
        print(f"ETS wrote {len(uncovered)} bytes the editor image does not contain")
        print("  " + _ranges(uncovered))
        print(
            "HINT uncovered: if these addresses are the start of the association "
            "table and the editor has the same bytes at the declared association "
            "address, see reference.md (packed association table). Otherwise follow "
            "the load-control record for this address, then the mask flavour in "
            "resources.py."
        )
    if clobber:
        print("editor would change bytes ETS read and left in place:")
        print("  " + _ranges(clobber))
    for record in task_mismatches:
        print(
            f"task segment app {record.application_id} "
            f"!= editor {meta['task_application_id']} ({record.raw})"
        )
    _print_hints(diffs, task_mismatches)
    failed = bool(diffs or uncovered or clobber or task_mismatches)
    print("RESULT " + ("diffs" if failed else "match"))
    return 1 if failed else 0


def _classify(
    address: int,
    ours: int,
    ets: int,
    segments: list[ControlRecord],
    final: dict[int, int],
) -> str:
    if ours ^ ets != _COMMUNICATION_BIT:
        return "other"
    for record in segments:
        assert record.address is not None and record.length is not None
        if not record.address <= address < record.address + record.length:
            continue
        offset = address - record.address
        count = final.get(record.address)
        if (
            count is not None
            and offset >= _GROUP_OBJECT_HEADER
            and (offset - _GROUP_OBJECT_HEADER) % _GROUP_OBJECT_RECORD == 0
            and _GROUP_OBJECT_HEADER + count * _GROUP_OBJECT_RECORD <= record.length
        ):
            return "group_object_communication"
    return "communication_bit"


def _print_diffs(diffs: list[tuple[int, int, int, str]]) -> None:
    for index, (address, ours, ets, kind) in enumerate(diffs):
        if index == 40:
            print(f"  … {len(diffs) - index} more diffs")
            return
        print(f"  0x{address:04x} {kind} editor 0x{ours:02x} ets 0x{ets:02x}")


def _ranges(addresses: list[int]) -> str:
    if not addresses:
        return ""
    ordered = sorted(addresses)
    spans: list[tuple[int, int]] = []
    start = prev = ordered[0]
    for address in ordered[1:]:
        if address == prev + 1:
            prev = address
            continue
        spans.append((start, prev))
        start = prev = address
    spans.append((start, prev))
    text = ", ".join(
        f"0x{start:04x}" if start == end else f"0x{start:04x}..0x{end:04x}"
        for start, end in spans[:12]
    )
    if len(spans) > 12:
        text += f", … {len(spans) - 12} more ranges"
    return text


def _print_hints(
    diffs: list[tuple[int, int, int, str]], mismatches: list[ControlRecord]
) -> None:
    kinds = {kind for _address, _ours, _ets, kind in diffs}
    if "group_object_communication" in kinds or "communication_bit" in kinds:
        print(
            "HINT group-object communication bit: unused table slots still have "
            "bit 0x04. See reference.md."
        )
    if mismatches:
        print(
            "HINT task-segment manufacturer: ETS application id differs from the "
            "id the editor sends. See reference.md."
        )
    if "other" in kinds:
        print(
            "HINT unclassified byte diffs: inspect the addresses above before editing."
        )


if __name__ == "__main__":
    sys.exit(main())
