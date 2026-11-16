"""64 by 64 monochrome symbols and their project payloads."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from io import BytesIO
from zipfile import BadZipFile, ZipFile, ZipInfo

from PIL import Image

from .profiles import DcaProfile

PIXELS = 64 * 64
PAYLOAD_SIZE = 514
MAX_LIBRARY_IMAGE_SIZE = 1024 * 1024


def import_symbol_library(
    archive: bytes, profile: DcaProfile, *, invert: bool = False
) -> tuple[Symbol, ...]:
    """Read a complete library of numbered BMPs without extracting files."""
    if profile.kind != "symbols" or type(invert) is not bool:
        raise ValueError("Invalid symbol library configuration")
    expected = {
        f"symbol_{slot + 1}.bmp": slot
        for slot in range(profile.first_custom_slot, profile.slots)
    }
    try:
        with ZipFile(BytesIO(archive)) as source:
            members: dict[str, ZipInfo] = {}
            for member in source.infolist():
                name = member.filename.lower()
                if name not in expected:
                    continue
                if name in members:
                    raise ValueError(f"Duplicate library symbol: {member.filename}")
                if member.file_size > MAX_LIBRARY_IMAGE_SIZE:
                    raise ValueError(f"Library symbol is too large: {member.filename}")
                members[name] = member
            result: list[Symbol] = []
            for name, slot in expected.items():
                if name not in members:
                    raise ValueError(f"Missing library symbol: Symbol_{slot + 1}.bmp")
                with source.open(members[name]) as bitmap_file:
                    data = bitmap_file.read(MAX_LIBRARY_IMAGE_SIZE + 1)
                if len(data) > MAX_LIBRARY_IMAGE_SIZE:
                    raise ValueError(f"Library symbol is too large: {name}")
                try:
                    with Image.open(BytesIO(data), formats=["BMP"]) as bitmap:
                        if bitmap.size != (64, 64):
                            raise ValueError("A symbol image must be 64 by 64 pixels")
                        bitmap.load()
                except (OSError, Image.DecompressionBombError) as exc:
                    raise ValueError(f"Library symbol must be a BMP: {name}") from exc
                pixels = import_symbol_image(data)
                if invert:
                    pixels = bytes(value ^ 255 for value in pixels)
                result.append(Symbol(slot, pixels))
            return tuple(result)
    except (BadZipFile, OSError, RuntimeError, NotImplementedError) as exc:
        raise ValueError(f"Invalid symbol library: {exc}") from exc


def export_symbol_library(symbols: Mapping[int, bytes], profile: DcaProfile) -> bytes:
    """Export effective device pixels as a complete library of numbered BMPs."""
    if profile.kind != "symbols":
        raise ValueError("Device has no symbol library")
    stream = BytesIO()
    with ZipFile(stream, "w") as archive:
        for slot in range(profile.first_custom_slot, profile.slots):
            if slot not in symbols:
                raise ValueError(f"No pixels available for symbol {slot + 1}")
            pixels = decode_symbol(symbols[slot])
            bmp = Symbol(slot, pixels).entries(profile)[f"Symbol_{slot}_bmp"]
            archive.writestr(ZipInfo(f"Symbol_{slot + 1}.bmp"), bmp)
    return stream.getvalue()


def import_symbol_image(data: bytes) -> bytes:
    """Read a 64 by 64 PNG/BMP image or a complete packed symbol."""
    try:
        with Image.open(BytesIO(data), formats=["PNG", "BMP"]) as image:
            if image.size != (64, 64):
                raise ValueError("A symbol image must be 64 by 64 pixels")
            rgb = image.convert("RGB").tobytes()
            return bytes(sum(rgb[i : i + 3]) // 3 for i in range(0, len(rgb), 3))
    except (OSError, Image.DecompressionBombError) as exc:
        if len(data) == PAYLOAD_SIZE:
            return decode_symbol(data)
        raise ValueError("Invalid symbol image") from exc


def export_symbol_image(grayscale: bytes) -> bytes:
    if len(grayscale) != PIXELS:
        raise ValueError("A symbol needs 4096 grayscale pixels")
    stream = BytesIO()
    Image.frombytes("L", (64, 64), grayscale).save(stream, format="PNG")
    return stream.getvalue()


def symbol_crc(pixels: bytes) -> bytes:
    """Return the two-byte checksum of 512 packed pixel bytes."""
    if len(pixels) != 512:
        raise ValueError("A symbol needs 512 packed pixel bytes")
    crc = 0
    for offset in range(0, 512, 2):
        word = int.from_bytes(pixels[offset : offset + 2], "big")
        for bit in range(16):
            feedback = ((crc >> 15) ^ (word >> bit)) & 1
            crc = ((crc << 1) ^ (0x1021 if feedback else 0)) & 0xFFFF
    return crc.to_bytes(2, "big")


def encode_symbol(grayscale: bytes) -> bytes:
    """Pack row-major grayscale pixels, using 128 as the white threshold."""
    if len(grayscale) != PIXELS:
        raise ValueError("A symbol needs 4096 grayscale pixels (64 by 64)")
    packed = bytearray(512)
    for y in range(64):
        for x in range(64):
            if grayscale[y * 64 + x] < 128:
                packed[x * 8 + y // 8] |= 1 << (y % 8)
    result = bytes(packed)
    return result + symbol_crc(result)


def decode_symbol(data: bytes) -> bytes:
    """Validate a complete symbol and return row-major black/white pixels."""
    if len(data) != PAYLOAD_SIZE or symbol_crc(data[:512]) != data[512:]:
        raise ValueError("Invalid symbol length or checksum")
    return bytes(
        0 if data[x * 8 + y // 8] & (1 << (y % 8)) else 255
        for y in range(64)
        for x in range(64)
    )


@dataclass(frozen=True)
class Symbol:
    slot: int
    grayscale: bytes

    def __post_init__(self) -> None:
        if type(self.slot) is not int or self.slot < 0 or len(self.grayscale) != PIXELS:
            raise ValueError("Invalid symbol slot or grayscale image")

    @property
    def data(self) -> bytes:
        return encode_symbol(self.grayscale)

    def entries(self, profile: DcaProfile) -> dict[str, bytes]:
        if (
            profile.kind != "symbols"
            or not profile.first_custom_slot <= self.slot < profile.slots
        ):
            raise ValueError("Symbol slot is outside this device's library")
        return _symbol_entries(self)


def _symbol_entries(symbol: Symbol) -> dict[str, bytes]:
    stream = BytesIO()
    Image.frombytes("L", (64, 64), symbol.grayscale).convert("RGB").save(
        stream, format="BMP"
    )
    return {
        f"Symbol_{symbol.slot}_bmp": stream.getvalue(),
        f"Symbol_{symbol.slot}_data": symbol.data,
    }


def recovered_symbol_entries(
    symbols: Mapping[int, bytes], profile: DcaProfile
) -> dict[str, bytes]:
    """Store a complete read-back, including protected slots, without guessing Lib."""
    if profile.kind != "symbols" or set(symbols) != set(range(profile.slots)):
        raise ValueError("Recovery requires the complete symbol library")
    return {
        key: value
        for slot, data in sorted(symbols.items())
        for key, value in _symbol_entries(Symbol(slot, decode_symbol(data))).items()
    }


def read_symbols(
    entries: Mapping[str, bytes], profile: DcaProfile
) -> tuple[Symbol, ...]:
    """Read stored custom symbols, rejecting incomplete or contradictory pairs."""
    if profile.kind != "symbols":
        raise ValueError("This application does not use custom symbols")
    if "Lib" in entries and entries["Lib"] not in (b"\x00", b"\x01"):
        raise ValueError("Invalid symbol library selection")
    result: list[Symbol] = []
    for slot in range(profile.slots):
        bmp = entries.get(f"Symbol_{slot}_bmp")
        data = entries.get(f"Symbol_{slot}_data")
        if bmp is None and data is None:
            continue
        if bmp is None or data is None:
            raise ValueError(
                f"Symbol {slot} has incomplete or inconsistent project data"
            )
        grayscale = bmp if len(bmp) == PIXELS else import_symbol_image(bmp)
        if encode_symbol(grayscale) != data:
            grayscale = bytes(value ^ 255 for value in grayscale)
            if encode_symbol(grayscale) != data:
                raise ValueError(f"Symbol {slot} has inconsistent project data")
        result.append(Symbol(slot, grayscale))
    return tuple(result)


def read_symbol_library(
    archive: bytes, profile: DcaProfile, *, new_library: bool, second_generation: bool
) -> tuple[Symbol, ...]:
    """Read pixel resources from an app archive without loading executable code."""
    if profile.kind != "symbols":
        raise ValueError("This application does not use custom symbols")
    folder = "images/"
    if profile.app_id == "M0083-A0041":
        folder += f"{'New' if new_library else 'Old'}_Symbol_Lib_v_{'02' if second_generation else '01'}/"
    try:
        with ZipFile(BytesIO(archive)) as source:
            result: list[Symbol] = []
            for slot in range(profile.slots):
                member = source.getinfo(f"{folder}Symbol_{slot}.data")
                if member.file_size != PAYLOAD_SIZE:
                    raise ValueError("Invalid symbol resource size")
                result.append(Symbol(slot, decode_symbol(source.read(member))))
            return tuple(result)
    except (BadZipFile, KeyError) as exc:
        raise ValueError(
            "The app archive does not contain this symbol library"
        ) from exc
