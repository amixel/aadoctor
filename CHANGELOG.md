# Changelog

All notable changes to aaDoctor will be documented here.

Format inspired by [Keep a Changelog](https://keepachangelog.com/).
Versioning follows the release scheme of README §48 (`v0.1.0`, `v0.1.1`, …).

No version has been released yet. The current source tree is `0.1.0-dev`.

## [Unreleased]

### Added
- Initial project architecture documented.
- Spec-driven development structure introduced.
- Architectural decision records introduced.
- Python package under `src/aadoctor/` with a single entry point (`aadoctor`),
  standard library only.
- `aadoctor --version`, reading the repository `VERSION` file.
- `aadoctor doctor`: read-only environment check (Linux, Python, aaPanel, Nginx
  vhost directory, site logs, systemd, privileges, configuration).
- `aadoctor status`: version, installation, aaPanel detection, site and log
  counts, how many logs the daemon holds a position in, the current load and
  load per core, whether an incident is open, service state and configuration
  source.
- aaPanel vhost discovery (SPEC-002): `src/aadoctor/discovery/` reads
  `/www/server/panel/vhost/nginx`, extracts `server_name`, `access_log` and
  `error_log` per site, and reports what it could not make sense of. Read-only.
- `aadoctor sites`, with `--json` and `--vhost-dir`: one row per discovered
  site and the state of each log.
- Incremental log monitoring (SPEC-003): `src/aadoctor/collectors/logs.py`
  follows every discovered log by `(device, inode, offset)`, reading only
  appended bytes. A log seen for the first time starts at its end, so a
  multi-gigabyte history is never read. Rotation, truncation, replacement,
  missing and unreadable files are each handled without stopping the others.
- Nginx log parsing (SPEC-004): `src/aadoctor/parsers/` turns each line the
  reader delivers into an `AccessEvent` or an `ErrorEvent`. Pure functions -
  no file access, no state, no knowledge of offsets.
- Access parsing covers Nginx's `common` and `combined` formats and the
  trailing field its default `main` adds, which is what aaPanel writes.
  IPv4 and IPv6, `-` for absent fields, path and query split without
  normalising the query, and absolute-form request targets.
- Error parsing extracts the prefix, level, pid, connection id, message and the
  `client` / `server` / `request` / `upstream` / `host` context, located by
  marker rather than by splitting on commas.
- A `kind` names what one error line says (`upstream_timeout`, `php_fatal`,
  `connect_failed`, …) from a table that is data. A kind is deliberately not a
  finding: those are SPEC-007's, and are named differently so the two cannot be
  confused.
- The daemon parses every line it reads and drops the result. Parsed and
  unparsed counts are kept per log type and per file; a file whose format is
  not recognised is reported once.
- Load incident detection (SPEC-006): `src/aadoctor/collectors/load.py` reads
  `/proc/loadavg` directly and normalises it by CPU count;
  `src/aadoctor/analyzers/incidents.py` opens an incident when the load stays
  high, follows its peak, and closes it when the load recovers.
- An incident freezes the traffic windows twice - when it opens and at each new
  peak - because load lags the traffic that caused it and the burst is often
  only visible at the peak. Written to `/var/lib/aadoctor/incidents/<id>.json`,
  atomically, on open, peak and close.
- Opening and closing use different thresholds, so an incident cannot flicker,
  and a continuous spike is one incident rather than one per poll.
- Incidents record what was measured and name no cause: no finding, no
  confidence, no responsible site. Reading them is `diagnose`, below.
- Deterministic root cause rules (SPEC-007): `src/aadoctor/rules/` turns a
  stored incident into named findings with the evidence behind each one, and
  `src/aadoctor/analyzers/diagnosis.py` correlates them into one answer. No
  AI, no network, no database, and no log file is reopened - the incident
  snapshot is the only input.
- The nine findings: `TRAFFIC_SPIKE`, `ONE_SITE_DOMINATING`,
  `ONE_URL_DOMINATING`, `ONE_IP_DOMINATING`, `NOT_FOUND_FLOOD`,
  `HTTP_5XX_SPIKE`, `UPSTREAM_TIMEOUT`, `FASTCGI_ERROR`, `PHP_ERROR_SPIKE`.
  Each carries its counts, its share and the threshold it was compared
  against, so it can be audited without the raw log.
- `aadoctor diagnose [ID] [--json]`: the probable site, the primary path, the
  associated address, the evidence, the findings and the confidence. Without
  an id it reads the most recent incident. Computed on demand and never
  written back, so an old incident can be re-read by a later ruleset - the
  output carries `ruleset_version`.
- A rule that could not be evaluated is reported as such, with its reason,
  instead of being silently absent. "No traffic spike" and "no way to tell
  whether there was one" are different answers.
- Confidence is a strength of evidence on a declared scale, never a
  probability. One scoring function for every rule, one table of weights for
  correlation, and no arithmetic that multiplies confidences together.
- Data quality limits confidence where the damage actually is: a short window
  limits everything, unparsed access lines limit the traffic findings only,
  unparsed error lines limit the error findings only, and a dimension pruned
  by its cardinality cap limits the findings that read it. Each finding names
  what held it back.
- A diagnosis can be inconclusive, and says so plainly. High load with the
  traffic spread evenly across ten sites produces "No clear log-based cause
  identified", not a suspect - and the report names what could never appear in
  a web server log at all.
- A site is named only when something ties it to the *server*: a share of a
  site's own traffic is not enough on its own. This came out of the container
  run, where a site holding 10% of the traffic was being named because all of
  its requests went to one endpoint.
- A site concentrating failures wins even without dominating the traffic, so
  aaDoctor is not merely a busy-site detector.
- A concentrated address is reported as concentration, with the CDN, proxy,
  NAT, integration and crawler caveat attached. Never as an attack, and never
  with a suggestion to block anything.
- `[rules]` configuration: the sixteen thresholds the findings fire on, with
  their defaults defined in one place. Commented out in
  `config.example.toml` - documented so a value can be looked up, unset so an
  installation does not pin numbers that are going to move.
- `aadoctor incidents [--limit N] [--json]` and
  `aadoctor show <id> [--json]`: when the server was under load, and what the
  logs showed at the time. `show` prints how much data the window covers, so a
  partial window is never read as five minutes of evidence.
- `[load]` configuration with `enabled`, `trigger_per_cpu`, `critical_per_cpu`,
  `recovery_per_cpu`, `trigger_polls` and `recovery_polls`, plus
  `[incidents] retention_days`. With `[load] enabled = false` everything else
  keeps running.
- Incident retention deletes only files whose name is an incident id, at
  startup and once a day.
- Traffic aggregation (SPEC-005): `src/aadoctor/analyzers/traffic.py` keeps
  bounded moving windows of one and five minutes, built from ten-second
  buckets. Requests by site, IP, path, status, method and user agent; errors by
  site, level and kind; and the same breakdown per site, so "which address is
  hitting which site" is answerable.
- Cardinality is capped per dimension, keeping the heaviest keys rather than
  the first ones seen: a flood of unique paths cannot push the dominant path
  out of the table. Displaced volume is reported as `other` rather than
  silently dropped.
- Memory is bounded by key count, not by request count. A bucket that leaves
  the window is dropped whole, and a quiet server's windows empty on their own.
- `aadoctor top`, with `--window 1m|5m`, `--site NAME` and `--json`: what the
  recent traffic looked like. It shows how old the published snapshot is, warns
  when it is stale, and warns when a large share of access lines did not parse
  so incomplete figures never look precise. It describes; it names no cause.
- `src/aadoctor/runtime.py` and `/var/lib/aadoctor/runtime.json`: the daemon
  publishes a bounded snapshot once per poll so the CLI, which runs in another
  process, can read it. Aggregates only - no log lines, no query strings, no
  user agent table.
- `src/aadoctor/storage/json_store.py`: atomic JSON state, written through a
  temporary file and a rename, never world-readable.
- Offsets persisted to `/var/lib/aadoctor/state.json` and restored on start;
  corrupt state degrades to tail-from-end, never to a full read.
- `[monitor] interval_seconds` (default 10, per README §14). The daemon polls
  the logs on that interval and saves offsets after any poll that moved one,
  including on shutdown.
- `[discovery] interval_seconds` (default 60). The daemon re-runs discovery on
  that interval and logs sites added or removed, so a site created in aaPanel
  is noticed without a restart.
- 12 vhost fixtures under `tests/fixtures/vhosts/` covering the shapes aaPanel
  produces, including an HTTP/HTTPS block pair and a deliberately malformed
  file.
- `aadoctor enable` / `aadoctor disable`: idempotent control of
  `aadoctor.service` and nothing else.
- `aadoctor daemon`: runs discovery, log tailing, parsing, aggregation and load
  detection on their own intervals, publishes the runtime snapshot, and shuts
  down cleanly on SIGTERM/SIGINT after persisting offsets.
- `aadoctor uninstall` and `aadoctor uninstall --purge`, delegating to
  `uninstall.sh`.
- `aadoctor update [--version X] [--force]`, delegating to `install.sh` in
  release mode. An update is an install from a verified release; there is no
  second implementation of that sequence.
- `install.sh`: idempotent installation, staged and swapped, preserving an
  existing `/etc/aadoctor/config.toml`. Does not enable the service. Installs
  from the local checkout when there is one, otherwise downloads a published
  release from `github.com/amixel/aadoctor` and verifies its SHA256 before
  extracting anything. `--release`, `--version`, `--force`; `AADOCTOR_BASE_URL`
  for a mirror or a local test server.
- `tools/package.sh`: builds `aadoctor-<version>.tar.gz` and its `.sha256`
  reproducibly, with no bytecode inside.
- `LICENSE`: MIT.
- `uninstall.sh`: removal through an exact-path allowlist; `--purge` for
  configuration, state and logs.
- `systemd/aadoctor.service` per README §56; the installer substitutes the
  detected python3 interpreter.
- `config.example.toml` with the keys this version actually reads.
- Test suite (`unittest`, standard library) covering path safety, configuration
  loading and its fallback parser, environment detection, the CLI contract and
  static safety properties of both shell scripts.
- `tests/integration/lifecycle.sh`: container-only verification of the full
  install, reinstall, enable, disable, uninstall and purge cycle, including a
  synthetic `/www` tree that must come through byte-identical. Refuses to run
  outside a container.
- `tests/integration/release.sh`: container-only verification of the release
  path, including a corrupted artifact that must be rejected with the existing
  installation intact.
- `tests/integration/diagnose.sh`: container-only verification of all three
  SPEC-007 scenarios end to end against the real daemon - a dominant site with
  its path, address and failures; high load with the traffic spread evenly;
  and a site holding a third of the traffic and nearly all of the failures.
  Only `/proc/loadavg` is substituted. It refuses to run outside a container,
  and refuses to touch a `/www` it did not create.
- ADR-008: minimum Python version 3.8, resolving the TBD in README §88.

### Changed
- SPEC-001 to SPEC-007 moved from `Draft` to `Implemented`, with implementation
  notes. SPEC-006 gained two corrections: hysteresis replaced the cooldown it
  specified, and an incident no longer depends on the rules engine to exist. SPEC-004's error classification table was renamed to lowercase kinds
  so a line classification can never be mistaken for a SPEC-007 finding.
- SPEC-007 reversed its own persistence decision: findings are computed on
  demand instead of being stored inside the incident file, so a past incident
  is re-read by a later ruleset rather than frozen with today's opinion. This
  also diverges from the example schema in README §37, whose own text says it
  may evolve; both documents now say so.
- SPEC-007 threshold changes from its draft, each recorded in the spec:
  `ip_share` 0.60 to 0.50, so an address holding 57.8% of a site is not
  invisible; the 5xx share 0.05 to 0.01, because a healthy server serves
  essentially no 5xx and one request in twenty failing means the site is
  already down.
- SPEC-007's `TRAFFIC_SPIKE` compares the incident's own two windows by rate
  rather than against a history it does not keep. Comparing totals would
  measure one minute against up to four; it is capped at MEDIUM confidence,
  since this is not a real baseline.
- Several evidence fields the SPEC-007 draft listed were dropped rather than
  faked: raw query strings, an address's own paths and user agents, the top
  404 paths, and error message samples. None of them exists in the aggregated
  data, and reporting them would have meant inventing correlations.
- SPEC-008 moved further through `In Progress`: `diagnose` now exists, leaving
  only `explain`. Its exit-code TBD is resolved - `diagnose` exits `0`
  whatever it concludes.
- The daemon's startup line no longer says diagnosis is unimplemented; it says
  incidents are diagnosed on demand.
- AAD-050 … AAD-055 moved from `Planned` to `Done`. Three of their acceptance
  criteria were written before SPEC-006 and SPEC-007 separated measurement from
  interpretation and are amended in place: `status` does not signal daemon
  health through its exit code, and neither `incidents` nor `show` prints a
  finding or a confidence.
- The spec index in `docs/specs/README.md` had said `Draft` for every spec
  since SPEC-001 landed. It now tracks the real statuses.
- SPEC-008 exit code table gained `5` for insufficient privileges.
- `CHANGELOG.md` moved from `docs/` to the repository root, matching README §11.
- README §11 and §12 updated: the module list now matches the source tree, and
  the installed layout names `runtime.json`.
- `src/` is copied with `tar --exclude=__pycache__` instead of `cp -a`, so a
  developer checkout's bytecode never reaches an installation.

### Fixed
- Configuration now accepts an integer where a float is expected, so
  `trigger_per_cpu = 1` is not rejected as a type error.
- `aadoctor status` said "diagnosis not implemented yet" long after it was
  implemented. A test had been asserting that sentence, which is how it
  survived; the test now asserts the opposite.
- **`install.sh`, `uninstall.sh`, `aadoctor` and the helper scripts were not
  executable in a fresh clone.** They were committed from a Windows checkout
  where `core.filemode` is false, so git recorded mode `100644`, and
  `sudo ./install.sh` on a real server answered `command not found`. The modes
  are now `100755`, and `tests/test_scripts.py` asserts the mode git records -
  the working tree cannot be used for this, because a Windows bind mount
  reports every file inside a container as `rwxrwxrwx`.
- **`install.sh` destroyed the checkout when run from `/opt/aadoctor`.** That
  directory is the install destination, and `git clone ... /opt/aadoctor` is a
  natural thing to do. The swap moved the source aside and deleted it - taking
  `.git` with it - and the files read afterwards were then missing, leaving a
  system with the CLI installed but no systemd unit and no configuration. The
  installer now refuses before touching anything and says where to clone
  instead. A lifecycle check covers it, verified by reproducing the original
  destruction with the guard removed.
- `tools/package.sh` inherited permissions from the checkout through `cp -a`,
  so an artifact built on Windows would have shipped every file
  world-writable and would not have matched one built on Linux from the same
  commit. It now sets the modes itself, and `release.sh` asserts them inside
  the tarball.

### Removed

### Security
- Configuration, state and log directories are created `0750` and the
  configuration file `0640`, since incidents may contain client IPs. State
  files are written `0640` at creation, never briefly world-readable.
- Site log lines are never written to `/var/log/aadoctor` or into any state
  file: duplicating an access log would be both a disk and a privacy problem.
- `runtime.json` and the incident files name sites, paths and client addresses,
  so they are written `0640` like the rest of `/var/lib/aadoctor` and hold
  aggregates only.
- Parsers extract no cookies, headers, bodies, tokens or session data, and
  treat every URL and user agent as data only - never as a path, a format
  string or anything executable. The rules inherit that: a site name, path or
  address reaching them came from a log line and is counted, compared and
  printed, nothing more.
- The rule modules import nothing outside `typing`, `dataclasses` and aaDoctor
  itself, checked by a test that reads the import statements rather than the
  text - so a docstring mentioning the PHP-FPM socket cannot be mistaken for a
  socket being opened.
- `diagnose` writes nothing, anywhere, including to the incident it reads.
