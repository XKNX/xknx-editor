"""SQLAlchemy ORM models for a project's SQLite document — one database per project.

The package's *own* models (no IR import), modelled as a slice of the KNX IR
(`xknxeditor.namespaces.intermediate`) so a future `.knxproj` converter stays a plain field mapping.
Rows are keyed by internal autoincrement integers; the `.knxproj` `id`/`puid` strings are a boundary
concern and not persisted.

Layout: ``Installation → Area → Line → Segment → Device``, with group addresses in their own
recursive ``GroupRange`` tree. ``Event`` holds undo/redo history (see :mod:`.core.event_store`); the
other tables are the live state.
"""

import datetime
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    LargeBinary,
    String,
    Text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    """Declarative base shared by every project ORM model."""


class Project(Base):
    """The one metadata row describing this project database."""

    __tablename__ = "projects"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    name: Mapped[str] = mapped_column(Text, nullable=False, default="")
    group_address_style: Mapped[str] = mapped_column(
        String, nullable=False, default="ThreeLevel"
    )
    # Descriptive metadata carried over from the imported .knxproj (project information).
    guid: Mapped[str] = mapped_column(String, nullable=False, default="")
    created_by: Mapped[str] = mapped_column(Text, nullable=False, default="")
    last_modified: Mapped[str] = mapped_column(String, nullable=False, default="")
    schema_version: Mapped[str] = mapped_column(String, nullable=False, default="")
    tool_version: Mapped[str] = mapped_column(String, nullable=False, default="")
    # Protection artifacts carried over verbatim from the imported .knxproj so an export is a
    # loadable project again: the signed knx_master.xml, the project's ".validation" file and its
    # "<pid>.certificate". Empty when the source project had none (e.g. a freshly created one).
    knx_master_xml: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    # The imported archive's manufacturer product data (``M-XXXX/*`` and ``M-XXXX.signature``
    # members), stored as a ZIP blob exactly as the source carried it. ETS re-issues product ids on
    # import, so resolving them through the catalog on export misses most folders; re-emitting these
    # original members keeps a re-exported ETS-imported project importable. ``None`` when the project
    # was not imported from a ``.knxproj`` (e.g. created from scratch) or carried no manufacturer data.
    imported_product_members: Mapped[bytes | None] = mapped_column(
        LargeBinary, nullable=True
    )
    # The other files of the imported archive's project folder (eg. attached user files or data of
    # device configuration apps), stored as a ZIP blob and written back on export. Excludes what the
    # export generates itself: project.xml, the installation files and BinaryData/*.
    imported_project_files: Mapped[bytes | None] = mapped_column(
        LargeBinary, nullable=True
    )
    knx_validation: Mapped[str] = mapped_column(Text, nullable=False, default="")
    knx_certificate: Mapped[str] = mapped_column(Text, nullable=False, default="")
    # The project id (P-XXXX) in the source .knxproj. The certificate/.validation are bound to it,
    # so an export of a protected project must reuse this id.
    original_project_id: Mapped[str] = mapped_column(String, nullable=False, default="")
    # The ProjectInformation free-text comment, carried over so a round-trip preserves it.
    comment: Mapped[str] = mapped_column(Text, nullable=False, default="")
    # ProjectInformation attributes xknxproject does not surface (ProjectStart, ProjectNumber,
    # ContractNumber, ProjectEnd, ProjectId, ProjectTracingPassword — valid in project/14, /20, /23),
    # captured verbatim and re-emitted. ``None`` when the source had none.
    extra_attributes: Mapped[dict[str, str] | None] = mapped_column(JSON)
    # ProjectInformation child collections xknxproject drops, captured verbatim (namespace-stripped
    # XML) and re-emitted: ``{"Tags": ..., "HistoryEntries": ..., "ToDoItems": ...,
    # "DeviceCertificates": ...}``. ``None`` when the source had none. (ProjectTraces are modelled
    # separately; UserFiles are archive files, not carried here.)
    info_subtrees: Mapped[dict[str, str] | None] = mapped_column(JSON)
    # Project-level (not ProjectInformation) child collections xknxproject drops, captured verbatim
    # from project.xml and re-emitted after ProjectInformation: ``{"UserFiles": ..., "AddinData":
    # ...}``. ``None`` when the source had none. ``UserFiles`` carries file references only (the
    # attached file bytes are not round-tripped).
    extra_subtrees: Mapped[dict[str, str] | None] = mapped_column(JSON)
    # Import-loss notes (JSON list, see core.import_notes): data the source .knxproj carried that our
    # store cannot fully represent (merged installations, unassigned devices, per-instance com-object
    # texts, IP config, multi-segment lines). Echoed at export so the user is reminded. Empty for a
    # freshly created project or a lossless import.
    import_notes: Mapped[str] = mapped_column(Text, nullable=False, default="")
    # Password protection (see core.project_secrets): the salt of the key derived from the project
    # password and a value encrypted with that key, to detect a wrong password. The password itself
    # is not stored. ``None`` when the project is not protected.
    password_salt: Mapped[str | None] = mapped_column(String)
    password_check: Mapped[str | None] = mapped_column(String)

    # ETS project-log entries (ProjectInformation/ProjectTraces), carried over verbatim so a
    # round-trip export preserves the project history. The ``comment`` is stored exactly as it
    # appears in the source (stored encrypted; we neither decrypt nor re-encrypt in this phase).
    traces: Mapped[list["ProjectTrace"]] = relationship(
        back_populates="project",
        cascade="all, delete-orphan",
        order_by="ProjectTrace.id",
    )


