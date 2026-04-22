# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for WI-joriv: ``stop_hook_state.json`` write discipline.

The stop hook's additive jq merge used to preserve any key already present
in the file forever — five zombie fields from a deleted-but-never-replaced
PR #2926 migration (``last_pr``, ``last_pr_num``, ``last_pr_state``,
``pending_hard_todos``, ``pending_soft_todos``) survived for months before
the 2026-04-18 cleanup found them. WI-joriv's fix is a self-cleaning
write: the merge EXTRACTS the maintained field set from the existing
object before merging in fresh values, so any unlisted key is silently
dropped on the very next write.

These tests guard both the jq expression (by exercising it against
fixtures) and the recover-state playbook (so the maintained-field list
stays in sync with the code).
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest


REPO_ROOT_REAL = Path(__file__).parent.parent
STOP_LOGIC = REPO_ROOT_REAL / ".agent" / "hooks" / "_shared" / "stop_logic.sh"
RECOVER_PLAYBOOK = REPO_ROOT_REAL / ".agent" / "agent_playbooks_protocols_sops_skills" / "recover-state-playbook.md"


# Single source of truth — also documented in the recover-state playbook.
MAINTAINED_KEYS = (
    "guidance_file",
    "bakeoff_convergence",
    "bakeoff_session_path",
    "bakeoff_session_type",
    "current_branch",
    "last_completed_utc",
)

# The exact jq fragment baked into stop_logic.sh. If stop_logic.sh changes
# its extract form, update this string — the guard test at the bottom of
# this file will flag the drift.
JQ_EXTRACT_FORM = """
({guidance_file, bakeoff_convergence, bakeoff_session_path,
  bakeoff_session_type, current_branch, last_completed_utc}
 | with_entries(select(.value != null)))
""".strip()


