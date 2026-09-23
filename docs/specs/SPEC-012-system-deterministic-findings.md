# SPEC-012 — System Deterministic Findings

Status: Draft

Related: [README.md](../../README.md) §21, §32–§35, §71 ·
[ADR-006](../adr/ADR-006-deterministic-engine-before-ai.md) ·
Backlog: AAD-090 … AAD-097

---

## Problem

[SPEC-010](SPEC-010-system-resource-monitoring.md) and
[SPEC-011](SPEC-011-process-attribution.md) produce numbers. A number is not an
answer. `MemAvailable: 74 MB` and `swap out: 1.0 MB/s` mean "this machine is
thrashing" to someone who already knows what those figures imply, and mean
nothing to everyone else — which is precisely the audience aaDoctor was built
for (README §1).

And there is a worse failure available. Today, when the logs explain nothing,
`diagnose` says:

```text
No clear log-based cause identified.
```

Once system evidence exists, continuing to print that sentence while
`MemAvailable` sits at 3% would be an outright false negative: the tool would be
holding the answer and reporting that it has none.

## Goal

Turn the measurements of SPEC-010 and SPEC-011 into named, evidence-carrying
findings on the **same scale and through the same machinery** as
[SPEC-007](SPEC-007-deterministic-rules.md), and extend the diagnosis so that it
distinguishes an HTTP-associated degradation from a system-resource one — and
says so when it is both, or neither.

**Every rule must be explainable in one sentence and reproducible from a
fixture.** Unchanged from SPEC-007, and this spec adds no exception to it.

## Non-goals

- A second rules engine. This spec adds rules to the existing registry; it does
  not build a parallel one with its own scoring, its own levels or its own path
  into the report.
- Remediation. The output may say memory is exhausted. It may never say to
  reduce `pm.max_children`, add RAM, restart PHP or disable swap — see
  **Safety constraints**, and README §90.
- Tuning advice, capacity planning or sizing recommendations.
- Anomaly detection, baselines or learned normality (README §70).
- A large catalogue. Few good rules beat many weak ones (README §32).
- Deciding *which site* caused a system-level problem. That correlation does not
  exist in the data — see **Known limitations**.

## Current context

