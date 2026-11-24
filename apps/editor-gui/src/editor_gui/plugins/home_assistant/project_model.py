"""The open project as Home Assistant's KNX integration reads a project: xknxproject's
``KNXProject``.

Home Assistant's KNX integration takes the project it decodes telegrams with, offers group
addresses from and suggests entities by from a ``KNXProject`` dictionary. It is built here from
what the editor holds, the way xknxproject builds it from a project file, so the integration
sees the same as with an uploaded project file, and without writing and parsing a file:

- the rows of the project (:meth:`ProjectService.model_source`), the trades included,
- each application program's group objects, channels, parameter blocks, module arguments and
  allocators (:mod:`.application_digest`, read once per program),
- the group objects' numbers as the editor resolves them (module base numbers included),
- the devices' group object trees for their channels and folders as the ``.knxproj`` export
  writes them (the stored ones, or those evaluated from the parameters where the editor
  activated objects), completed with the linked objects they do not list by the channel
  stored for each object, and the module arguments the export writes.

Where xknxproject reads an attribute that is missing as None, so does this; the text rules
(module arguments, text parameters, the order of the fallbacks) are xknxproject's own functions.

A build takes a fraction of a second once every program of the project has been read; reading
them is what takes long the first time, so :func:`prepare_project_model` does it on a worker
thread first.
"""

from __future__ import annotations

import html
import re
import xml.etree.ElementTree as ET
from collections import Counter, defaultdict
from collections.abc import Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, cast

import xknxproject.__version__ as _xknxproject_version
from striprtf.striprtf import (  # pyright: ignore[reportMissingTypeStubs]
    rtf_to_text,  # pyright: ignore[reportUnknownVariableType]
)
from xknxproject.combination import combine_project
from xknxproject.models.knxproject import (
    Area,
    Channel,
    CommunicationObject,
    Device,
    DPTType,
    Flags,
    Function,
    GroupAddress,
    GroupAddressRef,
    GroupObjectTreeNode,
    GroupRange,
    KNXProject,
    Line,
    ModuleInstanceInfos,
    ProjectInfo,
    Space,
    TextParameter,
    Trade,
)
from xknxproject.models.models import ParameterInstanceRef, XMLGroupAddress
from xknxproject.models.static import MEDIUM_TYPES, GroupAddressStyle
from xknxproject.util import (
    module_argument_replace,
    parse_dpt_types,
    strip_module_instance,
    text_parameter_insert_module_instance,
    text_parameter_template_replace,
)

from editor_gui.plugins.home_assistant.application_digest import (
    FLAG_NAMES,
    ApplicationDigest,
    ArgumentDef,
    ObjectDef,
    application_digest,
)
from xknxeditor.proj.core.knxproj_export import relidref

if TYPE_CHECKING:
    from editor_gui.plugins.project.service import ProjectService
    from xknxeditor.proj.core.service import ModelComObject as ComObjectRow
    from xknxeditor.proj.core.service import ModelSource
    from xknxeditor.proj.models import Device as DeviceRow
    from xknxeditor.proj.models import GroupRange as GroupRangeRow
    from xknxeditor.proj.models import Space as SpaceRow
    from xknxeditor.proj.models import Trade as TradeRow

CREATED_BY = "XKNX Editor"
_SUBMODULE = re.compile(r"(_SM-[^_]+)")


class ProjectModelUnavailable(Exception):
    """No project is open, or a background import holds it."""


def build_project_model(
    project: ProjectService, *, tool_version: str, language_code: str | None = None
) -> KNXProject:
    """The open project as xknxproject's ``KNXProject``.

    ``tool_version`` names the program in ``info`` (e.g. "XKNX Editor 0.9.12"),
    ``language_code`` the language of its texts (the product language). Raises
    :class:`ProjectModelUnavailable` without an open project.

    The devices whose group objects the editor changed get the group object tree and module
    arguments the ``.knxproj`` export writes for them, so the model equals what xknxproject
    reads from the editor's own export. Call it where the project is used (the GUI thread, or
    an MCP tool holding it).
    """
    digests: dict[int, ApplicationDigest] = {}
    numbers: dict[int, int] = {}
    for device in project.devices:
        if device.app is None:
            continue
        digests[device.node_id] = application_digest(device.app)
        for co in device.get_visible_com_objects():
            if co.db_id is not None:
                numbers[co.db_id] = co.number
    inputs = project.model_source_as_exported()
    if inputs is None:
        raise ProjectModelUnavailable("no project is open")
    source, trees, module_arguments = inputs
    return _Builder(source, digests, numbers, trees, module_arguments).build(
        tool_version=tool_version, language_code=language_code
    )


def prepare_project_model(project: ProjectService) -> None:
    """Read what :func:`build_project_model` takes from the application programs of the open
    project's devices, so the build itself is quick. Loads the programs that are not in memory,
    which takes seconds for a large project: call it on a worker thread (it does not read the
    project itself, only its devices' programs)."""
    for app in {
        id(d.app): d.app for d in project.devices if d.app is not None
    }.values():
        application_digest(app)


