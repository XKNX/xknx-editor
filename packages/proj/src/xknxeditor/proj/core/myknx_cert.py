"""Pure-Python, Windows-independent MyKnx cloud client to obtain a project certificate.

Requests a project certificate (``{pid}.certificate``) using the user's own MyKnx account.

Protocol:
  * Auth: ``POST /v1/user/login?username=&password=`` -> ``X-Session-ID`` + ``X-Next-OT-Token``.
  * API ``https://openapi.knx.org/v1`` (JSON over TLS). Authed requests send ``X-Session-ID`` +
    ``X-OT-Token``; the response returns the next ``X-Next-OT-Token``.
  * Certificate is workset-scoped and server-signed:
    ``POST /v1/workset`` -> ``.../claim`` -> ``.../product/{productId}``
    -> ``.../product/{productId}/certificate`` (body ``{"projectHash", "projectName"}``) -> ``.../release``.
    A license stays in its workset after the release; once it is in one (``worksetId`` in
    ``GET /v1/product/getAll``), that workset is claimed instead of creating and filling a new one.
  * Requires a cloud-enabled product license; others fail add-product with HTTP 422.
"""

from __future__ import annotations

import email.message
import io
import json
import logging
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import cast

logger = logging.getLogger(__name__)

API_BASE = "https://openapi.knx.org/v1"
# OAuth device-code login for accounts with 2FA (user+password login is rejected there).
ID_BASE = "https://id.knx.org"


class MyKnxError(RuntimeError):
    """A MyKnx API call failed.

    Carries the HTTP ``status`` and the raw server ``detail`` (for logs/debugging) plus a concise,
    actionable ``user_message`` suitable for showing in the UI. ``str(err)`` is the technical form.
    """

    def __init__(
        self,
        message: str,
        *,
        status: int | None = None,
        detail: str = "",
        user_message: str = "",
    ) -> None:
        super().__init__(message)
        self.status = status
        self.detail = detail
        self.user_message = user_message or message


def _server_detail(resp: bytes) -> str:
    """Decode a MyKnx error body into readable text (the API returns a JSON string or object)."""
    text = resp.decode("utf-8", "replace").strip()
    try:
        parsed: object = json.loads(text)
    except json.JSONDecodeError:
        return text
    if isinstance(parsed, str):
        return parsed
    if isinstance(parsed, dict):
        fields = cast("dict[str, object]", parsed)
        for key in ("message", "detail", "error", "title"):
            value = fields.get(key)
            if value:
                return str(value)
    return text


# A license is in one workset at a time: adding one that is in another workset is answered
# with "Product belongs to another workset which is still active" (when the license list,
# which names its workset, could not be read first).
_LICENSE_ELSEWHERE = (
    "The license is in another workset (another installation using it), and MyKnx did not "
    "tell which one. Try again, or pick another license that can sign online."
)


def _myknx_says(detail: str) -> str:
    flat = detail.replace("\n", " ").strip()
    return f" (MyKnx: {flat})" if flat else ""


def project_hash(folder_signature: bytes) -> str:
    """Return the ``projectHash`` for the certificate endpoint.

    It is the base64 ``{pid}.signature`` content sent verbatim as UTF-8 -- not hashed. The server
    derives the digest itself. A leading UTF-8 BOM is stripped.
    """
    if folder_signature.startswith(b"\xef\xbb\xbf"):
        folder_signature = folder_signature[3:]
    return folder_signature.decode("utf-8")


def certificate_name(pid: str) -> str:
    """Return the ``projectName`` to send: ``{pid}.certificate``.

    The server echoes it into the certificate's ``CERT KNX:"..."`` header, which must name the
    certificate file. A different name yields a valid certificate bound to the wrong name.
    """
    return f"{pid}.certificate"


