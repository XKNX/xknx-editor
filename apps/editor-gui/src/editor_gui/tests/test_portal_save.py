"""The portal save response carries the file-type filter the user confirmed."""

from editor_gui.portal_save import parse_save_response


def test_parse_save_response_reads_path_and_filter() -> None:
    body = (
        0,
        {
            "uris": ("as", ["file:///home/steve/ETS%206.knxproj"]),
            "current_filter": (
                "(sa(us))",
                ("ETS 5 Project (*.knxproj)", [(0, "*.knxproj")]),
            ),
        },
    )
    assert parse_save_response(body) == (
        "/home/steve/ETS 6.knxproj",
        "ETS 5 Project (*.knxproj)",
    )


def test_parse_save_response_cancel() -> None:
    body: tuple[object, ...] = (1, {})
    assert parse_save_response(body) is None


def test_parse_save_response_without_filter() -> None:
    body = (0, {"uris": ("as", ["file:///tmp/project.knxproj"])})
    assert parse_save_response(body) == ("/tmp/project.knxproj", "")
