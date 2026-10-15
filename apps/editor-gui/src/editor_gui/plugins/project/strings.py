from pathlib import Path

from editor_gui.strings import create_translator

_locale_dir = Path(__file__).parent / "locales"
_ = create_translator("project", _locale_dir)


class ProjectStrings:
    @property
    def PANEL_DEVICE(self) -> str:
        return _("Device")

    @property
    def PANEL_MANUFACTURER(self) -> str:
        return _("Manufacturer")

    @property
    def PANEL_DEVICES(self) -> str:
        return _("Devices")

    @property
    def PANEL_CONFIGURE(self) -> str:
        return _("Configure")

    @property
    def PANEL_HISTORY(self) -> str:
        return _("History")

    @property
    def PANEL_EDITOR(self) -> str:
        return _("Editor")

    @property
    def PANEL_GROUP_ADDRESSES(self) -> str:
        return _("Group Addresses")

    @property
    def PANEL_BUILDINGS(self) -> str:
        return _("Buildings")

    @property
    def PANEL_PROJECT_INFO(self) -> str:
        return _("Project")

    @property
    def PANEL_PROJECT_LOG(self) -> str:
        return _("Project Log")

    @property
    def PANEL_MASS_LINKER(self) -> str:
        return _("Mass Linker")

    @property
    def ML_TAB_GA_CO(self) -> str:
        return _("GA <> Object")

    @property
    def ML_TAB_CO_CO(self) -> str:
        return _("Object <> Object")

    @property
    def ML_TAB_LOG(self) -> str:
        return _("Log")

    @property
    def ML_NO_PROJECT(self) -> str:
        return _("Open a project to use the Mass Linker.")

    @property
    def ML_HELP(self) -> str:
        return _(
            "Pairs are matched by list order (1st with 1st, ...). Surplus items on the "
            "longer side are ignored. Reorder a list via its right-click menu."
        )

    @property
    def ML_DESC_GA_CO(self) -> str:
        return _(
            "Link many communication objects to existing group addresses at once. "
            "1) Add objects: '+ Selected device' (all objects of the device selected in the tree) "
            "or 'Add objects…'. 2) Set each row's target address in its dropdown — or fill them "
            "fast with 'Assign sequential' (next free addresses from the given start) or "
            "'Match by name'. 3) 'Link all' links every row that has a target."
        )

    @property
    def ML_DESC_CO_CO(self) -> str:
        return _(
            "Link communication objects to each other. 1) Add the source objects. 2) Pick a "
            "partner object per row. Each pair gets a NEW group address (source sends, target "
            "receives): the address and name are suggested (first free address, name template "
            "below) but can be edited per row. 3) 'Link all' creates and links them."
        )

    @property
    def ML_DESC_LOG(self) -> str:
        return _("Chronological record of every mass-link operation in this session.")

    @property
    def ML_SOURCE(self) -> str:
        return _("Source (objects)")

    @property
    def ML_TARGET_GA(self) -> str:
        return _("Target (Group Addresses)")

    @property
    def ML_TARGET_CO(self) -> str:
        return _("Target (objects)")

    @property
    def ML_PREVIEW(self) -> str:
        return _("Preview & Link")

    @property
    def ML_ADD_OBJECTS(self) -> str:
        return _("Add Objects...")

    @property
    def ML_ADD_GAS(self) -> str:
        return _("Add Group Addresses...")

    @property
    def ML_ADD_SELECTED(self) -> str:
        return _("Add Selected")

    @property
    def ML_CLEAR_ALL(self) -> str:
        return _("Clear")

    @property
    def ML_REMOVE(self) -> str:
        return _("Remove")

    @property
    def ML_MOVE_UP(self) -> str:
        return _("Move up")

    @property
    def ML_MOVE_DOWN(self) -> str:
        return _("Move Down")

    @property
    def ML_COUNT(self) -> str:
        return _("{count} items")

    @property
    def ML_PAIR_COUNT(self) -> str:
        return _("{count} pairs")

    @property
    def ML_UNPAIRED(self) -> str:
        return _("{count} unpaired (ignored)")

    @property
    def ML_LINK(self) -> str:
        return _("Link")

    @property
    def ML_NAME_TEMPLATE(self) -> str:
        return _("GA Name ({source}, {target}, {n})")

    @property
    def ML_START_ADDRESS(self) -> str:
        return _("Start Address")

    @property
    def ML_FILTER_HINT(self) -> str:
        return _("Filter...")

    @property
    def ML_PICK_OBJECTS_TITLE(self) -> str:
        return _("Add Communication Objects")

    @property
    def ML_PICK_GAS_TITLE(self) -> str:
        return _("Add Group Addresses")

    @property
    def ML_RESULT_TITLE(self) -> str:
        return _("Mass Linker Result")

    @property
    def ML_LINK_GA_CO_DONE(self) -> str:
        return _("Linked {count} object(s) to group addresses")

    @property
    def ML_LINK_CO_CO_DONE(self) -> str:
        return _("Linked {count} object pair(s) via new group addresses")

    @property
    def ML_ERRORS(self) -> str:
        return _("{count} error(s)")

    @property
    def ML_LOG_CLEAR(self) -> str:
        return _("Clear All")

    @property
    def ML_LOG_EMPTY(self) -> str:
        return _("No operations yet.")

    @property
    def ML_COL_DEVICE(self) -> str:
        return _("Device")

    @property
    def ML_COL_OBJECT(self) -> str:
        return _("Object")

    @property
    def ML_COL_DPT(self) -> str:
        return _("DPT")

    @property
    def ML_COL_TARGET_GA(self) -> str:
        return _("Target Group Address")

    @property
    def ML_COL_TARGET_CO(self) -> str:
        return _("Target Object")

    @property
    def ML_COL_GA_ADDR(self) -> str:
        return _("Group Address")

    @property
    def ML_COL_GA_NAME(self) -> str:
        return _("Group Address Name")

    @property
    def ML_TARGET_NONE(self) -> str:
        return _("(none)")

    @property
    def ML_ADD_DEVICE(self) -> str:
        return _("+ Selected Device")

    @property
    def ML_SELECT_ALL(self) -> str:
        return _("Select All")

    @property
    def ML_TABLE_EMPTY(self) -> str:
        return _("No objects yet — add the selected device or pick objects.")

    @property
    def ML_SEQ_FROM(self) -> str:
        return _("From")

    @property
    def ML_ASSIGN_SEQ(self) -> str:
        return _("Assign Sequential")

    @property
    def ML_MATCH_NAME(self) -> str:
        return _("Match by Name")

    @property
    def ML_LINK_ALL(self) -> str:
        return _("Link All ({count})")

    @property
    def ML_SUMMARY(self) -> str:
        return _(
            "{ready} ready · {amb} ambiguous · {bad} incompatible · {none} unmatched"
        )

    @property
    def ML_SHOW_PROBLEMS(self) -> str:
        return _("Only problems")

    @property
    def ML_OK(self) -> str:
        return _("OK")

    @property
    def ML_CANCEL(self) -> str:
        return _("Cancel")

    @property
    def PANEL_TOOLS(self) -> str:
        return _("Tools")

    @property
    def TOOLS_NO_PROJECT(self) -> str:
        return _("Open a project to use the tools.")

    @property
    def TOOLS_NO_DEVICES(self) -> str:
        return _("No devices in this project.")

    @property
    def TOOLS_TAB_COPY(self) -> str:
        return _("Extended Copy")

    @property
    def TOOLS_TAB_REPLACE(self) -> str:
        return _("Replace Device")

    @property
    def TOOLS_REPLACE_DESC(self) -> str:
        return _(
            "Swap a device for another model (a template device already in the project), "
            "keeping its group-address links. Group objects are matched by number and size; "
            "unmatched ones are reported. Parameters are not carried over."
        )

    @property
    def TOOLS_REPLACE_TARGET(self) -> str:
        return _("Device to Replace")

    @property
    def TOOLS_REPLACE_WITH(self) -> str:
        return _("Replace with (template)")

    @property
    def TOOLS_REPLACE_PREVIEW(self) -> str:
        return _("Group Object Mapping (by Number)")

    @property
    def TOOLS_REPLACE_COUNT(self) -> str:
        return _("{count} object(s) will be re-mapped")

    @property
    def TOOLS_REPLACE_RUN(self) -> str:
        return _("Replace Device")

    @property
    def TOOLS_REPLACE_DONE(self) -> str:
        return _("Replaced; re-mapped {count} group object(s)")

    @property
    def TOOLS_TAB_SHIFT(self) -> str:
        return _("Shift Addresses")

    @property
    def TOOLS_TAB_LABELS(self) -> str:
        return _("Labels")

    @property
    def TOOLS_TAB_TOPOLOGY(self) -> str:
        return _("Topology Check")

    @property
    def TOOLS_TAB_DIFF(self) -> str:
        return _("Project Diff")

    @property
    def TOOLS_DIFF_DESC(self) -> str:
        return _(
            "Compare the open project with another project file (.knxproj or .xknx): "
            "which devices and group addresses were added, removed or changed. The other "
            "project is read-only; nothing is modified."
        )

    @property
    def TOOLS_DIFF_PICK(self) -> str:
        return _("Choose project to compare...")

    @property
    def TOOLS_DIFF_PICK_TITLE(self) -> str:
        return _("Choose project to compare")

    @property
    def TOOLS_DIFF_FILTER(self) -> str:
        return _("KNX project (*.knxproj *.xknx)")

    @property
    def TOOLS_DIFF_COMPARED_WITH(self) -> str:
        return _("compared with {name}")

    @property
    def TOOLS_DIFF_IDENTICAL(self) -> str:
        return _("No differences in devices or group addresses.")

    @property
    def TOOLS_DIFF_COUNT(self) -> str:
        return _("{count} difference(s)")

    @property
    def TOOLS_DIFF_DEVICES(self) -> str:
        return _("Devices")

    @property
    def TOOLS_DIFF_GAS(self) -> str:
        return _("Group addresses")

    @property
    def TOOLS_DIFF_KIND(self) -> dict[str, str]:
        return {
            "added": _("added"),
            "removed": _("removed"),
            "changed": _("changed"),
        }

    @property
    def TOOLS_COPY_DESC(self) -> str:
        return _(
            "Duplicate a device several times. Optionally rewrite the copy name "
            "(find/replace) and create a group address for each of its objects."
        )

    @property
    def TOOLS_SHIFT_DESC(self) -> str:
        return _(
            "Renumber devices by adding an offset to the device part of their individual "
            "addresses, e.g. to open gaps for new devices. Group addresses are untouched."
        )

    @property
    def TOOLS_LABELS_DESC(self) -> str:
        return _(
            "Pick the fields and an output: a CSV, printable label sheets (Avery), or a "
            "full-page legend. HTML output opens in your browser to print or save as PDF."
        )

    @property
    def TOOLS_TOPOLOGY_DESC(self) -> str:
        return _(
            "Read-only scan: lists devices with a missing, malformed or duplicate "
            "individual address."
        )

    @property
    def TOOLS_COPY_SOURCE(self) -> str:
        return _("Source Device")

    @property
    def TOOLS_COPY_COUNT(self) -> str:
        return _("Copies")

    @property
    def TOOLS_COPY_FIND(self) -> str:
        return _("Find in Name")

    @property
    def TOOLS_COPY_REPLACE(self) -> str:
        return _("Replace with")

    @property
    def TOOLS_COPY_CREATE_GAS(self) -> str:
        return _("Create group addresses for each copy")

    @property
    def TOOLS_COPY_PREVIEW(self) -> str:
        return _("Copy name: {name}")

    @property
    def TOOLS_COPY_RUN(self) -> str:
        return _("Copy")

    @property
    def TOOLS_COPY_DONE(self) -> str:
        return _("Created {count} copy/copies")

    @property
    def TOOLS_SHIFT_HINT(self) -> str:
        return _(
            "Shifts the device part of each individual address. "
            "Group addresses are not shifted."
        )

    @property
    def TOOLS_SHIFT_OFFSET(self) -> str:
        return _("Offset")

    @property
    def TOOLS_FILTER_HINT(self) -> str:
        return _("Filter devices...")

    @property
    def TOOLS_SELECT_ALL(self) -> str:
        return _("All")

    @property
    def TOOLS_SELECT_NONE(self) -> str:
        return _("None")

    @property
    def TOOLS_SELECTED_COUNT(self) -> str:
        return _("{sel} of {total} selected")

    @property
    def TOOLS_SHIFT_INVALID(self) -> str:
        return _("(out of range)")

    @property
    def TOOLS_SHIFT_COUNT(self) -> str:
        return _("{count} address(es) will change")

    @property
    def TOOLS_SHIFT_RUN(self) -> str:
        return _("Shift Addresses")

    @property
    def TOOLS_SHIFT_DONE(self) -> str:
        return _("Shifted {count} address(es)")

    @property
    def TOOLS_SHIFT_MODE_IA(self) -> str:
        return _("Individual addresses")

    @property
    def TOOLS_SHIFT_MODE_GA(self) -> str:
        return _("Group addresses")

    @property
    def TOOLS_SHIFT_NO_GAS(self) -> str:
        return _("This project has no group addresses.")

    @property
    def TOOLS_SHIFT_GA_TITLE(self) -> str:
        return _("Shift Group Addresses")

    @property
    def TOOLS_SHIFT_GA_DESC(self) -> str:
        return _(
            "Renumber group addresses by adding an offset to their raw value, e.g. to open "
            "gaps. Com-object links move with each address. Nothing is written if any move "
            "would collide or leave the valid range."
        )

    @property
    def TOOLS_SHIFT_GA_HINT(self) -> str:
        return _(
            "The offset is added to the raw 16-bit value, so it may change the main/middle "
            "group as addresses cross a boundary."
        )

    @property
    def TOOLS_SHIFT_GA_RUN(self) -> str:
        return _("Shift Group Addresses")

    @property
    def TOOLS_SHIFT_GA_DONE(self) -> str:
        return _("Shifted {count} group address(es)")

    @property
    def TOOLS_LABELS_EXPORT(self) -> str:
        return _("Export CSV...")

    @property
    def TOOLS_LABELS_DONE(self) -> str:
        return _("Exported to {path}")

    # Labels: field selection (keys match label_render.FIELD_IDS).
    @property
    def TOOLS_LABEL_FIELDS(self) -> dict[str, str]:
        return {
            "ia": _("Address"),
            "name": _("Name"),
            "location": _("Location"),
            "description": _("Description"),
            "order": _("Order Number"),
            "manufacturer": _("Manufacturer"),
            "product": _("Product"),
            "hardware": _("Hardware"),
            "serial": _("Serial Number"),
            "application": _("Application"),
            "gas": _("Group Addresses"),
        }

    @property
    def TOOLS_LABELS_FIELDS_TITLE(self) -> str:
        return _("Fields")

    @property
    def TOOLS_LABELS_OUTPUT(self) -> str:
        return _("Output")

    @property
    def TOOLS_LABELS_MODE_CSV(self) -> str:
        return _("CSV")

    @property
    def TOOLS_LABELS_MODE_SHEET(self) -> str:
        return _("Label Sheet")

    @property
    def TOOLS_LABELS_MODE_LEGEND(self) -> str:
        return _("Legend (Full Page)")

    @property
    def TOOLS_LABELS_SHEET(self) -> str:
        return _("Sheet")

    @property
    def TOOLS_LABELS_SHEET_CUSTOM(self) -> str:
        return _("Custom Grid")

    @property
    def TOOLS_LABELS_GRID_COLS(self) -> str:
        return _("Columns")

    @property
    def TOOLS_LABELS_GRID_ROWS(self) -> str:
        return _("Rows")

    @property
    def TOOLS_LABELS_GRID_LABEL_MM(self) -> str:
        return _("Label W x H (mm)")

    @property
    def TOOLS_LABELS_GRID_MARGIN_MM(self) -> str:
        return _("Margin Top / Left (mm)")

    @property
    def TOOLS_LABELS_GRID_GAP_MM(self) -> str:
        return _("Gap X / Y (mm)")

    @property
    def TOOLS_LABELS_EXPORT_HTML(self) -> str:
        return _("Export HTML...")

    @property
    def TOOLS_LABELS_NO_FIELDS(self) -> str:
        return _("Select at least one field.")

    @property
    def TOOLS_TOPOLOGY_OK(self) -> str:
        return _("No topology issues found.")

    @property
    def TOOLS_TOPOLOGY_COUNT(self) -> str:
        return _("{count} issue(s)")

    @property
    def TOOLS_TOPOLOGY_NAV(self) -> str:
        return _("Click a finding to jump to the device.")

    @property
    def TOOLS_ERRORS(self) -> str:
        return _("{count} error(s)")

    @property
    def SPACES_EMPTY(self) -> str:
        return _("No buildings/rooms in this project")

    @property
    def SPACES_ADD_FUNCTION(self) -> str:
        return _("+ Function")

    @property
    def SPACES_ADD_SPACE(self) -> str:
        return _("+ Space")

    @property
    def SPACES_ADD_SUBSPACE(self) -> str:
        return _("+ Sub-space")

    @property
    def SPACES_SPACE_NEW_TITLE(self) -> str:
        return _("New Space")

    @property
    def SPACES_SPACE_NAME(self) -> str:
        return _("Name")

    @property
    def SPACES_SPACE_TYPE(self) -> str:
        return _("Type")

    @property
    def SPACES_SPACE_CREATE(self) -> str:
        return _("Create")

    @property
    def SPACES_RENAME(self) -> str:
        return _("Rename")

    @property
    def SPACES_DELETE(self) -> str:
        return _("Delete")

    @property
    def SPACES_RENAME_TITLE(self) -> str:
        return _("Rename Space")

    @property
    def SPACES_DELETE_TITLE(self) -> str:
        return _("Delete Space")

    @property
    def SPACES_TYPE(self) -> str:
        return _("Type")

    @property
    def SPACES_MOVE_TO(self) -> str:
        return _("Move to")

    @property
    def SPACES_MOVE_ROOT(self) -> str:
        return _("(Top Level)")

    @property
    def SPACES_DELETE_CONFIRM(self) -> str:
        return _(
            "Delete this space and everything in it? Devices are only unassigned, not deleted."
        )

    @property
    def SPACES_ASSIGN_DEVICE(self) -> str:
        return _("Assign Device")

    @property
    def SPACES_ASSIGN_TITLE(self) -> str:
        return _("Assign a Device to This Space")

    @property
    def SPACES_ASSIGN_EMPTY(self) -> str:
        return _("No unassigned devices")

    @property
    def SPACES_UNASSIGN(self) -> str:
        return _("Remove from Space")

    @property
    def SPACES_UNASSIGNED_TITLE(self) -> str:
        return _("Without Space")

    @property
    def SPACES_TYPE_BUILDING(self) -> str:
        return _("Building")

    @property
    def SPACES_TYPE_BUILDINGPART(self) -> str:
        return _("Building Part")

    @property
    def SPACES_TYPE_FLOOR(self) -> str:
        return _("Floor")

    @property
    def SPACES_TYPE_ROOM(self) -> str:
        return _("Room")

    @property
    def SPACES_TYPE_CORRIDOR(self) -> str:
        return _("Corridor")

    @property
    def SPACES_TYPE_STAIRWAY(self) -> str:
        return _("Stairway")

    @property
    def SPACES_TYPE_DISTRIBUTION(self) -> str:
        return _("Distribution Board")

    @property
    def SPACES_FN_NEW_TITLE(self) -> str:
        return _("New Function")

    @property
    def SPACES_FN_NAME(self) -> str:
        return _("Name")

    @property
    def SPACES_FN_TYPE(self) -> str:
        return _("Type")

    @property
    def SPACES_FN_CREATE(self) -> str:
        return _("Create")

    @property
    def SPACES_FN_RENAME(self) -> str:
        return _("Rename")

    @property
    def SPACES_FN_RENAME_TITLE(self) -> str:
        return _("Rename Function")

    @property
    def SPACES_FN_DELETE(self) -> str:
        return _("Delete")

    @property
    def SPACES_FN_ADD_GA(self) -> str:
        return _("Assign Group Address")

    @property
    def SPACES_FN_ROLE(self) -> str:
        return _("Role (optional)")

    @property
    def SPACES_FN_TYPE_CUSTOM(self) -> str:
        return _("Custom")

    @property
    def SPACES_FN_TYPE_SWITCH(self) -> str:
        return _("Switching (light)")

    @property
    def SPACES_FN_TYPE_DIM(self) -> str:
        return _("Dimming (light)")

    @property
    def SPACES_FN_TYPE_BLIND(self) -> str:
        return _("Blinds / Sun Protection")

    @property
    def SPACES_FN_TYPE_SOCKET(self) -> str:
        return _("Switchable Socket")

    @property
    def PROJECT_INFO_EMPTY(self) -> str:
        return _("No project")

    @property
    def PROJECT_INFO_NAME(self) -> str:
        return _("Name")

    @property
    def PROJECT_INFO_INSTALLATION(self) -> str:
        return _("Installation")

    @property
    def PROJECT_INFO_GA_STYLE(self) -> str:
        return _("Group Address Style")

    @property
    def PROJECT_INFO_CREATED_BY(self) -> str:
        return _("Created by")

    @property
    def PROJECT_INFO_TOOL_VERSION(self) -> str:
        return _("Tool Version")

    @property
    def PROJECT_INFO_SCHEMA_VERSION(self) -> str:
        return _("Schema Version")

    @property
    def PROJECT_INFO_LAST_MODIFIED(self) -> str:
        return _("Last Modified")

    @property
    def PROJECT_INFO_GUID(self) -> str:
        return _("GUID")

    @property
    def PROJECT_INFO_ID(self) -> str:
        return _("Project ID")

    @property
    def PROJECT_INFO_ORIGINAL_ID(self) -> str:
        return _("Original Project ID")

    @property
    def PROJECT_INFO_MASTER_DATA(self) -> str:
        return _("Master Data (Signed)")

    @property
    def PROJECT_INFO_VALIDATION(self) -> str:
        return _("Validation File")

    @property
    def PROJECT_INFO_CERTIFICATE(self) -> str:
        return _("Project Certificate")

    @property
    def PROJECT_INFO_ARTIFACT_NONE(self) -> str:
        return _("none")

    @property
    def PROJECT_INFO_ARTIFACT_PRESENT(self) -> str:
        return _("present ({size} bytes)")

    @property
    def PROJECT_INFO_PASSWORD(self) -> str:
        return _("Password")

    @property
    def PROJECT_INFO_PROTECTED(self) -> str:
        return _("Protected")

    @property
    def PROJECT_INFO_NOT_PROTECTED(self) -> str:
        return _("Not protected")

    @property
    def PROJECT_SET_PASSWORD(self) -> str:
        return _("Set Password")

    @property
    def PROJECT_CHANGE_PASSWORD(self) -> str:
        return _("Change Password")

    @property
    def PROJECT_REMOVE_PASSWORD(self) -> str:
        return _("Remove Password")

    @property
    def PROJECT_PASSWORD_TITLE(self) -> str:
        return _("Project Password")

    @property
    def PROJECT_PASSWORD_PROMPT(self) -> str:
        return _(
            "The project and its exports are protected with this password; opening or importing "
            "them asks for it. A password change cannot be undone."
        )

    @property
    def PROJECT_PASSWORD_REPEAT(self) -> str:
        return _("Repeat Password")

    @property
    def PROJECT_PASSWORD_MISMATCH(self) -> str:
        return _("The passwords do not match.")

    @property
    def PROJECT_PASSWORD_REQUIRED(self) -> str:
        return _(
            "A project with KNX Secure devices needs a password: it protects their keys in the "
            "project and in its exports."
        )

    @property
    def PROJECT_LOG_EMPTY(self) -> str:
        return _("No project log entries")

    @property
    def PROJECT_LOG_COL_DATE(self) -> str:
        return _("Date")

    @property
    def PROJECT_LOG_COL_USER(self) -> str:
        return _("User")

    @property
    def PROJECT_LOG_COL_COMMENT(self) -> str:
        return _("Comment")

    @property
    def PROJECT_LOG_FILTER_HINT(self) -> str:
        return _("Filter log")

    @property
    def PROJECT_LOG_DECRYPT(self) -> str:
        return _("Decrypt Comments")

    @property
    def PROJECT_LOG_EXTRACT(self) -> str:
        return _("Extract from Knx.Ets.Common.dll…")

    @property
    def PROJECT_LOG_DECRYPT_HINT(self) -> str:
        return _(
            "ETS encrypts the log comments. Extract the decryption key from your own ETS "
            "installation's Knx.Ets.Common.dll to read them; nothing is sent anywhere."
        )

    @property
    def PROJECT_LOG_ENCRYPTED(self) -> str:
        return _("Comments are encrypted")

    @property
    def PROJECT_LOG_DECRYPTED(self) -> str:
        return _("Comments decrypted")

    @property
    def PROJECT_LOG_EXTRACTING(self) -> str:
        return _("Extracting key…")

    @property
    def PROJECT_LOG_EXTRACT_DONE(self) -> str:
        return _("Key extracted; comments decrypted")

    @property
    def PROJECT_LOG_NO_BACKEND(self) -> str:
        return _(
            "No .NET runtime found (install the .NET SDK, or run on Windows with ETS)."
        )

    @property
    def PROJECT_LOG_DLL_FILTER(self) -> str:
        return _("KNX Common Assembly")

    @property
    def PROJECT_LOG_ALL_FILES(self) -> str:
        return _("All Files")

    @property
    def PROJECT_LOG_CREDIT(self) -> str:
        return _("Based on OpenKNXproducer")

    @property
    def CONFIGURE_ORDER_NUMBER(self) -> str:
        return _("Order Number")

    @property
    def CONFIGURE_DESCRIPTION(self) -> str:
        return _("Description")

    @property
    def GA_DESCRIPTION(self) -> str:
        return _("Description")

    @property
    def GA_COMMENT(self) -> str:
        return _("Comment")

    @property
    def EDITOR_TAB_PARAMETERS(self) -> str:
        return _("Parameters ({count})")

    @property
    def EDITOR_TAB_GROUP_OBJECTS(self) -> str:
        return _("Group Objects ({count})")

    @property
    def EDITOR_TAB_MODULES(self) -> str:
        return _("Channels ({count})")

    @property
    def EDITOR_TAB_DALI(self) -> str:
        return _("DALI (Bus)")

    @property
    def GA_NO_PROJECT(self) -> str:
        return _("No project")

    @property
    def GA_ASSIGNED_OBJECTS(self) -> str:
        return _("Assigned Objects")

    @property
    def GA_COL_DEVICE(self) -> str:
        return _("Device")

    @property
    def GA_COL_OBJECT(self) -> str:
        return _("Object")

    @property
    def GA_COL_SENDING(self) -> str:
        return _("S")

    @property
    def GA_COL_SENDING_TOOLTIP(self) -> str:
        return _(
            "S = Sending. This object sends its value to the group address; "
            "the others only receive it."
        )

    @property
    def GA_OBJECT_OPEN_TOOLTIP(self) -> str:
        return _("Click to open this device in the editor")

    @property
    def CONFIGURE_NO_DEVICES(self) -> str:
        return _("No devices")

    @property
    def CONFIGURE_NO_APPLICATION(self) -> str:
        return _("This product has no application program.")

    @property
    def CONFIGURE_NAME(self) -> str:
        return _("Name")

    @property
    def CONFIGURE_INDIVIDUAL_ADDRESS(self) -> str:
        return _("Individual Address")

    @property
    def BTN_PROGRAM_DEVICE(self) -> str:
        return _("Program Device")

    @property
    def BTN_EVAL_DEVICE(self) -> str:
        return _("Test Programming")

    @property
    def PROGRAM_CONFIRM_TITLE(self) -> str:
        return _("Program device?")

    @property
    def PROGRAM_CONFIRM_TEXT(self) -> str:
        return _(
            "This writes to the device at {address} (scope: {scope}) and changes its "
            "configuration. Continue?"
        )

    @property
    def CONFIGURE_DOWNLOAD_SCOPE(self) -> str:
        return _("Download")

    @property
    def SCOPE_FULL(self) -> str:
        return _("Full")

    @property
    def SCOPE_APPLICATION(self) -> str:
        return _("Application Program")

    @property
    def SCOPE_UNLOAD(self) -> str:
        return _("Unload")

    @property
    def SCOPE_UNLOAD_ALL(self) -> str:
        return _("Unload Application and Address")

    @property
    def SCOPE_PARAMETERS(self) -> str:
        return _("Partial: Parameters")

    @property
    def SCOPE_GROUP_COMMUNICATION(self) -> str:
        return _("Partial: Group Communication")

    @property
    def SCOPE_INDIVIDUAL_ADDRESS(self) -> str:
        return _("Individual Address")

    @property
    def SCOPE_OVERWRITE_INDIVIDUAL_ADDRESS(self) -> str:
        return _("Overwrite Individual Address")

    @property
    def PROGRAM_ADDRESS_TEXT(self) -> str:
        return _(
            "This writes the individual address {address} to the device whose programming "
            "button is pressed. Continue?"
        )

    @property
    def PROGRAM_OVERWRITE_TEXT(self) -> str:
        return _(
            "This overwrites the individual address of the device at its current address "
            "with {address}. Its programming button does not need to be pressed."
        )

    @property
    def OVERWRITE_CURRENT_ADDRESS(self) -> str:
        return _("Current Individual Address")

    @property
    def OVERWRITE_ADDRESS_INVALID(self) -> str:
        return _("Enter the device's current individual address, e.g. 1.1.5.")

    @property
    def OVERWRITE_ADDRESS_SAME(self) -> str:
        return _("The device already has the individual address {address}.")

    @property
    def SCOPE_INDIVIDUAL_ADDRESS_SERIAL(self) -> str:
        return _("Individual Address via Serial Number")

    @property
    def PROGRAM_ADDRESS_SERIAL_TEXT(self) -> str:
        return _(
            "This writes the individual address {address} to the device with the serial "
            "number {serial}. Its programming button does not need to be pressed."
        )

    @property
    def PROGRAM_USE_SERIAL_NUMBER(self) -> str:
        return _("Use Serial Number")

    @property
    def SERIAL_NUMBER_ALREADY_USED(self) -> str:
        return _("The serial number is already associated with device {device}.")

    @property
    def CONFIGURE_SERIAL_NUMBER(self) -> str:
        return _("Serial Number")

    @property
    def SERIAL_NUMBER_INVALID(self) -> str:
        return _(
            "Enter the serial number as 12 hexadecimal digits, e.g. 0083:7AA2039C."
        )

    @property
    def CONFIGURE_MANUFACTURER(self) -> str:
        return _("Manufacturer")

    @property
    def CONFIGURE_MANUFACTURER_INFO(self) -> str:
        return _("Manufacturer Info")

    @property
    def CONFIGURE_APPLICATION(self) -> str:
        return _("Application")

    @property
    def CONFIGURE_APP_VERSION(self) -> str:
        return _("Application Version")

    @property
    def BTN_READ_DEVICE_INFO(self) -> str:
        return _("Read from Device")

    @property
    def BTN_RESTART_DEVICE(self) -> str:
        return _("Restart")

    @property
    def BTN_RESET_DEVICE(self) -> str:
        return _("Reset...")

    @property
    def RESET_POPUP_TITLE(self) -> str:
        return _("Master Reset")

    @property
    def RESET_WARNING(self) -> str:
        return _(
            "Destructive: the device is reset over the bus and must be re-commissioned with "
            "ETS. In KNX Secure mode this deactivates device security."
        )

    @property
    def RESET_TYPE(self) -> str:
        return _("Reset Type")

    @property
    def RESET_CONFIRM(self) -> str:
        return _("I understand this resets the device")

    @property
    def RESET_EXECUTE(self) -> str:
        return _("Reset Device")

    @property
    def RESET_FACTORY(self) -> str:
        return _("Factory Reset")

    @property
    def RESET_FACTORY_KEEP_IA(self) -> str:
        return _("Factory Reset (Keep Address)")

    @property
    def RESET_IA(self) -> str:
        return _("Reset Individual Address")

    @property
    def RESET_AP(self) -> str:
        return _("Delete Application Program")

    @property
    def RESET_PARAM(self) -> str:
        return _("Reset Parameters")

    @property
    def RESET_LINKS(self) -> str:
        return _("Reset Group Address Links")

    @property
    def RESET_CONFIRMED_RESTART(self) -> str:
        return _("Confirmed Restart")

    @property
    def READOUT_NEEDS_ADDRESS(self) -> str:
        return _("Set an individual address and connect to the bus first")

    @property
    def PROGRAM_COMPARE_MATCHES(self) -> str:
        return _("The reported value matches {devices} in this project.")

    @property
    def READOUT_TITLE(self) -> str:
        return _("From Device (Live)")

    @property
    def READOUT_GROUP_OBJECTS(self) -> str:
        return _("Group objects read from the device")

    @property
    def READOUT_GROUP_ADDRESSES(self) -> str:
        return _("Group Addresses")

    @property
    def READOUT_MASK(self) -> str:
        return _("Mask Version")

    @property
    def READOUT_SERIAL(self) -> str:
        return _("Serial Number")

    @property
    def READOUT_ORDER(self) -> str:
        return _("Order Info")

    @property
    def READOUT_HARDWARE(self) -> str:
        return _("Hardware Type")

    @property
    def READOUT_STATUS(self) -> str:
        return _("Status")

    @property
    def READOUT_STATUS_OK(self) -> str:
        return _("no fault reported")

    @property
    def READOUT_PROG_MODE(self) -> str:
        return _("programming mode active")

    @property
    def READOUT_VERSION_MISMATCH(self) -> str:
        return _("differs from project V{version} — program to apply")

    @property
    def CONFIGURE_SCHEMA_VERSION(self) -> str:
        return _("Schema Version")

    @property
    def CONFIGURE_PRODUCT(self) -> str:
        return _("Product")

    @property
    def CONFIGURE_PRODUCT_REF(self) -> str:
        return _("Product Ref")

    @property
    def CONFIGURE_PROGRAM_REF(self) -> str:
        return _("Program Ref")

    @property
    def CONFIGURE_IP_CONFIG(self) -> str:
        return _("IP Configuration")

    @property
    def CONFIGURE_IP_ASSIGN(self) -> str:
        return _("Assignment")

    @property
    def CONFIGURE_IP_ADDRESS(self) -> str:
        return _("IP Address")

    @property
    def CONFIGURE_IP_SUBNET(self) -> str:
        return _("Subnet Mask")

    @property
    def CONFIGURE_IP_GATEWAY(self) -> str:
        return _("Default Gateway")

    @property
    def CONFIGURE_IP_MAC(self) -> str:
        return _("MAC Address")

    @property
    def CONFIGURE_OPEN_MANUAL(self) -> str:
        return _("Download PDF Manual")

    @property
    def CONFIGURE_OPEN_MANUAL_BUSY(self) -> str:
        return _("Searching manual...")

    @property
    def CONFIGURE_OPEN_MANUAL_HINT(self) -> str:
        return _(
            "Find this device's manual (via the KNX device database, else a manufacturer search) "
            "and open it in your browser. Best effort - opens the KNX device search if no direct "
            "document is found."
        )

    @property
    def CONFIGURE_UPDATE_BUTTON(self) -> str:
        return _("Update to V{version}")

    @property
    def CHANGE_APPLICATION(self) -> str:
        return _("Change Application Program")

    @property
    def UPDATE_APPLICATION_VERSION(self) -> str:
        return _("Update Application Program Version")

    @property
    def BTN_UPDATE(self) -> str:
        return _("Update")

    @property
    def UPDATING_APPLICATION(self) -> str:
        return _("Updating the application program...")

    @property
    def CHANGING_APPLICATION(self) -> str:
        return _("Changing the application program...")

    @property
    def CHANGE_APP_FAILED(self) -> str:
        return _("Changing the application program failed")

    @property
    def UPDATE_APP_FAILED(self) -> str:
        return _("Updating the application program failed")

    @property
    def CONFIGURE_COPIED(self) -> str:
        return _("Copied")

    @property
    def CONFIGURE_COPY_HINT(self) -> str:
        return _("Click to copy")

    @property
    def CONFIGURE_PARAM_FILTER_HINT(self) -> str:
        return _("Filter parameters...")

    @property
    def CONFIGURE_APPLY_ALL(self) -> str:
        return _("Apply changes to all {count} identical devices")

    @property
    def CONFIGURE_APPLY_ALL_CHANNELS(self) -> str:
        return _("Apply changes to all channels")

    @property
    def CONFIGURE_MULTI_EDIT(self) -> str:
        return _(
            "Editing {count} devices — edits apply to all, diverging values show <differs>"
        )

    @property
    def CONFIGURE_HARDWARE(self) -> str:
        return _("Hardware")

    @property
    def CONFIGURE_FIRMWARE(self) -> str:
        return _("Firmware")

    @property
    def CONFIGURE_PARAMETERS(self) -> str:
        return _("Parameters ({count})")

    @property
    def CONFIGURE_COM_FLAGS(self) -> str:
        return _("Com Flags ({count})")

    @property
    def CONFIGURE_LOAD_PROCEDURES(self) -> str:
        return _("Load Procedures ({count})")

    @property
    def BTN_PREVIEW_MEMORY(self) -> str:
        return _("Preview Memory")

    @property
    def CONFIGURE_MEMORY_PREVIEW(self) -> str:
        return _("Memory Preview")

    @property
    def MEMORY_LOAD_REF_TITLE(self) -> str:
        return _("Load Reference File")

    @property
    def MEMORY_SAVE_DUMP_TITLE(self) -> str:
        return _("Save Memory Dump")

    @property
    def MEMORY_SAVE(self) -> str:
        return _("Save")

    @property
    def MEMORY_DIFF(self) -> str:
        return _("Diff")

    @property
    def MEMORY_CLEAR_DIFF(self) -> str:
        return _("Clear Diff")

    @property
    def MEMORY_FILE_FILTER(self) -> str:
        return _("Memory Dumps (*.bin)")

    @property
    def MEMORY_ALL_FILES(self) -> str:
        return _("All Files")

    @property
    def MEMORY_BYTES(self) -> str:
        return _("{count} bytes")

    @property
    def MEMORY_NO_SEGMENTS(self) -> str:
        return _("No memory segments.")

    @property
    def MEMORY_ENCODE_FAILED(self) -> str:
        return _("Cannot encode memory image: {error}")

    @property
    def MEMORY_FILE_FAILED(self) -> str:
        return _("The file could not be read or written: {error}")

    @property
    def PREFLIGHT_RESULT_TITLE(self) -> str:
        return _("Programming Test Result")

    @property
    def PREFLIGHT_FAILED(self) -> str:
        return _("Evaluation failed")

    @property
    def PREFLIGHT_NO_CHANGES_MADE(self) -> str:
        return _(
            "Read-only test: checks that the download function generates the same image "
            "as what is already programmed on the device. Nothing is written to the device."
        )

    @property
    def PREFLIGHT_PROJECT_MODIFIED(self) -> str:
        return _(
            "Cannot run: the project was changed since it was opened. This test compares the "
            "generated image against the device's programmed state, which only matches the "
            "project as it was opened. Undo your changes in this session to test."
        )

    @property
    def PREFLIGHT_NO_CONNECTION(self) -> str:
        return _(
            "Cannot run: no KNX connection. Connect to a gateway (Connection menu) "
            "and make sure the device is reachable at its individual address."
        )

    @property
    def PREFLIGHT_MATCH(self) -> str:
        return _("Match: the generated image is identical to what is on the device")

    @property
    def PREFLIGHT_WOULD_CHANGE(self) -> str:
        return _(
            "Mismatch: {bytes} byte(s) differ from the device in {locations} location(s)"
        )

    @property
    def PREFLIGHT_SUMMARY_COUNTS(self) -> str:
        return _("{matched} matched, {changed} would change")

    @property
    def PREFLIGHT_ETS6_NOTE(self) -> str:
        return _(
            "Only for ETS 6 devices: this test compares the device against an image "
            "generated the ETS 6 way. A device last programmed with ETS 5 uses a "
            "different byte layout and will show large false differences - the result "
            "is only meaningful for a device last programmed with ETS 6."
        )

    @property
    def PREFLIGHT_ETS5_HINT(self) -> str:
        return _(
            "Structural differences (whole tables differ) can mean the device was "
            "last programmed with ETS 5, whose byte layout differs. Differences in "
            "values only usually mean the device holds a different configuration."
        )

    @property
    def PREFLIGHT_COL_LOCATION(self) -> str:
        return _("Location")

    @property
    def PREFLIGHT_COL_SIZE(self) -> str:
        return _("Size")

    @property
    def PREFLIGHT_COL_STATUS(self) -> str:
        return _("Status")

    @property
    def PREFLIGHT_COL_CHANGED(self) -> str:
        return _("Changed")

    @property
    def PREFLIGHT_STATUS_MATCH(self) -> str:
        return _("match")

    @property
    def PREFLIGHT_STATUS_CHANGE(self) -> str:
        return _("would change")

    @property
    def PREFLIGHT_STATUS_RUNTIME(self) -> str:
        return _("device-managed")

    @property
    def PREFLIGHT_RUNTIME_TOOLTIP(self) -> str:
        return _(
            "System byte the device sets itself at runtime (e.g. a download "
            "detection byte reset by the application after a download). A "
            "difference here is expected and does not indicate a bad download."
        )

    @property
    def PREFLIGHT_RUNTIME_NOTE(self) -> str:
        return _("plus {bytes} device-managed runtime byte(s) (expected, benign)")

    @property
    def PREFLIGHT_ONLY_BENIGN(self) -> str:
        return _(
            "No configured differences: the device already carries this "
            "configuration. The differences below are benign (see notes)."
        )

    @property
    def PREFLIGHT_STATUS_DEFAULT(self) -> str:
        return _("sets default")

    @property
    def PREFLIGHT_DEFAULT_TOOLTIP(self) -> str:
        return _(
            "This byte is written for a neighbouring parameter; the differing bit is "
            "not driven by any active parameter and is only reset to the application "
            "default. The device holds an older value (e.g. from a previous "
            "configuration or mode) that programming will normalise. Benign."
        )

    @property
    def PREFLIGHT_DEFAULT_NOTE(self) -> str:
        return _(
            "plus {bytes} byte(s) reset to the application default (the device holds "
            "an older value; benign, will be normalised on programming)"
        )

    @property
    def PREFLIGHT_EXPORT(self) -> str:
        return _("Export Ist/Soll...")

    @property
    def PREFLIGHT_EXPORT_PATH(self) -> str:
        return _("Export Path:")

    @property
    def PREFLIGHT_EXPORT_TITLE(self) -> str:
        return _("Export Programming Test Result")

    @property
    def PREFLIGHT_MEM_LABEL(self) -> str:
        return _("memory {address}")

    @property
    def PREFLIGHT_PROP_LABEL(self) -> str:
        return _("object {object} property {property}")

    @property
    def DEVICE_FILTER_HINT(self) -> str:
        return _("Filter devices (name, address)...")

    @property
    def DEVICE_EMPTY_HINT(self) -> str:
        return _(
            "No devices. Open or import a project (File menu), "
            "or right-click here to add an area."
        )

    @property
    def GA_FILTER_HINT(self) -> str:
        return _("Filter group addresses (address, name)...")

    @property
    def SPACES_FILTER_HINT(self) -> str:
        return _("Filter rooms, devices, functions...")

    @property
    def DEVICE_AREA(self) -> str:
        return _("Area {area}")

    @property
    def DEVICE_AREA_NAMED(self) -> str:
        return _("{name} (Area {area})")

    @property
    def DEVICE_LINE(self) -> str:
        return _("Line {area}.{line}")

    @property
    def DEVICE_LINE_NAMED(self) -> str:
        return _("{name} (Line {area}.{line})")

    @property
    def DEVICE_UNASSIGNED(self) -> str:
        return _("Unassigned ({count})")

    @property
    def CONTEXT_ADD_AREA(self) -> str:
        return _("Add Area")

    @property
    def CONTEXT_ADD_LINE(self) -> str:
        return _("Add Line")

    @property
    def CONTEXT_RENAME(self) -> str:
        return _("Rename")

    @property
    def CONTEXT_DELETE(self) -> str:
        return _("Delete")

    @property
    def CONTEXT_COPY_ADDRESS(self) -> str:
        return _("Copy Address")

    @property
    def CONTEXT_DUPLICATE(self) -> str:
        return _("Duplicate")

    @property
    def CONTEXT_COPY_CONFIG(self) -> str:
        return _("Copy Configuration")

    @property
    def CONTEXT_PASTE_CONFIG(self) -> str:
        return _("Paste Configuration")

    @property
    def CONFIG_DIALOG_TITLE(self) -> str:
        return _("Copy Configuration")

    @property
    def CONFIG_PASTE_INTO(self) -> str:
        return _("Paste configuration onto '{name}':")

    @property
    def CONFIG_PASTE_INTO_MULTI(self) -> str:
        return _("Paste configuration onto {count} selected devices:")

    @property
    def CONFIG_DUPLICATE_OF(self) -> str:
        return _("Duplicate '{name}':")

    @property
    def CONFIG_INCLUDE_PARAMS(self) -> str:
        return _("Parameters")

    @property
    def CONFIG_INCLUDE_LINKS(self) -> str:
        return _("Group address links")

    @property
    def CONFIG_PASTE_BTN(self) -> str:
        return _("Paste")

    @property
    def CONFIG_DUPLICATE_BTN(self) -> str:
        return _("Duplicate")

    @property
    def DEVICE_DELETE_TITLE(self) -> str:
        return _("Delete Device")

    @property
    def DEVICE_DELETE_CONFIRM(self) -> str:
        return _(
            "Delete '{name}'? Its com-objects and group-address links are removed and its "
            "individual address is freed. This can be undone."
        )

    @property
    def POPUP_NEW_AREA(self) -> str:
        return _("New Area")

    @property
    def POPUP_NEW_LINE(self) -> str:
        return _("New Line")

    @property
    def POPUP_RENAME(self) -> str:
        return _("Rename")

    @property
    def POPUP_NUMBER(self) -> str:
        return _("Number")

    @property
    def POPUP_NAME(self) -> str:
        return _("Name")

    @property
    def BTN_ADD(self) -> str:
        return _("Add")

    @property
    def BTN_OK(self) -> str:
        return _("OK")

    @property
    def BTN_SAVE(self) -> str:
        return _("Save")

    @property
    def BTN_LOAD(self) -> str:
        return _("Load")

    @property
    def COPY_LOG(self) -> str:
        return _("Copy Log")

    @property
    def BTN_CANCEL(self) -> str:
        return _("Cancel")

    @property
    def GA_NEW(self) -> str:
        return _("New Group Address")

    @property
    def GA_NEW_MAIN(self) -> str:
        return _("New Main Group")

    @property
    def GA_NEW_MIDDLE(self) -> str:
        return _("New Middle Group")

    @property
    def GA_FOLDER_NEW(self) -> str:
        return _("New Folder")

    @property
    def GA_FOLDER_RENAME(self) -> str:
        return _("Rename Folder")

    @property
    def GA_FOLDER_DELETE(self) -> str:
        return _("Delete Folder")

    @property
    def GA_FOLDER_MAIN_HINT(self) -> str:
        return _("Main group name (the next free number is assigned automatically)")

    @property
    def GA_FOLDER_MIDDLE_HINT(self) -> str:
        return _("Middle group name (the next free number is assigned automatically)")

    @property
    def GA_FOLDER_DELETE_CONFIRM(self) -> str:
        return _(
            "Delete this folder and every group address in it? This can be undone."
        )

    @property
    def GA_RENAME(self) -> str:
        return _("Rename Group Address")

    @property
    def GA_RENUMBER(self) -> str:
        return _("Change Address…")

    @property
    def GA_RENUMBER_PROMPT(self) -> str:
        return _("New group address (links are kept):")

    @property
    def GA_RENUMBER_FAILED(self) -> str:
        return _("Invalid address or already in use.")

    @property
    def GA_SET_DPT(self) -> str:
        return _("Set Datapoint Type")

    @property
    def GA_ADDRESS(self) -> str:
        return _("Address")

    @property
    def GA_DPT_HINT(self) -> str:
        return _("Datapoint type (e.g. DPST-1-1); empty to clear")

    @property
    def STATUS_PROJECT(self) -> str:
        return _("Project: {name}")

    @property
    def STATUS_UNSAVED(self) -> str:
        return _("(unsaved)")

    @property
    def HISTORY_NO_HISTORY(self) -> str:
        return _("No history")

    @property
    def HISTORY_REVERT(self) -> str:
        return _("Restore")

    @property
    def HISTORY_DEVICE_ADD(self) -> str:
        return _("Add device: {name}")

    @property
    def HISTORY_DEVICE_REMOVE(self) -> str:
        return _("Remove device: {name}")

    @property
    def HISTORY_ADDRESS_CHANGE(self) -> str:
        return _("Address: {old} -> {new}")

    @property
    def HISTORY_NAME_CHANGE(self) -> str:
        return _("Name: {old} -> {new}")

    @property
    def HISTORY_PARAM_CHANGE(self) -> str:
        return _("Parameter: {old} -> {new}")

    @property
    def HISTORY_DPT_CHANGE(self) -> str:
        return _("DPT: {old} -> {new}")

    @property
    def HISTORY_FLAG_CHANGE(self) -> str:
        return _("Flag: {flag} -> {state}")

    @property
    def HISTORY_GA_CREATE(self) -> str:
        return _("Group address {address} created")

    @property
    def HISTORY_GA_REMOVE(self) -> str:
        return _("Group address {address} removed")

    @property
    def HISTORY_GA_RENAME(self) -> str:
        return _("Group address: {old} -> {new}")

    @property
    def HISTORY_CO_LINKED(self) -> str:
        return _("Com object linked to group address")

    @property
    def HISTORY_CO_UNLINKED(self) -> str:
        return _("Com object unlinked from group address")

    @property
    def HISTORY_AREA_CREATE(self) -> str:
        return _("Area {number} created")

    @property
    def HISTORY_AREA_REMOVE(self) -> str:
        return _("Area {number} removed")

    @property
    def HISTORY_AREA_RENAME(self) -> str:
        return _("Area: {old} -> {new}")

    @property
    def HISTORY_LINE_CREATE(self) -> str:
        return _("Line {number} created")

    @property
    def HISTORY_LINE_REMOVE(self) -> str:
        return _("Line {number} removed")

    @property
    def HISTORY_LINE_RENAME(self) -> str:
        return _("Line: {old} -> {new}")

    @property
    def BUTTON_NEEDS_ADDRESS_LOADED(self) -> str:
        return _(
            "This action can only be performed when the individual address of the device is already downloaded."
        )

    @property
    def BUTTON_EXECUTED(self) -> str:
        return _("Button {0} executed. ")

    @property
    def BUTTON_DEVICE_BUSY(self) -> str:
        return _("A parameter script is running for this device.")

    @property
    def BUTTON_NO_VALID_ADDRESS(self) -> str:
        return _("The Device has no valid address or is not linked to an area or line.")

    @property
    def SCRIPT_OPERATION(self) -> str:
        return _("Parameter Script")

    @property
    def SCRIPT_RUNNING(self) -> str:
        return _("Executing Script")

    @property
    def SCRIPT_NOT_RESPONDING(self) -> str:
        return _("Script not responding")

    @property
    def SCRIPT_FORCE_STOP(self) -> str:
        return _("Force Stop")

    @property
    def SCRIPT_ABORTED(self) -> str:
        return _("The script was stopped.")

    @property
    def SCRIPT_CANCELING(self) -> str:
        return _("Canceling…")

    @property
    def SCRIPT_NOT_CONNECTED(self) -> str:
        return _("No connection to the bus.")

    @property
    def VALIDATION_FAILED(self) -> str:
        return _("Parameter value cannot be set, because validation failed.")

    @property
    def PANEL_OPERATIONS(self) -> str:
        return _("Operations")

    @property
    def OPERATIONS_ACTIVE(self) -> str:
        return _("Active")

    @property
    def OPERATIONS_HISTORY(self) -> str:
        return _("History")

    @property
    def OPERATIONS_CANCEL_ALL(self) -> str:
        return _("Cancel All")

    @property
    def OPERATIONS_CLEAR_HISTORY(self) -> str:
        return _("Clear History")

    @property
    def OPERATIONS_NONE(self) -> str:
        return _("No operations.")

    @property
    def OPERATION_DOWNLOAD(self) -> str:
        return _("Download")

    @property
    def DEVICE_INFO(self) -> str:
        return _("Device Info")

    @property
    def DEVICE_INFO_GROUPS(self) -> str:
        return _("Device Info (With Group Communication)")

    @property
    def OPERATION_READING(self) -> str:
        return _("Reading")

    @property
    def DEVICE_INFO_GENERAL(self) -> str:
        return _("General")

    @property
    def DEVICE_INFO_APPLICATION(self) -> str:
        return _("Application Program")

    @property
    def DEVICE_INFO_GROUP_COMMUNICATION(self) -> str:
        return _("Group Communication")

    @property
    def DEVICE_INFO_MASK(self) -> str:
        return _("Mask Version")

    @property
    def DEVICE_INFO_ADDRESS(self) -> str:
        return _("Individual Address")

    @property
    def DEVICE_INFO_MANUFACTURER(self) -> str:
        return _("Device Manufacturer")

    @property
    def DEVICE_INFO_ORDER(self) -> str:
        return _("Order Number")

    @property
    def DEVICE_INFO_SERIAL(self) -> str:
        return _("Serial Number")

    @property
    def DEVICE_INFO_HARDWARE(self) -> str:
        return _("Hardware Type")

    @property
    def DEVICE_INFO_FIRMWARE(self) -> str:
        return _("Firmware Version")

    @property
    def DEVICE_INFO_PROGMODE(self) -> str:
        return _("Programming Mode")

    @property
    def DEVICE_INFO_LOAD_STATE(self) -> str:
        return _("Load State")

    @property
    def DEVICE_INFO_ADDRESS_TABLE(self) -> str:
        return _("Address Table Load State")

    @property
    def DEVICE_INFO_ASSOCIATION_TABLE(self) -> str:
        return _("Association Table Load State")

    @property
    def DEVICE_INFO_GROUP_OBJECT_TABLE(self) -> str:
        return _("Group Object Table Load State")

    @property
    def DEVICE_INFO_TABLES_FAILED(self) -> str:
        return _("Reading the tables failed: {error}")

    @property
    def DEVICE_INFO_ON(self) -> str:
        return _("On")

    @property
    def DEVICE_INFO_OFF(self) -> str:
        return _("Off")

    @property
    def LOAD_STATE_UNLOADED(self) -> str:
        return _("Unloaded")

    @property
    def LOAD_STATE_LOADED(self) -> str:
        return _("Loaded")

    @property
    def LOAD_STATE_LOADING(self) -> str:
        return _("Loading")

    @property
    def LOAD_STATE_ERROR(self) -> str:
        return _("Error")

    @property
    def LOAD_STATE_UNLOADING(self) -> str:
        return _("Unloading")

    @property
    def LOAD_STATE_LOAD_COMPLETING(self) -> str:
        return _("Load Completing")

    @property
    def BTN_YES(self) -> str:
        return _("Yes")

    @property
    def BTN_NO(self) -> str:
        return _("No")

    @property
    def READ_INCLUDE_GROUP_COMMUNICATION(self) -> str:
        return _("Also read the group communication?")

    @property
    def DEVICE_INFO_APPLICATION_UNKNOWN(self) -> str:
        return _(
            "The application on the device is not in the catalog, so its tables cannot be located"
        )

    @property
    def PLUGIN_DOWNLOAD_REQUIRED(self) -> str:
        return _(
            "This device can only be programmed with the manufacturer's plugin, which is not available here."
        )

    @property
    def PLUGIN_USER_INTERFACE(self) -> str:
        return _(
            "The manufacturer configures this device with its own plugin dialog; settings made there are not shown here."
        )

    @property
    def PLUGIN_DOWNLOAD(self) -> str:
        return _(
            "The manufacturer programs this device with its own plugin; the standard download is used here."
        )

    @property
    def PLUGIN_DATA_HANDLER(self) -> str:
        return _(
            "The manufacturer keeps part of this device's data with its own plugin, which is not available here."
        )

    @property
    def PLUGIN_EXTERNAL_SOFTWARE(self) -> str:
        return _(
            "This device is also configured with separate software from the manufacturer."
        )

    @property
    def PLUGIN_CONFIGURATION_APP(self) -> str:
        return _(
            "This device includes a manufacturer configuration app (“{name}”) that XKNX "
            "Editor can't run. The device's standard parameters can still be programmed, "
            "but not the extra functionality that app provides."
        )

    @property
    def PLUGIN_CONFIGURATION_APP_PORTED(self) -> str:
        return _(
            "This device includes a manufacturer configuration app (“{name}”) that can't "
            "be run directly in XKNX Editor — but it has been ported, so its most important "
            "functions are usable here (see the DALI tab)."
        )

    @property
    def PLUGIN_DOWNLOAD_WARNING(self) -> str:
        return _(
            "The manufacturer allows programming this device only with its plugin, which is not available here. Programming it without the plugin may leave the device misconfigured or in an error state."
        )

    @property
    def PLUGIN_DOWNLOAD_CONFIRM(self) -> str:
        return _("Program without the manufacturer's plugin anyway")

    @property
    def OPERATION_DOWNLOADING(self) -> str:
        return _("Downloading")

    @property
    def OPERATION_PRESS_PROGRAMMING_BUTTON(self) -> str:
        return _("Please press the programming button")

    @property
    def OPERATION_CONFIRM(self) -> str:
        return _("Waiting for confirmation")

    @property
    def FULL_DOWNLOAD_DIFFERENT(self) -> str:
        return _(
            "The application program currently loaded in the device is different from "
            "the projected application program"
        )

    @property
    def FULL_DOWNLOAD_QUESTION(self) -> str:
        return _(
            "Do you want to overwrite the application program (this may take several "
            "minutes)?"
        )

    @property
    def DIFFERENT_PROGRAM_LOADED(self) -> str:
        return _(
            "The application program currently loaded in the device does not comply "
            "with the projected application program. You may try a full instead of a "
            "partial download."
        )

    @property
    def INCOMPATIBLE_MASK_VERSION(self) -> str:
        return _("Incompatible mask version: {found} instead of {expected}")

    @property
    def OPERATION_WAITING(self) -> str:
        return _("Waiting")

    @property
    def OPERATION_FINISHED(self) -> str:
        return _("Finished")

    @property
    def OPERATION_CANCELED(self) -> str:
        return _("Canceled")

    @property
    def OPERATION_FAILED(self) -> str:
        return _("failed")

    @property
    def OPERATION_NO_DETAILS(self) -> str:
        return _("No details available")

    @property
    def OPERATION_CONNECTION(self) -> str:
        return _("Connection: {connection}")

    @property
    def OPERATION_START(self) -> str:
        return _("Start: {time}")

    @property
    def OPERATION_END(self) -> str:
        return _("End: {time}")

    @property
    def CONFIGURE_HELP(self) -> str:
        return _("Help")

    @property
    def DEVICE_PRODUCT_DATA_MISSING(self) -> str:
        return _(
            "The product data of this device is missing - its application program is not in the catalog. Import its product to edit the device."
        )

    @property
    def CONFIGURE_RAIL(self) -> str:
        return _("DIN Rail Mounting")

    @property
    def CONFIGURE_BUS_CURRENT(self) -> str:
        return _("Bus Current")

    @property
    def GA_READ_VALUE(self) -> str:
        return _("Read Value")

    @property
    def GA_WRITE_VALUE(self) -> str:
        return _("Write Value…")

    @property
    def GA_WRITE_VALUE_TITLE(self) -> str:
        return _("Write Value")

    @property
    def GA_WRITE_ADDRESS(self) -> str:
        return _("Group Address")

    @property
    def GA_WRITE_DPT(self) -> str:
        return _("Data Type")

    @property
    def GA_WRITE_VALUE_LABEL(self) -> str:
        return _("Value")

    @property
    def GA_WRITE_NO_DPT(self) -> str:
        return _("Set the data type of the group address to write a value.")

    @property
    def GA_WRITE_BUTTON(self) -> str:
        return _("Write")

    @property
    def GA_NOT_CONNECTED(self) -> str:
        return _("Not connected to the bus.")

    @property
    def GA_EXPORT_MENU(self) -> str:
        return _("Export Group Addresses…")

    @property
    def GA_IMPORT_MENU(self) -> str:
        return _("Import Group Addresses…")

    @property
    def GA_IMPORT_TITLE(self) -> str:
        return _("Import Group Addresses")

    @property
    def GA_IMPORT_FILTER(self) -> str:
        return _("Group Address Files (XML, CSV)")

    @property
    def GA_IMPORT_ALL_FILES(self) -> str:
        return _("All Files")

    @property
    def GA_IMPORT_DONE(self) -> str:
        return _("{added} group addresses added, {updated} updated.")

    @property
    def GA_IMPORT_SKIPPED(self) -> str:
        return _("{count} group addresses could not be placed in a group.")

    @property
    def GA_IMPORT_FAILED(self) -> str:
        return _("The file could not be imported: {error}")

    @property
    def GA_IMPORT_STYLE_MISMATCH(self) -> str:
        return _(
            "The file uses {file} group addresses, but the project uses {project} group addresses."
        )

    @property
    def GA_STYLE_THREE_LEVEL(self) -> str:
        return _("3-level")

    @property
    def GA_STYLE_TWO_LEVEL(self) -> str:
        return _("2-level")

    @property
    def GA_STYLE_FREE(self) -> str:
        return _("free")

    @property
    def GA_EXPORT_BUTTON(self) -> str:
        return _("Export")

    @property
    def GA_IMPORT_BUTTON(self) -> str:
        return _("Import")


S = ProjectStrings()
