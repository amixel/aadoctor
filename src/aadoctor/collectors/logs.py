"""Follow the site logs discovered in aaPanel, reading only what is new.

The rule this module exists to enforce: **never read a historical log**. A
first sight of a 14 GB access log costs one `stat` and contributes nothing
until the file grows (README.md §17, ADR-005).

Files are opened for reading and nothing else. aaDoctor never rotates,
truncates, moves, deletes or chmods a log it follows - not even while handling
an error (ADR-001).

No parsing happens here. A line is bytes decoded to text with its metadata
attached; understanding it is SPEC-004's job.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, Iterable, List, Optional, Set, Tuple

from ..discovery import LOG_CONFIGURED, DiscoveryResult
from ..paths import STATE_FILE
from ..storage import read_json, write_json

logger = logging.getLogger("aadoctor.logs")

ACCESS = "access"
ERROR = "error"

#: Read size per filesystem read. Caps peak memory per poll.
CHUNK_BYTES = 256 * 1024

#: Most bytes one file may contribute to a single poll. A site under attack
#: cannot starve the others; the rest is picked up on the next tick.
MAX_BYTES_PER_POLL = 8 * 1024 * 1024

#: A "line" longer than this is emitted truncated rather than held forever.
MAX_LINE_BYTES = 64 * 1024

STATE_VERSION = 1


@dataclass
class LogEvent:
    """One raw line, with enough context for SPEC-004 to attribute it."""

    path: Path
    log_type: str
    line: str
    sites: Tuple[str, ...] = ()

    @property
    def site(self) -> str:
        """The site this line belongs to; the first when a log is shared."""
        return self.sites[0] if self.sites else ""


@dataclass
class WatchedLog:
    """A log file discovery told us to follow."""

    path: Path
    log_type: str
    sites: Tuple[str, ...] = ()


@dataclass
class FileState:
    """Where we are in one physical file.

    ``device`` travels with ``inode`` because an inode number is only unique
    within a filesystem. ``size`` is the size at the last poll: a file that
    shrank was truncated or replaced, and that is worth catching even when we
    were still behind its old end.
    """

    device: int
    inode: int
    offset: int
    size: int = 0

    def as_dict(self) -> Dict[str, int]:
        return {
            "device": self.device,
            "inode": self.inode,
            "offset": self.offset,
            "size": self.size,
        }

    @classmethod
    def from_dict(cls, data: object) -> Optional["FileState"]:
        if not isinstance(data, dict):
            return None
        try:
            state = cls(
                device=int(data["device"]),
                inode=int(data["inode"]),
                offset=int(data["offset"]),
                # Absent in state written before this field existed.
                size=int(data.get("size", 0)),
            )
        except (KeyError, TypeError, ValueError):
            return None
        return state if state.offset >= 0 else None


@dataclass
class PollStats:
    """What one poll did. Numbers only - never the lines themselves."""

    files: int = 0
    lines: int = 0
    bytes_read: int = 0
    first_seen: int = 0
    rotations: int = 0
    truncations: int = 0
    unavailable: int = 0
    behind: List[Path] = field(default_factory=list)

    @property
    def changed(self) -> bool:
        return bool(
            self.lines or self.first_seen or self.rotations or self.truncations
        )


class LogMonitor:
    """Follows a set of log files incrementally, across restarts.

    Discovery owns which files are followed; this class owns where we are in
    each of them.
    """

    def __init__(
        self,
        state_path: Optional[Path] = None,
        chunk_bytes: int = CHUNK_BYTES,
        max_bytes_per_poll: int = MAX_BYTES_PER_POLL,
        max_line_bytes: int = MAX_LINE_BYTES,
    ) -> None:
        self.state_path = Path(state_path) if state_path is not None else STATE_FILE
        self.chunk_bytes = max(1024, int(chunk_bytes))
        self.max_bytes_per_poll = max(self.chunk_bytes, int(max_bytes_per_poll))
        self.max_line_bytes = max(1024, int(max_line_bytes))

        self._watched: Dict[str, WatchedLog] = {}
        self._state: Dict[str, FileState] = {}
        self._unavailable: Set[str] = set()
        self._dirty = False

        self._load_state()

    # --- what we follow ---------------------------------------------------

    def update_sources(self, result: DiscoveryResult) -> Tuple[List[str], List[str]]:
        """Replace the watch set from a discovery pass.

        Returns the paths added and removed. A log that is configured but not
        on disk yet is watched: it starts producing lines when it appears
        (SPEC-002 separates "configured" from "exists").

        Two sites pointing at one file give one entry, so the file is read
        once and both names travel with every line.
        """
        collected: Dict[str, WatchedLog] = {}

        for site in result.sites:
            for target, log_type in ((site.access, ACCESS), (site.error, ERROR)):
                if target.state != LOG_CONFIGURED or target.path is None:
                    continue

                key = str(target.path)
                existing = collected.get(key)
                if existing is None:
                    collected[key] = WatchedLog(target.path, log_type, (site.name,))
                    continue

                if existing.log_type != log_type:
                    # One file used as both an access and an error log. Keep
                    # the first classification and read it once rather than
                    # opening the same bytes twice.
                    logger.warning(
                        "%s is declared as both %s and %s log; treating it as %s",
                        key,
                        existing.log_type,
                        log_type,
                        existing.log_type,
                    )
                if site.name not in existing.sites:
                    existing.sites = existing.sites + (site.name,)

        added = sorted(set(collected) - set(self._watched))
        removed = sorted(set(self._watched) - set(collected))
        self._watched = collected

        for key in removed:
            self._unavailable.discard(key)

        # State for a path we no longer watch is kept, not pruned: a site that
        # flaps out of discovery for one pass should not lose its position.
        # Entries are a few dozen bytes each and bounded by the number of
        # distinct log paths the server has ever had.
        return added, removed

    @property
    def watched_paths(self) -> List[Path]:
        return [self._watched[key].path for key in sorted(self._watched)]

    @property
    def watched_count(self) -> int:
        return len(self._watched)

    @property
    def tracked_count(self) -> int:
        """Files we hold an offset for, watched or not."""
        return len(self._state)

    def offset_of(self, path: Path) -> Optional[int]:
        state = self._state.get(str(path))
        return state.offset if state else None

    # --- reading ----------------------------------------------------------

    def poll(self, callback: Optional[Callable[[LogEvent], None]] = None) -> PollStats:
        """Read what is new in every watched file.

        One unreadable file never stops the others (README.md §65). Files are
        visited in a fixed order so a run is reproducible.
        """
        stats = PollStats()

        for key in sorted(self._watched):
            watched = self._watched[key]
            try:
                self._poll_one(key, watched, callback, stats)
            except Exception as exc:  # a bug here must not end the daemon
                logger.warning("error while reading %s: %s", watched.path, exc)

        return stats

    def _poll_one(
        self,
        key: str,
        watched: WatchedLog,
        callback: Optional[Callable[[LogEvent], None]],
        stats: PollStats,
    ) -> None:
        status = self._stat(key, watched, stats)
        if status is None:
            return

        stats.files += 1
        saved = self._state.get(key)

        if saved is None:
            # First sight: start at the end. The history in this file is not
            # ours to read, however large it is.
            self._state[key] = FileState(
                status.st_dev, status.st_ino, status.st_size, status.st_size
            )
            self._dirty = True
            stats.first_seen += 1
            logger.debug(
                "watching %s inode=%s from offset %s",
                key,
                status.st_ino,
                status.st_size,
            )
            return

        if (status.st_dev, status.st_ino) != (saved.device, saved.inode):
            # Rotation, or the file was replaced. This one is a file we were
            # already following, so its content is new and is read from the
            # start - unlike a file seen for the first time.
            logger.info(
                "rotation detected for %s; lines written to the previous file "
                "after offset %s were not read",
                key,
                saved.offset,
            )
            saved = FileState(status.st_dev, status.st_ino, 0, status.st_size)
            self._state[key] = saved
            self._dirty = True
            stats.rotations += 1
        elif status.st_size < max(saved.offset, saved.size):
            # The file shrank: truncated in place by copytruncate or '> file',
            # or replaced by a smaller one. Comparing against the last observed
            # size as well as the offset catches the case where we were still
            # behind the old end.
            logger.info(
                "truncation detected for %s; restarting at the beginning", key
            )
            saved = FileState(saved.device, saved.inode, 0, status.st_size)
            self._state[key] = saved
            self._dirty = True
            stats.truncations += 1

        if saved.size != status.st_size:
            saved.size = status.st_size
            self._dirty = True

        if status.st_size <= saved.offset:
            return

        self._read_new(key, watched, saved, callback, stats)

    def _stat(self, key: str, watched: WatchedLog, stats: PollStats) -> Optional[os.stat_result]:
        """Stat the file, reporting availability changes once each."""
        try:
            status = os.stat(key)
        except FileNotFoundError:
            self._mark_unavailable(key, stats, "does not exist")
            return None
        except PermissionError:
            self._mark_unavailable(key, stats, "permission denied")
            return None
        except OSError as exc:
            self._mark_unavailable(key, stats, str(exc))
            return None

        if key in self._unavailable:
            self._unavailable.discard(key)
            logger.info("%s is readable again", watched.path)
        return status

    def _mark_unavailable(self, key: str, stats: PollStats, reason: str) -> None:
        stats.unavailable += 1
        if key not in self._unavailable:
            self._unavailable.add(key)
            # Said once per state change, not once per tick.
            logger.info("%s is not available: %s", key, reason)

    def _read_new(
        self,
        key: str,
        watched: WatchedLog,
        saved: FileState,
        callback: Optional[Callable[[LogEvent], None]],
        stats: PollStats,
    ) -> None:
        """Read appended bytes in bounded chunks, emitting complete lines.

        The offset advances only past complete lines. Trailing bytes without a
        newline are left unconsumed and re-read next poll, so nothing partial
        has to be remembered - in memory or on disk - and a restart resumes
        correctly.
        """
        pending = b""
        read_bytes = 0
        still_more = False

        try:
            with open(key, "rb") as handle:
                handle.seek(saved.offset)

                while read_bytes < self.max_bytes_per_poll:
                    want = min(self.chunk_bytes, self.max_bytes_per_poll - read_bytes)
                    chunk = handle.read(want)
                    if not chunk:
                        break

                    read_bytes += len(chunk)
                    buffer = pending + chunk
                    pieces = buffer.split(b"\n")
                    pending = pieces.pop()

                    for raw in pieces:
                        self._emit(raw, watched, callback, stats)

                    if len(pending) > self.max_line_bytes:
                        # Never hold an unbounded "line": emit what we have and
                        # move on, rather than re-reading it every poll.
                        logger.warning(
                            "line longer than %s bytes in %s; emitting it truncated",
                            self.max_line_bytes,
                            key,
                        )
                        self._emit(pending, watched, callback, stats)
                        pending = b""

                still_more = bool(handle.read(1))
        except PermissionError:
            self._mark_unavailable(key, stats, "permission denied")
            return
        except FileNotFoundError:
            self._mark_unavailable(key, stats, "disappeared while reading")
            return
        except OSError as exc:
            logger.warning("cannot read %s: %s", key, exc)
            return

        consumed = read_bytes - len(pending)
        if consumed > 0:
            saved.offset += consumed
            self._state[key] = saved
            self._dirty = True

        stats.bytes_read += consumed

        if still_more:
            # Not an error: the rest arrives on the next tick.
            stats.behind.append(watched.path)
            logger.debug("%s has more than one poll's worth of new data", key)

    def _emit(
        self,
        raw: bytes,
        watched: WatchedLog,
        callback: Optional[Callable[[LogEvent], None]],
        stats: PollStats,
    ) -> None:
        line = raw.rstrip(b"\r").decode("utf-8", errors="replace")
        if not line:
            return

        stats.lines += 1
        if callback is not None:
            callback(LogEvent(watched.path, watched.log_type, line, watched.sites))

    # --- state ------------------------------------------------------------

    def _load_state(self) -> None:
        """Restore offsets. Anything unusable is discarded, never guessed at.

        Losing state means every file is seen as new and starts at its end.
        That loses recent history; reading gigabytes from byte zero would be
        far worse (ADR-005).
        """
        data = read_json(self.state_path)
        if data is None:
            return

        if not isinstance(data, dict) or not isinstance(data.get("files"), dict):
            logger.warning(
                "state in %s is not in a usable shape; starting from the end of "
                "each log",
                self.state_path,
            )
            return

        kept = 0
        for key, entry in data["files"].items():
            state = FileState.from_dict(entry)
            if state is None or not isinstance(key, str):
                logger.warning("ignoring malformed state entry for %r", key)
                continue
            self._state[key] = state
            kept += 1

        logger.debug("restored offsets for %s files from %s", kept, self.state_path)

    def save(self, force: bool = False) -> bool:
        """Persist offsets when they moved. Returns True when it wrote."""
        if not self._dirty and not force:
            return False

        payload = {
            "version": STATE_VERSION,
            "files": {key: state.as_dict() for key, state in sorted(self._state.items())},
        }
        if write_json(self.state_path, payload):
            self._dirty = False
            return True
        return False

    @property
    def dirty(self) -> bool:
        return self._dirty


def summarize(stats: PollStats) -> str:
    """One line for the daemon log. Counts only, never content."""
    parts = [f"{stats.lines} lines from {stats.files} files"]
    if stats.first_seen:
        parts.append(f"{stats.first_seen} newly watched")
    if stats.rotations:
        parts.append(f"{stats.rotations} rotated")
    if stats.truncations:
        parts.append(f"{stats.truncations} truncated")
    if stats.unavailable:
        parts.append(f"{stats.unavailable} unavailable")
    if stats.behind:
        parts.append(f"{len(stats.behind)} behind")
    return ", ".join(parts)


def iter_events(monitor: LogMonitor) -> Iterable[LogEvent]:
    """Collect one poll's events. Convenient for tests; the daemon uses the
    callback, which keeps only one event alive at a time."""
    collected: List[LogEvent] = []
    monitor.poll(collected.append)
    return collected
