"""Error surfacing for the MyKnx certificate flow.

The workset flow fails with HTTP 422 when the picked license is not cloud-enabled. That must raise a typed :class:`MyKnxError` carrying the raw server ``detail`` (for logs) and a
concise, actionable ``user_message`` (for the UI), not an opaque ``bytes`` repr.
"""

from __future__ import annotations

import json
import logging
import urllib.request
from typing import Any, ClassVar

import pytest

from xknxeditor.proj.core import myknx_cert
from xknxeditor.proj.core.myknx_cert import (
    MyKnxError,
    MyKnxSession,
    _redact_url,
    _server_detail,
    _type_is_cloud_capable,
)

_CLOUD_422 = (
    b'"Cannot add product to workset:\\nEncryption \\"cloud\\" is not enabled for '
    b'product_type KNX Specifications"'
)


def test_server_detail_unwraps_json_string() -> None:
    assert _server_detail(b'"boom"') == "boom"
    assert _server_detail(b'{"message": "nope"}') == "nope"
    assert _server_detail(b"not json") == "not json"


def test_type_is_cloud_capable_keys_on_the_encryptions_list() -> None:
    """Only product types whose ``encryptions`` contains ``"cloud"`` can sign online (ETS6 and
    cloud-enabled apps); ETS5/older or dongle-only types cannot -- this is the discriminator the
    certificate endpoint enforces (others fail add-product with HTTP 422)."""
    assert (
        _type_is_cloud_capable({"encryptions": ["ets_dongle", "cloud"]}) is True
    )  # ETS6
    assert _type_is_cloud_capable({"encryptions": ["cloud"]}) is True  # ETS app
    assert _type_is_cloud_capable({"encryptions": ["ets_dongle"]}) is False  # ETS5
    assert _type_is_cloud_capable({"encryptions": []}) is False  # account's licenses
    assert _type_is_cloud_capable({}) is False  # missing field
    assert _type_is_cloud_capable(None) is False  # unknown product type


def _fake_post(url: str, body: bytes, headers: dict[str, str], timeout: float):
    if url.endswith("/workset"):
        return 200, {}, b'{"id": "WS1"}'
    if url.endswith("/claim"):
        return 200, {}, b""
    if url.endswith("/product/PROD"):
        return 422, {}, _CLOUD_422
    if url.endswith("/release"):
        return 200, {}, b""
    raise AssertionError(f"unexpected POST {url}")


def test_cloud_disabled_license_raises_actionable_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(myknx_cert, "_post", _fake_post)
    monkeypatch.setattr(myknx_cert, "_get", _fake_get_licenses([{"id": "PROD"}]))
    session = MyKnxSession(access_token="", session_id="S1", next_ot_token="T1")
    with pytest.raises(MyKnxError) as excinfo:
        session.project_certificate("PROD", "deadbeef", "Demo")
    err = excinfo.value
    assert err.status == 422
    assert "KNX Specifications" in err.detail  # raw server text kept for logs
    assert "dongle" in err.user_message.lower()  # points the user at the workaround
    assert "\\n" not in err.user_message  # newlines flattened for a one-line toast


def _fake_get_licenses(licenses: list[dict[str, str]]) -> Any:
    def fake_get(
        url: str, headers: dict[str, str], timeout: float
    ) -> tuple[int, dict[str, str], bytes]:
        assert url.endswith("/product/getAll"), url
        return 200, {}, json.dumps(licenses).encode()

    return fake_get


def _recording_req(
    session: MyKnxSession,
    monkeypatch: pytest.MonkeyPatch,
    licenses: bytes,
    add: tuple[int, bytes] = (200, b""),
) -> list[str]:
    """Answer the session's requests like MyKnx with the license list ``licenses``; record them."""
    calls: list[str] = []

    def fake_req(
        method: str, path: str, body_bytes: bytes | None = None
    ) -> tuple[int, bytes]:
        calls.append(f"{method} {path}")
        if path == "/product/getAll":
            return 200, licenses
        if path == "/workset":
            return 200, b'{"id": "NEW"}'
        if path.endswith("/certificate"):
            return 200, b'"CERT KNX:\\"P-9.certificate\\"\\n"'
        if path == "/workset/NEW/product/PROD":
            return add
        return 200, b""

    monkeypatch.setattr(session, "_req", fake_req)
    return calls


