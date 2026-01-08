"""Capsule plan generation and validation.

Generates capsule_plan.json from detected repo profile and available catalog.
Template-based generation selects appropriate passes and packs based on
detected languages and frameworks without LLM assistance.

How It Works
------------
1. Profile detection identifies languages and frameworks in the repo
2. generate_plan() maps languages to available passes from the catalog
3. Framework detection enables corresponding packs (e.g., FastAPI → python-fastapi)
4. Default rules are added for common patterns (test excludes, entrypoints)
5. validate_plan() ensures all referenced passes/packs exist in catalog

The plan is deterministic given the same profile and catalog inputs,
ensuring reproducible analysis configuration.

Why This Design
---------------
- Template-based approach requires no network/LLM, keeping init offline
- Validation against catalog ensures only known components are used
- Separation of plan generation from execution enables plan inspection/editing
- Default rules provide sensible starting point without repo-specific knowledge
"""
from __future__ import annotations

import warnings
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from .catalog import Catalog, is_available
from .profile import RepoProfile

PLAN_VERSION = "0.1.0"

# Mapping from language names to pass IDs
LANGUAGE_TO_PASS = {
    "python": "python-ast-v1",
    "html": "html-pattern-v1",
    "javascript": "javascript-ts-v1",
    "typescript": "javascript-ts-v1",
}

# Mapping from framework names to pack IDs
FRAMEWORK_TO_PACK = {
    "fastapi": "python-fastapi",
    "flask": "python-fastapi",  # Reuse FastAPI pack for Flask routes
    "electron": "electron-app",
}

# Default exclude patterns
DEFAULT_EXCLUDE_RULES = [
    {"glob": "**/*_test.py", "reason": "Python test files"},
    {"glob": "**/test_*.py", "reason": "Python test files"},
    {"glob": "**/*.spec.js", "reason": "JavaScript test files"},
    {"glob": "**/*.test.js", "reason": "JavaScript test files"},
    {"glob": "**/node_modules/**", "reason": "npm dependencies"},
    {"glob": "**/.venv/**", "reason": "Python virtual environment"},
    {"glob": "**/venv/**", "reason": "Python virtual environment"},
]


@dataclass
class PassConfig:
    """Configuration for an analysis pass in the plan.

    Attributes:
        id: Pass identifier from catalog
        enabled: Whether this pass should run
        config: Optional pass-specific configuration
        requires: Optional list of required extras
    """

    id: str
    enabled: bool = True
    config: Dict[str, Any] = field(default_factory=dict)
    requires: Optional[List[str]] = None

    def to_dict(self) -> Dict[str, Any]:
        """Serialize to dict."""
        d: Dict[str, Any] = {
            "id": self.id,
            "enabled": self.enabled,
        }
        if self.config:
            d["config"] = self.config
        if self.requires:
            d["requires"] = self.requires
        return d


@dataclass
class PackConfig:
    """Configuration for a pack in the plan.

    .. deprecated:: 0.7.0
        PackConfig is deprecated along with Packs. Use the --frameworks flag
        and linker activation conditions instead.

    Attributes:
        id: Pack identifier from catalog
        enabled: Whether this pack should be used
        config: Optional pack-specific configuration
    """

    id: str
    enabled: bool = True
    config: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        """Emit deprecation warning on PackConfig creation."""
        warnings.warn(
            "PackConfig is deprecated and will be removed in a future version. "
            "Use the --frameworks flag and linker activation conditions instead.",
            DeprecationWarning,
            stacklevel=2,
        )

    def to_dict(self) -> Dict[str, Any]:
        """Serialize to dict."""
        d: Dict[str, Any] = {
            "id": self.id,
            "enabled": self.enabled,
        }
        if self.config:
            d["config"] = self.config
        return d


