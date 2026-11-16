"""Packaging guard: the PyInstaller spec must bundle the data files that our
runtime dependencies load lazily. These are invisible to PyInstaller's import
analysis, so they only reach the frozen app via an explicit collect_all; a
missing one surfaces as a runtime "file is missing" crash on a specific code
path (e.g. dukpy's jsruntime/process_runtime.js when a knxprod runs a parameter
calculation). The static check runs without PyInstaller; the dynamic one
verifies the actual collection when PyInstaller is available."""

from __future__ import annotations

import ast
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

_SPEC = Path(__file__).resolve().parent.parent / "xknx-editor.spec"

# Package -> a data file it loads at runtime that only collect_all bundles.
# Extend this when adding a dependency that ships data loaded lazily at runtime.
REQUIRED_RUNTIME_DATA = {
    "dukpy": "jsruntime/process_runtime.js",
    "tzdata": "zoneinfo/Europe/Berlin",
}


def _spec_collect_all_packages() -> set[str]:
    """The string literals passed to collect_all() in the spec's loop."""
    tree = ast.parse(_SPEC.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if not isinstance(node, ast.For) or not isinstance(node.iter, ast.Tuple):
            continue
        calls_collect_all = any(
            isinstance(c, ast.Call)
            and isinstance(c.func, ast.Name)
            and c.func.id == "collect_all"
            for c in ast.walk(node)
        )
        if calls_collect_all:
            return {
                e.value
                for e in node.iter.elts
                if isinstance(e, ast.Constant) and isinstance(e.value, str)
            }
    raise AssertionError("no collect_all loop found in xknx-editor.spec")


def test_spec_collects_runtime_data_packages() -> None:
    missing = set(REQUIRED_RUNTIME_DATA) - _spec_collect_all_packages()
    assert not missing, (
        f"packages dropped from the spec's collect_all: {sorted(missing)} "
        "- their runtime data files would be missing from the frozen app"
    )


@pytest.mark.parametrize(("package", "sentinel"), REQUIRED_RUNTIME_DATA.items())
def test_collect_all_bundles_runtime_sentinel(package: str, sentinel: str) -> None:
    hooks = pytest.importorskip("PyInstaller.utils.hooks")
    datas, _binaries, _hidden = hooks.collect_all(package)
    sources = [src.replace("\\", "/") for src, _dst in datas]
    assert any(s.endswith(sentinel) for s in sources), (
        f"collect_all({package!r}) did not bundle {sentinel!r}; sources: {sources[:5]}"
    )


def test_frozen_symbol_images_without_optional_pillow_backends(tmp_path: Path) -> None:
    pytest.importorskip("PyInstaller")
    analysis = next(
        node
        for node in ast.walk(ast.parse(_SPEC.read_text(encoding="utf-8")))
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "Analysis"
    )
    excludes = ast.literal_eval(
        next(kw.value for kw in analysis.keywords if kw.arg == "excludes")
    )
    script = tmp_path / "symbol_images.py"
    script.write_text(
        textwrap.dedent("""\
        import importlib.util
        import sys
        from io import BytesIO

        from PIL import Image
        from pygments import highlight
        from pygments.formatters import TerminalFormatter
        from pygments.lexers import PythonLexer
        from xknxeditor.vendor.mdt.profiles import DcaProfile
        from xknxeditor.vendor.mdt.symbols import (
            Symbol, decode_symbol, export_symbol_image, export_symbol_library,
            import_symbol_image, import_symbol_library,
        )

        assert sys.frozen
        assert "print" in highlight("print(1)", PythonLexer(), TerminalFormatter())
        pixels = bytes(range(256)) * 16
        png = export_symbol_image(pixels)
        assert png.startswith(b"\\x89PNG")
        assert import_symbol_image(png) == pixels
        for mode in ("1", "L", "P", "RGB", "RGBA"):
            for image_format in ("PNG", "BMP"):
                bitmap = Image.frombytes("L", (64, 64), pixels).convert(mode)
                rgb = bitmap.convert("RGB").tobytes()
                expected = bytes(sum(rgb[i:i + 3]) // 3 for i in range(0, len(rgb), 3))
                stream = BytesIO()
                bitmap.save(stream, format=image_format)
                assert import_symbol_image(stream.getvalue()) == expected
        for slots, first in ((36, 2), (20, 0)):
            profile = DcaProfile("test", "symbols", slots, first)
            symbol = Symbol(first, pixels)
            assert import_symbol_image(symbol.entries(profile)[f"Symbol_{first}_bmp"]) == pixels
            assert import_symbol_image(symbol.data) == decode_symbol(symbol.data)
            payload = {slot: Symbol(slot, pixels).data for slot in range(first, slots)}
            archive = export_symbol_library(payload, profile)
            assert {s.slot: s.data for s in import_symbol_library(archive, profile)} == payload
            inverted = import_symbol_library(archive, profile, invert=True)
            assert all(s.data[:512] == bytes(v ^ 255 for v in payload[s.slot][:512]) for s in inverted)
        for module in ("PIL._avif", "PIL._webp", "PIL._imagingft", "PIL._imagingcms"):
            assert importlib.util.find_spec(module) is None, module
        print("Frozen PNG/BMP/DATA/ZIP symbols OK")
        """),
        encoding="utf-8",
    )
    build = subprocess.run(
        [
            sys.executable,
            "-m",
            "PyInstaller",
            "--noconfirm",
            "--noupx",
            "--distpath",
            str(tmp_path / "dist"),
            "--workpath",
            str(tmp_path / "build"),
            "--specpath",
            str(tmp_path),
            "--additional-hooks-dir",
            str(_SPEC.parent / "packaging" / "hooks"),
            *[arg for module in excludes for arg in ("--exclude-module", module)],
            str(script),
        ],
        capture_output=True,
        text=True,
        timeout=180,
    )
    assert build.returncode == 0, build.stdout + build.stderr
    suffix = ".exe" if sys.platform == "win32" else ""
    result = subprocess.run(
        [str(tmp_path / "dist" / "symbol_images" / f"symbol_images{suffix}")],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "Frozen PNG/BMP/DATA/ZIP symbols OK" in result.stdout
