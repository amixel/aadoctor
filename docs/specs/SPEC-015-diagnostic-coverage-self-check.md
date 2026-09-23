# SPEC-015 — Diagnostic Coverage / Self-Check

Status: Draft

Related: [README.md](../../README.md) §39, §65, §80, §81 ·
Backlog: AAD-120 … AAD-122

---

## Problem

After SPEC-010 through SPEC-014, aaDoctor reads a dozen sources, and on any
given server some of them will not be there. PSI is absent on CentOS 7. One site
has a custom `log_format`. `/dev/kmsg` is restricted. A PHP version's pool
directory cannot be read. Each of these degrades a different part of the
diagnosis, and none of them is visible today.

Two failures follow, and the second is much worse than the first.

**The administrator cannot tell what the tool is actually watching.** `doctor`
reports that the environment is ready. Ready for what, exactly, is not stated,
and after five more specs "ready" will cover a very wide range of realities.

**And the diagnosis can make a negative claim it has not earned.** "No system
process was causing load" and "process attribution was unavailable" produce the
same silence, and a reader who cannot tell them apart will act on the wrong one.
[SPEC-012](SPEC-012-system-deterministic-findings.md) depends on this
distinction; this spec is what makes it computable.

## Goal

Answer one question, plainly:

> **What is aaDoctor able to observe on this server, and what is it blind to?**

And expose that answer as a structure the diagnosis can read, so a negative
statement is only ever made about something that was actually looked at.

## Non-goals

- Diagnosing anything. Coverage says what *can* be seen, never what *was* seen
  during an incident. `doctor` does not mention incidents, load, sites under
  stress or causes.
- Fixing a gap. aaDoctor reports that a log is unreadable; it does not change
  its permissions ([/CLAUDE.md](../../CLAUDE.md) §7).
- Recommending configuration changes to improve coverage. Documenting an opt-in
  enriched `log_format` is a README matter and is manual, reversible and never
  applied by the tool (README §68).
- A health score, a percentage or a grade. "Coverage: 78%" invites arithmetic
  over aspects that are not comparable.
- Monitoring coverage over time, alerting on it or storing its history.

## Current context

`doctor` already reports environment checks as `[OK]`, `[WARN]` and `[FAIL]`,
plus site and log counts, and it already runs read-only with the daemon stopped
([SPEC-008](SPEC-008-cli-reporting.md)). This spec extends it rather than adding
a command: the question "what can you see?" is the question `doctor` already
exists to answer, and splitting it across two commands would leave neither
complete.

---

## Functional requirements

### Coverage states

Four states, and the reason there are four is that two would lie:

```text
OK            the source is present, readable and understood
PARTIAL       readable, but part of what it should yield is missing
              e.g. 24 of 25 access logs parse
UNAVAILABLE   the source does not exist or cannot be read on this host
              e.g. PSI on a 3.10 kernel, /dev/kmsg in a container
UNSUPPORTED   the source exists and aaDoctor does not understand it
              e.g. a custom log_format
```

`UNAVAILABLE` and `UNSUPPORTED` are deliberately distinct: one is the server's
shape and nothing can change it, the other is a gap in aaDoctor and belongs in
the backlog. Collapsing them would erase the difference between "this host
cannot tell us" and "we never taught it to read this".

A fifth state exists only for aspects that need the daemon:

```text
UNKNOWN       cannot be determined right now — the daemon has published
              nothing, so this aspect has never been exercised
```

`UNKNOWN` is never a permanent answer. It means "start the daemon and ask
again", and the output says so rather than leaving a reader to interpret it.

Every state carries a **reason** whenever it is not `OK`. A state without a
reason is a state nobody can act on.

### Aspects

A flat, explicit list — not a tree discovered at runtime. Each aspect names the
spec that owns it, so a gap points at the document that explains it.

```text
environment
  aapanel                     SPEC-002
  nginx_vhost_dir             SPEC-002
  privileges                  SPEC-001

sites
  discovery                   SPEC-002
  access_logs                 SPEC-002 / SPEC-003
  access_parsing              SPEC-004
  error_logs                  SPEC-002 / SPEC-003
  error_parsing               SPEC-004

system
  memory                      SPEC-010
  swap                        SPEC-010
  cpu                         SPEC-010
  iowait                      SPEC-010
  pressure_cpu                SPEC-010
  pressure_memory             SPEC-010
  pressure_io                 SPEC-010
  disk                        SPEC-010
  oom_counter                 SPEC-010

processes
  attribution                 SPEC-011
  io_counters                 SPEC-011

php_fpm
  <per version>               SPEC-013
    pool_config
    log
    site_mapping

kernel
  events                      SPEC-014
```

