"""Online button handlers run as queue operations against a fake device."""

from __future__ import annotations

import asyncio
import threading
import time
from collections.abc import Callable, Coroutine, Iterator
from concurrent.futures import Future
from pathlib import Path
from typing import Any

import pytest

from editor_gui.plugins.base import Logger
from editor_gui.plugins.catalog.service import CatalogService
from editor_gui.plugins.logger.service import LogService
from editor_gui.plugins.project.online_runner import OnlineButtonRunner
from editor_gui.plugins.project.program_queue import QueueItem, ScriptRun
from editor_gui.plugins.project.service import ProjectService
from editor_gui.widgets.toasts import log_toast
from xknxeditor.download import script_online
from xknxeditor.prod.application import parse_application_xml
from xknxeditor.prod.parser_v2.ui import UiButton

APP = "M-00FA_A-0001-01-0000"


def _ref(n: int) -> str:
    return f"{APP}_P-{n}_R-{n}"


def _fixture(name: str) -> Path:
    for parent in Path(__file__).resolve().parents:
        cand = parent / "packages" / name
        if cand.exists():
            return cand
    raise FileNotFoundError(name)


class _Manager:
    def __init__(self, device: Any) -> None:
        self.device = device
        self.closed = 0

    async def open(self) -> Any:
        return self.device

    async def close(self) -> None:
        self.closed += 1


class _Connection:
    def __init__(self, loop: asyncio.AbstractEventLoop) -> None:
        self.loop = loop
        self.xknx = object()
        self.master = None
        self.interface_max_apdu_length: int | None = 248
        self.busy: tuple[str, str] | None = None
        # Connected already: nothing to open for an operation.
        self.operation_connector = None

    def not_connected(self, op: str) -> bool:
        return False

    def begin_operation(self, kind: str, address: str) -> bool:
        self.busy = (kind, address)
        return True

    def end_operation(self) -> None:
        self.busy = None

    def security_for(self, device: Any) -> None:
        return None

    def run_async(self, coro: Coroutine[Any, Any, Any]) -> Future[Any]:
        return asyncio.run_coroutine_threadsafe(coro, self.loop)


@pytest.fixture
def loop() -> Iterator[asyncio.AbstractEventLoop]:
    loop = asyncio.new_event_loop()
    thread = threading.Thread(target=loop.run_forever, daemon=True)
    thread.start()
    yield loop
    loop.call_soon_threadsafe(loop.stop)
    thread.join()
    loop.close()


def test_online_writes_are_kept_one_step_each(
    tmp_path: Path, loop: asyncio.AbstractEventLoop, monkeypatch: pytest.MonkeyPatch
) -> None:
    import sys

    sys.path.insert(0, str(_fixture("download") / "tests"))
    from conftest import FakeDevice  # type: ignore[import-not-found]

    manager = _Manager(FakeDevice(descriptor=0x07B0))
    monkeypatch.setattr(script_online, "management_session", lambda *a, **k: manager)

    proj = ProjectService(CatalogService(tmp_path / "c.xknxcatalog"))
    proj.set_logger(Logger(LogService(), "project"))
    proj.new(tmp_path / "p.xknx")
    xml = _fixture("prod") / "tests" / "fixtures" / "calculations_application.xml"
    app = parse_application_xml(xml.read_bytes(), "M-00FA")[0]
    node_id = proj.add_device("P-1", "HP-1", "Calc", app, address=5)
    assert node_id is not None
    history = len(proj.history())

    pending: list[Callable[[], None]] = []
    conn = _Connection(loop)
    logs = LogService()
    runner = OnlineButtonRunner(
        proj,
        conn,
        pending.append,
        Logger(logs, "project"),  # type: ignore[arg-type]
    )
    button = UiButton(
        id=f"{APP}_B-2",
        text="Online",
        handler="onlineButton",
        online="ConnectionOriented",
    )
    run = ScriptRun(button)
    future = runner.start(
        QueueItem(node_id=node_id, address="1.1.5", name="Calc", scope=None, script=run)
    )
    assert future is not None
    assert conn.busy == ("script", "1.1.5")
    deadline = time.monotonic() + 10
    while (not future.done() or pending) and time.monotonic() < deadline:
        while pending:
            pending.pop(0)()
        time.sleep(0.01)

    assert conn.busy is None
    assert (run.text, run.progress) == ("step", 50)
    assert manager.closed >= 1
    device = proj.find_device_by_node_id(node_id)
    assert device is not None and not device.script_running
    assert device.param_errors[button.id] == "late"
    # Shown on the button and in the operations history, so not as a toast.
    failed = [r for r in logs.get_records() if r.event == "parameter script failed"]
    assert len(failed) == 1
    assert (
        log_toast(failed[0].level, failed[0].plugin, failed[0].event, failed[0].payload)
        is None
    )
    stored = {p.ref_id: p.value for p in device.parameter_instance_refs}
    assert (stored[_ref(3)], stored[_ref(4)]) == ("10", "20")
    assert len(proj.history()) == history + 2
