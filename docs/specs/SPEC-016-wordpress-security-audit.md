# SPEC-016 — WordPress Security Audit

Status: Draft

Related: [README.md](../../README.md) §4, §5, §58, §61, §90 ·
[ADR-001](../adr/ADR-001-non-invasive-read-only-architecture.md) ·
[ADR-007](../adr/ADR-007-aapanel-nginx-only-mvp.md) ·
ADR-011 (proposed, network) ·
Backlog: AAD-130 … AAD-136

---

## Problem

An aaPanel server hosting twenty-five WordPress sites is compromised through one
outdated plugin on one of them. The administrator sees the symptoms aaDoctor
already reports — load, 5xx, a site dominating traffic — and none of them says
*there is a webshell in `wp-content/uploads/2026/09/.cache.php`*.

The two facts do not even have to be related, which is the point: a site can
cause load without being infected, and a site can be infected without causing
any load at all. Today aaDoctor can see the first and is blind to the second.

The administrator's alternative is to log in and grep by hand across millions of
files, on a production server, during an incident.

## Goal

Inspect a WordPress installation and report evidence of compromise, **changing
nothing**, with every claim carrying the observations behind it.

## Non-goals

- **Writing, moving, renaming, deleting or changing the permissions of any file
  under `/www/`.** This spec is read-only without exception; remediation is
  [SPEC-017](SPEC-017-wordpress-quarantine-recovery.md) and is a separate
  command, a separate spec and a separate decision.
- Antivirus. aaDoctor does not ship signatures, does not claim to detect known
  malware families, and does not pretend to be exhaustive.
- Running in the daemon. The scan is on demand; see **Performance constraints**.
- Executing any PHP, ever. Not `php -l`, not `include`, not `eval`. Every file is
  read as bytes.
- Connecting to the WordPress database.
- Reading or recording any credential, including the ones that live in
  `wp-config.php`.
- Deciding that a site caused an incident, or that an incident means a site is
  infected. See **Relation to diagnose**.
- Network access in the default configuration. See **Core integrity**.

## Current context

[SPEC-002](SPEC-002-aapanel-discovery.md) discovers sites from the aaPanel
vhosts and extracts `server_name`, `access_log` and `error_log`. It does **not**
currently capture the `root` directive, and this spec needs it — that is a
required extension, recorded in SPEC-002 rather than assumed here.

Everything else is new. This is the first part of aaDoctor that walks a site's
files rather than reading its logs, and the first with a performance profile
measured in files rather than lines.

---

## Functional requirements

### Inputs and data sources

```text
the vhost root directive          SPEC-002, extended
the site's filesystem tree        read-only, bounded
a core manifest                   optional; see Core integrity
a vulnerability feed              optional; see Vulnerability intelligence
```

No database, no network in the default configuration, no subprocess, no PHP
execution.

### Detecting a WordPress installation

**Never by directory name, and never by `wp-config.php` alone.** A directory
called `wordpress` may hold anything, and `wp-config.php` appears in backups,
in archives and in snippets that are not installations.

The authoritative marker is:

```text
<root>/wp-includes/version.php   containing an assignment to $wp_version
```

corroborated by `wp-settings.php`, `wp-load.php` and the presence of both
`wp-admin/` and `wp-includes/` as directories.

Installations are looked for in a **bounded** set of places, in order:

```text
1. the vhost root itself
2. each immediate subdirectory of the vhost root, one level only
```

and nowhere else. A recursive hunt for `wp-includes` across a site tree is how a
scanner becomes the load it was meant to diagnose, and a WordPress buried five
levels down is rare enough to be reported as "not found" rather than paid for on
every scan of every site.

A site may hold more than one installation; each is reported separately with its
own root.

### Core version

```text
<root>/wp-includes/version.php    ->    $wp_version = '6.4.2';
```

read with a regular expression over a bounded read of the file. The file is
**never executed**.

The corroborating assignments in the same file — `$wp_db_version`,
`$tinymce_version`, `$required_php_version` — are captured too, and `readme.html`
and `wp-admin/about.php` are secondary claims.

**The declared version is a claim, not a fact, and the spec treats it as one.**
`wp-includes/version.php` is a writable PHP file inside the installation; an
attacker who has already modified core can edit it, and a scanner that trusts it
can be pointed at the manifest of a different release, where every real
modification then reads as an ordinary version difference.

The defence is corroboration, not trust:

- `wp-includes/version.php` is itself checked against the manifest for the
  version it claims;
- if a large share of core files mismatch, the version claim is reported as
  `version_unverified` and the integrity result is presented as **unreliable
  rather than as a list of modifications**.

The threshold for "a large share" is a starting point
(`core_mismatch_ratio_suspect = 0.10`) and, like every number here, unvalidated.

### Core integrity

Comparison is by **content**, never by name, size or timestamp. A file is
`MATCH` only when its hash equals the manifest's hash for that path.

```text
CORE INTEGRITY   6.4.2   (manifest: local cache, md5, fetched 2026-09-20)

wp-settings.php              MATCH
wp-includes/load.php         MODIFIED
wp-admin/admin.php           MATCH
wp-admin/x.php               UNKNOWN
wp-includes/cache-old.php    UNKNOWN
wp-includes/pluggable.php    MISSING

2013 files checked - 2009 MATCH, 1 MODIFIED, 2 UNKNOWN, 1 MISSING
```

Four states, and each means something different:

```text
MATCH       content equals the official file for this version
MODIFIED    the path is in the manifest and the content differs
UNKNOWN     the file is inside a core directory and is not in the manifest
MISSING     the manifest lists the path and the file is not there
```

#### Where the reference comes from

Three options were evaluated. **None is chosen silently, and the default
requires no network.**

**1. The official WordPress checksums API.** A small JSON document listing a
hash per shipped file, per version and locale. It is complete, authoritative and
cheap. It requires an outbound HTTPS request, and the hashes it publishes are
**MD5** — adequate against accident and weak against an adversary who can craft
a collision, which is worth recording even though a colliding file that must
also be valid, running PHP is a much harder problem than a bare collision.

**2. The official release package.** Downloading the release archive and hashing
its contents locally yields SHA-256 and a definitive file list. It is a much
larger transfer for the same answer, and the archive itself still has to be
trusted through the same channel.

**3. Manifests shipped inside the aaDoctor release.** No network at any point,
but a manifest is roughly two thousand entries per version, WordPress ships
several releases a year, and a server running an older version would find no
manifest for it. Shipping the set that covers real installations would dominate
the size of a release whose whole appeal is that it is small — and it would go
stale between releases, which is the failure mode that matters, because a
missing manifest looks exactly like a clean site if the report is careless.

**The decision is a three-tier model with no network by default:**

```text
Tier 0   default. No manifest, no network.
         CORE INTEGRITY is reported UNAVAILABLE, naming the version it
         would have needed. Every offline check still runs.

Tier 1   a manifest present in /var/lib/aadoctor/wp-manifests/<version>.json,
         placed there by the administrator - copied from another machine,
         fetched by hand, or generated from a release they already trust.
         No network from aaDoctor.

Tier 2   [wordpress] fetch_manifests = true, off by default: aaDoctor
         fetches the official checksums over HTTPS and caches them in the
         Tier 1 location. Requires ADR-011.
```

Option 1 is what Tier 2 fetches, because it is small and carries the
authoritative file list that `UNKNOWN` and `MISSING` depend on. Option 2 remains
the fallback for a locale or variant the API does not cover, and is not built
until one is found.

**A missing manifest is reported, never silently skipped.** "We could not check
core integrity" and "core integrity is fine" must never render the same way —
the same principle SPEC-012 applies to host resources, for the same reason.

The exact coverage of the official manifest — whether it includes the bundled
default themes and Akismet, and how it treats locale files — **must be confirmed
against a real API response before implementation**, not written from what the
format is believed to look like. This project has already shipped one pattern
that matched nothing the real software writes.

### Unknown files, without an avalanche

`UNKNOWN` is the highest-value finding in this spec and the easiest way to
produce a report nobody reads. The rule that makes it work:

> **A file can only be `UNKNOWN` inside a directory whose complete contents are
> known.** Everywhere else, the word is meaningless and is not used.

```text
wp-admin/ and wp-includes/     the manifest lists every file.
                               Anything else is genuinely unknown, and this
                               is the strongest structural signal available.
                               Requires a manifest; without one, unavailable.

the installation root          the manifest lists the core root files. Extra
                               PHP here is common and often legitimate, so it
                               is reported at a lower weight.

wp-content/plugins/<slug>/     third-party contents. NOT knowable. "Unknown"
wp-content/themes/<slug>/      is never reported here; only content, name and
                               timing signals apply, at a higher bar.

wp-content/uploads/            WordPress never writes PHP here. Executable
                               content in this tree is reported on location
                               alone, and needs no manifest at all.

wp-content/mu-plugins/         always active, no interface to list them.
                               Every file is inventoried, always - not as
                               malware, but because nothing else shows them.
```

