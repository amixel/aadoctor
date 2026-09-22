"""Discover the sites configured in aaPanel's Nginx vhost directory.

Read-only, always: open for reading, stat, directory listing. Nothing here
writes, renames, removes or changes permissions on anything (ADR-001).

This is deliberately not an Nginx parser. It extracts three directives -
``server_name``, ``access_log`` and ``error_log`` - and gives up loudly rather
than guessing when a vhost does something it does not understand. A grammar,
an AST or ``include`` resolution would be far more code than the problem needs
(SPEC-002, README.md section 15).

Finding a log file here does not mean reading it. Tailing belongs to SPEC-003.
"""

from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

from ..environment import nginx_vhost_dir

logger = logging.getLogger("aadoctor.discovery")

#: Only files with this suffix are considered vhosts.
VHOST_SUFFIX = ".conf"

# State of one log directive within a site.
LOG_CONFIGURED = "configured"   # a usable absolute path
LOG_DISABLED = "disabled"       # 'off', or /dev/null
LOG_UNRESOLVED = "unresolved"   # a relative path we refuse to guess at
LOG_ABSENT = "absent"           # no directive at all

#: Values that mean "this log goes nowhere".
DISABLED_VALUES = ("off", "/dev/null")

#: Ranked best to worst, used when merging server blocks.
_STATE_RANK = (LOG_CONFIGURED, LOG_DISABLED, LOG_UNRESOLVED, LOG_ABSENT)

_DIRECTIVE = re.compile(
    r"^(server_name|access_log|error_log|include)\s+(.+)$",
)

_WORD_CHARS = re.compile(r"[A-Za-z0-9_]")


@dataclass
class LogTarget:
    """One log directive: what it says, and whether that file is there."""

    state: str = LOG_ABSENT
    path: Optional[Path] = None
    raw: Optional[str] = None
    log_format: Optional[str] = None
    exists: Optional[bool] = None
    readable: Optional[bool] = None

    @property
    def configured(self) -> bool:
        return self.state == LOG_CONFIGURED

    @property
    def missing_on_disk(self) -> bool:
        """Configured, but the file is not there (yet)."""
        return self.configured and self.exists is False


@dataclass
class SiteConfig:
    """One site as aaPanel's Nginx configuration describes it."""

    name: str
    server_names: List[str]
    config_path: Path
    access: LogTarget = field(default_factory=LogTarget)
    error: LogTarget = field(default_factory=LogTarget)
    unnamed: bool = False
    warnings: List[str] = field(default_factory=list)

    @property
    def access_log(self) -> Optional[Path]:
        return self.access.path if self.access.configured else None

    @property
    def error_log(self) -> Optional[Path]:
        return self.error.path if self.error.configured else None

    @property
    def aliases(self) -> List[str]:
        return [name for name in self.server_names if name != self.name]


@dataclass
class DiscoveryResult:
    """Everything one discovery pass found, plus what it could not read."""

    vhost_dir: Path
    sites: List[SiteConfig] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    unreadable: List[Path] = field(default_factory=list)

    @property
    def site_count(self) -> int:
        return len(self.sites)

    @property
    def names(self) -> List[str]:
        return [site.name for site in self.sites]

    def count_access(self, state: str) -> int:
        return sum(1 for site in self.sites if site.access.state == state)

    def count_error(self, state: str) -> int:
        return sum(1 for site in self.sites if site.error.state == state)

    @property
    def missing_files(self) -> List[Tuple[str, Path]]:
        """Logs a vhost declares that do not exist on disk."""
        missing = []
        for site in self.sites:
            for target in (site.access, site.error):
                if target.missing_on_disk and target.path is not None:
                    missing.append((site.name, target.path))
        return missing

    @property
    def site_warnings(self) -> List[str]:
        return [f"{site.name}: {text}" for site in self.sites for text in site.warnings]

    @property
    def all_warnings(self) -> List[str]:
        return self.warnings + self.site_warnings


# --- public API -----------------------------------------------------------


