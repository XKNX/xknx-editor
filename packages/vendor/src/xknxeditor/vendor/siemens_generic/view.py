"""Read-only view of a device's Generic Plug-in settings: pages, values and memory check.

The view walks the description's ``Dynamic`` tree the way the plug-in shows it: a ``choose``
activates every ``when`` whose test list (``"a;b;c"``) holds the value of its parameter ref, and
its ``default`` when none does; ``Assign`` elements in active branches set derived values, so the
walk repeats until those settle. Every parameter in an active branch becomes a row with its
name, symbolic value and text. Each row is then checked against the stored memory image of the
last download: the value is encoded with the description's own type and offset and compared
byte for byte. Nothing here writes anything.

Settings of extension modules that are not fitted (branches switched off by a "number of
submodules" parameter) can be shown too, marked as not fitted and not checked: the plug-in fills
their memory with values of its own. ``compare_views`` lines up two devices' views setting by
setting. "Percent" numbers use the 0..255 scale, with the raw value kept.
"""

from __future__ import annotations

import re
from contextlib import suppress
from dataclasses import dataclass, field
from enum import Enum

from .blob import PluginSettings
from .codec import byte_size, decode, encode
from .description import (
    INTEGER_KINDS,
    TIME_KINDS,
    TIME_UNIT_MS,
    Description,
    Node,
    NodeKind,
    Parameter,
    ParameterType,
    TypeKind,
    integer,
    locked_parameters,
)
from .presentation import ValueFormat, value_format

# How often the walk is repeated at most for Assign values to settle.
_MAX_PASSES = 128
# A choose on a parameter with such a name or text switches the settings of extension modules
# (e.g. the N 512/11's "parMaxSlaveNum", "Number of connected submodules").
# A time setting whose name gives its format ("On-delay in hh:mm:ss") is shown in it.
_CLOCK_FORMAT = re.compile(r"\b(hh:mm:ss|hh:mm|mm:ss)\b", re.IGNORECASE)
_MODULE_SWITCH = re.compile(
    r"slave|sub.?module|extension.?module|erweiterungsmodul|zusatzmodul", re.IGNORECASE
)


class MemoryCheck(Enum):
    MATCH = "match"
    MISMATCH = "mismatch"
    NOT_IN_MEMORY = "not_in_memory"  # the parameter has no memory offset
    UNCHECKED = (
        "unchecked"  # no image, a type that can't be encoded, or an unknown layout
    )


@dataclass(frozen=True, slots=True)
class Row:
    """One shown setting (or a separator line, ``separator`` True, only ``label`` set)."""

    ref_id: str
    parameter: str  # the parameter's name in the description
    label: str
    value: str  # the symbolic value, e.g. "yn_Yes" or "300"
    value_text: str  # what the value means, e.g. "Yes" or "300 s"
    default_value: str
    stored: bool  # the plug-in stored this value (else it is the default)
    assigned: bool  # set by a rule of the description, not by the user
    memory: MemoryCheck
    memory_value: str = ""  # the symbolic value the image holds, for a mismatch
    memory_value_text: str = ""
    address: int | None = None
    separator: bool = False
    scaled: bool = False  # value_text is a "Percent" number scaled from the raw value
    clock: bool = (
        False  # value_text is a time in the format its name gives, with the raw time
    )
    fitted: bool = True  # False: a setting of an extension module that is not fitted
    comparison_value: str | None = None


@dataclass(slots=True)
class Page:
    """One parameter block of the plug-in (a page in its tree)."""

    id: str
    title: str  # empty for settings outside any block
    channel: (
        int  # 0 for channel-independent pages, else the channel's position (1-based)
    )
    rows: list[Row] = field(default_factory=list[Row])
    fitted: bool = True  # False: a page of an extension module that is not fitted


