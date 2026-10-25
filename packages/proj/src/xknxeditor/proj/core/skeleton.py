"""Lay down a new project's baseline, matching a fresh project's starting state.

The baseline is not undoable, so it goes in directly (no event): a ``Project`` row (``ThreeLevel``
style) plus installation 0 with the default KNX topology, a default building, and an empty
group-address tree.

The topology follows the KNX standard for a fresh project: an IP backbone (Area 0 / Line 0, ``0.0``),
plus Area 1 with an IP main line (``1.0``) and a TP line (``1.1``) where end devices belong. New
devices default onto the TP line ``1.1`` (KNX best practice: backbone and main line carry only
couplers, not end devices). The building tree starts with one ``Building`` named after the project,
with ``1.1`` as its Current Line.
"""

from uuid import uuid4

from sqlalchemy.orm import Session

from xknxeditor.proj.core.addressing import GroupAddressStyle
from xknxeditor.proj.models import Area, Installation, Line, Project, Segment, Space

MEDIUM_IP = "MT-5"
MEDIUM_TP = "MT-0"

DEFAULT_INSTALLATION = 0


def seed_new_project(
    session: Session,
    project_id: str,
    name: str,
    group_address_style: GroupAddressStyle = GroupAddressStyle.THREE_LEVEL,
    building_name: str = "",
) -> None:
    """Add the baseline project plus installation 0, the default KNX topology and a default building.

    Topology: IP backbone (Area 0 / Line 0), and Area 1 with an IP main line (Line 0, ``1.0``) and a
    TP line (Line 1, ``1.1``). New devices default onto the ``1.1`` TP line (see module docstring).
    The building tree gets one top-level ``Building`` named ``building_name`` (the project file name).
    """
    # A stable Guid identifies the project across (re-)exports; the importer expects every project
    # to have one, and rejects a project without it. Generate it once at creation.
    session.add(
        Project(
            id=project_id,
            name=name,
            group_address_style=group_address_style,
            guid=str(uuid4()),
        )
    )
    installation = Installation(index=DEFAULT_INSTALLATION, name="")
    # Area 0: IP backbone line (0.0) - area couplers only, no end devices.
    installation.areas.append(
        Area(
            address=0,
            lines=[
                Line(
                    address=0,
                    segments=[Segment(number=0, medium_type=MEDIUM_IP)],
                )
            ],
        )
    )
    # Area 1: IP main line (1.0) for line couplers, plus a TP line (1.1) that hosts end devices.
    tp_line = Line(address=1, segments=[Segment(number=0, medium_type=MEDIUM_TP)])
    installation.areas.append(
        Area(
            address=1,
            lines=[
                Line(
                    address=0,
                    segments=[Segment(number=0, medium_type=MEDIUM_IP)],
                ),
                tp_line,
            ],
        )
    )
    # A default building (named after the project file) so the building tree is not empty. Its
    # Current Line is the TP line, so devices added to the building land there.
    installation.spaces.append(
        Space(space_type="Building", name=building_name, order=0, default_line=tp_line)
    )
    session.add(installation)
    session.commit()
