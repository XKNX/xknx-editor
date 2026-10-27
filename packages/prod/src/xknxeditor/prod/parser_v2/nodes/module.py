from __future__ import annotations

from xknxeditor.namespaces.intermediate import ModuleArg
from xknxeditor.namespaces.intermediate.module_t_numeric_arg import ModuleNumericArg

from ..context import EvalContext
from ..ui import UiNode
from .base import DynamicNode


def _resolved_base(ref_id: str, args: dict[str, ModuleArg]) -> int:
    ref = args.get(ref_id)
    return (
        ref.value if isinstance(ref, ModuleNumericArg) and ref.value is not None else 0
    )


def _resolve_arguments(
    ctx: EvalContext, ref_id: str, arguments: dict[str, ModuleArg]
) -> dict[str, ModuleArg]:
    out = dict(arguments)
    for r_id, arg in arguments.items():
        if not isinstance(arg, ModuleNumericArg):
            continue
        base = _resolved_base(arg.base_value, out) if arg.base_value is not None else 0
        if arg.allocator_ref_id is not None:
            out[r_id] = ModuleNumericArg(
                ref_id=r_id,
                value=ctx.allocate(ref_id, arg.allocator_ref_id, r_id, base),
            )
        elif base:
            out[r_id] = ModuleNumericArg(ref_id=r_id, value=(arg.value or 0) + base)
    return out


class ModuleNode(DynamicNode):
    """Expands a module definition's subtree within its own instance scope."""

    def __init__(
        self,
        module_id: str,
        subtree: DynamicNode,
        ref_id: str,
        arguments: dict[str, ModuleArg] | None = None,
        param_ref_defaults: dict[str, str] | None = None,
        arg_defaults: dict[str, str] | None = None,
        arg_names: dict[str, str] | None = None,
        param_ref_bases: dict[str, str] | None = None,
    ) -> None:
        self._module_id = module_id
        self._ref_id = ref_id
        self._subtree = subtree
        self._arguments: dict[str, ModuleArg] = arguments or {}
        self._param_ref_defaults: dict[str, str] = param_ref_defaults or {}
        self._arg_defaults: dict[str, str] = arg_defaults or {}
        # arg ref_id -> arg name, so per-instance NUMERIC args (allocator-driven, e.g. ECG_NO/GRP_NO/
        # MD_NO = 1,2,3…) can be exposed as text args for "{{ECG_NO}}"-style label substitution.
        self._arg_names: dict[str, str] = arg_names or {}
        # ParameterRef -> the argument its Parameter takes its value from (BaseValue).
        self._param_ref_bases: dict[str, str] = param_ref_bases or {}

    def eval(self, ctx: EvalContext) -> list[UiNode]:
        args = _resolve_arguments(ctx, self._ref_id, self._arguments)
        # Text args = static text-arg defaults + the resolved numeric args by name. Without the
        # latter, "{{ECG_NO}}"/"{{GRP_NO}}" placeholders in tab/block/parameter labels stay literal
        # (or collapse to "G,"), so every DALI group/ECG looks identical.
        text_args = dict(self._arg_defaults)
        for r_id, arg in args.items():
            name = self._arg_names.get(r_id)
            if name and isinstance(arg, ModuleNumericArg) and arg.value is not None:
                text_args[name] = str(arg.value)
        mctx = ctx.module_ctx(
            self._module_id,
            args,
            param_ref_defaults=self._param_ref_defaults or None,
            arg_defaults=text_args or None,
            ref_id=self._ref_id,
            param_ref_bases=self._param_ref_bases or None,
        )
        return self._subtree.eval(mctx)
