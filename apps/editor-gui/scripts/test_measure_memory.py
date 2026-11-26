"""Pure logic for the developer memory measurement command."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import cast
from zipfile import ZipFile

import pytest
from httpx2 import EventSource, Request, Response
from measure_memory import arguments, items_digest
from memory_measurement.analysis import (
    archive_manifest,
    compare,
    deep_size,
    differences,
)
from memory_measurement.editor import (
    MIB,
    Phase,
    Sample,
    editor_transport,
    render_table,
    summarize_phase,
    write_json,
)

from xknxeditor.proj.core.knxproj_protect import protected_folder_zip


def test_phase_boundary_and_worker_peak() -> None:
    phase = summarize_phase(
        "import",
        2.5,
        [
            Sample(10 * MIB, 10 * MIB, 1.0, 1.0),
            Sample(20 * MIB, 45 * MIB, 2.0, 4.0),
            Sample(15 * MIB, 15 * MIB, 2.5, 5.0),
        ],
    )
    assert phase == Phase(
        "import", 2.5, 1.5, 4.0, 20 * MIB, 15 * MIB, 45 * MIB, 15 * MIB
    )
    header, row = render_table([phase]).splitlines()
    assert "CPU s main/tree" in header
    assert "Tree MiB peak/end" in header
    assert row.split() == ["import", "2.50", "1.50/4.00", "20.0/15.0", "45.0/15.0"]


def test_snapshot_comparison(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    a, b = tmp_path / "a.json", tmp_path / "b.json"
    snapshot = {
        "devices": {"1": {"parameters": [False, "a"]}},
        "export": {"project/content": "same"},
    }
    write_json(a, {"phases": ["import"], "snapshot": snapshot})
    write_json(b, {"phases": ["start", "import"], "snapshot": snapshot})
    assert compare(a, b) == 0
    assert "Identical" in capsys.readouterr().out

    write_json(b, {"snapshot": {"devices": {"1": {"parameters": [0]}}, "export": {}}})
    assert compare(a, b) == 1
    assert capsys.readouterr().out.splitlines() == [
        "3 differences",
        "  /devices/1/parameters (length)",
        "  /devices/1/parameters/0",
        "  /export/project~1content",
    ]
    assert list(differences({"devices": {}}, {"devices": {}})) == []
    write_json(b, {"phases": []})
    with pytest.raises(ValueError, match="no correctness snapshot"):
        compare(a, b)


@pytest.mark.parametrize("schema", [20, 23])
def test_protected_exports_compare_decrypted_content(
    tmp_path: Path, schema: int
) -> None:
    a, b, c = (tmp_path / f"{name}.knxproj" for name in "abc")
    for path, inner_content in ((a, b"same"), (b, b"same"), (c, b"different")):
        with ZipFile(path, "w") as outer:
            outer.writestr(
                "knx_master.xml", f'<KNX xmlns="http://knx.org/xml/project/{schema}"/>'
            )
            outer.writestr("M-0001/Hardware.xml", b"hardware")
            outer.writestr(
                "P-0001.zip",
                protected_folder_zip({"0.xml": inner_content}, "test", schema),
            )
    assert a.read_bytes() != b.read_bytes()
    expected = archive_manifest(a, "test")
    assert expected == archive_manifest(b, "test")
    assert list(differences(expected, archive_manifest(c, "test"))) == [
        "/decrypted_project_folders/P-0001.zip/0.xml"
    ]
    with pytest.raises(ValueError, match="--password-file"):
        archive_manifest(a, None)


def test_deep_walk_counts_shared_slots_and_cycles_once() -> None:
    class Node:
        __slots__ = ("label", "next")

        def __init__(self, label: str) -> None:
            self.label = label
            self.next = self

    label = "shared label"
    node = Node(label)
    root = [node, node, label]
    seen: set[int] = set()
    size = deep_size(root, seen)
    assert size.objects == 3
    assert size.bytes == sum(map(sys.getsizeof, (root, node, label)))
    assert size.strings == 1
    assert size.string_bytes == sys.getsizeof(label)
    assert deep_size(node, seen).bytes == 0


def test_item_digest_preserves_values_visibility_and_order() -> None:
    row = {"value": "7", "visible": False}
    other = {"value": "2", "visible": True}
    expected = items_digest({"items": [row, other], "count": 2})
    assert expected == items_digest(
        {"items": [{"visible": False, "value": "7"}, other], "count": 2}
    )
    assert expected != items_digest({"items": [other, row], "count": 2})
    assert expected != items_digest(
        {"items": [{**row, "visible": True}, other], "count": 2}
    )


def test_large_parameter_event_preserves_snapshot_digest() -> None:
    rows = [
        {"ref_id": f"P-{i}", "value": "long value" * 60, "visible": i % 2 == 0}
        for i in range(2000)
    ]
    result = {"items": rows, "count": len(rows)}
    payload = json.dumps(result).encode()
    assert len(payload) > MIB

    transport = editor_transport(12345)
    response = Response(
        200,
        request=Request("POST", transport.url),
        headers={"content-type": "text/event-stream"},
        content=b"data: " + payload + b"\n\n",
    )
    (event,) = EventSource(response, max_event_size=transport.max_sse_event_size)
    decoded = cast(dict[str, object], event.json())
    assert decoded == result
    assert items_digest(decoded) == items_digest(result)


def test_arguments(tmp_path: Path) -> None:
    fixture = arguments(
        ["fixture", "--apps", "APP-A, APP-B", "--out", str(tmp_path / "f.knxproj")]
    )
    assert fixture.apps == ("APP-A", "APP-B")
    assert fixture.out == tmp_path / "f.knxproj"

    run = arguments(
        [
            "run",
            "--knxproj",
            "test.knxproj",
            "--mode",
            "warm",
            "--out",
            "warm.json",
            "--pi",
            "4:1024",
            "--hard-limit",
            "--memory-use",
            "low",
            "--snapshot",
        ]
    )
    assert run.mode == "warm"
    assert run.pi == (4, 1024)
    assert run.memory_use == "low"
    assert run.hard_limit
    assert run.snapshot

    for invalid in (["--pi", "0:1024"], ["--hard-limit"], ["--idle", "nan"]):
        with pytest.raises(SystemExit) as error:
            arguments(
                ["run", "--knxproj", "test.knxproj", "--out", "run.json", *invalid]
            )
        assert error.value.code == 2

    with pytest.raises(SystemExit) as error:
        arguments(["run", "--knxproj", "fixture.knxproj", "--out", "fixture.knxproj"])
    assert error.value.code == 2
