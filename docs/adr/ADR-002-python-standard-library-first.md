# ADR-002 — Python Standard Library first

Status: Accepted
Date: 2026-09-22

Related:
- README.md §13, §47, §85, §86, §88
- SPEC-004, SPEC-009

## Context

The installation target is an existing aaPanel server. Such servers usually have
a system Python 3, sometimes several Python installations from panel-managed
applications, and a package environment the administrator did not design and
does not want disturbed.

Introducing `pip` into that picture means choosing between installing packages
system-wide — touching an environment other software depends on — or shipping a
virtualenv, which adds size, a bootstrap step and a second Python to keep
current. Both conflict with a tool whose selling point is that it changes
nothing.

The workload also does not demand much: reading files, matching patterns,
counting integers, formatting text, and one optional HTTPS request. The standard
library covers all of it.

## Decision

aaDoctor is written against the Python 3 standard library.

An external dependency requires, before it is added:

1. an explanation of why the standard library does not solve the problem
   adequately;
2. a concrete demonstrated benefit;
3. an assessment of the installation impact on a plain aaPanel server;
4. its own ADR.

This applies equally to the optional AI integration: the HTTP call uses
`urllib.request`, not a provider SDK.

`sqlite3` is part of the standard library but is excluded by
[ADR-003](ADR-003-filesystem-state-without-database.md) for separate reasons.

The minimum supported Python version is **TBD** (README §88). It will be chosen
from what aaPanel servers actually run, not from what is newest. Until it is
fixed, avoid syntax and library features introduced in recent releases.

## Consequences

### Positive

- Installation is `curl`, `tar`, `sha256sum`, `python3`, `systemd` — nothing else
  (README §47).
- No dependency resolution, no lockfile, no supply chain to audit, no breakage
  when the administrator upgrades an unrelated package.
- The release artifact stays small and the install fast.
- Uninstall removes a directory; there is nothing installed elsewhere.
- Code stays boring, which is the point for a diagnostic tool.

### Negative

- More code written by hand: log parsing, TOML reading on older Pythons, top-N
  selection, HTTP handling.
- No `tomllib` before Python 3.11, so a minimal TOML reader may be needed for
  the configuration shape of README §14.
- No fast third-party parsing; performance has to come from restraint rather
  than from a C extension.
- Contributors used to reaching for a package will find this constraint
  annoying. That is the intended trade.

## Alternatives considered

**Ship a virtualenv with a curated dependency set.**
Rejected: it doubles install size and complexity, adds a second Python runtime
to maintain, and complicates upgrades and purge for no capability the tool
actually needs.

**Install dependencies system-wide with pip.**
Rejected: it modifies an environment aaDoctor does not own, which contradicts
[ADR-001](ADR-001-non-invasive-read-only-architecture.md) in spirit and can
break panel-managed software.

**Write the daemon in Go or Rust and ship a static binary.**
Genuinely attractive for a single-file, dependency-free install. Rejected for
the MVP: Python is already present on every target server, the team iterates
faster in it, and the workload is I/O-bound rather than CPU-bound. Worth
revisiting only if profiling shows the monitor itself contributing to load.
