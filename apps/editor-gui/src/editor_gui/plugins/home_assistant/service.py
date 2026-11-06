"""The open project as the KNX project of Home Assistant's KNX integration.

Inside Home Assistant the integration talks to the editor over the host link
(:mod:`editor_gui.web.host_link`). It reports the state of the KNX integration and the project
that integration holds; the editor sends the open project to it in xknxproject's format, so the
KNX integration's group monitor, project view, address pickers and entity suggestions know it as
they know an uploaded project.

A project is *linked* when the KNX integration holds it (same GUID, sent by this editor). A linked
project is sent again a little after each change when automatic updates are on (the default).
Linking is the user's choice: an unlinked project is only sent when the user asks.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import TYPE_CHECKING, Any, cast

from editor_gui.plugins.home_assistant.areas import (
    AreaRow,
    compare,
    plan_spaces,
    request,
)
from editor_gui.plugins.home_assistant.entities import (
    EntityCandidate,
    entity_candidates,
    entity_request,
    existing_entities,
)
from editor_gui.settings import load_settings, save_settings

if TYPE_CHECKING:
    from editor_gui.plugins.base import Logger
    from editor_gui.plugins.project.service import ProjectService
    from editor_gui.web.host_link import HostLink

# The tool that sent a project, in the project's info (``created_by``).
CREATED_BY = "XKNX Editor"
# A change is sent when the project has not changed for this long.
DEBOUNCE_SECONDS = 2.0
# The telegrams Home Assistant recorded that a history asks for.
HISTORY_HOURS = 24
_SETTINGS = "home_assistant"


@dataclass(frozen=True)
class GroupAddressUsage:
    """What Home Assistant does with a group address."""

    # {kind ("entity", "expose", "time"), name, entity_id, platform, state, ui, disabled} each.
    users: tuple[dict[str, Any], ...] = ()
    # Home Assistant sends to it (an entity's command, an expose, the time server).
    sends: bool = False


class LinkState(StrEnum):
    # Not inside Home Assistant, or the integration is not connected.
    UNAVAILABLE = "unavailable"
    # Home Assistant's KNX integration is not set up or not loaded.
    KNX_MISSING = "knx_missing"
    NO_PROJECT = "no_project"
    # The KNX integration holds no project, or another one.
    NOT_LINKED = "not_linked"
    UP_TO_DATE = "up_to_date"
    # Changes not sent yet (waiting, or automatic updates are off).
    PENDING = "pending"
    SENDING = "sending"
    FAILED = "failed"


def _usage(value: object) -> dict[int, GroupAddressUsage]:
    """The usage the integration reports: {raw address (as text): {users, sends}}."""
    if not isinstance(value, dict):
        return {}
    result: dict[int, GroupAddressUsage] = {}
    for key, entry in cast("dict[object, object]", value).items():
        if not isinstance(entry, dict):
            continue
        try:
            raw = int(str(key))
        except ValueError:
            continue
        data = cast("dict[str, Any]", entry)
        users: object = data.get("users")
        listed = cast("list[object]", users) if isinstance(users, list) else []
        result[raw] = GroupAddressUsage(
            users=tuple(
                cast("dict[str, Any]", u) for u in listed if isinstance(u, dict)
            ),
            sends=bool(data.get("sends")),
        )
    return result


def _texts(value: object) -> dict[str, str]:
    """{key: text} of a message; None (no error) becomes ""."""
    if not isinstance(value, dict):
        return {}
    return {
        str(k): "" if v is None else str(v)
        for k, v in cast("dict[object, object]", value).items()
    }


@dataclass(frozen=True)
class EntityRow:
    """A building function's entity and what Home Assistant has of it."""

    candidate: EntityCandidate
    # The area its room became (None: not linked).
    area_id: str | None
    # The entities of Home Assistant on the address it switches with.
    existing: tuple[str, ...]
    # Home Assistant's verdict: None not asked yet, "" it takes it, else why not.
    check: str | None
    # The entity created for it.
    created: str | None


def _default_builder(project: ProjectService) -> dict[str, Any]:
    from editor_gui import __version__
    from editor_gui.plugins.home_assistant.project_model import build_project_model

    return cast(
        "dict[str, Any]",
        build_project_model(project, tool_version=f"{CREATED_BY} {__version__}"),
    )


