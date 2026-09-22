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
  counts, how many logs the daemon holds a position in, service state and
  configuration source. Reports parsing as not implemented.
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
- `aadoctor daemon`: runs discovery and log tailing on their own intervals,
  with clean SIGTERM/SIGINT shutdown that persists offsets first.
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
- ADR-008: minimum Python version 3.8, resolving the TBD in README §88.

### Changed
- SPEC-001, SPEC-002 and SPEC-003 moved from `Draft` to `Implemented`, with
  implementation notes.
- SPEC-008 exit code table gained `5` for insufficient privileges.
- `CHANGELOG.md` moved from `docs/` to the repository root, matching README §11.
- `src/` is copied with `tar --exclude=__pycache__` instead of `cp -a`, so a
  developer checkout's bytecode never reaches an installation.

### Fixed

### Removed

### Security
- Configuration, state and log directories are created `0750` and the
  configuration file `0640`, since incidents may contain client IPs. State
  files are written `0640` at creation, never briefly world-readable.
- Site log lines are never written to `/var/log/aadoctor` or into the state
  file: duplicating an access log would be both a disk and a privacy problem.
