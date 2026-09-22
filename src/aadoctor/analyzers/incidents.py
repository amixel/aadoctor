"""Notice when the server is under load, and freeze what the logs showed.

This module decides *when* to look, and preserves what aggregation held at that
moment. It does not decide *why*: no finding, no probable cause, no responsible
site. Reading the frozen evidence is SPEC-007's job, and keeping the two apart
is what lets the detection be trusted - an incident file records what was
measured, not what someone concluded.

Load opens the window. It is a symptom, not an explanation (README §21).
"""

from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Dict, List, Optional

from ..collectors.load import LoadSample
from ..paths import INCIDENTS_DIR
from ..storage import read_json, write_json

logger = logging.getLogger("aadoctor.incidents")

SCHEMA_VERSION = 1

OPEN = "open"
CLOSED = "closed"
#: An incident the daemon was in the middle of when it stopped. Its end is
#: unknown, and pretending otherwise would be inventing data.
INTERRUPTED = "interrupted"

HIGH = "high"
CRITICAL = "critical"

#: Defaults, echoing README §14. All unvalidated starting points.
TRIGGER_PER_CPU = 1.0
CRITICAL_PER_CPU = 2.0
RECOVERY_PER_CPU = 0.75
TRIGGER_POLLS = 2
RECOVERY_POLLS = 3

#: Incident file names, and the only pattern retention will ever delete.
ID_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}-\d{2}-\d{2}(?:-\d+)?$")

#: Snapshot factory: returns the traffic windows to freeze, or None.
Capture = Callable[[], Optional[Dict[str, object]]]


@dataclass
class Incident:
    """One period of elevated load, with the traffic that accompanied it."""

    id: str
    started_at: str
    status: str = OPEN
    severity: str = HIGH
    ended_at: Optional[str] = None
    peak_at: Optional[str] = None

    cpu_count: int = 1
    cpus_assumed: bool = False
    start_load: Dict[str, float] = field(default_factory=dict)
    peak_load: Dict[str, float] = field(default_factory=dict)

    #: Traffic windows as SPEC-005 published them, copied in. Never raw lines.
    traffic_at_start: Optional[Dict[str, object]] = None
    traffic_at_peak: Optional[Dict[str, object]] = None

    @property
    def peak_load_per_cpu(self) -> float:
        return float(self.peak_load.get("load_per_cpu", 0.0))

    def duration_seconds(self) -> Optional[float]:
        if not self.ended_at:
            return None
        try:
            start = datetime.fromisoformat(self.started_at)
            end = datetime.fromisoformat(self.ended_at)
        except ValueError:  # pragma: no cover - defensive
            return None
        return max(0.0, (end - start).total_seconds())

    def as_dict(self) -> dict:
        data = {
            "schema_version": SCHEMA_VERSION,
            "id": self.id,
            "status": self.status,
            "severity": self.severity,
            "started_at": self.started_at,
            "peak_at": self.peak_at,
            "ended_at": self.ended_at,
            "system": {
                "cpu_count": self.cpu_count,
                "start": self.start_load,
                "peak": self.peak_load,
            },
            "traffic": {
                "start": self.traffic_at_start,
                "peak": self.traffic_at_peak,
            },
        }
        if self.cpus_assumed:
            data["system"]["cpus_assumed"] = True
        duration = self.duration_seconds()
        if duration is not None:
            data["duration_seconds"] = round(duration, 1)
        return data

    @classmethod
    def from_dict(cls, data: object) -> Optional["Incident"]:
        if not isinstance(data, dict) or not data.get("id"):
            return None

        system = data.get("system") or {}
        traffic = data.get("traffic") or {}
        if not isinstance(system, dict):
            system = {}
        if not isinstance(traffic, dict):
            traffic = {}

        return cls(
            id=str(data["id"]),
            started_at=str(data.get("started_at") or ""),
            status=str(data.get("status") or CLOSED),
            severity=str(data.get("severity") or HIGH),
            ended_at=data.get("ended_at"),
            peak_at=data.get("peak_at"),
            cpu_count=int(system.get("cpu_count") or 1),
            cpus_assumed=bool(system.get("cpus_assumed")),
            start_load=system.get("start") or {},
            peak_load=system.get("peak") or {},
            traffic_at_start=traffic.get("start"),
            traffic_at_peak=traffic.get("peak"),
        )