def normalize_certificate(text: str) -> bytes:
    """Return certificate text with CRLF line endings and exactly one trailing CRLF.

    The API returns LF text; archives store CRLF. Normalising keeps ``{pid}.certificate`` and the
    ``.validation`` block byte-identical.
    """
    normalized = text.replace("\r\n", "\n").replace("\r", "\n").rstrip("\n")
    return (normalized.replace("\n", "\r\n") + "\r\n").encode("utf-8")


def _redact_url(url: str) -> str:
    """Return ``url`` with its query string replaced by a placeholder.

    ``/user/login`` carries the user's MyKnx username and password as query parameters (see
    :meth:`MyKnxSession.login`), and the editor captures this module's DEBUG records into its in-app
    log panel — which offers a copy-everything button that ignores the level filter. Logging the raw
    URL would therefore put the account password one click away from a pasted bug report.

    No query string on any of these endpoints carries information worth logging, so drop them all
    rather than maintain a list of parameter names to hide.
    """
    base, sep, _query = url.partition("?")
    return f"{base}?<redacted>" if sep else base


def _body_preview(body: bytes, limit: int = 2000) -> str:
    """Return a log-safe, truncated text preview of a response body.

    Response *bodies* on these endpoints carry no secrets (login returns a status string; the
    certificate endpoint returns the certificate that is embedded in the shared ``.knxproj``), so
    they are safe to log for user bug reports. The rotating ``X-OT-Token``/``X-Session-ID`` live in
    *headers*, which are deliberately never logged (see :func:`_redact_url`).
    """
    text = body.decode("utf-8", "replace").strip()
    return (
        text[:limit] + f"... ({len(body)} bytes total)" if len(text) > limit else text
    )


def _headers_with_reason(
    raw: email.message.Message | None, reason: str | None
) -> dict[str, str]:
    """Lowercase the response headers and add the HTTP status reason phrase.

    The reason phrase (e.g. "Unauthorized") and headers like ``WWW-Authenticate`` are the only
    error information a response carries when the body is empty, so surface them under the synthetic
    ``x-http-reason`` key (no server sends that header) for :func:`_server_detail` fallbacks.
    """
    headers = {k.lower(): v for k, v in (raw.items() if raw is not None else [])}
    if reason:
        headers["x-http-reason"] = reason
    return headers


def _post(
    url: str, body: bytes, headers: dict[str, str], timeout: float
) -> tuple[int, dict[str, str], bytes]:
    req = urllib.request.Request(url, data=body, headers=headers, method="POST")
    safe_url = _redact_url(url)
    logger.debug("myknx POST %s (%d bytes)", safe_url, len(body))
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            resp = r.read()
            logger.debug(
                "myknx POST %s -> %d body=%s", safe_url, r.status, _body_preview(resp)
            )
            return (
                r.status,
                _headers_with_reason(r.headers, getattr(r, "reason", None)),
                resp,
            )
    except urllib.error.HTTPError as e:
        resp = e.read()
        logger.debug(
            "myknx POST %s -> HTTPError %d body=%s",
            safe_url,
            e.code,
            _body_preview(resp),
        )
        return e.code, _headers_with_reason(e.headers, getattr(e, "reason", None)), resp


def _get(
    url: str, headers: dict[str, str], timeout: float
) -> tuple[int, dict[str, str], bytes]:
    req = urllib.request.Request(url, headers=headers, method="GET")
    safe_url = _redact_url(url)
    logger.debug("myknx GET %s", safe_url)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            resp = r.read()
            logger.debug(
                "myknx GET %s -> %d body=%s", safe_url, r.status, _body_preview(resp)
            )
            return (
                r.status,
                _headers_with_reason(r.headers, getattr(r, "reason", None)),
                resp,
            )
    except urllib.error.HTTPError as e:
        resp = e.read()
        logger.debug(
            "myknx GET %s -> HTTPError %d body=%s",
            safe_url,
            e.code,
            _body_preview(resp),
        )
        return e.code, _headers_with_reason(e.headers, getattr(e, "reason", None)), resp


def _empty_headers() -> dict[str, str]:
    return {}


