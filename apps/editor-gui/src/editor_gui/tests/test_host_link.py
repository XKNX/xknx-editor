"""The host link: the websocket Home Assistant's integration talks to the editor over."""

from __future__ import annotations

import time
from collections.abc import Iterator
from pathlib import Path

import pytest
from starlette.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from editor_gui import __version__, host
from editor_gui.host import HostOptions
from editor_gui.web.host_link import PROTOCOL, HostLink
from editor_gui.web.server import WebHub

_PASSWORD = "integration-password"


@pytest.fixture(autouse=True)
def inside_home_assistant() -> Iterator[None]:
    host.set_current(HostOptions(home_assistant=True))
    yield
    host.set_current(HostOptions())


@pytest.fixture
def hub(tmp_path: Path) -> WebHub:
    return WebHub(tmp_path / "files", password=_PASSWORD)


def _sign_in(client: TestClient) -> dict[str, str]:
    """Sign in as the integration does: the headers of its websocket, with the session cookie
    (the test client names its own host for a websocket otherwise)."""
    response = client.post(
        "/login", data={"password": _PASSWORD}, follow_redirects=False
    )
    assert response.status_code == 303
    session = client.cookies["xknx_web_session"]
    return {"host": "127.0.0.1", "cookie": f"xknx_web_session={session}"}


def _wait_for(link: HostLink) -> list[dict[str, object]]:
    deadline = time.monotonic() + 5.0
    while time.monotonic() < deadline:
        if received := link.receive():
            return received
        time.sleep(0.01)
    return []


def test_the_integration_signs_in_and_talks_both_ways(hub: WebHub) -> None:
    with TestClient(hub.app(), base_url="http://127.0.0.1") as client:
        headers = _sign_in(client)
        with client.websocket_connect("/host", headers=headers) as socket:
            assert socket.receive_json() == {
                "type": "hello",
                "protocol": PROTOCOL,
                "editor": __version__,
            }
            assert hub.host_link.connected
            socket.send_json({"type": "knx", "loaded": True, "project": None})
            assert _wait_for(hub.host_link) == [
                {"type": "knx", "loaded": True, "project": None}
            ]
            assert hub.host_link.send({"type": "set_project", "id": 1, "project": {}})
            assert socket.receive_json() == {
                "type": "set_project",
                "id": 1,
                "project": {},
            }
            # Not an object with a type: dropped.
            socket.send_text("[1, 2]")
            socket.send_text("no json")
            socket.send_json({"type": "result", "id": 1, "error": None})
            assert _wait_for(hub.host_link) == [
                {"type": "result", "id": 1, "error": None}
            ]
    assert not hub.host_link.connected
    assert not hub.host_link.send({"type": "set_project"})


def test_a_new_connection_replaces_the_old_one(hub: WebHub) -> None:
    with TestClient(hub.app(), base_url="http://127.0.0.1") as client:
        headers = _sign_in(client)
        with client.websocket_connect("/host", headers=headers) as first:
            first.receive_json()
            generation = hub.host_link.generation
            with client.websocket_connect("/host", headers=headers) as second:
                second.receive_json()
                with pytest.raises(WebSocketDisconnect) as closed:
                    first.receive_json()
                assert closed.value.code == 4409
                assert hub.host_link.generation > generation
                assert hub.host_link.connected


@pytest.mark.parametrize(
    ("signed_in", "extra", "home_assistant"),
    [
        # Without signing in.
        (False, {}, True),
        # A browser page (it sends its origin).
        (True, {"origin": "http://127.0.0.1"}, True),
        # Outside Home Assistant there is no integration.
        (True, {}, False),
    ],
)
def test_others_are_not_let_in(
    hub: WebHub, signed_in: bool, extra: dict[str, str], home_assistant: bool
) -> None:
    with TestClient(hub.app(), base_url="http://127.0.0.1") as client:
        headers = _sign_in(client) if signed_in else {"host": "127.0.0.1"}
        host.set_current(HostOptions(home_assistant=home_assistant))
        with (
            pytest.raises(WebSocketDisconnect) as closed,
            client.websocket_connect("/host", headers={**headers, **extra}) as socket,
        ):
            socket.receive_json()
    assert closed.value.code == 4403
    assert not hub.host_link.connected


def test_the_gui_thread_is_woken_by_a_message(hub: WebHub) -> None:
    with TestClient(hub.app(), base_url="http://127.0.0.1") as client:
        headers = _sign_in(client)
        with client.websocket_connect("/host", headers=headers) as socket:
            socket.receive_json()
            hub.wake.clear()
            socket.send_json({"type": "hello", "protocol": PROTOCOL})
            assert hub.wake.wait(5.0)
