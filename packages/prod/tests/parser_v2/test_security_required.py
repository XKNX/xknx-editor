"""The KNX Data Secure requirement an application declares for a group object."""

from types import SimpleNamespace
from typing import Any, cast

from xknxeditor.prod.parser_v2.application_indexer import com_object_security_required


def _indexer() -> Any:
    def requirement(value: str | None) -> SimpleNamespace | None:
        return None if value is None else SimpleNamespace(value=value)

    return cast(
        Any,
        SimpleNamespace(
            com_objects={
                "M-1_A-1_O-1": SimpleNamespace(security_required=requirement("Auth"))
            },
            com_object_refs={
                "M-1_A-1_O-1_R-1": SimpleNamespace(
                    ref_id="M-1_A-1_O-1", security_required=None
                ),
                "M-1_A-1_O-1_R-2": SimpleNamespace(
                    ref_id="M-1_A-1_O-1", security_required=requirement("None")
                ),
            },
        ),
    )


def test_the_ref_overrides_the_object() -> None:
    indexer = _indexer()
    assert com_object_security_required(indexer, "M-1_A-1_O-1_R-1") == "Auth"
    assert com_object_security_required(indexer, "M-1_A-1_O-1_R-2") == "None"


def test_module_instance_refs_and_unknown_refs() -> None:
    indexer = _indexer()
    assert (
        com_object_security_required(indexer, "M-1_A-1_MD-1_M-1_MI-1_O-1_R-1") == "Auth"
    )
    assert com_object_security_required(indexer, "M-9_A-9_O-9_R-9") == "None"
