# SPDX-License-Identifier: MPL-2.0
"""Data model for the hypergumbo tracker.

Defines the operation types (frozen dataclasses), compiled item state,
tracker configuration with config loading chain, actor resolution,
and field schema types.

Design rationale:
- All ops are frozen dataclasses for immutability — the append-only log
  must never mutate existing ops.
- CompiledItem is mutable because compile() builds it incrementally by
  folding ops in Lamport clock order.
- Config loading follows the config.yaml → config.yaml.template → defaults
  chain, matching the .env.example → .env pattern.
- Actor resolution uses os.getuid() which is non-forgeable — the agent
  cannot become the human's UID without sudo, which it doesn't have.

See ADR-0013 for the full design specification.
"""

from __future__ import annotations

import fnmatch
import os
import pwd
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any

import yaml


# ---------------------------------------------------------------------------
# Tier enum
# ---------------------------------------------------------------------------

class Tier(Enum):
    """Visibility tier for tracker items.

    Items live in one of three tiers determined by their physical directory:
    - CANONICAL: committed, shared with upstream (.agent/tracker/.ops/)
    - WORKSPACE: committed, fork-local (.agent/tracker-workspace/.ops/)
    - STEALTH: gitignored, local-only (.agent/tracker-workspace/stealth/)
    """

    CANONICAL = "canonical"
    WORKSPACE = "workspace"
    STEALTH = "stealth"


# ---------------------------------------------------------------------------
# Op dataclasses — frozen, immutable
# ---------------------------------------------------------------------------
# Every op carries these common fields:
#   op: str          — the op type name
#   at: str          — ISO 8601 UTC timestamp
#   by: str          — "agent" or "human"
#   actor: str       — OS username (e.g. jgstern_agent)
#   clock: int       — Lamport clock value
#   nonce: str       — 4 random hex chars


@dataclass(frozen=True)
class CreateOp:
    """Initialize an item with all fields.

    The data dict contains: kind, title, status, priority, parent, tags,
    before, duplicate_of, not_duplicate_of, pr_ref,
    description, fields.
    """

    op: str
    at: str
    by: str
    actor: str
    clock: int
    nonce: str
    data: dict[str, Any]

    def __post_init__(self) -> None:
        if self.op != "create":
            raise ValueError(f"CreateOp.op must be 'create', got {self.op!r}")


@dataclass(frozen=True)
class UpdateOp:
    """Update scalar fields (LWW) and/or set-valued fields (add/remove).

    set: dict of field→value for LWW overwrites.
    add: optional dict of field→[values] for set accumulation.
    remove: optional dict of field→[values] for set subtraction.
    """

    op: str
    at: str
    by: str
    actor: str
    clock: int
    nonce: str
    set: dict[str, Any]
    add: dict[str, list[Any]] = field(default_factory=dict)
    remove: dict[str, list[Any]] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.op != "update":
            raise ValueError(f"UpdateOp.op must be 'update', got {self.op!r}")


@dataclass(frozen=True)
class DiscussOp:
    """Append a discussion entry."""

    op: str
    at: str
    by: str
    actor: str
    clock: int
    nonce: str
    message: str

    def __post_init__(self) -> None:
        if self.op != "discuss":
            raise ValueError(f"DiscussOp.op must be 'discuss', got {self.op!r}")


@dataclass(frozen=True)
class DiscussClearOp:
    """Clear all previous discussion entries. Human-authority only."""

    op: str
    at: str
    by: str
    actor: str
    clock: int
    nonce: str

    def __post_init__(self) -> None:
        if self.op != "discuss_clear":
            raise ValueError(f"DiscussClearOp.op must be 'discuss_clear', got {self.op!r}")


@dataclass(frozen=True)
class DiscussSummarizeOp:
    """Replace discussion with a single summary."""

    op: str
    at: str
    by: str
    actor: str
    clock: int
    nonce: str
    message: str

    def __post_init__(self) -> None:
        if self.op != "discuss_summarize":
            raise ValueError(
                f"DiscussSummarizeOp.op must be 'discuss_summarize', got {self.op!r}"
            )


