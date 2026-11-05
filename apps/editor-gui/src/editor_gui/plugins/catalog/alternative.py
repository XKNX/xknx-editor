"""Alternative product catalog: public cached index and authenticated downloads."""

from __future__ import annotations

import contextlib
import gzip
import hashlib
import json
import math
import re
import threading
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any, TypeGuard
from urllib.parse import urlsplit

import httpx

from editor_gui.device_info import application_version_text
from editor_gui.plugins.catalog.online_catalog import OnlineCatalogError
from editor_gui.settings import load_settings, save_settings

DEFAULT_URL = "https://reko.x3muha.de"
_SETTINGS = "alternative_catalog"
_INDEX_LIMIT = 128 * 1024 * 1024
_DOWNLOAD_LIMIT = 512 * 1024 * 1024


class AlternativeCatalogError(OnlineCatalogError):
    def __init__(
        self,
        message: str,
        *,
        status: int = 0,
        code: str = "",
        retry_after: int = 0,
        reset_at: str = "",
    ) -> None:
        self.status = status
        self.code = code
        self.retry_after = retry_after
        self.reset_at = reset_at
        detail = f" ({code})" if code else ""
        if retry_after:
            detail += f"; retry after {retry_after} s"
        if reset_at:
            detail += f"; reset at {reset_at}"
        super().__init__(message + detail)


def server_url(value: str) -> str:
    """An HTTPS origin, optionally followed by a deployment path."""
    value = value.strip().rstrip("/")
    parts = urlsplit(value)
    if (
        parts.scheme != "https"
        or not parts.hostname
        or parts.username is not None
        or parts.password is not None
        or parts.query
        or parts.fragment
        or "\\" in value
        or any(c.isspace() for c in value)
    ):
        raise ValueError(
            "catalog server must be an HTTPS URL without credentials, query or fragment"
        )
    _ = parts.port
    return value


@dataclass(frozen=True)
class AlternativeSettings:
    base_url: str = DEFAULT_URL
    user: str = ""
    password: str = field(default="", repr=False)

    @classmethod
    def load(cls) -> AlternativeSettings:
        raw = load_settings(_SETTINGS)
        try:
            base = server_url(str(raw.get("base_url", DEFAULT_URL)))
        except ValueError:
            return cls()
        return cls(base, str(raw.get("user", "")), str(raw.get("password", "")))


@dataclass(frozen=True)
class AlternativeEntry:
    id: str
    manufacturer: str
    manufacturer_id: str
    name: str
    order_numbers: tuple[str, ...]
    file_name: str
    file_type: str
    size: int
    sha256: str
    updated: str
    programs: tuple[dict[str, Any], ...]
    download_url: str
    search_text: str = field(repr=False)

    def as_dict(self) -> dict[str, Any]:
        """Metadata for clients; download URLs never contain authentication."""
        return {
            "id": self.id,
            "manufacturer": self.manufacturer,
            "manufacturer_id": self.manufacturer_id,
            "name": self.name,
            "order_numbers": list(self.order_numbers),
            "file_name": self.file_name,
            "file_type": self.file_type,
            "size": self.size,
            "sha256": self.sha256,
            "updated": self.updated,
            "programs": [
                {**program, "version_text": program_version_text(program["version"])}
                for program in self.programs
            ],
            "download_url": self.download_url,
        }


def _search_text(text: str) -> str:
    return "".join(c for c in text.casefold() if c.isalnum())


def program_version_text(value: str) -> str:
    """The index's hexadecimal version byte in the editor's major.minor notation."""
    if not re.fullmatch(r"[0-9A-Fa-f]{2}", value):
        return value
    return application_version_text(int(value, 16))


def _is_object(value: object) -> TypeGuard[dict[str, Any]]:
    return isinstance(value, dict)


def _is_list(value: object) -> TypeGuard[list[Any]]:
    return isinstance(value, list)


