"""Serialize native parameters in the shared saved-project format."""

from __future__ import annotations

import struct
import uuid
from collections.abc import Mapping
from dataclasses import replace

from .family import FamilyConfig, FamilyEvaluation, Node, from_saved
from .family_codec import FLAGS, OBJECT_TYPES
from .model import EvaluatedChannel, PowerToolConfig, functions
from .nrbf import (
    ArrayInfo,
    ClassInfo,
    Document,
    Record,
    Reference,
    TypeInfo,
    Value,
    read_document,
    write_document,
)
from .project_blob import native_application_keys, read_project_blob

_PREFIX = "BJE.toolB.PT2Logic."


def as_family(config: PowerToolConfig | FamilyConfig) -> FamilyConfig:
    if isinstance(config, FamilyConfig):
        return config
    state = FamilyEvaluation(
        FamilyConfig(
            "TasterBA1f" if config.variant == "01" else "TasterBA2f", database_code=3
        )
    )
    options: dict[str, int] = {}
    values: dict[str, int | float | str | None] = {}
    flags: dict[str, int] = {}
    for channel, native in zip(state.channels, config.channels, strict=True):
        function = functions()[native.function]
        candidates = channel.spec["applications"]
        key = native_application_keys()[native.function]
        options[channel.path] = candidates.index(key)
        evaluated = EvaluatedChannel(native)
        paths: dict[int, str] = {}
        indices: dict[int | None, int] = {}
        for i, field in enumerate(function.fields):
            position = indices.get(field.parent, 0)
            indices[field.parent] = position + 1
            paths[i] = (
                f"{paths[field.parent] if field.parent is not None else channel.path + '/application'}/{position}"
            )
            values[paths[i]] = (
                evaluated.stored_value(i) if field.default is not None else None
            )
        for obj, target in zip(
            function.objects, state.schema["nodes"][key]["objects"], strict=True
        ):
            old_key = f"channel_{len(options) - 1}/{native.function}/{obj.key}"
            if old_key in config.object_flags:
                flags[f"{channel.path}/{key}/{target}"] = config.object_flags[old_key]
    return replace(state.config, options=options, values=values, object_flags=flags)


class _Builder:
    def __init__(self, document: Document) -> None:
        self.document = document
        self.next_id = max((0, *map(abs, document.objects), *document.libraries)) + 1
        self.logic = self.library("PT2Logic")
        self.enums = self.library("PDBEnums")

    def library(self, name: str) -> int:
        for identity, value in self.document.libraries.items():
            if value.split(",", 1)[0] == name:
                return identity
        identity = self.next_id
        self.next_id += 1
        self.document.libraries[identity] = (
            f"{name}, Version=1.2.125.0, Culture=neutral, PublicKeyToken=null"
        )
        return identity

    def add(self, value: Value, *, inline: bool = False) -> Reference:
        identity = -self.next_id if inline else self.next_id
        self.next_id += 1
        self.document.objects[identity] = value
        return Reference(identity)

    def record(
        self,
        name: str,
        members: Mapping[str, Value],
        primitive: Mapping[str, int] | None = None,
        *,
        inline: bool = False,
    ) -> Reference:
        primitive = primitive or {}
        full_name = name if "." in name else _PREFIX + name
        ref = self.add(Record(full_name, dict(members)), inline=inline)
        library = (
            None
            if full_name.startswith("System.")
            else self.enums
            if full_name.startswith("BJE.toolB.PDBEnums.")
            else self.logic
        )
        self.document.classes[ref.id] = ClassInfo(
            full_name,
            tuple(members),
            tuple(
                TypeInfo(0, primitive[k]) if k in primitive else TypeInfo(2)
                for k in members
            ),
            library,
        )
        return ref

    def array(self, name: str, values: list[Value]) -> Reference:
        ref = self.add(values)
        self.document.arrays[ref.id] = ArrayInfo(
            7, TypeInfo(4, _PREFIX + name, self.logic)
        )
        return ref

    def guid(self) -> Reference:
        values = struct.unpack("<ihh8B", uuid.uuid4().bytes_le)
        names = tuple("_" + c for c in "abcdefghijk")
        return self.record(
            "System.Guid",
            dict(zip(names, values, strict=True)),
            dict(zip(names, (8, 7, 7, *([2] * 8)), strict=True)),
            inline=True,
        )

    def enum(self, name: str, value: int, primitive: int = 8) -> Reference:
        return self.record(
            name, {"value__": value}, {"value__": primitive}, inline=True
        )


