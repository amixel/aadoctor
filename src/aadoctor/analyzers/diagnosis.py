"""Correlating findings into one answer. The end of the deterministic pipeline.

```text
incident -> facts -> rules -> findings -> correlation -> diagnosis
```

A finding says "this site holds 84% of the requests". A diagnosis says which
site to look at first, and admits when there is no such site.

Two things this deliberately does not do.

**It does not invent correlations.** Aggregation holds `site -> paths` and
`site -> ips`, not `site x ip x path`. So the output can say that the suspect
site also had one dominant address, and it cannot say that the address is what
called the dominant path - that fact is not in the data, and writing it anyway
would be the most convincing kind of wrong.

**It does not present confidence as probability.** The score below is a sum of
declared weights, nothing more. Multiplying confidences together would produce
a number that looks like statistics and means nothing.

Nothing here reaches the network, an AI, a database or a log file.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from .. import rules
from ..rules import Facts, Finding, NotEvaluable, Quality, Thresholds
from ..rules.builtin import (
    HTTP_5XX_SPIKE,
    FASTCGI_ERROR,
    NOT_FOUND_FLOOD,
    ONE_IP_DOMINATING,
    ONE_SITE_DOMINATING,
    ONE_URL_DOMINATING,
    PHP_ERROR_SPIKE,
    TRAFFIC_SPIKE,
    UPSTREAM_TIMEOUT,
)

#: What each finding contributes when it points at a site.
#:
#: These are declared weights, not measurements. Traffic domination is the
#: strongest single statement the logs can make about where load came from;
#: failures weigh nearly as much, because a site failing under load is usually
#: nearer the cause than a site merely being busy; a dominant address and a
#: spike corroborate and rarely stand alone.
WEIGHTS: Dict[str, float] = {
    ONE_SITE_DOMINATING: 3.0,
    ONE_URL_DOMINATING: 2.0,
    HTTP_5XX_SPIKE: 2.0,
    UPSTREAM_TIMEOUT: 2.0,
    FASTCGI_ERROR: 2.0,
    PHP_ERROR_SPIKE: 2.0,
    ONE_IP_DOMINATING: 1.0,
    TRAFFIC_SPIKE: 1.0,
    NOT_FOUND_FLOOD: 1.0,
}

#: A finding that barely crossed its threshold must not weigh the same as one
#: that cleared it four times over. Scaling by the finding's own band keeps
#: that distinction without inventing arithmetic on top of the confidences.
LEVEL_MULTIPLIER: Dict[str, float] = {
    rules.LOW: 0.50,
    rules.MEDIUM: 0.75,
    rules.HIGH: 1.00,
    rules.VERY_HIGH: 1.25,
}

#: Score to confidence. The bands are wide on purpose: this is a judgement
#: about how much evidence agrees, and pretending to resolve it finely would
#: be false precision.
SCORE_BANDS: Tuple[Tuple[float, float], ...] = (
    (8.0, 0.92),
    (4.0, 0.85),
    (2.0, 0.70),
    (1.0, 0.45),
)

#: Findings whose subject is the error logs. Used to describe a site that is
#: failing without dominating the traffic - the case that stops aaDoctor from
#: being merely a busy-site detector.
ERROR_CODES = (HTTP_5XX_SPIKE, UPSTREAM_TIMEOUT, FASTCGI_ERROR, PHP_ERROR_SPIKE)

#: Findings whose subject is the access log.
TRAFFIC_CODES = (
    ONE_SITE_DOMINATING,
    ONE_URL_DOMINATING,
    ONE_IP_DOMINATING,
    NOT_FOUND_FLOOD,
    TRAFFIC_SPIKE,
)

#: Findings that say something about the site's weight on the *server*, rather
#: than about its own internal shape.
#:
#: A site may be nine tenths one endpoint and one tenth everything else, and
#: on a quiet site that is simply what the site is. ONE_URL_DOMINATING and
#: ONE_IP_DOMINATING are shares of the site, so on their own they cannot say a
#: site is behind a server-wide load rise - one of these has to be there
#: first. Without it the findings are still reported and the diagnosis stays
#: inconclusive, which is the honest reading and a real false positive
#: avoided: ten evenly loaded sites, one of which serves a single endpoint,
#: must not produce a suspect.
ANCHOR_CODES = ERROR_CODES + (ONE_SITE_DOMINATING, NOT_FOUND_FLOOD)


def points_for(finding: Finding) -> float:
    """What this finding contributes to its site's score."""
    return WEIGHTS.get(finding.code, 0.0) * LEVEL_MULTIPLIER.get(finding.level, 0.5)


def confidence_for(score: float) -> float:
    for floor, confidence in SCORE_BANDS:
        if score >= floor:
            return confidence
    return 0.0