def _plain_text(rtf: str) -> str:
    """A stored comment as plain text, as xknxproject gives it."""
    if not rtf:
        return ""
    return html.unescape(cast(str, rtf_to_text(rtf)))


def _local_id(source_id: str) -> str:
    """An id without its project prefix (``P-04BB-0_GA-7`` -> ``GA-7``), as xknxproject keeps the
    ids of group addresses and functions."""
    return source_id.split("_", 1)[1] if "_" in source_id else source_id


def _ia_key(ia: str) -> tuple[str, ...]:
    """xknxproject's order of the devices of a space or trade (by the address's parts as text)."""
    return tuple(ia.split("."))


def _range_key(start: int, end: int, style: GroupAddressStyle) -> str:
    """The key of a group range (xknxproject ``XMLGroupRange.str_address``)."""
    if style is GroupAddressStyle.FREE:
        return f"{start}...{end}"
    first = XMLGroupAddress.str_address(start, style).split("/")
    if style is GroupAddressStyle.TWOLEVEL:
        return first[0]
    if end - start >= 2046:
        return first[0]
    return "/".join(first[0:2])


@dataclass
class _DeviceContext:
    """A device's address and what its texts are resolved with."""

    row: DeviceRow
    ia: str
    application: str | None
    digest: ApplicationDigest | None
    parameters: dict[str, str]
    # module instance id -> [(argument ref id, value)], ref ids completed with the application
    module_arguments: list[tuple[str, list[tuple[str, str]]]] = field(
        default_factory=list[tuple[str, list[tuple[str, str]]]]
    )

    def arguments_for(self, instance_ref_id: str) -> dict[str, str]:
        """The module argument values by name for an object or channel instance
        (xknxproject ``DeviceInstance.module_arguments``)."""
        if not instance_ref_id.startswith("MD-") or self.digest is None:
            return {}
        values: dict[str, str | None] = {}
        # The base module first, its submodules overriding it.
        containing = sorted(
            (
                (instance_id, arguments)
                for instance_id, arguments in self.module_arguments
                if instance_ref_id.startswith(f"{instance_id}_")
            ),
            key=lambda item: len(item[0]),
        )
        for instance_id, arguments in containing:
            for ref_id, value in arguments:
                argument = self.digest.arguments.get(ref_id)
                if argument is None:
                    continue
                values[argument.name] = (
                    self._allocated(instance_id, argument, value)
                    if argument.computed
                    else value
                )
        return {name: value for name, value in values.items() if value is not None}

    def _allocated(
        self, instance_id: str, argument: ArgumentDef, value: str
    ) -> str | None:
        """The value an allocator gives a computed argument of a module instance (xknxproject
        ``_allocated_argument_value``): the stored number, else the allocator's ``Start`` plus
        ``Allocates`` per instance before it, when only one module draws from the allocator.
        None (the placeholder stays) for an argument of a submodule or of a base value."""
        assert self.digest is not None
        if _SUBMODULE.search(instance_id) is not None or argument.base_value:
            return None
        if value.isdigit():
            return value
        allocator = f"{self.application}_{value}"
        start = self.digest.allocators.get(allocator)
        if (
            not argument.allocator
            or argument.allocates is None
            or self.digest.allocator_modules.get(allocator) != 1
            or start is None
        ):
            return None
        index = instance_id.rsplit("_MI-", 1)[-1] if "_MI-" in instance_id else ""
        if not index.isdigit():
            return None
        return str(start + argument.allocates * (int(index) - 1))

    def parameter(self, ref_id: str) -> ParameterInstanceRef | None:
        value = self.parameters.get(ref_id)
        return (
            None if value is None else ParameterInstanceRef(ref_id=ref_id, value=value)
        )


