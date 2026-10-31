"""Submitting a release's WinGet manifests to microsoft/winget-pkgs (packaging/winget/submit_manifests.py)."""

from __future__ import annotations

import base64
import importlib.util
import io
import urllib.error
import urllib.request
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

_WINGET = Path(__file__).resolve().parent.parent / "packaging" / "winget"


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "submit_manifests", _WINGET / "submit_manifests.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


submit_manifests = _load()
PACKAGE_DIR = "manifests/x/XKNX/Editor"


@pytest.fixture
def manifests(tmp_path: Path) -> Path:
    folder = tmp_path / PACKAGE_DIR / "0.9.2"
    folder.mkdir(parents=True)
    for name in ("XKNX.Editor.yaml", "XKNX.Editor.installer.yaml"):
        (folder / name).write_bytes(f"# {name}\n".encode())
    return tmp_path


class FakeGitHub:
    """Answers the calls of :func:`submit` like the GitHub API, and records them."""

    def __init__(
        self,
        versions: list[str] | None = None,
        open_pulls: list[dict[str, Any]] | None = None,
        branch_exists: bool = False,
    ) -> None:
        self.versions = versions
        self.open_pulls = open_pulls or []
        self.branch_exists = branch_exists
        self.calls: list[tuple[str, str, dict[str, Any] | None]] = []

    def __call__(
        self, method: str, path: str, body: dict[str, Any] | None = None
    ) -> Any:
        self.calls.append((method, path, body))
        if path == f"repos/microsoft/winget-pkgs/contents/{PACKAGE_DIR}":
            if self.versions is None:
                raise submit_manifests.NotFoundError(path)
            return [{"name": v, "type": "dir"} for v in self.versions]
        if path == "user":
            return {"login": "ab-tools"}
        if path.startswith("repos/microsoft/winget-pkgs/pulls?"):
            return self.open_pulls
        if path == "repos/microsoft/winget-pkgs/git/ref/heads/master":
            return {"object": {"sha": "base"}}
        if path == "repos/microsoft/winget-pkgs/git/commits/base":
            return {"tree": {"sha": "base-tree"}}
        if path == "repos/ab-tools/winget-pkgs/git/blobs":
            return {"sha": f"blob-{len(self.calls)}"}
        if path == "repos/ab-tools/winget-pkgs/git/trees":
            return {"sha": "tree"}
        if path == "repos/ab-tools/winget-pkgs/git/commits":
            return {"sha": "commit"}
        if path == "repos/ab-tools/winget-pkgs/git/ref/heads/XKNX.Editor-0.9.2":
            if not self.branch_exists:
                raise submit_manifests.NotFoundError(path)
            return {"object": {"sha": "old"}}
        if path.startswith("repos/ab-tools/winget-pkgs/git/refs"):
            return {}
        if path == "repos/microsoft/winget-pkgs/pulls":
            return {"html_url": "https://github.com/microsoft/winget-pkgs/pull/1"}
        raise AssertionError(f"unexpected call {method} {path}")

    def posted(self, path: str) -> list[dict[str, Any]]:
        return [
            b for m, p, b in self.calls if m in ("POST", "PATCH") and p == path and b
        ]


def test_nothing_is_submitted_before_the_package_is_in_winget_pkgs(
    manifests: Path,
) -> None:
    api = FakeGitHub(versions=None)
    outcome = submit_manifests.submit(manifests, api)
    assert "not in microsoft/winget-pkgs yet" in outcome
    assert [m for m, _, _ in api.calls] == ["GET"]


def test_a_published_version_is_not_submitted_again(manifests: Path) -> None:
    api = FakeGitHub(versions=["0.9.1", "0.9.2"])
    assert "has XKNX.Editor 0.9.2 already" in submit_manifests.submit(manifests, api)
    assert all(m == "GET" for m, _, _ in api.calls)


def test_an_open_pull_request_is_not_opened_again(manifests: Path) -> None:
    api = FakeGitHub(versions=["0.9.1"], open_pulls=[{"number": 7}])
    assert "is open already" in submit_manifests.submit(manifests, api)
    assert all(m == "GET" for m, _, _ in api.calls)


