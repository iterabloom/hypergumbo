# SPDX-License-Identifier: AGPL-3.0-or-later
"""WI-fodad: every Woodpecker clone must be COMPLETE, not tree-filtered.

The bug this pins. `woodpeckerci/plugin-git` defaults `partial: true`, and its
own ``defaults.go`` then sets::

    p.Config.Depth  = 1
    p.Config.filter = "tree:0"

so the checkout is a *promisor* repository holding no tree objects beyond the
single commit it checked out. `git fetch --unshallow` restores the COMMIT
history and inherits the configured filter, so the trees stay missing. Anything
that then walks trees across history fetches them back from origin one object at
a time over HTTPS.

Measured on the CI agent, `git log --reverse -- <one path>` across 6,445
commits: **1,185,559 ms** on the first invocation and **19 ms** on the second
(by then the objects are local), against **27 ms** on a dev box with a complete
clone. The volume is a local ext4 SSD, so that 44,000x is neither disk nor
contention — it is roughly 26,000 sequential network round trips.

Why this is worth a test rather than a comment. The defect is SILENT: nothing
errors, a step just takes twenty minutes and reads as a hung runner. It cost
three wrong diagnoses (disk latency, runner contention, a cancelled pipeline)
and two "fixes" that made CI worse before the cause was found. The clone block
is also the kind of thing that regresses without anyone editing it — a plugin
bump, a copied-from-elsewhere workflow, or a new sibling workflow authored from
the old template.

Two independent gates guard it, because there are two distinct failure modes:
  * THIS test — the declaration is missing from a workflow file (static).
  * the `prepare-git` step's runtime check — the declaration is present but did
    not take effect (a plugin/server change). A static test cannot see that.

Both are DEFAULT-DENY (L54): they require the safe value to be stated, rather
than enumerating the filter values known to hurt.
"""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
WORKFLOW_DIR = REPO_ROOT / ".woodpecker"


def _workflows() -> list[Path]:
    return sorted(WORKFLOW_DIR.glob("*.yml"))


def _clone_steps(doc: dict) -> list[dict]:
    """Every clone step, normalising Woodpecker's two accepted spellings.

    Woodpecker accepts `clone:` as either a MAP of name -> step or a LIST of
    steps. Handling only the map form would let a list-form workflow regress
    silently, which is the same shape of hole this file exists to close.
    """
    clone = doc.get("clone")
    if clone is None:
        return []
    if isinstance(clone, dict):
        return [s for s in clone.values() if isinstance(s, dict)]
    if isinstance(clone, list):
        return [s for s in clone if isinstance(s, dict)]
    raise AssertionError(f"unrecognised clone block type: {type(clone)!r}")


@pytest.fixture(scope="module")
def workflows() -> list[tuple[Path, dict]]:
    """Parsed workflows, with a non-vacuity floor (L17).

    Without the floor, a renamed directory or a glob that matches nothing makes
    every assertion below pass by iterating an empty list -- the exact way a
    ratchet reports success while checking nothing.
    """
    found = _workflows()
    assert found, (
        f"no workflow files matched {WORKFLOW_DIR}/*.yml. Every assertion in "
        "this module iterates over that list and would pass vacuously."
    )
    parsed = [(p, yaml.safe_load(p.read_text())) for p in found]
    with_clone = [p.name for p, doc in parsed if _clone_steps(doc)]
    assert len(with_clone) >= 3, (
        "expected at least 3 workflows declaring a clone block (per-PR, "
        f"full-suite, nightly); found {with_clone}"
    )
    return parsed


def test_every_clone_step_disables_partial(
    workflows: list[tuple[Path, dict]],
) -> None:
    """The load-bearing assertion: no workflow may take plugin-git's default.

    `partial` must be explicitly False. Absent is NOT acceptable -- absent IS
    the bug, because the plugin's default is True.
    """
    offenders = []
    for path, doc in workflows:
        for step in _clone_steps(doc):
            settings = step.get("settings") or {}
            if settings.get("partial") is not False:
                offenders.append(
                    f"{path.name}: settings.partial="
                    f"{settings.get('partial', '<absent>')!r}",
                )
    assert not offenders, (
        "clone step(s) will inherit plugin-git's `partial: true` default, "
        "producing a --depth=1 --filter=tree:0 promisor checkout with no tree "
        "objects. Any history-wide git walk then fetches trees from origin one "
        "at a time (measured: 1,185,559 ms for a single pathspec walk).\n"
        "Add `settings: {partial: false}` to each clone step below:\n  "
        + "\n  ".join(offenders)
    )


def test_partial_false_is_not_confused_with_depth(
    workflows: list[tuple[Path, dict]],
) -> None:
    """`depth` must not be used as a stand-in for `partial`.

    Shallow and partial are independent axes and the plugin documents `depth` as
    "overwritten by partial". A workflow that sets only `depth` looks like it
    addressed clone completeness while still inheriting the tree filter -- which
    is precisely the state this repo shipped in, under a comment claiming a
    "Full clone".
    """
    for path, doc in workflows:
        for step in _clone_steps(doc):
            settings = step.get("settings") or {}
            if "depth" in settings:
                assert settings.get("partial") is False, (
                    f"{path.name} sets clone depth={settings['depth']!r} "
                    "without `partial: false`. depth does not disable the "
                    "tree:0 filter; only `partial: false` does."
                )


def test_prepare_git_asserts_the_clone_at_runtime(
    workflows: list[tuple[Path, dict]],
) -> None:
    """The static declaration is checked at run time too, where it can be false.

    This test guards the OTHER gate. A workflow can declare `partial: false` and
    still receive a filtered clone if the plugin or the server changes under it,
    and no static check can observe that. Any workflow carrying a `prepare-git`
    step must therefore also inspect `remote.origin.partialclonefilter` at run
    time; a workflow without `prepare-git` is out of scope here.
    """
    checked = []
    for path, doc in workflows:
        steps = doc.get("steps") or []
        for step in steps:
            if not isinstance(step, dict) or step.get("name") != "prepare-git":
                continue
            body = "\n".join(str(c) for c in (step.get("commands") or []))
            assert "partialclonefilter" in body, (
                f"{path.name}'s prepare-git step does not verify the clone is "
                "complete. The `partial: false` setting is unobservable from "
                "inside the pipeline unless something reads "
                "remote.origin.partialclonefilter."
            )
            checked.append(path.name)
    assert checked, (
        "no prepare-git step found in any workflow -- this test asserted "
        "nothing. Either the step was renamed (update this test) or the "
        "runtime half of the WI-fodad gate has been deleted."
    )
