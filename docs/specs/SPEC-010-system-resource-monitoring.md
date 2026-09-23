# SPEC-010 — System Resource Monitoring

Status: Draft

Related: [README.md](../../README.md) §6, §20, §21, §58 ·
[ADR-002](../adr/ADR-002-python-standard-library-first.md) ·
[ADR-003](../adr/ADR-003-filesystem-state-without-database.md) ·
Backlog: AAD-070 … AAD-074

---

## Problem

The first production server ran aaDoctor for twelve hours and produced twenty
incidents, peaking at 43.6 load per core. The request rate was flat or falling
throughout. Every diagnosis came back inconclusive, and every one of them was
right: the cause was memory pressure with sustained swap — roughly 1 MB/s paged
in and out, continuously, for five days, on a machine with 2 GB of RAM serving
25 sites.

aaDoctor had no instrument that could see it. The only host signal it reads is
`/proc/loadavg`, and load is deliberately a trigger rather than a measurement of
anything in particular (README §21). So the tool could say *where not to look*
and nothing more.

That is the gap: **a load rise whose cause is a host resource is currently
invisible, and indistinguishable from a load rise whose cause is simply not in
the logs.**

## Goal

Sample a small, fixed set of host resource metrics on the monitor interval, in
bounded memory, and freeze them into the incident alongside the traffic windows.

**Facts only.** This spec measures. Reading the measurements is
[SPEC-012](SPEC-012-system-deterministic-findings.md).

## Non-goals

- Storing a time series. aaDoctor is not a metrics database (README §7).
- Anything per-process — that is
  [SPEC-011](SPEC-011-process-attribution.md).
- Findings, thresholds, confidence or any statement about what a number means.
- Graphs, exporters, scrape endpoints, alerting, notification.
- Network throughput, socket tables, connection counts, temperature, SMART.
  None of them is needed to explain the gap above, and a metric no finding
  reads is weight without value.
- Opening a second kind of incident. Load remains the only trigger; see
  **Interactions**.

## Current context

The daemon already reads `/proc/loadavg` every `[monitor] interval_seconds`
([SPEC-006](SPEC-006-load-incident-detection.md)). This spec adds sibling reads
on the same tick, from the same kind of source, with the same discipline: small
files, parsed directly, no subprocess (README §58).

README §6 already sanctions "algumas métricas simples de `/proc` para contexto".
This spec widens that from context to a measured input, and the README is
amended to say so rather than leaving the two documents to disagree.

---

## Functional requirements

### Inputs and data sources

| Source | Fields read | When absent |
|---|---|---|
| `/proc/meminfo` | `MemTotal`, `MemAvailable`, `MemFree`, `Buffers`, `Cached`, `SwapTotal`, `SwapFree` | `MemAvailable` predates kernel 3.14 and some vendor kernels backport it; its presence is **detected, never assumed**, and never estimated from free + cached |
| `/proc/vmstat` | `pswpin`, `pswpout`, `pgmajfault`, `oom_kill` | `oom_kill` needs kernel 4.13; absent means unavailable, not zero |
| `/proc/stat` | the aggregate `cpu` line: user, nice, system, idle, iowait, irq, softirq, steal | the file is universal; a short line yields only the fields present |
| `/proc/pressure/cpu` | `some` avg10 / avg60 / avg300 | PSI needs kernel 4.20 and `CONFIG_PSI=y`; a large share of aaPanel servers still run 3.10 vendor kernels and will report it unavailable |
| `/proc/pressure/memory` | `some` and `full` | as above |
| `/proc/pressure/io` | `some` and `full` | as above |
| `os.statvfs(path)` | block and inode totals and free counts | a path that cannot be stat'ed is dropped for a cool-off period |

Every source is optional. A source that cannot be read yields `None` for its
fields and does not fail the sample. **An unavailable source is never recorded
as a zero**: zero swap-out and no swap counter at all are different facts, and a
finding built on the first would be wrong.

