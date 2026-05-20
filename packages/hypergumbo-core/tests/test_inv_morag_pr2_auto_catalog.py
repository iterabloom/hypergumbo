# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for INV-morag PR 2: auto-catalog + pass-ID rename.

PR 2 builds on PR 1's additive provenance fields (``pass_version`` +
``reproducibility_context``) and lands the breaking-change rename:

- Decorators ``register_analyzer`` / ``register_linker`` gain metadata fields:
  ``description``, ``pass_label``, ``backend``, ``languages``, ``availability``.
- Decorators auto-compute ``pass_version`` via :func:`compute_pass_version`
  at registration time.
- ``build_catalog_from_registries()`` derives a :class:`Catalog` from
  ``_ANALYZER_REGISTRY`` + ``_LINKER_REGISTRY`` (no hand-written list).
- ``make_pass_id`` returns the name unchanged — the legacy ``-v1`` /
  ``-ts-v1`` suffix is removed. Catalog IDs and runtime IDs converge on a
  single source of truth: the decorator's ``name`` argument.
- ``scripts/check-pass-id-agreement`` asserts catalog-declared IDs equal
  runtime-emitted IDs and exits non-zero on mismatch.

These tests pin the contract in one file so future drift fails loudly.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest


# ---------------------------------------------------------------------------
# Decorator metadata extension
# ---------------------------------------------------------------------------


def test_register_analyzer_accepts_extended_metadata() -> None:
    """register_analyzer stores description, pass_label, backend, languages, availability."""
    from hypergumbo_core.analyze.registry import (
        RegisteredAnalyzer,
        _ANALYZER_REGISTRY,
        register_analyzer,
    )

    @register_analyzer(
        "_test_extended",
        description="Test analyzer for metadata extension",
        pass_label="Test Analyzer",
        backend="ast",
        languages=["testlang"],
        availability="core",
    )
    def _analyze(repo_root):  # pragma: no cover - test stub
        raise NotImplementedError

    reg = _ANALYZER_REGISTRY["_test_extended"]
    try:
        assert isinstance(reg, RegisteredAnalyzer)
        assert reg.description == "Test analyzer for metadata extension"
        assert reg.pass_label == "Test Analyzer"
        assert reg.backend == "ast"
        assert reg.languages == ["testlang"]
        assert reg.availability == "core"
    finally:
        del _ANALYZER_REGISTRY["_test_extended"]


def test_register_linker_accepts_extended_metadata() -> None:
    """register_linker stores pass_label, backend, languages, availability."""
    from hypergumbo_core.linkers.registry import (
        RegisteredLinker,
        _LINKER_REGISTRY,
        register_linker,
    )

    @register_linker(
        "_test_linker_extended",
        description="Test linker",
        pass_label="Test Linker",
        backend="protocol",
        languages=["any"],
        availability="core",
    )
    def _link(ctx):  # pragma: no cover - test stub
        raise NotImplementedError

    reg = _LINKER_REGISTRY["_test_linker_extended"]
    try:
        assert isinstance(reg, RegisteredLinker)
        assert reg.description == "Test linker"
        assert reg.pass_label == "Test Linker"
        assert reg.backend == "protocol"
        assert reg.languages == ["any"]
        assert reg.availability == "core"
    finally:
        del _LINKER_REGISTRY["_test_linker_extended"]


def test_register_analyzer_auto_computes_pass_version() -> None:
    """At decoration time, pass_version is computed via compute_pass_version(func)."""
    from hypergumbo_core.analyze.registry import (
        _ANALYZER_REGISTRY,
        register_analyzer,
    )
    from hypergumbo_core.ir import compute_pass_version

    @register_analyzer("_test_passver")
    def _analyze(repo_root):  # pragma: no cover - test stub
        raise NotImplementedError

    reg = _ANALYZER_REGISTRY["_test_passver"]
    try:
        expected = compute_pass_version(_analyze)
        assert reg.pass_version == expected
        assert reg.pass_version.startswith("sha256:")
    finally:
        del _ANALYZER_REGISTRY["_test_passver"]


