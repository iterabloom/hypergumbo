# SPDX-License-Identifier: AGPL-3.0-or-later
"""In-repo catalogue data: the operator's opt-in, and what git says about each file.

ADR-0061 ruling 4. Catalogue data inside the repository being analysed -- the
catalogue keys of ``<repo>/.hypergumbo.toml`` (today ``io_primitives``) --
loads only when the operator opts in. INV-hamin is why: a repository that
ships a ``.hypergumbo.toml`` naming an overlay which grants
``module_completeness`` to its own egress module turned a real secret-to-network
flow into a clean-with-caveats verdict on a machine whose owner never agreed to
read that file, and nothing in the verdict named it.

TWO WAYS TO OPT IN, and deliberately no third:

* for one run, ``--in-repo-catalogues``;
* for one repository, a grant recorded here, under ``$XDG_STATE_HOME`` and
  keyed by the repository's RESOLVED PATH -- the shape backend trust uses
  (ADR-0045 ruling 7), for the same reason: two clones of one remote can hold
  different files, and it is the tree on disk that supplies the rows.

There is no ``config.toml`` key. ``$XDG_CONFIG_HOME`` is the directory people
sync between machines, and a setting there would be a standing grant to every
repository the operator ever clones -- the global switch the ruling refuses.

A DECLINE IS A DECISION (ADR-0045 ruling 8). :func:`read_in_repo_decision`
returns ``None`` for "never asked", so the run can say once what it did not
load, and goes quiet after the operator answers either way.

GIT STATE IS WHAT HYPERGUMBO CAN CHECK, so it is what it reports. It cannot
know who wrote a file in a working tree -- that is why the tier is named by
location -- but it can say whether the file is ``committed`` (tracked and
unchanged since HEAD), ``modified`` (tracked, changed locally) or
``untracked``. A directory that is not a git work tree tracks nothing, so its
files read ``untracked``.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
from pathlib import Path
from typing import Any, Dict, Mapping, Optional

from .safety_zones import repo_inspect_git

GIT_COMMITTED = "committed"
GIT_MODIFIED = "modified"
GIT_UNTRACKED = "untracked"

_STORE_DIRNAME = "catalogue-grants.d"


def grant_store_root(
    environ: Optional[Mapping[str, str]] = None,
    home: Optional[Path] = None,
) -> Path:
    """``$XDG_STATE_HOME/hypergumbo/catalogue-grants.d``, or the XDG default.

    A sibling of backend trust's ``trust.d``, not a member: that store holds
    grants to EXECUTE a repository's code and refuses anything else by design.
    """
    env = os.environ if environ is None else environ
    base = env.get("XDG_STATE_HOME")
    root = Path(base) if base else (home or Path.home()) / ".local" / "state"
    return root / "hypergumbo" / _STORE_DIRNAME


def _record_path(repo_root: Path, environ: Optional[Mapping[str, str]]) -> Path:
    resolved = str(Path(repo_root).resolve())
    key = hashlib.sha256(resolved.encode("utf-8")).hexdigest()[:16]
    return grant_store_root(environ=environ) / f"{key}.json"


def record_in_repo_decision(
    repo_root: Path,
    granted: bool,
    *,
    environ: Optional[Mapping[str, str]] = None,
) -> Path:
    """Record a grant or a decline for ``repo_root``'s in-repo catalogue data."""
    path = _record_path(repo_root, environ)
    path.parent.mkdir(parents=True, exist_ok=True)
    record = {"granted": granted, "repo_path": str(Path(repo_root).resolve())}
    path.write_text(json.dumps(record, indent=2, sort_keys=True), encoding="utf-8")
    # 0o600: which repositories an operator has agreed to take grading rules
    # from is theirs, like a trust grant.
    path.chmod(0o600)
    return path


def read_in_repo_decision(
    repo_root: Path,
    *,
    environ: Optional[Mapping[str, str]] = None,
) -> Optional[bool]:
    """``True`` granted, ``False`` declined, ``None`` never decided."""
    path = _record_path(repo_root, environ)
    if not path.is_file():
        return None
    try:
        record: Dict[str, Any] = dict(json.loads(path.read_text(encoding="utf-8")))
    except (OSError, ValueError):  # pragma: no cover - corrupt store
        # A corrupt store reads as NO DECISION, never as a grant.
        return None
    return bool(record.get("granted", False))


def is_inside(repo_root: Path, path: Path) -> bool:
    """Does ``path`` live inside the analysed repository?"""
    try:
        Path(path).resolve().relative_to(Path(repo_root).resolve())
    except ValueError:
        return False
    return True


def _git(repo_root: Path, *args: str) -> "tuple[int, str]":
    proc = repo_inspect_git(
        [shutil.which("git") or "git", *args],
        cwd=repo_root, capture_output=True, text=True, check=False,
    )
    return proc.returncode, proc.stdout


def git_state(repo_root: Path, path: Path) -> str:
    """``committed``, ``modified`` or ``untracked`` for a file in the repo."""
    root = Path(repo_root).resolve()
    rel = str(Path(path).resolve().relative_to(root))
    rc, _ = _git(root, "ls-files", "--error-unmatch", "--", rel)
    if rc != 0:
        return GIT_UNTRACKED
    _, status = _git(root, "status", "--porcelain", "--", rel)
    return GIT_MODIFIED if status.strip() else GIT_COMMITTED
