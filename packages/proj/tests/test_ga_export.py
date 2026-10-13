"""ETS group-address export (``ga-export/01``) import: a plain .xml carrying only the group-address
tree (main/middle folders + addresses with names and datapoint types), no devices or topology. The
importer builds a new project whose GA tree mirrors the export; the rest is the default skeleton.
A small synthetic fixture is used (the real customer export is copyrighted)."""

from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from xknxeditor.proj.core.addressing import GroupAddressStyle
from xknxeditor.proj.core.ga_export import (
    import_ga_export,
    is_ga_export,
    read_ga_export,
    write_ga_export,
)
from xknxeditor.proj.db import make_engine, url_for
from xknxeditor.proj.models import GroupAddress, GroupRange, Project

_EXPORT = """<?xml version="1.0" encoding="utf-8"?>
<GroupAddress-Export xmlns="http://knx.org/xml/ga-export/01">
  <GroupRange Name="Beleuchtung" RangeStart="2048" RangeEnd="4095">
    <GroupRange Name="Schalten" RangeStart="2048" RangeEnd="2303">
      <GroupAddress Name="Licht Buero" Address="1/0/1" DPTs="DPST-1-1" Description="EG" />
      <GroupAddress Name="Licht Flur" Address="1/0/2" DPTs="DPT-1" />
      <GroupAddress Name="Ohne DPT" Address="1/0/3" />
    </GroupRange>
    <GroupRange Name="Dimmen" RangeStart="2304" RangeEnd="2559">
      <GroupAddress Name="Dimmwert" Address="1/1/0" DPTs="DPST-5-1" />
    </GroupRange>
  </GroupRange>
  <GroupRange Name="Jalousie" RangeStart="4096" RangeEnd="6143">
    <GroupRange Name="Fahren" RangeStart="4096" RangeEnd="4351">
      <GroupAddress Name="Auf/Ab" Address="2/0/0" DPTs="DPST-1-8" />
    </GroupRange>
  </GroupRange>
</GroupAddress-Export>
"""

_FREE_EXPORT = """<?xml version="1.0" encoding="utf-8"?>
<GroupAddress-Export xmlns="http://knx.org/xml/ga-export/01">
  <GroupRange Name="All" RangeStart="1" RangeEnd="65535">
    <GroupAddress Name="Free" Address="5" DPTs="DPST-1-1" />
  </GroupRange>
</GroupAddress-Export>
"""

_TWO_LEVEL_EXPORT = """<?xml version="1.0" encoding="utf-8"?>
<GroupAddress-Export xmlns="http://knx.org/xml/ga-export/01">
  <GroupRange Name="Main" RangeStart="2048" RangeEnd="4095">
    <GroupAddress Name="Two" Address="1/5" DPTs="DPST-1-1" />
  </GroupRange>
</GroupAddress-Export>
"""

# A three-level 1/8/0 overflows the 3-bit middle group and would alias onto 1/0/0, plus a DPTs token
# that is not a valid datapoint type. Both the aliased address and the bad DPT must be dropped, not
# stored as a wrong value.
_LOSSY_EXPORT = """<?xml version="1.0" encoding="utf-8"?>
<GroupAddress-Export xmlns="http://knx.org/xml/ga-export/01">
  <GroupRange Name="Main" RangeStart="2048" RangeEnd="4095">
    <GroupRange Name="Mid" RangeStart="2048" RangeEnd="2303">
      <GroupAddress Name="Good" Address="1/0/5" DPTs="garbage" />
      <GroupAddress Name="Overflow" Address="1/8/0" DPTs="DPST-1-1" />
    </GroupRange>
  </GroupRange>
</GroupAddress-Export>
"""

# An outer and inner range of exactly equal width that both contain the address: the address must land
# in the deeper (leaf) range, not the outer one.
_EQUAL_WIDTH_EXPORT = """<?xml version="1.0" encoding="utf-8"?>
<GroupAddress-Export xmlns="http://knx.org/xml/ga-export/01">
  <GroupRange Name="Outer" RangeStart="2048" RangeEnd="2303">
    <GroupRange Name="Inner" RangeStart="2048" RangeEnd="2303">
      <GroupAddress Name="Edge" Address="1/0/9" DPTs="DPST-1-1" />
    </GroupRange>
  </GroupRange>
</GroupAddress-Export>
"""


