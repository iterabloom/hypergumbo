# SPDX-License-Identifier: MPL-2.0
"""Store for the hypergumbo tracker — YAML I/O, compile, CRUD, Lamport clock.

This module provides the core storage operations for the tracker:

- **YAML serialization:** Writes ops using ruamel.yaml (round-trip-safe,
  canonical field ordering, double-quoted strings for safety). Reads ops
  using PyYAML's CSafeLoader (~5x faster C extension).
- **Nonce-on-every-line:** Post-processes serialized YAML to append
  `  # <nonce>` inline comments on every non-empty line, making each line
  globally unique for merge=union correctness.
- **Proquint IDs:** Content-hash IDs using SHA-256 + proquint encoding.
  Same logical item → same ID, natural deduplication.
- **compile():** Pure function that folds an op log into current item state.
  LWW for scalars, accumulation for sets, Lamport-clock-ordered.
- **Lamport clock:** Cross-branch causal ordering via git cat-file --batch.
- **SimHash:** Fast locality-sensitive fingerprinting for near-duplicate
  detection on add().
- **CRUD:** add(), update(), discuss(), lock/unlock methods with advisory
  file locking (flock) around appends.
- **Prefix matching:** Resolve unambiguous ID prefixes to full IDs.
- **ready/list:** Filtered, sorted item queries.

The Store operates on a single tier directory (no TrackerSet, no cache —
those come in later PRs).

See ADR-0013 for the full design specification.
"""

from __future__ import annotations

import datetime
import fcntl
import hashlib
import json
import os
import secrets
import subprocess  # nosec B404
import warnings
from collections import defaultdict
from io import StringIO
from pathlib import Path
from typing import Any

import proquint
import yaml
from ruamel.yaml import YAML
from ruamel.yaml.comments import CommentedMap
from ruamel.yaml.scalarstring import DoubleQuotedScalarString

from hypergumbo_tracker.models import (
    CompiledItem,
    DiscussionEntry,
    TrackerConfig,
    load_config,
    resolve_actor,
)


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class ItemExistsError(Exception):
    """Raised when add() finds an item with the same ID already exists."""


class ItemNotFoundError(Exception):
    """Raised when an item ID cannot be resolved."""


class AmbiguousPrefixError(Exception):
    """Raised when an ID prefix matches multiple items."""

    def __init__(self, prefix: str, candidates: list[tuple[str, str]]) -> None:
        self.prefix = prefix
        self.candidates = candidates
        lines = [f"'{prefix}' is ambiguous. Did you mean:"]
        for item_id, title in candidates:
            lines.append(f"  {item_id}  {title}")
        super().__init__("\n".join(lines))


class LockedFieldError(Exception):
    """Raised when an agent attempts to modify a field that is locked."""


class HumanAuthorityError(Exception):
    """Raised when an agent attempts an operation reserved for humans."""


class CorruptFileError(Exception):
    """Raised when an ops file cannot be parsed or compiled."""


class DiscussionRateLimitError(Exception):
    """Raised when discussion rate limit is exceeded for an item."""


class CycleError(Exception):
    """Raised when before links would create a cycle."""


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Canonical field order for op serialization
_COMMON_FIELD_ORDER = ["op", "at", "by", "actor", "clock", "nonce"]

_CREATE_DATA_FIELD_ORDER = [
    "kind", "title", "status", "priority", "parent", "tags",
    "before", "duplicate_of", "not_duplicate_of", "pr_ref",
    "justification", "description", "fields",
]

# Fields that must always be double-quoted in YAML output
_DOUBLE_QUOTED_FIELDS = frozenset({
    "title", "description", "justification", "message",
})

# Set-valued fields on items (accumulated via add/remove, not LWW)
_SET_VALUED_FIELDS = frozenset({"tags", "before", "duplicate_of", "not_duplicate_of"})

# Daily discussion rate limit: 200,000 tokens per item
_DISCUSSION_DAILY_TOKEN_LIMIT = 200_000

# Chars-per-token estimate (no tokenizer dependency)
_CHARS_PER_TOKEN = 4.4

# SimHash fingerprint bit width
_SIMHASH_BITS = 40

# SimHash distance threshold for similarity warning
_SIMHASH_THRESHOLD = 8

# Discussion soft cap (entry count)
_DISCUSSION_SOFT_CAP = 20


# ---------------------------------------------------------------------------
# YAML serialization
# ---------------------------------------------------------------------------


def _make_nonce() -> str:
    """Generate a 4-character hex nonce for op serialization."""
    return secrets.token_hex(2)


def _double_quote(value: str) -> DoubleQuotedScalarString:
    """Wrap a string in double quotes for ruamel.yaml output."""
    return DoubleQuotedScalarString(value)


