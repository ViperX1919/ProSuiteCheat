"""Filesystem locations that differ between a source checkout and a frozen exe."""

from __future__ import annotations

import os
import sys
import tempfile
from functools import lru_cache


def is_frozen() -> bool:
    """True when running from a PyInstaller bundle."""
    return getattr(sys, "frozen", False)


@lru_cache(maxsize=1)
def base_dir() -> str:
    """Directory that assets are resolved against."""
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        return meipass
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


@lru_cache(maxsize=None)
def resource_path(relative_path: str) -> str:
    """Absolute path to a bundled asset.

    Looks in ``assets/`` first, then alongside the executable, and finally
    returns the ``assets/`` path so callers can report a sensible missing-file
    error.
    """
    root = base_dir()

    asset_path = os.path.join(root, "assets", relative_path)
    if os.path.exists(asset_path):
        return asset_path

    direct_path = os.path.join(root, relative_path)
    if os.path.exists(direct_path):
        return direct_path

    return asset_path


@lru_cache(maxsize=1)
def config_dir() -> str:
    """Directory used as the default location for saved configs.

    A frozen build cannot write next to itself reliably, so it falls back to a
    per-user temp directory.
    """
    if is_frozen():
        path = os.path.join(tempfile.gettempdir(), "ProSuiteCheat")
    else:
        path = os.path.join(base_dir(), "config")

    os.makedirs(path, exist_ok=True)
    return path


@lru_cache(maxsize=1)
def log_path() -> str:
    """Full path of the application log file."""
    if is_frozen():
        return os.path.join(config_dir(), "prosuite.log")
    return os.path.join(base_dir(), "prosuite.log")
