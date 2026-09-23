"""The nine rules. One function each, one entry in the table each.

A rule is a pure function of :class:`~aadoctor.rules.facts.Facts` and the
thresholds. It returns:

``Finding``        the rule fired, with its evidence
``NotEvaluable``   the rule could not be decided, and why
``None``           the rule looked and did not fire

The third and the second are different answers. "No traffic spike" and "no way
to tell whether there was a traffic spike" must never arrive at the reader as
the same silence.

No rule reads another rule's result, and none of them reads a file. Adding a
rule is one function and one line in :data:`RULES`.
"""

from __future__ import annotations

from typing import Dict, Optional, Tuple, Union

from ..analyzers.traffic import UNKNOWN
from . import (
    CAP_MEDIUM,
    CONFIDENCE_AT_THRESHOLD,
    Finding,
    NotEvaluable,
    count_confidence,
    share_confidence,
)
from .facts import MIN_BASELINE_SECONDS, Facts

Result = Optional[Union[Finding, NotEvaluable]]

TRAFFIC_SPIKE = "TRAFFIC_SPIKE"
ONE_SITE_DOMINATING = "ONE_SITE_DOMINATING"
ONE_URL_DOMINATING = "ONE_URL_DOMINATING"
ONE_IP_DOMINATING = "ONE_IP_DOMINATING"
NOT_FOUND_FLOOD = "NOT_FOUND_FLOOD"
HTTP_5XX_SPIKE = "HTTP_5XX_SPIKE"
UPSTREAM_TIMEOUT = "UPSTREAM_TIMEOUT"
FASTCGI_ERROR = "FASTCGI_ERROR"
PHP_ERROR_SPIKE = "PHP_ERROR_SPIKE"

#: Error-log classifications each rule aggregates, from SPEC-004's table. A
#: kind names one line; a finding is a conclusion about many of them.
UPSTREAM_TIMEOUT_KINDS = ("upstream_timeout",)
FASTCGI_KINDS = (
    "fastcgi_stderr",
    "connect_failed",
    "recv_failed",
    "upstream_closed",
    "no_live_upstreams",
)
PHP_SEVERE_KINDS = (
    "php_fatal",
    "php_parse_error",
    "php_memory_exhausted",
    "php_execution_timeout",
)
PHP_MINOR_KINDS = ("php_warning", "php_notice")

#: What a warning is worth next to a fatal. A site that logs notices all day
#: would otherwise look like a site that is dying.
PHP_MINOR_WEIGHT = 0.25

#: Share of a dimension's events one site must hold before the finding is
#: attributed to it. Below this the events are spread, and naming one site
#: would be picking a winner out of noise.
ATTRIBUTION_SHARE = 0.50


def _count(value: int) -> str:
    return f"{value:,}"


def _percent(value: float) -> str:
    return f"{value * 100:.1f}%"


def _dominant(counts: Dict[str, int]) -> Optional[str]:
    """The site holding most of these events, when one clearly does."""
    if not counts:
        return None
    total = sum(counts.values())
    if total <= 0:
        return None

    name, count = sorted(counts.items(), key=lambda item: (-item[1], item[0]))[0]
    if name == UNKNOWN:
        return None
    return name if count / float(total) >= ATTRIBUTION_SHARE else None


# --- traffic concentration ------------------------------------------------


