from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING

from editor_gui.dpt import DPT
from xknxeditor.prod import Application

if TYPE_CHECKING:
    from xknxeditor.namespaces.intermediate.com_object_instance_ref_t import (
        ComObjectInstanceRef,
    )
    from xknxeditor.namespaces.intermediate.module_instance_t import ModuleInstance
    from xknxeditor.namespaces.intermediate.parameter_instance_ref_t import (
        ParameterInstanceRef,
    )
    from xknxeditor.prod.parser_v2.calculation import ChangeSet
    from xknxeditor.prod.parser_v2.dynamic import DynamicUI
    from xknxeditor.prod.parser_v2.ui import UiComObject, UiNode


class PinDir(Enum):
    INPUT = "input"
    OUTPUT = "output"


@dataclass
class ComObjectFlags:
    communication: bool = True
    read: bool = False
    write: bool = False
    transmit: bool = False
    update: bool = False
    read_on_init: bool = False
    read_locked: bool = False
    write_locked: bool = False
    transmit_locked: bool = False
    update_locked: bool = False
    read_on_init_locked: bool = False

    @classmethod
    def default_input(cls) -> ComObjectFlags:
        return cls(communication=True, write=True)

    @classmethod
    def default_output(cls) -> ComObjectFlags:
        return cls(communication=True, read=True, transmit=True)


def default_flags_for(direction: PinDir) -> ComObjectFlags:
    if direction == PinDir.INPUT:
        return ComObjectFlags.default_input()
    return ComObjectFlags.default_output()


@dataclass
class ComObject:
    id: str
    name: str
    dpt: DPT
    flags: ComObjectFlags
    function_text: str = (
        ""  # object function (e.g. "Switch"); distinguishes same-channel objects
    )
    number: int = 0
    supported_dpts: list[DPT] = field(default_factory=list[DPT])
    object_size: str = ""  # resolved ComObjectSize, e.g. "1 Bit" / "1 Byte"
    priority: str = ""  # resolved ComObjectPriority, e.g. "Low"
    db_id: int | None = None
    # Per-instance text overrides (ETS ``@Text``/``@FunctionText``/``@Description``). ``None`` inherits
    # the application default; ``@Text``/``@FunctionText`` are preferred over the app default for
    # ``name``/``function_text``, ``@Description`` is a free-text note shown as a tooltip. Populated
    # from the stored ComObject row (see project/service.py).
    text_override: str | None = None
    function_text_override: str | None = None
    description: str = ""


def com_object_display_name(co: ComObject) -> str:
    """The object's name, plus its function when that adds information.

    Many devices name every com-object in a channel the same (e.g. a heating actuator's
    'G: Schlafzimmer' repeated 20 times); the object function ('Heizen', 'Status', ...) is what
    tells channel-siblings apart, so append it when it differs from the name."""
    function = co.function_text
    return f"{co.name}  ·  {function}" if function and function != co.name else co.name


def address_order(individual_address: str | None) -> tuple[int, int, int]:
    """Sort key for numeric individual-address order (1.0.5 before 1.0.49); a missing or invalid
    address sorts after all valid ones."""
    try:
        area, line, number = (int(p) for p in (individual_address or "").split("."))
    except ValueError:
        return (1 << 16, 0, 0)
    return (area, line, number)


_co_id_counter = 0


def _next_co_id() -> str:
    global _co_id_counter
    _co_id_counter += 1
    return f"co_{_co_id_counter}"


FLAG_LABELS = [
    ("communication", "C", "Communication"),
    ("read", "R", "Read"),
    ("write", "W", "Write"),
    ("transmit", "T", "Transmit"),
    ("update", "U", "Update"),
    ("read_on_init", "I", "Read on Init"),
]


def flag_diff_letters(
    flags: ComObjectFlags, direction: PinDir
) -> list[tuple[str, bool]]:
    default = default_flags_for(direction)
    diffs: list[tuple[str, bool]] = []
    for attr, letter, _ in FLAG_LABELS:
        if getattr(flags, attr) != getattr(default, attr):
            diffs.append((letter, getattr(flags, attr)))
    return diffs


def com_object_has_input(co: ComObject) -> bool:
    return co.flags.write or co.flags.update


def com_object_has_output(co: ComObject) -> bool:
    return co.flags.transmit or co.flags.read


@dataclass
class PinRow:
    left: ComObject | None = None
    right: ComObject | None = None


