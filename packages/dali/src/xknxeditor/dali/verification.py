"""Compare programmed tables with gateway readback."""

from collections.abc import Sequence

from xknxeditor.dali.connection import DaliCommissioningError
from xknxeditor.dali.model import Scene, Template


def verify_scenes(expected: Sequence[Scene], actual: Sequence[Scene]) -> None:
    received = {scene.index: scene for scene in actual}
    for wanted in expected:
        got = received.get(wanted.index)
        if (
            got is None
            or got.fade_time != wanted.fade_time
            or (got.knx_mapping_available and got.knx_scene != wanted.knx_scene)
            or sorted(got.values, key=lambda v: v.target_index)
            != sorted(wanted.values, key=lambda v: v.target_index)
        ):
            raise DaliCommissioningError(
                "scene readback does not match programmed configuration"
            )


def verify_templates(expected: Sequence[Template], actual: Sequence[Template]) -> None:
    received = {template.index: template for template in actual}
    for wanted in expected:
        got = received.get(wanted.index)
        if (
            got is None
            or sorted(got.groups) != sorted(wanted.groups)
            or sorted(got.ecgs) != sorted(wanted.ecgs)
            or got.commands
            != sorted(wanted.commands, key=lambda c: (c.hour, c.minute, c.func))
            or (
                wanted.commands
                and (got.mode, got.overwritable, got.reset_on_midnight)
                != (wanted.mode, wanted.overwritable, wanted.reset_on_midnight)
            )
        ):
            raise DaliCommissioningError(
                "template readback does not match programmed configuration"
            )