def discover_sites(
    vhost_dir: Optional[Path] = None,
    root: Optional[Path] = None,
    check_files: bool = True,
) -> DiscoveryResult:
    """Read every vhost in ``vhost_dir`` and return the sites it describes.

    ``vhost_dir`` defaults to aaPanel's Nginx directory; pass one explicitly to
    run against fixtures without touching /www. ``root`` reuses the same
    convention as :mod:`aadoctor.environment`.

    A file that cannot be read or parsed is skipped with a warning: one broken
    vhost never costs us the other 47 (README.md section 65).
    """
    directory = Path(vhost_dir) if vhost_dir is not None else nginx_vhost_dir(root)
    result = DiscoveryResult(vhost_dir=directory)

    try:
        entries = sorted(directory.iterdir())
    except FileNotFoundError:
        result.warnings.append(f"vhost directory not found: {directory}")
        return result
    except PermissionError:
        result.warnings.append(f"vhost directory not readable: {directory}")
        result.unreadable.append(directory)
        return result
    except OSError as exc:
        result.warnings.append(f"cannot list {directory}: {exc}")
        return result

    sites: List[SiteConfig] = []
    for entry in entries:
        if entry.suffix != VHOST_SUFFIX or not entry.is_file():
            continue

        text = _read_text(entry, result)
        if text is None:
            continue

        try:
            sites.extend(parse_vhost(text, entry))
        except Exception as exc:  # a parser bug must not end discovery
            logger.warning("failed to parse %s: %s", entry, exc)
            result.warnings.append(f"{entry.name}: could not be parsed ({exc})")

    if check_files:
        for site in sites:
            _stat_logs(site)

    # Stable across runs: name first, then the file it came from, so two sites
    # sharing a name keep a fixed order (SPEC-002, deterministic ordering).
    sites.sort(key=lambda site: (site.name, str(site.config_path)))
    result.sites = sites
    result.warnings.extend(_duplicate_name_warnings(sites))
    return result


def parse_vhost(text: str, config_path: Path) -> List[SiteConfig]:
    """Parse one vhost file into the site or sites it defines.

    Pure: no filesystem access, so the parser is testable from a string.
    """
    blocks = [_parse_block(body) for body in _server_blocks(text)]
    if not blocks:
        return []

    sites = _merge_blocks(blocks, Path(config_path))

    if _unbalanced(text):
        # Parsed what could be read rather than discarding the site, but the
        # administrator should know the file is not what Nginx would accept.
        for site in sites:
            site.warnings.append(
                "unbalanced braces in this file; only part of it could be read"
            )

    return sites


def diff_sites(
    previous: Sequence[SiteConfig],
    current: Sequence[SiteConfig],
) -> Tuple[List[str], List[str]]:
    """Names added and removed between two passes. Pure, for the daemon."""
    before = {site.name for site in previous}
    after = {site.name for site in current}
    return sorted(after - before), sorted(before - after)


def summarize(result: DiscoveryResult) -> str:
    """One line for the daemon log."""
    return (
        f"{result.site_count} sites, "
        f"{result.count_access(LOG_CONFIGURED)} access logs, "
        f"{result.count_error(LOG_CONFIGURED)} error logs"
    )


# --- reading --------------------------------------------------------------


def _read_text(path: Path, result: DiscoveryResult) -> Optional[str]:
    """Read a vhost, tolerating odd bytes. Never raises."""
    try:
        # errors='replace': a stray byte must not cost us the whole site.
        return path.read_text(encoding="utf-8", errors="replace")
    except PermissionError:
        logger.warning("permission denied reading %s", path)
        result.warnings.append(f"{path.name}: permission denied")
        result.unreadable.append(path)
    except OSError as exc:
        logger.warning("cannot read %s: %s", path, exc)
        result.warnings.append(f"{path.name}: cannot be read ({exc})")
        result.unreadable.append(path)
    return None


def _stat_logs(site: SiteConfig) -> None:
    """Record whether each configured log is currently on disk.

    Configured and existing are different questions: a site whose log has not
    been written yet is still a valid site (SPEC-002).
    """
    for target in (site.access, site.error):
        if not target.configured or target.path is None:
            continue
        try:
            target.exists = target.path.exists()
            target.readable = target.exists and os.access(str(target.path), os.R_OK)
        except OSError:
            target.exists = False
            target.readable = False


# --- block scanning -------------------------------------------------------


def _strip_comment(line: str) -> str:
    """Drop a trailing comment. Indented comments count too."""
    index = line.find("#")
    return line if index < 0 else line[:index]


def _unbalanced(text: str) -> bool:
    cleaned = "\n".join(_strip_comment(line) for line in text.splitlines())
    return cleaned.count("{") != cleaned.count("}")


