# SPDX-License-Identifier: MPL-2.0
"""Migration from markdown governance files to YAML tracker ops.

Converts `.agent/invariant-ledger.md` (INV + META entries) and
`work_items.md` (categorized backlog items) into structured YAML ops files
suitable for the tracker's append-only op-log storage.

Design decisions:
- **Manual op construction (not Store.add()):** Store.add() uses live
  os.getuid() for actor resolution and live timestamps, which breaks
  idempotency. Migration builds create op dicts directly and uses
  store._compute_id() for deterministic content-hash IDs and
  store._serialize_op() for YAML serialization with nonce-on-every-line.
- **INV/META → canonical, WI → workspace:** Invariants are institutional
  memory (shared). Work items are actionable backlog (fork-local).
- **Idempotency via content-hash IDs and skip-existing:** Same data →
  same ID. If the ops file already exists, skip (don't overwrite).
  Re-running migration never creates duplicates.
- **Random nonces:** Each op gets a fresh random nonce for merge=union
  safety — globally unique nonces prevent line deduplication when two
  agents migrate concurrently on different branches.

See ADR-0013 for the full design specification.
"""

from __future__ import annotations

import re
import textwrap
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from hypergumbo_tracker.models import TrackerConfig, load_config
from hypergumbo_tracker.store import _compute_id, _make_nonce, _serialize_op


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------


@dataclass
class ParsedItem:
    """Intermediate representation for a parsed governance item.

    Holds the parsed content from markdown in a structured form before
    conversion to tracker ops.
    """

    source_id: str
    kind: str
    title: str
    status: str
    priority: int
    description: str
    fields: dict[str, Any]
    tags: list[str]
    pr_ref: str | None
    justification: str | None
    parent_source_id: str | None


@dataclass
class MigrationResult:
    """Result summary from a migration run.

    Provides counts, errors, and the mapping from source IDs (e.g. INV-001)
    to proquint tracker IDs.
    """

    items_created: int = 0
    items_skipped: int = 0
    items_by_kind: dict[str, int] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)
    id_map: dict[str, str] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Phase 1: Pure functions
# ---------------------------------------------------------------------------


def normalize_status(raw: str) -> str:
    """Map a raw markdown status string to a normalized tracker status.

    Handles emoji prefixes (✅, ⬛), parenthetical notes, percentage values,
    and the various status keywords used in the governance files.

    Returns one of: done, wont_do, todo_hard, in_progress, todo_soft.
    Unknown statuses default to todo_hard.
    """
    s = raw.strip()

    # Strip emoji prefixes
    s = re.sub(r"^[✅⬛]\s*", "", s)

    # Strip parenthetical suffixes for FIXED
    s_upper = s.upper()

    if s_upper.startswith("FIXED"):
        return "done"
    if "WON'T DO" in s_upper or "WONT DO" in s_upper:
        return "wont_do"
    if s_upper == "UNFIXED":
        return "todo_hard"
    if s_upper.startswith("PARTIALLY ADDRESSED"):
        return "in_progress"
    if s_upper == "TBD":
        return "todo_hard"
    if s_upper == "DONE":
        return "done"
    if s_upper == "DEFERRED":
        return "todo_soft"
    if s_upper == "TODO!":
        return "todo_hard"
    if s_upper == "TODO":
        return "todo_soft"

    # Percentage values
    pct_match = re.match(r"(\d+)%", s)
    if pct_match:
        pct = int(pct_match.group(1))
        return "done" if pct >= 100 else "in_progress"

    return "todo_hard"


def assign_priority(status: str) -> int:
    """Map a normalized status to a priority level (0-4).

    Priority 0 is highest urgency (todo_hard), 4 is lowest (done/wont_do).
    """
    if status == "todo_hard":
        return 0
    if status in ("todo_soft", "in_progress"):
        return 1
    # done, wont_do
    return 4


def category_to_tag(category: str) -> str:
    """Convert a markdown section header to a normalized tag.

    Strips parenthetical suffixes, lowercases, replaces non-alphanumeric
    chars with underscores, collapses runs of underscores, and strips
    leading/trailing underscores.
    """
    if not category:
        return ""

    # Strip parenthetical suffixes: "From INV-011 (migrated from ...)" → "From INV-011"
    s = re.sub(r"\s*\([^)]*\)\s*$", "", category)

    # Replace hyphens and non-alphanumeric with underscores
    s = s.lower()
    s = re.sub(r"[^a-z0-9]+", "_", s)

    # Collapse runs and strip edges
    s = re.sub(r"_+", "_", s)
    s = s.strip("_")

    return s


