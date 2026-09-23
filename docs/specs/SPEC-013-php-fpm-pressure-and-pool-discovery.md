# SPEC-013 — PHP-FPM Pressure and Pool Discovery

Status: Draft

Related: [README.md](../../README.md) §1, §4, §30, §31, §67, §68 ·
[ADR-001](../adr/ADR-001-non-invasive-read-only-architecture.md) ·
[ADR-007](../adr/ADR-007-aapanel-nginx-only-mvp.md) ·
Backlog: AAD-100 … AAD-102 (facts), AAD-097 (the findings, in SPEC-012)

---

## Problem

The failure chain this project was built to explain runs through PHP-FPM
(README §1): load rises, PHP-FPM accumulates, Nginx starts returning 502 and
504, and every site looks broken. aaDoctor currently observes both ends of that
chain — the load and the Nginx errors — and nothing in the middle.

The consequence is concrete. On a 2 GB server running 25 sites, the most likely
single explanation is a pool configured to fork more workers than the machine
has memory for. aaDoctor can see the memory exhaustion (after
[SPEC-010](SPEC-010-system-resource-monitoring.md)) and can see that PHP-FPM is
holding the memory (after [SPEC-011](SPEC-011-process-attribution.md)), but it
cannot see `pm.max_children`, so it cannot say whether what it is watching is a
traffic surge or a configuration that was always able to do this.

## Goal

Discover, **read-only**, what PHP-FPM is configured to do on this server — the
installed versions, the pools, their limits, their sockets — and map sites to
them as far as the configuration deterministically allows and no further.

**Facts only.** The findings built on these facts are defined in
[SPEC-012](SPEC-012-system-deterministic-findings.md); see the note on that
boundary there.

## Non-goals

- **Writing anything under `/www/server/php/`.** Not a pool file, not a status
  path, not an include. This is the hardest line in this document and the one
  most worth restating: the fix for half of what this spec detects is a
  configuration change, and aaDoctor does not make it
  ([/CLAUDE.md](../../CLAUDE.md) §7, README §4).
- Enabling `pm.status_path` or the slow log. Both would make diagnosis easier
  and both require editing a pool file. Not done, not offered, not suggested.
- Restarting or reloading PHP-FPM, ever (README §4).
- Querying the FastCGI socket. See **Rejected sources**.
- Supporting non-aaPanel PHP layouts
  ([ADR-007](../adr/ADR-007-aapanel-nginx-only-mvp.md)).
- Inferring a site's PHP behaviour from its code. Nothing under `/www/wwwroot`
  is read.

## Current context

[SPEC-002](SPEC-002-aapanel-discovery.md) already parses every vhost under
`/www/server/panel/vhost/nginx/` and extracts `server_name`, `access_log` and
`error_log`. It deliberately **does not follow `include`**. That decision stands
and this spec works within it — see **Site to pool mapping**.

[SPEC-003](SPEC-003-incremental-log-monitoring.md) already knows how to follow a
log file by `(device, inode, offset)` through rotation and truncation. The
PHP-FPM log is a log file. Nothing new is needed to read it.

**Everything about aaPanel's on-disk layout in this document is expected, not
verified.** The one piece confirmed from a real installation is the vhost
`include enable-php-74.conf` line. The rest is written from the documented
convention and **must be checked against a real aaPanel server before any of it
is implemented** — that is the first acceptance criterion, and the project has
already paid once for a fixture written from what a format looks like rather
than from what it is.

---

## Functional requirements

### Inputs and data sources

| Source | Expected path | What it gives | Confirmed? |
|---|---|---|---|
| Installed versions | `/www/server/php/*/` | the directory name is the version (`74`, `82`) | expected |
| Global config | `/www/server/php/<v>/etc/php-fpm.conf` | `include` for the pool directory, `error_log`, `emergency_restart_*` | expected |
| Pool configs | `/www/server/php/<v>/etc/php-fpm.d/*.conf` | `[pool]`, `pm`, `pm.max_children`, `pm.start_servers`, `pm.min_spare_servers`, `pm.max_spare_servers`, `pm.max_requests`, `listen`, `user`, `slowlog`, `request_slowlog_timeout`, `php_admin_value[memory_limit]` | expected |
| PHP-FPM log | `/www/server/php/<v>/var/log/php-fpm.log` | `server reached pm.max_children`, worker exits, restarts | expected |
| Slow log | whatever `slowlog` names, **if already enabled** | slow request traces | expected |
| Running workers | SPEC-011's scan, matched by pool label | how many children are actually running per pool | derived |
| Vhost PHP marker | the vhost files SPEC-002 already reads | `include enable-php-NN.conf` or `fastcgi_pass ...php-cgi-NN.sock` | **confirmed** |