def _server_blocks(text: str) -> List[str]:
    """Return the body of every top-level ``server`` block.

    Scans once, tracking brace depth and the word that opened each brace.
    aaPanel writes the brace on its own line::

        server
        {
            ...
        }

    so matching a literal ``server {`` would find nothing.
    """
    cleaned = "\n".join(_strip_comment(line) for line in text.splitlines())

    blocks: List[str] = []
    depth = 0
    start: Optional[int] = None
    word = ""
    last_word = ""

    for index, char in enumerate(cleaned):
        if char == "{":
            opener = word or last_word
            if depth == 0:
                start = index + 1 if opener == "server" else None
            depth += 1
            word = last_word = ""
        elif char == "}":
            depth -= 1
            if depth <= 0:
                if start is not None:
                    blocks.append(cleaned[start:index])
                    start = None
                depth = max(depth, 0)
            word = last_word = ""
        elif char == ";":
            word = last_word = ""
        elif _WORD_CHARS.match(char):
            word += char
        else:
            if word:
                last_word = word
                word = ""

    if start is not None:
        # Unterminated block: take what is there rather than dropping the site.
        blocks.append(cleaned[start:])

    return blocks


# --- directive extraction -------------------------------------------------


class _Block:
    """Directives collected from one server block."""

    def __init__(self) -> None:
        self.server_names: List[str] = []
        self.access = LogTarget()
        self.error = LogTarget()
        self.has_include = False
        self.warnings: List[str] = []


def _parse_block(body: str) -> _Block:
    """Pull the directives we care about out of one server block.

    Nginx statements end at ``;``, not at a line break, so the body is split on
    statement boundaries rather than on lines: a whole block written on one line
    parses the same as an indented one, and a ``server_name`` continued across
    two lines is still one directive. Braces count as boundaries too, so a
    nested ``location`` block cannot glue itself to the directive before it.
    """
    block = _Block()
    normalized = body.replace("{", ";").replace("}", ";")

    for statement in normalized.split(";"):
        statement = " ".join(statement.split())
        if not statement:
            continue

        match = _DIRECTIVE.match(statement)
        if not match:
            continue

        directive, value = match.group(1), match.group(2).strip()
        if directive == "server_name":
            _collect_server_names(block, value)
        elif directive == "include":
            block.has_include = True
        elif directive == "access_log":
            _collect_log(block, block.access, "access_log", value)
        elif directive == "error_log":
            _collect_log(block, block.error, "error_log", value)

    return block


def _collect_server_names(block: _Block, value: str) -> None:
    for name in value.split():
        name = name.strip().strip('"').strip("'")
        if name and name not in block.server_names:
            block.server_names.append(name)


def _collect_log(block: _Block, target: LogTarget, directive: str, value: str) -> None:
    """Fill ``target`` from a log directive, keeping the first usable one.

    Nginx allows several of these and writes to all of them. We follow the
    first and note the rest, rather than inventing a merge rule (SPEC-002).
    """
    tokens = value.split()
    if not tokens:
        return

    # A log_format name, a severity or a buffer spec never contains a slash.
    # One that does means a missing semicolon swallowed the next directive, so
    # the path is trusted and everything after it is not.
    if any("/" in token for token in tokens[1:]):
        block.warnings.append(
            f"{directive} looks like it is missing a semicolon: {value!r}"
        )
        tokens = tokens[:1]

    parsed = _parse_log_value(tokens, directive)

    if target.state == LOG_ABSENT:
        _copy_log(parsed, target)
        return

    # A second directive: keep the first, but say so when they disagree.
    if parsed.state == LOG_CONFIGURED and target.state == LOG_CONFIGURED:
        if parsed.path != target.path:
            block.warnings.append(
                f"several {directive} directives; using {target.path}, ignoring {parsed.path}"
            )
    elif _rank(parsed.state) < _rank(target.state):
        # The first one was unusable and this one is better.
        _copy_log(parsed, target)


def _parse_log_value(tokens: List[str], directive: str) -> LogTarget:
    raw = tokens[0]
    extra = tokens[1] if len(tokens) > 1 else None

    # 'access_log off' is documented; 'error_log off' is not, across all Nginx
    # versions, and may produce a file literally named 'off'. Either way there
    # is no log worth following, so both count as disabled.
    if raw in DISABLED_VALUES:
        return LogTarget(state=LOG_DISABLED, raw=raw)

    if not raw.startswith("/"):
        # Relative to the Nginx prefix, which we do not try to determine.
        return LogTarget(state=LOG_UNRESOLVED, raw=raw)

    target = LogTarget(state=LOG_CONFIGURED, path=Path(raw), raw=raw)
    if directive == "access_log" and extra:
        target.log_format = extra
    return target