@dataclass(slots=True)
class GenericView:
    application_id: str
    application_matches: bool  # the stored settings name this description's application
    stored_application_id: str  # the application the stored settings name
    pages: list[Page]
    has_image: bool
    image_layout_known: bool
    modified: str
    last_download: str
    show_unfitted: bool = False  # the view includes settings of modules not fitted

    def count(self, check: MemoryCheck) -> int:
        """The shown settings (of fitted modules) with memory check ``check``."""
        return sum(
            1
            for p in self.pages
            for r in p.rows
            if r.memory is check and not r.separator and r.fitted
        )

    @property
    def unfitted_count(self) -> int:
        """The settings of modules not fitted the view holds (0 unless ``show_unfitted``)."""
        return sum(
            1 for p in self.pages for r in p.rows if not r.fitted and not r.separator
        )

    @property
    def mismatches(self) -> list[tuple[Page, Row]]:
        return [
            (p, r)
            for p in self.pages
            for r in p.rows
            if r.memory is MemoryCheck.MISMATCH
        ]


@dataclass(slots=True)
class _Walk:
    """State of one pass over the Dynamic tree."""

    pages: list[Page] = field(default_factory=list[Page])
    # (page, ref id or "\0" + separator id, fitted)
    shown: list[tuple[Page, str, bool]] = field(
        default_factory=list["tuple[Page, str, bool]"]
    )
    active_by_parameter: dict[str, str] = field(default_factory=dict[str, str])
    assigned: dict[str, str] = field(default_factory=dict[str, str])
    memory_values: list[tuple[str, str]] = field(default_factory=list[tuple[str, str]])
    objects: list[tuple[str, int]] = field(default_factory=list[tuple[str, int]])
    control_by_parameter: dict[str, str] = field(default_factory=dict[str, str])


