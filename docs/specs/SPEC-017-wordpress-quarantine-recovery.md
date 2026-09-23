# SPEC-017 — WordPress Quarantine and Recovery

Status: Draft

Related: [README.md](../../README.md) §4, §5, §53, §54, §90 ·
[ADR-001](../adr/ADR-001-non-invasive-read-only-architecture.md) ·
ADR-010 (proposed, required) ·
[ADR-003](../adr/ADR-003-filesystem-state-without-database.md) ·
Backlog: AAD-140 … AAD-143

---

> **This spec deliberately breaks the guarantee the project is built on.**
>
> ```text
> /www/wwwroot = read-only
> ```
>
> It is a separate spec for exactly that reason, and it **must not be
> implemented until [ADR-010] is accepted**. ADR-010 is `Proposed`, and ADR-001
> — currently `Accepted` — explicitly rejected optional remediation behind a
> confirmation flag. Until that contradiction is resolved in the ADRs, nothing
> here may be built.

---

## Problem

[SPEC-016](SPEC-016-wordpress-security-audit.md) can report a webshell with
evidence and a risk level, and then the administrator has to remove it by hand:
SSH in, find the path, and decide whether to delete it. Deleting is the
dangerous part. If the file was a false positive, or if it was a real file that
something legitimately depended on, the deletion is permanent and the site may
break in a way nobody can trace back.

What is missing is not automation. It is a **reversible** removal: get the file
out of the site's document root immediately, keep it intact, and be able to put
it back exactly as it was.

## Goal

Move a file identified by a stored finding out of the site and into aaDoctor's
own directory, recording everything needed to restore it byte for byte, and
provide restore and purge — **all three requiring an explicit human command
every time**.

## Non-goals

- **Automation of any kind.** No confidence level, including `VERY_HIGH`,
  causes anything to be moved. See **No automation**.
- Cleaning a file. aaDoctor never edits content to remove malicious code; a file
  is moved whole or left alone.
- Restoring WordPress core from an official source. Related, genuinely useful,
  and a **different capability** — see **Core restoration**.
- Deleting, updating or disabling a plugin or theme.
- Anything running in the daemon. The daemon has no code path that writes under
  `/www/`, and after this spec it still must not.
- Quarantining a directory. One file per operation.
- Being a backup system.

## Current context

Every write aaDoctor performs today lands under `/opt`, `/etc/aadoctor`,
`/var/lib/aadoctor`, `/var/log/aadoctor` or its own unit file
([ADR-001](../adr/ADR-001-non-invasive-read-only-architecture.md)). Uninstall is
complete precisely because of that, and "did aaDoctor touch this file?" has one
answer for everything outside those paths.

This spec introduces the first exception in the project's history, and the
design below is mostly a set of constraints to keep it the only one.

---

## Functional requirements

### Gating

Three conditions, all required, before any write under `/www/` is possible:

```text
1. [wordpress] allow_quarantine = true in /etc/aadoctor/config.toml
   Default false. With it false, the commands exist, explain the setting,
   and exit without touching anything.

2. An explicit command naming a quarantine id or a finding id.
   No glob, no --all, no "everything above HIGH".

3. root.
```

The configuration gate is not security — an administrator who can edit the
config can also delete the file directly. It is a **statement of intent**: a
server where the key was never set is a server where this capability was never
enabled, and that is answerable from the config file alone during a
post-mortem.

### Acting only on reviewed findings

```bash
aadoctor wp quarantine <finding-id>
```

The argument is a `finding_id` from a **stored scan report**
([SPEC-016](SPEC-016-wordpress-security-audit.md)), never a path.

That is the central safety property of this spec. A command that accepted a path
would be a file mover with a human typing a path into a root shell — the exact
operation this spec exists to make safer. A command that accepts a finding id
can prove three things before it acts:

```text
the file was examined by a scan
a human read the evidence that scan produced
the file has not changed since that evidence was written
```

Before moving anything, the current SHA-256 of the file is computed and compared
against the hash the report recorded. **If they differ, the operation is
refused**, naming the mismatch and telling the administrator to re-scan. The
file on disk is no longer the file anyone reviewed.

### What is refused outright