def parse_index(raw: object) -> tuple[AlternativeEntry, ...]:
    """Validate a complete snapshot before it replaces the offline catalog."""
    if (
        not _is_object(raw)
        or raw.get("schema") != 1
        or not _is_list(raw.get("entries"))
    ):
        raise ValueError("unsupported alternative catalog index")
    entries: list[AlternativeEntry] = []
    seen: set[str] = set()
    if raw.get("count") != len(raw["entries"]):
        raise ValueError("catalog entry count does not match")
    for row in raw["entries"]:
        if not _is_object(row):
            raise ValueError("invalid catalog entry")
        if row.get("fileType") != "knxprod":
            continue
        for key in (
            "id",
            "manufacturer",
            "manufacturerId",
            "name",
            "fileName",
            "fileType",
            "sha256",
            "updated",
            "download_url",
        ):
            if not isinstance(row.get(key), str):
                raise ValueError(f"invalid catalog field: {key}")
        if (
            not row["id"]
            or row["id"] in seen
            or not row["fileName"].lower().endswith("." + row["fileType"])
            or not re.fullmatch(r"[0-9a-fA-F]{64}", row["sha256"])
            or type(row.get("size")) is not int
            or row["size"] < 0
        ):
            raise ValueError("invalid catalog file metadata")
        _relative_download(row["download_url"])
        orders = row.get("orderNumbers")
        programs = row.get("programs")
        if (
            not _is_list(orders)
            or not all(isinstance(v, str) for v in orders)
            or not _is_list(programs)
        ):
            raise ValueError("invalid catalog order numbers or programs")
        texts = [
            row["manufacturer"],
            row["manufacturerId"],
            row["name"],
            row["fileName"],
            *orders,
        ]
        for program in programs:
            if not _is_object(program):
                raise ValueError("invalid catalog program")
            for key in (
                "key",
                "displayKey",
                "name",
                "manufacturer",
                "manufacturerId",
                "applicationNumber",
                "version",
                "maskVersion",
            ):
                if not isinstance(program.get(key), str):
                    raise ValueError(f"invalid program field: {key}")
                texts.append(program[key])
            texts.append(program_version_text(program["version"]))
            for key in ("orderNumbers", "productNames"):
                values = program.get(key)
                if not _is_list(values) or not all(isinstance(v, str) for v in values):
                    raise ValueError(f"invalid program field: {key}")
                texts.extend(values)
        entries.append(
            AlternativeEntry(
                row["id"],
                row["manufacturer"],
                row["manufacturerId"],
                row["name"] or row["fileName"],
                tuple(orders),
                row["fileName"],
                row["fileType"],
                row["size"],
                row["sha256"].lower(),
                row["updated"],
                tuple(programs),
                row["download_url"],
                "\n".join(_search_text(text) for text in texts),
            )
        )
        seen.add(row["id"])
    return tuple(entries)


def _relative_download(value: str) -> str:
    parts = urlsplit(value)
    if (
        not value.startswith("/")
        or value.startswith("//")
        or parts.scheme
        or parts.netloc
        or parts.fragment
        or "\\" in value
        or any(ord(c) < 32 for c in value)
        or parts.path != "/api/download"
    ):
        raise ValueError(
            "download URL must be the server's relative /api/download endpoint"
        )
    return value