def rule_traffic_spike(facts: Facts, t) -> Result:
    """Requests arriving faster now than in the minutes before.

    Compared by **rate**, not volume: the recent window is a minute and the
    period before it can be four, so comparing totals would call every busy
    minute a spike.

    This is not a historical baseline - it is the earlier part of the same
    frozen window. SPEC-007 caps a finding without a real baseline at MEDIUM,
    and this one is capped there by construction.
    """
    if not facts.has_traffic:
        return NotEvaluable(TRAFFIC_SPIKE, "no_traffic_recorded")
    if facts.earlier_seconds < MIN_BASELINE_SECONDS:
        return NotEvaluable(TRAFFIC_SPIKE, "insufficient_previous_window")
    if facts.recent_seconds <= 0:
        return NotEvaluable(TRAFFIC_SPIKE, "insufficient_recent_window")
    if facts.recent_requests < t.min_volume:
        return None

    recent_rps = facts.recent_requests / facts.recent_seconds
    earlier_rps = facts.earlier_requests / facts.earlier_seconds

    evidence = {
        "recent_requests": facts.recent_requests,
        "recent_seconds": round(facts.recent_seconds, 1),
        "recent_per_second": round(recent_rps, 2),
        "earlier_requests": facts.earlier_requests,
        "earlier_seconds": round(facts.earlier_seconds, 1),
        "earlier_per_second": round(earlier_rps, 2),
        "threshold": t.traffic_spike_factor,
        "baseline": "preceding part of the same window, not a historical baseline",
    }

    if earlier_rps <= 0:
        # Traffic where there was none. Real, but a ratio against zero is not a
        # number, so none is invented: the comparison is named instead.
        evidence["spike_ratio"] = None
        evidence["comparison"] = "from_idle"
        return Finding(
            code=TRAFFIC_SPIKE,
            confidence=min(CAP_MEDIUM, CONFIDENCE_AT_THRESHOLD),
            evidence=evidence,
            aspects=("access",),
            caps=["no_previous_traffic_to_compare"],
            description=(
                "%s requests in the last %.0fs, with no traffic before that"
                % (_count(facts.recent_requests), facts.recent_seconds)
            ),
        )

    ratio = recent_rps / earlier_rps
    if ratio < t.traffic_spike_factor:
        return None

    evidence["spike_ratio"] = round(ratio, 2)
    evidence["comparison"] = "within_window"
    confidence = min(CAP_MEDIUM, count_confidence(ratio, t.traffic_spike_factor))

    return Finding(
        code=TRAFFIC_SPIKE,
        confidence=confidence,
        evidence=evidence,
        aspects=("access",),
        caps=["no_historical_baseline"],
        description=(
            "%.1f req/s in the last %.0fs against %.1f req/s before that (%.1fx)"
            % (recent_rps, facts.recent_seconds, earlier_rps, ratio)
        ),
    )


def rule_one_site_dominating(facts: Facts, t) -> Result:
    """One site holding an abnormal share of the server's requests."""
    if not facts.has_traffic:
        return NotEvaluable(ONE_SITE_DOMINATING, "no_traffic_recorded")
    if facts.total_requests < t.min_volume:
        return NotEvaluable(ONE_SITE_DOMINATING, "below_minimum_volume")

    site = facts.busiest_site()
    if site is None:
        return NotEvaluable(ONE_SITE_DOMINATING, "no_site_attribution")

    share = site.requests / float(facts.total_requests)
    if share < t.site_share:
        return None

    runners = [
        other
        for other in facts.per_site
        if other.name != site.name and other.requests > 0
    ]
    runners.sort(key=lambda item: (-item.requests, item.name))

    evidence = {
        "site": site.name,
        "requests": site.requests,
        "total_requests": facts.total_requests,
        "share": round(share, 4),
        "threshold": t.site_share,
    }
    if runners:
        # Contrast, because one busy site among idle ones is a normal server.
        evidence["second_place"] = {
            "site": runners[0].name,
            "requests": runners[0].requests,
            "share": round(runners[0].requests / float(facts.total_requests), 4),
        }

    return Finding(
        code=ONE_SITE_DOMINATING,
        confidence=share_confidence(
            share, t.site_share, t.site_share_ceiling, site.requests, t.min_volume
        ),
        site=site.name,
        evidence=evidence,
        aspects=("access", "sites"),
        description=(
            "%s generated %s of requests (%s of %s)"
            % (
                site.name,
                _percent(share),
                _count(site.requests),
                _count(facts.total_requests),
            )
        ),
    )


