"""Regenerate the bundled mask resource table from a KNX master data file.

Usage: uv run python packages/download/tools/gen_mask_resources.py <knx_master.xml> <out.json>
Extracts the download-relevant resource locations (Load State Machine control,
table pointers, load status, run control, programming mode) per device mask version from the KNX
master data. Only these few resources are extracted - not the manufacturer data.
"""

import json
import sys
from pathlib import Path

from xknxeditor.namespaces.intermediate.resource_name_t import ResourceName as N
from xknxeditor.prod.master import parse_master_xml

RELEVANT = {
    N.APPLICATION_LOAD_CONTROL,
    N.APPLICATION_LOAD_STATUS,
    N.APPLICATION_RUN_CONTROL,
    N.APPLICATION_DATA_PTR,
    N.GROUP_ADDRESS_TABLE_LOAD_CONTROL,
    N.GROUP_ADDRESS_TABLE_PTR,
    N.GROUP_ADDRESS_TABLE_LOAD_STATUS,
    N.GROUP_ASSOCIATION_TABLE_LOAD_CONTROL,
    N.GROUP_ASSOCIATION_TABLE_PTR,
    N.GROUP_ASSOCIATION_TABLE_LOAD_STATUS,
    N.GROUP_OBJECT_TABLE_LOAD_CONTROL,
    N.GROUP_OBJECT_TABLE_PTR,
    N.GROUP_OBJECT_TABLE_LOAD_STATUS,
    N.PROGRAMMING_MODE,
}


def main(master_path: str, out_path: str) -> None:
    master = parse_master_xml(Path(master_path).read_bytes())
    table: dict = {}
    for mv in master.raw.mask_versions.mask_version:
        entry = {}
        for cfg in mv.hawk_configuration_data:
            if cfg.resources is None:
                continue
            for r in cfg.resources.resource:
                if r.name in RELEVANT and r.location is not None:
                    loc = r.location
                    entry[r.name.value] = [
                        loc.address_space.value,
                        loc.interface_object_ref,
                        loc.property_id,
                        loc.start_address,
                    ]
        if entry:
            table[mv.id] = dict(sorted(entry.items()))
    out = {mask: table[mask] for mask in sorted(table)}
    Path(out_path).write_text(json.dumps(out, indent=1, sort_keys=True) + "\n")
    print(f"wrote {len(out)} masks to {out_path}")


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
