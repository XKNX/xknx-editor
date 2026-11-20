"""
KNXnet/IP tunnelling server - KNX v01.07.01 - Tunnelling 03.08.04 - §2.

`KNXIPTunnellingServer` lets KNXnet/IP tunnelling clients use the bus connection
of an XKNX instance, e.g. a KNX USB interface as a KNXnet/IP interface.

A copy of ``xknx.io.server`` (branch ``usb-individual-address`` of the xknx fork:
XKNX/xknx#2025 and #2029, not yet in an xknx release), adapted to xknx 4.1.1: kept
identical to it but for the imports and what xknx 4.1.1 lacks (in ``compat``: the raw
send and receive of ``CEMIHandler``), so it can be dropped for ``xknx.io.server`` once the
editor requires an xknx release with it.
"""

from .connection import TunnelInfo
from .tunnelling_server import KNXIPTunnellingServer

__all__ = [
    "KNXIPTunnellingServer",
    "TunnelInfo",
]
