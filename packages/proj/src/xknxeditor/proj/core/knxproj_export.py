"""Export a project SQLite document to a ``.knxproj`` archive.

This is a *simple* export: it writes the project structure (topology, devices with their
com-object → group-address links, the group-address tree, and the locations tree of spaces with
their device/function assignments) plus project metadata, in the .knxproj project XML shape so it
round-trips back through :func:`import_knxproj`.

The target schema is selectable: ``schema="20"`` (the default, ``project/20``), ``"23"``
(current ``project/23``), ``"22"`` (older ``project/22``) or ``"14"`` (``project/14``). They share this
subset's element shape; only the XML namespace, the tool identity (``CreatedBy``/``ToolVersion``)
and the master-data source URL differ, so the same builder emits any of them by swapping the
namespace.

Manufacturer/application data (Hardware/Catalog/application program XMLs) is **not** read from a
catalog here — this package is catalog-free. Callers that have the catalog can pass those raw
archive members via ``extra_files`` (and a merged ``knx_master.xml`` via ``master_xml``) so the
resulting archive is self-contained and applications resolve.

Signature state of the exported archive:

- The **project folder** ``{pid}.signature`` is generated with a valid signature (see
  :mod:`knxproj_signing`); manufacturer ``M-XXXX.signature`` files, when needed, come from the
  catalog via ``extra_files``. The project **certificate** (``{pid}.certificate``, the licensing
  proof for projects above the free device limit) is server-signed and bound to the folder
  signature: pass a ``certificate_signer`` (e.g. :func:`myknx_cert.myknx_certificate_signer`) to
  request and embed it during export, or pass an existing one through via ``extra_files``.
- The **MasterData signature** in ``knx_master.xml`` is *not* generated here (``Signature=""``);
  its signing key is not part of this project. A fully signed archive can be built by reusing a
  signed ``knx_master.xml`` from an existing project via :func:`read_master_xml` (the signature
  covers the master content, not the project folders, so it stays valid) or by fetching the
  current signed master with :func:`fetch_master_xml`.

Known round-trip limitations. These are detected at import (see :mod:`..core.import_notes`),
persisted on the project and echoed in :class:`ExportResult.import_notes` so the user is told what a
round-trip merges or drops:

- **Multiple installations**: the importer builds exactly one :class:`~..models.Installation`.
  xknxproject flattens every ``<Installation>/Topology`` into one areas list, discarding the
  installation boundary, so true per-installation separation is unrecoverable. Import merges the
  topology by address (no duplicate rows); a line that would collide is dropped and counted.
- **Unassigned devices** (``<Topology>/<UnassignedDevices>``): every device is modelled under a
  Segment → Line → Area. A device not placed in any line has no home in the model and is dropped.
- **IP interface/routing device config** and **``Function`` ``@Number``/``@Implements``**: rarely set
  outside specialised projects; not modelled.
- **Multi-segment lines in a project/14+20 export**: import now keeps one
  :class:`~..models.Segment` per raw ``<Segment>`` (project/22+23), so each segment's own
  ``DomainAddress`` and device membership survive a project/22+23 round-trip. But project/14+20 is
  line-level (no ``<Segment>`` element), so a project/14+20 *export* of a multi-segment line
  necessarily merges it back to the line — a schema limitation, reported at import.
"""

from __future__ import annotations

import datetime
import io
import json
import logging
import re
import xml.etree.ElementTree as ET
import zipfile
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from urllib.request import Request, urlopen
from uuid import uuid4

from sqlalchemy.orm import Session

from xknxeditor.proj.core import import_notes
from xknxeditor.proj.core._dll_signer import sign_member_map
from xknxeditor.proj.core.dpt import normalize_datapoint_type
from xknxeditor.proj.core.import_notes import ImportLoss
from xknxeditor.proj.core.knxproj_signing import (
    SignatureAudit,
    audit_and_sign_folders,
    directory_signature,
)
from xknxeditor.proj.db import make_engine, url_for
from xknxeditor.proj.models import (
    Area,
    ComObject,
    ComObjectLink,
    Device,
    Function,
    GroupAddress,
    GroupRange,
    Installation,
    Line,
    Project,
    Space,
    Trade,
)

logger = logging.getLogger(__name__)

# A certificate signer turns the freshly computed folder signature into a project certificate.
# Args: (pid, folder_signature_b64_bytes, project_name); returns the ``{pid}.certificate`` bytes,
# or None to skip embedding a certificate. ``folder_signature_b64_bytes`` is the raw content of the
# ``{pid}.signature`` file (base64, no BOM) that the certificate cryptographically binds to.
# The concrete MyKnx implementation lives in :mod:`myknx_cert` (kept out of this module so the
# export itself performs no network I/O unless a signer is passed in).
CertificateSigner = Callable[[str, bytes, str], bytes | None]


def _project_info(project_guid: str, project_name: str) -> bytes:
    """Return the top-level ``{pid}.info`` member as ETS writes it.

    Genuine archives store two-space-indented JSON with CRLF line endings and no trailing
    newline, e.g.::

        {
          "ProjectGuid": "f674776d-715f-4249-ab60-6e954f96f54d",
          "ProjectName": "Trezzo - Zivolo",
          "IsPasswordProtected": false
        }
    """
    body = json.dumps(
        {
            "ProjectGuid": project_guid,
            "ProjectName": project_name,
            "IsPasswordProtected": False,
        },
        indent=2,
    )
    return body.replace("\n", "\r\n").encode("utf-8")


def validation_record(certificate: bytes, folder_signature: bytes) -> bytes:
    """Return the top-level ``.validation`` member as ETS writes it.

    Layout verified byte-for-byte against a genuine ETS6 archive: the certificate inlined after
    ``certificate:``, a blank line, the folder signature after ``signature:``, then the two
    outcome lines. CRLF throughout; the certificate block already ends in one CRLF, so two more
    produce the blank line ETS emits.
    """
    cert = certificate.replace(b"\r\n", b"\n").replace(b"\r", b"\n").rstrip(b"\n")
    cert = cert.replace(b"\n", b"\r\n")
    signature = folder_signature.removeprefix(b"\xef\xbb\xbf").strip()
    return (
        b"certificate:"
        + cert
        + b"\r\n\r\n\r\n"
        + b"signature:"
        + signature
        + b"\r\ncertificate.IsValid:True"
        + b"\r\nvalidation succeeded:True\r\n"
    )


@dataclass
class ExportResult:
    """What :func:`export_knxproj` produced.

    ``schema``: the project schema actually written (may differ from the requested one; see
    :func:`export_knxproj`). ``unverifiable_folders``: nested/baggage folders that had no signature
    and got a best-effort one we cannot verify offline. ``missing_references``:
    application-program or hardware-to-program ids the installation references that are absent from
    the bundled manufacturer data - the importer looks these up in a dictionary and aborts
    with "The given key was not present in the dictionary" when one is missing. The caller should
    surface both to the user. Both empty on a normal, self-contained export. ``import_notes``: the
    lossy-import notes carried on the project (see core.import_notes), echoed so the caller can
    remind the user what a round-trip already merged or dropped. Empty for a lossless import.

    The remaining fields describe the archive just written, for a success summary: ``file_size``
    (bytes on disk), ``device_count``/``building_count``/``group_address_count`` (project contents),
    ``manufacturer_count`` (``M-`` folders bundled), ``signed`` (the project folder carries a
    signature; always true for a successful export) and ``certificate`` (a MyKnx project certificate
    was embedded). ``project_name`` is the exported ProjectInformation name and ``master_version``
    the KNX master-data version written. ``area_count``/``line_count`` describe the topology,
    ``com_object_link_count`` the group-object links written, ``floor_count``/``room_count`` the
    location spaces below buildings, and ``function_count`` the building functions.
    """

    schema: str
    unverifiable_folders: list[str] = field(default_factory=list[str])
    missing_references: list[str] = field(default_factory=list[str])
    import_notes: list[ImportLoss] = field(default_factory=list[ImportLoss])
    file_size: int = 0
    device_count: int = 0
    building_count: int = 0
    group_address_count: int = 0
    manufacturer_count: int = 0
    signed: bool = False
    certificate: bool = False
    project_name: str = ""
    master_version: int = 0
    area_count: int = 0
    line_count: int = 0
    com_object_link_count: int = 0
    floor_count: int = 0
    room_count: int = 0
    function_count: int = 0

    def __str__(
        self,
    ) -> str:  # keep backwards-compatible with callers that used the schema string
        return self.schema