def _prepare_op_for_yaml(op_dict: dict[str, Any]) -> CommentedMap:
    """Convert an op dict to a CommentedMap with canonical ordering and quoting.

    Applies double-quoting to fields that need it (title, description, message,
    fields.* string values) and canonical field ordering.
    Common fields (op, at, by, actor, clock, nonce) never need double-quoting.
    """
    op_type = op_dict.get("op", "")
    result = CommentedMap()

    # Common fields first (none of these are in _DOUBLE_QUOTED_FIELDS)
    for key in _COMMON_FIELD_ORDER:
        if key in op_dict:
            result[key] = op_dict[key]

    # Op-specific fields
    if op_type == "create" and "data" in op_dict:
        data_raw = op_dict["data"]
        data_cm = CommentedMap()
        for key in _CREATE_DATA_FIELD_ORDER:
            if key in data_raw:
                val = data_raw[key]
                if key in _DOUBLE_QUOTED_FIELDS and isinstance(val, str):
                    val = _double_quote(val)
                elif key == "fields" and isinstance(val, dict):
                    fields_cm = CommentedMap()
                    for fk, fv in val.items():
                        if isinstance(fv, str):
                            fields_cm[fk] = _double_quote(fv)
                        else:
                            fields_cm[fk] = fv
                    val = fields_cm
                data_cm[key] = val
        # Any remaining data keys not in canonical order
        for key in data_raw:
            if key not in data_cm:
                data_cm[key] = data_raw[key]
        result["data"] = data_cm
    elif op_type == "update":
        if "set" in op_dict:
            set_cm = CommentedMap()
            for k, v in op_dict["set"].items():
                if k in _DOUBLE_QUOTED_FIELDS and isinstance(v, str):
                    set_cm[k] = _double_quote(v)
                elif k == "fields" and isinstance(v, dict):
                    fields_cm = CommentedMap()
                    for fk, fv in v.items():
                        if isinstance(fv, str):
                            fields_cm[fk] = _double_quote(fv)
                        else:
                            fields_cm[fk] = fv
                    set_cm[k] = fields_cm
                else:
                    set_cm[k] = v
            result["set"] = set_cm
        if "add" in op_dict:
            add_cm = CommentedMap()
            for k, v in op_dict["add"].items():
                add_cm[k] = v
            result["add"] = add_cm
        if "remove" in op_dict:
            remove_cm = CommentedMap()
            for k, v in op_dict["remove"].items():
                remove_cm[k] = v
            result["remove"] = remove_cm
    elif op_type == "discuss" and "message" in op_dict:
        result["message"] = _double_quote(op_dict["message"])
    elif op_type == "discuss_summarize" and "message" in op_dict:
        result["message"] = _double_quote(op_dict["message"])
    elif op_type == "lock" and "lock" in op_dict:
        result["lock"] = op_dict["lock"]
    elif op_type == "unlock" and "unlock" in op_dict:
        result["unlock"] = op_dict["unlock"]
    elif op_type == "reconcile":
        if "from_tier" in op_dict:
            result["from_tier"] = op_dict["from_tier"]
        if "reason" in op_dict:
            result["reason"] = _double_quote(op_dict["reason"])

    return result


def _serialize_op(op_dict: dict[str, Any]) -> str:
    """Serialize a single op dict to YAML with nonce-on-every-line.

    Uses ruamel.yaml for proper quoting/ordering, then post-processes
    to append `  # <nonce>` to every non-empty line.

    Returns the serialized YAML string (including the leading `- ` for
    YAML sequence items) with trailing newline.
    """
    nonce = op_dict.get("nonce", _make_nonce())

    prepared = _prepare_op_for_yaml(op_dict)

    ry = YAML()
    ry.default_flow_style = False
    ry.width = 4096  # Prevent line wrapping

    stream = StringIO()
    ry.dump([prepared], stream)
    raw_yaml = stream.getvalue()

    # Post-process: add nonce comment to every non-empty line
    lines = raw_yaml.split("\n")
    result_lines = []
    for line in lines:
        stripped = line.rstrip()
        if stripped:
            # Don't add duplicate nonce if it somehow already has one
            if not stripped.endswith(f"# {nonce}"):
                result_lines.append(f"{stripped}  # {nonce}")
            else:  # pragma: no cover — defensive: ruamel.yaml won't produce this
                result_lines.append(stripped)
        else:
            result_lines.append("")

    result = "\n".join(result_lines)
    # Ensure trailing newline
    if not result.endswith("\n"):  # pragma: no cover — defensive: ruamel.yaml always adds \n
        result += "\n"
    return result


def _parse_ops_file(filepath: Path) -> list[dict[str, Any]]:
    """Parse an ops file using PyYAML CSafeLoader (fast C extension).

    Returns a list of op dicts. Raises CorruptFileError if the file
    cannot be parsed as valid YAML.
    """
    try:
        with open(filepath) as f:
            content = f.read()
        if not content.strip():
            return []
        data = yaml.load(content, Loader=yaml.CSafeLoader)
        if data is None:
            return []
        if not isinstance(data, list):
            raise CorruptFileError(f"{filepath}: expected YAML list, got {type(data).__name__}")
        return data
    except yaml.YAMLError as e:
        raise CorruptFileError(f"{filepath}: {e}") from e


def _parse_ops_bytes(content: bytes) -> list[dict[str, Any]]:
    """Parse YAML op bytes using CSafeLoader. For cross-branch peek."""
    try:
        data = yaml.load(content, Loader=yaml.CSafeLoader)
        if data is None:
            return []
        if not isinstance(data, list):
            return []
        return data
    except yaml.YAMLError:
        return []


# ---------------------------------------------------------------------------
# ID generation (proquint-encoded SHA-256)
# ---------------------------------------------------------------------------


def _compute_id(kind_prefix: str, data: dict[str, Any], salt: str = "") -> str:
    """Compute a content-hash ID for an item.

    SHA-256 of the canonicalized data dict (sorted keys JSON), first 128 bits
    encoded as 8 proquint syllables, prefixed with the kind prefix.

    The hash input is the data dict (excluding at, by, clock, nonce) so
    the same logical item created at different times by different actors
    gets the same ID.

    The proquint library's uint2quint() takes a 32-bit integer and produces
    two syllables (CVCVC-CVCVC). Four 32-bit words encode 128 bits as
    8 syllables total.

    Args:
        kind_prefix: e.g. "INV", "META", "WI"
        data: the create op's data dict
        salt: optional salt for hash collision resolution
    """
    canonical = json.dumps(data, sort_keys=True) + salt
    digest = hashlib.sha256(canonical.encode()).digest()

    # First 128 bits = 16 bytes = 4 x 32-bit words
    # proquint.uint2quint encodes a 32-bit int as two syllables (e.g. "lusab-bired")
    first_128 = digest[:16]

    # Convert to list of 32-bit integers
    words_32: list[int] = []
    for i in range(0, 16, 4):
        w = (first_128[i] << 24) | (first_128[i + 1] << 16) | \
            (first_128[i + 2] << 8) | first_128[i + 3]
        words_32.append(w)

    # Encode each 32-bit word as two proquint syllables
    syllable_pairs = [proquint.uint2quint(w) for w in words_32]
    proquint_str = "-".join(syllable_pairs)

    return f"{kind_prefix}-{proquint_str}"


