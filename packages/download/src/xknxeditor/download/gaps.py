"""Registry of Load Controls this engine does not execute yet.

The KNX Standard v3.0.0 (Chapter 2/3/1 "Load Controls" and the Application Layer
services in 3/3/7) plus the load procedure define more Load Controls than
this engine currently implements. When the runner meets one it fails - or, in a
read-only preflight, logs - a message that names the KNX Standard service the
control maps to. That way a bug report shows exactly which piece of the
implementation is missing instead of an opaque class name.

Keep :data:`KNOWN_GAPS` in sync with :mod:`xknxeditor.download.procedure`: a control
handled in ``LoadProcedureRunner._execute`` must not appear here, and a control
that appears here must not be silently accepted anywhere.
"""

from __future__ import annotations

# Control name -> the KNX Standard v3.0.0 service / clause it maps to. These are
# defined by the standard and emitted by load procedures but not yet executed
# by this engine; hitting one is a known implementation gap, not a data error.
KNOWN_GAPS: dict[str, str] = {}

# Controls that legitimately have nothing to write, so a read-only preflight can
# skip them without hiding a gap: state events, segment allocations, delays,
# restarts, read-backs and the client-side directives.
PREFLIGHT_NO_WRITE: frozenset[str] = frozenset(
    {
        "LdCtrlConnect",
        "LdCtrlDisconnect",
        "LdCtrlDelay",
        "LdCtrlRestart",
        "LdCtrlMasterReset",
        "LdCtrlLoad",
        "LdCtrlUnload",
        "LdCtrlLoadCompleted",
        "LdCtrlRelSegment",
        "LdCtrlTaskSegment",
        "LdCtrlTaskPtr",
        # xsdata splits TaskCtrl into two classes; both are executed load events with nothing for a
        # read-only preflight to diff (the stale "LdCtrlTaskCtrl" never matched type().__name__).
        "LdCtrlTaskCtrl1",
        "LdCtrlTaskCtrl2",
        "LdCtrlLoadImageMem",
        "LdCtrlLoadImageProp",
        "LdCtrlLoadImageRelMem",
        "LdCtrlReadFunctionProp",
        "LdCtrlInvokeFunctionProp",
        "LdCtrlMaxLength",
        "LdCtrlSetControlVariable",
        "LdCtrlMapError",
        # A merge point left inside a merged fragment does nothing.
        "LdCtrlMerge",
        "LdCtrlProgressText",
        "LdCtrlClearCachedObjectTypes",
        "LdCtrlDeclarePropDesc",
        # The coupler filter table is cleared then fully rewritten by the following
        # WriteRelMem/WriteMem; the clear itself writes nothing to preview. (xsdata class
        # name, matching type(control).__name__.)
        "LdCtrlClearLcfilterTable",
    }
)


def gap_hint(control_name: str) -> str | None:
    """The KNX Standard service a known-gap control maps to, else ``None``."""
    return KNOWN_GAPS.get(control_name)


def describe_missing(control_name: str) -> str:
    """A bug-report-ready description of why ``control_name`` is not handled."""
    hint = KNOWN_GAPS.get(control_name)
    if hint is not None:
        return (
            f"load control {control_name!r} is a known but not-yet-implemented "
            f"step in xknxeditor-download; it maps to {hint}"
        )
    return (
        f"load control {control_name!r} is not recognised by xknxeditor-download and is "
        f"not in its known-gap registry (gaps.py); it still needs to be mapped to a "
        f"KNX Standard v3.0.0 service and implemented"
    )
