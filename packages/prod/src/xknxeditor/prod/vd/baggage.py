"""``Baggages.xml`` and the baggage files (table ``baggage``)."""

from __future__ import annotations

import datetime as dt
import hashlib
import io
import re
import struct
import time
import zipfile
from typing import TYPE_CHECKING

from .ids import fragment
from .xmlout import El

if TYPE_CHECKING:
    from .context import Context
    from .database import Row

_TIME = re.compile(
    r"^\s*(?:(\d{4})[./-](\d{1,2})[./-](\d{1,2})|(\d{1,2})[./-](\d{1,2})[./-](\d{4}))"
    r"(?:[ T](\d{1,2}):(\d{2})(?::(\d{2})(?:\.(\d{1,7}))?)?)?"
)
_EPOCH = dt.datetime(1970, 1, 1)


class BaggageFailed(Exception):
    """A baggage the converter cannot create (it is left out)."""


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


def is_dst(local: dt.datetime) -> bool:
    """Whether daylight saving time applies at the local time ``local`` of this computer."""
    return utc_offset(local) != dt.timedelta(seconds=-time.timezone)


def now() -> dt.datetime:
    """The local time of this computer."""
    return dt.datetime.now()


def now_utc() -> dt.datetime:
    return dt.datetime.now(dt.UTC).replace(tzinfo=None)


def parse_time(text: str) -> dt.datetime | None:
    """A date and time as the converter reads it (a year last is read month first), or None."""
    m = _TIME.match(text)
    if not m:
        return None
    if m.group(1):
        y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
    else:
        mo, d, y = int(m.group(4)), int(m.group(5)), int(m.group(6))
    fraction = (m.group(10) or "").ljust(6, "0")[:6]
    try:
        return dt.datetime(
            y,
            mo,
            d,
            int(m.group(7) or 0),
            int(m.group(8) or 0),
            int(m.group(9) or 0),
            int(fraction),
        )
    except ValueError:
        return None


def xml_time(utc: dt.datetime, tick: int = 0) -> str:
    """A UTC time (and its digit of 100 ns) as the serializer writes it (fractions only where
    there are some)."""
    text = utc.strftime("%Y-%m-%dT%H:%M:%S")
    if utc.microsecond or tick:
        text += "." + f"{utc.microsecond:06d}{tick}".rstrip("0")
    return text + "Z"


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
        # the baggage folders as created (folder names do not depend on case: the first
        # spelling stays), the files in them (lower case path -> path, UTC time)
        self.folders: dict[str, str] = {}
        self.on_disk: dict[str, tuple[str, str]] = {}
        self.paths: dict[str, str] = {}
        # a list a program created (moving a baggage to its manufacturer) has no tool info
        self.tool_info = True

    def to_xml(self) -> El:
        m = El("Manufacturer", {"RefId": self.manufacturer})
        if self.baggages:
            m.sub("Baggages").children.extend(self.baggages)
        return m

    def folder(self, target_path: str) -> str:
        """The folder of ``target_path`` below ``Baggages`` as created on a file system that
        ignores case."""
        spelled = ""
        for part in re.split(r"[\\/]+", target_path):
            if not part:
                continue
            path = f"{spelled}/{part}" if spelled else part
            spelled = self.folders.setdefault(path.lower(), path)
        return spelled

    def remove(self, baggage: El) -> None:
        path = self.paths.pop(baggage["Id"], None)
        if path is not None:
            self.files.pop(path, None)
            self.on_disk.pop(path.lower(), None)


# characters a path cannot hold; the name and folder of a baggage the object model accepts
_INVALID_PATH = re.compile(r'[\x00-\x1f"<>|]')
_NAME = re.compile(r'[^"<>|:*?/\\\t\n\r]+')
_TARGET_PATH = re.compile(r'(([^"<>|:*?/\\\t\n\r]+\\)*[^"<>|:*?/\\\t\n\r]+)?')


def _rooted(path: str) -> bool:
    return path.startswith(("\\", "/")) or (len(path) >= 2 and path[1] == ":")