@dataclass(frozen=True)
class LockOp:
    """Add fields to the locked set. Human-authority only."""

    op: str
    at: str
    by: str
    actor: str
    clock: int
    nonce: str
    lock: list[str]

    def __post_init__(self) -> None:
        if self.op != "lock":
            raise ValueError(f"LockOp.op must be 'lock', got {self.op!r}")


@dataclass(frozen=True)
class UnlockOp:
    """Remove fields from the locked set. Human-authority only."""

    op: str
    at: str
    by: str
    actor: str
    clock: int
    nonce: str
    unlock: list[str]

    def __post_init__(self) -> None:
        if self.op != "unlock":
            raise ValueError(f"UnlockOp.op must be 'unlock', got {self.op!r}")


@dataclass(frozen=True)
class PromoteOp:
    """Record promotion (workspace → canonical)."""

    op: str
    at: str
    by: str
    actor: str
    clock: int
    nonce: str

    def __post_init__(self) -> None:
        if self.op != "promote":
            raise ValueError(f"PromoteOp.op must be 'promote', got {self.op!r}")


@dataclass(frozen=True)
class DemoteOp:
    """Record demotion (canonical → workspace)."""

    op: str
    at: str
    by: str
    actor: str
    clock: int
    nonce: str

    def __post_init__(self) -> None:
        if self.op != "demote":
            raise ValueError(f"DemoteOp.op must be 'demote', got {self.op!r}")


@dataclass(frozen=True)
class StealthOp:
    """Record move to stealth (workspace → stealth). Human-authority only."""

    op: str
    at: str
    by: str
    actor: str
    clock: int
    nonce: str

    def __post_init__(self) -> None:
        if self.op != "stealth":
            raise ValueError(f"StealthOp.op must be 'stealth', got {self.op!r}")


@dataclass(frozen=True)
class UnstealthOp:
    """Record move from stealth (stealth → workspace). Human-authority only."""

    op: str
    at: str
    by: str
    actor: str
    clock: int
    nonce: str

    def __post_init__(self) -> None:
        if self.op != "unstealth":
            raise ValueError(f"UnstealthOp.op must be 'unstealth', got {self.op!r}")


@dataclass(frozen=True)
class ReconcileOp:
    """Record automated cross-tier duplicate resolution."""

    op: str
    at: str
    by: str
    actor: str
    clock: int
    nonce: str
    from_tier: str
    reason: str

    def __post_init__(self) -> None:
        if self.op != "reconcile":
            raise ValueError(f"ReconcileOp.op must be 'reconcile', got {self.op!r}")


# Union type for all ops
Op = (
    CreateOp
    | UpdateOp
    | DiscussOp
    | DiscussClearOp
    | DiscussSummarizeOp
    | LockOp
    | UnlockOp
    | PromoteOp
    | DemoteOp
    | StealthOp
    | UnstealthOp
    | ReconcileOp
)

# Maps op type name → dataclass
OP_TYPE_MAP: dict[str, type] = {
    "create": CreateOp,
    "update": UpdateOp,
    "discuss": DiscussOp,
    "discuss_clear": DiscussClearOp,
    "discuss_summarize": DiscussSummarizeOp,
    "lock": LockOp,
    "unlock": UnlockOp,
    "promote": PromoteOp,
    "demote": DemoteOp,
    "stealth": StealthOp,
    "unstealth": UnstealthOp,
    "reconcile": ReconcileOp,
}

# Ops that only the human actor can perform
HUMAN_AUTHORITY_OPS: frozenset[str] = frozenset({
    "lock",
    "unlock",
    "discuss_clear",
    "stealth",
    "unstealth",
})


def dict_to_op(d: dict[str, Any]) -> Op:
    """Convert a parsed YAML dict to a typed Op dataclass.

    Looks up the op type from the 'op' field and constructs the appropriate
    frozen dataclass. Raises ValueError for unknown op types or missing fields.
    """
    op_type = d.get("op")
    if op_type not in OP_TYPE_MAP:
        raise ValueError(f"Unknown op type: {op_type!r}")

    cls = OP_TYPE_MAP[op_type]

    # Extract only fields the dataclass expects
    field_names = {f.name for f in cls.__dataclass_fields__.values()}
    kwargs = {k: v for k, v in d.items() if k in field_names}
    return cls(**kwargs)


# ---------------------------------------------------------------------------
# CompiledItem — mutable, built by compile()
# ---------------------------------------------------------------------------