That split is the whole anti-false-positive design: where a complete reference
exists, structure decides; where it does not, content and location decide, and
the tool does not pretend to know what belongs.

### Suspicious files

`eval()` in a file is not malware. Minifiers, licensing wrappers and legitimate
libraries all produce code that looks alarming in isolation, and a scanner that
reports each of them buries the one file that matters.

Evidence is therefore grouped into **five independent categories**, and risk
comes from how many categories agree, not from any single hit:

```text
L  location      where the file is
N  name          what it is called
I  integrity     what the manifest says (when there is one)
C  content       what the bytes contain
T  time/metadata when it changed, who owns it, what mode it has
```

Weights, all starting points and all unvalidated:

| Cat | Signal | Weight |
|---|---|---|
| L | executable PHP under `wp-content/uploads/` | 4 |
| L | PHP content in a file whose extension is not a PHP extension | 4 |
| L | `.htaccess` under uploads enabling PHP execution | 4 |
| L | PHP in a directory that contains no other PHP | 1 |
| N | hidden file (leading dot) with executable content | 2 |
| N | name imitating a core file but absent from the manifest | 3 |
| N | name with no pronounceable structure (random-looking) | 1 |
| I | file inside `wp-admin/` or `wp-includes/` absent from the manifest | 4 |
| I | path in the manifest, content differs | 4 |
| I | path in the manifest, file absent | 3 |
| C | an executor and an encoder in the same file | 3 |
| C | a request superglobal reaching an executor | 6 |
| C | command-execution function | 2 |
| C | dynamic function creation, or a variable function call | 2 |
| C | a single line longer than 2,000 characters | 1 |
| C | a base64-like blob longer than 512 characters | 2 |
| C | very low whitespace ratio in a PHP file | 1 |
| T | mtime newer than every core file by more than 30 days | 2 |
| T | mtime exactly equal to a core file's while content differs | 3 |
| T | world-writable mode | 1 |
| T | owner differs from the site's dominant owner | 1 |

Executors: `eval`, `assert`, `preg_replace` with the `/e` modifier,
`create_function`, `call_user_func` and `call_user_func_array` on request data.
Encoders: `base64_decode`, `gzinflate`, `gzuncompress`, `str_rot13`,
`convert_uudecode`, `hex2bin`. Command execution: `system`, `exec`,
`shell_exec`, `passthru`, `popen`, `proc_open`, and the backtick operator.

Risk bands require **breadth, not just score**:

```text
VERY_HIGH   a sufficient signal fired, or score >= 8 with 3+ categories
HIGH        score >= 5 with 3+ categories
MEDIUM      score >= 3 with 2+ categories
LOW         anything else that fired
```

Three signals are sufficient on their own, because each is something no
legitimate file does:

```text
a request superglobal passed directly to an executor
PHP content inside a file with an image extension, under uploads
a file in wp-admin/ or wp-includes/ that is absent from the manifest
  AND contains an executor/encoder pair
```

Each still prints its full evidence. Nothing is ever reported as malware on a
label alone:

```text
SUSPICIOUS_FILE                                           Risk: VERY HIGH

/www/wwwroot/site/wp-content/uploads/2026/09/.cache.php

Evidence:
- PHP inside wp-content/uploads, where WordPress never writes PHP   [L +4]
- hidden filename                                                  [N +2]
- created 2026-09-21, 412 days after the newest core file          [T +2]
- base64_decode and eval in the same file                          [C +3]
- base64-like blob of 3,104 characters                             [C +2]
Score 13, four categories.

This is evidence, not a verdict. aaDoctor did not open, execute or
change this file.
```

### Specific files always examined

Regardless of the walk, these are read and reported when present:

```text
wp-config.php          executor/encoder patterns, auto_prepend_file,
                       includes of paths outside the site
.htaccess              auto_prepend_file, handlers mapping non-PHP
                       extensions to PHP, rewrites to unexpected targets
index.php              the core root file; integrity and content
wp-settings.php        integrity and content
wp-content/mu-plugins/ inventoried in full, always
active theme functions.php    content signals
```

