"""The open project as Home Assistant's KNX integration reads a project: xknxproject's
``KNXProject``.

Home Assistant's KNX integration takes the project it decodes telegrams with, offers group
addresses from and suggests entities by from a ``KNXProject`` dictionary. It is built here from
what the editor holds, the way xknxproject builds it from a project file, so the integration
sees the same as with an uploaded project file, and without writing and parsing a file:

- the rows of the project (:meth:`ProjectService.model_source`),
- each application program's group objects, channels and module arguments
  (:mod:`.application_digest`, read once per program),
- the group objects' numbers as the editor resolves them (module base numbers included),
- the devices' stored group object trees for the channels, completed with the linked objects
  they do not list by the channel stored for each object.

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
from collections.abc import Iterable, Mapping
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
    GroupRange,
    KNXProject,
    Line,
    ModuleInstanceInfos,
    ProjectInfo,
    Space,
    TextParameter,
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
    ObjectDef,
    application_digest,
)

if TYPE_CHECKING:
    from editor_gui.plugins.project.service import ProjectService
    from xknxeditor.proj.core.service import ModelComObject as ComObjectRow
    from xknxeditor.proj.core.service import ModelSource
    from xknxeditor.proj.models import Device as DeviceRow
    from xknxeditor.proj.models import GroupRange as GroupRangeRow
    from xknxeditor.proj.models import Space as SpaceRow

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
    """
    source = project.model_source()
    if source is None:
        raise ProjectModelUnavailable("no project is open")
    digests: dict[int, ApplicationDigest] = {}
    numbers: dict[int, int] = {}
    for device in project.devices:
        if device.app is None:
            continue
        digests[device.node_id] = application_digest(device.app)
        for co in device.get_visible_com_objects():
            if co.db_id is not None:
                numbers[co.db_id] = co.number
    return _Builder(source, digests, numbers).build(
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
        for _instance_id, arguments in containing:
            for ref_id, value in arguments:
                argument = self.digest.arguments.get(ref_id)
                if argument is None:
                    continue
                values[argument.name] = None if argument.computed else value
        return {name: value for name, value in values.items() if value is not None}

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
    ) -> None:
        self.source = source
        self.digests = digests
        self.numbers = numbers
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
        communication_objects: dict[str, CommunicationObject] = {}
        devices: dict[str, Device] = {}
        for context in contexts:
            device_objects: list[str] = []
            for co in self.com_objects[context.row.id]:
                links = self._links(co)
                if not links:
                    continue
                key = f"{context.ia}/{self._instance_ref_id(context, co)}"
                communication_objects[key] = self._com_object(context, co, links)
                device_objects.append(key)
            devices[context.ia] = self._device(context, device_objects)

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
                module_arguments=[
                    (
                        instance.instance_id,
                        [
                            (
                                self._argument_ref_id(
                                    application, instance.ref_id, arg["ref_id"]
                                ),
                                arg["value"],
                            )
                            for arg in instance.arguments
                            if "ref_id" in arg and "value" in arg
                        ],
                    )
                    for instance in row.module_instances
                ],
            )
            placed.append(((area.address, line.address, row.address, row.id), context))
        placed.sort(key=lambda item: item[0])
        return [context for _key, context in placed]

    @staticmethod
    def _argument_ref_id(application: str | None, module_ref: str, ref_id: str) -> str:
        """A module argument's id completed with the application (xknxproject
        ``ModuleInstanceArgument.complete_ref_id``)."""
        if ref_id.startswith(f"{application}_"):
            return ref_id
        if ref_id.startswith("SM-"):
            return f"{application}_{module_ref.split('_', 1)[0]}_{ref_id}"
        return f"{application}_{ref_id}"

    def _device(self, context: _DeviceContext, objects: list[str]) -> Device:
        row = context.row
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
            communication_object_ids=objects,
            channels=self._channels(context),
            serial_number=row.serial_number,
            last_download=row.last_download,
            individual_address_loaded=row.individual_address_loaded,
            application_program_loaded=row.application_program_loaded,
            communication_part_loaded=row.communication_part_loaded,
            medium_config_loaded=row.medium_config_loaded,
            parameters_loaded=row.parameters_loaded,
        )

    def _channels(self, context: _DeviceContext) -> dict[str, Channel]:
        """The channels of the device that hold group objects: those of its stored group object
        tree with their objects, completed with the linked objects the tree does not list (added
        in the editor) by the channel the editor stored for them."""
        nodes: dict[str, tuple[str, list[str]]] = {}
        channel_nodes, listed = _tree_channels(context.row.group_object_tree)
        for ref_id, text, objects in channel_nodes:
            nodes[ref_id] = (text, objects)
        for co in self.com_objects[context.row.id]:
            instance = self._instance_ref_id(context, co)
            if co.channel_id and instance not in listed:
                nodes.setdefault(co.channel_id, ("", []))[1].append(instance)
                listed.add(instance)
        channels: dict[str, Channel] = {}
        for ref_id, (text, objects) in nodes.items():
            if not objects:
                continue
            name, blocks = self._channel_name(context, ref_id, text)
            channels[ref_id] = Channel(
                identifier=ref_id,
                name=name,
                communication_object_ids=[f"{context.ia}/{o}" for o in objects],
                functional_blocks=list(blocks) if blocks is not None else None,
            )
        return channels

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
        self, context: _DeviceContext, co: ComObjectRow, links: list[str]
    ) -> CommunicationObject:
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
            channel=co.channel_id,
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
        identifier = next(
            (
                instance_id
                for instance_id, _args in context.module_arguments
                if instance.startswith(f"{instance_id}_")
            ),
            instance.split("_O-", 1)[0],
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


def _tree_channels(
    tree: str,
) -> tuple[list[tuple[str, str, list[str]]], set[str]]:
    """The channel nodes of a group object tree, at any depth (their ``RefId``, ``Text`` and
    ``GroupObjectInstances``), and every object the tree lists anywhere."""
    if not tree:
        return [], set()
    try:
        root = ET.fromstring(tree)
    except ET.ParseError:
        return [], set()
    channels: list[tuple[str, str, list[str]]] = []
    listed: set[str] = set()
    for node in root.iter():
        objects = (node.get("GroupObjectInstances") or "").split()
        listed.update(objects)
        if node.tag.rsplit("}", 1)[-1] == "Node" and node.get("Type") == "Channel":
            channels.append((node.get("RefId", ""), node.get("Text", ""), objects))
    return channels, listed
