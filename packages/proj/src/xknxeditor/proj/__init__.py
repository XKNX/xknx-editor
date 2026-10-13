"""xknxeditor-proj: an editable KNX project backed by one relational SQLite file.

Each project is a single on-disk database. All edits run through the command-based
:class:`~xknxeditor.proj.core.service.ProjectService`, which logs every command to an ``events``
table so undo/redo works. Topology and group-address models (:mod:`xknxeditor.proj.models`) are
the package's own, a slice of the KNX IR rather than the IR. See :mod:`xknxeditor.proj.core`.

Ref-only by design: a device keeps a ``product_ref_id`` and ``hardware2program_ref_id`` but nothing
here touches the catalog to resolve the application. The caller (the GUI, which holds the catalog)
expands it and hands the parameters and com-object refs to :meth:`ProjectService.add_device`.
"""

__version__ = "0.1.0"

from xknxeditor.proj.core import (
    DLL_NAME,
    TRACE_DLL_NAME,
    CompactCancelled,
    CompactResult,
    ExportResult,
    KeyExtractionError,
    MyKnxError,
    ProductPruneReport,
    ProjectService,
    current_marker,
    current_signing_key,
    decrypt_comment,
    default_dll_path,
    default_trace_dll_path,
    encrypt_comment,
    export_knxproj,
    extract_converter_key,
    extract_trace_key,
    extraction_backend,
    fetch_master_xml,
    fetch_myknx_products,
    import_ga_export,
    import_knxproj,
    is_encrypted,
    is_ga_export,
    myknx_certificate_signer,
    prune_product_blob,
    prune_product_members,
    read_master_xml,
    reset_signing_key,
    reset_trace_key,
    set_signing_key,
    set_trace_key,
    sign_exported_knxproj,
    signing_key_is_placeholder,
    trace_key_available,
)
from xknxeditor.proj.db import ProjectStorageError, ensure_sqlite_writable

__all__ = [
    "DLL_NAME",
    "TRACE_DLL_NAME",
    "CompactCancelled",
    "CompactResult",
    "ExportResult",
    "KeyExtractionError",
    "MyKnxError",
    "ProductPruneReport",
    "ProjectService",
    "ProjectStorageError",
    "current_marker",
    "current_signing_key",
    "decrypt_comment",
    "default_dll_path",
    "default_trace_dll_path",
    "encrypt_comment",
    "ensure_sqlite_writable",
    "export_knxproj",
    "extract_converter_key",
    "extract_trace_key",
    "extraction_backend",
    "fetch_master_xml",
    "fetch_myknx_products",
    "import_ga_export",
    "import_knxproj",
    "is_encrypted",
    "is_ga_export",
    "myknx_certificate_signer",
    "prune_product_blob",
    "prune_product_members",
    "read_master_xml",
    "reset_signing_key",
    "reset_trace_key",
    "set_signing_key",
    "set_trace_key",
    "sign_exported_knxproj",
    "signing_key_is_placeholder",
    "trace_key_available",
]
