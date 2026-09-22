# SPEC-001 — Installation Lifecycle

Status: In Progress

Related: [README.md](../../README.md) §5, §12, §45–§56 ·
[ADR-004](../adr/ADR-004-systemd-daemon-lifecycle.md) ·
[ADR-008](../adr/ADR-008-minimum-python-version.md) ·
Backlog: AAD-001, AAD-002, AAD-004, AAD-005, AAD-006

Implemented: local installation, configuration handling, `enable` / `disable`,
`uninstall` and `--purge`, the service unit and a minimal daemon.
Not implemented: release download, SHA256 verification and `aadoctor update`,
because no release has been published yet. See **Implementation notes** below.

---

## Problem

aaDoctor is installed on production servers that already host dozens of live
sites. An installer that half-succeeds, overwrites a configuration, or leaves
residue in the aaPanel tree is worse than no tool at all. The administrator must
be able to install, upgrade and remove aaDoctor without ever wondering what it
touched.

## Goal

A single installation path that is idempotent, verifiable and fully reversible,
plus the lifecycle commands that control only aaDoctor's own service.

## Non-goals

- Package manager integration (`apt`, `yum`, `pip`).
- Multiple concurrent installations on one host.
- Automatic rollback to a previous version (Parking Lot).
- Any configuration of Nginx, PHP or aaPanel.

## Current context

The target layout is fixed by README §12 and the release scheme by README §48.
Python 3.8 or newer is assumed present on aaPanel servers
([ADR-008](../adr/ADR-008-minimum-python-version.md)); `git` is not
(README §47).

---

## Functional requirements

### Filesystem layout

Installed:

```text
/opt/aadoctor/            src/, aadoctor, VERSION, LICENSE
/etc/aadoctor/            config.toml
/var/lib/aadoctor/        state.json, offsets/, incidents/
/var/log/aadoctor/        aadoctor.log
/usr/local/bin/aadoctor   CLI entry point
/etc/systemd/system/aadoctor.service
```

No other path outside this list is created, modified or removed. In particular
nothing under `/www/`.

### install

Ordered steps (README §49):

1. verify privileges;
2. verify the system is Linux with systemd;
3. verify Python 3;
4. detect aaPanel;
5. create required directories;
6. download the release artifact;
7. verify SHA256 against the published checksum;
8. install files;
9. preserve existing configuration;
10. install or update the service unit;
11. `systemctl daemon-reload`.

The installer uses `set -euo pipefail` and requires only:

```text
curl  tar  sha256sum  python3  systemd
```

A failed step aborts with a clear message and leaves the previous state intact.
There is no silent partial installation.

When aaPanel is not detected, installation may complete but the daemon is not
enabled, and the output states why (README §80).

### upgrade

Triggered by `aadoctor update`:

```text
discover installed version
↓
discover available version
↓
download release
↓
verify SHA256
↓
extract to a temporary location
↓
replace /opt/aadoctor
↓
preserve /etc/aadoctor and /var/lib/aadoctor
↓
restart aadoctor.service only if it was running
```

The current installation is never deleted before the new package is downloaded
and verified (README §79).

### enable / disable

```text
enable   systemctl enable --now aadoctor
disable  systemctl disable --now aadoctor
```

Both are idempotent. After `disable`, no aaDoctor process remains. Data under
`/etc/aadoctor/` and `/var/lib/aadoctor/` is preserved.

### uninstall

```text
stop daemon
disable service
remove service unit
remove CLI entry point
remove /opt/aadoctor
systemctl daemon-reload
```

Preserves `/etc/aadoctor/` and `/var/lib/aadoctor/` by default.

### purge

`aadoctor uninstall --purge` additionally removes:

```text
/etc/aadoctor
/var/lib/aadoctor
/var/log/aadoctor
```

Final output:

```text
aaDoctor completely removed.

No aaPanel files were modified.
```

That statement must be literally true: no cron entry, no firewall rule, no
permission or ownership change, no Nginx or PHP configuration change, and
nothing left under `/www/server/`, `/www/wwwroot/` or `/www/wwwlogs/`
(README §55).

### Idempotency

Running the installer once or twenty times produces the same end state.
Re-running after a successful install is a no-op apart from version replacement
and `daemon-reload`.

### Configuration preservation

If `/etc/aadoctor/config.toml` exists, it is never overwritten. The default
configuration is copied only when no configuration exists. A new release that
adds keys relies on built-in defaults for the missing keys rather than rewriting
the administrator's file.

### Checksum verification

Each release publishes:

```text
aadoctor-<version>.tar.gz
aadoctor-<version>.tar.gz.sha256
```

The installer verifies the artifact before extraction. A mismatch aborts before
any file is replaced.

---

## Technical behavior

- The service unit follows README §56 (`Type=simple`, `Restart=on-failure`,
  `RestartSec=5`, `NoNewPrivileges=true`, `PrivateTmp=true`).
- Only `aadoctor.service` is ever started, stopped, enabled, disabled or
  restarted. No other unit is touched, ever.
