# Development

Practical notes for working on aaDoctor. Rules for code agents are in
[/CLAUDE.md](../CLAUDE.md); architecture is in [README.md](../README.md).

---

## Requirements

For real integration testing:

```text
Linux
Python 3.8+      see ADR-008
systemd          daemon integration tests
aaPanel + Nginx  real environment tests
```

Minimum Python version: **3.8**
([ADR-008](adr/ADR-008-minimum-python-version.md)). `tomllib` is used when the
interpreter has it (3.11+) and a subset parser otherwise, so no external
dependency is needed on older hosts.

Unit development does not require a real aaPanel server. Discovery, parsing,
offsets, rotation and rules must all be testable against fixtures on any
machine, including Windows and macOS for editing purposes.

Anything that cannot be tested without a real aaPanel server is, by definition,
not covered by unit tests — keep that surface small.

## Verifying from a Windows workstation

The primary development machine is Windows and has no Python interpreter.
Docker Desktop is available, and it is how anything is actually executed:
every command below runs in a disposable Linux container against the working
tree, so nothing on the host is installed or modified.

Test suite, on the minimum supported interpreter:

```powershell
docker run --rm -e PYTHONDONTWRITEBYTECODE=1 -v "${PWD}:/work" -w /work python:3.8-slim `
  python -m unittest discover -s tests -t tests
```

Run it on `python:3.12-slim` as well: that is the path where `tomllib` replaces
the fallback parser, and both must behave identically.

CLI smoke test:

```powershell
docker run --rm -e PYTHONDONTWRITEBYTECODE=1 -v "${PWD}:/work" -w /work python:3.8-slim sh -c `
  "python3 ./aadoctor --version; python3 ./aadoctor doctor; python3 ./aadoctor status"
```

Full SPEC-001 lifecycle — install, reinstall, enable, disable, uninstall, purge,
plus a synthetic `/www` tree that must come through byte-identical:

```powershell
docker run --rm -v "${PWD}:/work:ro" python:3.8-slim bash /work/tests/integration/lifecycle.sh
```

Release path — build an artifact, serve it, install from it, reject a corrupted
one, update. Needs network on first run, to install `curl` in the image:

```powershell
docker run --rm -v "${PWD}:/work:ro" python:3.8-slim bash /work/tests/integration/release.sh
```

SPEC-007 end to end against the real daemon — three scenarios, roughly four
minutes each, driving a load curve and a traffic burst and then running
`diagnose` on what the daemon wrote. Needs the tree writable, because it
builds a synthetic `/www`:

```powershell
docker run --rm -v "${PWD}:/work" python:3.8-slim bash /work/tests/integration/diagnose.sh
```

The container has no systemd, so that script stubs `systemctl` and records every
call. It proves which units aaDoctor acts on; it does not prove the unit starts.
Confirming that still needs a host with real systemd.

Three tests are skipped when the suite runs as root, which is the default in a
container. To exercise them, run the suite as a non-root user inside the
container.

### What the container cannot check: file permissions

**A Windows bind mount reports every file as `-rwxrwxrwx`.** Inside the
container the permission bits are fiction, so nothing about them can be
verified through the working tree — and `core.filemode` is `false` here, so the
bits on the Windows disk are fiction too.

This cost a broken first install on a real server: everything came out of
`git clone` as `-rw-r--r--` and `sudo ./install.sh` answered `command not
found`. The whole container suite had passed, because every script is invoked
there as `bash ./install.sh`, which never needs the bit.

Two defences, and both look at something other than the working tree:

* `tests/test_scripts.py::Executable` asserts the mode **git records**, which
  is what a server clones. Add a new script to `EXECUTABLES` there.
* `tools/package.sh` sets the modes itself instead of inheriting them, and
  `release.sh` asserts them inside the built tarball — otherwise an artifact
  built here would ship world-writable files.

The general lesson is worth more than the rule: verifying that something
*works* is not the same as verifying it is usable **the way the documentation
says to use it**. Run the documented command, not a convenient equivalent.

---

## Development philosophy

```text
small commits
small specs
small diffs
deterministic behavior
explicit verification
```

Concretely:

* One backlog item per branch and per review.
* If a change needs a paragraph to justify itself, it needs a spec or an ADR.
* Same input, same output. No hidden clocks, no random iteration order in
  anything that produces a finding.
* "It works" is not verification. Say what you ran and what it showed.

---

## Local execution

### Current commands

From a checkout (no installation, no root):

```bash
python3 ./aadoctor --version
python3 ./aadoctor --help
python3 ./aadoctor doctor
python3 ./aadoctor status
python3 ./aadoctor sites
python3 ./aadoctor top
python3 ./aadoctor top --window 1m --site example.com
python3 ./aadoctor top --json
python3 ./aadoctor incidents
python3 ./aadoctor show 2026-09-22T18-31-40
python3 ./aadoctor diagnose
python3 ./aadoctor diagnose 2026-09-22T18-31-40 --json
python3 ./aadoctor daemon --verbose --log-file /tmp/aadoctor.log
```

The daemon needs aaPanel to be present before it discovers or follows anything.
On a machine without it, it starts, says so and idles.

`top` reads `/var/lib/aadoctor/runtime.json`, which the daemon publishes once
per poll. Without a daemon running there is nothing to read, and `top` says so
rather than showing an empty table as if it were the truth.

