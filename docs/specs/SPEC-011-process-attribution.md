# SPEC-011 — Process Attribution

Status: Draft

Related: [README.md](../../README.md) §57, §58, §61 ·
[ADR-002](../adr/ADR-002-python-standard-library-first.md) ·
ADR-009 (proposed) ·
Backlog: AAD-080 … AAD-082

---

## Problem

[SPEC-010](SPEC-010-system-resource-monitoring.md) can say the machine is out of
memory and thrashing. It cannot say **what was holding the memory**, and that is
the first question an administrator asks next. On the production server that
motivated this work the real answer was almost certainly "seventeen PHP-FPM
workers on a 2 GB machine", but aaDoctor had no way to state it, and an
administrator reading `MemAvailable: 74 MB` still has to go and run `top` — by
which time the incident is over, which is the exact failure this project exists
to prevent (README §1).

## Goal

When the server is under pressure, capture a **bounded** table of which
processes and which process families were consuming CPU, memory and I/O, and
freeze it into the incident.

**Facts only.** This spec observes. Deciding that a family is responsible for
anything is [SPEC-012](SPEC-012-system-deterministic-findings.md), and even
there it is constrained — see **Known limitations**.

## Non-goals

- Naming the site behind a process. In the general case this is **not derivable**
  and this spec does not pretend otherwise; see **Known limitations**.
- Killing, renicing, stopping, restarting or signalling any process, ever
  (README §4, §90).
- A continuous process monitor. The scan runs under pressure and not otherwise.
- Storing command lines (see **Privacy**).
- Per-thread data, open file descriptors, socket inodes, namespaces, cgroup
  membership, or an inventory of what is installed on the machine.
- `psutil` or any other dependency
  ([ADR-002](../adr/ADR-002-python-standard-library-first.md)).

## Current context

`/proc/<pid>/` gives everything needed as plain text, and aaDoctor already runs
as root (README §57), so the data is readable without changing a single
permission. The tool already knows how to read small kernel files on an interval
and how to turn two cumulative samples into a rate
([SPEC-010](SPEC-010-system-resource-monitoring.md)); this spec reuses both
rather than inventing a second way.

---

## Functional requirements

### Inputs and data sources

| Source | Fields read | Notes |
|---|---|---|
| `/proc` directory | numeric entries | the process list, via one `scandir` |
| `/proc/<pid>/stat` | `comm`, state, `ppid`, `utime`, `stime`, `starttime`, `rss` | one read gives almost everything |
| `/proc/<pid>/status` | `Uid`, `VmRSS` | read only for shortlisted processes |
| `/proc/<pid>/io` | `read_bytes`, `write_bytes` | root-only; absent on some kernels |
| `/proc/<pid>/cmdline` | read, **never stored** — see Privacy | shortlisted processes only |

Denominators — total memory and the total CPU delta — come from SPEC-010's
sample. This spec does not re-read `/proc/meminfo` or `/proc/stat`. One metric,
one reader, so the two can never disagree.

### Parsing `/proc/<pid>/stat` safely

The second field is `comm`, wrapped in parentheses, and **a process can put
anything in it**, including spaces and a closing parenthesis. Splitting the line
on whitespace is the classic bug. The field is located by the first `(` and the
**last** `)`, and everything after that is split positionally. A process named
`(evil) 1 2 3 (` must not be able to shift every subsequent field.

`comm` is at most 15 characters and is truncated by the kernel; that is
acceptable here, because it is used as a family label and not as an identity.

### When the scan runs

Not on every tick. A full scan is the most expensive thing in this document and
a quiet server must pay nothing for it.

```text
scan when  load_per_cpu >= recovery_per_cpu     (default 0.75)
at most    once per [system] process_scan_interval_seconds (default 30)
otherwise  not at all
```

The trigger deliberately reuses `recovery_per_cpu`, a threshold that already
exists and is already configured, and which sits *below* `trigger_per_cpu`. So
by the time an incident opens there is normally already a previous scan in hand,
which matters because CPU percentages need two.

Consequences, stated rather than hidden: the **first scan of a quiet-to-busy
transition carries memory and I/O but no CPU percentage**, and an incident
shorter than the scan interval may carry only one scan. Both are reported as
such. Inventing a CPU figure from a single sample would be the same mistake as
dividing by a zero baseline, which SPEC-007 already refused to make.

### Two-phase scan

A full read of every file for every process is wasteful, and most processes are
never interesting.

```text
phase 1   one read of /proc/<pid>/stat for every pid
          yields comm, ppid, state, starttime, rss, utime+stime
          cost: one open+read+close per process

phase 2   for the shortlist only — the union of the top N by CPU,
          top N by RSS and top N by I/O candidates —
          read status (uid), io (bytes), and cmdline for classification
```

