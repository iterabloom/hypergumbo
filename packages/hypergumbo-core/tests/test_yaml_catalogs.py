# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for the canonical YAML catalog registry."""

from __future__ import annotations

from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

from hypergumbo_core.yaml_catalogs import (
    YAML_CATALOGS,
    CatalogSpec,
    enumerate_catalogs,
    validate_registry,
)


def test_registry_has_no_duplicate_directories():
    dirs = [spec.directory for spec in YAML_CATALOGS]
    assert len(dirs) == len(set(dirs)), f"duplicate directories: {dirs}"


def test_catalog_spec_is_frozen():
    spec = YAML_CATALOGS[0]
    with pytest.raises(FrozenInstanceError):
        spec.directory = "x"  # type: ignore[misc]


def test_registry_matches_filesystem_clean_tree():
    """The real package tree should have zero drift findings."""
    assert validate_registry() == []


def test_enumerate_catalogs_returns_one_entry_per_spec():
    rows = enumerate_catalogs()
    assert len(rows) == len(YAML_CATALOGS)


def test_enumerate_catalogs_counts_real_files():
    """Every registered catalog should have at least one YAML file shipped."""
    for spec, count in enumerate_catalogs():
        assert count > 0, (
            f"catalog {spec.directory!r} is registered but ships no YAML files"
        )


def _make_fake_tree(root: Path, dirs_with_yaml: dict[str, int]) -> None:
    for name, count in dirs_with_yaml.items():
        d = root / name
        d.mkdir(parents=True, exist_ok=True)
        for i in range(count):
            (d / f"{name}_{i}.yaml").write_text("id: x\n")


def test_enumerate_catalogs_with_missing_directory(tmp_path: Path):
    """A registered directory that does not exist is reported with count=0."""
    # tmp_path is empty — none of the registered catalogs exist
    rows = enumerate_catalogs(pkg_root=tmp_path)
    assert all(count == 0 for _, count in rows)


def test_validate_registry_flags_missing_registered_directory(tmp_path: Path):
    findings = validate_registry(pkg_root=tmp_path)
    # Every registered catalog is missing from tmp_path
    assert len(findings) == len(YAML_CATALOGS)
    for spec in YAML_CATALOGS:
        assert any(spec.directory in msg for msg in findings)


def test_validate_registry_flags_unregistered_yaml_directory(tmp_path: Path):
    # Create every registered directory (so those don't flag), plus one extra
    registered_dirs = {s.directory: 1 for s in YAML_CATALOGS}
    registered_dirs["mystery_catalog"] = 3  # not in YAML_CATALOGS
    _make_fake_tree(tmp_path, registered_dirs)

    findings = validate_registry(pkg_root=tmp_path)
    assert len(findings) == 1
    assert "mystery_catalog" in findings[0]
    assert "3 YAML file" in findings[0]


def test_validate_registry_ignores_yamlless_directories(tmp_path: Path):
    # Create every registered directory + one empty (no YAML) extra
    registered_dirs = {s.directory: 1 for s in YAML_CATALOGS}
    _make_fake_tree(tmp_path, registered_dirs)
    (tmp_path / "empty_dir").mkdir()

    findings = validate_registry(pkg_root=tmp_path)
    assert findings == []


def test_validate_registry_ignores_top_level_files(tmp_path: Path):
    """A loose .yaml file at package root is not a catalog."""
    registered_dirs = {s.directory: 1 for s in YAML_CATALOGS}
    _make_fake_tree(tmp_path, registered_dirs)
    (tmp_path / "loose.yaml").write_text("x: 1\n")

    findings = validate_registry(pkg_root=tmp_path)
    assert findings == []


def test_catalog_spec_fields_populated():
    """Every spec carries the four required descriptive fields."""
    for spec in YAML_CATALOGS:
        assert spec.directory and isinstance(spec.directory, str)
        assert spec.purpose and isinstance(spec.purpose, str)
        assert spec.loader and isinstance(spec.loader, str)
        # adr is Optional but in practice every current catalog has one
        assert spec.adr is None or spec.adr.startswith("ADR-")


def test_catalog_spec_constructor_accepts_none_adr():
    """The adr field is Optional — verify None is accepted."""
    spec = CatalogSpec(
        directory="x", purpose="y", loader="z", adr=None,
        user_channel=None, no_channel_reason="internal",
    )
    assert spec.adr is None


def test_catalog_spec_cannot_be_built_without_answering_extensibility():
    """ADR-0047 r7's forcing function, asserted rather than assumed.

    ``adr`` is Optional and defaulted-by-convention; the channel fields are
    deliberately not. If someone later gives them a default, a new catalogue
    family silently inherits "no channel, no reason" and the ruling stops
    binding — so the absence of a default is the thing under test.
    """
    with pytest.raises(TypeError):
        CatalogSpec(directory="x", purpose="y", loader="z", adr=None)


def test_each_catalog_loader_module_is_importable():
    """Every registered loader must name an importable module.

    The CatalogSpec.loader field is a contract for documentation and tooling
    (it tells operators where to look when a catalog directory changes shape).
    A typo or a stale loader name silently degrades that contract — the
    pre-fix registry shipped function_summaries with loader='hypergumbo_core.cli'
    even though cli.py doesn't load function_summaries; the real loader is
    hypergumbo_core.function_summaries.load_function_summaries. This test
    enforces that whatever module name a spec carries, it at least exists.
    """
    import importlib

    for spec in YAML_CATALOGS:
        try:
            importlib.import_module(spec.loader)
        except ImportError as e:  # pragma: no cover - failure surfaces in assert
            raise AssertionError(
                f"CatalogSpec({spec.directory!r}).loader = {spec.loader!r} "
                f"does not import: {e}"
            ) from e


