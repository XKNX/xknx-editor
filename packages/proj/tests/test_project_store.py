"""Tests for the ETS ProjectStore integrity primitives (project_store).

Includes two independent sources of ground truth, on top of the ordinary unit tests:

* Vectors captured from a real, licensed ETS 5.7.7 ProjectStore folder (project ``P-0680``,
  manufacturer folder ``M-0001``, running under Wine with a genuine KNX dongle forwarded via
  USB/IP) -- these pin :func:`project_mask`, :func:`ets5hash` and :func:`store_descriptor`
  (including its reuse for a manufacturer folder, not just the project folder) against bytes ETS
  itself wrote and accepted.
* The published self-test vector from `etssign <https://github.com/daimoniac/etssign>`_, an
  independent, dependency-free C reimplementation of the same ``.ets5hash`` recipe, confirmed
  separately against both ETS 5.7.7 and ETS 6.4.1. Pinning it here means the two implementations
  can never silently drift apart.
"""

from __future__ import annotations

from xknxeditor.proj.core.project_store import ets5hash, project_mask, store_descriptor

# --- captured from a genuine ETS 5.7.7 ProjectStore (P-0680) -----------------------------------

_P0680_GUID = "4c9dd897-c732-40c2-913f-fd77f092cbef"
_P0680_ID = "P-0680"
_P0680_INSTALLER_GUID = "d559977f-8688-444f-a1b4-9eb870292f9d"

# Every optional section this project never touched (U, PT, and the legacy AI/BA/D/HE/IPSD/S/T/UF
# streams) shares this one value: the .ets5hash of zero bytes under the project's mask.
_P0680_EMPTY_SECTION_HASH = "CHzHCYyKfyhQYOMGtZdKp9MfOyw="


def test_project_mask_matches_genuine_store() -> None:
    assert project_mask(_P0680_GUID, _P0680_ID) == 2846


def test_empty_section_hash_matches_genuine_store() -> None:
    mask = project_mask(_P0680_GUID, _P0680_ID)
    assert ets5hash(b"", mask) == _P0680_EMPTY_SECTION_HASH


def test_store_descriptor_matches_genuine_project_folder() -> None:
    assert (
        store_descriptor(_P0680_ID, _P0680_INSTALLER_GUID)
        == "C603eS3FaGd+fEdvAqL5qCPy3/tBA+rsvTzulXd8FZs="
    )


def test_store_descriptor_matches_genuine_manufacturer_folder() -> None:
    # Same HMAC, same installer GUID, keyed by the manufacturer folder's own id instead of the
    # project id -- confirms store_descriptor's per-folder reuse (see its docstring).
    assert (
        store_descriptor("M-0001", _P0680_INSTALLER_GUID)
        == "5tvzkT3csR9d3gugQQErX/3+n27MQteqIiWlHI0PzZs="
    )


# --- cross-check against the independent etssign C implementation ------------------------------


def test_matches_etssign_self_test_vector() -> None:
    """Pin https://github.com/daimoniac/etssign's published self-test vector.

    ``etssign --self-test`` asserts the identical recipe produces ``mask == 319`` and
    ``ets_hash_b64("ets5hash", mask) == "TnwiqUYt9qo22B/u9TAwSUUcPTM="`` for this guid/id. Keeping
    that exact vector here means the two from-scratch implementations are checked against each
    other, not just against themselves.
    """
    guid = "00000000-0000-0000-0000-000000000001"
    project_id = "P-0001"
    mask = project_mask(guid, project_id)
    assert mask == 319
    assert ets5hash(b"ets5hash", mask) == "TnwiqUYt9qo22B/u9TAwSUUcPTM="
