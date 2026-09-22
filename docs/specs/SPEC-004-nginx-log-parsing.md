# SPEC-004 — Nginx Log Parsing

Status: Implemented

Implemented in `src/aadoctor/parsers/`. See **Implementation notes** for the
decisions this spec left open, the one requirement deliberately deferred, and
the renaming of the classification table.

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

Input is complete lines from [SPEC-003](SPEC-003-incremental-log-monitoring.md),
each already attributed to a site and a log type by its source file.

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
- The timestamp is parsed with its timezone offset. When unparseable it is
  `None`: the line's arrival time is not the request's time, and substituting
  one for the other would put requests in the wrong window.
- `-` means absent for referer, user agent and timing fields.
- The status must be a three-digit number; otherwise the record is counted as
  malformed.

### Error log classification

Classify against a table of known patterns (README §30, §31):

| Pattern | Kind |
|---|---|
| `PHP Fatal error` | `php_fatal` |
| `PHP Parse error` | `php_parse_error` |
| `Allowed memory size` | `php_memory_exhausted` |
| `Maximum execution time` | `php_execution_timeout` |
| `PHP Warning` | `php_warning` |
| `PHP Notice` | `php_notice` |
| `upstream timed out` | `upstream_timeout` |
| `upstream prematurely closed connection` | `upstream_closed` |
| `FastCGI sent in stderr` | `fastcgi_stderr` |
| `connect() failed` | `connect_failed` |
| `recv() failed` | `recv_failed` |
| `worker_connections are not enough` | `worker_connections_exhausted` |
| `too many open files` | `too_many_open_files` |
| `client intended to send too large body` | `body_too_large` |
| `open() failed` | `open_failed` |
| `no live upstreams` | `no_live_upstreams` |
| `SSL_do_handshake() failed` | `ssl_handshake_failed` |

The table is data, not code branches: adding a pattern is one entry. Order
matters and the PHP patterns come first: a FastCGI stderr line carrying a PHP
fatal is more usefully a `php_fatal` than a `fastcgi_stderr`. Matching ignores
case, because some of these strings come from `strerror`.

**A kind is not a finding.** It names what one line says. A finding is a
conclusion about many lines, with a threshold and a confidence behind it, and
belongs to [SPEC-007](SPEC-007-deterministic-rules.md). The two use different
naming - `php_fatal` against `PHP_ERROR_SPIKE` - so they cannot be confused.

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

Counters are per file and per log type. No sample of the lines themselves is
kept; see the implementation notes.

If the malformed ratio for a file exceeds a threshold over a window, log a
single warning suggesting a format mismatch. Never attempt to auto-detect and
switch formats silently.

### Multi-line errors

PHP stack traces span lines. A continuation line — one that does not start with
a timestamp — currently yields no event and is counted as unparsed.

Attaching continuations to the event before them is **deferred**, not done.
Doing it needs state across lines, which the parsers deliberately do not have,
and the shape of the continuation depends on the PHP and Nginx configuration.
Without a sample from a real server, an implementation would be guesswork of
exactly the kind this project avoids elsewhere (see SPEC-002 on `include`, and
SPEC-003 on the rotated tail). Tracked in the Parking Lot.

In practice the common case is already one line: PHP messages reach the Nginx
error log wrapped in `FastCGI sent in stderr: "..."`, which parses whole.

---

## Technical behavior

- Standard library only: `re`, `datetime`. No parsing dependency.
- Patterns are anchored and avoid nested quantifiers over unbounded input; no
  regex may exhibit catastrophic backtracking on a hostile 8 KB URL.
- Compiled once at startup, reused across lines.
- Parsing is pure: same line in, same record out, no I/O, no global state beyond
  counters. This is what makes rule behavior reproducible from fixtures.
- Very long lines are not pre-truncated; see the implementation notes.
- Any exception inside the parser is caught at the line boundary, counted, and
  the loop continues.

## Implementation notes

Decisions taken while implementing this spec. They extend it; the
classification table above and the multi-line section were rewritten in place.

**Two formats, and a refusal to guess beyond them.** `common` and `combined`
are matched by one anchored pattern whose referer/user-agent group is optional.
Anything else is unparsed. Field positions of an unknown `log_format` are not
something to infer.

**The trailing field of Nginx's default `main`.** That format — the one aaPanel
uses — appends `"$http_x_forwarded_for"` after the user agent. Without
tolerating it, every line on a real server would be unparsed. It is captured as
`extra`, verbatim and uninterpreted: calling it the client address is the
decision the `X-Forwarded-For` TBD below has not taken.

**Timestamps are parsed from a month table, not `strptime`.** `%b` follows the
process locale; Nginx writes English month names whatever the server's locale
is. On a `pt_BR` server, `strptime` would fail on every line.

**Error context is found by marker, not by splitting on commas.** Messages and
URLs contain commas. The known keys (`client`, `server`, `request`, `upstream`,
`host`, and `referrer`/`subrequest`, which are matched only so they terminate
the value before them) are located, and the message is everything before the
first of them.

**Error timestamps are naive.** The Nginx error log carries no offset, and one
is not invented. Access timestamps are aware. SPEC-005 must decide what the
server's local zone is before comparing the two.

