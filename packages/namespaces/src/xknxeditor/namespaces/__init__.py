from xknxeditor.namespaces.ids import unpadded_id
from xknxeditor.namespaces.schema import (
    SUPPORTED_VERSIONS,
    VERSION_MODULES,
    VERSION_NAMESPACES,
    VERSION_PATTERN,
    VersionError,
    detect_version,
    get_model_class,
    load_xml,
    serialize_xml,
)

__version__ = "0.1.0"

__all__ = [
    "SUPPORTED_VERSIONS",
    "VERSION_MODULES",
    "VERSION_NAMESPACES",
    "VERSION_PATTERN",
    "VersionError",
    "detect_version",
    "get_model_class",
    "load_xml",
    "serialize_xml",
    "unpadded_id",
]
