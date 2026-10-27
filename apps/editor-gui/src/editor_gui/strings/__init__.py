"""App-wide and per-plugin i18n string registry."""

import gettext
import sys
from pathlib import Path

_current_locale: str = "en"


def set_locale(locale: str) -> None:
    global _current_locale
    _current_locale = locale


def get_locale() -> str:
    return _current_locale


def create_translator(domain: str, locale_dir: Path):
    # Cache the translation object per locale. gettext.translation() runs gettext.find(), which stats
    # the filesystem for .mo files on every call; a panel's render() reads dozens of S.* strings each
    # frame, so without this cache the UI spends most of its time stat-ing locale dirs. gettext itself
    # caches the parsed catalog, but only after find() has already walked the filesystem.
    cache: dict[str, gettext.NullTranslations] = {}

    def translate(text: str) -> str:
        trans = cache.get(_current_locale)
        if trans is None:
            trans = gettext.translation(
                domain, locale_dir, languages=[_current_locale], fallback=True
            )
            cache[_current_locale] = trans
        return trans.gettext(text)

    return translate


_base_locale_dir = Path(__file__).parent.parent / "locales"
_ = create_translator("editor_gui", _base_locale_dir)


def _on_mac() -> bool:
    """Whether shortcuts are those of a Mac; in the browser view the browser's platform counts."""
    from editor_gui import web

    hub = web.active_hub
    return hub.client_mac if hub is not None else sys.platform == "darwin"


def mac_keys() -> bool:
    """Whether the keyboard is a Mac's: Cmd for Ctrl, its Delete key sends Backspace."""
    return _on_mac()


def delete_key() -> str:
    """The key that deletes, as shortcut labels name it: Delete on a Mac, else Del."""
    if _on_mac():
        return "Delete"
    # Translators: the Del key as shortcut labels name it (German keyboards: "Entf").
    return _("Del")


def modifier_key() -> str:
    """The key shortcuts are held with, as the platform names it: Cmd on a Mac, else Ctrl."""
    if _on_mac():
        return "Cmd"
    # Translators: the Ctrl key as shortcut labels name it, e.g. "Ctrl+Z".
    return _("Ctrl")


def shortcut(key: str, *, mac: str | None = None) -> str:
    """A key combination as the platform writes it: ``Ctrl+key``, or on a Mac ``Cmd+key`` (``mac``
    replaces ``key`` there)."""
    return f"{modifier_key()}+{mac if mac and _on_mac() else key}"


class BaseStrings:
    @property
    def APP_TITLE(self) -> str:
        return _("XKNX Editor")

    @property
    def WINDOW_TITLE(self) -> str:
        return _("{project} - {app}")

    @property
    def WINDOW_TITLE_FILE(self) -> str:
        return _("{project} – {file} - {app}")  # noqa: RUF001

    @property
    def MENU_HELP(self) -> str:
        return _("Help")

    @property
    def MENU_FEEDBACK(self) -> str:
        return _("Feedback")

    @property
    def MENU_ABOUT(self) -> str:
        return _("About")

    @property
    def MENU_LANGUAGE(self) -> str:
        return _("Language")

    @property
    def PALETTE_NO_MATCH(self) -> str:
        return _("No matches")

    @property
    def ABOUT_TITLE(self) -> str:
        return _("About")

    @property
    def ABOUT_VERSION(self) -> str:
        return _("Version {version}")

    @property
    def ABOUT_TEXT(self) -> str:
        return _(
            "A desktop editor for building-automation installation projects, built on the "
            "open-source xknx library and based on xknxtoolkit by kewde. Not affiliated with "
            "or endorsed by the KNX Association."
        )

    @property
    def NETWORK_CONSENT_TITLE(self) -> str:
        return _("Network Location")

    @property
    def NETWORK_CONSENT_PROMPT(self) -> str:
        return _(
            "This location is on a network drive:\n{home}\n\n"
            "KNX project files use a database that cannot run reliably on network shares. The "
            "editor can work on a local copy instead and write it back to the network location "
            "when you close the project."
        )

    @property
    def NETWORK_CONSENT_USE_LOCAL(self) -> str:
        return _("Use a Local Copy")

    @property
    def MIRROR_NOTICE(self) -> str:
        return _(
            "Working on a local copy; written back to {home} when you close the project."
        )

    @property
    def BTN_ADD(self) -> str:
        return _("Add")

    @property
    def BTN_CLOSE(self) -> str:
        return _("Close")

    @property
    def BTN_CANCEL(self) -> str:
        return _("Cancel")

    @property
    def NEW_PROJECT_TITLE(self) -> str:
        return _("New Project")

    @property
    def NEW_PROJECT_NAME_LABEL(self) -> str:
        return _("Project Name")

    @property
    def NEW_PROJECT_STYLE_LABEL(self) -> str:
        return _("Group Address Style")

    @property
    def NEW_PROJECT_STYLE_FREE(self) -> str:
        return _("Free")

    @property
    def NEW_PROJECT_STYLE_TWO_LEVEL(self) -> str:
        return _("Two-level")

    @property
    def NEW_PROJECT_STYLE_THREE_LEVEL(self) -> str:
        return _("Three-level")

    @property
    def NEW_PROJECT_CREATE(self) -> str:
        return _("Create…")

    @property
    def BTN_COPY(self) -> str:
        return _("Copy")

    @property
    def BTN_CLEAR(self) -> str:
        return _("Clear")

    @property
    def BTN_STOP(self) -> str:
        return _("Stop")

    @property
    def FILE_DIALOG_ALL_FILES(self) -> str:
        return _("All Files")

    @property
    def STATUS_PROJECT(self) -> str:
        return _("Project: {name}  ·  {devices} devices  ·  {gas} GAs")

    @property
    def STATUS_NO_PROJECT(self) -> str:
        return _("No project open")

    @property
    def STATUS_PROGRAMMING(self) -> str:
        return _("Programming {address}…")

    @property
    def STATUS_READING(self) -> str:
        return _("Reading {address}…")

    @property
    def STATUS_PROGRAM_DONE(self) -> str:
        return _("Programming complete")

    @property
    def STATUS_PROGRAM_FAILED(self) -> str:
        return _("Programming failed")

    @property
    def STATUS_NO_CONNECTION(self) -> str:
        return _("No KNX connection")

    @property
    def STATUS_MASTER_DATA(self) -> str:
        return _("KNX Master v{version} ({date})")

    @property
    def SHORTCUT_UNDO(self) -> str:
        return shortcut("Z")

    @property
    def SHORTCUT_REDO(self) -> str:
        return shortcut("Y", mac="Shift+Z")

    @property
    def SHORTCUT_NEW(self) -> str:
        return shortcut("N")

    @property
    def SHORTCUT_OPEN(self) -> str:
        return shortcut("O")

    @property
    def SHORTCUT_EXPORT(self) -> str:
        return shortcut("E")

    @property
    def SHORTCUT_ZOOM_IN(self) -> str:
        return shortcut("+")

    @property
    def SHORTCUT_ZOOM_OUT(self) -> str:
        return shortcut("-")

    @property
    def SHORTCUT_ZOOM_RESET(self) -> str:
        return shortcut("0")


