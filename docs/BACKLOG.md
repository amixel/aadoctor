# aaDoctor Backlog

Work items only. Not an idea dump. Ideas without a decision go to `Parking Lot`
or stay out of this file.

Item states:

```text
Planned      accepted, not started
In Progress  being worked on now
Blocked      waiting on a decision or another item
Implemented  code exists in the repository, verification still pending
Done         implemented and verified
```

Nothing is marked `Implemented` without code in the repository, and nothing is
marked `Done` without evidence that it was actually exercised.

Buckets:

* **Current** — the phase being executed now.
* **Next** — accepted and sequenced, not started.
* **Later** — accepted, further out; details may still change.
* **Parking Lot** — deliberately deferred; needs a decision before moving up.
* **Done** — completed and verified.

Rules for agents working this backlog are in [/CLAUDE.md](../CLAUDE.md).

---

## Current

Phase 1 — Foundation, complete except for one check that needs real systemd.

AAD-001, AAD-002, AAD-003, AAD-005, AAD-006 and AAD-007 are `Done`: implemented
and verified in a Linux container — 647 unit tests on Python 3.8 and 3.12, 49
lifecycle checks and 37 release checks (see [DEVELOPMENT.md](DEVELOPMENT.md)).

AAD-004 stays `Implemented`: a container has no init system, so the unit has
never been loaded by real systemd.

### AAD-001 — Project skeleton

Status: Done
Phase: 1
Priority: High
Related spec: [SPEC-001](specs/SPEC-001-installation-lifecycle.md)
Dependencies: None

Goal:
Create the repository layout of README §11 with a runnable entry point and a
minimal CLI dispatcher. No diagnostic logic.

Acceptance:
- `src/aadoctor/` package tree exists and matches README §11.
- `python3 ./aadoctor --help` lists implemented commands and exits 0.
- An unknown command exits non-zero with a usable message.
- Standard library only.
- Running from source writes nothing outside the repository.

---

### AAD-002 — Configuration loading

Status: Done
Phase: 1
Priority: High
Related spec: [SPEC-001](specs/SPEC-001-installation-lifecycle.md)
Dependencies: AAD-001

Goal:
Load `/etc/aadoctor/config.toml` with the defaults of README §14, falling back
to built-in defaults when the file or a key is absent.

Acceptance:
- `config.example.toml` matches the built-in defaults exactly. It ships only the
  keys this version reads, not the full set in README §14; the rest arrive with
  the phase that uses them.
- A missing config file yields defaults, not a crash.
- A malformed config produces a clear error naming file and key; the daemon does
  not start with a half-applied configuration.
- `[mysql] enabled` and `[ai] enabled` default to `false`.
- Config is read through one place, not scattered literals.

---

### AAD-003 — aaPanel environment detection

Status: Done
Phase: 1
Priority: High
Related spec: [SPEC-002](specs/SPEC-002-aapanel-discovery.md)
Dependencies: AAD-001

Goal:
Detect aaPanel, the Nginx vhost directory and log readability, and report it
through `aadoctor doctor` without changing anything.

Acceptance:
- Detects `/www/server/panel/` and the vhost directory.
- Reports clearly when aaPanel is absent (README §80) or Nginx is absent
  (README §81) and starts no monitoring in those cases.
- `doctor` performs zero writes anywhere on the system.
- Unreadable paths are warnings, not fatal errors.

---

### AAD-004 — systemd lifecycle

Status: Implemented
Phase: 1
Priority: High
Related spec: [SPEC-001](specs/SPEC-001-installation-lifecycle.md)
Dependencies: AAD-001, AAD-002

Goal:
Ship `aadoctor.service` and wire `aadoctor enable` / `aadoctor disable` to it.

Acceptance:
- Unit matches README §56, including `NoNewPrivileges` and `PrivateTmp`.
- `enable` and `disable` are idempotent; repeated runs give the same end state.
- After `disable`, no aaDoctor process remains running.
- Only `aadoctor.service` is ever touched; no other unit is restarted.

Remaining for `Done`: the unit has only ever been exercised against a stubbed
`systemctl`. On a host with real systemd, confirm that it loads, starts, keeps
running, restarts on failure and stops cleanly on `disable`.

---

### AAD-005 — Idempotent installation

Status: Done
Phase: 1
Priority: High
Related spec: [SPEC-001](specs/SPEC-001-installation-lifecycle.md)
Dependencies: AAD-004

Goal:
`install.sh` implementing the ordered steps of README §49 for a local checkout
or unpacked release archive, with configuration preservation.

Acceptance:
- Running the installer twenty times yields the same end state as once.
- An existing `/etc/aadoctor/config.toml` is never overwritten.
- No partial installation is left silently behind on failure.
- `set -euo pipefail`; no `git` requirement.
- Nothing is written under `/www/`.
- Installing does not enable or start the service.

Download and SHA256 verification (README §49 steps 6 and 7) live in AAD-007.

---

### AAD-006 — Clean uninstall and purge

Status: Done
Phase: 1
Priority: High
Related spec: [SPEC-001](specs/SPEC-001-installation-lifecycle.md)
Dependencies: AAD-005

Goal:
`aadoctor uninstall` and `aadoctor uninstall --purge` per README §53–§55.

Acceptance:
- Default uninstall preserves `/etc/aadoctor/` and `/var/lib/aadoctor/`.
- `--purge` removes every path listed in README §54 and nothing else.
- After purge there is no cron entry, firewall rule, permission change or config
  change attributable to aaDoctor.
- Final output states that no aaPanel files were modified — and that is true.

---

### AAD-007 — Release packaging, checksum verification and update

Status: Done
Phase: 1
Priority: Medium
Related spec: [SPEC-001](specs/SPEC-001-installation-lifecycle.md)
Dependencies: AAD-005

Goal:
Publish versioned release artifacts and let `install.sh` fetch and verify one,
then implement `aadoctor update` on top of it (README §46–§52).

Acceptance:
- A release publishes `aadoctor-<version>.tar.gz` and its `.sha256`.
- `install.sh` with no local source downloads the release and verifies the
  checksum before extracting anything.
- A checksum mismatch aborts before any file is replaced, and removes the
  temporary download.
- `aadoctor update` preserves `/etc/aadoctor/` and `/var/lib/aadoctor/`, and
  restarts only `aadoctor.service`, only if it was running.
- No `git` requirement on the server.

Verified with a locally built and served release: reproducible artifact, checksum
verified before extraction, corrupted artifact rejected with the installation
left intact, missing release reported clearly, and the real repository — which
has no releases yet — correctly refusing to guess a version.

One step remains for the first real release: tag `v<version>` on GitHub and
attach both files from `tools/package.sh`.

---

## Next

Phase 2 — Log tail. Phase 3 — Aggregation.

Phase 2 is complete: discovery (AAD-010, AAD-011) and incremental log reading
(AAD-012, AAD-013, AAD-014) are `Done`.

Phase 3 is complete: parsing (AAD-020, AAD-021) and aggregation (AAD-022,
AAD-023, AAD-024) are `Done`. `aadoctor top` now shows what the last minutes
looked like.

Phase 4 is complete too: load collection and incident detection (AAD-030 …
AAD-034) are `Done`. See the Later section.