class _Builder:
    def __init__(
        self,
        source: ModelSource,
        digests: Mapping[int, ApplicationDigest],
        numbers: Mapping[int, int],
        trees: Mapping[int, str] | None = None,
        module_arguments: Mapping[int, Mapping[str, Sequence[tuple[str, str]]]]
        | None = None,
    ) -> None:
        """``trees`` and ``module_arguments`` (by device id) are the evaluated group object
        trees and module instance arguments the export writes for a device instead of what it
        stored (see ``ProjectService.model_source_as_exported``)."""
        self.source = source
        self.digests = digests
        self.numbers = numbers
        self.trees = trees or {}
        self.module_arguments = module_arguments or {}
        self.com_objects: dict[int, list[ComObjectRow]] = defaultdict(list)
        for co in source.com_objects:
            self.com_objects[co.device_id].append(co)
        self.style = GroupAddressStyle(source.project.group_address_style)
        self.ga_address: dict[int, str] = {
            ga.id: XMLGroupAddress.str_address(ga.address, self.style)
            for ga in source.group_addresses
        }
        self.space_identifier: dict[int, str] = {
            space.id: space.source_id or f"SP-x{space.id}" for space in source.spaces
        }

    def build(self, *, tool_version: str, language_code: str | None) -> KNXProject:
        contexts = self._device_contexts()
        trades, trade_ids = self._trades(contexts)
        communication_objects: dict[str, CommunicationObject] = {}
        devices: dict[str, Device] = {}
        for context in contexts:
            tree = self._object_tree(context)
            device_objects: list[str] = []
            for co in self.com_objects[context.row.id]:
                links = self._links(co)
                if not links:
                    continue
                instance = self._instance_ref_id(context, co)
                key = f"{context.ia}/{instance}"
                communication_objects[key] = self._com_object(
                    context,
                    co,
                    links,
                    co.channel_id or tree.object_channels.get(instance),
                )
                device_objects.append(key)
            devices[context.ia] = self._device(
                context, device_objects, tree, trade_ids.get(context.row.id)
            )

        objects_by_address: dict[str, list[str]] = defaultdict(list)
        for key, com_object in communication_objects.items():
            for address in dict.fromkeys(com_object["group_address_links"]):
                objects_by_address[address].append(key)

        # As xknxproject's parse does by default: DPTs inferred from object sizes and links.
        return combine_project(
            KNXProject(
                info=self._info(tool_version, language_code),
                communication_objects=communication_objects,
                topology=self._topology(contexts),
                devices=devices,
                group_addresses=self._group_addresses(objects_by_address),
                group_ranges=self._group_ranges(),
                locations=self._locations(contexts),
                trades=trades,
                functions=self._functions(),
            )
        )

    # --- project ---------------------------------------------------------------------------

    def _info(self, tool_version: str, language_code: str | None) -> ProjectInfo:
        project = self.source.project
        return ProjectInfo(
            project_id=project.original_project_id or project.id,
            name=project.name,
            last_modified=project.last_modified or None,
            group_address_style=self.style.value,
            guid=project.guid,
            created_by=CREATED_BY,
            schema_version=project.schema_version,
            tool_version=tool_version,
            xknxproject_version=_xknxproject_version.__version__,
            language_code=language_code,
        )

    # --- devices -----------------------------------------------------------------------------

    def _device_contexts(self) -> list[_DeviceContext]:
        """The devices xknxproject reads: those with an address on a line, in its order."""
        segments = {segment.id: segment for segment in self.source.segments}
        lines = {line.id: line for line in self.source.lines}
        areas = {area.id: area for area in self.source.areas}
        placed: list[tuple[tuple[int, int, int, int], _DeviceContext]] = []
        for row in self.source.devices:
            segment = (
                segments.get(row.segment_id) if row.segment_id is not None else None
            )
            if segment is None or row.address is None:
                continue
            line = lines[segment.line_id]
            area = areas[line.area_id]
            application = row.application_program_ref_id or None
            digest = self.digests.get(row.id)
            context = _DeviceContext(
                row=row,
                ia=f"{area.address}.{line.address}.{row.address}",
                application=application,
                digest=digest,
                parameters=self.source.parameters.get(row.id, {}),
                module_arguments=self._module_instances(row, application),
            )
            placed.append(((area.address, line.address, row.address, row.id), context))
        placed.sort(key=lambda item: item[0])
        return [context for _key, context in placed]

    def _module_instances(
        self, row: DeviceRow, application: str | None
    ) -> list[tuple[str, list[tuple[str, str]]]]:
        """The device's module instances with their arguments as the export writes them: the
        stored ones, those without stored arguments (a device added in the editor) with the
        evaluated ones, and the evaluated instances not stored."""
        evaluated = {
            relidref(instance_id): list(arguments)
            for instance_id, arguments in self.module_arguments.get(row.id, {}).items()
        }
        instances: dict[str, tuple[str, list[tuple[str, str]]]] = {}
        for instance in row.module_instances:
            relative = relidref(instance.instance_id)
            stored = [
                (arg["ref_id"], arg["value"])
                for arg in instance.arguments
                if "ref_id" in arg and "value" in arg
            ]
            instances.setdefault(
                relative,
                (instance.ref_id, stored or evaluated.get(relative, [])),
            )
        for relative, arguments in evaluated.items():
            instances.setdefault(
                relative, (re.sub(r"_MI-.*$", "", relative), arguments)
            )
        return [
            (
                relative,
                [
                    (self._argument_ref_id(application, module_ref, ref_id), value)
                    for ref_id, value in arguments
                ],
            )
            for relative, (module_ref, arguments) in instances.items()
        ]

    @staticmethod
    def _argument_ref_id(application: str | None, module_ref: str, ref_id: str) -> str:
        """A module argument's id completed with the application (xknxproject
        ``ModuleInstanceArgument.complete_ref_id``)."""
        if ref_id.startswith(f"{application}_"):
            return ref_id
        if ref_id.startswith("SM-"):
            return f"{application}_{module_ref.split('_', 1)[0]}_{ref_id}"
        return f"{application}_{ref_id}"

    def _device(
        self,
        context: _DeviceContext,
        objects: list[str],
        tree: _ObjectTree,
        trade_id: str | None,
    ) -> Device:
        row = context.row
        linked = set(objects)
        group_object_tree = self._tree_nodes(context, tree.nodes, linked)
        # A channel is exported if one of its objects is, or one of a channel below it (as a
        # node of the tree then).
        exported = set(_channel_ids(group_object_tree))
        channels: dict[str, Channel] = {}
        for node in tree.channels:
            if node.ref_id not in exported:
                continue
            name, blocks = self._channel_name(context, node.ref_id, node.text)
            channels[node.ref_id] = Channel(
                identifier=node.ref_id,
                name=name,
                communication_object_ids=[
                    key
                    for instance in (*node.objects, *_folder_objects(node.nodes))
                    if (key := f"{context.ia}/{instance}") in linked
                ],
                functional_blocks=list(blocks) if blocks is not None else None,
            )
        product = row.product_ref_id or ""
        return Device(
            name=row.name or row.product_name,
            hardware_name=row.product_name,
            order_number=row.order_number,
            description=row.description,
            manufacturer_name=row.manufacturer_name,
            individual_address=context.ia,
            application=context.application,
            project_uid=row.puid,
            product_id=product,
            hardware_id=product.split("_P-", 1)[0] if "_P-" in product else "",
            hardware_program_id=row.hardware2program_ref_id or "",
            space_id=(
                self.space_identifier.get(row.space_id)
                if row.space_id is not None
                else None
            ),
            trade_id=trade_id,
            communication_object_ids=objects,
            channels=channels,
            group_object_tree=group_object_tree,
            serial_number=row.serial_number,
            last_download=row.last_download,
            individual_address_loaded=row.individual_address_loaded,
            application_program_loaded=row.application_program_loaded,
            communication_part_loaded=row.communication_part_loaded,
            medium_config_loaded=row.medium_config_loaded,
            parameters_loaded=row.parameters_loaded,
        )

    def _object_tree(self, context: _DeviceContext) -> _ObjectTree:
        """The device's group object tree as the export writes it (the evaluated one when the
        stored one does not list all its objects, else the stored one), completed with the
        linked objects it does not list by the channel the editor stored for them: in that
        channel's node, else in a channel node of their own after the others."""
        nodes, listed = _parse_tree(
            self.trees.get(context.row.id) or context.row.group_object_tree
        )
        for co in self.com_objects[context.row.id]:
            instance = self._instance_ref_id(context, co)
            if not co.channel_id or instance in listed:
                continue
            node = next(
                (
                    n
                    for n in _iter_nodes(nodes)
                    if n.is_channel and n.ref_id == co.channel_id
                ),
                None,
            )
            if node is None:
                node = _TreeNode("Channel", co.channel_id, "", [], [])
                nodes.append(node)
            node.objects.append(instance)
            listed.add(instance)
        used = _used_nodes(nodes)
        channels: list[_TreeNode] = []
        _collect_channels(used, channels)
        return _ObjectTree(
            nodes=used, channels=channels, object_channels=_object_channels(used)
        )

    def _tree_nodes(
        self, context: _DeviceContext, nodes: list[_TreeNode], linked: set[str]
    ) -> list[GroupObjectTreeNode]:
        """The nodes as ``Device.group_object_tree`` holds them (xknxproject
        ``_transform_group_object_tree``): only the linked objects, a node without any in or below
        it left out, the names resolved."""
        result: list[GroupObjectTreeNode] = []
        for node in nodes:
            objects = [
                key
                for instance in node.objects
                if (key := f"{context.ia}/{instance}") in linked
            ]
            children = self._tree_nodes(context, node.nodes, linked)
            if not objects and not children:
                continue
            name = (
                self._channel_name(context, node.ref_id, node.text)[0]
                if node.is_channel
                else self._folder_name(context, node.ref_id, node.text)
            )
            result.append(
                GroupObjectTreeNode(
                    type="Channel" if node.is_channel else "Folder",
                    identifier=node.ref_id,
                    name=name,
                    communication_object_ids=objects,
                    nodes=children,
                )
            )
        return result

    @staticmethod
    def _folder_name(context: _DeviceContext, ref_id: str, name: str) -> str:
        """A folder's name (xknxproject ``FolderNode.resolve_folder_attributes``): its own text,
        else that of its parameter block, with the module arguments and the text parameter."""
        if context.digest is None:
            return name
        block = context.digest.blocks.get(
            f"{context.application}_{strip_module_instance(ref_id, search_id='PB')}"
        )
        if not name and block is not None:
            name = block.text or block.name or ""
        if "{{" in name:
            name = module_argument_replace(name, context.arguments_for(ref_id))
            parameter: ParameterInstanceRef | None = None
            if block is not None and block.text_parameter_ref_id:
                parameter = context.parameter(
                    text_parameter_insert_module_instance(
                        instance_ref=ref_id,
                        instance_next_id="PB",
                        text_parameter_ref_id=block.text_parameter_ref_id,
                    )
                )
            name = text_parameter_template_replace(name, parameter)
        if not name.strip() and block is not None:
            name = block.name or ""
        return name

    def _channel_name(
        self, context: _DeviceContext, ref_id: str, name: str
    ) -> tuple[str, tuple[str, ...] | None]:
        """A channel's name and functional blocks (xknxproject
        ``ChannelNode.resolve_channel_attributes``)."""
        arguments = context.arguments_for(ref_id)
        blocks: tuple[str, ...] | None = None
        channel = (
            context.digest.channels.get(
                f"{context.application}_{strip_module_instance(ref_id, search_id='CH')}"
            )
            if context.digest is not None
            else None
        )
        if channel is not None:
            if not name:
                if channel.text and channel.text_parameter_ref_id:
                    parameter = context.parameter(
                        text_parameter_insert_module_instance(
                            instance_ref=ref_id,
                            instance_next_id="CH",
                            text_parameter_ref_id=channel.text_parameter_ref_id,
                        )
                    )
                    name = (
                        text_parameter_template_replace(
                            module_argument_replace(channel.text, arguments), parameter
                        )
                        or channel.name
                    )
                else:
                    name = channel.text or channel.name
            blocks = channel.functional_blocks
        return module_argument_replace(name, arguments), blocks

    # --- group objects -----------------------------------------------------------------------

    def _links(self, co: ComObjectRow) -> list[str]:
        """The addresses of the object's links that exist, the sending one first."""
        links = sorted(co.links, key=lambda link: (not link.is_sending, link.id))
        return [
            address
            for link in links
            if (address := self.ga_address.get(link.group_address_id)) is not None
        ]

    @staticmethod
    def _instance_ref_id(context: _DeviceContext, co: ComObjectRow) -> str:
        if co.instance_ref_id:
            return co.instance_ref_id
        return co.ref_id.removeprefix(f"{context.application}_")

    def _com_object(
        self,
        context: _DeviceContext,
        co: ComObjectRow,
        links: list[str],
        channel: str | None,
    ) -> CommunicationObject:
        """The object (xknxproject ``CommunicationObject``); ``channel`` is the one the editor
        stored for it, else the one above it in the device's group object tree."""
        instance = self._instance_ref_id(context, co)
        arguments = context.arguments_for(instance)
        ref: ObjectDef | None = None
        obj: ObjectDef | None = None
        if context.digest is not None:
            ref = context.digest.refs.get(
                f"{context.application}_{strip_module_instance(instance, search_id='O')}"
            )
            if ref is not None and ref.ref_id is not None:
                obj = context.digest.objects.get(ref.ref_id)

        def replace(text: str | None) -> str | None:
            return (
                module_argument_replace(text, arguments) if text is not None else None
            )

        parents = [parent for parent in (ref, obj) if parent is not None]
        name = next((replace(p.name) for p in parents if p.name is not None), None)
        text = replace(co.text_override)
        if text is None and ref is not None:
            text = self._ref_text(context, instance, ref, arguments)
            if not text:
                text = replace(ref.text)
        if text is None and obj is not None:
            text = replace(obj.text)
        function_text = replace(co.function_text_override)
        if function_text is None:
            function_text = next(
                (
                    replace(p.function_text)
                    for p in parents
                    if p.function_text is not None
                ),
                None,
            )
        object_size = next(
            (p.object_size for p in parents if p.object_size is not None), None
        )
        flags = self._flags(co, parents)
        dpts = parse_dpt_types(co.datapoint_type_override)
        for parent in parents:
            if dpts:
                break
            dpts = parse_dpt_types(parent.datapoint_types)
        number = obj.number if obj is not None else None
        module: ModuleInstanceInfos | None = None
        if (
            obj is not None
            and obj.base_number is not None
            and instance.startswith("MD-")
            and obj.number is not None
        ):
            module = ModuleInstanceInfos(
                definition=self._module_definition(context, instance),
                root_number=obj.number,
            )
        number = self.numbers.get(co.id, number)
        text_parameter: TextParameter | None = None
        if ref is not None and ref.text_parameter_ref_id:
            parameter_ref = text_parameter_insert_module_instance(
                instance_ref=instance,
                instance_next_id="O",
                text_parameter_ref_id=ref.text_parameter_ref_id,
            )
            text_parameter = TextParameter(
                identifier=parameter_ref.removeprefix(f"{context.application}_"),
                value=context.parameters.get(parameter_ref) or None,
            )
        return CommunicationObject(
            name=cast(str, name),
            number=cast(int, number),
            text=cast(str, text),
            function_text=cast(str, function_text),
            description=co.description_override or "",
            device_address=context.ia,
            device_application=context.application,
            module_def=module,
            channel=channel,
            dpts=dpts,
            object_size=cast(str, object_size),
            group_address_links=links,
            flags=flags,
            dpas=list(ref.dpas) if ref is not None and ref.dpas is not None else None,
            text_parameter=text_parameter,
        )

    @staticmethod
    def _ref_text(
        context: _DeviceContext,
        instance: str,
        ref: ObjectDef,
        arguments: dict[str, str],
    ) -> str | None:
        """A reference's text with its text parameter (xknxproject
        ``ComObjectRef.com_object_ref_text_with_paramter``)."""
        if not ref.text_parameter_ref_id or not ref.text:
            return None
        parameter = context.parameter(
            text_parameter_insert_module_instance(
                instance_ref=instance,
                instance_next_id="O",
                text_parameter_ref_id=ref.text_parameter_ref_id,
            )
        )
        return text_parameter_template_replace(
            module_argument_replace(ref.text, arguments), parameter=parameter
        )

    @staticmethod
    def _flags(co: ComObjectRow, parents: Iterable[ObjectDef]) -> Flags:
        """The object's own flags, else those of its reference and its object."""
        values: dict[str, Any] = {}
        parent_list = list(parents)
        for index, name in enumerate(FLAG_NAMES):
            value: bool | None = getattr(co, f"{name}_flag")
            for parent in parent_list:
                if value is not None:
                    break
                value = parent.flags[index]
            values[name] = value
        return cast(Flags, values)

    @staticmethod
    def _module_definition(context: _DeviceContext, instance: str) -> str:
        """The module definition of an object instance (xknxproject
        ``ModuleInstance.definition_id``): ``MD-1``, or ``MD-1_SM-2`` in a submodule."""
        # The innermost instance: a submodule instance's id starts with that of the instance
        # it is in.
        identifier = max(
            (
                instance_id
                for instance_id, _args in context.module_arguments
                if instance.startswith(f"{instance_id}_")
            ),
            key=len,
            default=instance.split("_O-", 1)[0],
        )
        definition = identifier.split("_", 1)[0]
        if (match := _SUBMODULE.search(identifier)) is not None:
            return f"{definition}{match.group()}"
        return definition

    # --- topology ----------------------------------------------------------------------------

    def _topology(self, contexts: list[_DeviceContext]) -> dict[str, Area]:
        devices_by_line: dict[int, list[tuple[int, str]]] = defaultdict(list)
        segment_line = {segment.id: segment.line_id for segment in self.source.segments}
        for context in contexts:
            assert (
                context.row.segment_id is not None and context.row.address is not None
            )
            devices_by_line[segment_line[context.row.segment_id]].append(
                (context.row.address, context.ia)
            )
        first_segment: dict[int, str] = {}
        for segment in sorted(self.source.segments, key=lambda s: (s.number, s.id)):
            first_segment.setdefault(segment.line_id, segment.medium_type)
        lines_by_area: dict[int, list[Any]] = defaultdict(list)
        for line in self.source.lines:
            lines_by_area[line.area_id].append(line)
        topology: dict[str, Area] = {}
        for area in sorted(self.source.areas, key=lambda a: a.address):
            lines: dict[str, Line] = {}
            for line in sorted(lines_by_area[area.id], key=lambda li: li.address):
                lines[str(line.address)] = Line(
                    name=line.name,
                    description=line.description or None,
                    devices=[ia for _address, ia in sorted(devices_by_line[line.id])],
                    medium_type=MEDIUM_TYPES.get(
                        first_segment.get(line.id, ""), "Unknown"
                    ),
                )
            topology[str(area.address)] = Area(
                name=area.name, description=area.description or None, lines=lines
            )
        return topology

    # --- group addresses ---------------------------------------------------------------------

    def _group_addresses(
        self, objects_by_address: Mapping[str, list[str]]
    ) -> dict[str, GroupAddress]:
        result: dict[str, GroupAddress] = {}
        for ga in sorted(self.source.group_addresses, key=lambda g: (g.address, g.id)):
            address = self.ga_address[ga.id]
            dpts = parse_dpt_types(ga.datapoint_type)
            result[address] = GroupAddress(
                name=ga.name,
                identifier=_local_id(ga.source_id) if ga.source_id else f"GA-x{ga.id}",
                raw_address=ga.address,
                address=address,
                project_uid=ga.puid,
                dpt=DPTType(main=dpts[0]["main"], sub=dpts[0]["sub"]) if dpts else None,
                data_secure=ga.id in self.source.keyed,
                communication_object_ids=list(objects_by_address.get(address, ())),
                description=ga.description,
                comment=_plain_text(ga.comment),
            )
        return result

    def _group_ranges(self) -> dict[str, GroupRange]:
        children: dict[int | None, list[GroupRangeRow]] = defaultdict(list)
        for group_range in self.source.group_ranges:
            children[group_range.parent_id].append(group_range)
        # In document order, which follows the Puids of a project file; added ones last.
        addresses: dict[int, list[int]] = defaultdict(list)
        for ga in sorted(
            self.source.group_addresses,
            key=lambda g: (g.puid is None, g.puid or 0, g.id),
        ):
            addresses[ga.group_range_id].append(ga.address)

        def convert(parent: int | None) -> dict[str, GroupRange]:
            return {
                _range_key(row.range_start, row.range_end, self.style): GroupRange(
                    name=row.name,
                    address_start=row.range_start,
                    address_end=row.range_end,
                    group_addresses=[
                        XMLGroupAddress.str_address(address, self.style)
                        for address in addresses[row.id]
                    ],
                    comment=_plain_text(row.comment),
                    group_ranges=convert(row.id),
                )
                for row in sorted(children[parent], key=lambda r: (r.range_start, r.id))
            }

        return convert(None)

    # --- buildings ---------------------------------------------------------------------------

    def _locations(self, contexts: list[_DeviceContext]) -> dict[str, Space]:
        devices_by_space: dict[int, list[str]] = defaultdict(list)
        for context in contexts:
            if context.row.space_id is not None:
                devices_by_space[context.row.space_id].append(context.ia)
        functions_by_space: dict[int, list[str]] = defaultdict(list)
        for function in sorted(self.source.functions, key=lambda f: (f.order, f.id)):
            functions_by_space[function.space_id].append(
                self._function_identifier(function)
            )
        children: dict[int | None, list[SpaceRow]] = defaultdict(list)
        for space in self.source.spaces:
            children[space.parent_id].append(space)

        def convert(parent: int | None) -> dict[str, Space]:
            spaces = sorted(children[parent], key=lambda s: (s.order, s.id))
            numbers = Counter((s.name, s.number) for s in spaces if s.number)
            result: dict[str, Space] = {}
            for space in spaces:
                identifier = self.space_identifier[space.id]
                key = space.name
                if key in result:
                    if space.number and numbers[(space.name, space.number)] == 1:
                        key = f"{space.name} ({space.number})"
                    else:
                        key = f"{space.name} ({identifier})"
                    while key in result:
                        key = f"{key} ({identifier})"
                result[key] = Space(
                    type=space.space_type,
                    identifier=identifier,
                    name=space.name,
                    usage_id=space.usage or None,
                    usage_text=space.usage_text,
                    number=space.number,
                    description=space.description,
                    project_uid=space.puid,
                    devices=sorted(devices_by_space[space.id], key=_ia_key),
                    spaces=convert(space.id),
                    functions=functions_by_space[space.id],
                )
            return result

        return convert(None)

    @staticmethod
    def _function_identifier(function: Any) -> str:
        return (
            _local_id(function.source_id) if function.source_id else f"F-x{function.id}"
        )

    def _functions(self) -> dict[str, Function]:
        ga_rows = {ga.id: ga for ga in self.source.group_addresses}
        result: dict[str, Function] = {}
        spaces = {space.id: space for space in self.source.spaces}
        ordered = sorted(
            self.source.functions,
            key=lambda f: (self._space_order(spaces, f.space_id), f.order, f.id),
        )
        for function in ordered:
            identifier = self._function_identifier(function)
            group_addresses: dict[str, GroupAddressRef] = {}
            for ref in function.group_addresses:
                ga = ga_rows.get(ref.group_address_id)
                if ga is None:
                    continue
                address = self.ga_address[ga.id]
                group_addresses[address] = GroupAddressRef(
                    address=address,
                    name=cast(str, ref.name or None),
                    project_uid=ref.puid,
                    role=ref.role,
                )
            result[identifier] = Function(
                function_type=function.function_type,
                group_addresses=group_addresses,
                identifier=identifier,
                name=function.name,
                project_uid=function.puid,
                space_id=self.space_identifier.get(function.space_id, ""),
                usage_text=function.usage_text,
            )
        return result

    def _space_order(
        self, spaces: Mapping[int, SpaceRow], space_id: int
    ) -> tuple[tuple[int, int], ...]:
        """A space's place in the building tree in document order, for the order of functions."""
        path: list[tuple[int, int]] = []
        current = spaces.get(space_id)
        while current is not None:
            path.append((current.order, current.id))
            current = (
                spaces.get(current.parent_id) if current.parent_id is not None else None
            )
        return tuple(reversed(path))

    # --- trades ------------------------------------------------------------------------------

    def _trades(
        self, contexts: list[_DeviceContext]
    ) -> tuple[list[Trade], dict[int, str]]:
        """The trades in project file order, and the trade of each device (``Device.trade_id``,
        by device id): the first listing it in that order, which has a trade's sub-trades before
        its own devices."""
        addresses = {context.row.id: context.ia for context in contexts}
        children: dict[int | None, list[TradeRow]] = defaultdict(list)
        for trade in self.source.trades:
            children[trade.parent_id].append(trade)
        trade_ids: dict[int, str] = {}

        def convert(parent: int | None) -> list[Trade]:
            result: list[Trade] = []
            for trade in sorted(children[parent], key=lambda t: (t.order, t.id)):
                identifier = trade.source_id or f"T-x{trade.id}"
                trades = convert(trade.id)
                devices: list[str] = []
                for link in sorted(trade.devices, key=lambda d: (d.order, d.id)):
                    address = addresses.get(link.device_id)
                    if address is None:
                        continue
                    devices.append(address)
                    trade_ids.setdefault(link.device_id, identifier)
                result.append(
                    Trade(
                        identifier=identifier,
                        name=trade.name,
                        number=trade.number,
                        description=trade.description,
                        project_uid=trade.puid,
                        devices=sorted(devices, key=_ia_key),
                        trades=trades,
                    )
                )
            return result

        return convert(None), trade_ids


