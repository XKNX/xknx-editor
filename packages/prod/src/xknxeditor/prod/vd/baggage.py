"""``Baggages.xml`` and the baggage files (table ``baggage``)."""

from __future__ import annotations

import datetime as dt
import hashlib
import io
import re
import zipfile
from typing import TYPE_CHECKING

from .ids import fragment
from .xmlout import El

if TYPE_CHECKING:
    from .context import Context
    from .database import Row

_TIME = re.compile(
    r"^\s*(\d{4})-(\d{1,2})-(\d{1,2})(?:[ T](\d{1,2}):(\d{2})(?::(\d{2}))?)?"
)


def to_utc(local: dt.datetime) -> dt.datetime:
    """A file time of the database, read as local time of this computer (as the converter reads
    it), as UTC."""
    return local - utc_offset(local)


def utc_offset(local: dt.datetime) -> dt.timedelta:
    """The offset of this computer's time zone at ``local``; none where the platform cannot tell
    (times before 1970 on Windows)."""
    try:
        return local.astimezone().utcoffset() or dt.timedelta()
    except (OSError, OverflowError, ValueError):
        return dt.timedelta()


def parse_time(text: str) -> dt.datetime | None:
    m = _TIME.match(text)
    if not m:
        return None
    try:
        return dt.datetime(
            int(m.group(1)),
            int(m.group(2)),
            int(m.group(3)),
            int(m.group(4) or 0),
            int(m.group(5) or 0),
            int(m.group(6) or 0),
        )
    except ValueError:
        return None


def baggage_version(hi: int, lo: int) -> str:
    return f"{hi // 65536}.{hi % 65536}.{lo // 65536}.{lo % 65536}"


def _version_key(version: str | None) -> tuple[int, ...]:
    if not version:
        return ()
    parts: list[int] = []
    for p in version.split("."):
        try:
            parts.append(int(p))
        except ValueError:
            parts.append(0)
    return tuple(parts)


def file_integrity(name: str, data: bytes) -> str | None:
    """The integrity value of program files (MD5 folded to four bytes)."""
    if not name.lower().endswith((".msi", ".exe", ".dll")):
        return None
    digest = hashlib.md5(data).digest()
    folded = bytes(
        digest[i] ^ digest[i + 4] ^ digest[i + 8] ^ digest[i + 12] for i in range(4)
    )
    return folded.hex().upper()


class BaggageDoc:
    def __init__(self, manufacturer: str) -> None:
        self.manufacturer = manufacturer
        self.baggages: list[El] = []
        self.files: dict[str, bytes] = {}

    def to_xml(self) -> El:
        m = El("Manufacturer", {"RefId": self.manufacturer})
        if self.baggages:
            m.sub("Baggages").children.extend(self.baggages)
        return m


def _content(row: Row) -> tuple[bytes | None, dt.datetime | None]:
    data = row["DATA"]
    if not isinstance(data, bytes):
        data = None
    compression = row["COMPRESSION"]
    if compression is None:
        return data, None
    if str(compression).upper() != "Z" or data is None:
        return None, None
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        entries = [i for i in zf.infolist() if not i.is_dir()]
        if len(entries) != 1:
            return None, None
        info = entries[0]
        return zf.read(info), dt.datetime(*info.date_time)


def append_baggage(ctx: Context, doc: BaggageDoc, row: Row) -> None:
    from .pefile import file_version

    name = row.str("LOCALFILENAME") or ""
    path = row.str("LOCALFILEDIR") or ""
    if re.match(r"^([A-Za-z]:)?[\\/]", path):
        path = ""
    path = path.rstrip("\\")
    baggage = El("Baggage", {"TargetPath": path, "Name": name})
    info = baggage.sub("FileInfo")
    time = row["LOCALFILETIME"]
    utc: dt.datetime | None = None
    if time is not None:
        local = parse_time(str(time))
        if local is not None:
            utc = to_utc(local)
    hi = int(row["LOCALFILEVERSIONHI"] or 0)
    lo = int(row["LOCALFILEVERSIONLO"] or 0)
    version: str | None = None
    if hi != 0 or lo != 0:
        version = baggage_version(hi, lo)
    attributes = row["LOCALFILEATTRIBUTES"]
    hidden = readonly = False
    if attributes is not None:
        hidden = bool(int(attributes) & 2)
        readonly = bool(int(attributes) & 1)
    dbid = int(row["ID"] or 0)
    target = f"{path}\\{name}" if path else name
    for other in doc.baggages:
        other_target = (
            f"{other['TargetPath']}\\{other['Name']}"
            if other["TargetPath"]
            else other["Name"]
        )
        if other_target.lower() != target.lower():
            continue
        other_info = other.find("FileInfo")
        other_version = other_info.get("Version") if other_info is not None else None
        if _version_key(other_version) >= _version_key(version):
            ctx.add_refid(dbid, other["Id"])
            return
        doc.baggages.remove(other)
        ctx.add_refid(dbid, other["Id"])
        doc.files.pop(_file_path(doc.manufacturer, other), None)
        break
    content, entry_time = _content(row)
    if utc is None and entry_time is not None:
        utc = to_utc(entry_time)
    if content is not None and version is None:
        version = file_version(name, content)
    if version is not None:
        info["Version"] = version
    if utc is not None:
        info["TimeInfo"] = utc.strftime("%Y-%m-%dT%H:%M:%SZ")
    if hidden:
        info["Hidden"] = "true"
    if readonly:
        info["ReadOnly"] = "true"
    if content is not None:
        integrity = file_integrity(name, content)
        if integrity is not None:
            baggage["FileIntegrity"] = integrity
    baggage["Id"] = f"{doc.manufacturer}_BG-{fragment(path)}-{fragment(name)}"
    doc.baggages.append(baggage)
    ctx.add_refid(dbid, baggage["Id"])
    if content is not None:
        doc.files[_file_path(doc.manufacturer, baggage)] = content


def _file_path(manufacturer: str, baggage: El) -> str:
    path = baggage["TargetPath"].replace("\\", "/")
    name = baggage["Name"]
    return (
        f"{manufacturer}/Baggages/{path}/{name}"
        if path
        else f"{manufacturer}/Baggages/{name}"
    )


def convert_baggages(ctx: Context) -> list[BaggageDoc]:
    from .context import sort_rows

    docs: list[BaggageDoc] = []
    doc: BaggageDoc | None = None
    current = -1
    for row in sort_rows(ctx.rows("baggage"), "MANUFACTURERID"):
        number = int(row["MANUFACTURERID"] or 0)
        if ctx.master.manufacturer(number) is None:
            ctx.errors.append(
                f"UnknownManufacturerId: Baggage {row['LOCALFILEDIR']}/{row['LOCALFILENAME']}"
            )
            continue
        if doc is None or number != current:
            doc = BaggageDoc(ctx.manufacturer_ref(number))
            docs.append(doc)
            current = number
        append_baggage(ctx, doc, row)
    return docs
