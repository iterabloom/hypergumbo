# SPDX-License-Identifier: AGPL-3.0-or-later
"""Hypergumbo's internal write wrappers — declare each write's safety zone.

These thin wrappers exist for one purpose: give the verify-claims taint
analysis distinct callees per safety zone. The structural taint pass
matches sinks by callee name, not by argument value, so two
``path.write_text(...)`` call sites are indistinguishable to it even when
one writes to ``~/.cache/hypergumbo/`` and the other to a user-supplied
``--out`` path. Wrapping each write in a uniquely-named function gives
each safety zone a distinct sink callee, which the project-local catalog
at ``docs/hypergumbo-self-catalog/`` declares in its corresponding zone.

Most wrappers are otherwise transparent — they delegate to
``Path.write_text`` / ``Path.write_bytes`` / ``shutil.copy2`` /
``np.save`` etc. with no behavior change relative to a direct call.
The DESTRUCTIVE ones are not: :func:`cache_rmtree`, :func:`cache_unlink`,
:func:`cache_write_zip`, :func:`tmp_artifact_rmtree` and
:func:`install_artifact_unlink` first resolve the path and raise
:class:`SafetyZoneViolation` if it escapes its zone root (symlinks
included). That check is a real runtime guard, not a label.

How to use — the full wrapper inventory, by zone:

- **user_cache** (``~/.cache/hypergumbo/<fingerprint>/...``):
  :func:`cache_write`, :func:`cache_write_bytes`, :func:`cache_save_npy`,
  :func:`cache_mkdir`; and the zone-enforced :func:`cache_write_zip`
  (soft-delete archives), :func:`cache_unlink`, :func:`cache_rmtree`.
- **user_out** (a user-supplied ``--out`` path): :func:`user_out_write`,
  :func:`user_out_open_json_dump` (the ``with open(...) as f:
  json.dump(...)`` pattern), :func:`user_out_open_json_dump_gzip`,
  :func:`user_out_mkdir`.
- **tmp_artifact** (ephemeral ``/tmp/`` — sketch comparisons, grammar
  build dirs): :func:`tmp_artifact_write`, :func:`tmp_artifact_mkdir`,
  and the zone-enforced :func:`tmp_artifact_rmtree`.
- **install_artifact** (downloaded binaries, archive contents):
  :func:`install_artifact_write_bytes`, :func:`install_artifact_copy`,
  :func:`install_artifact_mkdir`, :func:`install_artifact_chmod`, and the
  zone-enforced :func:`install_artifact_unlink`.

- **repo_inspection** — not writes at all. This family wraps SUBPROCESS
  launches against the analysed repository so the taint pass can bin them
  the same way, narrowing as it goes: :func:`repo_inspect_git` (git plumbing
  for cache-key fingerprinting and repo identity), :func:`repo_inspect_scan`
  (a read-only content scanner — today the ``gitleaks`` secret scan), and
  :func:`repo_inspect_probe`, the narrowest member, which reads nothing at
  all — not the repository, not the environment — and only asks an external
  tool whether it is installed and at what version.

What this pattern does NOT do:
- It does NOT prevent misuse for the NON-destructive wrappers — a
  developer who calls ``tmp_artifact_write`` on a cache path still gets
  the wrong zone label, silently. For those the pattern is a
  declarative-honesty discipline; the linter or audit catches drift.
  The destructive wrappers listed above are the exception: they enforce
  containment at runtime, because mislabelling a delete is not
  recoverable the way mislabelling a write is.
- It does NOT extend hypergumbo-the-tool's analyzer with new
  capabilities. The analyzer still works on call-graph BFS by callee
  name. The wrappers just give the BFS distinct callees to bin into
  zones.
- It is NOT shipped to PyPI consumers. This module lives in
  hypergumbo's source tree so its OWN code can be zone-tagged; the
  pattern is documented in ``docs/SECURITY.md`` as the recommended
  technique for any hypergumbo user wanting path-bounded claims in
  their own code (in any language). Each project authors its own
  wrappers in its own language.
"""
from __future__ import annotations

