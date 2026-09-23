# ADR-010 — Explicit opt-in filesystem mutation for WordPress remediation

Status: Proposed
Date: 2026-09-23

Related:
- README.md §4, §5, §53, §54, §90
- [ADR-001](ADR-001-non-invasive-read-only-architecture.md) — **contradicted**
- [ADR-003](ADR-003-filesystem-state-without-database.md)
- SPEC-016, SPEC-017, SPEC-001

> **Proposed, not accepted.** [SPEC-017](../specs/SPEC-017-wordpress-quarantine-recovery.md)
> may not be implemented while this ADR is in this state.

## Context

[SPEC-016](../specs/SPEC-016-wordpress-security-audit.md) can find a webshell
and report it with evidence. It cannot remove it, and the administrator's
remaining option is `rm` in a root shell against a path they copied from a
report — permanent, unlogged, and unrecoverable if the finding was wrong.

[SPEC-017](../specs/SPEC-017-wordpress-quarantine-recovery.md) proposes a
reversible alternative: move the file into aaDoctor's own directory, keep it
intact, and be able to put it back byte for byte.

That requires writing under `/www/wwwroot/`, which
[ADR-001](ADR-001-non-invasive-read-only-architecture.md) forbids in terms that
leave no room:

> Nothing is ever written under `/www/server/`, `/www/wwwroot/` or
> `/www/wwwlogs/`. aaDoctor never runs `chmod`, `chown`, `setfacl`, `rm`, `mv`
> or `truncate` against aaPanel paths.

**And this exact proposal was already considered and rejected there**, in its
own words:

> **Include optional remediation behind a confirmation flag.**
> Rejected for the MVP. Once the code can block an IP or restart PHP-FPM, that
> capability exists on every server regardless of the flag, and the tool's
> position in incident post-mortems changes fundamentally (README §90).

That argument has not been refuted and this ADR does not claim to refute it. It
is correct: once the code exists, the capability exists on every installation,
whatever the default is. What has changed is not the argument but the problem —
malware is not something observation can solve, and a diagnosis nobody can act
on safely is where SPEC-016 otherwise ends.

So this is a genuine trade, recorded as one.

## Decision

aaDoctor may modify files under `/www/wwwroot/` **only** through the commands of
SPEC-017, and only under every one of these constraints:

**1. Disabled by default.** `[wordpress] allow_quarantine = false`. With it
unset or false, the commands exist, explain the setting and write nothing.

**2. Never automatic.** There is no threshold, no confidence level and no
configuration key that causes a file to be moved. `VERY_HIGH` risk moves
nothing. The daemon has no code path into any of it and never will.

**3. One file, named by a reviewed finding.** The commands take a finding id or
a quarantine id — **never a path**, never a glob, never `--all`. The file's hash
must still match what the stored scan report recorded, or the operation is
refused: the file must be the one a human actually read about.

**4. Move, never modify.** No file content under `/www/` is ever edited. No
directory is created or removed. No mode, owner or timestamp is changed, except
when restoring the recorded metadata of a file aaDoctor itself removed.

**5. Reversible by construction.** The file is copied, fsynced and re-hashed
before the original is unlinked — never the reverse — and the copy is kept after
a restore.

**6. Bounded by the resolved site root**, checked after path resolution, with
symlinks, directories, core files, `wp-config.php` and the root `.htaccess` all
refused outright.

**7. Audited.** Every action **and every refusal** is appended to
`/var/log/aadoctor/wp-actions.log`.

**8. `uninstall --purge` refuses while the quarantine is non-empty**, because
that directory can hold the only copy of a file removed from a live site.

Everything outside SPEC-017 stays exactly as ADR-001 describes it. The daemon,
`diagnose`, `top`, `status`, `show`, `doctor`, discovery, log reading and every
collector remain read-only, and no future feature inherits this exception by
being adjacent to it.

**On acceptance, this ADR supersedes ADR-001**, and accepting it requires
writing ADR-001's successor in the same change — restating the full read-only
guarantee with exactly this one carve-out. Accepting this ADR alone would leave
two `Accepted` ADRs whose decision sections contradict each other, which is
worse than either outcome.

## Consequences

### Positive

- A finding becomes actionable without `rm` in a root shell, and the action is
  reversible — which is the entire point. A false positive costs a restore
  instead of a lost file.
- The move is safer than what the administrator would otherwise do by hand:
  hash-verified against reviewed evidence, boundary-checked after resolution,
  refusing core files and `wp-config.php`, and logged.
- The file is preserved rather than destroyed, so it remains available for
  analysis — which `rm` does not offer.
- The constraints are mechanically testable: a filesystem audit asserting a
  byte-identical `/www` with the flag off, and a failure-injection suite proving
  the original survives an abort at every step.

### Negative

- **ADR-001's rejection argument stands, and is the real cost.** The code to
  write under `/www/` will exist on every installation, enabled or not. A bug in
  path resolution becomes a bug that can delete part of a site, in a way that
  was structurally impossible before.
- **"Did aaDoctor touch this file?" stops having one answer.** ADR-001 listed
  that as a positive consequence and this removes it. The action log is the
  replacement and it is weaker: root can edit it, and it only records what
  aaDoctor was asked to do.
- **Uninstall stops being unconditionally complete.** `--purge` now has a state
  in which it refuses, which complicates a contract that was previously absolute
  (README §54).
- **aaDoctor's position in a post-mortem changes**, exactly as ADR-001 predicted.
  A tool that can move files is a suspect when a file is missing, and no amount
  of logging fully removes that.
- It invites the next request. Deleting a vulnerable plugin, restoring a core
  file, disabling a theme are each one short step from here, and each is
  explicitly **not** granted by this ADR. The narrow scope is the defence and it
  will be under pressure.

### Neutral

- A server where the configuration key was never set is one where this was never
  enabled, and that is answerable from `/etc/aadoctor/config.toml` alone.

## Alternatives considered

**Keep aaDoctor read-only and print the path.** The status quo, and it is a
serious option — it preserves the guarantee completely and the administrator can
still act. Rejected because the action they then take is strictly worse:
unverified, irreversible, unlogged, and against a path they retyped. The tool
has the hash, the evidence and the boundary checks, and declining to use them
does not make the file removal safer; it just makes it someone else's.

**Move to a directory inside the site, out of the document root.** Rejected: it
still writes under `/www/`, so it buys nothing on the guarantee, and it leaves
malicious content on a filesystem the web server can reach, in a directory a
misconfiguration could expose.

**Rename in place, for example to `.quarantined`.** Rejected for the same
reason: the content stays inside the site, and a rename can be undone by anyone
who still has write access — including whoever put it there.

**Write a helper script for the administrator to run.** Rejected as
dishonest. It has the same effect, with none of the checks, and it lets aaDoctor
claim a purity it would not have.

**Require a second confirmation, typed.** Considered and not adopted as the
primary control, because an interactive prompt is the thing people learn to
answer without reading. The controls chosen instead — a config gate, a finding
id rather than a path, and a hash that must still match — cannot be satisfied by
reflex. `--dry-run` covers the "let me see first" need explicitly.

**Accept a much wider remediation capability** (plugin deletion, core
restoration, PHP-FPM restart). Rejected. Each needs its own evidence, its own
failure modes and its own decision, and bundling them would make this ADR a
general permission rather than a specific one.
