"""Prepare parameter memory and the surrounding table load transaction.

The plug-in's download comes in two forms. Version 1.2.7 (and before) loads the group
communication tables first, checks that they are loaded and then loads the application
with its parameter memory. Later versions load the application and its parameter memory
first, then the tables, and load the application's segment again around them, marking the
download in progress (property 205 of object 6: 5Ah, then 10h when done).
"""

from collections.abc import Mapping, Sequence
from dataclasses import replace

from xknxeditor.download.conditions import (
    ConditionalRead,
    ConditionalWrite,
    FinalPropertyRead,
    PropertyAlternatives,
)
from xknxeditor.download.errors import ImageError
from xknxeditor.download.image import (
    DownloadImage,
    GroupCommunication,
    MemorySegment,
    ObjectMemorySegment,
    PropertyValue,
    build_image,
)
from xknxeditor.download.prepared import PreparedDownload
from xknxeditor.download.scope import DownloadScope
from xknxeditor.namespaces.intermediate.ld_ctrl_compare_prop_t import LdCtrlCompareProp
from xknxeditor.namespaces.intermediate.ld_ctrl_delay_t import LdCtrlDelay
from xknxeditor.namespaces.intermediate.ld_ctrl_load_completed_t import (
    LdCtrlLoadCompleted,
)
from xknxeditor.namespaces.intermediate.ld_ctrl_load_image_prop_t import (
    LdCtrlLoadImageProp,
)
from xknxeditor.namespaces.intermediate.ld_ctrl_load_t import LdCtrlLoad
from xknxeditor.namespaces.intermediate.ld_ctrl_rel_segment_t import LdCtrlRelSegment
from xknxeditor.namespaces.intermediate.ld_ctrl_restart_t import LdCtrlRestart
from xknxeditor.namespaces.intermediate.ld_ctrl_unload_t import LdCtrlUnload
from xknxeditor.namespaces.intermediate.ld_ctrl_write_mem_t import LdCtrlWriteMem
from xknxeditor.namespaces.intermediate.ld_ctrl_write_prop_t import LdCtrlWriteProp
from xknxeditor.namespaces.intermediate.ld_ctrl_write_rel_mem_t import LdCtrlWriteRelMem
from xknxeditor.prod import Application

from .description import Description
from .image import CompiledImage

# The last version with the download that loads the tables first, and the first one known
# with the later download; the versions between are not known.
_TABLES_FIRST_UNTIL = (1, 2, 7)
_APPLICATION_FIRST_FROM = (1, 2, 16)
# Octets per memory write: absolute writes take 10, relative ones 8.
_ABSOLUTE_CHUNK = 10
_RELATIVE_CHUNK = 8
_LOADED = b"\x01"


def validate_profile(application: Application, description: Description) -> None:
    """Require compatibility metadata and the implemented fixed-memory layout."""
    program = application.program
    if program.mask_version != "MV-07B0":
        raise ValueError("Unsupported Siemens mask version")
    version = description.plugin_version
    if version is not None and _TABLES_FIRST_UNTIL < version < _APPLICATION_FIRST_FROM:
        raise ValueError(
            "Unsupported Siemens Generic Plug-in version "
            + ".".join(str(v) for v in version)
        )
    hardware = description.compatible_hardware
    if not hardware or any(len(v) != 6 for v in hardware):
        raise ValueError("Missing Siemens hardware compatibility data")
    extension = program.static.extension
    if extension is not None and any(
        token in b.ref_id.lower()
        for b in extension.baggage
        for token in ("functab_", "reftab_")
    ):
        raise ValueError("Additional Siemens function tables are not implemented")


def _identity(application: Application) -> bytes:
    """The application's manufacturer, number and version as PID 13 holds them."""
    return bytes.fromhex(
        application.id[2:6] + application.id[9:13] + application.id[14:16]
    )