import json
import shutil
# The repo_inspection zone below is built on this; see its section header.
import subprocess  # nosec B404
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # pragma: no cover - typing-only import
    import numpy as np


class SafetyZoneViolation(RuntimeError):
    """A zone wrapper was handed a path outside the zone it declares.

    Raised rather than silently no-oping: a caller asking to delete
    ``$HOME`` through the cache wrapper has a bug, and swallowing it would
    hide the bug while still failing to do what the caller asked.
    """


def _cache_zone_root() -> Path:
    """Root of the ``user_cache`` zone: ``$XDG_CACHE_HOME/hypergumbo``.

    Resolved at call time, not import time, so a test (or a user) changing
    ``XDG_CACHE_HOME`` moves the zone with it. Duplicating
    ``sketch_embeddings._get_xdg_cache_base`` is deliberate and narrow:
    importing it here would make this module depend on the embeddings stack
    (and on numpy being installed) purely to learn one environment variable.
    """
    import os

    xdg = os.environ.get("XDG_CACHE_HOME")
    base = Path(xdg) if xdg else Path.home() / ".cache"
    return base / "hypergumbo"


def _tmp_zone_root() -> Path:
    """Root of the ``tmp_artifact`` zone: the system temp directory."""
    import tempfile

    return Path(tempfile.gettempdir())


def _install_zone_root() -> Path:
    """Root of the ``install_artifact`` zone: ``~/.local/bin``.

    Kept as a literal rather than imported from :mod:`gitleaks` (which owns
    ``GITLEAKS_INSTALL_DIR``) so this module stays import-light and so the
    zone boundary is stated where it is enforced. ``test_safety_zones``
    pins the two against each other, so they cannot drift apart silently.
    """
    return Path.home() / ".local" / "bin"


def _require_within_zone(path: Path, root: Path, zone: str) -> None:
    """Refuse ``path`` unless it is inside ``root``.

    THE DEFECT THIS EXISTS TO CLOSE, stated plainly because a guard whose
    reason is forgotten gets removed: ``cmd_cache_clear`` built its target as
    ``cache_dir / repo`` and deleted it. ``pathlib`` discards the left
    operand when the right is ABSOLUTE, so ``--repo /home/you/thesis``
    resolved to ``/home/you/thesis`` and was recursively deleted, reported as
    a routine cache eviction. A relative ``../..`` escapes the same way. The
    call site checked ``is_dir()`` — existence, not containment.

    Both paths are ``resolve()``d first, so a symlink pointing out of the
    zone is refused too; checking the unresolved path would let
    ``<cache>/evil -> /`` through.

    ``root`` itself is permitted (clearing the whole cache is legitimate);
    anything above or beside it is not.
    """
    try:
        resolved = path.resolve()
        resolved_root = root.resolve()
    except OSError as exc:  # pragma: no cover - unreadable path component
        raise SafetyZoneViolation(
            f"cannot resolve {path} to check the {zone} zone: {exc}",
        ) from exc
    if resolved != resolved_root and not resolved.is_relative_to(resolved_root):
        raise SafetyZoneViolation(
            f"refusing to operate on {resolved}: outside the {zone!r} safety "
            f"zone rooted at {resolved_root}",
        )


def _safety_zone_barrier() -> None:
    """Structural-taint-pass barrier marker. Invoked by every wrapper below.

    The wrapper functions in this module each call this marker exactly
    once. Declaring ``_safety_zone_barrier`` as a sanitizer for every
    entry-point taint label in
    ``docs/hypergumbo-self-catalog/zone_barrier_sanitizers.yaml`` makes
    each wrapper itself behave as a sanitizer node in the structural
    BFS: the BFS records the wrapper as a zone sink (correct) but does
    NOT propagate forward into the wrapper's internal
    ``open`` / ``json.dump`` / ``Path.write_text`` calls — which would
    otherwise spuriously fire a ``host_fs`` finding for every
    user_out / user_cache / tmp_artifact write.

    Body is a no-op at runtime. The presence of the call is what makes
    each wrapper a sanitizer node.
    """


