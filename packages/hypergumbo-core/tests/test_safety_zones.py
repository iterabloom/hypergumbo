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
    _safety_zone_barrier,
    cache_mkdir,
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
        if name.endswith(("_rmtree", "_unlink"))
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
