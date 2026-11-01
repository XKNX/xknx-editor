"""Buttons of the parameter view pressed through MCP, as a click there does: an offline handler
runs right away as one undo step, an online one is queued as a "Parameter Script" operation;
both with the checks of the parameter view."""

from __future__ import annotations

from collections.abc import Callable
from concurrent.futures import Future
from pathlib import Path
from typing import Any

import pytest
from fastmcp import Client

from editor_gui.plugins.mcp.server import build_server
from editor_gui.plugins.project import button_runner
from editor_gui.plugins.project.plugin import ProjectPlugin
from editor_gui.plugins.project.program_queue import ProgramQueue, QueueItem, ScriptRun
from editor_gui.plugins.project.strings import S
from xknxeditor.prod.application import parse_application_xml
from xknxeditor.prod.parser_v2.ui import UiButton
from xknxeditor.prod.script import ScriptError

from .test_server import _build_ctx, _data

APP = "M-00FA_A-0001-01-0000"
RUN = f"{APP}_B-1"
ONLINE = f"{APP}_B-2"


def _ref(n: int) -> str:
    return f"{APP}_P-{n}_R-{n}"


def _fixture() -> Path:
    for parent in Path(__file__).resolve().parents:
        cand = (
            parent
            / "packages"
            / "prod"
            / "tests"
            / "fixtures"
            / "calculations_application.xml"
        )
        if cand.exists():
            return cand
    raise FileNotFoundError("calculations_application.xml not found")


def _setup(tmp_path: Path) -> tuple[Any, ProjectPlugin, int]:
    ctx = _build_ctx(tmp_path)
    plugin = ProjectPlugin(ctx.api)
    ctx.project_plugin = plugin
    project = ctx.api.project
    project.new(tmp_path / "p.xknx")
    app = parse_application_xml(_fixture().read_bytes(), "M-00FA")[0]
    node_id = project.add_device("P-1", "HP-1", "Calc", app, address=5)
    assert node_id is not None
    return ctx, plugin, node_id


def _queue(plugin: ProjectPlugin, start: Callable[[QueueItem], Future[Any]]) -> None:
    plugin._program_queue = ProgramQueue(is_busy=lambda: False, start=start)  # pyright: ignore[reportPrivateUsage]


async def _refused(client: Client, match: str, **args: Any) -> None:
    with pytest.raises(Exception, match=match):
        await client.call_tool("project_press_button", args)


async def test_an_offline_button_runs_as_one_undo_step(tmp_path: Path) -> None:
    ctx, _plugin, node_id = _setup(tmp_path)
    async with Client(build_server(ctx)) as client:
        address = (await _data(client, "project_get_device", node_id=node_id))[
            "individual_address"
        ]
        result = await _data(
            client, "project_press_button", button_id=RUN, device_address=address
        )
    assert result["state"] == "finished"
    assert (result["text"], result["online"], result["error"]) == ("Run", None, None)
    changed = {p["ref_id"]: p for p in result["changed_parameters"]}
    # The handler adds 2 to A (1); a calculation derives B from it.
    assert changed[_ref(1)]["new_value"] == "3"
    assert _ref(2) in changed
    assert isinstance(changed[_ref(1)]["label"], str)
    history = ctx.api.project.history()
    assert history[0].display_text == S.BUTTON_EXECUTED.format("Run")
    device = ctx.api.project.find_device_by_node_id(node_id)
    assert device.get_param_value(_ref(1)) == "3"


async def test_a_failing_offline_button_stores_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ctx, _plugin, node_id = _setup(tmp_path)

    def fail(ui: Any, button: UiButton, *, abort: Any, on_log: Any) -> Any:
        on_log("info", "checked")
        raise ScriptError("stop")

    monkeypatch.setattr(button_runner, "run_offline_button", fail)
    entries = len(ctx.api.project.history())
    async with Client(build_server(ctx)) as client:
        result = await _data(
            client, "project_press_button", button_id=RUN, node_id=node_id
        )
    assert (result["state"], result["error"]) == ("failed", "stop")
    assert result["changed_parameters"] == []
    assert result["log"] == [{"level": "info", "message": "checked"}]
    assert len(ctx.api.project.history()) == entries
    # Shown under the button too, as after a click.
    device = ctx.api.project.find_device_by_node_id(node_id)
    assert device.param_errors[RUN] == "stop"


