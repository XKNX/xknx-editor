"""Prepare saved plugin data from the export's database snapshot."""

from dataclasses import replace

from xknxeditor.download.tables import com_object_flag_byte, priority_from_bits
from xknxeditor.proj.core.identity import qualified_com_object_ref
from xknxeditor.proj.models import ComObject, Device
from xknxeditor.vendor.powertool.family import FamilyConfig
from xknxeditor.vendor.powertool.model import BLOB_NAME
from xknxeditor.vendor.powertool.native import compile_native, from_original, load
from xknxeditor.vendor.powertool.project_blob import PLUGIN_BLOB_NAME


def binary_data_for_export(device: Device) -> dict[str, bytes]:
    """Verify that the stored plugin blob already matches the current configuration."""
    blobs = {row.name: row.data for row in device.binary_data}
    if BLOB_NAME not in blobs:
        return {}
    config = load(blobs[BLOB_NAME])
    source = blobs.get(PLUGIN_BLOB_NAME)
    if source is None:
        raise ValueError(
            "PowerTool plugin blob is missing; edit or re-import its native configuration first"
        )
    flags = dict(config.object_flags)
    compiled = compile_native(config)
    defaults = {obj.key: obj.flags for obj in compiled.objects}
    prefixes = {
        reference.split("_O-", 1)[0]
        for reference in config.bindings.values()
        if "_O-" in reference
    }
    app_id = device.application_program_ref_id or (
        next(iter(prefixes)) if len(prefixes) == 1 else ""
    )
    objects: dict[str, ComObject] = {}
    for row in device.com_objects:
        ref = qualified_com_object_ref(
            row.ref_id,
            row.instance_ref_id or "",
            app_id,
        )
        if ref in objects:
            raise ValueError(f"PowerTool export has ambiguous object {ref}")
        objects[ref] = row
    for role, reference in config.bindings.items():
        obj = objects.get(reference)
        if obj is None:
            raise ValueError(f"PowerTool export has no object for {reference}")
        flags[role] = com_object_flag_byte(
            priority=obj.priority or priority_from_bits(defaults[role]),
            **{
                name: bool(defaults[role] & bit)
                if getattr(obj, name + "_flag") is None
                else getattr(obj, name + "_flag")
                for name, bit in (
                    ("communication", 4),
                    ("read", 8),
                    ("write", 16),
                    ("transmit", 64),
                    ("update", 128),
                    ("read_on_init", 32),
                )
            },
        )
    saved = from_original(source)
    if isinstance(config, FamilyConfig) and isinstance(saved, FamilyConfig):
        programs = {
            prop.object_index: prop.data.hex()
            for prop in compiled.properties
            if prop.pid == 13
        }
        saved = replace(
            saved,
            program_ids=(programs[4], programs[5]),
            mask_version=config.mask_version,
        )
    if (
        compile_native(saved).pid
        != compile_native(replace(config, object_flags=flags)).pid
    ):
        raise ValueError(
            "PowerTool plugin blob is out of date; edit or re-import its native configuration first"
        )
    return {}