def _run_jq(input_json: str, expr: str) -> dict:
    """Run jq on ``input_json`` with ``expr`` and return the parsed result."""
    result = subprocess.run(
        ["jq", expr],
        input=input_json,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, (
        f"jq failed:\nexpr={expr!r}\nstderr={result.stderr!r}"
    )
    return json.loads(result.stdout)


# --- The extract form drops unmaintained keys ---


def test_extract_form_drops_zombie_keys() -> None:
    """The write-discipline extract form must silently drop any key NOT in
    the maintained list. This is the core regression guard for the five
    zombie fields cleaned up on 2026-04-18."""
    seed = json.dumps({
        "guidance_file": "/old/path.md",
        "current_branch": "dev",
        # Zombie keys — deliberately included to verify they are dropped.
        "last_pr": "whatever",
        "last_pr_num": 999,
        "last_pr_state": "merged",
        "pending_hard_todos": 5,
        "pending_soft_todos": 10,
        "unexpected_new_field": {"nested": "object"},
    })
    result = _run_jq(seed, JQ_EXTRACT_FORM)
    # Maintained keys present.
    assert result == {
        "guidance_file": "/old/path.md",
        "current_branch": "dev",
    }


def test_extract_form_preserves_all_maintained_keys() -> None:
    """When every maintained key is present, the extract form must return
    all of them unchanged."""
    full = {
        "guidance_file": "/a.md",
        "bakeoff_convergence": "CONVERGED cohort=3 iter=5",
        "bakeoff_session_path": "/home/u/bakeoff/broad-123",
        "bakeoff_session_type": "broad",
        "current_branch": "jgstern-agent/feat/foo",
        "last_completed_utc": "2026-04-18T06:00:00Z",
    }
    result = _run_jq(json.dumps(full), JQ_EXTRACT_FORM)
    assert result == full


def test_extract_form_tolerates_missing_keys() -> None:
    """When some maintained keys are absent, the extract form must emit
    only the present keys — not synthesize nulls for the missing ones,
    and not error out."""
    partial = {"guidance_file": "/only.md"}
    result = _run_jq(json.dumps(partial), JQ_EXTRACT_FORM)
    assert result == {"guidance_file": "/only.md"}


def test_extract_form_from_empty_object() -> None:
    """Starting from ``{}`` (first-ever write) must produce ``{}``, not a
    set of maintained keys with null values."""
    result = _run_jq("{}", JQ_EXTRACT_FORM)
    assert result == {}


# --- Full pipeline: extract + merge ---


FULL_MERGE_EXPR = (
    JQ_EXTRACT_FORM
    + """
 + {guidance_file: $gf}
 + (if $bc != "" then {bakeoff_convergence: $bc} else {} end)
 + (if $bs != "" then {bakeoff_session_path: $bs, bakeoff_session_type: $bt} else {} end)
 + (if $branch != "" then {current_branch: $branch} else {} end)
 + (if ($elapsed | tonumber) >= 30 then {last_completed_utc: $now} else {} end)
"""
)


def _run_jq_full(input_json: str, **args: str) -> dict:
    """Run the full stop_hook_state.json merge jq expression."""
    cmd = ["jq"]
    for k, v in args.items():
        cmd.extend(["--arg", k, v])
    cmd.append(FULL_MERGE_EXPR)
    result = subprocess.run(
        cmd, input=input_json, capture_output=True, text=True, check=False,
    )
    assert result.returncode == 0, (
        f"jq failed:\nstderr={result.stderr!r}"
    )
    return json.loads(result.stdout)


def test_full_merge_drops_zombies_and_sets_fresh_values() -> None:
    """End-to-end: a seed with zombie keys plus fresh arg values produces
    exactly the new maintained set, with zombies gone."""
    seed = json.dumps({
        "guidance_file": "/old.md",
        "last_pr_num": 42,           # zombie
        "pending_hard_todos": 3,     # zombie
        "current_branch": "dev",
    })
    result = _run_jq_full(
        seed,
        gf="/new.md",
        bc="CONVERGED cohort=1 iter=1",
        bs="/home/u/bakeoff/deep-42",
        bt="deep",
        now="2026-04-18T07:00:00Z",
        elapsed="45",  # >= 30, so last_completed_utc gets set
        branch="jgstern-agent/feat/bar",
    )
    assert result == {
        "guidance_file": "/new.md",
        "bakeoff_convergence": "CONVERGED cohort=1 iter=1",
        "bakeoff_session_path": "/home/u/bakeoff/deep-42",
        "bakeoff_session_type": "deep",
        "current_branch": "jgstern-agent/feat/bar",
        "last_completed_utc": "2026-04-18T07:00:00Z",
    }


def test_full_merge_elapsed_below_30_omits_last_completed_utc() -> None:
    """The cooldown gate: when elapsed < 30, ``last_completed_utc`` stays
    at whatever the existing (maintained) value was, instead of being set
    to now."""
    seed = json.dumps({
        "guidance_file": "/old.md",
        "last_completed_utc": "2026-04-18T05:00:00Z",
    })
    result = _run_jq_full(
        seed,
        gf="/new.md",
        bc="",
        bs="",
        bt="",
        now="2026-04-18T05:20:00Z",
        elapsed="20",  # < 30
        branch="",
    )
    # last_completed_utc must be the OLD (maintained) value, not `now`.
    assert result["last_completed_utc"] == "2026-04-18T05:00:00Z"


def test_full_merge_empty_optionals_skip_fields() -> None:
    """When optional args are empty strings, the corresponding keys must
    not appear in the output (to preserve the existing add-only-if-present
    semantics from before the discipline fix)."""
    seed = json.dumps({})
    result = _run_jq_full(
        seed,
        gf="/only-this.md",
        bc="",
        bs="",
        bt="",
        now="2026-04-18T05:00:00Z",
        elapsed="0",
        branch="",
    )
    assert result == {"guidance_file": "/only-this.md"}


# --- Guards: code and docs stay in sync ---


def test_stop_logic_sh_contains_discipline_extract_form() -> None:
    """The extract-form substring must appear in stop_logic.sh. If someone
    reverts the write-discipline change, this guard fails loudly."""
    content = STOP_LOGIC.read_text()
    # Collapse whitespace so the assertion tolerates minor reformatting.
    normalized = re.sub(r"\s+", " ", content)
    needle = re.sub(r"\s+", " ", JQ_EXTRACT_FORM)
    assert needle in normalized, (
        "stop_logic.sh no longer contains the WI-joriv extract form. If "
        "you deliberately reshaped it, update JQ_EXTRACT_FORM in this "
        "test to match."
    )


def test_recover_state_playbook_lists_every_maintained_key() -> None:
    """The recover-state playbook must enumerate every maintained key so
    future agents / humans learn the list without reading source."""
    content = RECOVER_PLAYBOOK.read_text()
    for key in MAINTAINED_KEYS:
        assert f"`{key}`" in content, (
            f"Maintained key {key!r} is missing from the recover-state "
            f"playbook's WI-joriv table."
        )


def test_recover_state_playbook_warns_unlisted_keys_are_dropped() -> None:
    """The playbook must warn that unlisted keys are silently dropped so
    future agents don't discover that the hard way."""
    content = RECOVER_PLAYBOOK.read_text().lower()
    assert "silently dropped" in content or "silently drops" in content


@pytest.mark.parametrize("key", MAINTAINED_KEYS)
def test_each_maintained_key_documented_with_writer(key: str) -> None:
    """Each maintained key must have its writer documented in the playbook
    so it's clear why the key exists and what sets it."""
    content = RECOVER_PLAYBOOK.read_text()
    # Find the row starting with `| `KEY`` and check it has content past
    # the key cell.
    row_pattern = re.compile(rf"\|\s*`{re.escape(key)}`\s*\|[^|]+\|[^|]+\|")
    assert row_pattern.search(content), (
        f"Maintained key {key!r} must appear as a table row with writer "
        f"and meaning columns in the recover-state playbook."
    )
