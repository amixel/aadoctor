# Project Status

Last updated: 2026-09-23

## Current phase

Phases 1 to 6 are complete, except for one check that needs a host with real
systemd. Phase 7 (AI) is optional and deliberately not started.

Phases 8 to 10 are **planned and documented, not started**: SPEC-010 to SPEC-015
are Draft, and nothing in them exists in the repository. They close the gap the
first production server exposed — see `Current objective`.

Phase 11 — WordPress security — is **planned and documented, and its second half
is blocked**. SPEC-016 (audit, read-only) and SPEC-017 (quarantine) are Draft.
SPEC-017 contradicts ADR-001 and may not be implemented until ADR-010 is
accepted; ADR-010 is `Proposed`. This is a second domain, not a continuation:
security and performance stay apart, and no security finding ever enters a
diagnosis.

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

**Done: it is on a real aaPanel server**, since 2026-09-22. See `Known risks`
for what that produced — it retired the two largest risks and cost nine
defects, all of them found by reading real output.

Field validation produced two objectives, and they are independent.

**1. The core HTTP diagnosis is implemented and still unproven in the positive
direction.** That server's load comes from memory pressure, not from HTTP, so it
exercises only aaDoctor's ability to *decline* to name a culprit. Naming one
correctly — the reason the project exists — needs a server whose load genuinely
comes from traffic.

**2. Field validation revealed a diagnostic gap the tool cannot close as
built.** Twenty incidents in twelve hours, load to 43.6 per core, request rate
flat or falling. The cause was memory pressure with roughly 1 MB/s of sustained
swap on a 2 GB machine serving 25 sites, and a web-server log cannot show that
by construction. aaDoctor was right every time and useful only negatively.

Six Draft specs now cover that gap — system resources, process attribution,
system findings, PHP-FPM pools, kernel events and diagnostic coverage. **The
next planned work is SPEC-010.** Nothing in it is implemented; nothing below
should be read as a capability that exists.

## Completed

Documentation:

- `README.md`, [USAGE.md](USAGE.md), [/CLAUDE.md](../CLAUDE.md), 17 specs,
  11 ADRs, backlog, changelog.
  Nine specs are Implemented or In Progress; **SPEC-010 to SPEC-017 are Draft**
  and describe work that does not exist yet. ADR-009, ADR-010 and ADR-011 are
  `Proposed`, not accepted — and ADR-010 contradicts ADR-001, which is
  `Accepted`, so that contradiction is live and recorded rather than resolved.
- `LICENSE` — MIT, Alex Torres.

Code (version `0.1.0-dev`), all of it committed and pushed:

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
- `tests/` — 647 unit tests, 12 vhost fixtures, three container integration
  suites.

Verified, in disposable Linux containers:

- 647/647 unit tests on Python 3.8 and 3.12, as root and as a non-root user.
  `compileall` clean.
- 49/49 lifecycle checks and 37/37 release checks — SPEC-001 unaffected.
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

- **SPEC-010 to SPEC-015 — system observability and diagnosis.** Draft only.
  Memory, swap, CPU, I/O and PSI sampling; process attribution; the system
  findings and the two-domain diagnosis; PHP-FPM pool discovery; kernel and OOM
  events; and the coverage self-check. No collector, no rule and no command
  exists for any of it. Backlog AAD-070 … AAD-122, all `Planned`.
- **SPEC-016 and SPEC-017 — WordPress security.** Draft only. No scanner, no
  quarantine, no command and no configuration key exists. Backlog AAD-130 …
  AAD-143, all `Planned`. SPEC-016 is buildable today and needs no ADR; **all of
  SPEC-017 is blocked on ADR-010**, which is `Proposed` and which contradicts
  the `Accepted` ADR-001.
