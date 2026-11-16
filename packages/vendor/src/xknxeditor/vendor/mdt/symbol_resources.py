"""Validated default symbol resources without application executable code."""

from io import BytesIO
from xml.etree import ElementTree as ET
from zipfile import ZIP_DEFLATED, BadZipFile, ZipFile, ZipInfo

from .profiles import profile_for_application
from .symbols import PAYLOAD_SIZE, decode_symbol

MAX_APP_SIZE = 32 * 1024 * 1024


def _member(source: ZipFile, name: str, limit: int) -> bytes:
    members = [member for member in source.infolist() if member.filename == name]
    if len(members) != 1 or members[0].file_size > limit:
        raise ValueError(f"Missing, duplicate or oversized symbol resource: {name}")
    with source.open(members[0]) as stream:
        data = stream.read(limit + 1)
    if len(data) > limit:
        raise ValueError(f"Symbol resource is too large: {name}")
    return data


def prepare_default_library(archive: bytes, application_id: str) -> bytes:
    """Keep all library variants of a matching app, validating every symbol CRC."""
    profile = profile_for_application(application_id)
    if profile is None or profile.kind != "symbols":
        raise ValueError("Device has no MDT symbol library")
    if len(archive) > MAX_APP_SIZE:
        raise ValueError("Symbol app archive is too large")
    try:
        with ZipFile(BytesIO(archive)) as source:
            manifest = ET.fromstring(_member(source, "AddInManifest.xml", 256 * 1024))
            applications = sorted(
                {
                    element.get("Id", "")
                    for element in manifest.findall(
                        "./DeviceConfiguration/ApplicationProgram"
                    )
                }
            )
            if manifest.get("AppId") != profile.app_id or application_id.split("-O", 1)[
                0
            ].rsplit("-", 1)[0] not in {
                identity.rsplit("-", 1)[0] for identity in applications
            }:
                raise ValueError("Symbol app does not support this application")
            clean = ET.Element("Knxapp", AppId=profile.app_id)
            configuration = ET.SubElement(clean, "DeviceConfiguration")
            for identity in applications:
                ET.SubElement(configuration, "ApplicationProgram", Id=identity)
            folders = ["images/"]
            if profile.app_id == "M0083-A0041":
                folders = [
                    f"images/{library}_Symbol_Lib_v_{generation}/"
                    for library in ("Old", "New")
                    for generation in ("01", "02")
                ]
            output = BytesIO()
            with ZipFile(output, "w", ZIP_DEFLATED) as target:
                target.writestr(ZipInfo("AddInManifest.xml"), ET.tostring(clean))
                for folder in folders:
                    for slot in range(profile.slots):
                        name = f"{folder}Symbol_{slot}.data"
                        data = _member(source, name, PAYLOAD_SIZE)
                        decode_symbol(data)
                        target.writestr(ZipInfo(name), data, compress_type=ZIP_DEFLATED)
            return output.getvalue()
    except (
        BadZipFile,
        ET.ParseError,
        OSError,
        RuntimeError,
        NotImplementedError,
    ) as exc:
        raise ValueError(f"Invalid symbol app archive: {exc}") from exc