Adding a source to any spec means adding an aspect here. An observable this
document does not list is an observable whose absence nobody will notice — which
is the failure this spec exists to prevent.

### Where the data comes from

Two sources, and the distinction is visible in the output:

- **Static** — determinable by `doctor` itself, with the daemon stopped: does
  `/proc/pressure/memory` exist, is it readable, does `/www/server/php/74/etc`
  exist, how many sites have an access log configured.
- **Runtime** — only the daemon knows: how many access lines actually parsed,
  whether a process scan has ever succeeded, whether `/dev/kmsg` produced
  records, whether a log's format was recognised.

Runtime aspects are read from the snapshot the daemon publishes to
`/var/lib/aadoctor/runtime.json`
([SPEC-005](SPEC-005-traffic-aggregation.md)). When it is absent they are
`UNKNOWN`; when it is stale its age is shown, because a coverage report from a
snapshot two days old describes a server that may no longer exist — the same
staleness rule `top` already applies.

Each daemon-dependent aspect is therefore **published, not recomputed by the
CLI**. `doctor` must not start a process scan or open `/dev/kmsg` to find out
whether it works; a read-only report that performs the expensive operation it is
reporting on would be both slow and misleading.

### Known gaps

Beyond per-aspect states, the report ends with a list of specific, actionable
observations:

```text
KNOWN GAPS

- example.com: access log uses an unrecognised format (SPEC-004)
- cliente.com.br: no access log configured (SPEC-002)
- PSI unavailable: kernel 3.10 has no /proc/pressure (SPEC-010)
- PHP 8.2: pool directory not readable (SPEC-013)
- kernel events: 3 buffer gaps in the last hour (SPEC-014)
```

Each names the thing, what is wrong and which spec covers it. The list is
bounded — at most twenty entries, with a count of the remainder — because a
report that scrolls off the screen during an incident is a report nobody reads.

### Output

`doctor` gains a `DIAGNOSTIC COVERAGE` section after its existing checks:

```text
DIAGNOSTIC COVERAGE

aaPanel            OK          detected
Sites              OK          25 discovered
Access logs        PARTIAL     24 of 25 parsing
Error logs         OK          25 monitored

Memory             OK
Swap               OK
CPU                OK
I/O wait           OK
Pressure (CPU)     UNAVAILABLE kernel has no /proc/pressure
Pressure (memory)  UNAVAILABLE kernel has no /proc/pressure
Pressure (I/O)     UNAVAILABLE kernel has no /proc/pressure
Disk               OK          3 filesystems
OOM counter        OK

Process attribution  OK
Process I/O          OK

PHP 7.4            OK          pool www, log readable, 19 sites mapped
PHP 8.2            PARTIAL     pool config readable, log not readable

Kernel events      OK          /dev/kmsg

KNOWN GAPS

- example.com: access log uses an unrecognised format (SPEC-004)
- PHP 8.2: /www/server/php/82/var/log/php-fpm.log not readable (SPEC-013)
```

Plain text, bounded columns, readable at 80 columns, unambiguous when piped —
SPEC-008's conventions, unchanged.

Flags:

```text
aadoctor doctor                 environment checks plus coverage
aadoctor doctor --coverage      the coverage section alone
aadoctor doctor --json          the whole structure, machine-readable
```

**No `aadoctor coverage` command.** One question, one command.

### Exit code

`doctor` keeps its existing contract: `0` when the environment is ready, `2`
when it is not ([SPEC-008](SPEC-008-cli-reporting.md)).

**A coverage gap is not an environment failure.** A server with no PSI is a
server aaDoctor works on, and exiting non-zero would break every script that
checks whether the tool is usable. `PARTIAL` and `UNAVAILABLE` never change the
exit code. `UNSUPPORTED` does not either — a site with a custom log format is a
known, documented limitation, not a broken installation.

---

## Relation to confidence

The rule, stated narrowly because a wide version would do damage:

> **Coverage constrains negative statements. It never raises a confidence, and
> it never invalidates a finding from an aspect that is fine.**

Three consequences:

1. **`process_attribution = UNAVAILABLE` must not weaken an HTTP finding.** Three
   hundred upstream timeouts read cleanly from an error log are three hundred
   timeouts whatever the process scanner could do. This is exactly the
   scoped-caps principle SPEC-007 already established, applied to a new
   dimension.
2. **A negative claim requires the coverage to support it.** The diagnosis may
   say "no system process was dominating" only when process attribution is `OK`.
   With it `UNAVAILABLE`, the report says so instead — which is SPEC-012's third
   inconclusive wording, and this is where its input comes from.
