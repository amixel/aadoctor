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
| [SPEC-001](SPEC-001-installation-lifecycle.md) | Installation lifecycle | Draft |
| [SPEC-002](SPEC-002-aapanel-discovery.md) | aaPanel discovery | Draft |
| [SPEC-003](SPEC-003-incremental-log-monitoring.md) | Incremental log monitoring | Draft |
| [SPEC-004](SPEC-004-nginx-log-parsing.md) | Nginx log parsing | Draft |
| [SPEC-005](SPEC-005-traffic-aggregation.md) | Traffic aggregation | Draft |
| [SPEC-006](SPEC-006-load-incident-detection.md) | Load incident detection | Draft |
| [SPEC-007](SPEC-007-deterministic-rules.md) | Deterministic rules | Draft |
| [SPEC-008](SPEC-008-cli-reporting.md) | CLI reporting | Draft |
| [SPEC-009](SPEC-009-ai-explainer.md) | AI explainer | Draft |

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