def _audit_installation_refs(members: Mapping[str, bytes]) -> list[str]:
    """Return references in the installation that point outside the bundled manufacturer data.

    Mirrors the two dictionaries the importer builds: every
    application-program id (``M-XXXX_A-...``) an instance ref names must have its program XML in the
    bundle, and every ``Hardware2ProgramRefId`` must be declared in that manufacturer's
    ``Hardware.xml``. A miss is what makes the importer abort with "The given key was not present in
    the dictionary", so we flag it here instead of letting the import fail opaquely.
    """
    installations = [data for path, data in members.items() if path.endswith("/0.xml")]
    if not installations:
        return []
    hardware_xml: dict[str, str] = {
        path.split("/", 1)[0]: data.decode("utf-8", "replace")
        for path, data in members.items()
        if path.endswith("/Hardware.xml")
    }
    missing: set[str] = set()
    for data in installations:
        text = data.decode("utf-8", "replace")
        for value in re.findall(r'="([^"]+)"', text):
            parts = value.split("_")
            if (
                len(parts) >= 2
                and parts[0].startswith("M-")
                and parts[1].startswith("A-")
            ):
                app_id = f"{parts[0]}_{parts[1]}"
                if f"{parts[0]}/{app_id}.xml" not in members:
                    missing.add(f"application program {app_id}")
        for ref in re.findall(r'Hardware2ProgramRefId="([^"]+)"', text):
            mfr = ref.split("_", 1)[0]
            hardware = hardware_xml.get(mfr)
            if hardware is None or f'Id="{ref}"' not in hardware:
                missing.add(f"hardware2program {ref}")
    return sorted(missing)


# Supported target schemas mapped to (CreatedBy, ToolVersion) — the tool identity validated on
# import. Verified against genuine exports: ETS5 (5.7) writes project/20;
# ETS6 6.3 and 6.4 both use project/23 natively. We stamp the 6.3 tool version (6.3.7959.0) for
# schema 23 so the export imports into 6.3 as well as 6.4+ (a newer build number would be
# refused by an older importer as "from a newer version"). project/22 is older ETS6, project/14 ETS4.
_SCHEMA_TOOLS: dict[str, tuple[str, str]] = {
    "14": ("ETS4", "4.2.1287.32739"),
    "20": ("ETS5", "5.7.1428.39779"),
    "22": ("ETS6", "6.2.0.0"),
    "23": ("ETS6", "6.3.7959.0"),
}
DEFAULT_SCHEMA = "20"  # project/20: read by both ETS5 and ETS6


def _ns_for(schema: str) -> str:
    if schema not in _SCHEMA_TOOLS:
        raise ValueError(
            f"unsupported export schema {schema!r}; expected one of {sorted(_SCHEMA_TOOLS)}"
        )
    return f"http://knx.org/xml/project/{schema}"


def _schema_from_knx_xml(xml_bytes: bytes) -> str | None:
    """Return the ``project/<NN>`` schema number from a KNX XML root namespace, or ``None``."""
    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError:
        return None
    if not root.tag.startswith("{"):
        return None
    uri = root.tag[1:].split("}", 1)[0]
    match = re.fullmatch(r"http://knx\.org/xml/project/(\d+)", uri)
    return match.group(1) if match else None


def _master_url(schema: str) -> str:
    return f"https://update.knx.org/data/XML/project-{schema}/knx_master.xml"


def _check_master(master: bytes, source: str, ns: str) -> bytes:
    root = ET.fromstring(master)
    if root.tag != f"{{{ns}}}KNX":
        raise ValueError(
            f"master from {source} uses namespace {root.tag}, expected {ns} — it would "
            "not verify against this export's project folders"
        )
    master_data = root.find(f"{{{ns}}}MasterData")
    if master_data is None or not master_data.get("Signature"):
        raise ValueError(
            f"knx_master.xml from {source} carries no MasterData signature"
        )
    return master


def _unzip_members(blob: bytes | None) -> dict[str, bytes]:
    """Unpack the stored manufacturer-provenance ZIP blob into ``{path: bytes}`` (empty if none)."""
    if not blob:
        return {}
    with zipfile.ZipFile(io.BytesIO(blob)) as zf:
        return {name: zf.read(name) for name in zf.namelist()}


def _provenance_schema(
    master: bytes | None, members: Mapping[str, bytes]
) -> str | None:
    """The ``project/<NN>`` schema of stored provenance, from its master or a Hardware.xml."""
    if master is not None:
        detected = _schema_from_knx_xml(master)
        if detected is not None:
            return detected
    for name, data in members.items():
        if name.endswith("/Hardware.xml"):
            detected = _schema_from_knx_xml(data)
            if detected is not None:
                return detected
    return None


_LEGACY_SCHEMA_MAX = (
    14  # project/10..14 are the ETS3/4-era family converted up on export
)

_PARAM_TYPE_SELF_CLOSED = re.compile(rb"(<ParameterType\b[^>]*?)/>")
_PARAM_TYPE_EMPTY_PAIR = re.compile(rb"(<ParameterType\b[^>]*?>)</ParameterType>")


def _restamp_tool_identity(data: bytes, schema: str) -> bytes:
    """Rewrite an existing, non-target ``CreatedBy``/``ToolVersion`` on the root to the released pair.

    ETS refuses an archive whose manufacturer XML names an unreleased tool identity
    (``CreatedBy="MT"``). Only an existing root attribute is rewritten — a member without one is
    left untouched (adding it would break byte-identity for clean members)."""
    created_by, tool_version = _SCHEMA_TOOLS[schema]
    match = re.search(rb"<KNX\b[^>]*>", data)
    if match is None:
        return data
    tag = match.group(0)
    current = re.search(rb'CreatedBy="([^"]*)"', tag)
    if current is None:
        return data
    version = re.search(rb'ToolVersion="([^"]*)"', tag)
    if current.group(1) == created_by.encode() and (
        version is None or version.group(1) == tool_version.encode()
    ):
        return data
    new_tag = tag.replace(current.group(0), b'CreatedBy="' + created_by.encode() + b'"')
    if version is not None:
        new_tag = new_tag.replace(
            version.group(0), b'ToolVersion="' + tool_version.encode() + b'"'
        )
    return data[: match.start()] + new_tag + data[match.end() :]


def _fill_empty_parameter_types(data: bytes) -> bytes:
    """Give every childless ``<ParameterType/>`` an explicit ``<TypeNone/>`` child.

    ETS dereferences a parameter type's child and aborts the import with a NullReferenceException
    when it has none. Types that already carry a child are left untouched."""
    data = _PARAM_TYPE_SELF_CLOSED.sub(rb"\1><TypeNone/></ParameterType>", data)
    return _PARAM_TYPE_EMPTY_PAIR.sub(rb"\1<TypeNone/></ParameterType>", data)


def _heal_manufacturer_member(data: bytes, schema: str) -> bytes:
    """Heal one manufacturer XML member for the target schema: convert a legacy namespace up,
    restamp an unreleased tool identity, and fill childless parameter types. A strict no-op on a
    clean member already at the target schema."""
    detected = _schema_from_knx_xml(data)
    if (
        detected is not None
        and detected != schema
        and int(detected) <= _LEGACY_SCHEMA_MAX
    ):
        data = data.replace(
            f'xmlns="http://knx.org/xml/project/{detected}"'.encode(),
            f'xmlns="http://knx.org/xml/project/{schema}"'.encode(),
        )
    data = _restamp_tool_identity(data, schema)
    return _fill_empty_parameter_types(data)


def _check_extra_schema(extra_files: Mapping[str, bytes], schema: str) -> None:
    """Reject a modern-family schema mismatch in the bundled manufacturer data.

    Legacy (project/10..14) members are converted up on export; a modern mismatch (e.g. project/20
    data in a project/23 export) is a hard error — the importer rejects the whole archive as
    'Invalid import data' when its XMLs disagree on schema."""
    for name, data in extra_files.items():
        if not name.endswith("/Hardware.xml"):
            continue
        detected = _schema_from_knx_xml(data)
        if (
            detected is None
            or detected == schema
            or int(detected) <= _LEGACY_SCHEMA_MAX
        ):
            continue
        raise ValueError(
            f"cannot export as project/{schema}: bundled manufacturer data uses "
            f"project/{detected}. All device product data must match the project schema — "
            "import ETS6-era .knxprod for a native project/23 export."
        )


