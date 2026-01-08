"""Tests for plan generation module."""
from unittest.mock import patch

from hypergumbo.plan import (
    PassConfig,
    PackConfig,
    Rule,
    CapsulePlan,
    generate_plan,
    validate_plan,
)
from hypergumbo.catalog import get_default_catalog
from hypergumbo.profile import RepoProfile


class TestPassConfig:
    """Tests for PassConfig dataclass."""

    def test_pass_config_has_required_fields(self) -> None:
        """PassConfig has id and enabled fields."""
        pc = PassConfig(id="python-ast-v1", enabled=True)
        assert pc.id == "python-ast-v1"
        assert pc.enabled is True

    def test_pass_config_with_custom_config(self) -> None:
        """PassConfig can have custom config dict."""
        pc = PassConfig(
            id="python-ast-v1",
            enabled=True,
            config={"parse_decorators": True},
        )
        assert pc.config["parse_decorators"] is True

    def test_pass_config_to_dict(self) -> None:
        """PassConfig serializes to dict."""
        pc = PassConfig(id="python-ast-v1", enabled=True)
        d = pc.to_dict()
        assert d["id"] == "python-ast-v1"
        assert d["enabled"] is True

    def test_pass_config_to_dict_with_config_and_requires(self) -> None:
        """PassConfig to_dict includes config and requires when set."""
        pc = PassConfig(
            id="python-ast-v1",
            enabled=True,
            config={"parse_decorators": True},
            requires=["hypergumbo[extra]"],
        )
        d = pc.to_dict()
        assert d["config"]["parse_decorators"] is True
        assert d["requires"] == ["hypergumbo[extra]"]


class TestPackConfig:
    """Tests for PackConfig dataclass."""

    def test_pack_config_has_required_fields(self) -> None:
        """PackConfig has id and enabled fields."""
        pc = PackConfig(id="python-fastapi", enabled=True)
        assert pc.id == "python-fastapi"
        assert pc.enabled is True

    def test_pack_config_to_dict(self) -> None:
        """PackConfig serializes to dict."""
        pc = PackConfig(id="python-fastapi", enabled=True)
        d = pc.to_dict()
        assert d["id"] == "python-fastapi"

    def test_pack_config_to_dict_with_config(self) -> None:
        """PackConfig to_dict includes config when set."""
        pc = PackConfig(
            id="python-fastapi",
            enabled=True,
            config={"route_patterns": ["@app.get"]},
        )
        d = pc.to_dict()
        assert d["config"]["route_patterns"] == ["@app.get"]


class TestRule:
    """Tests for Rule dataclass."""

    def test_rule_exclude_pattern(self) -> None:
        """Can create exclude pattern rule."""
        rule = Rule(
            rule_type="exclude_pattern",
            glob="**/*_test.py",
            reason="test files",
        )
        assert rule.rule_type == "exclude_pattern"
        assert rule.glob == "**/*_test.py"

    def test_rule_entrypoint_pattern(self) -> None:
        """Can create entrypoint pattern rule."""
        rule = Rule(
            rule_type="entrypoint_pattern",
            pattern="if __name__ == '__main__':",
            label="cli_entry",
        )
        assert rule.rule_type == "entrypoint_pattern"
        assert rule.pattern == "if __name__ == '__main__':"

    def test_rule_to_dict(self) -> None:
        """Rule serializes to dict."""
        rule = Rule(
            rule_type="exclude_pattern",
            glob="**/*_test.py",
            reason="test files",
        )
        d = rule.to_dict()
        assert d["type"] == "exclude_pattern"
        assert d["glob"] == "**/*_test.py"

    def test_rule_to_dict_entrypoint(self) -> None:
        """Rule to_dict includes pattern and label for entrypoints."""
        rule = Rule(
            rule_type="entrypoint_pattern",
            pattern="if __name__ == '__main__':",
            label="cli_entry",
        )
        d = rule.to_dict()
        assert d["type"] == "entrypoint_pattern"
        assert d["pattern"] == "if __name__ == '__main__':"
        assert d["label"] == "cli_entry"


class TestCapsulePlan:
    """Tests for CapsulePlan dataclass."""

    def test_capsule_plan_has_version(self) -> None:
        """CapsulePlan has version field."""
        plan = CapsulePlan(version="0.1.0")
        assert plan.version == "0.1.0"

    def test_capsule_plan_to_dict(self) -> None:
        """CapsulePlan serializes to dict."""
        plan = CapsulePlan(
            version="0.1.0",
            passes=[PassConfig("python-ast-v1", True)],
            packs=[],
            rules=[],
            features=[],
        )
        d = plan.to_dict()
        assert d["version"] == "0.1.0"
        assert len(d["passes"]) == 1
        assert d["passes"][0]["id"] == "python-ast-v1"

    def test_capsule_plan_empty_sections(self) -> None:
        """CapsulePlan with empty sections."""
        plan = CapsulePlan(version="0.1.0")
        d = plan.to_dict()
        assert d["passes"] == []
        assert d["packs"] == []
        assert d["rules"] == []
        assert d["features"] == []