### AAD-010 — Site and vhost discovery

Status: Done
Phase: 2
Priority: High
Related spec: [SPEC-002](specs/SPEC-002-aapanel-discovery.md)
Dependencies: AAD-003

Goal:
Parse the aaPanel Nginx vhost files and extract `server_name` per site.

Acceptance:
- Multiple `server_name` values map to one site with a stable primary name.
- A malformed or unreadable vhost is skipped with a warning; discovery continues
  for the rest.
- Discovery re-runs on the configured interval; a new site appears without a
  daemon restart.
- Vhost files are opened read-only.

Implemented in `src/aadoctor/discovery/`. The daemon re-runs discovery every
`[discovery] interval_seconds` and logs only what changed.
---

### AAD-011 — Access and error log mapping

Status: Done
Phase: 2
Priority: High
Related spec: [SPEC-002](specs/SPEC-002-aapanel-discovery.md)
Dependencies: AAD-010

Goal:
Resolve each site to its `access_log` and `error_log` from the vhost directives
rather than from filename conventions.

Acceptance:
- Paths come from the parsed directives when present.
- A site with no access log is reported, still monitored for errors when
  possible, and does not break discovery (README §39 WARN example).
- `access_log off;` is recognized and produces no bogus path.
- A missing or unreadable log file is a per-site warning only.

Implemented: paths come from the directives, `off` and `/dev/null` are recorded
as disabled, and "configured" is tracked separately from "the file exists".

---

### AAD-012 — Incremental log reader

Status: Done
Phase: 2
Priority: High
Related spec: [SPEC-003](specs/SPEC-003-incremental-log-monitoring.md)
Dependencies: AAD-011

Goal:
Read only new bytes from each tracked log file.

Acceptance:
- First observation of a file starts at end of file; a 14 GB log is never read
  from byte zero.
- Only new bytes are read per poll.
- A partial trailing line is held until complete, never split.
- One unreadable file does not stop the others (README §65).

---

### AAD-013 — Persistent offsets

Status: Done
Phase: 2
Priority: High
Related spec: [SPEC-003](specs/SPEC-003-incremental-log-monitoring.md)
Dependencies: AAD-012

Goal:
Persist `path`, `inode` and `offset` per file so a restart resumes where it
stopped.

Acceptance:
- State shape matches README §18.
- Writes are atomic; a crash mid-write cannot leave unreadable state.
- Corrupt or unreadable state degrades to a fresh tail-from-end plus a log line.
- State lives under `/var/lib/aadoctor/`, never under `/www/`.

---

### AAD-014 — Log rotation handling

Status: Done
Phase: 2
Priority: High
Related spec: [SPEC-003](specs/SPEC-003-incremental-log-monitoring.md)
Dependencies: AAD-013

Goal:
Detect rotation, truncation and delete/recreate, and resume correctly.

Acceptance:
- A changed inode is treated as rotation; the new file is read from offset 0.
- `size < offset` on the same inode is treated as truncation; the offset resets.
- A temporarily missing file is retried, not treated as fatal.
- aaDoctor never rotates, truncates, moves or deletes an aaPanel log.

---

### AAD-020 — Nginx access log parser

Status: Done
Phase: 3
Priority: High
Related spec: [SPEC-004](specs/SPEC-004-nginx-log-parsing.md)
Dependencies: AAD-012

Goal:
Parse the aaPanel default Nginx access format into a structured record, with
optional timing fields when present.

Acceptance:
- Extracts timestamp, IP, method, path, query, status, bytes, referer,
  user agent.
- `request_time` / `upstream_response_time` are not parsed: the supported
  formats do not carry them and guessing at an extra field would be worse than
  admitting they are missing. See SPEC-004.
- An unparseable line increments a counter and is skipped (README §66).
- No regex with catastrophic backtracking on hostile input.

---

### AAD-021 — Nginx error log parser

Status: Done
Phase: 3
Priority: High
Related spec: [SPEC-004](specs/SPEC-004-nginx-log-parsing.md)
Dependencies: AAD-012

Goal:
Classify known error-log patterns: upstream timeouts, FastCGI failures, Nginx
resource limits, PHP errors.

Acceptance:
- Recognizes the patterns of README §30 and §31.
- A multi-line PHP stack trace does not produce one event per line. Partly met:
  a continuation line produces no event at all and is counted as unparsed.
  Joining it to the event before it is deferred - see the Parking Lot.
- Unknown lines are counted and ignored.
- Classification is table-driven: a new pattern is one entry, not a new layer.

---

### AAD-022 — Request aggregation

Status: Done
Phase: 3
Priority: High
Related spec: [SPEC-005](specs/SPEC-005-traffic-aggregation.md)
Dependencies: AAD-020

Goal:
Maintain moving windows (10 s, 1 min, 5 min) of request counters in memory.

Acceptance:
- Windows roll on time, not on request count.
- Memory stays bounded under sustained high-cardinality traffic.
- Individual requests are not retained indefinitely.
- Aggregation cost stays negligible relative to log volume (README §58).

Implemented with ten-second buckets keyed by arrival time; a bucket that leaves
the window is dropped whole.

---

### AAD-023 — Top site / IP / path calculations

Status: Done
Phase: 3
Priority: High
Related spec: [SPEC-005](specs/SPEC-005-traffic-aggregation.md)
Dependencies: AAD-022

Goal:
Compute top-N per site, IP, path and user agent with share of total, under
cardinality limits.

Acceptance:
- Top-N and cardinality caps are configurable with documented defaults.
- Query strings are normalized per SPEC-005 so `/p?id=1..N` is one path.
- Shares are computed against an explicit denominator (server or site).
- A truncated tail is reported as `other`, not silently dropped.

Implemented, including the property that matters most: a flood of unique keys
cannot push the dominant key out of the table.

Top-N and the caps are constructor arguments rather than `config.toml` keys -
nothing has needed to change them yet, and `config.toml` holds only keys
something reads.

---

### AAD-024 — HTTP status aggregation

Status: Done
Phase: 3
Priority: Medium
Related spec: [SPEC-005](specs/SPEC-005-traffic-aggregation.md)
Dependencies: AAD-022

Goal:
Count status classes and the specific codes of README §29.

Acceptance:
- 2xx/3xx/4xx/5xx classes plus 403, 404, 408, 429, 499, 500, 502, 503, 504.
- Counters available per site and per server, per window.
- A missing or non-numeric status does not corrupt the counters.

Implemented: every status seen is counted exactly, and the classes are derived
from them rather than kept separately, so the two can never disagree. A request
whose status the parser could not read is still counted as a request.

---

## Later

Phase 6 — the rest of the CLI reports. Phase 7 — AI explanation.

Phase 4 is complete: load collection, CPU count, load per core, incident
correlation and incident persistence (AAD-030 … AAD-034) are `Done`.

**Phase 5 is complete.** All nine findings (AAD-040 … AAD-048) are `Done`,
correlated into a diagnosis, and rendered by `aadoctor diagnose` (AAD-053).
The pipeline now answers the question the project exists for, deterministically
and without AI:

```text
aaPanel -> discovery -> log tail -> parsing -> aggregation -> load
        -> incident -> facts -> rules -> findings -> correlation -> diagnosis
```

