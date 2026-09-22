"""Read the system load average. Collect only; decide nothing.

`/proc/loadavg` is read directly - no `uptime`, no `top`, no subprocess in a
loop (README §58). Whether a number means trouble is the detector's business,
not this module's.

One thing worth stating plainly, because it is the most common mistake made
with this number: **load is not CPU usage**. A Linux load average counts tasks
that are runnable *and* tasks in uninterruptible sleep, so a server stuck on
disk or on a database can show a high load with idle CPUs. aaDoctor uses load
as a sign that something is wrong and as the moment to look at the logs - never
as the diagnosis itself (README §21).
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Tuple

logger = logging.getLogger("aadoctor.load")

LOADAVG_PATH = Path("/proc/loadavg")

#: Used when the CPU count cannot be determined. Never zero: it is a divisor.
FALLBACK_CPUS = 1


@dataclass
class LoadSample:
    """One reading of the load average, normalised by CPU count."""

    load1: float
    load5: float
    load15: float
    cpu_count: int
    #: True when the CPU count could not be read and the fallback was used, so
    #: a load-per-core computed on a guess is visible as such.
    cpus_assumed: bool = False

    @property
    def load_per_cpu(self) -> float:
        return self.load1 / float(self.cpu_count or FALLBACK_CPUS)

    def as_dict(self) -> dict:
        data = {
            "load1": self.load1,
            "load5": self.load5,
            "load15": self.load15,
            "cpu_count": self.cpu_count,
            "load_per_cpu": round(self.load_per_cpu, 3),
        }
        if self.cpus_assumed:
            data["cpus_assumed"] = True
        return data


def read_loadavg(path: Optional[Path] = None) -> Optional[Tuple[float, float, float]]:
    """Return (load1, load5, load15), or None when the file cannot be used.

    ``path`` exists so tests can point at a file of their own; production always
    reads /proc/loadavg. Any failure returns None rather than raising: a load
    reading that cannot be trusted must not open or close an incident, and must
    not stop the daemon from reading logs.
    """
    target = Path(path) if path is not None else LOADAVG_PATH

    try:
        text = target.read_text(encoding="utf-8", errors="replace")
    except FileNotFoundError:
        logger.warning("%s not found; load monitoring is unavailable", target)
        return None
    except OSError as exc:
        logger.warning("cannot read %s: %s", target, exc)
        return None

    return parse_loadavg(text)


def parse_loadavg(text: str) -> Optional[Tuple[float, float, float]]:
    """Parse the first three fields of a loadavg line.

    The format is ``0.42 1.23 2.01 2/441 12345``; only the averages are used.
    Anything that does not start with three numbers is treated as unreadable.
    """
    fields = text.split()
    if len(fields) < 3:
        logger.warning("unexpected loadavg content: %r", text[:80])
        return None

    try:
        values = (float(fields[0]), float(fields[1]), float(fields[2]))
    except ValueError:
        logger.warning("unexpected loadavg content: %r", text[:80])
        return None

    if any(value < 0 for value in values):
        logger.warning("negative load average: %r", text[:80])
        return None

    return values


def cpu_count() -> Tuple[int, bool]:
    """Usable CPU count, and whether it had to be assumed.

    Never returns zero: this number divides the load average.
    """
    try:
        count = os.cpu_count()
    except Exception:  # pragma: no cover - defensive
        count = None

    if not count or count < 1:
        return FALLBACK_CPUS, True
    return int(count), False


def collect(
    path: Optional[Path] = None,
    cpus: Optional[int] = None,
) -> Optional[LoadSample]:
    """One complete sample, or None when the load could not be read."""
    values = read_loadavg(path)
    if values is None:
        return None

    if cpus is not None:
        count, assumed = max(1, int(cpus)), False
    else:
        count, assumed = cpu_count()

    return LoadSample(
        load1=values[0],
        load5=values[1],
        load15=values[2],
        cpu_count=count,
        cpus_assumed=assumed,
    )