@dataclass
class MyKnxSession:
    access_token: str
    session_id: str = ""
    next_ot_token: str = ""
    timeout: float = 30.0
    extra_headers: dict[str, str] = field(default_factory=_empty_headers)

    def _headers(self, json_body: bool) -> dict[str, str]:
        h = {"accept": "application/json", "User-Agent": "ETS/6.4.1 (x64) XKNX"}
        if json_body:
            h["content-type"] = "application/json"
        # VERIFIED live: authed requests send X-Session-ID + the CURRENT one-time token as
        # X-OT-Token; the response returns the next one in X-Next-OT-Token (see _absorb).
        if self.session_id:
            h["X-Session-ID"] = self.session_id
        if self.next_ot_token:
            h["X-OT-Token"] = self.next_ot_token
        h.update(self.extra_headers)
        return h

    def _absorb(self, resp_headers: dict[str, str]) -> None:
        # Rotate the one-time token: the response's X-Next-OT-Token is the next request's X-OT-Token.
        if "x-next-ot-token" in resp_headers:
            self.next_ot_token = resp_headers["x-next-ot-token"]
        if "x-session-id" in resp_headers:
            self.session_id = resp_headers["x-session-id"]

    def login(self, username: str, password: str) -> None:
        """Establish a session: ``POST /user/login?username=&password=``.

        Returns 200 with ``x-session-id`` + ``x-next-ot-token`` headers, stored for later requests.
        Accounts with 2FA are rejected here -- use :meth:`oauth_login` for those.

        The credentials travel as query parameters because that is what the API accepts. That keeps
        them out of our logs only because :func:`_redact_url` strips the query before logging -- they
        are still visible to anything that records request lines server-side.
        """
        qs = urllib.parse.urlencode({"username": username, "password": password})
        status, headers, body = _post(
            f"{API_BASE}/user/login?{qs}",
            b"",
            self._headers(json_body=False),
            self.timeout,
        )
        if status // 100 != 2:
            detail = (
                _server_detail(body)
                or headers.get("www-authenticate", "")
                or headers.get("x-http-reason", "")
            )
            logger.error("MyKnx login failed: HTTP %s: %s", status, detail)
            raise MyKnxError(
                f"HTTP {status}: {detail}" if detail else f"HTTP {status}",
                status=status,
                detail=detail,
                user_message="MyKnx login failed - check your username and password.",
            )
        self._absorb(headers)
        if not self.session_id:
            raise MyKnxError("login returned no x-session-id")

    def oauth_login(
        self,
        client_id: str,
        client_secret: str,
        on_verification: Callable[[str, str], None],
        *,
        poll_interval: float = 5.0,
        overall_timeout: float = 600.0,
    ) -> None:
        """Establish a session via the OAuth device-code flow (for 2FA accounts).

        Posts a device code to id.knx.org, calls ``on_verification(url, user_code)`` so the UI can
        open the browser, polls for the access token, then swaps it for an openapi session at
        ``/user/oauthLogin?token=``. Blocking; run from a worker thread.
        """
        form = {
            "content-type": "application/x-www-form-urlencoded",
            "accept": "application/json",
        }
        cred = (
            f"client_id={client_id}&client_secret={urllib.parse.quote(client_secret)}"
        )
        status, _h, body = _post(
            f"{ID_BASE}/oauth/devicecode", cred.encode(), form, self.timeout
        )
        if status // 100 != 2:
            raise MyKnxError(
                f"device code request failed: HTTP {status}", status=status
            )
        dc = json.loads(body)
        verification_url = dc.get("verification_uri_complete") or dc["verification_uri"]
        logger.info(
            "myknx oauth device code: url=%s user_code=%s expires_in=%s",
            verification_url,
            dc.get("user_code"),
            dc.get("expires_in"),
        )
        on_verification(verification_url, dc["user_code"])

        token_body = (
            "grant_type=urn:ietf:params:oauth:grant-type:device_code"
            f"&device_code={dc['device_code']}&client_id={client_id}"
            f"&client_secret={urllib.parse.quote(client_secret)}"
        ).encode()
        deadline = time.monotonic() + overall_timeout
        access_token = ""
        while time.monotonic() < deadline:
            status, _h, body = _post(
                f"{ID_BASE}/oauth/accesstoken", token_body, form, self.timeout
            )
            if status // 100 == 2:
                access_token = json.loads(body)["access_token"]
                break
            text = body.decode("utf-8", "replace")
            if status == 428 or "authorization_pending" in text or "slow_down" in text:
                logger.debug("myknx oauth token poll: HTTP %s (waiting)", status)
                time.sleep(poll_interval)
                continue
            raise MyKnxError(
                f"token request failed: HTTP {status}", status=status, detail=text
            )
        if not access_token:
            raise MyKnxError("timed out waiting for browser sign-in")
        logger.info("myknx oauth: access token received, exchanging for session")

        url = f"{API_BASE}/user/oauthLogin?token={urllib.parse.quote(access_token)}"
        status, headers, body = _post(
            url, b"", self._headers(json_body=False), self.timeout
        )
        if status // 100 != 2:
            raise MyKnxError(
                f"oauthLogin failed: HTTP {status}",
                status=status,
                detail=_server_detail(body),
                user_message="MyKnx sign-in failed.",
            )
        self._absorb(headers)
        if not self.session_id:
            raise MyKnxError("oauthLogin returned no x-session-id")

    def _req(
        self, method: str, path: str, body: bytes | None = None
    ) -> tuple[int, bytes]:
        json_body = body is not None
        if method == "POST":
            status, headers, resp = _post(
                f"{API_BASE}{path}", body or b"", self._headers(json_body), self.timeout
            )
        else:  # GET
            status, headers, resp = _get(
                f"{API_BASE}{path}", self._headers(False), self.timeout
            )
        self._absorb(headers)
        return status, resp

    def entities(self) -> list[dict[str, object]]:
        status, resp = self._req("GET", "/entity/getAll")
        if status // 100 != 2:
            raise RuntimeError(f"entity/getAll failed: HTTP {status}: {resp[:200]!r}")
        return json.loads(resp or b"[]")

    def products(self) -> list[dict[str, object]]:
        status, resp = self._req("GET", "/product/getAll")
        if status // 100 != 2:
            raise RuntimeError(f"product/getAll failed: HTTP {status}: {resp[:200]!r}")
        return json.loads(resp or b"[]")

    def product_types(self) -> dict[str, dict[str, object]]:
        """Map each product-type id to its type object (``GET /productType/getAll``).

        A product from :meth:`products` carries only ids and a ``licenseNumber`` -- no display
        name and no capability flags; both live on its product type. The type's ``encryptions``
        list tells whether an online certificate is possible (it contains ``"cloud"`` for
        cloud-enabled types, e.g. ETS6; ETS5/older or add-on-only types do not), so callers resolve
        both the name and cloud-capability via this map."""
        status, resp = self._req("GET", "/productType/getAll")
        if status // 100 != 2:
            raise RuntimeError(
                f"productType/getAll failed: HTTP {status}: {resp[:200]!r}"
            )
        types: list[dict[str, object]] = json.loads(resp or b"[]")
        return {str(t.get("id") or ""): t for t in types}

    def _product_workset(self, product_id: str) -> str | None:
        """The workset the license ``product_id`` belongs to, or None. None too when the license
        list cannot be read: a new workset is tried then."""
        try:
            products = self.products()
        except (RuntimeError, OSError, ValueError) as exc:
            logger.warning("license list not readable: %s", exc)
            return None
        for product in products:
            if str(product.get("id") or "") == product_id:
                return str(product.get("worksetId") or "") or None
        return None

    def _add_product(self, ws: str, product_id: str) -> None:
        """Add the license ``product_id`` to the workset ``ws``."""
        s, resp = self._req("POST", f"/workset/{ws}/product/{product_id}")
        if s // 100 != 2:
            detail = _server_detail(resp)
            logger.error(
                "add product to workset failed: HTTP %s (product_id=%s): %s",
                s,
                product_id,
                detail,
            )
            # The most common failure: the picked license is not cloud-enabled.
            cloud_disabled = s == 422 and "cloud" in detail.lower()
            if cloud_disabled:
                user_message = (
                    "This license cannot sign projects online: "
                    + detail.replace("\n", " ").strip()
                    + ". Pick a cloud-enabled ETS license (e.g. ETS Professional), or use "
                    "'License with a dongle' instead."
                )
            elif s == 422 and "another workset" in detail.lower():
                user_message = _LICENSE_ELSEWHERE + _myknx_says(detail)
            else:
                user_message = f"MyKnx rejected the license (HTTP {s}): " + (
                    detail.replace("\n", " ").strip()
                )
            raise MyKnxError(
                f"add product to workset failed: HTTP {s}: {detail}",
                status=s,
                detail=detail,
                user_message=user_message,
            )

    def project_certificate(
        self, product_id: str, project_hash_hex: str, project_name: str
    ) -> bytes:
        """Request a project certificate for ``product_id``.

        A license is in one workset at a time and stays there (another installation's workset,
        claimed by its session). For a license in a workset the certificate is requested in that
        workset as it is: ``POST /v1/workset/{ws}/product/{product_id}/certificate`` with
        ``{"projectHash","projectName"}``, without claiming or releasing it, so the session using
        it keeps it (its claim answers 404 here anyway). For a license in none: create a
        workset, claim it, add the product, request the certificate, release it. Returns the
        certificate bytes. The product must be a cloud-enabled license; others yield HTTP 422.
        """
        logger.debug("requesting project certificate", extra={"product_id": product_id})
        held = self._product_workset(product_id)
        if held is not None:
            logger.debug("license belongs to workset", extra={"workset": held})
            return self._certificate(held, product_id, project_hash_hex, project_name)
        s, resp = self._req(
            "POST", "/workset", json.dumps({"description": "xknx-editor"}).encode()
        )
        if s // 100 != 2:
            detail = _server_detail(resp)
            logger.error("create workset failed: HTTP %s: %s", s, detail)
            raise MyKnxError(
                f"create workset failed: HTTP {s}: {detail}", status=s, detail=detail
            )
        ws = json.loads(resp)["id"]
        logger.debug("workset created", extra={"workset": ws})
        try:
            s, resp = self._req("POST", f"/workset/{ws}/claim")
            if s // 100 != 2:
                detail = _server_detail(resp)
                logger.error("claim workset failed: HTTP %s: %s", s, detail)
                raise MyKnxError(
                    f"claim workset failed: HTTP {s}: {detail}",
                    status=s,
                    detail=detail,
                    user_message="MyKnx did not hand over the new workset "
                    f"(HTTP {s}){_myknx_says(detail)}.",
                )
            self._add_product(ws, product_id)
            return self._certificate(ws, product_id, project_hash_hex, project_name)
        finally:
            self._req("POST", f"/workset/{ws}/release")

    def _certificate(
        self, ws: str, product_id: str, project_hash_hex: str, project_name: str
    ) -> bytes:
        """The project certificate for ``product_id`` in the workset ``ws``."""
        payload = json.dumps(
            {"projectHash": project_hash_hex, "projectName": project_name}
        ).encode()
        # projectHash is the base64 folder signature sent verbatim (see project_hash); log its
        # length + prefix so a rejected certificate can be diagnosed from a user's log file.
        logger.debug(
            "certificate request: projectName=%s projectHash=%d chars prefix=%.24s",
            project_name,
            len(project_hash_hex),
            project_hash_hex,
        )
        s, resp = self._req(
            "POST", f"/workset/{ws}/product/{product_id}/certificate", payload
        )
        if s // 100 != 2:
            detail = _server_detail(resp)
            logger.error("certificate request failed: HTTP %s: %s", s, detail)
            raise MyKnxError(
                f"certificate request failed: HTTP {s}: {detail}",
                status=s,
                detail=detail,
                user_message=f"MyKnx did not issue the certificate (HTTP {s})"
                f"{_myknx_says(detail)}.",
            )
        logger.info("project certificate obtained", extra={"product_id": product_id})
        text = resp.decode("utf-8", "replace").lstrip()
        if text.startswith("{"):
            try:
                data = json.loads(resp)
                for key in ("certificate", "file", "File", "data"):
                    if data.get(key):
                        return normalize_certificate(str(data[key]))
            except json.JSONDecodeError:
                pass
        # The endpoint returns the certificate as a bare JSON *string* (not an object), i.e.
        # the body is `"CERT KNX:\"...\"\n\tID=\"CloudLicense\"\n..."`. Returning it verbatim
        # writes the JSON escaping into {pid}.certificate, so the file starts with a quote and
        # contains literal \n / \" — the project is then rejected as uncertified.
        if text.startswith('"'):
            try:
                return normalize_certificate(json.loads(resp))
            except (json.JSONDecodeError, TypeError):
                pass
        return normalize_certificate(text)


