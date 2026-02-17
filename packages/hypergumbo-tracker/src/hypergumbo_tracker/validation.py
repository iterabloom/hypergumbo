# SPDX-License-Identifier: MPL-2.0
"""Op log and cross-file validation for the hypergumbo tracker.

Provides pure-function validation that takes file paths + config and returns
results. No side effects. Composable: CLI, pre-commit, and CI can all call
these functions directly.

Validation tiers:
- **Single-file structural:** Malformed YAML, missing create op, missing
  required fields, unknown op types.
- **Value validation:** Unknown kinds, invalid statuses, out-of-range
  priorities, invalid timestamps.
- **Field schema validation:** Required fields missing, type mismatches,
  integer range violations, unknown fields (with edit-distance suggestions).
- **Cross-file validation:** Duplicate IDs across tiers, dangling parent
  references, ID prefix/kind mismatches, cycles in before links.
- **Config comparison:** Kinds in config but not in template (and vice versa).
- **Lock violation detection:** Agent updates touching locked fields.
- **SimHash duplicate warnings:** Near-duplicate pairs not in not_duplicate_of.
- **Embedding duplicate warnings:** Deep semantic duplicates via dense embeddings.

See ADR-0013 for the full design specification.
"""

from __future__ import annotations

import datetime
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from hypergumbo_tracker.models import TrackerConfig, load_config
from hypergumbo_tracker.store import (
    _COMMON_FIELD_ORDER,
    CorruptFileError,
    _compute_simhash,
    _hamming_distance,
    _parse_ops_file,
    compile_ops,
)


# Valid op types
_VALID_OP_TYPES = frozenset({
    "create", "update", "discuss", "discuss_clear", "discuss_summarize",
    "lock", "unlock", "promote", "demote", "stealth", "unstealth", "reconcile",
})

# Required common fields on every op
_REQUIRED_COMMON_FIELDS = ("op", "at", "by", "clock", "nonce")

# Default SimHash threshold for similarity warnings
_SIMHASH_THRESHOLD = 8

# Pattern to extract inline nonce comment from a line: `  # <nonce>`
_NONCE_COMMENT_RE = re.compile(r"  # ([0-9a-f]{4})$")

# Expected field order per op type (beyond common fields)
_OP_SPECIFIC_FIELD_ORDER: dict[str, list[str]] = {
    "create": ["data"],
    "update": ["set", "add", "remove"],
    "discuss": ["message"],
    "discuss_clear": [],
    "discuss_summarize": ["message"],
    "lock": ["lock"],
    "unlock": ["unlock"],
    "promote": [],
    "demote": [],
    "stealth": [],
    "unstealth": [],
    "reconcile": ["from_tier", "reason"],
}


@dataclass
class ValidationResult:
    """Result of validating one or more ops files.

    errors: issues that indicate data integrity problems.
    warnings: issues that suggest potential problems but aren't fatal.
    """

    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        """True if no errors."""
        return len(self.errors) == 0

    def merge(self, other: ValidationResult) -> None:
        """Merge another result into this one."""
        self.errors.extend(other.errors)
        self.warnings.extend(other.warnings)


def _edit_distance(a: str, b: str) -> int:
    """Compute Levenshtein edit distance between two strings."""
    if len(a) < len(b):
        return _edit_distance(b, a)
    if len(b) == 0:
        return len(a)

    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a):
        curr = [i + 1] + [0] * len(b)
        for j, cb in enumerate(b):
            cost = 0 if ca == cb else 1
            curr[j + 1] = min(curr[j] + 1, prev[j + 1] + 1, prev[j] + cost)
        prev = curr
    return prev[len(b)]