**Phase 6 is complete.** AAD-050 … AAD-055 are `Done`. Three of them had
acceptance criteria written before SPEC-006 and SPEC-007 separated measurement
from interpretation, and those criteria are amended in place with the reason:
`status` does not signal daemon health through its exit code, and neither
`incidents` nor `show` prints a finding or a confidence.

Phase 7 is optional by design: SPEC-009 explains what the engine above already
decided, and never decides anything itself. It is deliberately **not** next —
what the project needs now is two or three real incidents from a real server,
to find out whether the thresholds produce good diagnoses. An explanation
layer over answers nobody has checked would only make them more convincing.

### AAD-030 — Loadavg collector

Status: Done
Phase: 4
Priority: High
Related spec: [SPEC-006](specs/SPEC-006-load-incident-detection.md)
Dependencies: AAD-001

Goal:
Read `load1`, `load5`, `load15` from `/proc/loadavg` on the monitor interval.

Acceptance:
- Reads `/proc` directly; no subprocess in the loop (README §58).
- An unreadable `/proc/loadavg` disables load correlation with a warning instead
  of crashing the daemon.

---

### AAD-031 — CPU count detection

Status: Done
Phase: 4
Priority: High
Related spec: [SPEC-006](specs/SPEC-006-load-incident-detection.md)
Dependencies: AAD-030

Goal:
Determine the CPU count used to normalize load.

Acceptance:
- Detected once at startup, re-checked only on demand.
- Never returns 0; a failed detection falls back to 1 and logs it.
- TBD: whether a container CPU quota should override the host CPU count.

---

### AAD-032 — Load per core calculation

Status: Done
Phase: 4
Priority: High
Related spec: [SPEC-006](specs/SPEC-006-load-incident-detection.md)
Dependencies: AAD-031

Goal:
Compute `load_per_cpu = load1 / cpu_count` and compare it against
`trigger_per_cpu` and `critical_per_cpu`.

Acceptance:
- Thresholds come from config, not literals.
- Crossing the trigger threshold raises a trigger event; load alone is never
  reported as a diagnosis (README §21).

---

### AAD-033 — Incident window correlation

Status: Done
Phase: 4
Priority: High
Related spec: [SPEC-006](specs/SPEC-006-load-incident-detection.md)
Dependencies: AAD-032, AAD-023

Goal:
On a load trigger, freeze the preceding analysis window and attach the
aggregated traffic and error evidence to a candidate incident.

Acceptance:
- Window length comes from `[logs] window_seconds`.
- A cooldown prevents a sustained spike from creating one incident per tick.
- An ongoing incident is extended rather than duplicated.

---

### AAD-034 — Incident JSON persistence

Status: Done
Phase: 4
Priority: High
Related spec: [SPEC-006](specs/SPEC-006-load-incident-detection.md)
Dependencies: AAD-033

Goal:
Write incidents to `/var/lib/aadoctor/incidents/` in the README §37 shape and
apply retention.

Acceptance:
- Filename matches the incident id, e.g. `2026-09-22T12-41-20.json`.
- Atomic write; a partially written incident is never readable.
- Retention deletes only aaDoctor's own files, per `retention_days`.
- No cookies, headers, bodies or tokens are persisted (README §61).

---

### AAD-040 — TRAFFIC_SPIKE

Status: Done
Phase: 5
Priority: High
Related spec: [SPEC-007](specs/SPEC-007-deterministic-rules.md)
Dependencies: AAD-033

Goal:
Detect an abnormal rise in total request volume in the incident window.

Acceptance:
- Inputs, condition, evidence and confidence implemented as specified.
- Thresholds configurable; defaults documented.
- Evidence includes absolute counts and the comparison baseline used.

---

### AAD-041 — ONE_SITE_DOMINATING

Status: Done
Phase: 5
Priority: High
Related spec: [SPEC-007](specs/SPEC-007-deterministic-rules.md)
Dependencies: AAD-033

Goal:
Detect a single site holding an abnormal share of server requests.

Acceptance:
- Share computed against the server total in the window.
- A low-volume window cannot produce a high-confidence finding.
- Evidence lists site, requests and share.

---

### AAD-042 — ONE_URL_DOMINATING

Status: Done
Phase: 5
Priority: High
Related spec: [SPEC-007](specs/SPEC-007-deterministic-rules.md)
Dependencies: AAD-041

Goal:
Detect a single normalized path dominating a site's requests.

Acceptance:
- Share computed against the site total, not the server total.
- Uses the normalized path; a raw sample is kept as evidence.

---

### AAD-043 — ONE_IP_DOMINATING

Status: Done
Phase: 5
Priority: High
Related spec: [SPEC-007](specs/SPEC-007-deterministic-rules.md)
Dependencies: AAD-041

Goal:
Detect a single client IP responsible for an abnormal share of requests.

Acceptance:
- Output states possible bot / crawler / integration / flood, not "attack"
  (README §27).
- Proxy/CDN front-end situations are noted as a false-positive risk.

---

### AAD-044 — NOT_FOUND_FLOOD

Status: Done
Phase: 5
Priority: Medium
Related spec: [SPEC-007](specs/SPEC-007-deterministic-rules.md)
Dependencies: AAD-024

Goal:
Detect an abnormal rate and share of 404 responses.

Acceptance:
- Requires both an absolute rate floor and a share threshold.
- Evidence includes top 404 paths under the cardinality cap.

---

### AAD-045 — HTTP_5XX_SPIKE

Status: Done
Phase: 5
Priority: High
Related spec: [SPEC-007](specs/SPEC-007-deterministic-rules.md)
Dependencies: AAD-024

Goal:
Detect an abnormal rate of 5xx responses.

Acceptance:
- 502/503/504 reported separately from other 5xx.
- Evidence includes the affected sites.

---

### AAD-046 — UPSTREAM_TIMEOUT

Status: Done
Phase: 5
Priority: High
Related spec: [SPEC-007](specs/SPEC-007-deterministic-rules.md)
Dependencies: AAD-021

Goal:
Detect `upstream timed out` events in the window.

Acceptance:
- Counted per site, and per upstream when the log carries it.
- Correlated with 504 counts when both are present.

---

### AAD-047 — FASTCGI_ERROR

Status: Done
Phase: 5
Priority: Medium
Related spec: [SPEC-007](specs/SPEC-007-deterministic-rules.md)
Dependencies: AAD-021

Goal:
Detect FastCGI / PHP-FPM connection failures.

Acceptance:
- Covers the patterns of README §30 (FastCGI stderr, `connect() failed`,
  `upstream prematurely closed connection`, `recv() failed`).
- Never restarts or reloads PHP-FPM.

---

### AAD-048 — PHP_ERROR_SPIKE

Status: Done
Phase: 5
Priority: Medium
Related spec: [SPEC-007](specs/SPEC-007-deterministic-rules.md)
Dependencies: AAD-021

Goal:
Detect an abnormal rate of PHP errors in the error logs.

Acceptance:
- Fatal / memory exhausted / execution timeout counted distinctly inside the
  finding's evidence.
- Evidence includes the affected site and a bounded sample of messages.

---

### AAD-050 — `status` command