`incidents`, `show` and `diagnose` read `/var/lib/aadoctor/incidents/`. To
exercise the load detector without waiting for a real spike, point the
collector at a file you control - `aadoctor.collectors.load.LOADAVG_PATH` -
and write load values into it. That is how the container verification drives a
load curve.

`show` and `diagnose` are deliberately different commands over the same file:
`show` renders what was measured, `diagnose` renders a reading of it. If you
are ever tempted to have `show` print a finding, that is the moment the tool
stops being checkable.

Discovery can read any directory, so it needs no aaPanel and no root:

```bash
python3 ./aadoctor sites --vhost-dir tests/fixtures/vhosts
python3 ./aadoctor sites --vhost-dir tests/fixtures/vhosts --json
```

`python3 -m aadoctor` works too, with `src` on the path:

```bash
PYTHONPATH=src python3 -m aadoctor --version
```

Requiring root, and therefore a disposable machine.

**Clone anywhere except `/opt/aadoctor`** — that is where aaDoctor installs to,
and a checkout there would be the thing being replaced. The installer refuses
rather than letting it happen, but `/usr/local/src/aadoctor` is the habit to
have:

```bash
sudo ./install.sh                  # from this checkout
sudo ./install.sh --release        # from the latest published release
sudo ./install.sh --version 0.1.0  # from that exact release
sudo aadoctor enable
sudo aadoctor disable
sudo aadoctor update [--version X] [--force]
sudo aadoctor uninstall
sudo aadoctor uninstall --purge
```

Building a release artifact (no root, writes to `dist/`):

```bash
./tools/package.sh
```

Tests:

```bash
python3 -m unittest discover -s tests -t tests
```

### Planned commands

Not implemented, and deliberately not registered — running it is a usage error
listing what does exist:

```bash
aadoctor explain <incident>
```

Do not document a command before it exists. When a command lands, move it from
`Planned` to `Current` in this file and record it in
[CHANGELOG.md](../CHANGELOG.md).

---

## Safety while developing

The non-invasive guarantee applies to development machines too:

* Never run a development build against a production aaPanel server with write
  access to anything under `/www/`.
* Never test uninstall or purge on a machine you cannot rebuild. The integration
  script refuses to run outside a container unless
  `AADOCTOR_INTEGRATION_ALLOW=1` is set explicitly — do not set it casually, it
  removes `/opt/aadoctor`, `/etc/aadoctor`, `/var/lib/aadoctor` and
  `/var/log/aadoctor`.
* When testing rotation, rotate a fixture copy — never an aaPanel log.

---

## Fixtures

### Vhosts (SPEC-002)

```text
tests/fixtures/vhosts/
```

Twelve committed `.conf` files covering the shapes aaPanel produces: a plain
site, several `server_name` values, commented-out directives, `access_log off`,
a log format argument, an error severity, absent directives, duplicate
directives, an HTTP block redirecting to an HTTPS one, a catch-all
`server_name _`, a helper file with no `server` block, and a deliberately
malformed file.

They are written in aaPanel's style, with the brace on its own line. When a
real `/www/server/panel/vhost/nginx` is available, compare it against these and
add any shape that is missing — anonymized, as below.

### Log lines (SPEC-004)

Parser cases live as inline strings in `tests/test_parsers.py`, not as files: a
parser that had to open a file would already have failed its own contract.

### Incident records (SPEC-007)

```text
tests/_scenarios.py
```

Builders producing the incident shape SPEC-006 writes, including the three
scenarios SPEC-007 names. The rules consume structured data and never reopen a
log, so a fixture made of log lines would exercise SPEC-004 and SPEC-005 a
second time and SPEC-007 not at all.

### Log files (SPEC-005 onwards)

Planned location:

```text
tests/fixtures/
```

Planned cases, derived from README §76:

```text
normal-access.log
traffic-spike.log
one-ip-flood.log
404-flood.log
502-spike.log
upstream-timeout.error.log
php-fatal.error.log
```

Further cases the specs already need: log rotation, truncated log, missing
access log, missing error log, malformed line, huge query strings.

Fixture rules:

* Anonymize real data before committing it. No customer domains, no real client
  IPs, no tokens or session identifiers.
* Keep each fixture small and focused on the case it is named after.
* A fixture is evidence for a test; if no test reads it, delete it.

The log fixtures are not created yet. They land with the phase that needs them.

---

## Verification expectations

Per phase, before an item is `Done`:

| Phase | Minimum verification |
|---|---|
| 1 | Installer runs twice with the same end state; purge leaves no trace; `doctor` writes nothing |
| 2 | Discovery finds sites from directives and survives a bad vhost; first sight of a log reads nothing; offsets survive restart; rotation and truncation resume correctly; no full-file reads |
| 3 | Parser handles malformed lines without stopping and never raises on arbitrary text; aggregation memory stays bounded; a flood of unique keys cannot hide the dominant one |
| 4 | Incident created on a synthetic load trigger; one incident per sustained spike, not one per poll; the peak snapshot holds the traffic, not the opening one |
| 5 | Each rule fires on its fixture, at its threshold and not below it, and none fires on an evenly loaded server; a diagnosis can be inconclusive and says so; the same incident always yields the same answer |
| 6 | Each command renders from stored data only and fits an 80-column terminal |
| 7 | Everything above still passes with AI disabled, and with AI enabled but failing |

---

## Testing scope

Priorities from README §77:

```text
parser
offset
log rotation
incident rules
installation safety
uninstallation
```

Hard rule: **no test may modify a real aaPanel installation.**
