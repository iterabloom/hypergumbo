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
from pathlib import Path

import pytest

from hypergumbo_core.safety_zones import (
    _safety_zone_barrier,
    cache_rmtree,
    cache_save_npy,
    cache_write,
    cache_write_bytes,
    install_artifact_chmod,
    install_artifact_copy,
    install_artifact_unlink,
    install_artifact_write_bytes,
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


def test_cache_rmtree(tmp_path: Path) -> None:
    """``cache_rmtree`` recursively deletes a directory tree."""
    d = tmp_path / "cache_dir"
    d.mkdir()
    (d / "a.txt").write_text("a")
    (d / "sub").mkdir()
    (d / "sub" / "b.txt").write_text("b")
    cache_rmtree(d)
    assert not d.exists()


def test_tmp_artifact_rmtree(tmp_path: Path) -> None:
    """``tmp_artifact_rmtree`` recursively deletes a tmp-scaffold directory."""
    d = tmp_path / "scaffold"
    d.mkdir()
    (d / "setup.py").write_text("...")
    tmp_artifact_rmtree(d)
    assert not d.exists()


def test_install_artifact_chmod(tmp_path: Path) -> None:
    """``install_artifact_chmod`` sets the mode bits on an installed file."""
    import stat
    p = tmp_path / "binary"
    p.write_bytes(b"binary")
    # Start with a non-executable mode then chmod to executable
    p.chmod(0o644)
    install_artifact_chmod(p, p.stat().st_mode | stat.S_IXUSR)
    assert p.stat().st_mode & stat.S_IXUSR


def test_install_artifact_unlink(tmp_path: Path) -> None:
    """``install_artifact_unlink`` removes a single installed file."""
    p = tmp_path / "binary"
    p.write_bytes(b"binary")
    assert p.exists()
    install_artifact_unlink(p)
    assert not p.exists()
