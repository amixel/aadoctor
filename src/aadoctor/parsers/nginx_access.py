"""Turn one Nginx access log line into a record. Nothing more.

Pure: a string goes in, a record or ``None`` comes out. No file is opened, no
offset is known, no state is kept. That is what lets SPEC-005's aggregation be
reproduced from fixtures, and what keeps this module independent of the reader
that feeds it.

Two formats are supported - Nginx's ``common`` and ``combined``, the latter
including the trailing field Nginx's own default ``main`` format adds. A line
in some other shape is reported as unparsed rather than guessed at: the field
positions of an unknown ``log_format`` are not something to assume (SPEC-004).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional, Tuple

ACCESS = "access"

#: Nginx writes English month abbreviations regardless of the server locale,
#: so they are matched from a table rather than with strptime's %b, which
#: follows the process locale and would fail on, say, a pt_BR server.
_MONTHS = {
    "Jan": 1, "Feb": 2, "Mar": 3, "Apr": 4, "May": 5, "Jun": 6,
    "Jul": 7, "Aug": 8, "Sep": 9, "Oct": 10, "Nov": 11, "Dec": 12,
}

# $remote_addr - $remote_user [$time_local] "$request" $status $body_bytes_sent
# followed, in the combined format, by "$http_referer" "$http_user_agent" and
# whatever a site's log_format appends after that.
#
# Every quantifier here is linear over its own delimiter, so a hostile 8 KB
# URL cannot make it backtrack.
_ACCESS = re.compile(
    r"^(?P<remote_addr>\S+)"
    r" (?P<ident>\S+)"
    r" (?P<remote_user>\S+)"
    r" \[(?P<time_local>[^\]]+)\]"
    r' "(?P<request>[^"]*)"'
    r" (?P<status>\d{3})"
    r" (?P<body_bytes_sent>-|\d+)"
    r'(?: "(?P<referer>[^"]*)"'
    r' "(?P<user_agent>[^"]*)"'
    r"(?P<extra>.*))?$"
)

_TIME_LOCAL = re.compile(
    r"^(?P<day>\d{1,2})/(?P<month>[A-Za-z]{3})/(?P<year>\d{4})"
    r":(?P<hour>\d{2}):(?P<minute>\d{2}):(?P<second>\d{2})"
    r"(?: (?P<sign>[+-])(?P<tz_hour>\d{2})(?P<tz_minute>\d{2}))?$"
)

_ABSOLUTE_FORM = ("http://", "https://")


@dataclass
class AccessEvent:
    """One request, as the access log described it.

    Fields the line did not carry are ``None``. Nothing is zero-filled and
    nothing is inferred: a missing value must stay missing, or the counts built
    on top of it in SPEC-005 would be fiction.
    """

    timestamp: Optional[datetime] = None
    remote_addr: Optional[str] = None
    remote_user: Optional[str] = None
    method: Optional[str] = None
    request_target: Optional[str] = None
    path: Optional[str] = None
    query_string: Optional[str] = None
    protocol: Optional[str] = None
    status: Optional[int] = None
    body_bytes_sent: Optional[int] = None
    referer: Optional[str] = None
    user_agent: Optional[str] = None
    #: Whatever a site's log_format appends after the user agent, kept verbatim
    #: and deliberately uninterpreted. Nginx's default `main` puts
    #: $http_x_forwarded_for here; treating it as the client is a decision
    #: SPEC-004 has not taken.
    extra: Optional[str] = None

    # Filled by the dispatcher from the monitor's metadata, not by this module.
    site: str = ""
    log_path: Optional[Path] = None

    #: Class attribute, not a field: the type of the object is the log type.
    log_type = ACCESS


def parse_access_line(line: str) -> Optional[AccessEvent]:
    """Parse one line, or return None when it is not a format we know.

    Returning None is the normal outcome for an unsupported ``log_format``; it
    is not an error and never raises.
    """
    match = _ACCESS.match(line)
    if match is None:
        return None

    method, target, protocol = _split_request(match.group("request"))
    path, query = _split_target(target)
    extra = match.group("extra")

    return AccessEvent(
        timestamp=parse_time_local(match.group("time_local")),
        remote_addr=match.group("remote_addr"),
        remote_user=_optional(match.group("remote_user")),
        method=method,
        request_target=target,
        path=path,
        query_string=query,
        protocol=protocol,
        status=int(match.group("status")),
        body_bytes_sent=_optional_int(match.group("body_bytes_sent")),
        referer=_optional(match.group("referer")),
        user_agent=_optional(match.group("user_agent")),
        extra=extra.strip() or None if extra else None,
    )


def parse_time_local(value: str) -> Optional[datetime]:
    """Parse ``22/Sep/2026:16:20:31 -0300`` into an aware datetime.

    Returns None rather than raising when the stamp is not in that shape. A
    line without an offset yields a naive datetime: inventing a timezone would
    be worse than admitting we do not know.
    """
    match = _TIME_LOCAL.match(value.strip())
    if match is None:
        return None

    month = _MONTHS.get(match.group("month").title())
    if month is None:
        return None

    offset = None
    if match.group("sign"):
        minutes = int(match.group("tz_hour")) * 60 + int(match.group("tz_minute"))
        if match.group("sign") == "-":
            minutes = -minutes
        offset = timezone(timedelta(minutes=minutes))

    try:
        return datetime(
            int(match.group("year")),
            month,
            int(match.group("day")),
            int(match.group("hour")),
            int(match.group("minute")),
            int(match.group("second")),
            tzinfo=offset,
        )
    except ValueError:
        # 31/Feb and friends.
        return None


def _split_request(request: str) -> Tuple[Optional[str], Optional[str], Optional[str]]:
    """Split ``GET /a?b=1 HTTP/1.1`` into method, target and protocol.

    A request is not guaranteed to have three clean parts: Nginx logs ``-``
    when it never read one, and an unencoded space in the target is logged
    raw. Whatever is recoverable is kept; the rest is None.
    """
    request = request.strip()
    if not request or request == "-":
        return None, None, None

    parts = request.split(" ")
    if len(parts) == 1:
        return None, parts[0], None
    if len(parts) == 2:
        return parts[0], parts[1], None

    # More than three parts means the target itself contained spaces.
    if parts[-1].startswith("HTTP/"):
        return parts[0], " ".join(parts[1:-1]), parts[-1]
    return parts[0], " ".join(parts[1:]), None


def _split_target(target: Optional[str]) -> Tuple[Optional[str], Optional[str]]:
    """Split the target at the first ``?``.

    The query string is kept exactly as it arrived. Normalising it - sorting
    parameters, dropping tracking, lowercasing - belongs to aggregation, where
    the cost of collapsing two different endpoints into one is understood.
    """
    if not target:
        return None, None

    if target.startswith(_ABSOLUTE_FORM):
        # Absolute-form request target, as a proxy may send it.
        from urllib.parse import urlsplit

        try:
            split = urlsplit(target)
        except ValueError:
            return target, None
        return (split.path or "/"), (split.query or None)

    path, _separator, query = target.partition("?")
    # A trailing '?' with nothing after it is not a query string.
    return path, (query or None)


def _optional(value: Optional[str]) -> Optional[str]:
    """Nginx writes ``-`` for a field it had no value for."""
    if value is None or value == "-" or value == "":
        return None
    return value


def _optional_int(value: Optional[str]) -> Optional[int]:
    if value is None or value == "-":
        return None
    try:
        return int(value)
    except ValueError:  # pragma: no cover - the pattern only matches digits
        return None
