"""Embedding-based config extraction for sketch generation.

This module contains the optional sentence-transformers-based functionality
for semantic config file discovery and extraction. It's separated from
sketch.py to allow coverage to be measured independently when the heavy
dependencies (sentence-transformers, torch) aren't available.

The main entry points are:
- extract_config_embedding(): Semantic selection using UnixCoder embeddings
- extract_config_hybrid(): Heuristics + embeddings combined approach

These functions fall back gracefully when sentence-transformers isn't installed.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import numpy as np

# Probe patterns for embedding-based config extraction
# These are embedded and compared against config file content

ANSWER_PATTERNS = [
    # Project identity
    "project name declaration",
    "package name",
    "module name",
    "application name",

    # Versioning
    "version number",
    "semantic version",
    "edition or language version",
    "minimum required version",

    # Dependencies
    "dependency declaration",
    "package dependency",
    "library dependency",
    "dev dependency",
    "build dependency",
    "optional dependency",

    # Licensing
    "license identifier",
    "SPDX license expression",
    "open source license",

    # Build configuration
    "build system configuration",
    "build target",
    "compilation settings",
    "entry point",
    "main module",
    "script definition",
    "command definition",

    # Runtime configuration
    "environment variable",
    "configuration option",
    "feature flag",
    "runtime setting",

    # Repository and authorship
    "repository URL",
    "homepage URL",
    "author name",
    "maintainer",
    "contributors list",

    # Documentation
    "project description",
    "readme file",

    # Discovery
    "package keywords",
    "package categories",
    "package tags",

    # Exports and binaries
    "binary executable",
    "library exports",
    "public API",
]

# Open-ended questions for big-picture/architectural context
# NOTE: License questions removed - ANSWER_PATTERNS already captures compact
# license declarations (e.g., 'license = "MIT"') without matching verbose
# LICENSE file boilerplate.
BIG_PICTURE_QUESTIONS = [
    # Machine learning and AI
    "What ML framework does this use?",
    "Does this use PyTorch?",
    "Does this use TensorFlow?",
    "Does this use JAX?",
    "Does this use scikit-learn?",
    "Does this use Hugging Face Transformers?",
    "What model architecture does this implement?",
    "Does this support GPU acceleration?",
    "Does this support TPU?",
    "Does this use CUDA?",
    "What quantization methods are supported?",
    "Does this use ONNX?",
    "What inference runtime does this use?",

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
    "Does this run on AWS?",
    "Does this run on GCP?",
    "Does this run on Azure?",
    "Does this use Terraform?",
    "Does this use Helm?",
    "What container registry does this use?",
    "Does this use GitHub Actions?",
    "Does this use GitLab CI?",
    "What infrastructure as code tool is used?",

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

    # Architecture and design (harder, open-ended)
    "What is the overall architecture of this project?",
    "How is the codebase organized?",
    "What design patterns does this use?",
    "How do the components communicate?",
    "What is the data flow through the system?",
    "How does authentication work?",
    "How does authorization work?",
    "What are the main modules or services?",
    "Is this a monolith or microservices?",
    "How is state managed?",

    # Scale and complexity
    "How large is this codebase?",
    "How many services does this have?",
    "What are the performance characteristics?",
    "How does this handle concurrency?",
    "What are the scaling considerations?",

    # Integration and external systems
    "What external services does this integrate with?",
    "What third-party APIs does this call?",
    "How does this communicate with other systems?",
    "What message queues or event buses are used?",
    "What caching strategy is used?",

    # Security and reliability
    "How are secrets managed?",
    "What security measures are in place?",
    "How are errors handled?",
    "What logging and monitoring is used?",
    "How is configuration managed across environments?",

    # Development workflow
    "How do I set up the development environment?",
    "What are the contribution guidelines?",
    "How is code review done?",
    "What CI/CD pipeline is used?",
    "How are database migrations handled?",
]


def _has_sentence_transformers() -> bool:
    """Check if sentence-transformers is available."""
    try:
        from sentence_transformers import SentenceTransformer  # noqa: F401
        import numpy  # noqa: F401
        return True
    except ImportError:
        return False


def _get_repo_languages(repo_root: Path) -> set[str]:
    """Detect languages in a repo by scanning for common file extensions."""
    ext_to_lang = {
        ".py": "python", ".js": "javascript", ".ts": "typescript",
        ".go": "go", ".rs": "rust", ".java": "java", ".kt": "kotlin",
        ".scala": "scala", ".rb": "ruby", ".php": "php",
        ".ex": "elixir", ".exs": "elixir", ".erl": "erlang",
        ".hs": "haskell", ".swift": "swift", ".cs": "csharp",
        ".fs": "fsharp", ".c": "c", ".cpp": "cpp", ".cc": "cpp",
        ".ml": "ocaml", ".clj": "clojure", ".zig": "zig",
        ".nim": "nim", ".dart": "dart", ".jl": "julia",
        ".groovy": "groovy",
    }
    languages: set[str] = set()
    try:
        for item in repo_root.rglob("*"):
            if item.is_file():
                ext = item.suffix.lower()
                if ext in ext_to_lang:
                    languages.add(ext_to_lang[ext])
                    if len(languages) > 10:
                        break
    except OSError:
        pass
    return languages if languages else {"_common"}


def _collect_config_content(
    repo_root: Path,
    config_files: list[str],
    config_subdirs: list[str],
    license_files: list[str],
) -> list[tuple[str, str]]:
    """Collect all config file content as (filename, content) pairs.

    Used by embedding mode to have raw content for semantic selection.

    Args:
        repo_root: Path to repository root.
        config_files: List of config file names to look for.
        config_subdirs: List of subdirectories to check.
        license_files: List of license file names.

    Returns:
        List of (prefixed_filename, content) tuples.
    """
    config_content: list[tuple[str, str]] = []

    for config_name in config_files:
        for subdir in config_subdirs:
            config_path = repo_root / subdir / config_name if subdir else repo_root / config_name
            if not config_path.exists():
                continue

            try:
                content = config_path.read_text(encoding="utf-8", errors="replace")
                prefix = f"{subdir}/" if subdir else ""
                config_content.append((f"{prefix}{config_name}", content))
            except OSError:
                pass

    # Also include LICENSE file content
    for license_name in license_files:
        license_path = repo_root / license_name
        if license_path.exists():
            try:
                content = license_path.read_text(encoding="utf-8", errors="replace")[:2000]
                config_content.append((license_name, content))
                break  # Only first license file
            except OSError:
                pass

    return config_content


def _discover_config_files_embedding(
    repo_root: Path,
    config_files_by_lang: dict[str, list[str]],
    similarity_threshold: float = 0.85,
    max_dir_size: int = 200,
    detected_languages: set[str] | None = None,
) -> set[Path]:
    """Discover potential config files using embedding similarity.

    Uses language-specific probe embeddings to reduce false positives.
    A Kotlin project won't match on "Pipfile" because Python config patterns
    aren't included when only Kotlin is detected.

    Uses sentence-transformers to find files with names similar to known
    CONFIG_FILES patterns. This catches config files in unfamiliar formats.

    Args:
        repo_root: Path to repository root.
        config_files_by_lang: Mapping of language to config file patterns.
        similarity_threshold: Minimum cosine similarity to consider a match.
        max_dir_size: Skip directories with more than this many items.
        detected_languages: Pre-detected languages (optional).

    Returns:
        Set of discovered config file paths.
    """
    try:
        from sentence_transformers import SentenceTransformer
        import numpy as np
    except ImportError:
        return set()  # No discovery without sentence-transformers

    # Detect languages if not provided
    if detected_languages is None:
        detected_languages = _get_repo_languages(repo_root)

    # Build language-specific config file list
    relevant_configs: set[str] = set()
    for lang in detected_languages:
        if lang in config_files_by_lang:
            relevant_configs.update(config_files_by_lang[lang])
    # Always include common configs
    relevant_configs.update(config_files_by_lang.get("_common", []))

    # If no language detected, fall back to all configs
    if not relevant_configs:
        for files in config_files_by_lang.values():
            relevant_configs.update(files)

    # Get base names (strip glob patterns)
    known_names = []
    for name in relevant_configs:
        if "*" in name:
            # For patterns like "*.csproj", use the extension as semantic hint
            known_names.append(name.replace("*", "config"))
        else:
            known_names.append(name)

    # Collect unique filenames from repo, excluding large directories
    repo_files: dict[str, list[Path]] = {}  # filename -> list of paths
    try:
        for item in repo_root.rglob("*"):
            if not item.is_file():
                continue
            # Skip hidden directories and common non-config paths
            parts = item.relative_to(repo_root).parts
            if any(p.startswith(".") and p not in {".ruby-version"} for p in parts[:-1]):
                continue
            if any(p in {"node_modules", "vendor", "venv", ".venv", "__pycache__",
                        "dist", "build", "target", "_build", "deps"} for p in parts):
                continue

            # Check parent directory size (skip if too large)
            parent = item.parent
            try:
                dir_size = sum(1 for _ in parent.iterdir())
                if dir_size > max_dir_size:
                    continue
            except OSError:
                continue

            filename = item.name
            repo_files.setdefault(filename, []).append(item)
    except OSError:
        return set()

    if not repo_files:
        return set()

    # Get unique filenames that aren't already in our language-specific configs
    candidate_names = [
        name for name in repo_files.keys()
        if name not in relevant_configs
        and not name.endswith((".md", ".txt", ".rst", ".html", ".css", ".js",
                               ".ts", ".py", ".go", ".rs", ".java", ".c", ".h",
                               ".cpp", ".hpp", ".rb", ".ex", ".exs"))  # Skip source files
        and len(name) > 2  # Skip trivial names
    ]

    if not candidate_names:
        return set()

    # Pre-filter using character n-gram similarity (fast)
    def ngram_similarity(s1: str, s2: str, n: int = 3) -> float:
        """Compute character n-gram Jaccard similarity."""
        if len(s1) < n or len(s2) < n:
            return 1.0 if s1 == s2 else 0.0
        ngrams1 = {s1[i:i+n] for i in range(len(s1) - n + 1)}
        ngrams2 = {s2[i:i+n] for i in range(len(s2) - n + 1)}
        if not ngrams1 or not ngrams2:
            return 0.0
        intersection = len(ngrams1 & ngrams2)
        union = len(ngrams1 | ngrams2)
        return intersection / union if union > 0 else 0.0

    # Filter candidates by n-gram similarity to known config files
    ngram_threshold = 0.15  # Low threshold - just filter obvious non-matches
    filtered_candidates = []
    for name in candidate_names:
        max_sim = max(ngram_similarity(name.lower(), known.lower())
                     for known in known_names)
        if max_sim >= ngram_threshold:
            filtered_candidates.append(name)

    if not filtered_candidates:
        return set()

    # Limit remaining candidates for embedding
    max_candidates = 50
    if len(filtered_candidates) > max_candidates:
        # Sort by best n-gram similarity and take top
        filtered_candidates = sorted(
            filtered_candidates,
            key=lambda n: max(ngram_similarity(n.lower(), k.lower()) for k in known_names),
            reverse=True
        )[:max_candidates]

    # Load embedding model and compute similarities
    model = SentenceTransformer("microsoft/unixcoder-base")

    # Embed known config file names
    known_embeddings = model.encode(known_names, convert_to_numpy=True)

    # Embed candidate filenames (pre-filtered by n-grams)
    candidate_embeddings = model.encode(filtered_candidates, convert_to_numpy=True)

    # Normalize for cosine similarity
    known_norms = np.linalg.norm(known_embeddings, axis=1, keepdims=True)
    known_normalized = known_embeddings / (known_norms + 1e-8)

    candidate_norms = np.linalg.norm(candidate_embeddings, axis=1, keepdims=True)
    candidate_normalized = candidate_embeddings / (candidate_norms + 1e-8)

    # Compute pairwise similarities (candidates x known)
    similarities = np.dot(candidate_normalized, known_normalized.T)

    # Find candidates that match any known config file pattern
    discovered: set[Path] = set()
    max_sims = np.max(similarities, axis=1)

    for name, max_sim in zip(filtered_candidates, max_sims, strict=True):
        if max_sim >= similarity_threshold:
            # Add all paths with this filename (could be in multiple subdirs)
            for path in repo_files[name]:
                discovered.add(path)

    return discovered


def _collect_config_content_with_discovery(
    repo_root: Path,
    config_files: list[str],
    config_subdirs: list[str],
    config_files_by_lang: dict[str, list[str]],
    license_files: list[str],
    use_discovery: bool = True,
) -> list[tuple[str, str]]:
    """Collect config file content, optionally with embedding-based discovery.

    Extends _collect_config_content by also including files discovered through
    embedding similarity matching.

    Args:
        repo_root: Path to repository root.
        config_files: List of config file names.
        config_subdirs: List of subdirectories to check.
        config_files_by_lang: Mapping of language to config file patterns.
        license_files: List of license file names.
        use_discovery: If True, use embedding-based discovery for additional files.

    Returns:
        List of (prefixed_filename, content) tuples.
    """
    # Start with standard config collection
    config_content = _collect_config_content(
        repo_root, config_files, config_subdirs, license_files
    )
    seen_paths: set[Path] = set()

    # Track which files we already have
    for config_name in config_files:
        for subdir in config_subdirs:
            if "*" in config_name:
                # Handle glob patterns
                pattern = config_name
                search_dir = repo_root / subdir if subdir else repo_root
                if search_dir.exists():
                    for match in search_dir.glob(pattern):
                        if match.is_file():
                            seen_paths.add(match)
            else:
                config_path = repo_root / subdir / config_name if subdir else repo_root / config_name
                if config_path.exists():
                    seen_paths.add(config_path)

    # Also handle glob patterns from CONFIG_FILES
    for config_name in config_files:
        if "*" in config_name:
            for subdir in config_subdirs:
                search_dir = repo_root / subdir if subdir else repo_root
                if search_dir.exists():
                    for match in search_dir.glob(config_name):
                        if match.is_file() and match not in seen_paths:
                            try:
                                content = match.read_text(encoding="utf-8", errors="replace")
                                rel_path = match.relative_to(repo_root)
                                config_content.append((str(rel_path), content))
                                seen_paths.add(match)
                            except OSError:
                                pass

    if not use_discovery:
        return config_content

    # Discover additional config files using embeddings
    discovered = _discover_config_files_embedding(repo_root, config_files_by_lang)

    for path in discovered:
        if path in seen_paths:
            continue
        try:
            content = path.read_text(encoding="utf-8", errors="replace")
            rel_path = path.relative_to(repo_root)
            config_content.append((str(rel_path), content))
            seen_paths.add(path)
        except OSError:
            pass

    return config_content


def _compute_log_sample_size(num_lines: int, fleximax: int) -> int:
    """Compute log-scaled sample size for a file.

    For small files (num_lines <= fleximax), samples all lines.
    For larger files, uses formula: fleximax + log10(num_lines) * (fleximax/10)

    This ensures large files get more samples but growth is logarithmic.
    """
    import math
    if num_lines <= fleximax:
        return num_lines
    # log10(1000) = 3, so a 1000-line file with fleximax=100 gets 100 + 3*10 = 130
    return int(fleximax + math.log10(num_lines) * (fleximax / 10))


def _compute_stride(num_lines: int, sample_size: int) -> int:
    """Compute stride N for sampling, ensuring N >= 4 for context windows.

    Returns the smallest N >= 4 such that num_lines / N <= sample_size.
    If num_lines <= sample_size, returns 1 (sample all).
    """
    if num_lines <= sample_size:
        return 1
    # Find N such that ceil(num_lines / N) <= sample_size
    # N = ceil(num_lines / sample_size)
    n = (num_lines + sample_size - 1) // sample_size
    return max(4, n)


def _build_context_chunk(
    lines: list[str],
    center_idx: int,
    max_chunk_chars: int,
    fleximax_words: int = 50,
) -> str:
    """Build a 3-line chunk with context, subsampling words if too long.

    Takes lines [center_idx-1, center_idx, center_idx+1] and joins them.
    If the result exceeds max_chunk_chars, applies word-level subsampling
    with ellipsis to indicate elision.

    Args:
        lines: All lines in the file.
        center_idx: Index of the center line to build chunk around.
        max_chunk_chars: Maximum characters for the chunk.
        fleximax_words: Base sample size for word-level subsampling.

    Returns:
        Chunk string, possibly with ellipsis if words were subsampled.
    """
    import math

    # Get context lines (before, center, after)
    start_idx = max(0, center_idx - 1)
    end_idx = min(len(lines), center_idx + 2)
    context_lines = [lines[i] for i in range(start_idx, end_idx) if lines[i]]

    chunk = " ".join(context_lines)

    # If within limit, return as-is
    if len(chunk) <= max_chunk_chars:
        return chunk

    # Need to subsample at word level
    words = chunk.split()
    num_words = len(words)

    if num_words <= fleximax_words:
        # Just truncate to max_chars
        return chunk[:max_chunk_chars]

    # Compute log-scaled sample size for words
    sample_size = int(fleximax_words + math.log10(num_words) * (fleximax_words / 10))
    stride = max(4, (num_words + sample_size - 1) // sample_size)

    # Sample words with context (before, target, after) and ellipsis
    result_parts: list[str] = []
    i = 0
    while i < num_words:
        # Get context: before, center, after
        before_idx = max(0, i - 1)
        after_idx = min(num_words - 1, i + 1)

        context_words = []
        if before_idx < i:
            context_words.append(words[before_idx])
        context_words.append(words[i])
        if after_idx > i:
            context_words.append(words[after_idx])

        result_parts.append(" ".join(context_words))
        i += stride

    # Join with ellipsis
    result = " ... ".join(result_parts)

    # Final truncation if still too long
    if len(result) > max_chunk_chars:
        result = result[:max_chunk_chars - 3] + "..."

    return result


def extract_config_embedding(
    repo_root: Path,
    config_files: list[str],
    config_subdirs: list[str],
    config_files_by_lang: dict[str, list[str]],
    license_files: list[str],
    heuristic_fallback: callable,
    max_lines: int = 30,
    similarity_threshold: float = 0.25,
    max_lines_per_file: int = 8,
    max_config_files: int = 15,
    fleximax_lines: int = 100,
    max_chunk_chars: int = 800,
) -> list[str]:
    """Extract config metadata using dual-probe stratified embedding selection.

    Uses a dual-probe system with sentence-transformers:
    1. ANSWER_PATTERNS probe: Matches factual metadata lines (version, name, etc.)
    2. BIG_PICTURE_QUESTIONS probe: Matches architectural/contextual lines

    Each file is searched independently (stratified) to prevent large files
    from crowding out smaller ones. Uses log-scaled sampling for large files:
    files with more lines get proportionally more samples (logarithmically).

    Lines are sampled with context (before/after) and combined into chunks
    for embedding. If chunks exceed max_chunk_chars, word-level subsampling
    with ellipsis is applied.

    Args:
        repo_root: Path to repository root.
        config_files: List of config file names.
        config_subdirs: List of subdirectories to check.
        config_files_by_lang: Mapping of language to config file patterns.
        license_files: List of license file names.
        heuristic_fallback: Function to call if sentence-transformers unavailable.
        max_lines: Maximum total lines to extract across all files.
        similarity_threshold: Minimum similarity score to include a line.
        max_lines_per_file: Maximum lines to extract per config file.
        max_config_files: Maximum number of config files to process.
        fleximax_lines: Base sample size for log-scaled line sampling.
        max_chunk_chars: Maximum characters per chunk for embedding.

    Returns:
        List of extracted metadata lines, ordered by file then relevance.
    """
    try:
        from sentence_transformers import SentenceTransformer
        import numpy as np
    except ImportError:
        # Fall back to heuristic if sentence-transformers not available
        return heuristic_fallback(repo_root)[:max_lines]

    # Collect all config content (with embedding-based discovery)
    config_content = _collect_config_content_with_discovery(
        repo_root, config_files, config_subdirs, config_files_by_lang,
        license_files, use_discovery=True
    )
    if not config_content:
        return []

    # Verbose logging setup
    import sys as _sys
    import time as _time
    _verbose = "HYPERGUMBO_VERBOSE" in os.environ

    def _vlog(msg: str) -> None:
        if _verbose:
            print(f"[embed] {msg}", file=_sys.stderr)

    # Load embedding model once
    _t_load = _time.time()
    model = SentenceTransformer("microsoft/unixcoder-base")
    _vlog(f"Model loaded in {_time.time() - _t_load:.1f}s")

    # Compute normalized embeddings for both probes
    # Using max-to-any-pattern approach (not centroid) for better exact matching
    _t_probes = _time.time()
    # Probe 1: Answer patterns (factual metadata lines)
    answer_embeddings = model.encode(ANSWER_PATTERNS, convert_to_numpy=True)
    answer_norms = np.linalg.norm(answer_embeddings, axis=1, keepdims=True)
    normalized_answer_patterns = answer_embeddings / (answer_norms + 1e-8)

    # Probe 2: Big-picture questions (architectural context)
    question_embeddings = model.encode(BIG_PICTURE_QUESTIONS, convert_to_numpy=True)
    question_norms = np.linalg.norm(question_embeddings, axis=1, keepdims=True)
    normalized_question_patterns = question_embeddings / (question_norms + 1e-8)
    _vlog(f"Probe embeddings ({len(ANSWER_PATTERNS)}+{len(BIG_PICTURE_QUESTIONS)}) in {_time.time() - _t_probes:.1f}s")

    # === PASS 1: Score all files, collect top candidates from each ===
    # Structure: {source: [(sim, center_idx, chunk_text, file_lines), ...]}
    file_candidates: dict[str, list[tuple[float, int, str, list[str], np.ndarray]]] = {}
    processed_files = 0

    for source, content in config_content:
        if processed_files >= max_config_files:
            break

        file_lines = [ln.strip() for ln in content.split("\n")]
        _vlog(f"Processing {source} ({len(file_lines)} lines)...")

        # Get non-empty lines with their indices
        non_empty = [(idx, line) for idx, line in enumerate(file_lines)
                     if line and len(line) > 3]

        if not non_empty:
            continue

        num_lines = len(non_empty)

        # Compute log-scaled sample size and stride
        sample_size = _compute_log_sample_size(num_lines, fleximax_lines)
        stride = _compute_stride(num_lines, sample_size)
        _vlog(f"  Log-scaled: {num_lines} lines -> sample {sample_size}, stride {stride}")

        # Sample line indices at stride intervals
        sampled_indices: list[int] = []
        for i in range(0, num_lines, stride):
            sampled_indices.append(non_empty[i][0])  # Get original line index

        # Build context chunks for each sampled line
        chunks: list[tuple[int, str]] = []  # (center_idx, chunk_text)
        for center_idx in sampled_indices:
            chunk = _build_context_chunk(file_lines, center_idx, max_chunk_chars)
            if chunk:  # Skip empty chunks
                chunks.append((center_idx, chunk))

        if not chunks:
            continue

        # Embed chunks
        chunk_texts = [chunk for _, chunk in chunks]
        _t0 = _time.time()
        chunk_embeddings = model.encode(chunk_texts, convert_to_numpy=True)
        _vlog(f"  Encoded {len(chunk_texts)} chunks in {_time.time() - _t0:.1f}s")

        # Normalize chunks and compute similarity to all probes
        _t1 = _time.time()
        chunk_norms = np.linalg.norm(chunk_embeddings, axis=1, keepdims=True)
        normalized_chunks = chunk_embeddings / (chunk_norms + 1e-8)
        # Shape: (num_chunks, num_answer_patterns)
        answer_sim_matrix = np.dot(normalized_chunks, normalized_answer_patterns.T)
        # Shape: (num_chunks, num_question_patterns)
        question_sim_matrix = np.dot(normalized_chunks, normalized_question_patterns.T)
        # Combine into single matrix: (num_chunks, num_all_probes)
        combined_sim_matrix = np.concatenate(
            [answer_sim_matrix, question_sim_matrix], axis=1
        )
        # Top-k mean: require k probes to "agree" rather than one spurious match
        # This softens max-pooling sensitivity while preserving signal
        top_k = 3
        num_probes = combined_sim_matrix.shape[1]
        if num_probes >= top_k:
            # Partition to get top-k values (more efficient than full sort)
            top_k_values = np.partition(combined_sim_matrix, -top_k, axis=1)[:, -top_k:]
            similarities = np.mean(top_k_values, axis=1)
        else:
            # Fallback if fewer probes than k (shouldn't happen in practice)
            similarities = np.mean(combined_sim_matrix, axis=1)

        # Apply penalty for LICENSE/COPYING files - their verbose content is
        # semantically similar to many probes but has low information density.
        # ANSWER_PATTERNS already captures compact 'license = "MIT"' declarations.
        source_lower = source.lower()
        if "license" in source_lower or "copying" in source_lower:
            license_penalty = 0.5  # Reduce similarity scores by 50%
            similarities = similarities * license_penalty
            _vlog(f"  Applied LICENSE penalty ({license_penalty}x) to {source}")

        _vlog(f"  Dot products/similarity in {(_time.time() - _t1)*1000:.1f}ms")

        # Collect chunks above threshold, sorted by similarity
        # Store center_idx, chunk_text, file_lines, AND embedding for diversity computation
        above_threshold = [
            (float(sim), center_idx, chunk_text, file_lines, normalized_chunks[i])
            for i, ((center_idx, chunk_text), sim) in enumerate(
                zip(chunks, similarities, strict=True)
            )
            if sim >= similarity_threshold
        ]
        above_threshold.sort(reverse=True, key=lambda x: x[0])

        if above_threshold:
            file_candidates[source] = above_threshold

        processed_files += 1

    if not file_candidates:
        return []

    # === PASS 2: Fair allocation across files ===
    # Each file gets equal base allocation, then remainder distributed by quality
    base_per_file = max(5, max_lines_per_file // 2)  # Minimum 5 lines per file

    # Collect selected chunks with fair allocation
    # Structure: [(sim, source, center_idx, chunk_text), ...]
    selected_chunks: list[tuple[float, str, int, str]] = []

    # Track picks per file for diminishing returns AND selected embeddings for diversity
    picks_per_file: dict[str, int] = dict.fromkeys(file_candidates, 0)
    # selected_embeddings_per_file: {source: [embedding1, embedding2, ...]}
    selected_embeddings_per_file: dict[str, list[np.ndarray]] = {
        source: [] for source in file_candidates
    }

    # First: give each file its base allocation
    for source, candidates in file_candidates.items():
        for sim, center_idx, chunk_text, _file_lines, embedding in candidates[
            :base_per_file
        ]:
            selected_chunks.append((sim, source, center_idx, chunk_text))
            picks_per_file[source] += 1
            selected_embeddings_per_file[source].append(embedding)

    # Second: if budget remains, fill with diminishing returns + diversity selection
    remaining_budget = max_lines - len(selected_chunks)
    if remaining_budget > 0:
        # Parameters for diminishing returns and diversity
        diminishing_alpha = 0.5  # Same as symbol selection
        diversity_weight = 0.3  # How much to penalize similar chunks

        # Build priority queue with adjusted scores
        # Structure: [(-adjusted_score, sim, source, center_idx, chunk_text, embedding)]
        import heapq

        pq: list[tuple[float, float, str, int, str, np.ndarray]] = []

        for source, candidates in file_candidates.items():
            for sim, center_idx, chunk_text, _file_lines, embedding in candidates[
                base_per_file:
            ]:
                # Compute initial adjusted score
                picks = picks_per_file[source]
                marginal = sim / (1 + diminishing_alpha * picks)

                # Compute diversity penalty (max similarity to already-selected from same file)
                diversity_penalty = 0.0
                if selected_embeddings_per_file[source]:
                    selected_embs = np.array(selected_embeddings_per_file[source])
                    # embedding is already normalized, selected_embs are normalized
                    chunk_sims = np.dot(selected_embs, embedding)
                    diversity_penalty = float(np.max(chunk_sims))

                # Adjusted score: diminishing returns * diversity discount
                adjusted = marginal * (1 - diversity_weight * diversity_penalty)
                heapq.heappush(
                    pq, (-adjusted, sim, source, center_idx, chunk_text, embedding)
                )

        # Greedy selection with recomputation after each pick
        while len(selected_chunks) < max_lines and pq:
            neg_adj, sim, source, center_idx, chunk_text, embedding = heapq.heappop(pq)

            # Add to selected
            selected_chunks.append((sim, source, center_idx, chunk_text))
            picks_per_file[source] += 1
            selected_embeddings_per_file[source].append(embedding)

            # Recompute scores for remaining candidates from the SAME file
            # (their diversity penalty has changed)
            new_pq: list[tuple[float, float, str, int, str, np.ndarray]] = []
            while pq:
                neg_adj2, sim2, source2, center_idx2, chunk_text2, emb2 = heapq.heappop(
                    pq
                )
                if source2 == source:
                    # Recompute adjusted score for this candidate
                    picks = picks_per_file[source2]
                    marginal = sim2 / (1 + diminishing_alpha * picks)
                    selected_embs = np.array(selected_embeddings_per_file[source2])
                    chunk_sims = np.dot(selected_embs, emb2)
                    diversity_penalty = float(np.max(chunk_sims))
                    adjusted = marginal * (1 - diversity_weight * diversity_penalty)
                    new_pq.append(
                        (-adjusted, sim2, source2, center_idx2, chunk_text2, emb2)
                    )
                else:
                    # Keep original score (unchanged)
                    new_pq.append(
                        (neg_adj2, sim2, source2, center_idx2, chunk_text2, emb2)
                    )
            # Rebuild heap
            heapq.heapify(new_pq)
            pq = new_pq

    # === PASS 3: Format output, grouping by file ===
    from collections import defaultdict
    by_source: dict[str, list[tuple[float, int, str]]] = defaultdict(list)
    for sim, source, center_idx, chunk_text in selected_chunks:
        by_source[source].append((sim, center_idx, chunk_text))

    # Sort each file's chunks by center line index for coherent output
    for source in by_source:
        by_source[source].sort(key=lambda x: x[1])

    # Build output - all files get representation
    result_lines: list[str] = []

    for source in sorted(by_source.keys()):
        file_selected = by_source[source]
        if not file_selected:
            continue

        # Add file header
        if result_lines:
            result_lines.append("")
        result_lines.append(f"[{source}]")

        # Output chunks (context already included, may have ellipsis for subsampled)
        seen_chunks: set[int] = set()
        for _sim, center_idx, chunk_text in file_selected:
            # Deduplicate overlapping chunks by center index
            if center_idx in seen_chunks:
                continue
            seen_chunks.add(center_idx)

            # Format chunk - indent and mark with ~ if it contains ellipsis (was subsampled)
            if " ... " in chunk_text:
                result_lines.append(f"  ~ {chunk_text}")
            else:
                result_lines.append(f"  > {chunk_text}")

    return result_lines


def extract_config_hybrid(
    repo_root: Path,
    config_files: list[str],
    config_subdirs: list[str],
    config_files_by_lang: dict[str, list[str]],
    license_files: list[str],
    heuristic_func: callable,
    max_chars: int = 1500,
    max_config_files: int = 15,
    fleximax_lines: int = 100,
    max_chunk_chars: int = 800,
) -> list[str]:
    """Extract config using hybrid approach: heuristics first, then embeddings.

    This combines the best of both approaches:
    1. First, extract known fields using fast heuristic patterns
    2. Then, use embedding-based selection to fill remaining budget
       with semantically relevant content not captured by heuristics

    Args:
        repo_root: Path to repository root.
        config_files: List of config file names.
        config_subdirs: List of subdirectories to check.
        config_files_by_lang: Mapping of language to config file patterns.
        license_files: List of license file names.
        heuristic_func: Function to extract config via heuristics.
        max_chars: Maximum characters for output.
        max_config_files: Maximum config files to process (embedding mode).
        fleximax_lines: Base sample size for log-scaled line sampling.
        max_chunk_chars: Maximum characters per chunk for embedding.

    Returns:
        List of extracted metadata lines.
    """
    # Step 1: Get heuristic extraction (fast, reliable for known fields)
    heuristic_lines = heuristic_func(repo_root)
    heuristic_text = "\n".join(heuristic_lines)

    # If heuristics already fill the budget, we're done
    if len(heuristic_text) >= max_chars * 0.8:
        return heuristic_lines

    # Step 2: Compute remaining budget for embedding-based extraction
    remaining_chars = max_chars - len(heuristic_text) - 50  # Buffer
    if remaining_chars < 100:
        return heuristic_lines

    # Estimate lines we can add
    remaining_lines = max(5, remaining_chars // 50)

    # Step 3: Get embedding-based extraction
    try:
        embedding_lines = extract_config_embedding(
            repo_root,
            config_files=config_files,
            config_subdirs=config_subdirs,
            config_files_by_lang=config_files_by_lang,
            license_files=license_files,
            heuristic_fallback=heuristic_func,
            max_lines=remaining_lines,
            max_config_files=max_config_files,
            fleximax_lines=fleximax_lines,
            max_chunk_chars=max_chunk_chars,
        )
    except Exception:
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
