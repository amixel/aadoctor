# SPEC-003 — Incremental Log Monitoring

Status: Draft

Related: [README.md](../../README.md) §17, §18, §19, §58, §65 ·
[ADR-005](../adr/ADR-005-incremental-log-reading.md) ·
[ADR-003](../adr/ADR-003-filesystem-state-without-database.md) ·
Backlog: AAD-012, AAD-013, AAD-014

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

Nothing is implemented. The file set comes from
[SPEC-002](SPEC-002-aapanel-discovery.md). State shape is fixed by README §18
and lives in `/var/lib/aadoctor/`.

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
A path that stays missing for longer than a configurable grace period is dropped
until the next discovery pass reintroduces it.

### Persistent state

Written to `/var/lib/aadoctor/state.json` (README §18):

```json
{
  "/www/wwwlogs/site1.com.log": {
    "inode": 1192281,
    "offset": 84477219
  },
  "/www/wwwlogs/site2.com.log": {
    "inode": 1192298,
    "offset": 1128829
  }
}
```

Rules:

- Write via temporary file plus atomic rename. A crash mid-write never leaves
  unreadable state.
- Flush on a configurable interval and on clean shutdown, not on every line.
- Unreadable or corrupt state is discarded with a log line; every file then
  restarts from end of file. Degrading to "lose recent history" is correct;
  degrading to "read everything" is not.
- Entries for files no longer discovered may be pruned.

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
- Files are opened, read and closed per poll, or kept open with a bounded
  descriptor pool — whichever keeps descriptor count sane across 100+ sites.
  TBD: which of the two, decided by measurement during AAD-012.
- Decoding is byte-oriented with permissive decoding; invalid UTF-8 never raises.

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

Owns `/var/lib/aadoctor/state.json`. The `offsets/` directory in README §12 is
reserved for a future per-file split if a single state file proves contentious;
the MVP uses the single file. TBD.

---

## Acceptance criteria

- [ ] A first-seen 14 GB fixture log produces zero parsed lines until it grows.
- [ ] After a restart, reading resumes at the stored offset with no duplicates
      and no gap.
- [ ] Rotation is detected by inode change and the new file is read from 0.
- [ ] Truncation is detected by `size < offset` and handled without a crash.
- [ ] A partial trailing line is never parsed as a complete line.
- [ ] Corrupt `state.json` results in tail-from-end, not a full read.
- [ ] One unreadable file does not stop the others.
- [ ] Total bytes read never exceeds bytes appended, plus a bounded rotation
      tail.

## Verification

Fixtures under `tests/fixtures/` driving a scripted sequence:

1. Append lines, poll, assert the parsed count.
2. Rotate (`mv` + create), poll, assert the new file is read from 0.
3. Truncate in place, poll, assert the offset reset.
4. Delete, poll, recreate, poll, assert recovery.
5. Kill mid-write of state, restart, assert state is readable.
6. Feed a 500 MB generated file on first sight, assert zero lines parsed.

All fixtures are copies. No real aaPanel log is ever rotated by a test.

## Out of scope

- Historical backfill of existing logs.
- inotify or other kernel notification mechanisms — polling is sufficient at a
  10 s interval and avoids watch-descriptor limits across 100+ files.
