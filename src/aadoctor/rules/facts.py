"""Everything the rules are allowed to look at, and nothing else.

The rules receive this, never the incident file and never a log. Two reasons:
an old incident stays diagnosable long after its logs have rotated away, and a
rule that cannot reach the filesystem cannot become non-deterministic by
accident.

The incident JSON is untrusted input here. It was written by an older build,
or half-written by a crashed one, or edited by hand; every field is read
defensively and a missing one becomes an absence rather than an exception.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from ..analyzers.traffic import UNKNOWN

#: The snapshot a diagnosis prefers. The peak is when the server was pressed
#: hardest; the opening often catches the cause just as it starts (SPEC-006).
PEAK = "peak"
START = "start"

#: Below this much previous traffic, a within-window rate comparison says more
#: about when the daemon started than about the traffic.
MIN_BASELINE_SECONDS = 60.0


def _dict(value: Any) -> Dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> List[Any]:
    return value if isinstance(value, list) else []


def _int(value: Any, default: int = 0) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return default
    return int(value)


def _float(value: Any, default: float = 0.0) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return default
    return float(value)


def _counts(value: Any) -> Dict[str, int]:
    """A ``{name: count}`` table, with anything unusable dropped."""
    result: Dict[str, int] = {}
    for key, count in _dict(value).items():
        number = _int(count)
        if number:
            result[str(key)] = number
    return result


@dataclass
class Entry:
    """One row of a top-N list."""

    key: str
    count: int
    share: float

    @classmethod
    def from_dict(cls, data: Any) -> Optional["Entry"]:
        record = _dict(data)
        key = record.get("key")
        if not isinstance(key, str) or not key:
            return None
        return cls(key=key, count=_int(record.get("count")), share=_float(record.get("share")))


def _entries(value: Any) -> List[Entry]:
    found = [Entry.from_dict(item) for item in _list(value)]
    return [entry for entry in found if entry is not None]


def _named(entries: List[Entry]) -> Optional[Entry]:
    """The heaviest entry that actually names something.

    ``-`` means the line did not carry the field. Reporting it as the
    responsible site or address would be naming the gap in the data.
    """
    for entry in entries:
        if entry.key != UNKNOWN:
            return entry
    return None


@dataclass
class SiteFacts:
    """One site's slice of the window."""

    name: str
    requests: int = 0
    share: float = 0.0
    errors: int = 0
    unparsed: int = 0
    paths: List[Entry] = field(default_factory=list)
    ips: List[Entry] = field(default_factory=list)
    statuses: Dict[str, int] = field(default_factory=dict)
    error_kinds: Dict[str, int] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: Any) -> Optional["SiteFacts"]:
        record = _dict(data)
        name = record.get("name")
        if not isinstance(name, str) or not name:
            return None
        return cls(
            name=name,
            requests=_int(record.get("requests")),
            share=_float(record.get("share")),
            errors=_int(record.get("errors")),
            unparsed=_int(record.get("unparsed")),
            paths=_entries(record.get("paths")),
            ips=_entries(record.get("ips")),
            statuses=_counts(record.get("statuses")),
            error_kinds=_counts(record.get("error_kinds")),
        )

    def status_class(self, first: str) -> int:
        return sum(
            count for code, count in self.statuses.items() if code[:1] == first
        )

    def kinds(self, names: Tuple[str, ...]) -> int:
        return sum(self.error_kinds.get(name, 0) for name in names)


