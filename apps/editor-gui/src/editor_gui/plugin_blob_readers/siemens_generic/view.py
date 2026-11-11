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
setting. "Percent" numbers are shown scaled (raw / full scale x 100) with the raw value kept.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum

from .blob import PluginSettings
from .codec import byte_size, decode, encode
from .description import (
    Description,
    Node,
    NodeKind,
    Parameter,
    ParameterType,
    TypeKind,
)

# How often the walk is repeated at most for Assign values to settle.
_MAX_PASSES = 8
# Below this share of matching values the image is taken to be laid out differently than
# assumed, and no row is marked.
_MIN_LAYOUT_MATCH = 0.5
_MIN_LAYOUT_CHECKED = 10
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
    channels: int = 0


class _Builder:
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

    # --- walk -----------------------------------------------------------------------------

    def run(self) -> _Walk:
        walk = _Walk()
        show_unfitted, self.show_unfitted = self.show_unfitted, False
        for _ in range(_MAX_PASSES):
            walk = _Walk()
            self._visit(self.d.dynamic, walk, None, 0)
            settled = walk.assigned == self.assigned
            self.assigned = dict(walk.assigned)
            self.previous_active = dict(walk.active_by_parameter)
            if settled:
                break
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
                walk.channels += 1
                self._visit(node.children, walk, None, walk.channels, fitted)
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
                value = self.choose_value(node, walk)
                whens = [c for c in node.children if c.kind is NodeKind.WHEN]
                active = [w for w in whens if value in w.tests]
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
                ref = self.d.refs.get(node.id)
                if ref is not None and fitted:
                    walk.active_by_parameter[ref.parameter_id] = node.id
            elif kind is NodeKind.SEPARATOR:
                page = self._page(walk, page, channel, fitted)
                walk.shown.append((page, "\0" + node.id, fitted))
            elif kind is NodeKind.ASSIGN and node.target and fitted:
                if node.value is not None:
                    walk.assigned[node.target] = node.value
                elif node.source:
                    walk.assigned[node.target] = self.value(node.source, walk)
                target = self.d.refs.get(node.target)
                if target is not None:
                    walk.active_by_parameter.setdefault(
                        target.parameter_id, node.target
                    )
        return page

    # --- rows -----------------------------------------------------------------------------

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
            enum = ptype.enum_by_name(value)
            if enum is None:
                return value
            return self.texts.get(enum.id) or enum.text or value
        if ptype.kind is TypeKind.TIME_S:
            return f"{value} s"
        if ptype.kind is TypeKind.TIME_MS:
            return f"{value} ms"
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
        base = self.d.memory_base
        if param is not None and param.offset is None:
            memory = MemoryCheck.NOT_IN_MEMORY
        elif not fitted:
            memory = MemoryCheck.UNCHECKED
        elif (
            ptype is not None
            and address is not None
            and image is not None
            and base is not None
        ):
            expected = encode(value, ptype)
            size = byte_size(ptype)
            start = address - base
            if (
                expected is not None
                and size is not None
                and 0 <= start <= len(image) - size
            ):
                held = image[start : start + size]
                if held == expected:
                    memory = MemoryCheck.MATCH
                else:
                    memory = MemoryCheck.MISMATCH
                    memory_value = decode(held, ptype) or held.hex()
        label = self.label(ref_id)
        return Row(
            ref_id=ref_id,
            parameter=param.name if param is not None else "",
            label=label,
            value=value,
            value_text=self.value_text(value, ptype, param, label),
            default_value=self.default(ref_id),
            stored=ref_id in self.s.values,
            assigned=ref_id in walk.assigned,
            memory=memory,
            memory_value=memory_value,
            memory_value_text=self.value_text(memory_value, ptype, param, label)
            if memory_value
            else "",
            address=address,
            scaled=percent_text(value, ptype, param) is not None,
            clock=clock_text(value, ptype, label) is not None,
            fitted=fitted,
        )


def clock_text(value: str, ptype: ParameterType | None, label: str) -> str | None:
    """A time setting in the format its name gives ("mm:ss", "hh:mm:ss" or "hh:mm"), followed
    by the raw time: "15:00 (900 s)", "00:00:01.5 (1500 ms)". None for any other setting, a
    name without a format or a value that is not a number."""
    if ptype is None or ptype.kind not in (TypeKind.TIME_S, TypeKind.TIME_MS):
        return None
    found = _CLOCK_FORMAT.search(label)
    if found is None:
        return None
    try:
        raw = int(value)
    except ValueError:
        return None
    millis = raw if ptype.kind is TypeKind.TIME_MS else raw * 1000
    unit = "ms" if ptype.kind is TypeKind.TIME_MS else "s"
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
    return f"{sign}{text} ({raw} {unit})"


def percent_text(
    value: str, ptype: ParameterType | None, param: Parameter | None
) -> str | None:
    """A "Percent" number as a percentage of its full scale ("40 %" for 102 of 255), None for
    any other setting or a value that is not a number."""
    if param is None or ptype is None or param.format.lower() != "percent":
        return None
    if ptype.kind is not TypeKind.NUMBER or ptype.full_scale is None:
        return None
    try:
        raw = int(value)
    except ValueError:
        return None
    share = round(raw * 100 / ptype.full_scale, 1)
    text = f"{share:.1f}".removesuffix(".0")
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
    builder = _Builder(
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
    matched = sum(1 for r in checked if r.memory is MemoryCheck.MATCH)
    layout_known = bool(checked) and (
        len(checked) < _MIN_LAYOUT_CHECKED
        or matched / len(checked) >= _MIN_LAYOUT_MATCH
    )
    if checked and not layout_known:
        for page in pages:
            page.rows = [
                _unchecked(r) if r.memory is not MemoryCheck.NOT_IN_MEMORY else r
                for r in page.rows
            ]
    return GenericView(
        application_id=description.id,
        application_matches=_same_application(settings.application_id, description.id),
        stored_application_id=settings.application_id,
        pages=pages,
        has_image=settings.memory_image is not None,
        image_layout_known=layout_known,
        modified=settings.modified,
        last_download=settings.last_download,
        show_unfitted=show_unfitted,
    )


def _same_application(stored: str, described: str) -> bool:
    """Whether the settings' application name is the description's. The plug-in has written
    it as the description's id ("id-DEMO_0001_01") and as its bare characters ("DEMO000101")."""
    if not stored:
        return True
    return _bare_id(stored) == _bare_id(described)


def _bare_id(text: str) -> str:
    text = text.lower()
    text = text.removeprefix("id-")
    return "".join(c for c in text if c.isalnum())


def _unchecked(row: Row) -> Row:
    return Row(
        ref_id=row.ref_id,
        parameter=row.parameter,
        label=row.label,
        value=row.value,
        value_text=row.value_text,
        default_value=row.default_value,
        stored=row.stored,
        assigned=row.assigned,
        memory=MemoryCheck.UNCHECKED,
        address=row.address,
        separator=row.separator,
        scaled=row.scaled,
        clock=row.clock,
        fitted=row.fitted,
    )


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
        return self.first.value != self.second.value


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
    parameter ref, and differ when their symbolic values do."""
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
