"""The daemon: discovery, following the logs it finds, and parsing their lines.

It re-reads the aaPanel vhost files on an interval so a new site is noticed
without a restart (SPEC-002), follows each discovered log incrementally
(SPEC-003), parses every complete line it reads (SPEC-004), counts them into
moving windows (SPEC-005), watches the load average and freezes those windows
when the server is under pressure (SPEC-006), persists its offsets, and shuts
down cleanly on SIGTERM and SIGINT.

Parsed events feed bounded moving windows (SPEC-005) and are then dropped, and
the daemon publishes those windows for the CLI to read. What it still does not
do is conclude anything: an incident records what the load was and what the
logs showed. Naming a culprit from that is `aadoctor diagnose` (SPEC-007),
which reads the stored incident on demand and never runs in this loop.
"""

from __future__ import annotations

import logging
import signal
import sys
import threading
import time
from pathlib import Path
from typing import Optional

from . import __version__
from . import parsers, runtime
from .analyzers import TrafficAggregator
from .analyzers import incidents
from .analyzers.incidents import IncidentDetector
from .collectors import LogMonitor, PollStats
from .collectors import load
from .collectors.load import LoadSample
from .collectors import logs as collector
from .collectors.logs import LogEvent
from .config import Config
from .discovery import DiscoveryResult, diff_sites, discover_sites, summarize
from .environment import has_aapanel
from .parsers import ParseStats
from .paths import DISPLAY_NAME, LOG_FILE

#: How often the wait loop wakes up. Only affects shutdown latency.
TICK_SECONDS = 1.0

#: How often expired incidents are swept. Scanning a directory on every poll
#: would be work for nothing; once a day is enough for a retention window
#: measured in days.
CLEANUP_INTERVAL = 24 * 3600

logger = logging.getLogger("aadoctor")


def setup_logging(log_file: Optional[Path] = None, verbose: bool = False) -> None:
    """Log to aaDoctor's own log file, falling back to stderr.

    A developer running the daemon without root must not be forced to create
    /var/log/aadoctor just to see output.
    """
    target = Path(log_file) if log_file is not None else LOG_FILE
    level = logging.DEBUG if verbose else logging.INFO
    formatter = logging.Formatter("%(asctime)s %(levelname)s %(message)s")

    logger.setLevel(level)
    for existing in list(logger.handlers):
        logger.removeHandler(existing)

    handler: logging.Handler
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        handler = logging.FileHandler(str(target), encoding="utf-8")
    except OSError:
        handler = logging.StreamHandler(sys.stderr)
        handler.setFormatter(formatter)
        logger.addHandler(handler)
        logger.warning("cannot write to %s; logging to stderr", target)
        return

    handler.setFormatter(formatter)
    logger.addHandler(handler)


def run(config: Config, log_file: Optional[Path] = None, verbose: bool = False) -> int:
    """Run until a termination signal arrives. Returns the process exit code."""
    setup_logging(log_file=log_file, verbose=verbose)

    stop = threading.Event()

    def _handle(signum: int, _frame: object) -> None:
        logger.info("received signal %s, shutting down", signal.Signals(signum).name)
        stop.set()

    for name in ("SIGTERM", "SIGINT"):
        number = getattr(signal, name, None)
        if number is not None:
            signal.signal(number, _handle)

    logger.info("%s %s daemon started", DISPLAY_NAME, __version__)
    if config.source is None:
        logger.info("no configuration file found; using built-in defaults")
    else:
        logger.info("configuration loaded from %s", config.source)

    enabled = bool(config.get("monitor", "enabled"))
    if not enabled:
        logger.info("monitoring disabled in configuration")
    elif not has_aapanel():
        logger.warning("aaPanel not detected; no discovery will run")
        enabled = False

    # The daemon still concludes nothing. It records what the load was and
    # what the logs showed; reading that evidence is `aadoctor diagnose`,
    # computed on demand so an old incident can be re-read by a later ruleset.
    logger.info("incidents are diagnosed on demand by `aadoctor diagnose`")

    discovery_interval = max(1, int(config.get("discovery", "interval_seconds")))
    monitor_interval = max(1, int(config.get("monitor", "interval_seconds")))

    monitor = LogMonitor()
    aggregator = TrafficAggregator()
    parse_stats = ParseStats()
    detector = _build_detector(config)
    retention_days = max(0, int(config.get("incidents", "retention_days")))

    if detector is not None:
        for incident_id in detector.recover():
            logger.warning("incident %s was left open by a previous run", incident_id)

    previous = DiscoveryResult(vhost_dir=Path("."))
    next_discovery = 0.0
    next_poll = 0.0
    next_cleanup = 0.0
    published = None
    sample = None

    while not stop.is_set():
        now = time.monotonic()

        if enabled and now >= next_discovery:
            previous = run_discovery(monitor, previous)
            next_discovery = now + discovery_interval

        if enabled and now >= next_poll:
            run_poll(monitor, aggregator, parse_stats)
            sample = run_load(detector, aggregator)
            published = publish(aggregator, published, sample, detector)
            next_poll = now + monitor_interval

        if now >= next_cleanup:
            incidents.purge_old(retention_days=retention_days)
            next_cleanup = now + CLEANUP_INTERVAL

        stop.wait(TICK_SECONDS)

    if parse_stats.parsed or parse_stats.unparsed:
        logger.info("parsing totals: %s", parse_stats.summary())

    # Whatever we consumed is recorded before we go, so a restart does not
    # re-read it (SPEC-001 kept the clean shutdown; this is what it now saves).
    if monitor.save():
        logger.debug("offsets persisted on shutdown")

    logger.info("%s daemon stopped", DISPLAY_NAME)
    logging.shutdown()
    return 0


