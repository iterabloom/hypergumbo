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

from enum import Enum
from pathlib import Path
from typing import List, Optional

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


class ConfigExtractionMode(Enum):
    """Mode for extracting config file content.

    - HEURISTIC: Extract known fields using pattern matching (fast, no model)
    - EMBEDDING: Use semantic similarity to prototype questions (requires model)
    - HYBRID: Extract known fields first, then use embeddings for remaining budget
    """

    HEURISTIC = "heuristic"
    EMBEDDING = "embedding"
    HYBRID = "hybrid"


# Prototype questions for semantic similarity in embedding mode.
# This centroid represents the space of common metadata questions.
# The broader this list, the better the embedding mode will work.
METADATA_QUESTIONS = [
    # License and legal
    "What license does this project use?",
    "Is this project open source?",
    "What are the licensing terms?",
    "Is this MIT licensed?",
    "Is this GPL licensed?",
    "Can I use this commercially?",

    # Version and release info
    "What version is this project?",
    "What is the current version number?",
    "When was the last release?",
    "What version of Node.js does this require?",
    "What Python version is required?",
    "What is the minimum supported version?",

    # Database and storage
    "What database does this project use?",
    "Does this use PostgreSQL?",
    "Does this use MySQL?",
    "Does this use MongoDB?",
    "Does this use Redis?",
    "Does this use SQLite?",
    "What ORM does this use?",
    "How does this store data?",

    # Web frameworks and HTTP
    "What web framework does this use?",
    "Is this built with Express?",
    "Is this built with FastAPI?",
    "Is this built with Django?",
    "Is this built with Flask?",
    "Is this built with Rails?",
    "Is this built with Spring?",
    "Is this a REST API?",
    "Does this use GraphQL?",

    # Frontend frameworks
    "What frontend framework does this use?",
    "Is this built with React?",
    "Is this built with Vue?",
    "Is this built with Angular?",
    "Is this built with Svelte?",
    "Does this use TypeScript?",
    "What CSS framework does this use?",

    # Testing
    "What testing framework does this use?",
    "Does this use Jest?",
    "Does this use pytest?",
    "Does this use JUnit?",
    "How do I run the tests?",
    "What is the test coverage?",

    # Build and tooling
    "What build system does this use?",
    "Does this use webpack?",
    "Does this use Vite?",
    "Does this use Maven?",
    "Does this use Gradle?",
    "Does this use Cargo?",
    "How do I build this project?",

    # Package management
    "What package manager does this use?",
    "Does this use npm or yarn?",
    "Does this use pnpm?",
    "Does this use pip?",
    "What are the main dependencies?",
    "What are the dev dependencies?",

    # Language and runtime
    "What programming language is this?",
    "What runtime does this require?",
    "Is this a TypeScript project?",
    "Is this a Python project?",
    "Is this a Go project?",
    "Is this a Rust project?",
    "Is this a Java project?",

    # Project identity
    "What is this project called?",
    "What is the project name?",
    "Who maintains this project?",
    "What organization owns this?",
    "Who are the contributors?",

    # Deployment and infrastructure
    "How do I deploy this?",
    "Does this use Docker?",
    "Does this use Kubernetes?",
    "What cloud platform does this target?",
    "Is this serverless?",

    # API and protocols
    "What API does this expose?",
    "Does this use WebSockets?",
    "Does this use gRPC?",
    "What ports does this use?",

    # Miscellaneous metadata
    "What is the project description?",
    "What problem does this solve?",
    "Is this a library or application?",
    "Is this a CLI tool?",
    "Is this production ready?",
]


# Approximate characters per token (conservative estimate for English text)
CHARS_PER_TOKEN = 4

# Config files to extract project metadata from
CONFIG_FILES = [
    "package.json", "go.mod", "pom.xml", "Cargo.toml", "pyproject.toml",
    "composer.json", "Gemfile", "build.gradle", "requirements.txt",
]

# Subdirectories to check for config files (monorepo support)
CONFIG_SUBDIRS = ["", "server", "client", "backend", "frontend", "src", "app", "api"]

# Key dependencies to highlight (db drivers, frameworks, etc.)
INTERESTING_DEPS = frozenset({
    # Databases
    "pg", "postgres", "postgresql", "mysql", "mysql2", "mongodb", "mongoose",
    "redis", "sqlite", "sqlite3", "prisma", "typeorm", "sequelize", "knex",
    # Frameworks
    "express", "fastify", "koa", "hapi", "nestjs", "next", "nuxt", "gatsby",
    "react", "vue", "angular", "svelte", "django", "flask", "fastapi",
    "spring", "rails", "laravel", "gin", "echo", "fiber",
    # Testing
    "jest", "vitest", "mocha", "pytest", "junit", "rspec",
    # Build/tooling
    "typescript", "webpack", "vite", "esbuild", "rollup", "babel",
})

# License file names to check
LICENSE_FILES = ["LICENSE", "LICENSE.md", "LICENSE.txt", "COPYING"]