def obtain_certificate(
    username: str,
    password: str,
    product_id: str,
    folder_signature: bytes,
    project_name: str,
) -> bytes:
    """End-to-end: log in with the user's MyKnx credentials, then request the project certificate.

    NOTE: login is verified live; the certificate request itself still hits the unresolved
    data-route auth (see the module docstring) and will fail until that is captured.
    """
    session = MyKnxSession(access_token="")
    session.login(username, password)
    return session.project_certificate(
        product_id, project_hash(folder_signature), project_name
    )


def _type_is_cloud_capable(product_type: dict[str, object] | None) -> bool:
    """Return whether a product type can sign projects online.

    A type carries an ``encryptions`` list; it contains ``"cloud"`` exactly for the types the
    certificate endpoint accepts (ETS6 licenses and cloud-enabled add-ons). ETS5/older Professional
    or dongle-only types have no ``"cloud"`` entry and always fail add-product with HTTP 422. This
    mirrors the CloudKit license filter (IsExpired + HasFeature)."""
    if product_type is None:
        return False
    encryptions = product_type.get("encryptions")
    if not isinstance(encryptions, list):
        return False
    return "cloud" in encryptions


def session_products(session: MyKnxSession) -> list[dict[str, object]]:
    """Return the account's products for an already-authenticated session (OAuth 2FA path).

    Enriches each product with ``name`` + ``cloud_capable`` like :func:`fetch_myknx_products`.
    """
    products = session.products()
    types = session.product_types()
    for p in products:
        product_type = types.get(str(p.get("productTypeId") or ""))
        p["name"] = str(product_type.get("name") or "") if product_type else ""
        p["cloud_capable"] = _type_is_cloud_capable(product_type)
    return products


