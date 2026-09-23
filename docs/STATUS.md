# Project Status

Last updated: 2026-09-22

## Current phase

Phases 1 to 6 are complete, except for one check that needs a host with real
systemd. Phase 7 (AI) is optional and deliberately not started.

**The pipeline now answers the question the project exists for.** Load opens an
incident, the incident freezes what the logs showed, and `aadoctor diagnose`
reads that evidence and says which site, path, address or error is the probable
source — deterministically, with the numbers attached, and with no AI involved
at any point.

```text
aaPanel -> discovery -> log tail -> parsing -> aggregation -> load
        -> incident -> facts -> rules -> findings -> correlation -> diagnosis
```

## Current objective

Put it on a real aaPanel server. Every threshold in SPEC-006 and SPEC-007 is an
unvalidated guess, and no amount of further code will change that.

## Completed

Documentation:

- `README.md`, [/CLAUDE.md](../CLAUDE.md), 9 specs, 8 ADRs, backlog, changelog.
- `LICENSE` — MIT, Alex Torres.

Code (version `0.1.0-dev`), SPEC-001 to SPEC-003 committed:

- `src/aadoctor/` — `paths`, `config`, `environment`, `service`, `daemon`,
  `cli`, `runtime`, plus `discovery/` (SPEC-002), `collectors/` (SPEC-003 and
  SPEC-006), `parsers/` (SPEC-004), `analyzers/` (SPEC-005, SPEC-006 and
  SPEC-007), `rules/` (SPEC-007) and `storage/`.
- Commands: `--version`, `doctor`, `status`, `sites`, `top`, `incidents`,
  `show`, `diagnose`, `enable`, `disable`, `update`, `uninstall [--purge]`,
  `daemon`.
- The daemon discovers sites, follows their logs, parses every line, counts
  them into bounded windows, watches the load average, freezes those windows
  into an incident when the server is pressed, publishes a snapshot for the
  CLI, and persists offsets. It still concludes nothing: `diagnose` computes
  on demand from the stored incident and never writes back.
- `install.sh`, `uninstall.sh`, `tools/package.sh`,
  `systemd/aadoctor.service`, `config.example.toml`, `VERSION`.
- `tests/` — 615 unit tests, 12 vhost fixtures, three container integration
  suites.

Verified, in disposable Linux containers:

- 615/615 unit tests on Python 3.8 and 3.12, as root and as a non-root user.
  `compileall` clean.
- 42/42 lifecycle checks and 32/32 release checks — SPEC-001 unaffected.
- The real daemon over a 200,000-line access log: none of that history read,
  rotation and truncation detected live, a restart resuming without re-reading.
- The real daemon on a three-site burst with a load curve from 0.5 to 2.9 per
  core: **one** incident, severity critical, whose peak snapshot holds 8,000
  requests for the busiest site, 5,000 for the busiest path, 4,620 for the
  busiest address, 120 responses of 502 and 40 upstream timeouts — 16 KB,
  mode 640, naming no cause.
- 27/27 diagnosis checks across all three SPEC-007 scenarios, end to end
  against the real daemon: the dominant site named with its path and address
  at VERY HIGH; ten evenly loaded sites answered with "no clear log-based
  cause identified"; and a site holding only a third of the traffic named
  anyway on 300 upstream timeouts and 1,500 responses of 502.

Decisions recorded:

- [ADR-008](adr/ADR-008-minimum-python-version.md) — minimum Python 3.8.
- MIT licence; `CHANGELOG.md` at the repository root, per README §11.
- SPEC-002: `off` and `/dev/null` disabled, `include` not followed.
- SPEC-003: no partial line stored, files opened per poll, one `state.json`.
- SPEC-004: two supported formats and no guessing beyond them; a `kind` that is
  not a finding.
- SPEC-005: buckets by arrival time, cardinality bounded by keeping the
  heaviest keys, a published file rather than a socket for the CLI.
- SPEC-006: hysteresis instead of a cooldown, an incident that does not depend
  on the rules engine to exist, and traffic frozen at the peak as well as at
  the opening.
- SPEC-007: a diagnosis computed on demand rather than stored in the incident;
  quality caps scoped to the input they damage; evidence weighted by each
  finding's own strength; and a site named only when something ties it to the
  server, not merely to itself.

## In progress

Nothing.

## Not started

- SPEC-009, the AI explainer. Optional by design, and deliberately held: it
  explains what the deterministic engine decided, so it should not be built
  until two or three real incidents show the engine decides well. An
  explanation layer over unchecked answers only makes them more convincing.
- The first published release. `tools/package.sh` builds the artifact; nothing
  has been tagged or uploaded. `VERSION` still reads `0.1.0-dev`.

## Known risks

- ~~**Never run under real systemd.**~~ **Retired.** `install.sh`,
  `aadoctor enable` and `aadoctor status` ran on a real aaPanel host on
  2026-09-22; the unit installed, loaded and reports `active (enabled)`.
  AAD-004 and Phase 1 close once a `disable` and a `uninstall --purge` have
  also been exercised there.
