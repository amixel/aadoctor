"""Builders for incident records, so a test reads as the situation it describes.

Every helper produces the shape SPEC-006 actually writes and SPEC-005 actually
publishes. Nothing here is a simplified stand-in: a rule that only works
against a convenient fixture is not a rule that works.
"""

from __future__ import annotations

from typing import Dict, Iterable, List, Optional, Tuple

import _support  # noqa: F401  (puts src/ on sys.path)


def entry(key: str, count: int, total: int) -> dict:
    return {"key": key, "count": count, "share": round(count / total, 4) if total else 0.0}


def ranked(pairs: Iterable[Tuple[str, int]], total: int) -> List[dict]:
    ordered = sorted(pairs, key=lambda item: (-item[1], item[0]))
    return [entry(key, count, total) for key, count in ordered]


def site(
    name: str,
    requests: int,
    total_requests: int,
    paths: Optional[Iterable[Tuple[str, int]]] = None,
    ips: Optional[Iterable[Tuple[str, int]]] = None,
    statuses: Optional[Dict[str, int]] = None,
    error_kinds: Optional[Dict[str, int]] = None,
    unparsed: int = 0,
) -> dict:
    kinds = dict(error_kinds or {})
    return {
        "name": name,
        "requests": requests,
        "share": round(requests / total_requests, 4) if total_requests else 0.0,
        "errors": sum(kinds.values()),
        "unparsed": unparsed,
        "paths": ranked(paths or [], requests),
        "ips": ranked(ips or [], requests),
        "statuses": dict(statuses or {}),
        "error_kinds": kinds,
    }


