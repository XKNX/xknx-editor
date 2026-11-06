"""Alternative catalog and account registration using the editor's shared service."""

# pyright: reportUnusedFunction=false
from __future__ import annotations

from typing import TYPE_CHECKING, Any

from editor_gui.plugins.mcp.context import McpContext, make_tool

if TYPE_CHECKING:
    from fastmcp import FastMCP


def register(mcp: FastMCP, ctx: McpContext) -> None:
    tool = make_tool(mcp, ctx)
    catalog = ctx.api.catalog
    client = catalog.alternative

    @tool("read")
    def catalog_alternative_status() -> dict[str, Any]:
        """Read {base_url, user, has_password, count, cached, checked_at, generated_at, error}.
        No password or token is returned. count is the number of .knxprod files in the offline
        index, cached tells whether an index was loaded. Dates are ISO 8601, empty if unknown."""
        client.load_cache()
        return client.status()

    @tool("write")
    def catalog_alternative_configure(
        base_url: str, user: str, password: str | None = None
    ) -> dict[str, Any]:
        """Save the alternative catalog's HTTPS base URL and login in the editor's local settings.
        Password omitted: keep it only if URL and user are unchanged. Empty password clears it.
        Changing server clears the active index and login; refresh afterward. Returns the same
        public fields as catalog_alternative_status. Outside project undo history."""
        client.configure(base_url, user, password)
        return client.status()

    @tool("read", open_world=True)
    def catalog_alternative_refresh() -> dict[str, Any]:
        """Update the public .knxprod index with ETag validation, without login. A failed refresh
        preserves the offline cache and reports an error. Returns catalog_alternative_status fields."""
        return client.refresh()

    @tool("read")
    def catalog_alternative_search(
        query: str = "", limit: int = 100, offset: int = 0
    ) -> dict[str, Any]:
        """Search cached .knxprod files by manufacturer, product, order number, filename or program
        number/version. All space-separated terms must match (case and punctuation ignored).
        Manufacturer matches come first, then product fields, then matches in program metadata.
        limit is 1..1000, offset >= 0. No network; refresh first when no cache exists.
        Returns {items, total}; items have id, manufacturer, manufacturer_id, name, order_numbers,
        file_name, file_type, size (bytes), sha256, updated, download_url and programs. Each program
        includes key, displayKey, name, manufacturer, manufacturerId, applicationNumber, version,
        maskVersion, orderNumbers and productNames. version is the raw hexadecimal byte;
        version_text is the editor's major.minor display (23 -> 2.3, 2A -> 2.10).
        Use the file id for download."""
        client.load_cache()
        entries, total = client.search(query, limit, offset)
        return {"items": [entry.as_dict() for entry in entries], "total": total}

    @tool("write", open_world=True)
    def catalog_alternative_download(entry_id: str) -> dict[str, Any]:
        """Download one .knxprod id from catalog_alternative_search using the saved login, check
        SHA-256 and size, and import into the local catalog. Refreshes open-project resolution.
        Returns {added_refs, added_versions}, as catalog_download_online_products; use these refs
        with project_add_device. Outside project undo history. Does not program the bus.
        An INVALID_TOKEN response renews the token and retries once; other errors are reported.
        Daily limits and Retry-After are respected without automatic repeated downloads."""
        from editor_gui.plugins.mcp.tools.catalog import added_products

        data = client.download(entry_id)

        def import_product() -> dict[str, Any]:
            added = catalog.import_alternative_product(data)
            ctx.api.project.refresh_catalog_resolution()
            return added_products(added)

        return ctx.run_locked(import_product, timeout=300)

    @tool("write", open_world=True)
    def catalog_alternative_register(
        user: str, email: str, password: str
    ) -> dict[str, str]:
        """Register a new account at the configured server and request its email verification code.
        This sends a registration email. User: 3..32 ASCII letters/digits or . _ -; password:
        >=10 characters including upper/lowercase, digit, special character, <=72 UTF-8 bytes.
        Saves the new login locally on success. Returns {status: 'unverified', message}.
        The account needs email verification and then approval by the server administrator."""
        return client.register(user, email, password)

    @tool("write", open_world=True)
    def catalog_alternative_resume_registration() -> dict[str, str]:
        """Sign in with the saved login to resume a registration, including after editor restart.
        Returns {status, message}; status 'unverified' needs an email code, 'pending' awaits manual
        approval, empty means no pending registration was reported."""
        return client.resume_registration()

    @tool("write", open_world=True)
    def catalog_alternative_verify_registration(
        verification_code: str,
    ) -> dict[str, str]:
        """Submit the six-digit code from the registration email. Resumes the saved registration
        session if needed. Returns {status: 'pending', message}; administrator approval is still
        required before the new account can download."""
        return client.verify_registration(verification_code)

    @tool("write", open_world=True)
    def catalog_alternative_resend_registration_code() -> dict[str, str]:
        """Request another verification email for the saved registration, resuming its session
        if needed. Returns {status: 'unverified', message}. Respects the server's Retry-After."""
        return client.resend_registration_code()
