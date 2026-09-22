"""Parsing raw log lines into records. No diagnosis happens here.

The two parsers are pure functions. This module adds the one thing they
deliberately do not know about: which parser a line belongs to, and the site
metadata the monitor already established, so nothing has to be rediscovered
from the line itself.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Dict, Optional, Union

from ..collectors.logs import ACCESS, ERROR, LogEvent
from .nginx_access import AccessEvent, parse_access_line, parse_time_local
from .nginx_error import ErrorEvent, classify, parse_error_line

logger = logging.getLogger("aadoctor.parsers")

ParsedEvent = Union[AccessEvent, ErrorEvent]

#: Below this many lines from one file, an unparsed ratio means nothing.
MIN_LINES_FOR_RATIO = 20

#: Above this share of unparsed lines, the format is probably not one we know.
UNSUPPORTED_RATIO = 0.5


@dataclass
class ParseStats:
    """Counters for a daemon run. Numbers only - never the lines themselves.

    Per-path counts are bounded by the number of log files being followed, so
    this cannot grow with traffic.
    """

    access_parsed: int = 0
    access_unparsed: int = 0
    error_parsed: int = 0
    error_unparsed: int = 0
    #: Exceptions raised inside a parser. A malformed line is normal; this is
    #: not, and is the number worth looking at.
    failures: int = 0

    lines_by_path: Dict[str, int] = field(default_factory=dict)
    unparsed_by_path: Dict[str, int] = field(default_factory=dict)
    _warned: set = field(default_factory=set)

    @property
    def parsed(self) -> int:
        return self.access_parsed + self.error_parsed

    @property
    def unparsed(self) -> int:
        return self.access_unparsed + self.error_unparsed

    def summary(self) -> str:
        text = f"{self.parsed} parsed, {self.unparsed} unparsed"
        if self.failures:
            text += f", {self.failures} parser failures"
        return text


def parse(line: str, log_type: str) -> Optional[ParsedEvent]:
    """Parse one line with the parser its log type calls for.

    The type comes from discovery, not from guessing at the line's shape.
    """
    if log_type == ACCESS:
        return parse_access_line(line)
    if log_type == ERROR:
        return parse_error_line(line)
    return None


def parse_event(event: LogEvent) -> Optional[ParsedEvent]:
    """Parse a line the monitor delivered, carrying its metadata across."""
    parsed = parse(event.line, event.log_type)
    if parsed is None:
        return None

    parsed.site = event.site
    parsed.log_path = event.path
    return parsed


def consume(event: LogEvent, stats: ParseStats) -> Optional[ParsedEvent]:
    """Parse and count, without letting a parser bug reach the daemon loop.

    An unparsed line is counted silently: warning once per line would turn a
    format mismatch under load into a second flood, this time in our own log.
    """
    key = str(event.path)
    stats.lines_by_path[key] = stats.lines_by_path.get(key, 0) + 1

    try:
        parsed = parse_event(event)
    except Exception as exc:  # a bug in a parser, not a malformed line
        stats.failures += 1
        logger.warning("parser failed on a line from %s: %s", key, exc)
        return None

    if parsed is not None:
        if event.log_type == ACCESS:
            stats.access_parsed += 1
        else:
            stats.error_parsed += 1
        return parsed

    if event.log_type == ACCESS:
        stats.access_unparsed += 1
    else:
        stats.error_unparsed += 1
    stats.unparsed_by_path[key] = stats.unparsed_by_path.get(key, 0) + 1
    _maybe_warn_unsupported(key, stats)
    return None


def _maybe_warn_unsupported(key: str, stats: ParseStats) -> None:
    """Say once, per file, that its format looks unfamiliar.

    aaDoctor never adapts silently and never rewrites the server's log_format
    to suit itself (README.md §68): it reports and leaves the decision alone.
    """
    if key in stats._warned:
        return

    lines = stats.lines_by_path.get(key, 0)
    if lines < MIN_LINES_FOR_RATIO:
        return

    unparsed = stats.unparsed_by_path.get(key, 0)
    if unparsed / lines < UNSUPPORTED_RATIO:
        return

    stats._warned.add(key)
    logger.warning(
        "%s: %s of %s lines are in a format aaDoctor does not recognise; "
        "its log_format is probably not one of the standard ones",
        key,
        unparsed,
        lines,
    )


__all__ = [
    "ACCESS",
    "ERROR",
    "AccessEvent",
    "ErrorEvent",
    "ParseStats",
    "ParsedEvent",
    "classify",
    "consume",
    "parse",
    "parse_access_line",
    "parse_error_line",
    "parse_event",
    "parse_time_local",
]
