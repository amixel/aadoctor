# SPEC-008 — CLI Reporting

Status: Draft

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

No CLI exists. Command names and output shapes are fixed by README §38–§44.

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

A command that is not implemented is **not registered**: `aadoctor top` today
is a usage error listing the commands that exist, not a stub printing "not
implemented". Nothing may look implemented when it is not. As of 0.1.0-dev the
registered commands are `doctor`, `status`, `sites`, `enable`, `disable`,
`update`, `uninstall` and `daemon`.

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

Per README §40: version, aaPanel detection, site and log counts, daemon state,
monitoring state, current load, CPU count, load per core, last incident.

Works with the daemon stopped and says so plainly.

### top

Per README §41: top sites, top path, top IP and error counts for the current
window. Must also show:

- the window length actually used;
- the `other` bucket when the cardinality cap engaged
  ([SPEC-005](SPEC-005-traffic-aggregation.md));
- a clear statement when the daemon holds no data yet.

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
site, primary finding, evidence, confidence.

Rules:

- Every claim is followed by the numbers behind it.
- Confidence comes from [SPEC-007](SPEC-007-deterministic-rules.md).
- When no degradation is detected, say so; do not invent a suspect.
- When load is high but the logs show nothing, say that explicitly. "The cause is
  not visible in the web logs" is a useful answer.

### incidents

Lists stored incidents, newest first: id, start time, primary finding,
confidence, severity. Supports a count limit. An empty directory produces a
clear empty result, not an error.

### show

Renders one incident by id, from the stored JSON only — never recomputed.
An unknown id exits non-zero with a message naming what was searched.

### Output conventions

- Plain text, no box-drawing characters, readable at 80 columns.
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

TBD: whether `diagnose` should exit non-zero when it finds an active incident.
Useful for scripting, surprising interactively. Undecided.

### Privilege

Read-only commands run without root where file permissions allow, and say
clearly when a permission gap limits the result rather than failing opaquely.
Lifecycle commands require root.

---

## Technical behavior

- The CLI renders; it does not analyze. `show` and `incidents` read stored JSON;
  `top` and `diagnose` read the daemon's current state.
- How the CLI reaches a running daemon's in-memory windows is **TBD**: a state
  file written periodically, or a local socket. The file approach is simpler and
  fits [ADR-003](../adr/ADR-003-filesystem-state-without-database.md); it is the
  default assumption unless staleness proves unacceptable.
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
- [ ] `diagnose` invents no suspect when there is no evidence.
- [ ] Rendering the same incident twice produces identical bytes.

## Verification

- Golden-output tests per command against fixture incidents and snapshots.
- A filesystem audit during a full command sweep, asserting no writes.
- A width test at 80 columns and a pipe test asserting no control characters.

## Out of scope

- `explain` behavior — [SPEC-009](SPEC-009-ai-explainer.md).
- `--json` output — TBD above.
- Any web or graphical interface (README §92).