class Evaluator:
    def __init__(
        self,
        description: Description,
        settings: PluginSettings,
        texts: dict[str, str],
        globals_: dict[str, str],
        show_unfitted: bool = False,
    ) -> None:
        self.show_unfitted = show_unfitted
        self.d = description
        self.s = settings
        self.texts = texts
        self.globals = globals_
        self.assigned: dict[str, str] = {}
        self.previous_active: dict[str, str] = {}
        self._locked: tuple[_Walk, set[str]] | None = None

    # --- values ---------------------------------------------------------------------------

    def default(self, ref_id: str) -> str:
        ref = self.d.refs.get(ref_id)
        if ref is None:
            return ""
        if ref.global_variable and ref.global_variable in self.globals:
            return self.globals[ref.global_variable]
        if ref.default is not None:
            return ref.default
        param = self.d.parameters.get(ref.parameter_id)
        return param.default if param is not None else ""

    def value(self, ref_id: str, walk: _Walk | None = None) -> str:
        ref = self.d.refs.get(ref_id)
        if ref is not None and ref.global_variable in self.globals:
            return self.globals[ref.global_variable]
        if walk is not None and ref_id in walk.assigned:
            return walk.assigned[ref_id]
        if ref_id in self.assigned:
            return self.assigned[ref_id]
        if ref_id in self.s.values:
            return self.s.values[ref_id]
        return self.default(ref_id)

    def choose_value(self, node: Node, walk: _Walk) -> str:
        if node.search_active:
            ref = self.d.refs.get(node.id)
            if ref is not None:
                active = walk.active_by_parameter.get(
                    ref.parameter_id
                ) or self.previous_active.get(ref.parameter_id)
                if active:
                    return self.value(active, walk)
        return self.value(node.id, walk)

    def test_value(self, ref_id: str, value: str) -> str:
        ref = self.d.refs.get(ref_id)
        parameter = self.d.parameters.get(ref.parameter_id) if ref else None
        ptype = self.d.types.get(parameter.type_id) if parameter else None
        enum = ptype.enum_by_name(value) if ptype else None
        if enum is not None:
            return str(enum.value)
        if value.lower().startswith("0x"):
            try:
                return str(int(value, 16))
            except ValueError:
                pass
        return value

    # --- walk -----------------------------------------------------------------------------

    def run(self) -> _Walk:
        walk = _Walk()
        show_unfitted, self.show_unfitted = self.show_unfitted, False
        seen: set[tuple[tuple[tuple[str, str], ...], tuple[tuple[str, str], ...]]] = (
            set()
        )
        for _ in range(_MAX_PASSES):
            walk = _Walk()
            self._visit(self.d.dynamic, walk, None, 0)
            settled = (
                walk.assigned == self.assigned
                and walk.active_by_parameter == self.previous_active
            )
            self.assigned = dict(walk.assigned)
            self.previous_active = dict(walk.active_by_parameter)
            if settled:
                break
            state = (
                tuple(sorted(self.assigned.items())),
                tuple(sorted(self.previous_active.items())),
            )
            if state in seen:
                raise ValueError("Plugin parameter dependencies do not converge")
            seen.add(state)
        else:
            raise ValueError(
                "Plugin parameter dependencies exceed the evaluation limit"
            )
        if show_unfitted:
            # One more pass on the settled values that also walks the branches of modules
            # not fitted; nothing in them assigns or activates anything.
            self.show_unfitted = True
            walk = _Walk()
            self._visit(self.d.dynamic, walk, None, 0)
        return walk

    def is_module_switch(self, node: Node) -> bool:
        """Whether the choose ``node`` switches the settings of extension modules."""
        ref = self.d.refs.get(node.id)
        param = self.d.parameters.get(ref.parameter_id) if ref is not None else None
        if param is None:
            return False
        return bool(_MODULE_SWITCH.search(f"{param.name} {param.text}"))

    def _page(
        self, walk: _Walk, page: Page | None, channel: int, fitted: bool = True
    ) -> Page:
        if page is not None and page.fitted == fitted:
            return page
        if page is not None:
            # Settings of a module not fitted inside a block: a page of their own, titled
            # like the block.
            loose = Page(
                id=page.id + "~", title=page.title, channel=channel, fitted=fitted
            )
            walk.pages.append(loose)
            return loose
        loose = Page(id="", title="", channel=channel, fitted=fitted)
        walk.pages.append(loose)
        return loose

    def _visit(
        self,
        nodes: list[Node],
        walk: _Walk,
        page: Page | None,
        channel: int,
        fitted: bool = True,
    ) -> Page | None:
        for node in nodes:
            kind = node.kind
            if kind is NodeKind.CHANNEL:
                self._visit(node.children, walk, None, node.channel, fitted)
            elif kind is NodeKind.INDEPENDENT:
                self._visit(node.children, walk, None, 0, fitted)
            elif kind is NodeKind.BLOCK:
                block = Page(
                    id=node.id,
                    title=self.texts.get(node.id) or node.text,
                    channel=channel,
                    fitted=fitted,
                )
                walk.pages.append(block)
                self._visit(node.children, walk, block, channel, fitted)
            elif kind is NodeKind.CHOOSE:
                value = self.test_value(node.id, self.choose_value(node, walk))
                whens = [c for c in node.children if c.kind is NodeKind.WHEN]
                active = [
                    w
                    for w in whens
                    if any(value == self.test_value(node.id, test) for test in w.tests)
                ]
                if not active:
                    active = [w for w in whens if w.default]
                for when in active:
                    page = self._visit(when.children, walk, page, channel, fitted)
                if self.show_unfitted and fitted and self.is_module_switch(node):
                    unfitted_page = page
                    for when in whens:
                        if when not in active:
                            unfitted_page = self._visit(
                                when.children, walk, unfitted_page, channel, False
                            )
            elif kind is NodeKind.WHEN:
                page = self._visit(node.children, walk, page, channel, fitted)
            elif kind is NodeKind.PARAMETER:
                page = self._page(walk, page, channel, fitted)
                if not fitted and any(r == node.id and f for _p, r, f in walk.shown):
                    continue  # already shown for a fitted module
                walk.shown.append((page, node.id, fitted))
                if fitted:
                    walk.memory_values.append((node.id, self.value(node.id, walk)))
                ref = self.d.refs.get(node.id)
                if ref is not None and fitted:
                    active = walk.control_by_parameter.setdefault(
                        ref.parameter_id, node.id
                    )
                    walk.active_by_parameter[ref.parameter_id] = active
            elif kind is NodeKind.SEPARATOR:
                page = self._page(walk, page, channel, fitted)
                walk.shown.append((page, "\0" + node.id, fitted))
            elif kind is NodeKind.OBJECT and fitted:
                walk.objects.append((node.id, channel))
            elif kind is NodeKind.ASSIGN and node.target and fitted:
                if node.value is not None:
                    walk.assigned[node.target] = node.value
                elif node.source:
                    walk.assigned[node.target] = self.value(node.source, walk)
                walk.memory_values.append((node.target, self.value(node.target, walk)))
                target = self.d.refs.get(node.target)
                if target is not None:
                    walk.active_by_parameter.setdefault(
                        target.parameter_id, node.target
                    )
        return page

    # --- rows -----------------------------------------------------------------------------

    def locked(self, walk: _Walk) -> set[str]:
        """The parameters a download of ``walk``'s settings leaves as the device holds them
        (see :func:`.description.locked_parameters`)."""
        if self._locked is None or self._locked[0] is not walk:
            active = [r for r, _ in walk.memory_values]
            values = {**self.s.values, **walk.assigned}
            values.update((r, self.value(r, walk)) for r in active)
            self._locked = (walk, locked_parameters(self.d, values, active))
        return self._locked[1]

    def label(self, ref_id: str) -> str:
        ref = self.d.refs.get(ref_id)
        if ref is None:
            return ref_id
        param = self.d.parameters.get(ref.parameter_id)
        candidates = [self.texts.get(ref_id), ref.text]
        if param is not None:
            candidates += [self.texts.get(param.id), param.text, param.name]
        return next((c for c in candidates if c), ref_id)

    def value_text(
        self,
        value: str,
        ptype: ParameterType | None,
        param: Parameter | None = None,
        label: str = "",
    ) -> str:
        if ptype is None:
            return value
        scaled = percent_text(value, ptype, param)
        if scaled is not None:
            return scaled
        clock = clock_text(value, ptype, label)
        if clock is not None:
            return clock
        if ptype.kind is TypeKind.ENUM:
            enum = ptype.enumeration(value)
            if enum is None:
                return value
            return self.texts.get(enum.id) or enum.text or value
        if ptype.kind in TIME_KINDS:
            with suppress(ValueError):
                return raw_time(integer(value), ptype.kind)
        return value

    def row(self, ref_id: str, walk: _Walk, fitted: bool = True) -> Row:
        ref = self.d.refs.get(ref_id)
        param = self.d.parameters.get(ref.parameter_id) if ref is not None else None
        ptype = self.d.types.get(param.type_id) if param is not None else None
        value = self.value(ref_id, walk)
        memory = MemoryCheck.UNCHECKED
        memory_value = ""
        address = param.offset if param is not None else None
        image = self.s.memory_image
        base = self.d.image_origin
        if param is not None and param.offset is None:
            memory = MemoryCheck.NOT_IN_MEMORY
        elif not fitted:
            memory = MemoryCheck.UNCHECKED
        elif (
            ptype is not None
            and address is not None
            and image is not None
            and base is not None
            and same_application(self.s.application_id, self.d.id)
        ):
            expected = encode(value, ptype)
            size = byte_size(ptype)
            start = address - base
            if (
                expected is not None
                and size is not None
                and 0 <= start <= len(image) - size
                and (
                    self.s.memory_mask is None
                    or self.s.memory_mask[start : start + size] == b"\xff" * size
                )
                and (param is None or param.id not in self.locked(walk))
            ):
                held = image[start : start + size]
                if held == expected:
                    memory = MemoryCheck.MATCH
                else:
                    memory = MemoryCheck.MISMATCH
                    memory_value = decode(held, ptype) or held.hex()
        label = self.label(ref_id)
        comparison_value = value
        if ptype is not None:
            enum = ptype.enumeration(value) if ptype.kind is TypeKind.ENUM else None
            if enum is not None:
                comparison_value = str(enum.value)
            elif ptype.kind in INTEGER_KINDS:
                with suppress(ValueError):
                    comparison_value = str(integer(value))
        formatting = (
            value_format(self.d, ref_id)
            if ref is not None and ptype is not None
            else None
        )

        def formatted(raw: str) -> str:
            if formatting is not None and (formatting.percent or formatting.time):
                with suppress(ValueError):
                    shown = formatting.display(raw)
                    if formatting.percent:
                        return f"{shown} %"
                    return f"{shown} ({raw_time(integer(raw), formatting.kind)})"
            return self.value_text(raw, ptype, param, label)

        return Row(
            ref_id=ref_id,
            parameter=param.name if param is not None else "",
            label=label,
            value=value,
            value_text=formatted(value),
            default_value=self.default(ref_id),
            stored=ref_id in self.s.values,
            assigned=ref_id in walk.assigned,
            memory=memory,
            memory_value=memory_value,
            memory_value_text=formatted(memory_value) if memory_value else "",
            address=address,
            scaled=bool(formatting and formatting.percent)
            or percent_text(value, ptype, param) is not None,
            clock=bool(formatting and formatting.time)
            or clock_text(value, ptype, label) is not None,
            fitted=fitted,
            comparison_value=comparison_value,
        )


