# SPEC-002 — aaPanel Discovery

Status: Implemented

Related: [README.md](../../README.md) §15, §16, §39, §65, §80, §81 ·
[ADR-001](../adr/ADR-001-non-invasive-read-only-architecture.md) ·
[ADR-007](../adr/ADR-007-aapanel-nginx-only-mvp.md) ·
Backlog: AAD-003, AAD-010, AAD-011

Implemented in `src/aadoctor/discovery/`, verified against fixtures and a
synthetic aaPanel tree. See **Implementation notes** for the decisions this
spec did not settle in advance.

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

Expected paths (README §15):

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
- Relative paths are **not** resolved: the Nginx prefix is not guessed at. The
  log is recorded as `unresolved`, with the raw value kept and a warning
  raised. See the note below.
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

## Implementation notes

Decisions taken while implementing this spec. They extend it; nothing above is
replaced except the relative-path rule, which is marked in place.

**Statement-based parsing, not line-based.** Nginx statements end at `;`, not
at a line break, so a block body is split on `;` with `{` and `}` treated as
boundaries too. A whole vhost written on one line parses the same as an
indented one, a `server_name` continued across two lines is still one
directive, and a nested `location` block cannot glue itself to the directive
before it.

**Finding `server` blocks.** A single left-to-right scan tracks brace depth and
the word that opened each brace. aaPanel writes the brace on its own line:

```nginx
server
{
    ...
}
```

so matching a literal `server {` would find nothing.

**Merging blocks within one file.** Blocks that share a `server_name` are one
site — the common case being an HTTP block redirecting to an HTTPS one. A block
with no name of its own joins the first group, because one aaPanel file
describes one site. Blocks with different names stay apart: merging two real
sites would be worse than reporting two.

**Log precedence.** Ranked `configured` > `disabled` > `unresolved` > `absent`;
the first block that offers the better state wins. A second `access_log` with a
different path is ignored and reported, rather than merged under an invented
rule.

**`off` disables both directives.** `access_log off` is documented Nginx.
`error_log off` is not consistent across Nginx versions and may produce a file
literally named `off`; either way there is no log worth following, so both are
recorded as `disabled`. `/dev/null` is treated the same way.

**Relative paths are not resolved.** No Nginx prefix is guessed. The value is
kept as `unresolved` with a warning. **TBD:** resolve against the prefix if a
real aaPanel install is ever found using relative log paths — no evidence of
one so far.

**`include` is not followed.** A site with no log directive in its own file,
that contains an `include`, is warned about explicitly rather than guessed at.

**Missing semicolons are reported.** A log format, severity or buffer spec never
contains a slash. One that does means a missing `;` swallowed the next
directive, so the path is trusted and everything after it is discarded with a
warning. Unbalanced braces are reported the same way, and what could be read is
still returned.

**Canonical name.** The first `server_name` that is not `_`, a wildcard, a
regular expression or a variable. A vhost with none is named after its file
stem and marked `unnamed` — `0.default.conf` becomes `0.default`.

**Duplicates across files are never merged.** Two vhosts claiming the same name
produce two sites and one warning.

**Which files are read.** Regular files ending in `.conf`. A `.conf` file with
no `server` block yields no site and no warning: aaPanel keeps helper snippets
in the same directory.

## Data structures

In-memory site record, as implemented:

```text
name             canonical server_name, or the file stem
server_names     every name declared, in order, deduplicated
aliases          server_names minus the canonical one
config_path      source file
access / error   LogTarget
unnamed          True when named after its file
warnings         per-site, human-readable
```

`LogTarget`:

```text
state        configured | disabled | unresolved | absent
path         Path when configured, else None
raw          the literal token, kept for debugging
log_format   access_log's format argument when declared
exists       whether the file is on disk (None when not checked)
readable     whether it can be opened (None when not checked)
```

Configured and existing are separate questions: a site whose log has not been
written yet is still a valid site.

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

Feeds `doctor` (README §39) and the site counts in `status` (README §40), and
adds `aadoctor sites` — a table of site, access log and error log, with
`--json` for machine use and `--vhost-dir` to read a directory other than
aaPanel's. See [SPEC-008](SPEC-008-cli-reporting.md).

## Persistence impact

None directly. Discovery output drives which files
[SPEC-003](SPEC-003-incremental-log-monitoring.md) tracks.

---

## Acceptance criteria

- [x] Sites and log paths are derived from vhost directives, not filenames.
- [x] A new aaPanel site appears within one discovery interval, without a
      restart.
- [x] A malformed or unreadable vhost does not stop discovery.
- [x] Absent aaPanel and absent Nginx are reported and start no monitoring.
- [x] `doctor` reports site and log counts, with the reasons behind them.
- [x] A filesystem audit after a discovery run shows no write under `/www/`.

## Verification

Automated, in a disposable Linux container:

- `tests/test_discovery.py` — 12 committed vhost fixtures plus temporary
  directories: normal site, several `server_name`, commented directives,
  `access_log off`, `/dev/null`, log format argument, error severity, absent
  directives, relative path, duplicate directives, HTTP/HTTPS block pairs,
  catch-all naming, helper file with no `server` block, unreadable file,
  parser failure, duplicate names across files, configured-but-absent log
  files, deterministic ordering, and mtimes unchanged after a pass.
- `tests/test_cli.py` — the `sites` table and its JSON form.
- A synthetic `/www/server/panel/vhost/nginx` tree driven through `doctor`,
  `status`, `sites` and the daemon: a vhost added while the daemon ran was
  picked up on the next pass and logged, and removing it was logged too.
  Checksums and permissions under `/www` were identical before and after.

Not yet verified: a real aaPanel installation. The fixtures follow aaPanel's
formatting conventions but were written by hand.

## Out of scope

- aaPanel database or API access.
- Resolving arbitrary `include` chains.
- Non-Nginx web servers.