class MenuStrings:
    @property
    def MENU_FILE(self) -> str:
        return _("File")

    @property
    def MENU_NEW_PROJECT(self) -> str:
        return _("New Project")

    @property
    def MENU_OPEN_PROJECT(self) -> str:
        return _("Open Project (.knxproj/.xknx)")

    @property
    def MENU_SAVE_AS(self) -> str:
        return _("Save As…")

    @property
    def SAVE_AS_OK(self) -> str:
        return _("Saved as {name}")

    @property
    def MENU_OPEN_RECENT(self) -> str:
        return _("Open Recent")

    @property
    def MENU_SETTINGS(self) -> str:
        return _("Settings…")

    @property
    def PANEL_MCP(self) -> str:
        return _("MCP Server")

    @property
    def SETTINGS_TITLE(self) -> str:
        return _("Settings")

    @property
    def SETTINGS_TAB_GATEWAY(self) -> str:
        return _("Gateway")

    @property
    def SETTINGS_TAB_MCP(self) -> str:
        return _("MCP")

    @property
    def SETTINGS_TAB_CATALOG(self) -> str:
        return _("Catalog")

    @property
    def SETTINGS_ROUTING(self) -> str:
        return _("Use multicast routing (instead of tunneling)")

    @property
    def SETTINGS_CONTROLLER_IP(self) -> str:
        return _("Gateway IP")

    @property
    def SETTINGS_MULTICAST(self) -> str:
        return _("Multicast Group")

    @property
    def SETTINGS_MCP_HINT(self) -> str:
        return _(
            "Run the MCP server so an LLM can drive the editor over Streamable HTTP. "
            "It shares this app's catalog database."
        )

    @property
    def SETTINGS_MCP_HOST(self) -> str:
        return _("MCP Host")

    @property
    def SETTINGS_MCP_PORT(self) -> str:
        return _("MCP Port")

    @property
    def SETTINGS_MCP_TOKEN(self) -> str:
        return _("Bearer Token (Optional)")

    @property
    def SETTINGS_MCP_RUNNING(self) -> str:
        return _("Server: Running")

    @property
    def SETTINGS_MCP_STOPPED(self) -> str:
        return _("Server: Stopped")

    @property
    def MCP_PORT_IN_USE(self) -> str:
        return _(
            "Port {port} is already in use by another program, so the MCP server cannot start. "
            "Choose another port, or end the other program."
        )

    @property
    def MCP_START_FAILED(self) -> str:
        return _("The MCP server could not start: {error}")

    @property
    def SETTINGS_MCP_START(self) -> str:
        return _("Start Server")

    @property
    def SETTINGS_MCP_STOP(self) -> str:
        return _("Stop Server")

    @property
    def PANEL_KI(self) -> str:
        return _("AI MCP")

    @property
    def KI_INFO(self) -> str:
        return _(
            "This editor is controllable by an AI assistant (Claude, Codex, …) through the "
            "built-in MCP server: it exposes the catalog and project as tools over Streamable "
            "HTTP. Start the server below, then register its endpoint in your AI tool."
        )

    @property
    def KI_ENDPOINT(self) -> str:
        return _("MCP Endpoint")

    @property
    def KI_COMMANDS_HINT(self) -> str:
        return _("Example: register the MCP server in your AI tool (adjust host/port):")

    @property
    def KI_HOST_HINT(self) -> str:
        return _(
            "Use host 0.0.0.0 to make the server reachable from other devices on the "
            "network; 127.0.0.1/localhost only allows this machine."
        )

    @property
    def KI_HOST_PRESET_LOCAL(self) -> str:
        # Combo entry for host 127.0.0.1 (loopback only).
        return _("This Machine Only")

    @property
    def KI_HOST_PRESET_NETWORK(self) -> str:
        # Combo entry for host 0.0.0.0 (all interfaces).
        return _("On the Network")

    @property
    def KI_CLAUDE_CMD(self) -> str:
        return _("claude mcp add --transport http xknx-editor {url}")

    @property
    def KI_CLAUDE_CMD_AUTH(self) -> str:
        return _(
            "claude mcp add --transport http xknx-editor {url} "
            '--header "Authorization: Bearer {token}"'
        )

    @property
    def KI_CODEX_CMD(self) -> str:
        return _("codex mcp add xknx-editor --url {url}")

    @property
    def KI_CODEX_CMD_AUTH(self) -> str:
        return _(
            "export XKNX_MCP_TOKEN='{token}'\n"
            "codex mcp add xknx-editor --url {url} --bearer-token-env-var XKNX_MCP_TOKEN"
        )

    @property
    def KI_COPY(self) -> str:
        return _("Copy")

    @property
    def KI_COPIED(self) -> str:
        return _("Copied")

    @property
    def SETTINGS_CATALOG_LANGUAGE(self) -> str:
        return _("Download Language")

    @property
    def STATUS_MCP_RUNNING(self) -> str:
        return _("MCP running")

    @property
    def WELCOME_TITLE(self) -> str:
        return _("Welcome")

    @property
    def WELCOME_HINT(self) -> str:
        return _("Create a new project or open an existing one to get started.")

    @property
    def WELCOME_RECENT(self) -> str:
        return _("Recent Projects")

    @property
    def WELCOME_RECOVER(self) -> str:
        return _("Recover from Bus…")

    @property
    def WELCOME_CLOSE(self) -> str:
        return _("Close")

    @property
    def MENU_EXPORT_KNXPROJ(self) -> str:
        return _("Export .knxproj…")

    @property
    def MENU_EXPORT_ETS5_STORE(self) -> str:
        return _("Export to ETS 5 ProjectStore")

    @property
    def MENU_EXPORT_GA_EXPORT(self) -> str:
        return _("Export Group Addresses…")

    @property
    def MENU_IMPORT_GA(self) -> str:
        return _("Import Group Addresses…")

    @property
    def GA_EXPORT_OK(self) -> str:
        return _("Exported {count} group address(es)")

    @property
    def GA_EXPORT_OPTIONS_TITLE(self) -> str:
        return _("Export Group Addresses")

    @property
    def GA_EXPORT_OUTPUT_FORMAT(self) -> str:
        return _("Output Format")

    @property
    def GA_EXPORT_CSV_FORMAT(self) -> str:
        return _("CSV Format")

    @property
    def GA_EXPORT_CSV_3_1(self) -> str:
        return _("3/1 - three columns, Main/Middle/Sub group separated")

    @property
    def GA_EXPORT_CSV_1_3(self) -> str:
        return _("1/3 - Group address name/Main - Middle - Sub group")

    @property
    def GA_EXPORT_CSV_1_1(self) -> str:
        return _("1/1 - Name/Address")

    @property
    def GA_EXPORT_CSV_3_3(self) -> str:
        return _(
            "3/3 - Main - Middle - Sub group name/Main - Middle - Sub group address"
        )

    @property
    def GA_EXPORT_CSV_HEADER(self) -> str:
        return _("Export with Header Line")

    @property
    def GA_EXPORT_CSV_SEPARATOR(self) -> str:
        return _("CSV Separator")

    @property
    def GA_EXPORT_SEPARATOR_TAB(self) -> str:
        return _("Tabulator")

    @property
    def GA_EXPORT_SEPARATOR_COMMA(self) -> str:
        return _("Comma")

    @property
    def GA_EXPORT_SEPARATOR_SEMICOLON(self) -> str:
        return _("Semicolon")

    @property
    def BTN_EXPORT(self) -> str:
        return _("Export")

    @property
    def FILE_DIALOG_GA_EXPORT_CSV_FILTER(self) -> str:
        return _("Group Address Export (*.csv)")

    @property
    def EXPORT_DONE(self) -> str:
        return _("Exported to {path} ({size}) as {schema}")

    @property
    def EXPORT_SUMMARY_TITLE(self) -> str:
        return _("Export Complete")

    @property
    def EXPORT_SUMMARY_INTRO(self) -> str:
        return _("Successfully created {name}")

    @property
    def EXPORT_SUMMARY_FILE_SIZE(self) -> str:
        return _("File Size")

    @property
    def EXPORT_SUMMARY_PROJECT(self) -> str:
        return _("Project")

    @property
    def EXPORT_SUMMARY_FORMAT(self) -> str:
        return _("Format")

    @property
    def EXPORT_SUMMARY_DEVICES(self) -> str:
        return _("Devices")

    @property
    def EXPORT_SUMMARY_BUILDINGS(self) -> str:
        return _("Buildings")

    @property
    def EXPORT_SUMMARY_GROUP_ADDRESSES(self) -> str:
        return _("Group Addresses")

    @property
    def EXPORT_SUMMARY_GROUP_OBJECT_LINKS(self) -> str:
        return _("Group Object Links")

    @property
    def EXPORT_SUMMARY_TOPOLOGY(self) -> str:
        return _("Areas / Lines")

    @property
    def EXPORT_SUMMARY_FLOORS_ROOMS(self) -> str:
        return _("Floors / Rooms")

    @property
    def EXPORT_SUMMARY_FUNCTIONS(self) -> str:
        return _("Functions")

    @property
    def EXPORT_SUMMARY_MASTER_VERSION(self) -> str:
        return _("KNX Master Version")

    @property
    def EXPORT_SUMMARY_EXPORTED_AT(self) -> str:
        return _("Exported at")

    @property
    def EXPORT_SUMMARY_MANUFACTURERS(self) -> str:
        return _("Manufacturer Data (M Folders)")

    @property
    def EXPORT_SUMMARY_SIGNED(self) -> str:
        return _("Signed")

    @property
    def EXPORT_SUMMARY_CERTIFICATE(self) -> str:
        return _("Certificate")

    @property
    def EXPORT_SUMMARY_YES(self) -> str:
        return _("yes")

    @property
    def EXPORT_SUMMARY_NO(self) -> str:
        return _("no")

    @property
    def EXPORT_SUMMARY_OPEN_FOLDER(self) -> str:
        return _("Open Folder")

    @property
    def EXPORT_SUMMARY_CLOSE(self) -> str:
        return _("Close")

    @property
    def IMPORT_NOTES_TITLE(self) -> str:
        return _("Good to Know")

    @property
    def IMPORT_NOTES_INTRO_IMPORT(self) -> str:
        return _(
            "Your project opened fine and everything you need to work with is here. "
            "A few extra details from the original file are shown a bit more simply:"
        )

    @property
    def IMPORT_NOTES_INTRO_EXPORT(self) -> str:
        return _(
            "This project came from a file with a few extra details this editor does not "
            "use. The export leaves these out:"
        )

    @property
    def IMPORT_NOTES_CLOSE(self) -> str:
        return _("Close")

    @property
    def IMPORT_NOTE_MULTIPLE_INSTALLATIONS(self) -> str:
        return _(
            "The original file had {count} installations. They were combined into one."
        )

    @property
    def IMPORT_NOTE_UNASSIGNED_DEVICES(self) -> str:
        return _(
            "{count} device(s) aren't assigned to a line. They're kept in the project and "
            "included on export, but can't be edited here yet."
        )

    @property
    def IMPORT_NOTE_COM_OBJECT_TEXT_OVERRIDES(self) -> str:
        return _(
            "{count} group object(s) had an extra description in the original file. "
            "The custom name and function are kept; only that description is not shown here."
        )

    @property
    def IMPORT_NOTE_EXAMPLES(self) -> str:
        return _("For example: {examples}")

    @property
    def IMPORT_NOTE_IP_CONFIG(self) -> str:
        return _(
            "{count} IP or function setting(s) from the original file are not used here."
        )

    @property
    def IMPORT_NOTE_MULTI_SEGMENT(self) -> str:
        return _(
            "{count} line(s) use several segments. In a schema 14/20 export they are "
            "shown at line level only."
        )

    @property
    def IMPORT_NOTE_DROPPED_DUPLICATE_LINES(self) -> str:
        return _(
            "{count} line(s) had the same address in different installations. "
            "One of each was kept when combining."
        )

    @property
    def EXPORT_PROGRESS_TEXT(self) -> str:
        return _("Exporting {name} — this can take a while for large projects.")

    @property
    def EXPORT_STAGE_BUNDLE(self) -> str:
        return _("Collecting the manufacturer data…")

    @property
    def EXPORT_STAGE_WRITE(self) -> str:
        return _("Writing the archive…")

    @property
    def EXPORT_STAGE_CERTIFICATE(self) -> str:
        return _("Getting the certificate from MyKNX…")

    @property
    def EXPORT_STAGE_CANCELLING(self) -> str:
        return _("Cancelling…")

    @property
    def EXPORT_CANCELLED(self) -> str:
        return _("Export cancelled, no file was written")

    @property
    def EXPORT_FAILED_TITLE(self) -> str:
        return _("Export Failed")

    @property
    def EXPORT_FAILED_TEXT(self) -> str:
        return _("The project could not be exported to {name}.")

    @property
    def EXPORT_FAILED_CERTIFICATE(self) -> str:
        return _("MyKNX did not issue the certificate for the project:")

    @property
    def EXPORT_FAILED_CERTIFICATE_HINT(self) -> str:
        return _(
            "To export without it, choose 'Export Without Certificate' or the 'License with a "
            "Dongle' tab in the Sign Export window."
        )

    @property
    def EXPORT_FAILED_NOT_IMPORTABLE(self) -> str:
        return _("{name} cannot be imported.")

    @property
    def EXPORT_FAILED_MISSING(self) -> str:
        return _(
            "The file was written, but it refers to product data that is not in the catalog: "
            "{references}. Add these products to the catalog and export again."
        )

    @property
    def MYKNX_SIGN_TITLE(self) -> str:
        return _("Sign Export")

    @property
    def MYKNX_SIGN_PROMPT(self) -> str:
        return _(
            "ETS 6: needs a licensed .knxproj through a MyKNX login or a dongle.\n"
            "ETS 5: just use 'Export Without Certificate'."
        )

    @property
    def MYKNX_SIGN_TAB(self) -> str:
        return _("Sign with MyKNX Login")

    @property
    def MYKNX_DONGLE_TAB(self) -> str:
        return _("License with a Dongle")

    @property
    def MYKNX_SIGN_ETS_RUNNING(self) -> str:
        return _(
            "ETS must not be running on any computer with this MyKNX account while you log in "
            "and export here."
        )

    @property
    def MYKNX_SIGN_USERNAME(self) -> str:
        return _("MyKNX Username")

    @property
    def MYKNX_SIGN_PASSWORD(self) -> str:
        return _("MyKNX Password")

    @property
    def MYKNX_SIGN_LOGIN(self) -> str:
        return _("Log in")

    @property
    def MYKNX_SIGN_2FA_WAITING(self) -> str:
        return _("Complete sign-in in your browser. Code: {code}")

    @property
    def MYKNX_2FA_PROMPT(self) -> str:
        return _(
            "Login failed (HTTP 401). Either the password is wrong, or this account uses "
            "two-factor authentication - the server does not say which."
        )

    @property
    def MYKNX_2FA_REENTER(self) -> str:
        return _("Re-enter Credentials")

    @property
    def MYKNX_2FA_START(self) -> str:
        return _("Start 2FA Sign-In")

    @property
    def MYKNX_OAUTH_TITLE(self) -> str:
        return _("MyKNX OAuth Credentials (2FA)")

    @property
    def MYKNX_OAUTH_HELP(self) -> str:
        return _(
            "Needed only for accounts with two-factor authentication. The 2FA sign-in opens in "
            "your browser automatically when a normal login is rejected. Enter the OAuth client "
            "id/secret, or extract them from your installed ETS: pick 'Knx.Ets.MyKnx.dll' from the "
            "ETS program folder (e.g. C:\\Program Files (x86)\\ETS6\\Knx.Ets.MyKnx.dll).\n"
            "\n"
            "Alternatively, if you have any licensed ETS installed, skip online signing entirely "
            "and use the local project store instead - see the 'License with a Dongle' tab."
        )

    @property
    def MYKNX_OAUTH_CLIENT_ID(self) -> str:
        return _("Client ID")

    @property
    def MYKNX_OAUTH_CLIENT_SECRET(self) -> str:
        return _("Client Secret")

    @property
    def MYKNX_OAUTH_EXTRACT(self) -> str:
        return _("Extract from ETS…")

    @property
    def MYKNX_OAUTH_EXTRACTING(self) -> str:
        return _("Extracting…")

    @property
    def MYKNX_OAUTH_SAVE(self) -> str:
        return _("Save")

    @property
    def MYKNX_OAUTH_ALL_FILES(self) -> str:
        return _("All files")

    @property
    def MYKNX_SIGN_LOGGING_IN(self) -> str:
        return _("Logging in…")

    @property
    def MYKNX_SIGN_LICENSE(self) -> str:
        return _("License")

    @property
    def MYKNX_SIGN_NO_LICENSES(self) -> str:
        return _("No licenses found on this account")

    @property
    def MYKNX_SIGN_NO_CLOUD(self) -> str:
        return _("no online certificate")

    @property
    def MYKNX_SIGN_EXPIRED(self) -> str:
        return _("expired")

    @property
    def MYKNX_SIGN_NO_CLOUD_HINT(self) -> str:
        return _(
            "None of these licenses can sign online. Only ETS6 (or a cloud-enabled ETS app) "
            "licenses produce a certificate; ETS5/older licenses cannot. Use 'License with a "
            "dongle' instead."
        )

    @property
    def MYKNX_SIGN_LOGIN_FAILED(self) -> str:
        return _("Login failed: {error}")

    @property
    def EXPORT_FORMAT(self) -> str:
        return _("Format Version")

    @property
    def EXPORT_INCOMPAT_REMOVE(self) -> str:
        return _("Remove device(s) & re-export")

    @property
    def EXPORT_INCOMPAT_REMOVED(self) -> str:
        return _("Removed {count} incompatible device(s); re-exporting")

    @property
    def EXPORT_SUMMARY_INCOMPAT(self) -> str:
        return _(
            "This file will NOT import into the chosen ETS version: the product data of these "
            "manufacturers only exists for a newer ETS (e.g. an ETS 6 product in an ETS 5 "
            "export), so ETS rejects the whole project. Remove the affected device(s) or export "
            "for the newer ETS."
        )

    @property
    def EXPORT_FORMAT_ETS6(self) -> str:
        return _("ETS 6 (schema 23)")

    @property
    def EXPORT_FORMAT_ETS5(self) -> str:
        return _("ETS 5 (schema 20)")

    @property
    def SIGNING_KEY_BUTTON(self) -> str:
        return _("Signing Key…")

    @property
    def SIGNING_KEY_TITLE(self) -> str:
        return _("Signing Key")

    @property
    def SIGNING_KEY_CLOSE(self) -> str:
        return _("Close")

    @property
    def SIGNING_KEY_PLACEHOLDER_HINT(self) -> str:
        return _(
            "Folder signature uses a placeholder key. Enter a valid signature under 'Signing "
            "Key…' or use the 'License with a Dongle' way."
        )

    @property
    def MYKNX_DONGLE_EXPORT_STORE(self) -> str:
        return _("Export to ProjectStore")

    @property
    def MYKNX_DONGLE_PICK_STORE(self) -> str:
        return _("Select the {ets} ProjectStore folder")

    @property
    def MYKNX_DONGLE_PICK_INSTALLER(self) -> str:
        return _("Select the ETS installer.guid file")

    @property
    def MYKNX_DONGLE_INSTALLER_FILTER(self) -> str:
        return _("ETS installer id")

    @property
    def MYKNX_DONGLE_EXISTS_TITLE(self) -> str:
        return _("Project Already in the Store")

    @property
    def MYKNX_DONGLE_EXISTS_PROMPT(self) -> str:
        return _(
            "A project folder '{pid}' already exists in this ETS ProjectStore. Overwrite it, "
            "or add it as a separate copy?"
        )

    @property
    def MYKNX_DONGLE_OVERWRITE(self) -> str:
        return _("Overwrite")

    @property
    def MYKNX_DONGLE_NEW_COPY(self) -> str:
        return _("Add Copy")

    @property
    def MYKNX_DONGLE_COPY_SUFFIX(self) -> str:
        return _("XKNX Export")

    @property
    def MYKNX_DONGLE_STEPS(self) -> str:
        return _(
            "Re-license the project through your own licensed ETS, which re-signs the copy with "
            "your dongle license:\n"
            "\n"
            "ETS 6 - use the local project store:\n"
            "1. Click 'Export to ProjectStore'. It writes the project's 'P-XXXX' folder "
            "straight into your ETS 6 project store. If ETS is not installed on this computer, you "
            "are asked for the store folder (or any folder you then copy over manually).\n"
            "2. The export needs your 'installer.guid' from C:\\ProgramData\\KNX\\ETS6\\Installer. "
            "If it cannot be found automatically, you are asked to select it.\n"
            "3. Restart ETS 6 - the project appears in the project overview.\n"
            "4. Make a copy of it there (Copy in the overview). The copy is re-signed with your "
            "license - open it, then use or export it normally.\n"
            "\n"
            "ETS 5 - no project store is needed:\n"
            "Choose 'Export Without Certificate' (with ETS 5 "
            "as version) and an active signing key to save a .knxproj, then import that file into "
            "ETS 5.\n"
            "\n"
            "This is bounded by the device-per-project limit of your license."
        )

    @property
    def MYKNX_SIGN_CONFIRM(self) -> str:
        return _("Sign & Export")

    @property
    def MYKNX_SIGN_SKIP(self) -> str:
        return _("Export Without Certificate")

    @property
    def FILE_DIALOG_KNXPROJ_SAVE_TITLE(self) -> str:
        return _("Export ETS Project")

    @property
    def FILE_DIALOG_GA_EXPORT_SAVE_TITLE(self) -> str:
        return _("Export Group Addresses")

    @property
    def MENU_LOAD_KNXPROD(self) -> str:
        return _("Load .knxprod…")

    @property
    def MENU_LOAD_FROM_URL(self) -> str:
        return _("Load OpenKNX from URL or Folder…")

    @property
    def MENU_LOAD_FROM_URL_FILES(self) -> str:
        return _("Load OpenKNX from URL or Files…")

    @property
    def URL_PROMPT_TITLE(self) -> str:
        return _("Load OpenKNX from URL or Folder")

    @property
    def URL_PROMPT_TITLE_FILES(self) -> str:
        return _("Load OpenKNX from URL or Files")

    @property
    def URL_PROMPT_HINT(self) -> str:
        return _(
            "Paste an OpenKNX GitHub release URL (the release page works — the .zip asset is found "
            "automatically). A direct .knxprod or KNX product XML link also works.\n"
            "\n"
            "Examples:\n"
            "  https://github.com/OpenKNX/OAM-RaumController/releases/tag/v5.4.3\n"
            "  https://github.com/OpenKNX/OAM-RaumController/releases/latest"
        )

    @property
    def URL_PROMPT_FOLDER_HINT(self) -> str:
        return _(
            "Or choose a local folder with product files (.knxprod, OpenKNX release .zip, or "
            "KNX product XML) to import all of them."
        )

    @property
    def URL_PROMPT_BROWSE_FOLDER(self) -> str:
        return _("Choose Folder…")

    @property
    def URL_PROMPT_FILES_HINT(self) -> str:
        return _(
            "Or upload product files (.knxprod, OpenKNX release .zip, or KNX product XML) from "
            "this computer to import all of them."
        )

    @property
    def URL_PROMPT_CHOOSE_FILES(self) -> str:
        return _("Choose Files…")

    @property
    def URL_PROMPT_LOAD(self) -> str:
        return _("Load")

    @property
    def URL_PROMPT_CANCEL(self) -> str:
        return _("Cancel")

    @property
    def URL_DOWNLOADING(self) -> str:
        return _("Downloading and importing…")

    @property
    def URL_IMPORT_EMPTY(self) -> str:
        return _("Nothing new imported (already in the catalog)")

    @property
    def URL_IMPORT_FAILED(self) -> str:
        return _("Import from URL failed: {error}")

    @property
    def FOLDER_IMPORT_TITLE(self) -> str:
        return _("Choose a Folder with KNX Product Files")

    @property
    def FOLDER_IMPORTING(self) -> str:
        return _("Importing from folder…")

    @property
    def FOLDER_IMPORT_EMPTY(self) -> str:
        return _(
            "Nothing new imported from folder (no product files found, or already in the catalog)"
        )

    @property
    def FOLDER_IMPORT_FAILED(self) -> str:
        return _("Import from folder failed: {error}")

    @property
    def FILES_IMPORT_TITLE(self) -> str:
        return _("Choose KNX Product Files")

    @property
    def FILES_IMPORT_FILTER(self) -> str:
        return _("KNX Product Files (*.knxprod, *.zip, *.xml)")

    @property
    def FILES_IMPORTING(self) -> str:
        return _("Importing files…")

    @property
    def FILES_IMPORT_EMPTY(self) -> str:
        return _(
            "Nothing new imported from the files (no product files, or already in the catalog)"
        )

    @property
    def FILES_IMPORT_FAILED(self) -> str:
        return _("Import of the files failed: {error}")

    @property
    def MENU_EXIT(self) -> str:
        return _("Exit")

    @property
    def MENU_EDIT(self) -> str:
        return _("Edit")

    @property
    def MENU_VIEW(self) -> str:
        return _("View")

    @property
    def MENU_RESTORE_LAYOUT(self) -> str:
        return _("Restore Default Layout")

    @property
    def MENU_SHOW_ALL(self) -> str:
        return _("Show All")

    @property
    def MENU_HIDE_ALL(self) -> str:
        return _("Hide All")

    @property
    def MENU_HIGHLIGHT_CHANGES(self) -> str:
        return _("Highlight Changes")

    @property
    def MENU_CONTEXT_HELP(self) -> str:
        return _("Context Help")

    @property
    def MENU_STATUS_BAR(self) -> str:
        return _("Status Bar")

    @property
    def MENU_STATUS_FPS(self) -> str:
        return _("FPS in Status Bar")

    @property
    def MENU_ENABLE_IDLING(self) -> str:
        return _("Enable Idling")

    @property
    def STATUS_FPS(self) -> str:
        return _("FPS: {fps:.1f}{idling}")

    @property
    def STATUS_IDLING(self) -> str:
        return _(" (Idling)")

    @property
    def MENU_THEME(self) -> str:
        return _("Theme")

    @property
    def MENU_THEME_LIGHT(self) -> str:
        return _("Light Theme")

    @property
    def MENU_THEME_DARK(self) -> str:
        return _("Dark Theme")

    @property
    def MENU_THEME_CUSTOMIZE(self) -> str:
        return _("Customize Theme…")

    @property
    def THEME_WINDOW_TITLE(self) -> str:
        return _("Customize Theme")

    @property
    def THEME_TAB_SIZES(self) -> str:
        return _("Sizes")

    @property
    def THEME_TAB_COLORS(self) -> str:
        return _("Colors")

    @property
    def THEME_TAB_RENDERING(self) -> str:
        return _("Rendering")

    @property
    def THEME_SECTION_ADJUSTMENTS(self) -> str:
        return _("Adjustments")

    @property
    def THEME_DEFAULT(self) -> str:
        return _("Theme Default")

    @property
    def THEME_RESET(self) -> str:
        return _("Reset to Theme")

    @property
    def THEME_ROUNDING(self) -> str:
        return _("Rounding")

    @property
    def THEME_SCROLLBAR_ROUNDING_RATIO(self) -> str:
        return _("Scrollbar Rounding Ratio")

    @property
    def THEME_OPACITY(self) -> str:
        return _("Opacity")

    @property
    def THEME_HUE(self) -> str:
        return _("Hue")

    @property
    def THEME_SATURATION(self) -> str:
        return _("Saturation")

    @property
    def THEME_BRIGHTNESS_WIDGETS(self) -> str:
        return _("Brightness of Controls")

    @property
    def THEME_BRIGHTNESS_BACKGROUNDS(self) -> str:
        return _("Brightness of Backgrounds")

    @property
    def THEME_BRIGHTNESS_TEXT(self) -> str:
        return _("Brightness of Text")

    @property
    def THEME_BRIGHTNESS_FIELDS(self) -> str:
        return _("Brightness of Input Fields")

    @property
    def THEME_SECTION_SPACING(self) -> str:
        return _("Spacing")

    @property
    def THEME_SECTION_BORDERS(self) -> str:
        return _("Borders")

    @property
    def THEME_SECTION_ALIGNMENT(self) -> str:
        return _("Alignment")

    @property
    def THEME_WINDOW_PADDING(self) -> str:
        return _("Window Padding")

    @property
    def THEME_FRAME_PADDING(self) -> str:
        return _("Field Padding")

    @property
    def THEME_CELL_PADDING(self) -> str:
        return _("Cell Padding")

    @property
    def THEME_ITEM_SPACING(self) -> str:
        return _("Item Spacing")

    @property
    def THEME_ITEM_INNER_SPACING(self) -> str:
        return _("Item Inner Spacing")

    @property
    def THEME_TOUCH_PADDING(self) -> str:
        return _("Extra Touch Padding")

    @property
    def THEME_INDENT(self) -> str:
        return _("Indent")

    @property
    def THEME_SCROLLBAR_SIZE(self) -> str:
        return _("Scrollbar Width")

    @property
    def THEME_GRAB_SIZE(self) -> str:
        return _("Minimum Slider Grip Size")

    @property
    def THEME_WINDOWS(self) -> str:
        return _("Windows")

    @property
    def THEME_CHILD_WINDOWS(self) -> str:
        return _("Child Windows")

    @property
    def THEME_POPUPS(self) -> str:
        return _("Popups")

    @property
    def THEME_FIELDS(self) -> str:
        return _("Fields")

    @property
    def THEME_TABS(self) -> str:
        return _("Tabs")

    @property
    def THEME_TAB_BAR(self) -> str:
        return _("Tab Bar")

    @property
    def THEME_SCROLLBARS(self) -> str:
        return _("Scrollbars")

    @property
    def THEME_SLIDER_GRIPS(self) -> str:
        return _("Slider Grips")

    @property
    def THEME_WINDOW_TITLE_ALIGN(self) -> str:
        return _("Window Title")

    @property
    def THEME_BUTTON_TEXT_ALIGN(self) -> str:
        return _("Button Text")

    @property
    def THEME_SELECTABLE_TEXT_ALIGN(self) -> str:
        return _("List Entry Text")

    @property
    def THEME_SEPARATOR_TEXT_ALIGN(self) -> str:
        return _("Separator Text")

    @property
    def THEME_FONT_SCALE(self) -> str:
        return _("Font Scale")

    @property
    def THEME_ANTI_ALIASED_LINES(self) -> str:
        return _("Anti-Aliased Lines")

    @property
    def THEME_ANTI_ALIASED_FILL(self) -> str:
        return _("Anti-Aliased Fills")

    @property
    def THEME_CURVE_ERROR(self) -> str:
        return _("Maximum Curve Tessellation Error")

    @property
    def THEME_CIRCLE_ERROR(self) -> str:
        return _("Maximum Circle Tessellation Error")

    @property
    def THEME_GLOBAL_OPACITY(self) -> str:
        return _("Global Opacity")

    @property
    def THEME_DISABLED_OPACITY(self) -> str:
        return _("Opacity of Disabled Items")

    @property
    def THEME_DEMO_WINDOW(self) -> str:
        return _("Show ImGui Demo Window")

    @property
    def THEME_FILTER_COLORS(self) -> str:
        return _("Filter colors")

    @property
    def MENU_UNDO(self) -> str:
        return _("Undo")

    @property
    def MENU_REDO(self) -> str:
        return _("Redo")

    @property
    def FILE_DIALOG_KNXPROD_TITLE(self) -> str:
        return _("Open KNX Product Archive")

    @property
    def FILE_DIALOG_KNXPROD_FILTER(self) -> str:
        return _("KNX Product (*.knxprod, *.vd1-*.vd5)")

    @property
    def LEGACY_VD_HINT(self) -> str:
        return _(
            "Legacy ETS product databases (.vd1-.vd5) are not imported directly.\n\n"
            "Convert the file to .knxprod first with the conversion tools that ship with "
            "ETS5/ETS6 - open the product file in ETS, or use its command-line converter - "
            "then import the resulting .knxprod here."
        )

    @property
    def FILE_DIALOG_KNXPROJ_FILTER(self) -> str:
        return _("ETS Project (*.knxproj)")

    @property
    def FILE_DIALOG_ETS6_FILTER(self) -> str:
        return _("ETS 6 Project (*.knxproj)")

    @property
    def FILE_DIALOG_ETS5_FILTER(self) -> str:
        return _("ETS 5 Project (*.knxproj)")

    @property
    def FILE_DIALOG_GA_IMPORT_FILTER(self) -> str:
        return _("Group Address Exports (*.xml, *.csv)")

    @property
    def FILE_DIALOG_GA_EXPORT_FILTER(self) -> str:
        return _("Group Address Export (*.xml)")

    @property
    def FILE_DIALOG_PROJECT_TITLE(self) -> str:
        return _("Open XKNX Project")

    @property
    def FILE_DIALOG_PROJECT_SAVE_TITLE(self) -> str:
        return _("Save XKNX Project")

    @property
    def FILE_DIALOG_SAVE_AS_TITLE(self) -> str:
        return _("Save Project As")

    @property
    def OVERWRITE_TITLE(self) -> str:
        return _("Replace File")

    @property
    def OVERWRITE_HEADING(self) -> str:
        return _("{name} already exists.")

    @property
    def OVERWRITE_PROMPT(self) -> str:
        return _(
            "It is in {dir}. Replacing it overwrites its current content; this cannot be undone."
        )

    @property
    def BTN_OVERWRITE(self) -> str:
        return _("Replace")

    @property
    def FILE_DIALOG_PROJECT_FILTER(self) -> str:
        return _("XKNX Project (*.xknx)")

    @property
    def FILE_DIALOG_OPEN_FILTER(self) -> str:
        return _("Projects (*.xknx, *.knxproj, *.xml, *.csv)")

    @property
    def PROGRESS_TITLE(self) -> str:
        return _("Working…")

    @property
    def IMPORT_PROGRESS_TEXT(self) -> str:
        return _("Importing project — this can take a while for large projects.")

    @property
    def PROGRESS_DEVICES(self) -> str:
        return _("{done}/{total} devices")

    @property
    def PROGRESS_LOAD_KNXPROD(self) -> str:
        return _("Loading product catalog…")

    @property
    def KNXPROD_IMPORT_SUMMARY(self) -> str:
        return _("Imported {count} product version(s) from {name}:")

    @property
    def KNXPROD_DETAIL_SECURE(self) -> str:
        return _("KNX Secure")

    @property
    def KNXPROD_DETAIL_RAIL(self) -> str:
        return _("DIN rail")

    @property
    def KNXPROD_DETAIL_FLUSH(self) -> str:
        return _("Flush-mounted")

    @property
    def KNXPROD_DETAIL_REGISTERED(self) -> str:
        return _("Registered: {info}")

    @property
    def KNXPROD_COL_MANUFACTURER(self) -> str:
        return _("Manufacturer")

    @property
    def KNXPROD_COL_PRODUCT(self) -> str:
        return _("Product")

    @property
    def KNXPROD_COL_ORDER(self) -> str:
        return _("Order no.")

    @property
    def KNXPROD_COL_APPLICATION(self) -> str:
        return _("Application")

    @property
    def KNXPROD_COL_VERSION(self) -> str:
        return _("Version")

    @property
    def KNXPROD_COL_MASK(self) -> str:
        return _("Mask")

    @property
    def KNXPROD_COL_HARDWARE(self) -> str:
        return _("Hardware")

    @property
    def KNXPROD_COL_REGISTERED(self) -> str:
        return _("Registered")

    @property
    def KNXPROD_IMPORT_NOTHING_NEW(self) -> str:
        return _("{name} was already in the catalog, nothing new imported")

    @property
    def KNXPROD_IMPORT_TITLE(self) -> str:
        return _("Product Catalog Import")

    @property
    def PROGRESS_OPEN_PROJECT(self) -> str:
        return _("Opening project…")

    @property
    def PROGRESS_IMPORT_GA_EXPORT(self) -> str:
        return _("Importing group addresses…")

    @property
    def PROGRESS_LANGUAGE(self) -> str:
        return _("Switching language…")

    @property
    def PROGRESS_CANCELLING(self) -> str:
        return _("Cancelling…")

    @property
    def IMPORT_PASSWORD_TITLE(self) -> str:
        return _("Project Password")

    @property
    def IMPORT_PASSWORD_PROMPT(self) -> str:
        return _("This ETS project is password protected. Enter its password:")

    @property
    def IMPORT_PASSWORD_WRONG(self) -> str:
        return _("Wrong password, please try again.")

    @property
    def OPEN_PASSWORD_PROMPT(self) -> str:
        return _(
            "This project is protected with a password. Enter it to open the project:"
        )

    @property
    def NEW_PASSWORD_PROMPT(self) -> str:
        return _(
            "This project contains KNX Secure keys. Set a project password to protect them; the "
            "project can then only be opened with it."
        )

    @property
    def PASSWORD_LABEL(self) -> str:
        return _("Password")

    @property
    def PASSWORD_REPEAT_LABEL(self) -> str:
        return _("Repeat Password")

    @property
    def PASSWORD_MISMATCH(self) -> str:
        return _("The passwords do not match.")

    @property
    def PROGRESS_PROTECT_PROJECT(self) -> str:
        return _("Protecting the project with the password…")

    @property
    def BTN_OK(self) -> str:
        return _("OK")

    @property
    def BTN_CANCEL(self) -> str:
        return _("Cancel")

    # --- update check ---------------------------------------------------
    @property
    def MENU_CHECK_UPDATES(self) -> str:
        return _("Check for Updates")

    @property
    def MENU_UPDATE_ON_STARTUP(self) -> str:
        return _("Check for Updates on Startup")

    @property
    def UPDATE_TITLE(self) -> str:
        return _("Update Available")

    @property
    def UPDATE_AVAILABLE(self) -> str:
        return _("Version {latest} is available. You have {current}.")

    @property
    def UPDATE_UP_TO_DATE(self) -> str:
        return _("You are up to date (version {version}).")

    @property
    def UPDATE_DOWNLOAD(self) -> str:
        return _("Download")

    @property
    def UPDATE_INSTALL(self) -> str:
        return _("Install Update")

    @property
    def UPDATE_INSTALL_HINT(self) -> str:
        return _(
            "Install Update downloads the installer, closes XKNX Editor and starts it again "
            "with the open project."
        )

    @property
    def PROGRESS_DOWNLOAD_UPDATE(self) -> str:
        return _("Downloading the update…")

    @property
    def UPDATE_DOWNLOAD_PROGRESS(self) -> str:
        return _("{done} of {total} MB")

    @property
    def UPDATE_SKIP(self) -> str:
        return _("Skip This Version")

    @property
    def UPDATE_LATER(self) -> str:
        return _("Later")

    @property
    def UPDATE_NOTES_HEADER(self) -> str:
        return _("Release Notes:")

    @property
    def ABOUT_LICENSE(self) -> str:
        return _("License: GPL-2.0-only")

    @property
    def ABOUT_WEBSITE(self) -> str:
        return _("Website:")

    @property
    def MENU_OPEN_CONFIG_DIR(self) -> str:
        return _("Open Config Folder")

    @property
    def MENU_OPEN_CACHE_DIR(self) -> str:
        return _("Open Cache Folder")

    @property
    def MENU_OPEN_LOG_DIR(self) -> str:
        return _("Open Log Folder")

    # --- compact project ------------------------------------------------
    @property
    def MENU_COMPACT_PROJECT(self) -> str:
        return _("Compact Project…")

    @property
    def COMPACT_TITLE(self) -> str:
        return _("Compact Project")

    @property
    def PROGRESS_COMPACT_ANALYSE(self) -> str:
        return _("Analysing project…")

    @property
    def PROGRESS_COMPACT(self) -> str:
        return _("Compacting project…")

    @property
    def COMPACT_STEP_DEVICES(self) -> str:
        return _("Analysing devices")

    @property
    def COMPACT_STEP_ANALYSE(self) -> str:
        return _("Analysing product data ({manufacturer})")

    @property
    def COMPACT_STEP_REMOVE(self) -> str:
        return _("Removing unused product data ({manufacturer})")

    @property
    def COMPACT_STEP_STORE(self) -> str:
        return _("Saving product data")

    @property
    def COMPACT_STEP_VACUUM(self) -> str:
        return _("Compacting project file")

    @property
    def COMPACT_INTRO(self) -> str:
        return _(
            "The following product data is not used by any device in this project and will be "
            "removed:"
        )

    @property
    def COMPACT_NOTHING_UNUSED(self) -> str:
        return _(
            "All product data in this project is used by its devices, nothing to remove. The "
            "project file can still be compacted to free unused space."
        )

    @property
    def COMPACT_APPLICATIONS(self) -> str:
        return _("Application Programs")

    @property
    def COMPACT_PRODUCTS(self) -> str:
        return _("Device Types (Products)")

    @property
    def COMPACT_HARDWARE(self) -> str:
        return _("Hardware")

    @property
    def COMPACT_MANUFACTURERS(self) -> str:
        return _("Manufacturers")

    @property
    def COMPACT_CATALOG_ITEMS(self) -> str:
        return _("Catalog Entries")

    @property
    def COMPACT_BAGGAGES(self) -> str:
        return _("Help and Plug-in Files")

    @property
    def COMPACT_DETAILS(self) -> str:
        return _("Details")

    @property
    def COMPACT_SIZE_NOW(self) -> str:
        return _("Current File Size")

    @property
    def COMPACT_SIZE_ESTIMATE(self) -> str:
        return _("Estimated Size Afterwards")

    @property
    def COMPACT_WARNING(self) -> str:
        return _(
            "This cannot be undone. Removed product data has to be imported again if these "
            "products are added to the project later."
        )

    @property
    def COMPACT_CONFIRM(self) -> str:
        return _("Compact")

    @property
    def COMPACT_RESULT_TITLE(self) -> str:
        return _("Project Compacted")

    @property
    def COMPACT_RESULT_TEXT(self) -> str:
        return _("The project file was compacted.")

    @property
    def COMPACT_SIZE_BEFORE(self) -> str:
        return _("Size before")

    @property
    def COMPACT_SIZE_AFTER(self) -> str:
        return _("Size after")

    @property
    def COMPACT_CANCELLED(self) -> str:
        return _("Compacting cancelled, the project was not changed")

    @property
    def COMPACT_CLOSE(self) -> str:
        return _("Close")

    @property
    def EXPORT_REMOVE_UNUSED(self) -> str:
        return _("Remove Unused Product Data")

    @property
    def EXPORT_REMOVE_UNUSED_HELP(self) -> str:
        return _("(only in the export, the project stays unchanged)")

    @property
    def DOCK_HIDE_TAB_BAR(self) -> str:
        return _("Hide Tab Bar")

    @property
    def LINK_OPEN(self) -> str:
        return _("Open {url}")

    @property
    def LINK_COPY(self) -> str:
        return _("Copy Link")

    @property
    def WEB_LOGIN_HINT(self) -> str:
        return _("Enter the password for access to the editor.")

    @property
    def WEB_LOGIN_PASSWORD(self) -> str:
        return _("Password")

    @property
    def WEB_LOGIN_BUTTON(self) -> str:
        return _("Sign In")

    @property
    def WEB_LOGIN_WRONG(self) -> str:
        return _("Wrong password.")

    @property
    def WEB_RECONNECTING(self) -> str:
        return _("Disconnected. Reconnecting…")

    @property
    def WEB_DENIED(self) -> str:
        return _("Access denied. Please sign in again.")

    @property
    def WEB_UPLOAD(self) -> str:
        return _("Upload from This Computer…")

    @property
    def WEB_DROP_HINT(self) -> str:
        return _("Or drop files here.")

    @property
    def WEB_SERVER_FILES(self) -> str:
        return _("Files on the Server")

    @property
    def WEB_NO_FILES(self) -> str:
        return _("No files yet.")

    @property
    def WEB_OPEN(self) -> str:
        return _("Open")

    @property
    def WEB_SAVE(self) -> str:
        return _("Save")

    @property
    def WEB_SELECT(self) -> str:
        return _("Select")

    @property
    def WEB_FILE_NAME(self) -> str:
        return _("File Name")

    @property
    def WEB_FOLDER_NAME(self) -> str:
        return _("Folder Name")

    @property
    def WEB_FILE_TYPE(self) -> str:
        return _("File Type")

    @property
    def WEB_DOWNLOAD_AFTER(self) -> str:
        return _("Download to This Computer")

    @property
    def WEB_UPLOADING(self) -> str:
        return _("Uploading {name}…")

    @property
    def WEB_UPLOAD_FAILED(self) -> str:
        return _("Upload failed: {error}")

    @property
    def WEB_DOWNLOAD(self) -> str:
        return _("Download")

    @property
    def WEB_SHOW(self) -> str:
        return _("Show")

    @property
    def WEB_FOLDER_TITLE(self) -> str:
        return _("Folder on the Server")

    @property
    def WEB_VIEW_ONLY(self) -> str:
        return _("View only: another browser controls the editor.")

    @property
    def WEB_TAKE_CONTROL(self) -> str:
        return _("Take Control")

    @property
    def WEB_COL_NAME(self) -> str:
        return _("Name")

    @property
    def WEB_COL_SIZE(self) -> str:
        return _("Size")

    @property
    def WEB_COL_MODIFIED(self) -> str:
        return _("Modified")

    @property
    def WEB_LINK_BLOCKED(self) -> str:
        return _("The browser did not open it by itself.")

    @property
    def WEB_DOWNLOAD_STARTED(self) -> str:
        return _("Download started: {name}")

    @property
    def WEB_CHOOSE_FILE(self) -> str:
        return _("Choose File…")

    @property
    def WEB_FILE_READY(self) -> str:
        return _("{name} is ready.")

    @property
    def WEB_OPEN_LINK(self) -> str:
        return _("Open Link")