def rule_one_url_dominating(facts: Facts, t) -> Result:
    """One path holding an abnormal share of the busiest site's requests.

    The share is against the **site** total, never the server total: a path
    that is 62% of its own site is a statement about that site.
    """
    if not facts.has_traffic:
        return NotEvaluable(ONE_URL_DOMINATING, "no_traffic_recorded")

    site = facts.busiest_site(minimum=t.min_volume)
    if site is None:
        return NotEvaluable(ONE_URL_DOMINATING, "below_minimum_volume")

    path = _top(site.paths)
    if path is None:
        return NotEvaluable(ONE_URL_DOMINATING, "no_path_data")

    share = path.count / float(site.requests)
    if share < t.url_share:
        return None

    return Finding(
        code=ONE_URL_DOMINATING,
        confidence=share_confidence(
            share, t.url_share, t.url_share_ceiling, path.count, t.min_volume
        ),
        site=site.name,
        path=path.key,
        evidence={
            "site": site.name,
            "path": path.key,
            "path_requests": path.count,
            "site_requests": site.requests,
            "share": round(share, 4),
            "threshold": t.url_share,
            "path_per_second": round(path.count / facts.effective_seconds, 2),
            # Counted without the query string (SPEC-005), so two calls to the
            # same endpoint with different arguments are one endpoint here.
            "normalized": "path only, query string not counted",
        },
        aspects=("access", "paths"),
        description=(
            "%s generated %s of %s's requests (%s of %s)"
            % (
                path.key,
                _percent(share),
                site.name,
                _count(path.count),
                _count(site.requests),
            )
        ),
    )


def rule_one_ip_dominating(facts: Facts, t) -> Result:
    """One address holding an abnormal share of the busiest site's requests.

    This is concentration, not an accusation. A CDN or reverse proxy in front
    of the site makes every request appear to come from one address, NAT
    concentrates many real people behind one, and a paid integration may poll
    from a fixed one. The finding says how much; it never says attack, and it
    never suggests blocking anything.
    """
    if not facts.has_traffic:
        return NotEvaluable(ONE_IP_DOMINATING, "no_traffic_recorded")

    site = facts.busiest_site(minimum=t.min_volume)
    if site is None:
        return NotEvaluable(ONE_IP_DOMINATING, "below_minimum_volume")

    address = _top(site.ips)
    if address is None:
        return NotEvaluable(ONE_IP_DOMINATING, "no_address_data")

    share = address.count / float(site.requests)
    if share < t.ip_share:
        return None

    evidence = {
        "site": site.name,
        "ip": address.key,
        "requests": address.count,
        "site_requests": site.requests,
        "share_of_site": round(share, 4),
        "threshold": t.ip_share,
        "caveat": "may be a CDN, reverse proxy, NAT, integration or crawler",
    }

    # Both proportions when both exist: an address at 58% of one site and 49%
    # of the whole server are two different facts, and each is worth having.
    server_wide = _find(facts.ips, address.key)
    if server_wide is not None and facts.total_requests:
        evidence["server_requests"] = server_wide.count
        evidence["share_of_server"] = round(
            server_wide.count / float(facts.total_requests), 4
        )

    return Finding(
        code=ONE_IP_DOMINATING,
        confidence=share_confidence(
            share, t.ip_share, t.ip_share_ceiling, address.count, t.min_volume
        ),
        site=site.name,
        ip=address.key,
        evidence=evidence,
        aspects=("access", "ips"),
        description=(
            "%s generated %s of %s's requests (%s)"
            % (address.key, _percent(share), site.name, _count(address.count))
        ),
    )


