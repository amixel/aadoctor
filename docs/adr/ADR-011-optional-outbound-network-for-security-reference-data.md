# ADR-011 — Optional outbound network for security reference data

Status: Proposed
Date: 2026-09-23

Related:
- README.md §9, §13, §58, §74
- [ADR-002](ADR-002-python-standard-library-first.md),
  [ADR-006](ADR-006-deterministic-engine-before-ai.md)
- SPEC-016

> **Proposed, not accepted.** No fetching may be implemented while this ADR is
> in this state.

## Context

Two capabilities in
[SPEC-016](../specs/SPEC-016-wordpress-security-audit.md) need data that does
not exist on the server being scanned:

```text
core integrity          requires the official file hashes for the exact
                        WordPress version installed
vulnerability matching  requires current knowledge of which plugin and theme
                        versions are exploitable
```

Neither can be derived locally. A WordPress installation cannot tell you what it
*should* look like, and nothing on the disk knows which versions were
compromised last month.

aaDoctor has no outbound network today. The daemon makes no requests, the CLI
makes none, and the only network capability ever contemplated is the optional AI
call of [SPEC-009](../specs/SPEC-009-ai-explainer.md) — which is off by default,
explicitly invoked, and still unimplemented.

This is not an accident of scope. A monitoring tool installed on a production
server during an incident is trusted partly because it talks to nothing. Adding
an HTTPS client changes what an administrator has to reason about: what is sent,
to whom, how often, what happens when it fails, and what a proxy or an egress
firewall does to it.

Three routes exist and they are genuinely different, so the decision cannot be
folded into the implementation.

## Decision

**aaDoctor performs no outbound network request in its default configuration,
and every security feature is useful without one.**

Reference data is obtained through a three-tier model, in this order:

**Tier 0 — no data.** The default. The affected check reports `UNAVAILABLE`,
naming exactly what it would have needed. Every other check still runs. A scan
with no manifest and no feed still finds PHP under `uploads/`, hidden
executables, obfuscation patterns and suspicious timestamps.

**Tier 1 — data the administrator placed on the server.**

```text
/var/lib/aadoctor/wp-manifests/<version>.json
/var/lib/aadoctor/wp-vulns/feed.json
```

Copied from another machine, fetched by hand, generated from a release they
already trust, or distributed by their own configuration management. **aaDoctor
makes no request; it reads a file.** This is the recommended mode for servers
with restricted egress, and it is the tier that makes the whole feature work
without this ADR being accepted at all.

**Tier 2 — aaDoctor fetches, opt-in.**

```toml
[wordpress]
fetch_manifests = false      # default
fetch_vulns     = false      # default
```

When enabled, and **only during an explicitly invoked `aadoctor wp` command**,
aaDoctor may fetch reference data over HTTPS and cache it into the Tier 1
locations.

Constraints on any Tier 2 request:

1. **The daemon never makes one.** Not on a timer, not at startup, not on an
   incident. Only a command a human typed.
2. **Nothing about the server is transmitted.** The request carries a WordPress
   version number and nothing else — no site name, no domain, no file path, no
   hash, no plugin list, no identifier, no telemetry. A request must not reveal
   what is installed or who is asking.
3. **HTTPS with certificate verification**, via `urllib.request` and the
   standard library only ([ADR-002](ADR-002-python-standard-library-first.md)).
   No provider SDK, no `requests`.
4. **Bounded**: a connect and read timeout, a maximum response size, no
   redirects to another host, and no retry loop.
5. **Failure is never fatal and never silent.** A failed fetch degrades to
   Tier 0 for that check, with the reason shown. It does not fail the scan.
6. **Cached and reused.** A manifest for a version is fetched once.
7. **Fetched data is data, never code.** It is parsed as JSON into hashes and
   version strings. Nothing from a response is ever executed, written under
   `/www/`, or used to construct a path.

**The governing rule, whichever tier applies:** absent or stale reference data
produces `UNAVAILABLE` or a visible age — **never a clean result**. "We could
not check" and "we checked and it is fine" must never render the same way. A
stale vulnerability feed reporting "no known vulnerabilities" is the most
dangerous output this feature could produce, because the reader's next action is
to stop looking.

Trust is stated rather than implied: every report names the source, the
algorithm and the age of the data it used.

## Consequences

### Positive

- The default installation is unchanged: no network, no egress rules to write,
  nothing to explain to a security team.
- Core integrity — the strongest structural check in SPEC-016 — becomes
  available to administrators who want it, without being imposed on those who
  do not.
- Tier 1 means restricted-egress servers are first-class rather than degraded,
  and it works whether or not Tier 2 is ever built.
- The privacy position is simple enough to state in one sentence: a version
  number goes out, nothing else.
- Standard library only; no dependency is added
  ([ADR-002](ADR-002-python-standard-library-first.md)).

### Negative

- **aaDoctor gains an HTTPS client**, and with it TLS handling, timeouts,
  proxies and a class of failure it has never had. Even off by default, the code
  exists — the same argument ADR-001 made about remediation, and it is equally
  valid here.
- **A version number is still a disclosure.** It reveals that someone is running
  that WordPress version, correlated with an IP address, to whoever serves the
  endpoint.
- **A fetched manifest is only as trustworthy as the channel.** TLS
  authenticates the server, not the content, and the official checksums are
  **MD5** — adequate against accident, weak against an adversary who could
  supply a colliding file.
- **Tier 0 is the honest default and the weaker product.** Most users will run
  a scan whose best check is unavailable, and the report has to say so clearly
  enough that they understand what they did not get.
- A vulnerability feed implies a schema aaDoctor now depends on and does not
  control.

## Alternatives considered

**Ship manifests inside the aaDoctor release.** No network ever, which is
attractive. Rejected: a manifest is roughly two thousand entries per WordPress
version, several versions ship each year, and a server on an older release would
find nothing. Covering real installations would dominate the size of a release
whose appeal is that it is small — and it would go stale between releases, which
is the failure that matters, because a missing manifest is indistinguishable
from a clean site unless the report is careful.

**Maintain a vulnerability database in the project.** Rejected outright. The
project is not a security intelligence provider, could not keep it current, and
a stale list that answers confidently is worse than no list.

**Fetch by default, with an opt-out.** Rejected. It inverts the property that
makes aaDoctor easy to install on a production server, and it would make the
first run of a diagnostic tool generate unexpected egress — discovered by
whoever is watching the firewall, at the worst possible moment.

**Let the daemon refresh reference data on a schedule.** Rejected. A background
process making periodic outbound requests is a materially different thing from a
command that fetches once when asked, and it is exactly what an administrator
would not expect from this tool.

**Download the full official release archive instead of the checksums.** It
yields SHA-256 computed locally and a definitive file list, which is stronger
than MD5. Rejected as the primary route because it is a far larger transfer for
the same answer and the archive is trusted through the same channel anyway. Kept
as the fallback for a locale or variant the checksums endpoint does not cover,
and not built until one is found.

**Use the AI integration's network path** ([SPEC-009](../specs/SPEC-009-ai-explainer.md)).
Rejected: unrelated purpose, unrelated data, unrelated consent. Reference data
is not an explanation, and bundling them would mean enabling one to get the
other.