def _extract_config_heuristic(repo_root: Path) -> list[str]:
    """Extract config metadata using heuristic pattern matching.

    This is the fast path that extracts known fields from common config files
    without requiring any ML models.

    Args:
        repo_root: Path to repository root.

    Returns:
        List of extracted metadata lines.
    """
    import json
    import re

    lines: list[str] = []

    def _extract_package_json(path: Path, prefix: str) -> list[str]:
        """Extract key fields from package.json."""
        result = []
        try:
            data = json.loads(path.read_text(encoding="utf-8", errors="replace"))
            info = []

            # Core metadata
            for key in ["name", "version", "license"]:
                if key in data:
                    info.append(f"{key}: {data[key]}")

            # Interesting dependencies with versions
            for dep_type in ["dependencies", "devDependencies"]:
                if dep_type in data and isinstance(data[dep_type], dict):
                    deps = data[dep_type]
                    for dep_name in INTERESTING_DEPS:
                        if dep_name in deps:
                            info.append(f"{dep_name}: {deps[dep_name]}")

            if info:
                result.append(f"{prefix}package.json: {'; '.join(info)}")
        except (json.JSONDecodeError, OSError):
            pass
        return result

    def _extract_go_mod(path: Path, prefix: str) -> list[str]:
        """Extract module name and key dependencies from go.mod."""
        result = []
        try:
            content = path.read_text(encoding="utf-8", errors="replace")
            extracted = []

            # Module name
            module_match = re.search(r"^module\s+(\S+)", content, re.MULTILINE)
            if module_match:
                extracted.append(f"module: {module_match.group(1)}")

            # Go version
            go_match = re.search(r"^go\s+([\d.]+)", content, re.MULTILINE)
            if go_match:
                extracted.append(f"go: {go_match.group(1)}")

            # Key require statements (look for database drivers, web frameworks)
            interesting_go = {
                "gorilla/websocket", "gorilla/mux", "gin-gonic/gin",
                "labstack/echo", "gofiber/fiber", "lib/pq", "go-sql-driver/mysql",
                "jackc/pgx", "go-redis/redis", "mongodb/mongo-go-driver",
            }
            for dep in interesting_go:
                if dep in content:
                    extracted.append(dep.split("/")[-1])

            if extracted:
                result.append(f"{prefix}go.mod: {'; '.join(extracted)}")
        except OSError:  # pragma: no cover
            pass  # pragma: no cover
        return result

    def _extract_pom_xml(path: Path, prefix: str) -> list[str]:
        """Extract Maven coordinates from pom.xml."""
        result = []
        try:
            content = path.read_text(encoding="utf-8", errors="replace")[:4000]
            extracted = []

            for tag in ["groupId", "artifactId", "version", "packaging"]:
                match = re.search(f"<{tag}>([^<]+)</{tag}>", content)
                if match:
                    extracted.append(f"{tag}: {match.group(1)}")

            if extracted:
                result.append(f"{prefix}pom.xml: {'; '.join(extracted)}")
        except OSError:  # pragma: no cover
            pass  # pragma: no cover
        return result

    def _extract_cargo_toml(path: Path, prefix: str) -> list[str]:
        """Extract Rust package info from Cargo.toml."""
        result = []
        try:
            content = path.read_text(encoding="utf-8", errors="replace")
            extracted = []

            # Parse [package] section fields
            for field in ["name", "version", "license"]:
                match = re.search(rf'^{field}\s*=\s*"([^"]+)"', content, re.MULTILINE)
                if match:
                    extracted.append(f"{field}: {match.group(1)}")

            if extracted:
                result.append(f"{prefix}Cargo.toml: {'; '.join(extracted)}")
        except OSError:  # pragma: no cover
            pass  # pragma: no cover
        return result

    def _extract_pyproject_toml(path: Path, prefix: str) -> list[str]:
        """Extract Python project info from pyproject.toml."""
        result = []
        try:
            content = path.read_text(encoding="utf-8", errors="replace")
            extracted = []

            for field in ["name", "version", "license"]:
                # Handle both quoted and unquoted values
                match = re.search(rf'^{field}\s*=\s*["\']?([^"\'#\n]+)', content, re.MULTILINE)
                if match:
                    extracted.append(f"{field}: {match.group(1).strip()}")

            if extracted:
                result.append(f"{prefix}pyproject.toml: {'; '.join(extracted)}")
        except OSError:  # pragma: no cover
            pass  # pragma: no cover
        return result

    # Scan config files in root and common subdirectories
    for config_name in CONFIG_FILES:
        for subdir in CONFIG_SUBDIRS:
            config_path = repo_root / subdir / config_name if subdir else repo_root / config_name
            if not config_path.exists():
                continue

            prefix = f"{subdir}/" if subdir else ""

            if config_name == "package.json":
                lines.extend(_extract_package_json(config_path, prefix))
            elif config_name == "go.mod":
                lines.extend(_extract_go_mod(config_path, prefix))
            elif config_name == "pom.xml":
                lines.extend(_extract_pom_xml(config_path, prefix))
            elif config_name == "Cargo.toml":
                lines.extend(_extract_cargo_toml(config_path, prefix))
            elif config_name == "pyproject.toml":
                lines.extend(_extract_pyproject_toml(config_path, prefix))

    # Detect license type from LICENSE files
    for license_name in LICENSE_FILES:
        license_path = repo_root / license_name
        if license_path.exists():
            try:
                # Read just enough to detect license type
                content = license_path.read_text(encoding="utf-8", errors="replace")[:500]
                license_type = None

                # Check for common license types (order matters: AGPL before GPL)
                content_upper = content.upper()
                if "AGPL" in content_upper or "AFFERO" in content_upper:
                    license_type = "AGPL"
                elif "GPL" in content_upper and "LESSER" in content_upper:
                    license_type = "LGPL"
                elif "GPL" in content_upper:
                    license_type = "GPL"
                elif "MIT LICENSE" in content_upper or "PERMISSION IS HEREBY GRANTED" in content_upper:
                    license_type = "MIT"
                elif "APACHE LICENSE" in content_upper:
                    license_type = "Apache"
                elif "BSD" in content_upper:
                    license_type = "BSD"
                elif "MOZILLA PUBLIC LICENSE" in content_upper:
                    license_type = "MPL"
                elif "ISC LICENSE" in content_upper:
                    license_type = "ISC"
                elif "UNLICENSE" in content_upper:
                    license_type = "Unlicense"

                if license_type:
                    lines.append(f"LICENSE: {license_type}")
                break  # Only process first found license file
            except OSError:  # pragma: no cover
                pass  # pragma: no cover

    return lines


