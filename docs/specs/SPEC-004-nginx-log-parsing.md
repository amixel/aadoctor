# SPEC-004 — Nginx Log Parsing

Status: Draft

Related: [README.md](../../README.md) §23, §29, §30, §31, §66, §67, §69 ·
[ADR-002](../adr/ADR-002-python-standard-library-first.md) ·
Backlog: AAD-020, AAD-021

---

## Problem

Log lines arrive from dozens of sites in a format the administrator may have
customized. A parser that raises on the first unexpected line takes the daemon
down during exactly the incident it exists to diagnose.

## Goal

Turn raw access and error log lines into structured records, resiliently, with
optional fields treated as optional.

**A bad line must never bring down the daemon.**

## Non-goals

- Supporting arbitrary user-defined `log_format` by parsing the Nginx
  configuration. The aaPanel default plus common variants is the target.
- Reconstructing full request context: no headers, no bodies, no cookies.
- Changing `log_format` to get richer data (README §68).

## Current context

Nothing is implemented. Input is complete lines from
[SPEC-003](SPEC-003-incremental-log-monitoring.md), each already attributed to a
site by its source file.

---

## Functional requirements

### Access log fields

Extract when the format allows (README §23):

```text
timestamp
ip
method
path
query
status
bytes
referer
user_agent
request_time              when present
upstream_response_time    when present
```

Rules:

- Missing optional fields are absent, never zero-filled and never invented.
- `path` and `query` are split at the first `?`.
- The timestamp is parsed with its timezone offset and normalized internally;
  when unparseable, the record keeps the line's arrival time and is flagged.
- `-` means absent for referer, user agent and timing fields.
- The status must be a three-digit number; otherwise the record is counted as
  malformed.

### Error log classification

Classify against a table of known patterns (README §30, §31):

| Pattern | Class |
|---|---|
| `upstream timed out` | `UPSTREAM_TIMEOUT` |
| `FastCGI sent in stderr` | `FASTCGI_ERROR` |
| `connect() failed` | `FASTCGI_ERROR` |
| `upstream prematurely closed connection` | `FASTCGI_ERROR` |
| `recv() failed` | `FASTCGI_ERROR` |
| `worker_connections are not enough` | `NGINX_RESOURCE_LIMIT` |
| `too many open files` | `NGINX_RESOURCE_LIMIT` |
| `PHP Fatal error` | `PHP_FATAL_ERROR` |
| `Allowed memory size exhausted` | `PHP_MEMORY_EXHAUSTED` |
| `Maximum execution time exceeded` | `PHP_EXECUTION_TIMEOUT` |
| `PHP Warning` / `PHP Parse error` | `PHP_ERROR` |

The table is data, not code branches: adding a pattern is one entry.

Where the line carries them, also extract severity, client IP, server name,
request line and upstream. These are evidence for
[SPEC-007](SPEC-007-deterministic-rules.md).

### Unknown lines

An unrecognized or unparseable line:

```text
ignore line
increment parser_error_count
continue
```

Counters are per file and per kind (`access_malformed`, `error_unknown`). A
bounded sample of distinct unknown lines may be retained for diagnostics, with
no unbounded growth.

If the malformed ratio for a file exceeds a threshold over a window, log a
single warning suggesting a format mismatch. Never attempt to auto-detect and
switch formats silently.

### Multi-line errors

PHP stack traces span lines. Continuation lines — those not starting with a
timestamp — attach to the preceding event and do not create new events. A
continuation buffer is bounded; beyond the bound, excess lines are dropped with
a counter increment.

---

## Technical behavior

- Standard library only: `re`, `datetime`. No parsing dependency.
- Patterns are anchored and avoid nested quantifiers over unbounded input; no
  regex may exhibit catastrophic backtracking on a hostile 8 KB URL.
- Compiled once at startup, reused across lines.
- Parsing is pure: same line in, same record out, no I/O, no global state beyond
  counters. This is what makes rule behavior reproducible from fixtures.
- Very long lines are truncated to a maximum length before matching; the
  truncation is recorded on the record.
- Any exception inside the parser is caught at the line boundary, counted, and
  the loop continues.

## Data structures

Access record:

```text
site            from the source file
ts              normalized timestamp
ip              client address as logged
method          GET, POST, ...
path            without query
query           raw query string, may be absent
status          int
bytes           int, may be absent
referer         may be absent
user_agent      may be absent
request_time            float, may be absent
upstream_response_time  float, may be absent
```

Error record:

```text
site
ts
severity        may be absent
class           from the classification table
message         bounded length
client_ip       may be absent
request         may be absent
upstream        may be absent
```

## Edge cases

| Case | Behavior |
|---|---|
| Custom `log_format` | Best-effort field extraction; unmatched lines counted |
| Missing timing fields | Fields absent; [SPEC-005](SPEC-005-traffic-aggregation.md) skips timing metrics |
| Status `499` (client closed) | Parsed normally; counted separately per README §29 |
| Request line without method | Method absent; path kept when recoverable |
| Binary garbage in the line | Permissive decode, then malformed counter |
| 8 KB query string | Truncated to the max length, flagged |
| IPv6 client address | Parsed as-is; no normalization to IPv4 |
| `X-Forwarded-For` in a custom format | TBD — which address counts as the client is undecided; MVP uses the logged `$remote_addr` |
| Line from the future or past | Accepted; window assignment uses the parsed timestamp |
| Non-UTF-8 user agent | Decoded permissively, never raises |
| Stack trace with no preceding event | Dropped, counted |

## Safety constraints

- No cookies, headers, bodies, tokens, authorization values or session data are
  extracted or retained (README §61).
- Parsing is read-only and allocates bounded memory per line.
- The parser never writes to any log file.

## CLI impact

None directly. `doctor` and `status` may surface parser error counters so a
format mismatch is visible instead of silent.

## Persistence impact

None. Records are transient; only aggregates
([SPEC-005](SPEC-005-traffic-aggregation.md)) and incidents
([SPEC-006](SPEC-006-load-incident-detection.md)) are persisted.

---

## Acceptance criteria

- [ ] The aaPanel default access format parses into all mandatory fields.
- [ ] Optional fields are absent rather than defaulted when not logged.
- [ ] A malformed line increments a counter and never raises.
- [ ] Every pattern in the classification table is recognized by its fixture.
- [ ] A multi-line PHP trace yields exactly one error event.
- [ ] A hostile 8 KB URL parses in bounded time.
- [ ] Parsing a fixture twice yields byte-identical aggregates.

## Verification

- Fixture files per case, including `php-fatal.error.log` and
  `upstream-timeout.error.log`.
- A malformed-line fixture mixing truncated lines, binary bytes and empty lines;
  assert the daemon survives and counters match expectations.
- A timing test on a pathological URL fixture to catch regex blowup.

## Out of scope

- Parsing arbitrary `log_format` definitions from the Nginx configuration.
- Recommending or applying an enhanced log format — Parking Lot, README §68.
- Access log formats from web servers other than Nginx.
