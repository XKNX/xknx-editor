"""The comparison's read of a Generic Plug-in device's parameter memory, on a stub device.

The application and the description are the small, made-up documents of the vendor tests: no
manufacturer product data is used.
"""

from types import SimpleNamespace
from typing import Any, cast

import pytest

from editor_gui.plugin_providers.siemens_generic import sources
from editor_gui.plugins.project.plugin import ProjectPlugin
from packages.vendor.tests.test_siemens_generic_download import (
    DESCRIPTION_ID,
    application_xml,
    description_xml,
)
from xknxeditor.prod import parse_application_xml
from xknxeditor.vendor.siemens_generic.configuration import default_config


def _device(version: tuple[int, int, int], **description: str) -> Any:
    app = parse_application_xml(application_xml(version), "M-0001")[0]
    xml = description_xml(**description)
    device = SimpleNamespace(
        app=app,
        plugin_baggage=lambda ref: xml if ref == DESCRIPTION_ID else None,
        binary_data=[],
        order_number="",
        native_error="",
    )
    blob = default_config(sources(cast(Any, device)).description)
    device.binary_data = [SimpleNamespace(name="BLOB_STRING", data=blob)]
    return device


@pytest.mark.parametrize(
    ("version", "segment", "expected"),
    [
        # The parameter memory at its addresses.
        ((2, 0, 37), None, (0x5000, 25, None)),
        # A plug-in of version 1 stores an image of the whole address space, but the device
        # is read from the memory base on all the same.
        ((1, 2, 16), None, (0x5000, 25, None)),
        # Relative to the application's segment (object 4).
        (
            (2, 0, 36),
            '<CodeSegment Id="s" Address="0x1000" AllocLength="8192"'
            ' UseRelDownloadSubThisOffset="0"/>',
            (0x1000, 25, 4),
        ),
    ],
)
def test_the_memory_read_follows_the_plug_in_s_download(version, segment, expected):
    device = _device(version, **({"segment": segment} if segment else {}))
    assert sources(device).description.plugin_version == version
    request = ProjectPlugin._generic_memory_request(cast(Any, None), device)
    assert (request.address, request.size, request.object_index) == expected


def _oem_device(described: str) -> Any:
    """A device of an OEM's application (its own manufacturer, "-O" with the original's)
    whose description names the application ``described``."""
    application = (
        application_xml((2, 0, 36))
        .replace(b"M-0001_A-9821-01-0000", b"M-000B_A-9821-01-0000-O0001")
        .replace(b'RefId="M-0001"', b'RefId="M-000B"')
    )
    app = parse_application_xml(application, "M-000B")[0]
    xml = description_xml().replace(b'Id="id-0001_9821_01"', described.encode())
    return SimpleNamespace(
        app=app, plugin_baggage=lambda ref: xml if ref == DESCRIPTION_ID else None
    )


def test_an_oem_application_is_described_as_the_oem_s_own():
    found = sources(_oem_device('Id="id-000B_9821_01"'))
    assert found.description.id == "id-000B_9821_01"
    assert found.description.plugin_version == (2, 0, 36)
    # A description of the original manufacturer's application is another one.
    with pytest.raises(ValueError, match="another catalog application"):
        sources(_oem_device('Id="id-0001_9821_01"'))