def window(
    seconds: int = 300,
    coverage: Optional[float] = None,
    total_requests: int = 0,
    per_site: Optional[List[dict]] = None,
    statuses: Optional[Dict[str, int]] = None,
    error_kinds: Optional[Dict[str, int]] = None,
    ips: Optional[Iterable[Tuple[str, int]]] = None,
    paths: Optional[Iterable[Tuple[str, int]]] = None,
    access_unparsed: int = 0,
    error_unparsed: int = 0,
    other: Optional[Dict[str, int]] = None,
) -> dict:
    sites = list(per_site or [])
    kinds = dict(error_kinds or {})
    error_sites: Dict[str, int] = {}
    for record in sites:
        errors = sum(record["error_kinds"].values())
        if errors:
            error_sites[record["name"]] = errors

    return {
        "window_seconds": seconds,
        "coverage_seconds": float(seconds if coverage is None else coverage),
        "buckets": max(1, seconds // 10),
        "updated_at": 1_758_000_000.0,
        "total_requests": total_requests,
        "total_errors": sum(kinds.values()),
        "requests_per_second": round(total_requests / seconds, 2) if seconds else 0.0,
        "access_unparsed": access_unparsed,
        "error_unparsed": error_unparsed,
        "unparsed_ratio": round(
            access_unparsed / float(total_requests + access_unparsed), 4
        )
        if (total_requests + access_unparsed)
        else 0.0,
        "sites": ranked(
            [(record["name"], record["requests"]) for record in sites], total_requests
        ),
        "ips": ranked(ips or [], total_requests),
        "paths": ranked(paths or [], total_requests),
        "statuses": dict(statuses or {}),
        "status_classes": {},
        "methods": {},
        "error_kinds": kinds,
        "error_levels": {},
        "error_sites": error_sites,
        "other": dict(other or {}),
        "per_site": sites,
    }


def incident(
    peak: Optional[Dict[str, dict]] = None,
    start: Optional[Dict[str, dict]] = None,
    incident_id: str = "2026-09-22T18-31-40",
    status: str = "closed",
    severity: str = "critical",
    cpu_count: int = 4,
    peak_load: float = 11.6,
) -> dict:
    per_cpu = peak_load / float(cpu_count or 1)
    return {
        "schema_version": 1,
        "id": incident_id,
        "status": status,
        "severity": severity,
        "started_at": "2026-09-22T18:31:40-03:00",
        "peak_at": "2026-09-22T18:33:10-03:00",
        "ended_at": "2026-09-22T18:36:00-03:00" if status == "closed" else None,
        "duration_seconds": 260.0,
        "system": {
            "cpu_count": cpu_count,
            "start": {
                "load1": peak_load * 0.62,
                "load5": peak_load * 0.4,
                "load15": peak_load * 0.3,
                "load_per_cpu": round(per_cpu * 0.62, 2),
            },
            "peak": {
                "load1": peak_load,
                "load5": peak_load * 0.7,
                "load15": peak_load * 0.45,
                "load_per_cpu": round(per_cpu, 2),
            },
        },
        "traffic": {"start": start, "peak": peak},
    }


def one_window(record: dict) -> Dict[str, dict]:
    """A snapshot holding a single window, as the shortest path to a fixture."""
    return {str(record["window_seconds"]): record}


# --- the scenarios the specification names --------------------------------


def dominant_site_scenario(coverage: Optional[float] = None, **kwargs) -> dict:
    """SPEC-007 section 99: one site, one path, one address, and failures.

    8,000 of 9,500 requests on site-a; 5,000 of those on /wp-cron.php; 4,620
    from one address; 120 responses of 502 and 40 upstream timeouts.
    """
    total = 9_500
    sites = [
        site(
            "site-a.com.br",
            8_000,
            total,
            paths=[("/wp-cron.php", 5_000), ("/", 2_000), ("/produto/123", 1_000)],
            ips=[("185.1.2.3", 4_620), ("10.0.0.9", 2_380), ("10.0.0.10", 1_000)],
            statuses={"200": 7_880, "502": 120},
            error_kinds={"upstream_timeout": 40},
        ),
        site("site-b.com.br", 900, total, paths=[("/", 900)], ips=[("10.0.0.1", 900)],
             statuses={"200": 900}),
        site("site-c.com.br", 600, total, paths=[("/", 600)], ips=[("10.0.0.2", 600)],
             statuses={"200": 600}),
    ]
    long_window = window(
        300,
        coverage=coverage,
        total_requests=total,
        per_site=sites,
        statuses={"200": 9_380, "502": 120},
        error_kinds={"upstream_timeout": 40},
        ips=[("185.1.2.3", 4_620), ("10.0.0.9", 2_380)],
        paths=[("/wp-cron.php", 5_000), ("/", 3_500)],
        **kwargs,
    )
    short_window = window(
        60,
        coverage=min(60.0, coverage) if coverage is not None else 60.0,
        total_requests=5_000,
        per_site=[site("site-a.com.br", 4_500, 5_000, paths=[("/wp-cron.php", 3_000)])],
        statuses={"200": 5_000},
    )
    return incident(peak={"60": short_window, "300": long_window})


def distributed_scenario() -> dict:
    """SPEC-007 section 100: ten sites, no concentration, nothing broken."""
    total = 9_500
    per_site = []
    for index in range(10):
        name = f"site-{index}.com.br"
        requests = 950
        per_site.append(
            site(
                name,
                requests,
                total,
                paths=[("/", 300), ("/produto", 250), ("/sobre", 200), ("/api", 200)],
                ips=[(f"10.0.{index}.{n}", 190) for n in range(1, 6)],
                statuses={"200": 950},
            )
        )
    return incident(peak=one_window(window(300, total_requests=total, per_site=per_site,
                                           statuses={"200": total})))


def error_concentration_scenario() -> dict:
    """SPEC-007 section 101: site-b is a third of the traffic and is failing."""
    total = 9_500
    per_site = [
        site("site-a.com.br", 3_200, total, paths=[("/", 1_000), ("/produto", 900)],
             ips=[("10.0.0.1", 800), ("10.0.0.2", 700)], statuses={"200": 3_200}),
        site("site-b.com.br", 2_850, total, paths=[("/", 900), ("/checkout", 800)],
             ips=[("10.0.1.1", 700), ("10.0.1.2", 600)],
             statuses={"200": 2_600, "502": 150, "504": 100},
             error_kinds={"upstream_timeout": 300}),
        site("site-c.com.br", 3_450, total, paths=[("/", 1_200), ("/blog", 900)],
             ips=[("10.0.2.1", 900), ("10.0.2.2", 800)], statuses={"200": 3_450}),
    ]
    return incident(
        peak=one_window(
            window(
                300,
                total_requests=total,
                per_site=per_site,
                statuses={"200": 9_250, "502": 150, "504": 100},
                error_kinds={"upstream_timeout": 300},
            )
        )
    )
