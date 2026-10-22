from __future__ import annotations

from xknxeditor.namespaces.intermediate import ComObjectRefRef
from xknxeditor.namespaces.intermediate.com_object_ref_t import (
    ComObjectRef as IrComObjectRef,
)
from xknxeditor.namespaces.intermediate.com_object_t import ComObject as IrComObject
from xknxeditor.namespaces.intermediate.enable_t import Enable
from xknxeditor.namespaces.intermediate.module_def_static_t_com_objects_com_object import (
    ModuleDefStaticComObjectsComObject,
)

from .._name import apply_text_args, fill_name
from ..context import EvalContext
from ..ui import UiNode
from ..ui.com_object import UiComObject
from .base import DynamicNode


def _flag(ref_val: Enable | None, base_val: Enable | None) -> bool:
    v = ref_val if ref_val is not None else base_val
    return v is Enable.ENABLED


def dpt_codes(ref_dpts: list[str], base_dpts: list[str]) -> tuple[str, ...]:
    raw = ref_dpts or base_dpts
    codes: list[str] = []
    for token in raw:
        parts = token.split("-")
        if len(parts) < 2 or parts[0] not in {"DPT", "DPST"}:
            continue
        try:
            major = int(parts[1])
            # A main-only "DPT-N" has no sub-number and stays distinct from "DPST-N-0";
            # keep that distinction so the derived token is faithful (bare "N" vs "N.0").
            minor = int(parts[2]) if len(parts) >= 3 else None
        except ValueError:
            continue
        codes.append(f"{major}.{minor}" if minor is not None else str(major))
    return tuple(codes)


class ComObjectRefRefNode(DynamicNode):
    """A communication object; wired at build time, name resolved on eval."""

    __slots__ = (
        "_base_number_ref",
        "_communication",
        "_dpt_codes",
        "_function_text",
        "_local_ref_id",
        "_name_template",
        "_number",
        "_object_size",
        "_priority",
        "_read",
        "_read_locked",
        "_read_on_init",
        "_read_on_init_locked",
        "_text_param_ref_id",
        "_transmit",
        "_transmit_locked",
        "_update",
        "_update_locked",
        "_write",
        "_write_locked",
    )

    def __init__(
        self,
        elem: ComObjectRefRef,
        cor: IrComObjectRef | None,
        co: IrComObject | None,
    ) -> None:
        self._local_ref_id = elem.ref_id
        self._name_template: str = (
            (cor.text if cor else None)
            or (co.text if co else None)
            or (cor.name if cor else None)
            or (co.name if co else None)
            or ""
        )
        # The object function (e.g. "Switch", "Status") — distinguishes objects that share the
        # same channel Text (e.g. "G: Schlafzimmer"). Ref overrides the base ComObject.
        self._function_text: str = (
            (cor.function_text if cor else None)
            or (co.function_text if co else None)
            or ""
        )
        self._text_param_ref_id: str | None = cor.text_parameter_ref_id if cor else None
        self._number: int = co.number if co else 0
        self._base_number_ref: str | None = (
            co.base_number
            if isinstance(co, ModuleDefStaticComObjectsComObject)
            else None
        )
        self._dpt_codes: tuple[str, ...] = dpt_codes(
            cor.datapoint_type if cor else [],
            co.datapoint_type if co else [],
        )
        # Size and priority: the ref overrides the base, else the base value.
        size = (cor.object_size if cor else None) or (co.object_size if co else None)
        self._object_size: str = size.value if size is not None else ""
        priority = (cor.priority if cor else None) or (co.priority if co else None)
        self._priority: str = priority.value if priority is not None else ""
        self._communication = _flag(
            cor.communication_flag if cor else None,
            co.communication_flag if co else None,
        )
        self._read = _flag(cor.read_flag if cor else None, co.read_flag if co else None)
        self._write = _flag(
            cor.write_flag if cor else None, co.write_flag if co else None
        )
        self._transmit = _flag(
            cor.transmit_flag if cor else None, co.transmit_flag if co else None
        )
        self._update = _flag(
            cor.update_flag if cor else None, co.update_flag if co else None
        )
        self._read_on_init = _flag(
            cor.read_on_init_flag if cor else None,
            co.read_on_init_flag if co else None,
        )
        # Locked flags come from the base ComObject; a ref cannot change them.
        self._read_locked: bool = bool(co.read_flag_locked) if co else False
        self._write_locked: bool = bool(co.write_flag_locked) if co else False
        self._transmit_locked: bool = bool(co.transmit_flag_locked) if co else False
        self._update_locked: bool = bool(co.update_flag_locked) if co else False
        self._read_on_init_locked: bool = (
            bool(co.read_on_init_flag_locked) if co else False
        )

    def eval(self, ctx: EvalContext) -> list[UiNode]:
        ctx.mark_active_com_object(self._local_ref_id)
        qualified_ref_id = ctx.qualify(self._local_ref_id)
        if ctx.capture is not None:
            # Snapshot the gate chain (the Choose/Repeat parameters this emission sits under) for the
            # chain-AND activeness test in DynamicUI.active_parameter_driven_com_object_ref_ids.
            ctx.capture.record_object(qualified_ref_id)
        name_value = (
            ctx.get(self._text_param_ref_id) if self._text_param_ref_id else None
        )
        name = fill_name(
            apply_text_args(self._name_template, ctx.get_arg_defaults()),
            name_value or "",
        )
        function_text = fill_name(
            apply_text_args(self._function_text, ctx.get_arg_defaults()),
            name_value or "",
        )
        ov = ctx.get_com_obj_instance_ref(qualified_ref_id)
        base = (
            ctx.get_arg_value(self._base_number_ref)
            if self._base_number_ref is not None
            else 0
        )
        return [
            UiComObject(
                ref_id=qualified_ref_id,
                name=name,
                function_text=function_text,
                number=self._number + base,
                dpt_codes=self._dpt_codes,
                object_size=self._object_size,
                priority=self._priority,
                communication=_flag(
                    ov.communication_flag if ov else None,
                    Enable.ENABLED if self._communication else Enable.DISABLED,
                ),
                read=_flag(
                    ov.read_flag if ov else None,
                    Enable.ENABLED if self._read else Enable.DISABLED,
                ),
                write=_flag(
                    ov.write_flag if ov else None,
                    Enable.ENABLED if self._write else Enable.DISABLED,
                ),
                transmit=_flag(
                    ov.transmit_flag if ov else None,
                    Enable.ENABLED if self._transmit else Enable.DISABLED,
                ),
                update=_flag(
                    ov.update_flag if ov else None,
                    Enable.ENABLED if self._update else Enable.DISABLED,
                ),
                read_on_init=_flag(
                    ov.read_on_init_flag if ov else None,
                    Enable.ENABLED if self._read_on_init else Enable.DISABLED,
                ),
                read_locked=self._read_locked,
                write_locked=self._write_locked,
                transmit_locked=self._transmit_locked,
                update_locked=self._update_locked,
                read_on_init_locked=self._read_on_init_locked,
            )
        ]
