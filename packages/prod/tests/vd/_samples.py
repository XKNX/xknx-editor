"""The optional sample product databases: the folder ``XKNX_TEST_VD_SAMPLES`` names (``.vd1`` …
``.vd5`` files). Tests on the samples skip when it is not set or a file is missing."""

from __future__ import annotations

import os
from pathlib import Path

SAMPLES = Path(os.environ.get("XKNX_TEST_VD_SAMPLES") or "vd-samples-not-set")
