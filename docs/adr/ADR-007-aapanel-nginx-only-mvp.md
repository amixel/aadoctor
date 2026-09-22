# ADR-007 — aaPanel + Nginx only MVP

Status: Accepted
Date: 2026-09-22

Related:
- README.md §6, §7, §81, §82, §86
- SPEC-002, SPEC-004

## Context

The problem aaDoctor solves — one site dragging down a shared server — is not
specific to aaPanel. It occurs on cPanel, Plesk, CyberPanel, plain LEMP stacks
and Apache servers alike.

The temptation is to build a web-server abstraction up front so support for
Apache or OpenLiteSpeed is "just another backend". That abstraction would have
to be designed against a single real implementation, which reliably produces the
wrong seams: discovery, log formats, upstream semantics and error patterns all
differ in ways that are only visible once a second stack is actually supported.

The value of the MVP depends on being right about one environment, not roughly
right about four.

## Decision

The MVP targets exactly:

```text
aaPanel + Nginx + PHP-FPM + Linux
```

Discovery reads aaPanel's Nginx vhost layout directly
([SPEC-002](../specs/SPEC-002-aapanel-discovery.md)). Parsing targets the Nginx
access and error log formats
([SPEC-004](../specs/SPEC-004-nginx-log-parsing.md)).

When the environment does not match, aaDoctor says so and stops:

```text
Nginx not detected.

aaDoctor currently supports aaPanel + Nginx only.

No monitoring started.
```

No silent adaptation to another stack, and no partial support that produces
misleading numbers.

No abstraction layer for other web servers or panels is introduced in advance.
Apache, OpenLiteSpeed and generic LEMP support stay in the Parking Lot until
there is real demand, and when that demand arrives, the abstraction is derived
from two working implementations rather than imagined from one.

## Consequences

### Positive

- Discovery and parsing can rely on concrete paths and formats, making them
  simpler and more accurate.
- No speculative interface to maintain, and no abstraction shaped by a single
  case.
- Clear, honest failure on unsupported environments instead of wrong diagnoses.
- Smaller test surface, so the fixtures that exist cover the real target well.
- Matches the anti-overengineering rule in [/CLAUDE.md](../../CLAUDE.md) §11 and
  README §82.

### Negative

- Users on other panels or web servers cannot use it at all.
- Retrofitting a second stack later will require refactoring discovery and
  parsing — accepted deliberately as the cheaper path than guessing the
  abstraction now.
- Some aaPanel-specific assumptions will end up spread through the code and will
  have to be found during that refactor.

## Alternatives considered

**Generic web-server support from day one.**
Rejected: it multiplies the discovery, parsing and testing surface before the
core diagnostic value is proven on one stack, and the resulting abstraction
would be a guess.

**A plugin architecture for panels and web servers.**
Rejected: a plugin framework with exactly one plugin is pure overhead
(README §86), and its interface would be wrong for the same reason.

**aaPanel with both Nginx and Apache in the MVP.**
Rejected for now: Apache brings a different vhost layout, a different log
format, different error semantics and `mod_php` as well as FPM. It roughly
doubles phases 2 and 3 for an audience the project has not yet served once.
