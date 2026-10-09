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
    return _UnionCompetitors(members, member_refs)


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