- ~~**The trigger may fire too readily.**~~ **Answered: it does not.** The
  first night in production produced twenty incidents in twelve hours on a
  2-CPU server, peaking between 1.4 and 43.6 per core — load 87 at the worst.
  They are not threshold noise; that machine is genuinely in distress, and
  `trigger_per_cpu = 1.0` found it. The number stands unchanged.
- ~~**Never run against a real aaPanel installation.**~~ **Retired.** Installed
  on a production aaPanel server on 2026-09-22: 25 sites discovered, 21 access
  and 21 error logs configured, 40 followed. **The real `log_format` parses** —
  `top` renders sites, paths, addresses, status classes and error kinds from
  live traffic. This was the largest risk for four rounds.
- **What the first night actually taught, in one place.** Twenty incidents,
  twelve hours, one server. Every diagnosis came back inconclusive or LOW, and
  every one of them was right: the request rate was flat or falling while the
  load multiplied — 0.2× the preceding period at the worst incident, load 42
  per core. `vmstat` showed 955 KB/s of swap-in sustained across five days of
  uptime on a machine with 2 GB of RAM serving 25 sites. The cause was memory
  pressure, and it is invisible to a web-server log by construction.

  The engine was never going to name it, and it never pretended to. That is
  the result: **on this server aaDoctor's value was telling the administrator
  where not to look.** It also means the positive path — naming a site with
  high confidence — is still unproven in the field, and needs a server whose
  load genuinely comes from HTTP.

  Four defects came out of that night, all of them in what the tool *said*
  rather than what it computed: a summary claiming the logs showed nothing
  when there had been nothing to look at; a summary contradicting its own
  numbers; a site named beside a load its traffic could not account for; and
  an error pattern that had never matched a line Nginx writes. Every one was
  found by reading real output, not by a test.
- **Thresholds are unvalidated — apart from the load trigger.**
  `critical_per_cpu = 2.0` and the sixteen values in `[rules]` have still not
  been checked against a server whose load comes from traffic.
  A machine stuck on I/O shows a high load with idle CPUs — which is exactly
  what the first server did, and it is expected: load is a trigger, not a
  diagnosis. The rule thresholds still decide *what gets named*, and that is
  the part no server has exercised yet.
- **A wrong answer is worse than no answer.** The engine will put a site, a
  path and an address on the screen with a confidence next to them. The
  defences against a false positive — minimum volume, the anchor requirement,
  scoped quality caps, net scoring under disagreement, and now the note when
  the request rate did not rise — held on the first server, in the sense that
  nothing was ever confidently wrong. They have still never been tested
  against a real incident that traffic *did* cause.
- **Container CPU quota is ignored.** `os.cpu_count()` reports the host's CPUs,
  so inside a container with a cgroup limit the load per core is understated.
  TBD in SPEC-006.
- **Incident windows can be short.** An incident opening shortly after startup
  freezes a window with only seconds of data. It is labelled, not hidden, and
  it caps the confidence of everything read from it.
- **`site × ip × path` does not exist.** The diagnosis can say a site had a
  dominant path and a dominant address; it cannot say the address called the
  path, and deliberately does not imply it.
- **A dominant path or address on a site that is not the busiest is not
  reported** (SPEC-007). The alternative — taking the strongest share anywhere
  — would make a small quiet site win every time.
- **TRAFFIC_SPIKE needs 60 seconds of preceding data**, so it is often
  `not_evaluable` on an incident that opens shortly after the daemon starts.
  That is reported rather than read as "no spike".
- **A custom `log_format` yields no traffic data**, so an incident from such a
  server would hold load figures and an empty traffic picture.
- **Counts can be slightly high after a crash** (at-least-once, SPEC-003), and
  **aggregates do not survive a restart** (SPEC-005).
- **Multi-line error entries are not joined** (SPEC-004), **`include` is not
  followed** (SPEC-002), **inode reuse with a larger replacement is
  undetectable** (SPEC-003).
- **Root read access.** Reading every site's logs without touching aaPanel
  permissions implies running as root (README §57). Deliberate.

## Next recommended work

1. **Install on a real aaPanel server.** `aadoctor doctor`, `aadoctor sites`,
   then the daemon for a day, then `aadoctor top`, `aadoctor incidents` and
   `aadoctor diagnose` on whatever it recorded. Three things get answered:
   whether the real `log_format` parses, whether `trigger_per_cpu = 1.0` is a
   sensible line on that machine, and — the new one — whether the diagnosis it
   produces is the one an administrator would have reached by hand.
2. On a Linux host with systemd: `./install.sh`, `aadoctor enable`, confirm the
   unit is active, `aadoctor disable`, `aadoctor uninstall --purge`. Closes
   AAD-004 and Phase 1.
3. Tag `v0.1.0` and attach both files produced by `tools/package.sh` — **after**
   step 1, not before. Decided deliberately: the first server is installed from
   a checkout, which `install.sh` supports and the lifecycle script exercises.
   Pinning a version number before any field data would most likely pin one
   that needs correcting in its first week.
4. SPEC-009, the AI explainer — last, and optional.

Step 1 is no longer just useful, it is the gate. Everything up to here can be
checked against a fixture; whether the answers are *right* cannot.