@dataclass
class DiscussionEntry:
    """A single discussion entry in a compiled item."""

    by: str
    actor: str
    at: str
    message: str
    is_summary: bool = False


@dataclass
class CompiledItem:
    """The current state of a tracker item, derived by compile() from ops.

    Mutable because compile() builds it incrementally by folding ops in
    Lamport clock order. Fields correspond to the ADR-0013 compiled item spec.
    """

    id: str
    kind: str
    title: str
    status: str
    priority: int = 2
    parent: str | None = None
    tags: list[str] = field(default_factory=list)
    isbefore: list[str] = field(default_factory=list)
    duplicate_of: list[str] = field(default_factory=list)
    not_duplicate_of: list[str] = field(default_factory=list)
    pr_ref: str | None = None
    description: str = ""
    fields: dict[str, Any] = field(default_factory=dict)
    locked_fields: set[str] = field(default_factory=set)
    discussion: list[DiscussionEntry] = field(default_factory=list)
    created_at: str = ""
    updated_at: str = ""
    cross_tier_conflict: bool = False
    frozen: bool = False
    simhash: int | None = None
    tier: Tier | None = None


# ---------------------------------------------------------------------------
# Field schema types
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class FieldSchema:
    """Schema definition for a field within a kind's fields_schema.

    Supported types: text, integer (with optional min/max), list, boolean.
    """

    type: str
    required: bool = False
    description: str = ""
    min: int | None = None
    max: int | None = None

    def __post_init__(self) -> None:
        valid_types = {"text", "integer", "list", "boolean"}
        if self.type not in valid_types:
            raise ValueError(
                f"FieldSchema type must be one of {valid_types}, got {self.type!r}"
            )
        if self.type != "integer" and (self.min is not None or self.max is not None):
            raise ValueError("min/max only valid for integer type fields")


@dataclass(frozen=True)
class KindConfig:
    """Configuration for a tracker item kind.

    prefix: Short uppercase prefix for IDs (e.g. INV, META, WI).
    description: Human-readable description of the kind.
    fields_schema: Optional dict of field_name → FieldSchema. If None or empty,
      the kind's fields dict accepts arbitrary keys with no validation.
    allowed_statuses: Optional list of statuses valid for this kind. If None,
      all global statuses are valid. Used to restrict invariant-kind items to
      perpetual-truth statuses (satisfied, violated, needs_human_review, deleted).
    deprecated_statuses: Optional list of statuses that are accepted when reading
      historical ops but rejected on new create/update operations. This allows
      renaming statuses without rewriting the append-only ops log.
    """

    prefix: str
    description: str = ""
    fields_schema: dict[str, FieldSchema] | None = None
    allowed_statuses: list[str] | None = None
    deprecated_statuses: list[str] | None = None


# ---------------------------------------------------------------------------
# Tracker configuration
# ---------------------------------------------------------------------------


class ConfigValidationError(Exception):
    """Raised when tracker config fails validation."""


@dataclass
class TrackerConfig:
    """Tracker configuration loaded from config.yaml or template.

    Holds kinds, statuses, stop hook settings, actor resolution patterns,
    well-known tags, and Lamport clock branch configuration.
    """

    kinds: dict[str, KindConfig]
    statuses: list[str]
    blocking_statuses: list[str]
    resolved_statuses: list[str]
    human_only_statuses: list[str] = field(default_factory=list)
    well_known_tags: list[str] = field(default_factory=list)
    agent_usernames: list[str] = field(default_factory=lambda: ["*_agent"])
    scope: str = "all"
    lamport_branches: list[str] = field(default_factory=lambda: ["dev", "main"])


def _parse_fields_schema(raw: dict[str, Any] | None) -> dict[str, FieldSchema] | None:
    """Parse a raw fields_schema dict from config YAML into FieldSchema objects."""
    if not raw:
        return None
    result: dict[str, FieldSchema] = {}
    for name, spec in raw.items():
        if not isinstance(spec, dict):
            raise ConfigValidationError(
                f"Field schema for '{name}' must be a dict, got {type(spec).__name__}"
            )
        result[name] = FieldSchema(
            type=spec.get("type", "text"),
            required=spec.get("required", False),
            description=spec.get("description", ""),
            min=spec.get("min"),
            max=spec.get("max"),
        )
    return result


