"""Connection failures must reach the recovery retry instead of producing partial data."""

from types import SimpleNamespace
from typing import Any, cast

import pytest
from xknx.exceptions import ManagementConnectionError

from xknxeditor.download.errors import VerificationError
from xknxeditor.recover.read_config import (
    _association_table_address,
    read_parameter_memory,
    read_property_values,
)


class _Programmer:
    def __init__(self, error: Exception) -> None:
        self.error = error

    async def read_memory(
        self, address: int, size: int, *, user: bool = False
    ) -> bytes:
        raise self.error

    async def read_table_reference(self, object_index: int) -> int:
        return 0x1000

    async def read_property(self, object_index: int, property_id: int) -> bytes:
        raise self.error


@pytest.mark.parametrize("relative", [False, True])
@pytest.mark.parametrize("disconnected", [False, True])
async def test_memory_disconnect_propagates_but_unreadable_segment_is_skipped(
    relative: bool, disconnected: bool
) -> None:
    segment = "APP_RS-04-00000" if relative else "APP_AS-1000"
    application = cast(
        Any,
        SimpleNamespace(
            dynamic_ui=lambda: SimpleNamespace(
                segment_sizes=lambda: {segment: 8},
                segment_base_addrs=lambda: {segment: 0x1000},
            ),
            load_procedure_style=None,
            load_procedures=None,
            program=SimpleNamespace(
                static=SimpleNamespace(
                    address_table=None
                    if relative
                    else SimpleNamespace(code_segment=segment),
                    code=SimpleNamespace(
                        absolute_segment=[]
                        if relative
                        else [SimpleNamespace(id=segment, user_memory=False)],
                        relative_segment=[SimpleNamespace(id=segment, offset=0)]
                        if relative
                        else [],
                    ),
                )
            ),
        ),
    )
    error = (
        ManagementConnectionError("disconnected")
        if disconnected
        else VerificationError("unreadable")
    )
    programmer = cast(Any, _Programmer(error))
    if disconnected:
        with pytest.raises(ManagementConnectionError):
            await read_parameter_memory(programmer, application)
    else:
        assert await read_parameter_memory(programmer, application) == {}


async def test_property_disconnect_propagates(monkeypatch: pytest.MonkeyPatch) -> None:
    import xknxeditor.prod.parser_v2.application_indexer as indexing
    import xknxeditor.prod.parser_v2.encode as encoding

    monkeypatch.setattr(
        indexing,
        "ApplicationIndexer",
        lambda _: SimpleNamespace(parameter_types={}, module_defs={}),
    )
    monkeypatch.setattr(
        encoding,
        "collect_writes",
        lambda *args: SimpleNamespace(
            prop=[
                SimpleNamespace(
                    object_index=4,
                    object_type=None,
                    property_id=80,
                    occurrence=0,
                    parameter_type="PT",
                )
            ]
        ),
    )
    # No unions, so only the property the stubbed writes name is read.
    application = cast(
        Any,
        SimpleNamespace(
            program=SimpleNamespace(static=SimpleNamespace(parameters=None))
        ),
    )
    with pytest.raises(ManagementConnectionError):
        await read_property_values(
            cast(Any, _Programmer(ManagementConnectionError("disconnected"))),
            application,
        )
    assert (
        await read_property_values(
            cast(Any, _Programmer(VerificationError("unreadable"))), application
        )
        == {}
    )


async def test_a_disconnect_does_not_fall_back_to_the_static_table_address() -> None:
    application = cast(
        Any,
        SimpleNamespace(
            program=SimpleNamespace(mask_version="MV-0705"), load_procedures=None
        ),
    )
    with pytest.raises(ManagementConnectionError):
        await _association_table_address(
            cast(Any, _Programmer(ManagementConnectionError("disconnected"))),
            application,
        )
