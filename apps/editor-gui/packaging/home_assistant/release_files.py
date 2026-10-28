"""Build the files of an XKNX Editor release that the Home Assistant integration installs from.

    python release_files.py --tag xknx-editor-v0.9.0 --out ha-release

The integration (XKNX/xknx-editor-ha-integration) runs the editor in a virtual environment of
its own, filled from the release's locked requirements file. It writes into ``--out``:

- ``xknx-editor-<version>-requirements.txt``: the third-party packages of ``uv.lock`` with their
  hashes, and the wheels below by their release download URL and hash;
- the wheels of the editor's own packages, and of every package ``uv.lock`` takes from a git
  revision (xknxproject), built from a clone of that revision;
- ``xknx-editor-<version>-keyextract.zip``: the key-extraction helpers for the .NET runtime, built
  like in the Docker image (needs the .NET SDK). The requirements file names its SHA-256 in a
  ``# keyextract.zip sha256:`` line, which the integration checks.

Run from the repository root with uv, git and the .NET SDK on PATH. Standard library only.
"""

from __future__ import annotations

import argparse
import hashlib
import re
import subprocess
import tempfile
import zipfile
from pathlib import Path

REPO_URL = "https://github.com/XKNX/xknx-editor"
TAG_PREFIX = "xknx-editor-v"
HELPERS_NAME = "keyextract.zip"
_ROOT = Path(__file__).resolve().parents[4]
_SOURCES = _ROOT / "packages" / "proj" / "src" / "xknxeditor" / "proj" / "core"

# The helpers of the Docker image (docker/Dockerfile, dotnet-build): folder, source, extra package.
HELPERS = (
    ("sign", "_extract.cs", ""),
    (
        "trace",
        "_extract_trace.cs",
        '<PackageReference Include="System.Configuration.ConfigurationManager" '
        'Version="10.0.14" />',
    ),
    ("oauth", "_extract_oauth.cs", ""),
)
CSPROJ = """<Project Sdk="Microsoft.NET.Sdk">
  <PropertyGroup>
    <OutputType>Exe</OutputType>
    <TargetFramework>net10.0</TargetFramework>
    <Nullable>disable</Nullable>
    <ImplicitUsings>disable</ImplicitUsings>
    <AssemblyName>xknxkeyx</AssemblyName>
    <EnableDefaultCompileItems>false</EnableDefaultCompileItems>
  </PropertyGroup>
  <ItemGroup>
    <Compile Include="{source}" />
    {package}
  </ItemGroup>
</Project>
"""
# "name @ git+https://host/owner/repo@revision" in the exported requirements
_GIT = re.compile(
    r"^(?P<name>[A-Za-z0-9._-]+) @ git\+(?P<url>\S+?)@(?P<rev>[0-9a-f]{7,40})$"
)


def version_of(tag: str) -> str:
    """The version of a release tag ``xknx-editor-v<version>``."""
    if not tag.startswith(TAG_PREFIX):
        raise SystemExit(f"not an XKNX Editor release tag: {tag}")
    return tag[len(TAG_PREFIX) :]


def git_sources(exported: str) -> list[tuple[str, str, str]]:
    """The packages the exported requirements take from git: (name, URL, revision)."""
    return [
        (m["name"], m["url"], m["rev"])
        for line in exported.splitlines()
        if (m := _GIT.match(line))
    ]


def requirements_text(
    exported: str, wheels: list[Path], base_url: str, helpers_sha256: str
) -> str:
    """The requirements file: the exported packages without the ones from git (their wheels
    replace them), then every wheel by its URL under ``base_url`` and its hash."""
    lines = [f"# {HELPERS_NAME} sha256:{helpers_sha256}"]
    skip = False
    for line in exported.splitlines():
        if _GIT.match(line):
            skip = True  # its indented lines ("# via ...") go with it
            continue
        if skip and line.startswith("    "):
            continue
        skip = False
        lines.append(line)
    for wheel in sorted(wheels, key=lambda w: w.name):
        name = wheel.name.split("-")[0].replace("_", "-")
        digest = hashlib.sha256(wheel.read_bytes()).hexdigest()
        lines.append(f"{name} @ {base_url}/{wheel.name} \\\n    --hash=sha256:{digest}")
    return "\n".join(lines) + "\n"


def _run(*args: str, cwd: Path) -> str:
    return subprocess.run(
        args, cwd=cwd, check=True, capture_output=True, text=True
    ).stdout


def build_helpers(target: Path) -> None:
    """The key-extraction helpers as the zip ``target`` (folders sign/, trace/, oauth/)."""
    with tempfile.TemporaryDirectory() as temp:
        built = Path(temp) / "out"
        for name, source, package in HELPERS:
            project = Path(temp) / name
            project.mkdir()
            (project / source).write_bytes((_SOURCES / source).read_bytes())
            (project / "xknxkeyx.csproj").write_text(
                CSPROJ.format(source=source, package=package), encoding="utf-8"
            )
            _run(
                "dotnet",
                "publish",
                "xknxkeyx.csproj",
                "-c",
                "Release",
                "-o",
                str(built / name),
                "-nologo",
                cwd=project,
            )
        with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as archive:
            for file in sorted(built.rglob("*")):
                if file.is_file() and file.suffix != ".pdb":
                    archive.write(file, file.relative_to(built).as_posix())


def build(tag: str, out: Path) -> Path:
    """Write the release files of ``tag`` into ``out``; returns the requirements file."""
    version = version_of(tag)
    out.mkdir(parents=True, exist_ok=True)
    members = sorted(p.parent for p in _ROOT.glob("packages/*/pyproject.toml"))
    members += sorted(p.parent for p in _ROOT.glob("apps/*/pyproject.toml"))
    for member in members:
        _run("uv", "build", "--wheel", "--out-dir", str(out), str(member), cwd=_ROOT)
    exported = _run(
        "uv",
        "export",
        "--frozen",
        "--no-dev",
        "--all-packages",
        "--no-emit-workspace",
        "--no-header",
        "--format",
        "requirements.txt",
        cwd=_ROOT,
    )
    with tempfile.TemporaryDirectory() as temp:
        for name, url, rev in git_sources(exported):
            clone = Path(temp) / name
            _run("git", "clone", "--quiet", url, str(clone), cwd=_ROOT)
            _run("git", "checkout", "--quiet", rev, cwd=clone)
            _run("uv", "build", "--wheel", "--out-dir", str(out), str(clone), cwd=_ROOT)
    helpers = out / f"xknx-editor-{version}-{HELPERS_NAME}"
    build_helpers(helpers)
    requirements = out / f"xknx-editor-{version}-requirements.txt"
    requirements.write_text(
        requirements_text(
            exported,
            list(out.glob("*.whl")),
            f"{REPO_URL}/releases/download/{tag}",
            hashlib.sha256(helpers.read_bytes()).hexdigest(),
        ),
        encoding="utf-8",
    )
    return requirements


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Build the release files the Home Assistant integration installs from."
    )
    parser.add_argument(
        "--tag", required=True, help="the release tag, xknx-editor-v<version>"
    )
    parser.add_argument("--out", required=True, type=Path, help="folder for the files")
    args = parser.parse_args()
    requirements = build(args.tag, args.out)
    print(f"wrote {requirements} and {len(list(args.out.glob('*.whl')))} wheels")


if __name__ == "__main__":
    main()
