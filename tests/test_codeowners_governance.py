# SPDX-License-Identifier: AGPL-3.0-or-later
"""Rot-guard: CODEOWNERS mirrors AGENTS.md's governance surface (GitHub syntax).

AGENTS.md §Governance Files is the authoritative list of paths whose changes
require human (@jgstern) approval and which the agent must not self-merge. The
root ``CODEOWNERS`` file is the forge-enforced mirror of that list. These two
must not drift: adding a governance file to AGENTS.md without adding it to
CODEOWNERS silently drops it from the review gate.

Three assertions lock the contract:

  1. every path AGENTS.md names as governance is covered by a CODEOWNERS entry;
  2. every CODEOWNERS entry designates at least one ``@owner``;
  3. the patterns are GitHub gitignore-globs, not Forgejo/Gitea regex — this
     file was migrated from regex (``\\.githooks/.*``) to GitHub glob
     (``/.githooks/``) during the Codeberg → GitHub migration (PR-E), and must
     not regress to regex tells (``\\.`` escapes / ``.*`` quantifiers), which
     GitHub would misinterpret.

Pure file reads; no source import, so it neither contributes nor consumes
package coverage.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
AGENTS_MD = REPO_ROOT / "AGENTS.md"
CODEOWNERS = REPO_ROOT / "CODEOWNERS"


def _norm(token: str) -> str:
    """Reduce an AGENTS.md path or a CODEOWNERS pattern to a comparable stem.

    Strips markdown backticks, leading/trailing ``/``, and the ``/**`` recursion
    suffix so ``.githooks/**`` (AGENTS.md) and ``/.githooks/`` (CODEOWNERS)
    normalize to the same ``.githooks``.
    """
    return token.strip().strip("`").strip("/").replace("/**", "").rstrip("/")


def _governance_paths() -> set[str]:
    line = next(
        ln
        for ln in AGENTS_MD.read_text().splitlines()
        if ln.lstrip().startswith("- **Governance Files:**")
    )
    return {_norm(t) for t in re.findall(r"`([^`]+)`", line)}


def _codeowners_entries() -> list[tuple[str, list[str]]]:
    entries: list[tuple[str, list[str]]] = []
    for raw in CODEOWNERS.read_text().splitlines():
        ln = raw.strip()
        if not ln or ln.startswith("#"):
            continue
        parts = ln.split()
        entries.append((parts[0], parts[1:]))
    return entries


def test_codeowners_covers_agents_governance_list() -> None:
    gov = _governance_paths()
    assert gov, "no governance paths parsed from AGENTS.md — parser drifted"
    owned = {_norm(p) for p, _ in _codeowners_entries()}
    missing = gov - owned
    assert not missing, (
        f"CODEOWNERS does not cover governance paths from AGENTS.md: "
        f"{sorted(missing)}"
    )


def test_every_codeowners_entry_has_an_owner() -> None:
    for pattern, owners in _codeowners_entries():
        assert owners, f"CODEOWNERS entry {pattern!r} designates no owner"
        assert all(o.startswith("@") for o in owners), (pattern, owners)


def test_codeowners_uses_github_glob_not_forgejo_regex() -> None:
    for pattern, _ in _codeowners_entries():
        assert ".*" not in pattern, f"regex quantifier in CODEOWNERS: {pattern!r}"
        assert "\\" not in pattern, f"regex escape in CODEOWNERS: {pattern!r}"