**From `wp-config.php`, no file content is ever recorded.** Only line numbers
and the names of the patterns that matched. The database password, the salts and
the authentication keys all live in that file, and a scanner that quoted a
matching line into a report — a report written to disk and possibly pasted into
a support ticket — would leak them. Recording only positions and pattern names
is a rule with no heuristic in it, which is why it is the rule.

### Plugin and theme inventory

```text
plugin    slug, name, version, path, status
theme     slug, name, version, path, parent theme, status
```

Read from the headers WordPress itself uses: the plugin header block in a
plugin's main PHP file, and `style.css` for a theme. Both are parsed with a
regular expression over a bounded read of the first 8 KB. Single-file plugins
are included. **No PHP is executed to obtain any of this.**

**`status` is `unknown`, and will stay `unknown` in this spec.** Which plugins
are active lives in the database, in `wp_options.active_plugins`. Reaching it
means reading the credentials out of `wp-config.php` and opening a database
connection — two things this spec refuses to do, one of them a credential
handling problem and the other a dependency README §8 deliberately keeps
optional. The field exists, it is honest about being unknown, and the one
exception is `mu-plugins`, which are active by definition.

Missing a version header is recorded as `version_unknown`, never guessed from a
directory name.

### Vulnerability intelligence

An inventory is only half useful without knowing which versions are exploitable.
This capability is planned and **deliberately not resolved here**:

```text
known vulnerable versions
abandoned plugins
plugins removed from the repository
plugins under active exploitation
```

Two things are ruled out immediately. **A static list baked into the aaDoctor
release is rejected**: it is stale the day it ships, it grows without bound, and
worst of all it produces a confident "no known vulnerabilities" from data that
is a year old. Silence from a stale source is the most dangerous output this
whole spec could produce.

**Inventing a local database is also rejected** — the project does not maintain
vulnerability intelligence and should not pretend to.

What is planned instead:

```text
an offline feed at /var/lib/aadoctor/wp-vulns/feed.json, with a declared
schema and a generated_at that the report always shows, and ages
```

The administrator refreshes it however they choose. An optional fetch sits
behind the same opt-in as the manifests (ADR-011) and is off by default.

The governing rule, whichever source arrives:

```text
no feed         -> UNAVAILABLE, naming what could not be checked
stale feed      -> the age is shown, prominently, next to every result
feed present    -> matches reported, with the feed's own identifiers
```

**Never "no vulnerabilities found" from an absent or stale feed.** This is the
same distinction SPEC-012 draws between "we looked and it was fine" and "we
could not look", and it matters more here, because the reader's next action is
to stop worrying.

### Report

A scan produces a report, and **the report is persisted** to
`/var/lib/aadoctor/wp-scans/<scan-id>.json`. That is aaDoctor's own directory
and is permitted (ADR-003).

Persisting is not a convenience. [SPEC-017](SPEC-017-wordpress-quarantine-recovery.md)
may only ever act on a finding from a stored report, identified by id and
carrying the hash of the file as it was when reviewed. A quarantine command that
accepted a path would be a remote file mover with a human typing the path; one
that accepts a finding id can verify that what it is about to touch is exactly
what a human read about.

Findings carry a stable `finding_id` and the file's SHA-256 at scan time.

Retention follows the existing `[incidents] retention_days` model, and a report
referenced by a live quarantine entry is never deleted.

---

## Technical behavior

- Every file is opened read-only and read in chunks. Nothing is executed.
- Hashing is SHA-256 for aaDoctor's own records; comparison against a manifest
  uses whatever algorithm that manifest declares, and the report says which.
- Content matching is literal substring and bounded regular expression work over
  a byte window. No regular expression may backtrack catastrophically on hostile
  input — the same rule SPEC-004 already follows, and the input here is
  attacker-authored by definition.
- The walk uses `os.scandir` and never `os.walk` with `followlinks`.
- Standard library only.

## Performance constraints

A server may hold twenty-five sites and millions of files. This is the first
part of aaDoctor that could plausibly become the outage.

- **On demand only. The daemon never scans.** See **CLI impact**.
- **One site per invocation.** There is no "scan everything".
- Hard caps, all configurable, all with defaults:

  ```text
  wp_max_files            200000    files visited per scan
  wp_max_depth            12        directory levels below the site root
  wp_max_file_bytes       2097152   2 MB; larger files are hashed but
                                    not content-scanned, and said to be
  wp_max_seconds          300       wall clock, then stop and report
  wp_read_chunk_bytes     65536     never read a whole file into memory
  ```

