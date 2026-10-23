"""Monitor plugin strings."""

from pathlib import Path

from editor_gui.strings import create_translator, shortcut

_locale_dir = Path(__file__).parent / "locales"
_ = create_translator("monitor", _locale_dir)


class MonitorStrings:
    @property
    def PANEL_MONITOR(self) -> str:
        return _("Monitor")

    @property
    def MONITOR_NO_GAS(self) -> str:
        return _("No group addresses (open or import a project)")

    @property
    def MONITOR_DISCONNECTED(self) -> str:
        return _("Not Connected")

    @property
    def MONITOR_VALUE_HINT(self) -> str:
        return _("value (e.g. on / 21.5 / 50)")

    @property
    def MONITOR_VALUE_LABEL(self) -> str:
        return _("Value:")

    @property
    def MONITOR_FILTER_HINT(self) -> str:
        return _("Filter by address or name…")

    @property
    def MONITOR_WRITE(self) -> str:
        return _("Write")

    @property
    def MONITOR_READ(self) -> str:
        return _("Read")

    @property
    def MONITOR_TAB_GROUP_OBJECTS(self) -> str:
        return _("Group Objects")

    @property
    def MONITOR_TAB_BUS(self) -> str:
        return _("Bus Monitor")

    @property
    def MONITOR_TAB_GROUP(self) -> str:
        return _("Group Monitor")

    @property
    def MONITOR_TAB_ACTIVITY(self) -> str:
        return _("Bus Activity")

    @property
    def MONITOR_CLEAR(self) -> str:
        return _("Clear")

    @property
    def MONITOR_SHOW_EXTENDED(self) -> str:
        return _("Show Extended Information")

    @property
    def MONITOR_HIDE_EXTENDED(self) -> str:
        return _("Hide Extended Information")

    @property
    def MONITOR_TELEGRAM_COUNT(self) -> str:
        return _("{count} telegrams")

    @property
    def MONITOR_TELEGRAM_COUNT_ONE(self) -> str:
        return _("{count} telegram")

    @property
    def MONITOR_BUS_LOAD(self) -> str:
        return _("Load: {rate} t/s")

    # --- command bar ---------------------------------------------------------

    @property
    def MONITOR_ADDRESS_LABEL(self) -> str:
        return _("Group Address:")

    @property
    def MONITOR_ADDRESS_HINT(self) -> str:
        return _("e.g. 1/2/3")

    @property
    def MONITOR_UNKNOWN_ADDRESS(self) -> str:
        return _("Enter a group address of the project to send to it.")

    # --- toolbar -------------------------------------------------------------

    @property
    def TOOL_START(self) -> str:
        return _("Start")

    @property
    def TOOL_STOP(self) -> str:
        return _("Stop")

    @property
    def TOOL_IMPORT(self) -> str:
        return _("Import")

    @property
    def TOOL_EXPORT(self) -> str:
        return _("Export")

    @property
    def TOOL_PRINT(self) -> str:
        return _("Print")

    @property
    def TOOL_REPLAY(self) -> str:
        return _("Replay Telegrams")

    @property
    def TOOL_STOP_REPLAY(self) -> str:
        return _("Stop Replay")

    @property
    def TOOL_START_GROUP_HINT(self) -> str:
        return _("Start recording the group telegrams")

    @property
    def TOOL_START_ACTIVITY_HINT(self) -> str:
        return _(
            "Start recording the connection's communication other than group telegrams, "
            "including every frame this app sends"
        )

    @property
    def TOOL_START_BUS_HINT(self) -> str:
        return _("Connect the interface in bus monitor mode and record every frame")

    @property
    def TOOL_STOP_HINT(self) -> str:
        return _("Stop recording")

    @property
    def TOOL_STOP_BUS_HINT(self) -> str:
        return _("Close the bus monitor connection and restore the normal connection")

    @property
    def TOOL_CLEAR_HINT(self) -> str:
        return _("Remove all telegrams")

    @property
    def TOOL_IMPORT_HINT(self) -> str:
        return _("Load a recording")

    @property
    def TOOL_EXPORT_HINT(self) -> str:
        return _("Save the telegrams to a file")

    @property
    def TOOL_PRINT_HINT(self) -> str:
        return _("Print the telegrams as a PDF document")

    @property
    def TOOL_REPLAY_HINT(self) -> str:
        return _(
            "Send the selected group telegrams (all if none is selected) again with their "
            "original timing"
        )

    @property
    def TOOL_STOP_REPLAY_HINT(self) -> str:
        return _("Cancel the replay")

    @property
    def TOOL_COPY(self) -> str:
        return _("Copy")

    @property
    def TOOL_COPY_HINT(self) -> str:
        return _(
            "Copy the selected telegrams (all shown if none is selected) to the clipboard "
            "({shortcut}). Double-click a telegram to select its source device."
        ).format(shortcut=shortcut("C"))

    @property
    def STATUS_RECORDING(self) -> str:
        return _("Rec.")

    @property
    def STATUS_STOPPED(self) -> str:
        return _("Stopped")

    @property
    def STATUS_REPLAYING(self) -> str:
        return _("Replaying {done}/{total}")

    @property
    def STATUS_BUSMON_STARTING(self) -> str:
        return _("Connecting in bus monitor mode…")

    @property
    def STATUS_BUSMON_STOPPING(self) -> str:
        return _("Closing the bus monitor connection…")

    @property
    def STATUS_BUSMON_RUNNING(self) -> str:
        return _(
            "Bus monitor mode: the normal connection is closed. Nothing can be sent or "
            "downloaded until the bus monitor is stopped."
        )

    # --- bus monitor ---------------------------------------------------------

    @property
    def BUSMON_CONFIRM_TITLE(self) -> str:
        return _("Start Bus Monitor")

    @property
    def BUSMON_CONFIRM_TEXT(self) -> str:
        return _(
            "The bus monitor connects the interface in bus monitor mode. The normal connection "
            "is closed while the bus monitor runs: nothing can be sent or downloaded in that "
            "time. Stopping the bus monitor restores the normal connection.\n\nStart the bus "
            "monitor?"
        )

    @property
    def BUSMON_UNSUPPORTED_CONNECTION(self) -> str:
        return _(
            "The bus monitor is not supported for this connection type. It needs a tunnelling "
            "connection."
        )

    @property
    def BUSMON_UNSUPPORTED(self) -> str:
        return _("The interface does not support bus monitor mode.")

    @property
    def BUSMON_BUSY(self) -> str:
        return _(
            "The interface rejected bus monitor mode: another client holds a connection to it."
        )

    @property
    def BUSMON_FAILED(self) -> str:
        return _("The bus monitor connection could not be established.")

    @property
    def BUSMON_RESTORED(self) -> str:
        return _("The normal connection is restored.")

    # --- questions -----------------------------------------------------------

    @property
    def QUESTION_TITLE(self) -> str:
        return _("Unsaved Telegrams")

    @property
    def QUESTION_CLEAR_UNSAVED(self) -> str:
        return _("The telegrams have not been exported. Clear them anyway?")

    @property
    def QUESTION_IMPORT_UNSAVED(self) -> str:
        return _(
            "The telegrams have not been exported. Loading a recording replaces them and stops "
            "the recording. Continue?"
        )

    @property
    def QUESTION_IMPORT(self) -> str:
        return _(
            "Loading a recording replaces the telegrams and stops the recording. Continue?"
        )

    @property
    def BTN_YES(self) -> str:
        return _("Yes")

    @property
    def BTN_NO(self) -> str:
        return _("No")

    @property
    def BTN_OK(self) -> str:
        return _("OK")

    @property
    def BTN_CANCEL(self) -> str:
        return _("Cancel")

    @property
    def SCOPE_EXPORT_TITLE(self) -> str:
        return _("Export Telegrams")

    @property
    def SCOPE_PRINT_TITLE(self) -> str:
        return _("Print Telegrams")

    @property
    def SCOPE_LABEL(self) -> str:
        return _("Which telegrams?")

    @property
    def SCOPE_ALL(self) -> str:
        return _("All Telegrams ({count})")

    @property
    def SCOPE_SELECTED(self) -> str:
        return _("Selected Telegrams ({count})")

    @property
    def SCOPE_FILTERED(self) -> str:
        return _("Filtered Telegrams ({count})")

    @property
    def FILE_FILTER(self) -> str:
        return _("Telegram recordings")

    @property
    def FILE_ALL(self) -> str:
        return _("All files")

    @property
    def EXPORTED(self) -> str:
        return _("{count} telegrams exported to {path}")

    @property
    def EXPORT_FAILED(self) -> str:
        return _("Export failed: {error}")

    @property
    def IMPORTED(self) -> str:
        return _("{count} entries loaded from {path}")

    @property
    def IMPORT_FAILED(self) -> str:
        return _("Loading failed: {error}")

    @property
    def IMPORT_SKIPPED(self) -> str:
        return _("{count} telegrams in another frame format were skipped")

    @property
    def PRINT_FAILED(self) -> str:
        return _("Printing failed: {error}")

    @property
    def PRINT_PAGE(self) -> str:
        return _("Page {page}")

    @property
    def REPLAY_SECURE_SKIPPED(self) -> str:
        return _("{count} Data Secure telegrams are not replayed")

    @property
    def REPLAY_NOTHING(self) -> str:
        return _("There are no group telegrams to replay")

    @property
    def REPLAY_FINISHED(self) -> str:
        return _("Replay finished: {count} telegrams sent")

    @property
    def REPLAY_CANCELLED(self) -> str:
        return _("Replay cancelled: {count} telegrams sent")

    # --- rows ----------------------------------------------------------------

    @property
    def ROW_FROM_BUS(self) -> str:
        return _("from bus")

    @property
    def ROW_TO_BUS(self) -> str:
        return _("to bus")

    @property
    def ROW_START(self) -> str:
        return _("Start")

    @property
    def ROW_STOP(self) -> str:
        return _("Stop")

    @property
    def ROW_CONNECTION(self) -> str:
        return _("Connection event")

    @property
    def ROW_INVALID_FRAME(self) -> str:
        return _("Invalid frame")

    @property
    def ROW_COMMENT(self) -> str:
        return _("Comment")

    @property
    def INFO_STARTED(self) -> str:
        return _(
            "Recording was started, Host={host}, Connection={connection}, Mode={mode}"
        )

    @property
    def INFO_STOPPED(self) -> str:
        return _("Recording was stopped")

    @property
    def INFO_ESTABLISHED(self) -> str:
        return _("Connection was established")

    @property
    def INFO_LOST(self) -> str:
        return _("Connection was lost")

    @property
    def PRIO_SYSTEM(self) -> str:
        return _("System")

    @property
    def PRIO_NORMAL(self) -> str:
        return _("Normal")

    @property
    def PRIO_URGENT(self) -> str:
        return _("Urgent")

    @property
    def PRIO_LOW(self) -> str:
        return _("Low")

    @property
    def YES(self) -> str:
        return _("Yes")

    @property
    def FRAME_STANDARD(self) -> str:
        return _("Standard")

    @property
    def FRAME_EXTENDED(self) -> str:
        return _("Extended")

    # --- columns -------------------------------------------------------------

    @property
    def COL_NUMBER(self) -> str:
        return _("#")

    @property
    def COL_DATE(self) -> str:
        return _("Date")

    @property
    def COL_TIME(self) -> str:
        return _("Time")

    @property
    def COL_SERVICE(self) -> str:
        return _("Service")

    @property
    def COL_FLAGS(self) -> str:
        return _("Flags")

    @property
    def COL_PRIO(self) -> str:
        return _("Prio")

    @property
    def COL_SOURCE_ADDRESS(self) -> str:
        return _("Source Address")

    @property
    def COL_SOURCE_NAME(self) -> str:
        return _("Source Name")

    @property
    def COL_DESTINATION_ADDRESS(self) -> str:
        return _("Destination Address")

    @property
    def COL_DESTINATION_NAME(self) -> str:
        return _("Destination Name")

    @property
    def COL_BUILDING_PART(self) -> str:
        return _("Building Part")

    @property
    def COL_BUILDING_FUNCTION(self) -> str:
        return _("Building Function")

    @property
    def COL_HOP_COUNT(self) -> str:
        return _("Hop Count")

    @property
    def COL_TYPE(self) -> str:
        return _("Telegram Type")

    @property
    def COL_TYPE_SHORT(self) -> str:
        return _("Telegram Type (Short)")

    @property
    def COL_DPT(self) -> str:
        return _("DPT")

    @property
    def COL_INFO(self) -> str:
        return _("Value")

    @property
    def COL_IACK(self) -> str:
        return _("Iack")

    @property
    def COL_RAW_DATA(self) -> str:
        return _("Raw Data")

    @property
    def COL_RAW_VALUE(self) -> str:
        return _("Raw Value")

    @property
    def COL_MESSAGE_CODE(self) -> str:
        return _("Message Code")

    @property
    def COL_REPEATED(self) -> str:
        return _("Repeated")

    @property
    def COL_SECURITY(self) -> str:
        return _("Security")

    @property
    def COL_FRAME_FORMAT(self) -> str:
        return _("Frame Format")

    @property
    def COL_SYSTEM_BROADCAST(self) -> str:
        return _("System Broadcast")

    @property
    def COL_INTERFACE(self) -> str:
        return _("Interface")

    @property
    def COL_MEDIUM_INFO(self) -> str:
        return _("MediumInfo")

    @property
    def COL_SOURCE_DESCRIPTION(self) -> str:
        return _("Source Description")

    @property
    def COL_DESTINATION_DESCRIPTION(self) -> str:
        return _("Destination Description")

    @property
    def ACTIVITY_COL_ADDRESS(self) -> str:
        return _("Address")

    @property
    def ACTIVITY_COL_NAME(self) -> str:
        return _("Name")

    @property
    def ACTIVITY_COL_DPT(self) -> str:
        return _("DPT")

    @property
    def ACTIVITY_COL_VALUE(self) -> str:
        return _("Value")

    @property
    def ACTIVITY_COL_TIME(self) -> str:
        return _("Time")


S = MonitorStrings()
