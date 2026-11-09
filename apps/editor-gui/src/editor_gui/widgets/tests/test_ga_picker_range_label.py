from types import SimpleNamespace

from editor_gui.widgets.group_objects_widgets import ga_range_label


def test_range_label_shows_group_number_like_the_ga_tree() -> None:
    node = SimpleNamespace(id=7, name="Licht", address="1/2")
    assert ga_range_label(node) == "1/2  Licht"


def test_range_label_without_number_or_name() -> None:
    assert ga_range_label(SimpleNamespace(id=7, name="Licht", address="")) == "Licht"
    assert ga_range_label(SimpleNamespace(id=7, name="", address="")) == "[7]"