@dataclass
class Diagnosis:
    """What the rules, taken together, support saying."""

    incident_id: str = ""
    status: str = ""
    severity: str = ""
    ruleset_version: int = rules.RULESET_VERSION

    conclusive: bool = False
    confidence: float = 0.0
    score: float = 0.0

    primary_site: Optional[str] = None
    primary_path: Optional[str] = None
    associated_ip: Optional[str] = None
    #: A different site concentrating the failures, and a different site
    #: concentrating the traffic. Present only when the evidence genuinely
    #: points in two directions (SPEC-007 conflict case), and reported instead
    #: of being flattened away - the busiest site and the failing site are not
    #: always the same site, and the difference is usually the interesting part.
    error_site: Optional[str] = None
    traffic_site: Optional[str] = None

    findings: List[Finding] = field(default_factory=list)
    not_evaluable: List[NotEvaluable] = field(default_factory=list)
    scores: Dict[str, float] = field(default_factory=dict)
    caps: List[str] = field(default_factory=list)

    summary: str = ""
    facts: Optional[Facts] = None
    quality: Optional[Quality] = None
    thresholds: Dict[str, Any] = field(default_factory=dict)

    @property
    def level(self) -> str:
        return rules.level(self.confidence)

    def evidence(self) -> List[str]:
        """One readable line per finding, strongest first."""
        return [finding.description for finding in self.findings if finding.description]

    def as_dict(self) -> dict:
        facts = self.facts
        return {
            "ruleset_version": self.ruleset_version,
            "incident": {
                "id": self.incident_id,
                "status": self.status,
                "severity": self.severity,
                "cpu_count": facts.cpu_count if facts else None,
                "peak_load": facts.peak_load if facts else None,
                "peak_load_per_cpu": facts.peak_load_per_cpu if facts else None,
                "started_at": facts.started_at if facts else None,
                "peak_at": facts.peak_at if facts else None,
            },
            "diagnosis": {
                "conclusive": self.conclusive,
                "summary": self.summary,
                "primary_site": self.primary_site,
                "primary_path": self.primary_path,
                "associated_ip": self.associated_ip,
                "error_site": self.error_site,
                "traffic_site": self.traffic_site,
                "score": round(self.score, 2),
                "site_scores": {
                    name: round(value, 2)
                    for name, value in sorted(self.scores.items())
                },
            },
            "confidence": {
                "value": round(self.confidence, 2),
                "level": self.level,
                "limited_by": list(self.caps),
                "note": (
                    "strength of the evidence the rules found, not a probability"
                ),
            },
            "findings": [finding.as_dict() for finding in self.findings],
            # Reported, not omitted: a rule that could not run is different
            # from a rule that ran and found nothing.
            "not_evaluable": [item.as_dict() for item in self.not_evaluable],
            "evidence": self.evidence(),
            "data_quality": self.quality.as_dict() if self.quality else {},
            "window": {
                "source": facts.source if facts else "",
                "window_seconds": facts.window_seconds if facts else 0,
                "coverage_seconds": round(facts.coverage_seconds, 1) if facts else 0.0,
                "total_requests": facts.total_requests if facts else 0,
            },
            "thresholds": self.thresholds,
        }


def diagnose(incident: Any, thresholds: Optional[Thresholds] = None) -> Diagnosis:
    """Read one incident and say what its evidence supports.

    Computed on demand, never written back: the incident file records what was
    measured, and keeping conclusions out of it means an old incident can be
    re-read by a later ruleset instead of being frozen with today's opinion.
    """
    limits = thresholds or Thresholds()
    facts = Facts.from_incident(incident)
    findings, blocked, quality = rules.evaluate(facts, limits)

    diagnosis = Diagnosis(
        incident_id=facts.incident_id,
        status=facts.status,
        severity=facts.severity,
        findings=findings,
        not_evaluable=blocked,
        facts=facts,
        quality=quality,
        thresholds=limits.as_dict(),
    )

    scores = _score_sites(findings)
    diagnosis.scores = scores

    winner = _winner(scores)
    if winner is None:
        diagnosis.summary = _inconclusive_summary(facts, findings, blocked, limits, None)
        return diagnosis

    # Net of everything pointing elsewhere. Disagreement lowers confidence by
    # construction, instead of through a special case for "conflict".
    net = scores[winner] - sum(
        value for name, value in scores.items() if name != winner
    )
    confidence = confidence_for(net)

    # Only the coverage cap applies here. The other quality limits already did
    # their work inside the findings they belong to, and applying them again
    # would punish the whole diagnosis for a problem with one input.
    cap, reasons = quality.cap_for(())
    if confidence > cap:
        confidence = cap
        diagnosis.caps.extend(reasons)

    diagnosis.score = net
    diagnosis.confidence = confidence
    diagnosis.conclusive = (
        net >= SCORE_BANDS[-1][0]
        and confidence > 0.0
        and _anchored(findings, winner)
    )
    diagnosis.primary_site = winner if diagnosis.conclusive else None

    if diagnosis.conclusive:
        diagnosis.primary_path = _value(findings, ONE_URL_DOMINATING, winner, "path")
        diagnosis.associated_ip = _value(findings, ONE_IP_DOMINATING, winner, "ip")
        diagnosis.error_site = _other_site(findings, winner, ERROR_CODES)
        diagnosis.traffic_site = _other_site(findings, winner, TRAFFIC_CODES)
        diagnosis.summary = _summary(diagnosis)
    else:
        diagnosis.confidence = 0.0
        diagnosis.summary = _inconclusive_summary(facts, findings, blocked, limits, winner)

    return diagnosis


