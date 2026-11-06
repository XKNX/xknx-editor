"""The open project in Home Assistant's KNX integration: linking, updates, the integration's
answers."""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Any, cast

import pytest

from editor_gui.plugins.home_assistant.service import (
    CREATED_BY,
    DEBOUNCE_SECONDS,
    HomeAssistantService,
    LinkState,
)


@dataclass
class _Meta:
    id: str = "P-0001"
    guid: str = "guid-1"
    name: str = "House"


@dataclass
class _Project:
    is_open: bool = True
    generation: int = 1
    revision: int = 10
    meta: _Meta | None = field(default_factory=_Meta)

    def get_project_metadata(self) -> _Meta | None:
        return self.meta if self.is_open else None


@dataclass
class _Link:
    connected: bool = True
    generation: int = 1
    inbox: list[dict[str, Any]] = field(default_factory=list[dict[str, Any]])
    sent: list[dict[str, Any]] = field(default_factory=list[dict[str, Any]])

    def receive(self) -> list[dict[str, Any]]:
        messages, self.inbox = self.inbox, []
        return messages

    def send(self, message: dict[str, Any]) -> bool:
        if not self.connected:
            return False
        self.sent.append(message)
        return True


class _Clock:
    def __init__(self) -> None:
        self.now = 100.0

    def __call__(self) -> float:
        return self.now


def _ours(guid: str = "guid-1") -> dict[str, Any]:
    return {
        "name": "House",
        "guid": guid,
        "tool_version": f"{CREATED_BY} 1.0",
        "created_by": CREATED_BY,
        "last_modified": "2026-11-06T10:00:00",
    }


_FOREIGN = {
    "name": "Old House",
    "guid": "guid-1",
    "tool_version": "Other Tool 6.3",
    "created_by": "Other Tool",
    "last_modified": "2026-03-01T08:00:00",
}


class _Env:
    def __init__(self) -> None:
        self.project = _Project()
        self.link: _Link | None = _Link()
        self.clock = _Clock()
        self.builds = 0
        self.service = HomeAssistantService(
            cast("Any", self.project),
            lambda: cast("Any", self.link),
            build=self._build,
            prepare=None,
            clock=self.clock,
            persist=False,
        )

    def _build(self, _project: object) -> dict[str, Any]:
        self.builds += 1
        return {"info": {"guid": "guid-1"}, "group_addresses": {}, "devices": {}}

    def report(self, project: dict[str, Any] | None, *, loaded: bool = True) -> None:
        assert self.link is not None
        self.link.inbox.append({"type": "knx", "loaded": loaded, "project": project})
        self.service.poll()

    def answer(
        self, error: str | None = None, *, project: dict[str, Any] | None = None
    ) -> None:
        """The integration's answer to the last request: the new project first, then the result."""
        assert self.link is not None
        request = self.link.sent[-1]
        if error is None:
            self.link.inbox.append(
                {"type": "knx", "loaded": True, "project": project or _ours()}
            )
        self.link.inbox.append({"type": "result", "id": request["id"], "error": error})
        self.service.poll()


@pytest.fixture
def env() -> _Env:
    return _Env()


def test_nothing_is_known_until_the_integration_reports(env: _Env) -> None:
    env.service.poll()
    assert env.service.state is LinkState.UNAVAILABLE
    env.link = None
    env.service.poll()
    assert env.service.state is LinkState.UNAVAILABLE
    assert not env.service.send_now()


def test_without_the_knx_integration_nothing_is_sent(env: _Env) -> None:
    env.report(None, loaded=False)
    assert env.service.state is LinkState.KNX_MISSING
    assert not env.service.send_now()
    assert not env.service.wants_question()


def test_a_project_home_assistant_does_not_hold_is_only_sent_when_asked(
    env: _Env,
) -> None:
    env.report(None)
    assert env.service.state is LinkState.NOT_LINKED
    assert env.service.wants_question()
    env.clock.now += 60
    env.service.poll()
    assert env.link is not None and env.link.sent == []

    assert env.service.send_now()
    assert env.service.state is LinkState.SENDING
    request = env.link.sent[-1]
    assert request["type"] == "set_project"
    assert request["project"]["info"]["guid"] == "guid-1"
    env.answer()
    assert env.service.linked
    assert env.service.state is LinkState.UP_TO_DATE
    assert not env.service.wants_question()


