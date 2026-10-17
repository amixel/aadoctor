# SPEC-018 — PHP-FPM Slow-Log Evidence

Status: Draft

Related: [SPEC-003](SPEC-003-incremental-log-monitoring.md), [SPEC-011](SPEC-011-process-attribution.md), [SPEC-013](SPEC-013-php-fpm-pressure-and-pool-discovery.md), [SPEC-015](SPEC-015-diagnostic-coverage-self-check.md), [ADR-001](../adr/ADR-001-non-invasive-read-only-architecture.md)
Backlog: AAD-150, AAD-151

## Problem

On 2026-10-17 an aaPanel host with 2 vCPUs and 2 GiB RAM ran PHP 7.4, 8.2 and 8.3. At 09:56, PHP 8.2 logged many executions over 10 seconds and terminated several requests near 100 seconds, including `portaldestaques.com.br/index.php`. The existing slow log showed stacks stopping in `wp-config.php`, `wp-settings.php` and `INCLUDE_OR_EVAL`; `x24.com.br` also appeared. These traces do **not** establish that those files caused the delay. aaDoctor currently cannot summarize already-enabled slow-log evidence.

## Goal

Read **existing, enabled** PHP-FPM slow logs incrementally and summarize which versions, scripts/sites and top stack frames were sampled around an incident, without inventing a root cause.

## Non-goals

Enabling slowlog, changing timeouts, probing a socket, running PHP, inspecting WordPress source, automatic tuning, storing full traces, ranking a site from a stack frame alone, or creating a new incident trigger.

## Current context

SPEC-013 covers PHP versions, pools, config and FPM event logs, and explicitly leaves slowlog support TBD. This spec resolves **only that TBD**. The source path must be discovered from the already-read pool config `slowlog` value, never guessed or constructed from untrusted request text. A missing or disabled slowlog is normal.

## Functional requirements

1. Recognize `request_slowlog_timeout` and `slowlog` already configured for each discovered pool; expose `enabled | disabled | unreadable | unknown`.
2. Follow an existing regular-file slow log using SPEC-003's inode/offset handling, tail-from-end on first discovery, rotation/truncation and bounded bytes per tick; do not replay history.
3. Parse PHP-FPM headers in the observed form `[17-Oct-2026 09:56:18] [pool www] pid 1100809`, `script_filename = /www/wwwroot/portaldestaques.com.br/index.php`, and stack lines `[0x...] [INCLUDE_OR_EVAL]() /path/wp-config.php:97`. Handle missing fields and format drift without crashing.
4. Count samples per PHP version/pool, script/site when confidently mapped, and normalized *top* frame category (`wordpress_bootstrap`, `plugin`, `theme`, `other`, `unknown`). Do not retain full paths, source snippets, URIs, query strings or raw stack traces in incidents. No user-controlled path is printed verbatim in default output.
5. Keep separate **FPM termination** evidence from SPEC-013's `php-fpm.log` and **slow-stack sample** evidence. Time proximity is correlation, not causation. Never infer CPU, I/O, deadlock, database or OPcache cause from `INCLUDE_OR_EVAL` alone.
6. If several sites share the same FPM pool, infer the sampled site only from a validated script root under a discovered vhost; otherwise report `site_unknown` or `ambiguous`. A slow-stack sample does not apportion shared host load.
7. Include only counts, timestamps, categories, mapping quality and coverage state in the bounded incident evidence. Surface in existing `diagnose` system section and `doctor` coverage; do not add a separate command.

## Technical behavior

Use the existing incremental log reader with a bounded multiline state machine: header starts a record; script path and first N frames are considered until next header; malformed blocks are skipped with parse-error counters. Follow configured paths only after verifying regular file, size cap, readable ownership and no symlink escape from the allowed PHP log tree; record a coverage reason when custom paths cannot safely be read. Cap memory and per-tick work; never read a whole file after rotation. Sanitize control characters.

## Data structures

`SlowlogCoverage {version, pool, state, reason}`; `SlowlogSample {timestamp, version, pool, site_mapping_state, site?, category}`; `SlowlogWindow {sample_count, category_counts, mapped_site_counts, coverage, parse_errors}`. Samples are ephemeral; the incident persists aggregated fields only.

## Edge cases

Multi-pool files; partial lines across polls; log rotation; a stack without a header; frame addresses changing; shared pools; files with private URLs; historical timestamps; time-zone mismatch; identical sample repeated; missing/disabled slowlog; proc permission denial; sample during a CPU-saturated host. All downgrade coverage or attribution rather than creating a culprit.

## Safety constraints

Read-only; zero writes below `/www`. No subprocess, network call, socket access, daemon restart or PHP config mutation. No auto-remediation. Existing aaDoctor state only. Strict bounds on line length, frame count, path checks, output and stored evidence.

## CLI impact

`aadoctor diagnose <incident>` adds a factual slowlog evidence subsection when available; `aadoctor doctor` reports slowlog coverage. Report `not observed` only when coverage is valid; otherwise `not evaluable`.

## Persistence impact

Backward-compatible optional bounded `php_fpm.slowlog` aggregation in incident JSON and existing reader offsets. No database or time-series store.

## Performance constraints

One incremental read per distinct configured file, reused when pools share a file. Bounded per tick regardless of file size; no recursive filesystem scan.

## Incident impact

Freeze the slowlog aggregate for incident opening/peak window where available, with explicit sample window and gaps.

## Interactions with existing specs

SPEC-013 owns discovery and FPM event logs; its slowlog TBD becomes a link to this draft. SPEC-003 owns incremental reading. SPEC-011 supplies context, not automatic site attribution. SPEC-015 owns coverage wording. SPEC-012 must not treat sample frequency as proof of causation; any future rule requires separate acceptance.

## Acceptance criteria

- [ ] Real aaPanel-formatted PHP 8.2 sample at 09:56 parses, including multi-site traces, without leaking raw URIs.
- [ ] Missing/disabled slowlog reports lack of coverage, not health.
- [ ] One pool hosting multiple sites does not name a culprit from occupancy or stack alone.
- [ ] No stale pre-install entries are ingested; rotation and partial frames work.
- [ ] Bounded CPU, bytes, output, and incident size; zero changes under `/www`.
- [ ] No new incidents or rules generated from slowlog alone.

## Verification

Fixtures from redacted real PHP-FPM logs; parser and privacy tests; rotation tests; system-level incident replay with slowlog present/absent; filesystem immutability check on aaPanel-shaped tree. No live service changes.

## Out of scope

Full profiler, tracing, tuning, database query diagnosis, site code inspection and root-cause assertions from a sampled frame.
