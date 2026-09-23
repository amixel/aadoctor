# SPEC-008 — CLI Reporting

Status: In Progress

Implemented: `doctor`, `status`, `sites`, `top`, `incidents`, `show`,
`diagnose`, `enable`, `disable`, `update`, `uninstall`, `daemon`. Not
implemented, and deliberately not registered: `explain` - it waits for the
spec that gives it something to say (SPEC-009).

Related: [README.md](../../README.md) §38–§44, §93 ·
Backlog: AAD-050 … AAD-055, AAD-063

---

## Problem

The administrator is on SSH, usually during an incident, often on a phone
tethered connection. The output has to be readable at a glance in a plain
terminal and has to say where to look first.

## Goal

A small command set that renders discovery, live traffic and stored incidents,
evidence first, with no interactive UI.

## Non-goals

- A TUI, colors as the only signal, cursor control or live redraw.
- A web panel or any HTTP server.
- Commands that modify Nginx, PHP, the firewall or any site.

## Current context

Command names and output shapes are fixed by README §38–§44.

---

## Functional requirements

### Command set

MVP commands:

```text
doctor      validate the environment
status      current state of aaDoctor and the server
sites       the sites discovered in aaPanel
top         current window's traffic leaders
diagnose    current or most recent degradation, with evidence
incidents   list stored incidents
show        render one stored incident
enable      start and enable aadoctor.service
disable     stop and disable aadoctor.service
update      upgrade to a newer release
uninstall   remove aaDoctor (--purge for everything)
```

Future commands:

```text
explain     AI explanation of a stored incident (Phase 7, SPEC-009)
```

A command that is not implemented is **not registered**: `aadoctor explain`
today is a usage error listing the commands that exist, not a stub printing
"not implemented". Nothing may look implemented when it is not. As of
0.1.0-dev the registered commands are `doctor`, `status`, `sites`, `top`,
`incidents`, `show`, `diagnose`, `enable`, `disable`, `update`, `uninstall`
and `daemon`.

`update` takes `--version X.Y.Z` and `--force`, and delegates to `install.sh`
in release mode ([SPEC-001](SPEC-001-installation-lifecycle.md)).

Lifecycle commands (`enable`, `disable`, `update`, `uninstall`) behave per
[SPEC-001](SPEC-001-installation-lifecycle.md); this spec covers only how they
report.

### doctor

Validates and reports; changes nothing (README §39):

```text
aaDoctor environment check

[OK] Linux detected
[OK] Python 3 detected
[OK] aaPanel detected
[OK] Nginx configuration directory detected
[OK] /www/wwwlogs readable

Sites found:       48
Access logs found: 47
Error logs found:  48

[WARN]
cliente.com.br has no access log configured

Ready to monitor.
```

Every check is `[OK]`, `[WARN]` or `[FAIL]`. Warnings never hide a failure: if
any check fails, the closing line says the environment is not ready.

### status

Per README §40: version, aaPanel detection, site and log counts, current load
and load per core, whether an incident is open, daemon state and configuration
source. The load and incident lines come from what the daemon last published,
so they are absent when it has published nothing.

Works with the daemon stopped and says so plainly.

### top

Per README §41: top sites, top paths with the site they belong to, top IPs,
status classes and error kinds for the current window. It also shows:

- the window length actually used, via `--window 1m` or `--window 5m`;
- how old the published snapshot is, and a warning when it is stale, so an old
  snapshot is never read as the present;
- the `other` volume when a cardinality cap engaged
  ([SPEC-005](SPEC-005-traffic-aggregation.md));
- a warning when a large share of access lines did not parse, because the
  numbers then describe only part of the traffic;
- a clear statement when the daemon has published nothing yet.

`--site NAME` shows one site in detail: its paths, its addresses, its statuses
and its errors. `--json` prints the window as published.

`top` describes what happened. It does not name a cause, a culprit or an
attack - that is [SPEC-007](SPEC-007-deterministic-rules.md), and a report that
quietly starts concluding would be the most dangerous thing in this tool.