### Which filesystems

Not all of them. Resolved once at startup, at most four paths:

```text
/                                      the root filesystem
the mount holding /www                 when it differs from /
the mount holding /var/lib/aadoctor    when it differs from both
the mount holding /var/log             when it differs from all three
```

Network mounts are included only if one of those paths lands on one. Nothing
walks `/proc/mounts` looking for more.

### Sampling

One `ResourceSample` per `[monitor] interval_seconds` (default 10), taken on the
same tick as the load reading, so a sample and a load value always describe the
same instant.

Filesystem statistics are sampled on a longer interval —
`[system] disk_interval_seconds`, default 60 — because they are the only source
here that can block (see **Performance constraints**) and the only one that
changes slowly.

### Rates versus cumulative counters

`pswpin`, `pswpout`, `pgmajfault`, `oom_kill` and every field of the `/proc/stat`
`cpu` line are **cumulative since boot**. A rate requires two samples:

```text
rate = (current - previous) / (current_monotonic - previous_monotonic)
```

Rules, all of which exist because the naive version produces a number that looks
plausible and is wrong:

- **The first sample after start produces no rates.** They are `None`, not zero.
  A daemon that has just started has not observed an interval.
- **`current < previous` means the counter reset** — a reboot, or a wrap. The
  interval is discarded, the rate is `None`, and the reason is recorded. No
  negative rate and no astronomical one is ever published.
- **An interval far longer than expected is discarded too.** A suspended VM or a
  stalled loop makes the elapsed time meaningless; if the gap exceeds four times
  the monitor interval, that interval's rates are `None`.
- **Page counters become bytes.** `pswpin` and `pswpout` count pages; the sample
  publishes `swap_in_bytes_per_sec` and `swap_out_bytes_per_sec`, converted with
  `os.sysconf("SC_PAGE_SIZE")`. A number whose unit has to be guessed is a
  number that will eventually be compared against the wrong threshold.
- **CPU percentages are shares of the CPU delta, not of wall time.**

  ```text
  iowait_pct = delta(iowait) / sum of the deltas of every field on the cpu line
  ```

  `steal` is kept as its own share. A VPS being starved by its host looks
  exactly like a busy server if steal is folded into the rest, and on shared
  hosting that mistake is easy to make and expensive.
- **PSI is used as the kernel publishes it.** The kernel already provides
  `avg10`, `avg60` and `avg300`; those are read and stored unchanged. Nothing is
  derived from the `total=` microsecond counter. Computing a second average from
  a field the kernel already averages would produce two numbers that disagree
  with each other for no reason a reader could resolve.

### Bounded retention

A ring of samples covering `[logs] window_seconds` (default 300) at the monitor
interval (default 10) — thirty samples.

```text
MAX_SAMPLES = 64        a hard cap, whatever the configuration says
```

If the configured window would need more than the cap, the retained samples are
spaced further apart rather than the cap being raised. One sample is a fixed set
of scalars, so the whole ring is a few tens of kilobytes and cannot grow with
traffic, with site count or with uptime.

No sample is written to disk outside an incident. There is no resource history
file, no rotation and no retention policy for one, because there is nothing to
retain (README §7,
[ADR-003](../adr/ADR-003-filesystem-state-without-database.md)).

### Publication

The most recent sample and its rates are added to the snapshot the daemon
already publishes to `/var/lib/aadoctor/runtime.json`
([SPEC-005](SPEC-005-traffic-aggregation.md),
[SPEC-008](SPEC-008-cli-reporting.md)), so `status` and `doctor` can read them
without interrogating the daemon. The ring itself is not published.

---

## Technical behavior

- Every read is a plain parse of text the kernel produced. No subprocess, no
  library, standard library only (README §58,
  [ADR-002](../adr/ADR-002-python-standard-library-first.md)).
- A malformed or unexpected line is skipped; the rest of the file is still
  parsed. A kernel that adds a field must not break the sample.
