"""Offline workspace for IPAS DaliControl e64 Pro and Theben P64 V2."""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable, Generator
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING
from xml.etree import ElementTree as ET

from imgui_bundle import imgui

from editor_gui import file_dialogs
from editor_gui.device import Device
from editor_gui.plugin_blob_readers.dali_backup import COLOUR_FADE, EDITABLE_ENUMS
from editor_gui.plugin_blob_readers.dali_backup import e64pro as dca
from editor_gui.plugins.project.strings import S
from editor_gui.plugins.project.ui.e64pro_colour import (
    Colour,
    edit_colour,
    swatch,
)
from editor_gui.widgets.checkbox import checkbox
from editor_gui.widgets.combo import combo
from editor_gui.widgets.dialog import begin_dialog, dialog_buttons, end_dialog
from editor_gui.widgets.dpi import px
from editor_gui.widgets.filter_box import filter_box
from editor_gui.widgets.flow import button_width, same_line_if_fits
from editor_gui.widgets.tables import begin_data_table, data_table_headers
from editor_gui.widgets.tabs import begin_tab_bar
from editor_gui.widgets.tree import collapsing_header
from editor_gui.widgets.x_button import x_button

if TYPE_CHECKING:
    from editor_gui.plugins.project.service import ProjectService
    from editor_gui.plugins.project.ui.dali_commissioning import DaliRunner
    from xknxeditor.dali import MdtDaliCommissioner

EditHandler = Callable[[Device, bytes, dca.Edit | list[dca.Edit]], object]


@contextmanager
def table(
    name: str,
    columns: list[str],
    *,
    height: float = 260,
    min_width: float = 630,
    weights: list[float] | None = None,
) -> Generator[bool]:
    flags = (
        imgui.TableFlags_.row_bg
        | imgui.TableFlags_.borders_inner
        | imgui.TableFlags_.resizable
    )
    opened = begin_data_table(
        name,
        len(columns),
        flags | imgui.TableFlags_.scroll_y,
        imgui.ImVec2(0, px(height)),
        min_width=min_width,
    )
    try:
        if opened:
            imgui.table_setup_scroll_freeze(1, 1)
            for index, column in enumerate(columns):
                imgui.table_setup_column(
                    column,
                    imgui.TableColumnFlags_.width_stretch,
                    weights[index] if weights is not None else 1.0,
                )
            data_table_headers()
        yield opened
    finally:
        if opened:
            imgui.end_table()


def cells(values: list[str]) -> None:
    imgui.table_next_row()
    for value in values:
        imgui.table_next_column()
        imgui.text_unformatted(value)


def pick(label: str, current: str, options: dict[str, str]) -> str:
    keys = list(options)
    if current not in keys:
        keys.insert(0, current)
    changed, i = combo(label, keys.index(current), [options.get(k, k) for k in keys])
    return keys[i] if changed else current


def objects(root: ET.Element, section: str) -> dict[str, ET.Element]:
    return {
        e.attrib["index"]: e
        for e in root.findall(section + "/*")
        if "index" in e.attrib
    }


def number(index: str) -> str:
    return str(int(index) + 1) if index.isdecimal() else index


def named(prefix: str, index: str, e: ET.Element) -> str:
    return f"{prefix} {number(index)}  {e.get('description', '')}".strip()


def targets(root: ET.Element) -> dict[str, str]:
    return {
        f"{key}:{idx}": named(prefix, idx, e)
        for key, prefix in (("groups", S.E64_GROUP), ("ecgs", S.E64_ECG))
        for idx, e in objects(root, key).items()
    }


def target_key(e: ET.Element) -> str:
    kind = e.get("itemtype", "")
    prefix = next((k for k, v in dca.TARGETS.items() if kind == v), kind)
    return prefix + ":" + e.get("itemindex", "?")