def rule_not_found_flood(facts: Facts, t) -> Result:
    """404s at both an abnormal share and an abnormal rate.

    Both are required. A 404 is usually cheap to serve, so this explains noise
    far more often than it explains load, and a share alone on a quiet server
    would fire constantly.

    The two conditions multiply, which is a trap worth stating: requiring a
    share of S and a rate of R means the *server* has to be doing R/S requests
    a second before the rule can fire at all. The first thresholds shipped -
    0.30 and 5/s - silently needed 16.7 req/s, and the first production server
    it met was doing 2.9. Eighty-two per cent of its requests were 404s during
    a load spike and this stayed quiet. The floor is now set from what a small
    server looks like, not from what a busy one does.
    """
    if not facts.has_traffic:
        return NotEvaluable(NOT_FOUND_FLOOD, "no_traffic_recorded")
    if facts.total_requests < t.min_volume:
        return NotEvaluable(NOT_FOUND_FLOOD, "below_minimum_volume")
    if not facts.statuses:
        return NotEvaluable(NOT_FOUND_FLOOD, "no_status_data")

    count = facts.statuses.get("404", 0)
    share = count / float(facts.total_requests)
    rate = count / facts.effective_seconds

    if share < t.not_found_share or rate < t.not_found_min_rate:
        return None

    # Both conditions hold; the weaker one is what the confidence rests on.
    ratio = min(share / t.not_found_share, rate / t.not_found_min_rate)
    sites = facts.sites_by_status_class("4")

    return Finding(
        code=NOT_FOUND_FLOOD,
        confidence=count_confidence(ratio, 1.0),
        site=_dominant(sites),
        evidence={
            "count": count,
            "total_requests": facts.total_requests,
            "share": round(share, 4),
            "share_threshold": t.not_found_share,
            "per_second": round(rate, 2),
            "rate_threshold": t.not_found_min_rate,
            "sites_4xx": sites,
            # Aggregation counts statuses and paths separately, so which paths
            # the 404s were for is genuinely not in the data (SPEC-005).
            "top_404_paths": "not available: status and path are counted separately",
        },
        aspects=("access",),
        description=(
            "%s responses were 404 (%s of requests, %.1f/s)"
            % (_count(count), _percent(share), rate)
        ),
    )


# --- failures -------------------------------------------------------------


def rule_http_5xx_spike(facts: Facts, t) -> Result:
    """Server errors at an abnormal share or an abnormal rate.

    Either is enough: 300 failures a second matters on a busy server even at a
    low share, and a high share matters on a quiet one even at a low rate. A
    floor on the absolute count keeps three errors from being called a spike.
    """
    if not facts.has_traffic:
        return NotEvaluable(HTTP_5XX_SPIKE, "no_traffic_recorded")
    if not facts.statuses:
        return NotEvaluable(HTTP_5XX_SPIKE, "no_status_data")

    count = facts.status_class("5")
    if count < t.http_5xx_min_count:
        return None

    share = (count / float(facts.total_requests)) if facts.total_requests else 0.0
    rate = count / facts.effective_seconds
    if share < t.http_5xx_share and rate < t.http_5xx_min_rate:
        return None

    ratio = max(share / t.http_5xx_share, rate / t.http_5xx_min_rate)
    sites = facts.sites_by_status_class("5")

    return Finding(
        code=HTTP_5XX_SPIKE,
        confidence=count_confidence(ratio, 1.0),
        site=_dominant(sites),
        evidence={
            "count": count,
            "by_code": facts.status_codes("5"),
            "total_requests": facts.total_requests,
            "share": round(share, 4),
            "share_threshold": t.http_5xx_share,
            "per_second": round(rate, 2),
            "rate_threshold": t.http_5xx_min_rate,
            "min_count": t.http_5xx_min_count,
            "sites": sites,
        },
        aspects=("access",),
        description=(
            "%s responses were 5xx (%s)"
            % (
                _count(count),
                ", ".join(
                    "%s: %s" % (code, _count(value))
                    for code, value in facts.status_codes("5").items()
                )
                or "no breakdown",
            )
        ),
    )


def _error_rule(
    facts: Facts,
    code: str,
    kinds: Tuple[str, ...],
    minimum: int,
    noun: str,
) -> Result:
    """Shared shape for the error-log rules: count, threshold, attribution."""
    if not facts.has_error_log_data:
        return NotEvaluable(code, "no_error_log_data")

    count = facts.kinds(kinds)
    if count < minimum:
        return None

    sites = facts.sites_by_kinds(kinds)
    per_kind = {kind: facts.error_kinds[kind] for kind in kinds if facts.error_kinds.get(kind)}

    return Finding(
        code=code,
        confidence=count_confidence(count, minimum),
        site=_dominant(sites),
        evidence={
            "count": count,
            "threshold": minimum,
            "by_kind": per_kind,
            "sites": sites,
            "per_second": round(count / facts.effective_seconds, 2),
        },
        aspects=("error",),
        description="%s %s" % (_count(count), noun),
    )


