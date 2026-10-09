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