class AlternativeCatalogClient:
    """Serialized HTTP operations publish snapshots that the UI can read without blocking."""

    def __init__(
        self,
        cache_dir: Path,
        *,
        settings: AlternativeSettings | None = None,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.settings = settings or AlternativeSettings.load()
        server_url(self.settings.base_url)
        self._cache_dir = cache_dir
        self._http = httpx.Client(
            timeout=60,
            follow_redirects=False,
            transport=transport,
            headers={"User-Agent": "xknx-editor", "Accept-Encoding": "gzip"},
        )
        self._operation = threading.RLock()
        self._entries: tuple[AlternativeEntry, ...] = ()
        self._by_id: dict[str, AlternativeEntry] = {}
        self._loaded = False
        self._etag = ""
        self.generation = 0
        self.checked_at = ""
        self.generated_at = ""
        self.last_error = ""
        self._token = ""
        self._token_until = 0.0
        self._next_request = 0.0
        self._retry_until = 0.0
        self._download_until = 0.0
        self._csrf = ""
        self._registration_session = False
        self._closed = threading.Event()

    def configure(self, base_url: str, user: str, password: str | None = None) -> None:
        """Save credentials locally. A server change requires an explicit new password."""
        base_url = server_url(base_url)
        if not self._operation.acquire(blocking=False):
            raise AlternativeCatalogError(
                "catalog busy; try again after the current operation"
            )
        try:
            changed = base_url != self.settings.base_url
            secret = (
                password
                if password is not None
                else (
                    ""
                    if changed or user != self.settings.user
                    else self.settings.password
                )
            )
            self.settings = AlternativeSettings(base_url, user.strip(), secret)
            save_settings(
                _SETTINGS,
                {"base_url": base_url, "user": user.strip(), "password": secret},
            )
            self._token = self._csrf = ""
            self._token_until = 0
            self._registration_session = False
            self._http.cookies.clear()
            self._download_until = 0
            if changed:
                self._entries = ()
                self._by_id = {}
                self._loaded = False
                self._etag = self.checked_at = self.generated_at = self.last_error = ""
                self._retry_until = 0
                self.generation += 1
        finally:
            self._operation.release()

    def status(self) -> dict[str, Any]:
        return {
            "base_url": self.settings.base_url,
            "user": self.settings.user,
            "has_password": bool(self.settings.password),
            "count": len(self._entries),
            "cached": self._loaded,
            "checked_at": self.checked_at,
            "generated_at": self.generated_at,
            "error": self.last_error,
        }

    def search(
        self, query: str = "", limit: int | None = None, offset: int = 0
    ) -> tuple[list[AlternativeEntry], int]:
        """Search the offline snapshot; return all matches unless a page limit is given."""
        if (limit is not None and not 1 <= limit <= 1000) or offset < 0:
            raise ValueError(
                "limit must be None or 1..1000 and offset must be nonnegative"
            )
        terms = [_search_text(term) for term in query.split()]
        found: list[tuple[int, AlternativeEntry]] = []
        for entry in self._entries:
            if all(term in entry.search_text for term in terms):
                manufacturer = _search_text(entry.manufacturer)
                product = "\n".join(
                    _search_text(text)
                    for text in (entry.name, entry.file_name, *entry.order_numbers)
                )
                rank = (
                    0
                    if manufacturer == _search_text(query)
                    else 1
                    if all(term in manufacturer for term in terms)
                    else 2
                    if all(term in product for term in terms)
                    else 3
                )
                found.append((rank, entry))
        found.sort(key=lambda hit: hit[0])
        end = None if limit is None else offset + limit
        return [entry for _, entry in found[offset:end]], len(found)

    def _cache_path(self) -> Path:
        key = hashlib.sha256(self.settings.base_url.encode()).hexdigest()[:20]
        return self._cache_dir / f"alternative_catalog_{key}.json.gz"

    def load_cache(self) -> None:
        """Load once on a worker thread; a corrupt cache is replaced by the next refresh."""
        with self._operation:
            if self._loaded:
                return
            try:
                with gzip.open(self._cache_path(), "rb") as handle:
                    data = handle.read(_INDEX_LIMIT + 1)
                if len(data) > _INDEX_LIMIT:
                    return
                cached = json.loads(data)
                if cached["base_url"] != self.settings.base_url:
                    return
                entries = parse_index(cached["index"])
                self._etag = str(cached.get("etag", ""))
                self.generated_at = str(cached["index"].get("generatedAt", ""))
                self._publish(entries)
            except (OSError, EOFError, ValueError, KeyError, TypeError):
                pass

    def _publish(self, entries: tuple[AlternativeEntry, ...]) -> None:
        self._by_id = {entry.id: entry for entry in entries}
        self._entries = entries
        self._loaded = True
        self.generation += 1

    def refresh(self) -> dict[str, Any]:
        with self._operation:
            self.load_cache()
            try:
                headers = (
                    {"If-None-Match": self._etag} if self._etag and self._loaded else {}
                )
                response = self._request(
                    "GET",
                    "/api/catalog/index.json",
                    headers=headers,
                    limit=_INDEX_LIMIT,
                )
                if response.status_code != 304:
                    raw = response.json()
                    entries = parse_index(raw)
                    etag = response.headers.get("etag", "")
                    cached = {
                        "base_url": self.settings.base_url,
                        "etag": etag,
                        "index": raw,
                    }
                    path = self._cache_path()
                    try:
                        path.parent.mkdir(parents=True, exist_ok=True)
                        import tempfile

                        with tempfile.NamedTemporaryFile(
                            dir=path.parent, delete=False
                        ) as handle:
                            temporary = Path(handle.name)
                            try:
                                with gzip.GzipFile(fileobj=handle, mode="wb") as zipped:
                                    zipped.write(
                                        json.dumps(cached, ensure_ascii=False).encode()
                                    )
                                handle.close()
                                temporary.replace(path)
                            finally:
                                temporary.unlink(missing_ok=True)
                    except OSError:
                        pass
                    self._etag = etag
                    self.generated_at = str(raw.get("generatedAt", ""))
                    self._publish(entries)
                elif not self._loaded:
                    raise ValueError("server returned 304 without a cached index")
                self.checked_at = datetime.now(UTC).isoformat()
                self.last_error = ""
            except AlternativeCatalogError as exc:
                self.last_error = str(exc)
                raise
            except ValueError as exc:
                self.last_error = "invalid alternative catalog index"
                raise AlternativeCatalogError(self.last_error) from exc
            return self.status()

    def _request(
        self,
        method: str,
        path: str,
        *,
        headers: dict[str, str] | None = None,
        body: dict[str, str] | None = None,
        limit: int = 1024 * 1024,
    ) -> httpx.Response:
        if self._closed.is_set():
            raise AlternativeCatalogError("catalog closed")
        now = time.monotonic()
        if now < self._retry_until:
            raise AlternativeCatalogError(
                "catalog rate limit",
                status=429,
                retry_after=math.ceil(self._retry_until - now),
            )
        time.sleep(max(0, self._next_request - now))
        self._next_request = time.monotonic() + 0.55
        try:
            with self._http.stream(
                method, self.settings.base_url + path, headers=headers, json=body
            ) as response:
                data = bytearray()
                for chunk in response.iter_bytes():
                    if self._closed.is_set():
                        raise AlternativeCatalogError("catalog closed")
                    data.extend(chunk)
                    if len(data) > limit:
                        raise AlternativeCatalogError(
                            "catalog response exceeds size limit"
                        )
                response_headers = dict(response.headers)
                response_headers.pop("content-encoding", None)
                response_headers.pop("content-length", None)
                result = httpx.Response(
                    response.status_code,
                    headers=response_headers,
                    content=bytes(data),
                    request=response.request,
                )
        except httpx.HTTPError as exc:
            raise AlternativeCatalogError("catalog connection failed") from exc
        if result.status_code != 304 and not result.is_success:
            self._raise_response(result, body)
        return result

    def _raise_response(
        self, response: httpx.Response, body: dict[str, str] | None = None
    ) -> None:
        try:
            raw = response.json()
            raw = raw if _is_object(raw) else {}
        except ValueError:
            raw = {}
        code = str(raw.get("code", ""))
        retry = response.headers.get("retry-after", "")
        seconds = 0
        try:
            seconds = max(0, math.ceil(float(retry)))
        except (ValueError, OverflowError):
            with contextlib.suppress(ValueError, TypeError, OverflowError):
                seconds = max(
                    0,
                    math.ceil(
                        (
                            parsedate_to_datetime(retry) - datetime.now(UTC)
                        ).total_seconds()
                    ),
                )
        reset_at = str(raw.get("resetAt", ""))
        if code == "DOWNLOAD_DAILY_LIMIT":
            with contextlib.suppress(ValueError, TypeError):
                seconds = max(
                    seconds,
                    math.ceil(
                        (
                            datetime.fromisoformat(reset_at) - datetime.now(UTC)
                        ).total_seconds()
                    ),
                )
            self._download_until = time.monotonic() + max(seconds, 60)
        elif response.status_code in (429, 503):
            self._retry_until = time.monotonic() + max(seconds, 1)
        message = str(
            raw.get("error") or f"catalog request failed: HTTP {response.status_code}"
        )
        for secret in (
            self.settings.password,
            self._token,
            self._csrf,
            (body or {}).get("password", ""),
            (body or {}).get("code", ""),
        ):
            if secret:
                message = message.replace(secret, "***")
                code = code.replace(secret, "***")
                reset_at = reset_at.replace(secret, "***")
        raise AlternativeCatalogError(
            message,
            status=response.status_code,
            code=code,
            retry_after=seconds,
            reset_at=reset_at,
        )

    def _login(self) -> None:
        if not self.settings.user or not self.settings.password:
            raise AlternativeCatalogError(
                "configure a username and password before downloading"
            )
        response = self._request(
            "POST",
            "/api/auth/token",
            body={"user": self.settings.user, "password": self.settings.password},
        )
        try:
            raw = response.json()
            token = raw["token"]
            lifetime = float(raw["expiresIn"])
            if (
                not isinstance(token, str)
                or not token
                or raw["tokenType"].lower() != "bearer"
                or not math.isfinite(lifetime)
                or lifetime <= 0
            ):
                raise ValueError
        except (ValueError, KeyError, TypeError, AttributeError) as exc:
            raise AlternativeCatalogError("invalid catalog token response") from exc
        self._token = token
        self._token_until = time.monotonic() + max(0, lifetime - 30)

    def download(self, entry_id: str) -> bytes:
        with self._operation:
            self.load_cache()
            entry = self._by_id.get(entry_id)
            if entry is None:
                raise ValueError(
                    "unknown catalog entry; refresh or search the catalog first"
                )
            if entry.size > _DOWNLOAD_LIMIT:
                raise AlternativeCatalogError(
                    "catalog file exceeds download size limit"
                )
            if time.monotonic() < self._download_until:
                raise AlternativeCatalogError(
                    "daily download limit reached",
                    code="DOWNLOAD_DAILY_LIMIT",
                    retry_after=math.ceil(self._download_until - time.monotonic()),
                )
            if not self._token or time.monotonic() >= self._token_until:
                self._login()
            for attempt in range(2):
                try:
                    response = self._request(
                        "GET",
                        _relative_download(entry.download_url),
                        headers={"Authorization": f"Bearer {self._token}"},
                        limit=max(entry.size, 1024 * 1024),
                    )
                    break
                except AlternativeCatalogError as exc:
                    if attempt or exc.status != 401 or exc.code != "INVALID_TOKEN":
                        raise
                    self._token = ""
                    self._login()
            else:
                raise AssertionError("download retry exhausted")
            data = response.content
            if (
                len(data) != entry.size
                or hashlib.sha256(data).hexdigest() != entry.sha256
            ):
                raise AlternativeCatalogError(
                    "catalog download size or SHA-256 does not match the index"
                )
            return data

    def _session(self) -> dict[str, Any]:
        raw = self._request("GET", "/api/me").json()
        if (
            not _is_object(raw)
            or not isinstance(raw.get("csrfToken"), str)
            or not raw["csrfToken"]
        ):
            raise AlternativeCatalogError("invalid catalog registration session")
        self._csrf = raw["csrfToken"]
        return raw

    def _registration_post(self, path: str, body: dict[str, str]) -> dict[str, Any]:
        self._session()
        response = self._request(
            "POST", path, body=body, headers={"X-CSRF-Token": self._csrf}
        )
        raw = response.json()
        if not _is_object(raw):
            raise AlternativeCatalogError("invalid registration response")
        return raw

    def register(self, user: str, email: str, password: str) -> dict[str, str]:
        """Create an account and keep its session for the email-code verification."""
        if not re.fullmatch(r"[A-Za-z0-9._-]{3,32}", user) or not email.strip():
            raise ValueError(
                "registration needs a username (3..32 letters, digits, . _ -) and email"
            )
        if (
            len(password) < 10
            or len(password.encode()) > 72
            or not any(c.islower() for c in password)
            or not any(c.isupper() for c in password)
            or not any(c.isdecimal() for c in password)
            or not any(not c.isalnum() for c in password)
        ):
            raise ValueError(
                "password needs at least 10 characters, upper/lowercase, digit and special character (maximum 72 UTF-8 bytes)"
            )
        with self._operation:
            self._http.cookies.clear()
            self._registration_session = False
            raw = self._registration_post(
                "/api/auth/register",
                {
                    "user": user,
                    "email": email.strip(),
                    "password": password,
                    "website": "",
                },
            )
            self.settings = AlternativeSettings(self.settings.base_url, user, password)
            save_settings(
                _SETTINGS,
                {
                    "base_url": self.settings.base_url,
                    "user": user,
                    "password": password,
                },
            )
            self._token = ""
            self._token_until = self._download_until = 0
            self._registration_session = True
            return {"status": "unverified", "message": str(raw.get("message", ""))}

    def resume_registration(self) -> dict[str, str]:
        with self._operation:
            if not self.settings.user or not self.settings.password:
                raise ValueError(
                    "configure the registration's username and password first"
                )
            self._registration_post(
                "/api/auth/login",
                {"user": self.settings.user, "password": self.settings.password},
            )
            raw = self._session()
            pending = raw.get("registrationPending")
            self._registration_session = True
            return {
                "status": str(pending.get("status", "")) if _is_object(pending) else "",
                "message": "",
            }

    def verify_registration(self, code: str) -> dict[str, str]:
        if not re.fullmatch(r"[0-9]{6}", code):
            raise ValueError("enter the six-digit email verification code")
        return self._finish_registration(
            "/api/auth/register/verify", {"code": code}, "pending"
        )

    def resend_registration_code(self) -> dict[str, str]:
        return self._finish_registration("/api/auth/register/resend", {}, "unverified")

    def _finish_registration(
        self, path: str, body: dict[str, str], status: str
    ) -> dict[str, str]:
        with self._operation:
            if not self._registration_session:
                self.resume_registration()
            raw = self._registration_post(path, body)
            return {"status": status, "message": str(raw.get("message", ""))}

    def close(self) -> None:
        if self._closed.is_set():
            return
        self._closed.set()

        def close_when_idle() -> None:
            with self._operation:
                self._http.close()

        if self._operation.acquire(blocking=False):
            try:
                self._http.close()
            finally:
                self._operation.release()
        else:
            threading.Thread(
                target=close_when_idle, name="alternative-close", daemon=True
            ).start()
