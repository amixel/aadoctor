# SPEC-007 — Deterministic Rules

Status: Draft

Related: [README.md](../../README.md) §25–§35, §71, §32, §33, §34 ·
[ADR-006](../adr/ADR-006-deterministic-engine-before-ai.md) ·
Backlog: AAD-040 … AAD-048

---

## Problem

Aggregated numbers still require interpretation. The administrator needs a named
finding with the evidence behind it, produced the same way every time, without
depending on an external service.

## Goal

A small set of rules that turn a window snapshot into named findings with
evidence and confidence.

**Every rule must be explainable in one sentence and reproducible from a
fixture.**

## Non-goals

- Machine learning, anomaly models or historical baselines.
- Remediation suggestions that imply action on the server.
- A large finding catalogue. Few good rules beat many weak ones (README §32).

## Current context

Nothing is implemented. Input is the frozen window snapshot from
[SPEC-005](SPEC-005-traffic-aggregation.md), supplied by
[SPEC-006](SPEC-006-load-incident-detection.md).

---

## Functional requirements

### Findings in scope

```text
TRAFFIC_SPIKE
ONE_SITE_DOMINATING
ONE_URL_DOMINATING
ONE_IP_DOMINATING
NOT_FOUND_FLOOD
HTTP_5XX_SPIKE
UPSTREAM_TIMEOUT
FASTCGI_ERROR
PHP_ERROR_SPIKE
```

`BOT_OR_CRAWLER_SPIKE`, `PHP_MEMORY_EXHAUSTED` and `PHP_EXECUTION_TIMEOUT`
appear in README §32 but are not in this scope; they are in the Parking Lot.
PHP memory and execution-timeout events still appear as **evidence** inside
`PHP_ERROR_SPIKE`.

### Rule contract

Every rule declares:

```text
inputs       fields of the window snapshot it reads
condition    the boolean expression that fires it
evidence     the facts attached to the finding
confidence   how the score is computed
false positives  known situations that mimic it
```

A rule reads the snapshot only. It performs no I/O, and it never reads the
result of another rule — combination happens after all rules have run.

### Minimum volume guard

No rule fires when the window's relevant denominator is below a minimum volume
(default 100 requests, configurable). Three requests from one IP is 100% share
and means nothing. This guard applies to every share-based rule.

### Thresholds

All thresholds are configuration with documented defaults. **The values below
are starting points, not tuned values.** They will change once real incidents
are available; nothing in the implementation may hardcode them.

---

### TRAFFIC_SPIKE

```text
inputs      total_requests in the window, preceding comparable windows
condition   total_requests >= spike_factor × recent_typical AND
            total_requests >= min_volume
evidence    window requests, requests/sec, the comparison value used
confidence  grows with the ratio, capped without a baseline
```

Starting points: `spike_factor = 3.0`, comparison against the median of the
preceding N windows held in memory.

False positives: a legitimate campaign, a scheduled job, a site launch. This
finding is weak alone and gains meaning combined with others.

