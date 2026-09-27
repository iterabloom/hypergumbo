# SPDX-License-Identifier: AGPL-3.0-or-later
"""The four pip-audit ``--ignore-vuln`` lists must stay in lockstep.

Background
----------
The dependency audit runs in four places, each with its own hand-copied
ignore list: per-PR CI (``.github/workflows/ci.yml``), the dormant Woodpecker
pipeline (``.woodpecker/woodpecker.yml``), the release gate
(``.github/workflows/release.yml``) and the local go/no-go script
(``scripts/release-check``). Each file's comments claimed the lists were
"identical" or "kept in sync", and nothing checked it.

The lists are an allowance, so drift in either direction is a defect. A list
that is LONGER than the others accepts advisories the other gates reject; a
list that keeps an id after the advisory stops firing is a dead ignore, which
hides the NEXT advisory filed under that id (WI-totak retired a 24-id
torch / transformers / joblib carve-out for exactly that reason).

What is pinned
--------------
* The three CI lists are the same set.
* ``release-check`` is that set plus its declared LOCAL-ONLY carve-outs (no-fix
  advisories in packages only a dev box carries), and nothing else.

Whether an id still fires cannot be checked offline; that is the WI-totak
re-audit cadence, not this test.
"""
# covers: .github/workflows/ci.yml, .github/workflows/release.yml, .woodpecker/woodpecker.yml, scripts/release-check

from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

CI_FILES = (
    ".github/workflows/ci.yml",
    ".github/workflows/release.yml",
    ".woodpecker/woodpecker.yml",
)

#: No-fix advisories in packages absent from every CI audit env (diskcache via
#: llama_cpp_python, nltk via safety). Named here so adding a third is a
#: visible decision, not a silent divergence.
RELEASE_CHECK_LOCAL_ONLY = frozenset({"CVE-2025-69872", "PYSEC-2026-3740"})

_IGNORE = re.compile(r"--ignore-vuln\s+(\S+)")


def _ids_in(text: str) -> frozenset[str]:
    """The ids passed to ``--ignore-vuln`` in ``text``, comment lines excluded."""
    ids: set[str] = set()
    for line in text.splitlines():
        if not line.lstrip().startswith("#"):
            ids.update(_IGNORE.findall(line))
    return frozenset(ids)


def _ignored(relpath: str) -> frozenset[str]:
    return _ids_in((REPO_ROOT / relpath).read_text())


def test_every_ci_audit_ignores_the_same_ids() -> None:
    lists = {f: _ignored(f) for f in CI_FILES}
    assert all(lists.values()), lists  # reach: each file has an audit command
    reference = lists[CI_FILES[0]]
    for relpath, ids in lists.items():
        assert ids == reference, (
            f"{relpath} differs from {CI_FILES[0]}: "
            f"extra {sorted(ids - reference)}, missing {sorted(reference - ids)}"
        )


def test_release_check_is_ci_plus_its_declared_local_carve_outs() -> None:
    ci = _ignored(CI_FILES[0])
    local = _ignored("scripts/release-check")
    assert local == ci | RELEASE_CHECK_LOCAL_ONLY, (
        f"extra {sorted(local - ci - RELEASE_CHECK_LOCAL_ONLY)}, "
        f"missing {sorted((ci | RELEASE_CHECK_LOCAL_ONLY) - local)}"
    )


def test_comments_are_not_read_as_ignores() -> None:
    """release-check documents each ignore in a ``#   --ignore-vuln X`` comment;
    those lines must not count, or a documented-but-removed id would pass."""
    text = "#   --ignore-vuln GONE-1  why\n    --ignore-vuln LIVE-1 \\\n"
    assert _ids_in(text) == {"LIVE-1"}
