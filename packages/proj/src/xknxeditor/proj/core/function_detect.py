"""Infer building-function candidates from how group addresses are wired to device channels.

The signal is the resolved datapoint type of the group addresses linked to a device channel's
communication objects: a switch (DPT 1.001), a dimming-control object (DPT 3.007), a blind
up/down object (DPT 1.008) and so on. :func:`classify_function` maps a channel's set of DPTs to a
KNX FunctionType code, or ``None`` when the wiring matches no recognised function. Scope is
deliberately conservative (lighting + blinds); unrecognised channels are not suggested, so an
accepted suggestion is almost always right rather than merely plausible.
"""

from __future__ import annotations

# KNX FunctionType codes (verified against knx_master.xml, matching the GUI type picker).
FT_SWITCH = "FT-1"
FT_DIM = "FT-6"
FT_BLIND = "FT-7"


def parse_dpt(dpt: str | None) -> tuple[int, int | None] | None:
    """``"DPST-1-1"`` -> ``(1, 1)``, ``"DPT-1"`` -> ``(1, None)``; ``None``/malformed -> ``None``."""
    if not dpt:
        return None
    parts = dpt.split("-")
    if len(parts) < 2:
        return None
    try:
        main = int(parts[1])
        sub = int(parts[2]) if len(parts) > 2 and parts[2] else None
    except ValueError:
        return None
    return (main, sub)


def classify_function(dpts: list[str | None]) -> str | None:
    """Infer the FunctionType from the datapoint types wired to one channel, or ``None``.

    - blind: an up/down (DPT 1.008/1.009) or blind-control (DPT 3.008) object.
    - dimmable light: a dimming-control object (DPT 3.007), or a switch plus a value (DPT 5.x).
    - switchable light: a switch object (DPT 1.001).

    Returns ``None`` when the channel is incoherent — a blind command together with a light command
    (switch or dimming). That is not one load but a whole room's worth of addresses wired to one
    device (a visualisation/logic/central device), and must not be offered as a single function.
    """
    pairs = [p for p in (parse_dpt(d) for d in dpts) if p is not None]
    if not pairs:
        return None
    mains = {main for main, _ in pairs}
    subs = set(pairs)
    has_blind = (1, 8) in subs or (1, 9) in subs or (3, 8) in subs
    has_switch = (1, 1) in subs
    has_dim = (3, 7) in subs
    if has_blind and (has_switch or has_dim):
        return None
    if has_blind:
        return FT_BLIND
    if 3 in mains or (1 in mains and 5 in mains):
        return FT_DIM
    if 1 in mains:
        return FT_SWITCH
    return None
