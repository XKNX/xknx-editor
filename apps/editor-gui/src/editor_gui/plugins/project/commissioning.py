"""Shared names and display text for pending device downloads."""

from collections.abc import Sequence

from editor_gui.plugins.project.strings import S

LOADED_PARTS = {
    "individual_address_loaded": "individual_address",
    "application_program_loaded": "application_program",
    "parameters_loaded": "parameters",
    "communication_part_loaded": "group_communication",
    "medium_config_loaded": "medium_config",
}


def pending_download_text(parts: Sequence[str]) -> str:
    """Localized text shared by the device marker and health finding."""
    if not parts:
        return ""
    labels = S.DOWNLOAD_PART_LABELS
    return S.DOWNLOAD_PENDING.format(parts=", ".join(labels[part] for part in parts))
