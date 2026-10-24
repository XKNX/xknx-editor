import json
import os

import yaml

base = os.path.dirname(os.path.abspath(__file__))
manifest = {
    "name": "Open-KNX-KB",
    "version": "1.0.0",
    "description": "Herstellerneutrale, semantisch präzise Wissensbasis für Gebäudeautomation mit KNX",
    "domains": [],
    "functional_blocks": [],
    "device_archetypes": [],
    "addressing_patterns": [],
    "dpt_index": {},
}


def parse_md(path):
    with open(path, encoding="utf-8") as f:
        c = f.read()
    if c.startswith("---"):
        parts = c.split("---", 2)
        if len(parts) >= 3:
            return yaml.safe_load(parts[1])
    return {}


# 1. Domains
dom_dir = os.path.join(base, "01_domains")
for f in sorted(os.listdir(dom_dir)):
    if f.endswith(".md"):
        data = parse_md(os.path.join(dom_dir, f))
        manifest["domains"].append(
            {
                "id": data.get("id"),
                "title": data.get("title"),
                "file": f"01_domains/{f}",
                "primary_fbs": data.get("primary_functional_blocks", []),
            }
        )

# 2. Functional Blocks
fb_dir = os.path.join(base, "02_functional_blocks")
for f in sorted(os.listdir(fb_dir)):
    if f.endswith(".md"):
        data = parse_md(os.path.join(fb_dir, f))
        fb_entry = {
            "id": data.get("id"),
            "title": data.get("title"),
            "category": data.get("category"),
            "standard_reference": data.get("standard_reference"),
            "file": f"02_functional_blocks/{f}",
            "communication_objects_count": len(data.get("communication_objects", [])),
            "recommended_ga_structure": data.get("recommended_ga_structure", []),
        }
        manifest["functional_blocks"].append(fb_entry)
        for ko in data.get("communication_objects", []):
            dpt = ko.get("dpt")
            if dpt not in manifest["dpt_index"]:
                manifest["dpt_index"][dpt] = []
            manifest["dpt_index"][dpt].append(
                {
                    "fb": data.get("id"),
                    "ko_id": ko.get("id"),
                    "name": ko.get("name"),
                    "direction": ko.get("direction"),
                    "flags": ko.get("flags"),
                }
            )

# 3. Addressing patterns
addr_dir = os.path.join(base, "03_addressing_patterns")
for f in sorted(os.listdir(addr_dir)):
    if f.endswith(".md"):
        manifest["addressing_patterns"].append(
            {
                "file": f"03_addressing_patterns/{f}",
                "title": f.replace(".md", "").replace("_", " ").title(),
            }
        )

# 4. Device Archetypes
dev_dir = os.path.join(base, "04_device_archetypes")
for f in sorted(os.listdir(dev_dir)):
    if f.endswith(".md"):
        data = parse_md(os.path.join(dev_dir, f))
        manifest["device_archetypes"].append(
            {
                "id": data.get("id"),
                "title": data.get("title"),
                "file": f"04_device_archetypes/{f}",
                "device_class": data.get("device_class"),
                "implemented_fbs": data.get("implemented_functional_blocks", []),
            }
        )

manifest_path = os.path.join(base, "manifest.json")
with open(manifest_path, "w", encoding="utf-8") as f:
    json.dump(manifest, f, indent=2, ensure_ascii=False)

print("Manifest build finished:")
print(f"  Domains: {len(manifest['domains'])}")
print(f"  Functional Blocks: {len(manifest['functional_blocks'])}")
print(f"  Device Archetypes: {len(manifest['device_archetypes'])}")
print(f"  Addressing Patterns: {len(manifest['addressing_patterns'])}")
print(f"  Indexed DPTs: {len(manifest['dpt_index'])}")
