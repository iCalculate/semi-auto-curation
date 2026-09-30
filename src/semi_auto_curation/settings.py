from __future__ import annotations

from pathlib import Path


DEFAULT_AUTOTEST_SESSION_ROOT = Path(r"D:\Project\semi-auto-probe\autotest_session")


def default_source_browse_directory() -> str:
    """Return the shared starting directory for local source pickers."""
    if DEFAULT_AUTOTEST_SESSION_ROOT.is_dir():
        return str(DEFAULT_AUTOTEST_SESSION_ROOT)
    return str(Path.cwd())
