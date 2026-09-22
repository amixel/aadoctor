"""Moving windows over recent traffic. Counting, not concluding.

Everything here answers "what happened in the last N seconds": which site,
which IP, which path, which status, which error. Deciding that one of those
*caused* something is SPEC-006 and SPEC-007, and none of that reasoning lives
in this module.

Two properties matter more than any feature:

**Memory depends on the number of keys, not on the number of requests.** Time
is cut into fixed buckets; a bucket that leaves the window is dropped whole.
Within a bucket, every dimension is capped.

**A flood cannot hide the thing that matters.** Capping by "first N keys seen"
would let an attacker fill the table with noise and push the dominant path out
of it. Keys are kept by weight instead - see :class:`BoundedCounter`.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Callable, Dict, Iterable, List, Optional, Tuple

from ..parsers.nginx_access import ACCESS, AccessEvent
from ..parsers.nginx_error import ERROR, ErrorEvent

#: Time resolution. Ten seconds gives six buckets for a one-minute window and
#: thirty for five minutes, which is enough to compare recent rates later.
BUCKET_SECONDS = 10

#: The longest window a snapshot may ask for; it sets how many buckets live.
MAX_WINDOW_SECONDS = 300

# Cardinality caps, from SPEC-005. Unvalidated starting points.
MAX_PATHS = 1000
MAX_IPS = 1000
MAX_USER_AGENTS = 200
MAX_SITES = 500
MAX_SMALL = 100          # statuses, methods, error kinds, levels
MAX_SITE_PATHS = 200
MAX_SITE_IPS = 200

# Key length caps. A request target can be several kilobytes; it must not
# become a dictionary key at that size.
MAX_PATH_CHARS = 512
MAX_AGENT_CHARS = 200
MAX_KEY_CHARS = 256

#: Stands in for a field the line did not carry.
UNKNOWN = "-"

#: Marks a key that was cut short, so a truncated path is never mistaken for a
#: real one.
TRUNCATED = "...(truncated)"


def _key(value: Optional[str], limit: int = MAX_KEY_CHARS) -> str:
    """Make a bounded, deterministic dictionary key out of untrusted text."""
    if not value:
        return UNKNOWN
    if len(value) > limit:
        return value[:limit] + TRUNCATED
    return value


class BoundedCounter:
    """Counts keys, keeping the heaviest ones when there are too many.

    The table is allowed to grow to twice its limit, then is cut back to the
    heaviest ``limit`` keys; everything dropped is added to ``other``. That
    keeps admission O(1) and the cut amortised, which matters because the
    moment cardinality explodes is exactly the moment the server is in
    trouble - a per-admission scan would make aaDoctor part of the problem.

    A key that is dropped loses the count it had gathered. A key that is
    genuinely heavy survives every cut, which is the property the diagnosis
    depends on.
    """

    __slots__ = ("limit", "key_limit", "counts", "other", "_ceiling")

    def __init__(self, limit: int, key_limit: int = MAX_KEY_CHARS) -> None:
        self.limit = max(1, limit)
        self.key_limit = key_limit
        self.counts: Dict[str, int] = {}
        self.other = 0
        self._ceiling = self.limit * 2

    def add(self, value: Optional[str], count: int = 1) -> None:
        key = _key(value, self.key_limit)
        current = self.counts.get(key)
        if current is not None:
            self.counts[key] = current + count
            return

        if len(self.counts) >= self._ceiling:
            self._cut()
        self.counts[key] = count

    def _cut(self) -> None:
        ordered = sorted(self.counts.items(), key=lambda item: (-item[1], item[0]))
        for _dropped, value in ordered[self.limit :]:
            self.other += value
        self.counts = dict(ordered[: self.limit])

    @property
    def total(self) -> int:
        return sum(self.counts.values()) + self.other

    def __len__(self) -> int:
        return len(self.counts)


def merge(counters: Iterable[BoundedCounter], limit: int) -> Tuple[Dict[str, int], int]:
    """Sum several buckets' counters into one table plus its displaced total."""
    totals: Dict[str, int] = {}
    other = 0

    for counter in counters:
        other += counter.other
        for key, value in counter.counts.items():
            totals[key] = totals.get(key, 0) + value

    if len(totals) > limit:
        ordered = sorted(totals.items(), key=lambda item: (-item[1], item[0]))
        for _key_, value in ordered[limit:]:
            other += value
        totals = dict(ordered[:limit])

    return totals, other