# ---------------------------------------------------------------------------
# Config template
# ---------------------------------------------------------------------------

_CONFIG_TEMPLATE = textwrap.dedent("""\
    kinds:
      invariant:
        prefix: INV
        description: "Discovered invariant with root cause analysis"
        fields_schema:
          statement:
            type: text
            required: true
          root_cause:
            type: text
            required: true
          fix:
            type: text
          verification:
            type: text
          regression_tests:
            type: list
      meta_invariant:
        prefix: META
        description: "A meta-invariant tracking cross-language coverage"
        fields_schema:
          statement:
            type: text
            required: true
      work_item:
        prefix: WI
        description: "A work item"
    statuses:
      - todo_hard
      - todo_soft
      - in_progress
      - done
      - wont_do
    stop_hook:
      blocking_statuses:
        - todo_hard
        - todo_soft
      resolved_statuses:
        - done
        - wont_do
    well_known_tags:
      - developer_experience
      - cross_language_linkers
      - extends_edge_disambiguation
      - bakeoff_infrastructure
      - analysis_quality
      - language_additions
      - ci_infrastructure
    actor_resolution:
      agent_usernames:
        - "*_agent"
    lamport_branches:
      - dev
      - main
""")


def write_config_template(tracker_dir: Path) -> None:
    """Write config.yaml.template and config.yaml to a tracker tier directory.

    config.yaml.template is the tracked (shared) config.
    config.yaml is created with the same content for immediate use.
    Neither file is overwritten if it already exists.
    config.yaml is set read-only (0444) to enforce the governance boundary.
    """
    from hypergumbo_tracker.setup import config_lock

    template_path = tracker_dir / "config.yaml.template"
    config_path = tracker_dir / "config.yaml"

    if not template_path.exists():
        template_path.write_text(_CONFIG_TEMPLATE)

    if not config_path.exists():
        config_path.write_text(_CONFIG_TEMPLATE)
        config_lock(config_path)


# ---------------------------------------------------------------------------
# Phase 2: Parsers
# ---------------------------------------------------------------------------


# Regex for INV/META headers
_INV_HEADER_RE = re.compile(r"^#{2,3}\s+(INV-\d+):\s+(.+)$")
_META_HEADER_RE = re.compile(r"^#{2,3}\s+(META-\d+):\s+(.+)$")

# Field extraction: "- **FieldName:** value"
_FIELD_RE = re.compile(r"^-\s+\*\*([^*]+):\*\*\s*(.*)")

# Blockquote for META statement
_BLOCKQUOTE_RE = re.compile(r'^>\s+"(.+)"')

# INV fields that map to item.fields
_INV_FIELD_MAP = {
    "Statement": "statement",
    "Root cause": "root_cause",
    "Fix": "fix",
    "Verification": "verification",
}

# INV fields that go into description
_INV_DESCRIPTION_FIELDS = frozenset({
    "Limitation",
    "Trade-offs",
    "Discovery",
    "Architecture decision",
    "Impact",
    "Resolution",
    "Enhanced fix",
    "Enhanced fix (v2)",
    "Original fix",
    "Fix 2",
    "Related",
})

# PR ref pattern for work items
_PR_REF_RE = re.compile(r"\(([^)]*PR[^)]*)\)")


