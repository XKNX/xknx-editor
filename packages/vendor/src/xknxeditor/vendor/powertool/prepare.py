"""Validate the product identity and prepare a complete offline load image."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from types import MappingProxyType
from typing import Literal

from xknxeditor.download.image import (
    DownloadImage,
    MemorySegment,
    ObjectMemorySegment,
    PropertyValue,
    RelativeSegment,
)
from xknxeditor.download.prepared import PreparedDownload
from xknxeditor.prod import Application

from .codec import Association, CompiledPowerToolImage, HostProfile, compile_image
from .model import PowerToolConfig, Variant


def is_powertool_application(application: Application | None) -> bool:
    if application is None:
        return False
    return str(getattr(application, "id", "")).upper().startswith("M-0007_A-6108-")


def product_parameters(application: Application) -> dict[str, str]:
    from xknxeditor.prod.parser_v2.application_indexer import ApplicationIndexer

    return {
        p.name: str(p.value)
        for p in ApplicationIndexer(application.program).parameters.values()
    }


def profile_for(application: Application, secondary: Application | None) -> Variant:
    program = application.program
    identifier = application.id.upper().split("-")
    version = identifier[-2] if len(identifier) >= 5 else ""
    variant: Literal["01", "02"] = "01" if version == "21" else "02"
    expected = "31" if variant == "01" else "32"
    if not is_powertool_application(application) or version not in ("21", "22"):
        raise ValueError("unsupported 6108 application")
    if str(program.mask_version).upper() not in ("MV-07B0", "07B0"):
        raise ValueError("unsupported 6108 mask")
    if secondary is None or not secondary.id.upper().startswith(
        f"M-0007_A-6108-{expected}-"
    ):
        raise ValueError("missing or mismatched secondary application")
    if (
        application.manufacturer_id.upper() != "M-0007"
        or secondary.manufacturer_id.upper() != "M-0007"
        or program.application_number != 0x6108
        or secondary.program.application_number != 0x6108
        or program.application_version != int(version, 16)
        or secondary.program.application_version != int(expected, 16)
        or str(secondary.program.mask_version).upper() not in ("MV-07B0", "07B0")
    ):
        raise ValueError("inconsistent 6108 program identity")
    parameters = product_parameters(application)
    if (
        parameters.get("PTDatabaseCode") != "3"
        or parameters.get("ProductType")
        != ("TasterBA1f" if variant == "01" else "TasterBA2f")
        or parameters.get("BAUBVersion") != "0.1.0"
    ):
        raise ValueError("unsupported 6108 database profile")
    return variant


def prepare_image(
    compiled: CompiledPowerToolImage,
    *,
    short_associations: bool = False,
    secondary_application: Application | None = None,
    application: Application | None = None,
) -> PreparedDownload:
    occurrences: dict[int, tuple[int, ...]] = {}
    if secondary_application is not None:
        code = secondary_application.program.static.code
        segments = code.relative_segment if code else []
        if len(segments) != 1 or segments[0].load_state_machine not in (4, 5):
            raise ValueError("Unverified PowerTool secondary resource layout")
        if segments[0].load_state_machine == 4:
            occurrences[4] = (4, 5)
    by_object = {r.object_index: r.data for r in compiled.resources}
    allocation_sizes: dict[int, int] = {}
    if application is not None:
        table = application.program.static.com_object_table
        highest = (
            max((obj.number for obj in table.com_object), default=0) if table else 0
        )
        allocation_sizes[3] = max(len(by_object[3]), 2 + 2 * highest)
    image = DownloadImage(
        segments=(),
        properties=tuple(
            PropertyValue(
                p.object_index,
                p.pid,
                0,
                p.data,
                element_size=1 if (p.object_index, p.pid) == (1, 53) else len(p.data),
            )
            for p in compiled.properties
        ),
        application_segments=tuple(
            ObjectMemorySegment(r.object_index, MemorySegment(0, r.data, r.mask))
            for r in compiled.resources
        ),
        relative_segments=tuple(
            RelativeSegment({1: 1, 2: 2, 3: 9}[r.object_index], r.data, r.mask)
            for r in compiled.resources
            if r.object_index in (1, 2, 3)
        ),
        object_segments=MappingProxyType(by_object),
        allocation_sizes=allocation_sizes,
    )
    return PreparedDownload(
        image,
        {i: len(data) for i, data in by_object.items()},
        programming_allowed=True,
        programming_reason="",
        association_wide=not short_associations,
        resource_occurrences=occurrences,
        domain_address=int.from_bytes(compiled.pid[4:6], "big")
        if int.from_bytes(compiled.pid[6:8], "big") == 0x17B0
        else None,
        completion_properties=frozenset({(0, 73)})
        if int.from_bytes(compiled.pid[6:8], "big") == 0x17B0
        else frozenset(),
    )


def prepare(
    config: PowerToolConfig,
    application: Application,
    secondary: Application | None,
    host: HostProfile,
    associations: Sequence[Association] = (),
    flags: Mapping[str, int] | None = None,
) -> PreparedDownload:
    if config.variant != profile_for(application, secondary):
        raise ValueError("configuration does not match the product")
    return prepare_image(
        compile_image(config, host, associations, flags),
        short_associations=host.short_associations,
        secondary_application=secondary,
        application=application,
    )