Status: Done
Phase: 6
Priority: High
Related spec: [SPEC-008](specs/SPEC-008-cli-reporting.md)
Dependencies: AAD-004, AAD-032

Goal:
Report version, detection, site counts, daemon state and current load, per
README §40.

Acceptance:
- Runs with the daemon stopped and says so.
- Read-only.
- Exits `0` whether or not the daemon is running.

Amended: the original criterion said "exit code reflects daemon health". It
does not, deliberately. SPEC-008 has `status` work with the daemon stopped and
say so plainly, and a state report that fails when there is no state to report
is one nobody can script around. Exit `4` is for commands that genuinely
require the daemon.

Verified: version, installation, aaPanel detection, site and log counts,
followed-log count, service state, configuration source, current load and open
incident all render; the load and incident lines are absent when the daemon has
published nothing. Tests in `tests/test_cli.py`.

---

### AAD-051 — `doctor` command

Status: Done
Phase: 6
Priority: High
Related spec: [SPEC-008](specs/SPEC-008-cli-reporting.md)
Dependencies: AAD-003, AAD-011

Goal:
Environment validation report per README §39.

Acceptance:
- Lists OK / WARN per check; warnings do not mask a failing environment.
- Modifies nothing.

Verified: every check renders as `[OK]`, `[WARN]` or `[FAIL]`; the closing line
agrees with the checks; exit `2` when the environment is not ready. The
lifecycle script asserts `doctor` performs zero writes anywhere.

---

### AAD-052 — `top` command

Status: Done
Phase: 6
Priority: High
Related spec: [SPEC-008](specs/SPEC-008-cli-reporting.md)
Dependencies: AAD-023

Goal:
Show the current window's top sites, paths, IPs and errors, per README §41.

Acceptance:
- Output fits an 80-column SSH terminal.
- States clearly when the daemon holds no data yet.

Verified: `--window 1m|5m`, `--site NAME` and `--json`; snapshot age shown and
a stale snapshot flagged; displaced `other` volume and poor parser coverage
called out. It states what happened and names no cause — a test asserts the
absence of any finding vocabulary.

---

### AAD-053 — `diagnose` command

Status: Done
Phase: 6
Priority: High
Related spec: [SPEC-008](specs/SPEC-008-cli-reporting.md)
Dependencies: AAD-040 … AAD-048

Goal:
Render the evidence-first diagnosis of README §42.

Acceptance:
- Every claim carries the evidence behind it (README §35).
- Confidence is shown and comes from the rules, never from AI.

---

### AAD-054 — `incidents` command

Status: Done
Phase: 6
Priority: Medium
Related spec: [SPEC-008](specs/SPEC-008-cli-reporting.md)
Dependencies: AAD-034

Goal:
List stored incidents with id, start time, duration, peak load per core and
severity.

Acceptance:
- Reads the incident directory only; never rewrites an incident.
- An empty directory produces a clear empty result, not an error.
- `--limit N` and `--json`; the table fits 80 columns.

Amended: the original goal said "primary finding and confidence". It shows
neither, deliberately. SPEC-006 and SPEC-007 split measurement from
interpretation: an incident records when the server was under load and what
the logs showed, and reading that is `diagnose`. A listing that quietly
started concluding would put a verdict where nobody would think to check it.
This entry predates that decision.

Verified: newest first, an open incident shown as open, an unreadable file
skipped rather than crashing the listing, and the empty case naming the
directory the daemon would write to.

---

### AAD-055 — `show` command

Status: Done
Phase: 6
Priority: Medium
Related spec: [SPEC-008](specs/SPEC-008-cli-reporting.md)
Dependencies: AAD-054

Goal:
Render one stored incident in full: the load at the start and at the peak, and
the traffic frozen with it.

Acceptance:
- Accepts an incident id; an unknown id exits `3` with a clear message.
- Renders from the stored JSON only, never recomputed.
- States how much data the window actually covers.
- An id that is not an incident id never reaches the filesystem.

Amended: the original said "renders findings and evidence". It renders facts.
Same reason as AAD-054 — `show` is the record, `diagnose` is the reading of
it, and keeping them apart is what lets anyone check the second against the
first.

Verified: load and traffic rendered, coverage stated, an open incident and one
with no traffic both handled, a malformed file reported without crashing, and
a path-shaped id refused. A test asserts `show` prints no finding, confidence
or suspect.

---

### AAD-060 — Optional AI configuration

Status: Planned
Phase: 7
Priority: Low
Related spec: [SPEC-009](specs/SPEC-009-ai-explainer.md)
Dependencies: AAD-002

Goal:
An `[ai]` configuration block, disabled by default, with key handling that keeps
the key out of logs, incidents and stdout.

Acceptance:
- Default `enabled = false`; everything works with AI off.
- Key read from environment or a protected file, never from `incident.json`.
- `doctor` reports AI as configured / not configured without printing the key.

---

### AAD-061 — Incident summarization payload

Status: Planned
Phase: 7
Priority: Low
Related spec: [SPEC-009](specs/SPEC-009-ai-explainer.md)
Dependencies: AAD-034

Goal:
Build the compact structured payload of README §10 from a stored incident.

Acceptance:
- Contains aggregates and findings only; no raw log lines.
- Bounded size regardless of incident size.
- An optional IP anonymization hook exists (README §62), off by default.

---

### AAD-062 — AI explainer

Status: Planned
Phase: 7
Priority: Low
Related spec: [SPEC-009](specs/SPEC-009-ai-explainer.md)
Dependencies: AAD-060, AAD-061

Goal:
Send the payload to the configured model and return a human explanation.

Acceptance:
- Any API failure degrades to the deterministic diagnosis (README §73).
- AI never changes findings or confidence.
- One call per explicit user request; no automatic call per incident.

---

### AAD-063 — `explain` command

Status: Planned
Phase: 7
Priority: Low
Related spec: [SPEC-009](specs/SPEC-009-ai-explainer.md)
Dependencies: AAD-062

Goal:
`aadoctor explain <incident>` on top of AAD-062.

Acceptance:
- With AI disabled, prints how to enable it and exits cleanly.
- Output separates deterministic evidence from AI interpretation.

---

## Later — system observability and diagnosis

Phases 8, 9 and 10. **All items are `Planned` and every governing spec is
`Draft`**; nothing here is designed enough to implement from yet.

Phase 7 keeps its number and is skipped in time, not cancelled. SPEC-009
explains what the deterministic engine decided, and the engine is about to grow
a whole new half — so it waits for that half to be field-validated. Phase 8
following Phase 6 does not mean Phase 7 is done.

**Why this exists.** Twelve hours on the first production server produced twenty
incidents peaking at 43.6 load per core, with HTTP traffic flat or falling. The
diagnoses were correctly inconclusive; the cause was memory pressure and
sustained swap, which is invisible to a web-server log by construction. aaDoctor
could say where *not* to look and nothing more. These three phases close that.

```text
Phase 8 — System observability      SPEC-010, SPEC-011
Phase 9 — System diagnosis          SPEC-012, SPEC-013, SPEC-014
Phase 10 — Coverage and hardening   SPEC-015
```

