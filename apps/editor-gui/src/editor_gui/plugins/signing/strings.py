"""Signing plugin strings."""

from pathlib import Path

from editor_gui.strings import create_translator

_locale_dir = Path(__file__).parent / "locales"
_ = create_translator("signing", _locale_dir)


class SigningStrings:
    @property
    def MENU(self) -> str:
        return _("Signing")

    @property
    def MENU_SHOW_WINDOW(self) -> str:
        return _("Signing Key…")

    @property
    def STATUS_GENUINE(self) -> str:
        return _("A signing key is set (exports are signed).")

    @property
    def STATUS_PLACEHOLDER(self) -> str:
        return _("Placeholder key in use — signatures are not ETS-valid.")

    @property
    def MODULUS(self) -> str:
        return _("Modulus (hex)")

    @property
    def PRIVATE_EXPONENT(self) -> str:
        return _("Private Exponent (hex)")

    @property
    def PUBLIC_EXPONENT(self) -> str:
        return _("Public Exponent (hex)")

    @property
    def EXTRACT(self) -> str:
        return _("Extract from Knx.Ets.XmlSigning.dll…")

    @property
    def SAVE(self) -> str:
        return _("Save")

    @property
    def RESET(self) -> str:
        return _("Reset to Placeholder")

    @property
    def DLL_FILTER(self) -> str:
        return _("All DLLs")

    @property
    def ALL_FILES(self) -> str:
        return _("All Files")

    @property
    def PICK_HINT(self) -> str:
        return _(
            'Select "Knx.Ets.XmlSigning.dll" from your ETS installation '
            "(e.g. C:\\Program Files (x86)\\ETS6)."
        )

    @property
    def EXTRACTING(self) -> str:
        return _("Extracting key from the assembly…")

    @property
    def SAVED(self) -> str:
        return _("Signing key saved.")

    @property
    def RESET_DONE(self) -> str:
        return _("Reverted to the placeholder key.")

    @property
    def NO_BACKEND(self) -> str:
        return _(
            "No .NET runtime found. Install the .NET SDK (macOS: `brew install dotnet-sdk`; "
            "Fedora: `sudo dnf install dotnet-sdk-8.0`) to extract the key, or paste it "
            "manually below."
        )

    @property
    def HINT(self) -> str:
        return _(
            "The key is read from your own licensed ETS assembly and stored per-user, "
            "never in the project."
        )

    @property
    def CREDIT(self) -> str:
        return _("Based on OpenKNXproducer (github.com/OpenKNX/OpenKNXproducer).")


S = SigningStrings()
