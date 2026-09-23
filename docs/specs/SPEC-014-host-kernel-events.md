# SPEC-014 — Host / Kernel Events

Status: Draft

Related: [README.md](../../README.md) §30, §58, §61, §64 ·
[ADR-005](../adr/ADR-005-incremental-log-reading.md) ·
Backlog: AAD-110, AAD-111

---

## Problem

Some failures leave no trace in any log aaDoctor reads. The OOM killer takes a
PHP-FPM worker and the site returns 502; a filesystem goes read-only and every
write fails; a process segfaults; the connection tracking table fills and new
connections are dropped. In every case the web server logs a *symptom*, the
administrator sees 502s, and the sentence that explains it was written by the
kernel to a place aaDoctor has never looked.

[SPEC-010](SPEC-010-system-resource-monitoring.md) closes a sliver of this:
`/proc/vmstat`'s `oom_kill` counter proves an OOM happened. It does not say what
was killed, or when, or how much memory was involved. The difference between
"an OOM occurred during this incident" and "the OOM killer terminated a PHP-FPM
worker at 03:14, with 1.9 GB in use" is the difference between a hint and an
answer.

## Goal

Read kernel-level events incrementally and cheaply, classify a small set of
known kinds, and attach those falling inside an incident window to the incident.

**Facts only.** `OOM_EVENT` and everything else that draws a conclusion lives in
[SPEC-012](SPEC-012-system-deterministic-findings.md).

## Non-goals

- A general log collector. This is not a syslog reader, not a journal client and
  not a shipper.
- Security monitoring, intrusion detection or audit
  ([/CLAUDE.md](../../CLAUDE.md) §11 — aaDoctor is not a SIEM or an EDR).
- Reading the systemd journal's binary format.
- Running `dmesg` or `journalctl` in a loop (README §58).
- Changing `printk` levels, `dmesg_restrict`, or any kernel tunable.
- Interpreting kernel messages aaDoctor has no rule for. An unknown line is
  counted and dropped, never stored "just in case".

## Current context

aaDoctor already follows files incrementally by `(device, inode, offset)`,
handles rotation and truncation, and starts at the end of a file on first sight
([SPEC-003](SPEC-003-incremental-log-monitoring.md),
[ADR-005](../adr/ADR-005-incremental-log-reading.md)). Most of what this spec
needs already exists; what it mainly has to decide is **which source**, and that
decision is the substance of this document.

---

## Functional requirements

### Source selection

Four candidates were considered. The conclusion is that **no subprocess is
needed**, which was not obvious at the outset and is the single most important
result of this spec.

#### 1. `/dev/kmsg` — chosen, primary

A character device exposing the kernel ring buffer as readable records. Each
`read()` returns exactly one message:

```text
priority,sequence,timestamp_usec,flags;message text
```

Why it is the right source:

- **It is non-destructive and multi-reader.** Several processes can read it
  simultaneously and none consumes the buffer.
- **It supports positioning.** `lseek(fd, 0, SEEK_END)` positions after the last
  record, which is exactly the tail-from-end behaviour SPEC-003 already
  requires. `SEEK_SET` to 0 rewinds to the oldest record still held.
- **It is readable without a subprocess**, in non-blocking mode, returning
  `EAGAIN` when there is nothing new — so polling it costs one failed syscall
  when the machine is quiet.
- **It is the same content `dmesg` prints**, obtained without executing it.
- It is root-readable, and aaDoctor already runs as root (README §57).

Its failure mode is specific and must be handled: if records are overwritten
between two reads, the next read returns **`EPIPE`**. That is not an error but
information — messages were lost — and the response is to re-seek, record a gap,
and continue. A gap is reported in the coverage output rather than hidden, since
the lost records might have been the interesting ones.

#### 2. A plain text kernel log — chosen, fallback

`/var/log/kern.log`, `/var/log/messages` or `/var/log/syslog`, whichever exists.
On a system with rsyslog these carry the same messages, persist across reboots,
and are ordinary files that the **existing SPEC-003 reader handles with no new
code at all**.

Used when `/dev/kmsg` is unavailable — in a container, or where it has been
restricted. Where both exist, `/dev/kmsg` is preferred and the file is not
read, so no event is counted twice.

#### 3. `/proc/kmsg` — rejected

**Destructive.** Reading it consumes messages from the buffer, and only one
reader may hold it. An aaDoctor that opened it would quietly break `dmesg`,
`rsyslog` and anything else on the machine that expects to read kernel messages.

That is a side effect on the rest of the system, which is precisely what this
project promises not to have
([ADR-001](../adr/ADR-001-non-invasive-read-only-architecture.md)). It is
rejected on the non-invasive guarantee, not on convenience.