@dataclass
class Facts:
    """One incident, flattened into what the rules need."""

    incident_id: str = ""
    status: str = ""
    severity: str = ""
    started_at: str = ""
    peak_at: Optional[str] = None
    ended_at: Optional[str] = None
    duration_seconds: Optional[float] = None

    cpu_count: int = 1
    cpus_assumed: bool = False
    peak_load: float = 0.0
    peak_load_per_cpu: float = 0.0

    #: Which snapshot these numbers came from, and which window of it.
    source: str = ""
    window_seconds: int = 0
    coverage_seconds: float = 0.0

    total_requests: int = 0
    total_errors: int = 0
    access_unparsed: int = 0
    error_unparsed: int = 0

    sites: List[Entry] = field(default_factory=list)
    ips: List[Entry] = field(default_factory=list)
    paths: List[Entry] = field(default_factory=list)
    user_agents: List[Entry] = field(default_factory=list)

    statuses: Dict[str, int] = field(default_factory=dict)
    error_kinds: Dict[str, int] = field(default_factory=dict)
    error_sites: Dict[str, int] = field(default_factory=dict)
    other: Dict[str, int] = field(default_factory=dict)

    per_site: List[SiteFacts] = field(default_factory=list)

    #: The within-window rate comparison TRAFFIC_SPIKE needs. Both halves come
    #: from the same frozen snapshot, so no history is involved and nothing is
    #: read twice.
    recent_requests: int = 0
    recent_seconds: float = 0.0
    earlier_requests: int = 0
    earlier_seconds: float = 0.0

    # -- derived -----------------------------------------------------------

    @property
    def has_traffic(self) -> bool:
        return bool(self.source)

    @property
    def effective_seconds(self) -> float:
        """Seconds the window really covers, never zero, for rate maths."""
        covered = min(self.coverage_seconds, float(self.window_seconds or 0))
        if covered > 0:
            return covered
        return float(self.window_seconds) if self.window_seconds else 1.0

    @property
    def coverage_ratio(self) -> float:
        if self.window_seconds <= 0:
            return 0.0
        return max(0.0, min(1.0, self.coverage_seconds / float(self.window_seconds)))

    @property
    def requests_per_second(self) -> float:
        return self.total_requests / self.effective_seconds

    @property
    def traffic_ratio(self) -> Optional[float]:
        """Recent request rate over the preceding period's, or None.

        The same comparison TRAFFIC_SPIKE fires on, exposed separately because
        the *absence* of a rise is worth reporting on its own: a load that
        multiplied while the request rate did not is a load the request count
        does not account for.
        """
        if self.earlier_seconds <= 0 or self.recent_seconds <= 0:
            return None

        earlier = self.earlier_requests / self.earlier_seconds
        if earlier <= 0:
            return None
        return (self.recent_requests / self.recent_seconds) / earlier

    @property
    def access_unparsed_ratio(self) -> float:
        seen = self.total_requests + self.access_unparsed
        return (self.access_unparsed / float(seen)) if seen else 0.0

    @property
    def error_unparsed_ratio(self) -> float:
        seen = self.total_errors + self.error_unparsed
        return (self.error_unparsed / float(seen)) if seen else 0.0

    @property
    def has_error_log_data(self) -> bool:
        """Whether the error log contributed anything to this window.

        Distinguishes "no errors happened" from "the error log was unreadable
        or empty", which are different answers and must not be merged.
        """
        return bool(self.total_errors or self.error_unparsed or self.error_kinds)

    def status_class(self, first: str) -> int:
        return sum(count for code, count in self.statuses.items() if code[:1] == first)

    def status_codes(self, first: str) -> Dict[str, int]:
        return {
            code: count
            for code, count in sorted(self.statuses.items())
            if code[:1] == first
        }

    def kinds(self, names: Tuple[str, ...]) -> int:
        return sum(self.error_kinds.get(name, 0) for name in names)

    def site(self, name: str) -> Optional[SiteFacts]:
        for entry in self.per_site:
            if entry.name == name:
                return entry
        return None

    def busiest_site(self, minimum: int = 0) -> Optional[SiteFacts]:
        """The site carrying the most requests, or None.

        Path and address concentration are read inside this site. A dominant
        endpoint on a site with a few hundred requests is not what made the
        server slow, and hunting for the strongest share anywhere would let a
        small quiet site outrank the one actually generating the load.
        """
        candidates = [
            site
            for site in self.per_site
            if site.name != UNKNOWN and site.requests >= minimum and site.requests > 0
        ]
        if not candidates:
            return None
        candidates.sort(key=lambda site: (-site.requests, site.name))
        return candidates[0]

    def truncated(self) -> Dict[str, int]:
        """Volume the cardinality caps displaced, per dimension (SPEC-005)."""
        return {name: count for name, count in self.other.items() if count}

    def sites_by_kinds(self, names: Tuple[str, ...]) -> Dict[str, int]:
        """Which sites the named error kinds landed on."""
        found: Dict[str, int] = {}
        for site in self.per_site:
            count = site.kinds(names)
            if count:
                found[site.name] = count
        return found

    def sites_by_status_class(self, first: str) -> Dict[str, int]:
        found: Dict[str, int] = {}
        for site in self.per_site:
            count = site.status_class(first)
            if count:
                found[site.name] = count
        return found

    # -- building ----------------------------------------------------------

    @classmethod
    def from_incident(cls, incident: Any) -> "Facts":
        """Flatten an incident - the object or the raw record - into facts."""
        record = incident.as_dict() if hasattr(incident, "as_dict") else _dict(incident)

        system = _dict(record.get("system"))
        peak = _dict(system.get("peak"))

        facts = cls(
            incident_id=str(record.get("id") or ""),
            status=str(record.get("status") or ""),
            severity=str(record.get("severity") or ""),
            started_at=str(record.get("started_at") or ""),
            peak_at=record.get("peak_at"),
            ended_at=record.get("ended_at"),
            duration_seconds=(
                _float(record.get("duration_seconds"))
                if record.get("duration_seconds") is not None
                else None
            ),
            cpu_count=max(1, _int(system.get("cpu_count"), 1)),
            cpus_assumed=bool(system.get("cpus_assumed")),
            peak_load=_float(peak.get("load1")),
            peak_load_per_cpu=_float(peak.get("load_per_cpu")),
        )

        traffic = _dict(record.get("traffic"))
        source, windows = _best_snapshot(traffic)
        if not windows:
            return facts

        chosen = _best_window(windows)
        if chosen is None:
            return facts

        facts.source = source
        _fill_window(facts, windows[chosen])
        _fill_baseline(facts, windows)
        return facts