def _heal_extra_files(
    extra_files: Mapping[str, bytes], schema: str
) -> dict[str, bytes]:
    """Return the bundled members with every ``M-XXXX/*.xml`` healed for the target schema. Other
    members (e.g. ``M-XXXX.signature``) pass through verbatim; stale ones are recomputed by the
    signature audit after assembly."""
    healed: dict[str, bytes] = {}
    for name, data in extra_files.items():
        if name.endswith(".xml") and name.split("/", 1)[0].startswith("M-"):
            healed[name] = _heal_manufacturer_member(data, schema)
        else:
            healed[name] = data
    return healed


def _stored_provenance_sources(
    project: Project, schema: str, master_xml: bytes | None
) -> tuple[dict[str, bytes], bytes | None, str]:
    """Decide whether to re-emit the project's stored import provenance, and at which schema.

    Returns ``(members, master, schema)``. The provenance (manufacturer M-folders + knx_master.xml
    captured on import) is re-emitted when its schema matches the request, or when the request is the
    default and the provenance is a higher modern schema that cannot be converted down — the export
    then realigns up to it. Otherwise the provenance is dropped and only the supplied master is used.
    The realignment is up-only: an explicit higher request is honoured and lower provenance dropped
    rather than silently downgrading the export's schema."""
    stored_members = _unzip_members(project.imported_product_members)
    stored_master = project.knx_master_xml
    if not stored_members and stored_master is None:
        return {}, master_xml, schema
    prov = _provenance_schema(stored_master, stored_members)
    use = prov is not None and (
        prov == schema
        or (
            schema == DEFAULT_SCHEMA
            and prov in _SCHEMA_TOOLS
            and int(prov) > int(schema)
        )
    )
    if use:
        assert prov is not None
        return stored_members, stored_master, prov
    return {}, master_xml, schema


def _resolve_export_sources(
    project: Project,
    schema: str,
    extra_files: Mapping[str, bytes] | None,
    master_xml: bytes | None,
    master_source: Path | str | None,
    fetch_master: bool,
) -> tuple[str, str, dict[str, bytes], bytes | None]:
    """Resolve the schema, namespace, manufacturer members and master XML the archive is built from.

    Caller-supplied ``extra_files`` win; without them the project's stored import provenance is
    re-emitted (:func:`_stored_provenance_sources`). The whole ``.knxproj`` must use ONE schema, so
    the project files are aligned to the resolved master's schema, an unusable supplied master is
    substituted by the fetched one when ``fetch_master`` is set, and legacy manufacturer members are
    converted up while a modern-family mismatch stays a hard error."""
    if extra_files:
        effective_extra: dict[str, bytes] = dict(extra_files)
        effective_master = master_xml
    else:
        effective_extra, effective_master, schema = _stored_provenance_sources(
            project, schema, master_xml
        )

    if effective_master is None and (fetch_master or master_source is not None):
        try:
            if fetch_master:
                effective_master = fetch_master_xml(master_source, schema=schema)
            else:
                assert master_source is not None  # implied by the branch condition
                effective_master = read_master_xml(master_source, schema)
        except Exception as exc:
            logger.warning(
                "no signed master data available (%s); using generated unsigned master",
                exc,
            )

    # Align the export schema UP to the resolved master's schema first (the whole archive must use
    # one schema). Only after that do we judge whether the master is usable for that schema.
    if effective_master is not None:
        detected = _schema_from_knx_xml(effective_master)
        if detected is not None and detected != schema:
            if detected in _SCHEMA_TOOLS:
                logger.warning(
                    "aligning export schema %s -> %s to match master data",
                    schema,
                    detected,
                )
                schema = detected
            else:
                logger.warning(
                    "master uses unsupported schema %s; keeping %s (import may fail)",
                    detected,
                    schema,
                )

    # A supplied/merged master that does not verify for the (aligned) schema — wrong namespace that
    # cannot be aligned, or a blanked signature — would make the import fail; replace it with the
    # canonical signed one when fetching is allowed.
    if effective_master is not None and fetch_master:
        try:
            _check_master(effective_master, "bundled master", _ns_for(schema))
        except (ValueError, ET.ParseError) as exc:
            try:
                effective_master = fetch_master_xml(master_source, schema=schema)
                logger.warning(
                    "replaced unusable bundled master (%s) with the fetched one", exc
                )
            except Exception as fetch_exc:
                logger.warning(
                    "unusable bundled master (%s) and no replacement (%s); writing it as-is",
                    exc,
                    fetch_exc,
                )

    _check_extra_schema(effective_extra, schema)
    effective_extra = _heal_extra_files(effective_extra, schema)
    return schema, _ns_for(schema), effective_extra, effective_master


def read_master_xml(knxproj: Path | str, schema: str = DEFAULT_SCHEMA) -> bytes:
    """Read ``knx_master.xml`` from a (signed) ``.knxproj`` archive for reuse.

    Returns the master XML bytes as stored (so its signature stays valid). The master is a
    catalog snapshot (datapoint types, medium types, manufacturers) that does not embed
    project-specific data, so it can be shared across exports. Raises ``ValueError`` when the
    master is missing, unsigned, or its schema namespace does not match ``schema``.
    """
    with zipfile.ZipFile(knxproj) as zf:
        return _check_master(zf.read("knx_master.xml"), str(knxproj), _ns_for(schema))


def fetch_master_xml(
    knxproj: Path | str | None = None,
    timeout: float = 15.0,
    schema: str = DEFAULT_SCHEMA,
) -> bytes:
    """Return a signed master XML for ``schema``, preferring the current KNX release.

    Tries the official master data for the schema (the canonical KNX source), then falls
    back to the ``knx_master.xml`` from ``knxproj``. Raises ``OSError``/``ValueError`` when the
    download fails and no fallback archive was given. The returned bytes are always the untouched,
    signed master, so ``export_knxproj`` can pass them via ``master_xml`` directly.
    """
    ns = _ns_for(schema)
    url = _master_url(schema)
    try:
        with urlopen(Request(url), timeout=timeout) as response:
            return _check_master(response.read(), url, ns)
    except Exception as exc:
        if knxproj is None:
            raise
        logger.warning(
            "fetching current knx_master.xml failed (%s); using %s", exc, knxproj
        )
    return read_master_xml(knxproj, schema)


