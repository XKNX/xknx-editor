"""Module instances in the page tree: repeated instances whose app text is identical (the MDT DALI
gateway's 16 group tabs all labelled "G,") get their instance index; instances with texts of their
own (a switch actuator's channels, each one module instance ``MI-1``) keep them."""

from __future__ import annotations

from editor_gui.widgets.parameter_widgets import build_pages
from xknxeditor.prod.parser_v2.ui import NumberWidget, UiParameter, UiTab


def _tab(tab_id: str, text: str) -> UiTab:
    content = UiParameter(
        ref_id=f"{tab_id}_P-1",
        label="Value",
        value="0",
        default_value="0",
        widget=NumberWidget(min=0, max=10),
    )
    return UiTab(children=(content,), id=tab_id, text=text)


def _labels(*tabs: UiTab) -> list[str]:
    return [page.label for page in build_pages(list(tabs), "d")]


def test_identical_instance_labels_get_their_index() -> None:
    assert _labels(
        _tab("M-0083_A-0153_CH-0", "GENERAL"),
        _tab("M-0083_A-0153_MD-2_M-3_MI-1_CH-1", "G,"),
        _tab("M-0083_A-0153_MD-2_M-3_MI-16_CH-1", "G,"),
    ) == ["GENERAL", "G 1", "G 16"]


def test_instances_with_texts_of_their_own_keep_them() -> None:
    assert _labels(
        _tab("M-0083_A-0015_CH-1", "Channel A: Kitchen"),
        _tab("M-0083_A-0015_MD-1_M-1_MI-1_CH-4", "Channel B: Hallway"),
        _tab("M-0083_A-0015_MD-1_M-4_MI-1_CH-4", "Channel C: Terrace"),
    ) == ["Channel A: Kitchen", "Channel B: Hallway", "Channel C: Terrace"]


def test_a_label_that_carries_the_number_stays() -> None:
    # Already substituted ("Channel 3") must not become "Channel 3 3".
    assert _labels(_tab("x_MI-3", "Channel 3"), _tab("y_MI-3", "Channel 3")) == [
        "Channel 3",
        "Channel 3",
    ]
