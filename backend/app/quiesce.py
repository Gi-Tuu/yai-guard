"""Operator quiesce marker used by backup to stop new task writes.

The marker is an ordinary file under ARTIFACTS so the offline backup script can
create it without an HTTP round trip. Existing running tasks finish; new task
creation and worker claims are blocked until the marker is removed.
"""
from __future__ import annotations

from pathlib import Path

from app import settings


def marker_path() -> Path:
    return settings.ARTIFACTS / "quiesce"


def is_quiesced() -> bool:
    return marker_path().exists()
