# SPEC-006 — Load Incident Detection

Status: Draft

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

Nothing is implemented. Inputs: `/proc/loadavg`, CPU count, and window
snapshots from [SPEC-005](SPEC-005-traffic-aggregation.md).

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
3. Hand the snapshot to the rules engine
   ([SPEC-007](SPEC-007-deterministic-rules.md)).
4. Create the incident if at least one finding is produced, or if load is
   critical — a critical incident with no findings is itself useful information:
   the cause is not visible in the web logs.

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
- An incident closes when load falls below the trigger for a sustained period —
  default 3 samples, configurable.
- After an incident closes, a cooldown — default 300 s, configurable — suppresses
  a new incident unless load reaches `critical_per_cpu`.
- Extension may update findings and evidence; the incident id never changes.

### Persistence

Incidents are written to:

```text
/var/lib/aadoctor/incidents/2026-09-22T12-41-20.json
```

Shape follows README §37: `id`, `started_at`, `system`, `traffic`, `suspect`,
`top_path`, `top_ip`, `errors`, `findings`. The schema may evolve before the
first stable release; the version is recorded in the file so readers can adapt.

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

## Data structures

Incident file, following README §37 with the additions noted:

```text
id                 timestamp id, also the filename
schema_version     integer
started_at         ISO 8601 with offset
ended_at           when the incident closed
severity           trigger | critical
system             cpu_count, load1, load5, load15, load_per_cpu, peaks
traffic            total_requests, window_seconds
suspect            site, requests, share
top_path           path, requests
top_ip             ip, requests
errors             upstream_timeout, http_502, ...
findings           list of { code, confidence, evidence }
truncated          whether aggregation caps engaged in this window
```

## Edge cases

| Case | Behavior |
|---|---|
| `/proc/loadavg` unreadable | Load correlation disabled with a warning; daemon continues |
| CPU count detection fails | Fall back to 1, log it |
| Load high, no traffic in the window | Incident created only if critical; findings empty; evidence states the logs show nothing |
| Load high from a cron job or backup | Same as above; aaDoctor does not claim a web cause it cannot see |
| Sustained 6-hour spike | One extended incident, not 2160 incidents |
| Flapping load around the threshold | Sustained-sample requirement plus cooldown absorb it |
| Incident directory unwritable | Log and drop; no crash |
| Disk full | Same; retention pass may free space on the next cycle |
| Clock jump backwards | Incident ids may collide; a suffix disambiguates |
| Daemon restart during an incident | The incident closes as written; a new one may open after restart |
| Aggregation truncated in the window | Flag set on the incident so confidence can be capped |

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

- [ ] `load_per_cpu` is computed from `/proc/loadavg` and the detected CPU count.
- [ ] A single spiking sample does not create an incident; a sustained one does.
- [ ] A sustained spike produces one extended incident, not many.
- [ ] Cooldown suppresses immediate re-triggering below critical.
- [ ] The analysis window ends at the trigger and extends `window_seconds` back.
- [ ] The incident file matches README §37 and is written atomically.
- [ ] Retention deletes only aaDoctor incident files older than the limit.
- [ ] Load alone is never reported as a diagnosis.

## Verification

- Replay a scripted load sequence (normal → spike → sustained → decay) against a
  fixture traffic stream; assert incident count, extension and cooldown.
- Kill the process mid-write; assert no unreadable incident file exists.
- Set `retention_days = 0` on a fixture directory containing a non-aaDoctor file;
  assert that file survives.

## Out of scope

- Cause attribution — [SPEC-007](SPEC-007-deterministic-rules.md).
- Memory, disk and network collectors.
- Notifications of any kind.