@dataclass
class XmlExchange:
    descriptions: bool = False
    format: str = "xml"
    open_dialog: file_dialogs.OpenFileDialog | None = None
    save_dialog: file_dialogs.SaveFileDialog | None = None
    expected: bytes | None = None
    export_data: bytes = b""
    pending: Path | None = None
    message: str = ""
    selection: tuple[str, str] | None = None
    visible_selection: tuple[str, str] | None = None

    def render(
        self,
        device: Device,
        data: bytes | None,
        project: ProjectService,
        selection: tuple[str, str] | None = None,
    ) -> None:
        if self.visible_selection != selection:
            self.message = ""
            self.visible_selection = selection
        filters = (
            ["XML / TXT", "*.xml *.txt"] if self.descriptions else ["XML", "*.xml"]
        )
        import_label = (
            S.E64_IMPORT_DESCRIPTIONS if self.descriptions else S.E64_IMPORT_XML
        )
        export_label = (
            S.E64_EXPORT_DESCRIPTIONS if self.descriptions else S.E64_EXPORT_XML
        )
        title = S.E64_IMPORT_TITLE + "###e64_import_confirm"
        if self.open_dialog is not None and self.open_dialog.ready():
            paths = self.open_dialog.result()
            self.open_dialog = None
            if paths:
                self.pending = Path(paths[0])
                imgui.open_popup(title)
        if self.save_dialog is not None and self.save_dialog.ready():
            path = self.save_dialog.result()
            self.save_dialog = None
            if path:
                try:
                    Path(path).write_bytes(self.export_data)
                    self.message = S.E64_EXPORTED if selection == self.selection else ""
                except OSError as exc:
                    self.message = str(exc)
            self.export_data = b""

        imgui.begin_disabled(
            self.open_dialog is not None or self.save_dialog is not None
        )
        if self.descriptions:
            self.format = pick(
                "##description_format", self.format, {"xml": "XML", "txt": "TXT"}
            )
        if imgui.button(import_label):
            self.expected = data
            self.selection = selection
            self.open_dialog = file_dialogs.open_file(import_label, filters=filters)
        same_line_if_fits(button_width(export_label))
        imgui.begin_disabled(data is None)
        if imgui.button(export_label):
            self.selection = selection
            try:
                if self.descriptions and data is not None and selection is not None:
                    from editor_gui.plugin_blob_readers.dali_backup.e64pro_descriptions import (
                        export_descriptions,
                    )

                    self.export_data = export_descriptions(
                        data, selection[0], self.format
                    )
                else:
                    self.export_data = (
                        dca.export_slot(data, *selection)
                        if data is not None and selection is not None
                        else project.export_e64pro(device)
                    )
                self.save_dialog = file_dialogs.save_file(
                    export_label,
                    default_path=f"e64pro-{device.individual_address or device.node_id}{'-' + '-'.join(filter(None, selection)) if selection else ''}.{self.format if self.descriptions else 'xml'}",
                    filters=[self.format.upper(), "*." + self.format]
                    if self.descriptions
                    else filters,
                )
            except (ValueError, OSError) as exc:
                self.message = str(exc)
        imgui.end_disabled()
        imgui.end_disabled()

        if begin_dialog(title):
            imgui.text_wrapped(
                S.E64_IMPORT_DESCRIPTIONS_CONFIRM
                if self.descriptions
                else S.E64_IMPORT_SLOT_CONFIRM
                if self.selection
                else S.E64_IMPORT_CONFIRM.format(device=device.display_name)
            )
            clicked = dialog_buttons(S.DALI_CONTINUE, S.BTN_CANCEL)
            if clicked == 0 and self.pending is not None:
                try:
                    if self.selection is None:
                        project.import_e64pro(
                            device, self.expected, self.pending.read_bytes()
                        )
                    elif self.expected is not None and self.descriptions:
                        payload = self.pending.read_bytes()
                        format = (
                            "txt" if self.pending.suffix.lower() == ".txt" else "xml"
                        )
                        text = (
                            payload.decode("utf-8-sig")
                            if format == "txt"
                            else ET.tostring(ET.fromstring(payload), encoding="unicode")
                        )
                        project.edit_e64pro(
                            device,
                            self.expected,
                            dca.Edit(
                                self.selection[0],
                                "",
                                "import_descriptions",
                                field=format,
                                value=text,
                            ),
                        )
                    elif self.expected is not None:
                        parser = ET.XMLParser(
                            target=ET.TreeBuilder(insert_comments=True, insert_pis=True)
                        )
                        xml = ET.tostring(
                            ET.fromstring(self.pending.read_bytes(), parser=parser),
                            encoding="unicode",
                        )
                        project.edit_e64pro(
                            device,
                            self.expected,
                            dca.Edit(*self.selection, "import", value=xml),
                        )
                    self.message = S.E64_IMPORTED if selection == self.selection else ""
                except (ValueError, OSError, ET.ParseError) as exc:
                    self.message = str(exc)
            if clicked is not None:
                self.pending = None
                self.expected = None
                imgui.close_current_popup()
            end_dialog()
        if self.message:
            imgui.text_wrapped(self.message)