def _parse_config_dict(raw: dict[str, Any]) -> TrackerConfig:
    """Parse a raw config dict into a TrackerConfig, validating constraints.

    Validation enforces:
    - All referenced statuses exist in the statuses list
    - blocking_statuses and resolved_statuses don't overlap
    - blocking_statuses is non-empty
    - agent_usernames is a non-empty list
    - lamport_branches is a non-empty list
    """
    # Parse kinds
    kinds_raw = raw.get("kinds", {})
    kinds: dict[str, KindConfig] = {}
    for kind_name, kind_spec in kinds_raw.items():
        if not isinstance(kind_spec, dict):
            raise ConfigValidationError(
                f"Kind '{kind_name}' must be a dict, got {type(kind_spec).__name__}"
            )
        raw_allowed = kind_spec.get("allowed_statuses")
        if raw_allowed is not None and not isinstance(raw_allowed, list):
            raise ConfigValidationError(
                f"Kind '{kind_name}': allowed_statuses must be a list"
            )
        raw_deprecated = kind_spec.get("deprecated_statuses")
        if raw_deprecated is not None and not isinstance(raw_deprecated, list):
            raise ConfigValidationError(
                f"Kind '{kind_name}': deprecated_statuses must be a list"
            )
        kinds[kind_name] = KindConfig(
            prefix=kind_spec.get("prefix", kind_name.upper()[:3]),
            description=kind_spec.get("description", ""),
            fields_schema=_parse_fields_schema(kind_spec.get("fields_schema")),
            allowed_statuses=raw_allowed,
            deprecated_statuses=raw_deprecated,
        )

    # Parse statuses
    statuses = raw.get("statuses", [])
    if not isinstance(statuses, list) or not statuses:
        raise ConfigValidationError("'statuses' must be a non-empty list")

    # Parse stop hook
    stop_hook = raw.get("stop_hook", {})
    blocking = stop_hook.get("blocking_statuses", [])
    resolved = stop_hook.get("resolved_statuses", [])

    if not isinstance(blocking, list) or not blocking:
        raise ConfigValidationError("'stop_hook.blocking_statuses' must be a non-empty list")

    if not isinstance(resolved, list):
        raise ConfigValidationError("'stop_hook.resolved_statuses' must be a list")

    # Validate all referenced statuses exist
    status_set = set(statuses)
    for s in blocking:
        if s not in status_set:
            raise ConfigValidationError(
                f"blocking_statuses references unknown status '{s}'"
            )
    for s in resolved:
        if s not in status_set:
            raise ConfigValidationError(
                f"resolved_statuses references unknown status '{s}'"
            )

    # Validate no overlap
    overlap = set(blocking) & set(resolved)
    if overlap:
        raise ConfigValidationError(
            f"blocking_statuses and resolved_statuses overlap: {overlap}"
        )

    # Parse human_only_statuses
    human_only = stop_hook.get("human_only_statuses", [])
    if not isinstance(human_only, list):
        raise ConfigValidationError(
            "'stop_hook.human_only_statuses' must be a list"
        )
    for s in human_only:
        if s not in status_set:
            raise ConfigValidationError(
                f"human_only_statuses references unknown status '{s}'"
            )
    human_only_blocking_overlap = set(human_only) & set(blocking)
    if human_only_blocking_overlap:
        raise ConfigValidationError(
            f"human_only_statuses and blocking_statuses overlap: "
            f"{human_only_blocking_overlap}"
        )

    # Validate per-kind allowed_statuses and deprecated_statuses
    for kind_name, kind_config in kinds.items():
        if kind_config.allowed_statuses is not None:
            for s in kind_config.allowed_statuses:
                if s not in status_set:
                    raise ConfigValidationError(
                        f"Kind '{kind_name}': allowed_statuses references "
                        f"unknown status '{s}'"
                    )
        if kind_config.deprecated_statuses is not None:
            for s in kind_config.deprecated_statuses:
                if s not in status_set:
                    raise ConfigValidationError(
                        f"Kind '{kind_name}': deprecated_statuses references "
                        f"unknown status '{s}'"
                    )
            # Deprecated must not overlap with allowed
            if kind_config.allowed_statuses is not None:
                overlap = set(kind_config.deprecated_statuses) & set(kind_config.allowed_statuses)
                if overlap:
                    raise ConfigValidationError(
                        f"Kind '{kind_name}': deprecated_statuses and "
                        f"allowed_statuses overlap: {overlap}"
                    )

    # Parse actor resolution
    actor_res = raw.get("actor_resolution", {})
    agent_usernames = actor_res.get("agent_usernames", ["*_agent"])
    if not isinstance(agent_usernames, list) or not agent_usernames:
        raise ConfigValidationError(
            "'actor_resolution.agent_usernames' must be a non-empty list"
        )

    # Parse scope
    scope = stop_hook.get("scope", "all")
    if scope not in ("all", "workspace"):
        raise ConfigValidationError(
            f"'stop_hook.scope' must be 'all' or 'workspace', got {scope!r}"
        )

    # Parse lamport branches
    lamport_branches = raw.get("lamport_branches", ["dev", "main"])
    if not isinstance(lamport_branches, list) or not lamport_branches:
        raise ConfigValidationError(
            "'lamport_branches' must be a non-empty list"
        )

    # Parse well-known tags
    well_known_tags = raw.get("well_known_tags", [])

    return TrackerConfig(
        kinds=kinds,
        statuses=statuses,
        blocking_statuses=blocking,
        resolved_statuses=resolved,
        human_only_statuses=human_only,
        well_known_tags=well_known_tags,
        agent_usernames=agent_usernames,
        scope=scope,
        lamport_branches=lamport_branches,
    )