With `top_n = 10` the shortlist is at most 30 processes, so phase 2 costs at
most 90 extra reads regardless of how many processes the machine runs.

### Rates and PID reuse

CPU usage per process is a rate over two scans:

```text
cpu_pct = (delta(utime + stime) / SC_CLK_TCK) / elapsed_seconds
```

expressed as a share of one CPU, so a process using four cores fully reads as
`400%` — matching what an administrator sees in `top`, because a number that
disagrees with the familiar tool is a number nobody trusts.

**A process is matched between scans by `(pid, starttime)`, never by pid alone.**
Linux reuses pids, and a new process inheriting an old one's counters would
produce a spectacular and entirely fictional CPU figure. A pid whose `starttime`
changed is treated as a new process with no rate.

I/O rates are computed the same way, from `read_bytes` and `write_bytes`, which
count actual block-device traffic rather than logical reads.

### Process families

A pid is rarely the useful unit — seventeen PHP-FPM workers are one fact, not
seventeen. Processes are grouped into a **family** derived from `comm` by an
explicit table:

```text
php-fpm      php-fpm
nginx        nginx
mysqld, mariadbd, mysqld_safe    mysql
redis-server                     redis
crond, cron                      cron
restic, borg, rsync, tar, gzip, xz, zstd, duplicity   backup
python, python3, BT-Panel, bt, bt_task                aapanel
sshd                             sshd
anything else                    its own comm
```

The table is **data, not inference**. A family is a label for grouping; it is
never evidence that the family is responsible for anything, and the word
"family" is used rather than "service" precisely to avoid implying ownership.

Family aggregates — process count, total RSS, total CPU, total I/O — are
computed over **every** process seen in phase 1, and only then is the per-process
table truncated to the top N. Otherwise "php-fpm: 780 MB" would silently mean
"the ten largest php-fpm workers", which is a different and much smaller number.

### Bounded snapshot

```text
top 10 by CPU
top 10 by RSS
top 10 by I/O            when /proc/<pid>/io is readable
all families             a server has tens, not thousands
MAX_FAMILIES = 64        beyond that, the smallest are folded into "other"
```

Ties break deterministically by pid, so two runs over the same data produce the
same table.

### Privacy

Command lines routinely contain secrets: a database password in a connection
string, an API token on a backup command, a DSN, a URL with credentials. A tool
that freezes them into a JSON file on disk has created a credential store nobody
asked for, and README §61 exists to prevent exactly this.

The rule is absolute and easy to audit:

```text
cmdline is read into memory for classification and is NEVER persisted,
never logged, never printed and never sent anywhere.
```

What is persisted instead:

- `comm` — the kernel's 15-character process name;
- the executable's **basename**, from `readlink("/proc/<pid>/exe")`, when
  readable — a path, never arguments;
- `uid`, and the user name when it resolves through `pwd`;
- for PHP-FPM only, a **pool label** extracted by a single anchored expression:

  ```text
  ^php-fpm: pool ([A-Za-z0-9._-]{1,32})$
  ```

  PHP-FPM rewrites its own argv to exactly this, and the pool name is the one
  piece of command line with diagnostic value. Anything that does not match the
  expression in full is discarded — there is no partial extraction, no
  truncation-and-keep and no redaction heuristic that could fail open.

No other process's arguments are extracted, under any pattern. Adding a second
extraction rule requires the same scrutiny as the first, and is a change to this
section, not an implementation detail.

---

## Technical behavior

- `os.scandir("/proc")` once; entries whose name is not all digits are skipped
  without a stat.
- A process that exits mid-scan raises `FileNotFoundError` or `ProcessLookupError`
  on the next read. That is **normal**, not an error: it is skipped silently and
  does not count as a failure.
- A process whose files cannot be read is skipped and counted; on a correctly
  running aaDoctor (root) this should be zero, and a non-zero count is reported
  as a coverage gap rather than hidden.
- No process is signalled. `os.kill(pid, 0)` is not used even for liveness
  checking — the read itself already establishes it.
- Scanning is pure given a fixture `/proc` tree, so the whole spec is testable
  from a directory of files with no real processes involved.
- Standard library only.

## Performance constraints

- **Budget: a full two-phase scan in under 150 ms** on a server with 400
  processes. Measured, not assumed.
- At most one scan per 30 seconds, and **none at all while load is below
  `recovery_per_cpu`**. On a healthy server this spec costs nothing.
- Phase 2 touches at most 30 processes however many are running.
- Memory: the previous scan is retained for rate computation as a compact map of
  `(pid, starttime) → (cpu_jiffies, io_bytes)`. On 400 processes this is a few
  tens of kilobytes; it is dropped when scanning stops.