def generate_rows(com_objects: list[ComObject]) -> list[PinRow]:
    rows: list[PinRow] = []
    pending_input: ComObject | None = None

    for co in com_objects:
        has_in = com_object_has_input(co)
        has_out = com_object_has_output(co)
        if has_in and has_out:
            if pending_input is not None:
                rows.append(PinRow(left=pending_input))
                pending_input = None
            rows.append(PinRow(left=co, right=co))
        elif has_in:
            if pending_input is not None:
                rows.append(PinRow(left=pending_input))
            pending_input = co
        elif has_out:
            if pending_input is not None:
                rows.append(PinRow(left=pending_input, right=co))
                pending_input = None
            else:
                rows.append(PinRow(right=co))
    if pending_input is not None:
        rows.append(PinRow(left=pending_input))
    return rows


def _collect_ui_com_objects(
    nodes: list[UiNode] | tuple[UiNode, ...],
) -> list[UiComObject]:
    from xknxeditor.prod.parser_v2.ui import UiComObject as _UiComObject
    from xknxeditor.prod.parser_v2.ui import UiParameterBlock as _UiParameterBlock
    from xknxeditor.prod.parser_v2.ui import UiTab as _UiTab

    result: list[UiComObject] = []
    for node in nodes:
        if isinstance(node, _UiComObject):
            result.append(node)
        elif isinstance(node, (_UiTab, _UiParameterBlock)):
            result.extend(_collect_ui_com_objects(node.children))
    return result


@dataclass(frozen=True)
class UnloadedDevice:
    """A project device whose application could not be loaded (product data missing or broken)."""

    node_id: int
    name: str
    product_name: str
    individual_address: str
    program_ref: str | None

    @property
    def display_name(self) -> str:
        return self.name or self.product_name