# Built-in default config — used when no config.yaml or template exists
_DEFAULT_CONFIG_RAW: dict[str, Any] = {
    "kinds": {
        "invariant": {
            "prefix": "INV",
            "description": "Discovered invariant with root cause analysis",
            "fields_schema": {
                "statement": {"type": "text", "required": True},
                "root_cause": {"type": "text", "required": True},
            },
        },
        "work_item": {
            "prefix": "WI",
            "description": "Backlog item",
        },
    },
    "statuses": [
        "todo_hard",
        "todo_soft",
        "in_progress",
        "needs_human_review",
        "done",
        "wont_do",
        "deleted",
    ],
    "stop_hook": {
        "blocking_statuses": ["todo_hard", "todo_soft"],
        "resolved_statuses": ["done", "wont_do", "deleted"],
        "human_only_statuses": ["deleted"],
    },
    "well_known_tags": [],
    "actor_resolution": {"agent_usernames": ["*_agent"]},
    "lamport_branches": ["dev", "main"],
}


def load_config(config_dir: Path) -> TrackerConfig:
    """Load tracker config from the given directory.

    Loading chain:
    1. config.yaml (gitignored, human-owned) — if it exists, use it
    2. config.yaml.template (tracked, shared governance) — fallback
    3. Built-in defaults — if neither exists

    This is a fallback chain (not a merge): whichever file is found first
    is used in its entirety.
    """
    config_path = config_dir / "config.yaml"
    template_path = config_dir / "config.yaml.template"

    raw: dict[str, Any]
    if config_path.is_file():
        with open(config_path) as f:
            raw = yaml.safe_load(f) or {}
    elif template_path.is_file():
        with open(template_path) as f:
            raw = yaml.safe_load(f) or {}
    else:
        raw = dict(_DEFAULT_CONFIG_RAW)

    return _parse_config_dict(raw)


# ---------------------------------------------------------------------------
# Actor resolution
# ---------------------------------------------------------------------------


def resolve_actor(agent_patterns: list[str] | None = None) -> tuple[str, str]:
    """Resolve the current OS user to (by, actor) tuple.

    Returns:
        (by, actor) where by is "agent" or "human" and actor is the OS username.

    The resolution is based on os.getuid() — non-forgeable. The username
    is matched against glob patterns from config (default: ["*_agent"]).
    If the username matches any pattern, the actor is resolved as "agent";
    otherwise "human".
    """
    if agent_patterns is None:
        agent_patterns = ["*_agent"]

    uid = os.getuid()
    pw_entry = pwd.getpwuid(uid)
    username = pw_entry.pw_name

    for pattern in agent_patterns:
        if fnmatch.fnmatch(username, pattern):
            return ("agent", username)
    return ("human", username)