def test_register_linker_auto_computes_pass_version() -> None:
    """At decoration time, pass_version is computed via compute_pass_version(func)."""
    from hypergumbo_core.linkers.registry import (
        _LINKER_REGISTRY,
        register_linker,
    )
    from hypergumbo_core.ir import compute_pass_version

    @register_linker("_test_linker_passver")
    def _link(ctx):  # pragma: no cover - test stub
        raise NotImplementedError

    reg = _LINKER_REGISTRY["_test_linker_passver"]
    try:
        expected = compute_pass_version(_link)
        assert reg.pass_version == expected
        assert reg.pass_version.startswith("sha256:")
    finally:
        del _LINKER_REGISTRY["_test_linker_passver"]


# ---------------------------------------------------------------------------
# Pass-ID rename (drop legacy -v1 / -ts-v1 suffix)
# ---------------------------------------------------------------------------


def test_make_pass_id_returns_name_unchanged() -> None:
    """make_pass_id is now an identity function — no suffix mangling."""
    from hypergumbo_core.ir import make_pass_id

    assert make_pass_id("python") == "python"
    assert make_pass_id("javascript") == "javascript"
    assert make_pass_id("websocket-linker") == "websocket-linker"
    assert make_pass_id("containment-linker") == "containment-linker"


def test_no_catalog_pass_id_has_legacy_suffix() -> None:
    """Every pass in the registry-derived catalog has a suffix-free ID."""
    from hypergumbo_core.catalog import build_catalog_from_registries

    catalog = build_catalog_from_registries()
    offenders = [
        p.id for p in catalog.passes
        if p.id.endswith("-v1") or "-ts-v1" in p.id or "-ast-v1" in p.id
    ]
    assert offenders == [], (
        f"Pass IDs must not carry legacy -v1/-ts-v1/-ast-v1 suffix. "
        f"Offenders: {offenders}"
    )


# ---------------------------------------------------------------------------
# build_catalog_from_registries
# ---------------------------------------------------------------------------


def test_build_catalog_from_registries_includes_every_analyzer() -> None:
    """Every registered analyzer appears in the derived catalog by name."""
    from hypergumbo_core.analyze.registry import (
        _ANALYZER_REGISTRY,
        ensure_discovered,
    )
    from hypergumbo_core.catalog import build_catalog_from_registries

    ensure_discovered()
    catalog = build_catalog_from_registries()

    catalog_ids = {p.id for p in catalog.passes}
    registered_names = set(_ANALYZER_REGISTRY.keys())
    missing = registered_names - catalog_ids
    assert not missing, f"Catalog missing registered analyzers: {missing}"


def test_build_catalog_from_registries_includes_every_linker() -> None:
    """Every registered linker appears in the derived catalog by name."""
    # Trigger analyzer + linker discovery.
    from hypergumbo_core import cli
    from hypergumbo_core.analyze.registry import ensure_discovered
    from hypergumbo_core.linkers.registry import _LINKER_REGISTRY
    from hypergumbo_core.catalog import build_catalog_from_registries

    ensure_discovered()
    catalog = build_catalog_from_registries()

    catalog_ids = {p.id for p in catalog.passes}
    registered_linker_names = set(_LINKER_REGISTRY.keys())
    missing = registered_linker_names - catalog_ids
    assert not missing, f"Catalog missing registered linkers: {missing}"


def test_build_catalog_from_registries_no_strangers() -> None:
    """Catalog contains only registered analyzers + linkers — no hand-written extras."""
    from hypergumbo_core import cli
    from hypergumbo_core.analyze.registry import (
        _ANALYZER_REGISTRY,
        ensure_discovered,
    )
    from hypergumbo_core.linkers.registry import _LINKER_REGISTRY
    from hypergumbo_core.catalog import build_catalog_from_registries

    ensure_discovered()
    catalog = build_catalog_from_registries()

    catalog_ids = {p.id for p in catalog.passes}
    registered_ids = set(_ANALYZER_REGISTRY) | set(_LINKER_REGISTRY)
    strangers = catalog_ids - registered_ids
    assert not strangers, f"Catalog has unregistered passes: {strangers}"


