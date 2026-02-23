# SPDX-License-Identifier: MPL-2.0
"""Shared test constants and config factories for hypergumbo-tracker tests.

This module is the single source of truth for the tracker's status vocabulary
and kind definitions used across all test files. When the status list changes,
update TRACKER_TEST_STATUSES (and the blocking/resolved subsets) here — every
test file imports from this module, so one change propagates everywhere.

Constants:
    TRACKER_TEST_STATUSES           — canonical status list
    TRACKER_TEST_BLOCKING_STATUSES  — statuses that block the stop hook
    TRACKER_TEST_RESOLVED_STATUSES  — terminal statuses
    TRACKER_TEST_KINDS              — dict of kind name → KindConfig

Functions:
    make_test_config(**overrides)      — returns a TrackerConfig
    make_test_config_dict(**overrides) — returns a YAML-compatible dict
"""

from __future__ import annotations

from typing import Any

from hypergumbo_tracker.models import (
    FieldSchema,
    KindConfig,
    TrackerConfig,
)

# ---------------------------------------------------------------------------
# Canonical test vocabulary — update HERE when statuses change
# ---------------------------------------------------------------------------

TRACKER_TEST_STATUSES: list[str] = [
    "todo_hard",
    "todo_soft",
    "in_progress",
    "needs_human_review",
    "holding",
    "violated",
    "done",
    "wont_do",
    "deleted",
]

TRACKER_TEST_BLOCKING_STATUSES: list[str] = ["todo_hard", "todo_soft", "violated"]

TRACKER_TEST_RESOLVED_STATUSES: list[str] = ["done", "wont_do", "deleted", "holding"]

TRACKER_TEST_HUMAN_ONLY_STATUSES: list[str] = ["deleted"]

TRACKER_TEST_INVARIANT_ALLOWED_STATUSES: list[str] = [
    "holding", "violated", "needs_human_review", "deleted",
]

TRACKER_TEST_KINDS: dict[str, KindConfig] = {
    "invariant": KindConfig(
        prefix="INV",
        description="Test invariant",
        fields_schema={
            "statement": FieldSchema(type="text", required=True),
            "root_cause": FieldSchema(type="text", required=True),
            "progress_pct": FieldSchema(type="integer", min=0, max=100),
            "regression_tests": FieldSchema(type="list"),
            "verified": FieldSchema(type="boolean"),
        },
        allowed_statuses=["holding", "violated", "needs_human_review", "deleted"],
    ),
    "meta_invariant": KindConfig(
        prefix="META",
        description="A meta-invariant tracking cross-language coverage",
        fields_schema={
            "statement": FieldSchema(type="text", required=True),
            "languages_done": FieldSchema(type="list"),
            "languages_remaining": FieldSchema(type="list"),
            "progress_pct": FieldSchema(type="integer", min=0, max=100),
        },
        allowed_statuses=["holding", "violated", "needs_human_review", "deleted"],
    ),
    "work_item": KindConfig(prefix="WI", description="Work item"),
}


# ---------------------------------------------------------------------------
# Config factories
# ---------------------------------------------------------------------------


def make_test_config(**overrides: Any) -> TrackerConfig:
    """Build a TrackerConfig from the canonical test vocabulary.

    Any keyword argument is forwarded to TrackerConfig, overriding the
    default.  Common override: ``scope="workspace"``.
    """
    assert TRACKER_TEST_STATUSES, (
        "TRACKER_TEST_STATUSES is empty — update tests/helpers.py"
    )
    defaults: dict[str, Any] = {
        "kinds": TRACKER_TEST_KINDS,
        "statuses": TRACKER_TEST_STATUSES,
        "blocking_statuses": TRACKER_TEST_BLOCKING_STATUSES,
        "resolved_statuses": TRACKER_TEST_RESOLVED_STATUSES,
        "human_only_statuses": TRACKER_TEST_HUMAN_ONLY_STATUSES,
        "agent_usernames": ["*_agent"],
        "lamport_branches": ["dev", "main"],
    }
    defaults.update(overrides)
    return TrackerConfig(**defaults)


def make_test_config_dict(**overrides: Any) -> dict[str, Any]:
    """Return a YAML-compatible dict matching the canonical test vocabulary.

    Suitable for writing to config.yaml files on disk.  *overrides* are
    merged at the top level.
    """
    defaults: dict[str, Any] = {
        "kinds": {
            "invariant": {
                "prefix": "INV",
                "description": "Test invariant",
                "allowed_statuses": list(TRACKER_TEST_INVARIANT_ALLOWED_STATUSES),
                "fields_schema": {
                    "statement": {"type": "text", "required": True},
                    "root_cause": {"type": "text", "required": True},
                    "progress_pct": {"type": "integer", "min": 0, "max": 100},
                    "regression_tests": {"type": "list"},
                    "verified": {"type": "boolean"},
                },
            },
            "meta_invariant": {
                "prefix": "META",
                "description": "A meta-invariant tracking cross-language coverage",
                "allowed_statuses": list(TRACKER_TEST_INVARIANT_ALLOWED_STATUSES),
                "fields_schema": {
                    "statement": {"type": "text", "required": True},
                    "languages_done": {"type": "list"},
                    "languages_remaining": {"type": "list"},
                    "progress_pct": {"type": "integer", "min": 0, "max": 100},
                },
            },
            "work_item": {"prefix": "WI", "description": "Work item"},
        },
        "statuses": list(TRACKER_TEST_STATUSES),
        "stop_hook": {
            "blocking_statuses": list(TRACKER_TEST_BLOCKING_STATUSES),
            "resolved_statuses": list(TRACKER_TEST_RESOLVED_STATUSES),
            "human_only_statuses": list(TRACKER_TEST_HUMAN_ONLY_STATUSES),
        },
        "actor_resolution": {"agent_usernames": ["*_agent"]},
        "lamport_branches": ["dev", "main"],
    }
    defaults.update(overrides)
    return defaults
