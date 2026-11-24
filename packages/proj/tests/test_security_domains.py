"""KNX Data Secure per security domain (``security_domains``)."""

from __future__ import annotations

import pytest

from xknxeditor.proj.core.security_domains import (
    Domains,
    GroupSecurity,
    InterfaceLink,
    ObjectLink,
    SecurityContradiction,
    SegmentInfo,
)

# Backbone 0.0, area 1 with its main line 1.0 and the lines 1.1 and 1.2.
BACKBONE, MAIN, LINE1, LINE2 = 1, 2, 3, 4


def _segments(*, line1_proxy: bool, line2_proxy: bool = False) -> list[SegmentInfo]:
    return [
        SegmentInfo(BACKBONE, 0, 0, 0, ip=True, proxied=False),
        SegmentInfo(MAIN, 1, 0, 0, ip=True, proxied=False),
        SegmentInfo(LINE1, 1, 1, 0, ip=False, proxied=line1_proxy, coupler=11),
        SegmentInfo(LINE2, 1, 2, 0, ip=False, proxied=line2_proxy, coupler=12),
    ]


def _security(
    domains: Domains,
    objects: dict[int, list[ObjectLink]],
    interfaces: dict[int, list[InterfaceLink]] | None = None,
    modes: dict[int, str | None] | None = None,
) -> GroupSecurity:
    return GroupSecurity(
        domains,
        lambda ga: (modes or {}).get(ga),
        lambda ga: objects.get(ga, []),
        lambda ga: (interfaces or {}).get(ga, []),
    )


def test_a_security_proxy_starts_a_domain() -> None:
    domains = Domains(_segments(line1_proxy=True))
    root = domains.of_segment[BACKBONE]
    # The main line and line 1.2 belong to the backbone's domain, line 1.1 has its own.
    assert domains.of_segment[MAIN] == root == domains.of_segment[LINE2]
    line1 = domains.of_segment[LINE1]
    assert line1 != root
    assert domains.domains[line1].parent == root
    assert domains.domains[line1].proxy == 11
    assert domains.adjacent(line1) == [root]


def test_a_missing_main_line_starts_a_domain_of_its_own() -> None:
    domains = Domains([SegmentInfo(7, 2, 1, 0, ip=False, proxied=True)])
    # Root (no backbone), the area's main line (left out of the project), line 2.1.
    assert len(domains.domains) == 3
    line = domains.of_segment[7]
    area = domains.domains[line].parent
    assert area is not None and domains.domains[area].segments == []
    assert domains.domains[area].parent == 0


def test_a_sub_segment_joins_or_starts_a_domain() -> None:
    segments = [
        *_segments(line1_proxy=False),
        SegmentInfo(5, 1, 1, 1, ip=False, proxied=False),
        SegmentInfo(6, 1, 1, 2, ip=False, proxied=True, coupler=13),
    ]
    domains = Domains(segments)
    assert domains.of_segment[5] == domains.of_segment[LINE1]
    assert domains.of_segment[6] != domains.of_segment[LINE1]
    assert domains.domains[domains.of_segment[6]].proxy == 13


def test_auto_follows_the_devices_of_each_domain() -> None:
    domains = Domains(_segments(line1_proxy=True))
    security = _security(
        domains,
        {
            100: [
                ObjectLink(LINE1, secure_device=True),
                ObjectLink(LINE2, secure_device=False),
            ]
        },
    )
    line1, root = domains.of_segment[LINE1], domains.of_segment[BACKBONE]
    # Secured behind the Security Proxy, in the clear in front of it.
    assert security.secured(100, line1) is True
    assert security.secured(100, root) is False
    # A domain the group address does not reach takes no part.
    other = Domains(_segments(line1_proxy=True, line2_proxy=True))
    security = _security(other, {100: [ObjectLink(LINE1, secure_device=True)]})
    assert security.secured(100, other.of_segment[LINE2]) is None


def test_a_domain_connecting_two_others_takes_part() -> None:
    domains = Domains(_segments(line1_proxy=True, line2_proxy=True))
    root = domains.of_segment[BACKBONE]
    secured_both = _security(
        domains,
        {
            100: [
                ObjectLink(LINE1, secure_device=True),
                ObjectLink(LINE2, secure_device=True),
            ]
        },
    )
    # The backbone's domain carries it between the two lines: secured, as both are.
    assert secured_both.secured(100, root) is True
    mixed = _security(
        domains,
        {
            100: [
                ObjectLink(LINE1, secure_device=True),
                ObjectLink(LINE2, secure_device=False),
            ]
        },
    )
    assert mixed.secured(100, root) is True
    plain = _security(
        domains,
        {
            100: [
                ObjectLink(LINE1, secure_device=False),
                ObjectLink(LINE2, secure_device=False),
            ]
        },
    )
    assert plain.secured(100, root) is False