def cache_write(path: Path, content: str) -> None:
    """Write content to a hypergumbo cache path under ``~/.cache/hypergumbo/``.

    SAFETY ZONE: ``user_cache``. Only invoke for paths under the
    hypergumbo cache directory; never for arbitrary user paths.
    """
    _safety_zone_barrier()
    path.write_text(content)


def cache_write_bytes(path: Path, data: bytes) -> None:
    """Binary version of :func:`cache_write` for non-text cache entries.

    SAFETY ZONE: ``user_cache``.
    """
    _safety_zone_barrier()
    path.write_bytes(data)


def cache_rmtree(path: Path, zone_root: Path | None = None) -> None:
    """Recursively delete a cache directory under ``~/.cache/hypergumbo/``.

    SAFETY ZONE: ``user_cache``. Used by ``cmd_cache_clear`` to evict
    stale cache entries. Wrapping ``shutil.rmtree`` here gives the
    structural-taint pass a distinct callee — verify-claims previously
    flagged generic ``.rmtree`` reachability from runtime CLI as a
    documented overapproximation because the bare callee match couldn't
    tell cache-clear writes apart from arbitrary fs deletes.

    ENFORCED, not merely declared: a path outside the cache root raises
    :class:`SafetyZoneViolation`. See :func:`_require_within_zone` for the
    verified escape this closes.

    ``zone_root`` lets a caller that already resolved the cache base pass it
    in, so the guard is checked against the SAME root the caller is
    operating in rather than a second, independently-derived one. Omitting
    it falls back to the XDG-derived default — a caller that forgets is
    still guarded, just against the default zone.
    """
    _safety_zone_barrier()
    _require_within_zone(path, zone_root or _cache_zone_root(), "user_cache")
    shutil.rmtree(path)


def cache_write_zip(
    path: Path,
    root: Path,
    members: "list[Path]",
    zone_root: Path | None = None,
) -> None:
    """Write a deflate zip of *members* (arcnames relative to *root*) into the cache.

    SAFETY ZONE: ``user_cache``. Soft-deleted cache entries are archived rather
    than destroyed, and building that archive is a cache WRITE like any other —
    it belongs behind a wrapper for the same reason ``cache_save_npy`` does.
    Constructing a ``ZipFile`` inside runtime-path code would be an unwrapped
    fs-write primitive: the zone-barrier sanitizer would not apply, the write
    would land as an unsanitized ``host_fs`` flow, and the discipline gate
    (``test_runtime_fs_write_wrapper_discipline``) fires on it — correctly, and
    by NAME, since a syntactic check cannot tell a path-backed ZipFile from an
    in-memory one.

    ENFORCED, not merely declared: a destination outside the cache root raises
    :class:`SafetyZoneViolation`.

    The archive is assembled in memory and written in one call, so peak
    additional memory is the COMPRESSED size — measured at 6% of the source on
    a real 210.9 MB cache entry — rather than the size of the inputs, which are
    streamed through the compressor.
    """
    import io
    import zipfile

    _safety_zone_barrier()
    _require_within_zone(path, zone_root or _cache_zone_root(), "user_cache")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
        for member in members:
            zf.write(member, member.relative_to(root))
    path.write_bytes(buf.getvalue())


def cache_unlink(path: Path, zone_root: Path | None = None) -> None:
    """Delete a single file under ``~/.cache/hypergumbo/``.

    SAFETY ZONE: ``user_cache``. The soft-delete folders hold one zip per
    evicted entry, so bounding them means unlinking FILES rather than trees —
    ``cache_rmtree``'s shape does not fit, and a bare ``Path.unlink`` would be
    an unwrapped filesystem delete reachable from the runtime CLI, which is
    exactly the population the ``runtime-cli-no-host-fs`` claim drove to zero.

    ENFORCED, not merely declared: a path outside the cache root raises
    :class:`SafetyZoneViolation`, via the same guard ``cache_rmtree`` uses.
    """
    _safety_zone_barrier()
    _require_within_zone(path, zone_root or _cache_zone_root(), "user_cache")
    path.unlink()