class IncidentDetector:
    """Opens, extends and closes incidents from a stream of load samples.

    Two counters and one open incident - deliberately not a framework. What it
    has to get right is narrow: a continuous spike is one incident, a one-poll
    blip is none, and the thresholds for opening and closing are different
    numbers so an incident cannot flicker.
    """

    def __init__(
        self,
        directory: Optional[Path] = None,
        trigger_per_cpu: float = TRIGGER_PER_CPU,
        critical_per_cpu: float = CRITICAL_PER_CPU,
        recovery_per_cpu: float = RECOVERY_PER_CPU,
        trigger_polls: int = TRIGGER_POLLS,
        recovery_polls: int = RECOVERY_POLLS,
        clock: Optional[Callable[[], datetime]] = None,
    ) -> None:
        self.directory = Path(directory) if directory is not None else INCIDENTS_DIR
        self.trigger_per_cpu = float(trigger_per_cpu)
        self.critical_per_cpu = float(critical_per_cpu)
        # Closing must never be as easy as opening, or a load sitting on the
        # threshold would open and close an incident every other poll.
        self.recovery_per_cpu = min(float(recovery_per_cpu), self.trigger_per_cpu)
        self.trigger_polls = max(1, int(trigger_polls))
        self.recovery_polls = max(1, int(recovery_polls))
        self._clock = clock or _now

        self._high = 0
        self._low = 0
        self._open: Optional[Incident] = None

    # -- state -------------------------------------------------------------

    @property
    def open_incident(self) -> Optional[Incident]:
        return self._open

    def observe(self, sample: LoadSample, capture: Optional[Capture] = None) -> Optional[Incident]:
        """Take one load sample. Returns the incident it opened, if any.

        ``capture`` is called only when a snapshot is actually needed - opening
        an incident or reaching a new peak - so a quiet server never pays for
        building one.
        """
        per_cpu = sample.load_per_cpu

        if self._open is None:
            return self._while_normal(sample, per_cpu, capture)

        self._while_open(sample, per_cpu, capture)
        return None

    def _while_normal(
        self, sample: LoadSample, per_cpu: float, capture: Optional[Capture]
    ) -> Optional[Incident]:
        if per_cpu < self.trigger_per_cpu:
            self._high = 0
            return None

        self._high += 1
        if self._high < self.trigger_polls:
            # One noisy sample is not an incident.
            return None

        return self._open_incident(sample, capture)

    def _while_open(
        self, sample: LoadSample, per_cpu: float, capture: Optional[Capture]
    ) -> None:
        incident = self._open
        assert incident is not None

        if sample.load1 > float(incident.peak_load.get("load1", 0.0)):
            self._update_peak(incident, sample, capture)

        if per_cpu <= self.recovery_per_cpu:
            self._low += 1
            if self._low >= self.recovery_polls:
                self._close_incident(incident)
            return

        # Anywhere above the recovery threshold the incident simply continues:
        # a continuous spike is one incident, not one per poll.
        self._low = 0

    # -- lifecycle ---------------------------------------------------------

    def _open_incident(self, sample: LoadSample, capture: Optional[Capture]) -> Incident:
        now = self._clock()
        traffic = capture() if capture else None

        incident = Incident(
            id=self._next_id(now),
            started_at=now.isoformat(),
            status=OPEN,
            severity=self._severity(sample.load_per_cpu),
            peak_at=now.isoformat(),
            cpu_count=sample.cpu_count,
            cpus_assumed=sample.cpus_assumed,
            start_load=sample.as_dict(),
            peak_load=sample.as_dict(),
            traffic_at_start=traffic,
            traffic_at_peak=traffic,
        )

        self._open = incident
        self._high = 0
        self._low = 0

        logger.info(
            "load incident %s opened: load1=%.2f over %s CPUs (%.2f per core)",
            incident.id,
            sample.load1,
            sample.cpu_count,
            sample.load_per_cpu,
        )
        self.save(incident)
        return incident

    def _update_peak(
        self, incident: Incident, sample: LoadSample, capture: Optional[Capture]
    ) -> None:
        """Record a new peak, and re-freeze the traffic at that moment.

        The traffic responsible is often clearer at the peak than at the
        opening, when it may only just have started.
        """
        incident.peak_load = sample.as_dict()
        incident.peak_at = self._clock().isoformat()
        # Severity only ever rises: an incident that touched critical was a
        # critical incident, whatever it settled back to.
        if self._severity(sample.load_per_cpu) == CRITICAL:
            incident.severity = CRITICAL

        if capture:
            incident.traffic_at_peak = capture()

        self.save(incident)

    def _close_incident(self, incident: Incident) -> None:
        incident.status = CLOSED
        incident.ended_at = self._clock().isoformat()

        self._open = None
        self._high = 0
        self._low = 0

        logger.info(
            "load incident %s closed after %.0fs, peak %.2f per core",
            incident.id,
            incident.duration_seconds() or 0.0,
            incident.peak_load_per_cpu,
        )
        self.save(incident)

    def _severity(self, per_cpu: float) -> str:
        """Severity is a function of load and nothing else.

        It says how hard the server was pressed, never what pressed it.
        """
        return CRITICAL if per_cpu >= self.critical_per_cpu else HIGH

    def _next_id(self, now: datetime) -> str:
        """Second-resolution id, with a suffix only if that second is taken."""
        base = now.strftime("%Y-%m-%dT%H-%M-%S")
        candidate = base
        suffix = 1
        while (self.directory / f"{candidate}.json").exists():
            suffix += 1
            candidate = f"{base}-{suffix}"
        return candidate

    # -- persistence -------------------------------------------------------

    def save(self, incident: Incident) -> bool:
        """Write the incident out. Failure is logged, never raised.

        Written on opening, on each new peak and on closing, so an incident
        that is still running survives a crash as an interrupted record rather
        than disappearing.
        """
        path = self.directory / f"{incident.id}.json"
        if write_json(path, incident.as_dict()):
            return True
        logger.warning("could not write incident %s", incident.id)
        return False

    def recover(self) -> List[str]:
        """Mark incidents left open by a previous run as interrupted.

        Their end time is genuinely unknown. Guessing one, or quietly adopting
        the record as still open, would both put invented data in a file whose
        whole purpose is to be trustworthy.
        """
        recovered = []
        for incident in load_incidents(self.directory):
            if incident.status != OPEN:
                continue
            incident.status = INTERRUPTED
            self.save(incident)
            recovered.append(incident.id)
            logger.info("incident %s was open when the daemon stopped", incident.id)
        return recovered