- Files are read whole with a single read; they are small — `/proc/meminfo` is
  about 1.4 KB. No line-by-line iteration over a large file, no glob, no walk.
- The resource sample happens on the same tick as the load reading, before the
  tail pass, and is strictly bounded, so it cannot delay log reading by more
  than its own budget.
- Rate computation is pure given two samples and two timestamps, so it is
  replayable from a fixture sequence.
- Timestamps for rates come from a monotonic clock, not the wall clock. An NTP
  correction during an incident must not invent a swap storm.

## Performance constraints

The monitor must never become the load it exists to explain (README §58).

- At most **eight file reads per monitor tick**, all under 8 KB.
- Filesystem statistics at most every 60 seconds, at most four paths.
- Budget: a complete resource sample in **under 5 ms** on an idle server,
  measured in verification rather than assumed.
- Memory: the sample ring is capped at 64 fixed-size records.
- **Zero additional writes.** The sample rides along with the `runtime.json`
  write that already happens each poll; no new file is opened for writing.

One accepted cost: `os.statvfs()` on a hung network mount blocks, and a blocked
call blocks the monitor loop. The mitigations are that only four paths are
sampled, only every sixty seconds, and that a path which has failed once is
skipped for a cool-off period. Introducing a thread, a watchdog thread or a
subprocess to avoid this would cost more than the problem — see **Known
limitations**.

## Data structures

```text
ResourceSample
  taken_at              ISO 8601 with offset
  monotonic             float, used only for rate arithmetic

  memory
    total_bytes, available_bytes, free_bytes, buffers_bytes, cached_bytes
    available_ratio               available / total, when available exists

  swap
    total_bytes, free_bytes, used_bytes, used_ratio
    in_bytes_per_sec, out_bytes_per_sec       null on the first sample
    major_faults_per_sec

  cpu
    user_pct, system_pct, idle_pct, iowait_pct, steal_pct, irq_pct
                                              null on the first sample

  pressure
    cpu_some, memory_some, memory_full, io_some, io_full
                each { avg10, avg60, avg300 } or null when PSI is absent

  filesystems[]                     at most four
    mount, total_bytes, free_bytes, used_ratio
    inodes_total, inodes_free, inodes_used_ratio

  oom_kill_total                    cumulative counter, null when unavailable
  oom_kill_delta                    since the previous sample

  unavailable[]                     source names that could not be read
  rate_skipped                      null, "first_sample", "counter_reset"
                                    or "interval_implausible"
```

Every field is either a number or `null`. There is no sentinel value, no `-1`
and no zero standing in for "unknown".

## Incident impact

The incident file gains a top-level `resources` block and its `schema_version`
rises:

```text
resources
  start      the sample at the instant the incident opened
  peak       the sample at the load peak
  end        the sample when the incident closed
```

- **`peak` is anchored to the load peak SPEC-006 already tracks.** This spec
  does not define a second notion of "peak". One incident, one peak instant, and
  the traffic window and the resource sample describe the same moment.
- **`end` is absent on an `interrupted` incident**, because there was no close.
  Absent — not zero, and not the last sample that happened to be seen.
- An incident from before this spec has no `resources` block at all. A reader
  treats its absence as absence of data, never as a machine under no pressure.

The block holds measurements only. No finding, no threshold comparison and no
word about what the numbers mean goes into the incident file. That separation is
the guarantee SPEC-006 is built on, and this spec does not weaken it.

## Edge cases

