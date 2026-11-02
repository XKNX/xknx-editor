"""The staged archive and local signer use the same project password."""

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from editor_gui.main import KnxGuiApp


@pytest.mark.parametrize("password", [None, "", "project-password"])
def test_local_export_forwards_password(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, password: str | None
) -> None:
    service = SimpleNamespace(
        working_path=tmp_path / "project.xknx",
        project_password=password,
        program_refs=lambda: {},
        module_arguments=lambda: {},
        group_object_trees=lambda: {},
    )
    app = SimpleNamespace(
        _project_service=service,
        _catalog_service=object(),
        _myknx_thread=None,
        _export_remove_unused=False,
        _export_schema="23",
        _ets_is5=False,
        _log=Mock(),
        _begin_progress=Mock(),
        set_progress=Mock(),
        _show_ets_result=Mock(),
        _show_export_failure=Mock(),
    )
    bundle = SimpleNamespace(extra_files={}, master_xml=None)
    stage = Mock(return_value=SimpleNamespace(missing_references=[]))
    signer = Mock(
        return_value=SimpleNamespace(
            certificate_written=True,
            validation_ok=True,
            license_name="Owner",
            licensed_devices=1,
            notes=(),
        )
    )
    monkeypatch.setattr(
        "editor_gui.main.collect_manufacturer_bundle", lambda *_: bundle
    )
    monkeypatch.setattr("editor_gui.main.export_knxproj", stage)
    monkeypatch.setattr("editor_gui.main.ets_local_sign", signer)
    dest = str(tmp_path / "export.knxproj")

    KnxGuiApp._do_export_via_ets(app, dest, accept_untested=True)  # type: ignore[arg-type]
    assert app._myknx_thread is not None
    app._myknx_thread.join(timeout=10)
    assert not app._myknx_thread.is_alive()

    stage.assert_called_once()
    signer.assert_called_once()
    assert stage.call_args.kwargs["password"] == password
    assert signer.call_args.kwargs["password"] == (password or None)
    assert signer.call_args.args == (stage.call_args.args[1], dest)
    assert not stage.call_args.args[1].exists()
    app._show_export_failure.assert_not_called()
