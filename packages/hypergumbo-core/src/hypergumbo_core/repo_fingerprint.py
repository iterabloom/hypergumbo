# SPDX-License-Identifier: AGPL-3.0-or-later
"""Repository fingerprint: spec-defined hash of analyzed code state.

Implements ``AnalysisRun.repo_fingerprint`` per
``docs/hypergumbo-spec.md:378-384``. The field's purpose is **cache
invalidation and provenance tracking** — every IR ``AnalysisRun`` carries
a hash identifying the exact code snapshot it analyzed, so downstream
consumers can answer "was this the same repo state?" without re-running.

## Algorithm

Two branches, selected by the presence of ``repo_root/.git``:

* **Git branch** — ``sha256(git_head + sorted([(path, sha256(content_bytes))
  for each dirty file]))``.
    * ``git_head``: the HEAD commit hash.
    * "dirty file": every tracked file whose working-tree content differs
      from HEAD plus every untracked, non-ignored file. The set comes from
      ``git status --porcelain``, which honors ``.gitignore`` so build
      products and editor backups don't pollute the hash.
    * Content-hash each dirty file by reading its bytes. The spec is
      explicit (line 382): the field must change when dirty contents
      change, not when paths or mtimes change. This is exactly what makes
      this function the recommended replacement for the legacy
      ``sketch_embeddings._get_repo_state_hash`` whose path:size:mtime
      key picks up tracker ``.ops`` mtime jitter (INV-magul).

* **Non-git branch** — ``sha256(sorted([(path, sha256(content_bytes))
  for f in tree]))``.
    * The "tree" is every regular **source file** under ``repo_root`` —
      where "source file" is a file whose suffix is in
      ``_SOURCE_EXTENSIONS`` — excluding files inside ``.git``,
      ``__pycache__``, ``node_modules``, ``.venv``, or ``venv``. The
      spec's literal "all files" is interpreted as "files the analyzers
      actually consume" because the field's documented purpose is
      *cache invalidation* — output files, editor backups, and build
      products are not analyzer inputs, so their changes must not
      trigger re-analysis. (The git branch gets this for free via
      ``.gitignore`` filtering inside ``git status --porcelain``; the
      non-git branch needs an explicit allowlist to match.)

In both branches the input list is sorted to make order irrelevant; the
``sha256`` of the joined input is returned as a 64-char hex digest. An
empty directory returns ``sha256(b"")`` so callers can always stamp the
field without conditional logic.

## Scheme

The top-level ``repo_fingerprint_scheme`` declared in the spec
(``hypergumbo-repofp-v1``) covers this algorithm. Future algorithm
changes must bump the scheme version.

## Performance

Git branch: dirty-files set is typically small (<50 files) so per-file
content reads are cheap and ``git status --porcelain`` dominates.
Non-git branch: reads every source file in the tree, which can be slow
on large repos. The function is intended to run once per analysis and
the result cached by the caller; do not call it inside per-file hot
loops.
"""

from __future__ import annotations

import hashlib
import shutil
import subprocess  # nosec B404 - required for git inspection
from pathlib import Path

# Directory names excluded from the non-git branch's file walk. Mirrors
# the analyzer file-discovery convention so the fingerprint reflects what
# the analyzers actually consume.
_EXCLUDED_DIR_NAMES = frozenset({
    ".git",
    "__pycache__",
    "node_modules",
    ".venv",
    "venv",
})

# Suffixes counted as "source files" in the non-git branch. Mirrors the
# set the legacy ``sketch_embeddings._get_repo_state_hash`` used so the
# cache-invalidation semantics carry over unchanged; see module
# docstring for the rationale.
_SOURCE_EXTENSIONS = frozenset({
    ".py", ".js", ".ts", ".jsx", ".tsx",
    ".java", ".go", ".rs", ".rb",
    ".c", ".cpp", ".h", ".hpp",
    ".cs", ".php", ".swift", ".kt", ".scala",
})