def _best_snapshot(traffic: Dict[str, Any]) -> Tuple[str, Dict[int, Dict[str, Any]]]:
    """Prefer the peak, fall back to the opening, else nothing."""
    for name in (PEAK, START):
        windows = _windows(traffic.get(name))
        if windows:
            return name, windows
    return "", {}


def _windows(value: Any) -> Dict[int, Dict[str, Any]]:
    found: Dict[int, Dict[str, Any]] = {}
    for key, window in _dict(value).items():
        if not str(key).isdigit():
            continue
        record = _dict(window)
        if record:
            found[int(key)] = record
    return found


def _best_window(windows: Dict[int, Dict[str, Any]]) -> Optional[int]:
    """The window with the most of itself actually filled.

    A 300-second window holding thirteen seconds of data is weaker evidence
    than a 60-second window holding sixty, even though it is longer. Ties go
    to the longer window, which sees more.
    """
    best: Optional[Tuple[float, int]] = None
    chosen: Optional[int] = None

    for seconds, window in windows.items():
        length = _int(window.get("window_seconds"), seconds) or seconds
        covered = _float(window.get("coverage_seconds"), float(length))
        ratio = max(0.0, min(1.0, covered / float(length))) if length else 0.0

        # Rounded, so two windows that are both effectively full compare equal
        # and the tie-break picks the longer one rather than a rounding artefact.
        key = (round(ratio, 3), seconds)
        if best is None or key > best:
            best = key
            chosen = seconds

    return chosen


def _fill_window(facts: Facts, window: Dict[str, Any]) -> None:
    facts.window_seconds = _int(window.get("window_seconds"))
    facts.coverage_seconds = _float(
        window.get("coverage_seconds"), float(facts.window_seconds)
    )
    facts.total_requests = _int(window.get("total_requests"))
    facts.total_errors = _int(window.get("total_errors"))
    facts.access_unparsed = _int(window.get("access_unparsed"))
    facts.error_unparsed = _int(window.get("error_unparsed"))

    facts.sites = _entries(window.get("sites"))
    facts.ips = _entries(window.get("ips"))
    facts.paths = _entries(window.get("paths"))
    facts.user_agents = _entries(window.get("user_agents"))

    facts.statuses = _counts(window.get("statuses"))
    facts.error_kinds = _counts(window.get("error_kinds"))
    facts.error_sites = _counts(window.get("error_sites"))
    facts.other = _counts(window.get("other"))

    sites = [SiteFacts.from_dict(item) for item in _list(window.get("per_site"))]
    facts.per_site = [site for site in sites if site is not None]


def _fill_baseline(facts: Facts, windows: Dict[int, Dict[str, Any]]) -> None:
    """Split the longest window into "just now" and "before that".

    The incident freezes a short window and a long one at the same instant, so
    the long one contains the short one. Subtracting gives a genuine preceding
    period without keeping any history - but only a *rate* comparison is fair,
    since sixty seconds would otherwise be measured against up to four minutes.
    """
    if len(windows) < 2:
        return

    shortest = min(windows)
    longest = max(windows)
    recent, earlier = windows[shortest], windows[longest]

    recent_seconds = min(
        _float(recent.get("coverage_seconds"), float(shortest)), float(shortest)
    )
    earlier_seconds = (
        min(_float(earlier.get("coverage_seconds"), float(longest)), float(longest))
        - recent_seconds
    )
    earlier_requests = _int(earlier.get("total_requests")) - _int(
        recent.get("total_requests")
    )

    # A negative remainder means the two windows disagree - an older schema, or
    # a hand-edited file. There is nothing to salvage, so nothing is claimed.
    if earlier_seconds <= 0 or earlier_requests < 0:
        return

    facts.recent_requests = _int(recent.get("total_requests"))
    facts.recent_seconds = recent_seconds
    facts.earlier_requests = earlier_requests
    facts.earlier_seconds = earlier_seconds


__all__ = ["Entry", "Facts", "MIN_BASELINE_SECONDS", "PEAK", "START", "SiteFacts"]