def _application_checks(description: Description, identity: bytes) -> list[object]:
    """The check of the device's application (PID 13) before anything is written.

    A plug-in of version 1 takes the device's application of the same manufacturer and
    number, with a version up to the product's (1.2.16) or from it on (1.2.7). A later one
    takes one of the description's application id patterns ("*" ends a prefix), any
    application when it has none."""
    version = description.plugin_version
    if version is not None and version < (2,):
        head = int.from_bytes(identity[:4], "big") << 8
        own = identity[4]
        low, high = (own, 0xFF) if version <= _TABLES_FIRST_UNTIL else (0, own)
        return [
            LdCtrlCompareProp(
                obj_idx=4,
                prop_id=13,
                inline_data=identity,
                range=f"[{head | low},{head | high}]",
            )
        ]
    checks: list[LdCtrlCompareProp] = []
    for pattern in description.application_ids:
        if len(pattern) != 10:
            raise ImageError("Invalid Siemens application compatibility pattern")
        prefix = pattern.split("*", 1)[0]
        if len(prefix) % 2:
            raise ImageError("Unsupported Siemens application compatibility pattern")
        checks.append(
            LdCtrlCompareProp(
                obj_idx=4,
                prop_id=13,
                inline_data=bytes.fromhex(prefix).ljust(5, b"\0"),
                mask=(b"\xff" * (len(prefix) // 2)).ljust(5, b"\0"),
            )
        )
    return [PropertyAlternatives(tuple(checks))] if checks else []


def prepare_image(
    application: Application,
    description: Description,
    compiled: CompiledImage,
    parameters: Mapping[str, str],
    communication: GroupCommunication,
) -> PreparedDownload:
    """Build a full image and a source-defined multipass procedure, entirely offline."""
    validate_profile(application, description)
    if communication.security is not None or communication.filter_table is not None:
        raise ValueError("Unsupported Siemens communication profile")
    host = build_image(
        application, parameter_values=parameters, group_communication=communication
    )
    host = host.with_association_table_format(True)
    types = {1: 1, 2: 2, 3: 9}
    resources: dict[int, bytes] = {}
    for index, kind in types.items():
        segment = host.relative_segment(kind)
        if segment is None:
            raise ValueError("Missing Siemens communication table")
        resources[index] = segment.data
    identity = _identity(application)
    properties = (PropertyValue(4, 13, 0, identity, 5),)
    table_image = DownloadImage(
        segments=(),
        properties=properties,
        relative_segments=host.relative_segments,
        object_segments=resources,
        application_segments=tuple(
            ObjectMemorySegment(i, MemorySegment(0, d)) for i, d in resources.items()
        ),
    )
    table_prepared = PreparedDownload(
        table_image, {i: len(d) for i, d in resources.items()}
    )
    # The parameter memory from its lowest address on (the image may start lower).
    first = compiled.start - compiled.base
    used = compiled.data[first : compiled.used_length]
    used_mask = compiled.mask[first : compiled.used_length]
    relative = compiled.relative_offset
    if relative is None:
        memory = MemorySegment(compiled.start, used, used_mask)
        image = replace(
            table_image,
            segments=(memory,),
            object_segments={**resources, 4: memory.data},
            allocation_sizes={4: compiled.allocation_size},
        )
        absolute: dict[int, int] = {4: compiled.start}
    else:
        # Written relative to the application's segment: its image starts at the
        # segment's offset 0, the address ``relative``.
        lead = compiled.start - relative
        memory = MemorySegment(0, b"\xab" * lead + used, bytes(lead) + used_mask)
        image = replace(
            table_image,
            object_segments={**resources, 4: memory.data},
            application_segments=(
                *table_image.application_segments,
                ObjectMemorySegment(4, memory),
            ),
            allocation_sizes={4: compiled.allocation_size},
        )
        absolute = {}
    version = description.plugin_version
    tables_first = version is not None and version <= _TABLES_FIRST_UNTIL

    def materialize(controls: Sequence[object]) -> list[object]:
        table_controls: list[object] = []
        allocation_counts: dict[int, int] = {}
        for control in controls:
            if isinstance(control, LdCtrlRelSegment) and control.lsm_idx in (1, 2):
                index = control.lsm_idx
                assert index is not None
                allocation_counts[index] = allocation_counts.get(index, 0) + 1
        if allocation_counts != {1: 2, 2: 2}:
            raise ImageError("Unexpected Siemens table allocation sequence")
        seen: set[int] = set()
        for control in controls:
            if isinstance(control, LdCtrlRestart):
                continue
            if isinstance(control, LdCtrlRelSegment) and control.lsm_idx in (1, 2):
                assert control.lsm_idx is not None
                if control.lsm_idx not in seen:
                    seen.add(control.lsm_idx)
                    continue
            table_controls.append(control)
        table_controls = table_prepared.materialize(table_controls, DownloadScope.FULL)
        if any(
            isinstance(c, LdCtrlRelSegment) and c.lsm_idx == 4 for c in table_controls
        ):
            raise ImageError(
                "Unexpected application allocation in Siemens table procedure"
            )
        hardware = PropertyAlternatives(
            tuple(
                LdCtrlCompareProp(obj_idx=0, prop_id=78, inline_data=value)
                for value in description.compatible_hardware
            )
        )

        def unload() -> list[object]:
            return [LdCtrlDelay(milli_seconds=2000), LdCtrlUnload(lsm_idx=4)]

        def allocate() -> list[object]:
            return [
                LdCtrlLoad(lsm_idx=4),
                LdCtrlRelSegment(
                    lsm_idx=4, size=compiled.allocation_size, mode=0, fill=0
                ),
            ]

        writes: list[object]
        if relative is None:
            writes = [
                LdCtrlWriteMem(
                    address=address + offset,
                    size=len(data[offset : offset + _ABSOLUTE_CHUNK]),
                    inline_data=data[offset : offset + _ABSOLUTE_CHUNK],
                    verify=True,
                )
                for address, data in memory.masked_runs()
                for offset in range(0, len(data), _ABSOLUTE_CHUNK)
            ]
        else:
            writes = [
                LdCtrlWriteRelMem(
                    obj_idx=4,
                    offset=address + offset,
                    size=len(data[offset : offset + _RELATIVE_CHUNK]),
                    inline_data=data[offset : offset + _RELATIVE_CHUNK],
                    verify=True,
                )
                for address, data in memory.masked_runs()
                for offset in range(0, len(data), _RELATIVE_CHUNK)
            ]
        checks = [*_application_checks(description, identity), hardware]
        segment = [
            LdCtrlLoadImageProp(obj_idx=4, prop_id=7),
            LdCtrlLoadImageProp(obj_idx=4, prop_id=27),
        ]
        restart: list[object] = [
            LdCtrlDelay(milli_seconds=2000),
            LdCtrlRestart(),
            LdCtrlDelay(milli_seconds=12000),
            FinalPropertyRead(LdCtrlLoadImageProp(obj_idx=4, prop_id=27), 8),
        ]
        if tables_first:
            return [
                *checks,
                # The current function table, of a loaded application.
                ConditionalRead(
                    (LdCtrlCompareProp(obj_idx=4, prop_id=5, inline_data=_LOADED),),
                    LdCtrlLoadImageProp(obj_idx=6, prop_id=203),
                ),
                *segment,
                *table_controls,
                *unload(),
                *(
                    LdCtrlCompareProp(obj_idx=index, prop_id=5, inline_data=_LOADED)
                    for index in (1, 2, 3)
                ),
                *allocate(),
                *writes,
                LdCtrlLoadCompleted(lsm_idx=4),
                LdCtrlDelay(milli_seconds=4000),
                LdCtrlWriteProp(
                    obj_idx=6, prop_id=205, inline_data=b"\x00", verify=True
                ),
                *restart,
            ]
        loading = ConditionalWrite(
            (
                LdCtrlCompareProp(obj_idx=4, prop_id=5, inline_data=_LOADED),
                LdCtrlCompareProp(obj_idx=4, prop_id=6, inline_data=_LOADED),
            ),
            LdCtrlWriteProp(obj_idx=6, prop_id=205, inline_data=b"\x5a", verify=True),
        )
        return [
            *checks,
            *segment,
            loading,
            *unload(),
            *allocate(),
            *writes,
            *table_controls,
            *unload(),
            *allocate(),
            LdCtrlLoadCompleted(lsm_idx=4),
            LdCtrlDelay(milli_seconds=4000),
            LdCtrlWriteProp(obj_idx=6, prop_id=205, inline_data=b"\x10", verify=True),
            *restart,
        ]

    return PreparedDownload(
        image,
        {i: len(d) for i, d in image.object_segments.items()},
        programming_allowed=True,
        programming_reason="",
        absolute_resources=absolute,
        procedure_builder=materialize,
    )
