# ADR-003 — Filesystem state without a database

Status: Accepted
Date: 2026-09-22

Related:
- README.md §12, §13, §18, §36, §37, §63
- SPEC-003, SPEC-006

## Context

aaDoctor keeps two kinds of state: log offsets, which are a few hundred bytes
per file and are rewritten continuously, and incidents, which are written once
and read rarely.

Neither needs transactions across entities, relational queries or concurrent
writers. There is one writer — the daemon — and readers that want the most
recent snapshot or one incident by id.

Adding a database would mean either a service to run and monitor (Redis,
PostgreSQL) on a server already suffering from load, or a file format that needs
schema migrations and a client library (SQLite). Both introduce failure modes
that the diagnosis of a struggling server does not need.

## Decision

State lives in plain files under `/var/lib/aadoctor/`:

```text
/var/lib/aadoctor/state.json      path → { inode, offset }
/var/lib/aadoctor/incidents/      one JSON file per incident
```

No SQLite, no Redis, no PostgreSQL, no embedded key-value store — in the MVP and
until an ADR supersedes this one.

Rules that make this safe:

- Every write is atomic: temporary file plus rename.
- Corrupt or unreadable state degrades gracefully. Lost offsets mean resuming at
  end of file, never re-reading a multi-gigabyte log
  ([ADR-005](ADR-005-incremental-log-reading.md)).
- Offsets are flushed on an interval and on clean shutdown, not per line.
- Incident filenames are the incident ids, so lookup is a path join.
- Retention deletes only aaDoctor's own files (README §63).

Convenience is explicitly not a reason to add a database. "It would be easier to
query" is not a need; a demonstrated failure of the file approach is.

## Consequences

### Positive

- No service to install, secure, monitor or restart.
- Inspectable with `cat` and `jq` during an incident, including by the
  administrator without aaDoctor's help.
- Backup and purge are file operations.
- No schema migrations and no client library
  ([ADR-002](ADR-002-python-standard-library-first.md)).
- One fewer thing that can fail while the server is already degraded.

### Negative

- No queries across incidents. "Show every incident where this IP appeared"
  means reading the directory.
- Many small files over time; retention and a sensible directory layout matter.
- Concurrent access is coordinated by convention, not by a database.
- JSON is verbose and reparsed on every read.
- A future baselines feature (README §70) would stress this choice and is the
  most likely trigger for a superseding ADR.

## Alternatives considered

**SQLite.**
The closest call: it is in the standard library, single-file, transactional, and
would make cross-incident queries trivial. Rejected for the MVP because nothing
currently needs those queries, and it would bring a schema, migrations and a
binary file the administrator cannot read during an outage. Revisit if
historical analysis becomes a real requirement.

**Redis.**
Rejected outright. A network service and a daemon to run on a machine whose
problem is load, holding data that must survive restarts anyway.

**In-memory only, with no persistence.**
Rejected: offsets must survive a restart, or every restart either loses history
or risks re-reading whole logs. Incidents exist precisely because the
administrator arrives after the fact.

**Append-only log file for incidents.**
Rejected: incidents are naturally addressed by id, and one file per incident
makes retention, inspection and partial-write recovery simpler.