def rule_upstream_timeout(facts: Facts, t) -> Result:
    """Nginx waited for PHP-FPM and gave up.

    A timeout is a symptom. It usually accompanies the cause rather than being
    it, which is why it corroborates a site rather than convicting one.
    """
    finding = _error_rule(
        facts,
        UPSTREAM_TIMEOUT,
        UPSTREAM_TIMEOUT_KINDS,
        t.upstream_timeout_min,
        "upstream timeout errors",
    )
    if isinstance(finding, Finding):
        # 504s from the access log corroborate timeouts from the error log.
        # Two independent records of the same event is stronger than either.
        gateway_timeouts = facts.statuses.get("504", 0)
        if gateway_timeouts:
            finding.evidence["correlated_504"] = gateway_timeouts
    return finding


def rule_fastcgi_error(facts: Facts, t) -> Result:
    """The PHP-FPM socket refused, closed early or failed mid-response.

    aaDoctor reports these. It never restarts or reloads PHP-FPM (README
    section 90).
    """
    return _error_rule(
        facts, FASTCGI_ERROR, FASTCGI_KINDS, t.fastcgi_error_min, "FastCGI errors"
    )


def rule_php_error_spike(facts: Facts, t) -> Result:
    """PHP failing in the application itself.

    Weighted, not counted: a fatal, a parse error, an exhausted memory limit
    and a blown execution time each count fully, while warnings and notices
    count a quarter. Without a baseline a chronically chatty site would look
    like a dying one, and this is the cheapest honest defence against that.
    """
    if not facts.has_error_log_data:
        return NotEvaluable(PHP_ERROR_SPIKE, "no_error_log_data")

    severe = facts.kinds(PHP_SEVERE_KINDS)
    minor = facts.kinds(PHP_MINOR_KINDS)
    weighted = severe + minor * PHP_MINOR_WEIGHT

    if weighted < t.php_error_min:
        return None

    kinds = PHP_SEVERE_KINDS + PHP_MINOR_KINDS
    sites = facts.sites_by_kinds(kinds)

    return Finding(
        code=PHP_ERROR_SPIKE,
        confidence=count_confidence(weighted, t.php_error_min),
        site=_dominant(sites),
        evidence={
            "count": severe + minor,
            "severe": severe,
            "minor": minor,
            "weighted": round(weighted, 2),
            "minor_weight": PHP_MINOR_WEIGHT,
            "threshold": t.php_error_min,
            "by_kind": {
                kind: facts.error_kinds[kind]
                for kind in kinds
                if facts.error_kinds.get(kind)
            },
            "sites": sites,
        },
        aspects=("error",),
        description=(
            "%s PHP errors (%s fatal or fatal-class, %s warnings or notices)"
            % (_count(severe + minor), _count(severe), _count(minor))
        ),
    )


# --- helpers --------------------------------------------------------------


def _top(entries):
    """The heaviest entry that names something real."""
    for entry in entries:
        if entry.key != UNKNOWN and entry.count > 0:
            return entry
    return None


def _find(entries, key):
    for entry in entries:
        if entry.key == key:
            return entry
    return None


#: Evaluation order. It does not affect the result - every rule reads only the
#: facts - but it fixes the order things are computed in, which keeps a run
#: reproducible down to the last byte.
RULES = (
    rule_traffic_spike,
    rule_one_site_dominating,
    rule_one_url_dominating,
    rule_one_ip_dominating,
    rule_not_found_flood,
    rule_http_5xx_spike,
    rule_upstream_timeout,
    rule_fastcgi_error,
    rule_php_error_spike,
)

CODES = (
    TRAFFIC_SPIKE,
    ONE_SITE_DOMINATING,
    ONE_URL_DOMINATING,
    ONE_IP_DOMINATING,
    NOT_FOUND_FLOOD,
    HTTP_5XX_SPIKE,
    UPSTREAM_TIMEOUT,
    FASTCGI_ERROR,
    PHP_ERROR_SPIKE,
)
