"""Regenerate the bundled mask resource table from a KNX master data file.

Usage: uv run python packages/download/tools/gen_mask_resources.py <knx_master.xml> <out.json>

Each mask keeps the HawkConfigurationData block whose LegacyVersion is unset.
A second block is the legacy view of the same mask (MV-07B0's memory-mapped
tables, MV-091A's first block) and must not overwrite the current one. See the
module comment in xknxeditor.download.resources for what the flavours mean.
"""

import json
import sys
from pathlib import Path

from xknxeditor.namespaces.intermediate.resource_name_t import ResourceName as N
from xknxeditor.prod.master import parse_master_xml

# Locations the download reads, plus the three group tables and the individual
# address. The table flavours and the individual-address octets are how the
# image decides layout; the load-control locations are how the procedure
# addresses a mask. PeiprogLoadStatus stays out: publishing it would make
# MV-0705 poll an LSM 5 the devices we have do not answer.
RELEVANT = {
    N.APPLICATION_LOAD_CONTROL,
    N.APPLICATION_LOAD_STATUS,
    N.APPLICATION_RUN_CONTROL,
    N.APPLICATION_DATA_PTR,
    N.GROUP_ADDRESS_TABLE,
    N.GROUP_ADDRESS_TABLE_LOAD_CONTROL,
    N.GROUP_ADDRESS_TABLE_PTR,
    N.GROUP_ADDRESS_TABLE_LOAD_STATUS,
    N.GROUP_ASSOCIATION_TABLE,
    N.GROUP_ASSOCIATION_TABLE_LOAD_CONTROL,
    N.GROUP_ASSOCIATION_TABLE_PTR,
    N.GROUP_ASSOCIATION_TABLE_LOAD_STATUS,
    N.GROUP_OBJECT_TABLE,
    N.GROUP_OBJECT_TABLE_LOAD_CONTROL,
    N.GROUP_OBJECT_TABLE_PTR,
    N.GROUP_OBJECT_TABLE_LOAD_STATUS,
    N.INDIVIDUAL_ADDRESS,
    N.PROGRAMMING_MODE,
}


def _current_configurations(configurations: list[object]) -> list[object]:
    """The mask's own hawk configuration, skipping legacy compatibility views."""
    current = [
        cfg for cfg in configurations if getattr(cfg, "legacy_version", None) is None
    ]
    return current or configurations


def _property_mapped(configuration: object) -> bool:
    features = getattr(configuration, "features", None)
    if features is None:
        return False
    for feature in features.feature:
        if feature.name.value == "PropertyMappedLsms":
            return feature.value == 1
    return False


def _styles(resource: object) -> set[str]:
    return {style.value for style in getattr(resource, "mgmt_style", ())}


def _prefer(kept: object, candidate: object, property_mapped: bool) -> bool:
    """BCU2 lists a resource twice: ``simple`` is the memory pointer, ``lsm`` is
    the load-procedure property. A property-mapped mask keeps the ``lsm`` one."""
    if not property_mapped:
        return False
    return "lsm" in _styles(candidate) and "lsm" not in _styles(kept)


def _row(resource: object) -> list[object]:
    location = getattr(resource, "location", None)
    space = obj = pid = start = None
    if location is not None:
        space = location.address_space.value
        obj = location.interface_object_ref
        pid = location.property_id
        start = location.start_address
    flavour = resource.resource_type.flavour
    return [
        space,
        obj,
        pid,
        start,
        None if flavour is None else flavour.value,
        resource.resource_type.length,
    ]


def main(master_path: str, out_path: str) -> None:
    master = parse_master_xml(Path(master_path).read_bytes())
    table: dict[str, dict[str, object]] = {}
    for mv in master.raw.mask_versions.mask_version:
        features: dict[str, int] = {}
        chosen: dict[str, object] = {}
        for cfg in _current_configurations(list(mv.hawk_configuration_data)):
            property_mapped = _property_mapped(cfg)
            if cfg.features is not None:
                for feature in cfg.features.feature:
                    features.setdefault(feature.name.value, feature.value)
            if cfg.resources is None:
                continue
            # Resolve duplicates inside this block first. The first current
            # block then wins, so a later legacy-shaped block cannot replace it.
            picked: dict[str, object] = {}
            for resource in cfg.resources.resource:
                if resource.name not in RELEVANT:
                    continue
                previous = picked.get(resource.name.value)
                if previous is None or _prefer(previous, resource, property_mapped):
                    picked[resource.name.value] = resource
            for name, resource in picked.items():
                chosen.setdefault(name, resource)
        if not chosen and not features:
            continue
        table[mv.id] = {
            "features": dict(sorted(features.items())),
            "resources": {
                name: _row(resource) for name, resource in sorted(chosen.items())
            },
        }
    out = {mask: table[mask] for mask in sorted(table)}
    Path(out_path).write_text(json.dumps(out, indent=1) + "\n")
    print(f"wrote {len(out)} masks to {out_path}")


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
