# Architecture Decision Records

An ADR records a decision that would be expensive to reverse, why it was taken,
and what it costs.

These ADRs formalize decisions that were already made in
[README.md](../../README.md). They do not reconstruct discussions that never
happened: the context sections state the real constraints, not an invented
debate.

Specs describe behavior. ADRs hold the reasoning behind the constraints those
specs obey.

---

## Index

| ADR | Decision | Status |
|---|---|---|
| [ADR-001](ADR-001-non-invasive-read-only-architecture.md) | Non-invasive read-only architecture | Accepted |
| [ADR-002](ADR-002-python-standard-library-first.md) | Python Standard Library first | Accepted |
| [ADR-003](ADR-003-filesystem-state-without-database.md) | Filesystem state without a database | Accepted |
| [ADR-004](ADR-004-systemd-daemon-lifecycle.md) | systemd daemon lifecycle | Accepted |
| [ADR-005](ADR-005-incremental-log-reading.md) | Incremental log reading | Accepted |
| [ADR-006](ADR-006-deterministic-engine-before-ai.md) | Deterministic engine before AI | Accepted |
| [ADR-007](ADR-007-aapanel-nginx-only-mvp.md) | aaPanel + Nginx only MVP | Accepted |
| [ADR-008](ADR-008-minimum-python-version.md) | Minimum Python version 3.8 | Accepted |
| [ADR-009](ADR-009-process-level-observation.md) | Process-level observation, and what is never persisted | Proposed |
| [ADR-010](ADR-010-opt-in-filesystem-mutation-for-remediation.md) | Explicit opt-in filesystem mutation for WordPress remediation | Proposed |
| [ADR-011](ADR-011-optional-outbound-network-for-security-reference-data.md) | Optional outbound network for security reference data | Proposed |

ADR-010 **contradicts [ADR-001](ADR-001-non-invasive-read-only-architecture.md)**,
which is `Accepted` and which already rejected optional remediation behind a
confirmation flag. On acceptance it supersedes ADR-001, and accepting it
requires writing ADR-001's successor in the same change — restating the full
read-only guarantee with exactly that one carve-out. Accepting it alone would
leave two `Accepted` ADRs whose decisions contradict each other.

ADR-010 and ADR-011 are deliberately separate documents on separate axes:
filesystem mutation and outbound network. Either could be accepted without the
other, and bundling them would force one decision to carry the other's
consequences.

---

## Format

```text
# ADR-XXX — Title

Status: Accepted
Date: YYYY-MM-DD

## Context

## Decision

## Consequences

### Positive

### Negative

## Alternatives considered
```

Status values:

```text
Proposed    under discussion
Accepted    in force
Superseded  replaced; the successor ADR is named in the header
Deprecated  no longer applies, with no direct successor
```

An accepted ADR is never edited to say something different. It is superseded by
a new one that references it.

---

## When to write one

Write an ADR when a change would:

* add a dependency, a service or a datastore;
* weaken a non-invasive guarantee;
* alter the installation or uninstallation contract;
* introduce one of the patterns forbidden in [/CLAUDE.md](../../CLAUDE.md) §11;
* turn aaDoctor from an observer into an executor (README §90).

Ordinary implementation choices inside a spec do not need one.
