"""Line coupler group filter table, built offline from a project's plain XML.

The rule is the one ETS documents for line couplers: a group address of main groups 0-13 is in
the filter table of the coupler of line ``A.L`` when at least one device on that line (other than
the coupler itself) has a group object linked to it, or when the address is marked
``Unfiltered`` ("pass through line coupler"). Main groups 14 and 15 are not in the table; the
coupler's own "main group 14/15" setting decides them.

A stricter variant (``partner=True``) keeps only addresses that are also linked by a device
outside that line, i.e. ones with a project partner across the coupler. Addresses used only by
tools outside the project (visualisations, gateways reached over IP) have no project partner, so
the strict variant drops them: that is why ETS does not use it.

The table image is the classic BCU1 coupler bitmap: one bit per group address 0/0/0-13/7/255
(14 x 2048 = 28,672 addresses, 3,584 bytes, written to LcFilter memory 0x0200). Bit order inside
a byte is a parameter (``lsb_first``); it must be confirmed against a read-back of a real coupler
before any image is written.

Only plain XML is read (``P-*/0.xml``, the application file, ``knx_master.xml``); no plug-in or
binary data is opened.
"""

from __future__ import annotations

import re
import zipfile
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from xml.etree import ElementTree as ET

FILTER_GROUPS = 14 * 2048  # main groups 0-13
FILTER_BYTES = FILTER_GROUPS // 8  # 3584


def ga_int(text: str) -> int:
    """'1/2/3' -> 2563 (three-level), '1/515' (two-level) or a plain number."""
    parts = [int(p) for p in text.split("/")]
    if len(parts) == 3:
        return (parts[0] << 11) | (parts[1] << 8) | parts[2]
    if len(parts) == 2:
        return (parts[0] << 11) | parts[1]
    return parts[0]


def ga_text(value: int) -> str:
    return f"{value >> 11}/{(value >> 8) & 7}/{value & 255}"


@dataclass
class ProjectLinks:
    """What the filter rule needs: each device's line and linked group addresses."""

    # "1.1.5" -> (1, 1)
    device_line: dict[str, tuple[int, int]] = field(
        default_factory=dict[str, tuple[int, int]]
    )
    # "1.1.5" -> {GA ints}
    device_groups: dict[str, set[int]] = field(default_factory=dict[str, set[int]])
    group_names: dict[int, str] = field(default_factory=dict[int, str])
    unfiltered: set[int] = field(default_factory=set[int])


def filter_groups(
    links: ProjectLinks,
    coupler: str,
    *,
    partner: bool = False,
) -> set[int]:
    """The group addresses (ints, main groups 0-13) the coupler ``coupler`` ("A.L.0") must let
    through, by the rule in the module docstring."""
    area, line, number = (int(p) for p in coupler.split("."))
    if number != 0:
        raise ValueError(
            f"{coupler} is not a coupler address (device number must be 0)"
        )
    inside: set[int] = set()
    outside: set[int] = set()
    for device, groups in links.device_groups.items():
        if device == coupler:
            continue
        (inside if links.device_line.get(device) == (area, line) else outside).update(
            groups
        )
    chosen = inside & outside if partner else set(inside)
    chosen |= links.unfiltered
    return {g for g in chosen if g < FILTER_GROUPS}


def filter_image(groups: Iterable[int], *, lsb_first: bool = True) -> bytes:
    """The 3,584-byte bitmap for ``groups`` (bit set = let through)."""
    image = bytearray(FILTER_BYTES)
    for g in groups:
        if not 0 <= g < FILTER_GROUPS:
            continue
        bit = g & 7 if lsb_first else 7 - (g & 7)
        image[g >> 3] |= 1 << bit
    return bytes(image)


def image_groups(image: bytes, *, lsb_first: bool = True) -> set[int]:
    """The group addresses set in a bitmap (the inverse of :func:`filter_image`)."""
    out: set[int] = set()
    for index, byte in enumerate(image[:FILTER_BYTES]):
        for bit in range(8):
            if byte & (1 << bit):
                out.add(index * 8 + (bit if lsb_first else 7 - bit))
    return out


def _ns(root: ET.Element) -> str:
    return root.tag.split("}")[0] + "}" if root.tag.startswith("{") else ""