| Case | Behavior |
|---|---|
| `/proc/meminfo` unreadable | Memory fields null, source listed in `unavailable`, daemon continues |
| `MemAvailable` absent | Recorded as unavailable; never estimated from free + cached |
| PSI absent (kernel 3.10) | All pressure fields null; every other metric still sampled |
| `oom_kill` absent (kernel < 4.13) | Counter null; whether an OOM happened is then unknown, not false |
| No swap configured | `SwapTotal` is 0; used ratio null rather than a division by zero |
| First sample after start | All rates null, `rate_skipped = "first_sample"` |
| Counter smaller than before | Interval discarded, `rate_skipped = "counter_reset"` |
| Clock stepped by NTP | Rates use a monotonic clock and are unaffected |
| VM suspended and resumed | Interval implausible; that interval's rates discarded |
| `statvfs` on a hung mount | Blocks; that path is skipped for a cool-off period afterwards |
| Filesystem full | Sampled normally — this is exactly the case worth recording |
| Container with a cgroup memory limit | The host's numbers are reported; see Known limitations |
| Kernel adds a field to `/proc/stat` | Unknown fields count toward the denominator and are not reported individually |
| `/proc` not mounted | Every source unavailable; the daemon runs and says so |

## Safety constraints

- **Read-only on `/proc` and `/sys`.** Not one path under either is opened for
  writing, ever.
- `/proc/sys/vm/drop_caches` is **never opened, for any reason**. Nor is any
  other `/proc/sys` tunable written. aaDoctor does not tune (README §4, §90).
- No swap file or partition is created, resized, enabled or disabled.
- No service is restarted, reloaded, or inspected by restarting it
  ([/CLAUDE.md](../../CLAUDE.md) §8).
- Nothing under `/www` is opened by this spec. `os.statvfs("/www")` reads the
  filesystem's free-space counters and opens no file within it; it is a stat,
  and it is the only contact this spec has with that tree.
- No subprocess, no network, no database.
- The sample contains counters and ratios only. There is no path, no user name,
  no command line and no content of any kind in it, so there is nothing here to
  leak (README §61).

## CLI impact

- `status` gains a short resource line when a sample exists — memory available,
  swap used, and the swap rate when it is non-zero — and prints nothing at all
  when the daemon has published none.
- `top` is unchanged. It is about traffic, and mixing host metrics into it would
  blur the one command that is purely about the sites.
- `diagnose` is unchanged **by this spec**. It gains a system section in
  [SPEC-012](SPEC-012-system-deterministic-findings.md), which is where the
  numbers acquire a meaning.
- `doctor` reports which sources are readable. The shape of that report belongs
  to [SPEC-015](SPEC-015-diagnostic-coverage-self-check.md); this spec only
  supplies the per-source availability facts it renders.

## Persistence impact

Adds a `resources` block to the incident record and one object to
`runtime.json`. No new file, no new directory, no new retention rule.

## Interactions with existing specs

- **[SPEC-005](SPEC-005-traffic-aggregation.md)** — same bounded-window
  discipline, deliberately separate structure. Traffic windows and resource
  samples are never merged into one object: they have different shapes,
  different intervals and different reasons to be missing.
- **[SPEC-006](SPEC-006-load-incident-detection.md)** — owns the incident
  lifecycle and the peak instant. **A resource threshold does not open an
  incident.** Load stays the only trigger, for two reasons: a second trigger
  would multiply the incident population on a server that is permanently short
  of memory, and the case this spec exists for *already raised the load*. If
  field data later shows a resource event that load misses, that is a change to
  SPEC-006 with its own evidence, not a quiet addition here.
- **[SPEC-007](SPEC-007-deterministic-rules.md)** — gains a new fact source and
  no new rule. Its facts reader learns to read `resources` defensively, the same
  way it already tolerates a missing traffic field.
- **[SPEC-008](SPEC-008-cli-reporting.md)** — one new line in `status`.
- **[SPEC-011](SPEC-011-process-attribution.md)** — consumes this spec's sample
  for its denominators (total memory, total CPU delta) rather than re-reading
  `/proc/meminfo` and `/proc/stat` itself. One metric, one reader.
- **[SPEC-015](SPEC-015-diagnostic-coverage-self-check.md)** — the
  `unavailable[]` list is the raw material of the system half of the coverage
  report.