Every path is probed, never assumed. A version directory that does not exist, a
pool directory that is empty, a log that is not readable — each degrades to a
coverage state ([SPEC-015](SPEC-015-diagnostic-coverage-self-check.md)) and
never to an exception.

### Pool configuration parsing

PHP-FPM pool files are INI-shaped but not INI: they carry `php_admin_value[...]`
keys with brackets, `$pool` interpolation, and `include` directives. A general
INI parser will mangle them.

The parser reads what it needs and ignores the rest:

- a `[name]` line opens a pool;
- `key = value` is captured only for keys in an explicit allowlist — the `pm.*`
  family, `listen`, `user`, `group`, `slowlog`, `request_slowlog_timeout`,
  `php_admin_value[memory_limit]`, `catch_workers_output`;
- `$pool` in a value is substituted with the pool name, which is the only
  interpolation PHP-FPM performs that matters here;
- `include=` inside a pool file **is followed**, but only one level deep, only
  to a path under the same PHP version's `etc/` tree, and never through a
  symlink that leaves it. This differs from SPEC-002's refusal to follow
  includes and the difference is deliberate: an Nginx `include` can point
  anywhere and pulls in a whole grammar, whereas a PHP-FPM pool `include` is a
  bounded, conventional split of the same file. The constraint above is what
  keeps it bounded;
- anything else is skipped silently. An unknown directive is not an error.

Values are stored as text and converted on read. `pm.max_children = 50` and
`pm.max_children=50 ; fifty` must both yield 50, and a value that cannot be
converted is recorded as unparsed rather than defaulted — a `pm.max_children`
guessed as some number would be worse than none at all, since it feeds a finding.

### Site to pool mapping

This is where the spec must be most careful, because the mapping is the thing
everyone will want and the thing the data least supports.

**Step one — site to PHP version — is deterministic** and comes from the vhost
file SPEC-002 already reads:

```text
include enable-php-74.conf;           -> version 74
fastcgi_pass unix:/tmp/php-cgi-74.sock;   -> version 74
```

The first is aaPanel's convention and is present in the project's own vhost
fixture. Note that the **filename alone** carries the version, so the include is
*not followed* — SPEC-002's rule holds, and this spec asks it only to capture
one more field from a line it already sees.

**Step two — version to pool — is usually trivial and occasionally not.** A
default aaPanel installation has exactly one pool per PHP version, named `www`.
When a version has one pool, the mapping is certain. When it has several, the
pool is identified by matching the vhost's `fastcgi_pass` target against each
pool's `listen` value; when the vhost has no `fastcgi_pass` of its own because
it is inside the included file, the mapping is **`ambiguous`** and is reported
as such.

**Step three — pool to site — does not exist, and cannot.** This is the
limitation that shapes everything downstream:

> On a default aaPanel, **every site on a given PHP version shares one pool.**
> A worker in that pool serves whichever request arrives. Attributing a worker,
> its memory or its CPU to a particular site is not possible from this data, and
> no amount of correlation with the access log makes it possible — only a
> per-site pool would, and aaDoctor may not create one.

So the honest ceiling is: *"PHP-FPM, PHP 7.4, the pool shared by these 19
sites"*. The report says exactly that, listing the sites as **scope**, never as
suspects. A reader must not be able to mistake the list for an accusation, and
the wording is part of the spec, not a rendering detail.

### Detecting saturation without touching a configuration file

The usual way to see pool saturation is the FPM status page, which requires
adding `pm.status_path` to a pool file. That is forbidden. Two read-only routes
remain, and together they are enough:

**1. Counting workers.** SPEC-011 already scans processes and extracts the
PHP-FPM pool label from the process title PHP-FPM writes for itself
(`php-fpm: pool www`). Counting the workers in a pool and comparing with that
pool's `pm.max_children` gives occupancy directly:

```text
workers_running / pm.max_children
```

This costs nothing beyond a scan that is already happening, requires no
configuration change, and is the primary source.

**2. The PHP-FPM log.** When a pool hits its ceiling, PHP-FPM writes:

```text
WARNING: [pool www] server reached pm.max_children setting (50),
consider raising it
```

This line is unambiguous, timestamped, and the single most diagnostic string in
this entire problem space. It is read by following
`/www/server/php/<v>/var/log/php-fpm.log` with the existing incremental reader
(SPEC-003) — from the end on first sight, so a year of history is never
replayed, exactly as every other log in this project is treated.

