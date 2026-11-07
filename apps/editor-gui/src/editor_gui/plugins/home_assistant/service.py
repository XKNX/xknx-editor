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

import base64
import contextlib
import secrets
import tempfile
import threading
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

from xknx.telegram.apci import GroupValueRead, GroupValueWrite

from editor_gui.plugins.home_assistant.areas import (
    AreaRow,
    by_id,
    compare,
    plan_spaces,
    request,
    space_key,
)
from editor_gui.plugins.home_assistant.entities import (
    EntityCandidate,
    addresses_differ,
    entity_candidates,
    entity_request,
    existing_entities,
)
from editor_gui.plugins.home_assistant.imports import (
    HomeAssistantAddress,
    HomeAssistantSpace,
    addresses_to_add,
    spaces_to_add,
)
from editor_gui.plugins.home_assistant.strings import S
from editor_gui.settings import load_settings, save_settings
from xknxeditor.proj.core.space_rules import BUILDING, FLOOR, ROOM

if TYPE_CHECKING:
    from editor_gui.plugins.base import Logger
    from editor_gui.plugins.project.service import ProjectService
    from editor_gui.web.host_link import HostLink
    from xknxeditor.proj.core.service import SpaceInfo

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
    # The datapoint type Home Assistant's KNX devices use it with ("9.001"; None: unknown).
    dpt: str | None = None


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
    """The usage the integration reports: {raw address (as text): {users, sends, dpt}}."""
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
            dpt=str(dpt) if isinstance(dpt := data.get("dpt"), str) and dpt else None,
        )
    return result


