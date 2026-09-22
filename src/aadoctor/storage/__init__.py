"""Where aaDoctor keeps its own state. Files only, no database (ADR-003)."""

from __future__ import annotations

from .json_store import read_json, write_json

__all__ = ["read_json", "write_json"]