def export_knxproj(
    source: Path | str,
    dest: Path | str,
    *,
    schema: str = DEFAULT_SCHEMA,
    extra_files: Mapping[str, bytes] | None = None,
    master_xml: bytes | None = None,
    master_source: Path | str | None = None,
    fetch_master: bool = False,
    master_version: int = 1,
    certificate_signer: CertificateSigner | None = None,
    project_name: str | None = None,
) -> ExportResult:
    """Read the project at ``source`` (a ``.xknx``) and write a ``.knxproj`` archive to ``dest``.

    Returns an :class:`ExportResult`: the project schema actually written (``.schema`` — can differ
    from the requested ``schema`` because the export aligns to the bundled manufacturer/master data;
    ``str(result)`` yields it for backwards compatibility) plus ``.unverifiable_folders`` (nested
    folders that got a best-effort, offline-unverifiable signature — empty on a normal export).

    Args:
      source: Path to the ``.xknx`` project document.
      dest: Path to write the ``.knxproj`` archive to.
      schema: Target project schema — ``"20"`` (project/20, default), ``"23"`` (current project/23),
        ``"22"`` (older project/22) or ``"14"`` (project/14). Controls the XML namespace, the tool identity
        stamped on the root and the master-data URL used by ``fetch_master``.
      extra_files: Optional raw archive members to add verbatim (e.g. manufacturer ``M-XXXX/``
        trees and their ``M-XXXX.signature`` files, supplied by a caller that has the catalog).
        Keys colliding with the export's own paths are ignored.
      master_xml: Optional ``knx_master.xml`` bytes to write instead of the generated one. Takes
        precedence over ``fetch_master``/``master_source``.
      master_source: Optional signed ``.knxproj`` whose (signed) master data is reused. Used as the
        fallback source when ``fetch_master`` is set and the download fails.
      fetch_master: When true (and ``master_xml`` is not given), download the current signed master
        for ``schema`` (:func:`fetch_master_xml`), falling back to ``master_source`` and then to the
        generated unsigned master. Off by default so the export performs no network I/O unless asked.
      master_version: ``MasterData`` ``Version`` attribute for the generated master (informational;
        only meaningful when the ``knx_master.xml`` is subsequently signed by an external master-data
        signer). Ignored when a signed master is supplied/fetched/reused.
      certificate_signer: Optional callback that turns the exported folder signature into a project
        certificate. When given, it is called after the folder signature is computed and, if it
        returns bytes, they are written as ``{pid}.certificate`` into the archive (the licensing
        proof the importer checks for projects above the free device limit). Use
        :func:`myknx_cert.myknx_certificate_signer` for the MyKnx cloud signer. Off by default so
        the export performs no network I/O and needs no MyKnx account.
      project_name: Optional name for the exported ProjectInformation (shown in the
        project list). When ``None`` the project's stored name is used. Does not persist to
        the source ``.xknx``.
    """
    ns = _ns_for(schema)  # validate the requested schema early
    logger.debug(
        "export_knxproj: source=%s dest=%s schema=%s signer=%s extra_files=%d",
        source,
        dest,
        schema,
        certificate_signer is not None,
        len(extra_files) if extra_files else 0,
    )
    engine = make_engine(url_for(Path(source)))
    import_notes_raw = ""
    effective_extra: dict[str, bytes] = {}
    try:
        # autoflush=False keeps this read of the LIVE working-copy file strictly read-only. The
        # project_name override below marks the ORM row dirty; with autoflush on, the next query
        # would flush it as `UPDATE projects` and hold a RESERVED write lock for the whole archive
        # build. The GUI render thread reads this same file every frame, so that lock surfaced as
        # "database is locked" out of the render loop (no busy_timeout -> a contending read fails
        # instead of waiting). With autoflush off the override stays in-memory (the writer still
        # sees it) and the session never writes; it is rolled back at close as before.
        with Session(engine, autoflush=False) as session:
            project = session.query(Project).first()
            if project is None:
                raise ValueError(f"{source} is not a project (no project row)")
            import_notes_raw = project.import_notes
            if project_name:
                # Override the exported ProjectInformation Name (transient: this session is never
                # committed, so the source .xknx is untouched).
                project.name = project_name
            installation = (
                session.query(Installation).order_by(Installation.index).first()
            )
            # Resolve the schema, manufacturer members and master actually written: caller-supplied
            # extra_files win, else the project's captured import provenance is re-emitted (and the
            # schema realigned to it). Done inside the session so stored provenance can be read.
            schema, ns, effective_extra, master_xml = _resolve_export_sources(
                project, schema, extra_files, master_xml, master_source, fetch_master
            )
            # Counts for the caller's success summary, read while the session is open (one DB round
            # trip each; no extra engine). Buildings are the top-level location spaces.
            device_count = session.query(Device).count()
            building_count = (
                session.query(Space).filter(Space.space_type == "Building").count()
            )
            floor_count = (
                session.query(Space).filter(Space.space_type == "Floor").count()
            )
            room_count = session.query(Space).filter(Space.space_type == "Room").count()
            group_address_count = session.query(GroupAddress).count()
            area_count = session.query(Area).count()
            line_count = session.query(Line).count()
            com_object_link_count = session.query(ComObjectLink).count()
            function_count = session.query(Function).count()
            project_name_out = project.name
            created_by, tool_version = _SCHEMA_TOOLS[schema]
            audit, missing_refs, signed, certificate = _Writer(
                ns, created_by, tool_version
            ).write_archive(
                Path(dest),
                project,
                installation,
                effective_extra,
                master_xml,
                master_version,
                certificate_signer,
            )
    finally:
        engine.dispose()
    for folder in audit.signed:
        logger.info("signed folder %s (missing or invalid signature)", folder)
    for folder in audit.unverifiable:
        # A folder without a signature whose file names contain a character outside the embedded
        # NLS sort-key table: we cannot reproduce the genuine ordering, so the best-effort signature
        # may be rejected. Surface as an error so the GUI can alert the user.
        logger.error(
            "folder %s has no signature and uses characters outside the NLS table; the best-effort "
            "signature may be rejected by ETS (regenerate the NLS table via nls_sortkeys.ps1)",
            folder,
        )
    for ref in missing_refs:
        # The installation references manufacturer data missing from the bundle; the importer
        # aborts with "The given key was not present in the dictionary" on such a ref.
        logger.error(
            "export references %s, which is not in the bundled manufacturer data; ETS will reject "
            "the import (the device's product data is missing from the catalog bundle)",
            ref,
        )
    logger.debug(
        "export_knxproj done: %s (signed=%d unverifiable=%d missing_refs=%d)",
        dest,
        len(audit.signed),
        len(audit.unverifiable),
        len(missing_refs),
    )
    try:
        file_size = Path(dest).stat().st_size
    except OSError:
        file_size = 0
    # A bundled manufacturer is a distinct top-level ``M-`` folder in the written members.
    manufacturer_count = len(
        {path.split("/", 1)[0] for path in effective_extra if path.startswith("M-")}
    )
    return ExportResult(
        schema=schema,
        unverifiable_folders=audit.unverifiable,
        missing_references=missing_refs,
        import_notes=import_notes.loads(import_notes_raw),
        file_size=file_size,
        device_count=device_count,
        building_count=building_count,
        group_address_count=group_address_count,
        manufacturer_count=manufacturer_count,
        signed=signed,
        certificate=certificate,
        project_name=project_name_out,
        master_version=master_version,
        area_count=area_count,
        line_count=line_count,
        com_object_link_count=com_object_link_count,
        floor_count=floor_count,
        room_count=room_count,
        function_count=function_count,
    )


def _enable(value: bool | None) -> str | None:
    """A ``knx:Enable_t`` com-object flag attribute value, or ``None`` to omit it (inherit the
    application default). ``True`` -> ``"Enabled"``, ``False`` -> ``"Disabled"``."""
    return None if value is None else ("Enabled" if value else "Disabled")


def _localname(tag: str) -> str:
    """The local part of a possibly namespaced ElementTree tag (``{ns}Name`` -> ``Name``)."""
    return tag.rsplit("}", 1)[-1]


def _encode_binary_name(name: str) -> str:
    """Encode a BinaryData ``Name`` into the id/filename form used in the archive.

    ``BinaryData@Id`` is ``{DeviceInstanceId}_{encoded Name}`` and the payload is stored in
    ``BinaryData/{Id}.dat``. A letter/digit is kept verbatim; anything else is escaped as ``.``
    plus two uppercase hex digits per UTF-8 byte, so ``DaliGC16-Backup-Store`` becomes
    ``DaliGC16.2DBackup.2DStore`` (``-`` is 0x2D) and a non-ASCII char emits one ``.XX`` per UTF-8
    byte.
    """
    out: list[str] = []
    for char in name:
        if char.isalnum():
            out.append(char)
        else:
            out.extend(f".{byte:02X}" for byte in char.encode("utf-8"))
    return "".join(out)


def _relidref(ref_id: str) -> str:
    """Strip an instance ref id to the RELIDREF form the importer resolves against the application program.

    Genuine exports store ``ComObjectInstanceRef``/``ParameterInstanceRef`` ``@RefId`` as a "relative"
    id: the parent application-program part removed, leaving e.g. ``O-<n>_R-<m>`` for com-objects or
    ``UP-<n>_R-<m>`` / ``P-<n>_R-<m>`` for parameters (spec: ``knx:RELIDREF``). Our stored ref id
    keeps the full ``M-XXXX_A-XXXX-XX-XXXX_<local>`` form, so drop the two leading tokens of the
    application-program id (``M-XXXX`` and ``A-...``, which themselves contain no ``_``). Already
    relative ids are returned unchanged. Emitting the full id makes the importer unable to resolve
    the ref, so it drops the whole device on import.
    """
    parts = ref_id.split("_")
    if len(parts) >= 3 and parts[0].startswith("M-") and parts[1].startswith("A-"):
        return "_".join(parts[2:])
    return ref_id


def _has_override(co: ComObject) -> bool:
    """Whether the com-object carries a per-instance override a genuine ETS export persists.

    Mirrors ETS's write condition (``!HasDefaultValues``): a text/function/description override or
    any non-inherited flag. A link is checked separately by the caller."""
    return any(
        value is not None
        for value in (
            co.text_override,
            co.function_text_override,
            co.description_override,
            co.read_flag,
            co.write_flag,
            co.communication_flag,
            co.transmit_flag,
            co.update_flag,
            co.read_on_init_flag,
        )
    )