class ProjectTrace(Base):
    """One ETS project-log entry (``ProjectInformation/ProjectTraces/ProjectTrace``).

    ``date`` and ``user_name`` are plaintext; ``comment`` is stored verbatim as in the source
    ``.knxproj`` (stored encrypted). We preserve it byte-for-byte for a lossless re-export and do
    not decrypt it here (that needs the ETS key material — a separate phase).
    """

    __tablename__ = "project_traces"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    project_id: Mapped[str] = mapped_column(
        ForeignKey("projects.id"), nullable=False, index=True
    )
    date: Mapped[str] = mapped_column(String, nullable=False, default="")
    user_name: Mapped[str] = mapped_column(Text, nullable=False, default="")
    comment: Mapped[str] = mapped_column(Text, nullable=False, default="")

    project: Mapped["Project"] = relationship(back_populates="traces")


class Installation(Base):
    """An installation (= IR ``installation_id``, one ``N.xml`` in the .knxproj); ``index`` is user-facing."""

    __tablename__ = "installations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    index: Mapped[int] = mapped_column(Integer, nullable=False, unique=True)
    name: Mapped[str] = mapped_column(Text, nullable=False, default="")
    # Installation attributes xknxproject does not surface (InstallationId, IP-routing multicast/TTL/
    # latency/sync, SplitType, backbone-security mode — all valid in project/20 and /23), captured
    # verbatim as an attribute dict and re-emitted on export. Secret key material (IPRoutingBackboneKey,
    # BCUKey) is intentionally NOT persisted. ``None`` when the source had none.
    extra_attributes: Mapped[dict[str, str] | None] = mapped_column(JSON)
    # The installation's ``<P2PLinks>`` subtree (point-to-point links incl. their keys/endpoint
    # security), captured verbatim (namespace-stripped XML) and re-emitted. Empty when absent.
    p2p_links_xml: Mapped[str] = mapped_column(Text, nullable=False, default="")

    areas: Mapped[list["Area"]] = relationship(
        back_populates="installation", cascade="all, delete-orphan"
    )
    group_ranges: Mapped[list["GroupRange"]] = relationship(
        back_populates="installation", cascade="all, delete-orphan"
    )
    spaces: Mapped[list["Space"]] = relationship(
        back_populates="installation", cascade="all, delete-orphan"
    )
    trades: Mapped[list["Trade"]] = relationship(
        back_populates="installation", cascade="all, delete-orphan"
    )
    # Devices under ``<Topology>/<UnassignedDevices>`` (not yet placed on a line), captured
    # verbatim from the raw ``.knxproj`` as namespace-stripped ``<DeviceInstance>`` XML strings (one
    # per device, document order) and re-emitted on export. They have no line/segment, so they are
    # not modelled as ``Device`` rows (which require a segment); kept here as opaque provenance the
    # editor does not touch. ``None`` when the source had no such container.
    unassigned_devices_xml: Mapped[list[str] | None] = mapped_column(JSON)


class Area(Base):
    """A topology area (address bits 12-15)."""

    __tablename__ = "areas"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    installation_id: Mapped[int] = mapped_column(
        ForeignKey("installations.id"), nullable=False, index=True
    )
    address: Mapped[int] = mapped_column(Integer, nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False, default="")
    # Descriptive metadata carried over from the imported .knxproj.
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    comment: Mapped[str] = mapped_column(Text, nullable=False, default="")

    installation: Mapped["Installation"] = relationship(back_populates="areas")
    lines: Mapped[list["Line"]] = relationship(
        back_populates="area", cascade="all, delete-orphan"
    )


