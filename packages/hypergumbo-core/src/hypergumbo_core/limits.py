# SPDX-License-Identifier: AGPL-3.0-or-later
"""Limits tracking for behavior map output.

Tracks known limitations and failures during analysis:
- Files that failed during analysis (syntax/encoding errors)
- Languages detected but not analyzed (no analyzer available)
- Whole passes that crashed or were skipped (§17 fail-open)
- Files truncated or skipped due to size
- Supply chain classification failures and ambiguous tier assignments
- Fundamental limitations of static analysis (dynamic imports, eval, etc.)
Plus scalar honesty signals: partial_results_reason, max_tier_applied,
max_files_per_analyzer, test_files_excluded.

This explicit acknowledgment of gaps helps agents understand what
the analysis does NOT capture, preventing false confidence.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List

from . import __version__

# Known limitations of static analysis that apply UNIVERSALLY — a fixed
# disclaimer, identical for every repo, emitted verbatim as ``limits.not_captured``.
# It is NOT a per-repo measurement of which constructs THIS repo contains but the
# analyzer skipped; deriving that would need cross-analyzer instrumentation that
# has no producer today (WI-togop). Consumers must read it as "static analysis
# never captures these categories anywhere", not as a repo-specific finding.
# (Distinct from ``reproducibility_context.not_captured``, which names repro
# factors — the shared field name is tracked separately by WI-latip/WI-tubim.)
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
        """Serialize to dict.

        INV-virik: the two diagnostic reporting lists are present ONLY when
        non-empty (present-when-populated), so their absence honestly reads as
        "no classification failures / no ambiguous paths" rather than an
        always-empty ``[]`` that a consumer could misread as "classification
        ran and found nothing". An empty ``SupplyChainLimits`` therefore
        serializes as ``{}`` — matching the ``Limits.to_dict``
        truncated_files / skipped_languages / failed_files contract. Both
        fields are optional in the schema (no ``required`` entry), so omission
        is schema-valid.
        """
        result: Dict[str, Any] = {}
        if self.classification_failures:
            result["classification_failures"] = [
                f.to_dict() for f in self.classification_failures
            ]
        if self.ambiguous_paths:
            result["ambiguous_paths"] = [p.to_dict() for p in self.ambiguous_paths]
        return result


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
    partial_results_reason: str = ""
    max_tier_applied: int | None = None
    # WI-tulit: files whose symbols/edges the tier filter dropped (see
    # add_tier_filtered_file). Present-when-populated in to_dict per INV-virik.
    tier_filtered_files: List[str] = field(default_factory=list)
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

    def record_crashed_pass(self, pass_name: str, exc: BaseException) -> None:
        """Record an analyzer/linker pass that crashed mid-run.

        §17 fail-open / WI-madal L3: an exception escaping a *whole* pass is
        pass-level (not tied to one file), so it lands in ``skipped_passes``
        with a ``crashed:`` reason — distinct from the deliberate
        ``"no files matched"`` / missing-dependency skips — rather than
        ``failed_files`` (which is strictly per-file). Also sets
        ``partial_results_reason`` (without clobbering an existing value) so the
        top-level honesty signal fires. Keeping the crash on existing channels
        means no new output-schema field is introduced.
        """
        self.skipped_passes.append({
            "pass": pass_name,
            "reason": f"crashed: {type(exc).__name__}: {exc}",
        })
        if not self.partial_results_reason:
            self.partial_results_reason = (
                "one or more passes crashed; results are partial"
            )

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

    def add_tier_filtered_file(self, path: str) -> None:
        """Record a file whose symbols/edges the supply-chain tier filter dropped.

        WI-tulit: a tier drop (e.g. a DERIVED tier-4 file excluded by default)
        silently removes every symbol and edge for the file without touching
        ``analysis_incomplete`` or ``failed_files``. This list makes the *what*
        visible so a consumer can see which files were excluded; the
        ``max_tier_applied`` value is the *why*.
        """
        self.tier_filtered_files.append(path)

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
            partial_results_reason=self.partial_results_reason or other.partial_results_reason,
            max_tier_applied=self.max_tier_applied or other.max_tier_applied,
            tier_filtered_files=self.tier_filtered_files + other.tier_filtered_files,
            max_files_per_analyzer=self.max_files_per_analyzer or other.max_files_per_analyzer,
            test_files_excluded=self.test_files_excluded or other.test_files_excluded,
            supply_chain=merged_supply_chain,
        )
        return merged

    def to_dict(self) -> Dict[str, Any]:
        """Serialize to dict for JSON output."""
        result: Dict[str, Any] = {
            "not_captured": KNOWN_LIMITATIONS.copy(),
            "skipped_passes": self.skipped_passes,
            "analyzer_version": f"hypergumbo-{__version__}",
            # WI-miron: always observable — present as False when tests were not
            # excluded, so absence is never confused with "tests included".
            "test_files_excluded": self.test_files_excluded,
            "supply_chain": self.supply_chain.to_dict(),
        }
        # INV-virik: the diagnostic reporting lists are present ONLY when non-empty
        # (present-when-populated), so their absence honestly reads as "nothing
        # dropped/failed" rather than an always-empty list that masks the signal.
        # (skipped_passes stays unconditional — it is the populated provenance
        # surface per INV-nihug; test_files_excluded stays always-present per
        # WI-miron.)
        if self.truncated_files:
            result["truncated_files"] = self.truncated_files
        if self.skipped_languages:
            result["skipped_languages"] = self.skipped_languages
        if self.failed_files:
            result["failed_files"] = [f.to_dict() for f in self.failed_files]
        # WI-tamop: partial_results_reason follows the spec §960/§994
        # conditional-presence contract — present only when the analysis is
        # incomplete (a reason has been set), so a consumer can use key presence
        # as the incompleteness signal rather than getting a false positive from
        # an always-present empty string.
        if self.partial_results_reason:
            result["partial_results_reason"] = self.partial_results_reason
        if self.max_tier_applied is not None:
            result["max_tier_applied"] = self.max_tier_applied
        if self.tier_filtered_files:
            result["tier_filtered_files"] = self.tier_filtered_files
        if self.max_files_per_analyzer is not None:
            result["max_files_per_analyzer"] = self.max_files_per_analyzer
        return result