def clock_text(value: str, ptype: ParameterType | None, label: str) -> str | None:
    """A time setting in the format its name gives ("mm:ss", "hh:mm:ss" or "hh:mm"), followed
    by the raw time: "15:00 (900 s)", "00:00:01.5 (1500 ms)". None for any other setting, a
    name without a format or a value that is not a number."""
    if ptype is None or ptype.kind not in TIME_KINDS:
        return None
    found = _CLOCK_FORMAT.search(label)
    if found is None:
        return None
    try:
        raw = int(value)
    except ValueError:
        return None
    millis = raw * TIME_UNIT_MS[ptype.kind]
    sign = "-" if millis < 0 else ""
    millis = abs(millis)
    seconds, fraction = divmod(millis, 1000)
    hours, rest = divmod(seconds, 3600)
    minutes, secs = divmod(rest, 60)
    tail = f".{fraction:03d}".rstrip("0") if fraction else ""
    shape = found.group(1).lower()
    if shape == "mm:ss":
        text = f"{hours * 60 + minutes:02d}:{secs:02d}{tail}"
    elif shape == "hh:mm" and not secs and not tail:
        text = f"{hours:02d}:{minutes:02d}"
    else:
        text = f"{hours:02d}:{minutes:02d}:{secs:02d}{tail}"
    return f"{sign}{text} ({raw_time(raw, ptype.kind)})"