### sites

One row per discovered site, with the state of each log:

```text
SITE              ACCESS   ERROR
example.com       yes      yes
cliente.com.br    none     missing
quiet.com         off      yes
```

`yes` the file is there, `missing` a path is configured but the file is not
there, `none` nothing is configured, `off` logging is disabled, `?` the path is
relative and was not resolved. `none` and `missing` are deliberately different
answers ([SPEC-002](SPEC-002-aapanel-discovery.md)).

`--vhost-dir PATH` reads another directory, which is how the command is tested
without `/www`. `--json` prints the full records, including every
`server_name`, the source file and per-site warnings.

### diagnose

Per README §42: the evidence-first report — load context, probable responsible
site, primary suspect, evidence, confidence.

```text
aadoctor diagnose            the most recent incident
aadoctor diagnose <id>       that incident, however old
aadoctor diagnose --json     the whole document
```

The report is computed on demand from the stored incident and writes nothing
([SPEC-007](SPEC-007-deterministic-rules.md)). An incident from weeks ago is
diagnosable after its logs have rotated away, and it is read with today's
rules — so the output carries `ruleset_version`.

Sections, in order, each omitted when it has nothing to say:

```text
PRIMARY SITE            the site the evidence points at
PRIMARY PATH            not "URL": the query string is not counted
ASSOCIATED IP           with the CDN / proxy / NAT / crawler caveat
ERROR CONCENTRATION     a different site carrying the failures
TRAFFIC CONCENTRATION   a different site carrying the traffic
EVIDENCE                one line per finding, the numbers included
FINDINGS                level and code, strongest first
NOT EVALUABLE           rules that could not be decided, and why
DATA QUALITY            short window, unparsed lines, pruned dimensions
CONFIDENCE              LOW | MEDIUM | HIGH | VERY HIGH, or `-`
```

Rules:

- Every claim is followed by the numbers behind it.
- Confidence comes from [SPEC-007](SPEC-007-deterministic-rules.md), and is
  never called a probability.
- When nothing is conclusive, say so; do not invent a suspect. The confidence
  line reads `-` rather than a low number, because there is no conclusion to
  put a number on.
- When load is high but the logs show nothing, say that explicitly. "The cause
  is not visible in the web logs" is a useful answer, and the report closes by
  naming what could never appear in them — I/O, a backup, a remote database, a
  process owned by no site.
- Prose is wrapped; the report is read over SSH during an incident.

`NOT EVALUABLE` is the section that stops silence being misread. A rule that
never had the data to run is not a rule that ran and found nothing, and the
difference matters to a reader and to
[SPEC-009](SPEC-009-ai-explainer.md) later.

Exit code is `0` whatever the finding. Resolving the TBD below: an active
incident is a normal result, not a command failure.

### incidents

Lists stored incidents, newest first: id, start time, duration, peak load per
core and severity. `--limit N` (default 20) and `--json`. An empty directory
produces a clear empty result, not an error, and says where the daemon would
write them.

No finding and no confidence appear here: an incident records when the server
was under load and what the logs showed, and interpreting that is SPEC-007.

### show

Renders one incident by id, from the stored JSON only - never recomputed. The
load at the start and at the peak, then the traffic frozen with it: top sites,
top paths per site, top addresses, status classes and error kinds. `--json`
prints the record as stored.

It prints how much data the window actually covers, so a snapshot taken shortly
after the daemon started is not read as five minutes of evidence.

An unknown id exits with `3` and a message naming where it looked. An id that
is not an incident id - a path, say - never reaches the filesystem.

### Output conventions

- Plain text, no box-drawing characters, readable at 80 columns. Every column
  is bounded: a real server's paths run past a hundred characters, and a table
  sized to the longest value runs off the terminal. A shortened value is cut
  in the **middle**, marked with `...`, because two articles on one site share
  a long prefix and cutting the tail would render distinct rows identically.