# ---------------------------------------------------------------------------
# SimHash (locality-sensitive fingerprint)
# ---------------------------------------------------------------------------


def _tokenize(text: str) -> list[str]:
    """Tokenize text for SimHash: lowercase, split on whitespace/punctuation."""
    # Simple tokenization: lowercase, split on non-alphanumeric
    import re
    return re.findall(r"[a-z0-9]+", text.lower())


def _compute_simhash(text: str) -> int:
    """Compute a SimHash fingerprint for text.

    ~30 lines of pure Python, runs in microseconds, no dependencies.
    Hash each token with a lightweight hash, accumulate bit-position votes,
    threshold to produce a fingerprint of _SIMHASH_BITS width.

    Uses FNV-like hashing for speed and good distribution within
    the target bit range.
    """
    tokens = _tokenize(text)
    if not tokens:
        return 0

    # Accumulator for each bit position
    v = [0] * _SIMHASH_BITS
    mask = (1 << _SIMHASH_BITS) - 1

    for token in tokens:
        # Use a fast hash: FNV-1a adapted to _SIMHASH_BITS
        h = int(hashlib.sha1(token.encode(), usedforsecurity=False).hexdigest(), 16) & mask
        for i in range(_SIMHASH_BITS):
            if h & (1 << i):
                v[i] += 1
            else:
                v[i] -= 1

    # Threshold: positive votes → 1, else → 0
    fingerprint = 0
    for i in range(_SIMHASH_BITS):
        if v[i] > 0:
            fingerprint |= (1 << i)

    return fingerprint


def _hamming_distance(a: int, b: int) -> int:
    """Compute Hamming distance between two SimHash fingerprints."""
    return bin(a ^ b).count("1")


def _item_text_for_simhash(data: dict[str, Any]) -> str:
    """Extract text from a create op's data dict for SimHash computation."""
    parts = [
        data.get("title", ""),
        data.get("description", ""),
    ]
    fields = data.get("fields", {})
    if isinstance(fields, dict):
        for v in fields.values():
            if isinstance(v, str):
                parts.append(v)
            elif isinstance(v, list):
                parts.extend(str(item) for item in v)
    return " ".join(parts)


# ---------------------------------------------------------------------------
# compile() — pure function
# ---------------------------------------------------------------------------


def _op_sort_key(op_dict: dict[str, Any]) -> tuple[int, str, int]:
    """Sort key for ops: (clock, timestamp, actor_rank).

    actor_rank: human=0 (higher priority), agent=1.
    Lower rank wins in tiebreaker.
    """
    clock = op_dict.get("clock", 0)
    at = op_dict.get("at", "")
    by = op_dict.get("by", "agent")
    actor_rank = 0 if by == "human" else 1
    return (clock, at, actor_rank)


def compile_ops(ops: list[dict[str, Any]], item_id: str = "") -> CompiledItem:
    """Compile an op log (list of op dicts) into a CompiledItem.

    Pure function: deterministic given the same input ops (regardless of
    order in the file). Sorts ops by (clock, timestamp, actor_rank) and
    folds them to produce current state.

    Args:
        ops: List of parsed op dicts from an ops file.
        item_id: The item's ID (used for the compiled item's id field).

    Returns:
        CompiledItem with all fields populated from the op log.

    Raises:
        CorruptFileError: If no create op is found.
    """
    if not ops:
        raise CorruptFileError("Empty op log — no create op found")

    # Sort by Lamport clock order
    sorted_ops = sorted(ops, key=_op_sort_key)

    # Find the first create op (lowest clock)
    create_ops = [o for o in sorted_ops if o.get("op") == "create"]
    if not create_ops:
        raise CorruptFileError("Op log has no create op")

    first_create = create_ops[0]
    data = first_create.get("data", {})

    # Initialize compiled item from first create
    item = CompiledItem(
        id=item_id,
        kind=data.get("kind", ""),
        title=data.get("title", ""),
        status=data.get("status", ""),
        priority=data.get("priority", 2),
        parent=data.get("parent"),
        tags=list(data.get("tags", [])),
        before=list(data.get("before", [])),
        duplicate_of=list(data.get("duplicate_of", [])),
        not_duplicate_of=list(data.get("not_duplicate_of", [])),
        pr_ref=data.get("pr_ref"),
        justification=data.get("justification"),
        description=data.get("description", ""),
        fields=dict(data.get("fields", {})),
        created_at=first_create.get("at", ""),
    )

    # Fold subsequent ops
    for op_dict in sorted_ops:
        op_type = op_dict.get("op", "")
        item.updated_at = op_dict.get("at", item.updated_at)

        if op_type == "create":
            # Duplicate create (cross-branch merge): use earliest, skip rest
            if op_dict is not first_create:
                # Use earliest timestamp for created_at
                op_at = op_dict.get("at", "")
                if op_at and (not item.created_at or op_at < item.created_at):
                    item.created_at = op_at
            continue

        elif op_type == "update":
            # LWW for scalar fields in `set`
            set_dict = op_dict.get("set", {})
            for key, value in set_dict.items():
                if key == "fields" and isinstance(value, dict):
                    # Per-key LWW for fields dict
                    for fk, fv in value.items():
                        item.fields[fk] = fv
                elif key in _SET_VALUED_FIELDS:
                    # `set` on a set-valued field replaces wholesale
                    setattr(item, key, list(value) if isinstance(value, list) else [value])
                elif hasattr(item, key):
                    setattr(item, key, value)

            # Accumulate add ops (set-valued fields)
            add_dict = op_dict.get("add", {})
            for key, values in add_dict.items():
                if key in _SET_VALUED_FIELDS and hasattr(item, key):
                    current = getattr(item, key)
                    for v in values:
                        if v not in current:
                            current.append(v)

            # Accumulate remove ops (set-valued fields)
            remove_dict = op_dict.get("remove", {})
            for key, values in remove_dict.items():
                if key in _SET_VALUED_FIELDS and hasattr(item, key):
                    current = getattr(item, key)
                    for v in values:
                        if v in current:
                            current.remove(v)

        elif op_type == "discuss":
            item.discussion.append(DiscussionEntry(
                by=op_dict.get("by", ""),
                actor=op_dict.get("actor", ""),
                at=op_dict.get("at", ""),
                message=op_dict.get("message", ""),
            ))

        elif op_type == "discuss_clear":
            item.discussion = []

        elif op_type == "discuss_summarize":
            item.discussion = [DiscussionEntry(
                by=op_dict.get("by", ""),
                actor=op_dict.get("actor", ""),
                at=op_dict.get("at", ""),
                message=op_dict.get("message", ""),
                is_summary=True,
            )]

        elif op_type == "lock":
            for field_name in op_dict.get("lock", []):
                item.locked_fields.add(field_name)

        elif op_type == "unlock":
            for field_name in op_dict.get("unlock", []):
                item.locked_fields.discard(field_name)

        # promote, demote, stealth, unstealth, reconcile: audit-only, no state change

    # Clamp updated_at: wall-clock timestamps may not correlate with
    # Lamport clock order (clock drift, cross-branch merges).
    if item.updated_at < item.created_at:
        item.updated_at = item.created_at

    return item


