"""Load controls: the 16-byte load records of the old database as ``LdCtrl*`` elements."""

from __future__ import annotations

from .xmlout import El

PROC_TYPES = {
    0: "auto",
    1: "par",
    2: "grp",
    3: "par,grp",
    128: "full",
    129: "full,par",
    130: "full,grp",
    131: "all",
}

# property data types by code (17-36: PDT_GENERIC_01 to _20)
PROP_TYPES: dict[int, str] = {
    0: "PDT_CONTROL",
    1: "PDT_CHAR",
    2: "PDT_UNSIGNED_CHAR",
    3: "PDT_INT",
    4: "PDT_UNSIGNED_INT",
    5: "PDT_KNX_FLOAT",
    6: "PDT_DATE",
    7: "PDT_TIME",
    8: "PDT_LONG",
    9: "PDT_UNSIGNED_LONG",
    10: "PDT_FLOAT",
    11: "PDT_DOUBLE",
    12: "PDT_CHAR_BLOCK",
    13: "PDT_POLL_GROUP_SETTINGS",
    14: "PDT_SHORT_CHAR_BLOCK",
    15: "PDT_DATE_TIME",
    16: "PDT_VARIABLE_LENGTH",
    47: "PDT_UTF8",
    48: "PDT_VERSION",
    49: "PDT_ALARM_INFO",
    50: "PDT_BINARY_INFORMATION",
    51: "PDT_BITSET8",
    52: "PDT_BITSET16",
    53: "PDT_ENUM8",
    54: "PDT_SCALING",
    60: "PDT_NE_VL",
    61: "PDT_NE_FL",
    62: "PDT_FUNCTION",
}
PROP_TYPES.update({17 + i: f"PDT_GENERIC_{i + 1:02d}" for i in range(20)})


def _us(r: bytes, i: int) -> int:
    return (r[i] << 8) | r[i + 1]


def _ui(r: bytes, i: int) -> int:
    return (r[i] << 24) | (r[i + 1] << 16) | (r[i + 2] << 8) | r[i + 3]


def _hex(data: bytes) -> str:
    return data.hex().upper()