- Zero writes. The snapshot is attached to the incident record that SPEC-006
  already writes.

## Data structures

```text
ProcessSnapshot
  taken_at                 ISO 8601 with offset
  scan_duration_ms
  process_count            total seen in phase 1
  unreadable_count         processes that could not be read
  has_cpu_rates            false on the first scan of a run
  io_available             whether /proc/<pid>/io was readable

  top_cpu[]     / top_memory[]  / top_io[]        at most 10 each
    pid, ppid, comm, exe_basename, uid, user
    pool                   php-fpm only, else null
    family
    cpu_pct                null when there is no previous scan
    rss_bytes, rss_ratio   ratio against SPEC-010's MemTotal
    read_bytes_per_sec, write_bytes_per_sec       null when unavailable
    started_at

  families[]
    name, process_count
    rss_bytes, rss_ratio
    cpu_pct                null when there is no previous scan
    read_bytes_per_sec, write_bytes_per_sec
```

There is no `cmdline` field. There is nowhere for one to go.

## Incident impact

The incident gains a `processes` block:

```text
processes
  start      the most recent scan at or before the incident opening
  peak       the most recent scan at or before the load peak
```

**There is deliberately no `end` scan**, unlike SPEC-010's `resources`. A
resource sample at the close is meaningful — it distinguishes "the pressure
ended" from "the incident closed while the machine was still struggling". A
process table at the close is a list of what is running *after* the event, which
answers no question anyone has.

If no scan was taken in time, the block or one of its keys is absent. Absent
means no scan, and a reader must not read it as an empty machine.

## Edge cases

| Case | Behavior |
|---|---|
| Process exits mid-scan | Skipped silently; not counted as a failure |
| PID reused between scans | `starttime` differs, treated as a new process, no rate |
| `comm` contains spaces or `)` | Parsed by first `(` and last `)`; field positions unaffected |
| `/proc/<pid>/io` unreadable | I/O columns null and `io_available` false; CPU and memory unaffected |
| Running as non-root | Other users' processes partially readable; the gap is counted and reported, not hidden |
| First scan of a run | `has_cpu_rates` false; memory and I/O still reported |
| Incident shorter than the scan interval | One scan or none; the block says which |
| Thousands of processes | Phase 1 cost grows linearly, phase 2 does not; the budget covers 400 and the scan is timed |
| Kernel thread (`[kworker]`) | Included, with zero RSS; grouped under its own comm |
| Zombie process | Recorded with its state; no rate, no RSS |
| A process named to imitate another | Only `comm` and the exe basename are reported; neither is treated as an identity claim |
| `/proc/<pid>/exe` unreadable | `exe_basename` null; `comm` still reported |

## Safety constraints

- **Read-only.** No signal, no `kill`, no `renice`, no `prlimit`, no write to
  any `/proc/<pid>/` file (several of them are writable as root — none is
  opened for writing).
- **No command line is persisted, logged or printed**, with the single audited
  exception of the PHP-FPM pool label above. This is the constraint most worth a
  dedicated test.
- Nothing under `/www` is read by this spec.
- `comm`, the exe basename and the pool label originate outside aaDoctor and are
  **untrusted strings**. They are counted, compared and printed — never used in
  a path, a shell command, an executable format string or `eval`. This is the
  same rule SPEC-007 applies to URLs and user agents, for the same reason.
- No output of this spec is phrased as an instruction to stop, restart or
  reconfigure anything.
- The snapshot contains no environment variables. `/proc/<pid>/environ` is
  **never opened** — it is where credentials actually live.

## CLI impact

- `diagnose` gains process evidence inside the system section
  ([SPEC-012](SPEC-012-system-deterministic-findings.md)); this spec adds no
  section of its own.
- `show` renders the stored `processes` block as facts, consistent with its
  contract of rendering the record and concluding nothing
  ([SPEC-008](SPEC-008-cli-reporting.md)).
- **No `aadoctor ps` or `aadoctor processes` command.** The machine already has
  `top`, `ps` and `htop`, they are better at it, and aaDoctor's value is the
  frozen table from the moment of the incident — not a live one.
- `doctor` reports whether process attribution is available
  ([SPEC-015](SPEC-015-diagnostic-coverage-self-check.md)).

## Persistence impact

Adds a `processes` block to the incident record. Bounded by construction: at
most 30 process rows and 64 family rows, each a fixed set of fields. No new
file, no new directory.

## Interactions with existing specs

- **[SPEC-006](SPEC-006-load-incident-detection.md)** — provides the incident
  lifecycle and the peak instant this spec attaches scans to. It gains no new
  trigger: a process consuming memory does not open an incident.
