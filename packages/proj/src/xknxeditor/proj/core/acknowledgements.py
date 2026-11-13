"""Resolve acknowledged addresses against their persistent source identities."""

from collections.abc import Mapping

from xknxeditor.proj.models import ComObject, GroupAddress


def acknowledges(com_object: ComObject, address: GroupAddress) -> bool:
    tokens = {
        value.rsplit("_", 1)[-1] for value in (com_object.acknowledges or "").split()
    }
    return bool(address.source_id and address.source_id.rsplit("_", 1)[-1] in tokens)


def exported_acknowledgements(
    com_object: ComObject, ids: Mapping[int, str]
) -> str | None:
    return (
        " ".join(
            ids[link.group_address_id]
            for link in com_object.links
            if link.group_address_id in ids
            and acknowledges(com_object, link.group_address)
        )
        or None
    )