def test_another_project_in_home_assistant_is_not_linked(env: _Env) -> None:
    # The same GUID uploaded from another tool, and another project of this editor.
    for held in (_FOREIGN, _ours("guid-2")):
        env.report(held)
        assert not env.service.linked
        assert env.service.state is LinkState.NOT_LINKED


def test_not_now_is_asked_again_for_another_project_only(env: _Env) -> None:
    env.report(None)
    env.service.decline()
    assert not env.service.wants_question()
    env.project.meta = _Meta(guid="guid-2")
    env.project.generation += 1
    assert env.service.wants_question()


def test_a_linked_project_is_sent_a_moment_after_the_last_change(env: _Env) -> None:
    env.report(_ours())
    # The integration's state is not known to match this editor's run yet: sent once.
    env.clock.now += DEBOUNCE_SECONDS
    env.service.poll()
    env.answer()
    assert env.service.state is LinkState.UP_TO_DATE
    assert env.builds == 1

    env.project.revision += 1
    env.service.poll()
    assert env.service.state is LinkState.PENDING
    env.clock.now += DEBOUNCE_SECONDS / 2
    env.project.revision += 1
    env.service.poll()
    env.clock.now += DEBOUNCE_SECONDS / 2
    env.service.poll()
    # Changed again meanwhile: still waiting.
    assert env.builds == 1
    env.clock.now += DEBOUNCE_SECONDS
    env.service.poll()
    assert env.builds == 2
    env.answer()
    assert env.service.state is LinkState.UP_TO_DATE


def test_without_automatic_updates_changes_wait(env: _Env) -> None:
    env.service.set_auto_update(False)
    env.report(_ours())
    env.clock.now += 60
    env.service.poll()
    assert env.service.state is LinkState.PENDING
    assert env.builds == 0
    assert env.service.send_now()
    env.answer()
    assert env.service.state is LinkState.UP_TO_DATE


def test_a_refused_project_is_tried_again_with_the_next_change(env: _Env) -> None:
    env.report(_ours())
    env.clock.now += DEBOUNCE_SECONDS
    env.service.poll()
    env.answer("disk full")
    assert env.service.state is LinkState.FAILED
    assert env.service.error == "disk full"
    env.clock.now += 60
    env.service.poll()
    assert env.builds == 1

    env.project.revision += 1
    env.service.poll()
    env.clock.now += DEBOUNCE_SECONDS
    env.service.poll()
    assert env.builds == 2


def test_a_project_that_cannot_be_built_is_reported(env: _Env) -> None:
    def broken(_project: object) -> dict[str, Any]:
        raise ValueError("no application")

    env.service = HomeAssistantService(
        cast("Any", env.project),
        lambda: cast("Any", env.link),
        build=broken,
        prepare=None,
        clock=env.clock,
        persist=False,
    )
    env.report(None)
    assert not env.service.send_now()
    assert env.service.state is LinkState.FAILED
    assert env.service.error == "no application"


def test_an_upload_in_home_assistant_unlinks_the_project(env: _Env) -> None:
    env.report(None)
    env.service.send_now()
    env.answer()
    assert env.service.linked
    env.report(_FOREIGN)
    assert env.service.state is LinkState.NOT_LINKED
    # Not sent back on its own.
    env.clock.now += 60
    env.service.poll()
    assert env.builds == 1


def test_a_new_connection_sends_the_linked_project_again(env: _Env) -> None:
    env.report(_ours())
    env.clock.now += DEBOUNCE_SECONDS
    env.service.poll()
    env.answer()
    assert env.link is not None
    env.link.generation += 1
    env.service.poll()
    assert env.service.state is LinkState.UNAVAILABLE
    # Home Assistant may have started again: the project goes to it again at once.
    env.report(_ours())
    assert env.service.state is LinkState.SENDING
    assert env.builds == 2


def test_a_closed_project_is_not_sent(env: _Env) -> None:
    env.report(None)
    env.project.is_open = False
    assert env.service.state is LinkState.NO_PROJECT
    assert not env.service.send_now()
    assert env.service.project_guid() is None


