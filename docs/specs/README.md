# Specs

A spec describes **behavior to implement**. It is the document an implementer
reads instead of guessing.

It is not a design essay, not a requirements catalogue, and not a place for
hypotheses about features nobody asked for. If a spec cannot be implemented
from its own text, it is incomplete. If it contains sections that no
implementation will ever read, it is bloated.

Division of responsibility:

```text
README.md   vision and limits
SPEC        behavior
ADR         decisions
BACKLOG     work
STATUS      current moment
CHANGELOG   what changed
/CLAUDE.md  how agents work
```

Do not repeat the README inside a spec. Reference it.

---

## Index

| Spec | Subject | Status |
|---|---|---|
| [SPEC-001](SPEC-001-installation-lifecycle.md) | Installation lifecycle | Implemented |
| [SPEC-002](SPEC-002-aapanel-discovery.md) | aaPanel discovery | Implemented |
| [SPEC-003](SPEC-003-incremental-log-monitoring.md) | Incremental log monitoring | Implemented |
| [SPEC-004](SPEC-004-nginx-log-parsing.md) | Nginx log parsing | Implemented |
| [SPEC-005](SPEC-005-traffic-aggregation.md) | Traffic aggregation | Implemented |
| [SPEC-006](SPEC-006-load-incident-detection.md) | Load incident detection | Implemented |
| [SPEC-007](SPEC-007-deterministic-rules.md) | Deterministic rules | Implemented |
| [SPEC-008](SPEC-008-cli-reporting.md) | CLI reporting | In Progress |
| [SPEC-009](SPEC-009-ai-explainer.md) | AI explainer | Draft (deferred) |
| [SPEC-010](SPEC-010-system-resource-monitoring.md) | System resource monitoring — memory, swap, CPU, I/O, PSI and disk sampled from `/proc`, frozen into the incident | Draft |
| [SPEC-011](SPEC-011-process-attribution.md) | Process attribution — a bounded table of which process families held CPU, memory and I/O under pressure | Draft |
| [SPEC-012](SPEC-012-system-deterministic-findings.md) | System deterministic findings — the system-level rules, and how an HTTP cause and a resource cause are told apart | Draft |
| [SPEC-013](SPEC-013-php-fpm-pressure-and-pool-discovery.md) | PHP-FPM pressure and pool discovery — versions, pools, `pm.max_children`, saturation, read-only | Draft |
| [SPEC-014](SPEC-014-host-kernel-events.md) | Host / kernel events — OOM kills, segfaults and filesystem errors, from `/dev/kmsg`, with no subprocess | Draft |
| [SPEC-015](SPEC-015-diagnostic-coverage-self-check.md) | Diagnostic coverage / self-check — what aaDoctor can observe on this server, and which negatives it may state | Draft |

| [SPEC-016](SPEC-016-wordpress-security-audit.md) | WordPress security audit — detect installations, verify core integrity, find suspicious files, inventory plugins and themes. **Read-only** | Draft |
| [SPEC-017](SPEC-017-wordpress-quarantine-recovery.md) | WordPress quarantine and recovery — reversible removal of a reviewed file, restore and purge. **Writes under `/www/`; blocked on ADR-010** | Draft (blocked) |

SPEC-010 to SPEC-015 close the gap the first production server exposed: a load
rise caused by a host resource was invisible, and indistinguishable from a load
rise with no cause in the logs. They are drafts — nothing in them is
implemented, and the thresholds in SPEC-012 are less validated than any number
already in the project.

SPEC-016 and SPEC-017 open a **second domain**: security rather than
performance. They are deliberately split, because SPEC-016 is read-only like
everything before it and SPEC-017 is the first thing in the project that writes
under `/www/wwwroot/`. SPEC-017 **must not be implemented until ADR-010 is
accepted** — it contradicts ADR-001, which is currently `Accepted` and which
already rejected this exact proposal once.

Security output never enters `diagnose`. Malware found does not mean it caused
the load, and a site that caused the load is not thereby infected.

This table is part of the spec that changes status, not a separate chore: a
stale index is worse than no index.

---

## Status values

Declared at the top of every spec:

```text
Status: Draft
```

Allowed values:

```text
Draft         being written or revised; do not implement from it yet
Ready         complete enough to implement
In Progress   an implementation is underway
Implemented   behavior exists in the repository and matches this spec
Superseded    replaced; the successor is named in the header
```

No percentages. A spec is not 60% ready.

`Implemented` requires evidence in the repository. If the code drifts from the
spec, either the code is wrong or the spec is stale — fix one, do not leave both.

---

## Structure

Every spec uses these sections. A section with nothing real to say is written as
`None.` rather than padded.

```text
Problem
Goal
Non-goals
Current context
Functional requirements
Technical behavior
Data structures
Edge cases
Safety constraints
CLI impact
Persistence impact
Acceptance criteria
Verification
Out of scope
```

SPEC-010 onwards add three more, and a spec that needs them uses them rather
than folding the content somewhere it does not belong:

```text
Performance constraints          a concrete budget, when the work has a cost
                                 that could make aaDoctor the outage
Incident impact                  what this spec adds to the incident record
Interactions with existing specs what it requires of them, and what it reuses
```

The last one is not decoration. A change another spec must make is recorded
**in that spec too**, as a `Planned extension`, so the two cannot drift and
nobody resolves the difference silently in code ([/CLAUDE.md](../../CLAUDE.md)
§1).

Notes on the ones that are easy to get wrong:

* **Non-goals** — what this spec deliberately does not do, so an implementer
  does not add it.
* **Safety constraints** — the non-invasive guarantees that apply here. Never
  omit this section; write `Read-only; no writes outside /var/lib/aadoctor/.`
  if that is the whole story.
* **Acceptance criteria** — checkable statements, not intentions.
* **Verification** — how someone confirms it, including which fixtures.
* **Out of scope** — deferred work, with a pointer to the backlog item or
  Parking Lot entry that holds it.

---

## Writing a new spec

1. Confirm the README covers the area. If it does not, the README changes first.
2. Number it sequentially; numbers are never reused.
3. Start at `Status: Draft`.
4. Link the backlog items it governs, and the ADRs it depends on.
5. Keep thresholds configurable and mark unvalidated numbers as starting points.

When a decision inside a spec would be expensive to reverse, extract it to an
ADR and reference it. Specs describe behavior; ADRs hold the reasoning.