## Known limitations and TBDs

- **`os.statvfs` on a hung mount blocks the monitor loop.** Mitigated by
  interval, path count and a cool-off; not solved. Accepted deliberately over
  adding concurrency to a tool whose selling point is that it is small.
- **Container awareness is unresolved.** Inside a cgroup-limited container,
  `/proc/meminfo` reports the host's memory, so the available ratio is
  meaningless. This is the same class of problem as the CPU-count TBD already
  open in SPEC-006. TBD: read `/sys/fs/cgroup/memory.max` (v2) or
  `memory.limit_in_bytes` (v1) when present and prefer it. Not decided here,
  and deliberately not guessed at.
- **PSI will be unavailable on a large part of the target fleet.** aaPanel runs
  on CentOS 7 kernels, which have no PSI. Every memory, CPU and I/O finding in
  SPEC-012 must therefore be evaluable without it, with PSI raising confidence
  when present rather than being required.
- **Swap rate is a whole-machine number.** It says the machine is thrashing; it
  never says which process is responsible. That is SPEC-011, and even there the
  answer is bounded — see its limitations.
- **No thresholds are defined in this spec**, deliberately. Every number that
  decides something lives in SPEC-012, where it can be reviewed in one place.

## Acceptance criteria

- [ ] A sample is produced on every monitor tick, on a kernel with PSI and on
      one without, and neither case raises.
- [ ] An unavailable source produces `null` fields and an entry in
      `unavailable[]`, never a zero.
- [ ] The first sample after start publishes no rates.
- [ ] A counter that goes backwards produces a null rate and a recorded reason,
      never a negative or absurd value.
- [ ] Swap rates are published in bytes per second, derived from the page size.
- [ ] CPU shares are computed against the CPU delta and sum to approximately 1.
- [ ] PSI values are the kernel's own averages, unmodified.
- [ ] The sample ring never exceeds 64 records under any configuration.
- [ ] `resources.start`, `resources.peak` and `resources.end` appear in a closed
      incident; `end` is absent in an interrupted one.
- [ ] A full sample completes in under 5 ms on an idle test host.
- [ ] A filesystem audit over a sampling run shows zero writes outside
      `/var/lib/aadoctor/`.

## Verification

- **Fixture `/proc`.** A directory of files is injected in place of `/proc`, the
  technique SPEC-006 already uses for `loadavg`. Cases: a full modern kernel, a
  3.10 kernel with no PSI and no `oom_kill`, a machine with no swap, a truncated
  `/proc/stat`, a `meminfo` with an unknown extra field, and a file that raises
  `PermissionError`.
- **Rate arithmetic.** Two-sample sequences covering the normal case, the first
  sample, a counter reset, an implausible interval and a zero-length interval.
  The page-size conversion is asserted against a known page size rather than the
  host's.
- **Ring bounds.** A long run asserting the ring never exceeds the cap and that
  memory does not grow with the number of samples taken.
- **Incident integration.** The existing container scenario, extended: the
  incident it produces must carry `resources.start`, `resources.peak` and
  `resources.end`, with the peak sample taken at the same instant as the peak
  traffic window.
- **A real pressure run, in a container with a cgroup memory limit**: allocate
  until the kernel swaps, and confirm the samples show the swap rate rising and
  `MemAvailable` falling. This is the scenario the whole spec exists for, and a
  fixture cannot substitute for it — the first production server taught exactly
  that lesson.
- **Cost.** The 5 ms budget measured over a thousand samples.

## Out of scope

- Any interpretation of a number —
  [SPEC-012](SPEC-012-system-deterministic-findings.md).
- Per-process data — [SPEC-011](SPEC-011-process-attribution.md).
- Kernel and OOM event text — [SPEC-014](SPEC-014-host-kernel-events.md).
- Network, socket and connection metrics — not planned.
- Historical storage and trends — Parking Lot (README §70).
- cgroup-aware limits — TBD above.
