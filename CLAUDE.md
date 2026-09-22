# CLAUDE.md — Rules for code agents working on aaDoctor

Read [`/README.md`](README.md) before making any architectural or implementation decision.

The README is not background reading. It defines the scope, the limits and the
non-invasive guarantees of this project. If a change contradicts it, the change
is wrong until the README says otherwise.

---

## 1. Source of truth

Authority order:

```text
1. README.md
2. active SPEC
3. accepted ADRs
4. BACKLOG.md
5. current implementation
6. old comments
```

If two sources conflict, do not invent a third answer.

Do one of these instead:

* update the higher-authority document and say so in the summary; or
* record the divergence in [STATUS.md](docs/STATUS.md) under `Known risks` and stop.

Never resolve a conflict silently inside code.

---

## 2. Working rule

Work in small phases. One backlog item at a time.

Expected flow:

```text
read README
↓
read relevant SPEC
↓
read relevant ADRs
↓
inspect existing implementation
↓
create implementation plan
↓
implement only current scope
↓
verify
↓
update docs
```

Do not bundle unrelated refactors into a scoped task. If you find a real problem
outside the current scope, add a backlog item instead of fixing it inline.

---

## 3. Before writing code

Always present a short plan first:

```text
Scope
Files expected to change
Implementation approach
Risks
Verification
```

Do not start structural changes before understanding the SPEC that covers them.
If no SPEC covers the change, write or extend the SPEC first.

---

## 4. After writing code

Before calling a task done:

```text
run relevant verification
inspect diff
check non-invasive guarantees
update CHANGELOG
update STATUS when appropriate
update BACKLOG if task state changed
```

**Verification is not optional, and this project can always run it.** The
development workstation is Windows with no Python interpreter, but Docker
Desktop is available: run the tests, the CLI and the full install/uninstall
lifecycle in a disposable Linux container. The exact commands are in
[DEVELOPMENT.md](docs/DEVELOPMENT.md) under "Verifying from a Windows
workstation". Never report work as verified on the strength of a code review
alone, and never run `install.sh`, `enable` or `uninstall` against the host.

Finish with:

```text
Ready for review
```

and an objective summary: what changed, what was verified, what was left out.

Do not report completion for work that is partially done. Say what is missing.

---

## 5. Database rule

aaDoctor must not use a database in the MVP.

Do not add SQLite for convenience. Simple state stays in local files under
`/var/lib/aadoctor/`. See [ADR-003](docs/adr/ADR-003-filesystem-state-without-database.md).

---

## 6. Dependency rule

Prefer the Python Standard Library.

Before adding any external dependency:

1. explain why stdlib does not solve it adequately;
2. demonstrate a concrete benefit;
3. evaluate the installation impact on a plain aaPanel server;
4. document the decision in an ADR.

A dependency that saves a few lines but adds `pip` to the install path is not
worth it. See [ADR-002](docs/adr/ADR-002-python-standard-library-first.md).

---

## 7. aaPanel rule

Never write inside:

```text
/www/server/
/www/wwwroot/
/www/wwwlogs/
```

Read only.

Never run any of these against those paths:

```text
chmod
chown
setfacl
rm
mv
truncate
```

This is not a style preference. It is the guarantee the project is built on.
See [ADR-001](docs/adr/ADR-001-non-invasive-read-only-architecture.md).

---

## 8. Service rule

aaDoctor may control only its own service:

```text
aadoctor.service
```

Never automatically:

```text
restart nginx
reload nginx
restart php-fpm
restart mysql
```

Reporting that a service looks unhealthy is fine. Acting on it is not.

---

## 9. Log rule

Never read whole logs repeatedly.

Track each file by:

```text
path
inode
offset
```

Support log rotation and truncation. On first sight of a file, start at the end
of the file, not at byte zero. See [SPEC-003](docs/specs/SPEC-003-incremental-log-monitoring.md).

---

## 10. AI rule

AI is optional.

The project must work 100% without AI. `enabled = false` is the default.

AI receives structured incidents only. Never send raw logs automatically.
AI explains deterministic findings; it never creates them, never overrides
confidence, and never triggers remediation.
See [SPEC-009](docs/specs/SPEC-009-ai-explainer.md) and
[ADR-006](docs/adr/ADR-006-deterministic-engine-before-ai.md).

---

## 11. Anti-overengineering rule

Do not introduce any of the following without a proven need and a specific ADR:

```text
repository pattern
service container
dependency injection framework
event bus
CQRS
microservices
ORM
plugin framework
distributed workers
message queues
Redis
Docker requirement
```

aaDoctor is a small server tool. A function in a module beats a layer of
indirection. Abstractions are added when a second real case appears, not in
anticipation of one.

---

## 12. Language

Code, identifiers and commit messages: English.

Documentation and CLI output: English or PT-BR as the project evolves; keep a
single document internally consistent.

Comments only when they add information the code does not already carry.

---

## 13. Document responsibilities

Do not duplicate content across documents.

| Document | Owns |
|---|---|
| [README.md](README.md) | vision, scope, limits, guarantees |
| [specs/](docs/specs/) | behavior to implement |
| [adr/](docs/adr/) | decisions and their consequences |
| [BACKLOG.md](docs/BACKLOG.md) | work items and their state |
| [STATUS.md](docs/STATUS.md) | where the project is right now |
| [CHANGELOG.md](CHANGELOG.md) | what actually changed |
| /CLAUDE.md (repository root) | how agents work here |