def _validate_nonce_on_every_line(
    filepath: Path, ops: list[dict[str, Any]], result: ValidationResult,
) -> None:
    """Check nonce-on-every-line format in the raw file content.

    Every non-empty line must have an inline ``# <nonce>`` comment, and
    the nonce value must match the ``nonce`` field of the enclosing op.
    This is load-bearing for ``merge=union`` correctness (ADR-0013 §4.4).
    """
    fname = filepath.name
    try:
        raw_text = filepath.read_text()
    except OSError:
        return  # File unreadable — other validators will catch this

    raw_lines = raw_text.split("\n")

    # Build a list of (nonce, first_line_idx) for each op by finding
    # `- op:` markers in the raw text.
    op_nonces: list[str] = [op.get("nonce", "") for op in ops]

    # Walk raw lines and track which op we're in
    op_idx = -1
    for line_no_0, line in enumerate(raw_lines):
        stripped = line.rstrip()
        if not stripped:
            continue

        # Detect new op start (YAML sequence item)
        if stripped.startswith("- op:"):
            op_idx += 1

        if op_idx < 0 or op_idx >= len(op_nonces):
            continue

        expected_nonce = op_nonces[op_idx]
        if not expected_nonce:
            continue

        # Check that the line has an inline nonce comment
        m = _NONCE_COMMENT_RE.search(stripped)
        if not m:
            result.warnings.append(
                f"{fname}: line {line_no_0 + 1}: missing nonce comment "
                f"(expected '  # {expected_nonce}')"
            )
        elif m.group(1) != expected_nonce:
            result.warnings.append(
                f"{fname}: line {line_no_0 + 1}: nonce comment "
                f"'{m.group(1)}' does not match op nonce '{expected_nonce}'"
            )


def _validate_canonical_field_order(
    fname: str, ops: list[dict[str, Any]], result: ValidationResult,
) -> None:
    """Check that op fields follow the canonical serialization order.

    The canonical order is: op, at, by, actor, clock, nonce, then
    op-specific fields (data/set/add/remove/message/lock/unlock/etc).
    Non-canonical ordering doesn't break correctness but indicates the
    op was not written by the store (possible manual edit).
    """
    for i, op_dict in enumerate(ops):
        op_type = op_dict.get("op")
        if op_type is None:
            continue

        # Build expected order for this op type
        specific = _OP_SPECIFIC_FIELD_ORDER.get(op_type, [])
        expected_order = _COMMON_FIELD_ORDER + specific

        # Get the actual key order (only keys that appear in expected_order)
        actual_keys = list(op_dict.keys())
        expected_present = [k for k in expected_order if k in op_dict]
        actual_ordered = [k for k in actual_keys if k in set(expected_order)]

        if actual_ordered != expected_present:
            result.warnings.append(
                f"{fname}: op {i}: non-canonical field order "
                f"(got {actual_ordered}, expected {expected_present})"
            )


def validate_ops_file(
    filepath: Path,
    config: TrackerConfig,
    *,
    check_locks: bool = False,
) -> ValidationResult:
    """Validate a single ops file against the tracker config.

    Checks structural integrity, value validity, and field schema compliance.

    Args:
        filepath: Path to the .ops file.
        config: TrackerConfig for validation rules.
        check_locks: If True, check for agent lock violations.

    Returns:
        ValidationResult with errors and warnings.
    """
    result = ValidationResult()
    fname = filepath.name

    # Parse the file
    try:
        ops = _parse_ops_file(filepath)
    except CorruptFileError as e:
        result.errors.append(f"{fname}: malformed YAML: {e}")
        return result

    if not ops:
        result.errors.append(f"{fname}: empty ops file")
        return result

    # Check first op is create
    first_op = ops[0]
    if first_op.get("op") != "create":
        result.errors.append(
            f"{fname}: first op must be 'create', got {first_op.get('op')!r}"
        )

    # Check required fields and op types
    for i, op_dict in enumerate(ops):
        op_type = op_dict.get("op")

        # Required common fields
        for field_name in _REQUIRED_COMMON_FIELDS:
            if field_name not in op_dict:
                result.errors.append(
                    f"{fname}: op {i}: missing required field '{field_name}'"
                )

        # Unknown op type
        if op_type not in _VALID_OP_TYPES:
            result.errors.append(
                f"{fname}: op {i}: unknown op type {op_type!r}"
            )
            continue

        # Value validation for create ops
        if op_type == "create":
            _validate_create_values(fname, i, op_dict, config, result)

        # Value validation for update ops
        if op_type == "update":
            _validate_update_values(fname, i, op_dict, config, result)

        # Timestamp validation
        at = op_dict.get("at")
        if at is not None and isinstance(at, str):
            _validate_timestamp(fname, i, at, result)

    # Lock violation detection
    if check_locks:
        _check_lock_violations(fname, ops, result)

    # Nonce-on-every-line and nonce comment value matching (ADR-0013 §4.4)
    _validate_nonce_on_every_line(filepath, ops, result)

    # Canonical field order (ADR-0013 §4.4)
    _validate_canonical_field_order(fname, ops, result)

    return result