class _CombinedStrings(BaseStrings, MenuStrings):
    pass

    @property
    def UPDATE_CHECKING(self) -> str:
        return _("Checking for updates…")

    @property
    def UPDATE_CHECK_FAILED(self) -> str:
        return _("The check for updates failed: {error}")

    @property
    def GA_EXPORT_EMPTY(self) -> str:
        return _("The project has no group addresses to export.")

    @property
    def MENU_CLEAR_CACHE(self) -> str:
        return _("Clear Cache…")

    @property
    def CLEAR_CACHE_TITLE(self) -> str:
        return _("Clear Cache")

    @property
    def CLEAR_CACHE_CONFIRM(self) -> str:
        return _("Clear Cache")

    @property
    def CLEAR_CACHE_TEXT(self) -> str:
        return _(
            "The cache holds {size} of parsed product data and of documentation fetched for the AI tools. All of it is made again when needed; afterwards only the first use of each product takes longer."
        )

    @property
    def CLEAR_CACHE_DONE(self) -> str:
        return _("Cache cleared, {size} freed")

    @property
    def DATA_SECURE_DEVICE(self) -> str:
        return _("KNX Secure")

    @property
    def MENU_ZOOM_IN(self) -> str:
        return _("Zoom In")

    @property
    def MENU_ZOOM_OUT(self) -> str:
        return _("Zoom Out")

    @property
    def MENU_ZOOM_RESET(self) -> str:
        return _("Reset Zoom")

    @property
    def WEB_CUT(self) -> str:
        return _("Cut")

    @property
    def WEB_COPY(self) -> str:
        return _("Copy")

    @property
    def WEB_PASTE(self) -> str:
        return _("Paste")

    @property
    def WEB_SELECT_ALL(self) -> str:
        return _("Select All")

    @property
    def WEB_PASTE_HINT(self) -> str:
        return _("Long-press in the field below and choose Paste.")

    @property
    def WEB_LOGIN_LOCKED(self) -> str:
        return _("Too many wrong passwords. Please try again in a few minutes.")

    @property
    def WEB_PORT_IN_USE(self) -> str:
        return _(
            "Port {port} is already in use by another program, so the browser UI cannot start there. Start it with another port (e.g. --web={example}) or without a port to take a free one."
        )

    @property
    def TRAY_OPEN_IN_BROWSER(self) -> str:
        return _("Open in Browser")

    @property
    def TRAY_STOP(self) -> str:
        return _("Stop XKNX Editor")

    @property
    def EXIT_CONFIRM_TEXT(self) -> str:
        """File > Exit in the browser view: what stopping means."""
        return _(
            "This stops XKNX Editor on the server. Until it is started there again, no "
            "browser can connect to it."
        )

    @property
    def EDITOR_RUNNING_TITLE(self) -> str:
        return _("XKNX Editor Already Running")

    @property
    def EDITOR_RUNNING_TEXT(self) -> str:
        return _(
            "XKNX Editor is already running on this computer. Switch to it, or start another one, "
            "e.g. to work on a second project or with another bus connection."
        )

    @property
    def EDITOR_WINDOW(self) -> str:
        return _("Window")

    @property
    def EDITOR_BROWSER(self) -> str:
        return _("Browser UI ({url})")

    @property
    def EDITOR_NO_PROJECT(self) -> str:
        return _("No project open")

    @property
    def EDITOR_ENTRY(self) -> str:
        return _("{kind}: {project}")

    @property
    def EDITOR_SWITCH(self) -> str:
        return _("Switch to It")

    @property
    def EDITOR_START_ANOTHER(self) -> str:
        return _("Start Another")

    @property
    def EDITOR_GONE(self) -> str:
        return _("That XKNX Editor has ended in the meantime.")

    @property
    def PROJECT_ELSEWHERE_TITLE(self) -> str:
        return _("Project Already Open")

    @property
    def PROJECT_ELSEWHERE_TEXT(self) -> str:
        return _(
            "{name} is already open in another XKNX Editor. Editing it in two editors at once can "
            "overwrite changes."
        )

    @property
    def PROJECT_OPEN_ANYWAY(self) -> str:
        return _("Open Anyway")

    @property
    def FLAG_LETTER_COMMUNICATION(self) -> str:
        return _("C")

    @property
    def FLAG_LETTER_READ(self) -> str:
        return _("R")

    @property
    def FLAG_LETTER_WRITE(self) -> str:
        return _("W")

    @property
    def FLAG_LETTER_TRANSMIT(self) -> str:
        return _("T")

    @property
    def FLAG_LETTER_UPDATE(self) -> str:
        return _("U")

    @property
    def FLAG_LETTER_READ_ON_INIT(self) -> str:
        return _("I")

    @property
    def FLAG_NAME_COMMUNICATION(self) -> str:
        return _("Communication")

    @property
    def FLAG_NAME_READ(self) -> str:
        return _("Read")

    @property
    def FLAG_NAME_WRITE(self) -> str:
        return _("Write")

    @property
    def FLAG_NAME_TRANSMIT(self) -> str:
        return _("Transmit")

    @property
    def FLAG_NAME_UPDATE(self) -> str:
        return _("Update")

    @property
    def FLAG_NAME_READ_ON_INIT(self) -> str:
        return _("Read on Init")

    @property
    def LANGUAGE_ENGLISH(self) -> str:
        return _("English")

    @property
    def LANGUAGE_GERMAN(self) -> str:
        return _("German")

    @property
    def LANGUAGE_DUTCH(self) -> str:
        return _("Dutch")

    @property
    def LANGUAGE_ITEM(self) -> str:
        return _("{name} ({native})")

    @property
    def LANGUAGE_SPANISH(self) -> str:
        return _("Spanish")

    @property
    def LANGUAGE_GREEK(self) -> str:
        return _("Greek")

    @property
    def LANGUAGE_CHINESE(self) -> str:
        return _("Simplified Chinese")

    @property
    def LANGUAGE_JAPANESE(self) -> str:
        return _("Japanese")

    @property
    def LANGUAGE_CZECH(self) -> str:
        return _("Czech")

    @property
    def LANGUAGE_DANISH(self) -> str:
        return _("Danish")

    @property
    def LANGUAGE_FINNISH(self) -> str:
        return _("Finnish")

    @property
    def LANGUAGE_FRENCH(self) -> str:
        return _("French")

    @property
    def LANGUAGE_ITALIAN(self) -> str:
        return _("Italian")

    @property
    def LANGUAGE_NORWEGIAN(self) -> str:
        return _("Norwegian")

    @property
    def LANGUAGE_POLISH(self) -> str:
        return _("Polish")

    @property
    def LANGUAGE_PORTUGUESE(self) -> str:
        return _("Portuguese")

    @property
    def LANGUAGE_RUSSIAN(self) -> str:
        return _("Russian")

    @property
    def LANGUAGE_SWEDISH(self) -> str:
        return _("Swedish")

    @property
    def LANGUAGE_TURKISH(self) -> str:
        return _("Turkish")

    @property
    def LANGUAGE_UKRAINIAN(self) -> str:
        return _("Ukrainian")

    @property
    def MENU_PRODUCT_LANGUAGE(self) -> str:
        return _("Preferred Product Language")

    @property
    def LANGUAGE_HUNGARIAN(self) -> str:
        return _("Hungarian")

    @property
    def LANGUAGE_ROMANIAN(self) -> str:
        return _("Romanian")

    @property
    def LANGUAGE_SLOVAK(self) -> str:
        return _("Slovak")

    @property
    def LANGUAGE_SLOVENIAN(self) -> str:
        return _("Slovenian")

    @property
    def LANGUAGE_ICELANDIC(self) -> str:
        return _("Icelandic")


S = _CombinedStrings()