def _write(tmp_path: Path, name: str, text: str) -> Path:
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return path


def test_is_ga_export_detects_root(tmp_path: Path) -> None:
    assert is_ga_export(_write(tmp_path, "e.xml", _EXPORT)) is True
    assert is_ga_export(_write(tmp_path, "other.xml", "<Foo/>")) is False
    assert is_ga_export(_write(tmp_path, "bad.xml", "<not xml")) is False


def test_read_detects_three_level_style(tmp_path: Path) -> None:
    export = read_ga_export(_write(tmp_path, "e.xml", _EXPORT))
    assert export.style is GroupAddressStyle.THREE_LEVEL
    assert len(export.ranges) == 2
    assert len(export.addresses) == 5


def test_read_detects_free_style(tmp_path: Path) -> None:
    export = read_ga_export(_write(tmp_path, "free.xml", _FREE_EXPORT))
    assert export.style is GroupAddressStyle.FREE


def test_import_builds_mirrored_tree(tmp_path: Path) -> None:
    source = _write(tmp_path, "e.xml", _EXPORT)
    dest = tmp_path / "p.xknx"
    pid = import_ga_export(source, dest)

    engine = make_engine(url_for(dest))
    try:
        with Session(engine) as session:
            project = session.get(Project, pid)
            assert project is not None
            assert project.group_address_style == GroupAddressStyle.THREE_LEVEL.value

            ranges = session.scalars(select(GroupRange)).all()
            by_name = {r.name: r for r in ranges}
            # Two main groups, three middle groups.
            assert set(by_name) == {
                "Beleuchtung",
                "Schalten",
                "Dimmen",
                "Jalousie",
                "Fahren",
            }
            # Nesting is reproduced: a middle group's parent is its main group.
            assert by_name["Schalten"].parent is by_name["Beleuchtung"]
            assert by_name["Fahren"].parent is by_name["Jalousie"]
            assert by_name["Beleuchtung"].parent is None

            addresses = session.scalars(select(GroupAddress)).all()
            assert len(addresses) == 5
            by_addr = {a.address: a for a in addresses}
            licht = by_addr[parse_three(1, 0, 1)]
            assert licht.name == "Licht Buero"
            assert licht.datapoint_type == "DPST-1-1"
            assert licht.description == "EG"
            # A bare "1" token normalizes to the "DPT-1" form, kept verbatim.
            assert by_addr[parse_three(1, 0, 2)].datapoint_type == "DPT-1"
            # An address without a DPT keeps None rather than failing the import.
            assert by_addr[parse_three(1, 0, 3)].datapoint_type is None
            # Addresses land in the leaf range that contains them.
            assert licht.group_range is by_name["Schalten"]
            assert by_addr[parse_three(1, 1, 0)].group_range is by_name["Dimmen"]
    finally:
        engine.dispose()


def test_import_overwrites_existing_dest(tmp_path: Path) -> None:
    source = _write(tmp_path, "e.xml", _EXPORT)
    dest = tmp_path / "p.xknx"
    dest.write_text("stale", encoding="utf-8")
    pid = import_ga_export(source, dest)
    engine = make_engine(url_for(dest))
    try:
        with Session(engine) as session:
            assert session.get(Project, pid) is not None
    finally:
        engine.dispose()


def test_read_detects_two_level_style(tmp_path: Path) -> None:
    export = read_ga_export(_write(tmp_path, "two.xml", _TWO_LEVEL_EXPORT))
    assert export.style is GroupAddressStyle.TWO_LEVEL
    assert len(export.addresses) == 1
    # Two-level 1/5 encodes as (main << 11) | sub.
    assert export.addresses[0].address == (1 << 11) | 5