def _time_server(value: object) -> dict[str, str | None] | None:
    """The time server's group addresses as the integration reports them."""
    if not isinstance(value, dict):
        return None
    served = cast("dict[str, object]", value)
    return {
        kind: address if isinstance(address := served.get(kind), str) else None
        for kind in ("time", "date", "datetime")
    }


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
    # The created entity has other group addresses than the function now.
    changed: bool = False
    # Every device on its group addresses has its links downloaded (an entity follows its
    # function only then: before, the devices still use the old addresses).
    downloaded: bool = True


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
        # The ``knx`` part of the stored configuration of each created entity, by entity id.
        self.entity_configs: dict[str, dict[str, Any] | None] = {}
        # Where the KNX integration's time server sends to ({time, date, datetime}: a printed
        # address or None), and why the editor's keys cannot be its (None: they can).
        self.time_server: dict[str, str | None] | None = None
        self.keyring_refused: str | None = None
        # Requests answered by a result, by id: what takes the answer.
        self._requests: dict[int, Callable[[dict[str, Any]], None]] = {}
        # The outcome of the last of them: why it failed (None: it did not).
        self.request_error: str | None = None
        # Told what a request did (a text for the user) once it is answered.
        self.on_done: Callable[[str], None] | None = None

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

    def apply_areas(
        self, rows: list[AreaRow], targets: dict[str, str] | None = None
    ) -> bool:
        """Create, link or rename the floors and areas of ``rows`` in Home Assistant (those of
        ``targets``, by key, are linked to the floor or area given there as it is); False
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
        message = {"type": "apply_areas", "id": self._next_id, **request(rows, targets)}
        if not link.send(message):
            return False
        self._areas_request = self._next_id
        self.areas_error = None
        return True

    # --- from Home Assistant: floors and areas, group addresses ---

    def spaces_to_add(self) -> list[HomeAssistantSpace]:
        """Home Assistant's floors and areas the open project has no space for."""
        guid = self.project_guid()
        if guid is None or self.home_assistant_areas is None:
            return []
        planned = plan_spaces(self._project.get_space_tree(), guid)
        return spaces_to_add(planned, self.home_assistant_areas)

    def buildings(self) -> list[SpaceInfo]:
        """The project's buildings (where floors and rooms from Home Assistant go)."""
        return [s for s in self._project.get_space_tree() if s.space_type == BUILDING]

    def add_spaces(
        self, spaces: Sequence[HomeAssistantSpace], building_id: int | None = None
    ) -> int:
        """Add Home Assistant's floors (``spaces``) as floors of the building ``building_id``
        (None: the first one; a new one named like Home Assistant's home when there is none)
        and its areas as rooms on their floors (on no floor: in the building), one undo step;
        then link them to the floors and areas they were made from. Returns how many spaces
        were added."""
        guid = self.project_guid()
        if guid is None or not spaces:
            return 0
        tree = self._project.get_space_tree()
        planned = plan_spaces(tree, guid)
        state = self.home_assistant_areas or {}
        # The project's floors by Home Assistant's floor: linked, or of the same name.
        floor_spaces: dict[str, int] = {}
        links = state.get("links")
        if isinstance(links, dict):
            for key, ha_id in cast("dict[object, object]", links).items():
                prefix, _, space_id = str(key).rpartition(":")
                if prefix == guid and space_id.isdigit():
                    floor_spaces.setdefault(str(ha_id), int(space_id))
        by_name = {p.name.casefold(): p.space_id for p in planned if p.kind == "floor"}
        for ha_id, floor in by_id(state.get("floors"), "floor_id").items():
            if (sid := by_name.get(str(floor.get("name", "")).casefold())) is not None:
                floor_spaces.setdefault(ha_id, sid)
        targets: dict[str, str] = {}
        with self._project.one_undo_step(S.UNDO_ADD_SPACES):
            if building_id is None:
                buildings = [s.id for s in tree if s.space_type == BUILDING]
                building_id = buildings[0] if buildings else None
            if building_id is None:
                home = state.get("location_name")
                building_id = self._project.create_space(
                    None, BUILDING, str(home) if home else S.DEFAULT_BUILDING
                )
            if building_id is None:
                return 0
            for space in spaces:
                if space.kind != "floor":
                    continue
                sid = self._project.create_space(building_id, FLOOR, space.name)
                if sid is None:
                    continue
                if space.level is not None:
                    self._project.set_space_number(sid, str(space.level))
                floor_spaces[space.ha_id] = sid
                targets[space_key(guid, sid)] = space.ha_id
            for space in spaces:
                if space.kind != "area":
                    continue
                parent = floor_spaces.get(space.floor_id or "", building_id)
                sid = self._project.create_space(parent, ROOM, space.name)
                if sid is not None:
                    targets[space_key(guid, sid)] = space.ha_id
        rows = [row for row in self.area_rows() if row.planned.key in targets]
        self.apply_areas(rows, targets)
        return len(targets)

    def addresses_to_add(self) -> list[HomeAssistantAddress]:
        """The group addresses Home Assistant uses that the open project does not have."""
        if not self._project.is_open:
            return []
        known = {ga.raw for ga in self._project.group_addresses}
        return addresses_to_add(self.usage, known)

    def add_group_addresses(self, addresses: Sequence[HomeAssistantAddress]) -> int:
        """Add ``addresses`` to the project with their names and datapoint types, one undo step;
        returns how many were added."""
        if not self._project.is_open or not addresses:
            return 0
        added = 0
        with self._project.one_undo_step(S.UNDO_ADD_GROUP_ADDRESSES):
            for address in addresses:
                ga_id = self._project.create_group_address_value(
                    address.raw, address.name
                )
                if ga_id is None:
                    continue
                added += 1
                # A type the project cannot take: the address is added without one.
                if address.dpt:
                    with contextlib.suppress(ValueError):
                        self._project.set_group_address_dpt(ga_id, address.dpt)
        return added

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
            self._entity_row(c, area_links.get(c.space_key), users) for c in candidates
        ]

    def _entity_row(
        self,
        candidate: EntityCandidate,
        area_id: str | None,
        users: dict[int, tuple[dict[str, Any], ...]],
    ) -> EntityRow:
        created = self.entity_links.get(candidate.key)
        changed = created is not None and addresses_differ(
            candidate.knx, self.entity_configs.get(created)
        )
        return EntityRow(
            candidate=candidate,
            area_id=area_id,
            # Its own entity uses the address too: not another one.
            existing=tuple(
                e for e in existing_entities(candidate, users) if e != created
            ),
            check=self.entity_checks.get(candidate.key),
            created=created,
            changed=changed,
            downloaded=not changed or self.downloaded(candidate.group_address_ids),
        )

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

    # --- Home Assistant's connection ---

    @property
    def can_send_group(self) -> bool:
        """Whether a group telegram can go through the KNX integration's connection."""
        return self.connected and self.knx_loaded

    def send_group(self, address: str, apci: GroupValueWrite | GroupValueRead) -> bool:
        """Send a group read or write over the KNX integration's connection (its services
        knx.send and knx.read); False when it cannot."""
        link = self._link()
        if link is None or not self.can_send_group:
            return False
        message: dict[str, Any] = {"type": "group_send", "address": address}
        if isinstance(apci, GroupValueRead):
            message["read"] = True
        else:
            value = apci.value.value
            message["payload"] = value if isinstance(value, int) else list(value)
        return link.send(message)

    def connection_target(self) -> tuple[str, str] | None:
        """How to reach the bus as the KNX integration does, (transport, address: the gateway
        or the multicast group), for the editor's own connection; None for one the editor
        cannot take over (a secure one, or one found automatically)."""
        connection = self.connection or {}
        transport = connection.get("transport")
        host = connection.get("host")
        if transport not in ("tunneling", "tunneling_tcp", "routing") or not host:
            return None
        return str(transport), str(host)

    # --- follow-ups: entities, time server, keys ---

    @property
    def requesting(self) -> bool:
        """Whether a request of the follow-ups is waiting for its answer."""
        return bool(self._requests)

    def _ask(
        self, message: dict[str, Any], done: Callable[[dict[str, Any]], str]
    ) -> bool:
        """Send a request; ``done`` turns its answer into the text the user is told."""
        link = self._link()
        if link is None or not self.connected:
            return False
        self._next_id += 1
        if not link.send({**message, "id": self._next_id}):
            return False
        self.request_error = None

        def take(answer: dict[str, Any]) -> None:
            if (error := answer.get("error")) is not None:
                self.request_error = str(error)
                return
            errors = _texts(answer.get("errors"))
            if errors:
                self.request_error = "; ".join(errors.values())
            if self.on_done is not None:
                self.on_done(done(answer))

        self._requests[self._next_id] = take
        return True

    def downloaded(self, group_address_ids: tuple[int, ...]) -> bool:
        """Whether every device linked to these group addresses has its links downloaded."""
        wanted = set(group_address_ids)
        devices = {
            link.device_id
            for link in self._project.device_links()
            if link.group_address_id in wanted
        }
        for device_id in devices:
            info = self._project.get_device_info(device_id)
            if info is None or not info.communication_part_loaded:
                return False
        return True

    def orphans(self) -> list[tuple[str, str]]:
        """The entities created for building functions the open project no longer has (or that
        no longer make one): (key, entity id)."""
        guid = self.project_guid()
        if guid is None:
            return []
        current = {row.candidate.key for row in self.entity_rows()}
        prefix = f"{guid}:function:"
        return [
            (key, entity_id)
            for key, entity_id in sorted(self.entity_links.items())
            if key.startswith(prefix) and key not in current
        ]

    def update_entities(self, rows: list[EntityRow]) -> bool:
        """Give the created entities of ``rows`` their functions' group addresses."""
        entities = [{"key": r.candidate.key, "knx": r.candidate.knx} for r in rows]
        return bool(entities) and self._ask(
            {"type": "update_entities", "entities": entities},
            lambda answer: S.DONE_UPDATED.format(
                count=len(answer.get("updated") or [])
            ),
        )

    def remove_entities(self, keys: list[str]) -> bool:
        """Remove the entities of these keys (their functions are gone) from Home Assistant."""
        return bool(keys) and self._ask(
            {"type": "delete_entities", "keys": keys},
            lambda answer: S.DONE_REMOVED.format(
                count=len(answer.get("removed") or [])
            ),
        )

    def set_time_server(self, addresses: dict[str, str | None]) -> bool:
        """Let the KNX integration's time server send to these group addresses."""
        return self._ask(
            {"type": "set_time_server", "addresses": addresses},
            lambda _answer: S.DONE_TIME_SERVER,
        )

    def send_keys(self) -> bool:
        """Make the project's keys (its Data Secure group keys) the KNX integration's keyring,
        under a new random password."""
        if self.keyring_refused is not None or not self._project.is_open:
            return False
        password = secrets.token_urlsafe(24)
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "project.knxkeys"
            try:
                self._project.export_keyring(path, password)
            except Exception as exc:
                self.request_error = str(exc) or type(exc).__name__
                return False
            keyring = base64.b64encode(path.read_bytes()).decode("ascii")
        return self._ask(
            {"type": "set_keyring", "keyring": keyring, "password": password},
            lambda _answer: S.DONE_KEYS,
        )

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
            self.entity_configs = {}
            self.time_server = None
            self.keyring_refused = None
            self._requests = {}
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
            self.time_server = _time_server(message.get("time_server"))
            refused = message.get("keyring_refused")
            self.keyring_refused = str(refused) if refused is not None else None
            if not self.linked:
                # Another project replaced ours (e.g. an upload in the KNX panel).
                self._sent = None
        elif kind == "usage":
            self.usage = _usage(message.get("group_addresses"))
        elif kind == "areas":
            self.home_assistant_areas = {
                key: message.get(key)
                for key in ("floors", "areas", "links", "location_name")
            }
        elif kind == "entity_links":
            links = message.get("links")
            self.entity_links = (
                {str(k): str(v) for k, v in cast("dict[object, object]", links).items()}
                if isinstance(links, dict)
                else {}
            )
            configs = message.get("configs")
            self.entity_configs = (
                {
                    str(k): cast("dict[str, Any]", v) if isinstance(v, dict) else None
                    for k, v in cast("dict[object, object]", configs).items()
                }
                if isinstance(configs, dict)
                else {}
            )
        elif kind == "result" and message.get("id") in self._requests:
            self._requests.pop(cast("int", message.get("id")))(message)
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