Other lines worth classifying from the same file, in the table-driven style
[SPEC-004](SPEC-004-nginx-log-parsing.md) already uses for the Nginx error log:

```text
server reached pm.max_children          pool ceiling reached
seems busy (you may need to increase    pool under pressure
  pm.start_servers or pm.min_spare_servers)
child NNN exited on signal 9 (SIGKILL)  likely the OOM killer
child NNN exited on signal 11 (SIGSEGV) a crash
child NNN exited with code N after ...  worker recycling or failure
execution timed out                     request exceeded its limit
```

`exited on signal 9` deserves particular attention: it is very often the OOM
killer taking a PHP worker, which corroborates
[SPEC-014](SPEC-014-host-kernel-events.md)'s `OOM_EVENT` from a completely
independent source. Two independent sources agreeing is the strongest evidence
this project can produce, and it is available here for free.

**aaDoctor does not change the log level** to obtain any of this. Every line
above is written at PHP-FPM's default `notice`/`warning` level. If a server has
been configured to log less, the coverage report says so
([SPEC-015](SPEC-015-diagnostic-coverage-self-check.md)) and the findings become
`not_evaluable` — they are never inferred from the silence.

### Rejected sources

Stated because each looks attractive and each was considered:

| Source | Why not |
|---|---|
| `pm.status_path` over the FastCGI socket | requires editing a pool file — forbidden (README §4). Even where an administrator has already enabled it, reading it means speaking FastCGI to a socket, which is a request to a running service rather than an observation of it. Out of scope; revisit only with a concrete case |
| `/www/server/panel/` database | aaPanel's internal database is explicitly not to be read or modified (README §4); its schema is undocumented and may change with any panel update |
| `php -i` or `php-fpm -tt` | a subprocess per version, executing panel-managed binaries to learn what a file already says (README §58) |
| Enabling the slow log | a configuration change. Read it if it is already on; never turn it on |
| `/proc/<pid>/cmdline` beyond the pool label | forbidden by SPEC-011's privacy rule |

---

## Technical behavior

- Discovery runs on the existing `[discovery] interval_seconds` alongside site
  discovery, so a PHP version added in the panel appears without a restart
  (README §16). Pool configuration changes rarely; re-reading it once a minute
  costs a handful of small file reads.
- Parsing is pure: a fixture directory of pool files in, a structure out. No
  process, no socket, no subprocess.
- The PHP-FPM log is registered with the existing log reader as one more
  followed file per PHP version. It is **not** a site log: it belongs to a
  version, its events are attributed to a pool, and it never contributes to a
  site's traffic counters.
- A pool file that cannot be parsed is reported per pool; the other pools are
  unaffected, in the same way one bad vhost does not stop discovery (README §65).
- Standard library only.

## Performance constraints

- Pool discovery: at most a few dozen small files per interval, under 10 ms.
- The PHP-FPM log adds one followed file per installed PHP version — typically
  two or three, against the 40-plus site logs already followed. Negligible.
- Worker counting is free: it reads SPEC-011's existing scan and performs no new
  `/proc` access of its own.
- Zero writes.

## Data structures

```text
PhpInstallation
  version            "74", "82"
  root               /www/server/php/74
  config_path        the php-fpm.conf that was read, or null
  log_path           the php-fpm.log being followed, or null
  pools[]
  warnings[]         what could not be read or parsed

PhpPool
  name               "www"
  version
  source_file
  pm                 "static" | "dynamic" | "ondemand" | null
  max_children, start_servers, min_spare_servers, max_spare_servers,
  max_requests                             integers or null
  listen             socket path or address, as written
  user, group
  memory_limit       from php_admin_value[memory_limit], as written
  slowlog_enabled    bool
  unparsed[]         keys present but not convertible

PoolRuntime                    joined from SPEC-011, per incident
  pool, version
  workers_running
  occupancy          workers_running / max_children, null when either is null
  rss_bytes, rss_per_worker_bytes
  cpu_pct

SitePhpMapping
  site
  version            or null
  pool               or null
  mapping_state      certain | ambiguous | unknown
  reason             why, when not certain
```

`mapping_state` is a first-class field rather than a nullable pool name,
because "we know it is pool www" and "there is one pool so it must be" and "we
have no idea" are three different claims and the downstream report needs to tell
them apart.

## Incident impact

The incident gains a `php_fpm` block, factual:

```text
php_fpm
  installations[]        version, pool configuration as discovered
  runtime                per pool: workers running, occupancy, memory
                         at the incident opening and at the load peak
  events                 counts per kind from the PHP-FPM log, within
                         the incident window, per pool
```