def fetch_myknx_products(
    username: str, password: str, *, timeout: float = 30.0
) -> list[dict[str, object]]:
    """Log in and return the account's products/licenses (``GET /product/getAll``).

    Each product is enriched with a ``name`` (its product-type name) and a ``cloud_capable`` flag
    (whether its product type can sign projects online, see :func:`_type_is_cloud_capable`),
    resolved via :meth:`MyKnxSession.product_types` because the product itself carries only ids and
    a ``licenseNumber``. Used by the GUI so the user can pick a license by name and see which ones
    can actually produce an online certificate. Blocking (network I/O); call from a worker thread."""
    session = MyKnxSession(access_token="", timeout=timeout)
    session.login(username, password)
    return session_products(session)


def myknx_certificate_signer(
    username: str,
    password: str,
    product_id: str,
    *,
    timeout: float = 30.0,
) -> Callable[[str, bytes, str], bytes]:
    """Return a ``CertificateSigner`` for :func:`knxproj_export.export_knxproj`.

    The returned callable logs into MyKnx with the user's own credentials and requests the
    server-signed project certificate for the exported folder signature, so a single
    ``export_knxproj(..., certificate_signer=myknx_certificate_signer(...))`` produces a
    fully-signed, certified ``.knxproj``. Requires a cloud-enabled product license on the account
    (others yield HTTP 422). Blocking; call the export from a worker thread.
    """

    def _sign(_pid: str, folder_signature: bytes, project_name: str) -> bytes:
        logger.debug(
            "myknx signer invoked: product_id=%s project=%s sig=%d bytes",
            product_id,
            project_name,
            len(folder_signature),
        )
        session = MyKnxSession(access_token="", timeout=timeout)
        session.login(username, password)
        cert = session.project_certificate(
            product_id, project_hash(folder_signature), project_name
        )
        logger.debug("myknx signer done: certificate %d bytes", len(cert))
        return cert

    return _sign