| Refused | Why |
|---|---|
| A path argument instead of a finding id | above |
| A finding whose file hash has changed | it is not what was reviewed |
| Any file in the core manifest, including `MODIFIED` ones | removing `wp-includes/load.php` white-screens the site; a modified core file is fixed by restoring the official one, not by deleting it |
| `wp-config.php` | removing it takes the site down and destroys the only copy of its salts |
| `.htaccess` at the site root | same class of damage |
| A directory | one file per operation, always |
| A symlink | moving one moves a pointer and leaves the target; a symlink that is itself the finding is reported and refused |
| Anything whose resolved path is outside the resolved site root | see **Boundary** |
| A file already quarantined | no double-move |
| A finding from a report that no longer exists | the evidence cannot be shown |

`SPEC-016` marks each finding `quarantinable`, with a reason when it is not, so
the refusal is visible in the report before anyone types the command.

### Boundary

The **resolved** site root is the security boundary, and every path is checked
against it after resolution, not before:

```text
target   = os.path.realpath(candidate)
root     = os.path.realpath(site_root)
require  target starts with root + os.sep
require  target is a regular file, not a symlink, not a device
require  os.path.islink(candidate) is false
```

Resolution happens **once**, and the resolved path is what is used for every
subsequent operation, so nothing can change between the check and the use by
swapping a component for a symlink. Where the platform allows it, the file is
opened once and the descriptor is used for hashing, copying and removal, so the
check and the action apply to the same inode.

Explicitly defeated: `../` in any component, an absolute path injected through a
finding id, a symlinked directory partway down the path, and a path that escapes
into another site's root.

### The move

Order matters more than anything else in this spec.

```text
1. verify the gate, the finding, and the boundary
2. stat the original: size, mode, uid, gid, mtime, atime
3. compute SHA-256 of the original
4. compare with the report; refuse on mismatch
5. create /var/lib/aadoctor/quarantine/<site>/<id>/ with mode 0700
6. COPY the content into that directory
7. fsync the copy and the directory
8. re-hash the COPY and require it to equal the original's hash
9. write manifest.json atomically
10. only now, unlink the original
11. append to the action log
```

**The copy is verified before the original is removed, and never the other way
round.** `os.rename` is not used: `/var/lib` and `/www` are usually different
filesystems, so a rename would fail with `EXDEV` on most real servers, and a
move implemented as unlink-then-write has a window in which the file exists
nowhere. Copy-verify-unlink has a window in which it exists twice, which is
harmless.

If step 10 fails, the quarantine copy is removed and the operation reports
failure. A partial state where the file is both quarantined and still present
is never left behind silently.

Nothing else in the site is touched: no directory is created or removed, no
mode is changed, no other file is read or written.

### Quarantine store

```text
/var/lib/aadoctor/quarantine/<site>/<quarantine-id>/
    manifest.json
    file
```

`/tmp` is never used: it is world-writable, it is cleared on reboot, and
`PrivateTmp=true` in the unit means the daemon's `/tmp` is not even the same
directory the CLI would see.

The stored copy is named `file`, with no extension, and the directory is `0700`
owned by root. A quarantined webshell must not be reachable by a web server, and
must not carry a name that any tooling would treat as executable.

`manifest.json` records everything needed to put it back and to explain why it
left:

```text
quarantine_id
created_at
site, installation_root
original_path              the resolved path
sha256, size
mode, uid, gid, user, group
mtime, atime
finding_id, scan_id, finding_code, risk, signals[]
aadoctor_version, operator_uid
restored_at, purged_at     null until one happens
```

### `list`

```bash
aadoctor wp quarantine list
```

Everything currently held, newest first: id, site, original path, risk, size,
when, and whether the original location is now occupied by something else.

That last column matters — it is the difference between a restore that will
succeed and one that will be refused.

### `restore`

```bash
aadoctor wp restore <quarantine-id>
```

```text
1. locate the manifest
2. verify the quarantined copy still hashes to the recorded SHA-256
   -> mismatch means the quarantine store was tampered with: refuse
3. verify the destination is inside the resolved site root, again, now
4. if something exists at the destination:
     identical content  -> report already restored, change nothing
     different content  -> REFUSE, naming both hashes
5. verify the parent directory exists
   -> missing: refuse. aaDoctor does not recreate site directories
6. copy into place, fsync
7. re-hash the restored file and require a match
8. restore mode, uid, gid and mtime when they can be applied
9. mark the manifest restored; the copy is KEPT
10. append to the action log
```

**Never overwrite silently.** A different file at the destination means
something happened since the quarantine — a reinstall, a restore from backup, or
the attacker returning — and guessing which would be the single most destructive
thing in this document.

**The quarantine copy survives a restore.** Restore is not "undo and forget";
the copy stays until `purge` removes it deliberately. Disk is cheap and a
mistaken restore should still be reversible.