Counts and configuration only. No log line text is stored, consistent with
SPEC-006's rule that an incident holds aggregates and never raw log content —
the classification carries what a sample would have said.

## Edge cases

| Case | Behavior |
|---|---|
| No PHP installed | No installations; every PHP-FPM finding `not_evaluable` |
| PHP installed but FPM not running | Configuration discovered, zero workers, occupancy 0 — a real and reportable state |
| Several pools on one version | Mapping by `listen`; unresolved sites reported `ambiguous` |
| One pool, many sites | The normal case. Pool named, sites listed as scope, never as suspects |
| `pm = static` | `max_children` is the fixed count; occupancy still meaningful |
| `pm = ondemand` | Worker count varies by design; occupancy is reported with that noted in the evidence |
| `pm.max_children` missing or unparsable | Occupancy null; `PHP_FPM_POOL_SATURATION` reports `pm_limit_unknown` |
| Pool file unreadable | That pool reported as a warning; other pools unaffected |
| `include` inside a pool file | Followed one level within the same version's `etc/` tree, not beyond |
| A symlink pointing outside the tree | Not followed; recorded as a warning |
| PHP-FPM log missing or not readable | Log-derived events `not_evaluable`; worker counting still works |
| Log level reduced by the administrator | Coverage reports partial; silence is never read as health |
| Site uses no PHP | `version = null`, `mapping_state = certain`, and no PHP finding concerns it |
| Vhost has no PHP marker | `mapping_state = unknown`, reported as a coverage gap |
| Version directory exists, `etc/` does not | Installation recorded with no pools and a warning |

## Safety constraints

- **Read-only under `/www/server/php/`.** No file there is opened for writing,
  renamed, moved or created, and no permission is changed
  ([/CLAUDE.md](../../CLAUDE.md) §7,
  [ADR-001](../adr/ADR-001-non-invasive-read-only-architecture.md)).
- **PHP-FPM is never restarted, reloaded or signalled**, including after a
  configuration change made by someone else.
- `pm.status_path`, the slow log and the log level are never enabled, changed or
  suggested. A finding may report that a value is absent; it may not ask for it
  to be added.
- No FastCGI socket is connected to. No network request of any kind.
- No subprocess. `php`, `php-fpm` and `systemctl` are not executed.
- Nothing under `/www/wwwroot/` is read by this spec.
- Pool names, socket paths and log content are **untrusted strings** from
  outside aaDoctor. Counted, compared and printed; never used to build a
  filesystem path, a shell command or an executable format string. A pool name
  is never concatenated into a path — the pool's file is the one it was found
  in, remembered, not reconstructed.
- No output may be phrased as an instruction to change a PHP setting. PHP-FPM's
  own log says "consider raising it"; **aaDoctor does not repeat that sentence**
  as its own. It reports that the line was written, with its count and
  timestamp, which is a fact about the log rather than advice about the server.

## CLI impact

- `doctor` reports PHP-FPM coverage per version — configuration readable, log
  readable, mapping certain or ambiguous
  ([SPEC-015](SPEC-015-diagnostic-coverage-self-check.md)).
- `sites` may gain a PHP version column. Useful, cheap, and factual; decided
  when SPEC-015 fixes the coverage vocabulary, so the two agree.
- `diagnose` shows PHP-FPM findings through SPEC-012's system section. This spec
  adds no section of its own.
- **No `aadoctor php` command.** There is nothing to do with it that `diagnose`
  and `doctor` do not already cover.

## Persistence impact

Adds a `php_fpm` block to the incident record and one followed-file entry per
PHP version in `state.json`. No new file, no new directory.

## Interactions with existing specs

- **[SPEC-002](SPEC-002-aapanel-discovery.md)** — asked for **one additional
  captured field**: the PHP marker on the vhost (`include enable-php-NN.conf` or
  a `fastcgi_pass` target). Its rule of not following `include` is preserved —
  the filename itself carries the version. This is a real change to SPEC-002 and
  is recorded as such rather than assumed.
- **[SPEC-003](SPEC-003-incremental-log-monitoring.md)** — reused unchanged for
  the PHP-FPM log, including tail-from-end on first sight.
- **[SPEC-004](SPEC-004-nginx-log-parsing.md)** — a sibling table-driven
  classifier for PHP-FPM log lines. Its `kind` values are namespaced so a
  PHP-FPM kind can never be confused with an Nginx error kind.
- **[SPEC-011](SPEC-011-process-attribution.md)** — supplies the pool label and
  the worker counts. This spec defines no process reading of its own.