def session_certificate_signer(
    session: MyKnxSession, product_id: str
) -> Callable[[str, bytes, str], bytes]:
    """Return a ``CertificateSigner`` that uses an already-authenticated :class:`MyKnxSession`.

    For the OAuth 2FA flow: the interactive browser sign-in runs before export, then this signer
    only mints the certificate on the export worker thread.
    """

    def _sign(_pid: str, folder_signature: bytes, project_name: str) -> bytes:
        return session.project_certificate(
            product_id, project_hash(folder_signature), project_name
        )

    return _sign


# --- knxproj archive helpers (two-phase flow: export -> read signature -> cert -> add) ---


def read_folder_signature(knxproj: Path | str) -> tuple[str, bytes]:
    """Return ``(pid, {pid}.signature bytes)`` from an exported ``.knxproj`` archive.

    The ``pid`` (project id, e.g. ``P-XXXX``) is the name of the ``.signature`` member; its bytes
    are the converter-key folder signature the certificate binds to (see :func:`project_hash`).
    """
    with zipfile.ZipFile(knxproj) as zf:
        names = [n for n in zf.namelist() if n.endswith(".signature") and "/" not in n]
        if not names:
            raise RuntimeError(f"{knxproj} has no top-level .signature member")
        name = names[0]
        return name[: -len(".signature")], zf.read(name)