New id ranges follow the existing decade-per-spec convention: `07x` SPEC-010,
`08x` SPEC-011, `09x` SPEC-012, `10x` SPEC-013, `11x` SPEC-014, `12x` SPEC-015.
No existing id is reused or renumbered.

### AAD-070 — Memory and swap sampling

Status: Planned
Phase: 8
Priority: High
Related spec: [SPEC-010](specs/SPEC-010-system-resource-monitoring.md)
Dependencies: AAD-030

Goal:
Sample `/proc/meminfo` and the swap counters of `/proc/vmstat` on the monitor
tick, and derive swap rates in bytes per second.

Acceptance:
- `MemAvailable` presence is detected, never assumed and never estimated.
- Swap rates come from two samples and the system page size.
- The first sample publishes no rates; a counter reset publishes none either,
  with the reason recorded.
- An unreadable source yields nulls and an `unavailable` entry, never zeros.

---

### AAD-071 — CPU, iowait and steal sampling

Status: Planned
Phase: 8
Priority: High
Related spec: [SPEC-010](specs/SPEC-010-system-resource-monitoring.md)
Dependencies: AAD-070

Goal:
Sample the aggregate `cpu` line of `/proc/stat` and express each field as a
share of the CPU delta.

Acceptance:
- Shares are computed against the delta of all fields, not against wall time.
- `steal` is reported separately — a starved VPS must not read as a busy server.
- Unknown extra fields count toward the denominator without breaking the parse.

---

### AAD-072 — Pressure stall information

Status: Planned
Phase: 8
Priority: Medium
Related spec: [SPEC-010](specs/SPEC-010-system-resource-monitoring.md)
Dependencies: AAD-070

Goal:
Read `/proc/pressure/{cpu,memory,io}` where the kernel provides them.

Acceptance:
- The kernel's own `avg10`/`avg60`/`avg300` are stored unchanged; nothing is
  derived from the `total=` counter.
- Absence (CentOS 7, kernel 3.10) is a normal, reported state — and no finding
  may require PSI to be evaluable.

---

### AAD-073 — Disk and inode sampling

Status: Planned
Phase: 8
Priority: Medium
Related spec: [SPEC-010](specs/SPEC-010-system-resource-monitoring.md)
Dependencies: AAD-070

Goal:
`os.statvfs()` over at most four resolved mount points, on a longer interval.

Acceptance:
- Mounts resolved once at startup; `/proc/mounts` is not walked for more.
- Sampled at most every 60 seconds; a path that failed is skipped for a cool-off.
- Inode exhaustion is reported separately from space exhaustion.

---

### AAD-074 — Bounded sample ring and incident resource block

Status: Planned
Phase: 8
Priority: High
Related spec: [SPEC-010](specs/SPEC-010-system-resource-monitoring.md)
Dependencies: AAD-070, AAD-071, AAD-072, AAD-073, AAD-034

Goal:
Retain samples in a capped ring, publish the latest in `runtime.json`, and
freeze `start`, `peak` and `end` into the incident.

Acceptance:
- The ring never exceeds 64 records under any configuration.
- `peak` uses the load peak SPEC-006 already tracks; no second peak is defined.
- `end` is absent on an interrupted incident, not zero.
- **No resource threshold opens an incident.** Load remains the only trigger.
- A full sample completes in under 5 ms; zero writes outside
  `/var/lib/aadoctor/`.

---

### AAD-080 — Bounded process scan

Status: Planned
Phase: 8
Priority: High
Related spec: [SPEC-011](specs/SPEC-011-process-attribution.md)
Dependencies: AAD-074, ADR-009

Goal:
A two-phase scan of `/proc/<pid>/` producing top-N tables by CPU, memory and
I/O, only while the server is under pressure.

Acceptance:
- No scan runs while load is below `recovery_per_cpu`.
- A `comm` containing spaces or `)` does not shift any other field.
- Processes are matched between scans by `(pid, starttime)`; a reused pid yields
  no CPU rate.
- A process that exits mid-scan is skipped silently, not counted as a failure.
- A full scan over 400 processes completes within 150 ms.
- **No command line is persisted, logged or printed** — asserted by a test that
  plants a secret in a fixture and searches the serialized output for it.
- `/proc/<pid>/environ` is never opened — asserted by source inspection.

---

### AAD-081 — Process family aggregation

Status: Planned
Phase: 8
Priority: High
Related spec: [SPEC-011](specs/SPEC-011-process-attribution.md)
Dependencies: AAD-080

Goal:
Group processes into families from an explicit table, with totals per family.

Acceptance:
- Family totals are computed over every process seen, then the per-process table
  is truncated — never the reverse.
- The PHP-FPM pool label is extracted only by the anchored expression of
  ADR-009; anything else is discarded.
- A family is a label for grouping and is never presented as responsibility.
- Resident totals state that shared pages are counted more than once.

---

### AAD-082 — Process snapshot in the incident

Status: Planned
Phase: 8
Priority: Medium
Related spec: [SPEC-011](specs/SPEC-011-process-attribution.md)
Dependencies: AAD-081, AAD-034

Goal:
Attach the scans at the incident opening and at the load peak.

Acceptance:
- No `end` scan: the process table after recovery answers no question.
- A missing scan is an absent key, never an empty machine.
- `show` renders the block as facts and concludes nothing.

---

### AAD-090 — Memory and swap findings

Status: Planned
Phase: 9
Priority: High
Related spec: [SPEC-012](specs/SPEC-012-system-deterministic-findings.md)
Dependencies: AAD-074

Goal:
`MEMORY_PRESSURE`, `SWAP_PRESSURE`, `SWAP_THRASHING`.

Acceptance:
- SPEC-007's `strength()` and level bands are reused; no second scale exists.
- An inverted metric (lower is worse) scores correctly through the unmodified
  function.
- `MEMORY_PRESSURE` is evaluable without PSI.
- `SWAP_PRESSURE` is capped at LOW and never anchors a diagnosis.
- No swap configured yields `no_swap_configured`, never "no thrashing".

---

### AAD-091 — CPU and I/O findings

Status: Planned
Phase: 9
Priority: High
Related spec: [SPEC-012](specs/SPEC-012-system-deterministic-findings.md)
Dependencies: AAD-074

Goal:
`CPU_SATURATION` and `IO_PRESSURE`.

Acceptance:
- Steal above its note threshold appears prominently in the evidence.
- High load with low CPU busy is stated explicitly, not left to be inferred.
- When memory and I/O both fire with high swap rates, memory is primary and I/O
  is presented as its consequence — one problem, not two.

---

### AAD-092 — Disk and inode findings

Status: Planned
Phase: 9
Priority: Medium
Related spec: [SPEC-012](specs/SPEC-012-system-deterministic-findings.md)
Dependencies: AAD-073

Goal:
`DISK_SPACE_PRESSURE` and `INODE_PRESSURE`, per mount.

Acceptance:
- Reported per mount; a full `/boot` is not confused with a full `/`.
- Inode exhaustion is its own finding — the symptom is identical to a full disk
  and the cause is not.

---

### AAD-093 — Process domination findings

Status: Planned
Phase: 9
Priority: High
Related spec: [SPEC-012](specs/SPEC-012-system-deterministic-findings.md)
Dependencies: AAD-081