class Line(Base):
    """A topology line (address bits 8-11); individual addresses are unique per line."""

    __tablename__ = "lines"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    area_id: Mapped[int] = mapped_column(
        ForeignKey("areas.id"), nullable=False, index=True
    )
    address: Mapped[int] = mapped_column(Integer, nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False, default="")
    # Coupler "route regardless" pass-through addresses (``AdditionalGroupAddresses``), stored as
    # comma-separated raw integer addresses. Empty when none. (KNX PR #651 project-side field.)
    additional_group_addresses: Mapped[str] = mapped_column(
        Text, nullable=False, default=""
    )
    # Descriptive metadata carried over from the imported .knxproj.
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    comment: Mapped[str] = mapped_column(Text, nullable=False, default="")

    area: Mapped["Area"] = relationship(back_populates="lines")
    segments: Mapped[list["Segment"]] = relationship(
        back_populates="line", cascade="all, delete-orphan"
    )


class Segment(Base):
    """A media segment of a line, holding the medium type (e.g. ``MT-0`` TP / ``MT-5`` IP)."""

    __tablename__ = "segments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    line_id: Mapped[int] = mapped_column(
        ForeignKey("lines.id"), nullable=False, index=True
    )
    number: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    medium_type: Mapped[str] = mapped_column(String, nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False, default="")
    # RF/PL ``DomainAddress`` (project/22+23 Segment attr, project/14+20 Line attr). None for TP/IP.
    domain_address: Mapped[int | None] = mapped_column(Integer)
    # This segment's own ``AdditionalGroupAddresses`` (project/22+23, where they sit on the Segment),
    # comma-separated raw integer addresses. Empty when none. The Line keeps the per-line union for
    # the download/filter-table consumer; this preserves the per-segment split for a faithful export.
    additional_group_addresses: Mapped[str] = mapped_column(
        Text, nullable=False, default=""
    )
    # The segment's security ``MasterSalt`` (project/23 Segment attr), kept verbatim. None if absent.
    master_salt: Mapped[str | None] = mapped_column(String)

    line: Mapped["Line"] = relationship(back_populates="segments")
    devices: Mapped[list["Device"]] = relationship(
        back_populates="segment", cascade="all, delete-orphan"
    )


