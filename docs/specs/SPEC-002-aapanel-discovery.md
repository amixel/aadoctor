# SPEC-002 — aaPanel Discovery

Status: Draft

Related: [README.md](../../README.md) §15, §16, §39, §65, §80, §81 ·
[ADR-001](../adr/ADR-001-non-invasive-read-only-architecture.md) ·
[ADR-007](../adr/ADR-007-aapanel-nginx-only-mvp.md) ·
Backlog: AAD-003, AAD-010, AAD-011

---

## Problem

aaDoctor must know which sites exist and where each one writes its logs. Asking
the administrator to configure 48 sites by hand is unacceptable, and guessing
log paths from filenames breaks as soon as a vhost uses a custom path.

## Goal

Automatically derive, read-only and repeatedly, the mapping:

```text
site → access log → error log
```

from the aaPanel Nginx vhost configuration.

## Non-goals

- Reading the aaPanel internal database.
- Using the aaPanel HTTP API or credentials.
- Supporting Apache or OpenLiteSpeed ([ADR-007](../adr/ADR-007-aapanel-nginx-only-mvp.md)).
- Full Nginx configuration parsing, including `include` resolution across the
  whole tree.

## Current context

Nothing is implemented. Expected paths (README §15):

```text
/www/server/panel/              aaPanel installation
/www/server/panel/vhost/nginx/  per-site vhost files
/www/wwwlogs/                   usual log location
```

Log paths must come from the vhost directives, not from the convention
`<domain>.log` (README §15).

---

## Functional requirements

### Environment detection

1. aaPanel present: `/www/server/panel/` exists and is a directory.
2. Nginx vhost directory present and readable.
3. Log directory readable.

If aaPanel is absent:

```text
aaPanel not detected.

Expected:
 /www/server/panel/

No changes were made.
```

If Nginx is absent (for example an Apache-based aaPanel):

```text
Nginx not detected.

aaDoctor currently supports aaPanel + Nginx only.

No monitoring started.
```

No silent adaptation to another stack.

### Vhost parsing

For each file in the vhost directory, extract:

```nginx
server_name example.com www.example.com;
access_log  /www/wwwlogs/example.com.log;
error_log   /www/wwwlogs/example.com.error.log;
```

Rules:

- The first `server_name` token becomes the site's primary name; the rest are
  aliases mapped to the same site.
- `access_log off;` means the site has no access log — not a path named `off`.
- An `access_log` with a `log_format` argument is accepted; the format name is
  recorded when present, since [SPEC-004](SPEC-004-nginx-log-parsing.md) may
  need it.
- Relative paths are resolved against the Nginx prefix when it can be
  determined; otherwise the site is reported with an unresolved path warning.
- Commented lines are ignored.
- `server_name _;` or a missing `server_name` yields a site keyed by the vhost
  filename, marked as unnamed.

Parsing is line-oriented and tolerant. It is not a general Nginx parser and does
not need to resolve arbitrary `include` directives; a vhost whose logging
directives live in an included file is reported as incomplete rather than
guessed at.

### Periodic rediscovery

Discovery runs at startup and then every `[discovery] interval_seconds`
(default 60). A site created in aaPanel starts being monitored without a
daemon restart (README §16).

On each pass:

- New sites are added and start tailing from end of file
  ([SPEC-003](SPEC-003-incremental-log-monitoring.md)).
- Removed sites stop being tailed; their offsets may be dropped.
- A site whose log path changed is treated as a new file for offset purposes.

### Failure isolation

One bad site never stops the rest (README §65):

```text
48 sites found

47 monitored

1 ignored:
permission denied
```

Counts of found / monitored / ignored sites are exposed to `doctor` and
`status`.

---

## Technical behavior

- All file access is read-only: open for reading, `stat`, directory listing.
- Discovery never writes to the vhost directory, never creates lock files there,
  and never normalizes or rewrites a configuration file.
- Unchanged vhost files can be skipped on rediscovery using `mtime` and size, to
  keep the periodic pass cheap.
- Discovery results are held in memory; only the derived log offsets are
  persisted.

## Data structures

In-memory site record:

```text
name            primary server_name
aliases         remaining server_name values
vhost_path      source file
access_log      path or None
error_log       path or None
log_format      name when declared, else None
status          monitored | ignored
reason          when ignored
```

## Edge cases

| Case | Behavior |
|---|---|
| Vhost directory missing | Report as "Nginx not detected"; no monitoring |
| Vhost file unreadable | Skip, warn, continue |
| Malformed vhost | Skip, warn, continue |
| No `server_name` | Key by filename, mark unnamed |
| Duplicate `server_name` across vhosts | Keep both vhost paths, warn about ambiguity |
| Shared log file between sites | Attribution falls back to the log file; noted in the site record — see [SPEC-005](SPEC-005-traffic-aggregation.md) |
| `access_log off;` | Site has no access log; errors still monitored |
| Log path outside `/www/wwwlogs/` | Accepted; the path comes from the directive |
| Log file does not exist yet | Site kept, retried on the next pass |
| Symlinked log path | Followed for reading; never rewritten |
| Very large vhost directory | Discovery pass stays bounded; slow passes are logged |

## Safety constraints

Read-only throughout. No writes, no permission changes, no `chmod`, `chown` or
`setfacl` to gain access. If a log cannot be read, the site is reported as
ignored — the fix is the administrator's decision, not aaDoctor's.

## CLI impact

Feeds `doctor` (README §39) and the site counts in `status` (README §40).

## Persistence impact

None directly. Discovery output drives which files
[SPEC-003](SPEC-003-incremental-log-monitoring.md) tracks.

---

## Acceptance criteria

- [ ] Sites and log paths are derived from vhost directives, not filenames.
- [ ] A new aaPanel site is monitored within one discovery interval, without a
      restart.
- [ ] A malformed or unreadable vhost does not stop discovery.
- [ ] Absent aaPanel and absent Nginx produce the exact messages above and start
      no monitoring.
- [ ] `doctor` reports found / monitored / ignored counts with reasons.
- [ ] A filesystem audit after a discovery run shows no write under `/www/`.

## Verification

- Fixture vhost directory covering: normal site, multiple `server_name`,
  `access_log off`, custom log path, malformed file, unreadable file, missing
  `server_name`.
- Add and remove a fixture vhost while the discovery loop runs; confirm the site
  set converges within one interval.
- Run discovery against a read-only mounted fixture tree to prove no writes.

## Out of scope

- aaPanel database or API access.
- Resolving arbitrary `include` chains.
- Non-Nginx web servers.
