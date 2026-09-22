"""The small snapshot the daemon publishes for the CLI to read.

`aadoctor top` runs in a different process from the daemon, so it cannot see
the daemon's windows. Rather than opening a socket or an HTTP port for it, the
daemon writes a bounded snapshot to a file every poll and the CLI reads it.

This is not a database and not history: one file, overwritten in place, holding
only the aggregates two windows currently show. It is deleted with the rest of
`/var/lib/aadoctor` on purge, and it never contains a raw log line.
"""

from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Any, Dict, Optional

from .paths import RUNTIME_FILE
from .storage import read_json, write_json

#: Windows published for the CLI. More would only be more to keep current.
WINDOWS = (60, 300)

#: Rows per list in the file. `top` shows fewer; this leaves a little room.
TOP_ENTRIES = 10
TOP_SITES = 10

VERSION = 1


def build(aggregator, windows=WINDOWS, load=None, incident=None) -> Dict[str, Any]:
    """Assemble the payload. Aggregates only - never a line of log content.

    User agents are deliberately left out: they are the longest and least
    diagnostic values aggregation holds, and the file would carry them to
    disk for no benefit (SPEC-005). They stay in memory for SPEC-007.

    The current load and whether an incident is open travel here too, so
    `aadoctor status` can answer without talking to the daemon.
    """
    payload = {
        "version": VERSION,
        "updated_at": time.time(),
        "pid": os.getpid(),
        "windows": {
            str(window): aggregator.snapshot(
                window_seconds=window, top=TOP_ENTRIES, sites=TOP_SITES
            ).as_dict()
            for window in windows
        },
    }

    if load is not None:
        payload["load"] = load.as_dict()

    payload["incident"] = (
        {
            "open": True,
            "id": incident.id,
            "started_at": incident.started_at,
            "severity": incident.severity,
            "peak_load_per_cpu": incident.peak_load_per_cpu,
        }
        if incident is not None
        else {"open": False}
    )

    return payload


def write(
    aggregator,
    path: Optional[Path] = None,
    windows=WINDOWS,
    load=None,
    incident=None,
) -> bool:
    """Publish the current windows. Atomic, and never world-readable."""
    target = Path(path) if path is not None else RUNTIME_FILE
    return write_json(target, build(aggregator, windows, load=load, incident=incident))


def read(path: Optional[Path] = None) -> Optional[Dict[str, Any]]:
    """Read the published snapshot, or None when there is none to read."""
    target = Path(path) if path is not None else RUNTIME_FILE
    payload = read_json(target)

    if not isinstance(payload, dict) or "windows" not in payload:
        return None
    return payload


def age(payload: Dict[str, Any], now: Optional[float] = None) -> Optional[float]:
    """Seconds since the snapshot was written, or None if it does not say.

    The caller is expected to show this. A snapshot from an hour ago describes
    an hour ago, and presenting it as current would be a lie the reader has no
    way to catch.
    """
    updated = payload.get("updated_at")
    if not isinstance(updated, (int, float)):
        return None
    return max(0.0, (time.time() if now is None else now) - float(updated))


def window(payload: Dict[str, Any], seconds: int) -> Optional[Dict[str, Any]]:
    """One window out of the payload, or None when it was not published."""
    windows = payload.get("windows")
    if not isinstance(windows, dict):
        return None

    found = windows.get(str(int(seconds)))
    return found if isinstance(found, dict) else None


def available_windows(payload: Dict[str, Any]) -> list:
    windows = payload.get("windows")
    if not isinstance(windows, dict):
        return []
    return sorted(int(key) for key in windows if str(key).isdigit())
