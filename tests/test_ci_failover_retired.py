# SPDX-License-Identifier: AGPL-3.0-or-later
"""WI-hajif: the self-hosted-Forgejo CI failover is RETIRED — recurrence guard.

Why this file exists rather than a one-time sweep. The failover was not one
script: it was a *layer*. ``scripts/ci-failover`` stood the forge up, but
``scripts/lib/failover-git-shim.sh`` rewrote ``git status`` output,
``scripts/smart-test`` re-anchored its merge-base on ``selfh/dev``,
``scripts/install-hooks`` installed the shim, and ``scripts/auto-pr`` switched
its push/flush remote and its ops-diff base. Deleting the entry point while
leaving the limbs is exactly the half-migration this item exists to end, and the
limbs are individually plausible-looking (each reads like ordinary
remote-selection logic), so nothing but a gate will catch a re-introduction.

The replacement is NOT another forge. Per the validated 2026-07-23 outage SOP
(``github-outage-ci-continuity-sop.md``, drill/Phase-1 gate 9e): ``woodpecker
exec`` runs the entire gate offline with no server and no network, and a plain
bare git repo we control holds commits until the primary forge returns. That is
an architecture decision AND a values decision — standing a Forgejo back up
would re-introduce the very dependency the GitHub migration exists to remove,
given that vendor's prohibition on AI-assisted contribution.

Scope note: this guard deliberately does NOT forbid the string "codeberg"
everywhere. Codeberg survives as a passive ``git push --mirror`` backup target,
release/CHANGELOG history legitimately cites past Codeberg PR numbers, and
``sketch.py`` recognises ``codeberg.org`` as a forge host *in analysed repos* —
removing that would regress a product feature. What is forbidden is the failover
MACHINERY: the state file, the ``selfh`` remote, and the two deleted files.
"""
from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

# The two files whose entire purpose was the failover.
RETIRED_FILES = [
    "scripts/ci-failover",
    "scripts/lib/failover-git-shim.sh",
    "docs/sops/CI_FAILOVER.md",
]

# Directories whose sources must carry no failover awareness.
#
# `.agent/hooks` and `packages/*/src` are here because the FIRST draft of this
# guard scanned only scripts/ and .githooks/ — and went green while
# `.agent/hooks/_shared/stop_logic.sh` still CALLED the deleted
# `apply_failover_overrides`, and the tracker package still parsed the flag file
# into a `_FailoverState`. A gate's "0 violations" is only ever scoped to what it
# actually scans (L23), and the failover reached further than the forge scripts.
# TEST TREES ARE SCANNED TOO (WI-kasin), and the reason is the third instance
# of this guard's own header lesson. The gate scanned five source trees and no
# test tree, so it passed while TWO tests in `packages/hypergumbo-core/tests`
# still ASSERTED the retired string was present — demanding exactly what this
# file forbids. Both failed on clean dev and neither was ever selected by the
# per-PR gate, because nothing but `scripts/smart-test` maps to them (L48: a
# test that cannot be selected is indistinguishable from one that passes).
# A retirement sweep that updates the code and the recurrence gate but leaves
# the tests behind produces a suite that contradicts itself, with both halves
# green in CI.
SCANNED_DIRS = [
    "scripts",
    ".githooks",
    ".agent/hooks",
    "packages/hypergumbo-tracker/src",
    "packages/hypergumbo-core/src",
    "packages/hypergumbo-core/tests",
    "tests",
]

# A test that asserts the retirement must NAME the retired tokens to assert
# their absence, so the guard would flag itself and every sibling written to
# check the same property. Exempt those by path, deliberately narrow: an
# exemption keyed on "the file mentions the retirement" would let any file
# opt out by adding a comment.
SCAN_EXEMPT = {
    "tests/test_ci_failover_retired.py",
    "tests/test_top_level_test_map.py",
}

# VESTIGIAL RESIDUE, declared as data rather than left invisible (L23).
#
# Two KINDS of reference survive a retirement and they are not equally bad.
# A test that ASSERTS the machinery must exist is broken the moment it is
# retired — two of those were found failing on clean dev and are fixed.
# A test that defensively SCRUBS a dead env var, or stubs a dead function to a
# no-op inside a fixture, asserts nothing and cannot resurrect anything; it is
# untidy, not wrong. The token regex cannot tell them apart, so the second kind
# is listed here instead of silently widening the regex or quietly narrowing
# the scan.
#
# THIS LIST MAY ONLY SHRINK. `test_residue_list_has_no_dead_entries` fails if an
# entry stops referencing the tokens, so a cleanup cannot leave the list stale,
# and a NEW offender is not covered by it and fails the main gate. Tracked for
# removal on WI-kasin's follow-up.
KNOWN_VESTIGIAL_RESIDUE = {
    # env-scrub lists naming the retired forge's credential vars
    "tests/_forge_github_harness.py",
    "tests/test_autopr_result_sentinel.py",
    "tests/test_autopr_title_desc_flags.py",
    "tests/test_autopr_tracker_id.py",
    "tests/test_forge_backend_github.py",
    # fixture shell script stubbing apply_failover_overrides to a no-op
    "tests/test_merge_pr_close.py",
}

