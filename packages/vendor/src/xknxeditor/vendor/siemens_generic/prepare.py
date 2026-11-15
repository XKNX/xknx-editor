"""Prepare parameter memory and the surrounding table load transaction."""

from collections.abc import Mapping, Sequence
from dataclasses import replace

from xknxeditor.download.conditions import (
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
from xknxeditor.prod import Application

from .description import Description
from .image import CompiledImage


def validate_profile(application: Application, description: Description) -> None:
    """Require compatibility metadata and the implemented fixed-memory layout."""
    program = application.program
    if program.mask_version != "MV-07B0":
        raise ValueError("Unsupported Siemens mask version")
    if not description.hardware_types or any(
        len(v) != 6 for v in description.hardware_types
    ):
        raise ValueError("Missing Siemens hardware compatibility data")
    extension = program.static.extension
    if extension is not None and any(
        token in b.ref_id.lower()
        for b in extension.baggage
        for token in ("functab_", "reftab_")
    ):
        raise ValueError("Additional Siemens function tables are not implemented")


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
    identity = bytes.fromhex(
        application.id[2:6] + application.id[9:13] + application.id[14:16]
    )
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
    length = compiled.used_length
    memory = MemorySegment(
        compiled.base, compiled.data[:length], compiled.mask[:length]
    )
    image = replace(
        table_image,
        segments=(memory,),
        object_segments={**resources, 4: memory.data},
        allocation_sizes={4: compiled.allocation_size},
    )

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
        app_checks: list[LdCtrlCompareProp] = []
        for pattern in description.application_ids:
            if len(pattern) != 10:
                raise ImageError("Invalid Siemens application compatibility pattern")
            prefix = pattern.split("*", 1)[0]
            if len(prefix) % 2:
                raise ImageError(
                    "Unsupported Siemens application compatibility pattern"
                )
            app_checks.append(
                LdCtrlCompareProp(
                    obj_idx=4,
                    prop_id=13,
                    inline_data=bytes.fromhex(prefix).ljust(5, b"\0"),
                    mask=(b"\xff" * (len(prefix) // 2)).ljust(5, b"\0"),
                )
            )
        hardware = PropertyAlternatives(
            tuple(
                LdCtrlCompareProp(obj_idx=0, prop_id=78, inline_data=value)
                for value in description.hardware_types
            )
        )
        loading = ConditionalWrite(
            (
                LdCtrlCompareProp(obj_idx=4, prop_id=5, inline_data=b"\x01"),
                LdCtrlCompareProp(obj_idx=4, prop_id=6, inline_data=b"\x01"),
            ),
            LdCtrlWriteProp(obj_idx=6, prop_id=205, inline_data=b"\x5a", verify=True),
        )

        def allocate() -> list[object]:
            return [
                LdCtrlDelay(milli_seconds=2000),
                LdCtrlUnload(lsm_idx=4),
                LdCtrlLoad(lsm_idx=4),
                LdCtrlRelSegment(
                    lsm_idx=4, size=compiled.allocation_size, mode=0, fill=0
                ),
            ]

        writes: list[object] = [
            LdCtrlWriteMem(
                address=address + offset,
                size=len(data[offset : offset + 10]),
                inline_data=data[offset : offset + 10],
                verify=True,
            )
            for address, data in memory.masked_runs()
            for offset in range(0, len(data), 10)
        ]
        return [
            *([PropertyAlternatives(tuple(app_checks))] if app_checks else []),
            hardware,
            LdCtrlLoadImageProp(obj_idx=4, prop_id=7),
            LdCtrlLoadImageProp(obj_idx=4, prop_id=27),
            loading,
            *allocate(),
            *writes,
            *table_controls,
            *allocate(),
            LdCtrlLoadCompleted(lsm_idx=4),
            LdCtrlDelay(milli_seconds=4000),
            LdCtrlWriteProp(obj_idx=6, prop_id=205, inline_data=b"\x10", verify=True),
            LdCtrlDelay(milli_seconds=2000),
            LdCtrlRestart(),
            LdCtrlDelay(milli_seconds=12000),
            FinalPropertyRead(LdCtrlLoadImageProp(obj_idx=4, prop_id=27), 8),
        ]

    return PreparedDownload(
        image,
        {i: len(d) for i, d in image.object_segments.items()},
        programming_allowed=True,
        programming_reason="",
        absolute_resources={4: compiled.base},
        procedure_builder=materialize,
    )
