# SPEC-006 — Load Incident Detection

Status: Implemented

Implemented in `src/aadoctor/collectors/load.py` and
`src/aadoctor/analyzers/incidents.py`. See **Implementation notes** for the
decisions this spec left open and the two it got wrong.

Related: [README.md](../../README.md) §20, §21, §22, §36, §37, §63 ·
[ADR-003](../adr/ADR-003-filesystem-state-without-database.md) ·
Backlog: AAD-030, AAD-031, AAD-032, AAD-033, AAD-034

---

## Problem

By the time an administrator logs into the server, the spike is over. Something
must notice degradation as it happens and freeze the evidence of what the sites
were doing at that moment.

## Goal

Use load as a **temporal trigger** to capture a window of traffic evidence and
persist it as an incident.

**Load is a trigger, not a diagnosis** (README §21).

## Non-goals

- Explaining the cause. That is
  [SPEC-007](SPEC-007-deterministic-rules.md).
- Full system metrics: memory, disk, network, per-process CPU.
- Alerting, notification or remediation.

## Current context

Inputs: `/proc/loadavg`, CPU count, and window snapshots from
[SPEC-005](SPEC-005-traffic-aggregation.md).

---

## Functional requirements

### Load collection

Read `/proc/loadavg` every `[monitor] interval_seconds` (default 10) and keep
`load1`, `load5`, `load15`. Read the file directly; no subprocess (README §58).

### CPU count

Detect the usable CPU count once at startup. It must never be 0; a failed
detection falls back to 1 and logs it.

TBD: whether a container CPU quota (cgroup limit) should override the host CPU
count. Until decided, the host count is used.

### Load per core

```text
load_per_cpu = load1 / cpu_count
```

Compared against configuration (README §14):

```toml
[load]
trigger_per_cpu = 1.00
critical_per_cpu = 2.00
```

`trigger_per_cpu` opens an incident. `critical_per_cpu` marks it critical and
may raise the confidence ceiling in SPEC-007. Both are configurable; both are
starting points, not validated values.

### Trigger

A trigger fires when `load_per_cpu >= trigger_per_cpu` for a sustained number of
consecutive samples — default 2, configurable — so a single noisy sample does
not create an incident.

### Incident start

On trigger:

1. Record `started_at` and the system block.
2. Freeze the 5-minute window snapshot ending at the trigger time.
3. Create the incident. Every trigger produces one: the rules engine that this
   spec originally deferred to does not exist yet, and even once it does, an
   incident with no finding is itself useful information - it says the cause is
   not visible in the web logs.

### Analysis window

The window ends at the trigger and extends `window_seconds` backwards
(README §22):

```text
incident:  12:42:00
window:    12:37:00 → 12:42:00
```

Rationale: the traffic that caused the load precedes the load reading. Load
averages lag, so the window is deliberately anchored before the trigger rather
than centered on it.

### Cooldown and duplicate avoidance

- While load stays above the trigger, the open incident is **extended**, not
  duplicated: `ended_at` advances and peak values update.
- An incident closes when load falls below `recovery_per_cpu` (default 0.75)
  for a sustained period - default 3 samples, configurable. Closing on a lower
  number than opening is what stops an incident flickering; see the
  implementation notes.
- No separate cooldown is implemented: the hysteresis above already prevents
  the close-then-reopen cycle a cooldown would have absorbed.
- The incident id never changes once assigned.

### Persistence

Incidents are written to:

```text
/var/lib/aadoctor/incidents/2026-09-22T12-41-20.json
```

The record holds `id`, `status`, `severity`, the lifecycle timestamps, a
`system` block and a `traffic` block. It deliberately does **not** hold
README §37's `suspect`, `top_path`, `top_ip` or `findings`: those are
conclusions, and SPEC-007 adds them. The version is recorded in the file so a
later reader can adapt.

Writes happen on opening, on each new peak and on closing, so an incident that
is still running survives a crash as a record rather than disappearing.

Writes are atomic (temporary file plus rename). A partially written incident is
never readable.

### Retention

`[incidents] retention_days` (default 30). Expired incident files are deleted on
a periodic pass.

aaDoctor deletes **only its own files**. It never touches an aaPanel log
(README §63).

---

## Technical behavior

- The trigger evaluation is pure given the sample history, so it can be replayed
  from a fixture sequence of load values.