def test_get_default_catalog_uses_registries() -> None:
    """The public get_default_catalog() is the registry-derived catalog."""
    from hypergumbo_core import cli
    from hypergumbo_core.analyze.registry import ensure_discovered
    from hypergumbo_core.catalog import (
        build_catalog_from_registries,
        get_default_catalog,
    )

    ensure_discovered()
    a = {p.id for p in get_default_catalog().passes}
    b = {p.id for p in build_catalog_from_registries().passes}
    assert a == b


# ---------------------------------------------------------------------------
# Runtime ↔ catalog pass-ID agreement
# ---------------------------------------------------------------------------


def test_catalog_ids_match_runtime_make_pass_id() -> None:
    """For every registered name, catalog ID == make_pass_id(name).

    This is the central INV-morag invariant. After PR 2, both sides agree
    because the catalog is derived from the same registries that runtime
    code uses, and make_pass_id is now the identity function.
    """
    from hypergumbo_core import cli
    from hypergumbo_core.analyze.registry import (
        _ANALYZER_REGISTRY,
        ensure_discovered,
    )
    from hypergumbo_core.linkers.registry import _LINKER_REGISTRY
    from hypergumbo_core.catalog import build_catalog_from_registries
    from hypergumbo_core.ir import make_pass_id

    ensure_discovered()
    catalog = build_catalog_from_registries()
    catalog_by_id = {p.id: p for p in catalog.passes}

    for name in _ANALYZER_REGISTRY:
        runtime_id = make_pass_id(name)
        assert runtime_id in catalog_by_id, (
            f"Analyzer {name!r} has runtime ID {runtime_id!r} "
            f"but is missing from catalog."
        )
    for name in _LINKER_REGISTRY:
        runtime_id = make_pass_id(name)
        assert runtime_id in catalog_by_id, (
            f"Linker {name!r} has runtime ID {runtime_id!r} "
            f"but is missing from catalog."
        )


# ---------------------------------------------------------------------------
# scripts/check-pass-id-agreement CI gate
# ---------------------------------------------------------------------------


REPO_ROOT = Path(__file__).resolve().parents[3]
SCRIPT_PATH = REPO_ROOT / "scripts" / "check-pass-id-agreement"


def test_pass_to_dict_includes_backend_when_set() -> None:
    """Pass.to_dict() emits 'backend' iff backend is non-empty."""
    from hypergumbo_core.catalog import Pass

    p_with = Pass(id="x", description="d", availability="core", backend="tree-sitter")
    assert p_with.to_dict()["backend"] == "tree-sitter"
    p_without = Pass(id="x", description="d", availability="core")
    assert "backend" not in p_without.to_dict()


def test_pass_to_dict_includes_pass_label_when_distinct() -> None:
    """Pass.to_dict() emits 'pass_label' iff it differs from id."""
    from hypergumbo_core.catalog import Pass

    p_distinct = Pass(id="x", description="d", availability="core", pass_label="X (display)")
    assert p_distinct.to_dict()["pass_label"] == "X (display)"
    p_equal = Pass(id="x", description="d", availability="core", pass_label="x")
    assert "pass_label" not in p_equal.to_dict()


def test_check_pass_id_agreement_script_exists() -> None:
    """Pre-commit / CI script exists at scripts/check-pass-id-agreement."""
    assert SCRIPT_PATH.exists(), f"Missing CI gate: {SCRIPT_PATH}"
    assert SCRIPT_PATH.is_file()


def test_check_pass_id_agreement_script_passes_on_clean_repo() -> None:
    """On a clean repo, the gate exits 0 (catalog and runtime agree)."""
    if not SCRIPT_PATH.exists():  # pragma: no cover - covered by existence test
        pytest.skip("script not yet implemented")
    result = subprocess.run(
        [sys.executable, str(SCRIPT_PATH)],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, (
        f"check-pass-id-agreement failed:\n"
        f"stdout:\n{result.stdout}\n"
        f"stderr:\n{result.stderr}"
    )
