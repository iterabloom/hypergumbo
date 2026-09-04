# SPDX-License-Identifier: MPL-2.0
"""Out-of-repo write-ahead journal for tracker ops — the durability substrate.

Pending tracker ops live in the git working tree (``.agent/.../.ops/``) until
auto-sync commits and pushes them. That makes them collateral for any
working-tree-destroying command — ``git reset --hard`` (the one that bit a
human's approval messages), ``git checkout`` to a stale base, ``git clean -fd``
— and the historical fixes (WI-buhov, INV-dalup, INV-lovih) each hardened only
ONE path (auto-pr's rebase, auto-sync's cleanup), which is whack-a-mole. This
module closes the whole class structurally instead of per-path: every op is
mirror-appended to a journal at a location **git cannot see** (default
``~/hypergumbo_lab_notebook/tracker-ops-journal/<repo-id>/``), so there is no
in-repo git command that can lose it. The journal is the durable write-ahead
log; the working-tree ``.ops`` files are a derived cache that :func:`recover`
rebuilds by op-block union.

Design properties:

* **Structural, not a rule.** The journal root is outside every git working
  tree, so the op *data* survives any working-tree git command — empirically
  ``reset --hard``, ``checkout``, ``clean -fdx``, even ``rm -rf .git``. For the
  common vectors (``reset --hard`` / ``checkout`` / ``clean``) ``.git`` survives,
  so ``tracker reconcile`` runs directly (``tracker recover`` is the
  deprecated journal-only half of it). After ``rm -rf .git`` the *data* is still
  safe under the journal root, but ``recover`` (which locates the repo via
  ``.git``) can't auto-run until the repo is re-init'd / re-cloned — the journal
  is keyed by the toplevel's absolute path, so recovery is then a one-liner.
* **Rides the existing lock.** The write happens inside ``Store``'s existing
  per-file flock critical section, so it adds no new lock and is atomic with the
  in-repo append.
* **Best-effort, never fatal.** A journal failure (unwritable ``$HOME``, full
  disk) degrades silently — the in-repo ``.ops`` write is still the primary
  record, so a mutation never fails because the safety net did.
* **Per-clone keyed.** ``<repo-id>`` is ``sha256(abspath(toplevel))[:12]`` so
  multiple clones of the same remote get distinct journals (no cross-clone op
  bleed).

Recovery uses an **op-block** union (:func:`_union_op_blocks`) — it dedups whole
ops (the semantic unit of an append-only ``.ops`` log) rather than individual
lines. For the current format this is *equivalent* to a line-level union:
``store._serialize_op`` stamps a per-op ``# <nonce>`` comment on every non-empty
line (with scalar folding disabled via ``width=sys.maxsize``), so every line is
unique to its op and a line-level dedup never collides. Op-block is chosen as
the single canonical restore primitive so the journal and the ``do_sync`` /
``do_merge`` restorers share *one* implementation — not because the line-level
union corrupts the real format (it does not). The journal is the durable source
all of them read from.
"""
from __future__ import annotations

import fcntl
import hashlib
import os
from dataclasses import dataclass, field
from pathlib import Path

#: Env override for the journal root — used by tests and to relocate the WAL.
JOURNAL_ROOT_ENV = "HYPERGUMBO_TRACKER_JOURNAL_ROOT"
_DEFAULT_ROOT_PARTS = ("hypergumbo_lab_notebook", "tracker-ops-journal")


@dataclass
class RecoverResult:
    """Outcome of a :func:`recover` pass.

    ``restored`` holds the repo-relative paths of ``.ops`` files that were
    rewritten (recreated or op-block-union-extended); an empty list means the
    worktree already matched the journal.
    """

    journal_dir: Path
    restored: list[str] = field(default_factory=list)


def journal_root() -> Path:
    """Return the journal root directory.

    Honors the :data:`JOURNAL_ROOT_ENV` override (tests, relocation); otherwise
    ``~/hypergumbo_lab_notebook/tracker-ops-journal``.
    """
    override = os.environ.get(JOURNAL_ROOT_ENV)
    if override:
        return Path(override)
    return Path.home().joinpath(*_DEFAULT_ROOT_PARTS)


