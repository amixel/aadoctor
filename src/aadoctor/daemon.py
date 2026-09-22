"""The daemon: discovery, and following the logs it finds.

It starts, re-reads the aaPanel vhost files on an interval so a new site is
noticed without a restart (SPEC-002), follows each discovered log incrementally
(SPEC-003), persists its offsets, and shuts down cleanly on SIGTERM and SIGINT.

It does not yet parse a single line: understanding them is SPEC-004. Lines read
here are counted and discarded, which is enough to prove the reader works
without writing a second copy of every access log (README.md §64).
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
from .collectors import LogMonitor, PollStats
from .collectors import logs as collector
from .config import Config
from .discovery import DiscoveryResult, diff_sites, discover_sites, summarize
from .environment import has_aapanel
from .paths import DISPLAY_NAME, LOG_FILE

#: How often the wait loop wakes up. Only affects shutdown latency.
TICK_SECONDS = 1.0

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

    # Parsing is SPEC-004. This phase proves the reader, nothing more.
    logger.info("log parsing is not implemented in this version")

    discovery_interval = max(1, int(config.get("discovery", "interval_seconds")))
    monitor_interval = max(1, int(config.get("monitor", "interval_seconds")))

    monitor = LogMonitor()
    previous = DiscoveryResult(vhost_dir=Path("."))
    next_discovery = 0.0
    next_poll = 0.0

    while not stop.is_set():
        now = time.monotonic()

        if enabled and now >= next_discovery:
            previous = run_discovery(monitor, previous)
            next_discovery = now + discovery_interval

        if enabled and now >= next_poll:
            run_poll(monitor)
            next_poll = now + monitor_interval

        stop.wait(TICK_SECONDS)

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


def run_poll(monitor: LogMonitor) -> PollStats:
    """Read what is new, then persist the offsets if any moved.

    Lines are counted, not stored and not logged: writing them anywhere would
    duplicate every access log on the server (README.md §64).

    State is written once per poll rather than per line. A crash between
    reading and saving means a few lines are read twice on restart, which is
    the documented trade: at-least-once beats losing lines silently.
    """
    stats = monitor.poll()

    if stats.changed:
        logger.debug("poll: %s", collector.summarize(stats))
        monitor.save()

    return stats


__all__ = ["run", "setup_logging"]
