# Project Status

Last updated: 2026-09-22

## Current phase

Phase 3 — Aggregation. Phase 2 is complete: discovery (SPEC-002) and
incremental log monitoring (SPEC-003) are implemented and verified.

Phase 1 is complete except for one check that needs a host with real systemd.

## Current objective

Start SPEC-004: parse the lines the reader now delivers into structured
records. The reader hands over `LogEvent(path, log_type, line, sites)`; nothing
downstream of it exists yet.

## Completed

Documentation:

- `README.md`, [/CLAUDE.md](../CLAUDE.md), 9 specs, 8 ADRs, backlog, changelog.
- `LICENSE` — MIT, Alex Torres.

Code (version `0.1.0-dev`):

- `src/aadoctor/` — `paths`, `config`, `environment`, `service`, `daemon`,
  `cli`, `discovery/` (SPEC-002), `collectors/` (SPEC-003), `storage/`.
- Commands: `--version`, `doctor`, `status`, `sites`, `enable`, `disable`,
  `update`, `uninstall [--purge]`, `daemon`.
- The daemon now does real work: it discovers sites, follows their logs
  incrementally, and persists offsets.
- `install.sh`, `uninstall.sh`, `tools/package.sh`,
  `systemd/aadoctor.service`, `config.example.toml`, `VERSION`.
- `tests/` — 199 unit tests, 12 vhost fixtures, two container integration
  suites.

Verified, in disposable Linux containers:

- 199/199 unit tests on Python 3.8 and 3.12, as root and as a non-root user.
  `compileall` clean.
- 42/42 lifecycle checks and 32/32 release checks — SPEC-001 unaffected.
- The real daemon against a synthetic aaPanel tree with a 200,000-line,
  7.9 MB access log: **zero** of that history was read, the initial offset
  equalled the file size, five appended lines were picked up, a rotation and an
  in-place truncation were each detected and logged, and a restart resumed
  without re-reading anything. No log line reached `/var/log/aadoctor`.
  `/www` came through with identical checksums and permissions.

Decisions recorded:

- [ADR-008](adr/ADR-008-minimum-python-version.md) — minimum Python 3.8.
- MIT licence; `CHANGELOG.md` at the repository root, per README §11.
- SPEC-002 notes: statement-based vhost parsing, block merging, `off` and
  `/dev/null` as disabled, relative paths unresolved, `include` not followed.
- SPEC-003 notes: no partial line is stored anywhere, files opened per poll, a
  single `state.json`, no rotated-tail read, truncation measured against the
  last observed size.

## In progress

Nothing.

## Not started

- SPEC-004 parsing (AAD-020, AAD-021), then aggregation (SPEC-005), load
  correlation (SPEC-006), rules (SPEC-007), the reporting commands of SPEC-008,
  and the AI explainer (SPEC-009).
- Log fixtures under `tests/fixtures/` — only vhost fixtures exist so far.
- The first published release. `tools/package.sh` builds the artifact; nothing
  has been tagged or uploaded.

## Known risks

- **Never run under real systemd.** Containers have no init system, so the
  integration scripts stub `systemctl`. Running `install.sh` then
  `aadoctor enable` on a real Linux host is the remaining gate for AAD-004 and
  the one open item in Phase 1.
- **Never run against a real aaPanel installation.** Both discovery and tailing
  have only seen synthetic trees. `aadoctor sites` is read-only and installs
  nothing — running it on a real server is still the cheapest way to find
  parser gaps.
- **Inode reuse with a larger replacement is undetectable.** If a log is deleted
  and recreated, the filesystem reuses the inode, and the new file is larger
  than the one it replaced, nothing distinguishes it from an append. Lines are
  misread until the next rotation or truncation. Catching it needs a content
  fingerprint; the realistic shape of the problem — a smaller replacement — is
  caught by the size check. See SPEC-003.
- **At-least-once, by design.** A crash between reading lines and saving
  offsets means those lines are read again on restart. Re-reading a few lines
  beats losing them silently, and SPEC-004 must tolerate it.
- **`include` is not followed.** A site whose logging directives live in an
  included file is reported as incomplete, not resolved, and so is not tailed.
- **Latest-release resolution is only half-verified.** Exercised against a
  repository with no releases, where it correctly refuses to guess.
- **Thresholds are unvalidated.** Every number in SPEC-005, SPEC-006 and
  SPEC-007 is a starting point. Not yet relevant — no rules exist.
- **Root read access.** Reading every site's logs without touching aaPanel
  permissions implies running as root (README §57). Deliberate.
- **Two configuration parsers.** `tomllib` and the 3.8 fallback must stay
  behaviourally identical; the suite must keep running on both.

## Next recommended work

1. Run `aadoctor sites` on a real aaPanel server — read-only — and compare it
   with what aaPanel shows. Gaps found there are cheaper now than after
   SPEC-004 builds on the output.
2. On a Linux host with systemd: `./install.sh`, `aadoctor doctor`,
   `aadoctor enable`, confirm the unit is active, `aadoctor disable`,
   `aadoctor uninstall --purge`. Closes AAD-004 and Phase 1.
3. Tag `v0.1.0` and attach both files produced by `tools/package.sh`.
4. Start SPEC-004 (AAD-020) — Nginx access log parsing.

Steps 1 to 3 are independent of step 4.
