# SPEC-003 — Incremental Log Monitoring

Status: Implemented

Related: [README.md](../../README.md) §17, §18, §19, §58, §65 ·
[ADR-005](../adr/ADR-005-incremental-log-reading.md) ·
[ADR-003](../adr/ADR-003-filesystem-state-without-database.md) ·
Backlog: AAD-012, AAD-013, AAD-014

Implemented in `src/aadoctor/collectors/logs.py`, with state handled by
`src/aadoctor/storage/json_store.py`. See **Implementation notes** for the
decisions this spec left open and the one case it cannot detect.

---

## Problem

A busy aaPanel server has access logs measured in gigabytes. Reading them
repeatedly would make the monitor the single largest source of I/O on the
machine — the exact problem it is supposed to diagnose.

## Goal

Read each tracked log file once, incrementally, surviving restarts, rotation and
truncation, at negligible cost.

**Fundamental rule: never read historical multi-GB logs by default.**

## Non-goals

- Backfilling history from existing logs.
- Indexing or copying log content.
- Guaranteed at-least-once delivery of every line across catastrophic failure.
  Losing a few lines after a crash is acceptable; re-reading a 14 GB file is not.

## Current context

The file set comes from [SPEC-002](SPEC-002-aapanel-discovery.md). State lives
in `/var/lib/aadoctor/state.json`, following README §18.

---

## Functional requirements

### Tracking key

Each file is tracked by:

```text
path
inode
offset
```

`path` identifies it in configuration; `(inode, offset)` identifies the actual
bytes already consumed. Size is read at each poll to detect truncation.

### First observation

When a file is seen for the first time and has no persisted state:

```text
offset = current end of file
```

Never offset 0. A 14 GB log contributes nothing until it grows.

### Steady state

On each monitor tick (`[monitor] interval_seconds`, default 10):

1. `stat` the path;
2. compare inode and size against the persisted state;
3. read from `offset` to the current end;
4. split complete lines; hold any partial trailing line;
5. advance `offset` by the bytes consumed into complete lines;
6. hand the lines to [SPEC-004](SPEC-004-nginx-log-parsing.md).

A partial trailing line is never emitted and never split across ticks.

### Restart

On daemon start, persisted state is loaded and each file resumes at its stored
offset, provided the inode still matches and the size is at least the offset.
Otherwise the rotation or truncation rules below apply.

### Rotation

```text
inode on disk != stored inode
```

means rotation. Behavior:

1. If the previous inode is still reachable through the rotated name and holds
   unread bytes, read that tail first when it is cheap to do so — bounded by a
   configurable maximum; otherwise skip it and log the gap.
2. Track the new inode from offset 0.
3. Persist the new `(inode, offset)`.

Reading the tail of the rotated file is best-effort. Correctness of the new file
takes priority over completeness of the old one.

### Truncation

```text
same inode and size < stored offset
```

means the file was truncated in place (for example `> file`). Reset `offset` to
0 and continue from the beginning of the now-small file.

### Deleted and recreated

Path missing at poll time: keep the file registered, log once, and retry on
following ticks. When it reappears with a new inode, treat it as rotation.
The watch set is owned by discovery: a path stays registered for as long as a
site declares it, with no separate grace period.

### Persistent state

Written to `/var/lib/aadoctor/state.json` (README §18):

```json
{
  "version": 1,
  "files": {
    "/www/wwwlogs/site1.com.log": {
      "device": 66304,
      "inode": 1192281,
      "offset": 84477219,
      "size": 84477219
    }
  }
}
```

`device` travels with `inode` because an inode number is only unique within a
filesystem. `version` allows the shape to change without a guessing game.

Rules:

- Write via temporary file plus atomic rename. A crash mid-write never leaves
  unreadable state.
- Flush on a configurable interval and on clean shutdown, not on every line.
- Unreadable or corrupt state is discarded with a log line; every file then
  restarts from end of file. Degrading to "lose recent history" is correct;
  degrading to "read everything" is not.
- Entries for files no longer discovered are kept, not pruned; see the
  implementation notes.

### Failure isolation

One file failing — permission denied, I/O error, vanished mid-read — never stops
the others (README §65). The failure is counted, logged once per state change,
and the file is retried.

---

## Technical behavior

- Reads use a bounded buffer per poll. A file that grew by 2 GB between polls is
  consumed in chunks, with a configurable per-tick ceiling so one noisy site
  cannot starve the others.
- No `tail`, `grep` or other subprocess in the read loop (README §58).
- Files are opened, read and closed per poll; see the implementation notes.
- Decoding is byte-oriented with permissive decoding; invalid UTF-8 never raises.

## Implementation notes

Decisions taken while implementing this spec. They extend it; the rotation-tail
item below replaces what the Rotation section proposed.

**The partial line is not stored anywhere.** The offset advances only to the end
of the last complete line, so trailing bytes without a newline are simply not
consumed and are re-read on the next poll. Nothing partial has to be remembered
in memory or persisted, and a restart mid-line resumes correctly by
construction. A run of bytes longer than `MAX_LINE_BYTES` (64 KiB) with no
newline is emitted truncated, so a malformed file cannot be re-read forever.

**Files are opened and closed per poll.** This settles the spec's open question.
Opening costs microseconds, and with a few hundred logs at a 10-second interval
it is nothing; keeping descriptors open would risk exhausting them and would
hold a stale descriptor across a rotation.

**A single `state.json`.** The other open question. `offsets/` stays reserved in
the installed layout and unused.

