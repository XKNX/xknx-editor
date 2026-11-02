"""End to end: a small synthetic ``.vd5`` file to a signed ``.knxprod`` the loader reads."""

from __future__ import annotations

import io
import threading
import zipfile
from pathlib import Path
from typing import Any

import pytest

from xknxeditor.prod.errors import ImportCancelled
from xknxeditor.prod.loader import load
from xknxeditor.prod.vd import vd_to_knxprod

from .test_coverage_helpers import Tables, base, com_object, param, ptype
from .test_dump import _PW, _zipcrypto  # pyright: ignore[reportPrivateUsage]

# a complete master data file (the loader validates it), from a package in the repository
_FIXTURE = (
    Path(__file__).parent.parent / "fixtures" / "gira_2gang_button_interface.knxprod"
)


@pytest.fixture(scope="module")
def master() -> bytes:
    with zipfile.ZipFile(_FIXTURE) as zf:
        return zf.read("knx_master.xml")


def _kind(values: list[Any]) -> int:
    for v in values:
        if isinstance(v, bytes):
            return 8
        if isinstance(v, str):
            return 3
    return 1


def _lines(v: Any) -> list[str]:
    """One dump value: hex for bytes, wrapped into continuation lines after 40 characters."""
    if v is None:
        return [""]
    s = v.hex().upper() if isinstance(v, bytes) else str(v)
    out = [s[:40]]
    rest = s[40:]
    while rest:
        out.append("\\\\" + rest[:38])
        rest = rest[38:]
    return out


def ex_im(tables: Tables) -> bytes:
    """The EX-IM dump (schema 6.3) of ``tables``; every column nullable, the first one the key."""
    lines = ["EX-IM", "N C:/data/db.vd_", "V 6.3", "H virtual_device"]
    for number, (name, rows) in enumerate(tables.items(), start=3):
        columns: list[str] = []
        for r in rows:
            columns += [c.upper() for c in r if c.upper() not in columns]
        lines += ["-------------------------------------", f"T {number} {name}"]
        for k, c in enumerate(columns, start=1):
            values = [{key.upper(): v for key, v in r.items()}.get(c) for r in rows]
            size = 32767 if _kind(values) == 8 else 255
            lines.append(f"C{k} T{number} {_kind(values)} {size} Y {c}")
        for k, r in enumerate(rows, start=1):
            up = {key.upper(): v for key, v in r.items()}
            lines.append(f"R {k} T {number} {name}")
            for c in columns:
                lines += _lines(up.get(c))
    lines += ["XXX", ""]
    return "\r\n".join(lines).encode("cp1252")


def device_tables() -> Tables:
    t = base()
    t["parameter_type"] = [ptype(100, 1, "t_u8", 8)]
    t["parameter"] = [
        param(200, 0, "Page", None, space=0, order=1),
        param(201, 1, "Level", 100, space=2, address=0x4400, bit=0, order=2),
    ]
    t["communication_object"] = [com_object(300, 0, 1, "Switch", 0, dpt=(1, 1))]
    t["s19_block"] = [
        {
            "BLOCK_ID": 1001,
            "BLOCK_NUMBER": 1,
            "PROGRAM_ID": 40,
            "RECORD": bytes([0x0F]) + bytes(15),
            "MERGE_ID": None,
            "PROC_MASK": 0,
        }
    ]
    return t


def _vd(encrypted: bool) -> bytes:
    dump = ex_im(device_tables())
    if encrypted:
        return _zipcrypto("Data/db.vd_", dump, _PW, b"C:\\Data")
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("Data/db.vd_", dump)
    return buffer.getvalue()


@pytest.mark.parametrize("encrypted", [False, True])
def test_vd_file_to_signed_package(encrypted: bool, master: bytes) -> None:
    calls: list[tuple[int, int]] = []
    data = vd_to_knxprod(
        _vd(encrypted),
        master,
        passwords=(_PW,),
        progress=lambda done, total: calls.append((done, total)),
    )
    assert calls[-1] == (1, 1)
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        names = sorted(zf.namelist())
        assert zf.read("knx_master.xml") == master
    # signing replaces the 0000 fingerprint of the program id
    apps = [n for n in names if "_A-1234-01-" in n]
    assert len(apps) == 1
    assert not apps[0].endswith("-0000.xml")
    assert names == sorted(
        ["knx_master.xml", "M-0083/Catalog.xml", "M-0083/Hardware.xml", apps[0]]
    )
    registry = load(data)
    [app_id] = registry.applications
    assert app_id == apps[0].removeprefix("M-0083/").removesuffix(".xml")
    assert len(registry.hardware) == 1
    assert len(registry.products) == 1


def test_cancelled_conversion(master: bytes) -> None:
    cancel = threading.Event()
    cancel.set()
    with pytest.raises(ImportCancelled):
        vd_to_knxprod(_vd(False), master, cancel=cancel)
