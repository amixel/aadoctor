"""Small JSON files, written without leaving a corrupt one behind.

aaDoctor keeps its state in plain files under /var/lib/aadoctor
([ADR-003](../../../docs/adr/ADR-003-filesystem-state-without-database.md)).
This module is the whole storage layer: write to a temporary file in the same
directory, then rename over the target. A crash mid-write leaves the previous
file intact, never a half-written one.

There is deliberately no fsync. Losing the last write costs a few re-read log
lines, which SPEC-003 already accepts; fsync on every poll would cost disk I/O
on a server aaDoctor is supposed to stay out of the way of (README.md §58).
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger("aadoctor.storage")

#: State may name site log paths, so it is not world-readable.
FILE_MODE = 0o640


def read_json(path: Path) -> Optional[Any]:
    """Return the parsed contents, or None when missing or unusable.

    Unreadable state is never a reason to stop: the caller degrades to a safe
    default instead.
    """
    try:
        with open(str(path), "r", encoding="utf-8") as handle:
            return json.load(handle)
    except FileNotFoundError:
        return None
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        logger.warning("ignoring unreadable state in %s: %s", path, exc)
        return None
    except OSError as exc:
        logger.warning("cannot read %s: %s", path, exc)
        return None


def write_json(path: Path, data: Any) -> bool:
    """Write ``data`` atomically. Returns False when it could not be written.

    A failure here is logged and reported, never raised: the daemon keeps
    running with its in-memory state.
    """
    target = Path(path)
    temporary = target.with_name(target.name + ".tmp")

    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        # os.open sets the mode at creation, so the file is never briefly
        # world-readable and no chmod is needed afterwards.
        descriptor = os.open(
            str(temporary),
            os.O_WRONLY | os.O_CREAT | os.O_TRUNC,
            FILE_MODE,
        )
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(data, handle, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()

        os.replace(str(temporary), str(target))
        return True
    except OSError as exc:
        logger.warning("cannot write %s: %s", target, exc)
        _discard(temporary)
        return False


def _discard(temporary: Path) -> None:
    """Remove our own leftover temporary file. Never touches anything else."""
    try:
        os.unlink(str(temporary))
    except OSError:
        pass
