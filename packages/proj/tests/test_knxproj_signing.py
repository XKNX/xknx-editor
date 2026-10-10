"""Tests for the folder-signature audit (knxproj_signing).

Flat folders are signed byte-exact (verified elsewhere against genuine ETS archives). Here we
cover the audit that ensures every folder in an archive carries a signature and reports the ones
we cannot verify offline (nested/baggage folders, whose ETS digest order needs Windows-NLS
collation).
"""

from __future__ import annotations

from xknxeditor.proj.core.knxproj_signing import (
    audit_and_sign_folders,
    directory_signature,
    nls_directory_signature,
    verify_directory_signature,
)


def test_verify_roundtrip_flat() -> None:
    files = {"project.xml": b"<a/>", "0.xml": b"<b/>"}
    sig = directory_signature(files)
    assert verify_directory_signature(files, sig)
    # tampered content no longer verifies
    assert not verify_directory_signature({**files, "0.xml": b"<c/>"}, sig)
    # garbage signature is rejected, not crashing
    assert not verify_directory_signature(files, b"not-base64!!")


def test_audit_signs_flat_folder_without_signature() -> None:
    members = {"P-1/project.xml": b"<a/>", "P-1/0.xml": b"<b/>"}
    audit = audit_and_sign_folders(members)
    assert audit.signed == ["P-1"]
    assert audit.unverifiable == []
    assert "P-1.signature" in members
    assert verify_directory_signature(
        {"project.xml": b"<a/>", "0.xml": b"<b/>"}, members["P-1.signature"]
    )


def test_audit_keeps_valid_flat_signature() -> None:
    files = {"project.xml": b"<a/>"}
    members = {"P-1/project.xml": b"<a/>", "P-1.signature": directory_signature(files)}
    audit = audit_and_sign_folders(members)
    assert audit.signed == []
    assert audit.unverifiable == []


def test_audit_resigns_invalid_flat_signature() -> None:
    members = {"P-1/project.xml": b"<a/>", "P-1.signature": b"stale-wrong"}
    audit = audit_and_sign_folders(members)
    assert audit.signed == ["P-1"]
    assert verify_directory_signature(
        {"project.xml": b"<a/>"}, members["P-1.signature"]
    )


def test_audit_keeps_valid_nested_signature() -> None:
    # A nested folder whose signature already verifies (NLS order, covered charset) is untouched.
    files = {"Catalog.xml": b"<c/>", "Baggages/icon.png": b"\x89PNG"}
    valid = nls_directory_signature(files)
    assert valid is not None
    members = {
        "M-0083/Catalog.xml": b"<c/>",
        "M-0083/Baggages/icon.png": b"\x89PNG",
        "M-0083.signature": valid,
    }
    audit = audit_and_sign_folders(members)
    assert audit.signed == []
    assert audit.unverifiable == []
    assert members["M-0083.signature"] == valid


def test_audit_resigns_invalid_nested_signature() -> None:
    # Covered charset: an invalid nested signature is replaced with a reproducible, valid one.
    members = {
        "M-0083/Catalog.xml": b"<c/>",
        "M-0083/Baggages/icon.png": b"\x89PNG",
        "M-0083.signature": b"stale-wrong",
    }
    audit = audit_and_sign_folders(members)
    assert audit.signed == ["M-0083"]
    assert audit.unverifiable == []
    assert verify_directory_signature(
        {"Catalog.xml": b"<c/>", "Baggages/icon.png": b"\x89PNG"},
        members["M-0083.signature"],
    )


def test_audit_reports_nested_folder_with_uncovered_characters() -> None:
    # A file name with a character outside the embedded NLS table cannot be ordered offline.
    members = {
        "M-0083/Catalog.xml": b"<c/>",
        "M-0083/Baggages/あ.png": b"\x89PNG",  # U+3042 hiragana, not in the table
    }
    audit = audit_and_sign_folders(members)
    assert audit.signed == []
    assert audit.unverifiable == ["M-0083"]
    assert "M-0083.signature" in members  # best-effort still written


def test_dll_signer_signs_uncovered_folder_via_assembly(monkeypatch) -> None:
    """The vendor-assembly path (OpenKNXproducer's SignDirectory) is the primary signer;
    the offline audit only fills what the assembly leaves unsigned."""
    from xknxeditor.proj.core import _dll_signer as d

    calls: list[str] = []

    class _FakeSigner:
        def SignDirectory(self, path: str, casing: bool, excl: object) -> None:
            # Mimic the assembly: write a valid signature as a sibling of the folder.
            calls.append(path)
            rel: dict[str, bytes] = {}
            root = d.Path(path)
            for p in sorted(root.rglob("*")):
                if p.is_file():
                    rel[str(p.relative_to(root))] = p.read_bytes()
            (root.parent / (root.name + ".signature")).write_bytes(
                directory_signature(rel)
            )

    monkeypatch.setattr(d, "_load_signer", lambda: _FakeSigner())
    members = {"P-9/project.xml": b"<a/>", "P-9/0.xml": b"<b/>"}
    d.sign_member_map(members)
    assert calls, "SignDirectory should have been invoked for the unsigned folder"
    assert verify_directory_signature(
        {"project.xml": b"<a/>", "0.xml": b"<b/>"}, members["P-9.signature"]
    )


def test_dll_signer_leaves_already_valid_signature(monkeypatch) -> None:
    """A folder whose signature already verifies is not re-signed through the assembly."""
    from xknxeditor.proj.core import _dll_signer as d

    files = {"project.xml": b"<a/>"}
    called = []

    class _FakeSigner:
        def SignDirectory(self, path: str, casing: bool, excl: object) -> None:
            called.append(path)

    monkeypatch.setattr(d, "_load_signer", lambda: _FakeSigner())
    members = {"P-9/project.xml": b"<a/>", "P-9.signature": directory_signature(files)}
    d.sign_member_map(members)
    assert called == [], "a valid signature must not trigger SignDirectory"


def test_dll_signer_noop_without_assembly(monkeypatch) -> None:
    """Without a usable assembly (non-Windows / no signing DLL) the map is left untouched."""
    from xknxeditor.proj.core import _dll_signer as d

    monkeypatch.setattr(d, "_load_signer", lambda: None)
    members = {"P-9/project.xml": b"<a/>"}
    d.sign_member_map(members)
    assert "P-9.signature" not in members
