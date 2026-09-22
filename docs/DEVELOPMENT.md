# Development

Practical notes for working on aaDoctor. Rules for code agents are in
[/CLAUDE.md](../CLAUDE.md); architecture is in [README.md](../README.md).

---

## Requirements

For real integration testing:

```text
Linux
Python 3
systemd          daemon integration tests
aaPanel + Nginx  real environment tests
```

Minimum Python version: **TBD** (README §88). It will be chosen after checking
what is actually installed on common aaPanel servers, not by picking the newest
convenient release.

Unit development does not require a real aaPanel server. Discovery, parsing,
offsets, rotation and rules must all be testable against fixtures on any
machine, including Windows and macOS for editing purposes.

Anything that cannot be tested without a real aaPanel server is, by definition,
not covered by unit tests — keep that surface small.

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

None. No entry point exists yet.

### Planned commands

From README §75, once AAD-001 lands:

```bash
python3 ./aadoctor doctor
python3 ./aadoctor diagnose
```

Installed usage (README §93), once phase 1 completes:

```bash
aadoctor doctor
aadoctor enable
aadoctor status
aadoctor top
aadoctor diagnose
aadoctor disable
```

Do not document a command before it exists. When a command lands, move it from
`Planned` to `Current` in this file and record it in
[CHANGELOG.md](CHANGELOG.md).

---

## Safety while developing

The non-invasive guarantee applies to development machines too:

* Never run a development build against a production aaPanel server with write
  access to anything under `/www/`.
* Never test uninstall or purge on a machine you cannot rebuild.
* When testing rotation, rotate a fixture copy — never an aaPanel log.

---

## Fixtures

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

No fixtures are created yet. They land with the phase that needs them.

---

## Verification expectations

Per phase, before an item is `Done`:

| Phase | Minimum verification |
|---|---|
| 1 | Installer runs twice with the same end state; purge leaves no trace; `doctor` writes nothing |
| 2 | Offsets survive restart; rotation and truncation resume correctly; no full-file reads |
| 3 | Parser handles malformed lines without stopping; aggregation memory stays bounded |
| 4 | Incident is created on a synthetic load trigger; no duplicate incidents during a sustained spike |
| 5 | Each rule fires on its fixture and does not fire on `normal-access.log` |
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
