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
SCANNED_DIRS = [
    "scripts",
    ".githooks",
    ".agent/hooks",
    "packages/hypergumbo-tracker/src",
    "packages/hypergumbo-core/src",
]

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
            if p.is_file() and not p.name.endswith((".pyc", ".json")):
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
        if _TOKENS.search(text):
            offenders.append(str(p.relative_to(REPO_ROOT)))
    assert not offenders, (
        "the CI failover is retired (WI-hajif); these still reference its "
        f"machinery: {offenders}"
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
