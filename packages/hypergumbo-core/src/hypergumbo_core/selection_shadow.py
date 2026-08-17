# SPDX-License-Identifier: AGPL-3.0-or-later
"""Shadow-mode comparison: what coverage WOULD have selected, versus what ran.

WHY A SHADOW PHASE AT ALL. Coverage-directed selection can only be trusted on
evidence, and the cheapest evidence is free: run the comparison alongside the
real selection on every ordinary invocation, act on none of it, and accumulate
the one number that matters — did coverage ever fail to select a test that the
existing selectors ran AND that FAILED? A selector that never drops a test which
actually caught something is the only safety claim worth having. "The counts
look similar" is not one.

THE TWO DIRECTIONS ARE NOT SYMMETRIC, which is why they are reported separately
and never netted into a single delta:

    MISSED_BY_COVERAGE   ran, but coverage would not have selected it. The
                         DANGEROUS direction. A miss here that also failed is
                         a disqualifying result, not a tuning parameter.
    EXTRA_FROM_COVERAGE  coverage selects it and the existing selectors did
                         not. The WIN — and independently of speed, because it
                         means the static slice missed a real dependency.

GRANULARITY IS DELIBERATELY COARSENED FOR THE COMPARISON. The index selects
individual node ids; smart-test selects test FILES. Comparing node ids against
files would make coverage look better than it is by construction, so the
comparison is done file-to-file. The node-level count is reported alongside as
the POTENTIAL — what a future phase could exploit — and kept clearly distinct
from what is being compared.

JOIN RATE IS A FIRST-CLASS OUTPUT, not a debugging aid. Every join in this
project's history that could silently fail, did: pytest-cov's `|setup` suffix
matched 0 of 20,437 contexts, and node-id prefixes differ by rootdir. If the
junit test list and the index disagree about how a test is spelled, every
comparison below is meaningless while still producing confident-looking
numbers, so the rate is reported and a caller can refuse to draw conclusions.

WHAT PHASE 2 ADDED HERE. Shadow mode only ever REPORTED a selection, so nothing
it produced had to be runnable. ``selectable_test_files`` is the conversion that
makes a selection safe to hand to pytest, and it exists because the index is
persistent, out-of-repo, and remembers test files that have since been renamed
or deleted — a path pytest treats as a collection ERROR rather than a skip. An
unfiltered union would therefore fail runs it was only meant to widen, which
would break the one property Phase 2 rests on: it can only ADD tests.
"""
from __future__ import annotations

# nosec B405: parsed input is our OWN pytest junit output written into .ci/
# moments earlier, never fetched and never user-supplied. See ran_tests_from_junit.
import xml.etree.ElementTree as ET  # nosec B405
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Optional

from hypergumbo_core.selection_index import Selection, files_of


def _resolve_classname(classname: str, repo_root: Path) -> Optional[str]:
    """``a.b.test_x.Klass`` -> ``a/b/test_x.py``, by finding the real file.

    The dotted classname merges the module path and the class nesting with the
    same separator, so the boundary cannot be recovered by string surgery — a
    package directory with a dot, or a class named like a module, both break it.
    Resolution is by EXISTENCE: take the longest dotted prefix that names an
    actual ``.py`` file. Returns None when nothing resolves, so the caller can
    count it rather than silently inventing a path.
    """
    parts = classname.split(".")
    for cut in range(len(parts), 0, -1):
        candidate = Path(*parts[:cut]).with_suffix(".py")
        if (repo_root / candidate).exists():
            return str(candidate)
    return None


def ran_tests_from_junit(junit_path: Path, repo_root: Path) -> set[str]:
    """Node ids of every test in a junit report.

    This is the INDEPENDENT list — independent of coverage, which is the whole
    point. A test that ran and left no coverage rows is invisible to the
    coverage database by definition, so the unmeasured population can only be
    discovered by comparing against a source that does not come from coverage.
    """
    # Not untrusted input: this file is written by our OWN pytest run into
    # .ci/, in the same process tree, moments earlier. It is never fetched,
    # never user-supplied, and a caller able to plant it could simply edit the
    # tests instead. Pulling in defusedxml for it would add a dependency to
    # guard a threat model that does not exist here.
    root = ET.parse(junit_path).getroot()  # noqa: S314  # nosec B314
    out: set[str] = set()
    for case in root.iter("testcase"):
        classname = case.get("classname") or ""
        name = case.get("name") or ""
        path = _resolve_classname(classname, repo_root)
        if path is None:
            continue
        module_parts = len(Path(path).with_suffix("").parts)
        classes = classname.split(".")[module_parts:]
        out.add("::".join([path, *classes, name]))
    return out