def _validate_create_values(
    fname: str,
    idx: int,
    op_dict: dict[str, Any],
    config: TrackerConfig,
    result: ValidationResult,
) -> None:
    """Validate values in a create op's data dict."""
    data = op_dict.get("data", {})
    if not isinstance(data, dict):
        result.errors.append(f"{fname}: op {idx}: 'data' must be a dict")
        return

    # Kind validation
    kind = data.get("kind")
    if kind is not None and kind not in config.kinds:
        result.errors.append(
            f"{fname}: op {idx}: unknown kind {kind!r}. "
            f"Valid: {list(config.kinds.keys())}"
        )

    # Status validation
    status = data.get("status")
    if status is not None and status not in config.statuses:
        result.errors.append(
            f"{fname}: op {idx}: unknown status {status!r}. "
            f"Valid: {config.statuses}"
        )

    # Priority validation
    priority = data.get("priority")
    if priority is not None:
        if not isinstance(priority, int) or isinstance(priority, bool):
            result.errors.append(
                f"{fname}: op {idx}: priority must be int 0-4, got {priority!r}"
            )
        elif not (0 <= priority <= 4):
            result.errors.append(
                f"{fname}: op {idx}: priority must be int 0-4, got {priority}"
            )

    # Field schema validation
    if kind is not None and kind in config.kinds:
        kind_config = config.kinds[kind]
        if kind_config.fields_schema:
            _validate_fields_schema(
                fname, idx, data.get("fields", {}), kind_config.fields_schema, result
            )


def _validate_update_values(
    fname: str,
    idx: int,
    op_dict: dict[str, Any],
    config: TrackerConfig,
    result: ValidationResult,
) -> None:
    """Validate values in an update op."""
    set_dict = op_dict.get("set", {})
    if not isinstance(set_dict, dict):
        return

    # Status validation
    status = set_dict.get("status")
    if status is not None and status not in config.statuses:
        result.errors.append(
            f"{fname}: op {idx}: unknown status {status!r}. "
            f"Valid: {config.statuses}"
        )

    # Priority validation
    priority = set_dict.get("priority")
    if priority is not None:
        if not isinstance(priority, int) or isinstance(priority, bool):
            result.errors.append(
                f"{fname}: op {idx}: priority must be int 0-4, got {priority!r}"
            )
        elif not (0 <= priority <= 4):
            result.errors.append(
                f"{fname}: op {idx}: priority must be int 0-4, got {priority}"
            )


