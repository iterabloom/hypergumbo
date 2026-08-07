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
  for each source file in the tree]))``.
    * ``git_head``: the HEAD commit hash, so two trees with identical
      content on different commits stay distinguishable.
    * The file set is the same working-tree walk the non-git branch uses.
    * Content-hash each file by reading its bytes. The spec is explicit
      (line 382): the field must change when contents change, not when
      paths or mtimes change. This is what makes this function the
      recommended replacement for the legacy
      ``sketch_embeddings._get_repo_state_hash`` whose path:size:mtime
      key picks up tracker ``.ops`` mtime jitter (INV-magul).
    * **This branch used to derive a "dirty file" set from ``git status
      --porcelain``. It no longer runs git status at all, for security
      rather than tidiness.** That command executes programs the TARGET
      repo controls, demonstrated with a canary via three independent
      vectors — ``core.fsmonitor``; a bare
      ``.git/hooks/post-index-change`` (which fires with no config keys
      set, so auditing ``.git/config`` is not a mitigation); and
      ``filter.<driver>.clean`` armed by an in-tree ``.gitattributes``,
      which survived ``core.fsmonitor=false`` + ``core.hooksPath=/dev/null``
      + ``core.attributesFile=/dev/null`` + ``--literal-pathspecs`` +
      ``--no-optional-locks`` applied together. Since the attacker names
      the filter driver, no hardening list closes it. The dirty set was
      only an optimization; hashing the tree is strictly more precise.
      Pinned by ``test_repo_fingerprint.TestNoCodeExecutionFromTargetRepo``.
    * COST OF THAT CHANGE, measured rather than assumed: 0.12s on pretix,
      1.7s on hypergumbo, against analyses that run for minutes. The
      ``.gitignore`` filtering the old path got for free is replaced by the
      ``_SOURCE_EXTENSIONS`` allowlist, so build products still cannot
      pollute the hash — but a gitignored *source* file now counts where it
      previously did not.

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
      trigger re-analysis. (Both branches now share this allowlist; the
      git branch used to get equivalent filtering from ``.gitignore`` via
      ``git status --porcelain``, which was removed for the security
      reason recorded above.)

In both branches the input list is sorted to make order irrelevant; the
``sha256`` of the joined input is returned as a 64-char hex digest. An
empty directory returns ``sha256(b"")`` so callers can always stamp the
field without conditional logic.

## Scheme

The top-level ``repo_fingerprint_scheme`` declared in the spec
(``hypergumbo-repofp-v2``) covers this algorithm and its field rendering.
Future algorithm changes must bump the scheme version. v2 (WI-bosog) added
the ``sha256:`` prefix to the AR-record FIELD (:func:`compute_repo_fingerprint_field`),
uniform with run_signature / config_fingerprint; the bare digest returned by
:func:`compute_repo_fingerprint` (the colon-free cache-dir path segment) is
unchanged.

## Performance

Both branches read every source file in the tree, which can be slow on
large repos — measured at 0.12s (pretix, 1366 files) and 1.7s
(hypergumbo, 1087 files), against analyses that run for minutes. The git
branch was previously ~0.01s because it hashed only the dirty set; that
optimization is gone deliberately (see the security note above) and the
cost was priced before taking it. The function is intended to run once
per analysis and the result cached by the caller; do not call it inside
per-file hot loops.
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

# Suffixes counted as "source files". Mirrors the set the legacy
# ``sketch_embeddings._get_repo_state_hash`` used so the cache-invalidation
# semantics carry over unchanged; see module docstring for the rationale.
#
# KNOWN DRIFT, filed separately rather than fixed here: this list names no
# ``.yaml``, ``.toml``, ``.json``, ``.xml`` or ``Dockerfile``, all of which
# hypergumbo has analyzers for, so editing a ``pyproject.toml`` or an
# Ansible playbook does not invalidate the cache. Deriving the set from
# ``taxonomy.get_language`` was tried and REVERTED: ``.json`` is both a
# language hypergumbo analyses AND the extension of hypergumbo's own output
# artifacts, so classifying by extension alone re-pollutes the cache key on
# every run when a user writes ``--out`` into their repo — the exact defect
# ``test_non_source_files_excluded`` exists to prevent. Fixing this properly
# needs an artifact-name exclusion, not an extension rule, and that is a
# cache-semantics change that has no business riding along with a security
# fix.
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