def test_read_skips_out_of_range_and_malformed_dpt(tmp_path: Path) -> None:
    export = read_ga_export(_write(tmp_path, "lossy.xml", _LOSSY_EXPORT))
    # The overflowing 1/8/0 is dropped; only the in-range address survives.
    assert len(export.addresses) == 1
    good = export.addresses[0]
    assert good.name == "Good"
    assert good.address == parse_three(1, 0, 5)
    # The unrecognized DPTs token becomes None rather than aborting the import.
    assert good.datapoint_type is None


def test_import_places_address_in_leaf_on_equal_width_tie(tmp_path: Path) -> None:
    source = _write(tmp_path, "eq.xml", _EQUAL_WIDTH_EXPORT)
    dest = tmp_path / "p.xknx"
    pid = import_ga_export(source, dest)
    engine = make_engine(url_for(dest))
    try:
        with Session(engine) as session:
            assert session.get(Project, pid) is not None
            addresses = session.scalars(select(GroupAddress)).all()
            assert len(addresses) == 1
            assert addresses[0].group_range.name == "Inner"
    finally:
        engine.dispose()


def test_import_uses_project_name_over_dest_stem(tmp_path: Path) -> None:
    source = _write(tmp_path, "e.xml", _EXPORT)
    # The dest is a throwaway temp name; the facade passes the real project name separately.
    dest = tmp_path / "p.import-1234.tmp"
    pid = import_ga_export(source, dest, project_name="Haus Mueller")
    engine = make_engine(url_for(dest))
    try:
        with Session(engine) as session:
            project = session.get(Project, pid)
            assert project is not None
            assert project.name == "Haus Mueller"
    finally:
        engine.dispose()


def test_import_falls_back_to_dest_stem_without_project_name(tmp_path: Path) -> None:
    source = _write(tmp_path, "e.xml", _EXPORT)
    dest = tmp_path / "Buero.xknx"
    pid = import_ga_export(source, dest)
    engine = make_engine(url_for(dest))
    try:
        with Session(engine) as session:
            project = session.get(Project, pid)
            assert project is not None
            assert project.name == "Buero"
    finally:
        engine.dispose()


def parse_three(main: int, middle: int, sub: int) -> int:
    return (main << 11) | (middle << 8) | sub


def test_write_round_trips_the_tree(tmp_path: Path) -> None:
    # Import an export into a project, write it back out, and re-read: same style, same
    # range names and the same addresses with their names/DPTs/descriptions.
    source = _write(tmp_path, "e.xml", _EXPORT)
    project = tmp_path / "p.xknx"
    import_ga_export(source, project)

    out = tmp_path / "out.xml"
    count = write_ga_export(project, out)
    assert count == 5
    assert is_ga_export(out) is True

    export = read_ga_export(out)
    assert export.style is GroupAddressStyle.THREE_LEVEL
    assert {r.name for r in export.ranges} == {"Beleuchtung", "Jalousie"}
    nested = {c.name for r in export.ranges for c in r.children}
    assert nested == {"Schalten", "Dimmen", "Fahren"}

    by_addr = {a.address: a for a in export.addresses}
    licht = by_addr[parse_three(1, 0, 1)]
    assert licht.name == "Licht Buero"
    assert licht.datapoint_type == "DPST-1-1"
    assert licht.description == "EG"
    # An address that had no DPT stays without one on the way out (no DPTs attribute).
    assert by_addr[parse_three(1, 0, 3)].datapoint_type is None


def test_write_preserves_free_style(tmp_path: Path) -> None:
    source = _write(tmp_path, "free.xml", _FREE_EXPORT)
    project = tmp_path / "p.xknx"
    import_ga_export(source, project)
    out = tmp_path / "out.xml"
    assert write_ga_export(project, out) == 1
    export = read_ga_export(out)
    assert export.style is GroupAddressStyle.FREE
    assert export.addresses[0].address == 5


def test_write_overwrites_existing_dest(tmp_path: Path) -> None:
    source = _write(tmp_path, "e.xml", _EXPORT)
    project = tmp_path / "p.xknx"
    import_ga_export(source, project)
    out = tmp_path / "out.xml"
    out.write_text("stale", encoding="utf-8")
    write_ga_export(project, out)
    assert is_ga_export(out) is True