def cache_rename(src: Path, dst: Path, zone_root: Path | None = None) -> None:
    """Rename a path within ``~/.cache/hypergumbo/``.

    SAFETY ZONE: ``user_cache``. Used by ``_archive_entry`` to publish a
    ``.partial`` archive under its final name and to move an evicted entry to
    the ``.evicting-`` scratch name — the rename-before-delete ordering that
    is the whole crash-safety argument for soft eviction.

    WHY THIS WRAPPER HAD TO EXIST. It did not, and that was the defect. The
    module shipped ``cache_write`` / ``cache_write_bytes`` / ``cache_rmtree``
    / ``cache_write_zip`` / ``cache_unlink`` / ``cache_mkdir`` and no rename
    of any zone, so ``_archive_entry``'s two renames were bare for want of
    anything to call. Once ``pathlib.Path``'s surface was rowed in the I/O
    catalogue, ``Path.rename`` became a catalogued ``host_fs`` sink and
    ``runtime-cli-no-host-fs`` went ``confirmed_with_caveats`` ->
    ``violated`` on precisely those two flows, reachable from ``cmd_run`` and
    ``cmd_cache_status`` via ``_maybe_evict_cache``. The writes were always
    inside the cache; what was missing was the barrier that says so. This is
    the same argument ``cache_unlink``'s docstring makes about a bare
    ``Path.unlink``, one primitive later.

    ENFORCED, not merely declared: **both** endpoints must lie within the
    cache root or :class:`SafetyZoneViolation` is raised. Checking only the
    source would leave the wrapper able to deposit cache bytes anywhere on
    the host, which is the one way a rename's guard differs in shape from
    every single-path wrapper above.

    ``zone_root`` has the same meaning as in :func:`cache_rmtree`: a caller
    that already resolved the cache base passes it in so the guard is checked
    against the SAME root it is operating in.
    """
    _safety_zone_barrier()
    root = zone_root or _cache_zone_root()
    _require_within_zone(src, root, "user_cache")
    _require_within_zone(dst, root, "user_cache")
    src.rename(dst)


def cache_save_npy(path: Path, embedding: "np.ndarray") -> None:  # pragma: no cover - exercised by test_cache_save_npy with importorskip
    """Save a numpy array to a cache path via ``np.save``.

    SAFETY ZONE: ``user_cache``. Embeddings cache is the primary
    consumer; ``np.save`` is the canonical write for ``.npy`` files.
    """
    _safety_zone_barrier()
    # Local import keeps numpy soft-required; sketch_embeddings already
    # imports numpy at call time, so this stays cost-free in practice.
    import numpy as np
    np.save(path, embedding)


def user_out_write(path: Path, content: str) -> None:
    """Write content to a user-supplied ``--out`` / ``--output`` path.

    SAFETY ZONE: ``user_out``. Used by ``cmd_slice`` / ``cmd_search`` /
    ``cmd_io_boundaries`` and similar runtime CLI subcommands that
    accept a user-specified destination. The user typed the path; they
    own it.
    """
    _safety_zone_barrier()
    path.write_text(content)


def user_out_open_json_dump(path: Path, obj: Any) -> None:
    """``with open(path, "w") as f: json.dump(obj, f, ...)`` in one call.

    SAFETY ZONE: ``user_out``. Same semantics as ``user_out_write`` but
    for the common ``open() + json.dump()`` shape used by ``cmd_compact``,
    ``cmd_run``, and similar. Always ``indent=2, sort_keys=True`` to
    keep output deterministic — required so the safety claim's
    re-verification can run on a freshly-generated artifact and produce
    byte-identical output.
    """
    _safety_zone_barrier()
    with open(path, "w") as f:
        json.dump(obj, f, indent=2, sort_keys=True)


