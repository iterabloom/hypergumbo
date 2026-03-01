# SPDX-License-Identifier: MPL-2.0
"""Interactive CLI config editor for hypergumbo tracker.

Provides ``run_configure(root)`` which walks the human user through
each section of config.yaml (statuses, kinds, stop_hook, tags,
actor_resolution, lamport_branches), validates the result, shows a
diff summary, and writes with 0444 enforcement.

Human-only: agents are rejected at the top of run_configure().

Design rationale:
- Uses stdin prompts for each section, with Enter-to-skip for no-ops.
- Loads both template (baseline) and existing config (current state).
- Validates via _parse_config_dict() before writing — bad configs are
  rejected with an error message and the user can retry.
- After writing, config_lock() sets 0444.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import yaml

from hypergumbo_tracker.models import (
    ConfigValidationError,
    _parse_config_dict,
    resolve_actor,
)
from hypergumbo_tracker.setup import config_lock, config_unlock


def _prompt(message: str, *, input_fn: Any = None) -> str:
    """Prompt the user and return stripped input. Empty on EOF/interrupt."""
    read = input_fn or input
    try:
        return read(message).strip()
    except (EOFError, KeyboardInterrupt):
        return ""


def _edit_list(
    label: str,
    current: list[str],
    *,
    input_fn: Any = None,
) -> list[str]:
    """Let the user add/remove items from a string list.

    Prints the current list, then prompts to add and remove items.
    Returns the modified list.
    """
    result = list(current)
    print(f"  {label}: {result}")

    while True:
        val = _prompt(f"  Add to {label}? [name or Enter to skip]: ", input_fn=input_fn)
        if not val:
            break
        if val not in result:
            result.append(val)
            print(f"    added '{val}'")
        else:
            print(f"    '{val}' already present")

    while True:
        val = _prompt(f"  Remove from {label}? [name or Enter to skip]: ", input_fn=input_fn)
        if not val:
            break
        if val in result:
            result.remove(val)
            print(f"    removed '{val}'")
        else:
            print(f"    '{val}' not found")

    return result


def _configure_statuses(
    config: dict[str, Any],
    *,
    input_fn: Any = None,
) -> None:
    """Walk the user through editing the statuses list."""
    print("\n--- Statuses ---")
    statuses = list(config.get("statuses", []))
    config["statuses"] = _edit_list("statuses", statuses, input_fn=input_fn)


def _configure_kinds(
    config: dict[str, Any],
    *,
    input_fn: Any = None,
) -> None:
    """Walk the user through editing kinds and their allowed_statuses."""
    print("\n--- Kinds ---")
    kinds = config.get("kinds", {})
    if not isinstance(kinds, dict):
        kinds = {}

    for kind_name in list(kinds.keys()):
        kind_spec = kinds[kind_name]
        if not isinstance(kind_spec, dict):
            continue
        prefix = kind_spec.get("prefix", kind_name.upper()[:3])
        print(f"\n  Kind '{kind_name}' (prefix: {prefix}):")

        allowed = kind_spec.get("allowed_statuses")
        if allowed is not None:
            kind_spec["allowed_statuses"] = _edit_list(
                "allowed_statuses", list(allowed), input_fn=input_fn,
            )
        else:
            print("    No allowed_statuses restriction (all statuses valid)")
            val = _prompt("    Add restriction? [y/N]: ", input_fn=input_fn)
            if val.lower() in ("y", "yes"):
                kind_spec["allowed_statuses"] = _edit_list(
                    "allowed_statuses", [], input_fn=input_fn,
                )

    # Add new kind
    while True:
        val = _prompt("\n  Add a new kind? [name or Enter to skip]: ", input_fn=input_fn)
        if not val:
            break
        if val in kinds:
            print(f"    Kind '{val}' already exists")
            continue
        prefix = _prompt(f"    Prefix for '{val}' [default: {val.upper()[:3]}]: ", input_fn=input_fn)
        if not prefix:
            prefix = val.upper()[:3]
        desc = _prompt(f"    Description for '{val}': ", input_fn=input_fn)
        kinds[val] = {"prefix": prefix, "description": desc or ""}
        print(f"    added kind '{val}'")

    # Remove kind
    while True:
        val = _prompt("  Remove a kind? [name or Enter to skip]: ", input_fn=input_fn)
        if not val:
            break
        if val in kinds:
            del kinds[val]
            print(f"    removed kind '{val}'")
        else:
            print(f"    kind '{val}' not found")

    config["kinds"] = kinds


def _configure_stop_hook(
    config: dict[str, Any],
    *,
    input_fn: Any = None,
) -> None:
    """Walk the user through editing stop_hook settings."""
    print("\n--- Stop Hook ---")
    stop_hook = config.get("stop_hook", {})
    if not isinstance(stop_hook, dict):
        stop_hook = {}

    blocking = list(stop_hook.get("blocking_statuses", []))
    stop_hook["blocking_statuses"] = _edit_list(
        "blocking_statuses", blocking, input_fn=input_fn,
    )

    resolved = list(stop_hook.get("resolved_statuses", []))
    stop_hook["resolved_statuses"] = _edit_list(
        "resolved_statuses", resolved, input_fn=input_fn,
    )

    human_only = list(stop_hook.get("human_only_statuses", []))
    stop_hook["human_only_statuses"] = _edit_list(
        "human_only_statuses", human_only, input_fn=input_fn,
    )

    scope = stop_hook.get("scope", "all")
    print(f"  scope: {scope}")
    val = _prompt("  Change scope? [all/workspace/Enter to keep]: ", input_fn=input_fn)
    if val in ("all", "workspace"):
        stop_hook["scope"] = val

    config["stop_hook"] = stop_hook


def _configure_tags(
    config: dict[str, Any],
    *,
    input_fn: Any = None,
) -> None:
    """Walk the user through editing well_known_tags."""
    print("\n--- Well-Known Tags ---")
    tags = list(config.get("well_known_tags", []))
    config["well_known_tags"] = _edit_list(
        "well_known_tags", tags, input_fn=input_fn,
    )


def _configure_actor_resolution(
    config: dict[str, Any],
    *,
    input_fn: Any = None,
) -> None:
    """Walk the user through editing actor_resolution.agent_usernames."""
    print("\n--- Actor Resolution ---")
    actor_res = config.get("actor_resolution", {})
    if not isinstance(actor_res, dict):
        actor_res = {}
    patterns = list(actor_res.get("agent_usernames", ["*_agent"]))
    actor_res["agent_usernames"] = _edit_list(
        "agent_usernames", patterns, input_fn=input_fn,
    )
    config["actor_resolution"] = actor_res


def _configure_lamport_branches(
    config: dict[str, Any],
    *,
    input_fn: Any = None,
) -> None:
    """Walk the user through editing lamport_branches."""
    print("\n--- Lamport Branches ---")
    branches = list(config.get("lamport_branches", ["dev", "main"]))
    config["lamport_branches"] = _edit_list(
        "lamport_branches", branches, input_fn=input_fn,
    )


def _compute_diff(
    old: dict[str, Any],
    new: dict[str, Any],
) -> list[str]:
    """Compute a simple diff summary between old and new config dicts.

    Returns a list of human-readable change lines.
    """
    old_yaml = yaml.dump(old, default_flow_style=False, sort_keys=True)
    new_yaml = yaml.dump(new, default_flow_style=False, sort_keys=True)

    if old_yaml == new_yaml:
        return ["(no changes)"]

    changes: list[str] = []
    old_lines = old_yaml.splitlines()
    new_lines = new_yaml.splitlines()

    old_set = set(old_lines)
    new_set = set(new_lines)

    for line in old_lines:
        if line not in new_set:
            changes.append(f"  - {line}")
    for line in new_lines:
        if line not in old_set:
            changes.append(f"  + {line}")

    return changes or ["(no changes)"]


def run_configure(
    root: Path,
    *,
    input_fn: Any = None,
) -> int:
    """Interactive config editor entry point.

    Args:
        root: Path to the .agent/ directory.
        input_fn: Optional callable replacing input() for testing.

    Returns:
        0 on success, 1 on error.
    """
    # Human-only enforcement
    config_path = root / "tracker" / "config.yaml"
    agent_patterns = ["*_agent"]
    if config_path.exists():
        try:
            with open(config_path) as f:
                raw = yaml.safe_load(f) or {}
            actor_res = raw.get("actor_resolution", {})
            if isinstance(actor_res, dict):
                patterns = actor_res.get("agent_usernames")
                if isinstance(patterns, list) and patterns:
                    agent_patterns = patterns
        except yaml.YAMLError:
            pass

    by, username = resolve_actor(agent_patterns)
    if by == "agent":
        print(
            f"error: configure requires human authority "
            f"(current user '{username}' is agent)",
            file=sys.stderr,
        )
        return 1

    # Load template as baseline
    template_path = root / "tracker" / "config.yaml.template"
    template_data: dict[str, Any] = {}
    if template_path.exists():
        try:
            with open(template_path) as f:
                template_data = yaml.safe_load(f) or {}
        except yaml.YAMLError:
            pass

    # Load existing config (or start from template)
    if config_path.exists():
        try:
            config_unlock(config_path)
            with open(config_path) as f:
                config_data = yaml.safe_load(f) or {}
        except yaml.YAMLError:
            print("warning: existing config.yaml is unparseable, starting from template",
                  file=sys.stderr)
            config_data = dict(template_data)
        finally:
            # Re-lock even if we fail later — we'll unlock again before writing
            config_lock(config_path)
    else:
        config_data = dict(template_data)

    # Take a snapshot for diff
    import copy
    original = copy.deepcopy(config_data)

    # Walk through sections
    print("Tracker configuration editor")
    print("Press Enter to skip any prompt.\n")

    _configure_statuses(config_data, input_fn=input_fn)
    _configure_kinds(config_data, input_fn=input_fn)
    _configure_stop_hook(config_data, input_fn=input_fn)
    _configure_tags(config_data, input_fn=input_fn)
    _configure_actor_resolution(config_data, input_fn=input_fn)
    _configure_lamport_branches(config_data, input_fn=input_fn)

    # Validate before writing
    try:
        _parse_config_dict(config_data)
    except ConfigValidationError as e:
        print(f"\nerror: invalid configuration: {e}", file=sys.stderr)
        print("Config was NOT written. Fix the errors and try again.", file=sys.stderr)
        return 1

    # Show diff
    print("\n--- Changes ---")
    diff_lines = _compute_diff(original, config_data)
    for line in diff_lines:
        print(line)

    # Confirm
    confirm = _prompt("\nWrite config.yaml? [y/N]: ", input_fn=input_fn)
    if confirm.lower() not in ("y", "yes"):
        print("Aborted — config not written.")
        return 0

    # Write
    config_unlock(config_path)
    with open(config_path, "w") as f:
        yaml.dump(config_data, f, default_flow_style=False)
    config_lock(config_path)
    print(f"Wrote {config_path} (mode 0444)")

    # Offer to update workspace config if it exists
    ws_config_path = root / "tracker-workspace" / "config.yaml"
    if ws_config_path.exists():
        ws_confirm = _prompt(
            "Update workspace config.yaml too? [y/N]: ", input_fn=input_fn,
        )
        if ws_confirm.lower() in ("y", "yes"):
            config_unlock(ws_config_path)
            with open(ws_config_path, "w") as f:
                yaml.dump(config_data, f, default_flow_style=False)
            config_lock(ws_config_path)
            print(f"Wrote {ws_config_path} (mode 0444)")

    return 0