Goal:
`PROCESS_CPU_DOMINATING` and `PROCESS_MEMORY_DOMINATING`.

Acceptance:
- **A family is named only when a host-level finding for the same resource also
  fired.** Without the anchor the finding is listed and names no culprit.
- Reported as a family, with individual processes as supporting evidence.
- No process is named as responsible for a site.

---

### AAD-094 — OOM_EVENT

Status: Planned
Phase: 9
Priority: High
Related spec: [SPEC-012](specs/SPEC-012-system-deterministic-findings.md)
Dependencies: AAD-070, AAD-111

Goal:
Report an OOM kill from the `/proc/vmstat` counter, with the victim named when
SPEC-014 can supply it.

Acceptance:
- VERY_HIGH by construction; confidence does not scale with a count.
- The output states which source it had — a counter, or the kernel line.
- An unobservable OOM is `oom_counter_unavailable`, never "no OOM".

---

### AAD-095 — HTTP and system correlation

Status: Planned
Phase: 9
Priority: High
Related spec: [SPEC-012](specs/SPEC-012-system-deterministic-findings.md)
Dependencies: AAD-090 … AAD-094

Goal:
A diagnosis with two domains, a `primary_domain`, and honest negatives.

Acceptance:
- **The two scores are never added.** A site and a resource are not comparable.
- With both conclusive, both are reported and neither is called the cause of the
  other — aaDoctor cannot tell cause from effect here and says so.
- With strong system evidence the output **never** contains "no clear cause
  identified".
- "The host resources were quiet" and "the host resources could not be observed"
  produce different sentences — asserted directly.
- `ruleset_version` increments.

---

### AAD-096 — `diagnose` system sections

Status: Planned
Phase: 9
Priority: High
Related spec: [SPEC-008](specs/SPEC-008-cli-reporting.md)
Dependencies: AAD-095

Goal:
`SYSTEM`, `PROCESS` and `SYSTEM EVIDENCE` sections, and a domain marker on every
existing row.

Acceptance:
- 80 columns, clean when piped, bounded columns with middle elision.
- **SPEC-008's inconclusive closing paragraph is rewritten in the same change**,
  not after it: three of the four things it names as invisible become
  observable.
- No output is phrased as an instruction to change the server.

---

### AAD-097 — PHP-FPM findings

Status: Planned
Phase: 9
Priority: High
Related spec: [SPEC-012](specs/SPEC-012-system-deterministic-findings.md)
Dependencies: AAD-100, AAD-101, AAD-102

Goal:
`PHP_FPM_POOL_SATURATION`, `PHP_FPM_MAX_CHILDREN_REACHED` and
`PHP_FPM_PROCESS_PRESSURE`.

Deliberately numbered under SPEC-012 rather than SPEC-013: discovery produces
facts, one rules registry reads them. A finding defined elsewhere would need its
own scoring, its own levels and its own route into correlation.

Acceptance:
- `PHP_FPM_PROCESS_PRESSURE` is capped at MEDIUM — it projects from
  configuration and does not measure what happened.
- Missing pool facts yield `php_fpm_facts_unavailable`.
- **No output says to raise or lower `pm.max_children`**, including by quoting
  PHP-FPM's own "consider raising it".

---

### AAD-100 — PHP version and pool discovery

Status: Planned
Phase: 9
Priority: High
Related spec: [SPEC-013](specs/SPEC-013-php-fpm-pressure-and-pool-discovery.md)
Dependencies: AAD-010

Goal:
Discover installed PHP versions and parse their pool configuration, read-only.

Acceptance:
- **Every path confirmed against a real aaPanel server first**, and the spec
  corrected where it is wrong, before anything else here is accepted.
- `php_admin_value[...]` does not break the parser; `$pool` is substituted.
- An `include` inside a pool file is followed one level within that version's
  own tree and nowhere else.
- An unparsable `pm.max_children` is recorded as unparsed, never defaulted.
- Zero writes under `/www/`; no mode or ownership changes.

---

### AAD-101 — Site to PHP version and pool mapping

Status: Planned
Phase: 9
Priority: Medium
Related spec: [SPEC-013](specs/SPEC-013-php-fpm-pressure-and-pool-discovery.md)
Dependencies: AAD-100

Goal:
Resolve site to PHP version from the vhost, and to a pool where the
configuration decides it.

Acceptance:
- The version comes from the `include enable-php-NN.conf` filename or a
  `fastcgi_pass` target — **SPEC-002's rule of not following `include` holds**.
- `mapping_state` is `certain`, `ambiguous` or `unknown`, never a bare null.
- The sites sharing a pool are presented as scope, never as suspects.

---

### AAD-102 — PHP-FPM log and worker counts

Status: Planned
Phase: 9
Priority: High
Related spec: [SPEC-013](specs/SPEC-013-php-fpm-pressure-and-pool-discovery.md)
Dependencies: AAD-100, AAD-081

Goal:
Follow `php-fpm.log` with the existing incremental reader and count running
workers per pool from the existing process scan.

Acceptance:
- Tailed from the end on first sight, like every other log.
- `server reached pm.max_children` classified with its pool and timestamp.
- `child exited on signal 9` classified, and corroborates SPEC-014's OOM events.
- **Patterns tested against real PHP-FPM output**, not written from memory.
- No log level, slow log or `pm.status_path` is enabled or changed.

---

### AAD-110 — Kernel event source and incremental reader

Status: Planned
Phase: 9
Priority: Medium
Related spec: [SPEC-014](specs/SPEC-014-host-kernel-events.md)
Dependencies: AAD-012

Goal:
Read `/dev/kmsg` incrementally and non-blocking, with a text-file fallback.

Acceptance:
- **No subprocess.** `dmesg` and `journalctl` are not executed.
- **`/proc/kmsg` is never opened** — reading it consumes messages other software
  depends on, which is a side effect on the rest of the system.
- First read starts at the end of the buffer.
- `EAGAIN` is the quiet case; `EPIPE` records a gap and reading continues.
- The fallback is never used alongside `/dev/kmsg`, so nothing is counted twice.

---

### AAD-111 — Kernel event classification

Status: Planned
Phase: 9
Priority: Medium
Related spec: [SPEC-014](specs/SPEC-014-host-kernel-events.md)
Dependencies: AAD-110

Goal:
Classify OOM kills, segfaults, hung tasks, filesystem and I/O errors, and attach
those inside an incident window.

Acceptance:
- **Every pattern tested against real captured kernel output.** The project has
  already shipped one pattern that matched nothing the real software writes.
- An OOM line yields victim name, pid and the kernel's memory figures.
- Events are bounded in count and length; truncation and dropped counts are
  recorded, and so are buffer gaps.
- A crafted message containing a path or shell metacharacters is stored as text
  and never used to open a file or build a command.
- **A kernel event does not open an incident.**

---

### AAD-120 — Coverage model

Status: Planned
Phase: 10
Priority: High
Related spec: [SPEC-015](specs/SPEC-015-diagnostic-coverage-self-check.md)
Dependencies: AAD-074, AAD-080, AAD-100, AAD-110

Goal:
One structure holding a state and a reason per observable aspect.