#### 4. `journalctl` / the systemd journal — rejected

`journalctl` means a subprocess, and to follow events it means a subprocess in a
loop or a long-lived child — both against README §58, and a fork every few
seconds on a machine already short of memory is a genuinely bad idea in exactly
the situation this spec exists to diagnose.

Parsing the journal's binary format directly would be a substantial,
version-sensitive implementation for content that `/dev/kmsg` already provides.

**The tradeoff, recorded:** on a journald-only system with no rsyslog and no
readable `/dev/kmsg`, aaDoctor sees no kernel events and says so. That is
accepted. If a real server is found in that state, the tradeoff is revisited
with evidence — and a subprocess, if it is ever justified, arrives through an
ADR and not through this spec.

### First read

**Seek to the end on first sight**, consistent with README §17 and SPEC-003.

The cost is stated plainly: an OOM that happened shortly before the daemon
started is not seen. The alternative — replaying the whole ring buffer at every
start — would emit a burst of stale events after each restart and each upgrade,
and events dated before the daemon was running have no incident to attach to.
The same reasoning produced the same answer for site logs, and consistency here
is worth more than the occasional missed event.

### Polling

`/dev/kmsg` is polled on the existing `[monitor] interval_seconds` tick: read
until `EAGAIN`, then stop. On an idle machine this is one syscall that returns
nothing.

There is no separate thread, no `select` loop and no blocking read. A quiet
kernel costs one failed read per ten seconds.

### Classification

Table-driven, in the style
[SPEC-004](SPEC-004-nginx-log-parsing.md) already uses — a new kind is one table
entry, not a new layer.

| Kind | Matched on | Extracted |
|---|---|---|
| `oom_kill` | `Out of memory: Killed process` / `oom-kill:` | victim name, pid, and the memory figures the kernel printed |
| `oom_invoked` | `invoked oom-killer` | the invoking process name |
| `process_killed_signal` | `Killed process` without an OOM prefix | process name, pid |
| `segfault` | `segfault at` / `general protection fault` | process name, pid |
| `hung_task` | `task ... blocked for more than N seconds` | process name, the duration |
| `filesystem_error` | `EXT4-fs error`, `XFS ... error`, `Remounting filesystem read-only` | device, mount when present |
| `io_error` | `Buffer I/O error on device`, `critical medium error` | device |
| `conntrack_full` | `nf_conntrack: table full, dropping packet` | — |
| `listen_queue_overflow` | `TCP: request_sock ... Possible SYN flooding` | port when present |
| `too_many_open_files` | `VFS: file-max limit ... reached` | — |

Everything else is counted as unclassified and discarded. The count itself is
reported, because a machine emitting thousands of unclassified kernel messages
is worth knowing about even when aaDoctor cannot read them.

`oom_kill` is the reason this spec exists and gets the most care: the kernel's
own line names the victim, its pid and the memory state at the moment of the
kill, and all three are preserved. Nothing is inferred beyond what the line
says — no guess at which site, no guess at why.

### Bounded retention

```text
MAX_EVENTS_RETAINED     200      a ring, oldest dropped first
MAX_MESSAGE_CHARS       200      truncated, and marked as truncated
MAX_EVENTS_PER_INCIDENT 50       per kind, with a count of what was dropped
```

A kernel that is emitting a message every millisecond must not be able to grow
aaDoctor's memory. When events are dropped, the count of dropped events is kept
— a truncated list that does not say it was truncated is a lie about the scale
of the problem.

---

## Technical behavior

- `/dev/kmsg` is opened `O_RDONLY | O_NONBLOCK` with `os.open`, read with
  `os.read`, and each read returns one whole record. Records are never split or
  joined.
- `EAGAIN` means nothing new — the normal case. `EPIPE` means records were
  overwritten: re-seek to the current position, record a gap, continue.
- `ENOENT`, `EACCES` or `EPERM` on open means the source is unavailable: fall
  back to a text file, or report the whole aspect as unavailable.
- Sequence numbers are used to detect gaps; timestamps are the kernel's
  monotonic microseconds since boot and are converted to wall-clock time using
  the boot time from `/proc/stat`'s `btime`. **The conversion is approximate**
  and is labelled as such; it is good to the second, which is all an incident
  window needs.
- The text-file fallback registers with the existing log reader and needs no new
  reading code.
- Classification is a pure function from a line to a kind, replayable from a
  fixture.
- Standard library only. No subprocess, no network.

## Performance constraints