class _ProjectWriter:
    def __init__(self, source: bytes | None, config: FamilyConfig) -> None:
        self.document = (
            read_document(source) if source else Document(Reference(1), {}, 0)
        )
        self.builder = _Builder(self.document)
        self.state = FamilyEvaluation(config)
        self.old: dict[str, Record] = {}
        self.old_state = (
            FamilyEvaluation(from_saved(source, qualified=True)) if source else None
        )
        if source:
            root = self.document.resolve(self.document.root)
            assert isinstance(root, Record)
            self.root = root
            self.check_internal_addresses(root.members.get("internalAddresses"))
            associations = self.document.resolve(root.members.get("associations"))
            if isinstance(associations, Record):
                internal = self.document.resolve(
                    associations.members.get("internalAddressList")
                )
                if internal is not None and (
                    not isinstance(internal, Record)
                    or internal.members.get("HashSize") != 0
                    or "KeyValuePairs" in internal.members
                ):
                    raise ValueError("Unsupported internal PowerTool associations")
            self.index(root.members["ProjectedDevices"], "devices")
        else:
            self.root = self.new_project()

    def check_internal_addresses(self, value: Value) -> None:
        record = self.document.resolve(value)
        if record is None:
            return
        if not isinstance(record, Record) or record.type_name != _PREFIX + "Addresses":
            raise ValueError("Unsupported internal PowerTool addresses")
        tree = self.resolve_record(record.members["_dictionaryList"])
        entries = self.resolve_record(tree.members["_set"])
        if entries.members.get("Count") != 0 or "Items" in entries.members:
            raise ValueError("Unsupported internal PowerTool addresses")

    def new_project(self) -> Record:
        b = self.builder

        def empty_list(element: str) -> Reference:
            items = b.add([])
            self.document.arrays[items.id] = (
                ArrayInfo(17, TypeInfo(1))
                if element.startswith("System.String,")
                else ArrayInfo(7, TypeInfo(4, _PREFIX + "Nodes", b.logic))
            )
            return b.record(
                f"System.Collections.Generic.List`1[[{element}]]",
                {"_items": items, "_size": 0, "_version": 0},
                {"_size": 8, "_version": 8},
            )

        root = b.record(
            "Project",
            {
                "Version": 4,
                "productDBPath": b.add(self.state.schema["database"]),
                "productDBVersion": b.record(
                    "System.Version",
                    dict(
                        zip(
                            ("_Major", "_Minor", "_Build", "_Revision"),
                            (int(v) for v in self.state.schema["version"].split(".")),
                            strict=True,
                        )
                    ),
                    {k: 8 for k in ("_Major", "_Minor", "_Build", "_Revision")},
                ),
                "ProjectedDevices": None,
                "associations": None,
                "internalAddresses": None,
                "code": self.state.schema["code"],
                "externalFavourites": None,
                "internalFavourites": None,
                "locations": None,
                "cultureName": b.add("en-US"),
                "areaType": b.enum("BJE.toolB.PDBEnums.AreaType", 2),
                "productType": b.add(self.state.config.profile),
                "ID": b.guid(),
                "Upgrade": False,
                "controls": empty_list(
                    "System.String, mscorlib, Version=2.0.0.0, Culture=neutral, PublicKeyToken=b77a5c561934e089"
                ),
                "nodes": empty_list(
                    _PREFIX + "Nodes, " + self.document.libraries[b.logic]
                ),
            },
            {"Version": 14, "code": 8, "Upgrade": 1},
        )
        self.document = replace(self.document, root=root)
        self.builder.document = self.document
        return self.resolve_record(root)

    def resolve_record(self, value: Value) -> Record:
        result = self.document.resolve(value)
        if not isinstance(result, Record):
            raise ValueError("Invalid PowerTool configuration record")
        return result

    def index(self, value: Value, path: str) -> None:
        result = self.document.resolve(value)
        if isinstance(result, list):
            for i, child in enumerate(result):
                self.index(child, f"{path}/{i}")
        elif isinstance(result, Record):
            if result.type_name == _PREFIX + "SubSystem":
                self.index(result.members["attachedDevice"], path)
                return
            self.old[path] = result
            for member, suffix in (
                ("ProjectedDevices", "devices"),
                ("attachedDevice", "attached"),
                ("devices", "devices"),
                ("serializedSubSystems", "subsystems"),
                ("serializedChannels", "channels"),
                ("Application", "application"),
                ("serializedCommunicationObjects", "objects"),
                (
                    "serializedParameters",
                    ""
                    if result.type_name.endswith("Parameter")
                    or result.type_name == _PREFIX + "Application"
                    else "parameters",
                ),
            ):
                if member in result.members:
                    self.index(
                        result.members[member], path + ("/" + suffix if suffix else "")
                    )

    def previous(self, node: Node) -> Record | None:
        path = node.path
        if node.channel is not None:
            identity = self.state.config.channel_ids.get(
                node.channel.path, node.channel.path
            )
            path = identity + path[len(node.channel.path) :]
        old_node = self.old_state.nodes.get(path) if self.old_state else None
        return (
            self.old.get(path)
            if old_node is not None and old_node.key == node.key
            else None
        )

    def parameters(self, node: Node) -> Reference:
        return self.builder.array(
            "Parameter", [self.parameter(p) for p in node.parameters]
        )

    def parameter(self, node: Node) -> Reference:
        kind = node.spec["value_kind"]
        name = {
            "V_ENUM": "EnumParameter",
            "V_SYMBOL": "SymbolParameter",
            "V_SIGNED": "SignedParameter",
            "V_UNSIGNED": "UnSignedParameter",
            "V_FLOAT": "FloatParameter",
            "V_FREETIMER": "FreeTimerParameter",
            "V_STRING": "StringParameter",
            "V_STRINGPTR": "StringParameter",
            "V_TIME": "TimeParameter",
        }.get(kind, "SimpleTimeParameter" if kind.startswith("P_") else "Parameter")
        old = self.previous(node)
        value = self.state.stored(node)
        primitive = {"Version": 14}
        if value is not None and not isinstance(value, str):
            primitive["ParameterValue"] = (
                11
                if kind == "V_FLOAT"
                else 8
                if kind in ("V_ENUM", "V_SYMBOL", "V_FREETIMER")
                else 16
            )
        return self.builder.record(
            name,
            {
                "Version": 1,
                "ParameterValue": self.builder.add(value)
                if isinstance(value, str)
                else value,
                "serializedParameters": self.parameters(node),
                "ID": old.members["ID"] if old else self.builder.guid(),
            },
            primitive,
        )

    def application(self, node: Node) -> Reference:
        objects: list[Value] = []
        for obj in node.objects:
            assert node.channel is not None
            key = f"{self.state.config.channel_ids.get(node.channel.path, node.channel.path)}/{node.key}/{obj.key}"
            flags = self.state.config.object_flags.get(
                key,
                obj.spec["priority"]
                | sum(
                    bit for name, bit in FLAGS.items() if self.state.property(obj, name)
                ),
            )
            old = self.previous(obj)
            size = OBJECT_TYPES[str(self.state.property(obj, "Type"))][1]
            members: dict[str, Value] = {
                "Version": 0,
                "ID": old.members["ID"] if old else self.builder.guid(),
            }
            members.update(
                (name[0].lower() + name[1:], bool(flags & bit))
                for name, bit in FLAGS.items()
            )
            members.update(
                priority=flags & 3,
                lastObjectSizeInitialized=True,
                lastObjectSize=self.builder.enum(
                    "BJE.toolB.PDBEnums.ObjectSize", size, 15
                ),
            )
            objects.append(
                self.builder.record(
                    "CommunicationObject",
                    members,
                    {
                        "Version": 14,
                        "priority": 2,
                        "lastObjectSizeInitialized": 1,
                        **{name[0].lower() + name[1:]: 1 for name in FLAGS},
                    },
                )
            )
        old = self.previous(node)
        return self.builder.record(
            "Application",
            {
                "Version": 0,
                "serializedParameters": self.parameters(node),
                "serializedCommunicationObjects": self.builder.array(
                    "CommunicationObject", objects
                ),
                "serializedInternalCommunicationObjects": None,
                "viewType": old.members["viewType"]
                if old
                else self.builder.enum(_PREFIX + "Item+UIViewType", 1),
            },
            {"Version": 14},
        )

    def channel(self, node: Node) -> Reference:
        assert node.application is not None
        old = self.previous(node)
        return self.builder.record(
            "Channel",
            {
                "Version": 0,
                "Application": self.application(node.application),
                "ApplicationOptionIndex": self.state.config.options.get(node.path, 0),
                "serializedChannels": self.builder.array(
                    "Channel", [self.channel(c) for c in node.channels]
                ),
                "name": old.members["name"] if old else None,
                "serializedParameters": self.parameters(node),
            },
            {"Version": 14, "ApplicationOptionIndex": 14},
        )

    def device(self, node: Node) -> Reference:
        attached = self.state.nodes.get(node.path + "/attached")
        subsystems: list[Value] = []
        for i, choices in enumerate(node.spec["subsystems"]):
            child = self.state.nodes[f"{node.path}/subsystems/{i}"]
            subsystems.append(
                self.builder.record(
                    "SubSystem",
                    {
                        "Version": 0,
                        "attachedDevice": self.device(child),
                        "serializedProductOptionIndex": choices.index(child.key),
                    },
                    {"Version": 14, "serializedProductOptionIndex": 14},
                )
            )
        old = self.previous(node)
        disable = (
            old.members["disable"]
            if old
            and self.old_state is not None
            and self.old_state.nodes[node.path].spec.get("disabled", False)
            == node.spec.get("disabled", False)
            else None
        )
        if node.spec.get("disabled") and disable is None:
            ref = self.builder.add([True])
            self.document.arrays[ref.id] = ArrayInfo(15, TypeInfo(0, 1))
            disable = self.builder.record(
                "System.Collections.Generic.List`1[[System.Boolean, mscorlib, Version=2.0.0.0, Culture=neutral, PublicKeyToken=b77a5c561934e089]]",
                {"_items": ref, "_size": 1, "_version": 1},
                {"_size": 8, "_version": 8},
            )
        return self.builder.record(
            "Device",
            {
                "Version": 1,
                "address": node.spec["address"],
                "attachedDevice": self.device(attached) if attached else None,
                "serializedProductOptionIndex": node.spec["children"].index(
                    attached.key
                )
                if attached
                else 0,
                "serializedSubSystems": self.builder.array("SubSystem", subsystems),
                "serializedChannels": self.builder.array(
                    "Channel", [self.channel(c) for c in node.channels]
                ),
                "serializedParameters": self.parameters(node),
                "disable": disable,
                "devices": self.builder.array(
                    "Device",
                    [
                        self.device(self.state.nodes[f"{node.path}/devices/{i}"])
                        for i in range(len(node.spec["choices"]))
                    ],
                ),
            },
            {"Version": 14, "address": 2, "serializedProductOptionIndex": 14},
        )

    def write(self) -> bytes:
        self.root.members["ProjectedDevices"] = self.builder.array(
            "Device", [self.device(d) for d in self.state.devices if d.parent is None]
        )
        return write_document(self.document)


def write_project_blob(
    source: bytes | None, config: PowerToolConfig | FamilyConfig
) -> bytes:
    """Write native settings, retaining metadata from the supplied source.

    Channel identities refer to that source. Rebase the configuration when replacing
    the source with this output so subsequent edits retain shifted channel metadata.
    """
    saved = read_project_blob(source) if source else None
    family = as_family(config)
    state = FamilyEvaluation(family)
    if saved and (saved.product_type, saved.code) != (
        family.profile,
        state.schema["code"],
    ):
        raise ValueError("PowerTool source blob does not match native configuration")
    if saved and any(c.internal_objects for c in saved.channels):
        raise ValueError("Unsupported internal PowerTool objects")
    result = _ProjectWriter(source, family).write()
    from_saved(result, qualified=True)
    return result
