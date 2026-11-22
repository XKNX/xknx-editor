"""Members of a ZIP copied as they are stored read back octet for octet the same."""

from __future__ import annotations

import io
import zipfile
from pathlib import Path

import pytest

from xknxeditor.proj.core.zip_copy import copy_members

_MEMBERS = {
    "M-0083/M-0083_A-0001-01-0000.xml": b"<KNX>" + b"application " * 5000 + b"</KNX>",
    "M-0083/Baggages/icon.png": bytes(range(256)) * 40,
    "M-0083/Hardware.xml": b"<Hardware/>",
    "M-0083/Räume.xml": b"<Spaces/>",
    "knx_master.xml": b"<Master/>",
}


def _source(path: Path) -> Path:
    with zipfile.ZipFile(path, "w") as out:
        for n, (name, data) in enumerate(_MEMBERS.items()):
            info = zipfile.ZipInfo(name, date_time=(2024, 5, 17, 13, 45, 30))
            info.compress_type = zipfile.ZIP_STORED if n == 1 else zipfile.ZIP_DEFLATED
            out.writestr(info, data)
    return path


def test_the_copied_members_read_back_the_same(tmp_path: Path) -> None:
    names = [name for name in _MEMBERS if name.startswith("M-")]
    with zipfile.ZipFile(_source(tmp_path / "source.zip")) as source:
        copied = copy_members(source, names)
        stored = {name: source.getinfo(name) for name in names}
    assert copied is not None
    with zipfile.ZipFile(io.BytesIO(copied)) as result:
        assert result.testzip() is None
        assert result.namelist() == names
        for name in names:
            info = result.getinfo(name)
            assert result.read(name) == _MEMBERS[name]
            # As stored: the same compression, compressed size and time.
            assert info.compress_type == stored[name].compress_type
            assert info.compress_size == stored[name].compress_size
            assert info.date_time == stored[name].date_time


class _Unseekable(io.RawIOBase):
    """A stream written without seeking: a ZIP written to it gives each member's sizes after
    its data (a data descriptor)."""

    def __init__(self) -> None:
        self.data = bytearray()

    def writable(self) -> bool:
        return True

    def write(self, b: object) -> int:
        chunk = bytes(b)  # type: ignore[call-overload]
        self.data += chunk
        return len(chunk)


def test_members_written_with_a_data_descriptor_are_copied(tmp_path: Path) -> None:
    stream = _Unseekable()
    with zipfile.ZipFile(stream, "w", zipfile.ZIP_DEFLATED) as out:
        out.writestr("M-0001/streamed.xml", b"<stream/>" * 1000)
    path = tmp_path / "streamed.zip"
    path.write_bytes(bytes(stream.data))
    with zipfile.ZipFile(path) as source:
        assert source.getinfo("M-0001/streamed.xml").flag_bits & 0x8
        copied = copy_members(source, ["M-0001/streamed.xml"])
    assert copied is not None
    with zipfile.ZipFile(io.BytesIO(copied)) as result:
        assert result.read("M-0001/streamed.xml") == b"<stream/>" * 1000


def test_an_encrypted_member_is_left_to_the_caller(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with zipfile.ZipFile(_source(tmp_path / "source.zip")) as source:
        info = source.getinfo("M-0083/Hardware.xml")
        monkeypatch.setattr(info, "flag_bits", info.flag_bits | 0x1)
        assert copy_members(source, ["M-0083/Hardware.xml"]) is None