Metadata is restored **best effort and honestly reported**: on a filesystem or
in a context where a uid cannot be set, the file is restored with the content
correct and the ownership stated as not restored. Silence would leave a file the
web server may not be able to read.

### `purge`

```bash
aadoctor wp purge <quarantine-id>
```

Deletes the quarantined copy and marks the manifest purged. Permanent.

Constraints:

- **It accepts only a quarantine id.** Never a path, never a glob, never
  `--all`. A `purge` that could take a path would be `rm` with extra steps, and
  a bug in id resolution would then be able to delete something in the site.
- **It only ever deletes inside `/var/lib/aadoctor/quarantine/`**, and the
  resolved path is required to be under that directory before anything is
  removed.
- It refuses when the id does not exist, rather than succeeding vacuously.
- The manifest is kept, with `purged_at` set. The record of what was removed is
  the point; deleting the record too would erase the audit trail.

### Action log

Every quarantine, restore, purge and **refusal** is appended to

```text
/var/log/aadoctor/wp-actions.log
```

one line per event, append-only, never rewritten and never rotated by aaDoctor.

This exists because of what this spec costs. ADR-001 listed "auditable: did
aaDoctor touch this? always has the same answer" as a positive consequence, and
this spec takes that away. The log is the replacement, and it is weaker — so it
records refusals as well as actions, because "aaDoctor was asked to remove this
and refused" is exactly what a later post-mortem needs.

### Dry run

`--dry-run` on `quarantine` and `restore` performs every check and prints
exactly what would happen, writing nothing. It is the recommended first
invocation and the report says so.

### No automation

```text
the daemon finds something   -> it does nothing. The daemon never scans.
a scan finds something       -> it writes a report and stops.
risk is VERY_HIGH            -> a human still types the command.
```

There is no configuration key that enables automatic quarantine, no
`--auto`, no threshold above which action is taken. The gate enables the
*commands*, never a behaviour.

The reason is not caution for its own sake. A false positive that is merely
reported costs someone five minutes of reading; a false positive that is acted
on automatically takes a site down, at an hour nobody chose, for a reason nobody
will connect to aaDoctor. And the weights in SPEC-016 are the least validated
numbers in the entire project.

### Core restoration — a different capability

Replacing a `MODIFIED` core file with the official one is the obvious next
thought and is **deliberately not part of this spec**.

It is a different operation with different requirements: quarantine moves a file
*out* and needs no external truth, while core restoration writes a file *in* and
is only as trustworthy as where the replacement came from. That means a verified
manifest and almost certainly network access — ADR-011 — and it means writing
into `wp-includes/` rather than removing from `uploads/`.

Conflating the two under one `restore` verb would be worse than not having it:
`aadoctor wp restore` puts back something aaDoctor itself removed, which is
always safe to offer. Writing a file aaDoctor downloaded is not the same promise
and must not share the word.

**TBD**, with its own spec if it ever happens.

### Plugin remediation

Not planned here. Deleting a vulnerable plugin, updating one, or disabling one
are three more capabilities, the last of which needs database access. Each would
need its own decision; none is implied by this spec.

---

## Technical behavior

- Copies are chunked; a large file is never read whole into memory.
- Every write inside `/var/lib/aadoctor/` is atomic — temporary file plus rename
  ([ADR-003](../adr/ADR-003-filesystem-state-without-database.md)) — and
  `fsync` is called on the file and its directory before the original is
  removed, so a power loss cannot leave the file gone from both places.
- Hashing is SHA-256, computed at quarantine, verified before restore and
  verified after every write.
- Operations are serialized by a lock file under `/var/lib/aadoctor/`, so two
  concurrent invocations cannot act on one finding.
- No subprocess. No `mv`, no `cp`, no `rm`.
- Standard library only.

## Performance constraints

Irrelevant by design: these are single-file, interactive operations. One copy of
one file, bounded by that file's size. There is no walk, no scan and nothing
running continuously.

## Data structures

The quarantine manifest above, plus:

```text
QuarantineEntry        the in-memory view of one manifest
QuarantineIndex        built by scanning the quarantine directory
                       no index file: the directory is the index, so the
                       two can never disagree (ADR-003)
ActionLogRecord        at, action, result, quarantine_id, finding_id,
                       site, original_path, sha256, operator_uid, reason
```

## Incident impact

**None.** Quarantine has no relationship to incidents and appears in none.

## Edge cases

