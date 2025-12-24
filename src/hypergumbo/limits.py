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

    def merge(self, other: "Limits") -> "Limits":
        """Merge limits from another analysis pass."""
        merged = Limits(
            failed_files=self.failed_files + other.failed_files,
            skipped_languages=list(set(self.skipped_languages + other.skipped_languages)),
            skipped_passes=self.skipped_passes + other.skipped_passes,
            truncated_files=self.truncated_files + other.truncated_files,
            analysis_depth=self.analysis_depth,
            partial_results_reason=self.partial_results_reason or other.partial_results_reason,
        )
        return merged

    def to_dict(self) -> Dict[str, Any]:
        """Serialize to dict for JSON output."""
        return {
            "not_captured": KNOWN_LIMITATIONS.copy(),
            "truncated_files": self.truncated_files,
            "skipped_languages": self.skipped_languages,
            "skipped_passes": self.skipped_passes,
            "failed_files": [f.to_dict() for f in self.failed_files],
            "partial_results_reason": self.partial_results_reason,
            "analyzer_version": f"hypergumbo-{__version__}",
            "analysis_depth": self.analysis_depth,
        }
