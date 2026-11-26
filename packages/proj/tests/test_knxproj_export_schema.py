"""The schema of a KNX XML as the export reads it: from the root element only."""

from __future__ import annotations

from xknxeditor.proj.core.knxproj_export import (
    _heal_manufacturer_member,  # pyright: ignore[reportPrivateUsage]
    _provenance_schema,  # pyright: ignore[reportPrivateUsage]
    _schema_from_knx_xml,  # pyright: ignore[reportPrivateUsage]
    _schema_incompatible_folders,  # pyright: ignore[reportPrivateUsage]
)


def _knx(schema: str, body: bytes = b"<ManufacturerData/>") -> bytes:
    return (
        b'<?xml version="1.0" encoding="utf-8"?>\n'
        + f'<KNX xmlns="http://knx.org/xml/project/{schema}" CreatedBy="ETS6">'.encode()
        + body
        + b"</KNX>"
    )


def test_schema_from_root_namespace() -> None:
    assert _schema_from_knx_xml(_knx("20")) == "20"
    assert _schema_from_knx_xml(_knx("23")) == "23"


def test_schema_with_byte_order_mark_and_other_encodings() -> None:
    assert _schema_from_knx_xml(b"\xef\xbb\xbf" + _knx("21")) == "21"
    text = (
        '<?xml version="1.0" encoding="utf-16"?>'
        '<KNX xmlns="http://knx.org/xml/project/14"><MasterData/></KNX>'
    )
    assert _schema_from_knx_xml(text.encode("utf-16")) == "14"


def test_schema_after_comments_and_processing_instructions() -> None:
    data = (
        b'<?xml version="1.0"?>\n<!-- generated -->\n<?tool x?>\n'
        b'<KNX xmlns="http://knx.org/xml/project/20"/>'
    )
    assert _schema_from_knx_xml(data) == "20"


def test_no_schema_without_a_knx_project_namespace() -> None:
    assert _schema_from_knx_xml(b'<?xml version="1.0"?><KNX/>') is None
    assert _schema_from_knx_xml(b'<KNX xmlns="http://example.com/x/20"/>') is None
    assert _schema_from_knx_xml(b"") is None
    assert _schema_from_knx_xml(b"not xml at all") is None
    assert _schema_from_knx_xml(b'<?xml version="1.0"?>\n<KN') is None


def test_schema_of_a_root_split_over_read_chunks() -> None:
    """A root tag that only ends after the first chunk the reader takes is still found."""
    attributes = b" ".join(b'A%d="%s"' % (i, b"x" * 100) for i in range(2000))
    data = (
        b'<?xml version="1.0"?>\n<KNX xmlns="http://knx.org/xml/project/23" '
        + attributes
        + b"/>"
    )
    assert len(data) > 128 * 1024
    assert _schema_from_knx_xml(data) == "23"


def test_schema_is_read_from_the_head_only() -> None:
    """The body is not parsed: a product XML of tens of MB costs only its first bytes, and a
    member broken further down still has its root's schema."""
    assert _schema_from_knx_xml(_knx("20", b"<Broken><Unclosed>")) == "20"
    huge = _knx("20", b"<P/>" * 5_000_000)
    assert _schema_from_knx_xml(huge) == "20"


def test_heal_converts_an_older_member_up() -> None:
    healed = _heal_manufacturer_member(_knx("20"), "23")
    assert b'xmlns="http://knx.org/xml/project/23"' in healed
    assert _heal_manufacturer_member(_knx("23"), "23").count(b"project/23") == 1


def test_newer_members_are_reported() -> None:
    members = {
        "M-0001/M-0001_A-1.xml": _knx("23"),
        "M-0002/M-0002_A-2.xml": _knx("20"),
        "M-0003/Catalog.xml": b"not xml",
    }
    assert _schema_incompatible_folders(members, "21") == ["M-0001"]


def test_provenance_skips_a_malformed_master() -> None:
    """A stored master that is not well-formed does not decide the schema: the Hardware.xml of
    the stored members does."""
    malformed = (
        b'<?xml version="1.0"?>\n<KNX xmlns="http://knx.org/xml/project/20"><MasterData'
    )
    members = {"M-0001/Hardware.xml": _knx("23")}
    assert _provenance_schema(malformed, members) == "23"
    assert _provenance_schema(_knx("20", b"<MasterData/>"), members) == "20"
