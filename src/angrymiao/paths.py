"""xdg storage locations for firmware and device state."""

from __future__ import annotations

import os
from pathlib import Path


def _xdg_home(variable: str, fallback: Path) -> Path:
    configured = os.environ.get(variable)
    if configured:
        path = Path(configured)
        if path.is_absolute():
            return path
    return Path.home() / fallback


def state_root() -> Path:
    """returns the persistent state directory."""
    return _xdg_home("XDG_STATE_HOME", Path(".local/state")) / "angrymiao"


def firmware_root() -> Path:
    """returns the directory for retained firmware packages."""
    return _xdg_home("XDG_DATA_HOME", Path(".local/share")) / "angrymiao" / "firmware"