def parse_invariant_ledger(content: str) -> list[ParsedItem]:
    """Parse the invariant ledger markdown into ParsedItem objects.

    Extracts INV-NNN (invariant) and META-NNN (meta_invariant) entries.
    Skips template entries (INV-XXX, META-00X).

    Uses a line-by-line state machine:
    - `## INV-NNN: Title` → start INV entry
    - `### META-NNN: Title` or `## META-NNN: Title` → start META entry
    - `> "statement"` → extract META statement
    - `- **FieldName:** value` → extract field
    - Continuation lines → append to current field
    - `---` or next `##` → finalize entry
    """
    if not content.strip():
        return []

    items: list[ParsedItem] = []
    lines = content.split("\n")

    current: dict[str, Any] | None = None
    current_field_key: str | None = None

    def _finalize() -> None:
        nonlocal current, current_field_key
        if current is None:
            return
        current_field_key = None

        raw_status = current.get("raw_status", "")
        status = normalize_status(raw_status)
        priority = assign_priority(status)

        # Build description from description fields
        desc_parts = current.get("description_parts", [])
        description = "\n\n".join(desc_parts) if desc_parts else ""

        # Build regression_tests list
        regression_tests = current.get("regression_tests", [])
        fields = dict(current.get("fields", {}))
        if regression_tests:
            fields["regression_tests"] = regression_tests

        items.append(ParsedItem(
            source_id=current["source_id"],
            kind=current["kind"],
            title=current["title"],
            status=status,
            priority=priority,
            description=description,
            fields=fields,
            tags=[],
            pr_ref=None,
            justification=None,
            parent_source_id=None,
        ))

        # Create child items from pending generalizations
        pending_gens = current.get("pending_generalizations", [])
        for idx, gen_text in enumerate(pending_gens):
            child_source_id = f"{current['source_id']}-pg-{idx}"
            child_status = "todo_soft"
            # Detect status markers in the generalization text
            gen_upper = gen_text.upper()
            if gen_upper.startswith("**DONE"):
                child_status = "done"
            elif gen_upper.startswith("**DEFERRED"):
                child_status = "todo_soft"
            elif gen_upper.startswith("**TODO!"):
                child_status = "todo_hard"
            elif gen_upper.startswith("**TODO"):
                child_status = "todo_soft"
            child_priority = assign_priority(child_status)
            items.append(ParsedItem(
                source_id=child_source_id,
                kind=current["kind"],
                title=gen_text,
                status=child_status,
                priority=child_priority,
                description="",
                fields={},
                tags=[],
                pr_ref=None,
                justification=None,
                parent_source_id=current["source_id"],
            ))

        current = None

    for line in lines:
        stripped = line.strip()

        # Check for INV header
        inv_match = _INV_HEADER_RE.match(stripped)
        if inv_match:
            _finalize()
            source_id = inv_match.group(1)
            title = inv_match.group(2).strip()
            current = {
                "source_id": source_id,
                "kind": "invariant",
                "title": title,
                "fields": {},
                "description_parts": [],
                "regression_tests": [],
            }
            current_field_key = None
            continue

        # Check for META header
        meta_match = _META_HEADER_RE.match(stripped)
        if meta_match:
            _finalize()
            source_id = meta_match.group(1)
            title = meta_match.group(2).strip()
            current = {
                "source_id": source_id,
                "kind": "meta_invariant",
                "title": title,
                "fields": {},
                "description_parts": [],
            }
            current_field_key = None
            continue

        # Horizontal rule → finalize
        if stripped == "---":
            _finalize()
            continue

        if current is None:
            continue

        # Blockquote for META statement
        bq_match = _BLOCKQUOTE_RE.match(stripped)
        if bq_match and current["kind"] == "meta_invariant":
            current["fields"]["statement"] = bq_match.group(1)
            current_field_key = None
            continue

        # Field line
        field_match = _FIELD_RE.match(stripped)
        if field_match:
            field_name = field_match.group(1).strip()
            field_value = field_match.group(2).strip()
            current_field_key = None

            if field_name == "Status":
                current["raw_status"] = field_value
            elif field_name == "Regression tests":
                current_field_key = "_regression_tests"
            elif field_name == "Pending Generalizations":
                if field_value and not field_value.lower().startswith("none"):
                    current.setdefault("pending_generalizations", []).append(field_value)
                    current_field_key = "_pending_gen"
                elif not field_value:
                    # Empty value → content is on continuation lines
                    current_field_key = "_pending_gen"
                else:
                    # Value starts with "None" → no pending gens
                    current_field_key = None
            elif field_name in _INV_FIELD_MAP and current["kind"] == "invariant":
                mapped = _INV_FIELD_MAP[field_name]
                current["fields"][mapped] = field_value
                current_field_key = mapped
            elif field_name in _INV_DESCRIPTION_FIELDS:
                current["description_parts"].append(f"{field_name}: {field_value}")
                current_field_key = "_desc"
            elif field_name == "Notes" and current["kind"] == "meta_invariant":
                if field_value:
                    current["description_parts"].append(field_value)
                current_field_key = "_desc"
            elif field_name.startswith("Unified by"):
                if field_value:
                    current["description_parts"].append(f"Unified by: {field_value}")
                current_field_key = "_desc"
            elif field_name == "Implication":
                if field_value:
                    current["description_parts"].append(f"Implication: {field_value}")
                current_field_key = "_desc"
            else:
                # Unknown field → description
                if field_value:
                    current["description_parts"].append(f"{field_name}: {field_value}")
                current_field_key = "_desc"
            continue

        # Continuation line
        if current_field_key and stripped and not stripped.startswith("#"):
            cont_text = stripped.lstrip("- ").strip()
            if current_field_key == "_regression_tests":
                test_cont = re.match(r"`(.+)`", cont_text)
                if test_cont:
                    current.setdefault("regression_tests", []).append(test_cont.group(1))
            elif current_field_key == "_pending_gen":
                if cont_text and cont_text.lower() != "none":
                    current.setdefault("pending_generalizations", []).append(cont_text)
            elif current_field_key == "_desc":
                current["description_parts"].append(cont_text)
            elif current_field_key in current.get("fields", {}):
                current["fields"][current_field_key] += " " + cont_text

    _finalize()
    return items