# ---------------------------------------------------------------------------
# Lamport clock
# ---------------------------------------------------------------------------


def _compute_lamport_clock(
    filepath: Path,
    lamport_branches: list[str],
    git_dir: Path | None = None,
) -> int:
    """Compute the next Lamport clock value for an op to be appended.

    Peeks at the ops file on configured branches, HEAD, and any unmerged
    branches via `git cat-file --batch`. Returns max(all clocks) + 1.

    Falls back gracefully when branches are missing or git is unavailable.
    """
    max_clock = 0

    # Read local file for current max clock
    if filepath.exists():
        try:
            ops = _parse_ops_file(filepath)
            for op_dict in ops:
                c = op_dict.get("clock", 0)
                if c > max_clock:
                    max_clock = c
        except CorruptFileError:
            pass

    # Cross-branch peek via git cat-file --batch
    if git_dir is None:
        git_dir = _find_git_dir(filepath)

    if git_dir is not None:
        # Build list of refs to peek
        try:
            rel_path = filepath.relative_to(git_dir.parent)
        except ValueError:
            return max_clock + 1

        refs_to_check = []

        # Configured branches
        for branch in lamport_branches:
            refs_to_check.append(f"{branch}:{rel_path}")

        # HEAD
        refs_to_check.append(f"HEAD:{rel_path}")

        # Unmerged branches (best effort)
        primary = lamport_branches[0] if lamport_branches else "dev"
        try:
            result = subprocess.run(  # noqa: S603  # nosec B603, B607
                ["git", "branch", "--no-merged", primary, "--format=%(refname:short)"],  # noqa: S607
                capture_output=True, text=True, timeout=5,
                cwd=str(git_dir.parent),
            )
            if result.returncode == 0:
                for branch in result.stdout.strip().split("\n"):
                    branch = branch.strip()
                    if branch:
                        refs_to_check.append(f"{branch}:{rel_path}")
        except (subprocess.TimeoutExpired, OSError):
            pass

        # Batch peek
        max_clock = max(max_clock, _batch_peek_clocks(refs_to_check, git_dir))

    return max_clock + 1


def _batch_peek_clocks(refs: list[str], git_dir: Path) -> int:
    """Peek at clock values across branches using git cat-file --batch.

    Returns the maximum clock value found across all refs.
    """
    if not refs:
        return 0

    max_clock = 0

    try:
        proc = subprocess.Popen(  # nosec B603, B607
            ["git", "cat-file", "--batch"],  # noqa: S607
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=str(git_dir.parent),
        )

        input_data = "\n".join(refs) + "\n"
        stdout, _ = proc.communicate(input=input_data.encode(), timeout=10)

        # Parse batch output: each ref produces either a header + content
        # or a "missing" line
        pos = 0
        output = stdout
        while pos < len(output):
            # Read header line
            nl = output.find(b"\n", pos)
            if nl == -1:
                break
            header = output[pos:nl].decode(errors="replace")
            pos = nl + 1

            if "missing" in header:
                continue

            # Parse header: "<ref> <type> <size>"
            parts = header.split()
            if len(parts) < 3:
                continue

            try:
                size = int(parts[-1])
            except ValueError:
                continue

            # Read content
            if pos + size > len(output):
                break
            content = output[pos:pos + size]
            pos += size + 1  # +1 for trailing newline after content

            # Parse YAML content for clock values
            ops = _parse_ops_bytes(content)
            for op_dict in ops:
                c = op_dict.get("clock", 0)
                if c > max_clock:
                    max_clock = c

    except (subprocess.TimeoutExpired, OSError):
        pass

    return max_clock


def _find_git_dir(path: Path) -> Path | None:
    """Find the .git directory for the given path."""
    current = path if path.is_dir() else path.parent
    while current != current.parent:
        git_dir = current / ".git"
        if git_dir.is_dir():
            return git_dir
        # Handle gitdir files (worktrees)
        if git_dir.is_file():
            return git_dir
        current = current.parent
    return None


# ---------------------------------------------------------------------------
# Store
# ---------------------------------------------------------------------------


