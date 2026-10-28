# The @mcp.tool decorator registers each tool via its side effect, not a direct call.
# pyright: reportUnusedFunction=false
"""Keys read from the KNX authoring tool's assemblies the user provides: the signing key (Signing),
the Project Log's comment key and the MyKNX sign-in's client. Their status, reading them like the
GUI does, and resetting the signing key. Key material never leaves the editor."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from fastmcp.exceptions import ToolError

from editor_gui import myknx_oauth, signing_key, trace_key
from editor_gui.plugins.mcp.context import McpContext, make_tool
from xknxeditor.proj import (
    KeyExtractionError,
    extract_converter_key,
    extract_oauth_credentials,
    extract_trace_key,
    extraction_backend,
    trace_key_available,
)

if TYPE_CHECKING:
    from fastmcp import FastMCP

# The library Knx.Ets.Common.dll loads from its own folder.
_LOG4NET = "log4net"


def _extraction_error(error: KeyExtractionError) -> ToolError:
    if _LOG4NET in str(error):
        return ToolError(
            "Knx.Ets.Common.dll needs log4net.dll from its folder: put both in the same folder"
        )
    return ToolError(f"the key could not be read: {error}")


def register(mcp: FastMCP, ctx: McpContext) -> None:
    tool = make_tool(mcp, ctx)

    @tool("read")
    def keys_status() -> dict[str, Any]:
        """Return {signing_key, trace_key, myknx_client, extraction}. signing_key is "extracted"
        when a signing key is stored (exported projects are signed so other KNX tools accept
        them), else "placeholder". trace_key: whether Project Log comments can be decrypted.
        myknx_client: whether the MyKNX sign-in has its client credentials (needed for 2FA).
        extraction: the .NET runtime the editor reads assemblies with ("dotnet", "mono",
        "pythonnet"), or null when none is available. Key material is never returned."""
        return {
            "signing_key": "extracted"
            if signing_key.load_cached_key()
            else "placeholder",
            "trace_key": trace_key_available(),
            "myknx_client": myknx_oauth.load_cached_credentials() is not None,
            "extraction": extraction_backend(),
        }

    @tool("write")
    def signing_key_extract(path: str) -> dict[str, bool]:
        """Read the signing key from Knx.Ets.XmlSigning.dll at path on the editor host (from an
        installation of the KNX authoring tool the user owns) and store it, like Signing >
        Extract: exported projects are signed with it from now on. Takes a few seconds and needs
        a .NET runtime (see keys_status). Returns {stored: true}; the key itself is never
        returned."""
        try:
            modulus, private_exponent, public_exponent = extract_converter_key(path)
        except KeyExtractionError as error:
            raise _extraction_error(error) from error
        signing_key.save_key(modulus, private_exponent, public_exponent)
        return {"stored": True}

    @tool("destructive")
    def signing_key_reset() -> dict[str, bool]:
        """Forget the stored signing key, like Signing > Reset to Placeholder: exports are signed
        with the placeholder key again, which other KNX tools reject. Not undoable; read the key
        again with signing_key_extract. Returns {stored: false}."""
        signing_key.clear_key()
        return {"stored": False}

    @tool("write")
    def trace_key_extract(path: str) -> dict[str, bool]:
        """Read the key of the Project Log's comments from Knx.Ets.Common.dll at path on the
        editor host and store it, like the Project Log's Extract: the comments are shown
        decrypted from now on (project_log). log4net.dll from the same installation must be in the
        same folder. Takes a few seconds and needs a .NET runtime (see keys_status). Returns
        {stored: true}; the key itself is never returned."""
        try:
            key, iv, marker = extract_trace_key(path)
        except KeyExtractionError as error:
            raise _extraction_error(error) from error
        trace_key.save_key(key, iv, marker)
        return {"stored": True}

    @tool("write")
    def myknx_client_extract(path: str) -> dict[str, bool]:
        """Read the MyKNX sign-in's client credentials from Knx.Ets.MyKnx.dll at path on the
        editor host and store them: the sign-in for accounts with two-factor authentication
        needs them. Takes a few seconds and needs a .NET runtime (see keys_status). Returns
        {stored: true}; the credentials are never returned."""
        try:
            client_id, client_secret = extract_oauth_credentials(path)
        except KeyExtractionError as error:
            raise _extraction_error(error) from error
        myknx_oauth.save_credentials(client_id, client_secret)
        return {"stored": True}