| Case | Behavior |
|---|---|
| `allow_quarantine` false | Commands explain the setting and exit; nothing is touched |
| Not root | Refused with exit 5 |
| Finding id unknown | Refused, exit 3 |
| Scan report deleted by retention | Refused: the evidence cannot be shown |
| File changed since the scan | Refused, both hashes named |
| File already gone | Reported as already absent; the manifest is not created |
| Core file, `MODIFIED` or not | Refused, pointing at core restoration |
| `wp-config.php` or root `.htaccess` | Refused |
| Directory or symlink | Refused |
| Path escapes the site root after resolution | Refused and logged as a boundary violation |
| Different filesystem | Expected; copy-verify-unlink, never rename |
| Disk full in `/var/lib` | Copy fails, original untouched, clear error |
| Unlink fails after a verified copy | Quarantine copy removed, operation reported failed |
| Two invocations at once | Serialized by lock; the second sees the result of the first |
| Destination occupied by a different file at restore | Refused, both hashes named |
| Destination occupied by an identical file | Reported already restored; nothing written |
| Destination parent directory missing | Refused; aaDoctor does not recreate site directories |
| uid cannot be restored | Content restored, ownership reported as not restored |
| Quarantined copy corrupted | Restore refused; the copy is not trusted |
| `purge` on an unknown id | Refused, not a silent success |
| `uninstall --purge` with entries present | **Refused** — see below |

## Safety constraints

- **The only path under `/www/` that may be written or removed is the exact
  resolved path of a file named by a stored, unmodified finding.** Nothing else,
  under any circumstance.
- No directory under `/www/` is ever created, removed or modified.
- No mode, owner or timestamp under `/www/` is changed, except when restoring
  the recorded metadata of a file aaDoctor itself removed.
- `chmod`, `chown` and `setfacl` are never applied to any other file
  ([/CLAUDE.md](../../CLAUDE.md) §7).
- No service is restarted or reloaded. Removing a webshell does not touch PHP-FPM
  or Nginx.
- `purge` can only delete inside `/var/lib/aadoctor/quarantine/`, verified after
  resolution.
- No file content is ever modified. Files move whole.
- Quarantined content is stored `0700`, root-owned, with a non-executable name,
  and is never served, executed or opened for anything but hashing and copying.
- Findings, paths and filenames are attacker-controlled and are **untrusted**:
  never used to build a shell command or a format string, and every path is
  validated by resolution rather than by string inspection.
- The action log never contains file content, only paths and hashes.

## CLI impact

Extends SPEC-016's group:

```text
aadoctor wp quarantine <finding-id>   [--dry-run]
aadoctor wp quarantine list
aadoctor wp restore <quarantine-id>   [--dry-run]
aadoctor wp purge <quarantine-id>
```

Four commands, no more. Each prints what it did, what it verified and where the
copy is.

```text
exit 0   the operation completed
exit 1   usage error
exit 3   unknown finding or quarantine id
exit 5   not root
exit 6   refused by a safety rule - a new code, because "refused because
         it would have broken your site" is not a usage error and must be
         distinguishable in a script
```

## Persistence impact

```text
/var/lib/aadoctor/quarantine/<site>/<id>/{manifest.json,file}
/var/log/aadoctor/wp-actions.log
```

**This changes the meaning of `uninstall --purge`**, and the conflict is real
rather than theoretical. SPEC-001 removes `/var/lib/aadoctor/` entirely, and
after this spec that directory can contain the **only copy** of files removed
from a live site. A purge would destroy them permanently, in a command whose
documented promise is that it removes only aaDoctor's own data.

Resolution:

```text
uninstall --purge REFUSES while the quarantine holds any entry that has
not been restored or purged.
```

It names the count, points at `aadoctor wp quarantine list`, and exits non-zero.
A `--force-quarantine` escape may be offered, and if it is, it must state
exactly how many files will be destroyed and require them to be named. This is
recorded in SPEC-001 as well, so the two documents cannot drift.

## Interactions with existing specs

- **[SPEC-016](SPEC-016-wordpress-security-audit.md)** — the only source of
  findings. Its `quarantinable` flag and stored hash are what make this spec
  safe; without a persisted report this capability cannot exist as designed.
- **[SPEC-001](SPEC-001-installation-lifecycle.md)** — `uninstall --purge` must
  refuse while quarantine is non-empty. Recorded there.
- **[ADR-001](../adr/ADR-001-non-invasive-read-only-architecture.md)** — this
  spec contradicts its decision text. ADR-010 exists to resolve that, and until
  it is accepted the contradiction stands and nothing here may be built.
