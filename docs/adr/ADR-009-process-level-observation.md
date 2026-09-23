# ADR-009 — Process-level observation, and what is never persisted

Status: Proposed
Date: 2026-09-23

Related:
- README.md §57, §58, §61, §90
- [ADR-001](ADR-001-non-invasive-read-only-architecture.md),
  [ADR-002](ADR-002-python-standard-library-first.md)
- SPEC-011, SPEC-012, SPEC-013

## Context

Until now aaDoctor's observation surface has been narrow and easy to describe:
Nginx vhost files, the access and error logs of discovered sites, and
`/proc/loadavg`. Everything it reads was produced by the web stack it diagnoses,
and everything it stores is a count of something in a web request.

[SPEC-011](../specs/SPEC-011-process-attribution.md) proposes to widen that. To
explain a load rise caused by memory rather than traffic, aaDoctor would read
`/proc/<pid>/` for **every process on the host** — including processes that have
nothing to do with the web stack, belong to other tenants, or were started by
the administrator personally.

That is a different kind of observation, and it carries a consequence the
project has not had to weigh before. `/proc/<pid>/cmdline` routinely contains
secrets: a database password in a connection string, an API token on a backup
command, a DSN, a URL with embedded credentials. `/proc/<pid>/environ` contains
more, and worse.

aaDoctor writes incident files to disk and keeps them for thirty days. A tool
that copied command lines into those files would have created a credential store
nobody asked for, on a shared server, with a retention policy — and it would
have done so as a side effect of a feature about memory. README §61 forbids
storing cookies, headers, bodies and tokens from *requests*; it was written
before processes were in scope and does not cover this.

The decision is expensive to reverse in a specific way: once incident files
containing command lines exist on real servers, no later version can unwrite
them, and the project's claim to store nothing sensitive would be false for
every file already on disk.

`psutil` would supply all of this in a few lines. It is excluded by
[ADR-002](ADR-002-python-standard-library-first.md), and that exclusion is
reaffirmed rather than revisited: the data is plain text in `/proc`, the parsing
is a few dozen lines, and adding `pip` to the install path of a tool whose
selling point is that it changes nothing remains a bad trade.

## Decision

**aaDoctor may read per-process data from `/proc/<pid>/`, read-only, under three
constraints.**

**1. No command line is ever persisted, logged, printed or transmitted.**

`cmdline` may be read into memory for classification and must not reach any
file, any CLI output, any log line or any AI payload. What is stored instead is
`comm` (the kernel's 15-character process name), the executable's basename from
`/proc/<pid>/exe`, and the numeric uid with its resolved user name.

One exception, audited and anchored: for PHP-FPM, the pool label matched by

```text
^php-fpm: pool ([A-Za-z0-9._-]{1,32})$
```

in full. PHP-FPM rewrites its own argv to exactly this string; it contains no
arguments and no secrets, and the pool name is the one piece of command line
with diagnostic value ([SPEC-013](../specs/SPEC-013-php-fpm-pressure-and-pool-discovery.md)).
Anything not matching the expression in its entirety is discarded. There is no
partial extraction, no truncate-and-keep and no redaction heuristic, because a
redaction heuristic fails open and this one would fail open onto a password.

A second extraction rule requires amending this ADR, not an implementation
decision.

**2. `/proc/<pid>/environ` is never opened, for any reason.**

There is no diagnostic question it answers that justifies opening the file where
credentials actually live.

**3. Observation only. No process is ever signalled.**

No `kill`, no `renice`, no `prlimit`, no write to any `/proc/<pid>/` file —
several are writable as root and none is opened for writing. Liveness is
established by the read itself, so even `os.kill(pid, 0)` is unnecessary and is
not used. This follows [ADR-001](ADR-001-non-invasive-read-only-architecture.md)
and README §90 without adding an exception for the case where acting would
obviously help.

Process data is collected only while the server is under pressure — never during
normal operation — and is stored in bounded tables (SPEC-011).

## Consequences

### Positive

- aaDoctor can answer "what was holding the memory", which is the first question
  after "the machine is out of memory" and the one it could not answer on its
  first production server.
- The privacy rule is a property anyone can test: a test plants a secret in a
  fixture command line and asserts it appears nowhere in the serialized
  incident. A rule that can be asserted mechanically will survive contributors
  who never read this document.
- No new dependency, no subprocess, nothing installed
  ([ADR-002](ADR-002-python-standard-library-first.md)).
- Storing `comm` rather than argv also bounds the data: process names are short
  and low-cardinality, so the incident file cannot grow with what happens to be
  running.

### Negative

- **The observation surface genuinely widens**, and that is a real cost, not an
  accounting entry. aaDoctor now reads about processes that have nothing to do
  with any website, on a machine that may host other people's work.
- **`comm` is 15 characters and truncated by the kernel**, so two processes can
  be indistinguishable where a command line would have separated them. Accepted:
  the alternative is storing the thing that must not be stored.
- **A process cannot be attributed to a site**, and on a default aaPanel it
  never will be, because all sites on a PHP version share one pool. Process data
  therefore stops at "PHP-FPM" and the report must not bridge the gap with a
  guess (SPEC-011, Known limitations).
- The pool-label exception is a precedent, and precedents attract second cases.
  The anchored expression and the requirement to amend this ADR are the
  deliberate friction against that.

## Alternatives considered

**Store full command lines and redact by pattern.** Rejected. Redaction fails
open: an unrecognised secret format is stored, and the failure is silent and
permanent. Every incident file already written would have to be trusted to the
quality of a regular expression.

**Store command lines but truncate to the first token.** Rejected as a false
safety. `/usr/bin/mysql` is harmless, but the first token of a wrapper script is
not always the program, and the rule would need exceptions immediately — which
is how a redaction heuristic starts.

**Do not observe processes at all.** Rejected, but it was close. It keeps the
surface minimal, and the cost is that aaDoctor can say "the machine has no
memory left" and never "PHP-FPM is holding 60% of it" — which leaves the
administrator exactly where `top` already leaves them, after the incident is
over. The whole project exists to avoid that.

**Use `psutil`.** Rejected by
[ADR-002](ADR-002-python-standard-library-first.md). It would also make the
privacy rule harder to audit, since the library gathers command lines whether
they are wanted or not.

**Read `/proc/<pid>/smaps_rollup` for accurate shared-memory accounting.**
Deferred, not rejected. It is a much more expensive read per process, and
resident size with a stated caveat is honest enough for now. Recorded as a TBD
in SPEC-011 rather than decided here.
