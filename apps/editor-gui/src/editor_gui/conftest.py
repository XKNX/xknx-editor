"""The editor's tests never write to (or prune) the user's log folder: their log goes nowhere."""

import os

from editor_gui.log_file import set_session_log

set_session_log(open(os.devnull, "w", encoding="utf-8"))  # noqa: SIM115 - kept for the run