The input is the incident record, which after SPEC-010 and SPEC-011 carries
`traffic`, `resources` and `processes`. Nothing else: no file is reopened, no
`/proc` is read at diagnosis time, so a diagnosis remains computable long after
the machine has recovered and remains byte-identical across replays
(SPEC-007's contract, unchanged).

The diagnosis is still **computed on demand and never written back** to the
incident (SPEC-007, Persistence impact).

---

## Functional requirements

### Inputs and data sources

```text
incident.resources.start / .peak / .end     SPEC-010
incident.processes.start / .peak            SPEC-011
incident.system                             SPEC-006, load and cpu_count
incident.traffic                            SPEC-005, for the correlation only
php_fpm facts                               SPEC-013, when available
kernel events                               SPEC-014, when available
```

Every one of them is optional. A missing input produces a `not_evaluable`
entry with a reason, never a finding and never a silent absence — the rule
SPEC-007 established, applied here without change.

### Findings in scope

```text
MEMORY_PRESSURE
SWAP_PRESSURE
SWAP_THRASHING
CPU_SATURATION
IO_PRESSURE
DISK_SPACE_PRESSURE
INODE_PRESSURE
PROCESS_CPU_DOMINATING
PROCESS_MEMORY_DOMINATING
OOM_EVENT
```

and, when [SPEC-013](SPEC-013-php-fpm-pressure-and-pool-discovery.md) supplies
its facts:

```text
PHP_FPM_POOL_SATURATION
PHP_FPM_MAX_CHILDREN_REACHED
PHP_FPM_PROCESS_PRESSURE
```

**The PHP-FPM findings are defined here, not in SPEC-013**, and that boundary is
deliberate. SPEC-013 is a discovery spec: it establishes what pools exist and
what their configured limits are, exactly as
[SPEC-002](SPEC-002-aapanel-discovery.md) establishes what sites exist. A finding
defined outside this registry would need its own scoring function, its own level
scale and its own route into the correlation stage — three duplications to avoid
one file boundary. The division is the same one the project already uses:
**discovery produces facts, one rules module reads them.**

Prefixes are meaningful. `PROCESS_*` findings come from SPEC-011, `PHP_FPM_*`
from SPEC-013 facts, and neither can be confused with SPEC-007's `PHP_ERROR_*`,
which comes from an Nginx error log and is a different thing entirely.

### Rule contract

Identical to SPEC-007: every rule declares `inputs`, `condition`, `evidence`,
`confidence` and `false positives`; reads facts only; performs no I/O; never
reads another rule's result; and returns a finding, a `not_evaluable` reason, or
nothing.

### Thresholds

A new `[system_rules]` configuration section, separate from `[rules]`.

Separate, rather than folded in, because the two families are read by a human
under stress at three in the morning: `[rules]` holds traffic shares between 0
and 1, `[system_rules]` holds byte rates, percentages and counts. Mixing them
makes both harder to read, and they will also mature at different speeds —
`[rules]` has one night of field data, `[system_rules]` has none.

**Every value below is a starting point, not a tuned value.** Only one has any
field grounding at all, and it is marked.

```text
mem_available_ratio_low          0.10      MEMORY_PRESSURE fires below this
mem_available_ratio_ceiling      0.03      as bad as this measure expresses
memory_psi_some_avg60            10.0      percent, when PSI exists
memory_psi_ceiling               40.0

swap_used_ratio                  0.25      SWAP_PRESSURE, context only
swap_out_bytes_per_sec           524288    512 KB/s  <-- see note
swap_out_ceiling_bytes_per_sec   8388608   8 MB/s

cpu_busy_ratio                   0.85      non-idle, excluding iowait
cpu_busy_ceiling                 0.98
cpu_steal_ratio_note             0.10      evidence inside CPU_SATURATION

iowait_ratio                     0.25
iowait_ceiling                   0.60
io_psi_some_avg60                20.0
io_psi_ceiling                   60.0

disk_used_ratio                  0.90
disk_used_ceiling                0.99
inode_used_ratio                 0.90
inode_used_ceiling               0.99

process_cpu_share                0.50      of total CPU capacity
process_cpu_ceiling              1.50
process_memory_share             0.40      of MemTotal
process_memory_ceiling           0.70

php_fpm_children_ratio           0.90      of pm.max_children
php_fpm_children_ceiling         1.00
```

The note on `swap_out_bytes_per_sec`: the production server that motivated this
work sustained roughly 1 MB/s of swap-out for five days while serving 2.9
requests a second. A floor of 512 KB/s fires there and does not fire on a server
that pages a little during a nightly backup. It is the only number in this table
derived from an observation rather than from judgement, and it should be the
last one anyone changes without new evidence.

### The scoring scale is SPEC-007's, unchanged

`strength(observed, threshold, ceiling)` and `volume_factor(count, minimum)` in
`rules/__init__.py` are reused as they are. No second scale is defined, no
second set of level boundaries, and nothing multiplies two confidences together
(SPEC-007, Confidence).

Several metrics here are **inverted** — lower is worse. They are mapped onto the
same function without modifying it, by scoring the shortfall:

```text
strength(observed = threshold - actual,
         threshold = 0,
         ceiling = threshold - ceiling_value)
```

At exactly the threshold the shortfall is 0 and the function returns its
at-threshold value; at the ceiling it returns its ceiling value. A metric where
lower is worse therefore needs no special case and no second function, which is
worth stating because writing one would have been the obvious thing to do.

---

### MEMORY_PRESSURE

```text
inputs      resources.peak.memory.available_ratio,
            resources.peak.pressure.memory_some.avg60
condition   available_ratio <= mem_available_ratio_low
            OR memory_psi_some_avg60 >= memory_psi_some_avg60
evidence    available and total memory, the ratio, the threshold,
            PSI when present, swap used, cached and buffers
confidence  strength over whichever axis is proportionally stronger;
            raised one step when both axes agree
```

Either axis is sufficient, because **PSI is absent on a large part of the target
fleet** (SPEC-010, Known limitations) and a rule that required it would be dead
on every CentOS 7 aaPanel server — which is most of them.

Cached and buffered memory appear in the evidence because they are the first
thing a reader will ask about: a machine with 74 MB available and 900 MB cached
is a different machine from one with 74 MB available and nothing reclaimable.

False positives: a machine that is deliberately run close to full and is
perfectly healthy; a large page cache on a kernel without `MemAvailable`, where
the metric is unavailable rather than wrong — the rule reports
`mem_available_unavailable` and does not guess.

---

### SWAP_PRESSURE

```text
inputs      resources.peak.swap.used_ratio, swap.total_bytes
condition   swap_total > 0 AND used_ratio >= swap_used_ratio
evidence    swap used and total, the ratio, the threshold
confidence  capped at LOW, always
```

**A context finding, capped at LOW, and it never anchors a diagnosis.** Swap
being *used* is normal — Linux swaps out idle pages by design, and a server with
600 MB of swap in use and no paging activity is fine. What matters is movement,
which is the next rule. This finding exists so the number appears in the
evidence, not so it can win an argument.

The cap is the same device SPEC-007 applies to `TRAFFIC_SPIKE`, for the same
reason: a signal worth showing and not worth concluding from.

False positives: essentially all of them. That is why it is capped.

---

### SWAP_THRASHING

```text
inputs      resources.peak.swap.out_bytes_per_sec,
            resources.peak.swap.in_bytes_per_sec,
            resources.peak.swap.major_faults_per_sec,
            resources.peak.memory.available_ratio
condition   out_bytes_per_sec >= swap_out_bytes_per_sec
            OR in_bytes_per_sec >= swap_out_bytes_per_sec
evidence    both rates, major fault rate, the threshold, swap used,
            memory available, memory PSI when present
confidence  strength over the higher of the two rates
```

This is the finding the production incident needed and the one most likely to be
right. Sustained paging in *both* directions is the signature of a machine
whose working set does not fit: pages are evicted and immediately faulted back
in, and the CPU spends its time waiting rather than working — which is why the
load can reach 43 per core while the CPU is largely idle.

The rate is required. Swap *usage* alone is SWAP_PRESSURE and means much less.

`not_evaluable` with reason `single_sample_no_rate` when the incident holds only
one resource sample, and `counter_reset` when the interval was discarded
(SPEC-010). Neither is reported as "no thrashing".

False positives: a deliberate large batch job paging through more data than
fits; a machine recovering from a spike, where a brief burst of paging is the
cure rather than the disease.

---

### CPU_SATURATION

```text
inputs      resources.peak.cpu.{user,system,idle,steal}_pct,
            resources.peak.pressure.cpu_some.avg60, system.load_per_cpu
condition   (user_pct + system_pct) >= cpu_busy_ratio
            OR cpu_psi_some_avg60 >= its threshold
evidence    the CPU breakdown, load per core, CPU count, PSI when present,
            steal separately when it is above cpu_steal_ratio_note
confidence  strength over the busy ratio
```

**Steal is reported separately and prominently.** On a VPS, steal above 10%
means the host is not giving this guest the CPU it was sold, and the evidence
says so — a fact the administrator can act on with their provider and can find
nowhere else in this tool. It does not become its own finding, because it is a
property of the same measurement.

High load with *low* CPU busy is the interesting negative: it is stated in the
evidence rather than left for the reader to infer, because it is the single
strongest hint that the cause is memory or I/O. The first production server was
exactly this shape.

False positives: a legitimate CPU-bound workload; a video encode; a build. CPU
saturation is a description of the machine, not an accusation.

---

### IO_PRESSURE

```text
inputs      resources.peak.cpu.iowait_pct,
            resources.peak.pressure.io_some.avg60
condition   iowait_pct >= iowait_ratio OR io_psi_some_avg60 >= its threshold
evidence    iowait share, I/O PSI when present, load per core,
            the top I/O processes from SPEC-011 when available
confidence  strength over whichever axis is proportionally stronger
```

Note the interaction that will bite: **swap traffic is I/O**, so a thrashing
machine usually fires this rule too. The correlation stage below handles it by
reporting memory as the primary resource when both fire and swap rates are high,
with I/O pressure as a consequence rather than a second cause. Reporting two
independent problems where there is one would be worse than reporting neither.

False positives: a backup, a database dump, a large file copy — all real I/O,
none of them a fault.

---

### DISK_SPACE_PRESSURE

```text
inputs      resources.peak.filesystems[]
condition   any filesystem used_ratio >= disk_used_ratio
evidence    mount, used and total bytes, the ratio, the threshold
confidence  strength over the highest used ratio
```

Cheap, unambiguous and occasionally the whole answer: a full `/www` makes every
site fail in ways that look like a hundred other things. Reported per mount, so
a full `/boot` is not confused with a full `/`.

False positives: a deliberately full archive volume that nothing writes to.

---

### INODE_PRESSURE

```text
inputs      resources.peak.filesystems[]
condition   any filesystem inodes_used_ratio >= inode_used_ratio
evidence    mount, inodes used and total, the ratio, the threshold
confidence  strength over the highest used ratio
```

Separate from disk space because the symptom is identical and the cause is not,
and because the administrator who has not met it before will never find it: a
filesystem with 60% of its space free and no inodes left reports "no space left
on device" and looks like a lie. Sessions, cache files and mail queues are the
usual culprits on a shared host.

---

### PROCESS_CPU_DOMINATING

```text
inputs      processes.peak.families[], system.cpu_count
condition   family.cpu_pct / (100 x cpu_count) >= process_cpu_share
evidence    family, process count, CPU percentage, share of capacity,
            the largest individual processes in that family
confidence  strength over the share
```

Reported as a **family**, not a pid: seventeen PHP-FPM workers at 20% each are
one fact. The individual processes appear as supporting evidence.

**Requires an anchor** — see **Correlation** below. A family using half the CPU
on a machine that is not saturated is how the machine is configured, not a
problem, and naming it would manufacture a culprit out of normal operation.

False positives: the application the server exists to run, doing its job.

---

### PROCESS_MEMORY_DOMINATING

```text
inputs      processes.peak.families[], resources.peak.memory.total_bytes
condition   family.rss_bytes / memory_total >= process_memory_share
evidence    family, process count, resident bytes, share of total memory,
            average resident size per process, the largest processes
confidence  strength over the share
```

The evidence carries **resident size per process** alongside the total, because
that is the number that tells an administrator whether the family is large or
merely numerous — and those have different answers.

The evidence must state that **resident size counts shared pages more than
once**, so a family total overstates real consumption (SPEC-011, Known
limitations). A number presented without that caveat would be quoted back as if
it were exact.

Also requires an anchor.

False positives: a database cache sized as intended; PHP-FPM configured for a
machine twice this size, which is a configuration observation and still not
something aaDoctor may recommend changing.

---

### OOM_EVENT

```text
inputs      resources.*.oom_kill_delta (SPEC-010),
            kernel events of kind oom_kill (SPEC-014) when available
condition   oom_kill_delta >= 1 OR any OOM kernel event in the window
evidence    the count, the timestamp, and — only from SPEC-014 — the victim
            process name, its pid and the memory context the kernel printed
confidence  VERY_HIGH, by construction
```

**Not a matter of degree.** The kernel killed something to stay alive; there is
no weak version of that, and scaling this finding's confidence with a count
would be scoring a fact that is already certain.

Two sources at two levels of detail, and the difference is stated in the output:
`/proc/vmstat`'s counter proves an OOM *happened* and names nothing;
[SPEC-014](SPEC-014-host-kernel-events.md) names the victim when the kernel log
is readable. The finding fires on either, and says which it had.

`not_evaluable` with reason `oom_counter_unavailable` on a kernel before 4.13
with no readable kernel log. An OOM that cannot be observed must never be
reported as an OOM that did not happen.

---

### PHP_FPM_POOL_SATURATION · PHP_FPM_MAX_CHILDREN_REACHED · PHP_FPM_PROCESS_PRESSURE

Defined here, fed by [SPEC-013](SPEC-013-php-fpm-pressure-and-pool-discovery.md).

```text
PHP_FPM_POOL_SATURATION
  condition   workers_running / pm.max_children >= php_fpm_children_ratio
  evidence    pool, PHP version, workers running, pm.max_children, pm mode,
              the sites mapped to that pool when the mapping is deterministic
  confidence  strength over the ratio

PHP_FPM_MAX_CHILDREN_REACHED
  condition   a "server reached pm.max_children" line in the PHP-FPM log
              within the window, OR workers_running >= pm.max_children
  evidence    the count of such lines, the pool, the limit, the timestamp
  confidence  HIGH from the log line; MEDIUM from the worker count alone

PHP_FPM_PROCESS_PRESSURE
  condition   the php-fpm family's memory share is high AND
              pm.max_children x observed average RSS > MemTotal
  evidence    max_children, average resident size, the implied ceiling,
              total memory, the arithmetic spelled out
  confidence  capped at MEDIUM — it is a projection, not an observation
```

The third is the one that would have explained the production server, and it is
the one that most needs its cap. `pm.max_children x average RSS > MemTotal`
says the pool is *configured* to be able to exhaust the machine. That is a real
and useful observation and it is **not** a measurement of what happened, so it
is capped, worded as a configured ceiling, and never phrased as advice to change
the setting.

All three are `not_evaluable` with reason `php_fpm_facts_unavailable` when
SPEC-013 could not read the pool configuration.

---

### Evidence-first, unchanged

No finding is reported without the numbers behind it (README §35). Never:

```text
Servidor está sem memória.
```

Always:

```text
SWAP_THRASHING
- swap out 1.0 MB/s, in 0.9 MB/s, sustained across the window
- memory available 74 MB of 1.9 GB (3.8%), threshold 10%
- major faults 240/s
- memory PSI some avg60 unavailable (kernel 3.10)
```

---

## Correlation

This is where the spec earns its place, and where it is easiest to do damage.

### Two domains, two scores, never summed

HTTP findings name a **site**. System findings name a **resource**, and
sometimes a **process family**. These are not the same kind of answer, and their
scores are not commensurable.

**The two scores are computed independently and never added.** Summing them
would produce a single number that ranks a site against a resource, which is not
a comparison that means anything. SPEC-007's site scoring is untouched; this
spec adds a parallel system score over its own weights:

```text
OOM_EVENT                   4      IO_PRESSURE                2
SWAP_THRASHING              3      DISK_SPACE_PRESSURE        2
MEMORY_PRESSURE             3      INODE_PRESSURE             2
CPU_SATURATION              2      PHP_FPM_POOL_SATURATION    2
PROCESS_MEMORY_DOMINATING   2      PHP_FPM_MAX_CHILDREN...    2
PROCESS_CPU_DOMINATING      2      PHP_FPM_PROCESS_PRESSURE   1
                                   SWAP_PRESSURE              0
```

scaled by each finding's own level, using SPEC-007's existing multipliers
(`LOW 0.50 / MEDIUM 0.75 / HIGH 1.00 / VERY_HIGH 1.25`), and mapped to a level
through SPEC-007's existing bands. `SWAP_PRESSURE` weighs zero: it is evidence,
never an argument.

These are declared weights, not measurements — the same disclaimer SPEC-007
carries, and it applies here with more force, because none of these has been
near a real server yet.

### The system anchor rule

SPEC-007 requires an anchor before a *site* is named. The same discipline
applies here, for the same reason:

> **A process family is named only when a host-level finding for the same
> resource also fired.**

`PROCESS_MEMORY_DOMINATING` without `MEMORY_PRESSURE` or `SWAP_THRASHING` is a
description of how the machine is provisioned. `PROCESS_CPU_DOMINATING` without
`CPU_SATURATION` is a busy process on an idle machine. Without the anchor the
finding is still listed, and it names no culprit.

This is the mistake SPEC-007 actually made and caught in a container run, and it
is even easier to make here: on any web server, PHP-FPM is always the largest
consumer of something.

### Primary domain

```text
HTTP conclusive?   System conclusive?   Primary
no                 no                   inconclusive — and say which domains
                                        were actually observable
no                 yes                  SYSTEM
yes                no                   HTTP
yes                yes                  BOTH — both reported, neither suppressed
```

In the `BOTH` case the report presents both and **explicitly declines to order
them**, because aaDoctor cannot tell cause from effect here and must not imply
that it can. A site's traffic can exhaust the memory; exhausted memory can make
every site slow, including that one. Both produce the same evidence. Saying so
is the honest answer, and the report says it in those words.

Within the system domain, when both `MEMORY_PRESSURE`/`SWAP_THRASHING` and
`IO_PRESSURE` fire and swap rates are above their threshold, memory is reported
as the primary resource and I/O pressure is presented as its consequence. This
is the one causal ordering this spec asserts, and it is asserted because swap
traffic is I/O by definition rather than by inference.

### The inconclusive negatives

SPEC-007 defines three distinct inconclusive wordings and warns that
distinguishing them is what stops silence being misread. This spec **adds a
fourth and changes the boundaries of the existing ones**, which is a change to
[SPEC-008](SPEC-008-cli-reporting.md)'s output contract and is recorded there
too:

| Situation | What the report says |
|---|---|
| Logs show nothing, system evidence strong | The system diagnosis. **Never** "no clear cause" |
| Logs show nothing, system observable and quiet | No cause in the logs **and** none in the host resources observed — naming what was checked |
| Logs show nothing, system **not** observable | No cause in the logs; host resources could not be observed, and why |
| Too little traffic to judge, system quiet | Unchanged from SPEC-007 |

The third row is the one that matters most and is the easiest to get wrong. "We
looked at memory, CPU, I/O and disk and they were fine" and "we could not look"
are opposite statements, and a reader who cannot tell them apart will act on the
wrong one. This is the same principle as `NOT EVALUABLE`, applied to the
summary sentence rather than to a rule.

The closing paragraph of SPEC-008's inconclusive output — which today names I/O,
a backup, a remote database and a process owned by no site as things that could
never appear in a web log — must be **narrowed** once this spec lands. Three of
those four become observable. Leaving the sentence as it is would have the tool
listing its own capabilities as blind spots.

---

## Technical behavior

- Each rule is a pure function of the facts; identical inputs give identical
  findings, evidence and confidence across runs and across the ordering of the
  record (SPEC-007's determinism contract).
- Rules are registered in the same table-driven registry; adding one is one
  function and one entry.
- Evaluation order does not affect results.
- No I/O, no network, no subprocess, no clock read inside a rule.
- Standard library only.

## Performance constraints

Diagnosis is an interactive command, not a loop. The whole evaluation reads a
single JSON file already in memory and performs arithmetic over at most a few
hundred values.

- Budget: the complete rule set, HTTP and system, in **under 50 ms** for a
  typical incident.
- No rule may iterate over anything unbounded — every input is already capped by
  SPEC-005, SPEC-010 or SPEC-011.

## Data structures

Extends SPEC-007's diagnosis object; the existing fields keep their meaning.

```text
diagnosis:
  ruleset_version
  conclusive, confidence, level, score          HTTP domain, unchanged
  primary_site, primary_path, associated_ip     unchanged
  error_site, traffic_site                      unchanged

  primary_domain        http | system | both | none          NEW
  system:                                                    NEW
    conclusive, confidence, level, score
    primary_resource    memory | cpu | io | disk | inode | none
    primary_family      a process family, only with an anchor
    consequence_of      set when one resource finding is presented as
                        the consequence of another
  findings[]            both domains, each carrying its domain
  not_evaluable[]       both domains
  evidence[]            both domains
  data_quality          gains the coverage states of SPEC-015
  thresholds            gains the [system_rules] values actually used
```

`ruleset_version` **increments** when this spec is implemented. A diagnosis is
computed on demand, so an old incident is read with today's rules; which rules
those were has to be visible, and a reader comparing two reports of the same
incident needs to know why they differ.

## Incident impact

**None.** Nothing is written back. A diagnosis remains computed on demand
(SPEC-007, Persistence impact) and this spec does not weaken that — if anything
it strengthens the argument for it, since these thresholds are the least
validated in the project and will certainly move.

## Edge cases

| Case | Behavior |
|---|---|
| Incident predates SPEC-010 | No `resources`; every system rule reports `no_resource_samples` |
| Only one resource sample | Rate-based rules report `single_sample_no_rate`; level-based rules still evaluate |
| PSI unavailable | Rules use their non-PSI axis; the evidence says PSI was unavailable |
| No swap configured | SWAP_* report `no_swap_configured`, never "no thrashing" |
| `MemAvailable` unavailable | MEMORY_PRESSURE falls back to PSI; with neither, `not_evaluable` |
| Process scan missing | `PROCESS_*` report `process_attribution_unavailable`; no other finding is weakened by it |
| A family dominates but nothing is saturated | Finding listed, anchor absent, no family named |
| Memory and I/O both fire | Memory primary, I/O reported as consequence, both listed |
| HTTP and system both conclusive | Both reported; neither is called the cause of the other |
| System conclusive, HTTP inconclusive | The report leads with the system diagnosis and does not print "no clear cause" |
| Disk full and memory fine | DISK_SPACE_PRESSURE alone can be the whole answer |
| OOM counter unavailable and no kernel log | `oom_counter_unavailable`, never "no OOM" |
| Corrupt or hand-edited `resources` block | Read defensively; nothing is claimed from rubbish (SPEC-007's rule) |

## Safety constraints

- Rules read facts only: no file, network, subprocess or service access. The
  existing source-purity test is extended to cover these modules.
- **No output may be phrased as an instruction to change the server.** Not
  "reduce pm.max_children", not "add RAM", not "disable swap", not "restart
  PHP-FPM". Findings describe a state; the administrator decides. A test checks
  the wording, as it already does for SPEC-007.

  This constraint is under more pressure here than anywhere else in the project,
  because the system findings map so neatly onto remedies. The line is exactly
  where README §90 puts it: describing that `pm.max_children x average RSS`
  exceeds total memory is an observation; saying what to set it to is
  administration.
- No process is signalled and no service is restarted or reloaded.
- No sysctl, no swap change, no cache drop, no configuration write.
- Process names and pool labels reaching these rules are **untrusted strings**
  from outside aaDoctor. Counted, compared and printed; never used in a path, a
  shell, an executable format string or `eval`.
- Evidence is bounded by what the snapshot holds, which contains counters and
  bounded tables and never a command line (SPEC-011, Privacy).

## CLI impact

`diagnose` gains a system half. Sections, in SPEC-008's existing style, each
omitted when it has nothing to say:

```text
SYSTEM                  the resource the evidence points at
PROCESS                 the family, only when anchored
SYSTEM EVIDENCE         one line per system finding, numbers included
```

and the existing `FINDINGS`, `NOT EVALUABLE`, `DATA QUALITY` and `CONFIDENCE`
sections carry both domains, with each row marked by domain so the two are never
read as one list.

`status` may show a one-line resource summary (SPEC-010). `top` stays purely
about traffic. `show` renders the stored facts and still concludes nothing.

## Persistence impact

None. Reads the incident; writes nothing.

## Interactions with existing specs

- **[SPEC-007](SPEC-007-deterministic-rules.md)** — its rules, scale, level
  bands, anchor principle and `not_evaluable` mechanism are reused unchanged.
  Its site score is untouched. What changes is that the diagnosis is no longer
  the site score alone.
- **[SPEC-008](SPEC-008-cli-reporting.md)** — three new sections, a fourth
  inconclusive wording, and a **narrowing of the closing paragraph** that today
  lists I/O, backups and unowned processes as invisible. That paragraph becomes
  wrong when this spec lands and must be rewritten with it, not after.
- **[SPEC-010](SPEC-010-system-resource-monitoring.md)** /
  **[SPEC-011](SPEC-011-process-attribution.md)** — the fact sources. Neither
  knows this spec exists.
- **[SPEC-013](SPEC-013-php-fpm-pressure-and-pool-discovery.md)** — supplies
  pool facts; the findings built on them live here.
- **[SPEC-014](SPEC-014-host-kernel-events.md)** — supplies OOM identity and
  kernel events; `OOM_EVENT` lives here and fires without it.
- **[SPEC-015](SPEC-015-diagnostic-coverage-self-check.md)** — coverage states
  decide which *negative* statements this spec is allowed to make. It never
  raises a confidence and never invalidates a finding from another domain.

  **This is not a circular dependency, and the order is deliberate.** This spec
  is implementable first: SPEC-010's `unavailable[]` list and SPEC-011's absence
  of a scan already tell it which inputs it lacked, which is all it needs for
  its `not_evaluable` reasons. SPEC-015 then generalises that into one
  vocabulary shared by the human report and the rules, so the coverage an
  administrator reads and the coverage the engine uses are the same computation.
  Two implementations of "is this observable" would eventually disagree, and the
  disagreement would surface as a report contradicting itself — which this
  project has already shipped once.
- **[SPEC-009](SPEC-009-ai-explainer.md)** — still deferred, and this spec gives
  it more to explain and one more way to be wrong. AI continues to set no
  confidence and create no finding
  ([ADR-006](../adr/ADR-006-deterministic-engine-before-ai.md)).

## Known limitations and TBDs

- **No threshold here has been validated against anything**, except the swap
  rate, which has one observation behind it. This is a weaker position than
  SPEC-007 started from, and it should be stated in the output as well as here.
- **Cause and effect are not separable.** When a site's traffic and the memory
  pressure both appear, aaDoctor cannot say which produced which, and says so
  rather than choosing.
- **A system-level cause cannot be attributed to a site**, because PHP-FPM pools
  are shared per PHP version on a default aaPanel (SPEC-011, SPEC-013). The most
  aaDoctor can honestly say is "PHP-FPM, on PHP 7.4, across these sites".
- **A resource ceiling is not a measurement.** `PHP_FPM_PROCESS_PRESSURE`
  projects; its cap says so.
- **Single-sample incidents lose every rate-based finding**, which on a short
  incident is most of them.
- TBD: whether `critical_per_cpu` should raise the system confidence ceiling the
  way SPEC-006 allows for the HTTP side. Deferred until there is field data.

## Acceptance criteria

- [ ] Each finding fires on its dedicated fixture incident, at, just below and
      just above its threshold.
- [ ] No system finding fires on a healthy fixture.
- [ ] Every finding carries evidence sufficient to justify it without the raw
      measurements.
- [ ] An inverted metric scores correctly at its threshold and at its ceiling
      through the unmodified `strength` function.
- [ ] A missing input produces `not_evaluable` with a specific reason, never a
      finding and never silence.
- [ ] No process family is named without a host-level anchor.
- [ ] HTTP and system scores are never added; a test asserts the diagnosis of a
      fixture with both is `primary_domain = both` and names both.
- [ ] With strong system evidence, the output **never** contains the phrase "no
      clear cause identified".
- [ ] "System quiet" and "system not observable" produce different sentences —
      asserted directly.
- [ ] Replaying a fixture twice yields identical findings and confidences.
- [ ] No output recommends or performs an action on the server.
- [ ] The full rule set evaluates in under 50 ms on a large fixture incident.

## Verification

- `tests/test_system_rules.py` — each rule at its boundaries, at its ceiling,
  with its inputs missing, and on a kernel fixture with no PSI.
- `tests/test_diagnosis.py` extended — the four-cell primary-domain table, the
  anchor rule, memory-over-I/O ordering, and the wording assertions above.
- Fixtures are **incident records**, as in SPEC-007. A fixture built from
  `/proc` text would test SPEC-010 twice and this spec not at all.
- Three named scenarios, mirroring SPEC-007's structure:
  1. **Memory starvation** — flat traffic, `MemAvailable` at 3%, swap out
     1 MB/s, php-fpm holding 60% of RAM. Expected: `primary_domain = system`,
     resource memory, family php-fpm, HTTP inconclusive and **not** described as
     "no cause found".
  2. **Traffic-caused saturation** — one site dominating, CPU at 95%, php-fpm at
     70% of CPU. Expected: `primary_domain = both`, both named, and an explicit
     refusal to order them.
  3. **Quiet and blind** — flat traffic, no `resources` block at all. Expected:
     inconclusive, with the sentence naming that host resources were not
     observed and why.
- An integration run in a container reproducing scenario 1 with a real cgroup
  memory limit, end to end through the real daemon — the same standard SPEC-007
  was held to.

## Out of scope

- Remediation, tuning and sizing advice — forbidden (README §90).
- Collecting the measurements — SPEC-010, SPEC-011, SPEC-013, SPEC-014.
- Coverage reporting — [SPEC-015](SPEC-015-diagnostic-coverage-self-check.md).
- Baselines and learned normality — Parking Lot (README §70).
- Final threshold values — they require field data, and this time more of it.