- `systemctl daemon-reload` is issued after installing or removing the unit.
- Directory permissions: configuration and state are not world-readable, since
  incidents may contain client IPs.
- The installed `VERSION` file is the single source for the running version.

## Data structures

`/opt/aadoctor/VERSION`:

```text
0.1.0
```

Configuration follows README §14. Defaults live in code; `config.example.toml`
mirrors them.

## Edge cases

| Case | Behavior |
|---|---|
| Not root | Abort before any change, explain what is required |
| No systemd | Abort; the daemon is out of scope without it |
| Python 3 missing | Abort with the detected version, if any |
| aaPanel not detected | Install may proceed, daemon not enabled, reason printed (README §80) |
| Nginx not detected | No monitoring started; message per README §81 |
| Download fails | Abort, existing installation untouched |
| Checksum mismatch | Abort before extraction; temporary files removed |
| Disk full mid-install | Abort; previous `/opt/aadoctor` still present |
| Unit file modified by the administrator | TBD — overwrite vs. preserve is undecided |
| Purge with the daemon running | Stop first, then remove |
| Purge with files already absent | Succeeds; absence is not an error |

## Safety constraints

- No write anywhere under `/www/`.
- No `chmod`, `chown`, `setfacl`, `rm`, `mv` or `truncate` against aaPanel paths.
- No cron entry, no crontab modification, no firewall change.
- No restart or reload of Nginx, PHP-FPM or MySQL at any point, including during
  upgrade.
- The installer does not modify `log_format` or any Nginx configuration to get
  richer logs (README §68).

## CLI impact

Introduces `enable`, `disable`, `update`, `uninstall` (with `--purge`).
Rendering rules are in [SPEC-008](SPEC-008-cli-reporting.md).

## Persistence impact

Creates `/etc/aadoctor/` and `/var/lib/aadoctor/` including `offsets/` and
`incidents/`. Upgrade preserves both. Purge removes both.

---

## Implementation notes

Decisions taken while implementing this spec. They extend it; they do not
replace anything above.

**Installed contents.** `/opt/aadoctor/` also holds `uninstall.sh`, which the
layout above does not list. `aadoctor uninstall` delegates to it so that every
`rm` lives in exactly one place, and so removal still works when the Python
installation is the thing that is broken.

**Interpreter in the unit file.** README §56 hardcodes `/usr/bin/python3`. The
shipped unit keeps that line, and `install.sh` rewrites `ExecStart` with the
interpreter it actually detected, so hosts where python3 lives elsewhere work
without editing the unit.

**Installation does not enable the service.** The installer prepares everything
and stops. Starting monitoring is an explicit `aadoctor enable`, matching the
three-command flow in README §45. An upgrade restarts `aadoctor.service` only
if it was already running.

**Release mode is not implemented.** Steps 6 and 7 of the install sequence —
download and SHA256 verification — have no implementation, because no release
exists to download and inventing a URL would be worse than an honest error.
`install.sh` runs from a checkout or an unpacked release archive and aborts with
a clear message otherwise. `aadoctor update` is not registered as a command.

**Staging paths.** Installation stages into `/opt/.aadoctor.stage` and keeps the
outgoing version in `/opt/.aadoctor.previous` until the swap succeeds. Both are
literal constants inside the installer's removal allowlist.

**Insufficient privileges exit code.** Commands that need root exit `5`. The
code was added to the table in [SPEC-008](SPEC-008-cli-reporting.md).

**Minimum configuration.** `config.example.toml` ships only the keys this
version reads (`[monitor]`, `[mysql]`, `[ai]`), not the full set in README §14.
Keys arrive with the phase that uses them; because an existing configuration is
never overwritten, older files keep working through the built-in defaults.

**No LICENSE file yet.** README §11 and the layout above list one. Choosing a
licence is the project owner's decision, so none was invented; `install.sh`
copies `LICENSE` only if it is present.

---

## Acceptance criteria

- [ ] Installer run twice yields an identical end state.
- [ ] An existing `config.toml` survives an upgrade unchanged.
- [ ] A corrupted artifact is rejected before any file is replaced.
- [ ] `enable` / `disable` are idempotent; `disable` leaves no process.
- [ ] `uninstall` preserves config and state; `--purge` removes every path in
      README §54 and nothing else.
- [ ] After purge, a diff of the aaPanel tree shows no aaDoctor-attributable
      change.
- [ ] No step in any path restarts or reloads a service other than
      `aadoctor.service`.

## Verification

On a disposable Linux VM with aaPanel + Nginx:

1. Snapshot `/www/`, the crontab, and the firewall rules.
2. Install, install again, upgrade, enable, disable, uninstall, purge.
3. Re-snapshot and diff. The diff must be empty apart from aaDoctor's own paths
   being absent.
4. Repeat the install with a deliberately corrupted checksum file and confirm
   the abort happens before extraction.

## Out of scope

- Versioned rollback — Parking Lot.
- A dedicated non-root service user — Parking Lot, README §57.
- Hardening the unit beyond README §56 — Parking Lot, after field testing.
