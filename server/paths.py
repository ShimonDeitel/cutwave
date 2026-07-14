"""Shared filesystem paths -- works whether cutwave is run from source
(`./run.sh` / `python3 server/app.py`) or bundled as a standalone app
(PyInstaller sets `sys.frozen` and extracts read-only assets to
`sys._MEIPASS`)."""
import os
import sys


def bundle_root():
    """Where read-only bundled assets (static/, models/) live."""
    if getattr(sys, "frozen", False):
        return sys._MEIPASS
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def user_data_dir():
    """Writable per-user directory for uploads/outputs/work/license state.
    Never inside the app bundle itself -- that's read-only once installed
    and code-signed."""
    if getattr(sys, "frozen", False):
        d = os.path.expanduser("~/Library/Application Support/cutwave")
    else:
        d = bundle_root()
    os.makedirs(d, exist_ok=True)
    return d
