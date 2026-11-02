"""A .knxproj picked in the "Load .knxprod" dialog must be imported as a project, not catalog.

The file filter is only advisory on macOS, so a full project archive (.knxproj) can be selected
in the catalog-import dialog. Feeding it to the catalog importer only extracts the embedded product
XMLs and builds no devices, which looks like "the project loaded but has no devices".
``KnxGuiApp._load_product`` therefore routes a .knxproj to the project importer (as "Open Project"
already does). These tests drive that routing decision against a stub so no imgui app is built.
"""

from __future__ import annotations

from types import SimpleNamespace

from editor_gui.main import KnxGuiApp


def _stub() -> SimpleNamespace:
    calls: dict[str, list[str]] = {"prompt_import": [], "run_bg": []}
    return SimpleNamespace(
        _calls=calls,
        _prompt_import_dest=lambda source: calls["prompt_import"].append(source),
        # Record that the catalog worker would have started, without running it.
        _run_bg=lambda _label, _worker: calls["run_bg"].append("started"),
    )


def _load(stub: SimpleNamespace, path: str) -> None:
    KnxGuiApp._load_product(stub, path)  # type: ignore[arg-type]


def test_knxproj_routes_to_project_importer() -> None:
    stub = _stub()
    _load(stub, "/Users/hco/Documents/KNX/Wilhelmshohe.knxproj")
    assert stub._calls["prompt_import"] == [
        "/Users/hco/Documents/KNX/Wilhelmshohe.knxproj"
    ]
    assert stub._calls["run_bg"] == []  # catalog import must not start for a .knxproj


def test_knxproj_routing_is_case_insensitive() -> None:
    stub = _stub()
    _load(stub, "/tmp/Project.KNXPROJ")
    assert stub._calls["prompt_import"] == ["/tmp/Project.KNXPROJ"]
    assert stub._calls["run_bg"] == []


def test_knxprod_still_loads_as_catalog() -> None:
    stub = _stub()
    _load(stub, "/tmp/product.knxprod")
    assert (
        stub._calls["prompt_import"] == []
    )  # a real product goes to the catalog importer
    assert stub._calls["run_bg"] == ["started"]