- Incident creation never blocks log reading; a slow write must not stall the
  monitor loop.
- If the incident directory is unwritable, the incident is logged and dropped,
  and the daemon continues.
- The system block records `load1`, `load5`, `load15`, `cpu_count` and
  `load_per_cpu` at the trigger, plus the peak values if the incident is
  extended.

## Implementation notes

Decisions taken while implementing this spec, including two places where the
spec as written was wrong.

**Hysteresis, not a cooldown.** The spec opened and closed on the same
threshold and added a cooldown to absorb the flapping that causes. Two
thresholds are simpler and better: an incident opens at `trigger_per_cpu` and
closes at `recovery_per_cpu`, so a load sitting on 1.0 cannot open and close an
incident every other poll. The cooldown was dropped.

**Every trigger opens an incident.** The spec made incident creation depend on
the rules engine producing a finding. That engine is SPEC-007 and does not
exist, and the dependency was backwards anyway: what is recorded should not
depend on what can currently be concluded from it.

**Severity is `high` or `critical`.** The spec called the lower level
`trigger`, which is not a severity. Both are a function of load per core and
nothing else - severity says how hard the server was pressed, never by what.
It rises and never falls: an incident that touched critical was a critical
incident.

**The traffic is frozen twice: at the opening and at each new peak.** This
turned out to matter more than expected. Load lags the traffic that caused it,
so at the moment an incident opens the burst may barely be visible - in the
verification scenario the opening snapshot holds 4,500 requests and the peak
snapshot 9,500, with the dominant path only in the second. Keeping just the
opening snapshot would have thrown away the evidence the whole feature exists
to preserve.

**Windows say how much data they cover.** A snapshot taken forty seconds after
the daemon started covers forty seconds, not five minutes. `coverage_seconds`
was added to SPEC-005's snapshot for this, and `aadoctor show` prints it. The
alternative - presenting a partial window as five minutes of evidence - would
mislead exactly when it matters.

**An interrupted incident is marked, not guessed at.** An incident still open
when the daemon stops gets `status: interrupted` on the next start. Its end time
is genuinely unknown; inventing one would put fiction in the file whose purpose
is to be trustworthy.

**Ids are second-resolution with a numeric suffix on collision.** The suffix is
only reached when two incidents open in the same second, which needs a clock
jump or a test.

**Retention deletes only names that match an incident id.** Anything else in
the directory - including `state.json` and `runtime.json`, which live one level
up but could be copied in - is never considered. The sweep runs at startup and
once a day, not on every poll.

**Load monitoring can be switched off.** With `[load] enabled = false`,
discovery, tailing, parsing, aggregation and `top` all continue; only the
trigger goes away.

## Data structures

Incident file, as implemented:

```text
schema_version     integer
id                 timestamp id, also the filename
status             open | closed | interrupted
severity           high | critical, from load alone
started_at         ISO 8601 with offset
peak_at            when the highest load was seen
ended_at           null while open
duration_seconds   present once closed
system             cpu_count, and a start and peak load block each
traffic            start and peak, each holding the SPEC-005 windows
```

Each traffic window carries its own `coverage_seconds` and `unparsed_ratio`, so
a reader can tell how much data is actually behind the numbers.

## Edge cases

| Case | Behavior |
|---|---|
| `/proc/loadavg` unreadable | Load correlation disabled with a warning; daemon continues |
| CPU count detection fails | Fall back to 1, log it |
| Load high, no traffic in the window | Incident created; its snapshot shows no traffic, which is itself the answer |
| Load high from a cron job or backup | Same as above; aaDoctor does not claim a web cause it cannot see |
| Sustained 6-hour spike | One extended incident, not 2160 incidents |
| Flapping load around the threshold | Two thresholds plus the sustained-sample requirement absorb it |
| Incident directory unwritable | Log and drop; no crash |
| Disk full | Same; retention pass may free space on the next cycle |
| Clock jump backwards | Incident ids may collide; a suffix disambiguates |
| Daemon restart during an incident | Marked `interrupted` on the next start; detection begins again |
| Aggregation truncated in the window | The window carries its own `other` volume and `unparsed_ratio` |
| Daemon started moments ago | `coverage_seconds` says how little data the window has |
| `[load] enabled = false` | No load is read and no incident is created; everything else runs |