def links_from_knxproj(path: str) -> ProjectLinks:
    """Read device lines, group links, names and Unfiltered flags from a .knxproj (plain XML).
    Refuses a password-protected project (no 0.xml in the clear)."""
    with zipfile.ZipFile(path) as z:
        names = [n for n in z.namelist() if re.fullmatch(r"P-[0-9A-F]+/0\.xml", n)]
        if not names:
            raise ValueError("no plain P-*/0.xml (protected project?)")
        root = ET.fromstring(z.read(names[0]))
    ns = _ns(root)
    out = ProjectLinks()
    by_id: dict[str, int] = {}
    for g in root.iter(ns + "GroupAddress"):
        value = int(g.get("Address", "0"))
        gid = g.get("Id", "")
        by_id[gid] = value
        by_id[gid.split("_", 1)[-1] if "_" in gid else gid] = value  # "GA-34"
        out.group_names[value] = g.get("Name", "")
        if (g.get("Unfiltered") or "").lower() == "true":
            out.unfiltered.add(value)
    for area in root.iter(ns + "Area"):
        a = int(area.get("Address", "0"))
        for line in area.iter(ns + "Line"):
            ln = int(line.get("Address", "0"))
            for dev in line.iter(ns + "DeviceInstance"):
                if dev.get("Address") is None:
                    continue  # not yet addressed
                ia = f"{a}.{ln}.{dev.get('Address')}"
                out.device_line[ia] = (a, ln)
                groups = out.device_groups.setdefault(ia, set())
                for ref in dev.iter(ns + "ComObjectInstanceRef"):
                    for link in (ref.get("Links") or "").split():
                        if link in by_id:
                            groups.add(by_id[link])
                        else:
                            tail = link.split("_")[-1]
                            if tail in by_id:
                                groups.add(by_id[tail])
    return out


# --- the N 140/03 (application 0001-21, mask MV-0912) settings -------------------------------


@dataclass(frozen=True)
class CouplerSetting:
    name: str
    text: str
    offset: int  # byte offset in the parameter segment (segment base 0x0100)
    bit_offset: int  # from the most significant bit, as product data counts it
    size: int
    value: int
    choices: Mapping[int, str]

    @property
    def address(self) -> int:
        return 0x0100 + self.offset

    @property
    def mask(self) -> int:
        return ((1 << self.size) - 1) << (8 - self.bit_offset - self.size)

    @property
    def bits(self) -> int:
        return (self.value << (8 - self.bit_offset - self.size)) & self.mask


def coupler_settings(path: str, device_address: str) -> list[CouplerSetting]:
    """The memory-backed settings of the coupler at ``device_address`` in a .knxproj: product
    data defaults overridden by the device's ParameterInstanceRefs (plain XML only)."""
    area, line, number = device_address.split(".")
    with zipfile.ZipFile(path) as z:
        pname = next(n for n in z.namelist() if re.fullmatch(r"P-[0-9A-F]+/0\.xml", n))
        proot = ET.fromstring(z.read(pname))
        pns = _ns(proot)
        dev = None
        for a in proot.iter(pns + "Area"):
            if a.get("Address") != area:
                continue
            for ln in a.iter(pns + "Line"):
                if ln.get("Address") != line:
                    continue
                for d in ln.iter(pns + "DeviceInstance"):
                    if d.get("Address") == number:
                        dev = d
        if dev is None:
            raise ValueError(f"no device {device_address}")
        h2p = dev.get("Hardware2ProgramRefId", "")
        app_id = (
            "M-" + h2p.split("_HP-")[0].split("_")[0][2:] + "_A-" + h2p.split("_HP-")[1]
        )
        app_name = next(n for n in z.namelist() if n.endswith(app_id + ".xml"))
        aroot = ET.fromstring(z.read(app_name))
    ans = _ns(aroot)
    overrides = {
        p.get("RefId", ""): p.get("Value", "")
        for p in dev.iter(pns + "ParameterInstanceRef")
    }
    types: dict[str, tuple[int, dict[int, str]]] = {}
    for pt in aroot.iter(ans + "ParameterType"):
        tr = pt.find(ans + "TypeRestriction")
        if tr is None:
            continue
        choices = {
            int(e.get("Value", "0")): e.get("Text", "")
            for e in tr.iter(ans + "Enumeration")
        }
        types[pt.get("Id", "")] = (int(tr.get("SizeInBit", "8")), choices)
    refs = {
        r.get("RefId", ""): r.get("Id", "") for r in aroot.iter(ans + "ParameterRef")
    }
    out: list[CouplerSetting] = []
    for p in aroot.iter(ans + "Parameter"):
        mem = p.find(ans + "Memory")
        if mem is None or p.get("ParameterType") not in types:
            continue
        size, choices = types[p.get("ParameterType", "")]
        if size > 8:
            continue
        value = int(p.get("Value", "0"))
        ref_id = refs.get(p.get("Id", ""), "")
        if ref_id in overrides and overrides[ref_id] != "":
            value = int(overrides[ref_id])
        out.append(
            CouplerSetting(
                name=p.get("Name", ""),
                text=" ".join((p.get("Text") or "").split()),
                offset=int(mem.get("Offset", "0")),
                bit_offset=int(mem.get("BitOffset", "0")),
                size=size,
                value=value,
                choices=choices,
            )
        )
    return out


def expected_config_bytes(
    settings: Iterable[CouplerSetting],
) -> dict[int, tuple[int, int]]:
    """address -> (expected bits, mask) for the bytes the settings own."""
    out: dict[int, tuple[int, int]] = {}
    for s in settings:
        bits, mask = out.get(s.address, (0, 0))
        out[s.address] = (bits | s.bits, mask | s.mask)
    return out
