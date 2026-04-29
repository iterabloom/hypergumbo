# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for the cross-linker tree-sitter parse cache (WI-nuran)."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from hypergumbo_core.linkers import _text_filters
from hypergumbo_core.linkers._text_filters import (
    mask_doc_regions,
    read_masked_source,
    reset_active_parse_cache,
    set_active_parse_cache,
)
from hypergumbo_core.linkers.registry import LinkerContext, _run_linker_with_cache


@pytest.fixture(autouse=True)
def _clear_parser_cache():
    _text_filters._get_parser.cache_clear()
    yield
    _text_filters._get_parser.cache_clear()


def test_linker_context_has_parsed_trees_field(tmp_path: Path) -> None:
    ctx = LinkerContext(repo_root=tmp_path)
    assert isinstance(ctx.parsed_trees, dict)
    assert ctx.parsed_trees == {}


def test_set_active_parse_cache_round_trip() -> None:
    cache: dict = {}
    token = set_active_parse_cache(cache)
    try:
        assert _text_filters._active_parse_cache.get() is cache
    finally:
        reset_active_parse_cache(token)
    assert _text_filters._active_parse_cache.get() is None


def test_masker_populates_cache_on_miss(tmp_path: Path) -> None:
    cache: dict = {}
    token = set_active_parse_cache(cache)
    try:
        out = mask_doc_regions(
            '"""producer.send(\'x\')"""\nx = 1\n',
            "python",
            cache_key=("/abs/x.py", "python"),
        )
        assert "producer.send" not in out
        assert ("/abs/x.py", "python") in cache
    finally:
        reset_active_parse_cache(token)


def test_masker_reuses_cached_tree() -> None:
    """If a tree is pre-populated in the cache, the parser is not invoked."""
    src = '"""producer.send(\'x\')"""\nx = 1\n'
    # First call: get a real tree to seed the cache.
    cache: dict = {}
    token = set_active_parse_cache(cache)
    try:
        mask_doc_regions(src, "python", cache_key=("/abs/x.py", "python"))
        seeded_tree = cache[("/abs/x.py", "python")]
    finally:
        reset_active_parse_cache(token)

    # Now mock the parser so any new parse would fail.
    sentinel_parser = type(
        "FakeParser",
        (),
        {"parse": staticmethod(lambda *_a, **_k: (_ for _ in ()).throw(RuntimeError("no fresh parse")))},
    )()
    token = set_active_parse_cache({("/abs/x.py", "python"): seeded_tree})
    try:
        with patch.object(_text_filters, "_get_parser", return_value=sentinel_parser):
            out = mask_doc_regions(
                src, "python", cache_key=("/abs/x.py", "python")
            )
        assert "producer.send" not in out
    finally:
        reset_active_parse_cache(token)


def test_read_masked_source_uses_cache_via_contextvar(tmp_path: Path) -> None:
    f = tmp_path / "x.py"
    f.write_text('"""producer.send(\'x\')"""\nx = 1\n')

    cache: dict = {}
    token = set_active_parse_cache(cache)
    try:
        out1 = read_masked_source(f)
        assert "producer.send" not in out1
        # Cache populated.
        assert (str(f), "python") in cache
        # Second call from a different "linker": cache hit.
        # We monkeypatch parser.parse to raise, proving no fresh parse occurs.
        bad_parser = type(
            "P",
            (),
            {"parse": staticmethod(lambda *_a, **_k: (_ for _ in ()).throw(RuntimeError("must not parse")))},
        )()
        with patch.object(_text_filters, "_get_parser", return_value=bad_parser):
            out2 = read_masked_source(f)
        assert out1 == out2
    finally:
        reset_active_parse_cache(token)


def test_read_masked_source_works_without_active_cache(tmp_path: Path) -> None:
    """Backward-compat: when no cache is bound, masker still parses fresh."""
    f = tmp_path / "x.py"
    f.write_text('"""producer.send(\'x\')"""\nx = 1\n')
    out = read_masked_source(f)
    assert "producer.send" not in out


def test_run_linker_with_cache_binds_and_releases(tmp_path: Path) -> None:
    ctx = LinkerContext(repo_root=tmp_path)
    captured: dict = {}

    def fake_linker(c: LinkerContext):
        captured["seen"] = _text_filters._active_parse_cache.get()
        from hypergumbo_core.linkers.registry import LinkerResult

        return LinkerResult()

    _run_linker_with_cache(fake_linker, ctx)
    assert captured["seen"] is ctx.parsed_trees
    # And after the call, the contextvar is cleared.
    assert _text_filters._active_parse_cache.get() is None


def test_run_linker_with_cache_resets_on_exception(tmp_path: Path) -> None:
    ctx = LinkerContext(repo_root=tmp_path)

    def raising_linker(c: LinkerContext):
        raise RuntimeError("boom")

    with pytest.raises(RuntimeError, match="boom"):
        _run_linker_with_cache(raising_linker, ctx)
    # Contextvar is reset even on exception.
    assert _text_filters._active_parse_cache.get() is None


def test_run_all_linkers_propagates_parsed_trees(tmp_path: Path) -> None:
    """End-to-end: cache populated by one linker is visible to the next."""
    from hypergumbo_core.linkers.registry import (
        LinkerActivation,
        LinkerResult,
        run_all_linkers,
    )

    f = tmp_path / "x.py"
    f.write_text('"""producer.send(\'x\')"""\nx = 1\n')

    seen_caches: list = []

    def linker_a(c: LinkerContext) -> LinkerResult:
        # Trigger a parse via the masker.
        read_masked_source(f)
        seen_caches.append(dict(c.parsed_trees))
        return LinkerResult()

    def linker_b(c: LinkerContext) -> LinkerResult:
        seen_caches.append(dict(c.parsed_trees))
        return LinkerResult()

    from hypergumbo_core.linkers.registry import register_linker

    # Register fresh linkers with unique names so they don't collide with
    # the real registry entries from other tests.
    register_linker("__test_cache_a", priority=900, description="test")(linker_a)
    register_linker("__test_cache_b", priority=901, description="test")(linker_b)

    ctx = LinkerContext(repo_root=tmp_path)
    try:
        run_all_linkers(ctx)
    finally:
        # Clean up registry to avoid affecting other tests.
        from hypergumbo_core.linkers.registry import _LINKER_REGISTRY

        _LINKER_REGISTRY.pop("__test_cache_a", None)
        _LINKER_REGISTRY.pop("__test_cache_b", None)

    # linker_a populated the cache; linker_b sees the populated cache.
    assert any(seen_caches), "no caches captured"
    assert (str(f), "python") in ctx.parsed_trees
