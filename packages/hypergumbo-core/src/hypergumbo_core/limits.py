"""Limits tracking for behavior map output.

Tracks known limitations and failures during analysis:
- Files that failed to parse (syntax errors, encoding issues)
- Languages detected but not analyzed (no analyzer available)
- Fundamental limitations of static analysis (dynamic imports, eval, etc.)

This explicit acknowledgment of gaps helps agents understand what
the analysis does NOT capture, preventing false confidence.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List

from . import __version__

# Known limitations of static analysis that apply universally
KNOWN_LIMITATIONS = [
    "dynamic imports (importlib, __import__, require with variables)",
    "eval() and exec() calls",
    "runtime code generation",
    "monkey-patching and dynamic attribute assignment",
    "decorators with complex runtime logic",
    "metaprogramming patterns",
]


@dataclass
class ClassificationFailure:
    """A file that failed supply chain classification."""

    path: str
    reason: str

    def to_dict(self) -> Dict[str, str]:
        """Serialize to dict."""
        return {"path": self.path, "reason": self.reason}


@dataclass
class AmbiguousPath:
    """A file with ambiguous supply chain classification."""

    path: str
    assigned: int  # The tier that was assigned
    note: str

    def to_dict(self) -> Dict[str, Any]:
        """Serialize to dict."""
        return {"path": self.path, "assigned": self.assigned, "note": self.note}


@dataclass
class SupplyChainLimits:
    """Tracks supply chain classification issues."""

    classification_failures: List["ClassificationFailure"] = field(default_factory=list)
    ambiguous_paths: List["AmbiguousPath"] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        """Serialize to dict."""
        return {
            "classification_failures": [f.to_dict() for f in self.classification_failures],
            "ambiguous_paths": [p.to_dict() for p in self.ambiguous_paths],
        }


@dataclass
class FailedFile:
    """A file that failed during analysis."""

    path: str
    reason: str
    analyzer: str

    def to_dict(self) -> Dict[str, str]:
        """Serialize to dict."""
        return {
            "path": self.path,
            "reason": self.reason,
            "analyzer": self.analyzer,
        }


@dataclass
class Limits:
    """Tracks limitations and failures during analysis.

    Accumulates information about what was NOT captured during analysis,
    enabling honest reporting of gaps.
    """

    failed_files: List[FailedFile] = field(default_factory=list)
    skipped_languages: List[str] = field(default_factory=list)
    skipped_passes: List[Dict[str, str]] = field(default_factory=list)
    truncated_files: List[Dict[str, Any]] = field(default_factory=list)
    analysis_depth: str = "syntax_only"
    partial_results_reason: str = ""
    max_tier_applied: int | None = None
    max_files_per_analyzer: int | None = None
    test_files_excluded: bool = False
    supply_chain: SupplyChainLimits = field(default_factory=SupplyChainLimits)

    def add_failed_file(self, path: str, reason: str, analyzer: str) -> None:
        """Record a file that failed to analyze."""
        self.failed_files.append(FailedFile(
            path=path,
            reason=reason,
            analyzer=analyzer,
        ))

    def add_skipped_language(self, language: str) -> None:
        """Record a language that was detected but not analyzed."""
        if language not in self.skipped_languages:
            self.skipped_languages.append(language)

    def add_truncated_file(
        self,
        path: str,
        size_bytes: int,
        reason: str,
    ) -> None:
        """Record a file that was truncated or skipped due to size."""
        self.truncated_files.append({
            "path": path,
            "size_bytes": size_bytes,
            "reason": reason,
        })

    def add_classification_failure(self, path: str, reason: str) -> None:
        """Record a file that failed supply chain classification."""
        self.supply_chain.classification_failures.append(
            ClassificationFailure(path=path, reason=reason)
        )

    def add_ambiguous_path(self, path: str, assigned_tier: int, note: str) -> None:
        """Record a file with ambiguous supply chain classification."""
        self.supply_chain.ambiguous_paths.append(
            AmbiguousPath(path=path, assigned=assigned_tier, note=note)
        )

    def merge(self, other: "Limits") -> "Limits":
        """Merge limits from another analysis pass."""
        # Merge supply chain limits
        merged_supply_chain = SupplyChainLimits(
            classification_failures=(
                self.supply_chain.classification_failures
                + other.supply_chain.classification_failures
            ),
            ambiguous_paths=(
                self.supply_chain.ambiguous_paths + other.supply_chain.ambiguous_paths
            ),
        )
        merged = Limits(
            failed_files=self.failed_files + other.failed_files,
            skipped_languages=list(set(self.skipped_languages + other.skipped_languages)),
            skipped_passes=self.skipped_passes + other.skipped_passes,
            truncated_files=self.truncated_files + other.truncated_files,
            analysis_depth=self.analysis_depth,
            partial_results_reason=self.partial_results_reason or other.partial_results_reason,
            max_tier_applied=self.max_tier_applied or other.max_tier_applied,
            max_files_per_analyzer=self.max_files_per_analyzer or other.max_files_per_analyzer,
            test_files_excluded=self.test_files_excluded or other.test_files_excluded,
            supply_chain=merged_supply_chain,
        )
        return merged

    def to_dict(self) -> Dict[str, Any]:
        """Serialize to dict for JSON output."""
        result: Dict[str, Any] = {
            "not_captured": KNOWN_LIMITATIONS.copy(),
            "truncated_files": self.truncated_files,
            "skipped_languages": self.skipped_languages,
            "skipped_passes": self.skipped_passes,
            "failed_files": [f.to_dict() for f in self.failed_files],
            "partial_results_reason": self.partial_results_reason,
            "analyzer_version": f"hypergumbo-{__version__}",
            "analysis_depth": self.analysis_depth,
            "supply_chain": self.supply_chain.to_dict(),
        }
        if self.max_tier_applied is not None:
            result["max_tier_applied"] = self.max_tier_applied
        if self.max_files_per_analyzer is not None:
            result["max_files_per_analyzer"] = self.max_files_per_analyzer
        if self.test_files_excluded:
            result["test_files_excluded"] = True
        return result
