"""The devices built when a project opens are left out of the garbage collections while it is
open (see xknxeditor.prod.gc_pause), and looked at again once it closes."""

from __future__ import annotations

import gc
from typing import Any

from editor_gui.plugins.project.tests.test_device_snapshots import _project, _service
from xknxeditor.prod.gc_pause import keep_long_lived, release_long_lived


def test_the_devices_are_left_out_while_the_project_is_open(tmp_path: Any) -> None:
    cat, path, _ids = _project(tmp_path)
    keep_long_lived(True)
    try:
        release_long_lived()
        proj = _service(cat)
        proj.open(path)
        _ = proj.devices
        assert gc.get_freeze_count() > 0
        proj.close()
        assert gc.get_freeze_count() == 0
    finally:
        keep_long_lived(False)
        release_long_lived()