def _now() -> datetime:
    """Local time, with the offset attached.

    Aware on purpose: an incident timestamp is read by a human hours later, and
    SPEC-004's naive error-log stamps must never be mistaken for these.
    """
    return datetime.now(timezone.utc).astimezone()


# --- reading incidents back ----------------------------------------------


def incident_files(directory: Optional[Path] = None) -> List[Path]:
    """Files in the incident directory that aaDoctor recognises as its own."""
    target = Path(directory) if directory is not None else INCIDENTS_DIR

    try:
        entries = list(target.iterdir())
    except (FileNotFoundError, NotADirectoryError):
        return []
    except OSError as exc:
        logger.warning("cannot list %s: %s", target, exc)
        return []

    found = [
        entry
        for entry in entries
        if entry.suffix == ".json" and entry.is_file() and ID_PATTERN.match(entry.stem)
    ]
    # Ids sort chronologically as text, so newest first is a reverse sort.
    return sorted(found, key=lambda path: path.stem, reverse=True)


def load_incidents(directory: Optional[Path] = None, limit: Optional[int] = None) -> List[Incident]:
    """Read incidents, newest first. An unreadable one is skipped, not fatal."""
    incidents = []
    for path in incident_files(directory):
        incident = Incident.from_dict(read_json(path))
        if incident is None:
            logger.warning("ignoring unreadable incident file %s", path.name)
            continue
        incidents.append(incident)
        if limit is not None and len(incidents) >= limit:
            break
    return incidents


def load_incident(incident_id: str, directory: Optional[Path] = None) -> Optional[Incident]:
    """Read one incident by id, or None when there is no such file."""
    target = Path(directory) if directory is not None else INCIDENTS_DIR

    if not ID_PATTERN.match(str(incident_id)):
        return None

    return Incident.from_dict(read_json(target / f"{incident_id}.json"))


def purge_old(
    directory: Optional[Path] = None,
    retention_days: int = 30,
    now: Optional[float] = None,
) -> List[str]:
    """Delete aaDoctor's own expired incident files. Nothing else, ever.

    Only files whose name matches an incident id are considered, so anything
    else in the directory is left exactly where it is (README §63).
    """
    if retention_days <= 0:
        return []

    cutoff = (time.time() if now is None else now) - retention_days * 86400
    removed = []

    for path in incident_files(directory):
        try:
            if path.stat().st_mtime >= cutoff:
                continue
            path.unlink()
        except OSError as exc:
            logger.warning("could not remove %s: %s", path.name, exc)
            continue
        removed.append(path.stem)

    if removed:
        logger.info("removed %s incidents older than %s days", len(removed), retention_days)
    return removed