def _repo_id(repo_root: Path) -> str:
    """Stable 12-hex id for a repo toplevel (distinct per absolute path)."""
    return hashlib.sha256(str(repo_root).encode("utf-8")).hexdigest()[:12]


def _repo_root_for(ops_filepath: Path) -> Path | None:
    """Resolved repo toplevel containing ``ops_filepath``, or None if non-git."""
    from .store import _find_git_dir  # lazy: store imports this module

    git_dir = _find_git_dir(ops_filepath.resolve())
    if git_dir is None:
        return None
    return git_dir.parent.resolve()


def journal_path_for(ops_filepath: Path) -> Path | None:
    """Journal mirror path for an in-repo ``.ops`` file, or None if unresolvable.

    None when ``ops_filepath`` is not under a git repo (mirroring is then a
    no-op — there is no durable home to compute).
    """
    repo_root = _repo_root_for(ops_filepath)
    if repo_root is None:
        return None
    try:
        rel = ops_filepath.resolve().relative_to(repo_root)
    except ValueError:  # pragma: no cover - the ops file is always under its repo
        return None
    return journal_root() / _repo_id(repo_root) / rel


def mirror_op(ops_filepath: Path, serialized: str) -> None:
    """Best-effort mirror-append ``serialized`` to the out-of-repo journal.

    ``serialized`` must be the exact bytes appended to the in-repo ``.ops`` file
    so the journal is byte-faithful and op-block-union recovery is lossless. Never
    raises: a journal failure degrades silently rather than failing the
    already-committed in-repo mutation.
    """
    try:
        jpath = journal_path_for(ops_filepath)
        if jpath is None:
            return
        jpath.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(jpath, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o644)
        try:
            os.write(fd, serialized.encode("utf-8"))
            os.fsync(fd)
        finally:
            os.close(fd)
    except Exception:
        # Strictly best-effort: this side effect must NEVER fail the user's
        # mutation (the in-repo .ops write already succeeded). Catch broadly,
        # not just OSError — the default ``journal_root()`` calls ``Path.home()``
        # which raises ``RuntimeError`` (not OSError) when ``$HOME`` is unset and
        # there's no passwd entry (containers, minimal cron/CI), and
        # ``resolve()`` raises ``RuntimeError`` on a symlink loop. Letting either
        # escape would crash the very write the journal exists to protect.
        return


def _split_op_blocks(content: str) -> list[str]:
    """Split ``.ops`` content into op blocks, one per top-level ``- `` item.

    Each block is the exact bytes from one ``- `` line (start of a YAML list
    item) up to the next, preserving every line of the op intact.
    """
    blocks: list[str] = []
    current: list[str] = []
    for line in content.splitlines(keepends=True):
        if line.startswith("- ") and current:
            blocks.append("".join(current))
            current = [line]
        else:
            current.append(line)
    if current:
        blocks.append("".join(current))
    return blocks


def _union_op_blocks(target: str, backup: str) -> str:
    """Order-preserving union of ``.ops`` content at the OP-BLOCK granularity.

    Tracker ``.ops`` files are append-only logs of YAML list items (ops). This
    unions them at the op-block level — whole ops are deduped (identical ops,
    same ``clock``+``nonce``, serialize byte-identically) rather than individual
    lines. For the current format that is equivalent to a line-level union,
    because ``_serialize_op``'s per-op ``# <nonce>`` comment on every line makes
    every line unique to its op (so a line-dedup never collides); op-block is
    chosen as the one canonical primitive shared with the sync restorers, at the
    op granularity. The op compiler orders by Lamport clock, so appending
    backup-only blocks after the target's is safe.

    Precondition: dedup is by byte-exact block, which is sound because both the
    worktree ``.ops`` and the journal are written with the *same* ``serialized``
    bytes for a given op (store.py create/append sites and :func:`mirror_op`).
    Were a future path to re-serialize an op with even one differing byte, this
    would keep both copies — and the ``discuss`` fold appends unconditionally
    with no compile-time nonce backstop, so a byte-divergent duplicate discuss
    op would surface as a duplicated message. No current path re-serializes a
    worktree ``.ops`` after append, so this cannot arise today; a parsed
    ``(clock, nonce)`` key would harden it if that invariant ever weakens.
    """
    seen: set[str] = set()
    out: list[str] = []
    for content in (target, backup):
        for block in _split_op_blocks(content):
            if block not in seen:
                seen.add(block)
                out.append(block)
    return "".join(out)