@dataclass
class Device:
    node_id: int
    name: str
    app: Application
    individual_address: str
    description: str = ""
    product_name: str = ""
    com_objects: list[ComObject] = field(default_factory=list[ComObject])
    parameter_instance_refs: list[ParameterInstanceRef] = field(
        default_factory=list, repr=False, compare=False
    )
    module_instances: list[ModuleInstance] = field(
        default_factory=list, repr=False, compare=False
    )
    com_object_instance_refs: list[ComObjectInstanceRef] = field(
        default_factory=list, repr=False, compare=False
    )
    _dynamic_ui: DynamicUI | None = field(
        default=None, repr=False, compare=False, init=False
    )
    _cached_visible_cos: list[ComObject] | None = field(
        default=None, repr=False, compare=False, init=False
    )
    _cached_rows: list[PinRow] | None = field(
        default=None, repr=False, compare=False, init=False
    )
    # True once a parameter/com-object was edited in this session: the live DynamicUI then holds
    # state not reflected in parameter_instance_refs, so it must NOT be released (a rebuild would
    # lose the edit until the next full device-view rebuild).
    _dynamic_ui_dirty: bool = field(
        default=False, repr=False, compare=False, init=False
    )
    # Changed in place since it was built, so it no longer mirrors the data it was built from.
    edited_in_place: bool = field(default=False, repr=False, compare=False, init=False)
    # UI frozen while a script changes this device's parameters on the script worker.
    _script_ui: list[UiNode] | None = field(
        default=None, repr=False, compare=False, init=False
    )
    # Message of the last rejected edit per parameter, shown until it is edited again.
    param_errors: dict[str, str] = field(
        default_factory=dict[str, str], repr=False, compare=False, init=False
    )
    # The rejected input per parameter, kept in its field and submitted again after later edits.
    param_inputs: dict[str, str] = field(
        default_factory=dict[str, str], repr=False, compare=False, init=False
    )

    @property
    def display_name(self) -> str:
        """The device's name, else its product's name, else its application's name."""
        return self.name or self.product_name or self.app.name

    def __post_init__(self) -> None:
        self._ensure_dynamic_ui()
        if not self.com_objects:
            self.com_objects = self._create_com_objects_from_app()

    def _ensure_dynamic_ui(self) -> DynamicUI | None:
        """Build the (heavy) DynamicUI evaluator on demand; ``None`` if the app has no dynamic
        section. It is released for inactive devices to save memory (see :meth:`release_dynamic_ui`),
        so it may need rebuilding when a device is inspected or edited again."""
        if self._dynamic_ui is None and self.app.program.dynamic is not None:
            from xknxeditor.prod.parser_v2.dynamic import DynamicUI as _DynamicUI

            # Program and tree as one pair: an unused application may have been reloaded.
            program, tree_builder = self.app.resolved()
            self._dynamic_ui = _DynamicUI(
                program,
                parameter_instance_refs=self.parameter_instance_refs or None,
                module_instances=self.module_instances or None,
                com_object_instance_refs=self.com_object_instance_refs or None,
                tree_builder=tree_builder,
            )
        return self._dynamic_ui

    def release_dynamic_ui(self) -> None:
        """Drop the heavy DynamicUI evaluator to free memory, keeping the lightweight cached views.
        No-op for a device with unsaved in-session edits (its live state can't be rebuilt from the
        stored parameter refs). Rebuilt lazily on the next :meth:`_ensure_dynamic_ui`."""
        if not self._dynamic_ui_dirty:
            self._dynamic_ui = None

    def _create_com_objects_from_app(self) -> list[ComObject]:
        if self._dynamic_ui is None:
            return []
        from editor_gui.dpt import DPT_UNKNOWN, lookup_or_make_dpt

        ui_cos = _collect_ui_com_objects(self._dynamic_ui.ui())
        # An imported device carries the exact set of com objects it instantiated
        # (its group object tree). Constrain the visible set to those so channel-mode
        # products do not show the individual per-channel objects that the raw
        # parameter defaults would otherwise activate (matches genuine exports and the download).
        # A device configured from scratch has no saved instances -> keep the full
        # parameter-driven set.
        instantiated = self._dynamic_ui.instantiated_com_object_ref_ids()
        if instantiated:
            ui_cos = [co for co in ui_cos if co.ref_id in instantiated]
        result: list[ComObject] = []
        for ui_co in ui_cos:
            supported = [lookup_or_make_dpt(code) for code in ui_co.dpt_codes]
            seen: set[tuple[int, int, bool]] = set()
            unique_supported: list[DPT] = []
            for dpt in supported:
                key = (dpt.major, dpt.minor, dpt.has_subtype)
                if key not in seen:
                    seen.add(key)
                    unique_supported.append(dpt)
            primary = unique_supported[0] if unique_supported else DPT_UNKNOWN
            result.append(
                ComObject(
                    id=ui_co.ref_id,
                    name=ui_co.name,
                    function_text=ui_co.function_text,
                    dpt=primary,
                    number=ui_co.number,
                    flags=ComObjectFlags(
                        communication=ui_co.communication,
                        read=ui_co.read,
                        write=ui_co.write,
                        transmit=ui_co.transmit,
                        update=ui_co.update,
                        read_on_init=ui_co.read_on_init,
                        read_locked=ui_co.read_locked,
                        write_locked=ui_co.write_locked,
                        transmit_locked=ui_co.transmit_locked,
                        update_locked=ui_co.update_locked,
                        read_on_init_locked=ui_co.read_on_init_locked,
                    ),
                    supported_dpts=unique_supported,
                    object_size=ui_co.object_size,
                    priority=ui_co.priority,
                )
            )
        return result

    @property
    def rows(self) -> list[PinRow]:
        if self._cached_rows is None:
            self._cached_rows = generate_rows(self.get_visible_com_objects())
        return self._cached_rows

    @property
    def script_running(self) -> bool:
        return self._script_ui is not None

    def begin_script(self) -> DynamicUI | None:
        """Freeze what is rendered while a script changes parameters on another thread; the
        returned evaluator must only be used by that script until :meth:`end_script`."""
        dyn = self._ensure_dynamic_ui()
        if dyn is None:
            return None
        self._script_ui = dyn.ui()
        self.get_visible_com_objects()
        self._dynamic_ui_dirty = True
        self.edited_in_place = True
        return dyn

    def end_script(self) -> None:
        self._script_ui = None
        self._touched()

    def get_ui(self) -> list[UiNode]:
        if self._script_ui is not None:
            return self._script_ui
        dyn = self._ensure_dynamic_ui()
        if dyn is None:
            return []
        # Inactive channels are pruned centrally in DynamicUI.ui() (from the device's
        # instantiated group objects), so both display and encoding stay consistent.
        return dyn.ui()

    def get_visible_com_objects(self) -> list[ComObject]:
        if self._cached_visible_cos is not None:
            return self._cached_visible_cos
        dyn = self._ensure_dynamic_ui()
        if dyn is None:
            return list(self.com_objects)
        ui_cos = _collect_ui_com_objects(dyn.ui())
        ui_by_id = {co.ref_id: co for co in ui_cos}
        result: list[ComObject] = []
        for co in self.com_objects:
            ui_co = ui_by_id.get(co.id)
            if ui_co is None:
                continue
            # Prefer the per-instance ETS override (@Text/@FunctionText) over the app default.
            co.name = co.text_override or ui_co.name
            co.function_text = co.function_text_override or ui_co.function_text
            co.number = ui_co.number
            co.object_size = ui_co.object_size
            co.priority = ui_co.priority
            co.flags.communication = ui_co.communication
            co.flags.read = ui_co.read
            co.flags.write = ui_co.write
            co.flags.transmit = ui_co.transmit
            co.flags.update = ui_co.update
            co.flags.read_on_init = ui_co.read_on_init
            result.append(co)
        self._cached_visible_cos = result
        return result

    def active_parameter_driven_com_object_ref_ids(self) -> set[str]:
        """The parameter-driven active com-object set for the current parameter values (chain-AND:
        objects whose every gate is driven by an active parameter, plus unconditional objects — see
        :meth:`DynamicUI.active_parameter_driven_com_object_ref_ids`). Used for the ADD side of a
        reconcile. NOTE: for some applications the parser under-derives (e.g. an app whose tree yields
        an empty active set); callers must treat an empty result as "cannot derive" and not remove
        objects on its basis."""
        dyn = self._ensure_dynamic_ui()
        return (
            set() if dyn is None else dyn.active_parameter_driven_com_object_ref_ids()
        )

    def get_segment_base_addrs(self) -> dict[str, int]:
        dyn = self._ensure_dynamic_ui()
        if dyn is None:
            return {}
        return dyn.segment_base_addrs()

    def encode_to_memory(self) -> dict[str, bytes]:
        """Build per-segment byte images from the live parameter state."""
        dyn = self._ensure_dynamic_ui()
        if dyn is None:
            return {}
        return dyn.encode_to_memory()

    def get_memory_param_map(self) -> dict[str, dict[int, tuple[str, str]]]:
        """Per-segment offset-to-parameter map for the hex viewer."""
        dyn = self._ensure_dynamic_ui()
        if dyn is None:
            return {}
        return dyn.memory_param_map()

    def set_com_obj_instance_ref(self, ref_id: str, coir: ComObjectInstanceRef) -> None:
        dyn = self._ensure_dynamic_ui()
        if dyn is not None:
            dyn.set_com_obj_instance_ref(ref_id, coir)
            self._touched()

    def _touched(self) -> None:
        self._dynamic_ui_dirty = (
            True  # live edit not in the stored refs -> keep resident
        )
        self.edited_in_place = True
        self._cached_visible_cos = None
        self._cached_rows = None

    def set_param_value(self, ref_id: str, value: str) -> None:
        dyn = self._ensure_dynamic_ui()
        if dyn is not None:
            dyn.set_parameter_ref(ref_id, value)
            self._touched()

    def change_param_values(
        self,
        edits: Sequence[tuple[str, str]],
        *,
        validate: bool,
        skip_invalid: bool = False,
    ) -> ChangeSet:
        """Apply edits with their calculations (and validations); all-or-nothing unless
        ``skip_invalid``, which drops the edits that fail. All direct edits are applied before any
        calculation runs, so a calculation shared by several edits runs once (not once per edit)."""
        dyn = self._ensure_dynamic_ui()
        if dyn is None:
            return {}
        try:
            return dyn.edit_parameters(
                edits, validate=validate, skip_invalid=skip_invalid
            )
        finally:
            self._touched()

    def apply_param_values(self, values: Mapping[str, str | None]) -> None:
        """Set (``None``: clear) values as they are, without calculations."""
        dyn = self._ensure_dynamic_ui()
        if dyn is not None:
            dyn.apply_parameter_values(values)
            self._touched()

    def recalculate_params(self, ref_ids: Iterable[str]) -> ChangeSet:
        """Run the calculations depending on ``ref_ids``."""
        dyn = self._ensure_dynamic_ui()
        if dyn is None:
            return {}
        changes = dyn.recalculate(ref_ids)
        self._touched()
        return changes

    def get_param_value(self, ref_id: str) -> str | None:
        """Current value of a parameter ref in the live dynamic UI (for logging/diagnostics)."""
        dyn = self._ensure_dynamic_ui()
        return None if dyn is None else dyn.get_parameter_ref(ref_id)

    def get_module_instances(self) -> list[tuple[str, str]]:
        """Top-level module instances as ``(instance_id, ref_id)`` pairs."""
        dyn = self._ensure_dynamic_ui()
        if dyn is None:
            return []
        return dyn.get_module_instances()

    def find_com_object(self, co_id: str) -> ComObject | None:
        for co in self.com_objects:
            if co.id == co_id:
                return co
        return None

    @property
    def dynamic_ui(self) -> DynamicUI | None:
        """The live evaluator holding this device's current parameter state (built on demand)."""
        return self._ensure_dynamic_ui()
