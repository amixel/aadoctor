# SPEC-007 — Deterministic Rules

Status: Implemented

Implemented in `src/aadoctor/rules/` (facts, the nine rules, the confidence
scale) and `src/aadoctor/analyzers/diagnosis.py` (correlation), surfaced as
`aadoctor diagnose`. Thresholds live in `[rules]` in the configuration.

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

Input is the incident file written by
[SPEC-006](SPEC-006-load-incident-detection.md), which holds the frozen window
snapshots of [SPEC-005](SPEC-005-traffic-aggregation.md). Nothing else: no log
file is reopened, so an incident stays diagnosable long after its logs have
rotated away, and a rule that cannot reach the filesystem cannot become
non-deterministic by accident.

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

A rule reads the facts only. It performs no I/O, and it never reads the
result of another rule — combination happens after all rules have run. It
returns a finding, a `not_evaluable` reason, or nothing.

### Minimum volume guard

No share-based rule fires when its denominator is below `min_volume` (default
100 requests). Three requests from one IP is a 100% share and means nothing.
Below the guard the rule reports `below_minimum_volume` rather than simply
staying quiet.

### Thresholds

All thresholds live in the `[rules]` section of the configuration, which is
also where their defaults are defined — one source, so a tuned server and the
rules cannot drift apart. Nothing is hardcoded in a rule body, and the values
actually used travel with every diagnosis.

**The values below are starting points, not tuned values.** They will change
once real incidents are available. The section is commented out in
`config.example.toml`: it is documented so a value can be looked up, and left
unset so that an installation does not pin numbers that are going to move.

---

### TRAFFIC_SPIKE

```text
inputs      the 60s and 300s windows frozen at the same instant
condition   recent_rate >= spike_factor × earlier_rate AND
            recent_requests >= min_volume
evidence    both rates, both request counts, both durations, the ratio
confidence  grows with the ratio, capped at MEDIUM - there is no baseline
```

Starting point: `traffic_spike_factor = 3.0`.

**The comparison is within the incident, not against history.** An incident
freezes a short window and a long one at the same moment, so the long one
contains the short one. Subtracting gives a genuine preceding period without
keeping any history:

```text
recent_rate  = requests(60s)  / coverage(60s)
earlier_rate = (requests(300s) - requests(60s))
               / (coverage(300s) - coverage(60s))
```

Compared as **rates**. Comparing totals would measure one minute against up to
four and call every busy minute a spike — and would call a genuine spike a
slowdown whenever the earlier period simply had more time to accumulate in.

Two cases are named rather than computed around. When the preceding period is
shorter than 60 seconds the rule reports `insufficient_previous_window` and
does not fire. When the preceding period had no traffic at all the finding
fires with `comparison: from_idle` and a null ratio: traffic appearing out of
nothing is real, and dividing by zero to describe it is not.

False positives: a legitimate campaign, a scheduled job, a site launch. This
finding is weak alone and gains meaning combined with others.

