"""Tests for round-tripping the ETS project log (``ProjectInformation/ProjectTraces``).

``xknxproject`` does not surface the project log, so the importer reads the ``<ProjectTrace>``
entries straight from the raw ``project.xml`` and the exporter re-emits them verbatim. The
``Comment`` is stored exactly as written by ETS (encrypted); we neither decrypt nor re-encrypt it,
so a byte-faithful round-trip must preserve it unchanged.
"""

import io
import zipfile
from pathlib import Path

from sqlalchemy.orm import Session

from xknxeditor.proj import ProjectService, export_knxproj, import_knxproj
from xknxeditor.proj.core import knxproj_import
from xknxeditor.proj.db import make_engine, url_for
from xknxeditor.proj.models import Project, ProjectTrace

_NS = "http://knx.org/xml/project/23"
# A comment as ETS stores it (opaque, encrypted-looking) plus a plaintext one, to prove verbatim
# fidelity either way.
_CIPHER = "ENC:AbC123+/=="
_PLAIN = "commissioning done"


def _project_xml(traces_xml: str) -> bytes:
    return f"""<?xml version="1.0" encoding="utf-8"?>
<KNX xmlns="{_NS}">
  <Project Id="P-01">
    <ProjectInformation Name="Demo" GroupAddressStyle="ThreeLevel">
      <ProjectTraces>{traces_xml}</ProjectTraces>
    </ProjectInformation>
  </Project>
</KNX>
""".encode()


class _FakeContents:
    """Duck-types the bits of xknxproject's ``KNXProjContents`` the trace reader uses: the private
    ``_project_archive`` / ``_project_relative_path`` behind ``_read_project_file``."""

    def __init__(self, project_xml: bytes) -> None:
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr("P-01/project.xml", project_xml)
        buf.seek(0)
        self._project_archive = zipfile.ZipFile(buf)
        self._project_relative_path = "P-01/"

    def open_project_0(
        self,
    ) -> io.BytesIO:  # unused by the trace reader; present for the protocol
        return io.BytesIO(b"")


def test_read_project_traces_parses_entries_in_order() -> None:
    _comment, traces, _extra, _subtrees, _project_subtrees = (
        knxproj_import._read_project_meta(
            _FakeContents(
                _project_xml(
                    f'<ProjectTrace Date="2024-01-02T10:00:00" UserName="alice" Comment="{_CIPHER}"/>'
                    f'<ProjectTrace Date="2024-01-03T11:00:00" UserName="bob" Comment="{_PLAIN}"/>'
                )
            )
        )
    )
    assert [(t.date, t.user_name, t.comment) for t in traces] == [
        ("2024-01-02T10:00:00", "alice", _CIPHER),
        ("2024-01-03T11:00:00", "bob", _PLAIN),
    ]


def test_read_project_traces_empty_when_absent() -> None:
    assert knxproj_import._read_project_meta(_FakeContents(_project_xml(""))) == (
        "",
        [],
        {},
        {},
        {},
    )


def _project_with_traces(tmp_path: Path) -> Path:
    """Create a project via the public API, attach trace rows directly (no public API), return path."""
    src = tmp_path / "src.xknx"
    svc = ProjectService()
    pid = svc.create(src, "P-TR")
    svc.close(pid)

    with Session(make_engine(url_for(src))) as session:
        project = session.get(Project, pid)
        assert project is not None
        project.traces.append(
            ProjectTrace(
                project_id=pid,
                date="2024-01-02T10:00:00",
                user_name="alice",
                comment=_CIPHER,
            )
        )
        project.traces.append(
            ProjectTrace(
                project_id=pid,
                date="2024-01-03T11:00:00",
                user_name="bob",
                comment=_PLAIN,
            )
        )
        session.commit()
    return src


def test_export_emits_project_traces_verbatim(tmp_path: Path) -> None:
    src = _project_with_traces(tmp_path)
    out = tmp_path / "out.knxproj"
    export_knxproj(src, out)

    with zipfile.ZipFile(out) as zf:
        pid = next(n for n in zf.namelist() if n.endswith("/project.xml")).split("/")[0]
        xml = zf.read(f"{pid}/project.xml").decode("utf-8")
        assert "<ProjectTraces>" in xml
        assert f'Comment="{_CIPHER}"' in xml  # encrypted comment kept byte-for-byte
        assert 'UserName="alice"' in xml
        assert f'Comment="{_PLAIN}"' in xml


def test_traces_survive_full_roundtrip(tmp_path: Path) -> None:
    src = _project_with_traces(tmp_path)
    out = tmp_path / "out.knxproj"
    export_knxproj(src, out)
    round_path = tmp_path / "round.xknx"
    import_knxproj(out, round_path)

    with Session(make_engine(url_for(round_path))) as session:
        project = session.query(Project).one()
        assert [(t.date, t.user_name, t.comment) for t in project.traces] == [
            ("2024-01-02T10:00:00", "alice", _CIPHER),
            ("2024-01-03T11:00:00", "bob", _PLAIN),
        ]