def test_a_project_without_a_guid_is_known_by_its_id(env: _Env) -> None:
    env.project.meta = _Meta(guid="")
    assert env.service.project_guid() == "P-0001"


def test_the_programs_are_read_off_the_gui_thread_before_the_first_send(
    env: _Env,
) -> None:
    started = threading.Event()
    release = threading.Event()

    def prepare(_project: object) -> None:
        started.set()
        release.wait(5)

    env.service = HomeAssistantService(
        cast("Any", env.project),
        lambda: cast("Any", env.link),
        build=env._build,
        prepare=prepare,
        clock=env.clock,
        persist=False,
    )
    env.report(None)
    # Read as soon as the KNX integration can take the project.
    assert started.wait(5)
    assert env.service.send_now()
    assert env.service.state is LinkState.SENDING
    assert env.builds == 0
    release.set()
    deadline = time.monotonic() + 5
    while env.builds == 0 and time.monotonic() < deadline:
        env.service.poll()
        time.sleep(0.01)
    assert env.builds == 1
    env.answer()
    assert env.service.state is LinkState.UP_TO_DATE


def test_each_sent_project_has_its_time(env: _Env) -> None:
    env.report(None)
    env.service.send_now()
    assert env.link is not None
    info = env.link.sent[-1]["project"]["info"]
    assert info["guid"] == "guid-1"
    assert info["last_modified"].endswith("+00:00")


def test_what_home_assistant_does_with_the_group_addresses(env: _Env) -> None:
    assert env.link is not None
    env.report(None)
    env.link.inbox.append(
        {
            "type": "usage",
            "group_addresses": {
                "2563": {
                    "users": [{"kind": "entity", "entity_id": "light.kitchen"}],
                    "sends": True,
                },
                "2564": {"users": [], "sends": False},
                # Not a raw address, not an object: left out.
                "x": {"users": []},
                "2565": "text",
            },
        }
    )
    env.service.poll()
    assert set(env.service.usage) == {2563, 2564}
    assert env.service.usage[2563].users[0]["entity_id"] == "light.kitchen"
    assert env.service.senders() == frozenset({2563})
    # A new connection knows nothing yet.
    env.link.generation += 1
    env.service.poll()
    assert env.service.usage == {}


def test_home_assistants_telegrams_go_to_the_monitor(env: _Env) -> None:
    assert env.link is not None
    taken: list[tuple[dict[str, Any], bool]] = []
    env.service.on_telegram = lambda telegram, log: taken.append((telegram, log))
    env.link.inbox += [
        {"type": "values", "telegrams": [{"destination": "1/2/3"}, "bad"]},
        {"type": "telegram", "telegram": {"destination": "1/2/4"}},
    ]
    env.service.poll()
    assert taken == [
        ({"destination": "1/2/3"}, False),
        ({"destination": "1/2/4"}, True),
    ]


def test_the_history_is_asked_for_and_handed_on(env: _Env) -> None:
    assert env.link is not None
    loaded: list[list[dict[str, Any]]] = []
    env.service.on_history = loaded.append
    assert not env.service.request_history()
    env.report(None)
    assert env.service.request_history(hours=6)
    request = env.link.sent[-1]
    assert request == {"type": "telegram_history", "id": request["id"], "hours": 6}
    assert env.service.history_loading
    # One at a time.
    assert not env.service.request_history()
    env.link.inbox.append(
        {"type": "result", "id": request["id"], "error": None, "telegrams": [{"a": 1}]}
    )
    env.service.poll()
    assert loaded == [[{"a": 1}]]
    assert not env.service.history_loading

    assert env.service.request_history()
    env.link.inbox.append(
        {"type": "result", "id": env.link.sent[-1]["id"], "error": "store busy"}
    )
    env.service.poll()
    assert env.service.history_error == "store busy"


def test_the_knx_integrations_connection_is_known(env: _Env) -> None:
    assert env.link is not None
    env.link.inbox.append(
        {
            "type": "knx",
            "loaded": True,
            "project": None,
            "connection": {"type": "routing", "host": "224.0.23.12", "port": 3671},
        }
    )
    env.service.poll()
    assert env.service.connection == {
        "type": "routing",
        "host": "224.0.23.12",
        "port": 3671,
    }