@dataclass
class _TreeNode:
    """A ``Node`` of a group object tree: a channel or a folder, with the objects it lists
    directly (``GroupObjectInstances``) and its child nodes."""

    kind: str
    ref_id: str
    text: str
    objects: list[str]
    nodes: list[_TreeNode]

    @property
    def is_channel(self) -> bool:
        return self.kind == "Channel"


@dataclass
class _ObjectTree:
    """A device's group object tree as xknxproject reads it: the nodes listing objects in or
    below them, every such channel at any depth (each before the channels below it), and the
    channel above each object (none for those of folders directly under the device)."""

    nodes: list[_TreeNode]
    channels: list[_TreeNode]
    object_channels: dict[str, str]


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _parse_tree(tree: str) -> tuple[list[_TreeNode], set[str]]:
    """The nodes of a stored group object tree, and every object it lists anywhere (also
    directly under the device)."""
    if not tree:
        return [], set()
    try:
        root = ET.fromstring(tree)
    except ET.ParseError:
        return [], set()
    listed: set[str] = set()

    def nodes_of(element: ET.Element) -> list[_TreeNode]:
        result: list[_TreeNode] = []
        for wrapper in element:
            if _local(wrapper.tag) != "Nodes":
                continue
            for node in wrapper:
                if _local(node.tag) != "Node":
                    continue
                objects = (node.get("GroupObjectInstances") or "").split()
                listed.update(objects)
                result.append(
                    _TreeNode(
                        kind="Channel" if node.get("Type") == "Channel" else "Folder",
                        ref_id=node.get("RefId", ""),
                        text=node.get("Text", ""),
                        objects=objects,
                        nodes=nodes_of(node),
                    )
                )
        return result

    listed.update((root.get("GroupObjectInstances") or "").split())
    return nodes_of(root), listed


