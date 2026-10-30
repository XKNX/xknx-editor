"""Write the WinGet manifests of an XKNX Editor release (package XKNX.Editor).

    python make_manifests.py --tag xknx-editor-v0.3.1 \
        --x64 XKNX-Editor-windows-installer-x64.exe \
        --arm64 XKNX-Editor-windows-installer-arm64.exe --out winget-manifests

The manifests land in <out>/manifests/x/XKNX/Editor/<version>/, the folder layout of
microsoft/winget-pkgs, ready for `wingetcreate submit` or a pull request there. The installers are
the release assets; their SHA-256 is taken from the local files. Standard library only, so it runs
on a bare CI runner.
"""

from __future__ import annotations

import argparse
import datetime
import hashlib
import re
from pathlib import Path

PACKAGE_ID = "XKNX.Editor"
MANIFEST_VERSION = "1.28.0"
# Releases are tagged ``v<version>``; older ones used ``xknx-editor-v<version>``. Accept both.
TAG_PREFIXES = ("xknx-editor-v", "v")
REPO_URL = "https://github.com/XKNX/xknx-editor"
INSTALLER_NAME = "XKNX-Editor-windows-installer-{arch}.exe"
_ISS = Path(__file__).resolve().parent.parent / "windows" / "xknx-editor.iss"

DESCRIPTIONS = {
    "en-US": (
        "KNX project editor for building automation",
        "XKNX Editor is a desktop app for KNX building-automation projects: topology, group "
        "addresses, buildings and rooms, device parameters, commissioning and downloads over the "
        "bus, including KNX Secure. It imports and exports .knxproj projects.",
    ),
    "de-DE": (
        "KNX-Projekteditor für die Gebäudeautomation",
        "XKNX Editor ist eine Desktop-App für KNX-Projekte der Gebäudeautomation: Topologie, "
        "Gruppenadressen, Gebäude und Räume, Geräteparameter, Inbetriebnahme und Download über den "
        "Bus, auch mit KNX Secure. Sie importiert und exportiert .knxproj-Projekte.",
    ),
}
TAGS = ["knx", "home-automation", "building-automation", "smart-home"]


def product_code(iss: Path = _ISS) -> str:
    """The uninstall key of the Inno Setup installation: its AppId plus ``_is1``."""
    match = re.search(
        r"^AppId=\{(\{[0-9A-Fa-f-]+\})\s*$", iss.read_text(encoding="utf-8-sig"), re.M
    )
    if match is None:
        raise ValueError(f"no AppId in {iss}")
    return f"{match.group(1)}_is1"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest().upper()


def _quote(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _header(kind: str) -> list[str]:
    return [
        f"# yaml-language-server: $schema=https://aka.ms/winget-manifest.{kind}.{MANIFEST_VERSION}.schema.json",
        "",
    ]


def _footer(kind: str) -> list[str]:
    return [f"ManifestType: {kind}", f"ManifestVersion: {MANIFEST_VERSION}", ""]


def _version_from_tag(tag: str) -> str:
    """The ``<version>`` of a release tag, accepting either tag prefix."""
    for prefix in TAG_PREFIXES:
        if tag.startswith(prefix):
            return tag[len(prefix) :]
    raise ValueError(f"not an XKNX Editor release tag: {tag}")


def manifests(
    tag: str, installers: dict[str, Path], release_date: datetime.date
) -> dict[str, str]:
    """The manifest files of the release ``tag`` by file name, for the installers by architecture."""
    version = _version_from_tag(tag)
    code = product_code()
    ident = [f"PackageIdentifier: {PACKAGE_ID}", f"PackageVersion: {version}"]

    version_file = [
        *_header("version"),
        *ident,
        "DefaultLocale: en-US",
        *_footer("version"),
    ]

    installer_file = [
        *_header("installer"),
        *ident,
        "InstallerType: inno",
        "MinimumOSVersion: 10.0.0.0",
        "UpgradeBehavior: install",
        "FileExtensions:",
        "- xknx",
        "- knxproj",
        f"ProductCode: {_quote(code)}",
        f"ReleaseDate: {release_date.isoformat()}",
        "AppsAndFeaturesEntries:",
        "- DisplayName: XKNX Editor",
        "  Publisher: XKNX",
        f"  DisplayVersion: {version}",
        f"  ProductCode: {_quote(code)}",
        "Installers:",
    ]
    for arch, path in installers.items():
        url = f"{REPO_URL}/releases/download/{tag}/{INSTALLER_NAME.format(arch=arch)}"
        digest = sha256(path)
        for scope, switch in (("user", "/CURRENTUSER"), ("machine", "/ALLUSERS")):
            installer_file += [
                f"- Architecture: {arch}",
                f"  Scope: {scope}",
                f"  InstallerUrl: {url}",
                f"  InstallerSha256: {digest}",
                "  InstallerSwitches:",
                f"    Custom: {switch}",
            ]
            if scope == "machine":
                installer_file.append("  ElevationRequirement: elevatesSelf")
    installer_file += _footer("installer")

    files = {
        f"{PACKAGE_ID}.yaml": version_file,
        f"{PACKAGE_ID}.installer.yaml": installer_file,
    }
    for locale, (short, long) in DESCRIPTIONS.items():
        kind = "defaultLocale" if locale == "en-US" else "locale"
        files[f"{PACKAGE_ID}.locale.{locale}.yaml"] = [
            *_header(kind),
            *ident,
            f"PackageLocale: {locale}",
            "Publisher: XKNX",
            "PublisherUrl: https://github.com/XKNX",
            f"PublisherSupportUrl: {REPO_URL}/issues",
            "PackageName: XKNX Editor",
            f"PackageUrl: {REPO_URL}",
            "License: GPL-2.0-only",
            f"LicenseUrl: {REPO_URL}/blob/main/LICENSE",
            f"ShortDescription: {_quote(short)}",
            f"Description: {_quote(long)}",
            *(["Moniker: xknx-editor"] if kind == "defaultLocale" else []),
            "Tags:",
            *(f"- {tag_}" for tag_ in TAGS),
            f"ReleaseNotesUrl: {REPO_URL}/releases/tag/{tag}",
            *_footer(kind),
        ]
    return {name: "\n".join(lines) for name, lines in files.items()}


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Write the WinGet manifests of a release."
    )
    parser.add_argument(
        "--tag", required=True, help="release tag, e.g. xknx-editor-v0.3.1"
    )
    parser.add_argument("--x64", type=Path, required=True, help="the x64 installer")
    parser.add_argument("--arm64", type=Path, required=True, help="the arm64 installer")
    parser.add_argument("--out", type=Path, required=True, help="output folder")
    parser.add_argument(
        "--date", type=datetime.date.fromisoformat, help="release date (today)"
    )
    args = parser.parse_args()
    release_date = args.date or datetime.datetime.now(datetime.UTC).date()
    files = manifests(args.tag, {"x64": args.x64, "arm64": args.arm64}, release_date)
    version = _version_from_tag(args.tag)
    target = args.out / "manifests" / "x" / "XKNX" / "Editor" / version
    target.mkdir(parents=True, exist_ok=True)
    for name, text in files.items():
        (target / name).write_text(text, encoding="utf-8", newline="\n")
        print(target / name)


if __name__ == "__main__":
    main()