3. **The mechanism is the existing one.** A coverage state maps to a
   `not_evaluable` reason on the rules that read that aspect. No new field in
   the diagnosis, no second kind of caveat, no parallel vocabulary. SPEC-007
   already solved this problem; this spec feeds it.

The mapping is explicit and small:

```text
aspect state          effect
OK                    nothing; rules evaluate normally
PARTIAL               the existing quality caps apply, scoped to that aspect
UNAVAILABLE           rules reading it -> not_evaluable, with the reason
UNSUPPORTED           same, with a reason that names the spec
UNKNOWN               same, with "the daemon has published nothing"
```

## Technical behavior

- Coverage is computed, never stored. There is no coverage file and no history.
- Static aspects are probed with `os.access` and small reads. `doctor` performs
  **zero writes**, as it does today, and that property is asserted by the
  existing lifecycle audit.
- Probing is cheap: an existence check is not a sample. `doctor` does not read
  `/proc/meminfo` to decide that memory is observable; it checks that the file
  is readable.
- The structure is serializable and stable, so `--json` is a view of the same
  object the human-readable form renders.
- Deterministic: the same host state produces the same report.
- Standard library only.

## Performance constraints

- `doctor` completes in **under 500 ms** on a server with 50 sites, including
  coverage. It is typed at a prompt by someone who is already annoyed.
- Coverage adds at most a few dozen `access`/`stat` calls and one read of
  `runtime.json`.
- No process scan, no kernel buffer read, no log parse is performed by the CLI
  to compute coverage.

## Data structures

```text
CoverageAspect
  key            "system.pressure_memory"
  label          "Pressure (memory)"
  state          OK | PARTIAL | UNAVAILABLE | UNSUPPORTED | UNKNOWN
  detail         one short line, always present when state is not OK
  spec           "SPEC-010"
  source         "static" | "runtime"

CoverageReport
  generated_at
  snapshot_age_seconds     null when the daemon has published nothing
  aspects[]
  gaps[]                   bounded, each with a spec reference
  gaps_omitted             count of entries not shown
```

The same object is what
[SPEC-012](SPEC-012-system-deterministic-findings.md) reads when deciding which
negatives it may state, so the coverage a human sees and the coverage the rules
use are the same computation. Two implementations of "is this observable" would
eventually disagree, and the disagreement would appear as a report that
contradicts itself — which this project has already shipped once.

## Incident impact

The incident record gains a compact `coverage` block: the aspect states at the
moment the incident opened.

Not the full report — only the states, no labels and no prose. It exists so that
a diagnosis computed six weeks later knows what was observable *at the time*,
rather than what is observable today. A server that had PSI enabled since then
must not have its old incidents reinterpreted as though it always had.

This is the same reasoning that puts `thresholds` and `ruleset_version` in the
diagnosis output (SPEC-007): a reading of an old record has to be interpretable
in the conditions of the record.

## Edge cases

| Case | Behavior |
|---|---|
| Daemon never started | Runtime aspects `UNKNOWN`, static ones reported normally |
| Snapshot stale | Age shown; a warning above the section, as `top` already does |
| aaPanel not detected | Reported as today (README §80); coverage still renders what it can |
| Running as non-root | Many aspects `UNAVAILABLE` with "requires root" — the most useful coverage report there is |
| A site with an unrecognised format | `access_parsing` `PARTIAL`, and the site named in the gaps |
| All sites unrecognised | `access_parsing` `UNSUPPORTED`; the tool is running and seeing nothing, and says so unmistakably |
| No PHP installed | The `php_fpm` group is omitted entirely, not shown as failing |
| 200 gaps | Twenty shown, the remainder counted |
| A future aspect with no state | Rendered `UNKNOWN`; an unlisted aspect is a bug, and it looks like one |
| Piped to a file | No control characters, alignment preserved |

## Safety constraints

- **`doctor` writes nothing, anywhere.** Unchanged, and asserted by the existing
  audit.
- No permission, ownership or configuration is changed to close a gap. A gap is
  reported (README §65, [/CLAUDE.md](../../CLAUDE.md) §7).
- No service is started, restarted or reloaded to determine whether it is
  observable.
- The report contains no credential, no API key, no command line and no log
  content. Site names and file paths appear; that is all, and both are already
  visible in `sites`.
- Paths in the output come from discovery and from configuration and are
  **untrusted strings**; they are printed and never executed or used to build a
  command.
- Probing must not be destructive: no file is opened for writing to test
  writability, and no buffer is consumed to test readability.

