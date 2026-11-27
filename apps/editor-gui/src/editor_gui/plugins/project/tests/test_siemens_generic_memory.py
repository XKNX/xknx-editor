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
