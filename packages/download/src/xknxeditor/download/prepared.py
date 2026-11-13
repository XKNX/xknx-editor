"""Complete images with explicit allocation and property controls."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from types import MappingProxyType

from xknxeditor.namespaces.intermediate.ld_ctrl_load_completed_t import (
    LdCtrlLoadCompleted,
)
from xknxeditor.namespaces.intermediate.ld_ctrl_rel_segment_t import LdCtrlRelSegment
from xknxeditor.namespaces.intermediate.ld_ctrl_write_prop_t import LdCtrlWriteProp
from xknxeditor.namespaces.intermediate.ld_ctrl_write_rel_mem_t import LdCtrlWriteRelMem

from .errors import ImageError, UnsupportedProcedureError
from .image import DownloadImage
from .scope import DownloadScope


@dataclass(frozen=True)
class PreparedDownload:
    """A complete main/secondary image, ready for procedure materialization.

    Preflight is permitted even when programming is unavailable. No automatic
    scope or table-format substitution is permitted for a prepared image.
    Resource occurrences map repeated allocation/write indices to the ordered
    resources of the complete image.
    The domain address describes the target network; domain commissioning is
    separate from the application load procedure.
    """

    image: DownloadImage
    resource_sizes: Mapping[int, int]
    programming_allowed: bool = False
    programming_reason: str = "Programming has not been validated for this profile"
    association_wide: bool = True
    allowed_scopes: frozenset[DownloadScope] = frozenset({DownloadScope.FULL})
    resource_occurrences: Mapping[int, tuple[int, ...]] = field(
        default_factory=lambda: {}
    )
    domain_address: int | None = None
    completion_properties: frozenset[tuple[int, int]] = frozenset()

    def __post_init__(self) -> None:
        if self.domain_address is not None and (
            type(self.domain_address) is not int
            or not 0 <= self.domain_address <= 65535
        ):
            raise ImageError("invalid prepared domain address")
        object.__setattr__(
            self, "resource_sizes", MappingProxyType(dict(self.resource_sizes))
        )
        object.__setattr__(
            self,
            "resource_occurrences",
            MappingProxyType(
                {k: tuple(v) for k, v in self.resource_occurrences.items()}
            ),
        )
        targets = [i for values in self.resource_occurrences.values() for i in values]
        if (
            len(targets) != len(set(targets))
            or any(i not in self.resource_sizes for i in targets)
            or any(
                source not in self.resource_sizes or not values
                for source, values in self.resource_occurrences.items()
            )
        ):
            raise ImageError("invalid prepared resource occurrences")
        for index, size in self.resource_sizes.items():
            data = self.image.object_segments.get(index)
            if size <= 0 or data is None or len(data) != size:
                raise ImageError("prepared resource size does not match its image")
        covered: set[int] = set()
        for segment in self.image.application_segments:
            index = segment.object_index
            if index in covered or segment.memory.address != 0:
                raise ImageError("overlapping or displaced prepared resource")
            if self.image.object_segments.get(index) != segment.memory.data:
                raise ImageError("contradictory prepared resource data")
            covered.add(index)
        if covered != set(self.resource_sizes):
            raise ImageError("incomplete prepared application resources")
        properties = [(p.object_index, p.property_id) for p in self.image.properties]
        if len(properties) != len(set(properties)):
            raise ImageError("duplicate prepared property")
        if not self.completion_properties <= set(properties):
            raise ImageError("missing prepared completion property")
        if any(
            not p.element_size or len(p.data) % p.element_size
            for p in self.image.properties
        ):
            raise ImageError("prepared property has an invalid element size")

    def check_scope(self, scope: DownloadScope, *, writing: bool = False) -> None:
        if scope not in self.allowed_scopes:
            raise UnsupportedProcedureError(
                "prepared image requires a full download scope"
            )
        if writing and not self.programming_allowed:
            raise UnsupportedProcedureError(self.programming_reason)

    def materialize(
        self, controls: Sequence[object], scope: DownloadScope
    ) -> list[object]:
        self.check_scope(scope)
        allocated: set[int] = set()
        written: set[int] = set()
        props = {(p.object_index, p.property_id): p for p in self.image.properties}
        seen_props: set[tuple[int | None, int]] = set()
        occurrences: dict[tuple[type, int], int] = {}
        result: list[object] = []
        for control in controls:
            if isinstance(control, (LdCtrlRelSegment, LdCtrlWriteRelMem)):
                source = (
                    control.lsm_idx
                    if isinstance(control, LdCtrlRelSegment)
                    else control.obj_idx
                )
                if source in self.resource_occurrences:
                    assert source is not None
                    key = (type(control), source)
                    occurrence = occurrences.get(key, 0)
                    targets = self.resource_occurrences[source]
                    if occurrence >= len(targets):
                        raise ImageError("unexpected prepared resource occurrence")
                    occurrences[key] = occurrence + 1
                    control = (
                        replace(control, lsm_idx=targets[occurrence])
                        if isinstance(control, LdCtrlRelSegment)
                        else replace(control, obj_idx=targets[occurrence])
                    )
            if (
                isinstance(control, LdCtrlRelSegment)
                and control.lsm_idx in self.resource_sizes
            ):
                index = control.lsm_idx
                assert index is not None
                if index in allocated:
                    raise ImageError("duplicate prepared allocation")
                allocated.add(index)
                control = replace(control, size=self.resource_sizes[index])
            elif (
                isinstance(control, LdCtrlWriteRelMem)
                and control.obj_idx in self.resource_sizes
            ):
                index = control.obj_idx
                assert index is not None
                if index in written or control.offset != 0 or control.inline_data:
                    raise ImageError("unsupported prepared resource write")
                written.add(index)
                control = replace(control, size=self.resource_sizes[index], verify=True)
            elif isinstance(control, LdCtrlWriteProp):
                key = (control.obj_idx, control.prop_id)
                if key in props:
                    if key in seen_props:
                        raise ImageError("duplicate prepared property write")
                    seen_props.add(key)
                    control = replace(
                        control,
                        start_element=1,
                        count=len(props[key].data) // (props[key].element_size or 1),
                        inline_data=props[key].data,
                        verify=True,
                    )
            result.append(control)
        if allocated != set(self.resource_sizes) or written != set(self.resource_sizes):
            raise ImageError("load procedure does not cover every prepared resource")
        if any(
            occurrences.get((kind, source), 0) != len(targets)
            for source, targets in self.resource_occurrences.items()
            for kind in (LdCtrlRelSegment, LdCtrlWriteRelMem)
        ):
            raise ImageError("missing prepared resource occurrence")
        position = next(
            (i for i, c in enumerate(result) if isinstance(c, LdCtrlLoadCompleted)),
            None,
        )
        if position is None:
            raise ImageError("load procedure has no completion boundary")
        last_completion = max(
            i for i, c in enumerate(result) if isinstance(c, LdCtrlLoadCompleted)
        )
        if not self.completion_properties <= seen_props or any(
            isinstance(c, LdCtrlWriteProp)
            and (c.obj_idx, c.prop_id) in self.completion_properties
            for c in result[:last_completion]
        ):
            raise ImageError("load procedure omits or misplaces a completion property")
        for c in result[position:]:
            if (
                isinstance(c, LdCtrlWriteProp)
                and (c.obj_idx, c.prop_id) in self.completion_properties
            ):
                continue
            if isinstance(c, (LdCtrlWriteRelMem, LdCtrlWriteProp, LdCtrlRelSegment)):
                raise ImageError("prepared data is written after load completion")
        extra = [
            LdCtrlWriteProp(
                obj_idx=p.object_index,
                prop_id=p.property_id,
                start_element=1,
                count=len(p.data) // (p.element_size or 1),
                inline_data=p.data,
                verify=True,
            )
            for key, p in props.items()
            if key not in seen_props
        ]
        return [*result[:position], *extra, *result[position:]]
