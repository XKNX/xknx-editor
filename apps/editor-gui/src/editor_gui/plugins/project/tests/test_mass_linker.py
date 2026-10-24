"""Tests for the Mass Linker: the pure target-assignment helpers and the plugin's link
orchestration.

The orchestration methods live on ``ProjectPlugin`` but only touch ``self._api.project`` and
``self._api.log``; we build the plugin via ``__new__`` (skipping the heavy panel-wiring
``__init__``) and inject fakes, so the linking logic is exercised without a real project/DB.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from types import SimpleNamespace

from editor_gui.plugins.project.plugin import ProjectPlugin
from editor_gui.plugins.project.ui.mass_linker import (
    MassLinkerPanel,
    _FlatGa,  # pyright: ignore[reportPrivateUsage]
    _Row,  # pyright: ignore[reportPrivateUsage]
    first_free_values,
    match_by_name,
    sequential_targets,
)
from xknxeditor.proj.core.addressing import GroupAddressStyle

# --- pure assignment helpers ----------------------------------------------


def test_sequential_targets_assigns_ascending_from_start():
    # existing GAs (raw value, id), unordered; assign 3 rows starting at value 2.
    existing = [(5, 105), (2, 102), (3, 103), (1, 101)]
    assert sequential_targets(existing, 2, 3) == [102, 103, 105]


def test_sequential_targets_runs_out_returns_none():
    assert sequential_targets([(10, 1)], 0, 3) == [1, None, None]
    assert sequential_targets([], 0, 2) == [None, None]
    assert sequential_targets([(1, 1)], 5, 1) == [None]  # none >= start


def test_first_free_values_skips_used_and_zero():
    assert first_free_values({2, 3}, 1, 3) == [1, 4, 5]  # 2,3 used -> 1,4,5
    assert first_free_values(set(), 0, 1) == [1]  # 0 excluded, starts at 1
    assert first_free_values(set(), 5, 2) == [5, 6]  # honours start
    used = {1, 2}
    assert first_free_values(used, 1, 2) == [3, 4]
    assert used == {1, 2}  # caller's set is not mutated


def test_first_free_values_respects_upper_bound():
    # never returns a value above the 16-bit maximum; returns fewer when exhausted.
    assert first_free_values(set(), 65535, 3) == [65535]
    assert first_free_values(set(), 65535, 3, limit=65535) == [65535]
    assert first_free_values({65535}, 65535, 1) == []  # last slot used -> nothing free


def test_match_by_name_prefers_exact_then_contains():
    existing = [(1, "Living room light"), (2, "Kitchen"), (3, "Light")]
    assert match_by_name("Light", existing) == 3  # exact (case-insensitive)
    assert match_by_name("kitchen switch", existing) == 2  # contains
    assert match_by_name("bedroom", existing) is None
    assert match_by_name("", existing) is None


# --- fakes ----------------------------------------------------------------


def _co(db_id, name="obj", major=1, minor=1):
    return SimpleNamespace(
        db_id=db_id, name=name, dpt=SimpleNamespace(major=major, minor=minor)
    )


@dataclass
class FakeProject:
    # links hold objects with .com_object_id/.group_address_id/.is_sending, like the real service.
    links: list = field(default_factory=list)
    created: list[tuple[str | None, str]] = field(default_factory=list)
    dpts: list[tuple[int, str]] = field(default_factory=list)
    removed: list[int] = field(default_factory=list)
    _next_ga: int = 100

    @property
    def group_address_style(self) -> GroupAddressStyle:
        return GroupAddressStyle.THREE_LEVEL

    def get_links_for_com_object(self, co_db_id: int):
        return [ln for ln in self.links if ln.com_object_id == co_db_id]

    def link_com_object_to_ga(self, co_db_id, ga_id, is_sending=False):
        self.links.append(
            SimpleNamespace(
                com_object_id=co_db_id, group_address_id=ga_id, is_sending=is_sending
            )
        )
        return len(self.links)

    def create_group_address(self, address=None, name=""):
        self.created.append((address, name))
        self._next_ga += 1
        return self._next_ga

    def set_group_address_dpt(self, ga_id, dpt):
        self.dpts.append((ga_id, dpt))

    def remove_group_address(self, ga_id, address="", name=""):
        self.removed.append(ga_id)

    @property
    def link_tuples(self) -> list[tuple[int, int, bool]]:
        return [
            (ln.com_object_id, ln.group_address_id, ln.is_sending) for ln in self.links
        ]


class FakeLog:
    def info(self, *a, **k):
        pass


def _plugin(project: FakeProject) -> ProjectPlugin:
    p = ProjectPlugin.__new__(ProjectPlugin)
    p._api = SimpleNamespace(project=project, log=FakeLog())  # type: ignore[attr-defined]
    return p


# --- dpt token ------------------------------------------------------------


def test_dpt_token():
    assert ProjectPlugin._dpt_token(SimpleNamespace(major=1, minor=1)) == "DPST-1-1"
    assert ProjectPlugin._dpt_token(SimpleNamespace(major=5, minor=0)) == "DPT-5"
    assert ProjectPlugin._dpt_token(None) is None
    assert ProjectPlugin._dpt_token(SimpleNamespace(major=0, minor=0)) is None


# --- GA <> object ---------------------------------------------------------


def test_link_ga_co_pairs_and_first_is_sending():
    proj = FakeProject()
    plugin = _plugin(proj)
    count, errors = plugin._ml_link_ga_co([(_co(1), 10), (_co(2), 11)])
    assert count == 2
    assert errors == []
    # Each com-object's first link is its sending address.
    assert proj.link_tuples == [(1, 10, True), (2, 11, True)]


def test_link_ga_co_skips_objects_without_db_id():
    proj = FakeProject()
    count, errors = _plugin(proj)._ml_link_ga_co([(_co(None), 10), (_co(3), 11)])
    assert count == 1
    assert len(errors) == 1
    assert proj.link_tuples == [(3, 11, True)]


# --- object <> object -----------------------------------------------------


def test_link_co_co_creates_ga_sets_dpt_links_both():
    proj = FakeProject()
    count, errors = _plugin(proj).mass_link_objects(
        [(_co(1, "src", 1, 1), _co(2, "tgt"), "", "src->tgt")]
    )
    assert count == 1
    assert errors == []
    assert proj.created == [(None, "src->tgt")]  # empty address -> auto, given name
    ga_id = proj.dpts[0][0]
    assert proj.dpts == [(ga_id, "DPST-1-1")]
    # source is sending, target receiving, both on the new GA.
    assert (1, ga_id, True) in proj.link_tuples
    assert (2, ga_id, False) in proj.link_tuples


def test_link_ga_co_skips_already_linked():
    proj = FakeProject()
    proj.link_com_object_to_ga(1, 10, is_sending=True)  # pre-existing link
    count, errors = _plugin(proj)._ml_link_ga_co([(_co(1), 10)])
    assert count == 0
    assert any("already linked" in e for e in errors)


def test_link_co_co_rejects_self_pair():
    proj = FakeProject()
    count, errors = _plugin(proj).mass_link_objects([(_co(1), _co(1), "", "x")])
    assert count == 0
    assert any("itself" in e for e in errors)
    assert proj.created == []


def test_link_co_co_uses_per_pair_address_and_name():
    proj = FakeProject()
    _plugin(proj).mass_link_objects(
        [(_co(1), _co(2), "1/1/1", "a"), (_co(3), _co(4), "1/1/2", "b")]
    )
    assert proj.created == [("1/1/1", "a"), ("1/1/2", "b")]


def test_link_co_co_empty_address_is_auto():
    proj = FakeProject()
    count, errors = _plugin(proj).mass_link_objects([(_co(1, "obj"), _co(2), "", "")])
    assert count == 1
    assert errors == []
    assert proj.created == [
        (None, "obj")
    ]  # empty address -> auto, empty name -> source name


def test_link_co_co_skips_duplicate_address_in_batch():
    proj = FakeProject()
    count, errors = _plugin(proj).mass_link_objects(
        [(_co(1), _co(2), "1/1/1", "a"), (_co(3), _co(4), "1/1/1", "b")]
    )
    assert count == 1  # only the first; the duplicate is skipped, not half-applied
    assert proj.created == [("1/1/1", "a")]
    assert any("twice" in e for e in errors)


def test_autopair_name_and_dpt_constraint():
    from editor_gui.plugins.project.ui.mass_linker import autopair

    # (ga_id, name, major)
    gas = [(1, "Kitchen light", 1), (2, "Kitchen light", 3), (3, "Temperature", 9)]
    # object major 1 -> picks the name match with matching major (id 1), ready
    assert autopair("Kitchen light", 1, gas) == (1, "ready")
    # object major 5, only wrong-major name matches -> blocked
    assert autopair("Kitchen light", 5, gas) == (None, "incompatible")
    # unknown GA major among matches -> ambiguous (assigns it)
    assert autopair("Temperature", 9, [(3, "Temperature", None)]) == (3, "ambiguous")
    # unknown object major -> assign first name match, ambiguous
    assert autopair("Kitchen light", None, gas) == (1, "ambiguous")
    # no name match -> unmatched
    assert autopair("Blinds", 1, gas) == (None, "unmatched")


def test_autopair_edge_cases_from_review():
    from editor_gui.plugins.project.ui.mass_linker import autopair

    # blank GA name must not match everything.
    assert autopair("Light", 1, [(9, "", 1)]) == (None, "unmatched")
    # short object name: no substring matching (would otherwise match half the project).
    assert autopair("on", 1, [(1, "on/off switch", 1)]) == (None, "unmatched")
    # several equally valid compatible targets -> ambiguous (assigns the first).
    assert autopair("Light", 1, [(1, "Light", 1), (2, "Light", 1)]) == (1, "ambiguous")


def _panel_with_target(ga_id: int) -> MassLinkerPanel:
    panel = MassLinkerPanel(
        get_devices=list,
        get_range_tree=list,
        is_open=lambda: True,
        on_link_ga_co=lambda pairs: (0, []),
        on_link_co_co=lambda pairs: (0, []),
        get_selected_node_id=lambda: None,
        group_style=lambda: GroupAddressStyle.THREE_LEVEL,
    )
    co = SimpleNamespace(dpt=SimpleNamespace(major=1, minor=1), name="Light")
    row = _Row("1.1.1  Device", co)  # type: ignore[arg-type]
    row.target_ga = ga_id
    panel._rows1.append(row)  # pyright: ignore[reportPrivateUsage]
    return panel


def test_targets_stay_while_the_group_addresses_cannot_be_read():
    # A busy project (e.g. rebuilding its devices after a language switch) returns no group
    # addresses for a while; that must not clear the rows' targets.
    panel = _panel_with_target(7)
    panel._refresh_statuses({})  # pyright: ignore[reportPrivateUsage]
    assert panel._rows1[0].target_ga == 7  # pyright: ignore[reportPrivateUsage]


def test_a_deleted_target_is_cleared():
    panel = _panel_with_target(7)
    other = _FlatGa(8, 0x0808, "1/0/8", "Other", 1)
    panel._refresh_statuses({8: other})  # pyright: ignore[reportPrivateUsage]
    row = panel._rows1[0]  # pyright: ignore[reportPrivateUsage]
    assert (row.target_ga, row.status) == (None, "unmatched")
