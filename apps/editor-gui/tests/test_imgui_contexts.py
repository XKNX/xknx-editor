"""A test's imgui context reads and writes no imgui.ini, whether it is the current one or not."""

from imgui_bundle import imgui


def test_a_context_of_a_test_has_no_ini_file() -> None:
    first = imgui.create_context()
    try:
        assert imgui.get_current_context() == first
        assert imgui.get_io().get_ini_filename() == ""
        second = imgui.create_context()
        try:
            # The first one stays current; the second has no ini file either.
            assert imgui.get_current_context() == first
            imgui.set_current_context(second)
            assert imgui.get_io().get_ini_filename() == ""
        finally:
            imgui.destroy_context(second)
    finally:
        imgui.set_current_context(first)
        imgui.destroy_context(first)