- Reaching any cap **truncates the scan and says so**. A truncated scan that
  looked complete would be the worst possible outcome of this feature.
- Content scanning is restricted to files that can execute: PHP extensions,
  `.htaccess`, and — under `uploads/` and for files with no or an unexpected
  extension — a magic-byte check of the first bytes for `<?php`. Reading the
  head of every file on the server is not affordable and is not done.
- **Symlinks are never followed out of the site root.** Each candidate is
  resolved and required to remain under the resolved root; anything else is
  recorded as a boundary refusal and not visited. This is also what stops a scan
  of one site wandering into another.
- Directory loops are detected by tracking the `(device, inode)` of every
  directory entered.
- The process lowers its own scheduling priority with `os.nice()` before walking
  — standard library, no subprocess.
- Budget: a 50,000-file site in under 60 seconds with content scanning enabled,
  measured rather than assumed.

## Data structures

```text
WordPressInstallation
  site, root, detected_by
  version_claimed, version_verified, version_state
  multisite                     detected from wp-config, reported, not handled
  plugins[], themes[], mu_plugins[]

CoreIntegrityResult
  version, manifest_source, manifest_algorithm, manifest_fetched_at
  state                         OK | UNAVAILABLE | UNRELIABLE
  counts                        match, modified, unknown, missing
  files[]                       path + state, bounded

SecurityFinding
  finding_id                    stable, referenced by SPEC-017
  code                          SUSPICIOUS_FILE | CORE_MODIFIED |
                                CORE_UNKNOWN_FILE | CORE_FILE_MISSING |
                                PHP_IN_UPLOADS | HIDDEN_EXECUTABLE |
                                CONFIG_ANOMALY | MU_PLUGIN_PRESENT |
                                VULNERABLE_COMPONENT
  path, size, mode, owner, group, mtime
  sha256                        of the file as scanned
  risk                          LOW | MEDIUM | HIGH | VERY_HIGH
  score, categories
  signals[]                     category, name, weight - every one of them
  quarantinable                 bool, with a reason when false

ScanReport
  scan_id, site, started_at, finished_at
  truncated, truncation_reason
  installations[], findings[], coverage
```

`risk` is **not** the diagnosis confidence of SPEC-007 and SPEC-012. It uses the
same four words because inventing a fifth vocabulary would be worse, and it
never enters a diagnosis score. The two are computed from different evidence
about different questions.

## Incident impact

**None.** A security scan does not touch the incident record, does not open an
incident and does not appear in one.

## Edge cases

| Case | Behavior |
|---|---|
| No WordPress at the site root | Reported plainly; not an error |
| WordPress in a subdirectory | Found at one level; deeper is reported as not found |
| Several installations in one site | Each scanned and reported separately |
| Multisite | Detected and reported; network-wide semantics not handled |
| `version.php` present but unparseable | Version unknown; integrity unavailable |
| Version claim contradicted by the manifest | Integrity reported `UNRELIABLE`, not as a modification list |
| No manifest for the version | `UNAVAILABLE`, naming the version needed |
| Manifest present, site has extra languages | Locale files outside the manifest are reported at low weight, not as core unknowns |
| Symlink pointing outside the site | Not followed; recorded as a boundary refusal |
| Symlink loop | Detected by `(device, inode)`; the walk continues |
| A 4 GB file | Hashed in chunks, not content-scanned, and said not to be |
| Unreadable file or directory | Counted and reported as a coverage gap, never silently skipped |
| Permission denied on the whole site | Scan fails clearly, naming the path and that root is required |
| Minified legitimate JavaScript or PHP | Content signals fire in one category only, so it cannot exceed LOW |
| A plugin that ships obfuscated licensing code | Same; recorded as a known false-positive source |
| Cap reached | Scan truncated and **labelled truncated** in every rendering |
| Site being written to during the scan | The report is a snapshot; hashes are as read, and that is stated |

## Safety constraints

- **100% read-only.** No file under `/www/` is created, written, moved, renamed,
  deleted, truncated, or has its mode, owner or timestamps changed. Not as a
  side effect either: files are opened `O_RDONLY`, and the scan does not update
  access times it can avoid.
- **No PHP is ever executed**, by any mechanism, including a linter.
- **No malware is cleaned, no core is restored, no plugin is disabled.** The
  report presents evidence and stops
  ([ADR-001](../adr/ADR-001-non-invasive-read-only-architecture.md)).