def _iter_nodes(nodes: list[_TreeNode]) -> Iterator[_TreeNode]:
    for node in nodes:
        yield node
        yield from _iter_nodes(node.nodes)


def _used_nodes(nodes: list[_TreeNode]) -> list[_TreeNode]:
    """The nodes listing an object in or below them (xknxproject reads only those)."""
    result: list[_TreeNode] = []
    for node in nodes:
        children = _used_nodes(node.nodes)
        if node.objects or children:
            result.append(
                _TreeNode(node.kind, node.ref_id, node.text, node.objects, children)
            )
    return result


def _collect_channels(nodes: list[_TreeNode], channels: list[_TreeNode]) -> None:
    for node in nodes:
        if node.is_channel:
            channels.append(node)
        _collect_channels(node.nodes, channels)


def _object_channels(
    nodes: list[_TreeNode], channel: str | None = None
) -> dict[str, str]:
    """The nearest channel above each object of ``nodes`` (xknxproject
    ``_group_object_channels``): the channel listing it or the folder it is in."""
    result: dict[str, str] = {}
    for node in nodes:
        node_channel = node.ref_id if node.is_channel else channel
        if node_channel is not None:
            result |= dict.fromkeys(node.objects, node_channel)
        result |= _object_channels(node.nodes, node_channel)
    return result


def _folder_objects(nodes: list[_TreeNode]) -> Iterator[str]:
    """The objects of the folders of ``nodes``, nested; not those of channels below, which
    belong to that channel."""
    for node in nodes:
        if not node.is_channel:
            yield from node.objects
            yield from _folder_objects(node.nodes)


def _channel_ids(nodes: list[GroupObjectTreeNode]) -> Iterator[str]:
    for node in nodes:
        if node["type"] == "Channel":
            yield node["identifier"]
        yield from _channel_ids(node["nodes"])
