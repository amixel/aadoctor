"""Turn one Nginx error log line into a record. Nothing more.

Pure, like the access parser: a string in, a record or ``None`` out.

A ``kind`` is attached when the line matches a known pattern, from a table
that is data rather than a chain of branches. A kind describes *one line*
- "this line says an upstream timed out". It is not a finding: a finding is a
conclusion about many lines, with a threshold and a confidence behind it, and
that belongs to SPEC-007. The two use different naming on purpose, so they can
never be mistaken for each other.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Dict, Optional, Tuple

ERROR = "error"

#: Levels Nginx writes. An unknown one is kept as-is, never a reason to fail.
LEVELS = ("debug", "info", "notice", "warn", "error", "crit", "alert", "emerg")

#: Line classifications. First match wins, so the more specific PHP patterns
#: come before the transport-level ones they arrive wrapped in: a FastCGI
#: stderr line carrying a PHP fatal is more usefully a php_fatal.
#:
#: Adding a pattern is one entry here and nothing else.
KIND_PATTERNS: Tuple[Tuple[str, str], ...] = (
    ("PHP Fatal error", "php_fatal"),
    ("PHP Parse error", "php_parse_error"),
    ("Allowed memory size", "php_memory_exhausted"),
    ("Maximum execution time", "php_execution_timeout"),
    ("PHP Warning", "php_warning"),
    ("PHP Notice", "php_notice"),
    ("upstream timed out", "upstream_timeout"),
    ("upstream prematurely closed connection", "upstream_closed"),
    ("FastCGI sent in stderr", "fastcgi_stderr"),
    ("connect() failed", "connect_failed"),
    ("recv() failed", "recv_failed"),
    ("worker_connections are not enough", "worker_connections_exhausted"),
    ("too many open files", "too_many_open_files"),
    ("client intended to send too large body", "body_too_large"),
    ("open() failed", "open_failed"),
    ("no live upstreams", "no_live_upstreams"),
    ("SSL_do_handshake() failed", "ssl_handshake_failed"),
)

# 2026/09/22 16:42:12 [error] 1234#1234: *928 the message
_PREFIX = re.compile(
    r"^(?P<year>\d{4})/(?P<month>\d{2})/(?P<day>\d{2})"
    r" (?P<hour>\d{2}):(?P<minute>\d{2}):(?P<second>\d{2})"
    r" \[(?P<level>[a-zA-Z]+)\]"
    r"(?: (?P<pid>\d+)#(?P<tid>\d+):)?"
    r"(?: \*(?P<connection>\d+))?"
    r" (?P<rest>.*)$"
)

#: The trailing context Nginx appends. These are matched as markers rather
#: than splitting the line on commas: a message or a URL may contain commas,
#: and cutting there would mangle both. `referrer` and `subrequest` are listed
#: so they terminate the value before them, even though they are not kept.
_CONTEXT = re.compile(
    r", (?P<key>client|server|request|upstream|host|referrer|subrequest): "
)

_KEPT_CONTEXT = ("client", "server", "request", "upstream", "host")

#: Messages are bounded: a stderr dump should not travel around in memory.
MAX_MESSAGE = 2000


@dataclass
class ErrorEvent:
    """One line of the error log, taken apart but not judged."""

    timestamp: Optional[datetime] = None
    level: Optional[str] = None
    pid: Optional[int] = None
    tid: Optional[int] = None
    connection: Optional[int] = None
    message: str = ""
    #: A classification of this line, or None. Never a finding.
    kind: Optional[str] = None
    client: Optional[str] = None
    server: Optional[str] = None
    request: Optional[str] = None
    upstream: Optional[str] = None
    host: Optional[str] = None

    # Filled by the dispatcher from the monitor's metadata.
    site: str = ""
    log_path: Optional[Path] = None

    #: Class attribute, not a field.
    log_type = ERROR


def parse_error_line(line: str) -> Optional[ErrorEvent]:
    """Parse one line, or return None when it carries no Nginx error prefix.

    A continuation line from a PHP stack trace has no prefix and so returns
    None. Attaching continuations to the event before them is specified but
    deliberately not implemented yet - see SPEC-004.
    """
    match = _PREFIX.match(line)
    if match is None:
        return None

    message, context = _split_context(match.group("rest"))

    return ErrorEvent(
        timestamp=_timestamp(match),
        level=_level(match.group("level")),
        pid=_optional_int(match.group("pid")),
        tid=_optional_int(match.group("tid")),
        connection=_optional_int(match.group("connection")),
        message=message[:MAX_MESSAGE],
        kind=classify(message),
        client=context.get("client"),
        server=context.get("server"),
        request=context.get("request"),
        upstream=context.get("upstream"),
        host=context.get("host"),
    )


#: Lowercased once at import so matching costs one lower() per line.
_KIND_PATTERNS_LOWER = tuple(
    (pattern.lower(), kind) for pattern, kind in KIND_PATTERNS
)


def classify(message: str) -> Optional[str]:
    """Name what a message says, from the table above. Not a diagnosis.

    Matching ignores case: some of these strings come from ``strerror`` and
    arrive capitalised - Nginx writes ``(24: Too many open files)`` - and the
    casing of a C library message is not something to depend on.
    """
    lowered = message.lower()
    for pattern, kind in _KIND_PATTERNS_LOWER:
        if pattern in lowered:
            return kind
    return None


def _split_context(rest: str) -> Tuple[str, Dict[str, str]]:
    """Separate the message from the trailing ``key: value`` context."""
    markers = list(_CONTEXT.finditer(rest))
    if not markers:
        return rest.strip(), {}

    message = rest[: markers[0].start()].strip()

    context: Dict[str, str] = {}
    for index, marker in enumerate(markers):
        key = marker.group("key")
        end = markers[index + 1].start() if index + 1 < len(markers) else len(rest)
        if key in _KEPT_CONTEXT:
            context[key] = _unquote(rest[marker.end() : end])

    return message, context


def _unquote(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == '"' and value[-1] == '"':
        return value[1:-1]
    return value


def _timestamp(match: "re.Match") -> Optional[datetime]:
    """Build the timestamp. It is naive on purpose.

    The Nginx error log carries no timezone, so one is not invented; the
    access log's stamps are aware, and SPEC-005 must not compare the two
    without deciding what the server's local zone is.
    """
    try:
        return datetime(
            int(match.group("year")),
            int(match.group("month")),
            int(match.group("day")),
            int(match.group("hour")),
            int(match.group("minute")),
            int(match.group("second")),
        )
    except ValueError:
        return None


def _level(value: Optional[str]) -> Optional[str]:
    if not value:
        return None
    lowered = value.lower()
    # An unfamiliar level is kept rather than dropped; Nginx modules invent
    # their own, and losing the word would lose information.
    return lowered


def _optional_int(value: Optional[str]) -> Optional[int]:
    if value is None:
        return None
    try:
        return int(value)
    except ValueError:  # pragma: no cover - the pattern only matches digits
        return None