def test_license_in_a_workset_signs_in_that_workset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A license stays in its workset (another installation's, claimed by its session): adding
    it to a new one fails with "Product belongs to another workset which is still active" and a
    claim of its workset with 404, but the certificate is issued in its workset as it is. That
    workset is neither claimed nor released, so the session using it keeps it."""
    session = MyKnxSession(access_token="", session_id="s", next_ot_token="t")
    calls = _recording_req(
        session, monkeypatch, b'[{"id": "OTHER"}, {"id": "PROD", "worksetId": "WS9"}]'
    )

    cert = session.project_certificate("PROD", "hash", "P-9.certificate")

    assert cert.startswith(b'CERT KNX:"P-9.certificate"')
    assert calls == [
        "GET /product/getAll",
        "POST /workset/WS9/product/PROD/certificate",
    ]


def test_license_in_no_workset_gets_a_new_one(monkeypatch: pytest.MonkeyPatch) -> None:
    session = MyKnxSession(access_token="", session_id="s", next_ot_token="t")
    calls = _recording_req(session, monkeypatch, b'[{"id": "PROD", "worksetId": null}]')

    session.project_certificate("PROD", "hash", "P-9.certificate")

    assert calls == [
        "GET /product/getAll",
        "POST /workset",
        "POST /workset/NEW/claim",
        "POST /workset/NEW/product/PROD",
        "POST /workset/NEW/product/PROD/certificate",
        "POST /workset/NEW/release",
    ]


def test_license_in_an_unnamed_other_workset_says_so(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = MyKnxSession(access_token="", session_id="s", next_ot_token="t")
    _recording_req(
        session,
        monkeypatch,
        b"{}",
        add=(
            422,
            b'"Cannot add product to workset: session error\\n'
            b'Product belongs to another workset which is still active."',
        ),
    )

    with pytest.raises(MyKnxError) as excinfo:
        session.project_certificate("PROD", "hash", "P-9.certificate")

    message = excinfo.value.user_message
    assert message.startswith("The license is in another workset")
    assert "still active" in message


# --- credentials must never reach the log ---------------------------------------------------


def test_redact_url_drops_the_query() -> None:
    assert (
        _redact_url("https://openapi.knx.org/v1/user/login?username=u&password=p")
        == "https://openapi.knx.org/v1/user/login?<redacted>"
    )
    # No query string -> unchanged, so ordinary endpoints stay readable in the log.
    assert (
        _redact_url("https://openapi.knx.org/v1/product/getAll")
        == "https://openapi.knx.org/v1/product/getAll"
    )


class _FakeResponse:
    status = 200
    headers: ClassVar[dict[str, str]] = {
        "x-session-id": "S1",
        "x-next-ot-token": "T1",
    }

    def __enter__(self) -> _FakeResponse:
        return self

    def __exit__(self, *exc: object) -> None:
        return None

    def read(self) -> bytes:
        return b"Login successful"


def test_login_does_not_log_the_password(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """The editor captures this logger at DEBUG and can copy the whole log to the clipboard, so a
    logged password would travel straight into a pasted bug report."""

    def _fake_urlopen(req: Any, timeout: float = 0.0) -> _FakeResponse:
        assert (
            "password=hunter2" in req.full_url
        )  # still sent on the wire, as the API requires
        return _FakeResponse()

    monkeypatch.setattr(urllib.request, "urlopen", _fake_urlopen)

    with caplog.at_level(logging.DEBUG, logger="xknxeditor.proj.core.myknx_cert"):
        MyKnxSession(access_token="").login("user@example.com", "hunter2")

    captured = "\n".join(r.getMessage() for r in caplog.records)
    assert captured, "expected the request to be logged at DEBUG"
    assert "hunter2" not in captured
    assert "user@example.com" not in captured
    assert "?<redacted>" in captured


def test_certificate_name_is_the_certificate_filename() -> None:
    """projectName is `{pid}.certificate`; the server echoes it into the CERT header."""
    assert myknx_cert.certificate_name("P-0532") == "P-0532.certificate"


def test_project_hash_is_the_folder_signature_verbatim() -> None:
    """projectHash is the base64 folder signature sent unchanged (server hashes it), not sha256."""
    sig = "I0PYKDTsrx1/P+3BbLnQScy5DmGulsdXzB23mVKjYcdq"
    assert myknx_cert.project_hash(sig.encode("utf-8")) == sig
    # a UTF-8 BOM (as written into the .signature file) is stripped.
    assert myknx_cert.project_hash(b"\xef\xbb\xbf" + sig.encode("utf-8")) == sig


def test_normalize_certificate_produces_crlf_form() -> None:
    """The API returns LF text; archives store CRLF with exactly one trailing CRLF."""
    out = myknx_cert.normalize_certificate(
        'CERT KNX:"P-1.certificate"\n\tID="CloudLicense"\n\tSIGN=AB\n'
    )
    assert out == b'CERT KNX:"P-1.certificate"\r\n\tID="CloudLicense"\r\n\tSIGN=AB\r\n'
    # idempotent: already-CRLF input must not gain \r\r\n
    assert myknx_cert.normalize_certificate(out.decode("utf-8")) == out


def test_normalize_certificate_collapses_trailing_blank_lines() -> None:
    assert myknx_cert.normalize_certificate("CERT\n\n\n") == b"CERT\r\n"


def test_certificate_response_bare_json_string_is_unescaped(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The endpoint returns a bare JSON *string*; returning it verbatim wrote the escaping into
    {pid}.certificate (file starting with a quote, literal \\n), which is rejected."""
    body = (
        b'"CERT KNX:\\"P-9.certificate\\"\\n\\tID=\\"CloudLicense\\"\\n\\tSIGN=FF\\n"'
    )

    session = MyKnxSession(access_token="", session_id="s", next_ot_token="t")
    calls: list[str] = []

    def fake_req(
        method: str, path: str, body_bytes: bytes | None = None
    ) -> tuple[int, bytes]:
        calls.append(path)
        if path.endswith("/certificate"):
            return 200, body
        if path == "/workset":
            return 200, b'{"id": "WS1"}'
        return 200, b"{}"

    monkeypatch.setattr(session, "_req", fake_req)
    cert = session.project_certificate("PROD", "hash", "P-9.certificate")

    assert cert == (
        b'CERT KNX:"P-9.certificate"\r\n\tID="CloudLicense"\r\n\tSIGN=FF\r\n'
    )
    assert not cert.startswith(b'"')
    assert b"\\n" not in cert