## Safety constraints

- Read-only access to `/proc`.
- Writes confined to `/var/lib/aadoctor/incidents/`.
- Retention deletes only files aaDoctor created in its own directory.
- No service is inspected by restarting it, and none is restarted.
- Incidents contain aggregates and bounded evidence samples only — no raw log
  dumps, no cookies, headers or tokens (README §61).

## CLI impact

Incidents are the data behind `incidents`, `show`, `diagnose` and `explain`
([SPEC-008](SPEC-008-cli-reporting.md)).

## Persistence impact

Owns `/var/lib/aadoctor/incidents/`. Growth is bounded by retention and by the
cooldown that prevents incident storms.

---

## Acceptance criteria

- [x] `load_per_cpu` is computed from `/proc/loadavg` and the detected CPU count.
- [x] A single spiking sample does not create an incident; a sustained one does.
- [x] A sustained spike produces one incident, not many.
- [x] Hysteresis suppresses the flapping a cooldown was meant to absorb.
- [x] The window ends at the trigger and extends backwards, and says how much
      of itself it covers.
- [x] The incident file is written atomically, on open, on peak and on close.
- [x] Retention deletes only aaDoctor incident files older than the limit.
- [x] Load alone is never reported as a diagnosis: no incident record and no
      command output names a cause.

## Verification

`tests/test_load.py` and `tests/test_incidents.py`, with the clock and the
loadavg path injected so no real `/proc` and no waiting are involved:

- Load parsing: normal, high, integer and malformed lines; empty, missing and
  unreadable files; negative values rejected; CPU count of None or zero.
- Opening: quiet load, a single spike, a sustained one, the configured poll
  count, the counter resetting, and startup with load already high.
- Continuity: a continuous spike producing one incident; staying open between
  the two thresholds; an explicit flapping sequence (1.10 / 0.95 / 1.05 / 0.90)
  opening nothing.
- Peaks: rising, kept when load falls back, severity rising to critical and
  staying, and the peak time recorded.
- Closing: after sustained recovery only, duration from the timestamps, a
  second independent incident, and ids that do not collide within one second.
- Snapshots: the opening one stored, the peak one replacing it, none taken
  while nothing happens, and an incident recorded even without traffic data.
- Persistence: written on open, peak and close; a write failure logged not
  raised; a round trip; an interrupted incident marked on restart.
- Reading: empty and missing directories, newest first, the limit, files that
  are not incidents ignored, an unreadable one skipped, and an id containing a
  path refused before it can reach the filesystem.
- Retention: an old incident removed, a recent one kept, unknown files never
  touched, and `retention_days = 0` removing nothing.

The scenario this spec exists for, as an integration test and again against the
real daemon in a container: three sites, ordinary traffic, then a burst on one
path from mostly one address with 502s and upstream timeouts, while load runs
0.5 → 2.9 → 0.4. The result is **one** incident, peak 2.9 per core, whose peak
snapshot holds 8,000 requests for the busiest site, 5,000 for the busiest path,
4,620 for the busiest address, 120 responses of 502 and 40 upstream timeouts -
in a 16 KB file that names no cause.

## Planned extensions

Five Draft specs attach factual blocks to the incident record this spec owns.
Each is a new top-level key, none collides with an existing one, and
`schema_version` rises once when they land:

```text
resources        SPEC-010    memory, swap, CPU, PSI, disk at start/peak/end
processes        SPEC-011    bounded process tables at start/peak
php_fpm          SPEC-013    pool configuration, worker counts, log events
kernel_events    SPEC-014    OOM and kernel events inside the window
coverage         SPEC-015    what was observable when the incident opened
```

Two properties of this spec are explicitly preserved by all of them:

* **Load remains the only trigger.** No resource threshold, process state or
  kernel event opens an incident. A second trigger would multiply the incident
  population on a permanently pressed server, and the memory case that motivated
  those specs already raises the load.
* **The incident still records what was measured and names no cause.** Every new
  block is factual; reading it is `diagnose`
  ([SPEC-012](SPEC-012-system-deterministic-findings.md)).

There is one peak instant, the one this spec already tracks. No new spec defines
a second.

---

## Out of scope

- Cause attribution — [SPEC-007](SPEC-007-deterministic-rules.md).
- Memory, disk and network collectors.
- Notifications of any kind.