class SiteBucket:
    """One site's share of one bucket. Created only when the site has traffic."""

    __slots__ = ("requests", "unparsed", "errors", "paths", "ips", "statuses", "kinds")

    def __init__(self) -> None:
        self.requests = 0
        self.unparsed = 0
        self.errors = 0
        self.paths = BoundedCounter(MAX_SITE_PATHS, MAX_PATH_CHARS)
        self.ips = BoundedCounter(MAX_SITE_IPS)
        self.statuses = BoundedCounter(MAX_SMALL)
        self.kinds = BoundedCounter(MAX_SMALL)


class Bucket:
    """Everything that happened in one slice of time."""

    __slots__ = (
        "index",
        "requests",
        "errors",
        "access_unparsed",
        "error_unparsed",
        "sites",
        "ips",
        "paths",
        "user_agents",
        "statuses",
        "methods",
        "error_sites",
        "error_kinds",
        "error_levels",
        "per_site",
    )

    def __init__(self, index: int) -> None:
        self.index = index
        self.requests = 0
        self.errors = 0
        self.access_unparsed = 0
        self.error_unparsed = 0

        self.sites = BoundedCounter(MAX_SITES)
        self.ips = BoundedCounter(MAX_IPS)
        self.paths = BoundedCounter(MAX_PATHS, MAX_PATH_CHARS)
        self.user_agents = BoundedCounter(MAX_USER_AGENTS, MAX_AGENT_CHARS)
        self.statuses = BoundedCounter(MAX_SMALL)
        self.methods = BoundedCounter(MAX_SMALL)

        self.error_sites = BoundedCounter(MAX_SITES)
        self.error_kinds = BoundedCounter(MAX_SMALL)
        self.error_levels = BoundedCounter(MAX_SMALL)

        self.per_site: Dict[str, SiteBucket] = {}

    def site(self, name: str) -> SiteBucket:
        bucket = self.per_site.get(name)
        if bucket is None:
            if len(self.per_site) >= MAX_SITES:
                # Sites come from discovery and are bounded in practice; this
                # is a guard, not an expected path.
                return SiteBucket()
            bucket = SiteBucket()
            self.per_site[name] = bucket
        return bucket


# --- what a snapshot looks like ------------------------------------------


@dataclass
class Entry:
    """One row of a top-N list."""

    key: str
    count: int
    share: float

    def as_dict(self) -> dict:
        return {"key": self.key, "count": self.count, "share": round(self.share, 4)}


@dataclass
class SiteTraffic:
    """One site's slice of a window."""

    name: str
    requests: int
    share: float
    errors: int
    unparsed: int
    paths: List[Entry] = field(default_factory=list)
    ips: List[Entry] = field(default_factory=list)
    statuses: Dict[str, int] = field(default_factory=dict)
    error_kinds: Dict[str, int] = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {
            "name": self.name,
            "requests": self.requests,
            "share": round(self.share, 4),
            "errors": self.errors,
            "unparsed": self.unparsed,
            "paths": [entry.as_dict() for entry in self.paths],
            "ips": [entry.as_dict() for entry in self.ips],
            "statuses": self.statuses,
            "error_kinds": self.error_kinds,
        }


