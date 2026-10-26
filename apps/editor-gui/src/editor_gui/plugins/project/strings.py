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
    def PANEL_COMMENT(self) -> str:
        return _("Comment")

    @property
    def PANEL_AREA(self) -> str:
        return _("Area")

    @property
    def PANEL_LINE(self) -> str:
        return _("Line")

    @property
    def PANEL_SEGMENT(self) -> str:
        return _("Segment")

    @property
    def PANEL_BACKBONE(self) -> str:
        return _("Backbone")

    @property
    def BACKBONE_MEDIUM(self) -> str:
        return _("Backbone Medium")

    @property
    def NETWORK_LATENCY(self) -> str:
        return _("Network Latency")

    @property
    def LATENCY_LAN(self) -> str:
        return _("LAN (< 400ms)")

    @property
    def LATENCY_WLAN(self) -> str:
        return _("WLAN (< 1s)")

    @property
    def LATENCY_WAN(self) -> str:
        return _("WAN (< 2s)")

    @property
    def LATENCY_MOBILE(self) -> str:
        return _("Mobile (< 4s)")

    @property
    def LATENCY_SATELLITE(self) -> str:
        return _("Satellite (< 8s)")

    @property
    def LATENCY_INVALID(self) -> str:
        return _(
            "The network latency must be a number of milliseconds from 1 to 65535."
        )

    @property
    def MULTICAST_ADDRESS(self) -> str:
        return _("Multicast Address")

    @property
    def MULTICAST_ADDRESS_INVALID(self) -> str:
        return _("Enter a multicast address from 224.0.0.0 to 239.255.255.255.")

    @property
    def BUS_CONNECTION(self) -> str:
        return _("Bus Connection")

    @property
    def BUS_CONNECTION_AUTOMATIC(self) -> str:
        return _("Automatic")

    @property
    def MEDIUM(self) -> str:
        return _("Medium")

    @property
    def DOMAIN_ADDRESS(self) -> str:
        return _("Domain Address")

    @property
    def SEGMENT_DEFAULT_NAME(self) -> str:
        # The name a new segment gets.
        return _("segment")

    @property
    def MEDIUM_ERROR_SEGMENT_HAS_DEVICES(self) -> str:
        return _("The medium of a segment can only change while it holds no device.")

    @property
    def MEDIUM_ERROR_COUPLER_BELOW(self) -> str:
        return _(
            "As long as a coupler exists at a sub line or sub segment, a change of the"
            " medium type is not possible."
        )

    @property
    def MEDIUM_ERROR_MAIN_LINE_RF(self) -> str:
        return _("A main line of a project cannot have medium type RF.")

    @property
    def MEDIUM_ERROR_BACKBONE_IP_OR_TP(self) -> str:
        return _(
            "The operation could not be executed, because as medium type of the backbone"
            " only IP and TP are allowed."
        )

    @property
    def MEDIUM_ERROR_SUB_SEGMENT_IP_OR_PL(self) -> str:
        return _("A sub-segment of KNX medium type IP or PL is not allowed.")

    @property
    def MEDIUM_ERROR_SUB_SEGMENT_IOT(self) -> str:
        return _(
            "The medium type of the segment '{segment}' cannot be changed to IoT,"
            " because its parent line '{line}' is not of medium type IP."
        )

    @property
    def MEDIUM_ERROR_MAIN_LINE_PL_BELOW(self) -> str:
        return _(
            "The medium type of the main line '{line}' can no longer be changed to PL,"
            " because it already contains lines or segments that are not allowed in PL"
            " main lines."
        )

    @property
    def MEDIUM_ERROR_MAIN_LINE_IOT_BACKBONE(self) -> str:
        return _(
            "The medium type of the main line '{line}' cannot be changed to IoT, because"
            " the backbone line '{backbone}' is not of medium type IP."
        )

    @property
    def MEDIUM_ERROR_MAIN_LINE_IOT_BELOW(self) -> str:
        return _(
            "The medium type of the main line '{line}' can no longer be changed to IoT,"
            " because it already contains lines or segments that are not allowed in IoT"
            " main lines."
        )

    @property
    def MEDIUM_ERROR_IOT_BELOW(self) -> str:
        return _(
            "The medium type of the line '{line}' cannot be changed, because it already"
            " contains lines or segments that require an IoT or IP main line."
        )

    @property
    def MEDIUM_ERROR_RF_NO_SUB_SEGMENTS(self) -> str:
        return _(
            "A sub-segment is not allowed below a main segment of KNX medium type RF."
        )

    @property
    def MEDIUM_ERROR_PL_NO_SUB_SEGMENTS(self) -> str:
        return _(
            "A sub-segment is not allowed below a main segment of KNX medium type PL."
        )

    @property
    def MEDIUM_ERROR_IOT_NO_SUB_SEGMENTS(self) -> str:
        return _(
            "A sub-segment of KNX medium type IoT is not allowed below a main segment of"
            " the same type."
        )

    @property
    def MEDIUM_ERROR_RF_MAIN_LINE(self) -> str:
        return _(
            "The operation could not be executed, because a RF main line '{line}'"
            " requires all other lines in the area to be RF lines as well."
        )

    @property
    def MEDIUM_ERROR_PL_MAIN_LINE(self) -> str:
        return _(
            "The operation could not be executed, because a PL main line '{line}'"
            " requires all other lines in the area to be PL lines as well."
        )

    @property
    def MEDIUM_ERROR_IOT_MAIN_LINE(self) -> str:
        return _(
            "The operation could not be executed, because a IoT main line '{line}'"
            " requires all other lines in the area to be IoT lines as well."
        )

    @property
    def MEDIUM_ERROR_SUB_LINE_IOT(self) -> str:
        return _(
            "The medium type of the line '{line}' cannot be changed to IoT, because its"
            " main line '{main}' is not of medium type IP."
        )

    @property
    def MEDIUM_ERROR_SUB_LINE_IOT_BELOW(self) -> str:
        return _(
            "The medium type of the line '{line}' can no longer be changed to IoT,"
            " because it already contains segments that are not allowed in IoT lines."
        )

    @property
    def MEDIUM_ERROR_IP_BACKBONE_ROUTERS(self) -> str:
        return _(
            "The operation could not be executed, because the line is the IP backbone"
            " line and the project contains KNXnet/IP routers. KNXnet/IP routers require"
            " an IP backbone line."
        )

    @property
    def MEDIUM_ERROR_IP_MAIN_LINE_ROUTERS(self) -> str:
        return _(
            "The operation could not be executed, because the line is the IP main line"
            " and the project contains KNXnet/IP routers. KNXnet/IP routers require an"
            " IP main line."
        )

    @property
    def MEDIUM_ERROR_IP_BACKBONE_IP_LINES(self) -> str:
        return _(
            "The operation could not be executed, because the line is the IP backbone"
            " line and the project contains IoT or IP lines."
        )

    @property
    def MEDIUM_ERROR_IP_NEEDS_IP_MAIN_LINE(self) -> str:
        return _(
            "The operation could not be executed, because the medium type of the line"
            " can only be set to IP if an IP main line exists."
        )

    @property
    def MEDIUM_ERROR_IP_NEEDS_IP_BACKBONE(self) -> str:
        return _(
            "The operation could not be executed, because the medium type of the line"
            " can only be set to IP if an IP backbone line exists."
        )

    @property
    def MEDIUM_ERROR_BELOW_TP(self) -> str:
        return _(
            "The operation could not be executed, because below a TP main segment only"
            " segments of KNX medium type TP, RF or PL can exist."
        )

    @property
    def MEDIUM_ERROR_BELOW_IP(self) -> str:
        return _(
            "Below a main segment of KNX medium type IP only segments of medium type TP"
            " or RF can exist."
        )

    @property
    def MEDIUM_ERROR_PL_SUB_SEGMENT(self) -> str:
        return _(
            "A sub-segment of KNX medium type PL is not allowed below a main segment."
        )

    @property
    def MEDIUM_ERROR_TOO_MANY_SEGMENTS(self) -> str:
        return _("The maximum number of segments has been reached.")

    @property
    def MEDIUM_ERROR_IP_ROUTER_BACKBONE(self) -> str:
        return _("An IP router requires an IP backbone line.")

    @property
    def MEDIUM_ERROR_COUPLER_MAIN_LINE(self) -> str:
        return _(
            "The operation could not be executed, because a coupler needs a main line."
        )

    @property
    def MEDIUM_ERROR_AREA_COUPLER_BACKBONE(self) -> str:
        return _(
            "The operation could not be executed, because an area coupler requires a "
            "backbone line."
        )

    @property
    def MEDIUM_ERROR_COUPLER_MAIN_LINE_MEDIUM(self) -> str:
        return _(
            "The operation could not be executed, because this coupler needs a main line "
            "with the medium type '{medium}' ."
        )

    @property
    def MEDIUM_ERROR_ONE_WORLD_COUPLER(self) -> str:
        return _(
            "The operation could not be executed, because only one IP router is allowed "
            "as a world coupler."
        )

    @property
    def MEDIUM_ERROR_WORLD_COUPLER_IP_ROUTER(self) -> str:
        return _(
            "The operation could not be executed, because only IP router area allowed as "
            "a world coupler."
        )

    @property
    def MEDIUM_ERROR_WORLD_COUPLER_PRESENT(self) -> str:
        return _(
            "The backbone line contains a world coupler. It is not possible to use an IP "
            "router below another IP router."
        )

    @property
    def MEDIUM_ERROR_WORLD_COUPLER_IP_ROUTERS(self) -> str:
        return _(
            "It is not possible to use a world coupler, when there are already IP routers"
            " in the project."
        )

    @property
    def MEDIUM_ERROR_WORLD_COUPLER_TP_BACKBONE(self) -> str:
        return _(
            "A world coupler can only be used, when the medium type of the backbone is "
            "TP."
        )

    @property
    def MEDIUM_ERROR_COUPLER_NO_SEGMENTS(self) -> str:
        return _(
            "The coupler does not support segment coupling, so the sub-segment must be of"
            " the same KNX medium type as the main segment."
        )

    @property
    def MEDIUM_ERROR_COUPLER_MEDIA_ABOVE(self) -> str:
        return _(
            "The operation could not be executed, because this device couples medium "
            "'{above}' (above) to medium '{below}' (below), but the segment above is "
            "'{segment}'."
        )

    @property
    def MEDIUM_ERROR_COUPLER_SEGMENTS_FULL(self) -> str:
        return _(
            "The operation could not be executed, because medium type of coupler device "
            "matches only sub segment, but maximum amount of segments reached."
        )

    @property
    def MEDIUM_ERROR_COUPLER_MEDIA_BELOW(self) -> str:
        return _(
            "The operation could not be executed, because this device couples medium "
            "'{above}' (above) to medium '{below}' (below), but the medium type of the "
            "lower segment is '{segment}'."
        )

    @property
    def MEDIUM_ERROR_DOMAIN_INVALID(self) -> str:
        return _("The given domain address is invalid.")

    @property
    def MEDIUM_ERROR_PL_DOMAIN_RANGE(self) -> str:
        return _("Domain address is not valid! Valid values are 1 to 255.")

    @property
    def MEDIUM_ERROR_RF_DOMAIN_MINIMUM(self) -> str:
        return _(
            "DomainAddress of mediumType 'RF' is lower than the minimum value of "
            "0001:00000000."
        )

    @property
    def MEDIUM_ERROR_DOMAIN_MAXIMUM(self) -> str:
        return _(
            "DomainAddress of medium type '{medium}' exceeds the maximum valid value of "
            "{maximum}"
        )

    @property
    def MEDIUM_ERROR_RF_SUB_LINE_DOMAIN(self) -> str:
        return _(
            "DomainAddress of RF sublines must be equal to DomainAdress of main line."
        )

    @property
    def MEDIUM_ERROR_IOT_SUB_LINE_DOMAIN(self) -> str:
        return _(
            "Domain Address of IoT sublines must be equal to Domain Address of main line."
        )

    @property
    def MEDIUM_ERROR_RF_DOMAIN_UNIQUE(self) -> str:
        return _("Each line in a project must have a unique domain address.")

    @property
    def DOMAIN_ADDRESS_GENERATE(self) -> str:
        return _("Generate New")

    @property
    def MEDIUM_ERROR_MAIN_SEGMENT(self) -> str:
        return _("The main segment cannot be deleted.")

    @property
    def MEDIUM_ERROR_MAIN_SEGMENT_MOVE(self) -> str:
        return _("The main segment cannot be moved.")

    @property
    def MEDIUM_ERROR_TOO_MANY_LINES(self) -> str:
        return _("The maximum number of lines has been reached.")

    @property
    def MEDIUM_ERROR_LINE_IN_AREA(self) -> str:
        return _("The line is already assigned to an area.")

    @property
    def MEDIUM_ERROR_MAIN_LINE_MOVE(self) -> str:
        return _("The main line cannot be moved.")

    @property
    def MEDIUM_ERROR_DEVICE_LINE(self) -> str:
        return _("Device '{device}' is incompatible with line's medium type.")

    @property
    def MEDIUM_ERROR_DEVICE_SEGMENT(self) -> str:
        return _("Device '{device}' is incompatible with segment's medium type.")

    @property
    def TOPOLOGY_ADDRESS_INVALID(self) -> str:
        return _("The address is a number from 0 to 15.")

    @property
    def TOPOLOGY_ADDRESS_IN_USE(self) -> str:
        return _("Address {address} is already in use.")

    @property
    def HISTORY_EV_SET_AREA_DESCRIPTION(self) -> str:
        return _("Set area description")

    @property
    def HISTORY_EV_SET_LINE_DESCRIPTION(self) -> str:
        return _("Set line description")

    @property
    def HISTORY_EV_SET_AREA_ADDRESS(self) -> str:
        return _("Set area address")

    @property
    def HISTORY_EV_SET_LINE_ADDRESS(self) -> str:
        return _("Set line address")

    @property
    def PANEL_MAIN_GROUP(self) -> str:
        return _("Main Group")

    @property
    def PANEL_MIDDLE_GROUP(self) -> str:
        return _("Middle Group")

    @property
    def PANEL_GROUP_RANGE(self) -> str:
        return _("Group Range")

    @property
    def PANEL_GROUP_ADDRESS(self) -> str:
        return _("Group Address")

    @property
    def PANEL_FUNCTION(self) -> str:
        return _("Function")

    @property
    def PANEL_TRADE(self) -> str:
        return _("Trade")

    @property
    def GROUP_MAIN_NUMBER_INVALID(self) -> str:
        return _("The main group is a number from 0 to 31.")

    @property
    def GROUP_MIDDLE_NUMBER_INVALID(self) -> str:
        return _("The middle group is a number from 0 to 7.")

    @property
    def HISTORY_EV_SET_GA_DESCRIPTION(self) -> str:
        return _("Set group address description")

    @property
    def HISTORY_EV_SET_GROUP_RANGE_DESCRIPTION(self) -> str:
        return _("Set group range description")

    @property
    def HISTORY_EV_SET_GROUP_RANGE_ADDRESS(self) -> str:
        return _("Set group range address")

    @property
    def HISTORY_EV_MOVE_GROUP_RANGE(self) -> str:
        return _("Move middle group")

    @property
    def HISTORY_EV_MOVE_LINE(self) -> str:
        return _("Move line")

    @property
    def HISTORY_EV_MOVE_SEGMENT(self) -> str:
        return _("Move segment")

    @property
    def HISTORY_EV_MOVE_FUNCTION(self) -> str:
        return _("Move function")

    @property
    def GA_MOVE_NO_FREE(self) -> str:
        return _("The group has no free group address.")

    @property
    def GROUP_MOVE_NO_FREE(self) -> str:
        return _("The main group has no free middle group.")

    @property
    def COMMENT_INSTALLATION_HINTS(self) -> str:
        return _("Installation Hints")

    @property
    def COMMENT_NOTHING_SELECTED(self) -> str:
        return _("Select an element in a tree to see its comment.")

    @property
    def PANEL_TOPOLOGY(self) -> str:
        return _("Topology")

    @property
    def DRAG_DEVICES(self) -> str:
        return _("{count} devices")

    @property
    def PASTE_TITLE(self) -> str:
        return _("Paste Special")

    @property
    def CONTEXT_COPY(self) -> str:
        return _("Copy")

    @property
    def CONTEXT_CUT(self) -> str:
        return _("Cut")

    @property
    def PASTE_CANNOT_MOVE(self) -> str:
        return _("What was cut cannot be moved there.")

    @property
    def CONTEXT_PASTE(self) -> str:
        return _("Paste")

    @property
    def CONTEXT_PASTE_SPECIAL(self) -> str:
        return _("Paste Special…")

    @property
    def PASTE_COUNT(self) -> str:
        return _("Number of Items")

    @property
    def PASTE_GA_DONT_COPY(self) -> str:
        return _("Don't copy")

    @property
    def PASTE_GA_KEEP(self) -> str:
        return _("Keep")

    @property
    def PASTE_GA_NEW_MIDDLE(self) -> str:
        return _("Create new group address in middle group")

    @property
    def PASTE_GA_NEW_MAIN(self) -> str:
        return _("Create new group address in main group")

    @property
    def PASTE_GA_NEW_RANGE(self) -> str:
        return _("Create new group address in group range")

    @property
    def PASTE_GENERATE(self) -> str:
        return _("Generate Address")

    @property
    def PASTE_FILL_UP(self) -> str:
        return _("Fill up (use first free)")

    @property
    def PASTE_APPEND(self) -> str:
        return _("Append")

    @property
    def PASTE_START_WITH(self) -> str:
        return _("Start with")

    @property
    def PASTE_OFFSET(self) -> str:
        return _("Offset")

    @property
    def PASTE_NOT_MIDDLE(self) -> str:
        return _("Enter a middle group, e.g. 1/2.")

    @property
    def PASTE_NOT_MAIN(self) -> str:
        return _("Enter a main group, e.g. 1.")

    @property
    def PASTE_NO_GA_ROOM(self) -> str:
        return _("The group has too few free group addresses for the copies.")

    @property
    def PASTE_NO_RANGE_ROOM(self) -> str:
        return _("The maximum number of group ranges has been reached.")

    @property
    def PASTE_NO_TARGET_RANGE(self) -> str:
        return _("There is no group for the new group addresses.")

    @property
    def HISTORY_EV_COPIED(self) -> str:
        return _("Copied '{name}' to '{target}'.")

    @property
    def HISTORY_EV_COPIED_DEVICES(self) -> str:
        return _("Copied {count} devices to '{target}'.")

    @property
    def HISTORY_EV_COPIED_SPACES(self) -> str:
        return _("Copied {count} building parts to '{target}'.")

    @property
    def HISTORY_EV_COPIED_TRADES(self) -> str:
        return _("Copied {count} trades to '{target}'.")

    @property
    def HISTORY_EV_COPIED_FUNCTIONS(self) -> str:
        return _("Copied {count} functions to '{target}'.")

    @property
    def HISTORY_EV_COPIED_GROUP_ADDRESSES(self) -> str:
        return _("Copied {count} group addresses to '{target}'.")

    @property
    def HISTORY_EV_COPIED_MIDDLE_GROUPS(self) -> str:
        return _("Copied {count} middle groups to '{target}'.")

    @property
    def HISTORY_EV_COPIED_MAIN_GROUPS(self) -> str:
        return _("Copied {count} main groups to '{target}'.")

    @property
    def HISTORY_EV_COPIED_LINES(self) -> str:
        return _("Copied {count} lines to '{target}'.")

    @property
    def HISTORY_EV_COPIED_SEGMENTS(self) -> str:
        return _("Copied {count} segments to '{target}'.")

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
        return _("Object <> GA")

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
            "Pairs are matched by list order (1st with 1st, …). Surplus items on the "
            "longer side are ignored. Reorder a list via its right-click menu."
        )

    @property
    def ML_GUIDE_GA_CO(self) -> str:
        return _("Link group objects to existing group addresses.")

    @property
    def ML_GUIDE_GA_CO_1(self) -> str:
        return _('Add objects with "{add_device}" or "{add_objects}".')

    @property
    def ML_GUIDE_GA_CO_2(self) -> str:
        return _(
            'Pick a group address per row, or fill all with "{match}" or "{sequential}".'
        )

    @property
    def ML_GUIDE_GA_CO_3(self) -> str:
        return _('"{link_all}" links every row that has one.')

    @property
    def ML_GUIDE_CO_CO(self) -> str:
        return _(
            "Link objects to each other through a new group address each: the source sends, "
            "the target receives."
        )

    @property
    def ML_GUIDE_CO_CO_1(self) -> str:
        return _('Add the source objects with "{add_device}" or "{add_objects}".')

    @property
    def ML_GUIDE_CO_CO_2(self) -> str:
        return _(
            "Pick a target object per row. Address and name are suggested and can be changed."
        )

    @property
    def ML_GUIDE_CO_CO_3(self) -> str:
        return _('"{link_all}" creates the group addresses and links them.')

    @property
    def ML_LINK_ALL_NAME(self) -> str:
        return _("Link All")

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
        return _("Add Group Objects…")

    @property
    def ML_ADD_GAS(self) -> str:
        return _("Add Group Addresses…")

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
        return _("Filter…")

    @property
    def ML_PICK_OBJECTS_TITLE(self) -> str:
        return _("Add Group Objects")

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
        return _("Group Object")

    @property
    def ML_COL_DPT(self) -> str:
        return _("DPT")

    @property
    def ML_COL_TARGET_GA(self) -> str:
        return _("Target Group Address")

    @property
    def ML_COL_TARGET_CO(self) -> str:
        return _("Target Group Object")

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
    def ML_MATCHING_DPT_ONLY(self) -> str:
        return _("Matching DPT Only")

    @property
    def ML_MATCHING_DPT_TIP(self) -> str:
        return _(
            "Only group addresses with the object's main datapoint type, and those without one "
            "(they take the object's type when linked)."
        )

    @property
    def ML_GA_NO_DPT(self) -> str:
        return _("no DPT")

    @property
    def ML_MATCHING_DPT_OBJECT_TIP(self) -> str:
        return _(
            "Only objects with the same main datapoint type, and those without one."
        )

    @property
    def ML_NOTE_NO_DEVICE(self) -> str:
        return _("Select a device in the tree first.")

    @property
    def ML_NOTE_NO_OBJECTS(self) -> str:
        return _("{device} has no group objects.")

    @property
    def ML_NOTE_ALL_LISTED(self) -> str:
        return _("All objects of {device} are in the list already.")

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
        return _("Only Problems")

    @property
    def ML_STATUS_READY(self) -> str:
        return _("Ready to link.")

    @property
    def ML_STATUS_AMBIGUOUS(self) -> str:
        return _(
            "Check the group address: several match, or a datapoint type is unknown."
        )

    @property
    def ML_STATUS_INCOMPATIBLE(self) -> str:
        return _("The datapoint types do not match; this row is not linked.")

    @property
    def ML_STATUS_UNMATCHED(self) -> str:
        return _("No group address chosen yet.")

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
        return _("Replace with (Template)")

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
        return _("Choose Project to Compare…")

    @property
    def TOOLS_DIFF_PICK_TITLE(self) -> str:
        return _("Choose Project to Compare")

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
    def DEVICE_CERTIFICATE_TITLE(self) -> str:
        return _("Add Device Certificate")

    @property
    def DEVICE_CERTIFICATE_TITLE_STORED(self) -> str:
        return _("Device Certificate")

    @property
    def DEVICE_CERTIFICATE_IN_USE(self) -> str:
        return _(
            "This device already uses its own key from this project. Its certificate is only "
            "needed again after a factory reset."
        )

    @property
    def DEVICE_CERTIFICATE_NEEDED(self) -> str:
        return _(
            "This device still uses its factory key: its first secure download needs its "
            "certificate."
        )

    @property
    def DEVICE_CERTIFICATE_STORED(self) -> str:
        return _("Stored Certificate: {serial}")

    @property
    def DEVICE_CERTIFICATE_ENTER(self) -> str:
        return _("Enter the FDSK from its label or certificate (36 characters):")

    @property
    def DEVICE_CERTIFICATE_REPLACE(self) -> str:
        return _("To replace it, enter the new FDSK (36 characters):")

    @property
    def DEVICE_CERTIFICATE_REMOVE_CONFIRM(self) -> str:
        return _(
            "Remove the stored certificate? After a factory reset, the device can then only "
            "be reached again with the code from its label."
        )

    @property
    def CONFIGURE_DEVICE_CERTIFICATE(self) -> str:
        return _("Device Certificate…")

    @property
    def DEVICE_CERTIFICATE_REQUIRED(self) -> str:
        return _("The device certificate (FDSK) is missing.")

    @property
    def DEVICE_CERTIFICATE_INVALID(self) -> str:
        return _("This is not a valid FDSK; please check it.")

    @property
    def DEVICE_CERTIFICATE_OTHER_DEVICE(self) -> str:
        return _("This certificate belongs to the device with serial number {serial}.")

    @property
    def SECURE_DOWNLOAD_PASSWORD_REQUIRED(self) -> str:
        return _("Set a project password first: it protects the device keys.")

    @property
    def TOOLS_DIFF_PASSWORD_PROMPT(self) -> str:
        return _("The other project is protected with a password. Enter it to compare:")

    @property
    def TOOLS_DIFF_COMPARE(self) -> str:
        return _("Compare")

    @property
    def TOOLS_DIFF_WRONG_PASSWORD(self) -> str:
        return _("Wrong password, please try again.")

    @property
    def TOOLS_DIFF_DEVICES(self) -> str:
        return _("Devices")

    @property
    def TOOLS_DIFF_GAS(self) -> str:
        return _("Group Addresses")

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
        return _("Create Group Addresses for Each Copy")

    @property
    def TOOLS_COPY_PREVIEW(self) -> str:
        return _("Copy Name: {name}")

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
        return _("Filter devices…")

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
        return _("Individual Addresses")

    @property
    def TOOLS_SHIFT_MODE_GA(self) -> str:
        return _("Group Addresses")

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
            "gaps. Group object links move with each address. Nothing is written if any move "
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
        return _("Export CSV…")

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
        return _("Export HTML…")

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
    def SPACES_SPACE_NAME(self) -> str:
        return _("Name")

    @property
    def SPACES_SPACE_CREATE(self) -> str:
        return _("Create")

    @property
    def SPACES_DELETE(self) -> str:
        return _("Delete")

    @property
    def SPACES_DELETE_TITLE(self) -> str:
        return _("Delete Space")

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
        # Takes a device out of its building part.
        return _("Unlink")

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
        # The DistributionBoard type.
        return _("Cabinet")

    @property
    def SPACES_ROOT_BUILDINGS(self) -> str:
        return _("Buildings")

    @property
    def SPACES_ROOT_TRADES(self) -> str:
        return _("Trades")

    @property
    def SPACES_ADD_MENU(self) -> str:
        return _("Add")

    @property
    def SPACES_ADD_BUILDINGS(self) -> str:
        return _("Add Buildings…")

    @property
    def SPACES_ADD_BUILDING_PARTS(self) -> str:
        return _("Building Parts…")

    @property
    def SPACES_ADD_FLOORS(self) -> str:
        return _("Floors…")

    @property
    def SPACES_ADD_STAIRWAYS(self) -> str:
        return _("Stairways…")

    @property
    def SPACES_ADD_ROOMS(self) -> str:
        return _("Rooms…")

    @property
    def SPACES_ADD_CORRIDORS(self) -> str:
        return _("Corridors…")

    @property
    def SPACES_ADD_CABINETS(self) -> str:
        return _("Cabinets…")

    @property
    def SPACES_ADD_DEVICES(self) -> str:
        return _("Devices…")

    @property
    def SPACES_ADD_FUNCTIONS(self) -> str:
        return _("Functions…")

    @property
    def SPACES_TITLE_BUILDINGS(self) -> str:
        return _("Add Buildings")

    @property
    def SPACES_TITLE_BUILDING_PARTS(self) -> str:
        return _("Add Building Parts")

    @property
    def SPACES_TITLE_FLOORS(self) -> str:
        return _("Add Floors")

    @property
    def SPACES_TITLE_STAIRWAYS(self) -> str:
        return _("Add Stairways")

    @property
    def SPACES_TITLE_ROOMS(self) -> str:
        return _("Add Rooms")

    @property
    def SPACES_TITLE_CORRIDORS(self) -> str:
        return _("Add Corridors")

    @property
    def SPACES_TITLE_CABINETS(self) -> str:
        return _("Add Cabinets")

    @property
    def SPACES_COUNT(self) -> str:
        return _("Count")

    @property
    def SPACES_ADD_TO(self) -> str:
        # Below the title of an "Add" dialog: where the new ones go.
        return _('to "{name}"')

    @property
    def ADD_ROW(self) -> str:
        return _("Add Row")

    @property
    def ADD_REMOVE_ROW(self) -> str:
        return _("Remove this row")

    @property
    def ADD_GENERATE(self) -> str:
        return _("Generate Addresses")

    @property
    def SPACES_DEFAULT_BUILDING(self) -> str:
        return _("New building")

    @property
    def SPACES_DEFAULT_BUILDING_PART(self) -> str:
        return _("New building part")

    @property
    def SPACES_DEFAULT_FLOOR(self) -> str:
        return _("New floor")

    @property
    def SPACES_DEFAULT_STAIRWAY(self) -> str:
        return _("New stairway")

    @property
    def SPACES_DEFAULT_ROOM(self) -> str:
        return _("New room")

    @property
    def SPACES_DEFAULT_CORRIDOR(self) -> str:
        return _("New corridor")

    @property
    def SPACES_DEFAULT_CABINET(self) -> str:
        return _("New cabinet")

    @property
    def SPACE_ERROR_TYPE_NOT_HERE(self) -> str:
        return _("A building part of this type cannot be added here.")

    @property
    def SPACE_ERROR_CABINET_NOT_HERE(self) -> str:
        return _(
            "A building part of this type cannot be added here.\nCabinets can be added "
            "inside of rooms, corridors, or stairways only. If applicable the corridors have "
            "to be moved into the corresponding building parts."
        )

    @property
    def SPACE_ERROR_TARGET_IS_CHILD(self) -> str:
        return _("The target is a child building part.")

    @property
    def SPACE_ERROR_NO_DEVICES(self) -> str:
        return _(
            "Devices can only be added to stairways, rooms, corridors and cabinets."
        )

    @property
    def TRADES_ADD(self) -> str:
        return _("Add Trades…")

    @property
    def TRADES_ADD_TRADES(self) -> str:
        return _("Trades…")

    @property
    def TRADES_TITLE(self) -> str:
        return _("Add Trades")

    @property
    def TRADES_DEFAULT(self) -> str:
        return _("New trade")

    @property
    def TRADES_ADD_DEVICES_TITLE(self) -> str:
        return _("Add Devices")

    @property
    def TRADES_REMOVE_DEVICE(self) -> str:
        # Takes a device out of its trade.
        return _("Unlink")

    @property
    def SPACES_TITLE_FUNCTIONS(self) -> str:
        return _("Add Functions")

    @property
    def SPACES_DEFAULT_FUNCTION(self) -> str:
        # The name a new custom function gets when none is entered.
        return _("New function")

    @property
    def SPACES_FN_TYPE(self) -> str:
        return _("Type")

    @property
    def SPACES_FN_DELETE(self) -> str:
        return _("Delete")

    @property
    def SPACES_FN_ADD_GA(self) -> str:
        return _("Assign Group Address")

    @property
    def SPACES_FN_ROLE(self) -> str:
        return _("Role (Optional)")

    @property
    def SPACES_FN_TYPE_CUSTOM(self) -> str:
        return _("Custom")

    @property
    def SPACES_FN_TYPE_SWITCH(self) -> str:
        return _("Switchable Light")

    @property
    def SPACES_FN_TYPE_DIM(self) -> str:
        return _("Dimmable Light")

    @property
    def SPACES_FN_TYPE_SUN_PROTECTION(self) -> str:
        return _("Sun Protection")

    @property
    def SPACES_FN_TYPE_HEATING_SWITCHING(self) -> str:
        return _("Heating (Switching Variable)")

    @property
    def SPACES_FN_TYPE_HEATING_CONTINUOUS(self) -> str:
        return _("Heating (Continuous Variable)")

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
    def EDITOR_TAB_SIEMENS_DALI(self) -> str:
        return _("DALI (Siemens)")

    @property
    def EDITOR_TAB_PLUGIN_PARAMS(self) -> str:
        return _("Plugin Config")

    @property
    def PLUGIN_PARAMS_NO_MAPPING(self) -> str:
        return _(
            "No plugin mapping is available for this device yet. The vendor plugin's "
            "parameters stay unreachable from here."
        )

    @property
    def PLUGIN_PARAMS_EMPTY(self) -> str:
        return _("The application has no static parameters.")

    @property
    def PLUGIN_PARAMS_READONLY_NOTE(self) -> str:
        return _(
            "Read-only view. The hierarchy is a best-effort reconstruction from parameter name "
            "prefixes; names and groupings may be rough. Writing values is not available yet."
        )

    @property
    def PLUGIN_PARAMS_EDIT_NOTE(self) -> str:
        return _(
            "Editable fallback for parameters whose manufacturer plugin is not available here. "
            "The hierarchy is reconstructed from parameter name prefixes; labels and groupings "
            "may be rough. Edits are written to the project and transferred by a normal download."
        )

    @property
    def PLUGIN_PARAMS_FILTER(self) -> str:
        return _("##plugin_params_filter")

    @property
    def PLUGIN_PARAMS_FILTER_EMPTY(self) -> str:
        return _("No parameter matches the filter.")

    @property
    def PLUGIN_PARAMS_COL_NAME(self) -> str:
        return _("Parameter")

    @property
    def PLUGIN_PARAMS_COL_VALUE(self) -> str:
        return _("Value")

    @property
    def PLUGIN_PARAMS_COL_DEFAULT(self) -> str:
        return _("Default")

    @property
    def PLUGIN_PARAMS_PROGRAM_DCA(self) -> str:
        return _("Program DCA State")

    @property
    def PLUGIN_PARAMS_PROGRAM_DCA_HINT(self) -> str:
        return _("Sends the DALI device settings to the gateway as property values.")

    @property
    def PLUGIN_PARAMS_ADD_TO(self) -> str:
        return _("Add to {section}:")

    @property
    def PLUGIN_PARAMS_TYPE(self) -> str:
        return _("Type")

    @property
    def PLUGIN_PARAMS_NAME(self) -> str:
        return _("Name")

    @property
    def PLUGIN_PARAMS_ADD(self) -> str:
        return _("Add")

    @property
    def PLUGIN_PARAMS_NO_ENTRIES(self) -> str:
        return _("No entries.")

    @property
    def PLUGIN_PARAMS_COL_ENTRY(self) -> str:
        return _("Entry")

    @property
    def PLUGIN_PARAMS_COL_DETAILS(self) -> str:
        return _("Details")

    @property
    def PLUGIN_PARAMS_REMOVE(self) -> str:
        return _("Remove")

    @property
    def SIEMENS_DALI_CONNECT_FIRST(self) -> str:
        return _("Connect to a KNX interface to read the Siemens DALI bus.")

    @property
    def SIEMENS_DALI_SCAN_BUS(self) -> str:
        return _("Read DALI Bus")

    @property
    def SIEMENS_DALI_SCANNING(self) -> str:
        return _("Reading the DALI bus from object {object_index}…")

    @property
    def SIEMENS_DALI_NO_SCAN(self) -> str:
        return _("Press 'Read DALI Bus' to list the ECGs on the gateway.")

    @property
    def SIEMENS_DALI_PRESENT_COUNT(self) -> str:
        return _("{count} ECG(s) present on line {line}")

    @property
    def SIEMENS_DALI_READ_ONLY_NOTE(self) -> str:
        return _(
            "Read-only snapshot. Writing group assignments, scenes or firmware is not "
            "available yet and remains in the manufacturer's plugin."
        )

    @property
    def SIEMENS_DALI_COL_SLOT(self) -> str:
        return _("Slot")

    @property
    def SIEMENS_DALI_COL_SHORT_ADDRESS(self) -> str:
        return _("Short Address")

    @property
    def SIEMENS_DALI_COL_TYPE(self) -> str:
        return _("Type")

    @property
    def SIEMENS_DALI_COL_RANDOM(self) -> str:
        return _("Random Address")

    @property
    def SIEMENS_DALI_COL_MIN_DIM(self) -> str:
        return _("Min Dim")

    @property
    def SIEMENS_DALI_COL_COLOUR(self) -> str:
        return _("Colour Temp (K)")

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
    def CONFIGURE_LOADING_APPLICATION(self) -> str:
        return _("Loading application program…")

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
    def PROGRAM_UNLOAD_TITLE(self) -> str:
        return _("Unload Application Program")

    @property
    def PROGRAM_UNLOAD_TEXT(self) -> str:
        return _(
            "This unloads the application program of the device at {address}. The device "
            "stops working until its application program is downloaded again."
        )

    @property
    def PROGRAM_UNLOAD_ALL_TEXT(self) -> str:
        return _(
            "This unloads the application program of the device at {address} and then resets "
            "its individual address to 15.15.255; for that, its programming button has to be "
            "pressed. The device stops working until it is commissioned again."
        )

    @property
    def BTN_OVERWRITE_ADDRESS(self) -> str:
        return _("Overwrite Address")

    @property
    def PROGRAM_PLUGIN_TITLE(self) -> str:
        return _("Manufacturer's Plugin Not Available")

    @property
    def PROGRAM_SERIAL_USED_TITLE(self) -> str:
        return _("Serial Number Already Used")

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
    def CONFIGURE_LAST_MODIFIED(self) -> str:
        return _("Last Modified")

    @property
    def CONFIGURE_LAST_DOWNLOADED(self) -> str:
        return _("Last Downloaded")

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
        return _("Reset…")

    @property
    def RESET_POPUP_TITLE(self) -> str:
        return _("Master Reset")

    @property
    def RESET_WARNING(self) -> str:
        return _(
            "Destructive: the device is reset over the bus and must be commissioned again. "
            "In KNX Secure mode this deactivates device security."
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
    def CONFIGURE_IP_AUTOMATIC(self) -> str:
        return _("Obtain an IP address automatically")

    @property
    def CONFIGURE_IP_STATIC(self) -> str:
        return _("Use a static IP address")

    @property
    def CONFIGURE_IP_ASSIGNED(self) -> str:
        return _("Assigned IP Address")

    @property
    def CONFIGURE_IP_UNKNOWN(self) -> str:
        return _("Unknown")

    @property
    def CONFIGURE_IP_INVALID(self) -> str:
        return _("An invalid IP address was specified.")

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
        return _("Searching Manual…")

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
        return _("Updating the application program…")

    @property
    def CHANGING_APPLICATION(self) -> str:
        return _("Changing the application program…")

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
        return _("Filter parameters…")

    @property
    def CONFIGURE_APPLY_ALL(self) -> str:
        return _("Apply Changes to All {count} Identical Devices")

    @property
    def CONFIGURE_APPLY_ALL_CHANNELS(self) -> str:
        return _("Apply Changes to All Channels")

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
        return _("Save…")

    @property
    def MEMORY_DIFF(self) -> str:
        return _("Diff…")

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
        return _("Export Ist/Soll…")

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
        return _("Filter devices (name, address)…")

    @property
    def DEVICE_EMPTY_HINT(self) -> str:
        return _(
            "No devices. Open or import a project (File menu), "
            "or right-click here to add an area."
        )

    @property
    def GA_FILTER_HINT(self) -> str:
        return _("Filter group addresses (address, name)…")

    @property
    def SPACES_FILTER_HINT(self) -> str:
        return _("Filter rooms, devices, functions…")

    @property
    def BACKBONE_LINE_NAME(self) -> str:
        # The name a backbone line without one is shown with ("IP backbone line").
        return _("{medium} backbone line")

    @property
    def MAIN_LINE_NAME(self) -> str:
        # The name a main line without one is shown with ("TP main line").
        return _("{medium} main line")

    @property
    def DEVICE_UNASSIGNED(self) -> str:
        return _("Unassigned ({count})")

    @property
    def TOPOLOGY_ADD_AREAS(self) -> str:
        # An item of the "Add" submenu of the topology.
        return _("Areas…")

    @property
    def TOPOLOGY_ADD_LINES(self) -> str:
        return _("Lines…")

    @property
    def TOPOLOGY_ADD_SEGMENTS(self) -> str:
        return _("Segments…")

    @property
    def TOPOLOGY_ADD_SEGMENTS_ITEM(self) -> str:
        # The menu item of a line, which adds nothing else.
        return _("Add Segments…")

    @property
    def TOPOLOGY_TITLE_AREAS(self) -> str:
        return _("Add Areas")

    @property
    def TOPOLOGY_TITLE_LINES(self) -> str:
        return _("Add Lines")

    @property
    def TOPOLOGY_TITLE_SEGMENTS(self) -> str:
        return _("Add Segments")

    @property
    def ADD_LIMIT_REACHED(self) -> str:
        return _("The limit to add was reached.")

    @property
    def ADD_NO_LINE_MEDIUM(self) -> str:
        return _(
            "Cannot add any line to the area {area} because of topology constraints. "
            "Please check the medium type of the backbone and main lines."
        )

    @property
    def HISTORY_EV_ADDED_AREAS(self) -> str:
        return _("Added {count} areas to '{target}'.")

    @property
    def HISTORY_EV_ADDED_LINES(self) -> str:
        return _("Added {count} lines to '{target}'.")

    @property
    def HISTORY_EV_ADDED_SEGMENTS(self) -> str:
        return _("Added {count} segments to '{target}'.")

    @property
    def HISTORY_EV_ADDED_BUILDING_PARTS(self) -> str:
        return _("Added {count} building parts to '{target}'.")

    @property
    def HISTORY_EV_ADDED_TRADES(self) -> str:
        return _("Added {count} trades to '{target}'.")

    @property
    def HISTORY_EV_ADDED_FUNCTIONS(self) -> str:
        return _("Added {count} functions to '{target}'.")

    @property
    def HISTORY_EV_ADDED_GROUP_RANGES(self) -> str:
        return _("Added {count} group ranges to '{target}'.")

    @property
    def HISTORY_EV_ADDED_MAIN_GROUPS(self) -> str:
        return _("Added {count} main groups to '{target}'.")

    @property
    def HISTORY_EV_ADDED_MIDDLE_GROUPS(self) -> str:
        return _("Added {count} middle groups to '{target}'.")

    @property
    def HISTORY_EV_ADDED_GROUP_ADDRESSES(self) -> str:
        return _("Added {count} group addresses to '{target}'.")

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
    def CONTEXT_COMPARE_DEVICE(self) -> str:
        return _("Compare with…")

    @property
    def COMPARE_TITLE(self) -> str:
        return _("Compare Devices")

    @property
    def COMPARE_NEED_TWO(self) -> str:
        return _("Open a project with at least two devices to compare.")

    @property
    def COMPARE_ONLY_DIFF(self) -> str:
        return _("Only Differences")

    @property
    def COMPARE_COPY(self) -> str:
        return _("Copy")

    @property
    def COMPARE_FILTER_HINT(self) -> str:
        return _("Filter devices by address, name or description…")

    @property
    def COMPARE_SUMMARY(self) -> str:
        return _("{count} differences")

    @property
    def COMPARE_SECTION_HEADER(self) -> str:
        return _("Device")

    @property
    def COMPARE_SECTION_PARAMETERS(self) -> str:
        return _("Parameters")

    @property
    def COMPARE_SECTION_COM_OBJECTS(self) -> str:
        return _("Group Objects")

    @property
    def COMPARE_NO_DIFFERENCES(self) -> str:
        return _("No differences")

    @property
    def COMPARE_COL_FIELD(self) -> str:
        return _("Field")

    @property
    def COMPARE_COL_A(self) -> str:
        return _("Device A")

    @property
    def COMPARE_COL_B(self) -> str:
        return _("Device B")

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
        return _("Group Address Links")

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
            "Delete '{name}'? Its group objects and group-address links are removed and its "
            "individual address is freed. This can be undone."
        )

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
    def GA_ADD_MAIN_GROUPS(self) -> str:
        return _("Add Main Groups…")

    @property
    def GA_ADD_MIDDLE_GROUPS(self) -> str:
        return _("Add Middle Groups…")

    @property
    def GA_ADD_GROUP_ADDRESSES(self) -> str:
        return _("Add Group Addresses…")

    @property
    def GA_TITLE_MAIN_GROUPS(self) -> str:
        return _("Add Main Groups")

    @property
    def GA_TITLE_MIDDLE_GROUPS(self) -> str:
        return _("Add Middle Groups")

    @property
    def GA_TITLE_GROUP_ADDRESSES(self) -> str:
        return _("Add Group Addresses")

    @property
    def GA_DEFAULT_MAIN_GROUP(self) -> str:
        # The name a new main group gets when none is entered.
        return _("New main group")

    @property
    def GA_DEFAULT_MIDDLE_GROUP(self) -> str:
        return _("New middle group")

    @property
    def GA_DEFAULT_GROUP_ADDRESS(self) -> str:
        return _("New group address")

    @property
    def GA_ADD_GROUP_RANGES(self) -> str:
        # Free style: groups of any span.
        return _("Add Group Ranges…")

    @property
    def GA_TITLE_GROUP_RANGES(self) -> str:
        return _("Add Group Ranges")

    @property
    def GA_DEFAULT_GROUP_RANGE(self) -> str:
        return _("New group range")

    @property
    def ADD_CAPACITY(self) -> str:
        # How many addresses a new group range spans.
        return _("Capacity")

    @property
    def GA_FOLDER_DELETE(self) -> str:
        return _("Delete Folder")

    @property
    def GA_FOLDER_DELETE_CONFIRM(self) -> str:
        return _(
            "Delete this folder and every group address in it? This can be undone."
        )

    @property
    def GA_RENUMBER_FAILED(self) -> str:
        return _("Invalid address or already in use.")

    @property
    def GA_SECURITY(self) -> str:
        return _("Security")

    @property
    def GA_SECURITY_AUTO(self) -> str:
        return _("Automatic")

    @property
    def SECURITY_FORCED_ON(self) -> str:
        # The same as a choice of the Security drop-down.
        return _("Forced on")

    @property
    def SECURITY_FORCED_OFF(self) -> str:
        return _("Forced off")

    @property
    def SECURITY_FORCED_BY(self) -> str:
        return _("Overridden by '{name}': {mode}")

    @property
    def GA_CENTRAL(self) -> str:
        return _("Central")

    @property
    def GROUP_SECURE_ON_REFUSED(self) -> str:
        return _(
            "Not every device linked to a group address of this group is commissioned"
            " securely, so it cannot be secured."
        )

    @property
    def GA_SECURE_LINK_REFUSED(self) -> str:
        return _(
            "A secured group address can only be linked to devices that are commissioned"
            " securely."
        )

    @property
    def GA_SECURE_ON_REFUSED(self) -> str:
        return _(
            "Not every device linked to this group address is commissioned securely, so it"
            " cannot be secured."
        )

    @property
    def GA_DATA_TYPE(self) -> str:
        return _("Data Type")

    @property
    def GA_ADDRESS(self) -> str:
        return _("Address")

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
        return _("Group object linked to group address")

    @property
    def HISTORY_CO_UNLINKED(self) -> str:
        return _("Group object unlinked from group address")

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
    def OPERATION_RECOVER(self) -> str:
        return _("Recover Project from Bus")

    @property
    def OPERATION_PROGRAMMING_MODE(self) -> str:
        return _("Devices in Programming Mode")

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
    def PLUGIN_PARAMETERS_TITLE(self) -> str:
        return _("Parameters in the Manufacturer's Plugin")

    @property
    def PLUGIN_PARAMETERS_TEXT(self) -> str:
        return _(
            "The manufacturer sets this device's parameters in its own plugin dialog, which XKNX "
            "Editor cannot run, so they cannot be changed here. A download writes their current "
            "values unchanged; the group objects can be linked as usual."
        )

    @property
    def PLUGIN_PARAMETERS_EDIT_ANYWAY(self) -> str:
        return _("Edit parameters anyway")

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
    def PLUGIN_NOTICES_HEADING(self) -> str:
        return _("The manufacturer plugin is not available here:")

    @property
    def PLUGIN_ITEM_DOWNLOAD_REQUIRED(self) -> str:
        return _("Only it can program the device.")

    @property
    def PLUGIN_ITEM_USER_INTERFACE(self) -> str:
        return _("Settings made in its dialog are not shown.")

    @property
    def PLUGIN_ITEM_DOWNLOAD(self) -> str:
        return _("The standard download is used instead.")

    @property
    def PLUGIN_ITEM_DATA_HANDLER(self) -> str:
        return _("Part of the device data is not available.")

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
        return _("Program Without the Manufacturer's Plugin Anyway")

    @property
    def OPERATION_DOWNLOADING(self) -> str:
        return _("Downloading")

    @property
    def OPERATION_CONNECTING(self) -> str:
        return _("Connecting")

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
    def OPERATION_CANCELED_BY_USER(self) -> str:
        return _("Cancelled by user.")

    @property
    def OPERATION_STARTED_BY_MCP(self) -> str:
        return _("Started by an MCP client.")

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
    def BUS_CURRENT_ESTIMATED(self) -> str:
        # After a bus current that is (or includes) an estimate: "10 mA (estimated)".
        return _("(estimated)")

    @property
    def DEVICE_COUNT(self) -> str:
        return _("Number of Devices")

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

    @property
    def DEVICE_CERTIFICATE_PLAIN(self) -> str:
        return _("Plain")

    @property
    def SECURE_COMMISSIONING_OFF_TITLE(self) -> str:
        return _("Deactivate Secure Commissioning")

    @property
    def SECURE_COMMISSIONING_OFF_UNSECURED(self) -> str:
        return _("These group addresses will then communicate in the clear:")

    @property
    def SECURE_COMMISSIONING_OFF_REMOVED(self) -> str:
        return _(
            "The device's group objects will be removed from these secured group addresses:"
        )

    @property
    def SECURE_COMMISSIONING_OFF_CONFIRM(self) -> str:
        return _("Deactivate")

    @property
    def CONFIGURE_SECURE_COMMISSIONING(self) -> str:
        return _("Secure Commissioning")

    @property
    def CONFIGURE_ADD_CERTIFICATE(self) -> str:
        return _("Add Device Certificate…")

    @property
    def SECURE_COMMISSIONING_ON(self) -> str:
        return _("Activated")

    @property
    def SECURE_COMMISSIONING_OFF(self) -> str:
        return _("Deactivated")

    @property
    def GA_SECURE_REQUIRED(self) -> str:
        return _(
            "A group object that requires secure communication would end up on a group"
            " address in the clear."
        )

    @property
    def DEVICE_CERTIFICATE_LATER(self) -> str:
        return _("Later")

    @property
    def DEVICE_CERTIFICATE_DONT_ASK(self) -> str:
        return _("Don't Ask When Adding Devices")

    @property
    def CONFIGURE_IP_SECURE(self) -> str:
        return _("KNX IP Secure")

    @property
    def CONFIGURE_IP_SECURE_SHOW(self) -> str:
        return _("Show Passwords")

    @property
    def CONFIGURE_IP_SECURE_TUNNELLING(self) -> str:
        return _("Secure Tunneling")

    @property
    def CONFIGURE_IP_SECURE_PASSWORD(self) -> str:
        return _("Commissioning Password")

    @property
    def CONFIGURE_IP_SECURE_AUTHENTICATION(self) -> str:
        return _("Authentication Code")

    @property
    def CONFIGURE_IP_SECURE_INTERFACE(self) -> str:
        return _("Interface")

    @property
    def CONFIGURE_IP_SECURE_USER(self) -> str:
        return _("User")

    @property
    def CONFIGURE_IP_SECURE_INTERFACE_PASSWORD(self) -> str:
        return _("Password")

    @property
    def CONFIGURE_IP_SECURE_ROUTING(self) -> str:
        return _("Routing")

    @property
    def CONFIGURE_IP_SECURE_TUNNEL(self) -> str:
        return _("Tunnel {address}")

    @property
    def CONFIGURE_IP_SECURE_COPY(self) -> str:
        return _("Copy")

    @property
    def IP_SECURE_PASSWORD_INVALID(self) -> str:
        return _("A password needs 1 to 20 Latin-1 characters.")

    @property
    def BTN_CLOSE(self) -> str:
        return _("Close")

    @property
    def PROJECT_INFO_CERTIFICATES(self) -> str:
        return _("Device Certificates")

    @property
    def PROJECT_CERTIFICATES_MANAGE(self) -> str:
        return _("Manage…")

    @property
    def PROJECT_CERTIFICATES_TITLE(self) -> str:
        return _("Device Certificates")

    @property
    def PROJECT_CERTIFICATES_EMPTY(self) -> str:
        return _("The project has no device certificates.")

    @property
    def PROJECT_CERTIFICATES_SERIAL(self) -> str:
        return _("Serial Number")

    @property
    def PROJECT_CERTIFICATES_DEVICE(self) -> str:
        return _("Device")

    @property
    def PROJECT_CERTIFICATES_REMOVE(self) -> str:
        return _("Remove")

    @property
    def PROJECT_CERTIFICATES_ADD(self) -> str:
        return _("Add Device Certificate…")

    @property
    def GA_SECURE_TABLES_FULL(self) -> str:
        return _("The security tables of {device} cannot hold this link.")

    @property
    def CONFIGURE_ROUTING_BLOCKING(self) -> str:
        return _("Point-to-Point and Broadcast")

    @property
    def ROUTING_BLOCKING_AUTO(self) -> str:
        return _("Automatic")

    @property
    def ROUTING_BLOCKING_ON(self) -> str:
        return _("Blocked")

    @property
    def ROUTING_BLOCKING_OFF(self) -> str:
        return _("Routed")

    @property
    def DALI_CONNECT_HINT(self) -> str:
        return _("Connect to a KNX interface to commission the DALI bus.")

    @property
    def DALI_UNVERIFIED_WARNING(self) -> str:
        return _(
            "This gateway's commissioning protocol has not been verified against a "
            "capture; writes may misbehave. Proceed with care."
        )

    @property
    def DALI_PROJECT_NOT_UPDATED(self) -> str:
        return _(
            "DALI commissioning writes the gateway on the bus but does not update "
            "the project parameters; re-export the .knxproj after a DCA-side edit."
        )

    @property
    def DALI_PERSIST_LABEL(self) -> str:
        return _("DALI commissioning")

    @property
    def DALI_CHANNEL(self) -> str:
        return _("DALI Channel")

    @property
    def DALI_CHANNEL_N(self) -> str:
        return _("Channel {n}")

    @property
    def DALI_SCAN(self) -> str:
        return _("Scan Bus")

    @property
    def DALI_ALL_ON(self) -> str:
        return _("All ECGs On")

    @property
    def DALI_ALL_OFF(self) -> str:
        return _("All Off")

    @property
    def DALI_NEW_INSTALLATION(self) -> str:
        return _("New Installation…")

    @property
    def DALI_POST_INSTALLATION(self) -> str:
        return _("Post Installation…")

    @property
    def DALI_RUNNING(self) -> str:
        return _("Running: {task}")

    @property
    def DALI_TASK_SCAN(self) -> str:
        return _("scanning the bus")

    @property
    def DALI_TASK_BROADCAST(self) -> str:
        return _("switching all ECGs")

    @property
    def DALI_TASK_BLINK(self) -> str:
        return _("blinking")

    @property
    def DALI_TASK_BLINK_STOP(self) -> str:
        return _("stopping the blinking")

    @property
    def DALI_TASK_NEW(self) -> str:
        return _("new installation")

    @property
    def DALI_TASK_POST(self) -> str:
        return _("post installation")

    @property
    def DALI_TASK_GROUPS(self) -> str:
        return _("programming the groups")

    @property
    def DALI_TASK_SCENES(self) -> str:
        return _("programming the scenes")

    @property
    def DALI_FOUND(self) -> str:
        return _("{count} found")

    @property
    def DALI_SCAN_HINT(self) -> str:
        return _("Use 'Scan Bus' to read the DALI bus.")

    @property
    def DALI_FIRMWARE(self) -> str:
        return _("Firmware {version}")

    @property
    def DALI_CONFIRM_TITLE(self) -> str:
        return _("Confirm DALI Programming")

    @property
    def DALI_CONTINUE(self) -> str:
        return _("Continue")

    @property
    def DALI_CANCEL(self) -> str:
        return _("Cancel")

    @property
    def DALI_CONFIRM_NEW(self) -> str:
        return _(
            "New installation scans the DALI bus again and REASSIGNS the short\naddress of every ballast. This cannot be undone. Continue?"
        )

    @property
    def DALI_CONFIRM_POST(self) -> str:
        return _(
            "Post installation scans the bus for new ballasts and updates their addressing.\nThis changes the device. Continue?"
        )

    @property
    def DALI_CONFIRM_GROUPS(self) -> str:
        return _(
            "Write the group assignment to the DALI bus. This applies the groups to the\ngateway and its ballasts. Continue?"
        )

    @property
    def DALI_CONFIRM_SCENES(self) -> str:
        return _("Write the scene values to the DALI bus. Continue?")

    @property
    def DALI_ECGS_PRESENT(self) -> str:
        return _("{count} ECG(s) present")

    @property
    def DALI_COL_GROUP(self) -> str:
        return _("Group")

    @property
    def DALI_COL_TYPE(self) -> str:
        return _("Type")

    @property
    def DALI_COL_LONG_ADDRESS(self) -> str:
        return _("Long Address")

    @property
    def DALI_COL_ALARM(self) -> str:
        return _("Alarm")

    @property
    def DALI_BLINK(self) -> str:
        return _("Blink")

    @property
    def DALI_STOP(self) -> str:
        return _("Stop")

    @property
    def DALI_PROGRAM_GROUPS(self) -> str:
        return _("Program Groups…")

    @property
    def DALI_SCENES(self) -> str:
        return _("Scenes")

    @property
    def DALI_SCENES_HINT(self) -> str:
        return _("Assign the ECGs to groups above to define scenes.")

    @property
    def DALI_SCENE(self) -> str:
        return _("Scene")

    @property
    def DALI_SCENE_NUMBER(self) -> str:
        return _("Scene {number}")

    @property
    def DALI_GROUP_NUMBER(self) -> str:
        return _("Group {number}")

    @property
    def DALI_PROGRAM_SCENES(self) -> str:
        return _("Program Scenes…")

    @property
    def DALI_GROUP_SINGLE(self) -> str:
        return _("Single")

    @property
    def DALI_GROUP_UNASSIGNED(self) -> str:
        return _("Unassigned")

    @property
    def LOAD_PROCEDURE(self) -> str:
        return _("Procedure {number}  ({count} steps)")

    @property
    def LOAD_COL_KIND(self) -> str:
        return _("Kind")

    @property
    def LOAD_COL_APPLIES_TO(self) -> str:
        return _("Applies To")

    @property
    def LOAD_COL_DETAILS(self) -> str:
        return _("Details")

    @property
    def PREFLIGHT_SCOPE(self) -> str:
        return _("Scope: {scope}")

    @property
    def SPACE_NUMBER(self) -> str:
        return _("Number")

    @property
    def SPACE_USE(self) -> str:
        return _("Use")

    @property
    def CURRENT_LINE(self) -> str:
        return _("Current Line")

    @property
    def CURRENT_LINE_NONE(self) -> str:
        return _("None")

    @property
    def CURRENT_LINE_INHERITED(self) -> str:
        return _("{line} (inherits from {space})")

    @property
    def STATUS(self) -> str:
        return _("Status")

    @property
    def STATUS_UNDEFINED(self) -> str:
        return _("Unknown")

    @property
    def STATUS_EDITING(self) -> str:
        return _("Editing")

    @property
    def STATUS_FINISHED_DESIGN(self) -> str:
        return _("Finished design")

    @property
    def STATUS_FINISHED_COMMISSIONING(self) -> str:
        return _("Finished commissioning")

    @property
    def STATUS_TESTED(self) -> str:
        return _("Tested")

    @property
    def STATUS_ACCEPTED(self) -> str:
        return _("Accepted")

    @property
    def STATUS_LOCKED(self) -> str:
        return _("Locked")

    @property
    def CONFIGURE_ADDITIONAL_ADDRESSES(self) -> str:
        return _("Additional Individual Addresses")

    @property
    def CONFIGURE_ADDITIONAL_ADDRESS(self) -> str:
        return _("Additional Individual Address")

    @property
    def BTN_ASSIGN_ADDITIONAL_ADDRESSES(self) -> str:
        return _("Assign Missing Addresses")

    @property
    def ADDITIONAL_ADDRESSES_NOT_ENOUGH(self) -> str:
        return _(
            "The line does not provide enough addresses for all necessary additional addresses."
        )

    @property
    def ADDITIONAL_ADDRESS_INVALID(self) -> str:
        return _(
            "Enter a device number from 1 to 255 or a full individual address in the device's line."
        )

    @property
    def ADDITIONAL_ADDRESS_OTHER_LINE(self) -> str:
        return _("An additional address must be in the device's line {line}.")

    @property
    def ADDITIONAL_ADDRESS_IN_USE(self) -> str:
        return _("The individual address {address} is already used in this line.")

    @property
    def ADDITIONAL_ADDRESS_NO_LINE(self) -> str:
        return _("The device is on no line: put it on a line first.")

    @property
    def HISTORY_ADDITIONAL_ADDRESS_CHANGED(self) -> str:
        return _("Changed additional address of device '{0}' from '{1}' to '{2}'.")

    @property
    def HISTORY_IP_ADDRESS_CHANGED(self) -> str:
        return _("Change assigned IP address of '{0}' to '{1}'.")

    @property
    def HISTORY_IP_CONFIG_CHANGED(self) -> str:
        return _("IP configuration of device '{0}' changed.")

    @property
    def HISTORY_ADDITIONAL_ADDRESS_NAMES(self) -> str:
        return _("Change additional address names")

    @property
    def HISTORY_ADDITIONAL_ADDRESS_DESCRIPTIONS(self) -> str:
        return _("Change additional address descriptions")

    @property
    def HISTORY_ASSIGN_ADDITIONAL_ADDRESSES(self) -> str:
        return _("Assign additional addresses")

    @property
    def HISTORY_ADDITIONAL_ADDRESSES(self) -> str:
        return _("Change additional addresses")

    @property
    def ADDITIONAL_ADDRESS_INDEX(self) -> str:
        return "#"

    @property
    def HISTORY_EV_ADD_DEVICE(self) -> str:
        return _("Add device {name}")

    @property
    def HISTORY_EV_STORE_PLUGIN_DATA(self) -> str:
        return _("Store plugin data {name}")

    @property
    def HISTORY_EV_SET_PARAMETER(self) -> str:
        return _("Set {ref} = {value}")

    @property
    def HISTORY_EV_CHANGE(self) -> str:
        return _("Change")

    @property
    def HISTORY_EV_CREATE_AREA(self) -> str:
        return _("Create area {address}")

    @property
    def HISTORY_EV_CREATE_LINE(self) -> str:
        return _("Create line {address}")

    @property
    def HISTORY_EV_ADD_SEGMENT(self) -> str:
        return _("Add segment")

    @property
    def HISTORY_EV_SET_IP_ROUTING(self) -> str:
        return _("Set IP routing of the backbone")

    @property
    def HISTORY_EV_SET_BUS_CONNECTION(self) -> str:
        return _("Set bus connection of the backbone")

    @property
    def HISTORY_EV_RENAME_SEGMENT(self) -> str:
        return _("Rename segment to {name}")

    @property
    def HISTORY_EV_SET_MEDIUM(self) -> str:
        return _("Set medium to {medium}")

    @property
    def HISTORY_EV_SET_DOMAIN_ADDRESS(self) -> str:
        return _("Set domain address")

    @property
    def HISTORY_EV_CREATE_GA(self) -> str:
        return _("Create group address {address}")

    @property
    def HISTORY_EV_IMPORT_GAS(self) -> str:
        return _("Import group addresses")

    @property
    def HISTORY_EV_IMPORT_GAS_FROM(self) -> str:
        return _("Import group addresses from {file}")

    @property
    def HISTORY_EV_LINK(self) -> str:
        return _("Link group object")

    @property
    def HISTORY_EV_UNLINK(self) -> str:
        return _("Unlink group object")

    @property
    def HISTORY_EV_RENAME_AREA(self) -> str:
        return _("Rename area to {name}")

    @property
    def HISTORY_EV_RENAME_PROJECT(self) -> str:
        return _("Rename project to {name}")

    @property
    def HISTORY_EV_RENAME_LINE(self) -> str:
        return _("Rename line to {name}")

    @property
    def HISTORY_EV_RENAME_DEVICE(self) -> str:
        return _("Rename device to {name}")

    @property
    def HISTORY_EV_MOVE_DEVICE(self) -> str:
        return _("Move device")

    @property
    def HISTORY_EV_UNLINK_DEVICE(self) -> str:
        return _("Unlink device")

    @property
    def HISTORY_EV_PARK_DEVICE(self) -> str:
        return _("Park device")

    @property
    def CONTEXT_PARK(self) -> str:
        # Clears a device's device number; it stays on its line ("1.5.-").
        return _("Park")

    @property
    def CONTEXT_UNLINK_LINE(self) -> str:
        # Takes a device off its line in the topology.
        return _("Unlink")

    @property
    def HISTORY_EV_SET_FLAG(self) -> str:
        return _("Set flag")

    @property
    def HISTORY_EV_SET_DPT(self) -> str:
        return _("Set datapoint type")

    @property
    def HISTORY_EV_SET_PRIORITY(self) -> str:
        return _("Set priority")

    @property
    def HISTORY_EV_SET_DESCRIPTION(self) -> str:
        return _("Set description")

    @property
    def HISTORY_EV_SET_SENDING(self) -> str:
        return _("Set sending group address")

    @property
    def HISTORY_EV_ADD_INSTALLATION(self) -> str:
        return _("Add installation")

    @property
    def HISTORY_EV_REMOVE_DEVICE(self) -> str:
        return _("Remove device")

    @property
    def HISTORY_EV_REMOVE_AREA(self) -> str:
        return _("Remove area")

    @property
    def HISTORY_EV_REMOVE_LINE(self) -> str:
        return _("Remove line")

    @property
    def HISTORY_EV_REMOVE_SEGMENT(self) -> str:
        return _("Remove segment")

    @property
    def HISTORY_EV_REMOVE_GA(self) -> str:
        return _("Remove group address")

    @property
    def HISTORY_EV_REMOVE_FUNCTION(self) -> str:
        return _("Remove function")

    @property
    def HISTORY_EV_REMOVE_FUNCTION_GA(self) -> str:
        return _("Remove group address from function")

    @property
    def HISTORY_EV_REMOVE_SPACE(self) -> str:
        return _("Remove building part")

    @property
    def HISTORY_EV_REMOVE_GROUP_RANGE(self) -> str:
        return _("Remove group range")

    @property
    def HISTORY_EV_REMOVE(self) -> str:
        return _("Remove")

    @property
    def HISTORY_EV_UPDATE_APP(self) -> str:
        return _("Update application program")

    @property
    def HISTORY_EV_CHANGE_APP(self) -> str:
        return _("Change application program")

    @property
    def HISTORY_EV_SYNC_COM_OBJECTS(self) -> str:
        return _("Update group objects")

    @property
    def HISTORY_EV_SYNC_MODULES(self) -> str:
        return _("Update module instances")

    @property
    def HISTORY_EV_SET_GA_SECURITY(self) -> str:
        return _("Set group address security")

    @property
    def HISTORY_EV_SET_GROUP_SECURITY(self) -> str:
        return _("Set group security")

    @property
    def HISTORY_EV_SET_GA_CENTRAL(self) -> str:
        return _("Mark group address as central")

    @property
    def HISTORY_EV_UNSET_GA_CENTRAL(self) -> str:
        return _("Remove central mark from group address")

    @property
    def HISTORY_EV_SET_SECURE_COMMISSIONING(self) -> str:
        return _("Set secure commissioning")

    @property
    def HISTORY_EV_RENAME_GA(self) -> str:
        return _("Rename group address to {name}")

    @property
    def HISTORY_EV_SET_GA_ADDRESS(self) -> str:
        return _("Change group address")

    @property
    def HISTORY_EV_SET_CERTIFICATES(self) -> str:
        return _("Set device certificates")

    @property
    def HISTORY_EV_SET_DEVICE_DESCRIPTION(self) -> str:
        return _("Set device description")

    @property
    def HISTORY_EV_SET_COMMENT(self) -> str:
        return _("Set comment")

    @property
    def HISTORY_EV_SET_INSTALLATION_HINTS(self) -> str:
        return _("Set installation hints")

    @property
    def HISTORY_EV_SET_STATUS(self) -> str:
        return _("Set status to {status}")

    @property
    def HISTORY_EV_SET_SPACE_USE(self) -> str:
        return _("Set room use")

    @property
    def HISTORY_EV_SET_CURRENT_LINE(self) -> str:
        return _("Set current line")

    @property
    def HISTORY_EV_SET_FUNCTION_DESCRIPTION(self) -> str:
        return _("Set function description")

    @property
    def HISTORY_EV_CREATE_FUNCTION(self) -> str:
        return _("Create function {name}")

    @property
    def HISTORY_EV_RENAME_FUNCTION(self) -> str:
        return _("Rename function to {name}")

    @property
    def HISTORY_EV_SET_FUNCTION_TYPE(self) -> str:
        return _("Set function type")

    @property
    def HISTORY_EV_ADD_FUNCTION_GA(self) -> str:
        return _("Add group address to function")

    @property
    def HISTORY_EV_CREATE_SPACE(self) -> str:
        return _("Create building part {name}")

    @property
    def HISTORY_EV_RENAME_SPACE(self) -> str:
        return _("Rename building part to {name}")

    @property
    def HISTORY_EV_SET_SPACE_NUMBER(self) -> str:
        return _("Set building part number")

    @property
    def HISTORY_EV_SET_SPACE_DESCRIPTION(self) -> str:
        return _("Set building part description")

    @property
    def HISTORY_EV_SET_SPACE_TYPE(self) -> str:
        return _("Set building part type")

    @property
    def HISTORY_EV_MOVE_SPACE(self) -> str:
        return _("Move building part")

    @property
    def HISTORY_EV_SET_DEVICE_SPACE(self) -> str:
        return _("Assign device to building part")

    @property
    def HISTORY_EV_CREATE_TRADE(self) -> str:
        return _("Create trade {name}")

    @property
    def HISTORY_EV_RENAME_TRADE(self) -> str:
        return _("Rename trade to {name}")

    @property
    def HISTORY_EV_SET_TRADE_NUMBER(self) -> str:
        return _("Set trade number")

    @property
    def HISTORY_EV_SET_TRADE_DESCRIPTION(self) -> str:
        return _("Set trade description")

    @property
    def HISTORY_EV_MOVE_TRADE(self) -> str:
        return _("Move trade")

    @property
    def HISTORY_EV_REMOVE_TRADE(self) -> str:
        return _("Remove trade")

    @property
    def HISTORY_EV_ADD_TRADE_DEVICE(self) -> str:
        return _("Add device to trade")

    @property
    def HISTORY_EV_REMOVE_TRADE_DEVICE(self) -> str:
        return _("Remove device from trade")

    @property
    def HISTORY_EV_SET_COMMISSIONING(self) -> str:
        return _("Set commissioning state")

    @property
    def HISTORY_EV_CREATE_GROUP_RANGE(self) -> str:
        return _("Create group range {name}")

    @property
    def HISTORY_EV_RENAME_GROUP_RANGE(self) -> str:
        return _("Rename group range to {name}")

    @property
    def TOOLS_TOPOLOGY_NO_ADDRESS(self) -> str:
        return _("{device}: no individual address")

    @property
    def TOOLS_TOPOLOGY_MALFORMED(self) -> str:
        return _("{device}: malformed address '{address}'")

    @property
    def TOOLS_TOPOLOGY_DUPLICATE(self) -> str:
        return _("{device}: duplicate address {address}")


S = ProjectStrings()