def _locked_union_restore(worktree_file: Path, journal_content: str) -> bool:
    """Union ``journal_content`` into ``worktree_file`` under Store's per-file lock.

    Store appends to a worktree ``.ops`` file under ``fcntl.flock(fd, LOCK_EX)`` on
    that file's own fd (``store.py`` create / ``_append_op``). :func:`recover` does a
    whole-file read-modify-write, so without taking the SAME per-inode lock a
    concurrent append landing between recover's read and its rewrite is clobbered
    (INV-hakuv). This acquires that identical ``LOCK_EX`` around the read → union →
    rewrite, so recover serializes with appends: a concurrent append blocks until
    recover releases, then lands on top.

    The rewrite is IN PLACE — ``truncate`` + ``write`` on the held fd — never via
    ``os.replace``/rename, which would swap the inode out from under the lock and
    leave Store's flocked ``O_APPEND`` writing to a now-unlinked inode (worse than
    the original race). The fd is opened ``O_RDWR | O_CREAT`` (mode ``0o664`` with
    the umask cleared, matching Store's append site) so a worktree ``.ops`` the
    journal must recreate stays group-writable for two-user (agent + human) sharing.

    Returns ``True`` iff the file's content changed (an op was restored).
    """
    worktree_file.parent.mkdir(parents=True, exist_ok=True)
    old_umask = os.umask(0)
    try:
        fd = os.open(worktree_file, os.O_RDWR | os.O_CREAT, 0o664)
    finally:
        os.umask(old_umask)
    with os.fdopen(fd, "r+", encoding="utf-8") as f:
        fcntl.flock(f.fileno(), fcntl.LOCK_EX)
        try:
            current = f.read()
            merged = _union_op_blocks(current, journal_content)
            if merged == current:
                return False
            f.seek(0)
            f.truncate()
            f.write(merged)
            f.flush()
            os.fsync(f.fileno())
            return True
        finally:
            fcntl.flock(f.fileno(), fcntl.LOCK_UN)


def recover(repo_root: Path) -> RecoverResult:
    """Union-restore journalled ops back into ``repo_root``'s worktree ``.ops``.

    For each journal file under ``<journal_root>/<repo_id>/``: op-block-union its
    content into the corresponding worktree ``.ops`` file, recreating the file if
    the worktree lost it entirely. The per-file rewrite takes Store's ``LOCK_EX``
    flock (:func:`_locked_union_restore`), so it serializes with concurrent Store
    appends instead of clobbering them (INV-hakuv). Idempotent — a worktree file
    that already contains every journalled op is left untouched (so a clean repo
    recovers to a no-op). recover only ever reads the journal and writes the
    worktree, never the reverse: the journal is the durable, append-only source.
    """
    repo_root = repo_root.resolve()
    jdir = journal_root() / _repo_id(repo_root)
    result = RecoverResult(journal_dir=jdir)
    if not jdir.is_dir():
        return result
    for dirpath, _dirs, files in os.walk(jdir):
        for fname in sorted(files):
            if not fname.endswith(".ops"):  # pragma: no cover - journal holds only .ops
                continue
            jfile = Path(dirpath) / fname
            rel = jfile.relative_to(jdir)
            # Explicit utf-8 (matching mirror_op's utf-8 write and the worktree
            # read in _locked_union_restore) — robust on non-utf-8 locales.
            journal_content = jfile.read_text(encoding="utf-8")
            if not journal_content:
                # A 0-byte journal file (mirror_op created it via O_CREAT but the
                # write failed) carries no op — skip it rather than materialize a
                # spurious empty worktree .ops.
                continue
            if _locked_union_restore(repo_root / rel, journal_content):
                result.restored.append(str(rel))
    return result
