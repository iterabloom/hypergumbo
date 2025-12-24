"""Token-budgeted Markdown sketch generation.

This module generates human/LLM-readable Markdown summaries of repositories,
optimized for pasting into LLM chat interfaces. Output is token-budgeted
to fit within context limits.

How It Works
------------
The sketch is generated in priority order:
1. Header: repo name, language breakdown, LOC estimate (always included)
2. Entry points: detected routes, CLI mains, etc.
3. Structure: top-level directory overview
4. Frameworks: detected build systems and dependencies

Token budgeting uses a simple heuristic (~4 chars per token) which is
accurate enough for approximate sizing. For precise counting, tiktoken
can be used as an optional dependency.

Why Priority-Based Truncation
-----------------------------
When the token budget is exceeded, lower-priority sections are removed
entirely rather than truncating mid-section. This ensures the output
remains coherent and useful even at small token budgets.
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

from .profile import detect_profile, RepoProfile


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


def generate_sketch(
    repo_root: Path,
    max_tokens: Optional[int] = None,
) -> str:
    """Generate a token-budgeted Markdown sketch of the repository.

    The sketch includes (in priority order):
    1. Header with language breakdown and LOC
    2. Directory structure
    3. Detected frameworks

    Args:
        repo_root: Path to the repository root.
        max_tokens: Maximum tokens for output. If None, no limit applied.

    Returns:
        Markdown-formatted sketch string.
    """
    repo_root = Path(repo_root).resolve()
    profile = detect_profile(repo_root)
    repo_name = _get_repo_name(repo_root)

    # Build sections in priority order
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

    # Combine sections
    full_sketch = "\n\n".join(sections)

    # Apply token budget if specified
    if max_tokens is not None:
        full_sketch = truncate_to_tokens(full_sketch, max_tokens)

    return full_sketch