# Config files that must not advertise the retired machinery.
SCANNED_FILES = ["CODEOWNERS", ".env.template"]

# The failover's load-bearing tokens: the state file that switched behaviour, the
# git remote it switched to, the override entry point, and its credential env.
# `FAILOVER_ACTIVE` is listed separately from `CI_FAILOVER_ACTIVE` because the
# shell variable and the flag file are spelled differently and the first draft
# caught only the latter.
_TOKENS = re.compile(
    r"CI_FAILOVER_ACTIVE|\bFAILOVER_ACTIVE\b|apply_failover_overrides"
    r"|SELFHOSTED_FORGEJO|_FailoverState|_detect_failover|failover-git-shim"
)
_SELFH_REMOTE = re.compile(r"\bselfh\b")


def _scanned_files() -> list[Path]:
    out: list[Path] = []
    for f in SCANNED_FILES:
        p = REPO_ROOT / f
        if p.is_file():
            out.append(p)
    for d in SCANNED_DIRS:
        root = REPO_ROOT / d
        if not root.is_dir():
            continue
        for p in sorted(root.rglob("*")):
            if not p.is_file() or p.name.endswith((".pyc", ".json")):
                continue
            if str(p.relative_to(REPO_ROOT)) in SCAN_EXEMPT:
                continue
            out.append(p)
    return out


def test_scanned_tree_is_non_empty() -> None:
    """Non-vacuity floor (L17): a guard that scans nothing passes for free."""
    files = _scanned_files()
    assert len(files) > 20, f"expected a populated script tree, got {len(files)}"
    # and that it actually reaches OUTSIDE scripts/ — the first draft did not.
    roots = {str(f.relative_to(REPO_ROOT)).split("/")[0] for f in files}
    for required in (".agent", "packages", "scripts"):
        assert required in roots, f"guard does not reach {required}: {sorted(roots)}"

    # TOP-LEVEL ROOTS ARE NOT ENOUGH, and this floor learned that from its own
    # mutation test. Checking only `packages` is satisfied by the src tree
    # alone, so dropping `packages/hypergumbo-core/tests` from SCANNED_DIRS —
    # the tree where two contradicting tests actually sat — left this floor
    # green. A floor that a real regression can walk under is not a floor
    # (L18: widen a gate by PROPERTY, not just by scope).
    rels = {str(f.relative_to(REPO_ROOT)) for f in files}
    for tree in ("packages/hypergumbo-core/tests", "tests"):
        assert any(r.startswith(tree + "/") for r in rels), (
            f"guard does not reach {tree}; a retirement sweep that updates the "
            "code but leaves a test asserting the opposite would pass"
        )


def test_retired_files_are_gone() -> None:
    present = [f for f in RETIRED_FILES if (REPO_ROOT / f).exists()]
    assert not present, (
        "WI-hajif retired the self-hosted-Forgejo failover; these files must be "
        f"deleted, not repointed: {present}. The outage replacement is "
        "`woodpecker exec` + the bare git mirror (2026-07-23 SOP)."
    )


def test_no_source_references_failover_machinery() -> None:
    """No source may name the flag file, the override entry point, or its env."""
    offenders: list[str] = []
    for p in _scanned_files():
        try:
            text = p.read_text(encoding="utf-8", errors="ignore")
        except OSError:  # pragma: no cover - unreadable file
            continue
        rel = str(p.relative_to(REPO_ROOT))
        if _TOKENS.search(text) and rel not in KNOWN_VESTIGIAL_RESIDUE:
            offenders.append(rel)
    assert not offenders, (
        "the CI failover is retired (WI-hajif); these still reference its "
        f"machinery: {offenders}"
    )


def test_residue_list_has_no_dead_entries() -> None:
    """The residue list may only SHRINK (L33).

    A suppression list that outlives what it suppresses is worse than no list:
    it reads as "known and accepted" while covering nothing, and the next
    genuine offender to land at that path inherits the exemption. Fail when an
    entry no longer references the tokens, so a cleanup has to delete its line.
    """
    stale: list[str] = []
    for rel in sorted(KNOWN_VESTIGIAL_RESIDUE):
        p = REPO_ROOT / rel
        if not p.is_file():
            stale.append(f"{rel} (file gone)")
            continue
        if not _TOKENS.search(p.read_text(encoding="utf-8", errors="ignore")):
            stale.append(f"{rel} (cleaned)")
    assert not stale, (
        "these entries no longer reference the retired machinery and must be "
        f"removed from KNOWN_VESTIGIAL_RESIDUE: {stale}"
    )


def test_no_script_targets_the_selfh_remote() -> None:
    """No script may push to, fetch from, or anchor on the `selfh` remote."""
    offenders: list[str] = []
    for p in _scanned_files():
        try:
            text = p.read_text(encoding="utf-8", errors="ignore")
        except OSError:  # pragma: no cover - unreadable file
            continue
        if _SELFH_REMOTE.search(text):
            offenders.append(str(p.relative_to(REPO_ROOT)))
    assert not offenders, (
        "the `selfh` failover remote is retired (WI-hajif); these still "
        f"reference it: {offenders}"
    )
