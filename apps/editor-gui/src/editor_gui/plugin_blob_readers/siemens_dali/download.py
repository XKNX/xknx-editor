"""Siemens N141_21 DCA-level download: write the plug-in's property-image onto the device.

The ETS DCA plug-in programs the gateway by walking its ``Parameters.dll`` property-image
dictionary (``{pid: Property(size, noe, bytes)}``) and sending one
``A_PropertyValue_Write`` per entry on the matching Interface Object. We follow the exact
same contract from Python: ``build_<container>_properties`` from :mod:`properties`
produces the ``{pid: bytes}`` map, and this module ships it to the device via the standard
``DMP_InterfaceObjectWrite_R`` procedure.

Container ↔ Interface-Object-Index (odx) assignments come from the vendor plug-in.

Only ``DaliDevice`` (odx=11) is wired in this iteration — a strict MVP so we can validate
the pipeline against hardware before porting the remaining containers.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, cast
from xml.etree import ElementTree as ET

if TYPE_CHECKING:
    from xknxeditor.download import DeviceProgrammer


_N141_NS = "http://www.siemens.com/knx/N141_21_Parameters"
_NS = f"{{{_N141_NS}}}"


# Container tag -> interface-object-index. Line-scoped containers are omitted here; those
# are added as a second iteration once the global DaliDevice path is proven on hardware.
CONTAINER_ODX: dict[str, int] = {
    "DaliDevice": 11,
    "Rtc": 14,
    "Effects": 17,
    "LightControlTwoPoint": 32,
    "LightControlConstant": 33,
    "TimeSwitch": 34,
}


@dataclass(frozen=True, slots=True)
class PropertyWrite:
    """One A_PropertyValue_Write to run: Interface-Object-Index + PID + payload.

    The vendor's ``Property`` struct carries both ``Size`` and ``NoE``; the plug-in supplies
    ``Size=1, NoE=1`` even for the line-fanout PIDs
    (``_DALIDEVICE_LINE_PIDS``) whose ``data`` is two bytes. We mirror that: ``count=1``
    on every write, and let the device unpack according to its own property-table.
    Switching ``count`` to ``len(data)`` for fanout PIDs is still an open question that
    only a pcap of an ETS DCA download can settle.
    """

    odx: int
    pid: int
    data: bytes
    count: int = 1


async def program_dalidevice(
    programmer: DeviceProgrammer,
    blob_xml: bytes,
    *,
    verify: bool = False,
) -> int:
    """Decode the BLOB_PARA payload and push the ``DaliDevice`` property-image to the
    device over ``programmer``. Returns the number of successful property writes."""
    import zlib

    from editor_gui.plugin_blob_readers.siemens_dali.properties import (
        build_dalidevice_properties,
    )

    xml_text = zlib.decompress(blob_xml, -15).decode("utf-16-le")
    root = ET.fromstring(xml_text)
    blob_root = next((e for e in root.iter() if e.tag == f"{_NS}Blob"), None)
    if blob_root is None:
        raise ValueError("no <Blob> element in BLOB_PARA")
    dd = blob_root.find(f"{_NS}DaliDevice")
    if dd is None:
        raise ValueError("no <DaliDevice> element")
    properties = build_dalidevice_properties(dd)
    odx = CONTAINER_ODX["DaliDevice"]
    writes = [
        PropertyWrite(odx=odx, pid=pid, data=data) for pid, data in properties.items()
    ]
    return await _apply_writes(programmer, writes, verify=verify)


async def _apply_writes(
    programmer: DeviceProgrammer,
    writes: list[PropertyWrite],
    *,
    verify: bool,
) -> int:
    """Serialize each property write through ``DMP_InterfaceObjectWrite_R``.

    One write per PID. The xknx procedure handles the chunking when ``data`` is longer
    than one A_PropertyValue_Write APDU can carry (uses the device's PID_MAX_APDU_LENGTH
    the programmer already probed).
    """
    from xknx.management.management import P2PConnection
    from xknx.management.procedures.device.dmp_interface_object_write_r import (
        dmp_interface_object_write_r,
    )

    # ``programmer.connection`` is a structural ``BusConnection``; at runtime it is the concrete
    # ``P2PConnection`` the procedure expects.
    connection = cast("P2PConnection", programmer.connection)
    ok = 0
    for w in writes:
        await dmp_interface_object_write_r(
            connection,
            object_index=w.odx,
            property_id=w.pid,
            data=w.data,
            count=w.count,
            start_index=1,
            verify=verify,
            max_apdu_length=programmer.max_apdu_length,
        )
        ok += 1
    return ok


__all__ = ("CONTAINER_ODX", "PropertyWrite", "program_dalidevice")
