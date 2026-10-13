"""Per-user storage + runtime extraction of the MyKnx OAuth client credentials.

The device-code login (for accounts with 2FA) needs the ETS OAuth client id/secret. These are
proprietary constants, never shipped: they are read at runtime from the user's own
``Knx.Ets.MyKnx.dll`` via :func:`xknxeditor.proj.extract_oauth_credentials` and cached per-user under
:func:`settings.config_dir` so extraction runs only once.
"""

from __future__ import annotations

from editor_gui.settings import load_settings, save_settings
from xknxeditor.proj import default_myknx_dll_path, extract_oauth_credentials

_NAME = "myknx_oauth"


def load_cached_credentials() -> tuple[str, str] | None:
    """Return the cached ``(client_id, client_secret)``, or ``None`` if unset."""
    data = load_settings(_NAME)
    try:
        client_id = str(data["client_id"])
        client_secret = str(data["client_secret"])
    except (KeyError, TypeError):
        return None
    return (client_id, client_secret) if client_id and client_secret else None


def save_credentials(client_id: str, client_secret: str) -> None:
    """Persist the OAuth client credentials."""
    save_settings(_NAME, {"client_id": client_id, "client_secret": client_secret})


def resolve_credentials() -> tuple[str, str]:
    """Return cached credentials, else extract them from the installed ETS and cache them.

    Raises :class:`RuntimeError` if ETS is not installed (no ``Knx.Ets.MyKnx.dll`` found).
    """
    cached = load_cached_credentials()
    if cached is not None:
        return cached
    dll = default_myknx_dll_path()
    if not dll:
        raise RuntimeError(
            "Knx.Ets.MyKnx.dll not found (install ETS to enable 2FA sign-in)"
        )
    client_id, client_secret = extract_oauth_credentials(dll)
    save_credentials(client_id, client_secret)
    return client_id, client_secret