- Numbers right-aligned in tables; the README examples are the reference style.
- Color, if used at all, is additive; output must be unambiguous piped to a file.
- No spinners, no progress animation, no cursor manipulation.
- Errors go to stderr; data goes to stdout.
- Machine-readable output: `sites --json` exists, added alongside its
  human-readable form. `incidents --json` and `show --json` should follow the
  same shape when those commands land — human-readable first, never instead.

### Exit codes

```text
0  success
1  usage error or unknown command
2  environment not ready (doctor failed a check)
3  requested object not found (unknown incident id)
4  daemon not running where the command requires it
5  insufficient privileges (root required)
```

`argparse` exits with `2` on a usage error by default; the parser overrides
that to `1` so the table above holds.

Decided: `diagnose` exits `0` whatever it concludes. A non-zero code for an
active incident would be useful in a script and surprising at a prompt, and
the surprising reading is the one someone gets at three in the morning. An
unknown id still exits `3`.

### Privilege

Read-only commands run without root where file permissions allow, and say
clearly when a permission gap limits the result rather than failing opaquely.
Lifecycle commands require root.

---

## Technical behavior

- The CLI renders; it does not analyze. `show` and `incidents` read stored JSON;
  `top` reads the daemon's current state; `diagnose` reads a stored
  incident and computes from it.
- The CLI reaches the daemon's windows through a file: the daemon publishes a
  bounded snapshot to `/var/lib/aadoctor/runtime.json` once per poll and the
  CLI reads it, showing its age. No socket, no port, no protocol
  ([ADR-003](../adr/ADR-003-filesystem-state-without-database.md),
  [SPEC-005](SPEC-005-traffic-aggregation.md)).
- No command starts the daemon implicitly.
- Rendering is deterministic: the same incident file always renders identically.
- Standard library `argparse` only.

## Data structures

Consumes the incident schema of
[SPEC-006](SPEC-006-load-incident-detection.md) and the window snapshot of
[SPEC-005](SPEC-005-traffic-aggregation.md). Defines no persistent structure of
its own.

## Edge cases

| Case | Behavior |
|---|---|
| Daemon not running | `status` reports it; `top` says no live data |
| No incidents stored | Empty list, exit 0 |
| Unknown incident id | Exit 3 with a clear message |
| Incident file corrupt | Report the id as unreadable; do not crash the listing |
| Window empty | `top` prints zeros and says the window is empty |
| Output piped | No ANSI codes; alignment preserved |
| Narrow terminal | Degrade columns rather than wrap mid-number |
| Non-root read attempt | Report what could not be read |
| Schema from an older version | Render known fields, note the version difference |

## Safety constraints

- No reporting command writes anything, anywhere.
- No command restarts or reloads Nginx, PHP-FPM or MySQL.
- No command blocks an IP, edits a firewall or modifies a site.
- Output never includes credentials, API keys or request bodies.
- `uninstall --purge` is the only destructive command, and it removes only
  aaDoctor's own paths ([SPEC-001](SPEC-001-installation-lifecycle.md)).

## CLI impact

This spec is the CLI contract.

## Persistence impact

None. Read-only against `/var/lib/aadoctor/`.

---

## Acceptance criteria

- [ ] Every MVP command exists and matches the README output shape.
- [ ] `doctor` and every reporting command perform zero writes.
- [ ] Output is readable at 80 columns and clean when piped.
- [ ] `show` renders from stored JSON only.
- [ ] Exit codes follow the table above.
- [x] `diagnose` invents no suspect when there is no evidence.
- [ ] Rendering the same incident twice produces identical bytes.

## Verification

- Golden-output tests per command against fixture incidents and snapshots.
- A filesystem audit during a full command sweep, asserting no writes.
- A width test at 80 columns and a pipe test asserting no control characters.

## Out of scope

- `explain` behavior — [SPEC-009](SPEC-009-ai-explainer.md).
- `--json` output — TBD above.
- Any web or graphical interface (README §92).
