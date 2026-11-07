"""User-facing strings for the recover plugin."""

from __future__ import annotations

from pathlib import Path

from editor_gui.strings import create_translator

_locale_dir = Path(__file__).parent / "locales"
_ = create_translator("recover", _locale_dir)


class RecoverStrings:
    """Strings shown by the recover window."""

    @property
    def WINDOW_TITLE(self) -> str:
        return _("Recover Project from Bus")

    @property
    def RANGE_START(self) -> str:
        return _("From Address")

    @property
    def RANGE_END(self) -> str:
        return _("To Address")

    @property
    def BTN_SCAN(self) -> str:
        return _("Scan")

    @property
    def BTN_STOP(self) -> str:
        return _("Stop")

    @property
    def AUTO_APPLY(self) -> str:
        return _("Add to Open Project When Read")

    @property
    def BTN_RECOVER_SELECTED(self) -> str:
        return _("Recover Selected")

    @property
    def BTN_ADD_NEW(self) -> str:
        return _("Create New Project")

    @property
    def BTN_ADD_MERGE(self) -> str:
        return _("Add to Open Project")

    @property
    def BTN_VERIFY(self) -> str:
        return _("Verify (Read-Only)")

    @property
    def BTN_EXPORT_SNAPSHOT(self) -> str:
        return _("Export Snapshot")

    @property
    def STATUS_VERIFYING(self) -> str:
        return _("Verifying (re-reading and comparing)…")

    @property
    def OVERVIEW_WARNINGS(self) -> str:
        return _("%(count)d group address(es) have no sender or multiple senders.")

    @property
    def COL_ADDRESS(self) -> str:
        return _("Address")

    @property
    def COL_MASK(self) -> str:
        return _("Mask")

    @property
    def COL_PRODUCT(self) -> str:
        return _("Product/Application")

    @property
    def COL_STATUS(self) -> str:
        return _("Status")

    @property
    def STATUS_IDLE(self) -> str:
        return _("Set an address range and scan the bus.")

    @property
    def STATUS_SCANNING(self) -> str:
        return _("Scanning the bus…")

    @property
    def STATUS_SCAN_PROGRESS(self) -> str:
        return _("Scanning %(address)s  (%(done)d/%(total)d, %(found)d found)")

    @property
    def STATUS_FETCHING(self) -> str:
        return _("Loading missing products from online catalog: %(what)s")

    @property
    def STATUS_RECOVERING(self) -> str:
        return _("Reading devices…")

    @property
    def STATUS_RECOVER_PROGRESS(self) -> str:
        return _("Reading %(address)s  (%(done)d/%(total)d)  -  %(stage)s")

    @property
    def STAGE_GROUP_COMMUNICATION(self) -> str:
        return _("group communication tables")

    @property
    def STAGE_PARAMETERS(self) -> str:
        return _("parameter memory")

    @property
    def OVERVIEW_RECOVERED(self) -> str:
        return _(
            "Recovered %(devices)d device(s): %(group_addresses)d group addresses, "
            "%(links)d links, %(unknown)d parameter(s) not recoverable."
        )

    @property
    def COL_DETAILS(self) -> str:
        return _("Result")

    @property
    def APPLY_MERGED(self) -> str:
        return _("Added %(count)d device(s) to the open project (%(target)s).")

    @property
    def APPLY_NEW(self) -> str:
        return _("Created new project %(target)s with %(count)d device(s).")

    @property
    def APPLY_FAILED(self) -> str:
        return _(
            "%(count)d device(s) could not be added; the reason is in the Result column."
        )

    @property
    def STATUS_NOT_CONNECTED(self) -> str:
        return _("Not connected to a KNX bus.")

    @property
    def STATUS_NO_PROJECT(self) -> str:
        return _("Open or create a project to add recovered devices.")

    @property
    def STATE_FOUND(self) -> str:
        return _("Product found")

    @property
    def STATE_AMBIGUOUS(self) -> str:
        return _("Check the product and version, then select it")

    @property
    def STATE_NO_APP(self) -> str:
        return _("Not in the catalog: import its product in the Catalog")

    @property
    def STATE_UNPROGRAMMED(self) -> str:
        return _("Not programmed")

    @property
    def STATE_RECOVERED(self) -> str:
        return _("Recovered")

    @property
    def STATE_EXISTS(self) -> str:
        return _("Already in the project, skipped")

    @property
    def STATE_ERROR(self) -> str:
        return _("Error")

    @property
    def STATE_INCOMPLETE(self) -> str:
        return _("Recovered (incomplete)")

    @property
    def SEARCH_EFFORT(self) -> str:
        return _("Search Effort")

    @property
    def SEARCH_EFFORT_HINT(self) -> str:
        return _(
            "How many values are tried for the settings a device keeps no copy of. More "
            "effort finds settings in devices with many channels, but takes longer. Unlimited "
            "goes on until no value fits better; Stop ends the search."
        )

    @property
    def EFFORT_FAST(self) -> str:
        return _("Fast")

    @property
    def EFFORT_MEDIUM(self) -> str:
        return _("Medium")

    @property
    def EFFORT_MAXIMUM(self) -> str:
        return _("Maximum")

    @property
    def EFFORT_UNLIMITED(self) -> str:
        return _("Unlimited")

    @property
    def BTN_SEARCH_AGAIN(self) -> str:
        return _("Search Again")

    @property
    def SEARCH_AGAIN_HINT(self) -> str:
        return _(
            "Searches the settings of the selected incomplete devices again in what was read "
            "from them, with the chosen search effort. Needs no bus. A device already added "
            "to the project is replaced (one undo step)."
        )

    @property
    def STATUS_SEARCH_PROGRESS(self) -> str:
        return _("Searching %(address)s again  (%(done)d/%(total)d)…")

    @property
    def DETAIL_INCOMPLETE(self) -> str:
        return _(
            "%(bytes)d byte(s) and %(objects)d group object(s) differ from the device"
        )

    @property
    def DETAIL_NOT_READ(self) -> str:
        return _("Not read yet")

    @property
    def DETAIL_READ(self) -> str:
        return _(
            "%(group_addresses)d group addresses, %(links)d links, %(parameters)d parameters"
        )

    @property
    def DETAIL_UNKNOWN(self) -> str:
        return _("%(count)d unknown")

    @property
    def DETAIL_VERIFIED(self) -> str:
        return _("Verified")

    @property
    def DETAIL_VERIFY_DIFFERS(self) -> str:
        return _("Verify: %(bytes)d byte(s) differ")

    @property
    def DETAIL_MORE_EFFORT(self) -> str:
        return _("more search effort may help")

    @property
    def PARAMS_UNKNOWN(self) -> str:
        return _("%d parameters could not be recovered")


S = RecoverStrings()
