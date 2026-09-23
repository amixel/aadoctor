# SPEC-009 — AI Explainer

Status: Draft

**Deferred until deterministic system diagnostics are field-validated.** The
deterministic engine now answers HTTP-caused degradation, and SPEC-010 to
SPEC-015 extend it to host resources — none of which has been tested against a
real server whose load comes from traffic. This spec explains what that engine
decided, so it is worth nothing until the engine decides well: an explanation
layer over unchecked answers only makes them more convincing. No date, and no
re-planning of its contents here — everything below stands as written.

Related: [README.md](../../README.md) §9, §10, §62, §72, §73, §74 ·
[ADR-006](../adr/ADR-006-deterministic-engine-before-ai.md) ·
Backlog: AAD-060, AAD-061, AAD-062, AAD-063

---

## Problem

A deterministic incident report is precise but terse. Turning it into a plain
explanation helps, but sending logs to a model and asking it to guess would make
the tool slow, expensive, non-reproducible and wrong under pressure.

## Goal

An optional, off-by-default layer that rephrases an already-complete
deterministic diagnosis in human language.

**The AI explains findings. It never produces them.**

## Non-goals

- Detection, scoring or ranking of any kind.
- Automatic remediation or suggested commands to run.
- Sending raw logs anywhere.
- Being required for anything.

## Current context

Nothing is implemented. Default configuration (README §9):

```toml
[ai]
enabled = false
model = "gpt-5.6-luna"
```

---

## Functional requirements

### Disabled by default

With `enabled = false` — the default — no network call is ever made, no API key
is read, and every other feature works unchanged. The project must function 100%
without AI.

### Invocation

Only on explicit user action:

```bash
aadoctor explain <incident>
```

Never automatically on incident creation (README §58). One user request, one
call.

### Payload

The model receives the compact structured summary of README §10 and nothing
else:

```json
{
  "load": 9.82,
  "cpus": 4,
  "site": "loja.com.br",
  "site_requests": 8922,
  "top_path": "/wp-cron.php",
  "top_path_requests": 4812,
  "top_ip": "45.xxx.xxx.xxx",
  "top_ip_requests": 4102,
  "upstream_timeouts": 38,
  "http_502": 17,
  "findings": [
    "ONE_SITE_DOMINATING",
    "ONE_URL_DOMINATING",
    "ONE_IP_DOMINATING",
    "UPSTREAM_TIMEOUT"
  ]
}
```

Constraints:

- Aggregates and finding codes only. **No raw log lines, ever.**
- Bounded size regardless of incident size, so cost is predictable.
- No cookies, headers, bodies, tokens or credentials (README §61).
- Optional IP anonymization (`45.x.x.x` → `IP_1`) is available and off by
  default (README §62, Parking Lot for the full mapping design).

### Prompt constraints

The system prompt instructs the model (README §72):

```text
Use only the provided evidence.
Do not invent metrics.
Do not invent causes.
Distinguish observed cause from hypothesis.
State uncertainty when present.
```

The model is told what the finding codes mean; it is not asked to decide whether
they apply.

### No confidence override

Confidence comes from [SPEC-007](SPEC-007-deterministic-rules.md) and is
rendered by the CLI, not by the model. If the explanation text disagrees with
the deterministic confidence, the deterministic value stands and the output
keeps the two visually separated: evidence first, AI interpretation clearly
labeled.

### No remediation

The explanation may describe what the evidence means. It must not instruct the
administrator to run commands, block addresses, restart services or change
configuration. aaDoctor observes (README §3.1, §90).

### Failure isolation

Any failure — offline API, timeout, exhausted quota, invalid key, malformed
response (README §73) — degrades to:

```text
AI explanation unavailable.

Deterministic diagnosis remains available.
```

Exit code stays 0 when the deterministic diagnosis rendered successfully. A
network timeout has a bounded, configurable limit; the command never hangs.

### Key handling

The API key is read from an environment variable or a protected file
(README §74). It must never appear in:

```text
incident.json
aadoctor.log
stdout
stderr
a crash traceback
```

`doctor` reports AI as configured or not configured, without printing the key.

### Cost awareness

- One call per explicit request; no retries beyond a small bounded count.
- Payload size is capped.
- Responses may be cached alongside the incident so re-running `explain` on the
  same incident does not spend again. **TBD**: whether the cached explanation is
  stored in the incident file or beside it.

---

## Technical behavior

- The HTTP call uses `urllib.request` from the standard library
  ([ADR-002](../adr/ADR-002-python-standard-library-first.md)). An SDK
  dependency requires its own ADR.
- The call happens in the CLI process, never in the daemon loop.
- The provider integration is a single small module; swapping providers must not
  touch detection code.
- The explanation is text, rendered below the deterministic report, labeled as
  AI-generated.

## Data structures

Request payload as above. Stored explanation, if caching is adopted:

```text
incident_id
model
generated_at
text
payload_hash    so a stale explanation is detectable
```

## Edge cases

| Case | Behavior |
|---|---|
| AI disabled | Print how to enable it; exit 0 |
| Key missing while enabled | Clear message; deterministic diagnosis still shown |
| API timeout | Bounded wait, then the unavailable message |
| Rate limited | Same; no aggressive retry |
| Model returns nonsense | Rendered as AI text, clearly separated from evidence |
| Model contradicts the findings | Deterministic findings stand; no reconciliation attempt |
| Incident with no findings | Explanation may state the cause is not visible in the logs |
| Unknown incident id | Exit 3 per [SPEC-008](SPEC-008-cli-reporting.md); no call made |
| No internet on the server | Same as offline API |

## Safety constraints

- Never sends raw logs, file contents, configuration or credentials.
- Never runs a command, and never emits one as an instruction.
- Never writes outside `/var/lib/aadoctor/`.
- Never blocks the daemon.
- The key never reaches a log, an incident file or a terminal.

## CLI impact

Adds `explain <incident>`. With AI disabled it prints how to enable it and exits
cleanly. Output separates deterministic evidence from AI interpretation.

## Persistence impact

None required. Optional cached explanations, pending the TBD above.

---

## Acceptance criteria

- [ ] With `enabled = false`, no network call is made and nothing else changes.
- [ ] The payload contains aggregates and finding codes only; no raw log lines.
- [ ] The API key never appears in any file, log or terminal output.
- [ ] Every failure mode degrades to the deterministic diagnosis.
- [ ] AI output never alters findings or confidence.
- [ ] AI output contains no remediation instruction.
- [ ] `explain` makes exactly one call per invocation.

## Verification

- Run the whole suite with AI disabled; assert zero network calls.
- Stub the provider with: success, timeout, 401, 429, malformed JSON. Assert the
  deterministic output is intact in every case.
- Assert the serialized payload against a fixture incident contains no raw log
  content and no key material.
- Grep logs, incident files and stdout for the key after a full run.

## Out of scope

- AI-driven detection of any kind ([ADR-006](../adr/ADR-006-deterministic-engine-before-ai.md)).
- Automatic explanation of every incident.
- Full IP anonymization mapping — Parking Lot.
- Multiple providers or local models.
