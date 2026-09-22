"""Discovery of what aaPanel has configured. Read-only (ADR-001)."""

from __future__ import annotations

from .aapanel import (
    LOG_ABSENT,
    LOG_CONFIGURED,
    LOG_DISABLED,
    LOG_UNRESOLVED,
    DiscoveryResult,
    LogTarget,
    SiteConfig,
    diff_sites,
    discover_sites,
    parse_vhost,
    summarize,
)

__all__ = [
    "DiscoveryResult",
    "LOG_ABSENT",
    "LOG_CONFIGURED",
    "LOG_DISABLED",
    "LOG_UNRESOLVED",
    "LogTarget",
    "SiteConfig",
    "diff_sites",
    "discover_sites",
    "parse_vhost",
    "summarize",
]