@dataclass
class Rule:
    """A rule in the capsule plan.

    Rules define patterns for entrypoints, excludes, and other behaviors.

    Attributes:
        rule_type: Type of rule (exclude_pattern, entrypoint_pattern)
        glob: Glob pattern for exclude rules
        pattern: Code pattern for entrypoint rules
        label: Label for matched entrypoints
        reason: Human-readable reason for excludes
    """

    rule_type: str
    glob: Optional[str] = None
    pattern: Optional[str] = None
    label: Optional[str] = None
    reason: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        """Serialize to dict."""
        d: Dict[str, Any] = {"type": self.rule_type}
        if self.glob is not None:
            d["glob"] = self.glob
        if self.pattern is not None:
            d["pattern"] = self.pattern
        if self.label is not None:
            d["label"] = self.label
        if self.reason is not None:
            d["reason"] = self.reason
        return d


@dataclass
class CapsulePlan:
    """A complete capsule plan.

    Attributes:
        version: Plan schema version
        passes: List of pass configurations
        packs: List of pack configurations
        rules: List of rules
        features: List of pre-defined feature queries
    """

    version: str = PLAN_VERSION
    passes: List[PassConfig] = field(default_factory=list)
    packs: List[PackConfig] = field(default_factory=list)
    rules: List[Rule] = field(default_factory=list)
    features: List[Dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        """Serialize to dict for JSON output."""
        return {
            "version": self.version,
            "passes": [p.to_dict() for p in self.passes],
            "packs": [p.to_dict() for p in self.packs],
            "rules": [r.to_dict() for r in self.rules],
            "features": self.features,
        }


def generate_plan(profile: RepoProfile, catalog: Catalog) -> CapsulePlan:
    """Generate a capsule plan from profile and catalog.

    Template-based generation that selects passes based on detected
    languages. Framework-specific analysis is now handled by linker
    activation conditions rather than packs.

    Args:
        profile: Detected repo profile with languages and frameworks
        catalog: Available passes and packs

    Returns:
        Generated CapsulePlan ready for serialization
    """
    passes: List[PassConfig] = []
    packs: List[PackConfig] = []
    rules: List[Rule] = []

    # Build lookup of available passes by ID
    catalog_passes = {p.id: p for p in catalog.passes}

    # Add passes for detected languages
    for lang in profile.languages:
        pass_id = LANGUAGE_TO_PASS.get(lang)
        if pass_id and pass_id in catalog_passes:
            catalog_pass = catalog_passes[pass_id]
            # Only include if available (core or extras installed)
            if is_available(catalog_pass):
                # Avoid duplicates
                if not any(p.id == pass_id for p in passes):
                    passes.append(PassConfig(id=pass_id, enabled=True))

    # NOTE: Packs are deprecated (ADR-0003). Framework-specific analysis
    # is now handled by linker activation conditions.
    # The packs list remains empty for backward compatibility.

    # Add default exclude rules
    for rule_def in DEFAULT_EXCLUDE_RULES:
        rules.append(Rule(
            rule_type="exclude_pattern",
            glob=rule_def["glob"],
            reason=rule_def["reason"],
        ))

    return CapsulePlan(
        version=PLAN_VERSION,
        passes=passes,
        packs=packs,
        rules=rules,
        features=[],
    )


def validate_plan(plan: CapsulePlan, catalog: Catalog) -> List[str]:
    """Validate a plan against the catalog.

    Ensures all referenced passes and packs exist in the catalog.

    Args:
        plan: Plan to validate
        catalog: Catalog to validate against

    Returns:
        List of error messages (empty if valid)
    """
    errors: List[str] = []

    # Build lookup sets
    valid_pass_ids = {p.id for p in catalog.passes}
    valid_pack_ids = {p.id for p in catalog.packs}

    # Check passes
    for pass_config in plan.passes:
        if pass_config.id not in valid_pass_ids:
            errors.append(f"Unknown pass: {pass_config.id}")

    # Check packs
    for pack_config in plan.packs:
        if pack_config.id not in valid_pack_ids:
            errors.append(f"Unknown pack: {pack_config.id}")

    return errors