A true historical baseline stays in the
[Parking Lot](../BACKLOG.md#parking-lot). Until it exists this finding is
capped at MEDIUM, whatever the ratio.

---

### ONE_SITE_DOMINATING

```text
inputs      requests per site, server total
condition   site_requests / total_requests >= site_share
evidence    site, requests, server total, share, threshold, second place
confidence  scales from the threshold to the ceiling, discounted by volume
```

Starting points: `site_share = 0.70` (README §71),
`site_share_ceiling = 0.85` — the share at which the evidence is as strong as
this measure can express.

A site named `-` is never the answer: it means the line did not carry the
field, and reporting it would be naming the gap in the data.

False positives: a server that legitimately hosts one dominant site among many
idle ones. Contrast with the second-place site makes this visible in the output.

---

### ONE_URL_DOMINATING

```text
inputs      requests per normalized path within the busiest site, site total
condition   path_requests / site_requests >= url_share
evidence    normalized path, requests, site requests, share, threshold, rate
confidence  scales with share and absolute count
```

Starting points: `url_share = 0.50`, `url_share_ceiling = 0.75`.

The site examined is the **busiest** one in the window, not the strongest
share found anywhere. A small site serving one endpoint would otherwise always
win this rule, and it is not what made the server slow. A dominant path on a
minor site is consequently not reported; see the notes at the end.

No raw query strings are kept. Aggregation counts the path without them
(SPEC-005), so there is nothing to sample — the evidence says so rather than
leaving the reader to assume otherwise.

False positives: a normal high-traffic endpoint — the homepage, a health check,
a legitimate API. Absolute rate matters as much as share; `/` at 60% of a small
site is unremarkable.

Shares are computed against the **site** total, never the server total.

---

### ONE_IP_DOMINATING

```text
inputs      requests per IP within the busiest site, site total, server total
condition   ip_requests / site_requests >= ip_share
evidence    IP, requests, share of the site, share of the server, threshold
confidence  scales with share and absolute count
```

Starting points: `ip_share = 0.50`, `ip_share_ceiling = 0.75`.

**Changed from README §71's 0.60.** That figure is an illustrative example
whose own text says the thresholds will be refined; at 0.60 an address holding
57.8% of a site's traffic is invisible, which is a concentration worth naming.
The guard against a false positive here is volume, not share — three requests
from one address is 100% and means nothing, and `min_volume` is what stops it.

Both proportions are kept when both exist: an address at 58% of one site and
49% of the whole server are two different facts, and each is worth having.

The evidence does **not** contain that address's top paths or user agents.
Aggregation holds `site → paths` and `site → ips` separately, never
`site × ip × path`, so that cross-product does not exist. Reporting it would
be inventing the one correlation the data does not contain.

False positives — important (README §27): a CDN or reverse proxy in front of the
site makes every request appear to come from one address; NAT concentrates many
real users; a legitimate integration polls from a fixed address. The output says
**possible bot, crawler, integration or flood**, never "attack", and never
recommends blocking.

---

### NOT_FOUND_FLOOD

```text
inputs      404 count, total requests, 4xx counts per site
condition   404_count / total >= not_found_share AND
            404_rate_per_sec >= not_found_min_rate
evidence    404 count, share, rate, both thresholds, 4xx counts per site
confidence  scales with the weaker of the two axes
```

Starting points: `not_found_share = 0.30`, `not_found_min_rate = 0.5/s`.

**The two conditions multiply, and that nearly disabled the rule.** Requiring
a share of `S` and a rate of `R` means the whole server has to be serving
`R / S` requests a second before this can fire at all. The 5/s this spec
originally specified silently required 16.7 req/s — more than most aaPanel
servers ever see. The first production server it met was doing 2.9 req/s with
82% of its requests returning 404 during a load spike, and the rule stayed
silent. The floor now comes from what a small server looks like.

A test asserts the property rather than the number: whatever the thresholds
become, `not_found_min_rate / not_found_share` has to stay reachable.

The **top 404 paths are not available**: statuses and paths are counted as
separate dimensions (SPEC-005), so which paths the 404s were for is genuinely
not in the data. The evidence says that in as many words rather than omitting
the field silently.

False positives: a vulnerability scanner producing harmless 404s at low cost — a
404 is usually cheap, so this finding explains noise more often than load. It is
reported as context unless paired with a load trigger and high volume.

---

### HTTP_5XX_SPIKE

```text
inputs      5xx counts, per code and per site
condition   5xx_count >= http_5xx_min_count AND
            (5xx_share >= http_5xx_share OR 5xx_rate >= http_5xx_min_rate)
evidence    counts per code (500/502/503/504), share, rate, affected sites
confidence  scales with whichever axis is proportionally stronger
```

Starting points: `http_5xx_share = 0.01`, `http_5xx_min_rate = 1/s`,
`http_5xx_min_count = 10`.

**Share changed from 0.05 to 0.01.** By the time one request in twenty is
failing the site is effectively down; a healthy server serves essentially no
5xx at all, so 1% is already a real signal. The absolute floor is what keeps
two errors on a quiet server from being called a spike — that job belongs to
`http_5xx_min_count`, not to a permissive share.

Either axis is enough. Three hundred failures a second matters on a busy
server even at a low share, and a high share matters on a quiet one even at a
low rate.

The finding is attributed to a site when one site holds at least half of the
5xx responses; below that they are spread, and naming one would be picking a
winner out of noise.

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
inputs      fastcgi_stderr, connect_failed, recv_failed, upstream_closed and
            no_live_upstreams events from the error log
condition   event_count >= fastcgi_error_min
evidence    count, threshold, counts per kind, affected sites
confidence  scales with count
```

Starting point: `fastcgi_error_min = 5` per window.

No message samples: aggregation keeps classifications, not lines, and the
incident file deliberately never contains raw log content (SPEC-006). The
counts per kind carry what the samples were for.

False positives: PHP-FPM restarted by the administrator; a pool being
reconfigured. aaDoctor reports the errors; it never restarts PHP-FPM.

---

### PHP_ERROR_SPIKE

```text
inputs      PHP error events by class (fatal, parse, memory exhausted,
            execution timeout, warning, notice)
condition   weighted_count >= php_error_min
evidence    counts per class, severe and minor totals, the weight used,
            threshold, affected sites
confidence  scales with the weighted count
```

Starting point: `php_error_min = 10` per window.

Weighted, not counted. A fatal, a parse error, an exhausted memory limit and a
blown execution time each count fully; a warning or a notice counts a quarter.
Without a baseline a chronically chatty site would otherwise look like a dying
one, and this is the cheapest honest defence against that.

No message samples, for the reason given under FASTCGI_ERROR.

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

#### How correlation actually decides

Each finding that names a site contributes points to that site:

```text
ONE_SITE_DOMINATING  3    HTTP_5XX_SPIKE    2    ONE_IP_DOMINATING  1
ONE_URL_DOMINATING   2    UPSTREAM_TIMEOUT  2    TRAFFIC_SPIKE      1
                          FASTCGI_ERROR     2    NOT_FOUND_FLOOD    1
                          PHP_ERROR_SPIKE   2
```

scaled by how strong that finding is, so one barely over its threshold does
not weigh the same as one that cleared it four times over:

```text
LOW 0.50 ×    MEDIUM 0.75 ×    HIGH 1.00 ×    VERY_HIGH 1.25 ×
```

The site with the most points wins, and its score is taken **net** of
everything pointing at other sites. Disagreement therefore lowers confidence
by construction rather than through a special case, and the losing side is
still named — as `error_site` or `traffic_site` — because "the traffic is
here, the failures are there" is more useful than either half alone.

```text
score >= 8  VERY_HIGH      score >= 2  MEDIUM
score >= 4  HIGH           score >= 1  LOW        below that: inconclusive
```

These are declared weights, not measurements. Nothing multiplies two
confidences together: that would produce a number that looks like statistics
and means nothing.

**A site also needs an anchor.** `ONE_URL_DOMINATING` and `ONE_IP_DOMINATING`
are shares *of a site*, so on their own they say nothing about that site's
weight on the server. At least one of `ONE_SITE_DOMINATING`,
`NOT_FOUND_FLOOD` or an error finding must point at the winner before it is
named. Without one the findings are still reported and the diagnosis stays
inconclusive. This is not theoretical: the container run produced exactly that
case — ten evenly loaded sites, one of which served a single endpoint, and
that site was being named as the suspect on 10% of the traffic.

### Confidence

Internally `0.00 → 1.00`, presented as (README §34):

```text
LOW        < 0.50
MEDIUM     0.50 – 0.74
HIGH       0.75 – 0.89
VERY_HIGH  >= 0.90
```

**Confidence is not a probability.** 0.95 does not mean "95% chance this
caused the outage". It is the strength of the evidence the rules found, on a
scale this project defines and nothing else. The word *probability* is avoided
everywhere, in the code and in the output, because it invites arithmetic that
means nothing.

One scoring function, in `rules/__init__.py`, used by every rule:

```text
strength(observed, threshold, ceiling)
    0.00 below the threshold
    0.60 exactly on it          - a finding that barely crosses is MEDIUM
    0.98 at the ceiling or past it, rising linearly between

volume_factor(count, minimum)
    0.75 at the minimum volume, reaching 1.00 at ten times it
```

Share-based rules multiply the two. Count-based rules use `strength` over the
ratio `count / threshold` and stop there: the count *is* the observed
quantity, and applying the volume factor on top would discount the same fact
twice.

Caps are **scoped to the input they damage**, not applied to everything:

| Damage | What it limits |
|---|---|
| Window coverage below 25% / 50% | every finding — it says how much of the window exists at all |
| Access lines unparsed above 25% / 50% | the access-log findings only |
| Error lines unparsed above 25% / 50% | the error-log findings only |
| A dimension pruned by its cardinality cap | the findings that read that dimension |

Three hundred upstream timeouts read cleanly from the error log are three
hundred timeouts whatever the access log looked like; capping them for a
problem elsewhere would punish evidence that is not damaged. Each finding
records which limits applied, under `limited_by`.

The diagnosis itself is capped only by coverage. The other limits already did
their work inside the findings, and the score inherits the result.

**AI never sets, raises or lowers confidence** (README §34,
[ADR-006](../adr/ADR-006-deterministic-engine-before-ai.md)).

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
  site          the site it points at, when one is identifiable
  path, ip      when the rule is about one
  evidence      rule-specific, bounded, serializable, threshold included
  limited_by    the quality caps that lowered it, named
  description   one line a human can read

not_evaluable:
  code          the rule that could not be decided
  reason        e.g. insufficient_previous_window, no_error_log_data

diagnosis:
  ruleset_version, conclusive, confidence, level, score
  primary_site, primary_path, associated_ip
  error_site, traffic_site        when the evidence points two ways
  findings[], not_evaluable[], evidence[], data_quality, thresholds
```

A rule that could not be decided is reported, never dropped. The absence of a
finding is ambiguous — the rule may have looked and seen nothing, or it may
never have had the data to look at all — and a reader who cannot tell the two
apart will read silence as evidence of calm. This is also what lets
[SPEC-009](SPEC-009-ai-explainer.md) say "there was not enough data to assess
a traffic rise" instead of implying there was none.

Recording the thresholds used matters: an incident from a server with custom
configuration must be interpretable later. So does `ruleset_version` — a
diagnosis is computed on demand, so an old incident is read with today's
rules, and which those were has to be visible.

## Edge cases

| Case | Behavior |
|---|---|
| Volume below the minimum guard | No share-based finding fires; `below_minimum_volume` is reported |
| Empty window | No findings; every rule reports why it could not be evaluated |
| No traffic at all in the incident | The diagnosis says the logs cannot answer |
| Window truncated by caps | Findings allowed, the affected ones capped, the truncation stated |
| Two sites at 45% each | `ONE_SITE_DOMINATING` does not fire |
| Exact tie for top path, IP or site | Deterministic tie-break by key, then by name |
| Site behind a CDN | `ONE_IP_DOMINATING` may fire; the CDN/NAT caveat is part of the finding |
| Error log unreadable or absent | Error rules report `no_error_log_data` — never read as an absence of errors |
| Only intra-site findings | No site is named; the findings are still listed |
| Volume below the guard and no finding at all | The summary says there was too little traffic to judge — **not** that the logs showed nothing |
| A critical load with a request rate that did not rise | The site is still named, and the summary says the request count does not account for the load |
| Findings pointing at different sites | Score taken net; both sites named; confidence falls |
| An incident still open | Diagnosed from the peak snapshot; the status is shown |
| An incident file from an older build | Read defensively; missing fields become absences, never exceptions |
| A hand-edited or corrupt incident | No rule raises; nothing is claimed from rubbish |

## Safety constraints

- Rules read the facts only; no file, network, subprocess or service access.
  A test asserts the two rule modules import nothing outside `typing`,
  `dataclasses` and aaDoctor itself.
- Site names, paths, addresses and user agents reaching these rules came from
  a log line and are untrusted. They are counted, compared and printed —
  never used in a path, a shell, an executable format string or `eval`.
- No rule output may be phrased as an instruction to change the server.
  Findings describe; they never command. A test checks the wording.
- A concentrated address is reported as concentration, with the CDN, proxy,
  NAT, integration and crawler caveat attached. Never as an attack, and never
  with a suggestion to block anything (README §27).
- No finding triggers an action of any kind — no blocking, no restart, no
  configuration change (README §90).
- Evidence is bounded by what the snapshot holds, which contains counts and
  never a line of log content.

## CLI impact

Findings drive `diagnose` ([SPEC-008](SPEC-008-cli-reporting.md)) and nothing
else. `show`, `incidents`, `top` and `status` stay factual: if a report that
presents measurements ever began to conclude, nobody could check the
conclusion against the numbers.

## Persistence impact

**None. A diagnosis is computed on demand and never written back.**

This reverses what this spec originally said — findings stored inside the
incident file — and diverges from the example schema in README §37, whose own
text says it may evolve. Three reasons:

* rules will change, and an incident frozen with an old opinion could never be
  re-read with a better one; on demand, `aadoctor diagnose <old-incident>`
  benefits from every improvement since;
* the incident file records **what was measured**, which is the guarantee
  SPEC-006 is built on. Putting a conclusion in it blurs the one distinction
  that makes the measurement trustworthy;
* nothing is lost. The snapshot is sufficient to reproduce the diagnosis
  exactly, and `ruleset_version` in the output says which rules produced it.

`show` renders facts; `diagnose` renders a reading of them. Keeping them in
separate commands over separate data is what lets anyone check the second
against the first.

---

## Acceptance criteria

- [x] Each of the nine findings fires on its dedicated fixture.
- [x] No finding fires on a quiet, evenly distributed server.
- [x] No share-based finding fires below the minimum volume guard.
- [x] Every finding carries evidence sufficient to justify it without the raw log.
- [x] Thresholds are configurable and recorded in the output.
- [x] Replaying a fixture twice yields identical findings and confidences.
- [x] Confidence is capped when the window was truncated.
- [x] No rule output recommends or performs an action on the server.

## Verification

Fixtures are **incident records**, not log files. SPEC-007 consumes structured
data and never reopens a log, so a fixture built from log lines would test
SPEC-004 and SPEC-005 a second time and this spec not at all. They live in
`tests/_scenarios.py` and are built with the same shape SPEC-006 writes.

- `tests/test_rules.py` — each rule at, just below and just above its
  threshold, at low volume, and with the data it needs missing. 49.9 / 50.0 /
  50.1 wherever a share threshold exists.
- `tests/test_diagnosis.py` — correlation, conflict, the anchor rule,
  determinism across replays and across the order of the record, and the three
  named scenarios.
- `tests/test_cli.py` — `diagnose` latest, by id, unknown id, `--json`,
  eighty columns, no writes, and `show` still factual.
- `tests/integration/diagnose.sh` — all three scenarios end to end against the
  **real daemon**: it discovers the sites, follows the logs, parses,
  aggregates, opens the incident from a driven load curve, and `diagnose`
  reads what it wrote. Only `/proc/loadavg` is substituted.

## Implementation notes

Where the implementation departed from this spec as drafted, and why. Each is
now described in place above.

| Change | Reason |
|---|---|
| Findings computed on demand, not persisted | rules evolve; the incident stays a measurement |
| `ip_share` 0.60 → 0.50 | 57.8% of a site from one address is worth naming; volume is the real guard |
| `http_5xx_share` 0.05 → 0.01 | one request in twenty failing means the site is already down |
| TRAFFIC_SPIKE compares the incident's own two windows, by rate | no history is kept, and comparing totals measures 60s against 240s |
| ONE_URL and ONE_IP look inside the busiest site | the strongest share anywhere would always be a small quiet site |
| Anchor requirement before a site is named | found by the container run: a 10%-of-traffic site was being named |
| Quality caps scoped per input | clean error-log evidence should not be punished for a dirty access log |
| Evidence score scaled by each finding's strength | a finding at the threshold is not worth one at four times it |
| Some evidence fields dropped | raw query strings, per-IP paths, 404 paths and message samples are not in the data |
| Two distinct inconclusive answers, worded differently | found in production: an incident opening seconds after the daemon starts has no traffic to judge, and saying "the logs show nothing" there is a claim the data does not support |
| A steady request rate is reported next to a critical load | found in production: twelve overnight incidents peaking at 20–44 per core with the request rate flat under 1.5/s. The rules can only rank what is in the logs, so on a quiet server they name a crawler or the busiest small site — and that reads as an explanation |

## Known limitations

- **No threshold has been validated against a real server.** Every number here
  is a starting point.
- A dominant path or address on a site that is *not* the busiest is not
  reported.
- `site × ip × path` does not exist, so the address is reported as
  *associated* with the site, never as the caller of the path.
- TRAFFIC_SPIKE needs at least 60 seconds of preceding data, so it is often
  `not_evaluable` on an incident that opens shortly after the daemon starts.
- Everything inherited from below: a custom `log_format` yields no traffic
  evidence at all, aggregates do not survive a restart, and counts can be
  slightly high after a crash.

## Planned extension

[SPEC-012](SPEC-012-system-deterministic-findings.md) (Draft) adds system-level
findings — memory, swap, CPU, I/O, disk, processes, PHP-FPM pools, OOM — on top
of this spec rather than beside it. What it reuses, unchanged:

* the scoring functions `strength()` and `volume_factor()`, and the level bands;
* the level multipliers used when weighting evidence;
* the `not_evaluable` mechanism, and the rule that a rule which could not be
  decided is reported rather than dropped;
* the **anchor principle** — there, a process family is named only when a
  host-level finding for the same resource also fired;
* the scoped-quality-cap principle, extended by
  [SPEC-015](SPEC-015-diagnostic-coverage-self-check.md) to observability.

What changes here: `ruleset_version` increments, the facts reader learns to read
the new incident blocks defensively, and the diagnosis gains a second domain.
**The site score defined in this spec is not altered, and the two domain scores
are never added** — a site and a resource are not comparable quantities, and one
number ranking them would mean nothing.

---

## Out of scope

- `BOT_OR_CRAWLER_SPIKE`, `PHP_MEMORY_EXHAUSTED`, `PHP_EXECUTION_TIMEOUT` as
  standalone findings — Parking Lot.
- Baseline-relative detection — Parking Lot.
- `site × ip × path` aggregation — Parking Lot, and only if real cases show it
  is needed.
- Final threshold values — they require field data.
