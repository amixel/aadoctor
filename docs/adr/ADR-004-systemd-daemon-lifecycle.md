# ADR-004 — systemd daemon lifecycle

Status: Accepted
Date: 2026-09-22

Related:
- README.md §43, §44, §49, §56, §57
- SPEC-001, SPEC-006

## Context

aaDoctor has to be running *before* the incident, holding rolling windows of
recent traffic in memory, because the evidence it needs disappears with the
spike. That makes it a long-lived process, not a periodic job.

It must also start on boot, restart after a crash, and be controllable through
the same mechanism the administrator already uses for every other service on the
machine.

Target servers are modern Linux distributions running aaPanel; systemd is
present on all of them.

## Decision

The daemon runs under systemd as `aadoctor.service`, installed at
`/etc/systemd/system/aadoctor.service` (README §56):

```ini
[Service]
Type=simple
ExecStart=/usr/bin/python3 /opt/aadoctor/aadoctor daemon
Restart=on-failure
RestartSec=5
NoNewPrivileges=true
PrivateTmp=true
```

Cron is not used for the monitoring loop.

`aadoctor enable` and `aadoctor disable` wrap `systemctl enable --now` and
`systemctl disable --now`, and are idempotent.

aaDoctor controls **only** `aadoctor.service`. It never starts, stops, restarts
or reloads any other unit — not Nginx, not PHP-FPM, not MySQL — under any
circumstance, including upgrades
([ADR-001](ADR-001-non-invasive-read-only-architecture.md)).

The unit is also the reason the project requires systemd at all: without it,
installation aborts rather than falling back to another supervision mechanism.

## Consequences

### Positive

- Rolling windows survive between samples, which is what makes correlation
  possible at all.
- Boot persistence, crash restart, log integration and status come from the
  platform rather than from aaDoctor code.
- The administrator manages it exactly like every other service.
- Clean removal: stop, disable, remove the unit, `daemon-reload`.
- Hardening options are available later without code changes.

### Negative

- Hard dependency on systemd; non-systemd hosts are unsupported.
- A resident process consumes memory continuously, so bounded aggregation is not
  optional (README §58–§59).
- Running as root under systemd means a long-lived root process reading site
  logs — accepted in README §57, and the reason `NoNewPrivileges` and
  `PrivateTmp` are set from the start.
- State must be flushed regularly, since a restart loses in-memory windows.

## Alternatives considered

**Cron every minute.**
Rejected: no memory between runs, so rolling windows would have to be rebuilt
from disk each time; startup cost per run; minute granularity misses short
spikes; and offsets would be written far more often.

**A self-daemonizing process with a PID file.**
Rejected: reimplements supervision, boot persistence and restart logic that
systemd already provides correctly, and complicates uninstall.

**On-demand analysis only, with no daemon.**
Rejected: it is the current situation the project exists to fix. Running
`top` after the incident cannot answer which site caused it (README §1).

**A dedicated non-root service user.**
Deferred rather than rejected — it is in the Parking Lot. It is only acceptable
if it requires no change to aaPanel file permissions (README §57).