- **No credential is read or recorded.** From `wp-config.php` only line numbers
  and pattern names are kept; no `define()` value ever reaches a report, a log
  or stdout (README §61).
- No database connection.
- **No network in the default configuration.** Tier 2 is opt-in and gated by
  ADR-011.
- Paths, plugin names, theme names and file contents come from an attacker in
  the case this spec exists for. They are **untrusted in the strongest sense**:
  counted, compared, truncated and printed — never used to build a shell
  command, a format string, an `eval`, or a path to open. Output is escaped so a
  crafted filename containing control characters cannot forge a report line.
- No finding is phrased as an instruction. Not "delete this file", not "update
  this plugin". Describing what was found is this spec's whole job.
- A file reported at `VERY HIGH` risk triggers nothing. There is no automatic
  path from this spec to SPEC-017.

## CLI impact

A new command group, the first in the project:

```text
aadoctor wp sites            installations found, per site
aadoctor wp scan <site>      scan one site
aadoctor wp scan <site> --json
```

The group exists so the security domain is namespaced away from the performance
commands and can never be reached by accident from one of them.

Output follows SPEC-008's conventions: plain text, 80 columns, bounded columns
with middle elision, no colour as the only signal, clean when piped, errors to
stderr. Findings are ordered by risk, strongest first.

```text
exit 0   scan completed, whatever it found
exit 2   environment not ready
exit 3   site or installation not found
exit 5   insufficient privileges
```

**Exit 0 even at VERY HIGH risk.** A non-zero code for "found something" is
useful in a script and surprising at a prompt, and SPEC-008 already settled that
trade the same way for `diagnose`.

## Persistence impact

Writes only under `/var/lib/aadoctor/`:

```text
/var/lib/aadoctor/wp-scans/<scan-id>.json     scan reports
/var/lib/aadoctor/wp-manifests/<version>.json core manifests, Tier 1 and 2
/var/lib/aadoctor/wp-vulns/feed.json          the offline feed, when present
```

Atomic writes, per [ADR-003](../adr/ADR-003-filesystem-state-without-database.md).
Nothing outside that tree.

## Relation to `diagnose`

Deliberately separate, and the separation is a requirement rather than an
accident of layout.

```text
malware found          does NOT mean it caused the load
site caused the load   does NOT mean the site is infected
```

Both inferences are tempting and both are wrong often enough to matter. A
webshell can sit unused for months on an idle site; a site can dominate traffic
because a campaign went out. `aadoctor diagnose` never runs a security scan,
`aadoctor wp scan` never reads an incident, and neither one's findings enter the
other's score.

A future correlation — "the traffic spike went to a path that is a suspicious
file" — is genuinely interesting and is **out of scope here**. It needs both
halves to be trustworthy first, and it needs its own decision about what the
correlation is allowed to claim.

## Interactions with existing specs

- **[SPEC-002](SPEC-002-aapanel-discovery.md)** — must capture the vhost `root`
  directive, which it does not today. Recorded there. Without it this spec has
  no site path to scan.
- **[SPEC-008](SPEC-008-cli-reporting.md)** — a new command group under `wp`,
  reusing its output conventions and exit codes.
- **[SPEC-001](SPEC-001-installation-lifecycle.md)** — `uninstall --purge`
  removes `/var/lib/aadoctor/`, which now includes scan reports and cached
  manifests. Both are reproducible and their loss is harmless. **That is not
  true of SPEC-017's quarantine**, and the conflict is resolved there.
- **[SPEC-017](SPEC-017-wordpress-quarantine-recovery.md)** — consumes findings
  from a stored report, by id. It is the only thing in the project allowed to
  write under `/www/`, and only under ADR-010.
- **[SPEC-007](SPEC-007-deterministic-rules.md)** /
  **[SPEC-012](SPEC-012-system-deterministic-findings.md)** — unaffected. No
  security finding enters a diagnosis, and no diagnosis finding enters a scan.
- **ADR-011 (proposed)** — required before Tier 2 or any feed fetching exists.

## Known limitations and TBDs

- **This is not antivirus and cannot prove a site is clean.** A scan that finds
  nothing means these checks found nothing. The report must say that in those
  words, because "no findings" will otherwise be read as "not compromised".
- **Without a manifest, the strongest structural check is unavailable.** On a
  default installation with no network, `UNKNOWN` and `MODIFIED` cannot be
  computed at all, and the scan falls back to content, location and timing.