Acceptance:
- `OK`, `PARTIAL`, `UNAVAILABLE`, `UNSUPPORTED` and `UNKNOWN` are
  distinguishable and produce different sentences.
- Every non-`OK` state carries a reason and the spec that owns it.
- Runtime aspects come from the published snapshot; the CLI recomputes nothing
  and triggers no scan, no kernel read and no log parse.

---

### AAD-121 — `doctor` coverage section

Status: Planned
Phase: 10
Priority: High
Related spec: [SPEC-015](specs/SPEC-015-diagnostic-coverage-self-check.md)
Dependencies: AAD-120

Goal:
Render coverage in `doctor`, with `--coverage` and `--json`, plus a bounded
known-gaps list.

Acceptance:
- **A coverage gap never changes `doctor`'s exit code.** A server without PSI is
  a server aaDoctor works on.
- `doctor` still performs zero writes, and completes in under 500 ms with 50
  sites.
- With the daemon stopped, runtime aspects are `UNKNOWN` and the output says
  what to do about it.
- No `aadoctor coverage` command: one question, one command.

---

### AAD-122 — Coverage-aware negatives

Status: Planned
Phase: 10
Priority: High
Related spec: [SPEC-015](specs/SPEC-015-diagnostic-coverage-self-check.md)
Dependencies: AAD-120, AAD-095

Goal:
Let coverage constrain what the diagnosis may deny, through the existing
`not_evaluable` mechanism.

Acceptance:
- The diagnosis says "no dominant process" **only** when process attribution is
  `OK`, and a different sentence otherwise — asserted on the wording.
- An `UNAVAILABLE` aspect leaves every other finding's confidence untouched.
- The incident records the coverage states from the moment it opened, so an old
  incident is not reinterpreted under today's capabilities.
- Coverage never raises a confidence.

---

## Later — WordPress security

Phase 11. **All items `Planned`, both governing specs `Draft`.** This is a
second domain, not a continuation of the first: performance and security are
different questions about the same server, and they stay apart.

```text
Phase 11a — Security audit        SPEC-016   read-only
Phase 11b — Quarantine & recovery SPEC-017   writes under /www/
```

Id ranges continue the decade-per-spec convention: `13x` SPEC-016, `14x`
SPEC-017.

**Phase 11b is blocked.** SPEC-017 contradicts
[ADR-001](adr/ADR-001-non-invasive-read-only-architecture.md), which is
`Accepted` and which already rejected optional remediation behind a confirmation
flag. ADR-010 exists to resolve that and is `Proposed`. **Nothing in AAD-140 …
AAD-143 may be implemented while it stays that way** — and accepting it requires
writing ADR-001's successor in the same change.

Phase 11a is not blocked and needs no ADR to begin: every check works offline.
Only core integrity and vulnerability matching need reference data, and Tier 1
of ADR-011 covers those with a file the administrator places on the server.

### AAD-130 — WordPress discovery

Status: Planned
Phase: 11a
Priority: High
Related spec: [SPEC-016](specs/SPEC-016-wordpress-security-audit.md)
Dependencies: AAD-010

Goal:
Find WordPress installations under a discovered site, and report version claims.

Acceptance:
- **SPEC-002 captures the vhost `root` directive first** — it does not today,
  and without it there is no path to scan.
- Detection is by `wp-includes/version.php`, never by directory name and never
  by `wp-config.php` alone.
- The search is bounded: the site root and one level of subdirectories, never a
  recursive hunt.
- Several installations under one site are each reported.
- The version is parsed, **never executed**.
- Multisite is detected and reported as not handled.

---

### AAD-131 — Core manifest handling

Status: Planned
Phase: 11a
Priority: High
Related spec: [SPEC-016](specs/SPEC-016-wordpress-security-audit.md)
Dependencies: AAD-130

Goal:
Load a core manifest from the local cache; optionally fetch one under ADR-011.

Acceptance:
- **Tier 0 is the default**: no manifest, no network, integrity `UNAVAILABLE`
  naming the version it needed.
- Tier 1 reads `/var/lib/aadoctor/wp-manifests/<version>.json` and makes no
  request.
- Tier 2 is off by default and refuses to exist until ADR-011 is accepted.
- The report always names the source, the algorithm and the age.
- **The exact coverage of the official manifest is confirmed against a real API
  response** before this is built, not written from what the format is believed
  to look like.

---

### AAD-132 — Core integrity comparison

Status: Planned
Phase: 11a
Priority: High
Related spec: [SPEC-016](specs/SPEC-016-wordpress-security-audit.md)
Dependencies: AAD-131

Goal:
`MATCH` / `MODIFIED` / `UNKNOWN` / `MISSING` per core file.

Acceptance:
- Comparison is by content. **A file is never judged safe by its name.**
- A forged `version.php` claiming a different release yields `UNRELIABLE`, not a
  list of modifications.
- `UNKNOWN` is produced **only** inside directories the manifest describes
  completely — never inside `plugins/` or `themes/`.
- With no manifest, nothing implies the core is intact.

---

### AAD-133 — Suspicious file signals

Status: Planned
Phase: 11a
Priority: High
Related spec: [SPEC-016](specs/SPEC-016-wordpress-security-audit.md)
Dependencies: AAD-130

Goal:
The five signal categories, the weight table and the risk bands.

Acceptance:
- **`eval()` alone never exceeds `LOW`.** `MEDIUM` needs two categories, `HIGH`
  needs three.
- A request superglobal reaching an executor is `VERY_HIGH` on its own.
- PHP under `uploads/` is found regardless of file extension.
- A real minified library stays at `LOW`; a real webshell sample reaches
  `VERY_HIGH`. **Both from real samples** — patterns written from an idea of
  what a webshell looks like will match nothing.
- **No value from `wp-config.php` reaches the report** — only line numbers and
  pattern names. Asserted by planting a password in a fixture.
- Every finding prints every signal that contributed, with its weight.

---

### AAD-134 — Plugin and theme inventory

Status: Planned
Phase: 11a
Priority: Medium
Related spec: [SPEC-016](specs/SPEC-016-wordpress-security-audit.md)
Dependencies: AAD-130

Goal:
Slug, name, version and path for plugins, themes and mu-plugins.

Acceptance:
- Parsed from the plugin header block and from `style.css`, over a bounded read.
  No PHP is executed.
- Single-file plugins are included; `mu-plugins` are always inventoried.
- A missing version header is `version_unknown`, never guessed from a directory
  name.
- **`status` is `unknown`**: activation lives in the database, and this spec
  neither reads credentials nor connects to one.

---

### AAD-135 — Vulnerability intelligence

Status: Planned
Phase: 11a
Priority: Low
Related spec: [SPEC-016](specs/SPEC-016-wordpress-security-audit.md)
Dependencies: AAD-134, ADR-011

Goal:
Match the inventory against an offline feed.

Acceptance:
- **No static list is baked into the release.** It would be stale on the day it
  ships and would answer confidently from year-old data.
- The feed is a file the administrator places; fetching is opt-in and off by
  default.
- The feed's age is shown next to every result, and ages visibly.
- **Absent or stale never renders as "no known vulnerabilities".**

---

### AAD-136 — `wp scan` report and CLI

