"""Minimal daemon.

This phase implements the lifecycle only: start, stay alive, shut down cleanly
on SIGTERM and SIGINT. There is no log reading, no discovery, no load
monitoring and no incident detection - those belong to SPEC-003 onwards and
must not be smuggled in here.

The daemon exists now so that install, enable, disable, restart and uninstall
can be exercised against a real process.
"""

from __future__ import annotations

import logging
import signal
import sys
import threading
from pathlib import Path
from typing import Optional

from . import __version__
from .config import Config
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

    if not config.get("monitor", "enabled"):
        logger.info("monitoring disabled in configuration")
    elif not has_aapanel():
        logger.warning("aaPanel not detected; no monitoring started")

    # Monitoring is not implemented yet (SPEC-003 onwards). Said once, at
    # startup, rather than repeated on every tick.
    logger.info("monitoring is not implemented in this version")

    while not stop.is_set():
        stop.wait(TICK_SECONDS)

    logger.info("%s daemon stopped", DISPLAY_NAME)
    logging.shutdown()
    return 0


__all__ = ["run", "setup_logging"]