def _copy_log(source: LogTarget, target: LogTarget) -> None:
    target.state = source.state
    target.path = source.path
    target.raw = source.raw
    target.log_format = source.log_format


def _rank(state: str) -> int:
    try:
        return _STATE_RANK.index(state)
    except ValueError:  # pragma: no cover - defensive
        return len(_STATE_RANK)


# --- assembling sites -----------------------------------------------------


def _merge_blocks(blocks: List[_Block], config_path: Path) -> List[SiteConfig]:
    """Group the file's server blocks into sites.

    Blocks that share a ``server_name`` are the same site - the usual case
    being an HTTP block that redirects to an HTTPS one. Blocks with no name of
    their own join the first group, because one aaPanel file describes one
    site. Blocks with different names stay apart: merging two real sites would
    be worse than reporting two.
    """
    groups: List[Dict[str, object]] = []

    for block in blocks:
        target = _group_for(groups, block)
        if target is None:
            groups.append({"names": list(block.server_names), "blocks": [block]})
            continue

        names = target["names"]
        assert isinstance(names, list)
        for name in block.server_names:
            if name not in names:
                names.append(name)
        blocks_in_group = target["blocks"]
        assert isinstance(blocks_in_group, list)
        blocks_in_group.append(block)

    return [_build_site(group, config_path) for group in groups]


def _group_for(groups: List[Dict[str, object]], block: _Block) -> Optional[Dict[str, object]]:
    if not groups:
        return None

    if not block.server_names:
        return groups[0]

    for group in groups:
        names = group["names"]
        assert isinstance(names, list)
        if set(block.server_names) & set(names):
            return group
    return None


def _build_site(group: Dict[str, object], config_path: Path) -> SiteConfig:
    names = group["names"]
    blocks = group["blocks"]
    assert isinstance(names, list) and isinstance(blocks, list)

    access = LogTarget()
    error = LogTarget()
    warnings: List[str] = []
    has_include = False

    for block in blocks:
        _adopt(block.access, access)
        _adopt(block.error, error)
        warnings.extend(block.warnings)
        has_include = has_include or block.has_include

    name, unnamed = _canonical_name(names, config_path)

    if unnamed:
        warnings.append(
            "no usable server_name; identified by its configuration file name"
        )

    if has_include:
        for target, label in ((access, "access_log"), (error, "error_log")):
            if target.state == LOG_ABSENT:
                warnings.append(
                    f"no {label} in this file; it may come from an include, "
                    "which aaDoctor does not follow"
                )

    if access.state == LOG_UNRESOLVED:
        warnings.append(f"access_log path is relative and was not resolved: {access.raw}")
    if error.state == LOG_UNRESOLVED:
        warnings.append(f"error_log path is relative and was not resolved: {error.raw}")

    return SiteConfig(
        name=name,
        server_names=list(names),
        config_path=config_path,
        access=access,
        error=error,
        unnamed=unnamed,
        warnings=warnings,
    )


def _adopt(source: LogTarget, target: LogTarget) -> None:
    """Keep the most useful of two log targets: configured beats disabled."""
    if _rank(source.state) < _rank(target.state):
        _copy_log(source, target)


def _canonical_name(server_names: List[str], config_path: Path) -> Tuple[str, bool]:
    """Pick the name a human will see in reports.

    The first real ``server_name`` wins. Catch-alls (``_``), wildcards and
    regular expressions are kept as aliases but never become the site name; a
    vhost with none of them is named after its file (SPEC-002).
    """
    for name in server_names:
        if _is_usable_name(name):
            return name, False
    return config_path.stem, True


def _is_usable_name(name: str) -> bool:
    if not name or name == "_":
        return False
    return not name.startswith(("*", ".", "~", "$"))


def _duplicate_name_warnings(sites: List[SiteConfig]) -> List[str]:
    """Warn when two vhosts claim the same name - never merge them."""
    seen: Dict[str, List[Path]] = {}
    for site in sites:
        seen.setdefault(site.name, []).append(site.config_path)

    warnings = []
    for name, sources in sorted(seen.items()):
        if len(sources) > 1:
            files = ", ".join(path.name for path in sources)
            warnings.append(f"{name}: declared by several vhosts ({files})")
    return warnings