def run_discovery(monitor: LogMonitor, previous: DiscoveryResult) -> DiscoveryResult:
    """One discovery pass, feeding the monitor and logging only what changed.

    The first pass logs a summary; later passes stay quiet unless the set of
    sites actually moved, so a 60-second loop does not fill the log
    (README.md §64).
    """
    result = discover_sites()
    added_logs, removed_logs = monitor.update_sources(result)

    if not previous.sites:
        logger.info("discovery: %s", summarize(result))
        logger.info("following %s log files", monitor.watched_count)
        for warning in result.all_warnings:
            logger.warning("discovery: %s", warning)
        return result

    added, removed = diff_sites(previous.sites, result.sites)
    for name in added:
        logger.info("site added: %s", name)
    for name in removed:
        logger.info("site removed: %s", name)

    for path in added_logs:
        logger.info("now following %s", path)
    for path in removed_logs:
        logger.info("no longer following %s", path)

    if added or removed:
        logger.info("discovery: %s", summarize(result))

    return result


def run_poll(
    monitor: LogMonitor,
    aggregator: Optional[TrafficAggregator] = None,
    parse_stats: Optional[ParseStats] = None,
) -> PollStats:
    """Read what is new, parse it, count it, then persist the offsets.

    Each event is parsed, counted and dropped inside the callback, so only one
    is ever alive. The counters it feeds are bounded and expire with their
    window; nothing accumulates. Nothing is stored and nothing is logged per
    line: writing the lines anywhere would duplicate every access log on the
    server (README.md §64).

    A line that parsed into nothing is recorded separately - it is not a
    request, and counting it as one would inflate the traffic figures with the
    parser's own blind spots.

    State is written once per poll rather than per line. A crash between
    reading and saving means a few lines are read twice on restart, which is
    the documented trade: at-least-once beats losing lines silently. The
    counts can be slightly high after a crash for the same reason; deduplicating
    would cost more than the error is worth.
    """
    if parse_stats is None:
        parse_stats = ParseStats()

    before = parse_stats.parsed + parse_stats.unparsed

    def handle(event: LogEvent) -> None:
        parsed = parsers.consume(event, parse_stats)
        if aggregator is None:
            return
        if parsed is None:
            aggregator.note_unparsed(event.log_type, event.site)
        else:
            aggregator.add(parsed)

    stats = monitor.poll(handle)

    if aggregator is not None:
        # Buckets must age out even when nothing arrived, or a server that went
        # quiet would keep reporting the traffic it had an hour ago.
        aggregator.expire()

    if stats.changed:
        read = parse_stats.parsed + parse_stats.unparsed - before
        logger.debug(
            "poll: %s (%s lines parsed this pass, %s total)",
            collector.summarize(stats),
            read,
            parse_stats.summary(),
        )
        monitor.save()

    return stats


def _build_detector(config: Config) -> Optional[IncidentDetector]:
    """Build the load detector, unless load monitoring is switched off.

    With `[load] enabled = false` everything else carries on: logs are still
    followed, traffic is still counted, `top` still works. Only the load
    trigger goes away.
    """
    if not config.get("load", "enabled"):
        logger.info("load monitoring disabled in configuration")
        return None

    return IncidentDetector(
        trigger_per_cpu=config.get("load", "trigger_per_cpu"),
        critical_per_cpu=config.get("load", "critical_per_cpu"),
        recovery_per_cpu=config.get("load", "recovery_per_cpu"),
        trigger_polls=config.get("load", "trigger_polls"),
        recovery_polls=config.get("load", "recovery_polls"),
    )


def run_load(
    detector: Optional[IncidentDetector],
    aggregator: TrafficAggregator,
) -> Optional[LoadSample]:
    """Read the load and let the detector decide. Returns the sample.

    A reading that could not be taken is skipped: opening or closing an
    incident on a number we do not have would be worse than missing one poll.
    """
    sample = load.collect()
    if sample is None or detector is None:
        return sample

    def capture():
        # Built only when an incident opens or peaks, never every poll.
        return {
            str(window): aggregator.snapshot(window_seconds=window).as_dict()
            for window in runtime.WINDOWS
        }

    detector.observe(sample, capture)
    return sample


def publish(
    aggregator: TrafficAggregator,
    previous: Optional[tuple],
    sample: Optional[LoadSample] = None,
    detector: Optional[IncidentDetector] = None,
) -> Optional[tuple]:
    """Write the snapshot the CLI reads, when there is something new to say.

    Written once per poll at most, never per line: building and serialising it
    is the expensive part of aggregation, and doing it per request would defeat
    the point. It is rewritten when the numbers change and once more when they
    reach zero, so a quiet server publishes its silence instead of leaving the
    last busy snapshot behind.
    """
    snapshot = aggregator.snapshot(window_seconds=runtime.WINDOWS[-1])
    open_incident = detector.open_incident if detector else None
    current = (
        snapshot.total_requests,
        snapshot.total_errors,
        snapshot.access_unparsed,
        round(sample.load_per_cpu, 2) if sample else None,
        open_incident.id if open_incident else None,
    )

    if current == previous:
        return previous

    if not runtime.write(aggregator, load=sample, incident=open_incident):
        logger.debug("could not publish the runtime snapshot")
    return current


__all__ = ["run", "setup_logging"]