- **[SPEC-010](SPEC-010-system-resource-monitoring.md)** — provides the
  denominators. Strict division of labour: **SPEC-010 never reads
  `/proc/<pid>/`, and SPEC-011 never reads a host-wide aggregate.** That is the
  rule that keeps the same number from being measured twice in two places.
- **[SPEC-012](SPEC-012-system-deterministic-findings.md)** — the only consumer
  that draws conclusions. `PROCESS_CPU_DOMINATING` and
  `PROCESS_MEMORY_DOMINATING` are defined there, not here.
- **[SPEC-013](SPEC-013-php-fpm-pressure-and-pool-discovery.md)** — consumes the
  pool label to count workers per pool. That count, compared against
  `pm.max_children`, is how pool saturation is detected without touching a
  single PHP configuration file.
- **[SPEC-007](SPEC-007-deterministic-rules.md)** — unaffected. No HTTP finding
  reads a process.
- **ADR-009 (proposed)** — records the decision to observe processes at all and
  the command-line rule above.

## Known limitations and TBDs

- **A process cannot be attributed to a site, and on a default aaPanel it never
  will be.** aaPanel gives each *PHP version* a pool, shared by every site using
  that version — not a pool per site. So a PHP-FPM worker holding 200 MB was
  serving *some* site on PHP 7.4, and which one is genuinely not in the data.
  This is the single most important limitation in this document: it means
  "PHP-FPM is consuming the memory" is the honest ceiling of what process data
  can say, and the temptation to bridge the gap with a guess must be refused.
  See [SPEC-013](SPEC-013-php-fpm-pressure-and-pool-discovery.md) for how far
  the mapping can honestly go.
- **The scan is a sample, not a recording.** A process that ran for four seconds
  between two scans is invisible. A backup that starts and finishes inside one
  interval leaves no trace here — though it may leave one in
  [SPEC-014](SPEC-014-host-kernel-events.md) or in the I/O pressure of
  SPEC-010.
- **RSS double-counts shared memory.** Summing RSS across a family overstates it
  because workers share pages with their master; PSS in `/proc/<pid>/smaps_rollup`
  would be accurate but costs a much more expensive read per process. TBD, and
  until it is decided the evidence must say *resident, shared pages counted more
  than once* rather than presenting the sum as consumption.
- **`utime`/`stime` exclude a process's exited children**, which is correct for
  attribution and means a shell that spawns work is reported as idle.
- No thresholds are defined here; they live in SPEC-012.

## Acceptance criteria

- [ ] A scan over a fixture `/proc` tree produces the expected families and
      top-N tables, deterministically across repeated runs.
- [ ] A `comm` containing spaces and parentheses is parsed without shifting any
      other field.
- [ ] A pid reused between two scans produces no CPU rate.
- [ ] The first scan of a run reports `has_cpu_rates = false` and still reports
      memory.
- [ ] Family totals are computed over all processes, not over the truncated
      top-N table.
- [ ] **No command line appears anywhere** in the snapshot, the incident file,
      the log or any CLI output — asserted by a test that scans the serialized
      record for a planted secret.
- [ ] `/proc/<pid>/environ` is never opened — asserted by source inspection, in
      the manner of SPEC-007's existing import test.
- [ ] Only a string matching the anchored PHP-FPM pool pattern in full is kept
      from a command line.
- [ ] No scan occurs while load is below `recovery_per_cpu`.
- [ ] A full scan over 400 fixture processes completes within 150 ms.
- [ ] A process that disappears mid-scan does not raise and is not counted as
      unreadable.

## Verification

- `tests/test_processes.py` against a fixture `/proc` directory: a normal
  machine, a hostile `comm`, a zombie, a kernel thread, a pid reused between
  scans, a process removed between phase 1 and phase 2, and a tree with 400
  processes for the timing budget.
- A **privacy test** that plants `--password=hunter2` in a fixture `cmdline`,
  runs a scan, serializes the incident and asserts the string appears nowhere in
  the output, the record or the log.
- A **source test**, in the style of the existing `SourcePurity` test, asserting
  the module opens no path under `/proc/<pid>/environ` and imports nothing that
  can signal a process.
- An integration run in a container: start a known CPU-burning and a known
  memory-holding process, drive the load curve, and confirm the incident's
  `processes.peak` names both, with plausible figures and a family grouping that
  matches what `ps` reports for the same instant.

## Out of scope

- Site attribution — not derivable; see Known limitations.
- Any action on a process — forbidden (README §90).
- A live process view command — the system already has better ones.
- PSS / shared-memory-accurate accounting — TBD above.
- PHP-FPM pool configuration and saturation —
  [SPEC-013](SPEC-013-php-fpm-pressure-and-pool-discovery.md).
- Findings and thresholds —
  [SPEC-012](SPEC-012-system-deterministic-findings.md).