class TestGeneratePlan:
    """Tests for generate_plan function."""

    def test_generate_plan_for_python_project(self) -> None:
        """Generates plan with Python pass for Python project."""
        profile = RepoProfile(languages=["python"], frameworks=[])
        catalog = get_default_catalog()

        plan = generate_plan(profile, catalog)

        pass_ids = [p.id for p in plan.passes]
        assert "python-ast-v1" in pass_ids

    def test_generate_plan_for_html_project(self) -> None:
        """Generates plan with HTML pass for HTML project."""
        profile = RepoProfile(languages=["html"], frameworks=[])
        catalog = get_default_catalog()

        plan = generate_plan(profile, catalog)

        pass_ids = [p.id for p in plan.passes]
        assert "html-pattern-v1" in pass_ids

    def test_generate_plan_for_mixed_project(self) -> None:
        """Generates plan with multiple passes for mixed project."""
        profile = RepoProfile(languages=["python", "html"], frameworks=[])
        catalog = get_default_catalog()

        plan = generate_plan(profile, catalog)

        pass_ids = [p.id for p in plan.passes]
        assert "python-ast-v1" in pass_ids
        assert "html-pattern-v1" in pass_ids

    def test_generate_plan_includes_default_rules(self) -> None:
        """Generated plan includes default exclude rules."""
        profile = RepoProfile(languages=["python"], frameworks=[])
        catalog = get_default_catalog()

        plan = generate_plan(profile, catalog)

        rule_types = [r.rule_type for r in plan.rules]
        assert "exclude_pattern" in rule_types

    def test_generate_plan_skips_unavailable_extras(self) -> None:
        """Generated plan skips extras that aren't installed."""
        profile = RepoProfile(languages=["javascript"], frameworks=[])
        catalog = get_default_catalog()

        # Mock tree_sitter as not installed
        with patch("importlib.util.find_spec", return_value=None):
            plan = generate_plan(profile, catalog)

            # JS pass should not be included since tree-sitter not installed
            pass_ids = [p.id for p in plan.passes]
            assert "javascript-ts-v1" not in pass_ids

    def test_generate_plan_for_fastapi_project(self) -> None:
        """FastAPI project should NOT get packs (deprecated)."""
        # NOTE: Packs are deprecated (ADR-0003). Framework-specific analysis
        # is now handled by linker activation conditions.
        profile = RepoProfile(languages=["python"], frameworks=["fastapi"])
        import warnings
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", DeprecationWarning)
            catalog = get_default_catalog()
            plan = generate_plan(profile, catalog)

        # Packs are no longer added
        assert plan.packs == []
        # But the Python pass should still be present
        pass_ids = [p.id for p in plan.passes]
        assert "python-ast-v1" in pass_ids

    def test_generate_plan_has_version(self) -> None:
        """Generated plan has version field."""
        profile = RepoProfile(languages=["python"], frameworks=[])
        catalog = get_default_catalog()

        plan = generate_plan(profile, catalog)

        assert plan.version == "0.1.0"


class TestValidatePlan:
    """Tests for validate_plan function."""

    def test_validate_plan_accepts_valid_plan(self) -> None:
        """Valid plan passes validation."""
        plan = CapsulePlan(
            version="0.1.0",
            passes=[PassConfig("python-ast-v1", True)],
        )
        catalog = get_default_catalog()

        errors = validate_plan(plan, catalog)

        assert errors == []

    def test_validate_plan_rejects_unknown_pass(self) -> None:
        """Plan with unknown pass fails validation."""
        plan = CapsulePlan(
            version="0.1.0",
            passes=[PassConfig("unknown-pass-v1", True)],
        )
        catalog = get_default_catalog()

        errors = validate_plan(plan, catalog)

        assert len(errors) > 0
        assert "unknown-pass-v1" in errors[0]

    def test_validate_plan_rejects_unknown_pack(self) -> None:
        """Plan with unknown pack fails validation."""
        plan = CapsulePlan(
            version="0.1.0",
            packs=[PackConfig("unknown-pack", True)],
        )
        catalog = get_default_catalog()

        errors = validate_plan(plan, catalog)

        assert len(errors) > 0
        assert "unknown-pack" in errors[0]