@dataclass
class TrafficSnapshot:
    """What a window holds, copied out so the caller cannot disturb it."""

    window_seconds: int
    updated_at: float
    buckets: int
    #: Seconds of data the window actually covers. Lower than window_seconds
    #: when the daemon has not been running that long.
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
    methods: Dict[str, int] = field(default_factory=dict)
    error_kinds: Dict[str, int] = field(default_factory=dict)
    error_levels: Dict[str, int] = field(default_factory=dict)
    error_sites: Dict[str, int] = field(default_factory=dict)

    #: Volume displaced by the cardinality caps, per dimension. Reported so a
    #: truncated tail is visible rather than silently missing.
    other: Dict[str, int] = field(default_factory=dict)

    per_site: List[SiteTraffic] = field(default_factory=list)

    @property
    def requests_per_second(self) -> float:
        if self.window_seconds <= 0:
            return 0.0
        return self.total_requests / float(self.window_seconds)

    @property
    def status_classes(self) -> Dict[str, int]:
        classes: Dict[str, int] = {}
        for status, count in self.statuses.items():
            label = f"{status[0]}xx" if status and status[0].isdigit() else UNKNOWN
            classes[label] = classes.get(label, 0) + count
        return dict(sorted(classes.items()))

    @property
    def unparsed_ratio(self) -> float:
        """Share of access lines that no supported format matched.

        A high ratio means the numbers above describe only part of the traffic,
        which the reader has to be told (SPEC-004 does not adapt silently).
        """
        seen = self.total_requests + self.access_unparsed
        if seen <= 0:
            return 0.0
        return self.access_unparsed / float(seen)

    def site(self, name: str) -> Optional[SiteTraffic]:
        for entry in self.per_site:
            if entry.name == name:
                return entry
        return None

    def as_dict(self) -> dict:
        return {
            "window_seconds": self.window_seconds,
            "updated_at": self.updated_at,
            "buckets": self.buckets,
            "coverage_seconds": self.coverage_seconds,
            "total_requests": self.total_requests,
            "total_errors": self.total_errors,
            "requests_per_second": round(self.requests_per_second, 2),
            "access_unparsed": self.access_unparsed,
            "error_unparsed": self.error_unparsed,
            # Kept so SPEC-007 can see that the traffic figures describe only
            # part of the traffic before it puts confidence on them.
            "unparsed_ratio": round(self.unparsed_ratio, 4),
            "sites": [entry.as_dict() for entry in self.sites],
            "ips": [entry.as_dict() for entry in self.ips],
            "paths": [entry.as_dict() for entry in self.paths],
            "statuses": self.statuses,
            "status_classes": self.status_classes,
            "methods": self.methods,
            "error_kinds": self.error_kinds,
            "error_levels": self.error_levels,
            "error_sites": self.error_sites,
            "other": self.other,
            "per_site": [entry.as_dict() for entry in self.per_site],
        }


# --- the aggregator -------------------------------------------------------


