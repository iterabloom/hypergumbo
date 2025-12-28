"""Token-budgeted Markdown sketch generation.

This module generates human/LLM-readable Markdown summaries of repositories,
optimized for pasting into LLM chat interfaces. Output is token-budgeted
to fill the available context.

How It Works
------------
The sketch is generated progressively to fill the token budget:
1. Header: repo name, language breakdown, LOC estimate (always included)
2. Structure: top-level directory overview
3. Frameworks: detected build systems and dependencies
4. Source files: files in source directories (expands to fill budget)
5. All files: complete file listing (for very large budgets)

Token budgeting uses a simple heuristic (~4 chars per token) which is
accurate enough for approximate sizing. For precise counting, tiktoken
can be used as an optional dependency.

Why Progressive Expansion
-------------------------
Rather than truncating, we progressively add content until approaching
the token budget. This ensures the output uses available context space
effectively while remaining coherent.
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

from .discovery import find_files, DEFAULT_EXCLUDES
from .profile import detect_profile, RepoProfile
from .ir import Symbol
from .entrypoints import detect_entrypoints, Entrypoint
from .ranking import (
    compute_centrality,
    apply_tier_weights,
    compute_file_scores,
    _is_test_path,
)


# Approximate characters per token (conservative estimate for English text)
CHARS_PER_TOKEN = 4


def estimate_tokens(text: str) -> int:
    """Estimate token count using character-based heuristic.

    Uses ~4 characters per token, which is a reasonable approximation
    for English text with OpenAI's tokenizers. Uses ceiling division
    to be conservative and avoid exceeding budgets.

    Args:
        text: The text to estimate tokens for.

    Returns:
        Estimated token count (conservative/ceiling estimate).
    """
    if not text:
        return 0
    # Use ceiling division for conservative estimate
    return max(1, (len(text) + CHARS_PER_TOKEN - 1) // CHARS_PER_TOKEN)


def truncate_to_tokens(text: str, max_tokens: int) -> str:
    """Truncate text to approximately fit within token budget.

    Attempts to truncate at markdown section boundaries (## headers) to
    keep headers with their content. Avoids orphaned headers like
    "## Entry Points" appearing without their content.

    Args:
        text: The text to truncate.
        max_tokens: Maximum tokens allowed.

    Returns:
        Truncated text fitting within budget.
    """
    if estimate_tokens(text) <= max_tokens:
        return text

    # Target character count
    max_chars = max_tokens * CHARS_PER_TOKEN

    # Split by markdown section headers (## ...) while keeping them
    # This ensures headers stay with their content
    import re

    # Find all section starts (lines beginning with ## )
    section_pattern = re.compile(r"^(## .+)$", re.MULTILINE)
    section_starts = [(m.start(), m.group(1)) for m in section_pattern.finditer(text)]

    if not section_starts:
        # No markdown sections, fall back to paragraph splitting
        paragraphs = text.split("\n\n")
        result_parts = []
        current_length = 0

        for para in paragraphs:
            para_with_sep = para + "\n\n"
            if current_length + len(para_with_sep) <= max_chars:
                result_parts.append(para)
                current_length += len(para_with_sep)
            else:
                break

        if result_parts:
            return "\n\n".join(result_parts)
        return text[:max_chars]

    # Extract sections (each section is header + content until next header)
    sections = []
    for i, (start, _header) in enumerate(section_starts):
        if i + 1 < len(section_starts):
            end = section_starts[i + 1][0]
        else:
            end = len(text)
        sections.append(text[start:end].rstrip())

    # Include any content before the first section (like the title)
    prefix = text[: section_starts[0][0]].rstrip() if section_starts[0][0] > 0 else ""

    # Build result keeping whole sections
    result_parts = [prefix] if prefix else []
    current_length = len(prefix) + 2 if prefix else 0

    for section in sections:
        section_with_sep = section + "\n\n"
        if current_length + len(section_with_sep) <= max_chars:
            result_parts.append(section)
            current_length += len(section_with_sep)
        else:
            # Can't fit this section, stop here
            break

    if result_parts:
        return "\n\n".join(result_parts)

    # Fallback: hard truncate if nothing fits
    return text[:max_chars]  # pragma: no cover - defensive path


def _format_language_stats(profile: RepoProfile) -> str:
    """Format language statistics as a summary line."""
    if not profile.languages:
        return "No source files detected"

    # Sort by LOC descending
    sorted_langs = sorted(
        profile.languages.items(),
        key=lambda x: x[1].loc,
        reverse=True,
    )

    # Calculate percentages
    total_loc = sum(lang.loc for lang in profile.languages.values())
    if total_loc == 0:
        return "No source code detected"

    parts = []
    for lang, stats in sorted_langs[:5]:  # Top 5 languages
        pct = (stats.loc / total_loc) * 100
        if pct >= 1:  # Only show languages with ≥1%
            parts.append(f"{lang.title()} ({pct:.0f}%)")

    total_files = sum(lang.files for lang in profile.languages.values())
    return f"{', '.join(parts)} · {total_files} files · ~{total_loc:,} LOC"


def _format_structure(repo_root: Path) -> str:
    """Format top-level directory structure."""
    lines = ["## Structure", ""]

    # Get top-level directories
    dirs = sorted([
        d.name for d in repo_root.iterdir()
        if d.is_dir() and not d.name.startswith(".")
    ])

    # Common source directories to highlight
    source_dirs = {"src", "lib", "app", "pkg", "cmd", "internal", "core"}
    test_dirs = {"test", "tests", "spec", "specs", "__tests__"}
    doc_dirs = {"docs", "doc", "documentation"}

    for d in dirs[:10]:  # Limit to 10 directories
        if d in source_dirs:
            lines.append(f"- `{d}/` — Source code")
        elif d in test_dirs:
            lines.append(f"- `{d}/` — Tests")
        elif d in doc_dirs:
            lines.append(f"- `{d}/` — Documentation")
        else:
            lines.append(f"- `{d}/`")

    if len(dirs) > 10:
        lines.append(f"- ... and {len(dirs) - 10} more directories")

    return "\n".join(lines)


def _format_frameworks(profile: RepoProfile) -> str:
    """Format detected frameworks."""
    if not profile.frameworks:
        return ""

    lines = ["## Frameworks", ""]
    for framework in sorted(profile.frameworks):
        lines.append(f"- {framework}")

    return "\n".join(lines)


def _get_repo_name(repo_root: Path) -> str:
    """Get repository name from path."""
    return repo_root.resolve().name


# Source file extensions by language
SOURCE_EXTENSIONS = {
    "python": ["*.py"],
    "javascript": ["*.js", "*.jsx", "*.mjs"],
    "typescript": ["*.ts", "*.tsx"],
    "go": ["*.go"],
    "rust": ["*.rs"],
    "java": ["*.java"],
    "c": ["*.c", "*.h"],
    "cpp": ["*.cpp", "*.cc", "*.hpp", "*.hh"],
    "ruby": ["*.rb"],
    "php": ["*.php"],
}

# Common source directories
SOURCE_DIRS = {"src", "lib", "app", "pkg", "cmd", "internal", "core", "source"}


def _collect_source_files(repo_root: Path, profile: RepoProfile) -> list[Path]:
    """Collect source files, prioritizing source directories."""
    files: list[Path] = []
    seen: set[Path] = set()

    # Get patterns for detected languages
    patterns: list[str] = []
    for lang in profile.languages:
        if lang in SOURCE_EXTENSIONS:
            patterns.extend(SOURCE_EXTENSIONS[lang])

    if not patterns:
        # Fallback to common patterns
        patterns = ["*.py", "*.js", "*.ts", "*.go", "*.rs", "*.java"]

    # First, collect files from source directories (sorted for determinism)
    for source_dir in sorted(SOURCE_DIRS):
        src_path = repo_root / source_dir
        if src_path.is_dir():
            for f in find_files(src_path, patterns):
                if f not in seen:
                    files.append(f)
                    seen.add(f)

    # Then collect remaining files from root
    for f in find_files(repo_root, patterns):
        if f not in seen:
            files.append(f)
            seen.add(f)

    return files


def _format_source_files(
    repo_root: Path,
    files: list[Path],
    max_files: int = 50,
) -> str:
    """Format source files as a Markdown section."""
    if not files:
        return ""

    lines = ["## Source Files", ""]

    for f in files[:max_files]:
        rel_path = f.relative_to(repo_root)
        lines.append(f"- `{rel_path}`")

    if len(files) > max_files:
        lines.append(f"- ... and {len(files) - max_files} more files")

    return "\n".join(lines)


def _format_all_files(
    repo_root: Path,
    max_files: int = 200,
) -> str:
    """Format all files (non-excluded) as a Markdown section."""
    # Collect all non-excluded files
    files: list[Path] = []
    for f in repo_root.rglob("*"):
        if f.is_file():
            # Check exclusions
            excluded = False
            for part in f.relative_to(repo_root).parts:
                for pattern in DEFAULT_EXCLUDES:
                    if part == pattern or (
                        "*" in pattern and part.endswith(pattern.lstrip("*"))
                    ):
                        excluded = True
                        break
                if excluded:
                    break
            if not excluded and not any(p.startswith(".") for p in f.parts):
                files.append(f)

    if not files:
        return ""

    # Sort by path
    files.sort(key=lambda p: str(p.relative_to(repo_root)))

    lines = ["## All Files", ""]

    for f in files[:max_files]:
        rel_path = f.relative_to(repo_root)
        lines.append(f"- `{rel_path}`")

    if len(files) > max_files:
        lines.append(f"- ... and {len(files) - max_files} more files")

    return "\n".join(lines)


def _run_analysis(
    repo_root: Path, profile: RepoProfile, exclude_tests: bool = False
) -> tuple[list[Symbol], list]:
    """Run static analysis to get symbols and edges.

    Only runs analysis for detected languages to avoid unnecessary work.
    Applies supply chain classification to all symbols.

    Args:
        repo_root: Path to the repository root.
        profile: Detected repository profile with language info.
        exclude_tests: If True, filter out symbols from test files after analysis.

    Returns:
        (symbols, edges) tuple.
    """
    from .supply_chain import classify_file, detect_package_roots

    all_symbols: list[Symbol] = []
    all_edges: list = []

    # Only import and run analyzers if we have the relevant languages
    if "python" in profile.languages:
        try:
            from .analyze.py import analyze_python
            result = analyze_python(repo_root)
            all_symbols.extend(result.symbols)
            all_edges.extend(result.edges)
        except Exception:  # pragma: no cover
            pass  # Analysis failed, continue without Python symbols

    if "javascript" in profile.languages or "typescript" in profile.languages:
        try:  # pragma: no cover
            from .analyze.js_ts import analyze_javascript  # pragma: no cover
            result = analyze_javascript(repo_root)  # pragma: no cover
            all_symbols.extend(result.symbols)  # pragma: no cover
            all_edges.extend(result.edges)  # pragma: no cover
        except Exception:  # pragma: no cover
            pass  # JS/TS analysis failed or tree-sitter not available

    if "c" in profile.languages:
        try:  # pragma: no cover
            from .analyze.c import analyze_c  # pragma: no cover
            result = analyze_c(repo_root)  # pragma: no cover
            all_symbols.extend(result.symbols)  # pragma: no cover
            all_edges.extend(result.edges)  # pragma: no cover
        except Exception:  # pragma: no cover
            pass  # C analysis failed or tree-sitter not available

    if "rust" in profile.languages:
        try:  # pragma: no cover
            from .analyze.rust import analyze_rust  # pragma: no cover
            result = analyze_rust(repo_root)  # pragma: no cover
            all_symbols.extend(result.symbols)  # pragma: no cover
            all_edges.extend(result.edges)  # pragma: no cover
        except Exception:  # pragma: no cover
            pass  # Rust analysis failed or tree-sitter not available

    if "php" in profile.languages:
        try:  # pragma: no cover
            from .analyze.php import analyze_php  # pragma: no cover
            result = analyze_php(repo_root)  # pragma: no cover
            all_symbols.extend(result.symbols)  # pragma: no cover
            all_edges.extend(result.edges)  # pragma: no cover
        except Exception:  # pragma: no cover
            pass  # PHP analysis failed or tree-sitter not available

    if "java" in profile.languages:
        try:  # pragma: no cover
            from .analyze.java import analyze_java  # pragma: no cover
            result = analyze_java(repo_root)  # pragma: no cover
            all_symbols.extend(result.symbols)  # pragma: no cover
            all_edges.extend(result.edges)  # pragma: no cover
        except Exception:  # pragma: no cover
            pass  # Java analysis failed or tree-sitter not available

    if "go" in profile.languages:
        try:  # pragma: no cover
            from .analyze.go import analyze_go  # pragma: no cover
            result = analyze_go(repo_root)  # pragma: no cover
            all_symbols.extend(result.symbols)  # pragma: no cover
            all_edges.extend(result.edges)  # pragma: no cover
        except Exception:  # pragma: no cover
            pass  # Go analysis failed or tree-sitter not available

    if "ruby" in profile.languages:
        try:  # pragma: no cover
            from .analyze.ruby import analyze_ruby  # pragma: no cover
            result = analyze_ruby(repo_root)  # pragma: no cover
            all_symbols.extend(result.symbols)  # pragma: no cover
            all_edges.extend(result.edges)  # pragma: no cover
        except Exception:  # pragma: no cover
            pass  # Ruby analysis failed or tree-sitter not available

    if "kotlin" in profile.languages:
        try:  # pragma: no cover
            from .analyze.kotlin import analyze_kotlin  # pragma: no cover
            result = analyze_kotlin(repo_root)  # pragma: no cover
            all_symbols.extend(result.symbols)  # pragma: no cover
            all_edges.extend(result.edges)  # pragma: no cover
        except Exception:  # pragma: no cover
            pass  # Kotlin analysis failed or tree-sitter not available

    if "swift" in profile.languages:
        try:  # pragma: no cover
            from .analyze.swift import analyze_swift  # pragma: no cover
            result = analyze_swift(repo_root)  # pragma: no cover
            all_symbols.extend(result.symbols)  # pragma: no cover
            all_edges.extend(result.edges)  # pragma: no cover
        except Exception:  # pragma: no cover
            pass  # Swift analysis failed or tree-sitter not available

    if "scala" in profile.languages:
        try:  # pragma: no cover
            from .analyze.scala import analyze_scala  # pragma: no cover
            result = analyze_scala(repo_root)  # pragma: no cover
            all_symbols.extend(result.symbols)  # pragma: no cover
            all_edges.extend(result.edges)  # pragma: no cover
        except Exception:  # pragma: no cover
            pass  # Scala analysis failed or tree-sitter not available

    if "lua" in profile.languages:
        try:  # pragma: no cover
            from .analyze.lua import analyze_lua  # pragma: no cover
            result = analyze_lua(repo_root)  # pragma: no cover
            all_symbols.extend(result.symbols)  # pragma: no cover
            all_edges.extend(result.edges)  # pragma: no cover
        except Exception:  # pragma: no cover
            pass  # Lua analysis failed or tree-sitter not available

    if "haskell" in profile.languages:
        try:  # pragma: no cover
            from .analyze.haskell import analyze_haskell  # pragma: no cover
            result = analyze_haskell(repo_root)  # pragma: no cover
            all_symbols.extend(result.symbols)  # pragma: no cover
            all_edges.extend(result.edges)  # pragma: no cover
        except Exception:  # pragma: no cover
            pass  # Haskell analysis failed or tree-sitter not available

    if "agda" in profile.languages:
        try:  # pragma: no cover
            from .analyze.agda import analyze_agda  # pragma: no cover
            result = analyze_agda(repo_root)  # pragma: no cover
            all_symbols.extend(result.symbols)  # pragma: no cover
            all_edges.extend(result.edges)  # pragma: no cover
        except Exception:  # pragma: no cover
            pass  # Agda analysis failed or tree-sitter not available

    if "lean" in profile.languages:
        try:  # pragma: no cover
            from .analyze.lean import analyze_lean  # pragma: no cover
            result = analyze_lean(repo_root)  # pragma: no cover
            all_symbols.extend(result.symbols)  # pragma: no cover
            all_edges.extend(result.edges)  # pragma: no cover
        except Exception:  # pragma: no cover
            pass  # Lean analysis failed or tree-sitter not available

    if "wolfram" in profile.languages:
        try:  # pragma: no cover
            from .analyze.wolfram import analyze_wolfram  # pragma: no cover
            result = analyze_wolfram(repo_root)  # pragma: no cover
            all_symbols.extend(result.symbols)  # pragma: no cover
            all_edges.extend(result.edges)  # pragma: no cover
        except Exception:  # pragma: no cover
            pass  # Wolfram analysis failed or tree-sitter not available

    if "ocaml" in profile.languages:
        try:  # pragma: no cover
            from .analyze.ocaml import analyze_ocaml  # pragma: no cover
            result = analyze_ocaml(repo_root)  # pragma: no cover
            all_symbols.extend(result.symbols)  # pragma: no cover
            all_edges.extend(result.edges)  # pragma: no cover
        except Exception:  # pragma: no cover
            pass  # OCaml analysis failed or tree-sitter not available

    if "solidity" in profile.languages:
        try:  # pragma: no cover
            from .analyze.solidity import analyze_solidity  # pragma: no cover
            result = analyze_solidity(repo_root)  # pragma: no cover
            all_symbols.extend(result.symbols)  # pragma: no cover
            all_edges.extend(result.edges)  # pragma: no cover
        except Exception:  # pragma: no cover
            pass  # Solidity analysis failed or tree-sitter not available

    if "csharp" in profile.languages:
        try:  # pragma: no cover
            from .analyze.csharp import analyze_csharp  # pragma: no cover
            result = analyze_csharp(repo_root)  # pragma: no cover
            all_symbols.extend(result.symbols)  # pragma: no cover
            all_edges.extend(result.edges)  # pragma: no cover
        except Exception:  # pragma: no cover
            pass  # C# analysis failed or tree-sitter not available

    if "cpp" in profile.languages:
        try:  # pragma: no cover
            from .analyze.cpp import analyze_cpp  # pragma: no cover
            result = analyze_cpp(repo_root)  # pragma: no cover
            all_symbols.extend(result.symbols)  # pragma: no cover
            all_edges.extend(result.edges)  # pragma: no cover
        except Exception:  # pragma: no cover
            pass  # C++ analysis failed or tree-sitter not available

    if "zig" in profile.languages:
        try:  # pragma: no cover
            from .analyze.zig import analyze_zig  # pragma: no cover
            result = analyze_zig(repo_root)  # pragma: no cover
            all_symbols.extend(result.symbols)  # pragma: no cover
            all_edges.extend(result.edges)  # pragma: no cover
        except Exception:  # pragma: no cover
            pass  # Zig analysis failed or tree-sitter not available

    # Filter out test files if requested (significant speedup for large codebases)
    if exclude_tests:
        # Filter symbols from test files
        filtered_symbols = [s for s in all_symbols if not _is_test_path(s.path)]
        # Get IDs of remaining symbols for edge filtering
        remaining_ids = {s.id for s in filtered_symbols}
        # Filter edges to only include those between remaining symbols
        filtered_edges = [
            e for e in all_edges
            if getattr(e, "src", None) in remaining_ids
            and getattr(e, "dst", None) in remaining_ids
        ]
        all_symbols = filtered_symbols
        all_edges = filtered_edges

    # Apply supply chain classification to all symbols
    package_roots = detect_package_roots(repo_root)
    for symbol in all_symbols:
        file_path = repo_root / symbol.path
        classification = classify_file(file_path, repo_root, package_roots)
        symbol.supply_chain_tier = classification.tier.value
        symbol.supply_chain_reason = classification.reason

    return all_symbols, all_edges


def _format_entrypoints(
    entrypoints: list[Entrypoint],
    symbols: list[Symbol],
    repo_root: Path,
    max_entries: int = 20,
) -> str:
    """Format detected entry points as a Markdown section."""
    if not entrypoints:
        return ""

    # Build symbol lookup for path info
    symbol_by_id = {s.id: s for s in symbols}

    # Sort by confidence (highest first)
    sorted_eps = sorted(entrypoints, key=lambda e: -e.confidence)

    lines = ["## Entry Points", ""]

    for ep in sorted_eps[:max_entries]:
        sym = symbol_by_id.get(ep.symbol_id)
        if sym:
            rel_path = sym.path
            if rel_path.startswith(str(repo_root)):
                rel_path = rel_path[len(str(repo_root)) + 1:]
            lines.append(f"- `{sym.name}` ({ep.label}) — `{rel_path}`")
        else:
            lines.append(f"- `{ep.symbol_id}` ({ep.label})")

    if len(entrypoints) > max_entries:
        lines.append(f"- ... and {len(entrypoints) - max_entries} more entry points")

    return "\n".join(lines)


def _select_symbols_two_phase(
    by_file: dict[str, list[Symbol]],
    centrality: dict[str, float],
    file_scores: dict[str, float],
    max_symbols: int,
    entrypoint_files: set[str],
    max_files: int = 20,
    coverage_fraction: float = 0.33,
    diminishing_alpha: float = 0.7,
) -> list[tuple[str, Symbol]]:
    """Select symbols using two-phase policy for breadth + depth.

    Phase 1 (coverage-first): Pick the best symbol from each eligible file
    in rounds, ensuring representation across subsystems.

    Phase 2 (diminishing-returns greedy): Fill remaining slots using marginal
    utility that penalizes repeated picks from the same file.

    Args:
        by_file: Symbols grouped by file path, sorted by centrality within each file.
        centrality: Centrality scores for each symbol ID.
        file_scores: File importance scores (sum of top-K).
        max_symbols: Total symbol budget.
        entrypoint_files: Set of file paths containing entrypoints (always included).
        max_files: Maximum number of files to consider.
        coverage_fraction: Fraction of budget for phase 1 (coverage).
        diminishing_alpha: Penalty factor for repeated file picks in phase 2.

    Returns:
        List of (file_path, symbol) tuples in selection order.
    """
    import heapq

    # Gate eligible files: top N by file_score, plus entrypoint files
    sorted_files = sorted(file_scores.keys(), key=lambda f: -file_scores.get(f, 0))
    eligible_files = set(sorted_files[:max_files]) | entrypoint_files

    # Filter by_file to eligible files only
    eligible_by_file = {f: syms for f, syms in by_file.items() if f in eligible_files}

    if not eligible_by_file:  # pragma: no cover
        return []

    # Track per-file state: next symbol index and pick count
    file_state: dict[str, dict] = {
        f: {"next_idx": 0, "picks": 0, "symbols": syms}
        for f, syms in eligible_by_file.items()
    }

    selected: list[tuple[str, Symbol]] = []

    # Phase 1: Coverage-first - pick best symbol from each file in rounds
    coverage_budget = int(max_symbols * coverage_fraction)
    coverage_budget = min(coverage_budget, len(eligible_by_file))  # Cap at file count

    # Order files by file_score for round-robin
    phase1_files = sorted(eligible_by_file.keys(), key=lambda f: -file_scores.get(f, 0))

    for file_path in phase1_files:
        if len(selected) >= coverage_budget:
            break
        state = file_state[file_path]
        if state["next_idx"] < len(state["symbols"]):
            sym = state["symbols"][state["next_idx"]]
            selected.append((file_path, sym))
            state["next_idx"] += 1
            state["picks"] += 1

    # Phase 2: Diminishing-returns greedy fill
    remaining_budget = max_symbols - len(selected)

    if remaining_budget > 0:
        # Build priority queue with marginal utility
        # marginal = score / (1 + alpha * picks_from_file)
        pq: list[tuple[float, str, int]] = []  # (-marginal, file_path, sym_idx)

        for file_path, state in file_state.items():
            idx = state["next_idx"]
            if idx < len(state["symbols"]):
                sym = state["symbols"][idx]
                score = centrality.get(sym.id, 0)
                picks = state["picks"]
                marginal = score / (1 + diminishing_alpha * picks)
                heapq.heappush(pq, (-marginal, file_path, idx))

        while len(selected) < max_symbols and pq:
            neg_marginal, file_path, sym_idx = heapq.heappop(pq)
            state = file_state[file_path]

            # Check if this entry is stale (index already advanced)
            if sym_idx != state["next_idx"]:  # pragma: no cover
                continue

            sym = state["symbols"][sym_idx]
            selected.append((file_path, sym))
            state["next_idx"] += 1
            state["picks"] += 1

            # Push next symbol from this file if available
            next_idx = state["next_idx"]
            if next_idx < len(state["symbols"]):
                next_sym = state["symbols"][next_idx]
                score = centrality.get(next_sym.id, 0)
                picks = state["picks"]
                marginal = score / (1 + diminishing_alpha * picks)
                heapq.heappush(pq, (-marginal, file_path, next_idx))

    return selected


def _format_symbols(
    symbols: list[Symbol],
    edges: list,
    repo_root: Path,
    max_symbols: int = 100,
    first_party_priority: bool = True,
    entrypoint_files: set[str] | None = None,
    max_symbols_per_file: int = 5,
) -> str:
    """Format key symbols (functions, classes) as a Markdown section.

    Uses a two-phase selection policy for balanced coverage:
    1. Coverage-first: Pick best symbol from each top file
    2. Diminishing-returns: Fill remaining slots with marginal utility

    File ordering uses sum-of-top-K centrality scores (density metric)
    rather than single-max, for more stable and intuitive ranking.

    Per-file rendering is capped to avoid visual monopoly, with a
    summary line for additional selected symbols.

    Args:
        symbols: List of symbols from analysis.
        edges: List of edges from analysis.
        repo_root: Repository root path.
        max_symbols: Maximum symbols to include.
        first_party_priority: If True (default), boost first-party symbols.
        entrypoint_files: Set of file paths containing entrypoints (preserved).
        max_symbols_per_file: Max symbols to render per file (compression).
    """
    if not symbols:
        return ""

    if entrypoint_files is None:
        entrypoint_files = set()

    # Filter to functions and classes, exclude test files and derived artifacts
    key_symbols = [
        s for s in symbols
        if s.kind in ("function", "class", "method")
        and not _is_test_path(s.path)
        and "test_" not in s.name  # Exclude test functions
        and s.supply_chain_tier != 4  # Exclude derived artifacts (bundles, etc.)
    ]

    # Build lookup: symbol ID -> path (for filtering edges by source)
    symbol_path_by_id = {s.id: s.path for s in symbols}

    # Filter edges: exclude edges originating from test files
    production_edges = [
        e for e in edges
        if not _is_test_path(symbol_path_by_id.get(getattr(e, 'src', ''), ''))
    ]

    if not key_symbols:
        return ""

    # Compute centrality scores using only production edges
    raw_centrality = compute_centrality(key_symbols, production_edges)

    # Apply tier-based weighting (first-party symbols boosted) if enabled
    if first_party_priority:
        centrality = apply_tier_weights(raw_centrality, key_symbols)
    else:
        centrality = raw_centrality

    # Sort by weighted centrality (most called first), then by name for stability
    key_symbols.sort(key=lambda s: (-centrality.get(s.id, 0), s.name))

    # Group by file, preserving centrality order within files
    by_file: dict[str, list[Symbol]] = {}
    for s in key_symbols:
        rel_path = s.path
        if rel_path.startswith(str(repo_root)):
            rel_path = rel_path[len(str(repo_root)) + 1:]
        by_file.setdefault(rel_path, []).append(s)

    # Compute file scores using sum-of-top-K (B3: density metric)
    file_scores = compute_file_scores(by_file, centrality, top_k=3)

    # Normalize entrypoint file paths
    normalized_ep_files: set[str] = set()
    repo_root_str = str(repo_root)
    for ep_path in entrypoint_files:
        if ep_path.startswith(repo_root_str):
            normalized_ep_files.add(ep_path[len(repo_root_str) + 1:])
        else:  # pragma: no cover
            normalized_ep_files.add(ep_path)

    # Two-phase selection (B1)
    selected = _select_symbols_two_phase(
        by_file=by_file,
        centrality=centrality,
        file_scores=file_scores,
        max_symbols=max_symbols,
        entrypoint_files=normalized_ep_files,
    )

    if not selected:  # pragma: no cover
        return ""

    # Group selected symbols by file for rendering
    selected_by_file: dict[str, list[Symbol]] = {}
    for file_path, sym in selected:
        selected_by_file.setdefault(file_path, []).append(sym)

    # Order files by file_score (B3), then alphabetically for tie-breaking
    sorted_files = sorted(
        selected_by_file.keys(),
        key=lambda f: (-file_scores.get(f, 0), f)
    )

    # Find max centrality for star threshold
    max_centrality = max(centrality.values()) if centrality else 1.0
    star_threshold = max_centrality * 0.5

    lines = ["## Key Symbols", ""]
    lines.append("*★ = centrality ≥ 50% of max*")
    lines.append("")

    total_rendered = 0
    for file_path in sorted_files:
        file_symbols = selected_by_file[file_path]

        lines.append(f"### `{file_path}`")

        # Render up to max_symbols_per_file (B2: compression)
        rendered_count = 0
        for sym in file_symbols[:max_symbols_per_file]:
            kind_label = sym.kind
            score = centrality.get(sym.id, 0)
            if score >= star_threshold:
                lines.append(f"- `{sym.name}` ({kind_label}) ★")
            else:
                lines.append(f"- `{sym.name}` ({kind_label})")
            rendered_count += 1
            total_rendered += 1

        # Summary line for remaining symbols in this file (B2)
        remaining_in_file = len(file_symbols) - rendered_count
        if remaining_in_file > 0:
            # Show stats for compressed symbols
            remaining_scores = [centrality.get(s.id, 0) for s in file_symbols[max_symbols_per_file:]]
            if remaining_scores:
                top_score = max(remaining_scores)
                lines.append(f"  *… +{remaining_in_file} more (top score: {top_score:.2f})*")

        lines.append("")  # Blank line between files

    # Global summary of unselected symbols
    total_selected = len(selected)
    total_candidates = len(key_symbols)
    unselected = total_candidates - total_selected
    if unselected > 0:
        lines.append(f"*… and {unselected} more symbols across {len(by_file) - len(selected_by_file)} other files*")

    return "\n".join(lines)


def generate_sketch(
    repo_root: Path,
    max_tokens: Optional[int] = None,
    exclude_tests: bool = False,
    first_party_priority: bool = True,
) -> str:
    """Generate a token-budgeted Markdown sketch of the repository.

    The sketch progressively includes content to fill the token budget:
    1. Header with language breakdown and LOC (always included)
    2. Directory structure
    3. Detected frameworks
    4. Source files (for medium budgets)
    5. Entry points from static analysis (for larger budgets)
    6. Key symbols from static analysis (for large budgets)
    7. All files (for very large budgets)

    Args:
        repo_root: Path to the repository root.
        max_tokens: Target tokens for output. If None, returns minimal sketch.
        exclude_tests: If True, skip analyzing test files for faster performance.
        first_party_priority: If True (default), boost first-party symbols in
            ranking. Set False to use raw centrality scores.

    Returns:
        Markdown-formatted sketch string.
    """
    repo_root = Path(repo_root).resolve()
    profile = detect_profile(repo_root)
    repo_name = _get_repo_name(repo_root)

    # Build base sections (always included)
    sections = []

    # Section 1: Header (always included, highest priority)
    header = f"# {repo_name}\n\n## Overview\n{_format_language_stats(profile)}"
    sections.append(header)

    # Section 2: Structure
    structure = _format_structure(repo_root)
    if structure:
        sections.append(structure)

    # Section 3: Frameworks
    frameworks = _format_frameworks(profile)
    if frameworks:
        sections.append(frameworks)

    # Combine base sections
    base_sketch = "\n\n".join(sections)
    base_tokens = estimate_tokens(base_sketch)

    # If no budget or budget is small, return base sketch (possibly truncated)
    if max_tokens is None:
        return base_sketch

    if max_tokens <= base_tokens:
        return truncate_to_tokens(base_sketch, max_tokens)

    # We have room to expand - calculate remaining budget
    remaining_tokens = max_tokens - base_tokens

    # Collect source files for expansion
    source_files = _collect_source_files(repo_root, profile)

    # Estimate tokens per file item
    # Typical line: "- `path/to/long/filename.py`" is ~50 chars = ~12 tokens
    tokens_per_file = 12

    # Estimate tokens per entry point or symbol item (~25 chars = ~6 tokens)
    tokens_per_item = 6

    # Section 4: Source files (if we have budget >= 50 tokens remaining)
    if remaining_tokens > 50 and source_files:
        # Use up to half of remaining budget for source files at small budgets
        # Scale down the fraction as budget grows (files are less important)
        # Reserve space for Entry Points and Key Symbols sections
        if remaining_tokens < 300:
            budget_for_files = (remaining_tokens * 2) // 3  # 66% at small budgets
        else:
            # At larger budgets, limit files to 25% to leave room for analysis
            budget_for_files = remaining_tokens // 4  # 25% at larger budgets
        max_source_files = max(5, budget_for_files // tokens_per_file)

        source_section = _format_source_files(
            repo_root, source_files, max_files=max_source_files
        )
        if source_section:
            sections.append(source_section)

        # Recalculate remaining budget
        current_sketch = "\n\n".join(sections)
        current_tokens = estimate_tokens(current_sketch)
        remaining_tokens = max_tokens - current_tokens

    # For larger budgets, run static analysis
    symbols: list[Symbol] = []
    edges: list = []
    if remaining_tokens > 100:
        symbols, edges = _run_analysis(repo_root, profile, exclude_tests=exclude_tests)

    # Section 5: Entry points (if we have analysis results and budget)
    # Track entrypoint files for B4: preserve in Key Symbols
    entrypoint_files: set[str] = set()
    entrypoints: list[Entrypoint] = []

    if remaining_tokens > 50 and symbols:
        entrypoints = detect_entrypoints(symbols, edges)
        if entrypoints:
            # Build symbol lookup for extracting file paths
            symbol_by_id = {s.id: s for s in symbols}

            # Extract file paths from entrypoints (B4)
            for ep in entrypoints:
                sym = symbol_by_id.get(ep.symbol_id)
                if sym:
                    entrypoint_files.add(sym.path)

            # Entry points are high value, give them space
            budget_for_eps = remaining_tokens // 3
            max_eps = max(5, budget_for_eps // tokens_per_item)

            ep_section = _format_entrypoints(
                entrypoints, symbols, repo_root, max_entries=max_eps
            )
            if ep_section:
                sections.append(ep_section)

            # Recalculate remaining budget
            current_sketch = "\n\n".join(sections)
            current_tokens = estimate_tokens(current_sketch)
            remaining_tokens = max_tokens - current_tokens

    # Section 6: Key symbols (if we still have budget >= 200 tokens)
    if remaining_tokens > 200 and symbols:
        # Use most of remaining budget for symbols
        budget_for_symbols = (remaining_tokens * 4) // 5  # 80% of remaining
        max_symbols = max(10, budget_for_symbols // tokens_per_item)

        symbols_section = _format_symbols(
            symbols,
            edges,
            repo_root,
            max_symbols=max_symbols,
            first_party_priority=first_party_priority,
            entrypoint_files=entrypoint_files,  # B4: preserve entrypoint files
        )
        if symbols_section:
            sections.append(symbols_section)

            # Recalculate remaining budget
            current_sketch = "\n\n".join(sections)
            current_tokens = estimate_tokens(current_sketch)
            remaining_tokens = max_tokens - current_tokens

    # Section 7: All files (if we still have budget after everything else)
    if remaining_tokens > 50:
        budget_for_files = remaining_tokens - 10
        max_all_files = max(1, budget_for_files // tokens_per_item)

        all_files_section = _format_all_files(repo_root, max_files=max_all_files)
        if all_files_section:
            sections.append(all_files_section)

    # Combine all sections
    full_sketch = "\n\n".join(sections)

    # Final truncation to ensure we don't exceed budget
    return truncate_to_tokens(full_sketch, max_tokens)