TBD: how many preceding windows to keep in memory for comparison. Without a
baseline ([Parking Lot](../BACKLOG.md#parking-lot)), confidence is capped at
MEDIUM.

---

### ONE_SITE_DOMINATING

```text
inputs      requests per site, server total
condition   site_requests / total_requests > site_share_threshold
evidence    site, requests, share, second-place site for contrast
confidence  scales from the threshold to 1.0
```

Starting point: `site_share_threshold = 0.70` (README §71).

False positives: a server that legitimately hosts one dominant site among many
idle ones. Contrast with the second-place site makes this visible in the output.

---

### ONE_URL_DOMINATING

```text
inputs      requests per normalized path within the suspect site, site total
condition   path_requests / site_requests > url_share_threshold
evidence    normalized path, requests, share, sample raw query strings
confidence  scales with share and absolute count
```

Starting point: `url_share_threshold = 0.50`.

False positives: a normal high-traffic endpoint — the homepage, a health check,
a legitimate API. Absolute rate matters as much as share; `/` at 60% of a small
site is unremarkable.

Shares are computed against the **site** total, never the server total.

---

### ONE_IP_DOMINATING

```text
inputs      requests per IP within the suspect site, site total
condition   ip_requests / site_requests > ip_share_threshold
evidence    IP, requests, share, top paths for that IP, its user agents
confidence  scales with share and absolute count
```

Starting point: `ip_share_threshold = 0.60` (README §71).

False positives — important (README §27): a CDN or reverse proxy in front of the
site makes every request appear to come from one address; NAT concentrates many
real users; a legitimate integration polls from a fixed address. The output says
**possible bot, crawler, integration or flood**, never "attack", and never
recommends blocking.

---

### NOT_FOUND_FLOOD

```text
inputs      404 count, total requests, top 404 paths
condition   404_count / total > not_found_share AND
            404_rate_per_sec >= not_found_min_rate
evidence    404 count, share, rate, top 404 paths under the cardinality cap
confidence  scales with share and rate
```

Starting points: `not_found_share = 0.30`, `not_found_min_rate = 5/s`.

False positives: a vulnerability scanner producing harmless 404s at low cost — a
404 is usually cheap, so this finding explains noise more often than load. It is
reported as context unless paired with a load trigger and high volume.

---

### HTTP_5XX_SPIKE

```text
inputs      5xx counts, per code and per site
condition   5xx_count / total > error_share OR 5xx_rate >= error_min_rate
evidence    counts per code (500/502/503/504), affected sites
confidence  scales with rate; higher when concentrated on one site
```

Starting points: `error_share = 0.05`, `error_min_rate = 1/s`.

False positives: one broken site among healthy ones; a deploy in progress.

---

### UPSTREAM_TIMEOUT

```text
inputs      UPSTREAM_TIMEOUT events from the error log, 504 counts
condition   event_count >= upstream_timeout_min
evidence    count, affected sites, upstream when logged, correlated 504s
confidence  scales with count; higher when 504s corroborate
```

Starting point: `upstream_timeout_min = 5` per window.

False positives: one slow endpoint on one site during a maintenance task.
Timeouts are a symptom; they usually accompany the real cause rather than being
it.

---

### FASTCGI_ERROR

```text
inputs      FASTCGI_ERROR events from the error log
condition   event_count >= fastcgi_error_min
evidence    count, affected sites, message samples (bounded)
confidence  scales with count
```

Starting point: `fastcgi_error_min = 5` per window.

False positives: PHP-FPM restarted by the administrator; a pool being
reconfigured. aaDoctor reports the errors; it never restarts PHP-FPM.

---

### PHP_ERROR_SPIKE

```text
inputs      PHP error events by class (fatal, memory exhausted,
            execution timeout, warning, parse)
condition   php_error_count >= php_error_min
evidence    counts per class, affected sites, bounded message samples
confidence  scales with count; fatal and memory-exhausted weigh more than
            warnings
```

Starting point: `php_error_min = 10` per window.

False positives: a chronically noisy site that logs warnings constantly. Without
a baseline, a chatty site can look like a spike — a reason the finding weighs
fatal errors above warnings.

---

### Combination

Rules do not chain, but the incident records which fired together. Correlation
is where the value is (README §33):

```text
TRAFFIC_SPIKE + ONE_SITE_DOMINATING + ONE_URL_DOMINATING + ONE_IP_DOMINATING
→ one IP hammering one URL on one site, coincident with the load peak

ONE_SITE_DOMINATING + UPSTREAM_TIMEOUT + HTTP_5XX_SPIKE
→ one site concentrating activity while its upstream fails
```

The renderer ([SPEC-008](SPEC-008-cli-reporting.md)) presents the primary
finding first, then supporting findings, then evidence.

### Confidence

Internally `0.00 → 1.00`, presented as (README §34):

```text
LOW        < 0.50
MEDIUM     0.50 – 0.74
HIGH       0.75 – 0.89
VERY_HIGH  >= 0.90
```

Rules:

- Confidence is computed from evidence: share above threshold, absolute volume,
  and corroboration by other findings.
- Confidence is capped when the window was truncated by cardinality limits, when
  volume is near the minimum, or when no baseline is available.
- **AI never sets, raises or lowers confidence** (README §34,
  [ADR-006](../adr/ADR-006-deterministic-engine-before-ai.md)).
- The exact scoring function is TBD and must be a single documented function,
  not scattered per-rule arithmetic.

### Evidence-first output

No finding is reported without its evidence (README §35). Never:

```text
Causa: WordPress.
```

Always the counts, shares and correlations that produced it.

---

## Technical behavior

- Each rule is a pure function `snapshot → finding | None`.
- Rules are registered in a table; adding one is one entry and one function.
- Evaluation order does not affect results.
- Given identical snapshots, the finding set, evidence and confidence are
  byte-identical across runs.
- Standard library only.

## Data structures

```text
finding:
  code          e.g. ONE_SITE_DOMINATING
  confidence    0.00 - 1.00
  level         LOW | MEDIUM | HIGH | VERY_HIGH
  evidence      rule-specific, bounded, serializable
  thresholds    the values actually used, for auditability
```

Recording the thresholds used matters: an incident from a server with custom
configuration must be interpretable later.

## Edge cases

| Case | Behavior |
|---|---|
| Volume below the minimum guard | No share-based finding fires |
| Empty window | No findings; the incident says the logs show nothing |
| Window truncated by caps | Findings allowed, confidence capped, truncation flagged |
| Two sites at 45% each | `ONE_SITE_DOMINATING` does not fire; both appear in evidence |
| Exact tie for top path or IP | Deterministic tie-break by key |
| Site behind a CDN | `ONE_IP_DOMINATING` may fire; the false-positive note is part of the output |
| Missing timing fields | Cost-based reasoning is skipped, not approximated |
| Error log unreadable | Error-based rules cannot fire; the gap is stated in the incident |

## Safety constraints

- Rules read the snapshot only; no file, network or service access.
- No rule output may be phrased as an instruction to change the server. Findings
  describe; they never command.
- No finding triggers an action of any kind — no blocking, no restart, no
  configuration change (README §90).
- Evidence samples are bounded so an incident file cannot grow with the attack.

## CLI impact

Findings drive `diagnose`, `show` and the incident list
([SPEC-008](SPEC-008-cli-reporting.md)).

## Persistence impact

Findings are stored inside the incident file
([SPEC-006](SPEC-006-load-incident-detection.md)), including confidence and the
thresholds used.

---

## Acceptance criteria

- [ ] Each of the nine findings fires on its dedicated fixture.
- [ ] No finding fires on `normal-access.log`.
- [ ] No share-based finding fires below the minimum volume guard.
- [ ] Every finding carries evidence sufficient to justify it without the raw log.
- [ ] Thresholds are configurable and recorded in the incident.
- [ ] Replaying a fixture twice yields identical findings and confidences.
- [ ] Confidence is capped when the window was truncated.
- [ ] No rule output recommends or performs an action on the server.

## Verification

- One fixture per finding: `traffic-spike.log`, `one-ip-flood.log`,
  `404-flood.log`, `502-spike.log`, `upstream-timeout.error.log`,
  `php-fatal.error.log`, plus site- and URL-domination fixtures.
- A negative suite: every fixture is replayed against every rule; only the
  intended rules may fire.
- A determinism test: two replays, identical output.

## Out of scope

- `BOT_OR_CRAWLER_SPIKE`, `PHP_MEMORY_EXHAUSTED`, `PHP_EXECUTION_TIMEOUT` as
  standalone findings — Parking Lot.
- Baseline-relative detection — Parking Lot.
- Final threshold values — they require field data.
