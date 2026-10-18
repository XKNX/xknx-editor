"""Unit tests for the GitHub update-check version logic (no network)."""

from __future__ import annotations

from typing import Any

from editor_gui.update_check import pick_newer


def _rel(
    tag: str, *, draft: bool = False, prerelease: bool = False, body: str = ""
) -> dict:
    return {
        "tag_name": tag,
        "draft": draft,
        "prerelease": prerelease,
        "body": body,
    }


def test_newer_release_is_picked() -> None:
    info = pick_newer("0.1.0", [_rel("xknx-editor-v0.2.0")])
    assert info is not None
    assert info.version == "0.2.0"
    assert info.tag == "xknx-editor-v0.2.0"
    assert info.url.endswith("/releases/tag/xknx-editor-v0.2.0")


def test_release_notes_are_captured() -> None:
    info = pick_newer("0.1.0", [_rel("xknx-editor-v0.2.0", body="  - fixed things\n")])
    assert info is not None
    assert info.notes == "- fixed things"


def test_same_version_is_not_an_update() -> None:
    assert pick_newer("0.1.0", [_rel("xknx-editor-v0.1.0")]) is None


def test_older_release_is_ignored() -> None:
    assert pick_newer("0.2.0", [_rel("xknx-editor-v0.1.0")]) is None


def test_highest_matching_release_wins() -> None:
    releases = [
        _rel("xknx-editor-v0.1.5"),
        _rel("xknx-editor-v0.3.0"),
        _rel("xknx-editor-v0.2.9"),
    ]
    info = pick_newer("0.1.0", releases)
    assert info is not None and info.version == "0.3.0"


def test_library_and_draft_and_prerelease_tags_are_skipped() -> None:
    releases = [
        _rel("xknxeditor-namespaces-v9.9.9"),  # different component
        _rel("xknx-editor-v0.9.0", draft=True),  # draft
        _rel("xknx-editor-v0.8.0", prerelease=True),  # prerelease
        _rel("v1.0.0"),  # no component prefix
        _rel("xknx-editor-v0.2.0"),  # the only eligible newer one
    ]
    info = pick_newer("0.1.0", releases)
    assert info is not None and info.version == "0.2.0"


def test_non_numeric_or_malformed_tags_do_not_crash() -> None:
    releases = [
        _rel("xknx-editor-vnightly"),
        _rel("xknx-editor-v0.2.0-rc1"),
        _rel("xknx-editor-v"),
    ]
    assert pick_newer("0.1.0", releases) is None


def test_no_releases() -> None:
    assert pick_newer("0.1.0", []) is None


def test_assets_with_their_digests_are_kept() -> None:
    release: dict[str, Any] = _rel("xknx-editor-v0.2.0")
    release["assets"] = [
        {
            "name": "XKNX-Editor-windows-installer-x64.exe",
            "browser_download_url": "https://example.org/x64.exe",
            "digest": "sha256:abc123",
            "size": 42,
        },
        {
            "name": "XKNX-Editor-linux.tar.gz",
            "browser_download_url": "https://example.org/l",
        },
        {"name": "broken"},
    ]
    info = pick_newer("0.1.0", [release])
    assert info is not None
    assert [(a.name, a.url, a.sha256, a.size) for a in info.assets] == [
        (
            "XKNX-Editor-windows-installer-x64.exe",
            "https://example.org/x64.exe",
            "abc123",
            42,
        ),
        ("XKNX-Editor-linux.tar.gz", "https://example.org/l", None, 0),
    ]
