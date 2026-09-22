# ADR-008 — Minimum Python version 3.8

Status: Accepted
Date: 2026-09-22

Related:
- README.md §13, §88
- [ADR-002](ADR-002-python-standard-library-first.md)
- SPEC-001

## Context

README §88 deliberately left the minimum Python version open until it could be
checked against what aaPanel servers actually run. Phase 1 cannot proceed
without an answer: it determines what syntax is allowed, and whether
configuration can be read with `tomllib`.

aaPanel targets mainstream server distributions. The system interpreters there
are roughly:

```text
CentOS / AlmaLinux / Rocky 8   3.6
Debian 10                      3.7
Ubuntu 20.04                   3.8
Debian 11                      3.9
Ubuntu 22.04                   3.10
Debian 12                      3.11
Ubuntu 24.04                   3.12
```

`tomllib` — the standard library TOML reader — only arrived in 3.11. Requiring
it would exclude every distribution older than Debian 12, which is most of the
installed base aaDoctor targets. Adding `tomli` from PyPI would contradict
[ADR-002](ADR-002-python-standard-library-first.md) and put `pip` into an
install path that is currently just `curl` and `tar`.

## Decision

The minimum supported Python is **3.8**.

Configuration is read with `tomllib` when the interpreter provides it (3.11+),
and with a small internal parser otherwise. That parser covers only the subset
aaDoctor's own configuration uses — comments, `[section]` headers, and
`key = value` with booleans, integers, floats and basic quoted strings — and
raises on anything else rather than guessing.

Consequences for the code:

- No `match` statements (3.10), no `X | Y` type unions at runtime (3.10), no
  built-in generics in evaluated positions (3.9). `from __future__ import
  annotations` covers annotations.
- The entry point checks the version before importing the package, so an old
  interpreter produces a clear message instead of a `SyntaxError`.
- `install.sh` verifies the interpreter version and aborts before touching
  anything if it is too old.

3.6 and 3.7 are excluded: both are end-of-life, and supporting them would cost
f-string and dataclass usage for a shrinking set of hosts. An administrator on
CentOS 8 can install a newer Python without aaDoctor demanding it by default.

## Consequences

### Positive

- Runs on the system Python of every currently supported target distribution.
- No external dependency and no `pip` in the installation path.
- On modern hosts the standard, fully correct TOML parser is used automatically.
- The version boundary is enforced in three places — entry point, installer,
  `doctor` — so it fails early and legibly.

### Negative

- A second configuration parsing path exists, which must be kept behaviourally
  equivalent to `tomllib` for the supported subset. Tests exercise the fallback
  explicitly, on every interpreter.
- The fallback rejects valid TOML that aaDoctor does not use (arrays, inline
  tables, multi-line strings, dotted keys). An administrator who writes valid
  TOML beyond that subset on an older host gets an error rather than silent
  misreading — deliberate, but it is a real limitation.
- Newer language features stay unavailable while 3.8 is supported.

## Alternatives considered

**Require Python 3.11 and use `tomllib` only.**
Rejected: it excludes Ubuntu 20.04/22.04 and Debian 10/11, which is most of the
target audience, in exchange for deleting roughly eighty lines of parser.

**Depend on `tomli` for older interpreters.**
Rejected: it makes `pip` part of installation on exactly the hosts where the
environment is most fragile, contradicting
[ADR-002](ADR-002-python-standard-library-first.md).

**Use JSON or INI for configuration instead of TOML.**
`configparser` would remove the problem entirely and is in the standard
library. Rejected because README §14 specifies TOML and the configuration is a
documented user-facing interface; changing its format to avoid a small parser is
the wrong trade. Worth revisiting only if the configuration grows past what the
fallback can reasonably read.

**Ship a vendored TOML parser.**
Rejected: vendoring a full implementation is more code to audit than the subset
parser, for capability aaDoctor's configuration does not use.