class Device(Base):
    """A device instance. ``address`` is the 0-255 individual-address octet, unique per line.

    ``product_ref_id`` is the purchased catalog product; ``hardware2program_ref_id`` is the loaded
    ``HP-…`` program (= catalog ``HardwareProgram.id``) that resolves the application and hence its
    parameter/com-object definitions.
    """

    __tablename__ = "devices"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    segment_id: Mapped[int] = mapped_column(
        ForeignKey("segments.id"), nullable=False, index=True
    )
    address: Mapped[int | None] = mapped_column(Integer)
    name: Mapped[str] = mapped_column(Text, nullable=False, default="")
    product_ref_id: Mapped[str] = mapped_column(String, nullable=False)
    hardware2program_ref_id: Mapped[str | None] = mapped_column(String)
    # The device's application-program ref (``M-XXXX_A-...``), captured from the .knxproj. Used as a
    # fallback to resolve the application when the hardware-program id is not in the catalog (old /
    # reissued product ids): the application itself may still be present under its own id.
    application_program_ref_id: Mapped[str | None] = mapped_column(String)
    # The building/room (Space) the device is placed in, if any.
    space_id: Mapped[int | None] = mapped_column(ForeignKey("spaces.id"), index=True)
    # The DeviceInstance ``<GroupObjectTree>`` (channel/folder grouping of the device's group
    # objects), captured verbatim from the raw ``.knxproj`` as a namespace-stripped XML string and
    # re-emitted on export. xknxproject flattens this tree to ``ComObject.channel_id`` and loses the
    # folder nodes, nesting and order, so it is preserved here as opaque provenance metadata (the
    # editor does not edit the tree). Empty when the source device had none.
    group_object_tree: Mapped[str] = mapped_column(Text, nullable=False, default="")
    # The DeviceInstance ``<IPConfig>`` (IP interface/router config: assign method, static IP/subnet/
    # gateway/MAC), captured verbatim from the raw ``.knxproj`` as its attribute dict and re-emitted
    # on export. xknxproject does not surface it, so it is preserved as opaque provenance (the editor
    # does not edit it). ``None`` when the source device had no ``<IPConfig>``.
    ip_config: Mapped[dict[str, str] | None] = mapped_column(JSON)
    # DeviceInstance attributes xknxproject does not surface (UniqueId, InstallationHints,
    # IsRFRetransmitter, Context — all valid in project/20 and /23), captured verbatim as an
    # attribute dict and re-emitted on export. ``None`` when the source device had none.
    extra_attributes: Mapped[dict[str, str] | None] = mapped_column(JSON)
    # DeviceInstance subtrees xknxproject drops, captured verbatim (namespace-stripped XML) and
    # re-emitted: ``{"ChannelInstances": ..., "BusInterfaces": ..., "RfFastAckSlots": ...}`` (channel
    # instance names, bus-interface endpoints incl. their secure config, RF fast-ack slots). ``None``
    # when the device had none.
    extra_subtrees: Mapped[dict[str, str] | None] = mapped_column(JSON)
    # The DeviceInstance @Id from the imported .knxproj (e.g. ``P-04BB-0_DI-7``), kept so
    # point-to-point links (stored by that id on the installation) resolve to this device, also on
    # export. Empty for a device created in the editor.
    source_id: Mapped[str] = mapped_column(String, nullable=False, default="")
    # Descriptive metadata carried over from the imported .knxproj (for display without a catalog).
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    comment: Mapped[str] = mapped_column(Text, nullable=False, default="")
    # The DeviceInstance ``LastModified`` timestamp, carried over verbatim (nullable, like
    # ``last_download``). Emitted as-is on export.
    last_modified: Mapped[str | None] = mapped_column(String)
    order_number: Mapped[str] = mapped_column(String, nullable=False, default="")
    hardware_name: Mapped[str] = mapped_column(Text, nullable=False, default="")
    product_name: Mapped[str] = mapped_column(Text, nullable=False, default="")
    manufacturer_name: Mapped[str] = mapped_column(Text, nullable=False, default="")
    # Commissioning state (per-device "loaded" ticks + serial / last download), carried on the
    # DeviceInstance in a .knxproj. Round-tripped on import/export and updated after programming.
    serial_number: Mapped[str] = mapped_column(String, nullable=False, default="")
    last_download: Mapped[str | None] = mapped_column(String)
    individual_address_loaded: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )
    application_program_loaded: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )
    communication_part_loaded: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )
    medium_config_loaded: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )
    parameters_loaded: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )

    segment: Mapped["Segment"] = relationship(back_populates="devices")
    space: Mapped["Space | None"] = relationship(back_populates="devices")
    module_instances: Mapped[list["ModuleInstance"]] = relationship(
        back_populates="device", cascade="all, delete-orphan"
    )
    parameters: Mapped[list["Parameter"]] = relationship(
        back_populates="device", cascade="all, delete-orphan"
    )
    com_objects: Mapped[list["ComObject"]] = relationship(
        back_populates="device", cascade="all, delete-orphan"
    )
    binary_data: Mapped[list["DeviceBinaryData"]] = relationship(
        back_populates="device", cascade="all, delete-orphan"
    )
    additional_addresses: Mapped[list["DeviceAdditionalAddress"]] = relationship(
        back_populates="device",
        cascade="all, delete-orphan",
        order_by="DeviceAdditionalAddress.id",
    )
    # Trade memberships pointing at this device. Read-only here (Trade owns the association via
    # delete-orphan); the FK is ON DELETE CASCADE, so deleting a device drops these rows at the DB
    # level. Exposed so the undo snapshot can capture them and restore the memberships on undo.
    trade_links: Mapped[list["TradeDevice"]] = relationship(
        back_populates="device",
        viewonly=True,
    )


class DeviceAdditionalAddress(Base):
    """An extra individual address a DeviceInstance reserves (``AdditionalAddresses/Address``).

    Line/backbone couplers and IP interfaces claim additional individual addresses (e.g. the tunnel
    addresses an IP interface hands out). ``xknxproject`` drops these on import, so they are captured
    from the raw project XML and re-emitted verbatim on export. ``address`` is the 1-255 octet;
    ``name``/``description``/``comment`` are the optional labels.
    """

    __tablename__ = "device_additional_addresses"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    device_id: Mapped[int] = mapped_column(
        ForeignKey("devices.id"), nullable=False, index=True
    )
    address: Mapped[int | None] = mapped_column(Integer)
    name: Mapped[str] = mapped_column(Text, nullable=False, default="")
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    comment: Mapped[str] = mapped_column(Text, nullable=False, default="")

    device: Mapped["Device"] = relationship(back_populates="additional_addresses")


