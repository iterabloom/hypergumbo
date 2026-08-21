# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for the safety_zones wrapper module.

Each wrapper is a thin pass-through to the underlying stdlib write
(``Path.write_text`` / ``json.dump`` / ``np.save`` / ``shutil.copy2``),
plus a single call to ``_safety_zone_barrier`` so the structural-taint
pass treats the wrapper as a sanitizer node (per the project-local
catalog at ``docs/hypergumbo-self-catalog/zone_barrier_sanitizers.yaml``).

These tests confirm:
- Each wrapper writes the expected content to the requested path.
- ``_safety_zone_barrier`` is a no-op at runtime (returns None, no side
  effects). It exists purely for the structural pass to discover.
"""
from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest

from hypergumbo_core.safety_zones import (
    SafetyZoneViolation,
    cache_unlink,
    cache_write_zip,
    _safety_zone_barrier,
    cache_mkdir,
    cache_rename,
    cache_rmtree,
    cache_save_npy,
    cache_write,
    cache_write_bytes,
    install_artifact_chmod,
    install_artifact_copy,
    install_artifact_mkdir,
    install_artifact_unlink,
    install_artifact_write_bytes,
    tmp_artifact_mkdir,
    tmp_artifact_rmtree,
    tmp_artifact_write,
    user_out_open_json_dump,
    user_out_write,
)


def test_safety_zone_barrier_is_no_op() -> None:
    """``_safety_zone_barrier`` returns None and has no side effects."""
    assert _safety_zone_barrier() is None


def test_cache_write(tmp_path: Path) -> None:
    p = tmp_path / "cache.txt"
    cache_write(p, "hello")
    assert p.read_text() == "hello"


def test_cache_write_bytes(tmp_path: Path) -> None:
    p = tmp_path / "cache.bin"
    cache_write_bytes(p, b"\x00\x01\x02")
    assert p.read_bytes() == b"\x00\x01\x02"


def test_cache_save_npy(tmp_path: Path) -> None:
    # numpy is only installed when the `embeddings` optional dep is
    # selected; skip the test if not available rather than fail at
    # collection.
    np = pytest.importorskip("numpy")
    p = tmp_path / "embed.npy"
    arr = np.array([1.0, 2.0, 3.0], dtype=np.float32)
    cache_save_npy(p, arr)
    loaded = np.load(p)
    assert loaded.tolist() == arr.tolist()


def test_user_out_write(tmp_path: Path) -> None:
    p = tmp_path / "out.txt"
    user_out_write(p, "user data")
    assert p.read_text() == "user data"


def test_user_out_open_json_dump(tmp_path: Path) -> None:
    """``user_out_open_json_dump`` writes deterministic JSON.

    indent=2 and sort_keys=True are pinned in the wrapper so re-running
    the safety claim against a freshly-generated artifact produces
    byte-identical output.
    """
    p = tmp_path / "out.json"
    user_out_open_json_dump(p, {"b": 2, "a": 1})
    text = p.read_text()
    # Keys are sorted; pretty-printed with indent=2.
    assert text == '{\n  "a": 1,\n  "b": 2\n}'
    # Parses back to the same dict.
    assert json.loads(text) == {"a": 1, "b": 2}


def test_tmp_artifact_write(tmp_path: Path) -> None:
    p = tmp_path / "scratch.txt"
    tmp_artifact_write(p, "ephemeral")
    assert p.read_text() == "ephemeral"


def test_install_artifact_write_bytes(tmp_path: Path) -> None:
    p = tmp_path / "downloaded.bin"
    install_artifact_write_bytes(p, b"binary blob")
    assert p.read_bytes() == b"binary blob"


def test_install_artifact_copy(tmp_path: Path) -> None:
    src = tmp_path / "src-bin"
    src.write_bytes(b"executable")
    dst = tmp_path / "dst-bin"
    install_artifact_copy(src, dst)
    assert dst.read_bytes() == b"executable"


def test_cache_rmtree(tmp_path: Path, monkeypatch) -> None:
    """``cache_rmtree`` recursively deletes a directory tree INSIDE its zone.

    The zone is now established explicitly. This test previously passed a
    path with no relationship to the cache directory at all, which is what
    let the wrapper ship with a documented zone it did not enforce.
    """
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
    d = tmp_path / "hypergumbo" / "cache_dir"
    d.mkdir(parents=True)
    (d / "a.txt").write_text("a")
    (d / "sub").mkdir()
    (d / "sub" / "b.txt").write_text("b")
    cache_rmtree(d)
    assert not d.exists()


def test_cache_rmtree_refuses_a_path_outside_its_zone(
    tmp_path: Path, monkeypatch,
) -> None:
    """A wrapper whose docstring declares a SAFETY ZONE must enforce it.

    VERIFIED DEFECT, not a hypothetical: ``hypergumbo cache-clear --repo
    <absolute path>`` recursively deleted a directory outside the cache and
    reported it as a normal cache eviction, because ``cache_dir / repo``
    discards the left operand when ``repo`` is absolute and nothing
    downstream re-checked containment. A relative ``../..`` traverses out the
    same way.

    The zone is the guarantee. A wrapper that documents ``user_cache`` and
    deletes ``$HOME`` on request is a comment, not a boundary.
    """
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cachehome"))
    (tmp_path / "cachehome" / "hypergumbo").mkdir(parents=True)
    victim = tmp_path / "VICTIM"
    victim.mkdir()
    (victim / "thesis.txt").write_text("six months of work")

    with pytest.raises(SafetyZoneViolation):
        cache_rmtree(victim)
    assert (victim / "thesis.txt").read_text() == "six months of work"


def test_tmp_artifact_rmtree(tmp_path: Path) -> None:
    """``tmp_artifact_rmtree`` recursively deletes a tmp-scaffold directory."""
    d = tmp_path / "scaffold"
    d.mkdir()
    (d / "setup.py").write_text("...")
    tmp_artifact_rmtree(d)
    assert not d.exists()


def test_tmp_artifact_rmtree_refuses_a_path_outside_tmp(
    tmp_path: Path, monkeypatch,
) -> None:
    """Same guarantee for the ``tmp_artifact`` zone.

    Included because the fix must be the zone mechanism, not a patch to the
    one wrapper whose escape was demonstrated — otherwise the next wrapper
    ships the same hole.
    """
    monkeypatch.setattr(tempfile, "gettempdir", lambda: str(tmp_path / "fake_tmp"))
    (tmp_path / "fake_tmp").mkdir()
    victim = tmp_path / "NOT_TMP"
    victim.mkdir()
    (victim / "keep.txt").write_text("keep")

    with pytest.raises(SafetyZoneViolation):
        tmp_artifact_rmtree(victim)
    assert (victim / "keep.txt").read_text() == "keep"


def test_every_destructive_wrapper_enforces_a_zone() -> None:
    """PARITY: enumerate the destructive wrappers and assert each refuses an
    out-of-zone path, so a newly added one cannot ship unenforced.

    This is the check that would have caught the original defect. The zone
    discipline was documented per-wrapper in prose, and prose does not fail
    a build.
    """
    import inspect

    from hypergumbo_core import safety_zones as sz

    destructive = {
        name for name in dir(sz)
        if name.endswith(("_rmtree", "_unlink", "_rename"))
        and callable(getattr(sz, name))
    }
    assert destructive, "no destructive wrappers found — check the naming rule"
    for name in sorted(destructive):
        fn = getattr(sz, name)
        src = inspect.getsource(fn)
        assert "_require_within_zone" in src, (
            f"{name} does not enforce its declared safety zone; a wrapper "
            f"that documents a zone and does not enforce it is a comment"
        )


def test_install_artifact_chmod(tmp_path: Path) -> None:
    """``install_artifact_chmod`` sets the mode bits on an installed file."""
    import stat
    p = tmp_path / "binary"
    p.write_bytes(b"binary")
    # Start with a non-executable mode then chmod to executable
    p.chmod(0o644)
    install_artifact_chmod(p, p.stat().st_mode | stat.S_IXUSR)
    assert p.stat().st_mode & stat.S_IXUSR


def test_install_artifact_unlink(tmp_path: Path, monkeypatch) -> None:
    """``install_artifact_unlink`` removes a single installed file in-zone."""
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    bindir = tmp_path / ".local" / "bin"
    bindir.mkdir(parents=True)
    p = bindir / "binary"
    p.write_bytes(b"binary")
    assert p.exists()
    install_artifact_unlink(p)
    assert not p.exists()


def test_install_artifact_unlink_refuses_outside_its_zone(
    tmp_path: Path, monkeypatch,
) -> None:
    """Third destructive wrapper, same guarantee."""
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    (tmp_path / ".local" / "bin").mkdir(parents=True)
    victim = tmp_path / "elsewhere" / "important"
    victim.parent.mkdir()
    victim.write_text("keep me")

    with pytest.raises(SafetyZoneViolation):
        install_artifact_unlink(victim)
    assert victim.read_text() == "keep me"


def test_install_zone_root_matches_the_module_that_owns_it() -> None:
    """The zone root is duplicated on purpose (import-weight); pin the two
    together so they cannot drift into disagreeing about the boundary."""
    from hypergumbo_core.gitleaks import GITLEAKS_INSTALL_DIR
    from hypergumbo_core.safety_zones import _install_zone_root

    assert _install_zone_root() == GITLEAKS_INSTALL_DIR


def test_cache_mkdir_inv_zudak(tmp_path: Path) -> None:
    """INV-zudak: ``cache_mkdir`` routes ``Path.mkdir`` through the
    user_cache wrapper so verify-claims sees it as a user_cache sink
    rather than a raw host_fs flow.
    """
    p = tmp_path / "cache_subdir" / "nested"
    cache_mkdir(p, parents=True)
    assert p.is_dir()
    # exist_ok=True is supported (idempotent re-creation).
    cache_mkdir(p, parents=True, exist_ok=True)


def test_tmp_artifact_mkdir_inv_zudak(tmp_path: Path) -> None:
    """INV-zudak: ``tmp_artifact_mkdir`` routes scaffold creation
    (build_grammars.py) through the tmp_artifact wrapper.
    """
    p = tmp_path / "grammar_build" / "pkg"
    tmp_artifact_mkdir(p, parents=True)
    assert p.is_dir()
    tmp_artifact_mkdir(p, parents=True, exist_ok=True)
    # Without parents=, refuses to auto-create intermediates (mirrors Path.mkdir).
    import pytest
    with pytest.raises(FileNotFoundError):
        tmp_artifact_mkdir(tmp_path / "no_intermediate" / "child")


def test_install_artifact_mkdir_inv_zudak(tmp_path: Path) -> None:
    """INV-zudak: ``install_artifact_mkdir`` routes install-target
    directory creation (gitleaks GITLEAKS_INSTALL_DIR) through the
    install_artifact wrapper.
    """
    p = tmp_path / "install" / "bin"
    install_artifact_mkdir(p, parents=True, exist_ok=True)
    assert p.is_dir()


def test_cache_unlink(tmp_path: Path, monkeypatch) -> None:
    """Deletes a single FILE inside the zone.

    The soft-delete folders hold one zip per evicted entry, so bounding them
    means unlinking files rather than trees — ``cache_rmtree``'s shape does
    not fit, and a bare ``Path.unlink`` in runtime-path code would be an
    unwrapped fs delete of exactly the kind the host_fs claim drove to zero.
    """
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
    d = tmp_path / "hypergumbo" / "soft-deleted-surveys"
    d.mkdir(parents=True)
    f = d / "repo__state.zip"
    f.write_bytes(b"archive")
    cache_unlink(f)
    assert not f.exists()


def test_cache_unlink_refuses_a_path_outside_its_zone(
    tmp_path: Path, monkeypatch
) -> None:
    """The guard is enforced, not merely documented."""
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "hgcache"))
    outside = tmp_path / "precious.txt"
    outside.write_text("do not delete me")
    with pytest.raises(SafetyZoneViolation):
        cache_unlink(outside)
    assert outside.exists(), "the refusal must be a refusal, not a warning"


def test_cache_write_zip(tmp_path: Path, monkeypatch) -> None:
    """Archives members with arcnames relative to the given root.

    Reading the archive back and comparing BYTES rather than asserting the
    file exists: the whole point of soft delete is that the data survives, and
    a zip with the right name and the wrong contents would satisfy any weaker
    check while making recovery impossible.
    """
    import zipfile

    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
    root = tmp_path / "entry"
    (root / "inner").mkdir(parents=True)
    a = root / "survey.json"
    a.write_bytes(b'{"nodes": [1, 2, 3]}')
    b = root / "inner" / "sketch.md"
    b.write_text("# sketch")

    dest_dir = tmp_path / "hypergumbo" / "soft-deleted-surveys"
    dest_dir.mkdir(parents=True)
    out = dest_dir / "repo__state.zip"
    cache_write_zip(out, root, [a, b])

    with zipfile.ZipFile(out) as zf:
        assert sorted(zf.namelist()) == ["inner/sketch.md", "survey.json"]
        assert zf.read("survey.json") == b'{"nodes": [1, 2, 3]}'


def test_cache_write_zip_refuses_a_destination_outside_its_zone(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "hgcache"))
    root = tmp_path / "entry"
    root.mkdir()
    member = root / "survey.json"
    member.write_text("{}")
    outside = tmp_path / "escaped.zip"
    with pytest.raises(SafetyZoneViolation):
        cache_write_zip(outside, root, [member])
    assert not outside.exists()


def test_cache_rename(tmp_path: Path, monkeypatch) -> None:
    """Renames a path inside the zone.

    ``_archive_entry`` renames a ``.partial`` archive into place and moves an
    evicted entry to a scratch name. Both are cache-internal writes, but a
    bare ``Path.rename`` is an unwrapped fs mutation reachable from the
    runtime CLI — the same population ``cache_unlink`` exists to keep out of
    ``runtime-cli-no-host-fs``.
    """
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
    d = tmp_path / "hypergumbo" / "soft-deleted-surveys"
    d.mkdir(parents=True)
    src = d / "repo__state.zip.partial"
    src.write_bytes(b"archive")
    dst = d / "repo__state.zip"
    cache_rename(src, dst)
    assert dst.read_bytes() == b"archive"
    assert not src.exists()


def test_cache_rename_refuses_a_source_outside_its_zone(
    tmp_path: Path, monkeypatch
) -> None:
    """The guard is enforced, not merely documented."""
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "hgcache"))
    outside = tmp_path / "precious.txt"
    outside.write_text("do not move me")
    with pytest.raises(SafetyZoneViolation):
        cache_rename(outside, tmp_path / "hgcache" / "hypergumbo" / "moved.txt")
    assert outside.exists(), "the refusal must be a refusal, not a warning"


def test_cache_rename_refuses_a_destination_outside_its_zone(
    tmp_path: Path, monkeypatch
) -> None:
    """A rename has TWO endpoints; checking only the source is an escape.

    This is the one way ``cache_rename`` differs in shape from every other
    wrapper in this module: ``cache_unlink`` / ``cache_rmtree`` take a single
    path, so a single ``_require_within_zone`` call is the whole guard. A
    rename whose source is in-zone can still deposit the bytes anywhere on
    the host, so the destination needs its own check.
    """
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
    d = tmp_path / "hypergumbo"
    d.mkdir(parents=True)
    src = d / "entry.zip"
    src.write_bytes(b"archive")
    escape = tmp_path / "escaped.zip"
    with pytest.raises(SafetyZoneViolation):
        cache_rename(src, escape)
    assert not escape.exists(), "the destination guard must block the write"
    assert src.exists(), "a refused rename must leave the source intact"


def test_cache_rename_honours_an_explicit_zone_root(tmp_path: Path) -> None:
    """``zone_root`` checks against the caller's own resolved root.

    Mirrors ``cache_rmtree`` / ``cache_unlink``: a caller that already
    resolved the cache base passes it in so the guard is checked against the
    SAME root it is operating in, rather than a second XDG-derived one.
    """
    root = tmp_path / "explicit-cache"
    root.mkdir()
    src = root / "a.zip.partial"
    src.write_bytes(b"x")
    dst = root / "a.zip"
    cache_rename(src, dst, zone_root=root)
    assert dst.exists()


def test_cli_has_no_bare_rename_calls() -> None:
    """Positive control for the defect CLASS, not just the two call sites.

    ``safety_zones`` shipped wrappers for write / write_bytes / rmtree /
    write_zip / unlink / mkdir / copy / chmod and no rename wrapper at all,
    so ``_archive_entry``'s two renames were bare because there was nothing
    to call. When ``pathlib.Path``'s surface was rowed, ``rename`` became a
    catalogued ``host_fs`` sink and ``runtime-cli-no-host-fs`` flipped
    ``confirmed_with_caveats`` -> ``violated`` on exactly those two flows.

    NOT A DUPLICATE of ``test_runtime_fs_write_wrapper_discipline.py``, which
    walks the same ASTs over the same modules — do not delete this as
    redundant. That guard derives its primitive set from the live catalogue
    and then subtracts ``catalog.ambiguous_names``; ``rename`` IS catalogued
    ``fs_write`` (``pathlib.Path.rename`` and ``os.rename``) but IS ambiguous,
    so it is filtered out there. That is deliberate and documented — the
    guard calls itself "a syntactic backstop for the unambiguous majority"
    and delegates the rest to "the boundary analysis, which has receiver
    evidence". The boundary analysis is the ``self-claims-gate`` CI step,
    which is path-triggered on the claim surface and had not run for 63
    commits when this defect landed. Both layers were open at once. This
    test closes the ``rename`` name specifically, by name, unconditionally.

    Parsed as an AST rather than grepped: a regex over source also matches
    its own docstrings, and this very docstring names ``.rename`` repeatedly.
    """
    import ast

    src = Path(__file__).resolve().parents[1] / "src" / "hypergumbo_core" / "cli.py"
    tree = ast.parse(src.read_text())
    bare = [
        node.lineno
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "rename"
    ]
    assert bare == [], (
        f"bare .rename() call(s) in cli.py at line(s) {bare} — route "
        f"cache-internal renames through safety_zones.cache_rename so the "
        f"zone-barrier sanitizer applies"
    )


def test_tmp_artifact_dir_inv_lalad() -> None:
    """INV-lalad: ``tmp_artifact_dir`` routes ephemeral SCRATCH DIRECTORY
    creation through the tmp_artifact wrapper.

    INV-zudak added ``tmp_artifact_write`` / ``_mkdir`` / ``_rmtree`` so that
    "every fs-write primitive" routed through a wrapper — but it enumerated
    write_text / mkdir / unlink / chmod / copy2 / rmtree / replace, and
    ``tempfile.TemporaryDirectory`` is none of those. It is CONSTRUCTOR-shaped,
    and the taint walk could not traverse construction edges at all, so the two
    unwrapped call sites (gitleaks.py, graceful_degrade.py) never surfaced as
    raw ``host_fs`` flows and INV-zudak's drain looked complete. Closing the
    taint blind spot made them visible; this wrapper is the fix.
    """
    from hypergumbo_core.safety_zones import tmp_artifact_dir

    with tmp_artifact_dir() as tmpdir:
        created = Path(tmpdir)
        assert created.is_dir()
        # It really is a scratch dir inside the tmp_artifact zone.
        assert created.is_relative_to(Path(tempfile.gettempdir()))
        (created / "scratch.txt").write_text("x")
    # The context manager cleans up after itself, like TemporaryDirectory.
    assert not created.exists()


def test_tmp_artifact_dir_honours_prefix() -> None:
    """``graceful_degrade`` passes ``prefix=`` so the scratch dir is
    identifiable in a temp listing; the wrapper must not swallow it."""
    from hypergumbo_core.safety_zones import tmp_artifact_dir

    with tmp_artifact_dir(prefix="hg_probe_") as tmpdir:
        assert Path(tmpdir).name.startswith("hg_probe_")
