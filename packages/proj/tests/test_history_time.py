"""Each step of the history keeps when it was made, also after the project was opened again."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from xknxeditor.proj import ProjectService


def test_history_steps_keep_their_time_in_utc(tmp_path: Path) -> None:
    path = tmp_path / "p.xknx"
    svc = ProjectService()
    before = datetime.now(UTC)
    pid = svc.create(path, "P-0C02")
    svc.create_group_address(pid, 0, 0x0801, "Light")
    svc.create_group_address(pid, 0, 0x0802, "Blind")
    after = datetime.now(UTC)
    svc.close(pid)

    pid = svc.open(path)
    steps = svc.history(pid)
    svc.close(pid)
    assert len(steps) == 2
    for step in steps:
        assert step.timestamp is not None
        assert step.timestamp.tzinfo is not None
        assert step.timestamp.utcoffset() == timedelta(0)
        # SQLite keeps the time to the microsecond.
        assert before - timedelta(seconds=1) <= step.timestamp <= after
    # Newest first.
    assert steps[0].timestamp >= steps[1].timestamp  # type: ignore[operator]