class ModuleInstance(Base):
    """A module instance: ``instance_id`` is the eval-time key (e.g. ``M-100_MI-1``) and ``ref_id`` the
    module definition ref (e.g. ``M-100``). Only top-level instances are kept.

    ``repeat_index`` and ``arguments`` carry the on-disk ``<ModuleInstance>`` structure so it can be
    re-emitted on export: allocator-driven ``<Argument RefId=… Value=…>`` entries (stored as a list
    of ``{"ref_id", "value"}``) and the ``RepeatIndex`` string. ``xknxproject`` does not surface
    these, so they are captured from the raw project XML on import. The on-disk ``@RefId``
    (e.g. ``MD-1_M-2``) is the ``instance_id`` with its trailing ``_MI-<n>`` removed.
    """

    __tablename__ = "module_instances"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    device_id: Mapped[int] = mapped_column(
        ForeignKey("devices.id"), nullable=False, index=True
    )
    instance_id: Mapped[str] = mapped_column(String, nullable=False)
    ref_id: Mapped[str] = mapped_column(String, nullable=False)
    repeat_index: Mapped[str] = mapped_column(String, nullable=False, default="")
    arguments: Mapped[list[dict[str, str]]] = mapped_column(
        JSON, nullable=False, default=list
    )

    device: Mapped["Device"] = relationship(back_populates="module_instances")


class Parameter(Base):
    """A parameter instance: ``ref_id`` indexes an application parameter ref, ``value`` is its setting."""

    __tablename__ = "parameters"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    device_id: Mapped[int] = mapped_column(
        ForeignKey("devices.id"), nullable=False, index=True
    )
    ref_id: Mapped[str] = mapped_column(String, nullable=False)
    value: Mapped[str] = mapped_column(String, nullable=False, default="")
    # ParameterInstanceRef attributes xknxproject drops, round-tripped verbatim (``@CustomizedText``
    # a user-set custom label, ``@GrantUseByCustomer`` an end-customer-edit flag). ``None`` when absent.
    customized_text: Mapped[str | None] = mapped_column(String)
    grant_use_by_customer: Mapped[str | None] = mapped_column(String)

    device: Mapped["Device"] = relationship(back_populates="parameters")


