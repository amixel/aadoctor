# ADR-006 — Deterministic engine before AI

Status: Accepted
Date: 2026-09-22

Related:
- README.md §9, §10, §34, §35, §71, §72, §73
- SPEC-007, SPEC-009

## Context

Feeding logs to a language model and asking what went wrong is the obvious
shortcut, and it fails in exactly the situations aaDoctor is built for: it costs
money per incident, requires connectivity from a server that may be struggling,
produces a different answer each run, cannot be unit tested, and can state a
plausible cause that the data does not support.

The administrator acting at 3 a.m. needs a number they can check, not a
paragraph they have to trust.

At the same time, a structured incident report is terse, and a plain-language
summary genuinely helps — after the facts are established.

## Decision

The diagnosis is deterministic. AI is an optional explanation layer.

```text
logs → parser → aggregation → deterministic rules → incident.json
                                                        ↓
                                              optionally AI → explanation
```

Specifically:

- The full pipeline works with no API key, no internet and no model
  (README §9). `[ai] enabled = false` is the default.
- Rules are pure functions over a window snapshot. Same input, same findings,
  same confidence, every time
  ([SPEC-007](../specs/SPEC-007-deterministic-rules.md)).
- Confidence is computed by the rules. **AI never sets, raises or lowers it**
  (README §34).
- AI receives only the compact structured summary of README §10 — aggregates and
  finding codes. Never raw logs.
- AI is called only on explicit user action (`aadoctor explain`), never
  automatically per incident.
- Any AI failure degrades to the deterministic diagnosis (README §73).
- The AI prompt forbids inventing metrics or causes and requires distinguishing
  observation from hypothesis (README §72).
- AI never proposes remediation
  ([ADR-001](ADR-001-non-invasive-read-only-architecture.md)).

## Consequences

### Positive

- Works offline, on an air-gapped or firewalled server, at zero marginal cost.
- Findings are reproducible, which makes them testable against fixtures and
  arguable in a post-mortem.
- Every conclusion traces back to counts the administrator can verify by hand.
- No incident is delayed by an API call, and no outage is made worse by a
  hanging request.
- Threshold tuning is a visible configuration change, not an opaque model
  behavior change.

### Negative

- Rules only find what they were written to find. A novel failure pattern
  produces no finding until someone adds one.
- Thresholds need tuning against real incidents, and the initial values are
  guesses (README §71).
- The deterministic output is drier than a generated narrative.
- Two layers exist where a single AI-driven one would be less code — accepted,
  because the second layer is optional and removable.

## Alternatives considered

**Send logs to a model and let it diagnose.**
Rejected: non-reproducible, expensive per incident, dependent on connectivity
during an outage, untestable, and prone to asserting causes the evidence does
not support. It is explicitly the anti-pattern named in README §9.

**AI-assisted confidence scoring on top of deterministic findings.**
Rejected: it reintroduces non-determinism into the number the administrator acts
on, and makes two runs on the same incident disagree.

**Local model on the server.**
Rejected: adds runtime weight and dependencies
([ADR-002](ADR-002-python-standard-library-first.md)) to a tool that must stay
light on a machine that is already overloaded.

**No AI at all.**
Reasonable, and the project would still be complete. Kept as an optional layer
because turning a correct structured diagnosis into plain language is genuinely
useful for less experienced administrators — as long as it can never affect the
diagnosis itself.
