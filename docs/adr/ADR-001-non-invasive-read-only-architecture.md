# ADR-001 — Non-invasive read-only architecture

Status: Accepted
Date: 2026-09-22

Related:
- README.md §3.1, §4, §5, §55, §90
- SPEC-001, SPEC-002, SPEC-003

## Context

aaDoctor runs on production servers hosting dozens of live sites managed through
aaPanel. The administrator installs it precisely when the server is already
misbehaving — the worst possible moment to introduce a tool that changes things.

aaPanel owns its own configuration, its Nginx vhosts, its PHP pools and its log
rotation. A monitoring tool that adjusts permissions to read a log, or reloads
Nginx to apply a richer log format, creates a second source of truth for the
server's configuration and becomes a suspect in every later incident.

There is also a trust problem: a diagnostic tool is only installed if the
administrator is confident it cannot make the outage worse.

## Decision

aaDoctor observes. It does not administer.

It reads aaPanel configuration, Nginx vhosts, site logs and `/proc`, and it
writes exclusively to its own paths:

```text
/opt/aadoctor/
/etc/aadoctor/
/var/lib/aadoctor/
/var/log/aadoctor/
/usr/local/bin/aadoctor
/etc/systemd/system/aadoctor.service
```

Nothing is ever written under `/www/server/`, `/www/wwwroot/` or
`/www/wwwlogs/`. aaDoctor never runs `chmod`, `chown`, `setfacl`, `rm`, `mv` or
`truncate` against aaPanel paths; never creates cron entries or modifies a
crontab; never changes firewall rules or blocks addresses; and never restarts or
reloads Nginx, PHP-FPM or MySQL. The only service it controls is its own.

Reports may say what the administrator could investigate. They never act, and
they never phrase a finding as a command to run.

## Consequences

### Positive

- Installing during an incident is safe: the tool cannot be the cause.
- Uninstall is genuinely complete — after `--purge` the server is byte-identical
  outside aaDoctor's own paths.
- No conflict with aaPanel's own management of vhosts, logs and rotation.
- Auditable: "did aaDoctor touch this?" always has the same answer.
- Every later feature is constrained by a rule that is simple to check in review.

### Negative

- Reading every site's logs without changing permissions currently implies
  running as root (README §57). A read-only root process is accepted as the
  lesser evil against modifying aaPanel file permissions.
- aaDoctor cannot improve the data it receives. If the access log lacks
  `$request_time`, timing analysis is simply unavailable (README §67–§68).
- A site with unreadable logs stays unmonitored; the fix is the administrator's
  decision.
- No auto-remediation, ever. Users who want automatic blocking need a different
  tool — deliberately.

## Alternatives considered

**Adjust permissions or ACLs to read logs as a non-root user.**
Rejected: it modifies aaPanel-managed files, which is the exact guarantee this
decision protects. The permission change would also survive an uninstall.

**Apply an enhanced `log_format` and reload Nginx.**
Rejected as an automatic behavior. It would improve diagnosis quality but makes
aaDoctor a configuration manager and puts it in the blast radius of every
subsequent Nginx problem. It stays available as documented, manual, reversible
guidance (README §68).

**Include optional remediation behind a confirmation flag.**
Rejected for the MVP. Once the code can block an IP or restart PHP-FPM, that
capability exists on every server regardless of the flag, and the tool's
position in incident post-mortems changes fundamentally (README §90).