class DeviceBinaryData(Base):
    """Opaque per-device ``BinaryData`` carried on the DeviceInstance in a .knxproj.

    ETS device-configuration add-ins (DCAs) persist their state here — e.g. the MDT DALI gateway
    stores its channel config as ``DaliGC16-Backup-Store`` (UTF-16 XML). ``xknxproject`` drops these
    on import, so they are captured from the raw project XML and re-emitted verbatim on export.

    ETS stores the payload as a raw file ``P-XXXX/BinaryData/{Id}.dat`` (no inline ``<Data>``); the
    ``0.xml`` element carries only ``Id``/``Name``/``RefId``/``DoNotCopy``. ``name`` is the ``Name``,
    ``data`` the raw file bytes (empty for a ``ref_id``-only entry that borrows its content from the
    application program's ``ApplicationBinaryData``), ``ref_id`` that optional reference, and
    ``do_not_copy`` the copy-protection flag.
    """

    __tablename__ = "device_binary_data"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    device_id: Mapped[int] = mapped_column(
        ForeignKey("devices.id"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String, nullable=False)
    data: Mapped[bytes] = mapped_column(LargeBinary, nullable=False, default=b"")
    ref_id: Mapped[str] = mapped_column(String, nullable=False, default="")
    do_not_copy: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    device: Mapped["Device"] = relationship(back_populates="binary_data")


class ComObject(Base):
    """A com-object instance ref: ``ref_id`` indexes the application's com-object refs.

    ``channel_id`` names the ApplicationProgramChannel it sits in (``None`` for the
    channel-independent block). The flags are *overrides* — ``None`` inherits the product/application
    definition, a value pins ``Enabled``/``Disabled``. Defaults and lock state stay on the product.
    """

    __tablename__ = "com_objects"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    device_id: Mapped[int] = mapped_column(
        ForeignKey("devices.id"), nullable=False, index=True
    )
    ref_id: Mapped[str] = mapped_column(String, nullable=False)
    # The instance's own @RefId as it appeared in the .knxproj, e.g.
    # ``MD-1_M-1_MI-2_O-2-0_R-0``. For a module-based application this is the ONLY thing that says
    # which module instance the object belongs to: ``ref_id`` above is the application-program
    # definition id, which is deliberately module-instance-stripped and is therefore shared by every
    # instance of the same module (106 objects collapse onto 7 definitions on a real Atios bridge).
    # Export must re-emit this, or the refs no longer resolve. Empty for objects created in the
    # editor rather than imported, in which case ``ref_id`` is the best available id.
    instance_ref_id: Mapped[str] = mapped_column(String, nullable=False, default="")
    channel_id: Mapped[str | None] = mapped_column(String)

    read_flag: Mapped[bool | None] = mapped_column(Boolean)
    write_flag: Mapped[bool | None] = mapped_column(Boolean)
    communication_flag: Mapped[bool | None] = mapped_column(Boolean)
    transmit_flag: Mapped[bool | None] = mapped_column(Boolean)
    update_flag: Mapped[bool | None] = mapped_column(Boolean)
    read_on_init_flag: Mapped[bool | None] = mapped_column(Boolean)
    # The flags above are the EFFECTIVE values (xknxproject resolves inherit->default); the device
    # download programs them, so they must stay resolved. These ``*_override`` flags are the raw
    # per-instance overrides actually present in the source (``None`` = inherit); the export emits
    # these so a round-trip stays override-only (not all six resolved).
    read_flag_override: Mapped[bool | None] = mapped_column(Boolean)
    write_flag_override: Mapped[bool | None] = mapped_column(Boolean)
    communication_flag_override: Mapped[bool | None] = mapped_column(Boolean)
    transmit_flag_override: Mapped[bool | None] = mapped_column(Boolean)
    update_flag_override: Mapped[bool | None] = mapped_column(Boolean)
    read_on_init_flag_override: Mapped[bool | None] = mapped_column(Boolean)

    # Per-instance text overrides (``@Text``/``@FunctionText``/``@Description`` on the
    # ComObjectInstanceRef). Like the flags, ``None`` inherits the application/product default; a value
    # pins the custom text the user set. Preserved through import/display/export so a renamed
    # group object (and its free-text description) survives.
    text_override: Mapped[str | None] = mapped_column(String)
    function_text_override: Mapped[str | None] = mapped_column(String)
    description_override: Mapped[str | None] = mapped_column(String)
    # Further per-instance overrides from the ComObjectInstanceRef, captured from the raw XML and
    # re-emitted verbatim (``None`` inherits the application/product default): ``@Priority`` (comms
    # priority), ``@DatapointType`` (an instance DPT pin) and ``@Acknowledges`` (RELIDREFS).
    priority: Mapped[str | None] = mapped_column(String)
    datapoint_type_override: Mapped[str | None] = mapped_column(String)
    acknowledges: Mapped[str | None] = mapped_column(String)

    device: Mapped["Device"] = relationship(back_populates="com_objects")
    links: Mapped[list["ComObjectLink"]] = relationship(
        back_populates="com_object", cascade="all, delete-orphan"
    )


class GroupRange(Base):
    """A node in the recursive group-address range tree (main → middle under ThreeLevel)."""

    __tablename__ = "group_ranges"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    installation_id: Mapped[int] = mapped_column(
        ForeignKey("installations.id"), nullable=False, index=True
    )
    parent_id: Mapped[int | None] = mapped_column(
        ForeignKey("group_ranges.id"), index=True
    )
    range_start: Mapped[int] = mapped_column(Integer, nullable=False)
    range_end: Mapped[int] = mapped_column(Integer, nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False, default="")
    # Descriptive metadata carried over from the imported .knxproj.
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    comment: Mapped[str] = mapped_column(Text, nullable=False, default="")
    # Pass through line/backbone couplers unconditionally (``Unfiltered``). (KNX PR #651.)
    unfiltered: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    installation: Mapped["Installation"] = relationship(back_populates="group_ranges")
    parent: Mapped["GroupRange | None"] = relationship(
        back_populates="children", remote_side="GroupRange.id"
    )
    children: Mapped[list["GroupRange"]] = relationship(
        back_populates="parent", cascade="all, delete-orphan"
    )
    group_addresses: Mapped[list["GroupAddress"]] = relationship(
        back_populates="group_range", cascade="all, delete-orphan"
    )


class GroupAddress(Base):
    """A group address held in a leaf group range."""

    __tablename__ = "group_addresses"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    group_range_id: Mapped[int] = mapped_column(
        ForeignKey("group_ranges.id"), nullable=False, index=True
    )
    address: Mapped[int] = mapped_column(Integer, nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False, default="")
    datapoint_type: Mapped[str | None] = mapped_column(String)
    # Descriptive metadata carried over from the imported .knxproj.
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    comment: Mapped[str] = mapped_column(Text, nullable=False, default="")
    data_secure: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # Marked as a central/"main" group address (``Central``), carried over for round-trip.
    central: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # Pass through line/backbone couplers unconditionally (``Unfiltered``). (KNX PR #651.)
    unfiltered: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # Marked as a global/broadcast group address (``Global``), carried over for round-trip.
    is_global: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # Group-address security that xknxproject reduces to the ``data_secure`` bool, captured verbatim
    # for a faithful round-trip: ``@Security`` (mode), ``@Key`` (the AES key) and ``@Context``.
    # ``None`` when absent.
    security_mode: Mapped[str | None] = mapped_column(String)
    security_key: Mapped[str | None] = mapped_column(String)
    context: Mapped[str | None] = mapped_column(String)
    # The GroupAddress @Id from the imported .knxproj (e.g. ``P-04BB-0_GA-7``), kept so stored
    # references to it (a bus interface's ``Connector``) resolve on export. Empty for a group
    # address created in the editor.
    source_id: Mapped[str] = mapped_column(String, nullable=False, default="")

    group_range: Mapped["GroupRange"] = relationship(back_populates="group_addresses")
    links: Mapped[list["ComObjectLink"]] = relationship(
        back_populates="group_address", cascade="all, delete-orphan"
    )
    function_links: Mapped[list["FunctionGroupAddress"]] = relationship(
        back_populates="group_address", cascade="all, delete-orphan"
    )


class ComObjectLink(Base):
    """A link from a com-object instance to a group address. ``is_sending`` marks the one group
    address the object transmits to; the others are receive-only. (The IR encodes this by position
    as the first ``Links`` entry; here the bit is stored directly.)"""

    __tablename__ = "com_object_links"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    com_object_id: Mapped[int] = mapped_column(
        ForeignKey("com_objects.id"), nullable=False, index=True
    )
    group_address_id: Mapped[int] = mapped_column(
        ForeignKey("group_addresses.id"), nullable=False, index=True
    )
    is_sending: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    com_object: Mapped["ComObject"] = relationship(back_populates="links")
    group_address: Mapped["GroupAddress"] = relationship(back_populates="links")


class Space(Base):
    """A node in the building/location tree (recursive: building -> floor -> room -> ...), imported
    from the .knxproj project's locations. ``space_type`` is the project type string (e.g. ``Building``,
    ``Floor``, ``Room``). ``order`` preserves the project's original sibling order."""

    __tablename__ = "spaces"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    installation_id: Mapped[int] = mapped_column(
        ForeignKey("installations.id"), nullable=False, index=True
    )
    parent_id: Mapped[int | None] = mapped_column(ForeignKey("spaces.id"), index=True)
    space_type: Mapped[str] = mapped_column(String, nullable=False, default="")
    name: Mapped[str] = mapped_column(Text, nullable=False, default="")
    number: Mapped[str] = mapped_column(String, nullable=False, default="")
    # Raw ``@Usage`` id (e.g. ``tag:kitchen`` / ``SU-<int>``); ``usage_text`` is its translation.
    usage: Mapped[str] = mapped_column(String, nullable=False, default="")
    usage_text: Mapped[str] = mapped_column(Text, nullable=False, default="")
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    comment: Mapped[str] = mapped_column(Text, nullable=False, default="")
    # Non-displayed Space attributes xknxproject drops, round-tripped verbatim (``@DefaultLine`` id,
    # ``@Context``). Empty when the source had none.
    default_line: Mapped[str] = mapped_column(String, nullable=False, default="")
    context: Mapped[str] = mapped_column(String, nullable=False, default="")
    order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    installation: Mapped["Installation"] = relationship(back_populates="spaces")
    parent: Mapped["Space | None"] = relationship(
        back_populates="children", remote_side="Space.id"
    )
    children: Mapped[list["Space"]] = relationship(
        back_populates="parent", cascade="all, delete-orphan"
    )
    devices: Mapped[list["Device"]] = relationship(back_populates="space")
    functions: Mapped[list["Function"]] = relationship(
        back_populates="space", cascade="all, delete-orphan"
    )


class Function(Base):
    """A function assigned to a space (``Function``): a named grouping of group addresses by
    role (e.g. a light's switch/status/dimming addresses)."""

    __tablename__ = "functions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    space_id: Mapped[int] = mapped_column(
        ForeignKey("spaces.id"), nullable=False, index=True
    )
    function_type: Mapped[str] = mapped_column(String, nullable=False, default="")
    name: Mapped[str] = mapped_column(Text, nullable=False, default="")
    usage_text: Mapped[str] = mapped_column(Text, nullable=False, default="")
    # Descriptive metadata carried over from the imported .knxproj.
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    comment: Mapped[str] = mapped_column(Text, nullable=False, default="")
    # Non-displayed Function attributes xknxproject drops, round-tripped verbatim (``@Implements``,
    # ``@Number``, ``@DefaultGroupRange`` id, ``@Context``). Empty when the source had none.
    implements: Mapped[str] = mapped_column(String, nullable=False, default="")
    number: Mapped[str] = mapped_column(String, nullable=False, default="")
    default_group_range: Mapped[str] = mapped_column(String, nullable=False, default="")
    context: Mapped[str] = mapped_column(String, nullable=False, default="")
    order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    space: Mapped["Space"] = relationship(back_populates="functions")
    group_addresses: Mapped[list["FunctionGroupAddress"]] = relationship(
        back_populates="function", cascade="all, delete-orphan"
    )


