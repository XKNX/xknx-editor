"""Supplemental MDT data that is not part of parameter memory."""

from xknxeditor.download import DeviceProgrammer
from xknxeditor.prod import Application
from xknxeditor.recover.recover import AddinReader
from xknxeditor.vendor.mdt import profile_for_application
from xknxeditor.vendor.mdt.layout import parameter_map, status_map
from xknxeditor.vendor.mdt.symbols import recovered_symbol_entries
from xknxeditor.vendor.mdt.transfer import recover_symbols


def addin_reader(application_id: str) -> AddinReader | None:
    profile = profile_for_application(application_id)
    if profile is None or profile.kind != "symbols":
        return None

    async def read(programmer: DeviceProgrammer) -> dict[str, dict[str, bytes]]:
        symbols = await recover_symbols(programmer, profile)
        return {profile.app_id: recovered_symbol_entries(symbols, profile)}

    return read


def editable_parameter_refs(application: Application) -> frozenset[str]:
    """Hidden parameters whose values are managed by a supported DCA."""
    import re

    profile = profile_for_application(application.id)
    if profile is None or profile.kind == "symbols":
        return frozenset()
    ui = application.dynamic_ui()
    if ui is None:
        return frozenset()
    refs: set[str] = set()
    for ref in ui.indexer.parameter_refs:
        match = re.search(r"_P-(\d+)_R-(\d+)$", ref)
        if match is None:
            continue
        parameter, reference = map(int, match.groups())
        if profile.kind == "visucontrol":
            if "_MD-" not in ref and parameter in (2, 3, 4):
                refs.add(ref)
        elif "_MD-3_" in ref:
            if (parameter, reference) in parameter_map() or (
                parameter,
                -1,
            ) in parameter_map():
                refs.add(ref)
        elif "_MD-" not in ref and (
            (parameter, reference) in status_map()
            or (parameter, reference) == (570, 408)
        ):
            refs.add(ref)
    return frozenset(refs)


def parameter_candidates(application: Application) -> tuple[dict[str, str], ...]:
    """Standby layout selectors whose combination must fit the captured memory."""
    profile = profile_for_application(application.id)
    if profile is None or profile.kind != "layout":
        return ()
    ui = application.dynamic_ui()
    standby = f"{application.id}_P-569_R-352"
    custom = f"{application.id}_P-570_R-408"
    if ui is None or not all(ui.has_parameter(ref) for ref in (standby, custom)):
        return ()
    return ({standby: "1", custom: "1"}, {standby: "1", custom: "0"}, {standby: "0"})
