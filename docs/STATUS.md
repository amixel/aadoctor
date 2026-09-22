# Project Status

Last updated: 2026-09-22

## Current phase

Phase 1 — Foundation. Implemented, not yet verified on a Linux host.

## Current objective

Complete SPEC-001: install, enable, disable, uninstall and purge, with a minimal
daemon that exists only so the lifecycle can be exercised. No traffic
diagnostics of any kind.

## Completed

Documentation:

- `README.md`, [/CLAUDE.md](../CLAUDE.md), 9 specs, 8 ADRs, backlog, changelog.

Code (version `0.1.0-dev`):

- `src/aadoctor/` — `paths`, `config`, `environment`, `service`, `daemon`, `cli`.
- `aadoctor` entry point, working from a checkout, an install and a symlink.
- `install.sh`, `uninstall.sh`, `systemd/aadoctor.service`,
  `config.example.toml`, `VERSION`.
- `tests/` — path safety, configuration, environment detection, CLI contract,
  static script safety.

Decisions recorded:

- [ADR-008](adr/ADR-008-minimum-python-version.md) — minimum Python 3.8, with
  `tomllib` when available and a subset parser otherwise. This closes the TBD in
  README §88.

## In progress

Nothing is being worked on. Phase 1 is code-complete except for AAD-007.

## Not started

- AAD-007 — release packaging, SHA256 verification, `aadoctor update`. Blocked:
  no release exists to download, and no URL was invented.
- Everything from Phase 2 onwards: discovery (SPEC-002), log tailing
  (SPEC-003), parsing (SPEC-004), aggregation (SPEC-005), load correlation
  (SPEC-006), rules (SPEC-007), the reporting commands of SPEC-008, and the AI
  explainer (SPEC-009).
- `tests/fixtures/` — created with the phase that needs them.
- `LICENSE` — not chosen yet.

## Known risks

- **Nothing has been executed.** The development machine has no Python
  interpreter (`python3` resolves to the Microsoft Store stub, no other install,
  no usable WSL distribution) and no Linux host. The code was written and
  reviewed statically; the shell scripts passed `bash -n`. The Python modules
  have never been imported and the test suite has never run. Treat every Phase 1
  item as unverified until the checks in [DEVELOPMENT.md](DEVELOPMENT.md) pass.
- **The installer has never installed anything.** Idempotency, the staged swap,
  configuration preservation and purge are implemented and statically checked,
  but only a disposable Linux VM can confirm them.
- **Thresholds are unvalidated.** Every number in SPEC-005, SPEC-006 and
  SPEC-007 is a starting point. Unchanged from the previous round.
- **Access log format variance.** The parser assumes the aaPanel default Nginx
  format. Not yet relevant — no parser exists.
- **Root read access.** Reading every site's logs without touching aaPanel
  permissions implies running as root (README §57). Deliberate.
- **Document location divergence.** README §11 places `CHANGELOG.md` at the
  repository root; it is in `docs/`. Resolve before the first release.

## Next recommended work

1. On a Linux host with Python 3.8+, run the verification in
   [DEVELOPMENT.md](DEVELOPMENT.md): the test suite, then `--version`, `--help`,
   `doctor` and `status` from a checkout. Fix whatever the first real execution
   finds.
2. On a disposable VM with aaPanel, run the SPEC-001 verification sequence
   (install, install again, enable, disable, uninstall, purge) and diff `/www`,
   the crontab and the firewall rules before and after.
3. Move AAD-001 … AAD-006 to `Done` only after step 2 passes.
4. Choose a licence and add `LICENSE`.
5. Then, and only then, start SPEC-002 (AAD-010).