@dataclass
class Workspace:
    xml: XmlExchange = field(default_factory=XmlExchange)
    slot_xml: XmlExchange = field(default_factory=XmlExchange)
    description_xml: XmlExchange = field(
        default_factory=lambda: XmlExchange(descriptions=True)
    )
    descriptions: dict[tuple[str, str], str] = field(
        default_factory=dict[tuple[str, str], str]
    )
    description_origin: bytes | None = None
    selections: dict[str, str] = field(default_factory=dict[str, str])
    drafts: dict[str, tuple[str, str]] = field(
        default_factory=dict[str, tuple[str, str]]
    )
    error: str = ""
    snapshot: bytes | None = None
    input_revision: int = 0
    target_query: str = ""
    colour_draft: Colour | None = None
    colour_origin: tuple[str, int, Colour | None] | None = None
    colour_modes: tuple[str, ...] = ()
    open_colour: bool = False
    programming: bool = False

    def program(self, device: Device, project: ProjectService, run: DaliRunner) -> None:
        imgui.begin_disabled(self.programming)
        if imgui.button(S.PLUGIN_PARAMS_PROGRAM_DCA):
            from editor_gui.programming_dali import (
                GuardedDaliOperation,
                complete_dali_configuration,
                prepare_dali_configuration,
            )

            try:
                expected, configuration = prepare_dali_configuration(device)
                generation = project.generation
                self.programming = True
                self.error = ""
                completed = False

                async def operation(commissioner: MdtDaliCommissioner) -> object:
                    nonlocal completed
                    try:
                        result = await commissioner.write_configuration(configuration)
                        completed = True
                        return result
                    except Exception as exc:
                        self.error = str(exc)
                        raise

                def ended() -> None:
                    self.programming = False
                    if completed:
                        try:
                            complete_dali_configuration(
                                project, device, expected, generation
                            )
                        except Exception as exc:
                            self.error = str(exc)

                run(
                    device,
                    GuardedDaliOperation(
                        project, device, expected, generation, operation
                    ),
                    0,
                    S.PLUGIN_PARAMS_PROGRAM_DCA,
                    ended,
                    True,
                )
            except Exception as exc:
                self.programming = False
                self.error = str(exc)
        imgui.end_disabled()

    def choice(self, key: str, label: str, options: dict[str, str]) -> str:
        current = self.selections.get(key, "")
        if current not in options:
            current = next(iter(options), "")
        self.selections[key] = pick(label, current, options)
        return self.selections[key]

    def text(self, key: str, stored: str, label: str) -> str | None:
        origin, value = self.drafts.get(key, (stored, stored))
        if origin != stored:
            value = stored
        confirmed, new = imgui.input_text(
            f"{label}##revision{self.input_revision}",
            value,
            imgui.InputTextFlags_.enter_returns_true,
        )
        if confirmed or imgui.is_item_deactivated_after_edit():
            self.drafts.pop(key, None)
            return new if new != stored else None
        if new != stored:
            self.drafts[key] = (stored, new)
        return None

    def render(
        self,
        device: Device,
        data: bytes,
        root: ET.Element,
        write: EditHandler,
        details: Callable[[], None] | None = None,
        project: ProjectService | None = None,
        report: Callable[[], None] | None = None,
        service: Callable[[], None] | None = None,
    ) -> None:
        if self.snapshot != data:
            self.drafts.clear()
            self.colour_draft = None
            self.colour_origin = None
            self.open_colour = False
            self.input_revision += 1
            self.snapshot = data
        views = {
            "ecgs": S.E64_CONTROL_GEAR_AND_GROUPS,
            "inputs": S.E64_INPUT_DEVICES,
            "scenes": S.E64_SCENES,
            "effects": S.E64_EFFECTS,
            "templates": S.E64_TIME_CONTROL,
            "descriptions": S.E64_DESCRIPTIONS,
        }
        if report is not None:
            views["report"] = S.E64_REPORT
        if service is not None:
            views["service"] = S.E64_SERVICE
        if details is not None:
            views["raw"] = S.E64_DCA_DETAILS
        if not begin_tab_bar(
            "##dali_workspace", imgui.TabBarFlags_.fitting_policy_scroll
        ):
            return
        try:
            for section, label in views.items():
                if imgui.begin_tab_item(f"{label}##{section}")[0]:
                    try:
                        if section == "raw" and details is not None:
                            details()
                        elif section == "report" and report is not None:
                            report()
                        elif section == "service" and service is not None:
                            service()
                        else:
                            self._render_view(
                                device, data, root, write, section, label, project
                            )
                    finally:
                        imgui.end_tab_item()
        finally:
            imgui.end_tab_bar()

    def _render_view(
        self,
        device: Device,
        data: bytes,
        root: ET.Element,
        write: EditHandler,
        section: str,
        label: str,
        project: ProjectService | None = None,
    ) -> None:
        if self.error:
            imgui.text_wrapped(self.error)
        if section == "ecgs":
            self.gear(root)
        elif section == "inputs":
            self.inputs(root, lambda edit: write(device, data, edit))
        elif section == "descriptions":
            self.edit_descriptions(device, data, root, write, project)
        else:
            slots = objects(root, section)
            if not slots:
                imgui.text_disabled(S.E64_NO_ENTRIES)
                return
            imgui.set_next_item_width(
                min(px(220), imgui.get_content_region_avail().x * 0.65)
            )
            idx = self.choice(
                section, label, {k: named("", k, e) for k, e in slots.items()}
            )
            item = slots[idx]

            def submit(
                action: str,
                row: int = -1,
                field: str = "",
                value: str = "",
                target: str = "",
                index: str = "",
            ) -> None:
                try:
                    write(
                        device,
                        data,
                        dca.Edit(
                            section, idx, action, row, field, value, target, index
                        ),
                    )
                    self.error = ""
                    self.drafts.clear()
                except ValueError as exc:
                    self.error = str(exc)

            imgui.push_id(section + idx)
            try:
                if project is not None:
                    self.slot_xml.render(device, data, project, (section, idx))
                self._configuration(root, section, item, targets(root), submit)
            finally:
                imgui.pop_id()

    def gear(self, root: ET.Element) -> None:
        groups = objects(root, "groups")
        options = {
            "*": S.E64_ALL,
            **{k: named(S.E64_GROUP, k, g) for k, g in groups.items()},
            "16": S.E64_SINGLE_ECG,
            "255": S.E64_UNASSIGNED,
        }
        if imgui.get_content_region_avail().x < px(840):
            selected = self.choice("group", S.E64_GROUP, options)
            self.gear_rows(root, options, selected)
            return
        if imgui.begin_table("layout", 2):
            try:
                imgui.table_setup_column(
                    "##groups", imgui.TableColumnFlags_.width_fixed, px(192)
                )
                imgui.table_setup_column("##devices")
                imgui.table_next_row()
                imgui.table_next_column()
                selected = self.selections.get("group", "*")
                for key, label in options.items():
                    if imgui.selectable(label + "##group" + key, key == selected)[0]:
                        selected = key
                        self.selections["group"] = key
                imgui.table_next_column()
                self.gear_rows(root, options, selected)
            finally:
                imgui.end_table()

    def gear_rows(
        self, root: ET.Element, options: dict[str, str], selected: str
    ) -> None:
        physical = root.findall("devecgs/*")
        logical = root.findall("ecgs/*")
        addresses = Counter(e.get("short-address", "255") for e in logical)
        indexes = Counter(e.get("index", "") for e in logical)
        by_slot: dict[str, list[ET.Element]] = {}
        for e in physical:
            by_slot.setdefault(e.get("index", ""), []).append(e)
        assigned: dict[str, ET.Element] = {}
        for e in logical:
            index = e.get("index", "")
            address = e.get("short-address", "255")
            matches = by_slot.get(address, [])
            if (
                not index.isdecimal()
                or not 0 <= int(index) < 64
                or indexes[index] != 1
                or not address.isdecimal()
                or not 0 <= int(address) < 64
                or addresses[address] != 1
                or len(matches) != 1
            ):
                continue
            item = matches[0]
            group = item.get("groupindex", "255")
            if (
                item.get("short-address") == index
                and group.isdecimal()
                and 0 <= int(group) <= 255
                and int(group) & 63 <= 16
            ):
                assigned[index] = item
        with table(
            "gear",
            [
                S.E64_ECG,
                S.E64_DESCRIPTION,
                S.E64_GROUP,
                S.E64_SHORT_ADDRESS,
                S.E64_TYPE,
                S.E64_FLAG,
                "GTIN",
            ],
            min_width=620,
            weights=[0.55, 2.15, 2.15, 1.2, 0.55, 0.55, 1.35],
        ) as show:
            if show:
                for e in logical:
                    group = e.get("groupindex", "?")
                    if selected not in ("*", group):
                        continue
                    addr = e.get("short-address", "255")
                    item = assigned.get(e.get("index", ""))
                    gtin = item.get("gtin", "—") if item is not None else "—"
                    cells(
                        [
                            number(e.get("index", "?")),
                            e.get("description", ""),
                            options.get(group, group),
                            addr if addr.isdecimal() and 0 <= int(addr) < 64 else "—",
                            e.get("type", "?"),
                            e.get("flags", "?"),
                            gtin,
                        ]
                    )
        if not collapsing_header(S.E64_PHYSICAL_DEVICES_WITHOUT_A_UNIQUE_ASSIGNMENT):
            return
        with table(
            "physical", [S.E64_DEVICE, S.E64_SHORT_ADDRESS, S.E64_LONG_ADDRESS, "GTIN"]
        ) as show:
            if show:
                for e in physical:
                    if e not in assigned.values():
                        cells(
                            [
                                number(e.get("index", "?")),
                                e.get("index", "?"),
                                e.get("long-address", ""),
                                e.get("gtin", ""),
                            ]
                        )

    def inputs(
        self, root: ET.Element, write: Callable[[dca.Edit], object] | None = None
    ) -> None:
        for tag, label in (
            ("mds", S.E64_MOTION_BRIGHTNESS),
            ("gis", S.E64_GENERAL_INPUTS),
            ("pbs", S.E64_PUSH_BUTTONS),
            ("inputs", S.E64_PHYSICAL_INPUTS),
        ):
            if not collapsing_header(
                label, imgui.TreeNodeFlags_.default_open if tag != "inputs" else 0
            ):
                continue
            entries = objects(root, tag)
            if not entries:
                imgui.text_disabled(S.E64_NO_ENTRIES)
                continue
            imgui.push_id(tag)
            try:
                with table(
                    "inputs",
                    [S.E64_DESCRIPTION, S.E64_TYPE, S.E64_ADDRESS, S.E64_INSTANCE],
                    height=min(235, 45 + 27 * len(entries)),
                    min_width=440,
                    weights=[2.2, 1.3, 1, 1],
                ) as show:
                    if show:
                        for index, entry in entries.items():
                            instances = entry.findall("Instances/EtsInstance")
                            if tag == "inputs" or not instances:
                                cells(
                                    [
                                        named(label, index, entry),
                                        entry.get("type", label),
                                        entry.get(
                                            "short-address",
                                            index if tag == "inputs" else "",
                                        ),
                                        str(len(entry.findall("Instances/*"))),
                                    ]
                                )
                                continue
                            from editor_gui.plugin_blob_readers.dali_backup.e64pro_inputs import (
                                compatible,
                                input_type,
                                physical_inputs,
                            )

                            for row, instance in enumerate(instances):
                                imgui.push_id(f"{index}:{row}")
                                imgui.table_next_row()
                                imgui.table_next_column()
                                imgui.text_unformatted(named(label, index, entry))
                                imgui.table_next_column()
                                imgui.text_unformatted(
                                    S.E64_INPUT_TYPES.get(
                                        input_type(instance), S.E64_INPUT_TYPES[255]
                                    )
                                )
                                imgui.table_next_column()
                                options = {"": S.E64_UNASSIGNED}
                                for physical, child in physical_inputs(root):
                                    if compatible(instance, child):
                                        options[child.get("index", "")] = (
                                            f"{physical.get('index', '?')} / {child.get('number', '?')}"
                                        )
                                current = instance.get("ref-idx", "-1")
                                if current in ("-1", "254", "255"):
                                    current = ""
                                imgui.set_next_item_width(-1)
                                selected = pick("##assignment", current, options)
                                if selected != current and write is not None:
                                    try:
                                        write(
                                            dca.Edit(
                                                tag,
                                                index,
                                                "assign",
                                                row=row,
                                                index=selected,
                                            )
                                        )
                                        self.error = ""
                                    except ValueError as exc:
                                        self.error = str(exc)
                                imgui.table_next_column()
                                imgui.text_unformatted(str(row + 1))
                                imgui.pop_id()
            finally:
                imgui.pop_id()

    def edit_descriptions(
        self,
        device: Device,
        data: bytes,
        root: ET.Element,
        write: EditHandler,
        project: ProjectService | None = None,
    ) -> None:
        if self.description_origin != data and not self.descriptions:
            self.description_origin = data
        labels = {
            "groups": S.E64_GROUPS,
            "ecgs": S.E64_CONTROL_GEAR_AND_GROUPS,
            "mds": S.E64_MOTION_BRIGHTNESS,
            "pbs": S.E64_PUSH_BUTTONS,
            "gis": S.E64_GENERAL_INPUTS,
            "scenes": S.E64_SCENES,
            "effects": S.E64_EFFECTS,
            "templates": S.E64_TIME_CONTROL,
        }
        section = self.choice("description_section", S.E64_TYPE, labels)
        imgui.text_wrapped(S.E64_DESCRIPTIONS_HINT)
        with table(
            "descriptions",
            [S.E64_INSTANCE, S.E64_DESCRIPTION],
            min_width=340,
            weights=[0.3, 2],
        ) as show:
            if show:
                for index, item in objects(root, section).items():
                    imgui.table_next_row()
                    imgui.table_next_column()
                    imgui.text_unformatted(number(index))
                    imgui.table_next_column()
                    stored = item.get("description", "")
                    value = self.descriptions.get((section, index), stored)
                    imgui.set_next_item_width(-1)
                    changed, value = imgui.input_text(f"##{section}-{index}", value)
                    if changed:
                        if value == stored:
                            self.descriptions.pop((section, index), None)
                        else:
                            self.descriptions[section, index] = value
        imgui.begin_disabled(not self.descriptions)
        if imgui.button(S.E64_APPLY):
            try:
                write(
                    device,
                    self.description_origin
                    if self.description_origin is not None
                    else data,
                    [
                        dca.Edit(section, index, "description", value=value)
                        for (section, index), value in self.descriptions.items()
                    ],
                )
                self.descriptions.clear()
                self.description_origin = None
                self.error = ""
            except ValueError as exc:
                self.error = str(exc)
        imgui.end_disabled()
        same_line_if_fits(button_width(S.E64_CANCEL))
        if imgui.button(S.E64_CANCEL):
            self.descriptions.clear()
            self.description_origin = data
        same_line_if_fits(button_width(S.E64_CLEAR_DESCRIPTIONS))
        if imgui.button(S.E64_CLEAR_DESCRIPTIONS):
            for index, item in objects(root, section).items():
                if item.get("description", ""):
                    self.descriptions[section, index] = ""
                else:
                    self.descriptions.pop((section, index), None)
        if project is not None and section in ("groups", "ecgs", "mds", "pbs", "gis"):
            imgui.begin_disabled(bool(self.descriptions))
            self.description_xml.render(device, data, project, (section, ""))
            imgui.end_disabled()

    def _configuration(
        self,
        root: ET.Element,
        section: str,
        item: ET.Element,
        names: dict[str, str],
        submit: Callable[..., None],
    ) -> None:
        if (
            names
            and imgui.get_content_region_avail().x >= px(1120)
            and imgui.begin_table("##section_layout", 2, imgui.TableFlags_.resizable)
        ):
            try:
                imgui.table_setup_column(
                    "##data", imgui.TableColumnFlags_.width_stretch, 1
                )
                imgui.table_setup_column(
                    "##targets", imgui.TableColumnFlags_.width_fixed, px(248)
                )
                imgui.table_next_column()
                self.editor(root, section, item, names, submit)
                imgui.table_next_column()
                self._target_picker(section, item, names, submit)
            finally:
                imgui.end_table()
        else:
            if names:
                button = (
                    S.E64_ASSIGNED_GROUPS_AND_ECGS
                    if section == "templates"
                    else S.E64_ADD_TARGET
                )
                if imgui.button(button):
                    imgui.open_popup("##target_picker")
                if imgui.begin_popup("##target_picker"):
                    try:
                        self._target_picker(section, item, names, submit)
                    finally:
                        imgui.end_popup()
            self.editor(root, section, item, names, submit)
        self._colour_popup(section, submit)

    def _target_picker(
        self,
        section: str,
        item: ET.Element,
        names: dict[str, str],
        submit: Callable[..., None],
    ) -> None:
        window = imgui.get_io().display_size
        width = min(px(248), max(px(110), window.x - px(32)))
        height = min(px(325), max(px(110), window.y * 0.53))
        opened = imgui.begin_child(
            "##target_list", imgui.ImVec2(width, height), imgui.ChildFlags_.borders
        )
        try:
            if not opened:
                return
            self.target_query = filter_box(
                "##target_search", S.E64_SEARCH, self.target_query
            )
            query = self.target_query.strip().casefold()
            members = {
                target_key(entry)
                for entry in (
                    item.findall("Members/TplItem")
                    if section == "templates"
                    else dca.rows(item, section)
                )
            }
            for kind, heading in (("groups", S.E64_GROUPS), ("ecgs", S.E64_ECG)):
                matching = [
                    (key, label)
                    for key, label in names.items()
                    if key.startswith(kind + ":") and query in label.casefold()
                ]
                if not matching:
                    continue
                imgui.separator_text(heading)
                for key, label in matching:
                    index = key.split(":", 1)[1]
                    if section == "templates":
                        changed, selected = checkbox(f"{label}##{key}", key in members)
                        if changed:
                            submit(
                                "member", value=str(selected), target=kind, index=index
                            )
                    else:
                        assigned = section == "scenes" and key in members
                        flags = imgui.SelectableFlags_.no_auto_close_popups
                        imgui.begin_disabled(assigned)
                        clicked, _selected = imgui.selectable(
                            f"{label}##{key}", assigned, flags
                        )
                        imgui.end_disabled()
                        if clicked:
                            submit("add", target=kind, index=index)
        finally:
            imgui.end_child()

    def _colour_cell(
        self,
        section: str,
        row: int,
        mode: str,
        value: str,
        modes: tuple[str, ...],
        keep: bool = False,
    ) -> None:
        colour = Colour.parse(mode, value)
        verified = tuple(
            candidate for candidate in modes if candidate in dca.VERIFIED_COLOUR_MODES
        )
        if colour is None:
            if value:
                imgui.text_unformatted(
                    f"{dca.COLOURS.get(mode, mode)}  {value}".strip()
                )
                return
            if keep or not verified or (mode and mode not in verified):
                imgui.text_disabled("—")
                return
            if not imgui.button(S.E64_COLOR + "…##colour"):
                return
            initial = mode or verified[0]
            draft = Colour.parse(initial, dca.DEFAULT_COLOURS[initial])
        else:
            editable = mode in verified and not keep
            imgui.begin_disabled(not editable)
            if editable:
                preview = swatch(colour)
                red, green, blue = colour.rgb
                foreground = (
                    0.04 if 0.299 * red + 0.587 * green + 0.114 * blue > 0.55 else 1.0
                )
                imgui.push_style_color(imgui.Col_.button, preview)
                imgui.push_style_color(imgui.Col_.button_hovered, preview)
                imgui.push_style_color(imgui.Col_.button_active, preview)
                imgui.push_style_color(
                    imgui.Col_.text,
                    imgui.ImVec4(foreground, foreground, foreground, 1.0),
                )
            pressed = imgui.button(f"{colour.summary}##colour", imgui.ImVec2(-1, 0))
            if editable:
                imgui.pop_style_color(4)
            imgui.end_disabled()
            if not pressed:
                return
            draft = colour
        self.colour_origin = (section, row, colour)
        self.colour_draft = draft
        self.colour_modes = verified
        self.open_colour = True

    def _colour_popup(self, section: str, submit: Callable[..., None]) -> None:
        if self.open_colour:
            self.open_colour = False
            imgui.open_popup("##colour_dialog")
        if not imgui.begin_popup("##colour_dialog"):
            return
        try:
            origin, colour = self.colour_origin, self.colour_draft
            if origin is None or colour is None or origin[0] != section:
                imgui.close_current_popup()
                return
            imgui.separator_text(S.E64_COLOR)
            modes = self.colour_modes
            if len(modes) > 1:
                mode = pick(S.E64_MODE, colour.mode, {m: dca.COLOURS[m] for m in modes})
                if mode != colour.mode:
                    colour = Colour.parse(mode, dca.DEFAULT_COLOURS[mode]) or colour
            imgui.push_item_width(
                min(px(246), max(px(128), imgui.get_io().display_size.x - px(114)))
            )
            try:
                colour = edit_colour(colour)
            finally:
                imgui.pop_item_width()
            self.colour_draft = colour
            imgui.color_button(
                "##preview",
                swatch(colour),
                imgui.ColorEditFlags_.no_tooltip,
                imgui.ImVec2(px(34), px(20)),
            )
            imgui.same_line()
            imgui.text_unformatted(colour.summary)
            if colour.mode in ("1", "2"):
                imgui.text_disabled(S.E64_APPROXIMATE_PREVIEW)
            if imgui.button(S.E64_APPLY):
                if colour != origin[2]:
                    if section == "templates":
                        submit("set", origin[1], "value", colour.raw)
                    else:
                        submit("colour", origin[1], colour.mode, colour.raw)
                imgui.close_current_popup()
            imgui.same_line()
            if imgui.button(S.E64_CANCEL):
                self.colour_draft = None
                imgui.close_current_popup()
        finally:
            imgui.end_popup()

    def editor(
        self,
        root: ET.Element,
        section: str,
        item: ET.Element,
        names: dict[str, str],
        submit: Callable[..., None],
    ) -> None:
        def edit(
            e: ET.Element,
            field: str,
            label: str,
            row: int = -1,
            options: dict[str, str] | None = None,
        ) -> None:
            stored = e.get(field, "")
            if label.startswith("##"):
                imgui.set_next_item_width(-1.0)
            if options is not None:
                value = pick(label, stored, options)
            else:
                value = self.text(
                    f"{section}/{item.get('index')}/{row}/{field}", stored, label
                )
            if value is not None and value != stored:
                submit("header" if row < 0 else "set", row, field, value)

        def tick(label: str, value: bool, action: Callable[[bool], None]) -> None:
            changed, new = checkbox(label, value)
            if changed:
                action(new)

        width = imgui.get_content_region_avail().x
        description_width = (
            width - px(376)
            if section == "scenes" and width >= px(660)
            else min(px(390), width * 0.65)
        )
        imgui.set_next_item_width(description_width)
        edit(item, "description", S.E64_DESCRIPTION)
        if section == "scenes":
            if width >= px(660):
                imgui.same_line()
            imgui.set_next_item_width(px(55))
            edit(item, "knxScene", S.E64_KNX_SCENE)
            if width >= px(350):
                imgui.same_line()
            imgui.set_next_item_width(px(80))
            edit(item, "fadeTime", S.E64_FADE_TIME, options=COLOUR_FADE)
        if section == "effects":
            mode = item.get("mode", "0")
            if mode in ("0", "1"):
                tick(
                    S.E64_LOOP,
                    mode == "1",
                    lambda v: submit("header", -1, "mode", str(int(v))),
                )
            else:
                imgui.text_unformatted(mode)
        if section == "templates":
            edit(
                item,
                "mode",
                S.E64_MODE,
                options={
                    "Disabled": S.E64_OFF,
                    "Enabled": S.E64_ACTIVE,
                    "Controlled": S.E64_CONTROLLED,
                },
            )
            for attr, label in (
                ("isOverwritable", S.E64_MANUAL_OVERRIDE),
                ("resetOnMidnight", S.E64_MIDNIGHT_RESET),
            ):
                raw = item.get(attr, "True" if attr == "resetOnMidnight" else "False")
                if width >= px(620):
                    imgui.same_line()
                if raw in ("True", "False"):
                    tick(
                        label,
                        raw == "True",
                        lambda v, a=attr: submit("header", -1, a, str(v)),
                    )
                else:
                    imgui.text_unformatted(label + ": " + raw)
            members = dict.fromkeys(
                target_key(e) for e in item.findall("Members/TplItem")
            )
            if members:
                imgui.text_wrapped(", ".join(names.get(key, key) for key in members))
        sequence = dca.rows(item, section)
        weekdays = list(S.E64_WEEKDAYS.values())
        if section == "templates":
            columns = [
                S.E64_FUNCTION,
                S.E64_VALUE,
                S.E64_TIME,
                S.E64_FADE_TIME,
                *weekdays,
                S.E64_ACTIONS,
            ]
            weights = [2.7, 1.2, 1.1, 1.1, *([0.52] * 7), 1.4]
            functions = {
                "CallScene": S.E64_CALL_SCENE,
                "CallEffect": S.E64_CALL_EFFECT,
                "SetValue": S.E64_BRIGHTNESS,
                "SetMinValue": S.E64_MIN_BRIGHTNESS,
                "SetMaxValue": S.E64_MAX_BRIGHTNESS,
                "SetMaxOnValue": S.E64_SWITCH_ON_LIMIT,
                "ColourTemp": S.E64_COLOR_TEMPERATURE,
                **{
                    func: dca.COLOURS[mode]
                    for func, mode in dca.TEMPLATE_COLOURS.items()
                    if func != "ColourTemp"
                },
            }
        else:
            functions = {}
            columns = [
                S.E64_TARGET,
                S.E64_VALUE + " (%)",
                S.E64_COLOR,
                S.E64_KEEP_VALUE,
                S.E64_KEEP_COLOR,
            ]
            if section == "effects":
                columns += [S.E64_FADE_TIME, S.E64_DELAY]
            columns.append(S.E64_ACTIONS)
            weights = [
                2.7,
                0.9,
                2.2,
                1.15,
                1.15,
                *([1.0, 1.0] if section == "effects" else []),
                1.45 if section == "effects" else 1.25,
            ]
        with table(
            "sequence",
            columns,
            height=min(450, 55 + 30 * len(sequence)),
            min_width={"scenes": 690, "effects": 835, "templates": 780}[section],
            weights=weights,
        ) as show:
            if show:
                for n, e in enumerate(sequence):
                    imgui.push_id(n)
                    imgui.table_next_row()
                    imgui.table_next_column()
                    if section == "templates":
                        edit(
                            e,
                            "func",
                            "##func",
                            n,
                            functions,
                        )
                        imgui.table_next_column()
                        function = e.get("func", "")
                        mode = dca.TEMPLATE_COLOURS.get(function)
                        if mode is not None:
                            raw = e.get("value", "")
                            if mode == "2":
                                raw = raw.removesuffix(dca.LEGACY_KELVIN_SUFFIX)
                            self._colour_cell(section, n, mode, raw, (mode,))
                        elif function in dca.CALL_FUNCTIONS:
                            target_section = dca.CALL_FUNCTIONS[function]
                            choices = {
                                entry.get(
                                    "index", ""
                                ): f"{int(entry.get('index', '0')) + 1}: {entry.get('description', '')}"
                                for entry in root.findall(target_section + "/*")
                                if entry.get("index", "").isdecimal()
                                and 0 <= int(entry.get("index", "0")) < 16
                            }
                            edit(e, "value", "##value", n, choices)
                            if function == "CallEffect":
                                edit(
                                    e,
                                    "scaleMode",
                                    "##effect-action",
                                    n,
                                    {
                                        "ScaleNoneEffectStop": S.DALI_STOP,
                                        "ScaleNoneEffectStart": S.E64_START,
                                    },
                                )
                        elif function in dca.COMMAND_DEFAULTS:
                            edit(e, "value", "##value", n)
                        else:
                            imgui.text_unformatted(e.get("value", "—"))
                        imgui.table_next_column()
                        edit(e, "time", "##time", n)
                        imgui.table_next_column()
                        edit(
                            e,
                            "fadeIn",
                            "##fade",
                            n,
                            {
                                k: k.removeprefix("CFadeIn").removeprefix("VFadeIn")
                                + " s"
                                for k in dca.fade_choices(e)
                            },
                        )
                        days = e.get("weekdays", "")
                        valid_days = len(days) == 7 and set(days) <= {"1", "0"}
                        for day, _label in enumerate(weekdays):
                            imgui.table_next_column()
                            if valid_days:
                                position = 6 - day
                                tick(
                                    f"##day{day}",
                                    days[position] == "1",
                                    lambda v, i=position, row=n, mask=days: submit(
                                        "set",
                                        row,
                                        "weekdays",
                                        mask[:i] + str(int(v)) + mask[i + 1 :],
                                    ),
                                )
                            else:
                                imgui.text_disabled("—")
                    else:
                        key = target_key(e)
                        target = names.get(key, key)
                        imgui.text_unformatted(
                            f"{n + 1}. {target}" if section == "effects" else target
                        )
                        flags = e.get("setting", "0")
                        mask = int(flags) if flags.isdecimal() else 0
                        imgui.table_next_column()
                        imgui.begin_disabled(bool(mask & 2))
                        edit(e, "value", "##value", n)
                        imgui.end_disabled()
                        imgui.table_next_column()
                        self._colour_cell(
                            section,
                            n,
                            e.get("color-type", ""),
                            e.get("color", ""),
                            dca.colour_modes(root, e),
                            bool(mask & 1),
                        )
                        for flag, mask in (("keepValue", 2), ("keepColor", 1)):
                            imgui.table_next_column()
                            if flags.isdecimal() and (mask == 2 or "color" in e.attrib):
                                tick(
                                    "##" + flag,
                                    bool(int(flags) & mask),
                                    lambda v, f=flag, row=n: submit(
                                        "set", row, f, str(v)
                                    ),
                                )
                            else:
                                imgui.text_disabled("—")
                        if section == "effects":
                            for field_name in ("fadetime", "delay"):
                                imgui.table_next_column()
                                edit(
                                    e,
                                    field_name,
                                    "##" + field_name,
                                    n,
                                    EDITABLE_ENUMS[field_name],
                                )
                    imgui.table_next_column()
                    if section == "templates":
                        if imgui.button(S.E64_INSERT):
                            submit("insert", n)
                        imgui.same_line()
                        if imgui.button(S.E64_COPY):
                            submit("copy", n)
                        imgui.same_line()
                    if section == "effects":
                        imgui.begin_disabled(n == 0)
                        if imgui.arrow_button("##previous_step", imgui.Dir.up):
                            submit("up", n)
                        imgui.end_disabled()
                        imgui.same_line()
                        imgui.begin_disabled(n + 1 == len(sequence))
                        if imgui.arrow_button("##next_step", imgui.Dir.down):
                            submit("down", n)
                        imgui.end_disabled()
                        imgui.same_line()
                    if section == "effects":
                        size = imgui.get_frame_height()
                        removed = x_button("remove_step", imgui.ImVec2(size, size))
                        if imgui.is_item_hovered():
                            imgui.set_tooltip(S.E64_REMOVE)
                    else:
                        removed = imgui.button(S.E64_REMOVE)
                    if removed:
                        submit("remove", n)
                    imgui.pop_id()
        if section == "templates" and imgui.button(S.E64_ADD_COMMAND):
            submit("add")
        if section == "templates":
            for action, label in (
                ("sort_time", S.E64_SORT_TIME),
                ("sort_function", S.E64_SORT_FUNCTION),
                ("clear", S.E64_CLEAR_COMMANDS),
            ):
                same_line_if_fits(button_width(label))
                imgui.begin_disabled(not sequence)
                if imgui.button(label):
                    submit(action)
                imgui.end_disabled()