def _run_git(args: list[str], cwd: Path) -> tuple[int, str]:
    """Run a git subcommand in ``cwd``, returning (rc, stdout).

    Stderr is discarded — callers only need to know whether the command
    succeeded and what it wrote to stdout. Network and shell features
    are out of scope; this is plain local repo inspection.

    Resolves the git binary via ``shutil.which`` rather than letting the
    shell search ``PATH`` — same hardening pattern the adjacent
    ``sketch_embeddings._run_git_command`` uses (Bandit S607).
    """
    git_path = shutil.which("git") or "git"
    proc = subprocess.run(  # noqa: S603  # nosec B603 - git_path resolved via shutil.which
        [git_path, *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=False,
    )
    return proc.returncode, proc.stdout


def _hash_file_content(path: Path) -> str:
    """Return ``sha256`` of ``path``'s bytes as a 64-char hex digest."""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _iter_non_git_files(repo_root: Path) -> list[Path]:
    """Walk ``repo_root`` for the non-git branch.

    Returns the sorted list of regular files under ``repo_root`` with
    any ancestor directory whose name is in ``_EXCLUDED_DIR_NAMES``
    pruned. Sorting happens at the caller's input-list level too, but
    presorting here makes the result list directly usable.
    """
    out: list[Path] = []
    for p in repo_root.rglob("*"):
        if not p.is_file():
            continue
        if p.suffix not in _SOURCE_EXTENSIONS:
            continue
        try:
            rel = p.relative_to(repo_root)
        except ValueError:  # pragma: no cover — rglob always yields children
            continue
        if any(part in _EXCLUDED_DIR_NAMES for part in rel.parts):
            continue
        out.append(p)
    out.sort()
    return out


def _git_dirty_files(repo_root: Path) -> list[Path]:
    """Return the sorted set of dirty (changed + untracked-non-ignored) files.

    Uses ``git status --porcelain -z`` to honor ``.gitignore`` (untracked
    files inside an ignored directory are excluded) and to handle paths
    with spaces / newlines safely. Entries with status ``D`` (deleted)
    are dropped — they have no content to hash and their absence is
    already reflected in the next commit's HEAD.
    """
    rc, stdout = _run_git(
        ["status", "--porcelain=v1", "-z", "--untracked-files=normal"],
        cwd=repo_root,
    )
    if rc != 0 or not stdout:
        return []

    dirty: list[Path] = []
    # `git status --porcelain -z` separates records with NUL. Each record
    # is `XY<space><path>` where XY is the two-char status code. Renames
    # ('R ' / ' R') carry the old path in the next record, which we skip
    # — we only need the new path's content.
    records = stdout.split("\0")
    i = 0
    while i < len(records):
        record = records[i]
        if not record:
            i += 1
            continue
        # First two chars are the XY code; record[2] is a space.
        xy = record[:2]
        path_part = record[3:]
        # Rename / copy: next record holds the old path; skip it.
        if "R" in xy or "C" in xy:
            i += 2
            continue
        # Deletions have nothing to hash.
        if "D" in xy:
            i += 1
            continue
        candidate = repo_root / path_part
        if candidate.is_file():
            dirty.append(candidate)
        i += 1
    dirty.sort()
    return dirty


def _git_head(repo_root: Path) -> str:
    """Return HEAD commit SHA, or empty string if no commits yet."""
    rc, stdout = _run_git(["rev-parse", "HEAD"], cwd=repo_root)
    if rc != 0:
        return ""
    return stdout.strip()


def _format_pairs(pairs: list[tuple[str, str]]) -> str:
    """Serialize ``(rel_path, content_hash)`` pairs for hashing.

    One pair per line, ``"<path>\\t<hash>"`` shape, joined by ``\\n``.
    The tab keeps the path / hash boundary unambiguous even if a path
    contains other whitespace.
    """
    return "\n".join(f"{p}\t{h}" for p, h in pairs)


def _compute_git_fingerprint(repo_root: Path) -> str:
    """Spec lines 378-382: git branch."""
    head = _git_head(repo_root)
    dirty = _git_dirty_files(repo_root)
    pairs = sorted(
        (str(p.relative_to(repo_root)), _hash_file_content(p))
        for p in dirty
    )
    payload = f"{head}\n{_format_pairs(pairs)}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _compute_non_git_fingerprint(repo_root: Path) -> str:
    """Spec line 383: non-git branch."""
    files = _iter_non_git_files(repo_root)
    pairs = sorted(
        (str(p.relative_to(repo_root)), _hash_file_content(p))
        for p in files
    )
    return hashlib.sha256(_format_pairs(pairs).encode("utf-8")).hexdigest()


def compute_repo_fingerprint(repo_root: Path) -> str:
    """Compute the spec-defined ``repo_fingerprint`` for ``repo_root``.

    See the module docstring for the algorithm; see
    ``docs/hypergumbo-spec.md:378-384`` for the source of truth. Returns
    a 64-char hex ``sha256`` digest.
    """
    if (repo_root / ".git").exists():
        return _compute_git_fingerprint(repo_root)
    return _compute_non_git_fingerprint(repo_root)