- **[ADR-003](../adr/ADR-003-filesystem-state-without-database.md)** — the
  quarantine directory is the index; no separate database, no index file.
- **[SPEC-006](SPEC-006-load-incident-detection.md)** /
  **[SPEC-012](SPEC-012-system-deterministic-findings.md)** — unaffected. No
  incident, no diagnosis and no finding of theirs can reach this spec.
- **The daemon** — gains nothing. It has no code path into any of this.

## Known limitations and TBDs

- **Removing a webshell is not cleaning a compromise.** The entry point that put
  it there is still open, and there are usually more files. The report must say
  so every time, because a quarantine that felt decisive is exactly when someone
  stops looking.
- **Quarantine can break a site.** A false positive on a file something depended
  on takes functionality down until it is restored. That is the cost of the
  capability; restore is what bounds it.
- **A restore can be refused and leave the administrator stuck** if the
  destination is occupied by something different. Deliberate: the alternative is
  overwriting, which is worse.
- **Nothing here detects re-infection.** A file quarantined at noon and rewritten
  at one o'clock is simply a new finding on the next scan.
- **The action log is not tamper-proof.** Root can edit it. It is an audit aid,
  not evidence.
- TBD: core restoration from an official source — its own spec, and network.
- TBD: whether `uninstall --purge` gets a `--force-quarantine` escape at all.
- TBD: retention for quarantined files. Currently none: they stay until purged
  by hand, which is the safe default and will eventually need a policy.

## Acceptance criteria

- [ ] **With `allow_quarantine` false, no command writes anything** — asserted
      by a filesystem audit.
- [ ] A path argument is rejected; only a finding id is accepted.
- [ ] A finding whose file changed since the scan is refused, naming both hashes.
- [ ] A core file, `wp-config.php`, a root `.htaccess`, a directory and a
      symlink are each refused.
- [ ] A path escaping the site root through `../`, an absolute path or a
      symlinked directory component is refused and logged.
- [ ] The original is removed **only after** the copy is written, fsynced and
      re-hashed — asserted by injecting a failure at each step and checking the
      original survives every one.
- [ ] A cross-filesystem quarantine succeeds (no `rename`).
- [ ] `restore` refuses when a different file occupies the destination, and is a
      no-op when an identical one does.
- [ ] `restore` reproduces content, mode and mtime exactly; unrestorable
      ownership is reported rather than silently skipped.
- [ ] `purge` accepts only an id, deletes only inside the quarantine directory,
      and keeps the manifest.
- [ ] Every action **and every refusal** appears in the action log.
- [ ] `--dry-run` writes nothing anywhere.
- [ ] **`uninstall --purge` refuses while quarantine is non-empty.**
- [ ] No automatic path exists from a scan to any of these commands — asserted
      by source inspection and by a scan of a fixture containing a `VERY_HIGH`
      finding, after which the file is still in place.
- [ ] The daemon contains no code path that writes under `/www/`.

## Verification

- `tests/test_wp_quarantine.py` over a synthetic site tree: the happy path, each
  refusal above, the hash-mismatch case, and the failure-injection sequence that
  proves the original survives a failure at every step.
- `tests/test_wp_restore.py` — clean restore, occupied destination with
  identical and with different content, missing parent, corrupted quarantine
  copy, and metadata restoration.
- `tests/test_wp_purge.py` — id only, boundary enforcement, unknown id,
  manifest retained.
- A **traversal suite**: finding ids and paths crafted with `../`, absolute
  paths, symlinked components, a symlink pointing into another site, and a
  component swapped for a symlink between the check and the action.
- A container run exercising quarantine and restore across a real filesystem
  boundary (`/var/lib` and `/www` on different mounts), confirming the file
  returns byte-identical with its mode and mtime.
- The existing lifecycle audit extended: `uninstall --purge` refuses with a
  quarantine entry present, and succeeds after it is restored.
- A **negative audit**: a full daemon run over a site containing a `VERY_HIGH`
  finding, asserting the synthetic `/www` is byte-identical and stat-identical
  afterwards. Nothing happens without a human.

## Out of scope

- Scanning and detection — [SPEC-016](SPEC-016-wordpress-security-audit.md).
- Core restoration from an official source — TBD above.
- Plugin or theme deletion, update or deactivation.
- Cleaning or editing file content.
- Database access.
- Network access — ADR-011.
- Any automatic action, permanently.
