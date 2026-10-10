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
    def translate(text: str) -> str:
        try:
            trans = gettext.translation(domain, locale_dir, languages=[_current_locale])
            return trans.gettext(text)
        except FileNotFoundError:
            return text

    return translate


_base_locale_dir = Path(__file__).parent.parent / "locales"
_ = create_translator("editor_gui", _base_locale_dir)


class BaseStrings:
    @property
    def APP_TITLE(self) -> str:
        return _("XKNX Editor")

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
        return _("Network location")

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
        return _("Use a local copy")

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
        return _("All files")

    @property
    def STATUS_PROJECT(self) -> str:
        return _("Project: {name}  ·  {devices} devices  ·  {gas} GAs")

    @property
    def STATUS_NO_PROJECT(self) -> str:
        return _("No project open")

    @property
    def STATUS_PROGRAMMING(self) -> str:
        return _("Programming {address}...")

    @property
    def STATUS_TESTING(self) -> str:
        return _("Testing {address}...")

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
        return _("Save as...")

    @property
    def SAVE_AS_OK(self) -> str:
        return _("Saved as {name}")

    @property
    def MENU_OPEN_RECENT(self) -> str:
        return _("Open Recent")

    @property
    def MENU_SETTINGS(self) -> str:
        return _("Settings...")

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
        return _("Multicast group")

    @property
    def SETTINGS_MCP_HINT(self) -> str:
        return _(
            "Run the MCP server so an LLM can drive the editor over Streamable HTTP. "
            "It shares this app's catalog database."
        )

    @property
    def SETTINGS_MCP_HOST(self) -> str:
        return _("MCP host")

    @property
    def SETTINGS_MCP_PORT(self) -> str:
        return _("MCP port")

    @property
    def SETTINGS_MCP_TOKEN(self) -> str:
        return _("Bearer token (optional)")

    @property
    def SETTINGS_MCP_RUNNING(self) -> str:
        return _("Server: running")

    @property
    def SETTINGS_MCP_STOPPED(self) -> str:
        return _("Server: stopped")

    @property
    def SETTINGS_MCP_START(self) -> str:
        return _("Start server")

    @property
    def SETTINGS_MCP_STOP(self) -> str:
        return _("Stop server")

    @property
    def PANEL_KI(self) -> str:
        return _("AI")

    @property
    def KI_INFO(self) -> str:
        return _(
            "This editor is controllable by an AI assistant (Claude, Codex, ...) through the "
            "built-in MCP server: it exposes the catalog and project as tools over Streamable "
            "HTTP. Start the server below, then register its endpoint in your AI tool."
        )

    @property
    def KI_ENDPOINT(self) -> str:
        return _("MCP endpoint")

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
        return _("Download language")

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
        return _("Recent projects")

    @property
    def WELCOME_RECOVER(self) -> str:
        return _("Recover from bus…")

    @property
    def WELCOME_CLOSE(self) -> str:
        return _("Close")

    @property
    def MENU_EXPORT_KNXPROJ(self) -> str:
        return _("Export .knxproj...")

    @property
    def EXPORT_DONE(self) -> str:
        return _("Exported to {path} ({size}) as {schema}")

    @property
    def EXPORT_SUMMARY_TITLE(self) -> str:
        return _("Export complete")

    @property
    def EXPORT_SUMMARY_INTRO(self) -> str:
        return _("Successfully created {name}")

    @property
    def EXPORT_SUMMARY_FILE_SIZE(self) -> str:
        return _("File size")

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
        return _("Group addresses")

    @property
    def EXPORT_SUMMARY_GROUP_OBJECT_LINKS(self) -> str:
        return _("Group-object links")

    @property
    def EXPORT_SUMMARY_TOPOLOGY(self) -> str:
        return _("Areas / lines")

    @property
    def EXPORT_SUMMARY_FLOORS_ROOMS(self) -> str:
        return _("Floors / rooms")

    @property
    def EXPORT_SUMMARY_FUNCTIONS(self) -> str:
        return _("Functions")

    @property
    def EXPORT_SUMMARY_MASTER_VERSION(self) -> str:
        return _("KNX master version")

    @property
    def EXPORT_SUMMARY_EXPORTED_AT(self) -> str:
        return _("Exported at")

    @property
    def EXPORT_SUMMARY_MANUFACTURERS(self) -> str:
        return _("Manufacturer data (M folders)")

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
        return _("Open folder")

    @property
    def EXPORT_SUMMARY_CLOSE(self) -> str:
        return _("Close")

    @property
    def IMPORT_NOTES_TITLE(self) -> str:
        return _("Good to know")

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
        return _("Sign export")

    @property
    def MYKNX_SIGN_PROMPT(self) -> str:
        return _(
            "You can sign the .knxproj with your license so it can be opened in ETS. "
            "Log in to sign it, or save the .knxproj without a license."
        )

    @property
    def MYKNX_SIGN_USERNAME(self) -> str:
        return _("MyKnx username")

    @property
    def MYKNX_SIGN_PASSWORD(self) -> str:
        return _("MyKnx password")

    @property
    def MYKNX_SIGN_LOGIN(self) -> str:
        return _("Log in")

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
        return _("Export format")

    @property
    def EXPORT_FORMAT_ETS6(self) -> str:
        return _("ETS 6 (schema 23)")

    @property
    def EXPORT_FORMAT_ETS5(self) -> str:
        return _("ETS 5 (schema 20)")

    @property
    def MYKNX_DONGLE_BUTTON(self) -> str:
        return _("License with a dongle…")

    @property
    def SIGNING_KEY_BUTTON(self) -> str:
        return _("Signing key…")

    @property
    def SIGNING_KEY_TITLE(self) -> str:
        return _("Signing key")

    @property
    def SIGNING_KEY_CLOSE(self) -> str:
        return _("Close")

    @property
    def SIGNING_KEY_PLACEHOLDER_HINT(self) -> str:
        return _("Folder signature uses a placeholder key (not ETS-valid).")

    @property
    def MYKNX_DONGLE_BACK(self) -> str:
        return _("Back")

    @property
    def MYKNX_DONGLE_TITLE(self) -> str:
        return _("License with an ETS dongle")

    @property
    def MYKNX_DONGLE_STEPS(self) -> str:
        return _(
            "This .knxproj was exported without an online certificate, so a licensed ETS will not "
            "import it directly - it reports a missing signature. Use one of the methods below to "
            "bring the project into your own licensed ETS (with a dongle). Method 2 is the quick, "
            "reliable route; do it exactly as described.\n"
            "\n"
            "Method 2 - add an empty dummy-license marker (recommended)\n"
            "\n"
            "   1. A .knxproj is a ZIP archive. Open it with 7-Zip, or copy it and rename the "
            "copy's extension from .knxproj to .zip.\n"
            "   2. Find the 4-digit project id 'P-XXXX' - for example the file 'P-XXXX.signature' "
            "in the archive root (such as P-016A.signature).\n"
            "   3. Create an empty file named exactly 'P-XXXX_M-dummy': the same P-XXXX with "
            "'_M-dummy' appended (for example P-016A_M-dummy). It must have no file extension - "
            "not .txt.\n"
            "   4. Drag that empty file into the open .knxproj, into the archive root next to "
            "'P-XXXX.signature'. Keep the original 'P-XXXX.signature' - the dummy is added "
            "alongside it, not instead of it.\n"
            "   5. Import the .knxproj into ETS with your dongle. It now imports without the "
            "'signature missing' error.\n"
            "\n"
            "   Tip: in Windows Explorer, first turn off 'Hide extensions for known file types', "
            "so the name stays 'P-XXXX_M-dummy' and does not silently become "
            "'P-XXXX_M-dummy.txt'.\n"
            "\n"
            "Method 3 - clone in a licensed ETS 5 (fallback if Method 2 does not work)\n"
            "\n"
            "   1. Copy the whole 'P-XXXX' folder into C:\\ProgramData\\KNX\\ETS5\\ProjectStore on "
            "the licensed ETS 5 PC (the file 'P' inside the folder holds the project name).\n"
            "   2. Start ETS 5 - the project appears in the project list.\n"
            "   3. Right-click it and choose Copy > As Clone. The clone is a licensed project that "
            "you can export and then import into ETS 6."
        )

    @property
    def MYKNX_SIGN_CONFIRM(self) -> str:
        return _("Sign & export")

    @property
    def MYKNX_SIGN_SKIP(self) -> str:
        return _("Export without certificate")

    @property
    def FILE_DIALOG_KNXPROJ_SAVE_TITLE(self) -> str:
        return _("Export ETS project")

    @property
    def MENU_LOAD_KNXPROD(self) -> str:
        return _("Load .knxprod...")

    @property
    def MENU_LOAD_FROM_URL(self) -> str:
        return _("Load OpenKNX from URL...")

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
        return _("Downloading and importing...")

    @property
    def URL_IMPORT_OK(self) -> str:
        return _("Imported {count} product(s) from URL")

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
    def MENU_UNDO(self) -> str:
        return _("Undo")

    @property
    def MENU_REDO(self) -> str:
        return _("Redo")

    @property
    def FILE_DIALOG_KNXPROD_TITLE(self) -> str:
        return _("Open KNX product archive")

    @property
    def FILE_DIALOG_KNXPROD_FILTER(self) -> str:
        return _("KNX product (*.knxprod)")

    @property
    def FILE_DIALOG_KNXPROJ_FILTER(self) -> str:
        return _("ETS project (*.knxproj)")

    @property
    def FILE_DIALOG_GA_EXPORT_FILTER(self) -> str:
        return _("ETS group-address export (*.xml)")

    @property
    def FILE_DIALOG_PROJECT_TITLE(self) -> str:
        return _("Open XKNX project")

    @property
    def FILE_DIALOG_PROJECT_SAVE_TITLE(self) -> str:
        return _("Save XKNX project")

    @property
    def FILE_DIALOG_SAVE_AS_TITLE(self) -> str:
        return _("Save project as")

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
        return _("XKNX project (*.xknx)")

    @property
    def FILE_DIALOG_OPEN_FILTER(self) -> str:
        return _("Projects (*.xknx, *.knxproj, *.xml)")

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
        return _("Product catalog imported: {count} new products from {name}")

    @property
    def KNXPROD_IMPORT_NOTHING_NEW(self) -> str:
        return _("{name} was already in the catalog, nothing new imported")

    @property
    def KNXPROD_IMPORT_TITLE(self) -> str:
        return _("Product catalog import")

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
        return _("Project password")

    @property
    def IMPORT_PASSWORD_PROMPT(self) -> str:
        return _("This ETS project is password protected. Enter its password:")

    @property
    def IMPORT_PASSWORD_WRONG(self) -> str:
        return _("Wrong password, please try again.")

    @property
    def BTN_OK(self) -> str:
        return _("OK")

    @property
    def BTN_CANCEL(self) -> str:
        return _("Cancel")

    # --- update check ---------------------------------------------------
    @property
    def MENU_CHECK_UPDATES(self) -> str:
        return _("Check for updates")

    @property
    def MENU_UPDATE_ON_STARTUP(self) -> str:
        return _("Check for updates on startup")

    @property
    def UPDATE_TITLE(self) -> str:
        return _("Update available")

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
        return _("Skip this version")

    @property
    def UPDATE_LATER(self) -> str:
        return _("Later")

    @property
    def UPDATE_NOTES_HEADER(self) -> str:
        return _("Release notes:")

    @property
    def ABOUT_LICENSE(self) -> str:
        return _("License: GPL-2.0-only")

    @property
    def ABOUT_WEBSITE(self) -> str:
        return _("Website:")

    @property
    def MENU_OPEN_CONFIG_DIR(self) -> str:
        return _("Open config folder")

    @property
    def MENU_OPEN_CACHE_DIR(self) -> str:
        return _("Open cache folder")


class _CombinedStrings(BaseStrings, MenuStrings):
    pass


S = _CombinedStrings()