def _collect_config_content(repo_root: Path) -> list[tuple[str, str]]:
    """Collect all config file content as (filename, content) pairs.

    Used by embedding mode to have raw content for semantic selection.

    Args:
        repo_root: Path to repository root.

    Returns:
        List of (prefixed_filename, content) tuples.
    """
    config_content: list[tuple[str, str]] = []

    for config_name in CONFIG_FILES:
        for subdir in CONFIG_SUBDIRS:
            config_path = repo_root / subdir / config_name if subdir else repo_root / config_name
            if not config_path.exists():
                continue

            try:
                content = config_path.read_text(encoding="utf-8", errors="replace")
                prefix = f"{subdir}/" if subdir else ""
                config_content.append((f"{prefix}{config_name}", content))
            except OSError:  # pragma: no cover
                pass  # pragma: no cover

    # Also include LICENSE file content
    for license_name in LICENSE_FILES:
        license_path = repo_root / license_name
        if license_path.exists():
            try:
                content = license_path.read_text(encoding="utf-8", errors="replace")[:2000]
                config_content.append((license_name, content))
                break  # Only first license file
            except OSError:  # pragma: no cover
                pass  # pragma: no cover

    return config_content


def _extract_config_embedding(
    repo_root: Path,
    max_lines: int = 30,
) -> list[str]:
    """Extract config metadata using embedding-based semantic selection.

    Uses sentence-transformers with UnixCoder to compute similarity between
    config file lines and a centroid of METADATA_QUESTIONS. Lines most
    similar to the question centroid are selected.

    Args:
        repo_root: Path to repository root.
        max_lines: Maximum lines to extract.

    Returns:
        List of extracted metadata lines, ordered by semantic relevance.
    """
    try:
        from sentence_transformers import SentenceTransformer
        import numpy as np
    except ImportError:  # pragma: no cover
        # Fall back to heuristic if sentence-transformers not available
        return _extract_config_heuristic(repo_root)[:max_lines]

    # Collect all config content
    config_content = _collect_config_content(repo_root)
    if not config_content:
        return []  # pragma: no cover - defensive, caller checks for config files

    # Split content into lines with source info, preserving line indices for context
    # Structure: (source, line_idx, line_text, all_file_lines)
    all_lines: list[tuple[str, int, str, list[str]]] = []
    file_lines_cache: dict[str, list[str]] = {}

    for source, content in config_content:
        file_lines = [ln.strip() for ln in content.split("\n")]
        file_lines_cache[source] = file_lines
        for idx, line in enumerate(file_lines):
            if line and len(line) > 3:  # Skip empty/trivial lines
                all_lines.append((source, idx, line, file_lines))

    if not all_lines:
        return []  # pragma: no cover - defensive, requires empty config files

    # Load embedding model
    model = SentenceTransformer("microsoft/unixcoder-base")

    # Compute centroid of METADATA_QUESTIONS
    question_embeddings = model.encode(METADATA_QUESTIONS, convert_to_numpy=True)
    centroid = np.mean(question_embeddings, axis=0)
    centroid_norm = centroid / (np.linalg.norm(centroid) + 1e-8)

    # Embed all config lines
    line_texts = [line for _, _, line, _ in all_lines]
    line_embeddings = model.encode(line_texts, convert_to_numpy=True)

    # Compute similarities to centroid
    line_norms = np.linalg.norm(line_embeddings, axis=1, keepdims=True)
    normalized_embeddings = line_embeddings / (line_norms + 1e-8)
    similarities = np.dot(normalized_embeddings, centroid_norm)

    # Select top-k lines by similarity
    top_indices = np.argsort(-similarities)[:max_lines]

    # Build result with context (1 line before, selected line, 1 line after)
    result_lines: list[str] = []
    seen_sources: set[str] = set()
    seen_contexts: set[tuple[str, int]] = set()  # (source, line_idx) already included

    for sel_idx in top_indices:
        source, line_idx, line, file_lines = all_lines[sel_idx]

        # Add source header if new file
        if source not in seen_sources:
            if result_lines:  # Add separator between files
                result_lines.append("")
            result_lines.append(f"[{source}]")
            seen_sources.add(source)

        # Gather context window: 1 line before, selected line, 1 line after
        context_start = max(0, line_idx - 1)
        context_end = min(len(file_lines), line_idx + 2)

        for ctx_idx in range(context_start, context_end):
            ctx_key = (source, ctx_idx)
            if ctx_key in seen_contexts:
                continue  # Already included this line
            seen_contexts.add(ctx_key)

            ctx_line = file_lines[ctx_idx]
            if not ctx_line:
                continue

            # Mark the selected line vs context
            if ctx_idx == line_idx:
                result_lines.append(f"  > {ctx_line}")  # Selected line
            else:
                result_lines.append(f"    {ctx_line}")  # Context line

    return result_lines