# Fixed sentinel digest for files that exist but cannot be read (permission
# denied, transient I/O error, or a TOCTOU race where the file vanished
# between the directory walk and the read). §17 / WI-madal: fail open — an
# unreadable file must not abort the whole fingerprint. The file's path is
# already part of the ``(path, hash)`` pair the caller hashes, so a single
# path-independent sentinel still records *which* file was unreadable while
# keeping the fingerprint deterministic for a given unreadable-set. Readable
# repos are byte-identical to before — only unreadable files take this path.
_UNREADABLE_CONTENT_SENTINEL = hashlib.sha256(
    b"\x00hypergumbo:unreadable-file\x00"
).hexdigest()


def _hash_file_content(path: Path) -> str:
    """Return ``sha256`` of ``path``'s bytes as a 64-char hex digest.

    Returns ``_UNREADABLE_CONTENT_SENTINEL`` when the bytes cannot be read
    (``OSError`` — covers ``PermissionError`` and the ``FileNotFoundError``
    from a walk/read TOCTOU race). This is the single chokepoint both the
    git and non-git branches read through, so the fail-open guard lives
    here once rather than at each call site.
    """
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return _UNREADABLE_CONTENT_SENTINEL


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
    """Spec lines 378-382: git branch.

    NO LONGER USES ``git status``, and that is a security property rather
    than a refactor. Running ``git status`` with cwd inside a target repo
    executes programs that repo controls — demonstrated with a canary via
    three independent vectors: ``core.fsmonitor``, a bare
    ``.git/hooks/post-index-change`` (which fires with *no* config keys set,
    so auditing ``.git/config`` is not a mitigation), and
    ``filter.<driver>.clean`` armed by an in-tree ``.gitattributes``. The
    last of those was measured to survive ``core.fsmonitor=false``,
    ``core.hooksPath=/dev/null``, ``core.attributesFile=/dev/null``,
    ``--literal-pathspecs`` and ``--no-optional-locks`` applied together;
    suppressing it requires naming the driver, and the attacker picks the
    name. A hardening list cannot close this, so the command is gone.

    The dirty-file set was only ever an OPTIMIZATION — hash HEAD plus the
    few changed files instead of the whole tree. Hashing the working tree
    directly is strictly more precise and needs no subprocess. Measured
    cost: 0.12s on pretix, 1.7s on hypergumbo, against analyses that run for
    minutes. HEAD is still mixed in, so two trees with identical content on
    different commits stay distinguishable.
    """
    head = _git_head(repo_root)
    files = _iter_non_git_files(repo_root)
    pairs = sorted(
        (str(p.relative_to(repo_root)), _hash_file_content(p))
        for p in files
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


def compute_repo_fingerprint_field(repo_root: Path) -> str:
    """Render the AnalysisRun ``repo_fingerprint`` FIELD value (WI-bosog).

    The bare :func:`compute_repo_fingerprint` digest doubles as a filesystem
    path segment — the analysis cache-dir ``<state_hash>`` — so it must stay
    colon-free. The AR-record provenance FIELD, however, was the lone identity
    field rendered as bare 64-hex while its siblings ``run_signature`` and
    ``config_fingerprint`` carry the ``sha256:`` scheme prefix (``sha256:<16
    hex>``). That non-uniformity meant a consumer could not strip a scheme
    prefix uniformly across the AR's identity fields. This helper stamps the
    prefix onto the FIELD while leaving the cache-dir digest untouched. The
    full 64-hex digest is retained — ``repo_fingerprint`` is a content hash of
    the code snapshot, not a derived 16-hex signature — so the field is
    ``sha256:<64hex>`` (the ``pass_version`` rendering convention). Governed by
    ``schema.REPO_FINGERPRINT_SCHEME`` (bumped to v2 when the prefix landed).
    """
    return "sha256:" + compute_repo_fingerprint(repo_root)