def parse_work_items(content: str) -> list[ParsedItem]:
    """Parse the work items markdown into ParsedItem objects.

    Extracts items from category sections (## headers) with status markers
    (**DONE**, **DEFERRED**, **TODO!**, **TODO**).

    Source IDs are generated as wi-<category_slug>-<index> for
    deterministic content-hash IDs.
    """
    if not content.strip():
        return []

    items: list[ParsedItem] = []
    lines = content.split("\n")
    current_category: str | None = None
    category_counts: dict[str, int] = {}

    for line in lines:
        stripped = line.strip()

        # Category header
        cat_match = re.match(r"^##\s+(.+)$", stripped)
        if cat_match:
            current_category = cat_match.group(1).strip()
            continue

        # Work item line: starts with "- **STATUS**"
        wi_match = re.match(
            r"^-\s+\*\*(DONE|DEFERRED|TODO!|TODO)\*\*\s*(.*)", stripped
        )
        if wi_match and current_category:
            raw_status = wi_match.group(1)
            rest = wi_match.group(2).strip()

            # Normalize status
            status = normalize_status(raw_status)
            priority = assign_priority(status)

            # Extract PR ref
            pr_ref: str | None = None
            pr_match = _PR_REF_RE.match(rest)
            if pr_match:
                pr_ref = pr_match.group(1)
                rest = rest[pr_match.end():].strip()

            # Title is the first sentence; description is everything after first period
            # But many items are one-liners, so just use the whole line as title
            title = rest
            description = ""

            # For multi-sentence items, split at first period followed by space
            period_idx = rest.find(". ")
            if period_idx > 0:
                title = rest[:period_idx + 1]
                description = rest[period_idx + 2:].strip()

            # Generate source_id
            justification: str | None = None
            tag = category_to_tag(current_category)
            cat_slug = tag.replace("_", "-")
            idx = category_counts.get(cat_slug, 0)
            category_counts[cat_slug] = idx + 1
            source_id = f"wi-{cat_slug}-{idx}"

            items.append(ParsedItem(
                source_id=source_id,
                kind="work_item",
                title=title,
                status=status,
                priority=priority,
                description=description,
                fields={},
                tags=[tag] if tag else [],
                pr_ref=pr_ref,
                justification=justification,
                parent_source_id=None,
            ))

    return items


# ---------------------------------------------------------------------------
# Phase 3: Writer + pipeline
# ---------------------------------------------------------------------------


def build_create_op(
    item: ParsedItem,
    config: TrackerConfig,
) -> tuple[dict[str, Any], str]:
    """Build a deterministic create op dict and content-hash ID from a ParsedItem.

    Returns (op_dict, item_id) where op_dict is ready for _serialize_op().
    The ID is deterministic: same input → same ID.

    Uses fixed actor ("agent"/"migration"), clock=1, and a random nonce
    (for merge=union safety). The timestamp is the current time but is
    not part of the content hash.
    """
    kind_config = config.kinds[item.kind]

    data: dict[str, Any] = {
        "kind": item.kind,
        "title": item.title,
        "status": item.status,
        "priority": item.priority,
        "parent": None,
        "tags": item.tags,
        "before": [],
        "duplicate_of": [],
        "not_duplicate_of": [],
        "pr_ref": item.pr_ref,
        "justification": item.justification,
        "description": item.description,
        "fields": item.fields,
    }

    item_id = _compute_id(kind_config.prefix, data)

    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    op_dict: dict[str, Any] = {
        "op": "create",
        "at": now,
        "by": "agent",
        "actor": "migration",
        "clock": 1,
        "nonce": _make_nonce(),
        "data": data,
    }

    return op_dict, item_id