def _anchored(findings: List[Finding], site: str) -> bool:
    """Whether anything ties this site to the server, not just to itself."""
    return any(
        finding.site == site and finding.code in ANCHOR_CODES for finding in findings
    )


# --- correlation ----------------------------------------------------------


def _score_sites(findings: List[Finding]) -> Dict[str, float]:
    """Sum each site's supporting evidence, weighted by how strong it is."""
    scores: Dict[str, float] = {}
    for finding in findings:
        if not finding.site:
            continue
        scores[finding.site] = scores.get(finding.site, 0.0) + points_for(finding)
    return scores


def _winner(scores: Dict[str, float]) -> Optional[str]:
    if not scores:
        return None
    ordered = sorted(scores.items(), key=lambda item: (-item[1], item[0]))
    name, value = ordered[0]
    return name if value > 0 else None


def _value(findings: List[Finding], code: str, site: str, attribute: str) -> Optional[str]:
    for finding in findings:
        if finding.code == code and finding.site == site:
            return getattr(finding, attribute)
    return None


def _other_site(
    findings: List[Finding], winner: str, subset: Tuple[str, ...]
) -> Optional[str]:
    """The strongest site these findings point at, other than the winner.

    Used for both halves of a disagreement. Whichever side wins, the other is
    still named rather than dropped: "the traffic is here, the failures are
    there" is a more useful answer than either half alone.
    """
    scores: Dict[str, float] = {}
    for finding in findings:
        if finding.code in subset and finding.site and finding.site != winner:
            scores[finding.site] = scores.get(finding.site, 0.0) + points_for(finding)
    return _winner(scores)


# --- wording --------------------------------------------------------------


def _summary(diagnosis: Diagnosis) -> str:
    """Say what the evidence supports, in one sentence, without overclaiming.

    "Evidence points to" rather than "caused by". aaDoctor reads web server
    logs: a process outside every site, a backup, an I/O stall or a remote
    database will never appear in them, so even a very high confidence is a
    statement about the logs and not about the machine.
    """
    parts = ["Evidence points to %s" % diagnosis.primary_site]
    if diagnosis.primary_path:
        parts.append("with %s as the primary suspect" % diagnosis.primary_path)
    if diagnosis.error_site:
        parts.append("while %s concentrates the failures" % diagnosis.error_site)
    if diagnosis.traffic_site:
        parts.append("while %s concentrates the traffic" % diagnosis.traffic_site)
    return ", ".join(parts) + "."


#: The reason a rule gives when the window simply did not hold enough traffic
#: for a share to mean anything.
STARVED = "below_minimum_volume"


def _inconclusive_summary(
    facts: Facts,
    findings: List[Finding],
    blocked: List[NotEvaluable],
    limits: Thresholds,
    candidate: Optional[str] = None,
) -> str:
    """The honest answer when the logs do not name anything.

    This is a useful result, not a failure. The load rise was measured and is
    real; what the logs do not show is a dominant source for it. Inventing a
    suspect to avoid saying so would make every other answer worth less.

    But there are two different negatives here, and saying the wrong one is
    its own kind of invention. "We looked at a full window and nothing stood
    out" is a finding. "We had nine requests, so nothing could stand out" is
    not a finding at all, and must not be phrased as one - it happens on every
    incident that opens shortly after the daemon starts.
    """
    if not facts.has_traffic:
        return (
            "No traffic was recorded with this incident, so the logs cannot "
            "say anything about its cause."
        )

    base = "No clear log-based cause identified."

    if any(item.reason == STARVED for item in blocked) and not findings:
        return (
            "Not enough traffic to judge: the window holds %s requests over "
            "%.0f seconds, below the %s needed before any share means "
            "anything. The load rise is real and recorded. Whether the web "
            "traffic explains it is unknown - which is not the same as it "
            "being ruled out."
            % (f"{facts.total_requests:,}", facts.coverage_seconds, f"{limits.min_volume:,}")
        )

    if candidate and not _anchored(findings, candidate):
        return (
            "%s %s shows concentration within its own traffic, but it is not a "
            "large enough share of the server, and nothing failed on it." % (base, candidate)
        )
    if candidate:
        return "%s The findings below do not agree on a single site." % base
    return (
        "%s The load rise is recorded, but the monitored Nginx and PHP logs "
        "show no dominant site, path, address or error behind it." % base
    )


__all__ = [
    "ANCHOR_CODES",
    "Diagnosis",
    "ERROR_CODES",
    "TRAFFIC_CODES",
    "LEVEL_MULTIPLIER",
    "SCORE_BANDS",
    "WEIGHTS",
    "confidence_for",
    "diagnose",
    "points_for",
]