**No timing fields.** `request_time` and `upstream_response_time` are not
parsed: the supported formats do not contain them, and inferring "that extra
float is probably the request time" would produce confident wrong numbers. They
arrive when a format that declares them is explicitly supported.

**Lines are not pre-truncated.** The spec called for truncating before
matching. It is unnecessary: every quantifier in these patterns is linear over
its own delimiter, so there is no backtracking to protect against, and SPEC-003
already caps a line at 64 KiB. The error *message* is bounded at 2000
characters, since a stderr dump should not travel in memory.

**Counters, not samples.** The spec allowed retaining a bounded sample of
unknown lines. None is kept: a per-file count plus a single "this format is not
one we recognise" warning is enough to act on, and keeping log content out of
memory and out of our own log is worth more.

## Data structures

Implemented as `AccessEvent` and `ErrorEvent`, dataclasses in
`src/aadoctor/parsers/`. `site` and `log_path` are filled by the dispatcher
from the monitor's metadata - the parsers themselves see only a string.
`log_type` is a class attribute: the type of the object is the answer.

Access record:

```text
site, log_path      from the monitor, not from the line
timestamp           aware; None when unparseable
remote_addr         client address exactly as logged
remote_user         may be absent
method              may be absent
request_target      the raw target
path                target without the query
query_string        raw, unnormalised; may be absent
protocol            may be absent
status              int
body_bytes_sent     int; may be absent
referer             may be absent
user_agent          may be absent
extra               trailing fields, uninterpreted; may be absent
```

Error record:

```text
site, log_path  from the monitor
timestamp       naive; the error log carries no offset
level           as written; an unknown level is kept
pid, tid        may be absent
connection      the *N connection id; may be absent
message         bounded at 2000 characters
kind            from the table above; may be absent. Never a finding
client          may be absent
server          may be absent
request         may be absent
upstream        may be absent
host            may be absent
```

## Edge cases

| Case | Behavior |
|---|---|
| Custom `log_format` | Best-effort field extraction; unmatched lines counted |
| Missing timing fields | Fields absent; [SPEC-005](SPEC-005-traffic-aggregation.md) skips timing metrics |
| Status `499` (client closed) | Parsed normally; counted separately per README §29 |
| Request line without method | Method absent; path kept when recoverable |
| Binary garbage in the line | Permissive decode, then malformed counter |
| 8 KB query string | Parsed whole; the patterns are linear, so it is not a risk |
| IPv6 client address | Parsed as-is; no normalization to IPv4 |
| `X-Forwarded-For` in a custom format | TBD — which address counts as the client is undecided; MVP uses the logged `$remote_addr` |
| Line from the future or past | Accepted; window assignment uses the parsed timestamp |
| Non-UTF-8 user agent | Decoded permissively, never raises |
| Stack trace line | Unparsed and counted; joining is deferred |

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

- [x] The aaPanel default access format parses into all mandatory fields.
- [x] Optional fields are absent rather than defaulted when not logged.
- [x] A malformed line increments a counter and never raises.
- [x] Every pattern in the classification table is recognised by a test case.
- [ ] A multi-line PHP trace yields exactly one error event - deferred, see
      above; a continuation line is counted as unparsed instead.
- [x] A hostile 8 KB URL parses in bounded time.
- [x] Parsing the same line twice yields an identical record.

## Verification

`tests/test_parsers.py`, on inline strings rather than fixture files: a parser
that needed to open a file would already be wrong.

- Combined, common, and Nginx's default `main` with its trailing field.
- IPv4, full and abbreviated IPv6, and a non-address token.
- Every method; every status including 499; a non-numeric and a two-digit one.
- `-` for body size, referer, user agent, remote user and the whole request.
- Query preserved verbatim, empty query, a second `?`, absolute-form target,
  missing protocol, missing method, an unencoded space in the target.
- Offsets positive, negative and zero; malformed, impossible and
  non-English-month timestamps.
- Error prefix with and without pid and connection id; every level plus an
  unknown one; a message full of commas; a request URL containing commas; a
  trailing `referrer` that must not leak into `host`; a bounded long message.
- Every pattern in the kind table, including PHP messages wrapped in FastCGI
  stderr, where the PHP kind must win.
- Purity: repeated parses are equal; a fuzz over concatenated fragments and
  over every truncation of a valid line, asserting nothing raises; a source
  check that neither parser module contains I/O.
- Counting: parsed and unparsed apart, a parser bug apart from a malformed
  line, one warning per file for an unfamiliar format, and counters bounded by
  file count.
- A timing smoke test: 20000 lines, and an 8 KB query string.

Also exercised end to end against the real daemon in a container, on
aaPanel-shaped traffic: 13 lines read, 10 parsed, 3 unparsed (two malformed
access lines and one stack-trace continuation), with no log content reaching
`/var/log/aadoctor`.

## Out of scope

- Parsing arbitrary `log_format` definitions from the Nginx configuration.
- Recommending or applying an enhanced log format — Parking Lot, README §68.
- Access log formats from web servers other than Nginx.