async def test_buttons_the_view_does_not_offer_are_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ctx, plugin, node_id = _setup(tmp_path)
    async with Client(build_server(ctx)) as client:
        await _refused(
            client, "shows no button", button_id=f"{APP}_B-9", node_id=node_id
        )
        # Access None: the view shows no button.
        await _refused(
            client, "shows no button", button_id=f"{APP}_B-3", node_id=node_id
        )
        await _refused(client, "read-only", button_id=f"{APP}_B-4", node_id=node_id)
        await _refused(client, "individual address", button_id=ONLINE, node_id=node_id)
        ctx.api.project.set_device_commissioning(
            node_id, individual_address_loaded=True
        )
        await _refused(client, "no KNX connection", button_id=ONLINE, node_id=node_id)
        device = ctx.api.project.find_device_by_node_id(node_id)
        monkeypatch.setattr(type(device), "script_running", property(lambda _: True))
        await _refused(client, "script is running", button_id=RUN, node_id=node_id)
        await _refused(client, "exactly one", button_id=RUN)
    assert plugin.program_queue.active == []


async def test_an_online_button_runs_in_the_operations_queue(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ctx, plugin, node_id = _setup(tmp_path)
    ctx.api.project.set_device_commissioning(node_id, individual_address_loaded=True)
    monkeypatch.setattr(ctx.api.connection, "can_reach_bus", lambda: True)
    started: list[QueueItem] = []

    def start(item: QueueItem) -> Future[Any]:
        started.append(item)
        assert item.script is not None
        item.script.record({_ref(3): ("3", "10")})
        item.script.log.append(("info", "read"))
        done: Future[Any] = Future()
        done.set_result(None)
        return done

    _queue(plugin, start)
    async with Client(build_server(ctx)) as client:
        result = await _data(
            client, "project_press_button", button_id=ONLINE, node_id=node_id
        )
    assert [i.origin for i in started] == ["MCP"]
    assert (result["state"], result["online"]) == ("finished", "ConnectionOriented")
    assert result["changed_parameters"][0] | {"label": None} == {
        "ref_id": _ref(3),
        "label": None,
        "old_value": "3",
        "new_value": "10",
    }
    assert result["log"] == [{"level": "info", "message": "read"}]
    assert result["operation"]["origin"] == "MCP"
    assert result["operation"]["operation"] == S.SCRIPT_OPERATION


async def test_wait_returns_while_an_online_handler_still_runs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ctx, plugin, node_id = _setup(tmp_path)
    ctx.api.project.set_device_commissioning(node_id, individual_address_loaded=True)
    monkeypatch.setattr(ctx.api.connection, "can_reach_bus", lambda: True)
    running: Future[Any] = Future()
    _queue(plugin, lambda _item: running)
    async with Client(build_server(ctx)) as client:
        result = await _data(
            client, "project_press_button", button_id=ONLINE, node_id=node_id, wait=0
        )
        assert result["state"] == "running"
        # Not canceled: it goes on and ends later.
        running.set_result(None)
        history = (await _data(client, "connection_operations"))["history"]
    assert [(e["state"], e["origin"]) for e in history] == [("finished", "MCP")]


def test_a_click_queues_an_online_button_without_an_origin(tmp_path: Path) -> None:
    ctx, plugin, node_id = _setup(tmp_path)
    started: list[QueueItem] = []
    _queue(plugin, lambda item: started.append(item) or Future())
    device = ctx.api.project.find_device_by_node_id(node_id)
    button = UiButton(id=ONLINE, text="Online", online="ConnectionOriented")
    plugin._handle_button(device, button)  # pyright: ignore[reportPrivateUsage]
    assert [(i.origin, i.ended) for i in started] == [(None, None)]


def test_a_script_run_keeps_the_value_before_the_run() -> None:
    run = ScriptRun(UiButton(id=ONLINE, text="Online"))
    run.record({"a": ("1", "2"), "b": (None, "5")})
    run.record({"a": ("2", "3")})
    assert run.changes == {"a": ("1", "3"), "b": (None, "5")}
