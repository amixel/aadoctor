# SPEC-005 — Traffic Aggregation

Status: Implemented

Implemented in `src/aadoctor/analyzers/traffic.py`, published for the CLI by
`src/aadoctor/runtime.py`. See **Implementation notes** for the decisions this
spec left open.

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

Input is access and error records from
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

The primary incident window is 5 minutes. Ten-second buckets are summed over
the requested window, so 1 minute is six buckets and 5 minutes is thirty; the
10-second figure is whatever one bucket holds.

Windows roll on time, not on request count. A record belongs to the bucket that
was current when it arrived - see the implementation notes on why arrival time
rather than the timestamp on the line.

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

Not aggregated: SPEC-004 does not parse `request_time` or
`upstream_response_time`, because the supported formats do not carry them and
guessing at an extra field would produce confident wrong numbers. When a format
that declares them is supported, the cost signal of README §69
(`requests × average_request_time`) belongs here. Until then a site whose
format lacks timings and one whose parser ignores them look the same, which is
honest.

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
top paths per window         1000
top IPs per window           1000
top user agents per window    200
top paths per site            200
top IPs per site              200
```

Key lengths are capped too - 512 characters for a path, 200 for a user agent -
and a truncated key is marked, so a cut-down path is never mistaken for a real
one. A multi-kilobyte URL must not become a dictionary key at that size.

When the cap is reached the table is cut back to its heaviest keys and the
dropped volume accumulates into an `other` bucket, reported alongside the top-N
so a truncated tail is visible rather than silently lost. See the
implementation notes for why the cut is done in batches rather than per
admission.

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
- No sample of raw query strings is kept: it would put request content in
  memory and in the published snapshot for little gain. The parser keeps the
  query on the event, which is where SPEC-007 can reach it.
- Further normalization of path segments — numeric ids, UUIDs, slugs — is
  **TBD**. The MVP normalizes the query only; segment normalization needs real
  data before it is worth the false-merge risk.

### Memory limits

Total aggregation memory is bounded by:

```text
sites × dimensions × caps × buckets
```

with a documented worst case. No dimension-dropping ladder is implemented: the
per-dimension caps above already bound the total, and a mechanism that silently
stops counting IPs would be worse than the memory it saves. Worst case is
roughly 30 buckets x (2,400 global keys + 430 per active site), which is a few
tens of megabytes under a sustained high-cardinality flood and far less in
normal use.

---

## Technical behavior

- Counting is integer increments into dictionaries per bucket; no per-request
  objects are retained after counting.
- Bucket roll-off discards whole buckets; no per-key expiry scan.
- Iteration order of counters must not affect rule outcomes: ties are broken by
  a deterministic secondary key (the key string) so identical input yields
  identical top-N.
- Standard library only: plain dictionaries, sorted when a snapshot is taken.
  Sorting never happens per event.
- When one log file serves several sites
  ([SPEC-002](SPEC-002-aapanel-discovery.md)), records are attributed to that
  file's site record and the ambiguity is flagged in the aggregate.

## Implementation notes

Decisions taken while implementing this spec.

**Buckets are indexed by arrival time.** Access timestamps are timezone-aware
and error timestamps are not (SPEC-004); bucketing by them would mean deciding
the server's timezone, and a wrong clock in a log line could push traffic into
the wrong window. Logs arrive within seconds of being written, so arrival time
answers "what is happening now" just as well. SPEC-006 may revisit this when it
correlates with load.

**Ten-second buckets, monotonic clock.** Six buckets make a minute, thirty make
five. A bucket that leaves the window is dropped whole, which is what keeps
memory tied to key count rather than request count. The monotonic clock is used
for bucketing so a system time adjustment cannot shift the window; the wall
clock is used only for the `updated_at` a human reads.

**Cardinality: keep the heaviest, not the first seen.** The obvious bound - stop
admitting new keys once the table is full - is the one an attacker defeats:
fill it with noise first and the path that matters never gets in. Instead a
table grows to twice its limit, then is cut back to the heaviest keys, with the
dropped volume added to `other`. Admission stays O(1) and the cut is amortised,
which matters because cardinality explodes exactly when the server is already
in trouble. Space-Saving proper would scan for the minimum on every admission:
with 100,000 unique keys in one bucket that is hundreds of millions of
operations, and aaDoctor would become part of the problem.

The cost of the simpler policy: a key that is cut loses what it had
accumulated. A key that is genuinely heavy survives every cut, which is the
property the diagnosis rests on, and there is a test for exactly that.

**Per-site tables, not a full cross-product.** Each site keeps its own paths,
IPs, statuses and error kinds, because "which IP is hitting which site" is the
question SPEC-007 has to answer. What is *not* kept is `site × ip × path`:
naming the exact pair behind a burst would need that, and the memory it costs
is not yet justified. **TBD** when SPEC-007 shows whether it needs it; the
per-site top-N usually makes the pair obvious to a reader.

**Caps are constructor arguments, not configuration.** `config.toml` holds only
keys that something reads, and nobody has needed to change these yet. They are
module constants, overridable when constructing the aggregator, which is what
the tests do. Promoting them to `[aggregation]` is a one-line change if a real
server ever needs it.

**Windows report their own coverage and quality.** `coverage_seconds` says how
much of the requested window the aggregator has actually been running for, and
`unparsed_ratio` how much of the traffic the parser could not read. Both were
added for SPEC-006, which freezes these windows into an incident: a five-minute
window forty seconds after startup covers forty seconds, and a reader - human
or SPEC-007 - has to be able to tell.

**Unparsed lines are counted, never as requests.** A line no parser matched is
not traffic. Counting it as one would inflate the numbers with the parser's own
blind spots. It is tracked per window and per site so `top` can say the figures
are incomplete instead of looking precise.

**Nothing is deduplicated.** SPEC-003 may re-read a few lines after a crash, so
counts can be slightly high for one window afterwards. Detecting duplicates
would mean remembering individual lines, which is the one thing this design
avoids.

**Aggregation is not persisted.** A restart starts with empty windows. The only
state that survives is the log offsets; incidents get their own persistence in
SPEC-006. Keeping windows across restarts would turn a bounded in-memory
counter into a small database for no diagnostic gain.

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

Implements `aadoctor top` (README §41), with `--window 1m|5m`, `--site NAME`
and `--json`. The `other` volume is shown, and so is the snapshot's age: a
snapshot from ten minutes ago describes ten minutes ago, and presenting it as
current would be a lie the reader cannot catch.

## Persistence impact

The aggregates themselves are never persisted; a restart starts empty.

The daemon publishes a bounded snapshot to `/var/lib/aadoctor/runtime.json`
once per poll, because `aadoctor top` runs in a different process and cannot
see the daemon's memory. It is written atomically and is not world-readable,
since it names sites, paths and client addresses. It holds aggregates only:
never a log line, never a query string, never a referer, and not the user agent
table - the longest and least diagnostic thing aggregation holds.

A socket or an HTTP port would have been the other way to answer the CLI. One
file, overwritten in place, needs no listener, no port, no protocol and no
permissions model of its own.

---

## Acceptance criteria

- [x] 1 min and 5 min windows are maintained and roll on time; 10 s is one
      bucket.
- [x] Counts per site, IP, path, status, method and user agent are correct.
- [x] Memory stays bounded under a flood of unique paths and of unique IPs.
- [x] Displaced volume appears in `other`; truncated keys are marked.
- [x] `/produto?id=N` collapses to one path key.
- [x] Timing metrics are absent, not zero - SPEC-004 does not parse them.
- [x] Two snapshots of the same data are identical, including tie order.
- [x] A flood of unique keys cannot push the dominant key out of the table.
- [x] Per-site paths, IPs, statuses and error kinds are attributed correctly.
- [x] A window empties on its own when no events arrive.
- [x] `aadoctor top` shows the snapshot and how old it is.

## Verification

`tests/test_traffic.py`, with the clock injected so windows and expiry are
tested by moving time rather than by sleeping:

- Counting by site, IP, path, status, status class, method and user agent;
  missing fields; a request with no status; the query excluded from the key.
- Errors by kind, level and site; an unclassified error counted as `other`.
- Unparsed lines counted apart from requests, globally and per site.
- One-minute and five-minute windows; data leaving each; a window that empties
  after an hour of silence; requests per second; bucket boundaries.
- Per-site paths, IPs, statuses and error kinds, with shares relative to the
  right denominator.
- Ties broken by key; two snapshots identical; a snapshot that cannot be used
  to disturb the aggregator.
- 50,000 unique paths and 20,000 unique IPs kept bounded; long paths and user
  agents truncated and marked.
- Heavy hitters: 10,000 one-off paths followed by the real one, noise
  interleaved with it, and a dominant IP inside an IP flood - the dominant key
  must still come first.
- 100,000 events counted exactly, with memory tracking keys rather than
  requests.
- A realistic scenario - ordinary traffic, then a burst on one path from mostly
  one address, with 502s and upstream timeouts - asserting every fact is
  visible and that the snapshot names no cause.

`tests/test_cli.py` covers `top`: totals, leaders, snapshot age, a stale
snapshot, both windows, `--site`, `--json`, an empty window, and the warning
shown when parser coverage is poor.

Also exercised end to end against the real daemon in a container, on three
sites with 9,500 requests: `top` showed the busiest site at 84.2%, its busiest
path, the address behind most of it, 120 responses of 502 and 40 upstream
timeouts - and `runtime.json` held 14 KB of aggregates with no log content.

## Out of scope

- Historical baselines and seasonality — Parking Lot.
- Path segment normalization beyond query stripping — TBD above.
- Bot classification from user agents — Parking Lot
  (`BOT_OR_CRAWLER_SPIKE`).
