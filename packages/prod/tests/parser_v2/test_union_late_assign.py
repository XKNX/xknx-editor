"""A union member reached only after a late Assign must still win its overlay.

Models issue #195 (MDT Smart 86): a single-button module (``MD-1_M-9``) sits behind a
union-member selector whose enabling value is produced by an Assign evaluated after the
Choose that reads it. The union-discovery pass must settle before it freezes its snapshot,
or the freshly selected member is wrongly suppressed and its module is never instantiated.
"""

from __future__ import annotations

from pathlib import Path

from xknxeditor.prod.application import parse_application_xml
from xknxeditor.prod.parser_v2.activation import shown_parameters
from xknxeditor.prod.parser_v2.dynamic import DynamicUI

APP = "M-00FA_A-0005-01-0000"
XML = (
    Path(__file__).parents[1] / "fixtures" / "union_late_assign_application.xml"
).read_bytes()


def _ui() -> DynamicUI:
    return DynamicUI(parse_application_xml(XML, "M-00FA")[0].program)


def test_late_assign_selects_the_union_member_module() -> None:
    ui = _ui()
    shown = set(shown_parameters(ui.ui()))
    instances = {iid for iid, _ in ui.get_module_instances()}
    # Selector starts at 0 (shows B), the Assign copies Layout (2) into it only afterwards,
    # so the settle ends on the A branch: MD-1 is the active overlay, MD-2 is suppressed.
    assert f"{APP}_MD-1_M-9_MI-1" in instances
    assert f"{APP}_MD-2_M-16_MI-1" not in instances
    assert f"{APP}_MD-1_M-9_MI-1_P-1_R-1" in shown