def _combine(first: str, second: str) -> str:
    if not first or _rooted(second):
        return second
    if first.endswith(("\\", "/", ":")):
        return first + second
    return f"{first}\\{second}"


def _dos_time(packed: int) -> dt.datetime:
    """A DOS date and time of a ZIP entry as local time, corrected like the converter's ZIP
    library."""
    if packed in (0xFFFF, 0):
        return dt.datetime(1995, 1, 1)
    t = packed & 0xFFFF
    d = (packed >> 16) & 0xFFFF
    year = 1980 + ((d & 0xFE00) >> 9)
    month = (d & 0x1E0) >> 5
    day = d & 0x1F
    hour = (t & 0xF800) >> 11
    minute = (t & 0x7E0) >> 5
    second = (t & 0x1F) * 2
    if second >= 60:
        minute += 1
        second = 0
    if minute >= 60:
        hour += 1
        minute = 0
    if hour >= 24:
        day += 1
        hour = 0
    try:
        return dt.datetime(year, month, day, hour, minute, second)
    except ValueError:
        pass
    if year == 1980 and (month == 0 or day == 0):
        try:
            return dt.datetime(1980, 1, 1, hour, minute, second)
        except ValueError:
            return dt.datetime(1980, 1, 1)
    year = min(max(year, 1980), 2030)
    month = min(max(month, 1), 12)
    day = min(max(day, 1), 28)
    try:
        return dt.datetime(year, month, day, hour, minute, second)
    except ValueError as error:
        raise BaggageFailed("Bad date/time format in the zip file") from error