def _extract_config_hybrid(
    repo_root: Path,
    max_chars: int = 1500,
) -> list[str]:
    """Extract config using hybrid approach: heuristics first, then embeddings.

    This combines the best of both approaches:
    1. First, extract known fields using fast heuristic patterns
    2. Then, use embedding-based selection to fill remaining budget
       with semantically relevant content not captured by heuristics

    Args:
        repo_root: Path to repository root.
        max_chars: Maximum characters for output.

    Returns:
        List of extracted metadata lines.
    """
    # Step 1: Get heuristic extraction (fast, reliable for known fields)
    heuristic_lines = _extract_config_heuristic(repo_root)
    heuristic_text = "\n".join(heuristic_lines)

    # If heuristics already fill the budget, we're done
    if len(heuristic_text) >= max_chars * 0.8:
        return heuristic_lines  # pragma: no cover - edge case, very large configs

    # Step 2: Compute remaining budget for embedding-based extraction
    remaining_chars = max_chars - len(heuristic_text) - 50  # Buffer
    if remaining_chars < 100:
        return heuristic_lines  # pragma: no cover - edge case, budget nearly filled

    # Estimate lines we can add
    remaining_lines = max(5, remaining_chars // 50)

    # Step 3: Get embedding-based extraction
    try:
        embedding_lines = _extract_config_embedding(repo_root, max_lines=remaining_lines)
    except Exception:  # pragma: no cover
        # If embedding fails, just return heuristic results
        return heuristic_lines

    # Step 4: Merge, avoiding duplicates
    # Extract key terms from heuristic lines to avoid redundancy
    heuristic_terms = set()
    for line in heuristic_lines:
        # Extract significant words
        for word in line.lower().split():
            if len(word) > 3:
                heuristic_terms.add(word.strip(":;,"))

    # Add embedding lines that provide new information
    combined = heuristic_lines.copy()
    if embedding_lines:
        combined.append("")  # Separator
        combined.append("# Additional context (semantic)")
        for line in embedding_lines:
            # Skip if line content is already covered by heuristics
            line_lower = line.lower()
            is_redundant = sum(1 for term in heuristic_terms if term in line_lower) > 2
            if not is_redundant:
                combined.append(line)

    return combined


def _extract_config_info(
    repo_root: Path,
    max_chars: int = 1500,
    mode: ConfigExtractionMode = ConfigExtractionMode.HEURISTIC,
) -> str:
    """Extract key metadata from config files via extractive summarization.

    Supports three extraction modes:
    - HEURISTIC: Fast pattern-based extraction of known fields (default)
    - EMBEDDING: Semantic selection using UnixCoder + question centroid
    - HYBRID: Heuristics first, then embeddings for remaining budget

    For long config files (e.g., package.json with hundreds of deps), only
    the relevant fields/lines are extracted, keeping output bounded.

    Args:
        repo_root: Path to repository root.
        max_chars: Maximum characters for config section output.
        mode: Extraction mode (heuristic, embedding, or hybrid).

    Returns:
        Extracted config metadata as a formatted string, or empty string
        if no config files found.
    """
    # Select extraction strategy based on mode
    if mode == ConfigExtractionMode.EMBEDDING:
        max_lines = max(10, max_chars // 50)
        lines = _extract_config_embedding(repo_root, max_lines=max_lines)
    elif mode == ConfigExtractionMode.HYBRID:
        lines = _extract_config_hybrid(repo_root, max_chars=max_chars)
    else:  # HEURISTIC (default)
        lines = _extract_config_heuristic(repo_root)

    # Truncate output to max_chars
    result = "\n".join(lines[:30])  # Cap at 30 lines
    if len(result) > max_chars:  # pragma: no cover - defensive truncation
        result = result[:max_chars]  # pragma: no cover
        # Try to truncate at a line boundary
        last_newline = result.rfind("\n")  # pragma: no cover
        if last_newline > max_chars // 2:  # pragma: no cover
            result = result[:last_newline]  # pragma: no cover

    return result


def _format_config_section(config_info: str) -> str:
    """Format config info as a Markdown section.

    Args:
        config_info: Extracted config information string.

    Returns:
        Markdown-formatted configuration section.
    """
    if not config_info:
        return ""

    lines = ["## Configuration", ""]
    lines.append("```")
    lines.append(config_info)
    lines.append("```")

    return "\n".join(lines)


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


def _format_structure(
    repo_root: Path, extra_excludes: Optional[List[str]] = None
) -> str:
    """Format top-level directory structure.

    Filters out directories that match DEFAULT_EXCLUDES patterns
    (e.g., node_modules, __pycache__, .git) to show only meaningful
    project structure.
    """
    from fnmatch import fnmatch

    lines = ["## Structure", ""]

    # Combine default and extra excludes
    excludes = list(DEFAULT_EXCLUDES)
    if extra_excludes:
        excludes.extend(extra_excludes)

    # Get top-level directories, filtering out excluded ones
    dirs = []
    for d in repo_root.iterdir():
        if not d.is_dir():
            continue
        # Check if directory matches any exclude pattern
        excluded = any(fnmatch(d.name, pattern) for pattern in excludes)
        if not excluded:
            dirs.append(d.name)

    dirs = sorted(dirs)

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


def _extract_readme_description(
    repo_root: Path, max_chars: int = 200
) -> Optional[str]:
    """Extract a description from the project README file.

    Looks for README.md, README.rst, README.txt, or README (in that order)
    and extracts the first descriptive paragraph after the title.

    Args:
        repo_root: Path to the repository root.
        max_chars: Maximum characters to extract (default 200).

    Returns:
        Extracted description string, or None if no README found.
    """
    import re

    # Try different README file names in priority order
    readme_names = ["README.md", "README.rst", "README.txt", "README"]
    readme_path = None
    for name in readme_names:
        candidate = repo_root / name
        if candidate.is_file():
            readme_path = candidate
            break

    if readme_path is None:
        return None

    try:
        content = readme_path.read_text(encoding="utf-8", errors="replace")
    except OSError:  # pragma: no cover
        return None

    # Find the markdown title and extract description
    lines = content.split("\n")
    start_idx = 0
    title_subtitle = None

    # Find the first markdown H1 title (# ...)
    for i, line in enumerate(lines):
        if line.startswith("# "):
            # Check if title has a subtitle (e.g., "# Project: Description here")
            title_text = line[2:].strip()
            if ":" in title_text:
                parts = title_text.split(":", 1)
                if len(parts[1].strip()) > 10:  # Meaningful subtitle
                    title_subtitle = parts[1].strip()
            start_idx = i + 1
            break
        # Skip lines before title that are badges/images/comments
        stripped = line.strip()
        if stripped.startswith("![") or stripped.startswith("<!--"):
            continue
        if stripped.startswith("<"):
            continue
        # If we hit a non-skip line before finding title, treat as RST format
        if stripped and not stripped.startswith("#"):
            # RST title: text followed by === or --- underline
            if i + 1 < len(lines) and re.match(r"^[=\-~^]+$", lines[i + 1].strip()):
                start_idx = i + 2
                break

    # Skip any empty lines after title
    while start_idx < len(lines) and not lines[start_idx].strip():
        start_idx += 1

    # Find the first non-empty paragraph (stop at next header or empty line)
    # Skip common non-description content: badges, images, HTML comments
    paragraph_lines = []
    for line in lines[start_idx:]:
        stripped = line.strip()
        # Stop at headers (markdown ## or RST underlines)
        if line.startswith("#") or re.match(r"^[=\-~^]+$", stripped):
            break
        # Stop at empty line (end of paragraph)
        if not stripped and paragraph_lines:
            break
        # Skip markdown images and badges
        if stripped.startswith("![") or stripped.startswith("[!["):
            continue
        # Skip HTML comments
        if stripped.startswith("<!--"):
            continue
        # Skip HTML tags (picture, source, img, etc.)
        if stripped.startswith("<") and not stripped.startswith("<http"):
            continue
        # Skip lines that are just links (often badge URLs)
        if re.match(r"^\[.*\]\(https?://.*\)$", stripped):
            continue
        if stripped:
            paragraph_lines.append(stripped)

    if not paragraph_lines:
        # Fall back to title subtitle if available
        if title_subtitle:
            return title_subtitle
        return None

    description = " ".join(paragraph_lines)

    # Truncate if too long, trying to break at word boundary
    if len(description) > max_chars:
        # Find last space before max_chars
        truncate_at = description.rfind(" ", 0, max_chars)
        if truncate_at > max_chars // 2:
            description = description[:truncate_at] + "…"
        else:
            description = description[: max_chars - 1] + "…"

    return description


def _format_function_signature(node, max_len: int = 60) -> str:
    """Format a function signature from AST node.

    Args:
        node: AST FunctionDef or AsyncFunctionDef node.
        max_len: Maximum length of signature (default 60).

    Returns:
        Formatted signature string like "(x: int, y: str) -> bool".
    """
    # Format arguments
    args = node.args
    all_args = []

    # Positional-only args (before /)
    for arg in args.posonlyargs:
        all_args.append(_format_arg(arg))

    # Regular args
    for i, arg in enumerate(args.args):
        arg_str = _format_arg(arg)
        # Check for default value
        num_defaults = len(args.defaults)
        num_args = len(args.args)
        default_idx = i - (num_args - num_defaults)
        if default_idx >= 0 and default_idx < num_defaults:
            arg_str += "=…"
        all_args.append(arg_str)

    # *args
    if args.vararg:
        all_args.append(f"*{args.vararg.arg}")

    # Keyword-only args
    for i, arg in enumerate(args.kwonlyargs):
        arg_str = _format_arg(arg)
        if i < len(args.kw_defaults) and args.kw_defaults[i] is not None:
            arg_str += "=…"
        all_args.append(arg_str)

    # **kwargs
    if args.kwarg:
        all_args.append(f"**{args.kwarg.arg}")

    sig = "(" + ", ".join(all_args) + ")"

    # Add return type annotation if present
    if node.returns:
        ret_type = _format_annotation(node.returns)
        if ret_type:
            sig += f" -> {ret_type}"

    # Truncate if too long
    if len(sig) > max_len:
        sig = sig[:max_len - 1] + "…"

    return sig


def _format_arg(arg) -> str:
    """Format a single function argument."""
    result = arg.arg
    if arg.annotation:
        ann = _format_annotation(arg.annotation)
        if ann:
            result += f": {ann}"
    return result


def _format_annotation(node) -> str:
    """Format a type annotation node to a readable string."""
    import ast

    if isinstance(node, ast.Name):
        return node.id
    elif isinstance(node, ast.Constant):
        return repr(node.value)
    elif isinstance(node, ast.Subscript):
        # e.g., List[int], Dict[str, int]
        base = _format_annotation(node.value)
        slice_val = _format_annotation(node.slice)
        return f"{base}[{slice_val}]"
    elif isinstance(node, ast.Tuple):
        # e.g., (int, str) for Dict keys
        elts = [_format_annotation(e) for e in node.elts]
        return ", ".join(elts)
    elif isinstance(node, ast.BinOp) and isinstance(node.op, ast.BitOr):
        # Union types: X | Y
        left = _format_annotation(node.left)
        right = _format_annotation(node.right)
        return f"{left} | {right}"
    elif isinstance(node, ast.Attribute):
        # e.g., typing.Optional
        value = _format_annotation(node.value)
        return f"{value}.{node.attr}"
    else:
        return ""


def _extract_python_docstrings(
    repo_root: Path, symbols: list[Symbol], max_len: int = 80
) -> dict[str, str]:
    """Extract docstrings for Python symbols.

    Reads Python files and extracts the first line of docstrings for
    functions and classes. Returns a dict mapping symbol IDs to docstring
    summaries (truncated to max_len).

    Args:
        repo_root: Repository root path.
        symbols: List of symbols to extract docstrings for.
        max_len: Maximum length of docstring summary (default 80).

    Returns:
        Dict mapping symbol ID to first-line docstring summary.
    """
    import ast

    docstrings: dict[str, str] = {}

    # Group symbols by file for efficient reading
    symbols_by_file: dict[str, list[Symbol]] = {}
    for sym in symbols:
        if sym.language == "python" and sym.kind in ("function", "class", "method"):
            symbols_by_file.setdefault(sym.path, []).append(sym)

    for file_path, file_symbols in symbols_by_file.items():
        try:
            full_path = repo_root / file_path if not Path(file_path).is_absolute() else Path(file_path)
            if not full_path.exists():
                continue
            source = full_path.read_text(encoding="utf-8", errors="replace")
            tree = ast.parse(source)
        except (SyntaxError, OSError):
            continue

        # Build a map of (start_line, name) -> docstring
        node_docstrings: dict[tuple[int, str], str] = {}

        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                docstring = ast.get_docstring(node)
                if docstring:
                    # Take first line only
                    first_line = docstring.split("\n")[0].strip()
                    if len(first_line) > max_len:
                        first_line = first_line[:max_len - 1] + "…"
                    node_docstrings[(node.lineno, node.name)] = first_line

        # Match symbols to docstrings
        for sym in file_symbols:
            key = (sym.span.start_line, sym.name)
            if key in node_docstrings:
                docstrings[sym.id] = node_docstrings[key]

    return docstrings


def _extract_python_signatures(
    repo_root: Path, symbols: list[Symbol], max_len: int = 60
) -> dict[str, str]:
    """Extract function signatures for Python symbols.

    Reads Python files and extracts function signatures (parameters + return type)
    for functions and methods. Returns a dict mapping symbol IDs to signatures.

    Args:
        repo_root: Repository root path.
        symbols: List of symbols to extract signatures for.
        max_len: Maximum length of signature (default 60).

    Returns:
        Dict mapping symbol ID to signature string.
    """
    import ast

    signatures: dict[str, str] = {}

    # Group symbols by file for efficient reading
    symbols_by_file: dict[str, list[Symbol]] = {}
    for sym in symbols:
        if sym.language == "python" and sym.kind in ("function", "method"):
            symbols_by_file.setdefault(sym.path, []).append(sym)

    for file_path, file_symbols in symbols_by_file.items():
        try:
            full_path = repo_root / file_path if not Path(file_path).is_absolute() else Path(file_path)
            if not full_path.exists():
                continue
            source = full_path.read_text(encoding="utf-8", errors="replace")
            tree = ast.parse(source)
        except (SyntaxError, OSError):
            continue

        # Build a map of (start_line, name) -> signature
        node_signatures: dict[tuple[int, str], str] = {}

        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                sig = _format_function_signature(node, max_len)
                node_signatures[(node.lineno, node.name)] = sig

        # Match symbols to signatures
        for sym in file_symbols:
            # For methods, sym.name is "ClassName.method_name" but AST has just "method_name"
            match_name = sym.name.split(".")[-1] if "." in sym.name else sym.name
            key = (sym.span.start_line, match_name)
            if key in node_signatures:
                signatures[sym.id] = node_signatures[key]

    return signatures


# Common programming terms to exclude from domain vocabulary
_COMMON_TERMS = frozenset({
    # English stopwords
    "the", "and", "for", "not", "with", "this", "that", "from", "have", "has",
    "are", "was", "were", "been", "being", "will", "would", "could", "should",
    "all", "any", "each", "every", "both", "few", "more", "most", "other",
    "some", "such", "than", "too", "very", "when", "where", "which", "while",
    "who", "why", "how", "what", "then", "also", "just", "only",
    # Generic programming terms
    "get", "set", "add", "remove", "delete", "update", "create", "read", "write",
    "init", "start", "stop", "open", "close", "run", "call", "return", "value",
    "name", "type", "data", "item", "items", "list", "array", "object",
    "key", "keys", "val", "var", "vars", "arg", "args", "param", "params",
    "result", "results", "output", "input", "index", "idx", "len", "length",
    "count", "num", "number", "str", "string", "int", "integer", "float", "bool",
    "true", "false", "null", "none", "void", "use", "using", "used",
    "new", "old", "first", "last", "next", "prev", "current", "default",
    "error", "errors", "log", "console", "print", "debug", "info", "warn",
    "text", "msg", "message", "callback", "handler", "listener", "event",
    "async", "await", "promise", "resolve", "reject", "load", "save", "fetch",
    "send", "receive", "process", "handle", "path", "file", "config", "option",
    "options", "state", "props", "ref", "self", "super", "base", "parent",
    "child", "node", "tree", "root", "body", "head", "main", "temp", "util",
    "helper", "wrapper", "manager", "service", "factory", "builder", "module",
    "component", "context", "scope", "global", "local", "instance", "static",
    "public", "private", "protected", "virtual", "abstract", "final", "const",
    # Testing-related terms
    "test", "tests", "expect", "mock", "stub", "spy", "fixture",
    "logger", "logging", "describe", "spec", "suite", "setup",
    "teardown", "before", "after", "given", "verify",
})

# Programming language keywords to exclude
_KEYWORDS = frozenset({
    "class", "function", "return", "import", "export", "const", "else", "elif",
    "while", "break", "continue", "finally", "catch", "throw", "extends",
    "implements", "interface", "static", "public", "private", "protected",
    "super", "switch", "case", "yield", "assert", "raise", "pass", "lambda",
    "struct", "enum", "impl", "match", "trait", "package", "include", "define",
    "ifdef", "ifndef", "endif", "extern", "typedef", "sizeof", "typeof",
})


def _extract_domain_vocabulary(
    repo_root: Path, profile: "RepoProfile", max_terms: int = 12
) -> list[str]:
    """Extract domain-specific vocabulary from source code.

    Analyzes identifiers in source files to find domain-specific terms.
    Filters out common programming terms and language keywords to highlight
    terms unique to this codebase's domain.

    Args:
        repo_root: Path to the repository root.
        profile: Repository profile with language info.
        max_terms: Maximum number of domain terms to return (default 12).

    Returns:
        List of domain-specific terms, ordered by frequency.
    """
    import re
    from collections import Counter

    word_counts: Counter[str] = Counter()

    # File extensions to analyze
    extensions = ["*.py", "*.js", "*.ts", "*.jsx", "*.tsx", "*.java", "*.c", "*.h",
                  "*.go", "*.rs", "*.rb", "*.php", "*.cpp", "*.cc", "*.hpp"]

    # Directories to exclude
    excludes = {"node_modules", "__pycache__", "dist", "build", ".venv", "vendor",
                ".git", "target", "coverage", "htmlcov", ".pytest_cache"}

    for ext in extensions:
        for f in repo_root.rglob(ext):
            # Skip excluded directories
            if any(excl in f.parts for excl in excludes):
                continue
            try:
                text = f.read_text(encoding="utf-8", errors="replace")
                # Extract identifiers
                for match in re.finditer(r'\b[a-zA-Z_][a-zA-Z0-9_]*\b', text):
                    word = match.group()
                    if len(word) <= 3:
                        continue
                    if word.lower() in _KEYWORDS:
                        continue
                    # Split compound words (camelCase, PascalCase, snake_case)
                    # First try to find camelCase/PascalCase parts
                    parts = re.findall(r'[A-Z]?[a-z]+|[A-Z]+(?=[A-Z]|$)', word)
                    if parts:
                        for p in parts:
                            p_lower = p.lower()
                            if len(p_lower) > 3 and p_lower not in _COMMON_TERMS:
                                word_counts[p_lower] += 1
                    # Also split by underscore for snake_case (including UPPER_CASE)
                    for part in word.split('_'):
                        p_lower = part.lower()
                        if len(p_lower) > 3 and p_lower not in _COMMON_TERMS:
                            word_counts[p_lower] += 1
            except OSError:
                continue

    # Return top terms by frequency
    return [word for word, _ in word_counts.most_common(max_terms)]


def _format_vocabulary(terms: list[str]) -> str:
    """Format domain vocabulary as a Markdown section.

    Args:
        terms: List of domain-specific terms.

    Returns:
        Markdown-formatted vocabulary section.
    """
    if not terms:
        return ""

    lines = ["## Domain Vocabulary", ""]
    lines.append(f"*Key terms: {', '.join(terms)}*")

    return "\n".join(lines)


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
    docstrings: dict[str, str] | None = None,
    signatures: dict[str, str] | None = None,
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
        docstrings: Optional dict mapping symbol IDs to docstring summaries.
        signatures: Optional dict mapping symbol IDs to function signatures.
    """
    if docstrings is None:
        docstrings = {}
    if signatures is None:
        signatures = {}
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
            star = " ★" if score >= star_threshold else ""
            docstring = docstrings.get(sym.id)
            signature = signatures.get(sym.id)
            # Build symbol display name (with signature for functions)
            if signature and sym.kind in ("function", "method"):
                display_name = f"{sym.name}{signature}"
            else:
                display_name = sym.name
            if docstring:
                lines.append(f"- `{display_name}` ({kind_label}){star} — {docstring}")
            else:
                lines.append(f"- `{display_name}` ({kind_label}){star}")
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
    extra_excludes: Optional[List[str]] = None,
    config_extraction_mode: ConfigExtractionMode = ConfigExtractionMode.HEURISTIC,
) -> str:
    """Generate a token-budgeted Markdown sketch of the repository.

    The sketch progressively includes content to fill the token budget:
    1. Header with language breakdown and LOC (always included)
    2. Directory structure
    3. Detected frameworks
    4. Configuration metadata (extracted from package.json, go.mod, etc.)
    5. Domain vocabulary
    6. Source files (for medium budgets)
    7. Entry points from static analysis (for larger budgets)
    8. Key symbols from static analysis (for large budgets)
    9. All files (for very large budgets)

    Args:
        repo_root: Path to the repository root.
        max_tokens: Target tokens for output. If None, returns minimal sketch.
        exclude_tests: If True, skip analyzing test files for faster performance.
        first_party_priority: If True (default), boost first-party symbols in
            ranking. Set False to use raw centrality scores.
        extra_excludes: Additional exclude patterns beyond DEFAULT_EXCLUDES.
            Useful for excluding project-specific files (e.g., "*.json", "vendor").
        config_extraction_mode: Mode for extracting config file metadata.
            - HEURISTIC (default): Fast pattern-based extraction
            - EMBEDDING: Semantic selection using UnixCoder + question centroid
            - HYBRID: Heuristics first, then embeddings for remaining budget

    Returns:
        Markdown-formatted sketch string.
    """
    repo_root = Path(repo_root).resolve()
    profile = detect_profile(repo_root, extra_excludes=extra_excludes)
    repo_name = _get_repo_name(repo_root)

    # Build base sections (always included)
    sections = []

    # Section 1: Header (always included, highest priority)
    # Include project description from README if available
    readme_desc = _extract_readme_description(repo_root)
    if readme_desc:
        header = (
            f"# {repo_name}\n\n"
            f"{readme_desc}\n\n"
            f"## Overview\n{_format_language_stats(profile)}"
        )
    else:
        header = f"# {repo_name}\n\n## Overview\n{_format_language_stats(profile)}"
    sections.append(header)

    # Section 2: Structure
    structure = _format_structure(repo_root, extra_excludes=extra_excludes)
    if structure:
        sections.append(structure)

    # Section 3: Frameworks
    frameworks = _format_frameworks(profile)
    if frameworks:
        sections.append(frameworks)

    # Section 3.5: Configuration (extracted metadata from config files)
    # This section is high value for answering project metadata questions
    # (e.g., "what version of TypeScript?", "what license?", "what database?")
    config_info = _extract_config_info(repo_root, mode=config_extraction_mode)
    config_section = _format_config_section(config_info)
    if config_section:
        sections.append(config_section)

    # Section 3.75: Domain Vocabulary (only for medium+ budgets)
    if max_tokens is None or max_tokens >= 500:
        vocab_terms = _extract_domain_vocabulary(repo_root, profile)
        vocabulary = _format_vocabulary(vocab_terms)
        if vocabulary:
            sections.append(vocabulary)

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

    # Estimate tokens per entry point or symbol item with docstring/signature
    # Typical line: "- `func(x: int) -> str` (function) — Description. — `path.py`"
    # is ~70-100 chars = ~18-25 tokens. Use conservative estimate.
    tokens_per_item = 20

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

        # Extract docstrings and signatures for Python symbols
        docstrings = _extract_python_docstrings(repo_root, symbols)
        signatures = _extract_python_signatures(repo_root, symbols)

        symbols_section = _format_symbols(
            symbols,
            edges,
            repo_root,
            max_symbols=max_symbols,
            first_party_priority=first_party_priority,
            entrypoint_files=entrypoint_files,  # B4: preserve entrypoint files
            docstrings=docstrings,
            signatures=signatures,
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