def raw_time(raw: int, kind: TypeKind) -> str:
    """A stored time in its own unit, "900 s" or "1500 ms"; a time in tenths or hundredths
    of a second in milliseconds ("1500 ms" for 150 hundredths)."""
    if kind is TypeKind.TIME_S:
        return f"{raw} s"
    return f"{raw * TIME_UNIT_MS[kind]} ms"


def percent_text(
    value: str, ptype: ParameterType | None, param: Parameter | None
) -> str | None:
    """A "Percent" number on the 0..255 scale ("40 %" for 102), None for
    any other setting or a value that is not a number."""
    if param is None or ptype is None or param.format.lower() != "percent":
        return None
    if ptype.kind is not TypeKind.NUMBER:
        return None
    try:
        text = ValueFormat(ptype.kind, format="Percent").display(value)
    except ValueError:
        return None
    return f"{text} %"


def build_view(
    description: Description,
    settings: PluginSettings,
    texts: dict[str, str] | None = None,
    globals_: dict[str, str] | None = None,
    show_unfitted: bool = False,
) -> GenericView:
    """The read-only view of ``settings`` under ``description``.

    ``texts`` are the translated texts by id (from :func:`parse_translations`), ``globals_`` the
    values of global variables such as ``DeviceOrderNumber`` (else the description's defaults).
    ``show_unfitted`` adds the settings of extension modules that are not fitted (pages and rows
    marked ``fitted=False``, never checked against the memory image).
    """
    builder = Evaluator(
        description, settings, texts or {}, globals_ or {}, show_unfitted
    )
    walk = builder.run()
    for page, ref_id, fitted in walk.shown:
        if ref_id.startswith("\0"):
            sid = ref_id[1:]
            page.rows.append(
                Row(
                    ref_id=sid,
                    parameter="",
                    label=builder.texts.get(sid) or description.texts.get(sid, ""),
                    value="",
                    value_text="",
                    default_value="",
                    stored=False,
                    assigned=False,
                    memory=MemoryCheck.NOT_IN_MEMORY,
                    separator=True,
                    fitted=fitted,
                )
            )
        else:
            page.rows.append(builder.row(ref_id, walk, fitted))
    pages = [p for p in walk.pages if p.rows]
    rows = [r for p in pages for r in p.rows]
    checked = [r for r in rows if r.memory in (MemoryCheck.MATCH, MemoryCheck.MISMATCH)]
    layout_known = bool(checked)
    return GenericView(
        application_id=description.id,
        application_matches=same_application(settings.application_id, description.id),
        stored_application_id=settings.application_id,
        pages=pages,
        has_image=settings.memory_image is not None,
        image_layout_known=layout_known,
        modified=settings.modified,
        last_download=settings.last_download,
        show_unfitted=show_unfitted,
    )