def user_out_open_json_dump_gzip(path: Path, obj: Any) -> None:
    """``gzip.open + json.dump`` in one call. Same semantics as
    :func:`user_out_open_json_dump` but writes a gzipped JSON payload.

    SAFETY ZONE: ``user_out``. Used by ``cmd_run`` when ``--gzip`` is
    set, on both the main output and the budget-tier outputs. The
    inner JSON encoding is identical to the uncompressed path
    (``indent=2, sort_keys=True``) so ``gunzip <out>.json.gz`` produces
    byte-identical content to the uncompressed path's output.
    """
    _safety_zone_barrier()
    import gzip
    with gzip.open(path, "wt") as f:
        json.dump(obj, f, indent=2, sort_keys=True)


def user_out_mkdir(path: Path, *, parents: bool = False, exist_ok: bool = False) -> None:
    """Create the parent directory of a user-supplied ``--out`` path.

    SAFETY ZONE: ``user_out``. INV-zudak, reopened: this wrapper did not
    exist, and its absence is why the violation was unavoidable rather than
    careless. Four of the five offending ``cli.py`` callsites sit on the line
    DIRECTLY ABOVE a :func:`user_out_write` / :func:`user_out_open_json_dump`
    call — the write was already wrapped and the ``mkdir`` that creates its
    directory could not be, because ``user_out`` was the one zone with a write
    wrapper and no mkdir wrapper. The zone whose path comes from the USER is
    the one most in need of a checked call, and it was the one missing it.

    DELIBERATELY NOT ZONE-BOUNDED, unlike :func:`tmp_artifact_rmtree` and
    :func:`cache_rmtree`. There is no zone root to check against: the user
    names this path with ``--out``, and any directory they can write to is a
    legitimate destination. What the wrapper buys is not restriction but
    ATTRIBUTION — the zone-barrier sanitizer stops the taint walk here, so the
    write is recorded as a ``user_out`` crossing instead of surfacing as an
    unsanitized ``host_fs`` flow that ``runtime-cli-no-host-fs`` must report.
    Creating the directory the user asked for is correct behaviour; doing it
    through a bare ``Path.mkdir`` is what made it indistinguishable from
    arbitrary directory creation.
    """
    _safety_zone_barrier()
    path.mkdir(parents=parents, exist_ok=exist_ok)


def cache_mkdir(path: Path, *, parents: bool = False, exist_ok: bool = False) -> None:
    """Create a cache directory (typically under ``~/.cache/hypergumbo/``).

    SAFETY ZONE: ``user_cache``. INV-zudak: ``Path.mkdir`` is an
    fs-write primitive that needs to route through the wrapper just like
    ``cache_write`` / ``cache_write_bytes`` do, so the verify-claims
    pass can distinguish ``user_cache`` scaffolding from arbitrary
    ``host_fs`` directory creation.
    """
    _safety_zone_barrier()
    path.mkdir(parents=parents, exist_ok=exist_ok)


def tmp_artifact_write(path: Path, content: str) -> None:
    """Write content to an ephemeral path under ``/tmp/`` or a tempdir.

    SAFETY ZONE: ``tmp_artifact``. Used by sketch budget-comparison
    output and tree-sitter grammar build scaffolding. These writes are
    intentionally ephemeral — the OS clears them on reboot — and not
    audit-load-bearing the way cache writes are.
    """
    _safety_zone_barrier()
    path.write_text(content)


