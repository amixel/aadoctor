"""Configuration loading for aaDoctor.

Configuration lives at ``/etc/aadoctor/config.toml``. Missing file or missing
keys fall back to the built-in defaults below; a malformed file is an error
naming the file and the offending line, because starting with a half-applied
configuration is worse than not starting.

Only keys that are actually used exist here. The full eventual shape is in
README.md section 14; keys arrive as the phase that needs them lands.

TOML is read with :mod:`tomllib` on Python 3.11+ and with a small internal
parser below on older interpreters, so that no external dependency is needed.
See ADR-002 and ADR-008.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .paths import CONFIG_FILE

try:  # Python 3.11+
    import tomllib as _tomllib
except ImportError:  # pragma: no cover - depends on interpreter version
    _tomllib = None

#: Built-in defaults. The shipped config.example.toml mirrors this exactly.
DEFAULTS: Dict[str, Dict[str, Any]] = {
    "monitor": {"enabled": True, "interval_seconds": 10},
    "discovery": {"interval_seconds": 60},
    "load": {
        "enabled": True,
        "trigger_per_cpu": 1.0,
        "critical_per_cpu": 2.0,
        "recovery_per_cpu": 0.75,
        "trigger_polls": 2,
        "recovery_polls": 3,
    },
    "incidents": {"retention_days": 30},
    # Thresholds the deterministic rules fire on (SPEC-007). They live here
    # rather than inside the rules so that nothing is hardcoded and an
    # incident from a tuned server stays interpretable. Every one is an
    # unvalidated starting point; most people should never touch them.
    "rules": {
        "min_volume": 100,
        "site_share": 0.70,
        "site_share_ceiling": 0.85,
        "url_share": 0.50,
        "url_share_ceiling": 0.75,
        "ip_share": 0.50,
        "ip_share_ceiling": 0.75,
        "not_found_share": 0.30,
        "not_found_min_rate": 0.5,
        "http_5xx_share": 0.01,
        "http_5xx_min_rate": 1.0,
        "http_5xx_min_count": 10,
        "upstream_timeout_min": 5,
        "fastcgi_error_min": 5,
        "php_error_min": 10,
        "traffic_spike_factor": 3.0,
    },
    "mysql": {"enabled": False},
    "ai": {"enabled": False},
}


class ConfigError(Exception):
    """Configuration could not be read or is invalid."""


class Config:
    """Resolved configuration: defaults merged with the file, if any."""

    def __init__(
        self,
        data: Dict[str, Dict[str, Any]],
        source: Optional[Path] = None,
        unknown_keys: Optional[List[str]] = None,
    ) -> None:
        self.data = data
        self.source = source
        self.unknown_keys = unknown_keys or []

    @property
    def from_defaults(self) -> bool:
        """True when no configuration file was read."""
        return self.source is None

    def get(self, section: str, key: str) -> Any:
        try:
            return self.data[section][key]
        except KeyError:
            raise ConfigError(f"unknown configuration key: [{section}] {key}")

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"Config(source={self.source!r}, data={self.data!r})"


def load(path: Optional[Path] = None) -> Config:
    """Load configuration, falling back to defaults when the file is absent."""
    target = Path(path) if path is not None else CONFIG_FILE

    try:
        text = target.read_text(encoding="utf-8")
    except FileNotFoundError:
        return Config(_copy_defaults(), source=None)
    except OSError as exc:
        raise ConfigError(f"cannot read {target}: {exc}") from exc

    try:
        parsed = parse(text)
    except ConfigError as exc:
        raise ConfigError(f"{target}: {exc}") from exc

    data, unknown = _merge(parsed)
    return Config(data, source=target, unknown_keys=unknown)


def parse(text: str) -> Dict[str, Any]:
    """Parse TOML text with the standard library when available."""
    if _tomllib is not None:
        try:
            return _tomllib.loads(text)
        except Exception as exc:  # tomllib raises TOMLDecodeError
            raise ConfigError(str(exc)) from exc
    return _parse_minimal_toml(text)


def _copy_defaults() -> Dict[str, Dict[str, Any]]:
    return {section: dict(values) for section, values in DEFAULTS.items()}


def _merge(parsed: Dict[str, Any]) -> Tuple[Dict[str, Dict[str, Any]], List[str]]:
    """Merge parsed values over the defaults, validating types.

    Unknown sections and keys are not an error - a newer configuration file may
    be read by an older build - but they are collected so ``doctor`` can warn
    about a typo instead of silently ignoring it.
    """
    data = _copy_defaults()
    unknown: List[str] = []

    for section, values in parsed.items():
        if not isinstance(values, dict):
            raise ConfigError(f"[{section}] must be a section, not a bare value")
        if section not in data:
            unknown.append(f"[{section}]")
            data[section] = dict(values)
            continue
        for key, value in values.items():
            if key not in data[section]:
                unknown.append(f"[{section}] {key}")
                data[section][key] = value
                continue
            expected = type(DEFAULTS[section][key])
            # An integer where a float is expected is fine and natural to
            # write: rejecting `trigger_per_cpu = 1` would be pedantry.
            if expected is float and isinstance(value, int) and not isinstance(value, bool):
                value = float(value)
            if not isinstance(value, expected) or isinstance(value, bool) != (expected is bool):
                raise ConfigError(
                    f"[{section}] {key} must be {expected.__name__}, got {type(value).__name__}"
                )
            data[section][key] = value

    return data, unknown


def _parse_minimal_toml(text: str) -> Dict[str, Any]:
    """Parse the restricted TOML subset aaDoctor's own configuration uses.

    Supported: comments, ``[section]`` headers, and ``key = value`` where value
    is a boolean, an integer, a float or a basic quoted string. Anything else
    raises, rather than being guessed at - a configuration this parser cannot
    read is a configuration the daemon must not start with.
    """
    result: Dict[str, Any] = {}
    section: Optional[str] = None

    for number, raw in enumerate(text.splitlines(), start=1):
        line = _strip_comment(raw).strip()
        if not line:
            continue

        if line.startswith("["):
            if not line.endswith("]"):
                raise ConfigError(f"line {number}: unterminated section header")
            name = line[1:-1].strip()
            if not name or "." in name:
                raise ConfigError(f"line {number}: unsupported section header {line!r}")
            section = name
            result.setdefault(section, {})
            continue

        if "=" not in line:
            raise ConfigError(f"line {number}: expected key = value")
        if section is None:
            raise ConfigError(f"line {number}: key outside of any section")

        key, _, raw_value = line.partition("=")
        key = key.strip()
        if not key:
            raise ConfigError(f"line {number}: empty key")

        result[section][key] = _parse_value(raw_value.strip(), number)

    return result


def _strip_comment(line: str) -> str:
    """Remove a trailing comment, respecting quoted strings."""
    quote: Optional[str] = None
    for index, char in enumerate(line):
        if quote is not None:
            if char == quote:
                quote = None
            continue
        if char in ("'", '"'):
            quote = char
            continue
        if char == "#":
            return line[:index]
    return line


def _parse_value(raw: str, number: int) -> Any:
    if not raw:
        raise ConfigError(f"line {number}: missing value")

    if raw in ("true", "false"):
        return raw == "true"

    if len(raw) >= 2 and raw[0] == raw[-1] and raw[0] in ("'", '"'):
        return raw[1:-1]

    try:
        return int(raw)
    except ValueError:
        pass

    try:
        return float(raw)
    except ValueError:
        pass

    raise ConfigError(f"line {number}: unsupported value {raw!r}")