# ---------------------------------------------------------------------------
# ADR-0047 ruling 7: the registry answers extensibility
# ---------------------------------------------------------------------------


def _spec(**kw):
    """CatalogSpec with the required fields defaulted, for negative tests."""
    base = {
        "directory": "x", "purpose": "y", "loader": "hypergumbo_core.cli",
        "adr": None, "user_channel": None,
        "no_channel_reason": "internal, for testing",
    }
    base.update(kw)
    return CatalogSpec(**base)


def test_every_family_answers_the_extensibility_question():
    """ADR-0047 r7: a family cannot land without answering it.

    The fields are REQUIRED — no default — so a new ``CatalogSpec`` cannot be
    constructed without someone deciding whether users may extend the family.
    That is the forcing function; ``validate_registry`` then checks the answer
    is coherent. This test pins the property the required-ness buys.
    """
    for spec in YAML_CATALOGS:
        answered = spec.user_channel is not None or bool(spec.no_channel_reason)
        assert answered, (
            f"{spec.directory} answers neither: it declares no user channel "
            "and gives no reason for not having one"
        )


def test_ruling_10_table_is_the_shipped_registry():
    """The ten families carry exactly the ADR-0047 ruling-10 verdicts.

    Pinned as a table rather than a count so a future edit that flips one
    family's channel has to change this test and say so, instead of drifting.
    """
    verdicts = {
        s.directory: (s.user_channel, s.channel_scope, s.channel_gated)
        for s in YAML_CATALOGS
    }
    assert verdicts == {
        "frameworks": ("frameworks.d", None, None),
        # ADR-0047's own community overlays. NO channel of its own: a user's
        # rows for this content belong in the io_primitives channel, where
        # they displace a community row on a qualified-name collision. A
        # second home for one kind of edit is the drift this registry exists
        # to prevent.
        "io_primitives_overlays": (None, None, None),
        "dataflow_patterns": (
            "dataflow_patterns.d", "library_patterns", None,
        ),
        "io_primitives": ("io_primitives.d", None, None),
        "cfg_nodes": (None, None, None),
        "taint_sources": ("taint_sources.d", None, None),
        "taint_sanitizers": ("taint_sanitizers.d", None, None),
        "function_summaries": (
            "function_summaries.d", None, "CAVEAT_USER_SUPPLIED_SANITIZER",
        ),
        "url_folding": (None, None, None),
        # WI-lalot. Ruling 10's test is "does the family describe the USER'S
        # world or the LANGUAGE'S". These rows describe LIBRARIES and their
        # signatures, and an in-house factory returning an in-house type is
        # exactly the row a user has and the shipped catalogue cannot — so it
        # gets a channel, ungated and unscoped.
        "library_signatures": ("library_signatures.d", None, None),
    }


def test_channel_directory_is_bound_to_the_family_directory():
    """One fact, one home: the channel name is derived, never independent."""
    for spec in YAML_CATALOGS:
        if spec.user_channel is not None:
            assert spec.user_channel == f"{spec.directory}.d"


def test_validate_registry_refuses_a_channel_that_contradicts_itself(
    tmp_path: Path,
):
    """A family declaring both a channel and a reason for having none."""
    findings = validate_registry(
        tmp_path,
        catalogs=(_spec(
            user_channel="x.d", no_channel_reason="also internal",
        ),),
    )
    assert any("both a user channel and a no-channel reason" in f
               for f in findings), findings


def test_validate_registry_refuses_an_unanswered_family(tmp_path: Path):
    findings = validate_registry(
        tmp_path,
        catalogs=(_spec(user_channel=None, no_channel_reason=""),),
    )
    assert any("declares no user channel and gives no reason" in f
               for f in findings), findings


def test_validate_registry_refuses_a_misnamed_channel(tmp_path: Path):
    findings = validate_registry(
        tmp_path,
        catalogs=(_spec(
            directory="x", user_channel="somewhere_else.d",
            no_channel_reason=None,
        ),),
    )
    assert any("must be 'x.d'" in f for f in findings), findings


def test_validate_registry_refuses_a_scope_without_a_channel(tmp_path: Path):
    """A section-scoped channel that has no channel to scope."""
    findings = validate_registry(
        tmp_path,
        catalogs=(_spec(
            user_channel=None, no_channel_reason="internal",
            channel_scope="library_patterns",
        ),),
    )
    assert any("channel_scope" in f and "no user channel" in f
               for f in findings), findings


def test_validate_registry_refuses_a_gate_without_a_channel(tmp_path: Path):
    findings = validate_registry(
        tmp_path,
        catalogs=(_spec(
            user_channel=None, no_channel_reason="internal",
            channel_gated="CAVEAT_USER_SUPPLIED_SANITIZER",
        ),),
    )
    assert any("channel_gated" in f and "no user channel" in f
               for f in findings), findings


def test_live_registry_has_no_channel_findings():
    """The shipped registry passes the gate it just gained."""
    assert validate_registry() == []