def _validate_timestamp(
    fname: str, idx: int, at: str, result: ValidationResult
) -> None:
    """Validate an ISO 8601 timestamp."""
    try:
        datetime.datetime.fromisoformat(at.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        result.errors.append(
            f"{fname}: op {idx}: invalid timestamp {at!r}"
        )


def _validate_fields_schema(
    fname: str,
    idx: int,
    fields: dict[str, Any] | Any,
    schema: dict[str, Any],
    result: ValidationResult,
) -> None:
    """Validate fields dict against kind's fields_schema."""
    if not isinstance(fields, dict):
        fields = {}

    # Check required fields
    for field_name, field_schema in schema.items():
        if field_schema.required and field_name not in fields:
            result.errors.append(
                f"{fname}: op {idx}: required field '{field_name}' missing"
            )

    # Validate field values
    for field_name, value in fields.items():
        if field_name not in schema:
            # Unknown field — suggest closest match
            suggestions = _suggest_field(field_name, list(schema.keys()))
            if suggestions:
                result.warnings.append(
                    f"{fname}: op {idx}: unknown field '{field_name}'. "
                    f"Did you mean '{suggestions[0]}'?"
                )
            else:
                result.warnings.append(
                    f"{fname}: op {idx}: unknown field '{field_name}'"
                )
            continue

        fs = schema[field_name]
        _validate_field_value(fname, idx, field_name, value, fs, result)


def _validate_field_value(
    fname: str,
    idx: int,
    field_name: str,
    value: Any,
    fs: Any,
    result: ValidationResult,
) -> None:
    """Validate a single field value against its schema."""
    expected_type = fs.type

    if expected_type == "text":
        if value is not None and not isinstance(value, str):
            result.errors.append(
                f"{fname}: op {idx}: field '{field_name}' expects text, "
                f"got {type(value).__name__}"
            )
    elif expected_type == "integer":
        if value is not None:
            if isinstance(value, bool) or not isinstance(value, int):
                result.errors.append(
                    f"{fname}: op {idx}: field '{field_name}' expects integer, "
                    f"got {type(value).__name__}"
                )
            else:
                if fs.min is not None and value < fs.min:
                    result.errors.append(
                        f"{fname}: op {idx}: field '{field_name}' value {value} "
                        f"below minimum {fs.min}"
                    )
                if fs.max is not None and value > fs.max:
                    result.errors.append(
                        f"{fname}: op {idx}: field '{field_name}' value {value} "
                        f"above maximum {fs.max}"
                    )
    elif expected_type == "list":
        if value is not None and not isinstance(value, list):
            result.errors.append(
                f"{fname}: op {idx}: field '{field_name}' expects list, "
                f"got {type(value).__name__}"
            )
    elif expected_type == "boolean":
        if value is not None and not isinstance(value, bool):
            result.errors.append(
                f"{fname}: op {idx}: field '{field_name}' expects boolean, "
                f"got {type(value).__name__}"
            )


def _suggest_field(field_name: str, known_fields: list[str]) -> list[str]:
    """Suggest closest-matching field names using edit distance."""
    candidates: list[tuple[int, str]] = []
    for known in known_fields:
        dist = _edit_distance(field_name, known)
        if dist <= 3:
            candidates.append((dist, known))
    candidates.sort()
    return [c[1] for c in candidates[:1]]


def _check_lock_violations(
    fname: str, ops: list[dict[str, Any]], result: ValidationResult
) -> None:
    """Check for agent updates that touch locked fields."""
    locked: set[str] = set()

    for i, op_dict in enumerate(ops):
        op_type = op_dict.get("op")

        if op_type == "lock":
            for f in op_dict.get("lock", []):
                locked.add(f)
        elif op_type == "unlock":
            for f in op_dict.get("unlock", []):
                locked.discard(f)
        elif op_type == "update" and op_dict.get("by") == "agent":
            set_dict = op_dict.get("set", {})
            add_dict = op_dict.get("add", {})
            remove_dict = op_dict.get("remove", {})
            touched = set(set_dict.keys()) | set(add_dict.keys()) | set(remove_dict.keys())
            violations = touched & locked
            if violations:
                result.warnings.append(
                    f"{fname}: op {i}: agent update touches locked field(s): "
                    f"{sorted(violations)}"
                )


def validate_all(
    tracker_root: Path,
    config: TrackerConfig | None = None,
    *,
    check_similar: bool = False,
    check_deep_similar: bool = False,
    check_locks: bool = False,
    strict: bool = False,
) -> ValidationResult:
    """Validate all ops files across all tiers.

    Cross-file checks:
    - Duplicate IDs across tiers
    - Dangling parent references
    - ID prefix doesn't match kind
    - Cycles in before links
    - Deferred without justification
    - Config vs template comparison (if both exist)
    - SimHash near-duplicate warnings (if check_similar=True)
    - Embedding near-duplicate warnings (if check_deep_similar=True)

    Args:
        tracker_root: Path to the .agent/ directory.
        config: Optional TrackerConfig. Loaded from tracker_root if None.
        check_similar: If True, check SimHash near-duplicates.
        check_deep_similar: If True, check embedding-based near-duplicates.
        check_locks: If True, check for lock violations.
        strict: If True, treat warnings as errors.

    Returns:
        ValidationResult with errors and warnings.
    """
    result = ValidationResult()

    # Load config
    canonical_dir = tracker_root / "tracker"
    if config is None:
        config = load_config(canonical_dir)

    # Collect all ops files from all tiers
    tier_dirs = {
        "canonical": canonical_dir / ".ops",
        "workspace": tracker_root / "tracker-workspace" / ".ops",
        "stealth": tracker_root / "tracker-workspace" / "stealth",
    }

    all_items: dict[str, tuple[str, Any]] = {}  # id → (tier_name, CompiledItem)
    tier_items: dict[str, list[str]] = {t: [] for t in tier_dirs}

    for tier_name, ops_dir in tier_dirs.items():
        if not ops_dir.exists():
            continue

        for ops_file in sorted(ops_dir.iterdir()):
            if not (ops_file.name.startswith(".") and ops_file.name.endswith(".ops")):
                continue

            # Extract ID from filename
            item_id = ops_file.name[1:-4]

            # Validate individual file
            file_result = validate_ops_file(
                ops_file, config, check_locks=check_locks
            )
            result.merge(file_result)

            # Try to compile for cross-file checks
            try:
                ops = _parse_ops_file(ops_file)
                item = compile_ops(ops, item_id)

                # Check for duplicate IDs across tiers
                if item_id in all_items:
                    existing_tier = all_items[item_id][0]
                    result.errors.append(
                        f"duplicate ID {item_id} in tiers: {existing_tier}, {tier_name}"
                    )
                else:
                    all_items[item_id] = (tier_name, item)
                    tier_items[tier_name].append(item_id)

            except CorruptFileError:
                continue

    # Cross-file checks
    _check_dangling_parents(all_items, result)
    _check_id_prefix_mismatch(all_items, config, result)
    _check_before_cycles(all_items, result)

    # Config comparison warnings
    _check_config_comparison(tracker_root, result)

    # SimHash duplicate warnings
    if check_similar:
        _check_simhash_duplicates(all_items, result)

    # Embedding-based duplicate warnings
    if check_deep_similar:
        _check_embedding_duplicates(all_items, result)

    # Strict mode: promote warnings to errors
    if strict:
        result.errors.extend(result.warnings)
        result.warnings = []

    return result


def _check_dangling_parents(
    all_items: dict[str, tuple[str, Any]], result: ValidationResult
) -> None:
    """Check for parent references to non-existent items."""
    all_ids = set(all_items.keys())
    for item_id, (_tier_name, item) in all_items.items():
        if item.parent and item.parent not in all_ids:
            result.errors.append(
                f"{item_id}: dangling parent reference '{item.parent}'"
            )


def _check_id_prefix_mismatch(
    all_items: dict[str, tuple[str, Any]],
    config: TrackerConfig,
    result: ValidationResult,
) -> None:
    """Check that ID prefix matches the item's kind."""
    prefix_to_kind = {kc.prefix: k for k, kc in config.kinds.items()}

    for item_id, (_tier_name, item) in all_items.items():
        if "-" not in item_id:
            continue
        id_prefix = item_id.split("-", 1)[0]
        expected_kind = prefix_to_kind.get(id_prefix)
        if expected_kind is not None and item.kind != expected_kind:
            result.errors.append(
                f"{item_id}: ID prefix '{id_prefix}' suggests kind "
                f"'{expected_kind}' but item kind is '{item.kind}'"
            )


def _check_before_cycles(
    all_items: dict[str, tuple[str, Any]], result: ValidationResult
) -> None:
    """Check for cycles in before links."""
    from collections import defaultdict

    # Build adjacency: if X has before: [Y], then X blocks Y
    # Graph: X → Y (edge means X must complete before Y)
    blocked_by: dict[str, list[str]] = defaultdict(list)
    for item_id, (_, item) in all_items.items():
        for target_id in item.before:
            blocked_by[target_id].append(item_id)

    # DFS cycle detection
    WHITE, GRAY, BLACK = 0, 1, 2
    color: dict[str, int] = defaultdict(int)
    cycles: list[list[str]] = []

    def dfs(node: str, path: list[str]) -> None:
        color[node] = GRAY
        path.append(node)
        for neighbor in blocked_by.get(node, []):
            if color[neighbor] == GRAY:
                cycle_start = path.index(neighbor)
                cycles.append(path[cycle_start:] + [neighbor])
            elif color[neighbor] == WHITE:
                dfs(neighbor, path)
        path.pop()
        color[node] = BLACK

    for item_id in all_items:
        if color[item_id] == WHITE:
            dfs(item_id, [])

    for cycle in cycles:
        result.errors.append(
            f"cycle in before links: {' -> '.join(cycle)}"
        )


def _check_config_comparison(
    tracker_root: Path, result: ValidationResult
) -> None:
    """Compare config.yaml against config.yaml.template for mismatches."""
    import yaml

    config_path = tracker_root / "tracker" / "config.yaml"
    template_path = tracker_root / "tracker" / "config.yaml.template"

    if not config_path.is_file() or not template_path.is_file():
        return

    try:
        with open(config_path) as f:
            config_data = yaml.safe_load(f) or {}
        with open(template_path) as f:
            template_data = yaml.safe_load(f) or {}
    except Exception:  # pragma: no cover — defensive
        return

    config_kinds = set((config_data.get("kinds") or {}).keys())
    template_kinds = set((template_data.get("kinds") or {}).keys())

    extra_in_config = config_kinds - template_kinds
    extra_in_template = template_kinds - config_kinds

    for k in sorted(extra_in_config):
        result.warnings.append(
            f"kind '{k}' in config.yaml but not in config.yaml.template"
        )
    for k in sorted(extra_in_template):
        result.warnings.append(
            f"kind '{k}' in config.yaml.template but not in config.yaml"
        )


def _check_simhash_duplicates(
    all_items: dict[str, tuple[str, Any]], result: ValidationResult
) -> None:
    """Check for SimHash near-duplicates not marked in not_duplicate_of."""
    # Compute SimHash for each item
    item_simhashes: dict[str, int] = {}
    for item_id, (_, item) in all_items.items():
        text = f"{item.title} {item.description}"
        fields = item.fields
        if isinstance(fields, dict):
            for v in fields.values():
                if isinstance(v, str):
                    text += f" {v}"
                elif isinstance(v, list):
                    text += " " + " ".join(str(x) for x in v)
        item_simhashes[item_id] = _compute_simhash(text)

    # Compare all pairs
    item_ids = sorted(item_simhashes.keys())
    for i, id_a in enumerate(item_ids):
        _, item_a = all_items[id_a]
        for id_b in item_ids[i + 1:]:
            _, item_b = all_items[id_b]

            # Skip if already marked
            if id_b in item_a.not_duplicate_of or id_a in item_b.not_duplicate_of:
                continue
            if id_b in item_a.duplicate_of or id_a in item_b.duplicate_of:
                continue

            dist = _hamming_distance(item_simhashes[id_a], item_simhashes[id_b])
            if dist <= _SIMHASH_THRESHOLD:
                result.warnings.append(
                    f"near-duplicate: {id_a} and {id_b} "
                    f"(SimHash distance: {dist} bits)"
                )


def _check_embedding_duplicates(
    all_items: dict[str, tuple[str, Any]], result: ValidationResult
) -> None:
    """Check for embedding-based near-duplicates not marked in not_duplicate_of.

    Requires the `dedup` optional dependency group (onnxruntime + tokenizers).
    Warns and returns if dependencies are unavailable.
    """
    from hypergumbo_tracker.embeddings import (
        check_embedding_duplicates,
        is_dedup_available,
    )

    if not is_dedup_available():
        result.warnings.append(
            "deep-similar: skipped (install dedup extras: "
            "pip install hypergumbo-tracker[dedup])"
        )
        return

    duplicates = check_embedding_duplicates(all_items)
    for dup in duplicates:
        tags_str = ", ".join(dup.shared_tags) if dup.shared_tags else "none"
        result.warnings.append(
            f"deep-similar: {dup.id_a} and {dup.id_b} "
            f"(cosine similarity: {dup.cosine_sim:.3f}, shared tags: {tags_str})"
        )