- **MD5 in the official checksums is weak against a determined adversary.**
  Recorded rather than solved; a colliding file that is also valid PHP doing
  useful work for the attacker is substantially harder than a bare collision,
  but the weakness is real.
- **Timestamps can be forged.** `touch` is available to anyone who can write the
  file, so `T` signals are corroboration and never proof — which is exactly why
  mtime *matching a core file exactly* while content differs is weighted higher
  than mtime merely being recent.
- **Plugin and theme status is unknown** without the database.
- **A compromised site can lie about its version**, handled by corroboration
  above but not eliminated.
- **Every weight and band in this spec is a starting point**, less validated
  than anything else in the project, and they will produce false positives on
  first contact with real sites. The first real scan should be treated the way
  the first production server was: as the instrument that finds the defects.
- TBD: whether locale files and the bundled default themes are inside the
  official manifest. Must be confirmed, not assumed.
- TBD: a correlation between security findings and incidents.
- TBD: whether a scheduled scan is offered at all, and if so as a systemd timer
  the administrator installs rather than anything the daemon does.

## Acceptance criteria

- [ ] **A full scan performs zero writes under `/www/`** and changes no mode,
      owner or timestamp — asserted by a filesystem audit over a synthetic tree
      compared byte for byte and stat for stat.
- [ ] No PHP is executed by any path — asserted by source inspection.
- [ ] An installation is detected by `wp-includes/version.php`, and a directory
      named `wordpress` containing nothing relevant is **not** detected.
- [ ] A `wp-config.php` alone does not constitute an installation.
- [ ] The version is parsed without executing the file.
- [ ] With no manifest, integrity is `UNAVAILABLE` and the report never implies
      the core is intact.
- [ ] With a manifest, `MATCH`, `MODIFIED`, `UNKNOWN` and `MISSING` are each
      produced on a fixture that contains all four.
- [ ] A forged `version.php` claiming a different release yields `UNRELIABLE`,
      not a list of modifications.
- [ ] A legitimate minified file produces at most `LOW`.
- [ ] A file needs two categories for `MEDIUM` and three for `HIGH`.
- [ ] A superglobal reaching an executor yields `VERY_HIGH` on its own.
- [ ] PHP under `uploads/` is found regardless of extension.
- [ ] **No value from `wp-config.php` appears in the report** — asserted by
      planting a password in a fixture and searching the serialized output.
- [ ] A symlink out of the site root is refused and recorded, and a symlink loop
      does not hang the walk.
- [ ] A scan of one site never reads a file belonging to another.
- [ ] Reaching a cap truncates the scan and every rendering says so.
- [ ] A filename containing control characters cannot forge a report line.
- [ ] A 50,000-file fixture scans within the budget.
- [ ] The report is written only under `/var/lib/aadoctor/`, atomically.

## Verification

- `tests/test_wp_detect.py` — detection over fixture trees: a real installation,
  a subdirectory installation, two installations, a decoy directory, a bare
  `wp-config.php`, a forged `version.php`, and a multisite.
- `tests/test_wp_integrity.py` — a synthetic manifest against a tree containing
  one modification, one unknown file, one missing file and the rest matching.
- `tests/test_wp_signals.py` — every signal in the weight table, each in
  isolation and in the combinations that reach each band. Explicitly: a real
  minified library that must stay at `LOW`, and a real webshell sample that must
  reach `VERY_HIGH`.
- `tests/test_wp_safety.py` — the credential test, the control-character test,
  the symlink-escape test, the loop test, and a source-purity test asserting no
  subprocess and no PHP execution.
- A **filesystem audit** in a container: a synthetic `/www` tree hashed and
  stat'ed before and after a full scan, asserted identical in content, mode,
  owner and mtime.
- A performance run over a generated 50,000-file tree.
- **Real samples are required** for the content signals. Patterns written from
  an idea of what a webshell looks like will match nothing and prove nothing;
  the project has already paid for that mistake once, in a smaller table than
  this one.

## Out of scope

- Any modification of any file —
  [SPEC-017](SPEC-017-wordpress-quarantine-recovery.md) and ADR-010.
- Network access — ADR-011.
- Database access, and therefore plugin activation state.
- Malware family identification, signatures and heuristics beyond the table
  above.
- Scanning non-WordPress applications.
- Correlating security findings with incidents.
- Scheduled or continuous scanning.
