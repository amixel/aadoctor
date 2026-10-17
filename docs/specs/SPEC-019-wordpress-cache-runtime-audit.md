# SPEC-019 — WordPress Cache and Runtime Configuration Audit

Status: Draft

Related: [README](../../README.md), [SPEC-002](SPEC-002-aapanel-discovery.md), [SPEC-013](SPEC-013-php-fpm-pressure-and-pool-discovery.md), [ADR-001](../adr/ADR-001-non-invasive-read-only-architecture.md)
Backlog: AAD-152, AAD-153

## Problem

On 2026-10-17, a recently migrated WordPress site (`portalvitrine.com.br`) showed hundreds of HTTP 500 errors associated with the W3 Total Cache Lazy Load mutator on PHP 8.3. After disabling that option, Page Cache remained disabled even though `WP_CACHE=true` and `advanced-cache.php` existed. Re-enabling Page Cache reduced repeat-page TTFB from roughly 3.3 seconds to 0.118 seconds (about 96%). The external Cloudflare response was `cf-cache-status: DYNAMIC`; that does not prove origin cache was absent. aaDoctor's current logs cannot recognize this configuration discrepancy.

## Goal

Offer an **explicit, on-demand, read-only** audit of locally discovered WordPress installations and their cache configuration, distinguishing facts from hypotheses, without altering a site or performing HTTP probes.

## Non-goals

Automatic WordPress discovery via network; cache purges; plugin updates; change of `wp-config.php`; modifying W3TC, Nginx, Cloudflare or PHP; HTTP benchmarking; reading database credentials; active origin/CDN testing; estimating cache hit ratio from `WP_CACHE` alone; telling administrators to activate a specific cache method as an automated action.

## Current context

aaDoctor discovers vhost document roots. This audit is an opt-in subcommand, not continuous daemon work, to avoid reading PHP application files on every monitor tick. It is separate from the security scanner (SPEC-016), and must not feed security findings or drive the incident diagnosis score.

## Functional requirements

1. `aadoctor wp cache --site <discovered-site>` reports installation recognition (`wp-includes/version.php`, `wp-config.php`, `wp-content/`), PHP version if known, and source coverage. Reject unknown sites. No traversal out of the discovered site.
2. Check `WP_CACHE` as a safely parsed literal define (true/false/unknown), presence only of `advanced-cache.php`, `object-cache.php`, `db.php`, and presence/version of W3 Total Cache where discoverable. Do not evaluate PHP, execute WordPress, or dump config contents.
3. For an installed W3TC configuration, parse allowlisted JSON-like fields `pgcache.enabled`, `pgcache.engine`, `dbcache.enabled`, `objectcache.enabled`, and `lazyload.enabled` from `wp-content/w3tc-config/master.php` only when a safe structured payload is recognized. Unknown/unreadable/malformed is reported as such; do not perform broad regular-expression scans that expose secrets.
4. Make these distinctions explicit: `WP_CACHE=true` does **not** prove page cache enabled; presence of a drop-in does **not** prove active caching; `cf-cache-status: DYNAMIC` does **not** establish local cache status; `file`, `file_generic` and plugin-specific engine names must be reported verbatim with no unverified equivalence.
5. Report deterministic discrepancies, not causes: e.g. `WP_CACHE enabled, W3TC page cache disabled`, `drop-in present while plugin page cache disabled`, or `cache config unavailable`. A slow URL alone is never a caching diagnosis.
6. Inspect available local error-log evidence **only from existing SPEC-003/004 parsed kinds**, if available, with error counts and plugin path categories; never parse entire historical logs or execute a direct new tail. Fatal `UserExperience_LazyLoad_Mutator.php` is a concrete evidence example, not a general root-cause rule. If unavailable, say so.
7. Always provide uncertainty and suggest manual verification steps for the administrator (without altering server state).

## Technical behavior

Resolve a discovered document root to its canonical path; reject symlink escapes; open only explicitly allowlisted small files with file-size limits and no log replay. Sensitive values such as `DB_PASSWORD`, salts, API keys, session cookies and full file bodies must never enter result, errors, state or stdout. Parse configuration defensively; support variable spacing but never evaluate PHP code.

## Data structures

`WpCacheAudit {site, wordpress_detected, php_version?, wp_cache_state, dropin_presence, w3tc_config_state, page_cache_state, page_cache_engine?, object_cache_state, database_cache_state, lazyload_state, findings[], caveats[]}`. Every field that could not be determined is `unknown` rather than false.

## Edge cases

Site absent, WordPress relocated, symlinked docroot, inactive plugin with leftover config, stale drop-in after migration, malformed W3TC PHP-wrapped config, other caching plugins, Nginx bypass, page-specific exclusions, personalized HTML, multisite, edge CDN behavior, and plugin version drift. All limit the result without false assurance.

## Safety constraints

No mutation, no subprocess, no network/Cloudflare API, no requests to the target site, no DB connections, no plugin bootstrap, no reading secrets into outputs or persistent incident records. Stay within the aaDoctor existing read-only guarantee; audit never alters `/www`.

## CLI impact

Add `aadoctor wp cache --site domain` after SPEC-016 command compatibility is checked. If `wp` is reserved for security, reuse its dispatcher without changing existing syntax. Clear short output with evidence, limitations and unknown states. No changes to `top`, `diagnose` scoring or incident triggering.

## Persistence impact

None. On-demand output only; no new state.

## Performance constraints

At most a handful of bounded file reads under one discovered site; no recursive plugin scans and no background polling.

## Incident impact

None. Audit output can help human troubleshooting but does not become automatic incident evidence.

## Interactions with existing specs

SPEC-002 provides site roots and PHP mapping where known. SPEC-016 remains independent: security signals are not caching signals. SPEC-013 is not modified. Existing CLI dispatcher must avoid command collisions.

## Acceptance criteria

- [ ] Sample W3TC config with `WP_CACHE=true`, page cache false and drop-in present is correctly labeled inactive/mismatched.
- [ ] Enabling page cache in fixture changes only the reported state; no cache hit-rate claim.
- [ ] Missing plugins, stale files, unreadable config and non-W3TC cache show `unknown` rather than disabled.
- [ ] Redaction tests prove salts, DB credentials and config fragments never appear in stdout/diagnostics.
- [ ] No writes or outbound requests; no cache purge or Nginx/PHP modifications.
- [ ] End-to-end CLI fixture test with WordPress and non-WordPress vhosts.

## Verification

Fixture-driven tests using sanitized config examples from the October 2026 incident; negative tests for secrets, symlinks, huge files and malformed config; compare site tree hashes before and after.

## Out of scope

Live HTTP timing, Cloudflare analytics, automatic WordPress performance optimization, origin cache-hit verification and PHP/SQL profiling.
