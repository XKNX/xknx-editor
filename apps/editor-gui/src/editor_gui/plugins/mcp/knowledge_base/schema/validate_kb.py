import json
import os
import sys

import jsonschema
import yaml

BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
SCHEMA_DIR = os.path.join(BASE_DIR, "schema")


def load_schema(name):
    with open(os.path.join(SCHEMA_DIR, f"{name}.schema.json"), encoding="utf-8") as f:
        return json.load(f)


def extract_frontmatter(content):
    if content.startswith("---"):
        parts = content.split("---", 2)
        if len(parts) >= 3:
            return yaml.safe_load(parts[1]), parts[2]
    return None, content


def validate_functional_block(data, filepath):
    errors = []
    # 1. JSON Schema check
    schema = load_schema("function_block")
    validator = jsonschema.Draft7Validator(schema)
    for err in validator.iter_errors(data):
        errors.append(f"Schema-Fehler: {err.message} (Pfad: {list(err.path)})")

    # 2. Fachliche KNX-Regeln (System-Instruktion)
    for ko in data.get("communication_objects", []):
        direction = ko.get("direction")
        flags = ko.get("flags", [])
        ko_id = ko.get("id")

        if direction == "write" and ("C" not in flags or "W" not in flags):
            errors.append(
                f"KO '{ko_id}': Schreib-/Befehlsobjekt (direction: write) muss mindestens Flags ['C', 'W'] besitzen. Gefunden: {flags}"
            )
        elif direction == "transmit" and (
            "C" not in flags or "R" not in flags or "T" not in flags
        ):
            errors.append(
                f"KO '{ko_id}': Statusobjekt (direction: transmit) muss zwingend Flags ['C', 'R', 'T'] besitzen. Gefunden: {flags}"
            )

    return errors


def validate_domain(data, filepath):
    schema = load_schema("domain")
    validator = jsonschema.Draft7Validator(schema)
    return [f"Schema-Fehler: {e.message}" for e in validator.iter_errors(data)]


def validate_device_profile(data, filepath):
    schema = load_schema("device_profile")
    validator = jsonschema.Draft7Validator(schema)
    return [f"Schema-Fehler: {e.message}" for e in validator.iter_errors(data)]


def main():
    checks = [
        ("01_domains", validate_domain),
        ("02_functional_blocks", validate_functional_block),
        ("04_device_archetypes", validate_device_profile),
    ]
    total_files = 0
    total_errors = 0

    print("=== Starte Validierung der Open-KNX-KB ===")
    for folder, validator_func in checks:
        target_dir = os.path.join(BASE_DIR, folder)
        if not os.path.exists(target_dir):
            continue
        for fname in sorted(os.listdir(target_dir)):
            if not fname.endswith(".md"):
                continue
            fpath = os.path.join(target_dir, fname)
            total_files += 1
            with open(fpath, encoding="utf-8") as f:
                raw = f.read()
            fm, _body = extract_frontmatter(raw)
            if not fm:
                print(
                    f"[FEHLER] {folder}/{fname}: Kein gültiges YAML-Frontmatter vorhanden!"
                )
                total_errors += 1
                continue
            errs = validator_func(fm, fpath)
            if errs:
                print(f"[FEHLER] {folder}/{fname}:")
                for e in errs:
                    print(f"  - {e}")
                total_errors += len(errs)
            else:
                print(f"[OK] {folder}/{fname} (id: {fm.get('id')})")

    print(f"\nErgebnis: {total_files} Dateien geprüft, {total_errors} Fehler gefunden.")
    if total_errors > 0:
        sys.exit(1)


if __name__ == "__main__":
    main()