class TrafficAggregator:
    """Recent traffic, in bounded moving windows.

    Buckets are indexed by *arrival* time, not by the timestamp on the line.
    Access stamps are timezone-aware and error stamps are not (SPEC-004), and
    bucketing by them would mean inventing a timezone for the server. Logs
    arrive within seconds of being written, so arrival time answers "what is
    happening now" just as well - and it cannot be moved by a bad clock in a
    log line.
    """

    def __init__(
        self,
        bucket_seconds: int = BUCKET_SECONDS,
        max_window_seconds: int = MAX_WINDOW_SECONDS,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.bucket_seconds = max(1, int(bucket_seconds))
        self.max_window_seconds = max(self.bucket_seconds, int(max_window_seconds))
        self._clock = clock
        self._buckets: Dict[int, Bucket] = {}
        # When counting began, so a window can say how much of itself it
        # actually covers. A snapshot claiming "the last five minutes" forty
        # seconds after startup would be a lie SPEC-007 could not detect.
        self._started = clock()

    # -- taking events in --------------------------------------------------

    def add(self, event) -> None:
        """Take one parsed event. Counters only; the event is not retained."""
        if getattr(event, "log_type", None) == ACCESS:
            self.add_access(event)
        elif getattr(event, "log_type", None) == ERROR:
            self.add_error(event)

    def add_access(self, event: AccessEvent) -> None:
        bucket = self._bucket()
        site = event.site or UNKNOWN

        bucket.requests += 1
        bucket.sites.add(site)
        bucket.ips.add(event.remote_addr)
        # The path, not the request target: /produto?id=1 and /produto?id=2 are
        # one endpoint, and counting the query would make cardinality explode
        # for no diagnostic gain (README §60).
        bucket.paths.add(event.path)
        bucket.methods.add(event.method)
        bucket.user_agents.add(event.user_agent)
        if event.status is not None:
            bucket.statuses.add(str(event.status))

        site_bucket = bucket.site(site)
        site_bucket.requests += 1
        site_bucket.paths.add(event.path)
        site_bucket.ips.add(event.remote_addr)
        if event.status is not None:
            site_bucket.statuses.add(str(event.status))

    def add_error(self, event: ErrorEvent) -> None:
        bucket = self._bucket()
        site = event.site or UNKNOWN

        bucket.errors += 1
        bucket.error_sites.add(site)
        bucket.error_levels.add(event.level)
        # A line the classification table did not recognise still counts; it is
        # just not named. Inventing a kind for it would be worse.
        bucket.error_kinds.add(event.kind or "other")

        site_bucket = bucket.site(site)
        site_bucket.errors += 1
        site_bucket.kinds.add(event.kind or "other")

    def note_unparsed(self, log_type: str, site: str = "") -> None:
        """Record a line no parser matched.

        Kept apart from traffic: an unparsed line is not a request, and must
        never be counted as one. It is what tells the reader the numbers are
        incomplete.
        """
        bucket = self._bucket()
        if log_type == ACCESS:
            bucket.access_unparsed += 1
            bucket.site(site or UNKNOWN).unparsed += 1
        elif log_type == ERROR:
            bucket.error_unparsed += 1

    # -- time --------------------------------------------------------------

    def _bucket(self) -> Bucket:
        index = int(self._clock() // self.bucket_seconds)
        bucket = self._buckets.get(index)
        if bucket is None:
            bucket = Bucket(index)
            self._buckets[index] = bucket
            self.expire()
        return bucket

    def expire(self, now: Optional[float] = None) -> int:
        """Drop buckets that have left the longest window.

        Called when a bucket is created and again on every snapshot, so a
        server that goes quiet empties out instead of showing stale traffic
        forever.
        """
        current = int((self._clock() if now is None else now) // self.bucket_seconds)
        oldest = current - (self.max_window_seconds // self.bucket_seconds)

        expired = [index for index in self._buckets if index < oldest]
        for index in expired:
            del self._buckets[index]
        return len(expired)

    @property
    def bucket_count(self) -> int:
        return len(self._buckets)

    def coverage_seconds(self, window_seconds: int) -> float:
        """How much of the requested window the aggregator has been running for."""
        return max(0.0, min(float(window_seconds), self._clock() - self._started))

    @property
    def key_count(self) -> int:
        """Total tracked keys. Used by tests to prove memory is bounded."""
        total = 0
        for bucket in self._buckets.values():
            total += (
                len(bucket.sites)
                + len(bucket.ips)
                + len(bucket.paths)
                + len(bucket.user_agents)
                + len(bucket.statuses)
                + len(bucket.methods)
                + len(bucket.error_sites)
                + len(bucket.error_kinds)
                + len(bucket.error_levels)
            )
            for site in bucket.per_site.values():
                total += len(site.paths) + len(site.ips) + len(site.statuses) + len(site.kinds)
        return total

    # -- reading out -------------------------------------------------------

    def snapshot(
        self,
        window_seconds: int = MAX_WINDOW_SECONDS,
        top: int = 10,
        sites: int = 10,
    ) -> TrafficSnapshot:
        """Copy out one window. The result shares nothing with the internals.

        Sorting happens here, not on every event: adding a line must stay a
        handful of dictionary increments.
        """
        window = max(self.bucket_seconds, min(int(window_seconds), self.max_window_seconds))
        self.expire()

        current = int(self._clock() // self.bucket_seconds)
        oldest = current - (window // self.bucket_seconds) + 1
        selected = [
            bucket
            for index, bucket in sorted(self._buckets.items())
            if index >= oldest
        ]

        snapshot = TrafficSnapshot(
            window_seconds=window,
            updated_at=time.time(),
            buckets=len(selected),
            coverage_seconds=round(self.coverage_seconds(window), 1),
        )
        if not selected:
            return snapshot

        snapshot.total_requests = sum(bucket.requests for bucket in selected)
        snapshot.total_errors = sum(bucket.errors for bucket in selected)
        snapshot.access_unparsed = sum(bucket.access_unparsed for bucket in selected)
        snapshot.error_unparsed = sum(bucket.error_unparsed for bucket in selected)

        requests = snapshot.total_requests
        errors = snapshot.total_errors
        other: Dict[str, int] = {}

        snapshot.sites = _entries(selected, "sites", MAX_SITES, requests, top, other, "sites")
        snapshot.ips = _entries(selected, "ips", MAX_IPS, requests, top, other, "ips")
        snapshot.paths = _entries(selected, "paths", MAX_PATHS, requests, top, other, "paths")
        snapshot.user_agents = _entries(
            selected, "user_agents", MAX_USER_AGENTS, requests, top, other, "user_agents"
        )

        snapshot.statuses = _totals(selected, "statuses", MAX_SMALL)
        snapshot.methods = _totals(selected, "methods", MAX_SMALL)
        snapshot.error_kinds = _totals(selected, "error_kinds", MAX_SMALL)
        snapshot.error_levels = _totals(selected, "error_levels", MAX_SMALL)
        snapshot.error_sites = _totals(selected, "error_sites", MAX_SITES)
        snapshot.other = {name: value for name, value in other.items() if value}

        snapshot.per_site = _site_traffic(selected, requests, errors, top, sites)
        return snapshot


def _entries(
    buckets: List[Bucket],
    attribute: str,
    limit: int,
    total: int,
    top: int,
    other: Dict[str, int],
    label: str,
) -> List[Entry]:
    merged, displaced = merge((getattr(bucket, attribute) for bucket in buckets), limit)
    other[label] = displaced
    return _rank(merged, total, top)


def _totals(buckets: List[Bucket], attribute: str, limit: int) -> Dict[str, int]:
    merged, _displaced = merge((getattr(bucket, attribute) for bucket in buckets), limit)
    return dict(sorted(merged.items(), key=lambda item: (-item[1], item[0])))


def _rank(counts: Dict[str, int], total: int, top: int) -> List[Entry]:
    """Highest count first, ties broken by key so a run is reproducible."""
    ordered = sorted(counts.items(), key=lambda item: (-item[1], item[0]))
    return [
        Entry(key, count, (count / total) if total else 0.0)
        for key, count in ordered[:top]
    ]


def _site_traffic(
    buckets: List[Bucket],
    total_requests: int,
    total_errors: int,
    top: int,
    limit: int,
) -> List[SiteTraffic]:
    names = set()
    for bucket in buckets:
        names.update(bucket.per_site)

    collected: List[SiteTraffic] = []
    for name in names:
        parts = [bucket.per_site[name] for bucket in buckets if name in bucket.per_site]
        requests = sum(part.requests for part in parts)
        errors = sum(part.errors for part in parts)

        paths, _ = merge((part.paths for part in parts), MAX_SITE_PATHS)
        ips, _ = merge((part.ips for part in parts), MAX_SITE_IPS)
        statuses, _ = merge((part.statuses for part in parts), MAX_SMALL)
        kinds, _ = merge((part.kinds for part in parts), MAX_SMALL)

        collected.append(
            SiteTraffic(
                name=name,
                requests=requests,
                share=(requests / total_requests) if total_requests else 0.0,
                errors=errors,
                unparsed=sum(part.unparsed for part in parts),
                paths=_rank(paths, requests, top),
                ips=_rank(ips, requests, top),
                statuses=dict(sorted(statuses.items(), key=lambda item: (-item[1], item[0]))),
                error_kinds=dict(sorted(kinds.items(), key=lambda item: (-item[1], item[0]))),
            )
        )

    collected.sort(key=lambda site: (-site.requests, -site.errors, site.name))
    return collected[:limit]
