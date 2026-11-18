"""Access optional local original-encoder captures."""

import json
import os
import zipfile
from pathlib import Path
from typing import Any

import pytest

from xknxeditor.vendor.powertool import BLOB_NAME


def reference_config(data: bytes) -> bytes:
    """Use the current format identifier for captured configuration inputs."""
    config = json.loads(data)
    assert config["format"] in ("PowerTool6108-Config", BLOB_NAME)
    config["format"] = BLOB_NAME
    return json.dumps(config).encode()


def fixture_path(name: str) -> Path:
    override = os.environ.get("XKNX_POWERTOOL_FIXTURES")
    root = (
        Path(override)
        if override is not None
        else Path(__file__).resolve().parent / "fixtures" / "powertool"
    )
    return root / name


def open_archive(name: str) -> zipfile.ZipFile:
    path = fixture_path(name)
    if not path.is_file():
        pytest.skip(f"Local PowerTool reference archive unavailable: {name}")
    return zipfile.ZipFile(path)


def archive_cases(name: str, *, manifest: bool = True) -> list[Any]:
    if not fixture_path(name).is_file():
        return []
    with open_archive(name) as archive:
        if manifest:
            data = json.loads(archive.read("manifest.json"))
            return data["cases"] if isinstance(data, dict) else data
        return [
            n.removesuffix("/config.json")
            for n in archive.namelist()
            if n.endswith("/config.json")
        ]