def test_a_group_object_is_secured_for_all_its_group_addresses_or_none() -> None:
    domains = Domains(_segments(line1_proxy=False))
    security = _security(
        domains,
        {
            # 100 alone would be secured, but its group object also sends on 200, which a
            # device commissioned in the clear needs in the clear.
            100: [ObjectLink(LINE1, secure_device=True, others=(200,))],
            200: [
                ObjectLink(LINE1, secure_device=True, others=(100,)),
                ObjectLink(LINE2, secure_device=False),
            ],
        },
    )
    root = domains.of_segment[BACKBONE]
    assert security.secured(200, root) is False
    assert security.secured(100, root) is False


def test_bus_interfaces_count_like_group_objects() -> None:
    domains = Domains(_segments(line1_proxy=False))
    root = domains.of_segment[BACKBONE]
    plain = _security(
        domains,
        {100: [ObjectLink(LINE1, secure_device=True)]},
        {100: [InterfaceLink(MAIN, secure=False)]},
    )
    assert plain.secured(100, root) is False
    secure = _security(
        domains,
        {100: [ObjectLink(LINE1, secure_device=True)]},
        {100: [InterfaceLink(MAIN, secure=True)]},
    )
    assert secure.secured(100, root) is True
    # A secured interface alone secures it.
    alone = _security(domains, {}, {100: [InterfaceLink(MAIN, secure=True)]})
    assert alone.secured(100, root) is True


def test_on_and_off_count_everywhere() -> None:
    domains = Domains(_segments(line1_proxy=True))
    security = _security(
        domains,
        {100: [ObjectLink(LINE1, secure_device=True)]},
        modes={100: "On", 200: "Off"},
    )
    for domain in range(len(domains.domains)):
        assert security.secured(100, domain) is True
        assert security.secured(200, domain) is False


@pytest.mark.parametrize(
    ("objects", "interfaces"),
    [
        ([ObjectLink(LINE1, secure_device=False)], []),
        ([], [InterfaceLink(LINE1, secure=False)]),
    ],
)
def test_on_contradicts_links_in_the_clear(
    objects: list[ObjectLink], interfaces: list[InterfaceLink]
) -> None:
    domains = Domains(_segments(line1_proxy=False))
    security = _security(domains, {100: objects}, {100: interfaces}, {100: "On"})
    with pytest.raises(SecurityContradiction):
        security.states(100)


def test_off_puts_secure_links_in_the_clear() -> None:
    domains = Domains(_segments(line1_proxy=False))
    security = _security(
        domains,
        {100: [ObjectLink(LINE1, secure_device=True)]},
        {100: [InterfaceLink(LINE1, secure=True)]},
        {100: "Off"},
    )
    assert security.states(100) == {domains.of_segment[BACKBONE]: 1}


def test_the_extent_and_the_proxies_at_its_edge() -> None:
    domains = Domains(_segments(line1_proxy=True, line2_proxy=True))
    line1, line2, root = (
        domains.of_segment[LINE1],
        domains.of_segment[LINE2],
        domains.of_segment[BACKBONE],
    )
    security = _security(
        domains,
        {
            100: [
                ObjectLink(LINE1, secure_device=True),
                ObjectLink(LINE2, secure_device=False),
            ]
        },
    )
    # Secured on line 1.1 and the backbone (connecting both lines), in the clear on 1.2: the
    # Security Proxy of line 1.2 translates it into the secured part.
    reached, proxies = security.extent(100, line1)
    assert reached == {line1, root}
    assert proxies == [12]
    # Without joining the backbone, line 1.1 stays alone.
    reached, proxies = security.extent(100, line1, do_not_join=root)
    assert reached == {line1}
    assert proxies == []
    assert security.secured(100, line2) is False


def test_the_local_domain_of_a_connection() -> None:
    from types import SimpleNamespace

    from xknxeditor.proj.core.ga_security import ProjectSecurity

    rf_line, rf_other = 5, 6
    segments = [
        *_segments(line1_proxy=True),
        SegmentInfo(
            rf_line, 1, 1, 1, ip=False, proxied=True, rf=True, domain_address=7
        ),
        SegmentInfo(
            rf_other, 1, 2, 1, ip=False, proxied=True, rf=True, domain_address=8
        ),
    ]
    domains = Domains(segments)
    security = SimpleNamespace(segments={s.id: s for s in segments}, domains=domains)

    def local(address: int, rf: int | None = None) -> int | None:
        return ProjectSecurity.local_domain(security, address, rf)  # type: ignore[arg-type]

    # The main segment of the connection's line.
    assert local(0x11FF) == domains.of_segment[LINE1]
    assert local(0x12FF) == domains.of_segment[LINE2]
    assert local(0x31FF) is None
    # Through an RF interface: the RF segment with its domain address, its line's first.
    assert local(0x11FF, 7) == domains.of_segment[rf_line]
    assert local(0x31FF, 8) == domains.of_segment[rf_other]
    assert local(0x11FF, 9) is None
