"""Read-only view of a device's Generic Plug-in settings: pages, values and memory check.

The view walks the description's ``Dynamic`` tree the way the plug-in shows it: a ``choose``
activates every ``when`` whose test list (``"a;b;c"``) holds the value of its parameter ref, and
its ``default`` when none does; ``Assign`` elements in active branches set derived values, so the
walk repeats until those settle. Every parameter in an active branch becomes a row with its
name, symbolic value and text. Each row is then checked against the stored memory image of the
last download: the value is encoded with the description's own type and offset and compared
byte for byte. Nothing here writes anything.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from .blob import PluginSettings
from .codec import byte_size, decode, encode
from .description import Description, Node, NodeKind, ParameterType, TypeKind

# How often the walk is repeated at most for Assign values to settle.
_MAX_PASSES = 8
# Below this share of matching values the image is taken to be laid out differently than
# assumed, and no row is marked.
_MIN_LAYOUT_MATCH = 0.5
_MIN_LAYOUT_CHECKED = 10


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


@dataclass(slots=True)
class Page:
    """One parameter block of the plug-in (a page in its tree)."""

    id: str
    title: str  # empty for settings outside any block
    channel: (
        int  # 0 for channel-independent pages, else the channel's position (1-based)
    )
    rows: list[Row] = field(default_factory=list[Row])


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

    def count(self, check: MemoryCheck) -> int:
        return sum(
            1
            for p in self.pages
            for r in p.rows
            if r.memory is check and not r.separator
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
    shown: list[tuple[Page, str]] = field(default_factory=list["tuple[Page, str]"])
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
    ) -> None:
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
        for _ in range(_MAX_PASSES):
            walk = _Walk()
            self._visit(self.d.dynamic, walk, None, 0)
            settled = walk.assigned == self.assigned
            self.assigned = dict(walk.assigned)
            self.previous_active = dict(walk.active_by_parameter)
            if settled:
                break
        return walk

    def _page(self, walk: _Walk, page: Page | None, channel: int) -> Page:
        if page is not None:
            return page
        loose = Page(id="", title="", channel=channel)
        walk.pages.append(loose)
        return loose

    def _visit(
        self, nodes: list[Node], walk: _Walk, page: Page | None, channel: int
    ) -> Page | None:
        for node in nodes:
            kind = node.kind
            if kind is NodeKind.CHANNEL:
                walk.channels += 1
                self._visit(node.children, walk, None, walk.channels)
            elif kind is NodeKind.INDEPENDENT:
                self._visit(node.children, walk, None, 0)
            elif kind is NodeKind.BLOCK:
                block = Page(
                    id=node.id,
                    title=self.texts.get(node.id) or node.text,
                    channel=channel,
                )
                walk.pages.append(block)
                self._visit(node.children, walk, block, channel)
            elif kind is NodeKind.CHOOSE:
                value = self.choose_value(node, walk)
                whens = [c for c in node.children if c.kind is NodeKind.WHEN]
                active = [w for w in whens if value in w.tests]
                if not active:
                    active = [w for w in whens if w.default]
                for when in active:
                    page = self._visit(when.children, walk, page, channel)
            elif kind is NodeKind.WHEN:
                page = self._visit(node.children, walk, page, channel)
            elif kind is NodeKind.PARAMETER:
                page = self._page(walk, page, channel)
                walk.shown.append((page, node.id))
                ref = self.d.refs.get(node.id)
                if ref is not None:
                    walk.active_by_parameter[ref.parameter_id] = node.id
            elif kind is NodeKind.SEPARATOR:
                page = self._page(walk, page, channel)
                walk.shown.append((page, "\0" + node.id))
            elif kind is NodeKind.ASSIGN and node.target:
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

    def value_text(self, value: str, ptype: ParameterType | None) -> str:
        if ptype is None:
            return value
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

    def row(self, ref_id: str, walk: _Walk) -> Row:
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
        return Row(
            ref_id=ref_id,
            parameter=param.name if param is not None else "",
            label=self.label(ref_id),
            value=value,
            value_text=self.value_text(value, ptype),
            default_value=self.default(ref_id),
            stored=ref_id in self.s.values,
            assigned=ref_id in walk.assigned,
            memory=memory,
            memory_value=memory_value,
            memory_value_text=self.value_text(memory_value, ptype)
            if memory_value
            else "",
            address=address,
        )


def build_view(
    description: Description,
    settings: PluginSettings,
    texts: dict[str, str] | None = None,
    globals_: dict[str, str] | None = None,
) -> GenericView:
    """The read-only view of ``settings`` under ``description``.

    ``texts`` are the translated texts by id (from :func:`parse_translations`), ``globals_`` the
    values of global variables such as ``DeviceOrderNumber`` (else the description's defaults).
    """
    builder = _Builder(description, settings, texts or {}, globals_ or {})
    walk = builder.run()
    for page, ref_id in walk.shown:
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
                )
            )
        else:
            page.rows.append(builder.row(ref_id, walk))
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
    )
