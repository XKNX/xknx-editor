"""App-wide and per-plugin i18n string registry."""

import gettext
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


class BaseStrings:
    @property
    def APP_TITLE(self) -> str:
        return _("XKNX Editor")

    @property
    def WINDOW_TITLE(self) -> str:
        return _("{project} - {app}")

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
    def STATUS_TESTING(self) -> str:
        return _("Testing {address}…")

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
        return "Ctrl+Z"

    @property
    def SHORTCUT_REDO(self) -> str:
        return "Ctrl+Y"

    @property
    def SHORTCUT_NEW(self) -> str:
        return "Ctrl+N"

    @property
    def SHORTCUT_OPEN(self) -> str:
        return "Ctrl+O"

    @property
    def SHORTCUT_EXPORT(self) -> str:
        return "Ctrl+S"


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
        return _("Server: running")

    @property
    def SETTINGS_MCP_STOPPED(self) -> str:
        return _("Server: stopped")

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
    def MENU_EXPORT_ETS6_STORE(self) -> str:
        return _("Export to ETS 6 ProjectStore")

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
        return _("Export with header line")

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
        return _("Group-Object Links")

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
            "{count} com-object(s) had an extra description in the original file. "
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
    def MYKNX_SIGN_TITLE(self) -> str:
        return _("Sign Export")

    @property
    def MYKNX_SIGN_PROMPT(self) -> str:
        return _(
            "ETS 6 needs a signed and licensed .knxproj: log in to license it and set a "
            "signing key - or use the 'License with a Dongle' way below.\n"
            "ETS 5: just use 'Export without Certificate'."
        )

    @property
    def MYKNX_SIGN_USERNAME(self) -> str:
        return _("MyKnx Username")

    @property
    def MYKNX_SIGN_PASSWORD(self) -> str:
        return _("MyKnx Password")

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
        return _("MyKnx OAuth Credentials (2FA)")

    @property
    def MYKNX_OAUTH_HELP(self) -> str:
        return _(
            "Needed only for accounts with two-factor authentication. The 2FA sign-in opens in "
            "your browser automatically when a normal login is rejected. Enter the OAuth client "
            "id/secret, or extract them from your installed ETS: pick 'Knx.Ets.MyKnx.dll' from the "
            "ETS program folder (e.g. C:\\Program Files (x86)\\ETS6\\Knx.Ets.MyKnx.dll).\n"
            "\n"
            "Alternatively, if you have any licensed ETS installed, skip online signing entirely "
            "and use the local project store instead - see 'License with an ETS Dongle'."
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
        return _("Export Format")

    @property
    def EXPORT_FORMAT_ETS6(self) -> str:
        return _("ETS 6 (schema 23)")

    @property
    def EXPORT_FORMAT_ETS5(self) -> str:
        return _("ETS 5 (schema 20)")

    @property
    def MYKNX_DONGLE_BUTTON(self) -> str:
        return _("License with a Dongle…")

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
    def MYKNX_DONGLE_BACK(self) -> str:
        return _("Back")

    @property
    def MYKNX_DONGLE_TITLE(self) -> str:
        return _("License with an ETS Dongle")

    @property
    def MYKNX_DONGLE_EXPORT_STORE(self) -> str:
        return _("Export to ETS 6 ProjectStore")

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
            "Re-license the project through your own licensed ETS via its local project store, "
            "which re-signs the copy with your dongle license:\n"
            "\n"
            "   1. Click 'Export to ETS 5 ProjectStore' or 'Export to ETS 6 ProjectStore' "
            "(also in the File menu). It writes the project's 'P-XXXX' folder straight into "
            "that ETS version's project store. The two formats are not interchangeable. If ETS "
            "is not installed on this computer, you are asked for the store folder (or any "
            "folder you then copy over manually).\n"
            "   2. The export needs the 'installer.guid' of that ETS version, from "
            "C:\\ProgramData\\KNX\\ETS5\\Installer or ...\\ETS6\\Installer. ETS 5 and ETS 6 do "
            "not share one. If it cannot be found automatically, you are asked to select it.\n"
            "   3. Restart ETS - the project appears in the project overview.\n"
            "   4. Make a copy of it there (ETS 6: Copy in the overview; ETS 5: right-click > "
            "Copy > As Clone). The copy is re-signed with your license - open it, then use or "
            "export it normally.\n"
            "\n"
            "This is bounded by the device-per-project limit of your license."
        )

    @property
    def MYKNX_SIGN_CONFIRM(self) -> str:
        return _("Sign & Export")

    @property
    def MYKNX_SIGN_SKIP(self) -> str:
        return _("Export without Certificate")

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
        return _("Load OpenKNX from URL…")

    @property
    def URL_PROMPT_TITLE(self) -> str:
        return _("Load OpenKNX from URL")

    @property
    def URL_PROMPT_HINT(self) -> str:
        return _(
            "Paste an OpenKNX GitHub release URL (the release page works — the .zip asset is found "
            "automatically). A direct .knxprod or KNX product XML link also works.\n"
            "Examples:\n"
            "  https://github.com/OpenKNX/OAM-PresenceModule/releases/tag/3.6.2-Release\n"
            "  https://github.com/OpenKNX/OAM-PresenceModule/releases/latest"
        )

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
    def URL_IMPORT_OK(self) -> str:
        return _("Imported {count} new product version(s) from URL")

    @property
    def URL_IMPORT_EMPTY(self) -> str:
        return _("Nothing new imported (already in the catalog)")

    @property
    def URL_IMPORT_FAILED(self) -> str:
        return _("Import from URL failed: {error}")

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
    def MENU_THEME_TWEAKS(self) -> str:
        return _("Theme Tweaks")

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
        return _("Replace existing file?")

    @property
    def OVERWRITE_PROMPT(self) -> str:
        return _(
            "A file named {name} already exists in:\n{dir}\n\n"
            "Saving here replaces its current contents. This cannot be undone."
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
    def KNXPROD_IMPORT_DONE(self) -> str:
        return _("Product catalog imported: {count} new product version(s) from {name}")

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
        return _("Remove unused product data")

    @property
    def EXPORT_REMOVE_UNUSED_HELP(self) -> str:
        return _("Removes product data no device uses; the project stays unchanged.")


class _CombinedStrings(BaseStrings, MenuStrings):
    pass


S = _CombinedStrings()