## CLI impact

This spec is a change to `doctor`, described above. It changes no other command.

`status` stays a state report and does not grow a coverage section; the two
would drift, and `doctor` is where this question belongs.

## Persistence impact

None at runtime. Adds a small `coverage` block to the incident record.

## Interactions with existing specs

- **[SPEC-008](SPEC-008-cli-reporting.md)** — `doctor` gains a section, two
  flags and a documented exit-code rule. Its output conventions are reused
  unchanged.
- **[SPEC-002](SPEC-002-aapanel-discovery.md)** /
  **[SPEC-004](SPEC-004-nginx-log-parsing.md)** — supply the site and parsing
  aspects. Much of this already exists as warnings scattered across `doctor`,
  `sites` and `top`; this spec gathers it into one vocabulary rather than
  inventing new information.
- **[SPEC-010](SPEC-010-system-resource-monitoring.md)** … **SPEC-014** — each
  supplies its own availability facts. None of them formats a coverage line.
- **[SPEC-012](SPEC-012-system-deterministic-findings.md)** — the consumer that
  matters. Coverage decides which negative statements it may make, through the
  existing `not_evaluable` mechanism.
- **[SPEC-007](SPEC-007-deterministic-rules.md)** — its scoped quality caps are
  the model this spec generalises. `PARTIAL` maps onto them; it does not replace
  them.

## Known limitations and TBDs

- **Coverage is a snapshot, not a guarantee.** An aspect that is `OK` now may
  fail during an incident — a log rotated to a mode aaDoctor cannot read, a
  mount that went away. The per-incident `coverage` block is what records the
  state that actually applied.
- **`OK` means observable, not correct.** A parser that reads every line and
  misclassifies them all reports `OK`. Coverage measures reach, not accuracy,
  and nothing here substitutes for reading real output — the lesson of the first
  production night.
- **Runtime aspects lag by one snapshot**, up to the monitor interval.
- TBD: whether a long-lived `PARTIAL` should appear in `status` as well. Likely
  useful, likely noisy; left until the report has been seen on real servers.
- TBD: whether the coverage block belongs in every incident or only when it
  differs from the previous one. A few hundred bytes per incident is cheap;
  revisit only if incident size becomes a problem.

## Acceptance criteria

- [ ] Every aspect in the list above is reported, with a state and — when not
      `OK` — a reason.
- [ ] `UNAVAILABLE`, `UNSUPPORTED` and `UNKNOWN` are distinguishable in the
      output and produce different sentences.
- [ ] With the daemon stopped, runtime aspects are `UNKNOWN` and the report says
      what to do about it.
- [ ] A stale snapshot is flagged with its age.
- [ ] Coverage gaps never change `doctor`'s exit code.
- [ ] `doctor` performs zero writes — asserted by the existing filesystem audit.
- [ ] Computing coverage triggers no process scan, no kernel read and no log
      parse.
- [ ] `--json` and the human-readable form are two renderings of one structure,
      asserted field by field.
- [ ] The diagnosis states "no dominant process" **only** when process
      attribution is `OK`, and a different sentence otherwise — asserted
      directly on the wording.
- [ ] An `UNAVAILABLE` aspect produces `not_evaluable` on the rules that read it
      and leaves every other finding's confidence untouched.
- [ ] The incident record carries the coverage states from when it opened.
- [ ] `doctor` completes in under 500 ms with 50 sites.

## Verification

- `tests/test_coverage.py` over synthetic hosts: everything present; a 3.10
  kernel with no PSI; a container with no `/dev/kmsg`; a non-root run; one site
  with an unrecognised format; all sites unrecognised; no PHP installed; and a
  host with 200 gaps.
- `tests/test_cli.py` extended — the section renders at 80 columns, pipes
  cleanly, exits 0 with gaps present, and `--json` matches the text form.
- A **wording test**, in the style of SPEC-007's existing ones: with process
  attribution `UNAVAILABLE`, the diagnosis of an otherwise identical incident
  must not contain the sentence it produces when attribution is `OK`. This is
  the assertion the whole spec exists for.
- A container run against the real daemon, with PSI and `/dev/kmsg` deliberately
  made unavailable, confirming the report is accurate about its own blindness.
- The existing lifecycle audit, unchanged, confirming `doctor` still writes
  nothing.

## Out of scope

- Diagnosing incidents — [SPEC-012](SPEC-012-system-deterministic-findings.md).
- Closing a gap by changing the server — forbidden.
- A coverage score, grade or percentage.
- Coverage history, trending or alerting.
- Documenting an enriched `log_format` — README §68, manual and opt-in.
