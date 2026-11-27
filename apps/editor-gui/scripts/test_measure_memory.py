"""Pure logic for the developer memory measurement command."""

from __future__ import annotations

import json
import os
import sqlite3
import sys
from contextlib import closing
from pathlib import Path
from typing import cast
from unittest.mock import Mock
from zipfile import ZipFile

import psutil
import pytest
from httpx2 import EventSource, Request, Response
from measure_memory import (
    CATALOG,
    arguments,
    items_digest,
    seed_master_cache,
    select_products,
    snapshot_catalog,
)
from memory_measurement.analysis import (
    archive_manifest,
    compare,
    deep_size,
    differences,
)
from memory_measurement.editor import (
    APP_NAME,
    MIB,
    Phase,
    RunOptions,
    Sample,
    editor_transport,
    isolated_env,
    limit_command,
    render_table,
    stop_editor,
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


def test_deep_walk_counts_large_dictionary_key_once() -> None:
    key = "x" * 10_000
    value = {key: None}
    size = deep_size([value, key])
    assert size.strings == 1
    assert size.string_bytes == sys.getsizeof(key)
    assert deep_size(value).string_bytes == size.string_bytes


def test_snapshot_catalog_keeps_source_and_tolerates_missing_archive(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source"
    store = source / "knxprod"
    store.mkdir(parents=True)
    product = store / "product.knxprod"
    product.write_bytes(b"product")
    paths = [str(tmp_path / "old" / product.name), str(store / "missing.knxprod")]
    with closing(sqlite3.connect(source / CATALOG)) as db, db:
        db.execute("CREATE TABLE hardware_programs (knxprod_path TEXT)")
        db.executemany(
            "INSERT INTO hardware_programs VALUES (?)", [(p,) for p in paths]
        )

    destination = tmp_path / "copy"
    snapshot = snapshot_catalog(source, destination)
    with closing(sqlite3.connect(snapshot)) as db:
        present, missing = [
            Path(row[0])
            for row in db.execute(
                "SELECT knxprod_path FROM hardware_programs ORDER BY rowid"
            )
        ]
    assert present.is_relative_to(destination)
    assert present.read_bytes() == b"product"
    assert missing.is_relative_to(destination)
    assert not missing.exists()

    present.write_bytes(b"changed copy")
    assert product.read_bytes() == b"product"
    with closing(sqlite3.connect(source / CATALOG)) as db:
        assert db.execute(
            "SELECT knxprod_path FROM hardware_programs ORDER BY rowid"
        ).fetchall() == [(p,) for p in paths]


def test_select_products_skips_missing_archives_unless_explicitly_requested(
    tmp_path: Path,
) -> None:
    present = Mock(application_id="available", hardware2program_ref_id="present")
    missing = Mock(application_id="lost", hardware2program_ref_id="missing")
    archive = tmp_path / "available.knxprod"
    with ZipFile(archive, "w") as product:
        product.writestr("M-0001/available.xml", b"<KNX/>")
    catalog = Mock()
    catalog.list_products.return_value = [missing, present]
    catalog.get_program_source.side_effect = {
        "present": (str(archive), "M-0001"),
        "missing": (str(tmp_path / "lost.knxprod"), "M-0001"),
    }.get

    assert select_products(catalog, (), 1) == [(6, present)]
    with pytest.raises(ValueError, match="No product archive for lost"):
        select_products(catalog, ("lost",), 1)


def test_stop_editor_terminates_and_reaps_recursive_children(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app = Mock(pid=123)
    app.poll.return_value = None
    app.wait.return_value = 0
    worker = Mock()
    tree = Mock()
    tree.children.return_value = [worker]
    monkeypatch.setattr(psutil, "Process", Mock(return_value=tree))
    wait = Mock(side_effect=[([], [worker]), ([worker], [])])
    monkeypatch.setattr(psutil, "wait_procs", wait)
    monkeypatch.setattr(os, "killpg", Mock(), raising=False)

    stop_editor(app, tmp_path / "editor.pid", profiled=False)

    tree.children.assert_called_once_with(recursive=True)
    worker.terminate.assert_called_once_with()
    worker.kill.assert_called_once_with()
    assert wait.call_count == 2


def test_isolated_env_replaces_directory_settings_and_removes_passwords(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sensitive = (
        "PYTHONPATH",
        "PYTHONHOME",
        "XKNX_PROJECT_PASSWORD",
        "XKNX_WEB_PASSWORD",
    )
    for key in sensitive:
        monkeypatch.setenv(key, "inherited")
    monkeypatch.setenv("XDG_DATA_HOME", "host-data")

    env = isolated_env(tmp_path, "local-token")

    for key, directory in {
        "HOME": "home",
        "USERPROFILE": "home",
        "XDG_DATA_HOME": "data",
        "XDG_CONFIG_HOME": "config",
        "XDG_CACHE_HOME": "cache",
        "TMPDIR": "tmp",
        "TEMP": "tmp",
    }.items():
        assert env[key] == str(tmp_path / directory)
    assert not set(sensitive).intersection(env)
    assert env["XKNX_MCP_AUTH_TOKEN"] == "local-token"
    assert os.environ["XDG_DATA_HOME"] == "host-data"


def test_limit_command_uses_available_cpus_and_optional_systemd_scope(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    command = ["python", "launch.py"]
    assert limit_command(command, RunOptions(Path("python"))) == command
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr("shutil.which", Mock(return_value="available"))
    monkeypatch.setattr(psutil, "Process", lambda: Mock(cpu_affinity=lambda: [9, 5, 7]))
    options = RunOptions(Path("python"), pi=(2, 512), hard_limit=True)
    assert limit_command(command, options) == [
        "systemd-run",
        "--user",
        "--scope",
        "--quiet",
        "-p",
        "MemoryMax=512M",
        "-p",
        "MemorySwapMax=0",
        "taskset",
        "-c",
        "5,7",
        *command,
    ]
    with pytest.raises(ValueError, match="affinity allows only 3"):
        limit_command(command, RunOptions(Path("python"), pi=(4, 512)))
    monkeypatch.setattr("shutil.which", Mock(return_value=None))
    with pytest.raises(ValueError, match="needs Linux and taskset"):
        limit_command(command, options)


@pytest.mark.parametrize(
    ("schema", "filename"), [("20", "knx_master-20.xml"), ("23", "knx_master.xml")]
)
def test_seed_master_cache_uses_fixture_master(
    tmp_path: Path, schema: str, filename: str
) -> None:
    fixture = tmp_path / "fixture.knxproj"
    master = (
        f'<KNX xmlns="http://knx.org/xml/project/{schema}"><MasterData/></KNX>'.encode()
    )
    with ZipFile(fixture, "w") as archive:
        archive.writestr("knx_master.xml", master)
    root = tmp_path / "run"
    seed_master_cache(fixture, root)

    assert (root / "data" / APP_NAME / filename).read_bytes() == master


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