Status: Planned
Phase: 11a
Priority: High
Related spec: [SPEC-016](specs/SPEC-016-wordpress-security-audit.md)
Dependencies: AAD-132, AAD-133, AAD-134

Goal:
`aadoctor wp sites`, `aadoctor wp scan <site>`, `--json`, and a persisted report.

Acceptance:
- **A full scan performs zero writes under `/www/`** and changes no mode, owner
  or timestamp — asserted by a byte-and-stat audit of a synthetic tree.
- The report is written only under `/var/lib/aadoctor/wp-scans/`, atomically,
  with a stable `finding_id` and the file's SHA-256 per finding.
- Caps on files, depth, file size and wall clock; reaching one **truncates and
  says so** in every rendering.
- Symlinks are never followed out of the resolved site root; loops are detected;
  one site's scan never reads another's files.
- A filename containing control characters cannot forge a report line.
- Exit 0 whatever is found; a `VERY_HIGH` finding triggers nothing.
- Output never says "no findings" without saying what that does and does not
  mean.

---

### AAD-140 — Quarantine

Status: Planned
Phase: 11b
Priority: Medium
Related spec: [SPEC-017](specs/SPEC-017-wordpress-quarantine-recovery.md)
Dependencies: AAD-136, **ADR-010 accepted**

Goal:
Move one reviewed file into `/var/lib/aadoctor/quarantine/`, reversibly.

Acceptance:
- `[wordpress] allow_quarantine` defaults false; with it false **nothing is
  written anywhere**.
- The argument is a **finding id, never a path**. No glob, no `--all`.
- The file's current hash must equal what the report recorded, or it is refused.
- Core files, `wp-config.php`, the root `.htaccess`, directories and symlinks
  are each refused.
- Boundary enforced **after** path resolution; traversal, absolute paths and
  symlinked components are refused and logged.
- **Copy, fsync, re-hash, then unlink — never the reverse.** A failure injected
  at any step leaves the original in place.
- Cross-filesystem works: no `rename`.
- Every action and **every refusal** is appended to the action log.
- `--dry-run` writes nothing.

---

### AAD-141 — Quarantine listing

Status: Planned
Phase: 11b
Priority: Low
Related spec: [SPEC-017](specs/SPEC-017-wordpress-quarantine-recovery.md)
Dependencies: AAD-140

Goal:
`aadoctor wp quarantine list`.

Acceptance:
- The directory is the index; no separate index file that could disagree.
- Shows whether the original location is now occupied by something else — the
  difference between a restore that will work and one that will be refused.

---

### AAD-142 — Restore

Status: Planned
Phase: 11b
Priority: Medium
Related spec: [SPEC-017](specs/SPEC-017-wordpress-quarantine-recovery.md)
Dependencies: AAD-140

Goal:
Put a quarantined file back, byte for byte.

Acceptance:
- The quarantined copy is verified against its recorded hash first.
- **A different file at the destination is refused, naming both hashes.** An
  identical one is a no-op.
- A missing parent directory is refused; aaDoctor does not recreate site
  directories.
- Mode and mtime restored; ownership that cannot be applied is **reported**, not
  silently skipped.
- The quarantine copy survives the restore.

---

### AAD-143 — Purge, and the uninstall interaction

Status: Planned
Phase: 11b
Priority: Medium
Related spec: [SPEC-017](specs/SPEC-017-wordpress-quarantine-recovery.md)
Dependencies: AAD-140

Goal:
`aadoctor wp purge <id>`, and resolve the conflict with `uninstall --purge`.

Acceptance:
- **Accepts only a quarantine id**, never a path, and deletes only inside
  `/var/lib/aadoctor/quarantine/`, verified after resolution.
- An unknown id is refused, not a silent success.
- The manifest is kept with `purged_at` set — the record is the point.
- **`uninstall --purge` refuses while any entry is unrestored and unpurged**,
  names the count and exits non-zero. Recorded in SPEC-001 too.

---

### Not planned, and not implied

Deliberately absent from this phase, and not granted by ADR-010:

```text
delete a vulnerable plugin
update a plugin
disable a plugin or theme
clean or edit malicious code inside a file
restore a core file from an official source
any automatic action at any risk level
```

Core restoration is the closest of these and is still a different capability:
quarantine moves a file *out* and needs no external truth, while core
restoration writes a file *in* and is only as trustworthy as where the
replacement came from. It would need ADR-011 and its own spec, and it must not
share the word `restore`.

---

## Parking Lot

Deferred on purpose. Each needs a decision, and usually an ADR, before moving up.

* **MySQL module** — optional, off by default; many servers use a remote
  database (README §8).
* **Historical baselines** — normal RPS per site and per hour, without machine
  learning (README §70).
* **Additional PHP findings** — `PHP_MEMORY_EXHAUSTED` and
  `PHP_EXECUTION_TIMEOUT` as distinct findings rather than evidence inside
  `PHP_ERROR_SPIKE` (README §31).
* **BOT_OR_CRAWLER_SPIKE** — listed in README §32 but not in the Phase 5
  roadmap; needs user-agent classification first.
* **Versioned rollback** — `aadoctor update --version X` (README §52).
* **Enhanced access log format guide** — manual, opt-in, reversible; never
  applied by aaDoctor (README §68).
* **IP anonymization before AI** — `IP_1` style mapping (README §62).
* **Dedicated non-root user** — only if it requires no change to aaPanel file
  permissions (README §57).
* **Joining multi-line error entries** - a PHP stack trace continuation is
  currently counted as unparsed. Needs a sample from a real server before the
  shape can be implemented rather than guessed (SPEC-004).
* **Apache / OpenLiteSpeed / generic LEMP support** — explicitly out of the MVP
  ([ADR-007](adr/ADR-007-aapanel-nginx-only-mvp.md)).
* **systemd unit hardening beyond README §56** — after real-world testing.

---

## Done

Items stay in their phase section with `Status: Done` rather than being moved
here, so the phase reads as a whole. Currently done:

- AAD-001 — Project skeleton
- AAD-002 — Configuration loading
- AAD-003 — aaPanel environment detection
- AAD-005 — Idempotent installation
- AAD-006 — Clean uninstall and purge
- AAD-007 — Release packaging, checksum verification and update
- AAD-010 — Site and vhost discovery
- AAD-011 — Access and error log mapping
- AAD-012 — Incremental log reader
- AAD-013 — Persistent offsets
- AAD-014 — Log rotation handling
- AAD-020 — Nginx access log parser
- AAD-021 — Nginx error log parser
- AAD-022 — Request aggregation
- AAD-023 — Top site/IP/path calculations
- AAD-024 — HTTP status aggregation
- AAD-030 — Loadavg collector
- AAD-031 — CPU count detection
- AAD-032 — Load per core calculation
- AAD-033 — Incident window correlation
- AAD-034 — Incident JSON persistence

Verified in a Linux container on Python 3.8 and 3.12: 647 unit tests, 49
lifecycle checks, 37 release checks, and a synthetic `/www` tree unchanged in
content, permissions and ownership across install, purge, discovery, a live
tailing run over a 200,000-line history, parsing aaPanel-shaped traffic, a
three-site burst that `aadoctor top` reported correctly, and a load curve that
produced exactly one incident holding the evidence of that burst.
