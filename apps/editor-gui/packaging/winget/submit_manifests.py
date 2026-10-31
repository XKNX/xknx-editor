"""Submit the WinGet manifests of an XKNX Editor release to microsoft/winget-pkgs.

    WINGET_TOKEN=... python submit_manifests.py winget-manifests

Takes the output of make_manifests.py and opens the pull request "New version: XKNX.Editor version
<version>" from the token owner's fork, with the GitHub API alone (no git, no wingetcreate, whose
releases may not know the manifest version winget-pkgs asks for yet). Nothing is submitted, and the
run still succeeds, when:

- ``WINGET_TOKEN`` is not set,
- XKNX.Editor is not in winget-pkgs yet: its first ("New package") pull request is reviewed by hand,
- winget-pkgs has this version already, or a pull request for it is open.

Standard library only, so it runs on a bare CI runner.
"""

from __future__ import annotations

import base64
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Protocol

UPSTREAM = "microsoft/winget-pkgs"
PACKAGE_ID = "XKNX.Editor"
PACKAGE_DIR = "manifests/x/XKNX/Editor"
REPO_URL = "https://github.com/XKNX/xknx-editor"


class NotFoundError(Exception):
    """The GitHub API answered 404."""


class GitHub(Protocol):
    def __call__(
        self, method: str, path: str, body: dict[str, Any] | None = None
    ) -> Any: ...


def github(token: str) -> GitHub:
    """Calls of the GitHub REST API with ``token``; a 404 raises :class:`NotFoundError`."""

    def call(method: str, path: str, body: dict[str, Any] | None = None) -> Any:
        request = urllib.request.Request(
            f"https://api.github.com/{path}",
            data=None if body is None else json.dumps(body).encode(),
            method=method,
            headers={
                "Accept": "application/vnd.github+json",
                "Authorization": f"Bearer {token}",
                "X-GitHub-Api-Version": "2022-11-28",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                text = response.read().decode()
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                raise NotFoundError(path) from exc
            raise
        return json.loads(text) if text.strip() else {}

    return call


def version_dir(manifests: Path) -> Path:
    """The one version folder make_manifests.py wrote: <out>/manifests/x/XKNX/Editor/<version>."""
    versions = [p for p in (manifests / PACKAGE_DIR).iterdir() if p.is_dir()]
    if len(versions) != 1:
        raise ValueError(
            f"expected one version under {manifests / PACKAGE_DIR}, found {versions}"
        )
    return versions[0]


def pull_request_body(version: str) -> str:
    return (
        "## 📖 Description\n\n"
        f"New version: **{PACKAGE_ID}** version {version}, from the "
        f"[{version} release]({REPO_URL}/releases). The manifests are written by the editor's "
        "release build.\n\n"
        "## 📦 Manifest Checklist\n\n"
        "- [x] Checked that there aren't other open "
        "[pull requests](https://github.com/microsoft/winget-pkgs/pulls) for the same manifest "
        "update/change\n"
        "- [x] This PR only modifies one (1) manifest\n"
    )


def submit(manifests: Path, api: GitHub) -> str:
    """Open the pull request for the version in ``manifests``; the outcome, for the log."""
    folder = version_dir(manifests)
    version = folder.name
    try:
        published = api("GET", f"repos/{UPSTREAM}/contents/{PACKAGE_DIR}")
    except NotFoundError:
        return (
            f"{PACKAGE_ID} is not in {UPSTREAM} yet - its first pull request is reviewed "
            "there, so the manifests are kept as an artifact only"
        )
    if any(entry.get("name") == version for entry in published):
        return f"{UPSTREAM} has {PACKAGE_ID} {version} already"

    owner = api("GET", "user")["login"]
    fork = f"{owner}/winget-pkgs"
    branch = f"{PACKAGE_ID}-{version}"
    if api("GET", f"repos/{UPSTREAM}/pulls?state=open&head={owner}:{branch}"):
        return f"a pull request for {PACKAGE_ID} {version} is open already"

    base = api("GET", f"repos/{UPSTREAM}/git/ref/heads/master")["object"]["sha"]
    base_tree = api("GET", f"repos/{UPSTREAM}/git/commits/{base}")["tree"]["sha"]
    tree: list[dict[str, str]] = []
    for file in sorted(folder.glob("*.yaml")):
        blob = api(
            "POST",
            f"repos/{fork}/git/blobs",
            {
                "content": base64.b64encode(file.read_bytes()).decode(),
                "encoding": "base64",
            },
        )
        tree.append(
            {
                "path": f"{PACKAGE_DIR}/{version}/{file.name}",
                "mode": "100644",
                "type": "blob",
                "sha": blob["sha"],
            }
        )
    new_tree = api(
        "POST", f"repos/{fork}/git/trees", {"base_tree": base_tree, "tree": tree}
    )
    title = f"New version: {PACKAGE_ID} version {version}"
    commit = api(
        "POST",
        f"repos/{fork}/git/commits",
        {"message": title, "tree": new_tree["sha"], "parents": [base]},
    )
    try:
        api("GET", f"repos/{fork}/git/ref/heads/{branch}")
    except NotFoundError:
        api(
            "POST",
            f"repos/{fork}/git/refs",
            {"ref": f"refs/heads/{branch}", "sha": commit["sha"]},
        )
    else:
        # left over from an earlier run whose pull request was closed
        api(
            "PATCH",
            f"repos/{fork}/git/refs/heads/{branch}",
            {"sha": commit["sha"], "force": True},
        )
    pull = api(
        "POST",
        f"repos/{UPSTREAM}/pulls",
        {
            "title": title,
            "head": f"{owner}:{branch}",
            "base": "master",
            "body": pull_request_body(version),
        },
    )
    return f"opened {pull['html_url']}"


def main(argv: list[str]) -> int:
    token = os.environ.get("WINGET_TOKEN")
    if not token:
        print("WINGET_TOKEN is not set: the manifests are kept as an artifact only")
        return 0
    print(submit(Path(argv[1]), github(token)))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