- One non-blocking read per monitor tick on a quiet machine, returning `EAGAIN`.
- A burst is bounded: at most `MAX_EVENTS_RETAINED` records are kept, and
  reading stops at the end of the available records — it never loops waiting.
- Classification is a sequence of substring checks against a short table, over
  lines that are at most a few hundred bytes.
- Memory: 200 events at 200 characters is under 100 KB, fixed.
- Zero writes.

## Data structures

```text
KernelEvent
  at                 wall-clock time, converted from the kernel timestamp
  at_is_approximate  true — the conversion is derived from btime
  kernel_seconds     the kernel's own monotonic timestamp, kept verbatim
  priority
  kind               from the table above, or "unclassified"
  message            truncated to MAX_MESSAGE_CHARS
  truncated          bool
  process            victim or subject name, when the line names one
  pid                when the line names one
  device             for filesystem and I/O errors
  detail             a small dict of the numbers the line carried

KernelEventState
  source             "kmsg" | "file:/var/log/messages" | null
  available          bool
  gaps               count of EPIPE events — records that were lost
  unclassified_count
```

## Incident impact

The incident gains a `kernel_events` block holding the events whose timestamps
fall inside the incident window, bounded per kind:

```text
kernel_events
  source
  counts             per kind
  events[]           up to 50 per kind, oldest first
  dropped            how many were not kept
  gaps               records lost to buffer overwrite during the window
```

`gaps` is part of the record rather than a runtime detail, because a diagnosis
read six weeks later must be able to tell that some kernel messages from that
window were never seen.

## Edge cases

| Case | Behavior |
|---|---|
| `/dev/kmsg` absent (container) | Fall back to a text file; if none, aspect unavailable |
| `/dev/kmsg` unreadable (`dmesg_restrict`, non-root) | Same fallback path |
| `EPIPE` — records overwritten | Re-seek, count a gap, continue; the gap is reported |
| Both `/dev/kmsg` and a text log present | `/dev/kmsg` wins; no event is counted twice |
| Kernel message burst | Bounded ring; dropped count kept |
| A message longer than the cap | Truncated and marked truncated |
| `btime` unreadable | Timestamps kept as kernel-relative only; events are still usable within an incident, and the report says the wall-clock time is unknown |
| Clock stepped since boot | The conversion drifts; it is labelled approximate for this reason |
| OOM during an incident | Event attached; `OOM_EVENT` fires in SPEC-012 with the victim named |
| OOM before the daemon started | Not seen — first read is from the end. Stated as a limitation |
| Unknown message | Counted as unclassified, discarded |
| Reboot while running | New boot time; the sequence restarts and is detected as a gap rather than as time travel |

## Safety constraints

- **Read-only.** `/dev/kmsg` is opened `O_RDONLY`. `/proc/kmsg` is **never
  opened**, because reading it would consume messages other software depends on
  — a side effect on the rest of the system, which this project does not permit
  ([ADR-001](../adr/ADR-001-non-invasive-read-only-architecture.md)).
- No kernel tunable is changed: not `printk`, not `dmesg_restrict`, not
  `kernel.core_pattern`, not any `/proc/sys` path.
- No subprocess. `dmesg`, `journalctl` and `systemctl` are not executed.
- No network.
- **Kernel messages are untrusted text.** Any process can write to `/dev/kmsg`,
  so a message may be crafted. Extracted names, pids and device names are
  counted, compared and printed — never used to build a filesystem path, a shell
  command or an executable format string, and never used to select a file to
  open. This is the same rule SPEC-007 applies to URLs, and it matters more here
  because the text is easier to inject.
- Messages are truncated and bounded before storage, so a hostile flood cannot
  inflate an incident file.
- No credential, environment variable or command line is extracted from a kernel
  message even when one appears in it — only the fields the table names are
  kept (README §61).

## CLI impact

- `diagnose` surfaces kernel events as evidence under SPEC-012's system section;
  an OOM event is the strongest single piece of evidence this project can
  produce and is presented as such.
- `show` renders the stored `kernel_events` block as facts.
- `doctor` reports which source is in use, or that none is
  ([SPEC-015](SPEC-015-diagnostic-coverage-self-check.md)).
- **No `aadoctor dmesg` command.** The machine has `dmesg`.

## Persistence impact

Adds a `kernel_events` block to the incident record, bounded. The `/dev/kmsg`
read position is held in memory only and re-seeks to the end on start; it is
**not** stored in `state.json`, because a stored offset into a ring buffer that
has since wrapped is worse than no offset at all.

## Interactions with existing specs

