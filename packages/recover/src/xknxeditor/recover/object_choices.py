"""Settings whose value only picks which group objects are shown (see :func:`object_selectors`).

A setting the device keeps no copy of is inferred by trying each of its values and counting the
linked group objects the UI then shows (see :func:`.parameters._infer_unstored`). For a setting
whose chooses hold nothing but group objects, the objects a value shows follow from the UI
evaluated with another value: only the objects of the branches the value selects change.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Collection, Mapping, Sequence
from typing import TYPE_CHECKING, NamedTuple, cast

if TYPE_CHECKING:
    from _typeshed import DataclassInstance

    from xknxeditor.namespaces.intermediate import ApplicationProgram
    from xknxeditor.prod import Application
    from xknxeditor.prod.parser_v2.application_indexer import ApplicationIndexer
    from xknxeditor.prod.parser_v2.dynamic import DynamicTreeBuilder, DynamicUI

# A choose: the group object references of each test its branches name (branches with the
# same test merged, as the tree merges them), and those of its default branch (None: none).
_Choose = tuple[tuple[tuple[str, frozenset[str]], ...], frozenset[str] | None]


class ObjectSelector(NamedTuple):
    """A setting whose chooses hold group objects only, with nothing else depending on it.

    ``chooses`` are the chooses on it (see ``_Choose``), ``refs`` all the group object
    references in them, and ``objects`` the number and size of each."""

    chooses: tuple[_Choose, ...]
    refs: frozenset[str]
    objects: Mapping[str, tuple[int, str]]

    def selected(self, value: str) -> frozenset[str]:
        """The group object references the branches ``value`` selects hold: of each choose
        those of every branch whose test it satisfies, else those of the default branch."""
        from xknxeditor.prod.parser_v2.nodes.choose import satisfies

        found: set[str] = set()
        for branches, default in self.chooses:
            matched = False
            for test, refs in branches:
                if satisfies(test, value):
                    matched = True
                    found |= refs
            if not matched and default is not None:
                found |= default
        return frozenset(found)


class ShownObjects:
    """The group objects a UI shows with one setting changed, told from the UI as it is for
    the settings of :func:`object_selectors` (see :meth:`guess`).

    ``ui`` is the UI evaluated with the values the trials change one setting of, ``sizes``
    the object sizes the device's table gives and ``linked`` the linked objects, as for
    :func:`.parameters._infer_unstored`."""

    def __init__(
        self,
        application: Application,
        ui: DynamicUI,
        sizes: Mapping[int, str],
        linked: Collection[int],
    ) -> None:
        self._selectors = object_selectors(application)
        self._ui = ui
        self._sizes = sizes
        self._linked = linked
        self._shown: list[tuple[str, int, str]] | None = None
        # Per setting whether its chooses make their objects in the UI (None: the objects a
        # value shows are not told from the UI, see learn).
        self._made: dict[str, bool | None] = {}

    def guess(self, ref_id: str, value: str) -> tuple[set[int], int, bool] | None:
        """The linked objects the UI shows with ``ref_id`` set to ``value``, how many others,
        and whether it shows settings it does not show now (never: the setting shows group
        objects only); None when only an evaluation tells. Once an evaluation of another value
        of the setting showed its chooses' objects as they are told here (see :meth:`learn`),
        the objects are those shown now but the chooses', plus those of the branches the
        value selects. Before that, only a value whose branches hold no object is told."""
        selector = self._selectors.get(ref_id)
        if selector is None or (ref_id in self._made and self._made[ref_id] is None):
            return None
        selected = selector.selected(value)
        if not selected:
            return self._result(selector, frozenset())
        if ref_id not in self._made:
            return None
        return self._result(selector, selected if self._made[ref_id] else frozenset())

    def learn(
        self,
        ref_id: str,
        value: str,
        seeded: DynamicUI,
        result: tuple[set[int], int, bool],
    ) -> None:
        """Take what the evaluation ``seeded`` of ``ref_id`` set to ``value`` showed
        (``result``, as :meth:`guess` gives it) for the values of the setting tried next: told
        from the UI only while this one would have been told alike."""
        selector = self._selectors.get(ref_id)
        if selector is None or ref_id in self._made:
            return
        selected = selector.selected(value)
        if not selected:
            return
        shown = {ref for ref, _, _ in _objects(seeded) if ref in selector.refs}
        made = True if frozenset(shown) == selected else False if not shown else None
        if (
            made is not None
            and self._result(selector, selected if made else frozenset()) != result
        ):
            made = None
        self._made[ref_id] = made

    def _result(
        self, selector: ObjectSelector, made: frozenset[str]
    ) -> tuple[set[int], int, bool]:
        if self._shown is None:
            self._shown = _objects(self._ui)
        objects = {
            number
            for ref, number, size in self._shown
            if ref not in selector.refs and self._fits(number, size)
        }
        for ref in made:
            number, size = selector.objects[ref]
            if self._fits(number, size):
                objects.add(number)
        return (
            {number for number in objects if number in self._linked},
            len([number for number in objects if number not in self._linked]),
            False,
        )

    def _fits(self, number: int, size: str) -> bool:
        wanted = self._sizes.get(number) if self._sizes else None
        return wanted is None or size == wanted


def _objects(ui: DynamicUI) -> list[tuple[str, int, str]]:
    """Reference, number and size of each group object ``ui`` shows."""
    from xknxeditor.prod.parser_v2.ui import UiComObject, UiParameterBlock, UiTab

    found: list[tuple[str, int, str]] = []
    stack: list[object] = list(ui.ui())
    while stack:
        node = stack.pop()
        if isinstance(node, UiComObject):
            found.append((node.ref_id, node.number, node.object_size))
        elif isinstance(node, UiTab | UiParameterBlock):
            stack.extend(node.children)
    return found


def object_selectors(application: Application) -> Mapping[str, ObjectSelector]:
    """The settings of ``application`` whose value only picks which group objects its chooses
    show, by param-ref id. Of the last few applications.

    Only an application without module definitions has them. Such a setting:

    - is placed once (one ``ParameterRefRef``), and each choose on it is beside that placement
      (in the same list of nodes), so a choose on it is evaluated whenever it is shown;
    - is named nowhere else in the dynamic section: no Assign, Repeat, Rename, block heading or
      other choose reads or writes it;
    - is no member of a union and no input or output of a calculation;
    - has chooses whose branches hold group object references only, each of them placed
      nowhere else.

    Its value then changes nothing in an evaluation but which of these group objects it
    makes: no parameter, no value and no other node depends on them."""
    program, builder = application.resolved()
    for seen, known in _SELECTORS:
        if seen is program:
            return known
    found = _find(program, builder)
    _SELECTORS.insert(0, (program, found))
    del _SELECTORS[4:]
    return found


# The selectors of the last few programs (see object_selectors).
_SELECTORS: list[tuple[object, Mapping[str, ObjectSelector]]] = []


def _find(
    program: ApplicationProgram, builder: DynamicTreeBuilder
) -> dict[str, ObjectSelector]:
    from xknxeditor.namespaces.intermediate import (
        ApplicationProgramStaticParametersUnion,
        ChannelChoose,
        ComObjectParameterChoose,
        DependentChannelChoose,
        ParameterRefRef,
    )

    idx = builder.idx
    if program.dynamic is None or idx.module_defs:
        return {}
    chooses = (DependentChannelChoose, ChannelChoose, ComObjectParameterChoose)
    # Every text of every node, with the node, the field and the list of nodes it is in.
    named: dict[str, list[tuple[object, str, int]]] = {}

    def walk(node: object, siblings: int) -> None:
        for item in dataclasses.fields(cast("DataclassInstance", node)):
            value: object = getattr(node, item.name)
            if isinstance(value, str):
                named.setdefault(value, []).append((node, item.name, siblings))
            elif isinstance(value, list):
                children = cast("list[object]", value)
                for child in children:
                    if dataclasses.is_dataclass(child):
                        walk(child, id(children))
            elif dataclasses.is_dataclass(value):
                walk(value, id(value))

    walk(program.dynamic, 0)
    members: set[str] = set()
    parameters = program.static.parameters
    for item in parameters.choice if parameters is not None else []:
        if isinstance(item, ApplicationProgramStaticParametersUnion):
            members.update(member.id for member in item.parameter)
    found: dict[str, ObjectSelector] = {}
    for ref_id, places in named.items():
        placed = [
            siblings
            for node, name, siblings in places
            if isinstance(node, ParameterRefRef) and name == "ref_id"
        ]
        if len(placed) != 1:
            continue
        selecting: list[object] = [
            node
            for node, name, siblings in places
            if isinstance(node, chooses)
            and name == "param_ref_id"
            and siblings == placed[0]
        ]
        if not selecting or len(selecting) + 1 != len(places):
            continue
        ref = idx.parameter_refs.get(ref_id)
        if (
            ref is None
            or ref.ref_id in members
            or idx.calculations_for_l(ref_id)
            or idx.calculations_for_r(ref_id)
        ):
            continue
        selector = _selector(selecting, named, idx)
        if selector is not None:
            found[ref_id] = selector
    return found


def _selector(
    selecting: Sequence[object],
    named: Mapping[str, list[tuple[object, str, int]]],
    idx: ApplicationIndexer,
) -> ObjectSelector | None:
    """The selector of the chooses ``selecting``, None when a branch holds anything but group
    object references placed there alone."""
    from xknxeditor.namespaces.intermediate import ComObjectRefRef

    built: list[_Choose] = []
    objects: dict[str, tuple[int, str]] = {}
    for choose in selecting:
        tests: dict[str, set[str]] = {}
        default: set[str] | None = None
        for when in cast("list[object]", getattr(choose, "when", None) or []):
            refs: set[str] = set()
            for child in cast("list[object]", getattr(when, "choice", None) or []):
                if not isinstance(child, ComObjectRefRef):
                    return None
                if len(named.get(child.ref_id, ())) != 1:
                    return None
                objects[child.ref_id] = _number_and_size(idx, child.ref_id)
                refs.add(child.ref_id)
            if getattr(when, "default", False):
                default = (default or set[str]()) | refs
            test = cast("str | None", getattr(when, "test", None))
            if test is not None:
                tests.setdefault(test, set()).update(refs)
        built.append(
            (
                tuple((test, frozenset(refs)) for test, refs in tests.items()),
                None if default is None else frozenset(default),
            )
        )
    return ObjectSelector(tuple(built), frozenset(objects), objects)


def _number_and_size(idx: ApplicationIndexer, ref_id: str) -> tuple[int, str]:
    """The number and the size of the group object a reference shows, as the tree gives them
    (the reference's size, else the object's)."""
    cor = idx.com_object_refs.get(ref_id)
    co = idx.com_objects.get(cor.ref_id) if cor is not None else None
    size = (cor.object_size if cor is not None else None) or (
        co.object_size if co is not None else None
    )
    return (co.number if co is not None else 0), (
        "" if size is None else str(size.value)
    )