class FunctionGroupAddress(Base):
    """A group address referenced by a function, with its ``role`` within that function."""

    __tablename__ = "function_group_addresses"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    function_id: Mapped[int] = mapped_column(
        ForeignKey("functions.id"), nullable=False, index=True
    )
    group_address_id: Mapped[int] = mapped_column(
        ForeignKey("group_addresses.id"), nullable=False, index=True
    )
    role: Mapped[str] = mapped_column(String, nullable=False, default="")
    # The GroupAddressRef's own ``@Name`` and ``@Context`` (xknxproject drops them), round-tripped
    # verbatim. Empty when absent.
    name: Mapped[str] = mapped_column(Text, nullable=False, default="")
    context: Mapped[str] = mapped_column(String, nullable=False, default="")

    function: Mapped["Function"] = relationship(back_populates="group_addresses")
    group_address: Mapped["GroupAddress"] = relationship(
        back_populates="function_links"
    )


class Trade(Base):
    """A trade (Gewerke): a named grouping of devices by discipline, in a recursive tree, imported
    from the .knxproj Installation ``Trades``. ``order`` preserves the original sibling order.

    ``xknxproject`` does not surface trades, so the tree is read verbatim from the raw project XML
    and re-emitted on export (the referenced devices resolve through :class:`TradeDevice`)."""

    __tablename__ = "trades"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    installation_id: Mapped[int] = mapped_column(
        ForeignKey("installations.id"), nullable=False, index=True
    )
    parent_id: Mapped[int | None] = mapped_column(ForeignKey("trades.id"), index=True)
    name: Mapped[str] = mapped_column(Text, nullable=False, default="")
    number: Mapped[str] = mapped_column(String, nullable=False, default="")
    comment: Mapped[str] = mapped_column(Text, nullable=False, default="")
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    # Trade_t @CompletionStatus (default "Undefined") and @Context, preserved verbatim on round-trip.
    completion_status: Mapped[str] = mapped_column(String, nullable=False, default="")
    context: Mapped[str] = mapped_column(Text, nullable=False, default="")
    order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # Trade/DeviceInstanceRef @RefIds pointing to devices not placed on any line (unassigned), which
    # resolve to no TradeDevice row; kept verbatim so the trade's membership round-trips on export.
    unresolved_device_refs: Mapped[list[str] | None] = mapped_column(JSON)

    installation: Mapped["Installation"] = relationship(back_populates="trades")
    parent: Mapped["Trade | None"] = relationship(
        back_populates="children", remote_side="Trade.id"
    )
    children: Mapped[list["Trade"]] = relationship(
        back_populates="parent", cascade="all, delete-orphan"
    )
    devices: Mapped[list["TradeDevice"]] = relationship(
        back_populates="trade",
        cascade="all, delete-orphan",
        order_by="TradeDevice.order",
    )


class TradeDevice(Base):
    """A device referenced by a trade (``Trade/DeviceInstanceRef``), preserving its order.

    The ``device_id`` FK is ``ON DELETE CASCADE`` so removing a device also drops its trade
    memberships (the association carries no data of its own)."""

    __tablename__ = "trade_devices"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    trade_id: Mapped[int] = mapped_column(
        ForeignKey("trades.id"), nullable=False, index=True
    )
    device_id: Mapped[int] = mapped_column(
        ForeignKey("devices.id", ondelete="CASCADE"), nullable=False, index=True
    )
    order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    trade: Mapped["Trade"] = relationship(back_populates="devices")
    device: Mapped["Device"] = relationship(back_populates="trade_links")


class Event(Base):
    """The undo/redo history; ``data`` is the serialized command payload including before-values."""

    __tablename__ = "events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    type: Mapped[str] = mapped_column(String, nullable=False)
    data: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    timestamp: Mapped[datetime.datetime] = mapped_column(DateTime, nullable=False)
    reverted: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