def _default_prepare(project: ProjectService) -> None:
    from editor_gui.plugins.home_assistant.project_model import prepare_project_model

    prepare_project_model(project)


class HomeAssistantService:
    """Keeps the KNX integration's project in step with the open, linked project (GUI thread)."""

    def __init__(
        self,
        project: ProjectService,
        link: Callable[[], HostLink | None],
        *,
        build: Callable[[ProjectService], dict[str, Any]] = _default_builder,
        prepare: Callable[[ProjectService], None] | None = _default_prepare,
        clock: Callable[[], float] = time.monotonic,
        persist: bool = True,
    ) -> None:
        """``prepare`` reads on a worker thread what ``build`` needs (None: nothing)."""
        self._project = project
        self._link = link
        self._build = build
        self._prepare = prepare
        # The project generation whose programs were read, the one being read, and what failed.
        self._prepared: int | None = None
        self._preparing: int | None = None
        self._prepare_error: str | None = None
        # A send waits for the programs to be read.
        self._wanted = False
        self._clock = clock
        self._persist = persist
        self._log: Logger | None = None
        settings = load_settings(_SETTINGS) if persist else {}
        self._auto_update = bool(settings.get("auto_update", True))
        # What the integration last reported.
        self._link_generation: int = -1
        self.home_assistant_version = ""
        self.knx_loaded = False
        # The KNX integration's project info (name, guid, tool_version, created_by,
        # last_modified), None when it holds none.
        self.knx_project: dict[str, Any] | None = None
        self._knx_known = False
        # (project generation, revision) last sent with success, and the one being sent.
        self._sent: tuple[int, int] | None = None
        self._sending: tuple[int, tuple[int, int]] | None = None
        self._next_id = 0
        # When the project last changed (a change waits DEBOUNCE_SECONDS), and as which state.
        self._seen: tuple[int, int] | None = None
        self._changed_at = 0.0
        self.error: str | None = None
        # The projects whose question the user answered with Not Now (this session), by GUID.
        self._declined: set[str] = set()
        # The KNX integration's connection to the bus: {type, host, port}, None when unknown.
        self.connection: dict[str, Any] | None = None
        # What Home Assistant does with each group address (raw): who uses it ("users": entities,
        # exposes, the time server) and whether Home Assistant sends to it ("sends").
        self.usage: dict[int, GroupAddressUsage] = {}
        # Takes a telegram of Home Assistant's connection, and whether it goes to the Group
        # Monitor's log (False: only its latest value). Set by the app.
        self.on_telegram: Callable[[dict[str, Any], bool], None] | None = None
        # Takes the telegrams of a history asked for with request_history, oldest first.
        self.on_history: Callable[[list[dict[str, Any]]], None] | None = None
        self._history_request: int | None = None
        self.history_error: str | None = None
        # Home Assistant's floors and areas and the spaces linked to them ({floors, areas,
        # links}), None until the integration reports them.
        self.home_assistant_areas: dict[str, Any] | None = None
        self._areas_request: int | None = None
        # The outcome of the last apply_areas: an error, or None.
        self.areas_error: str | None = None
        # Told how many floors and areas an apply_areas created, linked or renamed.
        self.on_areas_applied: Callable[[int], None] | None = None
        # Home Assistant's verdict on each entity candidate by key ("": it takes it, else why
        # not), the candidates last sent to be checked, and the open request.
        self.entity_checks: dict[str, str] = {}
        self._checked: list[dict[str, Any]] | None = None
        self._check_request: int | None = None
        # The entities created for candidates, by their key (the integration remembers them).
        self.entity_links: dict[str, str] = {}
        self._create_request: int | None = None
        # The outcome of the last create_entities: why some were not created ({key: error}).
        self.entity_errors: dict[str, str] = {}
        # Told how many entities a create_entities created.
        self.on_entities_created: Callable[[int], None] | None = None

    def set_logger(self, log: Logger) -> None:
        self._log = log

    # --- settings ---

    @property
    def auto_update(self) -> bool:
        """Whether a linked project is sent again after each change."""
        return self._auto_update

    def set_auto_update(self, enabled: bool) -> None:
        self._auto_update = enabled
        if self._persist:
            save_settings(_SETTINGS, {"auto_update": enabled})

    # --- state ---

    @property
    def connected(self) -> bool:
        link = self._link()
        return link is not None and link.connected and self._knx_known

    def project_guid(self) -> str | None:
        """The identity of the open project in the KNX integration (its GUID, else its id)."""
        meta = self._project.get_project_metadata()
        if meta is None:
            return None
        return meta.guid or meta.id

    def project_name(self) -> str:
        meta = self._project.get_project_metadata()
        return meta.name if meta is not None else ""

    @property
    def linked(self) -> bool:
        """Whether the KNX integration holds the open project, sent by this editor."""
        guid = self.project_guid()
        held = self.knx_project
        return (
            guid is not None
            and held is not None
            and held.get("guid") == guid
            and held.get("created_by") == CREATED_BY
        )

    def _key(self) -> tuple[int, int]:
        return (self._project.generation, self._project.revision)

    @property
    def state(self) -> LinkState:
        if not self.connected:
            return LinkState.UNAVAILABLE
        if not self.knx_loaded:
            return LinkState.KNX_MISSING
        if not self._project.is_open:
            return LinkState.NO_PROJECT
        if self._sending is not None or self._wanted:
            return LinkState.SENDING
        if self.error is not None:
            return LinkState.FAILED
        if not self.linked:
            return LinkState.NOT_LINKED
        if self._sent != self._key():
            return LinkState.PENDING
        return LinkState.UP_TO_DATE

    def wants_question(self) -> bool:
        """Whether to ask the user to use the open project in Home Assistant: the KNX integration
        does not hold it, and the user did not decline it in this session."""
        guid = self.project_guid()
        return (
            self.state is LinkState.NOT_LINKED
            and guid is not None
            and guid not in self._declined
        )

    def decline(self) -> None:
        """Not now: do not ask again for the open project in this session."""
        if (guid := self.project_guid()) is not None:
            self._declined.add(guid)

    # --- actions ---

    def send_now(self) -> bool:
        """Send the open project to the KNX integration (it becomes its project, which links it).
        False when it cannot be sent now (no connection, no KNX integration, no project, or a send
        is under way). Until the project's programs are read (see :meth:`_start_prepare`) it is
        sent once they are."""
        if (
            self._link() is None
            or not self.connected
            or not self.knx_loaded
            or not self._project.is_open
            or self._sending is not None
            or self._wanted
        ):
            return False
        if not self._ready():
            self._wanted = True
            self._start_prepare()
            return True
        return self._send()

    def _ready(self) -> bool:
        return self._prepare is None or self._prepared == self._project.generation

    def _start_prepare(self) -> None:
        """Read the programs of the open project's devices on a worker thread: the first build
        of a large project would otherwise take seconds on the GUI thread."""
        prepare = self._prepare
        generation = self._project.generation
        if prepare is None or self._ready() or self._preparing == generation:
            return
        self._preparing = generation

        def run() -> None:
            try:
                prepare(self._project)
            except Exception as exc:
                self._prepare_error = str(exc) or type(exc).__name__
            finally:
                self._prepared = generation
                self._preparing = None

        threading.Thread(target=run, name="home-assistant-model", daemon=True).start()

    def _send(self) -> bool:
        link = self._link()
        if link is None:
            return False
        key = self._key()
        started = self._clock()
        try:
            model = self._build(self._project)
        except Exception as exc:
            self.error = str(exc) or type(exc).__name__
            if self._log is not None:
                self._log.error(
                    "project for Home Assistant not built", error=self.error
                )
            return False
        # The KNX integration's panel loads the project again when its info changes: the time of
        # this state of the project.
        model["info"] = {
            **model.get("info", {}),
            "last_modified": datetime.now(UTC).isoformat(timespec="seconds"),
        }
        self._next_id += 1
        if not link.send(
            {"type": "set_project", "id": self._next_id, "project": model}
        ):
            return False
        self._sending = (self._next_id, key)
        self.error = None
        if self._log is not None:
            self._log.info(
                "project sent to Home Assistant",
                group_addresses=len(model.get("group_addresses", {})),
                devices=len(model.get("devices", {})),
                seconds=round(self._clock() - started, 3),
            )
        return True

    def area_rows(self) -> list[AreaRow]:
        """The open project's floors and areas, and what each is in Home Assistant now."""
        guid = self.project_guid()
        if guid is None:
            return []
        planned = plan_spaces(self._project.get_space_tree(), guid)
        return compare(planned, self.home_assistant_areas)

    @property
    def areas_applying(self) -> bool:
        return self._areas_request is not None

    def apply_areas(self, rows: list[AreaRow]) -> bool:
        """Create, link or rename the floors and areas of ``rows`` in Home Assistant; False
        without a connection, without rows or while an earlier request is open."""
        link = self._link()
        if (
            link is None
            or not self.connected
            or not rows
            or self._areas_request is not None
        ):
            return False
        self._next_id += 1
        if not link.send({"type": "apply_areas", "id": self._next_id, **request(rows)}):
            return False
        self._areas_request = self._next_id
        self.areas_error = None
        return True

    def _lookup(self, group_address_id: int) -> tuple[str | None, int] | None:
        ga = self._project.get_group_address(group_address_id)
        return (ga.datapoint_type, ga.raw) if ga is not None else None

    def entity_rows(self) -> list[EntityRow]:
        """The entities the open project's building functions become, with their area, the
        entities of Home Assistant on their address, and Home Assistant's verdict."""
        guid = self.project_guid()
        if guid is None:
            return []
        candidates = entity_candidates(
            self._project.get_space_tree(), guid, self._lookup
        )
        links = (self.home_assistant_areas or {}).get("links")
        area_links = (
            {str(k): str(v) for k, v in cast("dict[object, object]", links).items()}
            if isinstance(links, dict)
            else {}
        )
        users = {raw: usage.users for raw, usage in self.usage.items()}
        return [
            EntityRow(
                candidate=c,
                area_id=area_links.get(c.space_key),
                existing=tuple(existing_entities(c, users)),
                check=self.entity_checks.get(c.key),
                created=self.entity_links.get(c.key),
            )
            for c in candidates
        ]

    def check_entities(self, rows: list[EntityRow]) -> None:
        """Ask Home Assistant whether it takes the candidates of ``rows``, unless they were
        asked for as they are."""
        link = self._link()
        requests = [entity_request(r.candidate, r.area_id) for r in rows]
        if (
            link is None
            or not self.connected
            or requests == self._checked
            or self._check_request is not None
        ):
            return
        self._next_id += 1
        if link.send(
            {"type": "check_entities", "id": self._next_id, "entities": requests}
        ):
            self._check_request = self._next_id
            self._checked = requests

    @property
    def entities_checking(self) -> bool:
        return self._check_request is not None

    @property
    def entities_creating(self) -> bool:
        return self._create_request is not None

    def create_entities(self, rows: list[EntityRow]) -> bool:
        """Create the entities of ``rows`` in Home Assistant; False without a connection,
        without rows or while an earlier request is open."""
        link = self._link()
        if (
            link is None
            or not self.connected
            or not rows
            or self._create_request is not None
        ):
            return False
        self._next_id += 1
        entities = [entity_request(r.candidate, r.area_id) for r in rows]
        if not link.send(
            {"type": "create_entities", "id": self._next_id, "entities": entities}
        ):
            return False
        self._create_request = self._next_id
        self.entity_errors = {}
        return True

    def senders(self) -> frozenset[int]:
        """The group addresses (raw) Home Assistant sends to."""
        return frozenset(raw for raw, usage in self.usage.items() if usage.sends)

    @property
    def history_loading(self) -> bool:
        return self._history_request is not None

    def request_history(self, hours: int = HISTORY_HOURS) -> bool:
        """Ask for the telegrams Home Assistant recorded in the last ``hours`` hours (they come
        to on_history); False without a connection or while one is asked for."""
        link = self._link()
        if link is None or not self.connected or self._history_request is not None:
            return False
        self._next_id += 1
        request = {"type": "telegram_history", "id": self._next_id, "hours": hours}
        if not link.send(request):
            return False
        self._history_request = self._next_id
        self.history_error = None
        return True

    def poll(self) -> None:
        """Handle the integration's messages and send a changed, linked project (each frame)."""
        link = self._link()
        if link is None:
            return
        if link.generation != self._link_generation:
            # A new connection (or none): nothing is known until the integration reports.
            self._link_generation = link.generation
            self._knx_known = False
            self._sending = None
            self._wanted = False
            self._sent = None
            self.usage = {}
            self.connection = None
            self._history_request = None
            self.home_assistant_areas = None
            self._areas_request = None
            self.entity_checks = {}
            self._checked = None
            self._check_request = None
            self.entity_links = {}
            self._create_request = None
        for message in link.receive():
            self._handle(message)
        key = self._key()
        if key != self._seen:
            self._seen = key
            self._changed_at = self._clock()
            # A failed send is tried again with the next change.
            self.error = None
        if self.connected and self.knx_loaded and self._project.is_open:
            self._start_prepare()
        if self._prepare_error is not None:
            if self._log is not None:
                self._log.warning(
                    "programs for Home Assistant not read", error=self._prepare_error
                )
            self._prepare_error = None
        if self._wanted and self._ready():
            self._wanted = False
            self._send()
        if (
            self._auto_update
            and self.state is LinkState.PENDING
            and self._clock() - self._changed_at >= DEBOUNCE_SECONDS
        ):
            self.send_now()

    def _handle(self, message: dict[str, Any]) -> None:
        kind = message.get("type")
        if kind == "hello":
            self.home_assistant_version = str(message.get("home_assistant", ""))
        elif kind == "knx":
            self._knx_known = True
            self.knx_loaded = bool(message.get("loaded"))
            project = message.get("project")
            self.knx_project = (
                cast("dict[str, Any]", project) if isinstance(project, dict) else None
            )
            connection = message.get("connection")
            self.connection = (
                cast("dict[str, Any]", connection)
                if isinstance(connection, dict)
                else None
            )
            if not self.linked:
                # Another project replaced ours (e.g. an upload in the KNX panel).
                self._sent = None
        elif kind == "usage":
            self.usage = _usage(message.get("group_addresses"))
        elif kind == "areas":
            self.home_assistant_areas = {
                key: message.get(key) for key in ("floors", "areas", "links")
            }
        elif kind == "entity_links":
            links = message.get("links")
            self.entity_links = (
                {str(k): str(v) for k, v in cast("dict[object, object]", links).items()}
                if isinstance(links, dict)
                else {}
            )
        elif kind == "result" and message.get("id") == self._check_request:
            self._check_request = None
            self.entity_checks = _texts(message.get("checks"))
        elif kind == "result" and message.get("id") == self._create_request:
            self._create_request = None
            self.entity_errors = _texts(message.get("errors"))
            if (error := message.get("error")) is not None:
                self.entity_errors[""] = str(error)
            created = message.get("created")
            count = (
                len(cast("dict[object, object]", created))
                if isinstance(created, dict)
                else 0
            )
            if self.on_entities_created is not None:
                self.on_entities_created(count)
        elif kind == "result" and message.get("id") == self._areas_request:
            self._areas_request = None
            if (error := message.get("error")) is not None:
                self.areas_error = str(error)
            elif self.on_areas_applied is not None:
                changed = message.get("changed")
                self.on_areas_applied(changed if isinstance(changed, int) else 0)
        elif kind in ("values", "telegram"):
            telegrams = (
                message.get("telegrams")
                if kind == "values"
                else [message.get("telegram")]
            )
            if self.on_telegram is not None and isinstance(telegrams, list):
                for telegram in cast("list[object]", telegrams):
                    if isinstance(telegram, dict):
                        self.on_telegram(
                            cast("dict[str, Any]", telegram), kind == "telegram"
                        )
        elif kind == "result" and message.get("id") == self._history_request:
            self._history_request = None
            telegrams = message.get("telegrams")
            if (error := message.get("error")) is not None or not isinstance(
                telegrams, list
            ):
                self.history_error = str(error or "no telegrams")
            elif self.on_history is not None:
                self.on_history(
                    [
                        cast("dict[str, Any]", t)
                        for t in cast("list[object]", telegrams)
                        if isinstance(t, dict)
                    ]
                )
        elif kind == "result" and self._sending is not None:
            request, key = self._sending
            if message.get("id") != request:
                return
            self._sending = None
            if (error := message.get("error")) is not None:
                self.error = str(error)
                if self._log is not None:
                    self._log.error(
                        "Home Assistant did not take the project", error=self.error
                    )
            else:
                self._sent = key
                self.error = None
