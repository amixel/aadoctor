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

Phase 1 — Foundation. AAD-001 … AAD-006 are implemented; none is `Done`, because
the development machine has no Python interpreter and no Linux host, so nothing
has been executed yet. Running the verification in
[DEVELOPMENT.md](DEVELOPMENT.md) on a Linux host is what moves them to `Done`.

AAD-007 carries the part of installation that could not be built: there is no
release to download.

### AAD-001 — Project skeleton

Status: Implemented
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

Status: Implemented
Phase: 1
Priority: High
Related spec: [SPEC-001](specs/SPEC-001-installation-lifecycle.md)
Dependencies: AAD-001

Goal:
Load `/etc/aadoctor/config.toml` with the defaults of README §14, falling back
to built-in defaults when the file or a key is absent.

Acceptance:
- `config.example.toml` matches README §14 section by section.
- A missing config file yields defaults, not a crash.
- A malformed config produces a clear error naming file and key; the daemon does
  not start with a half-applied configuration.
- `[mysql] enabled` and `[ai] enabled` default to `false`.
- Config is read through one place, not scattered literals.

---

### AAD-003 — aaPanel environment detection

Status: Implemented
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

---

### AAD-005 — Idempotent installation

Status: Implemented
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

Download and SHA256 verification (README §49 steps 6 and 7) moved to AAD-007:
there is no published release to fetch, and inventing a URL would be worse than
an explicit error.

---

### AAD-006 — Clean uninstall and purge

Status: Implemented
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

Status: Planned
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

Blocked until a release is actually published; no URL is invented before then.

---

## Next

Phase 2 — Log tail. Phase 3 — Aggregation.

### AAD-010 — Site and vhost discovery

Status: Planned
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

---

### AAD-011 — Access and error log mapping

Status: Planned
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

---

### AAD-012 — Incremental log reader

Status: Planned
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

Status: Planned
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

Status: Planned
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

Status: Planned
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
- `request_time` / `upstream_response_time` are parsed when present, absent
  otherwise, never faked.
- An unparseable line increments a counter and is skipped (README §66).
- No regex with catastrophic backtracking on hostile input.

---

### AAD-021 — Nginx error log parser

Status: Planned
Phase: 3
Priority: High
Related spec: [SPEC-004](specs/SPEC-004-nginx-log-parsing.md)
Dependencies: AAD-012

Goal:
Classify known error-log patterns: upstream timeouts, FastCGI failures, Nginx
resource limits, PHP errors.

Acceptance:
- Recognizes the patterns of README §30 and §31.
- A multi-line PHP stack trace does not produce one event per line.
- Unknown lines are counted and ignored.
- Classification is table-driven: a new pattern is one entry, not a new layer.

---

### AAD-022 — Request aggregation

Status: Planned
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

---

### AAD-023 — Top site / IP / path calculations

Status: Planned
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

---

### AAD-024 — HTTP status aggregation

Status: Planned
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

---

## Later

Phase 4 — Load correlation. Phase 5 — Deterministic findings. Phase 6 — CLI
reports. Phase 7 — AI explanation.

### AAD-030 — Loadavg collector

Status: Planned
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

Status: Planned
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

Status: Planned
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

Status: Planned
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

Status: Planned
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

Status: Planned
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

Status: Planned
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

Status: Planned
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

Status: Planned
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

Status: Planned
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

Status: Planned
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

Status: Planned
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

Status: Planned
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

Status: Planned
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

Status: Planned
Phase: 6
Priority: High
Related spec: [SPEC-008](specs/SPEC-008-cli-reporting.md)
Dependencies: AAD-004, AAD-032

Goal:
Report version, detection, site counts, daemon state and current load, per
README §40.

Acceptance:
- Runs with the daemon stopped and says so.
- Read-only; exit code reflects daemon health.

---

### AAD-051 — `doctor` command

Status: Planned
Phase: 6
Priority: High
Related spec: [SPEC-008](specs/SPEC-008-cli-reporting.md)
Dependencies: AAD-003, AAD-011

Goal:
Environment validation report per README §39.

Acceptance:
- Lists OK / WARN per check; warnings do not mask a failing environment.
- Modifies nothing.

---

### AAD-052 — `top` command

Status: Planned
Phase: 6
Priority: High
Related spec: [SPEC-008](specs/SPEC-008-cli-reporting.md)
Dependencies: AAD-023

Goal:
Show the current window's top sites, paths, IPs and errors, per README §41.

Acceptance:
- Output fits an 80-column SSH terminal.
- States clearly when the daemon holds no data yet.

---

### AAD-053 — `diagnose` command

Status: Planned
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

Status: Planned
Phase: 6
Priority: Medium
Related spec: [SPEC-008](specs/SPEC-008-cli-reporting.md)
Dependencies: AAD-034

Goal:
List stored incidents with id, time, primary finding and confidence.

Acceptance:
- Reads the incident directory only; never rewrites an incident.
- An empty directory produces a clear empty result, not an error.

---

### AAD-055 — `show` command

Status: Planned
Phase: 6
Priority: Medium
Related spec: [SPEC-008](specs/SPEC-008-cli-reporting.md)
Dependencies: AAD-054

Goal:
Render one stored incident in full.

Acceptance:
- Accepts an incident id; an unknown id exits non-zero with a clear message.
- Renders findings and evidence from the stored JSON only.

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
* **Apache / OpenLiteSpeed / generic LEMP support** — explicitly out of the MVP
  ([ADR-007](adr/ADR-007-aapanel-nginx-only-mvp.md)).
* **systemd unit hardening beyond README §56** — after real-world testing.

---

## Done

Nothing yet. AAD-001 … AAD-006 are `Implemented` and move here once they have
been executed on a Linux host - see the verification table in
[DEVELOPMENT.md](DEVELOPMENT.md).
