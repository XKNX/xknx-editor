"""Core project domain: a SQLite-backed store mutated via a command/event log."""

from xknxeditor.proj.core.compact import CompactCancelled, CompactResult
from xknxeditor.proj.core.event_store import EventStore
from xknxeditor.proj.core.ga_export import (
    GaExport,
    import_ga_export,
    is_ga_export,
    read_ga_export,
    write_ga_export,
)
from xknxeditor.proj.core.key_extract import (
    DLL_NAME,
    MYKNX_DLL_NAME,
    TRACE_DLL_NAME,
    KeyExtractionError,
    default_dll_path,
    default_myknx_dll_path,
    default_trace_dll_path,
    extract_converter_key,
    extract_oauth_credentials,
    extract_trace_key,
    extraction_backend,
)
from xknxeditor.proj.core.knxproj_export import (
    ExportCancelled,
    ExportResult,
    export_knxproj,
    fetch_master_xml,
    master_cache_path,
    read_master_xml,
)
from xknxeditor.proj.core.knxproj_import import import_knxproj
from xknxeditor.proj.core.knxproj_signing import (
    current_signing_key,
    reset_signing_key,
    set_signing_key,
    signing_key_is_placeholder,
)
from xknxeditor.proj.core.myknx_cert import (
    MyKnxError,
    MyKnxSession,
    fetch_myknx_products,
    myknx_certificate_signer,
    session_certificate_signer,
    session_products,
    sign_exported_knxproj,
)
from xknxeditor.proj.core.product_prune import (
    ProductPruneReport,
    prune_product_blob,
    prune_product_members,
)
from xknxeditor.proj.core.service import (
    AdditionalAddressError,
    AdditionalAddressInfo,
    GroupAddressSecurityError,
    ProjectService,
)
from xknxeditor.proj.core.trace_crypto import (
    current_marker,
    decrypt_comment,
    encrypt_comment,
    is_encrypted,
    reset_trace_key,
    set_trace_key,
    trace_key_available,
)

__all__ = [
    "DLL_NAME",
    "MYKNX_DLL_NAME",
    "TRACE_DLL_NAME",
    "AdditionalAddressError",
    "AdditionalAddressInfo",
    "CompactCancelled",
    "CompactResult",
    "EventStore",
    "ExportCancelled",
    "ExportResult",
    "GaExport",
    "GroupAddressSecurityError",
    "KeyExtractionError",
    "MyKnxError",
    "MyKnxSession",
    "ProductPruneReport",
    "ProjectService",
    "current_marker",
    "current_signing_key",
    "decrypt_comment",
    "default_dll_path",
    "default_myknx_dll_path",
    "default_trace_dll_path",
    "encrypt_comment",
    "export_knxproj",
    "extract_converter_key",
    "extract_oauth_credentials",
    "extract_trace_key",
    "extraction_backend",
    "fetch_master_xml",
    "fetch_myknx_products",
    "import_ga_export",
    "import_knxproj",
    "is_encrypted",
    "is_ga_export",
    "master_cache_path",
    "myknx_certificate_signer",
    "prune_product_blob",
    "prune_product_members",
    "read_ga_export",
    "read_master_xml",
    "reset_signing_key",
    "reset_trace_key",
    "session_certificate_signer",
    "session_products",
    "set_signing_key",
    "set_trace_key",
    "sign_exported_knxproj",
    "signing_key_is_placeholder",
    "trace_key_available",
    "write_ga_export",
]
