# SPEC-005 — Traffic Aggregation

Status: Draft

Related: [README.md](../../README.md) §22, §24–§29, §58–§60, §69 ·
Backlog: AAD-022, AAD-023, AAD-024

---

## Problem

Individual requests are useless during an incident; shares and rates are what
identify a culprit. But an attack can produce millions of distinct URLs and IPs
in minutes, and a naive counter dictionary would make the monitor itself the
cause of the next outage.

## Goal

Maintain rolling aggregates over recent traffic, per site and per server, in
bounded memory, sufficient to answer "who dominated the last five minutes".

## Non-goals

- Retaining individual requests.
- Historical baselines or trend storage (Parking Lot, README §70).
- Exact counts for the long tail of high-cardinality dimensions.

## Current context

Nothing is implemented. Input is access and error records from
[SPEC-004](SPEC-004-nginx-log-parsing.md). Output feeds
[SPEC-006](SPEC-006-load-incident-detection.md),
[SPEC-007](SPEC-007-deterministic-rules.md) and `aadoctor top`.

---

## Functional requirements

### Moving windows

Maintained concurrently (README §22):

```text
10 seconds
1 minute
5 minutes
```

The primary incident window is 5 minutes, configurable through
`[logs] window_seconds`.

Windows roll on time, not on request count. A window contains records whose
parsed timestamp falls inside it; records arriving late by more than the window
length are counted in a `late` counter and dropped from windowed aggregates.

Implementation is bucketed: fixed-size time buckets (for example 10 s) summed
over the window length. This keeps roll-off cheap and memory predictable.

### Dimensions

Per window, counted per site and for the server as a whole (README §24):

```text
total requests
requests per site
requests per IP
requests per path
requests per status
requests per method
requests per user agent
```

Error-derived counters, per site and per server:

```text
upstream_timeout
fastcgi_error
php_error (by class)
nginx_resource_limit
```

### Status aggregation

Class counters `2xx / 3xx / 4xx / 5xx`, plus explicit counters for
(README §29):

```text
403 404 408 429 499 500 502 503 504
```

### Timing metrics

When `request_time` is present (README §69):

```text
request count
average request time
max request time
total request time
```

and the derived cost signal:

```text
total_cost ≈ requests × average_request_time
```

`upstream_response_time` is aggregated the same way when present. When the field
is absent, timing metrics are reported as unavailable, never as zero. A site
whose format lacks timings must not appear artificially cheap.

### Top-N

For each of path, IP and user agent, expose the top N with:

```text
key
count
share of the denominator
```

The denominator is explicit: server total for site ranking, site total for path
and IP ranking within a site. Shares are never computed against a truncated
subtotal.

### Cardinality limits

Per window and per dimension, tracked keys are capped (README §59). Starting
points, all configurable and all unvalidated:

```text
top paths per window        1000
top IPs per window          1000
top user agents per window   200
```

When the cap is reached, new keys are admitted only by displacing the weakest
tracked key, and displaced volume accumulates into an `other` bucket. `other` is
always reported alongside the top-N so a truncated tail is visible rather than
silently lost.

Consequence to accept explicitly: counts for keys near the cap are approximate.
Counts for dominant keys — the ones that matter for every rule in
[SPEC-007](SPEC-007-deterministic-rules.md) — remain accurate, which is the
property that justifies the cap.

### Query normalization

Paths are normalized before counting (README §60):

```text
/produto?id=1
/produto?id=2   →   /produto
/produto?id=3
```

Rules:

- The query string is stripped for the counted key.
- A bounded sample of raw query strings is retained per top path, as evidence.
- Further normalization of path segments — numeric ids, UUIDs, slugs — is
  **TBD**. The MVP normalizes the query only; segment normalization needs real
  data before it is worth the false-merge risk.

### Memory limits

Total aggregation memory is bounded by:

```text
sites × dimensions × caps × buckets
```

with a documented worst case. Exceeding an overall ceiling drops the least
valuable dimension first — user agent before IP, IP before path, path before
site — and logs the degradation. The monitor degrades its own resolution rather
than the server's stability.

---

## Technical behavior

- Counting is integer increments into dictionaries per bucket; no per-request
  objects are retained after counting.
- Bucket roll-off discards whole buckets; no per-key expiry scan.
- Iteration order of counters must not affect rule outcomes: ties are broken by
  a deterministic secondary key (the key string) so identical input yields
  identical top-N.
- Standard library only: `collections.Counter` / `dict`, `heapq` for top-N.
- When one log file serves several sites
  ([SPEC-002](SPEC-002-aapanel-discovery.md)), records are attributed to that
  file's site record and the ambiguity is flagged in the aggregate.

## Data structures

Per window snapshot, as consumed by rules and by `top`:

```text
window_seconds
started_at / ended_at
total_requests
sites:          name → count
per_site:       name → { paths, ips, user_agents, statuses, methods,
                         errors, timings, other }
server_statuses
server_errors
late_records
parser_errors
truncated:      per dimension, whether the cap was hit
```

## Edge cases

| Case | Behavior |
|---|---|
| Empty window | Counters zero; rules cannot fire on an empty denominator |
| Very low traffic | Shares are meaningless below a minimum volume; see SPEC-007 |
| Clock skew between servers | Not applicable: one host, one clock |
| Records older than the window | Counted as `late`, excluded from the window |
| Cardinality explosion | Cap engages, `other` grows, degradation logged |
| Timing fields absent | Timing metrics reported unavailable |
| Status absent or malformed | Excluded from status counters; malformed counter increments |
| One site producing 99% of volume | Normal operation; this is the signal, not an error |
| Shared log between sites | Attributed to the file's site, ambiguity flagged |

## Safety constraints

- Aggregation is in memory only. No aggregate is written under `/www/`.
- No request URLs, IPs or user agents are persisted outside an incident
  ([SPEC-006](SPEC-006-load-incident-detection.md)).
- No cookies, headers or bodies enter aggregation (README §61).
- Aggregation must not become a load source: bounded memory, bounded CPU per
  tick, no full scans.

## CLI impact

Feeds `aadoctor top` (README §41). The `other` bucket and any truncation flag
must be visible in that output.

## Persistence impact

None while running. A window snapshot is copied into an incident when one is
created.

---

## Acceptance criteria

- [ ] 10 s, 1 min and 5 min windows are maintained and roll on time.
- [ ] Counts per site, IP, path, status, method and user agent are correct on
      fixtures.
- [ ] Memory stays bounded when a fixture generates one million unique paths.
- [ ] Displaced keys appear in `other`; truncation is flagged.
- [ ] `/produto?id=N` collapses to one path key.
- [ ] Timing metrics are absent, not zero, when the format lacks them.
- [ ] Replaying the same fixture twice produces identical top-N, including tie
      order.

## Verification

- Fixtures: `normal-access.log`, `traffic-spike.log`, `one-ip-flood.log`,
  `404-flood.log`, plus a generated high-cardinality file.
- Memory assertion around the high-cardinality replay.
- Determinism check: two replays, identical snapshots.

## Out of scope

- Historical baselines and seasonality — Parking Lot.
- Path segment normalization beyond query stripping — TBD above.
- Bot classification from user agents — Parking Lot
  (`BOT_OR_CRAWLER_SPIKE`).
