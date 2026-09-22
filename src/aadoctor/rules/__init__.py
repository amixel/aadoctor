"""Deterministic rules over a frozen incident. The first time aaDoctor concludes.

Everything up to here measured. This package interprets - and the whole design
exists to keep that interpretation auditable:

* a rule is a pure function of the facts, with no I/O and no knowledge of any
  other rule's result (SPEC-007);
* a finding never exists without the numbers that produced it, including the
  threshold it was compared against;
* the same incident always yields the same findings, in the same order, with
  the same confidences.

**Confidence is not a probability.** 0.95 does not mean "95% chance this caused
the outage". It is the strength of the evidence the rules found, on a scale
this module defines and nothing else. Calling it a probability would invite
arithmetic that means nothing - multiplying two of these numbers together
produces a third number with no interpretation at all.

No network, no AI, no database, no log access: the incident file is the only
input, which is what makes an old incident re-analysable long after its logs
have rotated away.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple

from ..config import DEFAULTS
from .facts import Facts

#: Bumped when a rule's condition or scoring changes, so a diagnosis produced
#: today can be told apart from one produced by a later build. Findings are
#: computed on demand rather than stored, which means an old incident is read
#: with today's rules - and the reader has to be able to see which those were.
RULESET_VERSION = 1

LOW = "LOW"
MEDIUM = "MEDIUM"
HIGH = "HIGH"
VERY_HIGH = "VERY_HIGH"

#: Presentation bands, from README section 34 and SPEC-007.
LEVEL_FLOORS: Tuple[Tuple[float, str], ...] = (
    (0.90, VERY_HIGH),
    (0.75, HIGH),
    (0.50, MEDIUM),
    (0.00, LOW),
)

#: The two values that shape every confidence in this package: what a finding
#: is worth the moment it crosses its threshold, and what it is worth when the
#: evidence is as strong as the measure can express.
CONFIDENCE_AT_THRESHOLD = 0.60
CONFIDENCE_AT_CEILING = 0.98

#: For count-based rules the observed value is a multiple of the threshold.
#: Four times the threshold is treated as the ceiling.
RATIO_CEILING = 4.0

#: Caps, expressed as the highest confidence a damaged input may still carry.
CAP_MEDIUM = 0.74
CAP_HIGH = 0.89


def level(confidence: float) -> str:
    """Map a confidence onto the four presented bands."""
    for floor, name in LEVEL_FLOORS:
        if confidence >= floor:
            return name
    return LOW  # pragma: no cover - the table ends at 0.0


def strength(observed: float, threshold: float, ceiling: float) -> float:
    """How far past its threshold an observation is, as a confidence.

    Zero below the threshold, :data:`CONFIDENCE_AT_THRESHOLD` exactly on it,
    rising linearly to :data:`CONFIDENCE_AT_CEILING` at ``ceiling`` and no
    further. A finding that barely crosses the line is deliberately MEDIUM:
    it is true, and it is weak, and the output should say both.
    """
    if observed < threshold:
        return 0.0
    if ceiling <= threshold:
        return CONFIDENCE_AT_CEILING

    span = min(1.0, (observed - threshold) / (ceiling - threshold))
    return CONFIDENCE_AT_THRESHOLD + span * (CONFIDENCE_AT_CEILING - CONFIDENCE_AT_THRESHOLD)


def volume_factor(count: int, minimum: int) -> float:
    """Discount a share that rests on very few requests.

    0.75 at the minimum volume, reaching 1.0 at ten times it. Nine requests out
    of ten is a 90% share and means nothing; the share alone cannot express
    that, so this does.

    Only share-based rules use it. For a count-based rule the count *is* the
    observed quantity, and applying this on top would discount the same fact
    twice.
    """
    if minimum <= 0:
        return 1.0
    if count <= minimum:
        return 0.75
    ratio = min(1.0, (count - minimum) / float(9 * minimum))
    return 0.75 + 0.25 * ratio


def share_confidence(share: float, threshold: float, ceiling: float, count: int, minimum: int) -> float:
    """Confidence for a rule that fires on a proportion."""
    return round(strength(share, threshold, ceiling) * volume_factor(count, minimum), 2)


def count_confidence(count: float, threshold: float, ceiling: float = RATIO_CEILING) -> float:
    """Confidence for a rule that fires on an absolute count."""
    if threshold <= 0:
        return 0.0
    return round(strength(count / float(threshold), 1.0, ceiling), 2)


# --- thresholds -----------------------------------------------------------


class Thresholds:
    """Every number a rule fires on, in one place.

    Defaults live in :data:`aadoctor.config.DEFAULTS` under ``[rules]`` so the
    configuration schema and the rules cannot drift apart. No rule body may
    contain a literal threshold: an incident from a server with tuned values
    has to stay interpretable, which is why the values used are recorded in
    the output.

    These are starting points. **None of them has been validated against a
    real server** (SPEC-007).
    """

    __slots__ = ("_values",)

    def __init__(self, values: Optional[Dict[str, Any]] = None) -> None:
        resolved = dict(DEFAULTS["rules"])
        for key, value in (values or {}).items():
            if key in resolved and isinstance(value, (int, float)) and not isinstance(value, bool):
                resolved[key] = type(resolved[key])(value)
        self._values = resolved

    def __getattr__(self, name: str) -> Any:
        try:
            return self._values[name]
        except KeyError:
            raise AttributeError(name)

    def as_dict(self) -> Dict[str, Any]:
        return dict(self._values)

    @classmethod
    def from_config(cls, config: Any = None) -> "Thresholds":
        data = getattr(config, "data", None)
        section = data.get("rules") if isinstance(data, dict) else None
        return cls(section if isinstance(section, dict) else None)


# --- what a rule produces -------------------------------------------------


@dataclass
class Finding:
    """One named observation, with the numbers that produced it.

    ``aspects`` names the inputs the finding rests on, which is how a quality
    problem is confined to what it actually damages: paths pruned by the
    cardinality cap weaken ONE_URL_DOMINATING and have nothing to do with 300
    upstream timeouts read cleanly from the error log.
    """

    code: str
    confidence: float
    site: Optional[str] = None
    path: Optional[str] = None
    ip: Optional[str] = None
    evidence: Dict[str, Any] = field(default_factory=dict)
    aspects: Tuple[str, ...] = ()
    #: Quality limits that lowered this confidence, named.
    caps: List[str] = field(default_factory=list)
    #: One line a human can read. Built by the rule, which knows what it means.
    description: str = ""

    @property
    def level(self) -> str:
        return level(self.confidence)

    def as_dict(self) -> dict:
        data: Dict[str, Any] = {
            "code": self.code,
            "confidence": round(self.confidence, 2),
            "level": self.level,
            "evidence": self.evidence,
            "description": self.description,
        }
        for name in ("site", "path", "ip"):
            value = getattr(self, name)
            if value is not None:
                data[name] = value
        if self.caps:
            data["limited_by"] = list(self.caps)
        return data


@dataclass
class NotEvaluable:
    """A rule that could not be decided, and why.

    Kept and reported rather than dropped. The absence of a finding is
    ambiguous - it may mean the rule looked and saw nothing, or that it never
    had the data to look at all - and a reader who cannot tell the two apart
    will read silence as evidence of calm.
    """

    code: str
    reason: str

    def as_dict(self) -> dict:
        return {"code": self.code, "reason": self.reason}


# --- quality --------------------------------------------------------------


@dataclass
class Quality:
    """How trustworthy each input was, so caps land where the damage is."""

    coverage_ratio: float = 1.0
    coverage_seconds: float = 0.0
    window_seconds: int = 0
    access_unparsed_ratio: float = 0.0
    error_unparsed_ratio: float = 0.0
    truncated: Dict[str, int] = field(default_factory=dict)

    def cap_for(self, aspects: Sequence[str]) -> Tuple[float, List[str]]:
        """The highest confidence these inputs may carry, and why."""
        cap = 1.0
        reasons: List[str] = []

        def apply(value: float, reason: str) -> None:
            nonlocal cap
            if value < cap:
                cap = value
            reasons.append(reason)

        # Coverage is not tied to one dimension: it says how much of the
        # requested window exists at all, which limits everything read from it.
        if self.coverage_ratio < 0.25:
            apply(CAP_MEDIUM, "low_window_coverage")
        elif self.coverage_ratio < 0.50:
            apply(CAP_HIGH, "partial_window_coverage")

        if "access" in aspects:
            if self.access_unparsed_ratio > 0.50:
                apply(CAP_MEDIUM, "access_log_mostly_unparsed")
            elif self.access_unparsed_ratio > 0.25:
                apply(CAP_HIGH, "access_log_partly_unparsed")

        if "error" in aspects:
            if self.error_unparsed_ratio > 0.50:
                apply(CAP_MEDIUM, "error_log_mostly_unparsed")
            elif self.error_unparsed_ratio > 0.25:
                apply(CAP_HIGH, "error_log_partly_unparsed")

        for dimension in ("sites", "paths", "ips"):
            if dimension in aspects and self.truncated.get(dimension):
                apply(CAP_HIGH, "%s_truncated_by_cardinality_cap" % dimension)

        return cap, reasons

    def issues(self) -> List[str]:
        """Everything worth telling the reader about the data itself."""
        found: List[str] = []
        if self.coverage_seconds and self.window_seconds and self.coverage_ratio < 0.99:
            found.append(
                "the %ss window holds %.0fs of data"
                % (self.window_seconds, self.coverage_seconds)
            )
        if self.access_unparsed_ratio > 0.05:
            found.append(
                "%.0f%% of access lines did not parse; traffic evidence may be incomplete"
                % (self.access_unparsed_ratio * 100)
            )
        if self.error_unparsed_ratio > 0.05:
            found.append(
                "%.0f%% of error lines did not parse" % (self.error_unparsed_ratio * 100)
            )
        for dimension, displaced in sorted(self.truncated.items()):
            if displaced:
                found.append(
                    "%s requests fell outside the %s cardinality cap"
                    % (f"{displaced:,}", dimension)
                )
        return found

    def as_dict(self) -> dict:
        return {
            "coverage_seconds": round(self.coverage_seconds, 1),
            "window_seconds": self.window_seconds,
            "coverage_ratio": round(self.coverage_ratio, 4),
            "access_unparsed_ratio": round(self.access_unparsed_ratio, 4),
            "error_unparsed_ratio": round(self.error_unparsed_ratio, 4),
            "truncated": {k: v for k, v in sorted(self.truncated.items()) if v},
            "issues": self.issues(),
        }


def quality_of(facts: Facts) -> Quality:
    return Quality(
        coverage_ratio=facts.coverage_ratio,
        coverage_seconds=facts.coverage_seconds,
        window_seconds=facts.window_seconds,
        access_unparsed_ratio=facts.access_unparsed_ratio,
        error_unparsed_ratio=facts.error_unparsed_ratio,
        truncated=facts.truncated(),
    )


# --- running the table ----------------------------------------------------


def evaluate(
    facts: Facts, thresholds: Optional[Thresholds] = None
) -> Tuple[List[Finding], List[NotEvaluable], Quality]:
    """Run every rule once. Order of evaluation does not affect the result."""
    from .builtin import RULES

    limits = thresholds or Thresholds()
    quality = quality_of(facts)

    findings: List[Finding] = []
    blocked: List[NotEvaluable] = []

    for rule in RULES:
        result = rule(facts, limits)
        if result is None:
            continue
        if isinstance(result, NotEvaluable):
            blocked.append(result)
            continue

        cap, reasons = quality.cap_for(result.aspects)
        if result.confidence > cap:
            result.confidence = cap
            result.caps.extend(reasons)
        findings.append(result)

    # Strongest first, ties broken by code so two runs cannot disagree.
    findings.sort(key=lambda item: (-item.confidence, item.code))
    blocked.sort(key=lambda item: item.code)
    return findings, blocked, quality


__all__ = [
    "CAP_HIGH",
    "CAP_MEDIUM",
    "Facts",
    "Finding",
    "HIGH",
    "LOW",
    "MEDIUM",
    "NotEvaluable",
    "Quality",
    "RATIO_CEILING",
    "RULESET_VERSION",
    "Thresholds",
    "VERY_HIGH",
    "count_confidence",
    "evaluate",
    "level",
    "quality_of",
    "share_confidence",
    "strength",
    "volume_factor",
]