- SPEC-009, the AI explainer. Optional by design, and deliberately held: it
  explains what the deterministic engine decided, so it should not be built
  until two or three real incidents show the engine decides well. An
  explanation layer over unchecked answers only makes them more convincing —
  and the engine is now planned to grow a whole second half, which makes the
  case for waiting stronger, not weaker.
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

  **Nine defects came out of that one server**, and not one of them was found
  by a test:

  | What broke | Why the suite missed it |
  |---|---|
  | Scripts not executable in a clone | tests invoke them as `bash x.sh`, and a Windows bind mount reports every file `rwxrwxrwx` |
  | `install.sh` destroyed a checkout in `/opt/aadoctor` | tests always installed from elsewhere |
  | `top` ran past 140 columns | every fixture path was short and tidy |
  | `status` still said diagnosis was unimplemented | a test asserted that sentence |
  | "the logs show no dominant site" on nine requests | no fixture had a window that thin |
  | `NOT_FOUND_FLOOD` could not fire below 16.7 req/s | the thresholds multiply, and no fixture was small |
  | `open() failed` matched no line Nginx writes | the fixture was written from imagination |
  | A summary contradicting its own numbers | the guard has two denominators |
  | An interrupted incident shown as a bare dash | nothing had been interrupted before |

  Seven of the nine are in what the tool **said**, not in what it computed.
  The lesson is narrow and repeatable: **a fixture written from what a format
  looks like proves only that the code agrees with its author.** Real output,
  read by a person, is a different instrument.
- **Thresholds are unvalidated — apart from the load trigger.**
  `critical_per_cpu = 2.0` and the sixteen values in `[rules]` have still not
  been checked against a server whose load comes from traffic.
  A machine stuck on I/O shows a high load with idle CPUs — which is exactly
  what the first server did, and it is expected: load is a trigger, not a
  diagnosis. The rule thresholds still decide *what gets named*, and that is
  the part no server has exercised yet.
- **The planned system thresholds are weaker than the traffic ones.** SPEC-012
  defines sixteen numbers and exactly one of them has any observation behind it
  — the 512 KB/s swap-out floor, derived from the single server that motivated
  the work. The rest are judgement. When that spec is implemented it will add a
  second way for aaDoctor to be confidently wrong, on a class of evidence that
  sounds more authoritative than a traffic share because it comes from the
  kernel. The defences planned are the ones that already worked once: a system
  anchor rule, caps on projections, and two scores that are never added.
- **The planned security work can damage a site, which nothing else here can.**
  SPEC-016 is read-only and safe by construction, but its signal weights are the
  least validated numbers ever written into this project and will produce false
  positives on first contact with real sites. SPEC-017 then makes a false
  positive actionable. The defences planned are the ones that make the action
  reversible — copy-verify-unlink, a finding id instead of a path, a hash that
  must still match, core files refused outright — and none of them has met a
  real server. **The first real scan should be treated the way the first
  production server was: as the instrument that finds the defects**, not as a
  result.
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

Step 1 of the previous list — *install on a real aaPanel server* — is **done**,
on 2026-09-22. What it answered, and what it opened, is in `Current objective`
and `Known risks`.

1. **SPEC-010 — system resource monitoring.** The first of the six new drafts
   and the one the rest rest on. Small, self-contained, no new dependency, and
   on its own it already lets `status` say that a machine is out of memory.
   SPEC-011 needs its denominators; SPEC-012 needs both. It is first because it
   addresses the gap the field actually found, and because it does not depend on
   finding a particular kind of server.
2. On a Linux host with systemd: `./install.sh`, `aadoctor enable`, confirm the
   unit is active, `aadoctor disable`, `aadoctor uninstall --purge`. Closes
   AAD-004 and Phase 1. Independent of everything else here.
3. **A server whose load comes from HTTP.** The positive path — naming a site
   with high confidence — is still unproven, and no amount of further building
   proves it. This is a waiting item, not a work item, and it should not block
   step 1.
4. SPEC-011, then SPEC-012 — process attribution and the system findings. After
   SPEC-012 the tool can answer the memory case end to end.
5. Tag `v0.1.0` and attach both files produced by `tools/package.sh`. Deferred
   deliberately: the first server is installed from a checkout, which
   `install.sh` supports and the lifecycle script exercises, and pinning a
   version before the system half exists would pin one that needs correcting
   almost immediately.
6. SPEC-013, SPEC-014, SPEC-015 — PHP-FPM pools, kernel events, coverage.
   SPEC-014 is independent of SPEC-010 and SPEC-011 and can move earlier if an
   OOM turns out to be the common case.
7. SPEC-009, the AI explainer — last, and optional.

The gate has moved. Everything up to SPEC-008 could be checked against a
fixture, and step 1 of the old list proved that is not enough — nine defects,
none found by a test, seven of them in what the tool *said*. The new specs are
larger and less validated than anything before them, so the standard is the
same: real output, read by a person, before any of it is believed.