def _extra_mtime(
    extra: bytes, local_extra: bytes | None, depth: int = 0
) -> tuple[bool, tuple[dt.datetime, int] | None]:
    """(times set, modification time in UTC) from the extra fields of a ZIP entry: NTFS times,
    extended timestamp or Info-ZIP Unix times. A short extended timestamp of the central
    directory makes the library read the extra fields of the local header."""
    found = False
    mtime: tuple[dt.datetime, int] | None = None
    i = 0
    while i + 3 < len(extra):
        start = i
        tag, size = struct.unpack_from("<HH", extra, i)
        i += 4
        field = extra[i : i + size]
        if tag == 0x000A:
            if size != 32 or len(field) < 32:
                raise BaggageFailed("Unexpected size for NTFS times extra field")
            if struct.unpack_from("<hh", field, 4) == (1, 24):
                (filetime,) = struct.unpack_from("<q", field, 8)
                if filetime < 0:
                    raise BaggageFailed("NTFS time out of range")
                mtime = (
                    dt.datetime(1601, 1, 1) + dt.timedelta(microseconds=filetime // 10),
                    filetime % 10,
                )
                found = True
        elif tag == 0x5455:
            if size not in (13, 9, 5):
                raise BaggageFailed(
                    "Unexpected size for extended timestamp extra field"
                )
            if size == 13 or depth > 0:
                if len(field) < size:
                    raise BaggageFailed("Truncated extended timestamp extra field")
                if field[0] & 1:
                    seconds = struct.unpack_from("<i", field, 1)[0]
                    mtime = (_EPOCH + dt.timedelta(seconds=seconds), 0)
                found = True
            elif local_extra is not None:
                more, local_mtime = _extra_mtime(local_extra, None, depth + 1)
                if more:
                    found = True
                    mtime = local_mtime if local_mtime is not None else mtime
        elif tag == 0x5855:
            if size not in (12, 8):
                raise BaggageFailed("Unexpected size for Info-ZIP Unix extra field")
            if len(field) < 8:
                raise BaggageFailed("Truncated Info-ZIP Unix extra field")
            seconds = struct.unpack_from("<i", field, 0)[0]
            mtime = (_EPOCH + dt.timedelta(seconds=seconds), 0)
            found = True
        i = start + size + 4
    return found, mtime


def _local_extra(data: bytes, info: zipfile.ZipInfo) -> bytes | None:
    offset = info.header_offset
    if offset + 30 > len(data):
        return None
    name_length, extra_length = struct.unpack_from("<HH", data, offset + 26)
    begin = offset + 30 + name_length
    return data[begin : begin + extra_length]


def entry_time(data: bytes, info: zipfile.ZipInfo) -> tuple[dt.datetime, int]:
    """The UTC modification time an extracted ZIP entry gets: the time of its extra fields, else
    its DOS time, which the library moves by an hour when daylight saving time differs between
    then and now (and the file system reads back as local time)."""
    found, mtime = _extra_mtime(info.extra, _local_extra(data, info))
    if found:
        if mtime is None:
            raise BaggageFailed("No modification time in the extra field")
        return mtime
    y, mo, d, h, mi, s = info.date_time
    packed = (((y - 1980) << 9 | mo << 5 | d) << 16) | (h << 11 | mi << 5 | s // 2)
    local = _dos_time(packed)
    if is_dst(now()) and not is_dst(local):
        local -= dt.timedelta(hours=1)
    elif not is_dst(now()) and is_dst(local):
        local += dt.timedelta(hours=1)
    return to_utc(local), 0


def _unzip(ctx: Context, data: bytes) -> tuple[bytes, tuple[dt.datetime, int]] | None:
    """The content and UTC time of the one file of a zipped baggage; None (and an error) for an
    archive of more entries."""
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            entries = zf.infolist()
            # every entry is extracted (and checked) first
            extracted: list[tuple[zipfile.ZipInfo, bytes, tuple[dt.datetime, int]]] = []
            for info in entries:
                content = b"" if info.is_dir() else zf.read(info)
                extracted.append((info, content, entry_time(data, info)))
    except BaggageFailed:
        raise
    except (
        zipfile.BadZipFile,
        NotImplementedError,
        RuntimeError,
        ValueError,
        EOFError,
    ) as e:
        raise BaggageFailed(str(e)) from e
    if len(entries) != 1:
        # (reported without id: the baggage has none yet)
        ctx.errors.append(
            "TooManyBaggagesInArchive: The zipped archive in database contains more than one"
            " file!"
        )
        return None
    info, content, mtime = extracted[0]
    if info.is_dir():
        raise BaggageFailed("The archive holds a folder")
    return content, mtime


def append_baggage(ctx: Context, doc: BaggageDoc, row: Row) -> None:
    try:
        _append_baggage(ctx, doc, row)
    except BaggageFailed as error:
        ctx.errors.append(
            f"CreatingBaggageFailed: Creating Baggage failed! TargetPath:"
            f" {row['LOCALFILEDIR']}, LocalFileName: {row['LOCALFILENAME']}, {error}"
        )


def _append_baggage(ctx: Context, doc: BaggageDoc, row: Row) -> None:
    from .pefile import file_version

    name = row.str("LOCALFILENAME") or ""
    path = row.str("LOCALFILEDIR") or ""
    if not _NAME.fullmatch(name):
        raise BaggageFailed("Value does not match pattern (Name)")
    if _INVALID_PATH.search(path):
        raise BaggageFailed("Illegal characters in path")
    if _rooted(path):
        path = ""
    path = path.rstrip("\\")
    if not _TARGET_PATH.fullmatch(path):
        raise BaggageFailed("Value does not match pattern (TargetPath)")
    baggage = El("Baggage", {"TargetPath": path, "Name": name})
    info = baggage.sub("FileInfo")
    time_value = row["LOCALFILETIME"]
    utc: dt.datetime | None = None
    if time_value is not None:
        local = parse_time(str(time_value))
        if local is None:
            raise BaggageFailed(
                f"String was not recognized as a valid DateTime: {time_value}"
            )
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
    target = _combine(path, name)
    for other in doc.baggages:
        if _combine(other["TargetPath"], other["Name"]).lower() != target.lower():
            continue
        other_info = other.find("FileInfo")
        other_version = other_info.get("Version") if other_info is not None else None
        if _version_key(other_version) >= _version_key(version):
            ctx.add_refid(dbid, other["Id"])
            return
        doc.baggages.remove(other)
        ctx.add_refid(dbid, other["Id"])
        doc.remove(other)
        break
    baggage["Id"] = f"{doc.manufacturer}_BG-{fragment(path)}-{fragment(name)}"
    folder = doc.folder(path)
    file_path = (
        f"{doc.manufacturer}/Baggages/{folder}/{name}"
        if folder
        else f"{doc.manufacturer}/Baggages/{name}"
    )
    existing = doc.on_disk.get(file_path.lower())
    content: bytes | None = None
    file_time: str | None = None
    compression = row["COMPRESSION"]
    data = row["DATA"]
    if compression is None:
        if existing is not None:
            raise BaggageFailed(f"The file '{file_path}' already exists.")
        content = data if isinstance(data, bytes) else b""
        # a new file without time has the time it is written
        file_time = xml_time(utc) if utc is not None else xml_time(now_utc())
    elif str(compression).upper() == "Z":
        if not isinstance(data, bytes):
            raise BaggageFailed("Unable to cast object of type 'System.DBNull'")
        unzipped = _unzip(ctx, data)
        if unzipped is not None:
            if existing is None:
                content = unzipped[0]
                file_time = xml_time(*unzipped[1])
            else:
                # a file of the same path stays
                file_path, file_time = existing
                content = doc.files.get(file_path)
    if content is None:
        # no file was created: its integrity cannot be computed
        raise BaggageFailed(f"Could not find file '{file_path}'.")
    if version is None:
        version = file_version(name, content)
    if version is not None:
        info["Version"] = version
    info["TimeInfo"] = xml_time(utc) if utc is not None else file_time or ""
    if hidden:
        info["Hidden"] = "true"
    if readonly:
        info["ReadOnly"] = "true"
    integrity = file_integrity(name, content)
    if integrity is not None:
        baggage["FileIntegrity"] = integrity
    doc.baggages.append(baggage)
    ctx.add_refid(dbid, baggage["Id"])
    doc.files[file_path] = content
    doc.on_disk[file_path.lower()] = (file_path, file_time or info["TimeInfo"])
    doc.paths[baggage["Id"]] = file_path


def convert_baggages(ctx: Context) -> list[BaggageDoc]:
    from .context import sort_rows

    docs: list[BaggageDoc] = []
    doc: BaggageDoc | None = None
    current = -1
    for row in sort_rows(ctx.rows("baggage"), "MANUFACTURERID"):
        number = int(row["MANUFACTURERID"] or 0)
        if not ctx.known_manufacturer(ctx.manufacturer_ref(number)):
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


# programs that refer to baggages of their original manufacturer: the converter moves these to
# the program's manufacturer (old, new manufacturer, number the other users are compared with)
FOREIGN_BAGGAGES = {
    "M-0004_A-C00A-02-0000-O0001": ("M-0001", "M-0004", 4),
    "M-0019_A-5700-11-0000-O000C": ("M-000C", "M-0019", 25),
    "M-0009_A-2F01-34-0000-O0002": ("M-0002", "M-0009", 9),
}


def _doc(ctx: Context, manufacturer: str) -> BaggageDoc | None:
    return next(
        (d for d in ctx.baggage_docs if d.manufacturer.lower() == manufacturer.lower()),
        None,
    )


def _in_folder(doc: BaggageDoc, folder: str) -> tuple[list[str], bool]:
    """The files directly in a folder below ``Baggages`` and whether it has sub folders."""
    base = (
        f"{doc.manufacturer}/Baggages/{folder}/"
        if folder
        else f"{doc.manufacturer}/Baggages/"
    )
    files: list[str] = []
    nested = False
    for path in doc.files:
        if path.lower().startswith(base.lower()):
            if "/" in path[len(base) :]:
                nested = True
            else:
                files.append(path)
    return files, nested


def move_to_manufacturer(
    ctx: Context, ref: str, old: str, new: str, must_stay: bool, owner: str
) -> str:
    """Moves (or copies, when other programs need it where it is) the baggage ``ref`` from
    manufacturer ``old`` to ``new``; returns the reference for the program of ``owner``.

    Like the converter, which works on the files written so far, this fails (raising
    ValueError, so the program fails) where the converter's file operations fail: the old
    baggage folder left with other folders, a copy that exists already."""
    found = next(
        ((d, b) for d in ctx.baggage_docs for b in d.baggages if b.get("Id") == ref),
        None,
    )
    if found is None:
        raise ValueError("Object reference not set to an instance of an object.")
    holder, baggage = found
    old_doc = _doc(ctx, old)
    if old_doc is None:
        raise ValueError(f"Could not find a part of the path '{old}'.")
    target_path = baggage.get("TargetPath") or ""
    name = baggage.get("Name") or ""
    parts = [p for p in re.split(r"[\\/]+", target_path) if p]
    old_folder = old_doc.folders.get("/".join(parts).lower(), "/".join(parts))
    source = f"{old}/Baggages/{old_folder + '/' if old_folder else ''}{name}"
    source_file = old_doc.on_disk.get(source.lower())
    new_doc = _doc(ctx, new)
    if new_doc is None:
        new_doc = BaggageDoc(new)
        new_doc.tool_info = False
    new_folder = new_doc.folder(target_path)
    dest = f"{new}/Baggages/{new_folder + '/' if new_folder else ''}{name}"
    if must_stay or dest.lower() not in new_doc.on_disk:
        if source_file is None:
            raise ValueError(f"Could not find file '{source}'.")
        if must_stay and dest.lower() in new_doc.on_disk:
            raise ValueError(f"The file '{dest}' already exists.")
        new_doc.files[dest] = old_doc.files[source_file[0]]
        new_doc.on_disk[dest.lower()] = (dest, source_file[1])
    if not must_stay and source_file is not None:
        old_doc.files.pop(source_file[0], None)
        old_doc.on_disk.pop(source.lower(), None)
    if new_doc not in ctx.baggage_docs:
        ctx.baggage_docs.append(new_doc)
    copy = El("Baggage", {"TargetPath": target_path, "Name": name})
    info = baggage.find("FileInfo")
    if info is not None:
        copy.children.append(El("FileInfo", dict(info.attrs)))
    copy["Id"] = f"{new}_BG-{fragment(target_path)}-{fragment(name)}"
    if not must_stay:
        holder.baggages.remove(baggage)
        holder.paths.pop(ref, None)
        if not holder.baggages:
            ctx.baggage_docs.remove(holder)
    if not any(
        b.get("Name") == name and b.get("TargetPath") == target_path
        for b in new_doc.baggages
    ):
        new_doc.baggages.append(copy)
        new_doc.paths[copy["Id"]] = dest
    # the folder of the baggage goes when it has no files, then the baggages folder (and
    # ``Baggages.xml``) of the old manufacturer when that has none; a folder that still holds
    # folders cannot be deleted
    files, nested = _in_folder(old_doc, old_folder)
    if not files and nested:
        raise ValueError("The directory is not empty.")
    if old_folder:
        files, nested = _in_folder(old_doc, "")
        if not files:
            # (the parents of a nested folder stay)
            if nested or "/" in old_folder:
                raise ValueError("The directory is not empty.")
            if old_doc in ctx.baggage_docs:
                ctx.baggage_docs.remove(old_doc)
    return ref.replace(ref[:6], owner)


def referenced_elsewhere(ctx: Context, baggage: int, manufacturer: int) -> bool:
    """Whether a program of another manufacturer uses the baggage (database id)."""
    for use in ctx.select("applicationprogrambaggage", "BAGGAGEID", baggage):
        row = ctx.first(
            "application_program", "PROGRAM_ID", use["APPLICATIONPROGRAMID"]
        )
        if row is None:
            raise ValueError("Object reference not set to an instance of an object.")
        if int(row["MANUFACTURER_ID"] or 0) != manufacturer:
            return True
    return False