- **[SPEC-012](SPEC-012-system-deterministic-findings.md)** — owns the three
  PHP-FPM findings. This spec owns the facts they read.
- **[SPEC-015](SPEC-015-diagnostic-coverage-self-check.md)** — consumes the
  per-version, per-pool coverage states.
- **[SPEC-007](SPEC-007-deterministic-rules.md)** — unaffected. Its
  `PHP_ERROR_SPIKE` reads the *Nginx* error log and stays a separate thing with
  a separate name.

## Known limitations and TBDs

- **The aaPanel paths in this document are unverified.** They must be confirmed
  on a real installation before implementation, and the spec updated with what
  is actually there. This is listed first because it is the one that can
  invalidate the rest.
- **A pool cannot be attributed to a site** on a default installation. The
  ceiling is the pool and the list of sites that share it.
- **Worker count is a sample.** A pool that hit its ceiling between two process
  scans leaves no trace in the count — only in the log, which is why both
  sources exist.
- **`ondemand` pools** make occupancy a weaker signal, since a low worker count
  is the intended behaviour rather than spare capacity.
- **`memory_limit` is per request, not per worker**, and a worker's resident
  size is not bounded by it. `PHP_FPM_PROCESS_PRESSURE` uses *observed* resident
  size for that reason, and the configured limit appears only as context.
- TBD: whether to read an already-enabled slow log. It contains request URIs and
  stack traces — useful, and a privacy surface this project has so far avoided.
  Not decided, and deliberately not started.
- TBD: whether `sites` gains a PHP column.

## Acceptance criteria

- [ ] **Every path in this spec confirmed against a real aaPanel server**, and
      the spec corrected where it is wrong, before any other criterion is met.
- [ ] Installed PHP versions are discovered from the filesystem, not assumed.
- [ ] Pool files are parsed for the allowlisted keys, with `$pool` substituted,
      and unknown directives ignored without error.
- [ ] A `php_admin_value[...]` key does not break the parser.
- [ ] An `include` inside a pool file is followed one level within the version's
      own tree and nowhere else.
- [ ] A site's PHP version is resolved from the vhost without following the
      Nginx include.
- [ ] `mapping_state` is `certain` for a one-pool version, `ambiguous` where the
      configuration genuinely does not decide, and `unknown` where there is no
      marker.
- [ ] Worker counts per pool come from SPEC-011's existing scan with no
      additional `/proc` access.
- [ ] `server reached pm.max_children` is classified from the PHP-FPM log, with
      its pool and timestamp.
- [ ] `child exited on signal 9` is classified and correlates with SPEC-014's
      OOM events on a fixture where both are present.
- [ ] The PHP-FPM log is tailed from its end on first sight.
- [ ] A filesystem audit over a full discovery and tailing run shows **zero
      writes under `/www/`**, and no change to any file's mode or ownership.
- [ ] No output contains an instruction to change a PHP-FPM setting — asserted
      on the wording, including the absence of "consider raising".

## Verification

- `tests/test_php_discovery.py` against a fixture tree shaped like
  `/www/server/php/`: one version, several versions, a version with no `etc/`,
  a pool with `$pool` interpolation, a pool with `php_admin_value[...]`, a pool
  with an `include`, a pool with an unparsable `max_children`, an unreadable
  pool file, and a symlink pointing outside the tree.
- `tests/test_php_fpm_log.py` — the classification table against real PHP-FPM
  log lines. **Real lines**, taken from a running PHP-FPM, not written from
  memory. The project has already shipped one pattern that matched nothing
  Nginx ever writes, and that mistake is not to be repeated in a new file.
- Mapping tests over the existing vhost fixtures, extended with a site that has
  no PHP marker and a version with two pools.
- A container integration run: a synthetic `/www/server/php/` tree, a PHP-FPM
  log fed the ceiling line during a driven load curve, and an incident whose
  `php_fpm` block carries the pool, the limit, the worker count and the event.
- A filesystem audit asserting the tree is byte-identical, and identical in mode
  and ownership, after a full run.

## Out of scope

- The three PHP-FPM findings —
  [SPEC-012](SPEC-012-system-deterministic-findings.md).
- Anything that writes a PHP configuration — forbidden, permanently.
- The FPM status page and the FastCGI protocol — rejected above.
- Slow log reading — TBD above.
- Apache, OpenLiteSpeed and non-aaPanel PHP layouts
  ([ADR-007](../adr/ADR-007-aapanel-nginx-only-mvp.md)).
- Per-site pools, and any suggestion that the administrator create them.