def same_application(stored: str, described: str) -> bool:
    """Whether the settings' application name is the description's. The plug-in has written
    it as the description's id ("id-DEMO_0001_01") and as its bare characters ("DEMO000101")."""
    if not stored:
        return True
    return _bare_id(stored) == _bare_id(described)


def _bare_id(text: str) -> str:
    text = text.lower()
    text = text.removeprefix("id-")
    return "".join(c for c in text if c.isalnum())


# --- comparing two devices -------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class CompareRow:
    """One setting of two devices side by side; ``first``/``second`` is None where that
    device does not show the setting (another branch is active there)."""

    first: Row | None
    second: Row | None

    @property
    def row(self) -> Row:
        row = self.first or self.second
        assert row is not None
        return row

    @property
    def differs(self) -> bool:
        if self.first is None or self.second is None:
            return True
        first = self.first.comparison_value
        second = self.second.comparison_value
        return (self.first.value if first is None else first) != (
            self.second.value if second is None else second
        )


@dataclass(slots=True)
class ComparePage:
    id: str
    title: str
    channel: int
    fitted: bool
    rows: list[CompareRow] = field(default_factory=list[CompareRow])


@dataclass(slots=True)
class ViewComparison:
    pages: list[ComparePage]
    same_application: bool  # both views come from the same description

    @property
    def differences(self) -> int:
        return sum(1 for p in self.pages for r in p.rows if r.differs)


def _page_keys(view: GenericView) -> list[tuple[str, int, bool]]:
    """A key per page of ``view`` that the same page of another device's view shares: the
    block id, or for settings outside a block the title and how often it came before."""
    seen: dict[tuple[str, int, bool], int] = {}
    keys: list[tuple[str, int, bool]] = []
    for page in view.pages:
        base = (page.id or "#" + page.title, page.channel, page.fitted)
        n = seen.get(base, 0)
        seen[base] = n + 1
        keys.append((f"{base[0]}#{n}", page.channel, page.fitted))
    return keys


def compare_views(first: GenericView, second: GenericView) -> ViewComparison:
    """``first`` and ``second`` setting by setting, in ``first``'s order (pages and settings
    only ``second`` shows follow). Separators are left out; settings are matched by page and
    parameter ref, and differ when their resolved values do."""
    pages: dict[tuple[str, int, bool], ComparePage] = {}
    seconds: dict[tuple[tuple[str, int, bool], str], Row] = {}
    first_keys, second_keys = _page_keys(first), _page_keys(second)
    for page, key in zip(second.pages, second_keys, strict=True):
        for row in page.rows:
            if not row.separator:
                seconds[(key, row.ref_id)] = row
    used: set[tuple[tuple[str, int, bool], str]] = set()
    for page, key in zip(first.pages, first_keys, strict=True):
        out = pages.setdefault(
            key, ComparePage(page.id, page.title, page.channel, page.fitted)
        )
        for row in page.rows:
            if row.separator:
                continue
            other = seconds.get((key, row.ref_id))
            if other is not None:
                used.add((key, row.ref_id))
            out.rows.append(CompareRow(row, other))
    for page, key in zip(second.pages, second_keys, strict=True):
        for row in page.rows:
            if row.separator or (key, row.ref_id) in used:
                continue
            out = pages.setdefault(
                key, ComparePage(page.id, page.title, page.channel, page.fitted)
            )
            out.rows.append(CompareRow(None, row))
    return ViewComparison(
        pages=[p for p in pages.values() if p.rows],
        same_application=_bare_id(first.application_id)
        == _bare_id(second.application_id),
    )
