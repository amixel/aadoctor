# ADR-005 — Incremental log reading

Status: Accepted
Date: 2026-09-22

Related:
- README.md §17, §18, §19, §58
- SPEC-003

## Context

An aaPanel server with 48 sites can hold tens of gigabytes of access logs, some
individual files exceeding 14 GB. aaDoctor polls those files continuously, on a
machine that is by definition already under load when it matters most.

Any approach that re-reads a file, scans it for recent entries, or shells out to
`tail` in a loop turns the monitor into a significant I/O consumer — and the
tool's core promise is that it never becomes the cause of the problem it
diagnoses.

Logs also rotate, get truncated, and are occasionally deleted and recreated
underneath the reader, all controlled by aaPanel and logrotate, neither of which
aaDoctor may interfere with.

## Decision

Each tracked file is followed incrementally, keyed by:

```text
path
inode
offset
```

Rules:

- On first observation, `offset` is set to **end of file**, never 0. A large
  historical log contributes nothing until it grows.
- Each poll reads only the bytes between `offset` and the current end.
- A partial trailing line is held until complete, never split or parsed twice.
- A changed inode means rotation: the new file is read from 0; the old inode's
  remaining tail is read only on a best-effort, bounded basis.
- `size < offset` on the same inode means truncation: the offset resets to 0.
- Offsets are persisted in `/var/lib/aadoctor/state.json`
  ([ADR-003](ADR-003-filesystem-state-without-database.md)) and restored on
  restart.
- Corrupt or missing state degrades to tail-from-end. Losing recent history is
  acceptable; re-reading everything is not.
- Reading is done in Python against the file descriptor; no `tail`, `grep` or
  other subprocess in the loop (README §58).

aaDoctor never rotates, truncates, moves or deletes a log it reads.

## Consequences

### Positive

- I/O is proportional to new traffic, not to log size. Adding a site with a
  10 GB historical log costs nothing.
- Startup is instant regardless of accumulated log volume.
- Restarts resume precisely, with no duplicate counting.
- Rotation and truncation are handled without cooperating with logrotate.
- Works identically for 5 sites and 500.

### Negative

- History is unavailable. aaDoctor cannot diagnose an incident that happened
  before it was installed, or during a period when it was stopped — by design
  (README §17).
- A crash between offset flushes loses a small amount of recent data.
- The rotated-file tail is best-effort, so a rotation during a spike can drop
  lines; correctness of the new file takes priority.
- The inode-based logic is subtle, and the edge cases in
  [SPEC-003](../specs/SPEC-003-incremental-log-monitoring.md) must be covered by
  tests rather than reasoned about ad hoc.

## Alternatives considered

**Read the whole file and filter by timestamp.**
Rejected: it is the failure mode this decision exists to prevent. On a 14 GB log
it would saturate I/O on an already-degraded server.

**Shell out to `tail -F` per site.**
Rejected: dozens of subprocesses, output parsing, no offset control across
restarts, and a process supervision problem of its own
([ADR-002](ADR-002-python-standard-library-first.md) also argues against it).

**inotify / filesystem watches.**
Rejected for the MVP: watch-descriptor limits across 100+ files, extra
complexity for rotation handling, and no real benefit at a 10-second polling
interval. Polling is sufficient because incidents are analyzed over minutes, not
milliseconds.

**Offset by line number instead of byte offset.**
Rejected: determining a line number requires reading from the start, which is
the exact cost being avoided.