def _group_object_tree_instances(raw: str) -> set[str]:
    """Instance ref ids a stored ``<GroupObjectTree>`` lists (``@GroupObjectInstances`` on the root
    and every node). Empty for an empty or malformed tree, so the COIR fallback still emits."""
    if not raw:
        return set()
    try:
        root = ET.fromstring(raw)
    except ET.ParseError:
        return set()
    out: set[str] = set()
    for el in root.iter():
        out.update((el.get("GroupObjectInstances") or "").split())
    return out


def _assign_ids(
    pid: str, installation: Installation | None
) -> tuple[dict[int, str], dict[int, str]]:
    """Assign the stable element ids genuine exports use: GA link tokens (``GA-n``) and device ids."""
    ga_link_id: dict[int, str] = {}
    di_id: dict[int, str] = {}
    if installation is None:
        return ga_link_id, di_id
    ga_seq = 0
    for group_range in installation.group_ranges:
        for ga in group_range.group_addresses:
            ga_seq += 1
            ga_link_id[ga.id] = f"GA-{ga_seq}"
    di_seq = 0
    for area in installation.areas:
        for line in area.lines:
            for segment in line.segments:
                for device in segment.devices:
                    di_seq += 1
                    di_id[device.id] = f"{pid}-0_DI-{di_seq}"
    return ga_link_id, di_id