def add_certificate_to_archive(knxproj: Path | str, certificate: bytes) -> None:
    """Add ``{pid}.certificate`` and ``.validation`` to an exported ``.knxproj`` in place.

    The validation outcome is recorded in a top-level ``.validation`` with the certificate inlined;
    both are written so either reader is satisfied (see knxproj_export.validation_record).
    """
    from xknxeditor.proj.core.knxproj_export import validation_record

    knxproj = Path(knxproj)
    pid, sig = read_folder_signature(knxproj)
    with zipfile.ZipFile(knxproj) as zf:
        members = {n: zf.read(n) for n in zf.namelist()}
    members[f"{pid}.certificate"] = certificate
    members[".validation"] = validation_record(certificate, sig)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for n, data in members.items():
            zf.writestr(n, data)
    knxproj.write_bytes(buf.getvalue())


def sign_exported_knxproj(
    knxproj: Path | str,
    username: str,
    password: str,
    product_id: str,
    project_name: str | None = None,
) -> None:
    """End-to-end for an already-exported archive: MyKnx login -> cert -> bundle into the archive.

    Reads the folder signature from ``knxproj``, logs in with the user's MyKnx credentials, requests
    the server-signed certificate for ``product_id``, and writes ``{pid}.certificate`` plus
    ``.validation`` back into the archive. Blocking; call from a worker thread in a GUI.

    ``project_name`` defaults to :func:`certificate_name` for the archive's own pid; pass a value
    only to override it deliberately.
    """
    pid, folder_signature = read_folder_signature(knxproj)
    cert = obtain_certificate(
        username,
        password,
        product_id,
        folder_signature,
        project_name or certificate_name(pid),
    )
    add_certificate_to_archive(knxproj, cert)