def failed_tests_from_junit(junit_path: Path, repo_root: Path) -> set[str]:
    """Node ids of every test that did NOT pass.

    ``<error>`` counts alongside ``<failure>``: a collection or fixture error is
    a test that did not pass, and treating only assertion failures as failures
    is exactly the INV-vilag shape — a green verdict computed over modules that
    never imported. ``<skipped>`` is not a failure.

    This is the load-bearing half of the phase's evidence. The shadow shipped
    without it, which left the exit criterion — "a miss that ALSO FAILED" —
    undecidable no matter how many observations were collected.
    """
    root = ET.parse(junit_path).getroot()  # noqa: S314  # nosec B314
    out: set[str] = set()
    for case in root.iter("testcase"):
        if not any(child.tag in ("failure", "error") for child in case):
            continue
        classname = case.get("classname") or ""
        path = _resolve_classname(classname, repo_root)
        if path is None:
            continue
        module_parts = len(Path(path).with_suffix("").parts)
        classes = classname.split(".")[module_parts:]
        out.add("::".join([path, *classes, case.get("name") or ""]))
    return out


@dataclass(frozen=True)
class ShadowReport:
    """A single observation. Accumulated across commits, these are the evidence."""

    changed_files: frozenset[str]
    coverage_tests: frozenset[str]
    coverage_files: frozenset[str]
    actual_files: frozenset[str]
    missed_by_coverage: frozenset[str]
    extra_from_coverage: frozenset[str]
    new_blocks: int
    unknown_paths: frozenset[str]
    missing_paths: frozenset[str]
    unmeasured: int
    join_rate: float
    #: Node ids that ran and did not pass. ``None`` means the junit
    #: report was unavailable — NOT that nothing failed.
    failed: Optional[frozenset[str]] = None

    @property
    def dangerous_misses(self) -> Optional[frozenset[str]]:
        """Tests that FAILED and whose file coverage would not have selected.

        The one disqualifying result for the phase; every other figure here is
        diagnostics. Raw miss counts are not the criterion — a run can miss 87
        files and lose nothing, because none of them failed.

        Returns None when no failure data was supplied, so "we did not look"
        cannot be read as "nothing failed".
        """
        if self.failed is None:
            return None
        return frozenset(t for t in self.failed
                         if t.split("::")[0] not in self.coverage_files)

    @property
    def informative(self) -> bool:
        """False when the index knew nothing about ANY changed file.

        A cold or unseeded index selects nothing, so every test that ran counts
        as a miss — observed on the first real run: 8 of 8 changed files
        unknown, 0 selected, 87 "missed". Those are phantom misses, and Phase 1's
        exit criterion COUNTS misses, so admitting them would poison the very
        evidence the shadow exists to gather. An observation resting on no
        knowledge is not a negative result; it is not a result.
        """
        return not self.changed_files or bool(
            self.changed_files - self.unknown_paths)

    @property
    def trustworthy(self) -> bool:
        """False when the comparison rests on a join that mostly failed, or on
        an index with nothing to say.

        Both failure modes present identically to a careless reader — zero
        selected, zero extras, and a clean-looking summary. Callers must gate on
        this before reading any other field.
        """
        return self.join_rate >= 0.5 and self.informative

    def summary(self) -> str:
        if self.trustworthy:
            head = ""
        elif not self.informative:
            head = ("  !! INDEX COLD for every changed file — the misses below "
                    "are phantom, exclude this observation\n")
        else:
            head = "  !! JOIN FAILED — figures below are meaningless\n"
        return (
            f"{head}"
            f"  coverage would select : {len(self.coverage_files)} files "
            f"({len(self.coverage_tests)} tests)\n"
            f"  actually selected     : {len(self.actual_files)} files\n"
            f"  MISSED_BY_COVERAGE    : {len(self.missed_by_coverage)}\n"
            f"  EXTRA_FROM_COVERAGE   : {len(self.extra_from_coverage)}\n"
            f"  join rate             : {self.join_rate:.0%}"
        )


def compare(
    selection: Selection,
    actual_files: Iterable[str],
    *,
    known_tests: Optional[Iterable[str]] = None,
    changed_files: Optional[Iterable[str]] = None,
    failed: Optional[Iterable[str]] = None,
) -> ShadowReport:
    """Build one shadow observation.

    ``known_tests`` is the junit-derived list; it exists solely to compute the
    join rate. Passing None means "not asked", and the rate is reported as 1.0
    rather than 0.0 — a missing control must not masquerade as a failed one.
    """
    actual = frozenset(actual_files)
    cov_files = files_of(selection.tests)
    if known_tests is None:
        rate = 1.0
    else:
        known = frozenset(known_tests)
        indexed = frozenset(selection.tests)
        rate = len(indexed & known) / len(indexed) if indexed else 1.0
    return ShadowReport(
        changed_files=frozenset(changed_files or ()),
        coverage_tests=frozenset(selection.tests),
        coverage_files=cov_files,
        actual_files=actual,
        missed_by_coverage=actual - cov_files,
        extra_from_coverage=cov_files - actual,
        new_blocks=len(selection.new_blocks),
        unknown_paths=selection.unknown_paths,
        missing_paths=selection.missing_paths,
        unmeasured=len(selection.unmeasured),
        join_rate=rate,
        failed=None if failed is None else frozenset(failed),
    )