class Store:
    """Single-tier store for tracker items.

    Operates on a single .ops/ directory. No TrackerSet, no cache,
    no multi-tier merging — those come in later PRs.

    Args:
        ops_dir: Path to the .ops/ directory (must exist).
        config: TrackerConfig to use. If None, loads from the ops_dir's
            parent directory.
    """

    def __init__(self, ops_dir: Path, config: TrackerConfig | None = None) -> None:
        self._ops_dir = ops_dir
        if config is None:
            config = load_config(ops_dir.parent)
        self._config = config
        self._last_list: list[str] = []

    @property
    def ops_dir(self) -> Path:
        return self._ops_dir

    @property
    def config(self) -> TrackerConfig:
        return self._config

    # -----------------------------------------------------------------------
    # File operations
    # -----------------------------------------------------------------------

    def item_path(self, item_id: str) -> Path:
        """Get the filesystem path for an item's ops file."""
        return self._ops_dir / f".{item_id}.ops"

    def item_ids(self) -> list[str]:
        """Return all item IDs in this store."""
        return [self._id_from_filename(p) for p in self._list_item_files()]

    def _list_item_files(self) -> list[Path]:
        """List all item ops files in the directory."""
        if not self._ops_dir.exists():
            return []
        return sorted(
            p for p in self._ops_dir.iterdir()
            if p.name.startswith(".") and p.name.endswith(".ops") and p.is_file()
        )

    def _id_from_filename(self, path: Path) -> str:
        """Extract item ID from a .ops filename."""
        # .INV-lusab-bired-....ops → INV-lusab-bired-...
        name = path.name
        if name.startswith(".") and name.endswith(".ops"):
            return name[1:-4]
        return name

    # -----------------------------------------------------------------------
    # CRUD: add()
    # -----------------------------------------------------------------------

    def add(
        self,
        kind: str,
        title: str,
        status: str | None = None,
        priority: int = 2,
        parent: str | None = None,
        tags: list[str] | None = None,
        before: list[str] | None = None,
        description: str = "",
        fields: dict[str, Any] | None = None,
        duplicate_of: list[str] | None = None,
        not_duplicate_of: list[str] | None = None,
        pr_ref: str | None = None,
        justification: str | None = None,
    ) -> str:
        """Add a new item to the store.

        Creates a .ops file with a single create op. Returns the item ID.

        Raises:
            ItemExistsError: If an item with the same content hash already exists.
            ValueError: If kind is not recognized in config.
        """
        if kind not in self._config.kinds:
            raise ValueError(
                f"Unknown kind '{kind}'. Valid kinds: {list(self._config.kinds.keys())}"
            )

        kind_config = self._config.kinds[kind]
        if status is None:
            status = self._config.blocking_statuses[0] if self._config.blocking_statuses else "todo_hard"

        by, actor = resolve_actor(self._config.agent_usernames)
        nonce = _make_nonce()
        now = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

        data: dict[str, Any] = {
            "kind": kind,
            "title": title,
            "status": status,
            "priority": priority,
            "parent": parent,
            "tags": tags or [],
            "before": before or [],
            "duplicate_of": duplicate_of or [],
            "not_duplicate_of": not_duplicate_of or [],
            "pr_ref": pr_ref,
            "justification": justification,
            "description": description,
            "fields": fields or {},
        }

        # Compute content-hash ID
        item_id = _compute_id(kind_config.prefix, data)

        # Check for existing item with same ID
        item_path = self.item_path(item_id)
        if item_path.exists():
            # Same data → refuse, report existing
            existing_ops = _parse_ops_file(item_path)
            existing_creates = [o for o in existing_ops if o.get("op") == "create"]
            if existing_creates:
                existing_title = existing_creates[0].get("data", {}).get("title", "")
                raise ItemExistsError(
                    f"{item_id} already exists (title: '{existing_title}'). "
                    f"Use 'update' to modify it."
                )
            # Different data but same hash → collision, auto-salt
            item_id = _compute_id(kind_config.prefix, data, salt=nonce)
            item_path = self.item_path(item_id)

        # Compute SimHash
        simhash = _compute_simhash(_item_text_for_simhash(data))

        # Check for similar items
        self._check_similarity(item_id, simhash, data)

        # Compute Lamport clock (placeholder path for new file)
        clock = _compute_lamport_clock(item_path, self._config.lamport_branches)

        # Build and write create op
        op_dict: dict[str, Any] = {
            "op": "create",
            "at": now,
            "by": by,
            "actor": actor,
            "clock": clock,
            "nonce": nonce,
            "data": data,
        }

        serialized = _serialize_op(op_dict)

        # Write with flock
        self._ops_dir.mkdir(parents=True, exist_ok=True)
        with open(item_path, "w") as f:
            fcntl.flock(f.fileno(), fcntl.LOCK_EX)
            try:
                f.write(serialized)
                f.flush()
                os.fsync(f.fileno())
            finally:
                fcntl.flock(f.fileno(), fcntl.LOCK_UN)

        return item_id

    # -----------------------------------------------------------------------
    # CRUD: update()
    # -----------------------------------------------------------------------

    def update(
        self,
        item_id: str,
        set_fields: dict[str, Any] | None = None,
        add_fields: dict[str, list[Any]] | None = None,
        remove_fields: dict[str, list[Any]] | None = None,
    ) -> None:
        """Append an update op to an item.

        Raises:
            ItemNotFoundError: If the item doesn't exist.
            LockedFieldError: If an agent tries to modify a locked field.
            CorruptFileError: If the ops file is corrupt.
        """
        item_id = self._resolve_id(item_id)
        item_path = self.item_path(item_id)

        if not item_path.exists():
            raise ItemNotFoundError(f"Item not found: {item_id}")

        by, actor = resolve_actor(self._config.agent_usernames)

        # Check locked fields
        if by == "agent":
            ops = _parse_ops_file(item_path)
            compiled = compile_ops(ops, item_id)
            all_fields_touched: set[str] = set()
            if set_fields:
                all_fields_touched.update(set_fields.keys())
            if add_fields:
                all_fields_touched.update(add_fields.keys())
            if remove_fields:
                all_fields_touched.update(remove_fields.keys())

            locked = compiled.locked_fields
            # Cross-branch lock check
            locked = locked | self._cross_branch_locked_fields(item_path, item_id)
            for field_name in all_fields_touched:
                if field_name in locked:
                    raise LockedFieldError(
                        f"Field '{field_name}' on {item_id} is locked. "
                        f"Ask the human to unlock it."
                    )

        nonce = _make_nonce()
        now = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

        op_dict: dict[str, Any] = {
            "op": "update",
            "at": now,
            "by": by,
            "actor": actor,
            "clock": 0,  # placeholder
            "nonce": nonce,
            "set": set_fields or {},
        }
        if add_fields:
            op_dict["add"] = add_fields
        if remove_fields:
            op_dict["remove"] = remove_fields

        self._append_op(item_path, op_dict)

    # -----------------------------------------------------------------------
    # CRUD: discuss()
    # -----------------------------------------------------------------------

    def discuss(
        self,
        item_id: str,
        message: str,
        clear: bool = False,
        summarize: bool = False,
    ) -> None:
        """Append a discussion op to an item.

        Args:
            item_id: Item ID or prefix.
            message: Discussion message (ignored if clear=True).
            clear: If True, append a discuss_clear op (human-authority only).
            summarize: If True, append a discuss_summarize op.

        Raises:
            ItemNotFoundError: If item doesn't exist.
            HumanAuthorityError: If agent tries discuss_clear.
            LockedFieldError: If discussion is locked for agent.
            DiscussionRateLimitError: If daily rate limit exceeded.
        """
        item_id = self._resolve_id(item_id)
        item_path = self.item_path(item_id)

        if not item_path.exists():
            raise ItemNotFoundError(f"Item not found: {item_id}")

        by, actor = resolve_actor(self._config.agent_usernames)

        if clear:
            if by == "agent":
                raise HumanAuthorityError(
                    "discuss_clear requires human authority"
                )
            op_dict: dict[str, Any] = {
                "op": "discuss_clear",
                "at": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "by": by,
                "actor": actor,
                "clock": 0,
                "nonce": _make_nonce(),
            }
            self._append_op(item_path, op_dict)
            return

        # Check locked discussion field for agents
        if by == "agent":
            ops = _parse_ops_file(item_path)
            compiled = compile_ops(ops, item_id)
            locked = compiled.locked_fields | self._cross_branch_locked_fields(item_path, item_id)
            if "discussion" in locked:
                raise LockedFieldError(
                    f"Discussion on {item_id} is locked. "
                    f"Ask the human to unlock it."
                )

            # Check rate limit
            self._check_discussion_rate_limit(item_path, item_id)

        # Emit soft cap warning
        ops = _parse_ops_file(item_path)
        compiled = compile_ops(ops, item_id)
        if len(compiled.discussion) >= _DISCUSSION_SOFT_CAP:
            warnings.warn(
                f"Discussion on {item_id} has {len(compiled.discussion)} entries "
                f"(soft cap: {_DISCUSSION_SOFT_CAP}). Consider using --summarize.",
                stacklevel=2,
            )

        nonce = _make_nonce()
        now = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

        if summarize:
            op_dict = {
                "op": "discuss_summarize",
                "at": now,
                "by": by,
                "actor": actor,
                "clock": 0,
                "nonce": nonce,
                "message": message,
            }
        else:
            op_dict = {
                "op": "discuss",
                "at": now,
                "by": by,
                "actor": actor,
                "clock": 0,
                "nonce": nonce,
                "message": message,
            }

        self._append_op(item_path, op_dict)

    # -----------------------------------------------------------------------
    # CRUD: lock() / unlock()
    # -----------------------------------------------------------------------

    def lock(self, item_id: str, field_names: list[str]) -> None:
        """Lock fields on an item. Human-authority only.

        Raises:
            HumanAuthorityError: If called by an agent.
            ItemNotFoundError: If item doesn't exist.
        """
        item_id = self._resolve_id(item_id)
        item_path = self.item_path(item_id)

        if not item_path.exists():
            raise ItemNotFoundError(f"Item not found: {item_id}")

        by, actor = resolve_actor(self._config.agent_usernames)
        if by == "agent":
            raise HumanAuthorityError("lock requires human authority")

        op_dict: dict[str, Any] = {
            "op": "lock",
            "at": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "by": by,
            "actor": actor,
            "clock": 0,
            "nonce": _make_nonce(),
            "lock": field_names,
        }
        self._append_op(item_path, op_dict)

    def unlock(self, item_id: str, field_names: list[str]) -> None:
        """Unlock fields on an item. Human-authority only.

        Raises:
            HumanAuthorityError: If called by an agent.
            ItemNotFoundError: If item doesn't exist.
        """
        item_id = self._resolve_id(item_id)
        item_path = self.item_path(item_id)

        if not item_path.exists():
            raise ItemNotFoundError(f"Item not found: {item_id}")

        by, actor = resolve_actor(self._config.agent_usernames)
        if by == "agent":
            raise HumanAuthorityError("unlock requires human authority")

        op_dict: dict[str, Any] = {
            "op": "unlock",
            "at": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "by": by,
            "actor": actor,
            "clock": 0,
            "nonce": _make_nonce(),
            "unlock": field_names,
        }
        self._append_op(item_path, op_dict)

    # -----------------------------------------------------------------------
    # Internal: _append_op
    # -----------------------------------------------------------------------

    def _append_op(self, filepath: Path, op_dict: dict[str, Any]) -> None:
        """Append an op to a file with flock and Lamport clock.

        The critical section: acquire lock → compute clock → serialize → write → fsync → release.
        """
        with open(filepath, "a") as f:
            fcntl.flock(f.fileno(), fcntl.LOCK_EX)
            try:
                clock = _compute_lamport_clock(filepath, self._config.lamport_branches)
                op_dict["clock"] = clock
                serialized = _serialize_op(op_dict)
                f.write(serialized)
                f.flush()
                os.fsync(f.fileno())
            finally:
                fcntl.flock(f.fileno(), fcntl.LOCK_UN)

    # -----------------------------------------------------------------------
    # Internal: cross-branch lock check
    # -----------------------------------------------------------------------

    def _cross_branch_locked_fields(self, filepath: Path, item_id: str) -> set[str]:
        """Check locked fields across branches via git cat-file --batch.

        Returns the union of locked_fields from all branches.
        """
        locked: set[str] = set()
        git_dir = _find_git_dir(filepath)
        if git_dir is None:
            return locked

        try:
            rel_path = filepath.relative_to(git_dir.parent)
        except ValueError:
            return locked

        refs = []
        for branch in self._config.lamport_branches:
            refs.append(f"{branch}:{rel_path}")
        refs.append(f"HEAD:{rel_path}")

        if not refs:  # pragma: no cover — always True: HEAD ref added above
            return locked

        try:
            proc = subprocess.Popen(  # nosec B603, B607
                ["git", "cat-file", "--batch"],  # noqa: S607
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                cwd=str(git_dir.parent),
            )
            input_data = "\n".join(refs) + "\n"
            stdout, _ = proc.communicate(input=input_data.encode(), timeout=10)

            pos = 0
            output = stdout
            while pos < len(output):
                nl = output.find(b"\n", pos)
                if nl == -1:
                    break
                header = output[pos:nl].decode(errors="replace")
                pos = nl + 1

                if "missing" in header:
                    continue

                parts = header.split()
                if len(parts) < 3:
                    continue

                try:
                    size = int(parts[-1])
                except ValueError:
                    continue

                if pos + size > len(output):
                    break
                content = output[pos:pos + size]
                pos += size + 1

                ops = _parse_ops_bytes(content)
                if ops:
                    try:
                        compiled = compile_ops(ops, item_id)
                        locked |= compiled.locked_fields
                    except CorruptFileError:
                        pass

        except (subprocess.TimeoutExpired, OSError):
            pass

        return locked

    # -----------------------------------------------------------------------
    # Internal: similarity check
    # -----------------------------------------------------------------------

    def _check_similarity(
        self, new_id: str, new_simhash: int, new_data: dict[str, Any]
    ) -> None:
        """Check new item SimHash against existing items; warn if similar."""
        for path in self._list_item_files():
            existing_id = self._id_from_filename(path)
            if existing_id == new_id:  # pragma: no cover — defensive: add() checks first
                continue

            try:
                ops = _parse_ops_file(path)
                compile_ops(ops, existing_id)
            except CorruptFileError:
                continue

            # Skip if marked as not-duplicate
            if existing_id in new_data.get("not_duplicate_of", []):
                continue

            # Compute SimHash for existing item
            create_ops = [o for o in ops if o.get("op") == "create"]
            if not create_ops:  # pragma: no cover — compile_ops above ensures create exists
                continue
            existing_text = _item_text_for_simhash(create_ops[0].get("data", {}))
            existing_simhash = _compute_simhash(existing_text)

            distance = _hamming_distance(new_simhash, existing_simhash)
            if distance <= _SIMHASH_THRESHOLD:
                warnings.warn(
                    f"{new_id} is similar to existing {existing_id} "
                    f"(SimHash distance: {distance} bits). "
                    f"Creating anyway. Use 'update {new_id} --duplicate-of {existing_id}' "
                    f"to mark as duplicate, or 'update {new_id} --not-duplicate-of "
                    f"{existing_id}' to suppress.",
                    stacklevel=3,
                )

    # -----------------------------------------------------------------------
    # Internal: discussion rate limit
    # -----------------------------------------------------------------------

    def _check_discussion_rate_limit(self, filepath: Path, item_id: str) -> None:
        """Check discussion rate limit for an item (200k tokens/day)."""
        ops = _parse_ops_file(filepath)
        today = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d")
        tokens_today = 0.0

        for op_dict in ops:
            op_type = op_dict.get("op", "")
            if op_type in ("discuss", "discuss_summarize"):
                op_date = op_dict.get("at", "")[:10]
                if op_date == today:
                    msg = op_dict.get("message", "")
                    tokens_today += len(msg) / _CHARS_PER_TOKEN

        if tokens_today >= _DISCUSSION_DAILY_TOKEN_LIMIT:
            raise DiscussionRateLimitError(
                f"Discussion rate limit reached on {item_id} "
                f"({_DISCUSSION_DAILY_TOKEN_LIMIT:,} tokens today). "
                f"Further discussion deferred until tomorrow, "
                f"or run --summarize to reset."
            )

    # -----------------------------------------------------------------------
    # Prefix matching and aliases
    # -----------------------------------------------------------------------

    def _resolve_id(self, prefix: str) -> str:
        """Resolve an ID prefix or positional alias to a full item ID.

        Accepts:
        - Full ID: returned as-is if it exists
        - Prefix with kind: e.g. "INV-lus" matches "INV-lusab-..."
        - Prefix without kind: e.g. "lus" matches across all kinds
        - Positional alias: ":1" refers to first item from last list output

        Raises:
            ItemNotFoundError: If no items match.
            AmbiguousPrefixError: If multiple items match.
        """
        # Positional alias
        if prefix.startswith(":"):
            try:
                idx = int(prefix[1:]) - 1
            except ValueError:
                raise ItemNotFoundError(f"Invalid positional alias: {prefix}") from None
            if 0 <= idx < len(self._last_list):
                return self._last_list[idx]
            raise ItemNotFoundError(
                f"Positional alias {prefix} out of range "
                f"(last list had {len(self._last_list)} items)"
            )

        # Check for exact match first
        exact_path = self.item_path(prefix)
        if exact_path.exists():
            return prefix

        # Prefix match
        candidates: list[tuple[str, str]] = []
        for path in self._list_item_files():
            item_id = self._id_from_filename(path)
            if item_id.startswith(prefix) or (
                "-" not in prefix and any(
                    part.startswith(prefix)
                    for part in item_id.split("-", 1)[1:]
                )
            ):
                # Get title for error message
                try:
                    ops = _parse_ops_file(path)
                    creates = [o for o in ops if o.get("op") == "create"]
                    title = creates[0].get("data", {}).get("title", "") if creates else ""
                except CorruptFileError:
                    title = ""
                candidates.append((item_id, title))

        if len(candidates) == 1:
            return candidates[0][0]
        elif len(candidates) > 1:
            raise AmbiguousPrefixError(prefix, candidates)
        else:
            raise ItemNotFoundError(f"No items match prefix: {prefix}")

    # -----------------------------------------------------------------------
    # Queries: ready, list_items, children, ancestors
    # -----------------------------------------------------------------------

    def list_items(
        self,
        status: str | None = None,
        kind: str | None = None,
        tag: str | None = None,
    ) -> list[CompiledItem]:
        """List all items, optionally filtered and sorted.

        Sorted by (priority, topological before order, created_at).
        Updates the positional alias stash.
        """
        items = self._compile_all()

        # Apply filters
        if status is not None:
            items = [i for i in items if i.status == status]
        if kind is not None:
            items = [i for i in items if i.kind == kind]
        if tag is not None:
            items = [i for i in items if tag in i.tags]

        # Sort by (priority, created_at) — topological sort of before is complex,
        # using priority + created_at for now
        items.sort(key=lambda i: (i.priority, i.created_at))

        # Update positional alias stash
        self._last_list = [i.id for i in items]

        return items

    def ready(self) -> list[CompiledItem]:
        """Return items that are actionable and unblocked.

        An item is ready if:
        - Its status is in blocking_statuses
        - No unresolved item X has X.before containing this item's ID
          (i.e., no unresolved predecessors block it)
        - It has no non-empty duplicate_of
        - It has no cross_tier_conflict

        Before semantics: X.before = [Y] means "X blocks Y — finish X before Y."
        So Y is not ready until X is resolved.

        Sorted by (priority, created_at).
        """
        all_items = self._compile_all()

        # Build resolved set: items with status in resolved_statuses
        resolved_ids: set[str] = set()
        for item in all_items:
            if item.status in self._config.resolved_statuses:
                resolved_ids.add(item.id)

        # Build blocked set: for each item X with before: [Y, Z, ...],
        # Y and Z are blocked if X is not resolved.
        blocked_ids: set[str] = set()
        for item in all_items:
            if item.id not in resolved_ids:
                for target_id in item.before:
                    blocked_ids.add(target_id)

        blocking_set = set(self._config.blocking_statuses)
        ready_items: list[CompiledItem] = []

        for item in all_items:
            # Must be in blocking status
            if item.status not in blocking_set:
                continue

            # Must not be a duplicate
            if item.duplicate_of:
                continue

            # Must not have cross-tier conflict
            if item.cross_tier_conflict:
                continue

            # Must not be blocked by an unresolved predecessor
            if item.id in blocked_ids:
                continue

            ready_items.append(item)

        ready_items.sort(key=lambda i: (i.priority, i.created_at))

        # Update positional alias stash
        self._last_list = [i.id for i in ready_items]

        return ready_items

    def children(self, item_id: str) -> list[CompiledItem]:
        """Return direct children of the given item."""
        item_id = self._resolve_id(item_id)
        all_items = self._compile_all()
        return [i for i in all_items if i.parent == item_id]

    def ancestors(self, item_id: str) -> list[CompiledItem]:
        """Return all ancestors of the given item (parent chain to root)."""
        item_id = self._resolve_id(item_id)
        all_items = self._compile_all()
        item_map = {i.id: i for i in all_items}

        result: list[CompiledItem] = []
        current_id = item_id

        # Walk up the parent chain
        visited: set[str] = set()
        while current_id in item_map:
            item = item_map[current_id]
            if item.parent is None or item.parent in visited:
                break
            visited.add(item.parent)
            if item.parent in item_map:
                result.append(item_map[item.parent])
                current_id = item.parent
            else:
                break

        return result

    def get(self, item_id: str) -> CompiledItem:
        """Get a single compiled item by ID or prefix.

        Raises:
            ItemNotFoundError: If item not found.
            AmbiguousPrefixError: If prefix is ambiguous.
        """
        item_id = self._resolve_id(item_id)
        item_path = self.item_path(item_id)
        if not item_path.exists():
            raise ItemNotFoundError(f"Item not found: {item_id}")
        ops = _parse_ops_file(item_path)
        return compile_ops(ops, item_id)

    def _compile_all(self) -> list[CompiledItem]:
        """Compile all items in the store."""
        items: list[CompiledItem] = []
        for path in self._list_item_files():
            item_id = self._id_from_filename(path)
            try:
                ops = _parse_ops_file(path)
                item = compile_ops(ops, item_id)
                items.append(item)
            except CorruptFileError:
                continue
        return items

    # -----------------------------------------------------------------------
    # before-cycle detection
    # -----------------------------------------------------------------------

    def check_before_cycles(self) -> list[list[str]]:
        """Detect cycles in before links across all items.

        Returns a list of cycles found, where each cycle is a list of item IDs.
        """
        all_items = self._compile_all()

        # Build adjacency: item → items it blocks (via before)
        # If X has before: [Y], then X blocks Y (Y must wait for X)
        # adjacency: X → [Y] means "X must be done before Y"
        blocked_by: dict[str, list[str]] = defaultdict(list)
        for item in all_items:
            for before_id in item.before:
                blocked_by[before_id].append(item.id)

        # DFS cycle detection
        WHITE, GRAY, BLACK = 0, 1, 2
        color: dict[str, int] = defaultdict(int)
        cycles: list[list[str]] = []

        def dfs(node: str, path: list[str]) -> None:
            color[node] = GRAY
            path.append(node)
            for neighbor in blocked_by.get(node, []):
                if color[neighbor] == GRAY:
                    # Found cycle
                    cycle_start = path.index(neighbor)
                    cycles.append(path[cycle_start:] + [neighbor])
                elif color[neighbor] == WHITE:
                    dfs(neighbor, path)
            path.pop()
            color[node] = BLACK

        item_ids = {i.id for i in all_items}
        for item_id in item_ids:
            if color[item_id] == WHITE:
                dfs(item_id, [])

        return cycles