class LoadControls:
    """Turns load records into load control elements; collects record errors."""

    def __init__(self, first_app_object_idx: int) -> None:
        self.first_app_object_idx = first_app_object_idx
        self.errors: list[str] = []

    def proc_type(self, value: int) -> str:
        name = PROC_TYPES.get(value)
        if name is None:
            self.errors.append(
                f"S19IllegalProcMaskValue: unexpected ProcMaskValue: {value}"
            )
            return "auto"
        return name

    def process(self, record: bytes, applies_to: str) -> El | None:
        if record[0] == 0xFF:
            return None
        el = self._extended(record) if record[0] == 0xF0 else self._standard(record)
        if el is not None and applies_to != "auto" and el.tag != "LdCtrlMerge":
            el["AppliesTo"] = applies_to
        return el

    @staticmethod
    def _el(tag: str, **attrs: int | str | bool) -> El:
        el = El(tag)
        for name, value in attrs.items():
            if isinstance(value, bool):
                el[name] = "true" if value else "false"
            else:
                el[name] = str(value)
        return el

    @staticmethod
    def _count_start(el: El, count: int, start: int) -> None:
        if count != 1:
            el["Count"] = str(count)
        if start != 1:
            el["StartElement"] = str(start)

    def _standard(self, r: bytes) -> El | None:
        low = r[0] & 0xF
        hi = r[0] & 0xF0
        lsm = (r[0] >> 4) & 0xF
        e = self._el
        if low == 0:
            return None
        if low == 1:
            return e("LdCtrlLoad", LsmIdx=lsm)
        if low == 2:
            return e("LdCtrlLoadCompleted", LsmIdx=lsm)
        if low == 4:
            return e("LdCtrlUnload", LsmIdx=lsm)
        if low == 3:
            kind = r[1]
            if kind in (0, 1):
                address = _us(r, 3)
                size = (_us(r, 5) - address + 1) & 0xFFFF
                return e(
                    "LdCtrlAbsSegment",
                    LsmIdx=lsm,
                    SegType=kind,
                    Address=address,
                    Size=size,
                    Access=r[7],
                    MemType=r[8] & 0xF,
                    SegFlags=r[9],
                )
            if kind == 2:
                return e("LdCtrlTaskSegment", LsmIdx=lsm, Address=_us(r, 3))
            if kind == 3:
                return e(
                    "LdCtrlTaskPtr",
                    LsmIdx=lsm,
                    InitPtr=_us(r, 3),
                    SavePtr=_us(r, 5),
                    SerialPtr=_us(r, 7),
                )
            if kind == 4:
                return e("LdCtrlTaskCtrl1", LsmIdx=lsm, Address=_us(r, 3), Count=r[5])
            if kind == 5:
                return e(
                    "LdCtrlTaskCtrl2",
                    LsmIdx=lsm,
                    Callback=_us(r, 3),
                    Address=_us(r, 5),
                    Seg0=_us(r, 7),
                    Seg1=_us(r, 9),
                )
            if kind == 10:
                return e("LdCtrlMaxLength", LsmIdx=lsm, Size=_us(r, 3))
            if kind == 11:
                return e(
                    "LdCtrlRelSegment", LsmIdx=lsm, Size=_ui(r, 3), Mode=r[7], Fill=r[8]
                )
            self.errors.append(
                f"S19IllegalLoadControlProperty: segment control with mem type {kind}"
            )
            return None
        if low in (5, 6):
            el = e("LdCtrlWriteProp", ObjIdx=r[1], PropId=r[2])
            self._count_start(
                el, ((r[3] << 4) & 0xF00) | r[5], ((r[3] & 0xF) << 8) | r[4]
            )
            el["Verify"] = "true" if r[0] == 6 else "false"
            el["InlineData"] = _hex(r[6:16])
            return el
        if low == 7:
            el = e(
                "LdCtrlCompareProp", InlineData=_hex(r[6:16]), ObjIdx=r[1], PropId=r[2]
            )
            self._count_start(
                el, ((r[3] << 4) & 0xF00) | r[5], ((r[3] & 0xF) << 8) | r[4]
            )
            return el
        if low in (8, 9, 10):
            if hi > 0x30:
                if hi != 0x70:
                    self.errors.append(
                        "S19IllegalLoadControlProperty: unknown mem type on write"
                    )
                    return None
                offset = (r[1] << 8) | r[2] | ((r[5] & 0xF0) << 12)
                end = (r[3] << 8) | r[4] | ((r[5] & 0xF) << 16)
                el = e(
                    "LdCtrlWriteRelMem",
                    ObjIdx=r[6],
                    Offset=offset,
                    Size=(end - offset + 1) & 0xFFFFFFFF,
                    Verify=low == 9,
                )
                if low == 10:
                    el["InlineData"] = _hex(r[8:16])
                return el
            inline = r[5:16] if low == 10 else None
            space = {0x10: "LcSlave", 0x20: "LcFilter", 0x30: "User"}.get(
                hi, "Standard"
            )
            address = _us(r, 1)
            end = _us(r, 3)
            if space == "User" and inline is None:
                address += (r[5] & 0xF0) << 12
                end += (r[5] & 0xF) << 16
            el = El("LdCtrlWriteMem")
            if space != "Standard":
                el["AddressSpace"] = space
            el["Address"] = str(address)
            el["Size"] = str((end - address + 1) & 0xFFFFFFFF)
            el["Verify"] = "true" if low == 9 else "false"
            if inline is not None:
                el["InlineData"] = _hex(inline)
            return el
        if low == 11:
            if hi in (0, 0x30):
                el = El("LdCtrlCompareMem", {"InlineData": _hex(r[5:16])})
                if hi == 0x30:
                    el["AddressSpace"] = "User"
                address = _us(r, 1)
                el["Address"] = str(address)
                el["Size"] = str((_us(r, 3) - address + 1) & 0xFFFFFFFF)
                return el
            if hi != 0x70:
                self.errors.append(
                    "S19IllegalLoadControlProperty: unknown mem type on compare"
                )
                return None
            offset = (r[1] << 8) | r[2] | ((r[5] & 0xF0) << 12)
            end = (r[3] << 8) | r[4] | ((r[5] & 0xF) << 16)
            return e(
                "LdCtrlCompareRelMem",
                InlineData=_hex(r[8:16]),
                ObjIdx=r[6],
                Offset=offset,
                Size=(end - offset + 1) & 0xFFFFFFFF,
            )
        if low == 12:
            return El("LdCtrlRestart")
        if low == 13:
            return e("LdCtrlDelay", MilliSeconds=(r[1] << 8) | r[2])
        if low == 14:
            return El("LdCtrlConnect")
        # low == 15
        return El("LdCtrlDisconnect")

    def _obj(self, el: El, r: bytes, kind: int, at: int) -> int:
        """ObjIdx/ObjType addressing of extended records; returns the index after it."""
        if kind == 4:
            el["ObjIdx"] = str(r[at])
            return at + 1
        if kind == 5:
            el["ObjIdx"] = str((r[at] + self.first_app_object_idx) & 0xFF)
            return at + 1
        el["ObjType"] = str(_us(r, at))
        if r[at + 2]:
            el["Occurrence"] = str(r[at + 2])
        return at + 3

    def _extended(self, r: bytes) -> El | None:
        cmd = r[1]
        e = self._el

        def objtype(tag: str) -> El:
            el = e(tag, ObjType=_us(r, 2))
            if r[4]:
                el["Occurrence"] = str(r[4])
            return el

        if cmd in (1, 3, 5, 12):
            name = {
                1: "EnableSegmentWrite",
                3: "EnableVerifyOnWriteDirect",
                5: "EnableOptimisticWrite",
                12: "EnableMemoryAutoVerify",
            }[cmd]
            return e("LdCtrlSetControlVariable", Name=name, Value=r[2] != 0)
        if cmd == 2:
            el = El("LdCtrlClearLCFilterTable")
            if r[2]:
                el["UseFunctionProp"] = "true"
            return el
        if cmd == 4:
            kind = r[2] & 0xF
            if kind in (4, 5):
                obj = r[3] if kind == 4 else (r[3] + self.first_app_object_idx) & 0xFF
                el = e("LdCtrlLoadImageProp", ObjIdx=obj, PropId=r[4])
                self._count_start(
                    el, r[7] | (((r[5] >> 4) & 0xF) << 8), r[6] | ((r[5] & 0xF) << 8)
                )
                return el
            if kind == 6:
                el = e("LdCtrlLoadImageProp", ObjType=_us(r, 3))
                if r[5]:
                    el["Occurrence"] = str(r[5])
                el["PropId"] = str(r[6])
                self._count_start(
                    el, r[9] | (((r[7] >> 4) & 0xF) << 8), r[8] | ((r[7] & 0xF) << 8)
                )
                return el
            if kind == 7:
                offset = (r[3] << 8) | r[4] | ((r[7] & 0xF0) << 12)
                end = (r[5] << 8) | r[6] | ((r[7] & 0xF) << 16)
                return e(
                    "LdCtrlLoadImageRelMem",
                    ObjIdx=r[8],
                    Offset=offset,
                    Size=(end - offset + 1) & 0xFFFFFFFF,
                )
            if kind > 3:
                self.errors.append(
                    "S19IllegalLoadControlProperty: unknown mem type on F0 04"
                )
                return None
            space = {2: "LcFilter", 3: "User", 1: "LcSlave"}.get(kind, "Standard")
            address = _us(r, 3)
            end = _us(r, 5)
            if space == "User":
                address += (r[7] & 0xF0) << 12
                end += (r[7] & 0xF) << 16
            el = El("LdCtrlLoadImageMem")
            if space != "Standard":
                el["AddressSpace"] = space
            el["Address"] = str(address)
            el["Size"] = str((end - address + 1) & 0xFFFFFFFF)
            return el
        if cmd == 6:
            el = El("LdCtrlMapError")
            if r[2]:
                el["LdCtrlFilter"] = str(r[2])
            el["OriginalError"] = str(_ui(r, 6))
            el["MappedError"] = str(_ui(r, 10))
            return el
        if cmd in (7, 9):
            kind = r[2]
            if kind not in (4, 5, 6):
                self.errors.append(
                    "S19IllegalLoadControlProperty: unknown mem type on F0 07/09"
                )
                return None
            inline: bytes | None = None
            if cmd == 9:
                count = r[7] if kind == 6 else r[5]
                if count:
                    skip = 2 if kind == 6 else 0
                    inline = bytes(r[6 + i + skip] for i in range(count))
            el = El("LdCtrlInvokeFunctionProp")
            at = self._obj(el, r, kind, 3)
            el["PropId"] = str(r[at])
            if inline is not None:
                el["InlineData"] = _hex(inline)
            return el
        if cmd == 8:
            kind = r[2]
            if kind not in (4, 5, 6):
                self.errors.append(
                    "S19IllegalLoadControlProperty: unknown mem type on F0 08"
                )
                return None
            el = El("LdCtrlReadFunctionProp")
            at = self._obj(el, r, kind, 3)
            el["PropId"] = str(r[at])
            return el
        if cmd == 10:
            self.errors.append("S19UnmappedControl: LC ADM1_DefContext not mapped")
            return None
        if cmd == 11:
            return e("LdCtrlMerge", MergeId=r[2])
        if cmd == 13:
            return e("LdCtrlProgressText", TextId=_ui(r, 2))
        if cmd == 14:
            return None
        if cmd == 15:
            kind = r[2]
            if kind not in (4, 5, 6):
                self.errors.append(
                    "S19IllegalLoadControlProperty: unknown mem type on F0 0F"
                )
                return None
            el = El("LdCtrlDeclarePropDesc")
            if kind in (4, 5):
                obj = r[3] if kind == 4 else (r[3] + self.first_app_object_idx) & 0xFF
                el["ObjIdx"] = str(obj)
                el["PropId"] = str(r[4])
                el["PropType"] = PROP_TYPES[r[5]]
                el["MaxElements"] = str(_us(r, 6))
                el["ReadAccess"] = str((r[8] >> 4) & 0xF)
                el["WriteAccess"] = str(r[8] & 0xF)
                el["Writable"] = "true" if r[9] & 0xF else "false"
            else:
                el["ObjType"] = str(_us(r, 3))
                if r[5]:
                    el["Occurrence"] = str(r[5])
                el["PropId"] = str(r[6])
                el["PropType"] = PROP_TYPES[r[7]]
                el["MaxElements"] = str(_us(r, 8))
                el["ReadAccess"] = str((r[10] >> 4) & 0xF)
                el["WriteAccess"] = str(r[10] & 0xF)
                el["Writable"] = "true" if r[11] & 0xF else "false"
            return el
        if cmd == 16:
            return El("LdCtrlClearCachedObjectTypes")
        if cmd == 17:
            return objtype("LdCtrlLoad")
        if cmd == 18:
            return objtype("LdCtrlLoadCompleted")
        if cmd == 20:
            return objtype("LdCtrlUnload")
        if cmd == 19:
            kind = r[5]
            if kind in (0, 1):
                el = objtype("LdCtrlAbsSegment")
                address = _us(r, 6)
                el["SegType"] = str(kind)
                el["Address"] = str(address)
                el["Size"] = str((_us(r, 8) - address + 1) & 0xFFFF)
                el["Access"] = str(r[10])
                el["MemType"] = str(r[11] & 0xF)
                el["SegFlags"] = str(r[12])
                return el
            if kind == 2:
                el = objtype("LdCtrlTaskSegment")
                el["Address"] = str(_us(r, 6))
                return el
            if kind == 3:
                el = objtype("LdCtrlTaskPtr")
                el["InitPtr"] = str(_us(r, 6))
                el["SavePtr"] = str(_us(r, 8))
                el["SerialPtr"] = str(_us(r, 10))
                return el
            if kind == 4:
                el = objtype("LdCtrlTaskCtrl1")
                el["Address"] = str(_us(r, 6))
                el["Count"] = str(r[8])
                return el
            if kind == 5:
                el = objtype("LdCtrlTaskCtrl2")
                el["Callback"] = str(_us(r, 6))
                el["Address"] = str(_us(r, 8))
                el["Seg0"] = str(_us(r, 10))
                el["Seg1"] = str(_us(r, 12))
                return el
            if kind == 10:
                el = objtype("LdCtrlMaxLength")
                el["Size"] = str(_us(r, 6))
                return el
            if kind == 11:
                el = objtype("LdCtrlRelSegment")
                el["Size"] = str(_ui(r, 6))
                el["Mode"] = str(r[10])
                el["Fill"] = str(r[11])
                return el
            return None
        if cmd in (21, 23):
            el = objtype("LdCtrlWriteProp")
            el["PropId"] = str(r[5])
            el["Verify"] = "true" if cmd == 23 else "false"
            el["InlineData"] = _hex(r[6:16])
            return el
        if cmd in (22, 24):
            el = objtype("LdCtrlWriteProp")
            el["PropId"] = str(r[5])
            self._count_start(
                el, r[8] | (((r[6] >> 4) & 0xF) << 8), r[7] | ((r[6] & 0xF) << 8)
            )
            el["Verify"] = "true" if cmd == 24 else "false"
            el["InlineData"] = _hex(r[9:16])
            return el
        if cmd == 25:
            el = objtype("LdCtrlCompareProp")
            el["PropId"] = str(r[5])
            el["InlineData"] = _hex(r[6:16])
            return el
        if cmd == 26:
            el = objtype("LdCtrlCompareProp")
            el["PropId"] = str(r[5])
            self._count_start(
                el, r[8] | (((r[6] >> 4) & 0xF) << 8), r[7] | ((r[6] & 0xF) << 8)
            )
            el["InlineData"] = _hex(r[9:16])
            return el
        if cmd == 27:
            return e("LdCtrlMasterReset", EraseCode=r[2], ChannelNumber=r[3])
        self.errors.append(
            f"S19IllegalLoadControlCommand: unknown extended load control: F0 {cmd:02X}"
        )
        return None