def tmp_artifact_mkdir(path: Path, *, parents: bool = False, exist_ok: bool = False) -> None:
    """Create an ephemeral build directory under ``/tmp/`` or a tempdir.

    SAFETY ZONE: ``tmp_artifact``. INV-zudak: pairs with
    :func:`tmp_artifact_write` and :func:`tmp_artifact_rmtree` so that
    every fs-write primitive used by ``build_grammars.py`` routes
    through the wrapper. Without this, ``Path.mkdir`` callsites in
    grammar-scaffolding code surface as raw ``host_fs`` flows.
    """
    _safety_zone_barrier()
    path.mkdir(parents=parents, exist_ok=exist_ok)


def tmp_artifact_rmtree(path: Path) -> None:
    """Recursively delete an ephemeral build directory under ``/tmp/``.

    SAFETY ZONE: ``tmp_artifact``. Used by ``build_grammars.py`` to
    refresh the per-grammar scaffold directory before regenerating it.
    Distinct wrapper from :func:`cache_rmtree` so verify-claims can
    distinguish cache eviction from grammar-build scaffold reset.

    ENFORCED: a path outside the system temp directory raises
    :class:`SafetyZoneViolation`.
    """
    _safety_zone_barrier()
    _require_within_zone(path, _tmp_zone_root(), "tmp_artifact")
    shutil.rmtree(path)


def install_artifact_mkdir(path: Path, *, parents: bool = False, exist_ok: bool = False) -> None:
    """Create an install-target directory (typically under ``~/.local/`` or
    an extras install dir).

    SAFETY ZONE: ``install_artifact``. INV-zudak: pairs with
    :func:`install_artifact_write_bytes` /
    :func:`install_artifact_copy` so the install zone has a complete
    sink surface for verify-claims. Used by ``install-gitleaks`` to
    scaffold ``GITLEAKS_INSTALL_DIR`` before landing the binary.
    """
    _safety_zone_barrier()
    path.mkdir(parents=parents, exist_ok=exist_ok)


def install_artifact_write_bytes(path: Path, data: bytes) -> None:
    """Write a downloaded archive's bytes to disk during install flow.

    SAFETY ZONE: ``install_artifact``. Used during ``install-gitleaks``
    to land the downloaded release archive before extraction. The path
    is typically under ``/tmp/`` or the install dir.
    """
    _safety_zone_barrier()
    path.write_bytes(data)


def install_artifact_copy(src: Path, dst: Path) -> None:
    """Copy a built/extracted binary to its installation target path.

    SAFETY ZONE: ``install_artifact``. Used by ``install-gitleaks`` to
    place the gitleaks binary under ``~/.local/bin/``. Wraps
    ``shutil.copy2`` so the install-artifact zone has a distinct sink
    callee, separate from the general filesystem zone.
    """
    _safety_zone_barrier()
    shutil.copy2(src, dst)


def install_artifact_chmod(path: Path, mode: int) -> None:
    """Set the mode bits on an installed binary (typically +x).

    SAFETY ZONE: ``install_artifact``. Called immediately after
    ``install_artifact_copy`` lands a binary, to make it executable.
    Distinct wrapper so verify-claims can distinguish hypergumbo's own
    post-install ``chmod`` from arbitrary mode-bit mutation on the
    user's filesystem.
    """
    _safety_zone_barrier()
    path.chmod(mode)


def install_artifact_unlink(path: Path) -> None:
    """Remove an installed binary (uninstall path).

    SAFETY ZONE: ``install_artifact``. Used by ``cmd_uninstall_gitleaks``
    to evict the hypergumbo-managed binary. Distinct wrapper so
    verify-claims's overapproximate ``.unlink`` matches don't conflate
    install/uninstall flows with arbitrary file removal.

    ENFORCED: a path outside ``~/.local/bin`` raises
    :class:`SafetyZoneViolation`.
    """
    _safety_zone_barrier()
    _require_within_zone(path, _install_zone_root(), "install_artifact")
    path.unlink()