def migrate(
    ledger_path: Path,
    work_items_path: Path,
    tracker_root: Path,
    *,
    dry_run: bool = False,
) -> MigrationResult:
    """Run the full migration pipeline.

    Parses both governance files, builds create ops, and writes ops files
    to the appropriate tier directories (INV/META → canonical, WI → workspace).

    Creates the directory structure, config templates, and .gitattributes
    files needed for the tracker.

    Args:
        ledger_path: Path to .agent/invariant-ledger.md.
        work_items_path: Path to work_items.md.
        tracker_root: Path to the .agent/ directory.
        dry_run: If True, compute what would happen but don't write ops files.

    Returns:
        MigrationResult with counts, errors, and ID mappings.
    """
    result = MigrationResult()

    # --- Parse source files ---
    all_items: list[ParsedItem] = []

    if ledger_path.exists():
        content = ledger_path.read_text()
        all_items.extend(parse_invariant_ledger(content))

    if work_items_path.exists():
        content = work_items_path.read_text()
        all_items.extend(parse_work_items(content))

    # --- Create directory structure ---
    canonical_ops = tracker_root / "tracker" / ".ops"
    workspace_ops = tracker_root / "tracker-workspace" / ".ops"
    stealth_dir = tracker_root / "tracker-workspace" / "stealth"

    canonical_ops.mkdir(parents=True, exist_ok=True)
    workspace_ops.mkdir(parents=True, exist_ok=True)
    stealth_dir.mkdir(parents=True, exist_ok=True)

    # .gitattributes for merge=union
    for ops_dir in [canonical_ops, workspace_ops]:
        ga = ops_dir / ".gitattributes"
        if not ga.exists():
            ga.write_text("*.ops merge=union\n")

    # .gitignore for stealth
    gi = stealth_dir / ".gitignore"
    if not gi.exists():
        gi.write_text("*.ops\n")

    # --- Write config templates to both tiers ---
    write_config_template(tracker_root / "tracker")
    write_config_template(tracker_root / "tracker-workspace")

    # --- Load config ---
    config = load_config(tracker_root / "tracker")

    # --- Build and write ops ---
    # Track items that need parent update ops (second pass)
    items_needing_parent: list[tuple[ParsedItem, str]] = []

    for item in all_items:
        try:
            op_dict, item_id = build_create_op(item, config)
        except Exception as e:
            result.errors.append(f"Failed to build op for {item.source_id}: {e}")
            continue

        # Determine target directory
        if item.kind in ("invariant", "meta_invariant"):
            target_dir = canonical_ops
        else:
            target_dir = workspace_ops

        ops_path = target_dir / f".{item_id}.ops"

        # Idempotency: skip if file already exists
        if ops_path.exists():
            result.items_skipped += 1
            result.id_map[item.source_id] = item_id
            if item.parent_source_id:
                items_needing_parent.append((item, item_id))
            continue

        if not dry_run:
            serialized = _serialize_op(op_dict)
            ops_path.write_text(serialized)

        result.items_created += 1
        result.items_by_kind[item.kind] = result.items_by_kind.get(item.kind, 0) + 1
        result.id_map[item.source_id] = item_id

        if item.parent_source_id:
            items_needing_parent.append((item, item_id))

    # --- Second pass: write parent update ops for child items ---
    for item, item_id in items_needing_parent:
        parent_tracker_id = result.id_map.get(item.parent_source_id)
        if not parent_tracker_id:  # pragma: no cover
            continue

        if item.kind in ("invariant", "meta_invariant"):
            target_dir = canonical_ops
        else:  # pragma: no cover
            target_dir = workspace_ops

        ops_path = target_dir / f".{item_id}.ops"
        if not ops_path.exists() or dry_run:
            continue

        # Check if parent is already set (idempotency)
        from hypergumbo_tracker.store import CorruptFileError, _parse_ops_file, compile_ops
        try:
            existing_ops = _parse_ops_file(ops_path)
            compiled = compile_ops(existing_ops, item_id)
            if compiled.parent == parent_tracker_id:
                continue
        except (ValueError, KeyError, CorruptFileError):
            continue

        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        update_op: dict[str, Any] = {
            "op": "update",
            "at": now,
            "by": "agent",
            "actor": "migration",
            "clock": 2,
            "nonce": _make_nonce(),
            "set": {"parent": parent_tracker_id},
        }
        serialized = _serialize_op(update_op)
        with open(ops_path, "a") as f:
            f.write(serialized)

    return result
