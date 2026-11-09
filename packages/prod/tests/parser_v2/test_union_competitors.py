"""Union members compete only when their bit ranges overlap; competitors are looked up lazily."""

from xknxeditor.prod.parser_v2.dynamic import _UnionCompetitors


def _competitors() -> _UnionCompetitors:
    members: dict[str, tuple[int, int, int | None]] = {
        "U1-A": (1, 0, 8),  # bits 0-7
        "U1-B": (1, 4, 8),  # bits 4-11, overlaps A and C
        "U1-C": (1, 8, 8),  # bits 8-15
        "U1-D": (1, 16, None),  # unknown size at bit 16
        "U1-E": (1, 16, 8),  # same start as D
        "U2-A": (2, 0, 8),  # another union at the same offsets
    }
    member_refs = {
        "U1-A": {"R-A1", "R-A2"},
        "U1-B": {"R-B"},
        "U1-C": {"R-C"},
        "U1-D": {"R-D"},
        "U1-E": {"R-E"},
        "U2-A": {"R-U2"},
    }
    order = ["R-A1", "R-A2", "R-B", "R-C", "R-D", "R-E", "R-U2"]
    return _UnionCompetitors(
        members, member_refs, {r: (0, i) for i, r in enumerate(order)}
    )


def test_overlapping_members_compete() -> None:
    c = _competitors()
    assert c.get("R-A1") == {"R-B"}
    assert c.get("R-B") == {"R-A1", "R-A2", "R-C"}
    assert c.get("R-C") == {"R-B"}


def test_unknown_size_competes_only_at_the_same_start() -> None:
    c = _competitors()
    assert c.get("R-D") == {"R-E"}
    assert c.get("R-E") == {"R-D"}


def test_no_competitors() -> None:
    c = _competitors()
    assert c.get("R-U2") is None
    assert c.get("R-unknown") is None
    assert c.get(None) is None


def test_aliases_share_one_cached_set() -> None:
    c = _competitors()
    assert c.get("R-A1") is c.get("R-A2")


def test_later_members_are_those_after_it_that_overlap() -> None:
    c = _competitors()
    assert c.later("R-A1") == {"R-B"}
    assert c.later("R-B") == {"R-C"}
    assert c.later("R-C") == set()
    assert c.later("R-unknown") == set()


def test_later_follows_the_parameter_ref_order_not_the_union_order() -> None:
    # The union lists X before Y, but Y's ref comes first: a download writes Y, then X.
    members: dict[str, tuple[int, int, int | None]] = {"X": (1, 0, 8), "Y": (1, 0, 8)}
    c = _UnionCompetitors(
        members, {"X": {"R-X"}, "Y": {"R-Y"}}, {"R-X": (0, 2), "R-Y": (0, 1)}
    )
    assert c.later("R-X") == set()
    assert c.later("R-Y") == {"R-X"}


def test_members_cover_a_member_only_with_all_of_its_bits() -> None:
    c = _competitors()
    # B writes bits 4-7 of A's 0-7; A and C together all of B's 4-11.
    assert not c.covered("R-A1", {"R-B"})
    assert not c.covered("R-B", {"R-C"})
    assert c.covered("R-B", {"R-A1", "R-C"})
    # Unknown widths: covered when one starts at the same bit.
    assert c.covered("R-D", {"R-E"})
    assert c.covered("R-E", {"R-D"})
    # Another union's member, the member itself and refs of no union cover nothing.
    assert not c.covered("R-A1", {"R-U2"})
    assert not c.covered("R-A1", {"R-A2"})
    assert not c.covered("R-unknown", {"R-B"})


def test_later_members_are_looked_up_once_per_ref() -> None:
    # A search asks for the later members of the same refs in every UI it seeds.
    c = _competitors()
    first = c.later("R-A1")
    assert c.later("R-A1") is first
    assert c.later("R-unknown") is c.later("R-unknown")
