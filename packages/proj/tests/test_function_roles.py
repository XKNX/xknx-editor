"""The role of each group address of a building function, from its type."""

from xknxeditor.proj.core.function_roles import function_roles


def test_roles_follow_the_function_points_by_datapoint_type() -> None:
    # Dimmable light: switching, dimming, value, status, dimming value.
    links = [
        ("", "DPST-3-7"),
        ("", "DPST-1-1"),
        ("", "DPST-1-1"),
        ("", "DPST-5-1"),
        ("", "DPST-5-1"),
    ]
    assert function_roles("FT-6", links) == [
        "DimmingControl",
        "SwitchOnOff",
        "InfoOnOff",
        "DimmingValue",
        "InfoDimmingValue",
    ]


def test_without_a_datapoint_type_the_points_are_taken_in_order() -> None:
    assert function_roles("FT-1", [("", None), ("", "")]) == [
        "SwitchOnOff",
        "InfoOnOff",
    ]


def test_a_stored_role_of_the_type_stays_and_others_are_replaced() -> None:
    assert function_roles("FT-1", [("InfoOnOff", None), ("Old", None)]) == [
        "InfoOnOff",
        "SwitchOnOff",
    ]


def test_the_same_main_type_matches_and_no_point_left_gives_none() -> None:
    # 1.002 (boolean) takes a 1.001 point; a third group address finds none, nor does 9.001.
    links = [("", "DPST-1-2"), ("", "DPT-1"), ("", "DPST-1-1"), ("", "DPST-9-1")]
    assert function_roles("FT-1", links) == ["SwitchOnOff", "InfoOnOff", "", ""]


def test_custom_and_unknown_types_keep_the_stored_roles() -> None:
    assert function_roles("FT-0", [("Mine", None), ("", "DPST-1-1")]) == ["Mine", ""]
    assert function_roles(None, [("", None)]) == [""]
    assert function_roles("FT-99", [("X", None)]) == ["X"]