def test_a_new_version_is_proposed_from_the_fork(manifests: Path) -> None:
    api = FakeGitHub(versions=["0.9.1"])
    outcome = submit_manifests.submit(manifests, api)
    assert outcome == "opened https://github.com/microsoft/winget-pkgs/pull/1"

    blobs = api.posted("repos/ab-tools/winget-pkgs/git/blobs")
    assert sorted(base64.b64decode(b["content"]).decode() for b in blobs) == [
        "# XKNX.Editor.installer.yaml\n",
        "# XKNX.Editor.yaml\n",
    ]
    (tree,) = api.posted("repos/ab-tools/winget-pkgs/git/trees")
    assert tree["base_tree"] == "base-tree"
    assert sorted(t["path"] for t in tree["tree"]) == [
        f"{PACKAGE_DIR}/0.9.2/XKNX.Editor.installer.yaml",
        f"{PACKAGE_DIR}/0.9.2/XKNX.Editor.yaml",
    ]
    (commit,) = api.posted("repos/ab-tools/winget-pkgs/git/commits")
    assert commit == {
        "message": "New version: XKNX.Editor version 0.9.2",
        "tree": "tree",
        "parents": ["base"],
    }
    assert api.posted("repos/ab-tools/winget-pkgs/git/refs") == [
        {"ref": "refs/heads/XKNX.Editor-0.9.2", "sha": "commit"}
    ]
    (pull,) = api.posted("repos/microsoft/winget-pkgs/pulls")
    assert pull["title"] == "New version: XKNX.Editor version 0.9.2"
    assert pull["head"] == "ab-tools:XKNX.Editor-0.9.2"
    assert pull["base"] == "master"
    assert "XKNX.Editor** version 0.9.2" in pull["body"]


def test_a_left_over_branch_is_moved_to_the_new_commit(manifests: Path) -> None:
    api = FakeGitHub(versions=["0.9.1"], branch_exists=True)
    submit_manifests.submit(manifests, api)
    assert api.posted(
        "repos/ab-tools/winget-pkgs/git/refs/heads/XKNX.Editor-0.9.2"
    ) == [{"sha": "commit", "force": True}]
    assert api.posted("repos/ab-tools/winget-pkgs/git/refs") == []


def test_one_version_folder_is_expected(tmp_path: Path) -> None:
    (tmp_path / PACKAGE_DIR / "0.9.1").mkdir(parents=True)
    (tmp_path / PACKAGE_DIR / "0.9.2").mkdir(parents=True)
    with pytest.raises(ValueError, match="expected one version"):
        submit_manifests.version_dir(tmp_path)


def test_without_a_token_nothing_is_submitted(
    manifests: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.delenv("WINGET_TOKEN", raising=False)
    assert submit_manifests.main(["submit_manifests.py", str(manifests)]) == 0
    assert "WINGET_TOKEN is not set" in capsys.readouterr().out


def test_main_submits_with_the_token(
    manifests: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("WINGET_TOKEN", "secret")
    tokens: list[str] = []

    def github(token: str) -> FakeGitHub:
        tokens.append(token)
        return FakeGitHub(versions=None)

    monkeypatch.setattr(submit_manifests, "github", github)
    assert submit_manifests.main(["submit_manifests.py", str(manifests)]) == 0
    assert tokens == ["secret"]
    assert "not in microsoft/winget-pkgs yet" in capsys.readouterr().out


class _Response(io.BytesIO):
    def __enter__(self) -> _Response:
        return self

    def __exit__(self, *args: object) -> None:
        self.close()


def test_the_api_client(monkeypatch: pytest.MonkeyPatch) -> None:
    requests: list[urllib.request.Request] = []

    def urlopen(request: urllib.request.Request, timeout: float) -> _Response:
        requests.append(request)
        if request.full_url.endswith("/missing"):
            raise urllib.error.HTTPError(request.full_url, 404, "Not Found", None, None)  # type: ignore[arg-type]
        if request.full_url.endswith("/broken"):
            raise urllib.error.HTTPError(request.full_url, 500, "Error", None, None)  # type: ignore[arg-type]
        if request.full_url.endswith("/empty"):
            return _Response(b"")
        return _Response(b'{"login": "ab-tools"}')

    monkeypatch.setattr(urllib.request, "urlopen", urlopen)
    api = submit_manifests.github("secret")
    assert api("GET", "user") == {"login": "ab-tools"}
    assert requests[0].full_url == "https://api.github.com/user"
    assert requests[0].get_header("Authorization") == "Bearer secret"
    assert requests[0].data is None
    assert api("POST", "repos/x/empty", {"a": 1}) == {}
    assert requests[1].data == b'{"a": 1}'
    assert requests[1].get_method() == "POST"
    with pytest.raises(submit_manifests.NotFoundError):
        api("GET", "repos/x/missing")
    with pytest.raises(urllib.error.HTTPError):
        api("GET", "repos/x/broken")