- **[SPEC-003](SPEC-003-incremental-log-monitoring.md)** — reused unchanged for
  the text-file fallback; `/dev/kmsg` follows the same tail-from-end principle
  with its own positioning, since it is a ring buffer rather than a growing file.
- **[SPEC-004](SPEC-004-nginx-log-parsing.md)** — the same table-driven
  classification pattern, with its own namespace of kinds.
- **[SPEC-006](SPEC-006-load-incident-detection.md)** — events are attached to
  incidents by timestamp. **A kernel event does not open an incident**, for the
  reason SPEC-010 gives: load stays the only trigger. An OOM outside an incident
  window is therefore not recorded anywhere, which is a known cost and is listed
  below.
- **[SPEC-010](SPEC-010-system-resource-monitoring.md)** — its `oom_kill`
  counter and this spec's OOM events are two sources for one fact, at two levels
  of detail. SPEC-012 states which it had.
- **[SPEC-012](SPEC-012-system-deterministic-findings.md)** — owns `OOM_EVENT`
  and every other conclusion drawn from these events.
- **[SPEC-013](SPEC-013-php-fpm-pressure-and-pool-discovery.md)** — a PHP-FPM
  worker killed by the OOM killer appears here as `oom_kill` and in the PHP-FPM
  log as `child exited on signal 9`. Two independent sources for one event, and
  their agreement is the strongest corroboration available in this project.
- **[SPEC-015](SPEC-015-diagnostic-coverage-self-check.md)** — reports the
  source in use, and gaps.

## Known limitations and TBDs

- **Events before the daemon started are not seen.** First read is from the end,
  by design.
- **An event outside an incident window is not persisted.** A 04:00 OOM that did
  not raise the load above the trigger leaves no record. TBD: whether the last N
  events should be published in `runtime.json` so `status` can show a recent OOM
  regardless. Cheap and probably worth it; not decided here.
- **A journald-only host sees nothing.** Accepted tradeoff, recorded above.
- **Wall-clock conversion is approximate**, derived from `btime`, and drifts if
  the clock is stepped. Labelled, never presented as exact.
- **The classification table is written from documented kernel message formats
  and must be checked against real output** before it is trusted. The project
  has already shipped one pattern that matched nothing the real software writes;
  this table is larger and the same risk applies to every row.
- **`/dev/kmsg` in a container** usually shows the host's kernel buffer or
  nothing at all, so container verification cannot fully substitute for a real
  host here.

## Acceptance criteria

- [ ] `/dev/kmsg` is read incrementally, non-blocking, with no subprocess.
- [ ] The first read starts at the end of the buffer.
- [ ] `EAGAIN` is the quiet case and costs one syscall per tick.
- [ ] `EPIPE` produces a recorded gap and reading continues.
- [ ] `/proc/kmsg` is never opened — asserted by source inspection.
- [ ] A text-file fallback is used when `/dev/kmsg` is unavailable, and never
      alongside it.
- [ ] Each kind in the table is classified from a **real** kernel message, and
      an unknown message is counted and discarded.
- [ ] An OOM line yields victim name, pid and the kernel's memory figures.
- [ ] Events are bounded in count and length, and truncation is marked.
- [ ] Events are attached to an incident by timestamp, and `gaps` is recorded in
      the incident.
- [ ] A crafted kernel message containing a path or shell metacharacters is
      stored as text and never used to open a file or build a command.
- [ ] No kernel tunable is written during any run.

## Verification

- `tests/test_kernel_events.py` against a fixture that stands in for
  `/dev/kmsg`: normal records, a burst, an oversized message, an `EPIPE`
  sequence, a malformed record, a record with a hostile payload, and a
  `btime`-less host.
- The classification table tested against **real captured kernel output**,
  collected from a live Linux host and stored as a fixture. Not written from
  memory — this is the specific mistake the project has already made once, and a
  ten-row table is ten chances to repeat it.
- An injection test: a message containing `../../etc/passwd` and `; rm -rf /` is
  classified, stored and rendered, and nothing opens a file or builds a command
  from it.
- A container run producing a real OOM under a cgroup memory limit, confirming
  the event is captured, classified, attached to the incident, and corroborated
  by PHP-FPM's `signal 9` line when the victim is a PHP worker.
- A source-purity test in the existing style: no subprocess import, no
  `/proc/kmsg`.

## Out of scope

- `OOM_EVENT` and every other finding —
  [SPEC-012](SPEC-012-system-deterministic-findings.md).
- General syslog collection, forwarding or storage.
- The systemd journal — rejected above, with the tradeoff recorded.
- Audit, intrusion detection and security event monitoring.
- Publishing recent events for `status` — TBD above.
