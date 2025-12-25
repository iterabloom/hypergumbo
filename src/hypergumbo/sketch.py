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


# Approximate characters per token (conservative estimate for English text)
CHARS_PER_TOKEN = 4


def estimate_tokens(text: str) -> int:
    """Estimate token count using character-based heuristic.

    Uses ~4 characters per token, which is a reasonable approximation
    for English text with OpenAI's tokenizers. This is intentionally
    conservative to avoid exceeding budgets.

    Args:
        text: The text to estimate tokens for.

    Returns:
        Estimated token count.
    """
    if not text:
        return 0
    return max(1, len(text) // CHARS_PER_TOKEN)


def truncate_to_tokens(text: str, max_tokens: int) -> str:
    """Truncate text to approximately fit within token budget.

    Attempts to truncate at section boundaries (double newlines) when
    possible to maintain coherent output.

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

    # Try to truncate at section boundaries
    sections = text.split("\n\n")
    result_parts = []
    current_length = 0

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

    # Fallback: hard truncate if no section fits
    return text[:max_chars]


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

    # First, collect files from source directories
    for source_dir in SOURCE_DIRS:
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


def _is_test_path(path: str) -> bool:
    """Check if a path looks like a test file.

    Matches common test patterns across Python, JavaScript, and TypeScript.
    Only matches actual test files, not directories that happen to contain 'test'.
    """
    import os
    filename = os.path.basename(path)

    # Directory patterns (actual test directories, not temp dirs)
    if "/test/" in path or "/tests/" in path or "/__tests__/" in path:
        return True
    # Handle paths that start with test/ (no leading slash)
    if path.startswith("test/") or path.startswith("tests/"):
        return True

    # File name patterns: test_*.py, test_*.js, etc.
    if filename.startswith("test_"):
        return True

    # Suffix patterns (.test.ts, .spec.js, _test.py, etc.)
    for ext in (".py", ".js", ".ts", ".jsx", ".tsx"):
        if filename.endswith(f".test{ext}") or filename.endswith(f".spec{ext}"):
            return True
        if filename.endswith(f"_test{ext}"):
            return True
    return False


# Tier weights for supply chain ranking (first-party prioritized)
# Tier 4 (derived) gets 0 weight since those files shouldn't be analyzed
_TIER_WEIGHTS = {1: 2.0, 2: 1.5, 3: 1.0, 4: 0.0}


def _compute_centrality(
    symbols: list[Symbol],
    edges: list,
) -> dict[str, float]:
    """Compute symbol importance using in-degree centrality.

    Symbols called by many others are considered more important.
    This uses in-degree as a simple proxy for "authority" in the codebase.
    """
    symbol_ids = {s.id for s in symbols}
    in_degree: dict[str, int] = {sid: 0 for sid in symbol_ids}

    for edge in edges:
        # Edge uses 'dst' for target in IR
        target = getattr(edge, 'dst', None)
        if target and target in in_degree:
            in_degree[target] += 1

    # Normalize to 0-1 range
    max_degree = max(in_degree.values()) if in_degree else 1
    if max_degree == 0:
        max_degree = 1

    return {sid: count / max_degree for sid, count in in_degree.items()}


def _apply_tier_weights(
    centrality: dict[str, float],
    symbols: list[Symbol],
) -> dict[str, float]:
    """Apply tier-based weighting to centrality scores.

    First-party symbols (tier 1) get a 2x boost, internal deps (tier 2) get 1.5x,
    external deps (tier 3) get 1x, and derived (tier 4) gets 0x.

    This ensures first-party code ranks higher than bundled dependencies
    even when dependencies have higher raw centrality.
    """
    symbol_tiers = {s.id: s.supply_chain_tier for s in symbols}
    weighted = {}
    for sid, score in centrality.items():
        tier = symbol_tiers.get(sid, 1)
        weight = _TIER_WEIGHTS.get(tier, 1.0)
        weighted[sid] = score * weight
    return weighted


def _format_symbols(
    symbols: list[Symbol],
    edges: list,
    repo_root: Path,
    max_symbols: int = 100,
) -> str:
    """Format key symbols (functions, classes) as a Markdown section.

    Uses graph centrality to prioritize the most-called symbols first.
    Test files are excluded from both symbols and edge sources to avoid
    inflating centrality of production code called by tests.
    """
    if not symbols:
        return ""

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
    # This prevents test code from inflating centrality of production code
    production_edges = [
        e for e in edges
        if not _is_test_path(symbol_path_by_id.get(getattr(e, 'src', ''), ''))
    ]

    if not key_symbols:
        return ""

    # Compute centrality scores using only production edges
    raw_centrality = _compute_centrality(key_symbols, production_edges)

    # Apply tier-based weighting (first-party symbols boosted)
    centrality = _apply_tier_weights(raw_centrality, key_symbols)

    # Sort by weighted centrality (most called first), then by name
    key_symbols.sort(key=lambda s: (-centrality.get(s.id, 0), s.name))

    # Group by file, preserving centrality order within files
    by_file: dict[str, list[Symbol]] = {}
    file_max_centrality: dict[str, float] = {}
    for s in key_symbols:
        rel_path = s.path
        if rel_path.startswith(str(repo_root)):
            rel_path = rel_path[len(str(repo_root)) + 1:]
        by_file.setdefault(rel_path, []).append(s)
        # Track max centrality per file for file ordering
        score = centrality.get(s.id, 0)
        if rel_path not in file_max_centrality or score > file_max_centrality[rel_path]:
            file_max_centrality[rel_path] = score

    # Sort files by their max centrality (most important files first)
    sorted_files = sorted(by_file.keys(), key=lambda f: -file_max_centrality.get(f, 0))

    lines = ["## Key Symbols", ""]

    count = 0
    for file_path in sorted_files:
        if count >= max_symbols:
            break

        file_symbols = by_file[file_path]

        lines.append(f"### `{file_path}`")

        for sym in file_symbols:
            if count >= max_symbols:
                break
            kind_label = sym.kind
            score = centrality.get(sym.id, 0)
            # Add importance indicator for highly-called symbols
            if score >= 0.5:
                lines.append(f"- `{sym.name}` ({kind_label}) ★")
            else:
                lines.append(f"- `{sym.name}` ({kind_label})")
            count += 1

        lines.append("")  # Blank line between files

    remaining = len(key_symbols) - count
    if remaining > 0:
        lines.append(f"*... and {remaining} more symbols*")

    return "\n".join(lines)


def generate_sketch(
    repo_root: Path,
    max_tokens: Optional[int] = None,
    exclude_tests: bool = False,
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

    # Estimate tokens per item (~20 chars average + formatting)
    tokens_per_item = 6

    # Section 4: Source files (if we have budget >= 50 tokens remaining)
    if remaining_tokens > 50 and source_files:
        # Use up to half of remaining budget for source files at small budgets
        # Scale down the fraction as budget grows (files are less important)
        if remaining_tokens < 300:
            budget_for_files = (remaining_tokens * 2) // 3  # 66% at small budgets
        else:
            budget_for_files = remaining_tokens // 3  # 33% at larger budgets
        max_source_files = max(5, budget_for_files // tokens_per_item)

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
    if remaining_tokens > 50 and symbols:
        entrypoints = detect_entrypoints(symbols, edges)
        if entrypoints:
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
            symbols, edges, repo_root, max_symbols=max_symbols
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
