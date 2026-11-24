"""KNX Data Secure per security domain: where a group address is secured, and who may send on it.

A coupler with the security extensions used as a Security Proxy splits the installation into
security domains: on each side of it a group address can be secured or in the clear, and the
coupler translates between the two. The domains form a tree from the backbone down: a segment
starts a domain of its own when the coupler feeding it is such a Security Proxy commissioned
securely, or when the project has no coupler for it at all (it could be one; an IP segment without
a coupler is not split). Every other segment belongs to the domain of the segment above it.

A group address with Security ``On`` is secured in every domain, one with ``Off`` in none. In the
default ``Auto`` mode it is decided per domain from the links in it (:class:`GroupSecurity`):

- a group object of a device commissioned in the clear, or a bus interface without security,
  needs it in the clear there; one of a device commissioned securely (a secured interface) allows
  it secured;
- a group object can only be secured or in the clear for all its group addresses, so a group
  address in the clear in a domain puts every other group address of its group objects there in
  the clear too;
- a domain without links of its own takes part only when it connects two or more domains below it
  that have links: it is secured when the domain above or one below is, else in the clear.

Links that contradict each other (``On`` with a device commissioned in the clear, for example)
raise :class:`SecurityContradiction`.

The module works on plain data (:class:`SegmentInfo`, :class:`ObjectLink`,
:class:`InterfaceLink`), so it can be tested without a project.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field

# The state of a group address in one domain while it is decided.
NONE = 0
PLAIN = 1
POSSIBLE = 2
SECURE = 3


class SecurityContradiction(Exception):
    """The links of a group address need it secured and in the clear in the same domain."""


@dataclass(frozen=True)
class SegmentInfo:
    """A segment of the topology: its id, the area and line it belongs to, its number (0 = the
    line's main segment), whether its medium is IP, and whether it starts a domain of its own
    (``proxied``: fed by a Security Proxy commissioned securely, or by no coupler at all)."""

    id: int
    area: int
    line: int
    number: int
    ip: bool
    proxied: bool
    # The coupler feeding the segment (a device id), if the project has one.
    coupler: int | None = None
    # Whether its medium is RF, and its domain address (RF/PL), if it has one.
    rf: bool = False
    domain_address: int | None = None


@dataclass(frozen=True)
class ObjectLink:
    """A group object linked to the group address: the segment of its device, whether the device
    is commissioned securely, and the other group addresses (ids) linked to it. Whether the group
    object itself requires security does not count here: it can only make links contradict each
    other, and the links are checked for that when they are made."""

    segment: int | None
    secure_device: bool
    others: tuple[int, ...] = ()


@dataclass(frozen=True)
class InterfaceLink:
    """A bus interface the group address is assigned to: the segment of its device and whether
    the interface is secured."""

    segment: int | None
    secure: bool


@dataclass
class Domain:
    """A security domain: its segments (the first one is the segment it starts with), the domain
    above it and those below it, and the Security Proxy feeding it (a device id; None for the
    backbone's domain and for a segment without a coupler)."""

    index: int
    parent: int | None
    segments: list[int] = field(default_factory=list[int])
    children: list[int] = field(default_factory=list[int])
    proxy: int | None = None


class Domains:
    """The security domains of a topology, from the backbone down (see the module docstring).

    A main line or backbone the project leaves out has no coupler either: it starts a domain of
    its own, without segments, that the lines below it hang from."""

    def __init__(self, segments: Iterable[SegmentInfo]) -> None:
        self.domains: list[Domain] = []
        self.of_segment: dict[int, int] = {}
        by_line: dict[tuple[int, int], list[SegmentInfo]] = {}
        for segment in segments:
            by_line.setdefault((segment.area, segment.line), []).append(segment)
        for members in by_line.values():
            members.sort(key=lambda s: (s.number, s.id))
        self._by_line = by_line
        self._areas = sorted({area for area, _line in by_line} - {0})
        root = self._new(None, None)
        backbone = self._main((0, 0))
        if backbone is not None:
            root.segments.append(backbone.id)
        self._collect(root, 0, 0)
        for domain in self.domains:
            for segment_id in domain.segments:
                self.of_segment.setdefault(segment_id, domain.index)

    def _main(self, line: tuple[int, int]) -> SegmentInfo | None:
        members = self._by_line.get(line)
        return members[0] if members and members[0].number == 0 else None

    def _new(self, parent: int | None, segment: SegmentInfo | None) -> Domain:
        domain = Domain(
            index=len(self.domains),
            parent=parent,
            proxy=segment.coupler if segment is not None else None,
        )
        self.domains.append(domain)
        if parent is not None:
            self.domains[parent].children.append(domain.index)
        return domain

    def _enter(self, domain: Domain, line: tuple[int, int]) -> None:
        """The main segment of ``line`` and what is below it: in the domain, or in one of its
        own below it."""
        main = self._main(line)
        if main is None or main.proxied:
            child = self._new(domain.index, main)
            if main is not None:
                child.segments.append(main.id)
            self._collect(child, *line)
            return
        domain.segments.append(main.id)
        self._collect(domain, *line)

    def _collect(self, domain: Domain, area: int, line: int) -> None:
        """What is below the main segment of ``area.line``: the line's other segments, the other
        lines of the area for a main line, the other areas for the backbone."""
        for segment in self._by_line.get((area, line), []):
            if segment.number == 0:
                continue
            if segment.proxied:
                self._new(domain.index, segment).segments.append(segment.id)
            else:
                domain.segments.append(segment.id)
        if line != 0:
            return
        for key in sorted(self._by_line):
            if key[0] == area and key[1] != 0:
                self._enter(domain, key)
        if area != 0:
            return
        for other in self._areas:
            self._enter(domain, (other, 0))

    def descendants(self, index: int) -> list[int]:
        """The domain and every domain below it, depth first."""
        found = [index]
        for child in self.domains[index].children:
            found.extend(self.descendants(child))
        return found

    def adjacent(self, index: int) -> list[int]:
        """The domain above and those below."""
        domain = self.domains[index]
        above = [] if domain.parent is None else [domain.parent]
        return [*above, *domain.children]


class GroupSecurity:
    """Where group addresses are secured, per domain.

    ``mode(ga)`` is the Security mode that counts for a group address (``"On"``, ``"Off"`` or
    ``None``/``"Auto"``), ``objects(ga)`` and ``interfaces(ga)`` its links."""

    def __init__(
        self,
        domains: Domains,
        mode: Callable[[int], str | None],
        objects: Callable[[int], Sequence[ObjectLink]],
        interfaces: Callable[[int], Sequence[InterfaceLink]],
    ) -> None:
        self.domains = domains
        self._mode = mode
        self._objects = objects
        self._interfaces = interfaces
        self._states: dict[int, dict[int, int]] = {}

    def states(self, ga: int) -> dict[int, int]:
        """The domains taking part in the group address in ``Auto`` mode, each ``PLAIN`` or
        ``SECURE``; :class:`SecurityContradiction` when its links contradict each other."""
        if ga not in self._states:
            result = self._determine(ga, {}, None)
            self._states[ga] = dict(result or {})
        return self._states[ga]

    def secured(self, ga: int, domain: int) -> bool | None:
        """Whether the group address is secured in the domain; None when it does not take part
        there (``Auto`` mode only: ``On`` and ``Off`` count everywhere)."""
        mode = self._mode(ga)
        if mode == "On":
            return True
        if mode == "Off":
            return False
        state = self.states(ga).get(domain)
        return None if state is None else state == SECURE

    # -- the decision, per domain --------------------------------------------------------------

    def _domain(self, segment: int | None, allowed: set[int] | None) -> int | None:
        if segment is None:
            return None
        domain = self.domains.of_segment.get(segment)
        if domain is None or (allowed is not None and domain not in allowed):
            return None
        return domain

    def _determine(
        self,
        ga: int,
        visited: dict[int, dict[int, int] | None],
        allowed: set[int] | None,
    ) -> dict[int, int] | None:
        if ga in visited:
            return visited[ga]
        visited[ga] = None
        mode = self._mode(ga)
        order = self.domains.descendants(0) if self.domains.domains else []
        sec = dict.fromkeys(order, NONE)
        touched: set[int] = set()
        pairs: list[tuple[int, int]] = []
        for link in self._objects(ga):
            domain = self._domain(link.segment, allowed)
            if domain is None:
                continue
            touched.add(domain)
            if mode == "On":
                if sec[domain] == PLAIN:
                    raise SecurityContradiction(ga)
                sec[domain] = SECURE
            elif mode == "Off":
                if sec[domain] == SECURE:
                    raise SecurityContradiction(ga)
                sec[domain] = PLAIN
            if not link.secure_device:
                if sec[domain] == SECURE:
                    raise SecurityContradiction(ga)
                sec[domain] = PLAIN
                continue
            if sec[domain] == NONE:
                sec[domain] = POSSIBLE
            for other in link.others:
                if other not in visited and (domain, other) not in pairs:
                    pairs.append((domain, other))
        for interface in self._interfaces(ga):
            domain = self._domain(interface.segment, allowed)
            if domain is None:
                continue
            touched.add(domain)
            if mode == "On":
                if sec[domain] == PLAIN or not interface.secure:
                    raise SecurityContradiction(ga)
                sec[domain] = SECURE
            elif mode == "Off" or not interface.secure:
                if sec[domain] == SECURE:
                    raise SecurityContradiction(ga)
                sec[domain] = PLAIN
            elif sec[domain] == NONE:
                sec[domain] = POSSIBLE
        # A group object is secured for all its group addresses or for none.
        by_other: dict[int, list[int]] = {}
        for domain, other in pairs:
            by_other.setdefault(other, []).append(domain)
        for other, other_domains in by_other.items():
            result = self._determine(
                other, visited, allowed if allowed is not None else touched
            )
            if result is None:
                continue
            for domain in other_domains:
                if result.get(domain) == PLAIN:
                    if sec[domain] == SECURE:
                        raise SecurityContradiction(ga)
                    sec[domain] = PLAIN
        for domain in order:
            if sec[domain] == POSSIBLE:
                sec[domain] = SECURE
        self._connect(sec)
        visited[ga] = sec
        return sec

    def _active_below(self, sec: Mapping[int, int], child: int) -> bool:
        return any(sec.get(d, NONE) != NONE for d in self.domains.descendants(child))

    def _connect(self, sec: dict[int, int]) -> None:
        """Decide the domains without links: those connecting domains with links take part, the
        rest are left out."""
        domains = self.domains.domains
        transit = {
            d
            for d, state in sec.items()
            if state == NONE
            and sum(self._active_below(sec, c) for c in domains[d].children) >= 2
        }
        changed = True
        while changed:
            changed = False
            for d in [d for d, state in sec.items() if state == NONE]:
                parent = domains[d].parent
                if parent is None:
                    continue
                if sec[parent] == NONE and parent not in transit:
                    continue
                if d not in transit and any(
                    self._active_below(sec, c) for c in domains[d].children
                ):
                    transit.add(d)
                    changed = True
        for d in [d for d, state in sec.items() if state == NONE and d not in transit]:
            del sec[d]
        while any(state == NONE for state in sec.values()):
            changed = False
            for d in [d for d, state in sec.items() if state == NONE]:
                parent = domains[d].parent
                children = domains[d].children
                if (parent is not None and sec.get(parent) == SECURE) or any(
                    sec.get(c) == SECURE for c in children
                ):
                    sec[d] = SECURE
                    changed = True
                elif (parent is None or sec.get(parent) == PLAIN) and all(
                    sec.get(c) == PLAIN for c in children
                ):
                    sec[d] = PLAIN
                    changed = True
            if not changed:
                for d in [d for d, state in sec.items() if state == NONE]:
                    sec[d] = PLAIN

    # -- who may send ----------------------------------------------------------------------------

    def extent(
        self, ga: int, domain: int, do_not_join: int | None = None
    ) -> tuple[set[int], list[int]]:
        """The domains a secured group address reaches from ``domain`` without being translated
        (neighbours where it is secured too), and the Security Proxies at its edge that translate
        it from a neighbour where it is in the clear (device ids)."""
        reached: set[int] = set()
        proxies: list[int] = []
        queue = [domain]
        while queue:
            current = queue.pop(0)
            if current in reached:
                continue
            reached.add(current)
            if current == do_not_join:
                continue
            for neighbour in self.domains.adjacent(current):
                if neighbour == do_not_join or neighbour in reached:
                    continue
                secured = self.secured(ga, neighbour)
                if secured is True:
                    queue.append(neighbour)
                elif secured is False:
                    edge = (
                        current
                        if neighbour == self.domains.domains[current].parent
                        else neighbour
                    )
                    proxy = self.domains.domains[edge].proxy
                    if proxy is not None:
                        proxies.append(proxy)
        return reached, proxies