# ---------------------------------------------------------------------------
# repo_inspection zone (WI-fasuv)
# ---------------------------------------------------------------------------
#
# WHY THIS ZONE EXISTS. The shipped claim ``runtime-cli-no-subprocess`` said
# "Runtime CLI subcommands do not shell out. All subprocess invocations (curl,
# git, pip, rustup, gitleaks) happen only via extras / build-time subcommands."
# That is false as written: ``strace -f -e trace=execve`` on a default
# ``sketch`` shows git x25, a real gitleaks secret scan, and a rust-analyzer
# --version probe. gitleaks is named in the claim as build-time-only and is not.
#
# THE OWNER CHOSE TO DECLARE THE CAPABILITY RATHER THAN REWORD THE CLAIM
# (2026-08-11). Rewording -- "except read-only repo inspection" -- was the
# cheap option and is the one that launders: it weakens a shipped security
# claim with nothing enforcing the carve-out's bounds, so the sentence becomes
# true by meaning less. Routing through declared wrappers keeps the claim
# strong AND true, and makes the boundary machine-checked.
#
# WHAT UNITES THESE THREE, since it is not what they read. It is what they
# CANNOT do: none writes to the host filesystem, none takes a network action on
# the user's behalf, and none executes code originating in the analysed
# repository. That last one is the load-bearing property -- see
# :func:`repo_inspect_probe`.
#
# THREE WRAPPERS, NOT ONE, despite near-identical bodies. This mirrors the
# existing cache_write / user_out_write / tmp_artifact_write split: the taint
# machinery keys on the FUNCTION, so separate names let a future claim
# prohibit one activity while permitting another. One wrapper would collapse
# "reads your git metadata" and "reads every byte of your source" into a single
# indistinguishable permission.
#
# RE-EVALUATION TRIGGER (a KEEP verdict needs one): if anything is added to
# this zone that reads repository CONTENT and acts on it -- rather than
# reporting on it -- split the zone before adding it.


def repo_inspect_git(argv: list[str], **kwargs: Any) -> "subprocess.CompletedProcess[Any]":
    """Run a local ``git`` command against the analysed repository.

    SAFETY ZONE: ``repo_inspection``. Used for cache-key fingerprinting
    (``rev-parse HEAD``, ``rev-list --max-parents=0 HEAD``) and repo identity
    (``config --get remote.origin.url``). Read-only and local: no fetch, no
    push, no network. Callers resolve the binary via ``shutil.which`` rather
    than letting the shell search ``PATH`` (Bandit S607).
    """
    _safety_zone_barrier()
    return subprocess.run(argv, **kwargs)  # noqa: S603  # nosec B603


def repo_inspect_scan(argv: list[str], **kwargs: Any) -> "subprocess.CompletedProcess[Any]":
    """Run a read-only scanner over repository content.

    SAFETY ZONE: ``repo_inspection``. Today this is the ``gitleaks`` secret
    scan, which reads source content on stdin and reports findings on stdout.
    It is the widest-reading member of the zone -- it sees every byte handed to
    it -- and the one whose disclosure matters most to a user deciding whether
    to run hypergumbo on a private repository.
    """
    _safety_zone_barrier()
    return subprocess.run(argv, **kwargs)  # noqa: S603  # nosec B603


def repo_inspect_probe(argv: list[str], **kwargs: Any) -> "subprocess.CompletedProcess[Any]":
    """Ask an external tool whether it is installed and what version it is.

    SAFETY ZONE: ``repo_inspection``. Reads nothing -- not the repository, not
    the environment. The narrowest member of the zone.

    THE DISTINCTION THIS PRESERVES IS THE MOST IMPORTANT ONE IN THE ZONE.
    rust-analyzer executes the analysed project's ``build.rs`` and proc macros
    when it INDEXES; that is the single most dangerous capability reachable
    from this tree. A default run probes ``--version`` and never asks it to
    index, so no code from the analysed repository is executed.
    ``test_repo_inspection_zone.py::TestTheProbeDoesNotIndex`` pins that, and
    if it goes red the disclosure in SECURITY.md is wrong.
    """
    _safety_zone_barrier()
    return subprocess.run(argv, **kwargs)  # noqa: S603  # nosec B603
