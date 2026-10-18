"""The project passed on the command line, e.g. by the .xknx file association."""

from __future__ import annotations

import os
from pathlib import Path

from editor_gui.main import startup_file


def test_no_arguments_open_nothing():
    assert startup_file(["XKNX-Editor.exe"]) is None


def test_options_are_not_files():
    assert startup_file(["XKNX-Editor.exe", "--profile"]) is None


def test_the_first_file_argument_is_opened_as_an_absolute_path(tmp_path: Path):
    project = tmp_path / "Haus.xknx"
    assert startup_file(
        ["XKNX-Editor.exe", "--profile", str(project), "other.xknx"]
    ) == str(project)


def test_a_relative_path_is_made_absolute():
    assert startup_file(["XKNX-Editor.exe", "Haus.xknx"]) == os.path.abspath(
        "Haus.xknx"
    )
