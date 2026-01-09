"""Tests for Pack deprecation (ADR-0003 item 5).

Packs are being deprecated in favor of:
- --frameworks flag for framework specification
- Linker activation conditions for framework-specific behavior

This module tests that:
1. Pack usage emits deprecation warnings
2. CLI catalog shows deprecation notice for packs
3. generate_plan no longer adds packs (uses linkers instead)
"""
import warnings

import pytest

from hypergumbo.catalog import Pack, get_default_catalog
from hypergumbo.plan import PackConfig, generate_plan
from hypergumbo.profile import RepoProfile


class TestPackDeprecationWarnings:
    """Tests for Pack deprecation warnings."""

    def test_pack_creation_emits_deprecation_warning(self) -> None:
        """Creating a Pack should emit a deprecation warning."""
        with pytest.warns(DeprecationWarning, match="Packs are deprecated"):
            Pack(
                id="test-pack",
                description="Test pack",
                passes=["python-ast-v1"],
            )

    def test_pack_config_creation_emits_deprecation_warning(self) -> None:
        """Creating a PackConfig should emit a deprecation warning."""
        with pytest.warns(DeprecationWarning, match="PackConfig is deprecated"):
            PackConfig(id="test-pack", enabled=True)

    def test_default_catalog_packs_emit_warning(self) -> None:
        """Accessing default catalog packs should emit deprecation warning."""
        with pytest.warns(DeprecationWarning, match="Packs are deprecated"):
            catalog = get_default_catalog()
            # Accessing packs triggers warning
            _ = catalog.packs


class TestGeneratePlanNoPacks:
    """Tests that generate_plan no longer adds packs."""

    def test_generate_plan_does_not_add_packs(self) -> None:
        """generate_plan should NOT add packs even for framework projects."""
        # FastAPI project - previously would add python-fastapi pack
        profile = RepoProfile(languages=["python"], frameworks=["fastapi"])

        # Suppress warnings for this test since we're testing behavior not warnings
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", DeprecationWarning)
            catalog = get_default_catalog()
            plan = generate_plan(profile, catalog)

        # Packs should be empty - frameworks handled by linkers now
        assert plan.packs == []

    def test_generate_plan_for_electron_no_packs(self) -> None:
        """Electron project should not get electron-app pack."""
        profile = RepoProfile(languages=["javascript"], frameworks=["electron"])

        with warnings.catch_warnings():
            warnings.simplefilter("ignore", DeprecationWarning)
            catalog = get_default_catalog()
            plan = generate_plan(profile, catalog)

        assert plan.packs == []


class TestPackBackwardCompatibility:
    """Tests that existing packs still work for backward compatibility."""

    def test_pack_to_dict_still_works(self) -> None:
        """Pack serialization should still work despite deprecation."""
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", DeprecationWarning)
            pack = Pack(
                id="test-pack",
                description="Test",
                passes=["python-ast-v1"],
            )
            d = pack.to_dict()

        assert d["id"] == "test-pack"
        assert d["passes"] == ["python-ast-v1"]

    def test_pack_config_to_dict_still_works(self) -> None:
        """PackConfig serialization should still work despite deprecation."""
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", DeprecationWarning)
            pc = PackConfig(id="test-pack", enabled=True)
            d = pc.to_dict()

        assert d["id"] == "test-pack"
        assert d["enabled"] is True

    def test_catalog_packs_list_still_accessible(self) -> None:
        """Catalog.packs should still be accessible for backward compatibility."""
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", DeprecationWarning)
            catalog = get_default_catalog()
            packs = catalog.packs

        # Packs should still be defined (for backward compatibility)
        assert isinstance(packs, list)
