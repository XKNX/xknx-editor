from dataclasses import dataclass
from enum import Enum
from functools import lru_cache

from imgui_bundle import imgui
from xknx.dpt.dpt import DPTBase


@dataclass(frozen=True)
class DPT:
    major: int
    minor: int
    name: str
    label: str
    has_subtype: bool = True

    @property
    def code(self) -> str:
        return f"{self.major}.{self.minor:03d}"

    @property
    def token(self) -> str | None:
        """ETS token form (``DPST-1-1`` / ``DPT-5``), or ``None`` for an unknown type.

        A specified sub-type is always kept (so ``16.000`` yields ``DPST-16-0``, not
        ``DPT-16``); only a main-only type collapses to ``DPT-<major>``.
        """
        if not self.major:
            return None
        if self.has_subtype:
            return f"DPST-{self.major}-{self.minor}"
        return f"DPT-{self.major}"


DPT_UNKNOWN = DPT(0, 0, "Unknown", "?", has_subtype=False)


def dpt_from_token(token: str | None) -> DPT:
    """Resolve a :class:`DPT` from an ETS token (``DPST-5-1`` / ``DPT-5``) or a dotted code
    (``5.001`` / ``5``). Returns :data:`DPT_UNKNOWN` for an empty or unparseable value."""
    if not token:
        return DPT_UNKNOWN
    parts = token.split("-")
    if parts[0] == "DPST" and len(parts) == 3:
        return lookup_or_make_dpt(f"{parts[1]}.{parts[2]}")
    if parts[0] == "DPT" and len(parts) == 2:
        return lookup_or_make_dpt(parts[1])
    return lookup_or_make_dpt(token)


@lru_cache(maxsize=512)
def lookup_or_make_dpt(code: str | None) -> DPT:
    if not code:
        return DPT_UNKNOWN
    parts = code.split(".")
    if len(parts) == 1:
        # Bare "major" -> a main-only type (no sub-number specified).
        try:
            major = int(parts[0])
        except ValueError:
            return DPT_UNKNOWN
        xknx_dpt = DPTBase.parse_transcoder(f"DPT-{major}")
        if xknx_dpt is not None:
            name = (xknx_dpt.value_type or "").replace("_", " ").title()
            label = xknx_dpt.unit or xknx_dpt.value_type or f"DPT-{major}"
            return DPT(major, 0, name, label, has_subtype=False)
        return DPT(major, 0, f"DPT {major}", f"DPT-{major}", has_subtype=False)
    if len(parts) != 2:
        return DPT_UNKNOWN
    try:
        major = int(parts[0])
        minor = int(parts[1])
    except ValueError:
        return DPT_UNKNOWN

    xknx_dpt = DPTBase.parse_transcoder(f"{major}.{minor}")
    if xknx_dpt is not None:
        name = (xknx_dpt.value_type or "").replace("_", " ").title()
        label = xknx_dpt.unit or xknx_dpt.value_type or f"{major}.{minor:03d}"
        return DPT(major, minor, name, label)

    return DPT(major, minor, f"DPT {major}.{minor:03d}", f"{major}.{minor:03d}")


@lru_cache(maxsize=512)
def transcoder_for(dpt_str: str | None) -> type[DPTBase] | None:
    """Resolve an xknx transcoder from a project DPT string (``"DPST-1-1"`` / ``"DPT-5"``).

    The project stores token DPTs (``"DPST-1-1"``), but xknx's ``parse_transcoder`` wants dotted ``"1.1"`` (or
    ``"DPT-1"`` for main-only). We convert and fall back to the main-only transcoder."""
    if not dpt_str:
        return None
    parts = dpt_str.split("-")
    if parts[0] == "DPST" and len(parts) >= 3:
        transcoder = DPTBase.parse_transcoder(f"{parts[1]}.{parts[2]}")
        if transcoder is not None:
            return transcoder
        return DPTBase.parse_transcoder(f"DPT-{parts[1]}")
    if parts[0] == "DPT" and len(parts) >= 2:
        return DPTBase.parse_transcoder(f"DPT-{parts[1]}")
    return DPTBase.parse_transcoder(dpt_str)


DPT_MAJOR_COLORS: dict[int, imgui.ImVec4] = {
    1: imgui.ImVec4(0.9, 0.3, 0.3, 1.0),
    2: imgui.ImVec4(0.9, 0.5, 0.5, 1.0),
    3: imgui.ImVec4(0.9, 0.6, 0.2, 1.0),
    5: imgui.ImVec4(0.2, 0.8, 0.4, 1.0),
    6: imgui.ImVec4(0.2, 0.7, 0.5, 1.0),
    7: imgui.ImVec4(0.6, 0.8, 0.9, 1.0),
    9: imgui.ImVec4(0.2, 0.6, 0.9, 1.0),
    10: imgui.ImVec4(0.7, 0.7, 0.5, 1.0),
    11: imgui.ImVec4(0.7, 0.7, 0.5, 1.0),
    16: imgui.ImVec4(0.5, 0.8, 0.8, 1.0),
    17: imgui.ImVec4(0.7, 0.3, 0.9, 1.0),
    18: imgui.ImVec4(0.7, 0.3, 0.9, 1.0),
    19: imgui.ImVec4(0.7, 0.7, 0.5, 1.0),
    232: imgui.ImVec4(0.9, 0.2, 0.6, 1.0),
    249: imgui.ImVec4(0.95, 0.4, 0.7, 1.0),
}


def dpt_color(dpt: DPT) -> imgui.ImVec4:
    return DPT_MAJOR_COLORS.get(dpt.major, imgui.ImVec4(0.5, 0.5, 0.5, 1.0))


class DPTMatch(Enum):
    NONE = "none"
    LOOSE = "loose"
    EXACT = "exact"


def dpt_match(a: DPT, b: DPT) -> DPTMatch:
    if a.major != b.major:
        return DPTMatch.NONE
    if a.minor == b.minor:
        return DPTMatch.EXACT
    return DPTMatch.LOOSE
