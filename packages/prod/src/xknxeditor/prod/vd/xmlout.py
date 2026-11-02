"""A small element tree and the serializer the converter writes its XML files with.

The converter serializes its object model: attributes in declaration order of the object model
(base class first), two-space indentation, CRLF line breaks, UTF-8 with byte order mark and no line
break after the root element. Attributes are given in any order here and sorted on output.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterator

NAMESPACE = "http://knx.org/xml/project/14"
CREATED_BY = "knxconv"
TOOL_VERSION = "5.6.241.33672"

# Element name -> object model class, where they differ (for the attribute order).
_CLASS_OF = {
    "Channel": "ApplicationProgramChannel",
    "ParameterBlock": "ComObjectParameterBlock",
    "choose": "ChannelChoose",
    "when": "When",
    "Memory": "ParameterMemory",
    "Property": "ParameterProperty",
    "Language": "LanguageData",
    "Translation": "TranslatedText",
    "LoadProcedure": "ApplicationProgramLoadProcedure",
    "Options": "ApplicationProgramOptions",
    "Extension": "ApplicationProgramExtension",
    "FileInfo": "BaggageFileInfo",
    "Enumeration": "ParameterTypeRestrictionEnumeration",
    "TypeNumber": "ParameterTypeNumber",
    "TypeRestriction": "ParameterTypeRestriction",
    "TypeText": "ParameterTypeText",
    "TypeFloat": "ParameterTypeFloat",
    "TypeTime": "ParameterTypeTime",
    "TypeDate": "ParameterTypeDate",
    "TypeIPAddress": "ParameterTypeIPAddress",
    "TypePicture": "ParameterTypePicture",
    "TypeRawData": "ParameterTypeRawData",
    "TypeColor": "ParameterTypeColor",
    "Attribute": "ProductAttribute",
}

# Attribute order per object model class (declaration order, base class members first).
_ATTR_ORDER: dict[str, tuple[str, ...]] = {
    "AbsoluteSegment": ("Id", "Name", "MemoryType", "Address", "Size", "UserMemory"),
    "AddressTable": ("CodeSegment", "Offset", "MaxEntries"),
    "ApplicationProgram": (
        "Id",
        "ApplicationNumber",
        "ApplicationVersion",
        "ProgramType",
        "MaskVersion",
        "VisibleDescription",
        "Name",
        "LoadProcedureStyle",
        "PeiType",
        "HelpTopic",
        "HelpFile",
        "ContextHelpFile",
        "IconFile",
        "DefaultLanguage",
        "DynamicTableManagement",
        "Linkable",
        "IsSecureEnabled",
        "MinEtsVersion",
        "OriginalManufacturer",
        "PreEts4Style",
        "ConvertedFromPreEts4Data",
        "CreatedFromLegacySchemaVersion",
        "IPConfig",
        "AdditionalAddressesCount",
        "MaxUserEntries",
        "MaxTunnelingUserEntries",
        "MaxSecurityIndividualAddressEntries",
        "MaxSecurityGroupKeyTableEntries",
        "MaxSecurityP2PKeyTableEntries",
        "NonRegRelevantDataVersion",
        "Broken",
        "DownloadInfoIncomplete",
        "ReplacesVersions",
        "Hash",
    ),
    "ApplicationProgramChannel": ("Name", "Text", "Number", "Id", "TextParameterRefId"),
    "ApplicationProgramExtension": (
        "EtsDownloadPlugin",
        "EtsUiPlugin",
        "EtsDataHandler",
        "EtsDataHandlerCapabilities",
        "RequiresExternalSoftware",
    ),
    "ApplicationProgramOptions": (
        "PreferPartialDownloadIfApplicationLoaded",
        "EasyCtrlModeModeStyleEmptyGroupComTables",
        "SetObjectTableLengthAlwaysToOne",
        "TextParameterEncoding",
        "TextParameterEncodingSelector",
        "TextParameterZeroTerminate",
        "ParameterByteOrder",
        "PartialDownloadOnlyVisibleParameters",
        "LegacyNoPartialDownload",
        "LegacyNoMemoryVerifyMode",
        "LegacyNoOptimisticWrite",
        "LegacyDoNotReportPropertyWriteErrors",
        "LegacyNoBackgroundDownload",
        "LegacyDoNotCheckManufacturerId",
        "LegacyAlwaysReloadAppIfCoVisibilityChanged",
        "LegacyNeverReloadAppIfCoVisibilityChanged",
        "LegacyDoNotSupportUndoDelete",
        "LegacyAllowPartialDownloadIfAp2Mismatch",
        "LegacyKeepObjectTableGaps",
        "LegacyProxyCommunicationObjects",
        "DeviceInfoIgnoreRunState",
        "DeviceInfoIgnoreLoadedState",
        "DeviceCompareAllowCompatibleManufacturerId",
        "LineCoupler0912NewProgrammingStyle",
        "PromptBeforeFullDownload",
        "LegacyPatchManufacturerIdInTaskSegment",
    ),
    "AssociationTable": ("CodeSegment", "Offset", "MaxEntries"),
    "Baggage": ("TargetPath", "Name", "FileIntegrity", "Id"),
    "BaggageFileInfo": ("Version", "TimeInfo", "Hidden", "ReadOnly"),
    "CatalogItem": (
        "Id",
        "Name",
        "Number",
        "VisibleDescription",
        "ProductRefId",
        "Hardware2ProgramRefId",
        "DefaultLanguage",
        "NonRegRelevantDataVersion",
    ),
    "CatalogSection": (
        "Id",
        "Name",
        "Number",
        "VisibleDescription",
        "DefaultLanguage",
        "NonRegRelevantDataVersion",
    ),
    "ChannelChoose": ("ParamRefId",),
    "ComObject": (
        "Id",
        "Name",
        "Text",
        "Number",
        "FunctionText",
        "Priority",
        "ObjectSize",
        "ReadFlag",
        "WriteFlag",
        "CommunicationFlag",
        "TransmitFlag",
        "UpdateFlag",
        "ReadOnInitFlag",
        "DatapointType",
        "InternalDescription",
        "SecurityRequired",
    ),
    "ComObjectParameterBlock": (
        "Id",
        "Name",
        "Text",
        "Access",
        "HelpTopic",
        "InternalDescription",
        "ParamRefId",
        "TextParameterRefId",
        "Inline",
        "Layout",
    ),
    "ComObjectRef": (
        "Id",
        "RefId",
        "Name",
        "Text",
        "Tag",
        "FunctionText",
        "Priority",
        "ObjectSize",
        "ReadFlag",
        "WriteFlag",
        "CommunicationFlag",
        "TransmitFlag",
        "UpdateFlag",
        "ReadOnInitFlag",
        "DatapointType",
        "TextParameterRefId",
    ),
    "ComObjectTable": ("CodeSegment", "Offset"),
    "DeviceCompare": ("StandardComTablesExpectable",),
    "Fixup": ("FunctionRef", "CodeSegment"),
    "Hardware": (
        "Id",
        "Name",
        "SerialNumber",
        "VersionNumber",
        "BusCurrent",
        "IsAccessory",
        "HasIndividualAddress",
        "HasApplicationProgram",
        "HasApplicationProgram2",
        "IsPowerSupply",
        "IsChoke",
        "IsCoupler",
        "IsPowerLineRepeater",
        "IsPowerLineSignalFilter",
        "IsCable",
        "IsIPEnabled",
        "IsRFRetransmitter",
        "RuntimeUnidirectional",
        "OriginalManufacturer",
        "RFDeviceMode",
        "NoDownloadWithoutPlugin",
        "NonRegRelevantDataVersion",
    ),
    "Hardware2Program": ("Id", "MediumTypes", "Hash", "CheckSums", "LoadedImage"),
    "KNX": ("CreatedBy", "ToolVersion"),
    "LdCtrlAbsSegment": (
        "AppliesTo",
        "LsmIdx",
        "ObjType",
        "Occurrence",
        "SegType",
        "Address",
        "Size",
        "Access",
        "MemType",
        "SegFlags",
    ),
    "LdCtrlClearLCFilterTable": ("AppliesTo", "UseFunctionProp"),
    "LdCtrlCompareMem": ("AppliesTo", "InlineData", "AddressSpace", "Address", "Size"),
    "LdCtrlCompareProp": (
        "AppliesTo",
        "InlineData",
        "Mask",
        "ObjIdx",
        "ObjType",
        "Occurrence",
        "PropId",
        "StartElement",
        "Count",
    ),
    "LdCtrlCompareRelMem": (
        "AppliesTo",
        "InlineData",
        "ObjIdx",
        "ObjType",
        "Occurrence",
        "Offset",
        "Size",
    ),
    "LdCtrlDeclarePropDesc": (
        "AppliesTo",
        "ObjIdx",
        "ObjType",
        "Occurrence",
        "PropId",
        "PropType",
        "MaxElements",
        "ReadAccess",
        "WriteAccess",
        "Writable",
    ),
    "LdCtrlDelay": ("AppliesTo", "MilliSeconds"),
    "LdCtrlInvokeFunctionProp": (
        "AppliesTo",
        "ObjIdx",
        "ObjType",
        "Occurrence",
        "PropId",
        "InlineData",
    ),
    "LdCtrlLoad": ("AppliesTo", "LsmIdx", "ObjType", "Occurrence"),
    "LdCtrlLoadCompleted": ("AppliesTo", "LsmIdx", "ObjType", "Occurrence"),
    "LdCtrlLoadImageMem": ("AppliesTo", "AddressSpace", "Address", "Size"),
    "LdCtrlLoadImageProp": (
        "AppliesTo",
        "ObjIdx",
        "ObjType",
        "Occurrence",
        "PropId",
        "Count",
        "StartElement",
    ),
    "LdCtrlLoadImageRelMem": (
        "AppliesTo",
        "ObjIdx",
        "ObjType",
        "Occurrence",
        "Offset",
        "Size",
    ),
    "LdCtrlMapError": ("AppliesTo", "LdCtrlFilter", "OriginalError", "MappedError"),
    "LdCtrlMasterReset": ("AppliesTo", "EraseCode", "ChannelNumber"),
    "LdCtrlMaxLength": ("AppliesTo", "LsmIdx", "ObjType", "Occurrence", "Size"),
    "LdCtrlMerge": ("AppliesTo", "MergeId"),
    "LdCtrlProgressText": ("AppliesTo", "TextId", "MessageRef"),
    "LdCtrlReadFunctionProp": (
        "AppliesTo",
        "ObjIdx",
        "ObjType",
        "Occurrence",
        "PropId",
    ),
    "LdCtrlRelSegment": (
        "AppliesTo",
        "LsmIdx",
        "ObjType",
        "Occurrence",
        "Size",
        "Mode",
        "Fill",
    ),
    "LdCtrlSetControlVariable": ("AppliesTo", "Name", "Value"),
    "LdCtrlTaskCtrl1": (
        "AppliesTo",
        "LsmIdx",
        "ObjType",
        "Occurrence",
        "Address",
        "Count",
    ),
    "LdCtrlTaskCtrl2": (
        "AppliesTo",
        "LsmIdx",
        "ObjType",
        "Occurrence",
        "Callback",
        "Address",
        "Seg0",
        "Seg1",
    ),
    "LdCtrlTaskPtr": (
        "AppliesTo",
        "LsmIdx",
        "ObjType",
        "Occurrence",
        "InitPtr",
        "SavePtr",
        "SerialPtr",
    ),
    "LdCtrlTaskSegment": ("AppliesTo", "LsmIdx", "ObjType", "Occurrence", "Address"),
    "LdCtrlUnload": ("AppliesTo", "LsmIdx", "ObjType", "Occurrence"),
    "LdCtrlWriteMem": (
        "AppliesTo",
        "AddressSpace",
        "Address",
        "Size",
        "Verify",
        "InlineData",
    ),
    "LdCtrlWriteProp": (
        "AppliesTo",
        "ObjIdx",
        "ObjType",
        "Occurrence",
        "PropId",
        "StartElement",
        "Count",
        "Verify",
        "InlineData",
    ),
    "LdCtrlWriteRelMem": (
        "AppliesTo",
        "ObjIdx",
        "ObjType",
        "Occurrence",
        "Offset",
        "Size",
        "Verify",
        "InlineData",
    ),
    "Parameter": (
        "LegacyPatchAlways",
        "Id",
        "Name",
        "ParameterType",
        "Text",
        "SuffixText",
        "Access",
        "Value",
        "InitialValue",
        "CustomerAdjustable",
    ),
    "ParameterCalculation": (
        "Id",
        "Language",
        "Name",
        "InternalDescription",
        "RLTransformationFunc",
        "RLTransformationParameters",
        "LRTransformationFunc",
        "LRTransformationParameters",
    ),
    "ParameterMemory": ("CodeSegment", "Offset", "BitOffset"),
    "ParameterProperty": (
        "ObjectIndex",
        "ObjectType",
        "Occurrence",
        "PropertyId",
        "Offset",
        "BitOffset",
    ),
    "ParameterRef": (
        "Id",
        "RefId",
        "Name",
        "Text",
        "SuffixText",
        "Tag",
        "DisplayOrder",
        "Access",
        "Value",
        "InitialValue",
        "CustomerAdjustable",
        "TextParameterRefId",
    ),
    "ParameterRefRef": ("RefId",),
    "ParameterCalculationLParameterRefRef": ("RefId", "AliasName"),
    "ParameterType": (
        "Id",
        "Name",
        "InternalDescription",
        "Plugin",
        "ValidationErrorRef",
    ),
    "ParameterTypeFloat": (
        "Encoding",
        "minInclusive",
        "maxInclusive",
        "Increment",
        "UIHint",
        "DisplayFormat",
    ),
    "ParameterTypeNumber": (
        "SizeInBit",
        "Type",
        "minInclusive",
        "maxInclusive",
        "Increment",
        "UIHint",
    ),
    "ParameterTypeRestriction": ("Base", "SizeInBit"),
    "ParameterTypeRestrictionEnumeration": (
        "Text",
        "Icon",
        "PictureAlignment",
        "Value",
        "Id",
        "DisplayOrder",
        "BinaryValue",
    ),
    "ParameterTypeText": ("SizeInBit", "Pattern"),
    "Product": (
        "Id",
        "Text",
        "OrderNumber",
        "IsRailMounted",
        "WidthInMillimeter",
        "VisibleDescription",
        "DefaultLanguage",
        "NonRegRelevantDataVersion",
        "Hash",
    ),
    "ProductAttribute": ("Id", "Name", "Value"),
    "RegistrationInfo": (
        "RegistrationStatus",
        "RegistrationNumber",
        "OriginalRegistrationNumber",
        "RegistrationDate",
    ),
    "RelativeSegment": ("Id", "Name", "Offset", "Size", "LoadStateMachine"),
    "TranslatedText": ("AttributeName", "Text"),
    "Union": ("SizeInBit",),
    "UnionParameter": (
        "Id",
        "Name",
        "ParameterType",
        "Offset",
        "BitOffset",
        "Text",
        "SuffixText",
        "Access",
        "Value",
        "InitialValue",
        "CustomerAdjustable",
        "DefaultUnionParameter",
    ),
}

_ORDER_INDEX = {
    cls: {a: i for i, a in enumerate(names)} for cls, names in _ATTR_ORDER.items()
}


class El:
    """An XML element: tag, attributes (sorted on output), children and an optional text."""

    __slots__ = ("attrs", "children", "cls", "tag", "text")

    tag: str
    attrs: dict[str, str]
    children: list[El]
    text: str | None
    cls: (
        str | None
    )  # object model class when the tag alone does not tell (attribute order)

    def __init__(
        self,
        tag: str,
        attrs: dict[str, str] | None = None,
        text: str | None = None,
        cls: str | None = None,
    ) -> None:
        self.tag = tag
        self.cls = cls
        self.attrs = attrs if attrs is not None else {}
        self.children = []
        self.text = text

    def add(self, child: El) -> El:
        self.children.append(child)
        return child

    def sub(
        self, tag: str, attrs: dict[str, str] | None = None, text: str | None = None
    ) -> El:
        """Append and return a new child element."""
        child = El(tag, attrs, text)
        self.children.append(child)
        return child

    def get(self, name: str) -> str | None:
        return self.attrs.get(name)

    def __setitem__(self, name: str, value: str) -> None:
        self.attrs[name] = value

    def __getitem__(self, name: str) -> str:
        return self.attrs[name]

    def find(self, tag: str) -> El | None:
        for child in self.children:
            if child.tag == tag:
                return child
        return None

    def iter(self) -> Iterator[El]:
        stack: list[El] = [self]
        while stack:
            el = stack.pop()
            yield el
            stack.extend(reversed(el.children))


def _escape_attr(value: str) -> str:
    if not any(c in value for c in '&<>"\r\n'):
        return value
    return (
        value.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("\r", "&#xD;")
        .replace("\n", "&#xA;")
    )


def _escape_text(value: str) -> str:
    return value.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _ordered(el: El) -> list[tuple[str, str]]:
    index = _ORDER_INDEX.get(el.cls or _CLASS_OF.get(el.tag, el.tag))
    items = list(el.attrs.items())
    if index is None or len(items) < 2:
        return items
    last = len(index)
    return sorted(items, key=lambda kv: index.get(kv[0], last))


def _write(el: El, depth: int, out: list[str]) -> None:
    indent = "  " * depth
    parts = [indent, "<", el.tag]
    for name, value in _ordered(el):
        parts.append(f' {name}="{_escape_attr(value)}"')
    if el.children:
        parts.append(">")
        out.append("".join(parts))
        for child in el.children:
            _write(child, depth + 1, out)
        out.append(f"{indent}</{el.tag}>")
    elif el.text is not None:
        parts.append(f">{_escape_text(el.text)}</{el.tag}>")
        out.append("".join(parts))
    else:
        parts.append(" />")
        out.append("".join(parts))


def document(manufacturer: El) -> El:
    """A ``KNX`` root element holding ``ManufacturerData`` with the given ``Manufacturer``."""
    root = El("KNX")
    root.sub("ManufacturerData").add(manufacturer)
    return root


def serialize(root: El, *, bom: bool = True) -> bytes:
    """The file bytes of a document with ``root`` as ``KNX`` element.

    ``bom=False`` writes the variant of the baggage list: no byte order mark and no encoding in the
    XML declaration.
    """
    out: list[str] = []
    for child in root.children:
        _write(child, 1, out)
    head = (
        '<KNX xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"'
        ' xmlns:xsd="http://www.w3.org/2001/XMLSchema"'
        f' CreatedBy="{CREATED_BY}" ToolVersion="{TOOL_VERSION}" xmlns="{NAMESPACE}">'
    )
    decl = '<?xml version="1.0" encoding="utf-8"?>' if bom else '<?xml version="1.0"?>'
    text = "\r\n".join([decl, head, *out, "</KNX>"])
    data = text.encode("utf-8")
    return b"\xef\xbb\xbf" + data if bom else data


def _net_g(value: float, digits: int) -> str:
    text = f"{value:.{digits}G}"
    if "E" in text:
        mantissa, _, exp = text.partition("E")
        sign = exp[0] if exp[0] in "+-" else "+"
        exp = exp.lstrip("+-").lstrip("0") or "0"
        text = f"{mantissa}E{sign}{exp.zfill(2)}"
    return text


def float32(value: float) -> str:
    """A single-precision value as the serializer writes it (round-trip format)."""
    import math
    import struct

    try:
        v = struct.unpack("<f", struct.pack("<f", value))[0]
    except OverflowError:
        # out of the single-precision range: infinite
        v = math.copysign(math.inf, value)
    if math.isnan(v):
        return "NaN"
    if math.isinf(v):
        return "INF" if v > 0 else "-INF"
    if v == int(v) and abs(v) < 1e7:
        return str(int(v)) if v != 0 or str(v)[0] != "-" else "-0"
    text = _net_g(v, 7)
    if struct.unpack("<f", struct.pack("<f", float(text)))[0] != v:
        text = _net_g(v, 9)
    return text


def double(value: float) -> str:
    """A double value as the serializer writes it (round-trip format)."""
    if value != value:
        return "NaN"
    if value in (float("inf"), float("-inf")):
        return "INF" if value > 0 else "-INF"
    text = _net_g(value, 15)
    if float(text) != value:
        text = _net_g(value, 17)
    return text


def boolean(value: bool) -> str:
    return "true" if value else "false"
