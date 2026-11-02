"""Reading a single application XML from a .knxprod archive."""

from pathlib import Path

from xknxeditor.prod.archive import Archive

_FIXTURE = Path(__file__).parent / "fixtures" / "gira_2gang_button_interface.knxprod"


def test_get_application_xml_reads_one_application() -> None:
    with Archive(str(_FIXTURE)) as archive:
        mid = sorted(archive.manufacturer_ids)[0]
        all_xmls = archive.get_application_xmls(mid)
        assert all_xmls
        for app_id, xml in all_xmls.items():
            assert archive.get_application_xml(mid, app_id) == xml


def test_get_application_xml_unknown_or_invalid_id() -> None:
    with Archive(str(_FIXTURE)) as archive:
        mid = sorted(archive.manufacturer_ids)[0]
        assert archive.get_application_xml(mid, f"{mid}_A-FFFF-FF-0000") is None
        assert archive.get_application_xml(mid, "Hardware") is None
        assert archive.get_application_xml(mid, f"../{mid}_A-0001-10-0000") is None


def _moved(application_folder: str) -> bytes:
    """The fixture with its application program in ``application_folder`` (a manufacturer folder
    without Hardware.xml and Catalog.xml)."""
    import io
    import zipfile

    buffer = io.BytesIO()
    with (
        zipfile.ZipFile(_FIXTURE) as source,
        zipfile.ZipFile(buffer, "w") as target,
    ):
        for name in source.namelist():
            data = source.read(name)
            if "_A-" in name:
                name = f"{application_folder}/{name.split('/', 1)[1]}"
            target.writestr(name, data)
    return buffer.getvalue()


def test_a_manufacturer_with_application_programs_only() -> None:
    from xknxeditor.prod.loader import load

    data = _moved("M-000A")
    with Archive(data) as archive:
        assert archive.manufacturer_ids == {"M-0008", "M-000A"}
        assert not archive.has_hardware("M-000A")
        assert not archive.has_catalog("M-000A")
        assert archive.has_hardware("M-0008")
    registry = load(data)
    (application_id,) = registry.applications
    assert application_id.startswith("M-0008_A-7072-21-")
    assert registry.manufacturer_to_hardware["M-000A"] == []
    assert any(
        application_id in program.application_ref_ids
        for program in registry.programs.values()
    )


def test_hardware_still_needs_its_catalog() -> None:
    import io
    import zipfile

    import pytest

    from xknxeditor.prod.errors import ArchiveError

    buffer = io.BytesIO()
    with (
        zipfile.ZipFile(_FIXTURE) as source,
        zipfile.ZipFile(buffer, "w") as target,
    ):
        for name in source.namelist():
            if not name.endswith("Catalog.xml"):
                target.writestr(name, source.read(name))
    with pytest.raises(ArchiveError, match=r"Catalog.xml"):
        Archive(buffer.getvalue())
    with Archive(buffer.getvalue(), require_catalog=False) as archive:
        assert archive.has_hardware("M-0008")
