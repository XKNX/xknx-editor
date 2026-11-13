"""Reference archives are optional; damaged supplied evidence is an error."""

import zipfile

import pytest

from packages.vendor.tests.powertool_fixtures import archive_cases, open_archive


def test_missing_reference_archive(monkeypatch, tmp_path):
    monkeypatch.setenv("XKNX_POWERTOOL_FIXTURES", str(tmp_path))
    assert archive_cases("original-images.zip", manifest=False) == []
    with pytest.raises(pytest.skip.Exception, match="reference archive unavailable"):
        open_archive("original-images.zip")


def test_corrupt_reference_archive_is_not_skipped(monkeypatch, tmp_path):
    monkeypatch.setenv("XKNX_POWERTOOL_FIXTURES", str(tmp_path))
    (tmp_path / "original-images.zip").write_bytes(b"broken")
    with pytest.raises(zipfile.BadZipFile):
        archive_cases("original-images.zip", manifest=False)
