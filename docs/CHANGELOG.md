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
- `aadoctor status`: version, installation, aaPanel detection, service state and
  configuration source. Reports monitoring as not implemented.
- `aadoctor enable` / `aadoctor disable`: idempotent control of
  `aadoctor.service` and nothing else.
- `aadoctor daemon`: minimal lifecycle-only daemon with clean SIGTERM/SIGINT
  shutdown. No log reading, no monitoring.
- `aadoctor uninstall` and `aadoctor uninstall --purge`, delegating to
  `uninstall.sh`.
- `install.sh`: idempotent local installation, staged and swapped, preserving an
  existing `/etc/aadoctor/config.toml`. Does not enable the service.
- `uninstall.sh`: removal through an exact-path allowlist; `--purge` for
  configuration, state and logs.
- `systemd/aadoctor.service` per README §56; the installer substitutes the
  detected python3 interpreter.
- `config.example.toml` with the keys this version actually reads.
- Test suite (`unittest`, standard library) covering path safety, configuration
  loading and its fallback parser, environment detection, the CLI contract and
  static safety properties of both shell scripts.
- ADR-008: minimum Python version 3.8, resolving the TBD in README §88.

### Changed
- SPEC-001 moved from `Draft` to `In Progress`, with implementation notes.
- SPEC-008 exit code table gained `5` for insufficient privileges.

### Fixed

### Removed

### Security
- Configuration, state and log directories are created `0750` and the
  configuration file `0640`, since incidents may contain client IPs.