class _Writer:
    """Builds the ``.knxproj`` XML for one target namespace (schema 14, 20, 22 or 23)."""

    def __init__(self, ns: str, created_by: str, tool_version: str) -> None:
        self._ns = ns
        self._created_by = created_by
        self._tool_version = tool_version
        self._puid = 0  # per-document unique element id stamped on topology/GA nodes
        self._default_line_id: str | None = (
            None  # Installation DefaultLine (line holding devices)
        )
        # Topology shape differs by schema: project/22 and /23 nest devices under a
        # <Segment> that carries MediumTypeRefId; project/14 and /20 put devices directly
        # under <Line> with MediumTypeRefId on the line. Emitting the wrong shape makes the
        # importer reject the topology and import no devices.
        schema = ns.rsplit("/", 1)[-1]
        self._schema = schema
        self._uses_segments = schema in {"22", "23"}
        # Trade@Context only exists from project/20 onward; the v10-v14 Trade_t binding has no such
        # attribute, so emitting it produces XML that fails schema-14 validation on re-read.
        self._trade_has_context = schema in {"20", "21", "22", "23"}
        # BinaryData copy-protection flag is named differently per schema: project/23 (+22) call it
        # "DoNotCopy", project/20 "AutoCopy" (same boolean meaning, per the schema docs), project/14
        # has no such attribute. Emit the right one so the export validates.
        self._binary_copy_attr = {
            "23": "DoNotCopy",
            "22": "DoNotCopy",
            "20": "AutoCopy",
        }.get(schema)
        # Folder-relative path ("BinaryData/{Id}.dat") -> raw bytes, filled while building devices.
        # ETS stores each per-device BinaryData payload as a file, not inline; these are added to the
        # archive under the project folder and must be covered by the folder signature.
        self._binary_files: dict[str, bytes] = {}

    def _next_puid(self) -> int:
        self._puid += 1
        return self._puid

    def _el(self, parent: ET.Element, tag: str, **attrs: object) -> ET.Element:
        child = ET.SubElement(parent, f"{{{self._ns}}}{tag}")
        for key, value in attrs.items():
            if value is not None:
                child.set(key, str(value))
        return child

    def _root(self) -> ET.Element:
        # The importer validates the tool identity on import, so mirror a genuine export's root:
        # the two extra schema namespaces plus CreatedBy/ToolVersion (a project/14 file with only
        # the default namespace is rejected as "Invalid import data").
        root = ET.Element(f"{{{self._ns}}}KNX")
        root.set("xmlns:xsi", "http://www.w3.org/2001/XMLSchema-instance")
        root.set("xmlns:xsd", "http://www.w3.org/2001/XMLSchema")
        root.set("CreatedBy", self._created_by)
        root.set("ToolVersion", self._tool_version)
        return root

    def _serialize(self, root: ET.Element) -> bytes:
        ET.register_namespace("", self._ns)
        # Genuine exports write every project XML as UTF-8 *with* a BOM; match it byte-for-byte so
        # the importer parses the installation files (without the BOM it can silently import
        # nothing). The folder signature is computed over exactly these bytes, so it stays
        # self-consistent.
        return (
            b"\xef\xbb\xbf"
            + b'<?xml version="1.0" encoding="utf-8"?>\n'
            + ET.tostring(root, encoding="unicode").encode("utf-8")
        )

    def write_archive(
        self,
        dest: Path,
        project: Project,
        installation: Installation | None,
        extra_files: Mapping[str, bytes] | None,
        master_xml: bytes | None,
        master_version: int,
        certificate_signer: CertificateSigner | None = None,
    ) -> tuple[SignatureAudit, list[str], bool, bool]:
        pid = project.id
        ga_link_id, di_id = _assign_ids(pid, installation)

        if master_xml is None:
            master = self._root()
            self._el(
                master, "MasterData", Id="MD-1", Version=master_version, Signature=""
            )
            master_xml = self._serialize(master)

        # Build the installation (0.xml) first: it stamps the Puids and picks the DefaultLine, both
        # of which the project.xml metadata references (LastUsedPuid must cover every Puid used).
        zero_xml = self._build_project_xml(
            pid, project, installation, ga_link_id, di_id
        )

        proj_xml = self._root()
        p = self._el(proj_xml, "Project", Id=pid)
        # The importer expects every project to carry a Guid and a LastModified timestamp; a project
        # created from scratch here has neither, and the importer then rejects the import ("Project
        # file has no valid signature"). Fall back to a fresh Guid + the current time so a
        # from-scratch export imports.
        now = datetime.datetime.now(datetime.UTC).strftime("%Y-%m-%dT%H:%M:%S.%f0Z")
        # Bind the Guid once: {pid}.info repeats it, and a second `uuid4()` there would disagree
        # with project.xml.
        project_guid = project.guid or str(uuid4())
        info = self._el(
            p,
            "ProjectInformation",
            Name=project.name,
            GroupAddressStyle=project.group_address_style,
            Comment=project.comment or None,
            LastUsedPuid=self._puid,
            Guid=project_guid,
            LastModified=project.last_modified or now,
        )
        # Re-emit the ETS project log verbatim so a round-trip preserves the history. The Comment is
        # written exactly as imported (ETS encrypts it); Date/UserName are plaintext. Skipped when the
        # project carries no traces, keeping ProjectTraces (minOccurs=0) absent as ETS does.
        if project.traces:
            traces_el = self._el(info, "ProjectTraces")
            for trace in project.traces:
                self._el(
                    traces_el,
                    "ProjectTrace",
                    Date=trace.date,
                    UserName=trace.user_name,
                    Comment=trace.comment,
                )

        own_paths = {
            "knx_master.xml",
            ".validation",
            f"{pid}.signature",
            f"{pid}.certificate",
            f"{pid}.info",
            f"{pid}/project.xml",
            f"{pid}/0.xml",
        }
        project_xml = self._serialize(proj_xml)
        zero = self._serialize(zero_xml)
        # Per-device BinaryData payloads (populated while building 0.xml) are files under the project
        # folder; ETS signs the folder recursively, so include them in the signed set (and the
        # certificate then binds to a signature that covers them).
        folder_files = {"project.xml": project_xml, "0.xml": zero, **self._binary_files}
        # Sign the project folder so a strict import accepts it (see knxproj_signing).
        folder_signature = directory_signature(folder_files)
        logger.debug(
            "export: project folder signed (pid=%s project.xml=%d 0.xml=%d bytes)",
            pid,
            len(project_xml),
            len(zero),
        )
        # Genuine exports write the .signature file as raw base64 with NO BOM (verified against
        # genuine archives, e.g. P-01D2.signature / M-0002.signature). A prepended UTF-8 BOM
        # corrupts the base64 and the importer rejects the project with "Project file has no valid
        # signature".
        project_signature = folder_signature

        # Optionally obtain the project certificate (licensing proof), which binds to the folder
        # signature just computed. Done before opening the archive so a signer failure aborts cleanly.
        certificate: bytes | None = None
        if certificate_signer is not None:
            logger.debug("export: requesting project certificate for %s", pid)
            # The signer's project_name is echoed by the server into the certificate's
            # `CERT KNX:"..."` header, and ETS expects that to name the certificate *file*
            # (genuine archives carry `CERT KNX:"P-0532.certificate"`). Passing project.name here
            # produced a valid certificate bound to the display name, which ETS rejected as
            # uncertified. See myknx_cert.certificate_name.
            certificate = certificate_signer(
                pid, folder_signature, f"{pid}.certificate"
            )
            logger.debug(
                "export: certificate %s",
                f"{len(certificate)} bytes" if certificate else "skipped",
            )

        # Assemble every archive member first, then run the signature audit over the whole set so
        # each folder ends up with a signature: our own flat project folder is signed above and
        # verified here; manufacturer folders keep their (valid) catalog signatures verbatim; any
        # folder still missing a signature is signed best-effort and reported (see audit docs).
        members: dict[str, bytes] = {
            "knx_master.xml": master_xml,
            f"{pid}.signature": project_signature,
            f"{pid}/project.xml": project_xml,
            f"{pid}/0.xml": zero,
        }
        # ETS writes a top-level {pid}.info alongside the signature; strict imports look for it.
        members[f"{pid}.info"] = _project_info(project_guid, project.name)
        if certificate:
            members[f"{pid}.certificate"] = certificate
            # ETS also records the validation outcome in a top-level `.validation`, with the
            # certificate inlined and the folder signature it binds to. Genuine archives carry
            # `.validation` (and no separate `.certificate`); we emit both so either reader is
            # satisfied.
            members[".validation"] = validation_record(certificate, project_signature)
        for rel, data in self._binary_files.items():
            members[f"{pid}/{rel}"] = data
        for path, data in (extra_files or {}).items():
            if path not in own_paths:
                members[path] = data

        # Sign through the vendor assembly when available (the primary path, as in
        # OpenKNXproducer); a no-op elsewhere, where the offline audit below still covers
        # every folder.
        sign_member_map(members)
        audit = audit_and_sign_folders(members)
        missing_refs = _audit_installation_refs(members)

        dest.unlink(missing_ok=True)
        with zipfile.ZipFile(dest, "w", zipfile.ZIP_DEFLATED) as zf:
            for path, data in members.items():
                zf.writestr(path, data)
        return (
            audit,
            missing_refs,
            f"{pid}.signature" in members,
            certificate is not None,
        )

    def _build_project_xml(
        self,
        pid: str,
        project: Project,
        installation: Installation | None,
        ga_link_id: dict[int, str],
        di_id: dict[int, str],
    ) -> ET.Element:
        root = self._root()
        p = self._el(root, "Project", Id=pid)
        insts = self._el(p, "Installations")
        if installation is None:
            return root
        # A genuine installation carries these; the importer expects them on import.
        inst = self._el(
            insts,
            "Installation",
            Name=installation.name,
            BCUKey="4294967295",
            IPRoutingLatencyTolerance="2000",
        )

        topo = self._el(inst, "Topology")
        l_seq = 0
        s_seq = 0
        for a_seq, area in enumerate(
            sorted(installation.areas, key=lambda x: x.address), start=1
        ):
            area_el = self._el(
                topo,
                "Area",
                Id=f"{pid}-0_A-{a_seq}",
                Address=area.address,
                Name=area.name,
                Comment=area.comment or None,
                Description=area.description or None,
                Puid=self._next_puid(),
            )
            for line in sorted(area.lines, key=lambda x: x.address):
                l_seq += 1
                line_id = f"{pid}-0_L-{l_seq}"
                medium = line.segments[0].medium_type if line.segments else "MT-0"
                # RF/PL DomainAddress lives on the Segment (project/22+23) or the Line (14+20).
                line_domain = next(
                    (
                        s.domain_address
                        for s in line.segments
                        if s.domain_address is not None
                    ),
                    None,
                )
                # project/22+23 (segments) keeps MediumTypeRefId on the Segment; project/14+20 on the Line.
                line_el = self._el(
                    area_el,
                    "Line",
                    Id=line_id,
                    Address=line.address,
                    Name=line.name,
                    Comment=line.comment or None,
                    Description=line.description or None,
                    MediumTypeRefId=None if self._uses_segments else medium,
                    DomainAddress=None if self._uses_segments else line_domain,
                    Puid=self._next_puid(),
                )
                first_segment_el: ET.Element | None = None
                for segment in line.segments:
                    if self._uses_segments:
                        s_seq += 1
                        container = self._el(
                            line_el,
                            "Segment",
                            Id=f"{pid}-0_S-{s_seq}",
                            Number=segment.number,
                            MediumTypeRefId=segment.medium_type,
                            DomainAddress=segment.domain_address,
                            Puid=self._next_puid(),
                        )
                        if first_segment_el is None:
                            first_segment_el = container
                    else:
                        container = line_el
                    for device in segment.devices:
                        # DefaultLine points at a line that actually holds devices.
                        if self._default_line_id is None:
                            self._default_line_id = line_id
                        self._build_device(
                            container, device, di_id[device.id], ga_link_id
                        )
                # Coupler "route regardless" pass-through addresses. The schema places
                # <AdditionalGroupAddresses> on the Segment (project/22+23) or on the Line
                # (project/14+20); it is the last child in either. The model stores them on the Line
                # (one segment per line), so emit on the first segment when the target uses segments,
                # else on the line.
                extra = [
                    a for a in line.additional_group_addresses.split(",") if a.strip()
                ]
                if extra:
                    target = (
                        first_segment_el
                        if self._uses_segments and first_segment_el is not None
                        else line_el
                    )
                    agas = self._el(target, "AdditionalGroupAddresses")
                    for addr in extra:
                        self._el(agas, "GroupAddress", Address=int(addr))

        if self._default_line_id is not None:
            inst.set("DefaultLine", self._default_line_id)

        # <UnassignedDevices> (schema order: last child of <Topology>, after all <Area>). Devices not
        # placed on a line, captured verbatim on import and re-grafted here so they round-trip.
        self._build_unassigned_devices(topo, installation)

        self._build_locations(inst, installation, pid, di_id, ga_link_id)

        gas = self._el(inst, "GroupAddresses")
        ranges = self._el(gas, "GroupRanges")
        gr_seq = 0
        roots = [gr for gr in installation.group_ranges if gr.parent_id is None]
        for group_range in sorted(roots, key=lambda x: x.range_start):
            gr_seq = self._build_range(ranges, group_range, pid, gr_seq, ga_link_id)

        self._build_trades(inst, installation, di_id)
        return root

    def _build_trades(
        self,
        inst_el: ET.Element,
        installation: Installation,
        di_id: dict[int, str],
    ) -> None:
        """Emit the ``<Trades>`` tree (Gewerke) after ``GroupAddresses`` (schema order). Each Trade
        re-lists its devices as ``DeviceInstanceRef`` and nests its child trades."""
        roots = [t for t in installation.trades if t.parent_id is None]
        if not roots:
            return
        trades_el = self._el(inst_el, "Trades")
        for trade in sorted(roots, key=lambda t: (t.order, t.id)):
            self._build_trade(trades_el, trade, di_id)

    def _build_trade(
        self,
        parent: ET.Element,
        trade: Trade,
        di_id: dict[int, str],
    ) -> None:
        trade_el = self._el(
            parent,
            "Trade",
            Name=trade.name,
            Number=trade.number or None,
            Comment=trade.comment or None,
            Description=trade.description or None,
            CompletionStatus=trade.completion_status or None,
            Context=(trade.context or None) if self._trade_has_context else None,
            Puid=self._next_puid(),
        )
        for child in sorted(trade.children, key=lambda t: (t.order, t.id)):
            self._build_trade(trade_el, child, di_id)
        for link in sorted(trade.devices, key=lambda d: (d.order, d.id)):
            ref = di_id.get(link.device_id)
            if ref is not None:
                self._el(trade_el, "DeviceInstanceRef", RefId=ref)

    def _build_locations(
        self,
        inst_el: ET.Element,
        installation: Installation,
        pid: str,
        di_id: dict[int, str],
        ga_link_id: dict[int, str],
    ) -> None:
        # Installation_t requires a <Locations> element (not optional), so emit the container even
        # when the project has no spaces; ETS reads an empty Locations without complaint.
        locs = self._el(inst_el, "Locations")
        roots = [s for s in installation.spaces if s.parent_id is None]
        counters = {"sp": 0, "f": 0, "gar": 0}
        for space in sorted(roots, key=lambda s: (s.order, s.id)):
            self._build_space(locs, space, pid, di_id, ga_link_id, counters)

    def _build_space(
        self,
        parent: ET.Element,
        space: Space,
        pid: str,
        di_id: dict[int, str],
        ga_link_id: dict[int, str],
        counters: dict[str, int],
    ) -> None:
        counters["sp"] += 1
        sp = self._el(
            parent,
            "Space",
            Type=space.space_type or "Room",
            Id=f"{pid}-0_BP-{counters['sp']}",
            Name=space.name,
            Number=space.number or None,
            Usage=space.usage or None,
            Description=space.description or None,
            Comment=space.comment or None,
            Puid=self._next_puid(),
        )
        # The Space content model is a strict sequence: nested Space* first, then DeviceInstanceRef*,
        # then Function*. Emitting DeviceInstanceRef/Function before child spaces violates the schema.
        for child in sorted(space.children, key=lambda s: (s.order, s.id)):
            self._build_space(sp, child, pid, di_id, ga_link_id, counters)
        for device in space.devices:
            if device.id in di_id:
                self._el(sp, "DeviceInstanceRef", RefId=di_id[device.id])
        for fn in sorted(space.functions, key=lambda f: (f.order, f.id)):
            counters["f"] += 1
            fn_el = self._el(
                sp,
                "Function",
                Id=f"{pid}-0_F-{counters['f']}",
                Name=fn.name,
                Type=fn.function_type or None,
                Comment=fn.comment or None,
                Description=fn.description or None,
                Puid=self._next_puid(),
            )
            gf = 0
            for fga in fn.group_addresses:
                if fga.group_address_id not in ga_link_id:
                    continue
                # GroupAddressRef_t requires Name and Puid (schema); the importer crashes building
                # the buildings tree when they are missing. The Id is scoped under the function
                # (``_F-n_GF-m``) as genuine exports write it.
                self._el(
                    fn_el,
                    "GroupAddressRef",
                    Id=f"{pid}-0_F-{counters['f']}_GF-{gf}",
                    Name="",
                    RefId=f"{pid}-0_{ga_link_id[fga.group_address_id]}",
                    Role=fga.role or None,
                    Puid=self._next_puid(),
                )
                gf += 1

    def _build_device(
        self,
        line_el: ET.Element,
        device: Device,
        device_id: str,
        ga_link_id: dict[int, str],
    ) -> None:
        di = self._el(
            line_el,
            "DeviceInstance",
            Id=device_id,
            Address=device.address,
            Name=device.name,
            ProductRefId=device.product_ref_id,
            Hardware2ProgramRefId=device.hardware2program_ref_id,
            Description=device.description or None,
            Comment=device.comment or None,
            LastModified=device.last_modified or None,
            # Commissioning state ("loaded" ticks + serial / last download). Emit the flags
            # only when set; an absent attribute means "not loaded" (matching genuine exports and
            # round-trips).
            SerialNumber=device.serial_number or None,
            LastDownload=device.last_download or None,
            IndividualAddressLoaded="true"
            if device.individual_address_loaded
            else None,
            ApplicationProgramLoaded="true"
            if device.application_program_loaded
            else None,
            CommunicationPartLoaded="true"
            if device.communication_part_loaded
            else None,
            MediumConfigLoaded="true" if device.medium_config_loaded else None,
            ParametersLoaded="true" if device.parameters_loaded else None,
            Puid=self._next_puid(),
        )
        # Genuine exports order the device's children ParameterInstanceRefs, then
        # ComObjectInstanceRefs.
        # NOTE: unlike ComObjectInstanceRef (a RELIDREF), ParameterInstanceRef/@RefId is the FULL
        # id ("M-XXXX_A-..._P-n_R-m"); the importer reconstructs it via its parameter-id parser and
        # crashes (IndexOutOfRange in ParameterRefId.GetLongId) on a stripped id, dropping every
        # device.
        if device.parameters:
            params = self._el(di, "ParameterInstanceRefs")
            for param in device.parameters:
                self._el(
                    params,
                    "ParameterInstanceRef",
                    RefId=param.ref_id,
                    Value=param.value,
                )
        # Emit a <ComObjectInstanceRef> only for objects a genuine ETS export writes: those with a
        # link or a per-instance override (ETS's !HasDefaultValues condition). An active-but-unlinked
        # object is carried by <GroupObjectTree>/@GroupObjectInstances and re-materialised on import,
        # so writing a COIR for it too would diverge from genuine exports. Fallback: an object the
        # tree does NOT cover (a from-scratch device has no tree) is still emitted so its
        # instantiation is not lost.
        tree_instances = _group_object_tree_instances(device.group_object_tree)
        pending: list[tuple[ComObject, str, str]] = []
        for co in device.com_objects:
            # Genuine exports order the sending link first; our ComObjectLink.is_sending marks it.
            ordered = sorted(co.links, key=lambda link: not link.is_sending)
            links = " ".join(
                ga_link_id[link.group_address_id]
                for link in ordered
                if link.group_address_id in ga_link_id
            )
            # RefId is a RELIDREF (app-program parent stripped); the full id would make the importer
            # unable to resolve the com-object and drop the whole device. Prefer the instance's own
            # ref id: for a module-based application ``ref_id`` is the shared definition, so writing
            # it collapses the instances onto one id that no longer resolves to a ModuleInstance.
            ref = _relidref(co.instance_ref_id or co.ref_id)
            if not (co.links or _has_override(co) or ref not in tree_instances):
                continue
            pending.append((co, ref, links))
        # An empty <ComObjectInstanceRefs> violates the schema, so only open it when non-empty.
        if pending:
            refs = self._el(di, "ComObjectInstanceRefs")
            for co, ref, links in pending:
                # ChannelId (``CH-n``, already relative) places the object in its application channel.
                # Links is omitted when unlinked. Each flag is emitted only when set (non-None): our
                # stored flags are the effective flags, so a user override survives export/re-import;
                # a reconcile-added object with all-None flags emits none and inherits the app default.
                self._el(
                    refs,
                    "ComObjectInstanceRef",
                    RefId=ref,
                    ChannelId=co.channel_id,
                    Text=co.text_override or None,
                    FunctionText=co.function_text_override or None,
                    Description=co.description_override or None,
                    Links=links or None,
                    ReadFlag=_enable(co.read_flag),
                    WriteFlag=_enable(co.write_flag),
                    CommunicationFlag=_enable(co.communication_flag),
                    TransmitFlag=_enable(co.transmit_flag),
                    UpdateFlag=_enable(co.update_flag),
                    ReadOnInitFlag=_enable(co.read_on_init_flag),
                )
        # <ModuleInstances> (schema order: after ComObjectInstanceRefs). Module-based devices
        # (e.g. the MDT DALI gateway: 16 group + 64 ECG repeating modules) carry their config in
        # module-scoped ParameterInstanceRefs, which reference these instances by their relative
        # @Id; without the block ETS can't resolve those refs. The on-disk @RefId is the instance_id
        # with its trailing "_MI-<n>" removed (RELID/RELIDREF, relative to the DeviceInstance).
        self._build_module_instances(di, device)
        # <GroupObjectTree> (schema order: after ModuleInstances, before BinaryData). The channel/
        # folder grouping of the device's group objects, captured verbatim on import and re-emitted
        # here so it round-trips (issue #14); xknxproject flattens it to channel ids and loses it.
        self._build_group_object_tree(di, device)
        # <AdditionalAddresses> (schema order: after GroupObjectTree, before BinaryData). Extra
        # individual addresses a coupler/interface reserves, captured on import and re-emitted here.
        self._build_additional_addresses(di, device)
        # <BinaryData> (schema order: later still). A DCA's persisted state (e.g. the MDT DALI
        # "DaliGC16-Backup-Store") lives here; re-emit it verbatim so it round-trips.
        self._build_binary_data(di, device, device_id)
        # <IPConfig> (schema order: after BinaryData, before Security). IP interface/router config
        # (assign method, static IP/subnet/gateway/MAC), captured verbatim on import and re-emitted.
        self._build_ip_config(di, device)

    def _build_ip_config(self, di: ET.Element, device: Device) -> None:
        """Re-emit the device's captured ``<IPConfig>`` attributes. Skipped when the device has none.

        The stored dict holds only the attributes present in the source (local names), so emitting
        them verbatim is a lossless round-trip. ``_el`` writes each as a string attribute."""
        if device.ip_config:
            self._el(di, "IPConfig", **device.ip_config)

    def _build_additional_addresses(self, di: ET.Element, device: Device) -> None:
        """Re-emit the device's extra individual addresses (``<AdditionalAddresses>``), e.g. the
        addresses a coupler or IP interface reserves. Skipped when the device has none.

        ``Address`` is optional in project/22+23 but required in project/14+20, so an address-less
        entry (only possible from a 22/23 source) is dropped when exporting to an older schema — it
        cannot be represented there and would make the archive schema-invalid."""
        entries = [
            addr
            for addr in device.additional_addresses
            if self._uses_segments or addr.address is not None
        ]
        if not entries:
            return
        container = self._el(di, "AdditionalAddresses")
        for addr in entries:
            self._el(
                container,
                "Address",
                Address=addr.address,
                Name=addr.name or None,
                Description=addr.description or None,
                Comment=addr.comment or None,
            )

    def _build_group_object_tree(self, di: ET.Element, device: Device) -> None:
        """Re-emit the device's captured ``<GroupObjectTree>`` under ``di`` in the target namespace.

        The stored string is the namespace-stripped subtree from import; rebuild it element by
        element so every tag gets the exporter's namespace and the nesting, order and attributes
        (Type/RefId/Text/GroupObjectInstances) are preserved exactly. Best-effort: a malformed
        stored tree is skipped rather than aborting the export.
        """
        raw = device.group_object_tree
        if not raw:
            return
        # DeviceInstance/GroupObjectTree only exists from project/20 onward; project/14 (ETS4/5 old
        # schema) has no such element, so emitting it there produces XML the importer rejects. Skip
        # it for schemas below 20 rather than write an invalid tree.
        schema = self._ns.rsplit("/", 1)[-1]
        if not (schema.isdigit() and int(schema) >= 20):
            logger.debug(
                "schema %s has no GroupObjectTree; skipping for device %s",
                schema,
                device.id,
            )
            return
        try:
            src = ET.fromstring(raw)
        except ET.ParseError:
            logger.debug(
                "skipping malformed stored GroupObjectTree for device %s", device.id
            )
            return
        # Prune @GroupObjectInstances to the device's current active com-object set so a channel
        # removed since import is not resurrected on re-import (reconciliation deletes the row but
        # the stored tree is verbatim). A no-op for a straight round-trip (active set == imported
        # tree); a newly-activated object the tree lacks survives as a ComObjectInstanceRef above.
        # With no com-objects there is no active-set information (import always materialises the
        # tree's instances into com_objects), so re-emit verbatim rather than empty every node.
        if not device.com_objects:
            self._graft(di, src)
            return
        active = {
            _relidref(co.instance_ref_id or co.ref_id) for co in device.com_objects
        }
        self._graft_group_object_tree(di, src, active)

    def _graft_group_object_tree(
        self, parent: ET.Element, src: ET.Element, active: set[str]
    ) -> ET.Element:
        """Like :meth:`_graft`, but drop ``@GroupObjectInstances`` tokens not in ``active``."""
        el = ET.SubElement(parent, f"{{{self._ns}}}{_localname(src.tag)}")
        for key, value in src.attrib.items():
            name = _localname(key)
            if name == "GroupObjectInstances":
                kept = " ".join(tok for tok in value.split() if tok in active)
                if not kept:
                    continue
                value = kept
            el.set(name, value)
        for child in src:
            self._graft_group_object_tree(el, child, active)
        return el

    def _graft(self, parent: ET.Element, src: ET.Element) -> ET.Element:
        """Append a namespaced deep copy of ``src`` (a namespace-stripped element) under ``parent``."""
        el = ET.SubElement(parent, f"{{{self._ns}}}{_localname(src.tag)}")
        for key, value in src.attrib.items():
            el.set(_localname(key), value)
        for child in src:
            self._graft(el, child)
        return el

    def _build_unassigned_devices(
        self, topo: ET.Element, installation: Installation
    ) -> None:
        """Re-emit the installation's captured ``<UnassignedDevices>`` under ``topo`` (last child of
        ``<Topology>``). Each stored string is a namespace-stripped ``<DeviceInstance>`` subtree,
        re-grafted in the target namespace. Skipped when there are none.

        The captured subtrees carry project/20+ elements (e.g. GroupObjectTree, IPConfig), so they
        are only emitted for schemas that define them; for project/14 the container is skipped rather
        than writing XML the importer would reject."""
        stored = installation.unassigned_devices_xml
        if not stored:
            return
        schema = self._ns.rsplit("/", 1)[-1]
        if not (schema.isdigit() and int(schema) >= 20):
            logger.debug("schema %s has no UnassignedDevices; skipping", schema)
            return
        container = self._el(topo, "UnassignedDevices")
        for raw in stored:
            try:
                src = ET.fromstring(raw)
            except ET.ParseError:
                logger.debug("skipping malformed stored UnassignedDevices entry")
                continue
            self._graft(container, src)

    def _build_module_instances(self, di: ET.Element, device: Device) -> None:
        if not device.module_instances:
            return
        container = self._el(di, "ModuleInstances")
        for mi in device.module_instances:
            module = self._el(
                container,
                "ModuleInstance",
                Id=mi.instance_id,
                RefId=re.sub(r"_MI-\d+$", "", mi.instance_id),
                RepeatIndex=mi.repeat_index or None,
            )
            if mi.arguments:
                args = self._el(module, "Arguments")
                for arg in mi.arguments:
                    self._el(
                        args,
                        "Argument",
                        RefId=arg.get("ref_id"),
                        Value=arg.get("value", ""),
                    )

    def _build_binary_data(
        self, di: ET.Element, device: Device, device_id: str
    ) -> None:
        if not device.binary_data:
            return
        container = self._el(di, "BinaryData")
        for bd in device.binary_data:
            # ETS id form: "{DeviceInstanceId}_{encoded Name}" (non-alnum -> ".{HEX2}", e.g. '-' ->
            # '.2D'); the payload lives in the file BinaryData/{Id}.dat, not inline.
            entry_id = f"{device_id}_{_encode_binary_name(bd.name)}"
            # Copy flag under its schema-specific attribute name (DoNotCopy / AutoCopy / none).
            copy_flag = (
                {self._binary_copy_attr: "true" if bd.do_not_copy else "false"}
                if self._binary_copy_attr is not None
                else {}
            )
            self._el(
                container,
                "BinaryData",
                Id=entry_id,
                Name=bd.name,
                RefId=bd.ref_id or None,
                **copy_flag,
            )
            if bd.data:
                self._binary_files[f"BinaryData/{entry_id}.dat"] = bd.data

    def _build_range(
        self,
        parent: ET.Element,
        group_range: GroupRange,
        pid: str,
        seq: int,
        ga_link_id: dict[int, str],
    ) -> int:
        seq += 1
        gr_el = self._el(
            parent,
            "GroupRange",
            Id=f"{pid}-0_GR-{seq}",
            RangeStart=group_range.range_start,
            RangeEnd=group_range.range_end,
            Name=group_range.name,
            Comment=group_range.comment or None,
            Description=group_range.description or None,
            # Coupler pass-through flag; emit only when set (absent means filtered).
            Unfiltered="true" if group_range.unfiltered else None,
            Puid=self._next_puid(),
        )
        # GroupRange_t is a strict sequence: nested GroupRange* first, then GroupAddress*. Emitting
        # addresses before child ranges violates the schema when a range holds both (Free style).
        for child in sorted(group_range.children, key=lambda x: x.range_start):
            seq = self._build_range(gr_el, child, pid, seq, ga_link_id)
        for ga in sorted(group_range.group_addresses, key=lambda x: x.address):
            # Heal a stored datapoint type to ETS token form; a value ETS cannot open is rejected
            # loudly, naming the group address so the user can fix it.
            try:
                datapoint_type = normalize_datapoint_type(ga.datapoint_type)
            except ValueError as exc:
                raise ValueError(f"group address {ga.name!r}: {exc}") from exc
            self._el(
                gr_el,
                "GroupAddress",
                Id=f"{pid}-0_{ga_link_id[ga.id]}",
                Address=ga.address,
                Name=ga.name,
                Description=ga.description or None,
                Comment=ga.comment or None,
                DatapointType=datapoint_type,
                Central="true" if ga.central else None,
                Unfiltered="true" if ga.unfiltered else None,
                Global="true" if ga.is_global else None,
                Puid=self._next_puid(),
            )
        return seq