**The rotated file's tail is not read.** The spec allowed a best-effort attempt.
It was not implemented: after `mv site.log site.log.1` the old inode is only
reachable by guessing the rotated name, which varies by logrotate
configuration. The gap is bounded by one poll interval and is logged explicitly,
naming the offset that was not reached. Correctness of the new file takes
priority, as the spec required.

**A shrinking file is truncation, measured against the last observed size.**
Comparing only against the offset misses the case where the daemon was behind:
a 1 MB file we had read 200 KB of, replaced by a 500 KB file, is not an append.
The last observed size is stored for exactly this.

**Inode reuse is a known gap.** If the filesystem hands a recreated file the
same inode *and* the replacement is larger than the file it replaced, nothing
distinguishes it from an append, and lines will be misread until the next
rotation or truncation. Detecting it needs a content fingerprint, which is more
machinery than the case justifies today. Delete-and-recreate in practice
produces a smaller file, which the size check catches. **TBD** if a real server
ever shows the problem.

**Lines are delivered by callback, one at a time.** A `LogEvent` carries the
path, the log type, the raw line and the site names that reference that file -
plural, because two sites can point at one log, which is read once. This is the
seam [SPEC-004](SPEC-004-nginx-log-parsing.md) plugs into.

**State is written once per poll, and only when an offset moved.** No `fsync`:
losing the last write costs a few re-read lines, which is the at-least-once
behaviour the spec already accepts, and fsync every 10 seconds is I/O this
project exists to avoid (README §58).

**State for an unwatched file is kept, not pruned.** A site that flaps out of
discovery for one pass keeps its position. Entries are a few dozen bytes and
bounded by the number of distinct log paths the server has ever had.

## Data structures

In-memory per file, alongside the persisted fields:

```text
pending_partial   bytes of an incomplete trailing line
last_error        current failure state, for one-shot logging
read_bytes        counter, for diagnostics
missing_since     timestamp when the path first went missing
```

## Edge cases

| Case | Behavior |
|---|---|
| First run, huge log | Start at end of file |
| Restart with valid state | Resume at stored offset |
| Rotation between polls | Best-effort tail of the old inode, then new file from 0 |
| Truncation in place | Reset offset to 0 |
| Rotate + truncate in one interval | Inode check wins; treat as rotation |
| File replaced by a directory | Drop with an error; retry on rediscovery |
| Permission denied | Count, log once, retry; site reported as ignored |
| Partial line at end of file | Held until completed |
| Line longer than the buffer | Read across polls up to a max line length, then discard with a counter increment |
| Clock change on the host | Irrelevant: offsets are byte-based, not time-based |
| Log written by two sites | Lines are attributed to the file; see [SPEC-005](SPEC-005-traffic-aggregation.md) |
| Disk full on `/var/lib` | State write fails, logged; reading continues in memory |

## Safety constraints

- Log files are opened read-only. No write, no lock, no rename, no truncate.
- aaDoctor never participates in log rotation and never changes the existing
  rotation setup (README §19).
- Only `/var/lib/aadoctor/` is written.

## CLI impact

None directly. Feeds `top`, `diagnose` and incident creation.

## Persistence impact

Owns `/var/lib/aadoctor/state.json`, holding metadata only - never a line of
log content. The `offsets/` directory in README §12 stays reserved for a future
per-file split and is unused; the single file is the decision.

---

## Acceptance criteria

- [x] A first-seen large log produces zero lines until it grows.
- [x] After a restart, reading resumes at the stored offset with no duplicates
      and no gap.
- [x] Rotation is detected by inode change and the new file is read from 0.
- [x] Truncation is detected by a shrinking file and handled without a crash.
- [x] A partial trailing line is never parsed as a complete line.
- [x] Corrupt `state.json` results in tail-from-end, not a full read.
- [x] One unreadable file does not stop the others.
- [x] Total bytes read never exceeds bytes appended; no rotation tail is read.

## Verification

`tests/test_log_monitor.py` and `tests/test_storage.py`, in temporary
directories - no fixture files on disk, no aaPanel, no root:

- First observation of an 8 MB sparse file reads zero bytes; the following
  append is the only thing emitted.
- Append one line, several lines, nothing; offsets advance by bytes consumed.
- Partial line held back, completed on a later poll, and completed across a
  restart; over-long lines emitted truncated; CRLF handled.
- Restart resumes from the stored offset; state holds no log content.
- Corrupt state, wrong shape, malformed entry, negative offset - each degrades
  to tail-from-end.
- Rotation by rename, replacement, shrink while behind; truncation in place;
  rotation distinguished from a first observation.
- Missing file reported once, appearing later, deleted while others continue,
  recreated; permission denied and recovery.
- Invalid UTF-8; byte offsets unaffected by multi-byte characters.
- Bounded reads: a large append is chunked, reported as behind, and caught up
  on later polls; a line spanning chunks stays intact.
- Watch set from discovery: only configured logs, new site, removed site,
  changed path, shared log read once with both site names.
- Source check: the collector opens logs only with `"rb"` and contains no call
  that could modify one.

Also exercised end to end against a real daemon in a container: a 200,000-line
history was never read, rotation and truncation were detected live, a restart
resumed without re-reading, and no log line reached `/var/log/aadoctor`.

## Out of scope

- Historical backfill of existing logs.
- inotify or other kernel notification mechanisms — polling is sufficient at a
  10 s interval and avoids watch-descriptor limits across 100+ files.
