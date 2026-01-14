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

import logging
import os
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import numpy as np

# Model name constant
_EMBEDDING_MODEL = "microsoft/unixcoder-base"


def _load_embedding_model():
    """Load SentenceTransformer model with warnings suppressed.

    The sentence-transformers library logs a warning when creating a new model
    wrapper for models it doesn't recognize. This is expected for UnixCoder
    and not useful to users.
    """
    from sentence_transformers import SentenceTransformer

    # Suppress "No sentence-transformers model found" warning
    logging.getLogger("sentence_transformers").setLevel(logging.ERROR)
    return SentenceTransformer(_EMBEDDING_MODEL)

# Probe patterns for embedding-based config extraction
# These are embedded and compared against config file content
#
# WARNING: If you modify any probe patterns (ANSWER_PATTERNS, BIG_PICTURE_QUESTIONS,
# or README_DESCRIPTION_PROBES), you MUST regenerate the precomputed embeddings:
#     python scripts/compute_probe_embeddings.py
# Otherwise the embeddings in _embedding_data.py will be out of sync with the probes.

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


def _decode_probe_embeddings(b64_data: str, num_probes: int) -> "np.ndarray":
    """Decode pre-computed probe embeddings from base64 float16.

    Args:
        b64_data: Base64-encoded float16 array.
        num_probes: Number of probes (for reshape).

    Returns:
        Normalized float32 embeddings array of shape (num_probes, 768).
    """
    import base64
    import numpy as np

    raw = base64.b64decode(b64_data)
    arr = np.frombuffer(raw, dtype=np.float16).reshape(num_probes, 768)
    return arr.astype(np.float32)


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
    if not _has_sentence_transformers():
        return set()  # No discovery without sentence-transformers
    import numpy as np

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
    model = _load_embedding_model()

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
    if not _has_sentence_transformers():
        # Fall back to heuristic if sentence-transformers not available
        return heuristic_fallback(repo_root)[:max_lines]
    import numpy as np

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
    model = _load_embedding_model()
    _vlog(f"Model loaded in {_time.time() - _t_load:.1f}s")

    # Get pre-computed normalized embeddings for both probes
    # Using max-to-any-pattern approach (not centroid) for better exact matching
    _t_probes = _time.time()
    # Probe 1: Answer patterns (factual metadata lines)
    normalized_answer_patterns = _get_answer_probe_embeddings()

    # Probe 2: Big-picture questions (architectural context)
    normalized_question_patterns = _get_bigpic_probe_embeddings()
    _vlog(f"Probe embeddings ({len(ANSWER_PATTERNS)}+{len(BIG_PICTURE_QUESTIONS)}) decoded in {_time.time() - _t_probes:.3f}s")

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


# Probe patterns for README description extraction
# These are mission statements from well-known open source projects
# Used to identify lines that describe what a project does
#
# WARNING: Changes require regenerating embeddings! Run:
#     python scripts/compute_probe_embeddings.py
README_DESCRIPTION_PROBES = [
    # Canonical “what it is + what it does + why it matters”
    "(Project Name) is an open-source (tool type/category) built for (user demographic) to (do their job) in (relevant circumstances). It offers (top 2-3 capabilities) so you can (primary benefit) with (reliability/security/scale/simplicity).",
    "(Project Name) is an open-source (tool type/category) for (audience/context) that (does something). With (top 2-3 capabilities), it helps you (primary benefit) while keeping things (reliable/secure/scalable/simple).",
    "(Project Name) is a (tool type/category) that enables (user demographic) to (do their job) in (relevant circumstances). It combines (top 2-3 capabilities) to deliver (primary benefit) at (scale/security/reliability/simplicity).",

    # Concise one-liners (common when repos lead with a tight thesis)
    "(Project Name) is a (tool type/category) for (audience/context) that (does something)—so you can (benefit).",
    "(Project Name) is a (tool type/category) that (does something) in (context) for (audience), helping you (benefit).",

    # Two-beat openers (matches “local-first CLI…” style)
    "A (adjective) (tool type/category) that (does something) from (input/source). Helps (audience) (achieve outcome) in (context).",
    "(Does something) in (context). So (audience) can (benefit).",

    # Tagline-led (common in trendy repos)
    "(Project Name): (tagline describing outcome in 3-7 words).",
    "(Punchy label/tagline). A (adjective) (tool type/category) for (audience/context) that (does something), so you can (benefit).",

    # Noun-phrase + promise (very “README-first”)
    "A (tool type/category) for (job-to-be-done) in (context). (Primary outcome/benefit), for (audience).",

    # Problem-first / user-need framing
    "If you need to (problem/job) in (context), (Project Name) helps by (core mechanism), so you can (benefit).",
    "For (audience) who need to (job) in (context), (Project Name) (does something) to (benefit).",

    # Purpose / mission framing (often used instead of “is a…”)
    "Built to (primary job) for (audience) in (context), (Project Name) provides (capabilities) to deliver (benefit).",
    "The goal of (Project Name) is to (primary outcome) for (audience) working in (context) by (mechanism).",

    # Category implied (library/framework/service language)
    "This (library/framework/service) lets you (do something) by (mechanism), making it easier to (benefit).",

    # Positioning by analogy (“X for Y”, “like A but B”)
    "(Project Name) is (known thing/category) for (new domain/audience)—like (comparison), but (key difference).",

    # Feature-bundle opener (some READMEs list capabilities before benefits)
    "(Project Name) is a (tool type/category) for (audience/context) that includes (capability 1), (capability 2), and (capability 3).",

    # Trust/attribute hook (leads with adjectives instead of function)
    "Fast, (secure/reliable/simple), and (scalable/portable), (Project Name) is a (tool type/category) for (audience) to (do job).",

    # Imperative “use it to…” (instructional openers)
    "Use (Project Name) to (primary job) in (context)—for example, (example use case).",
]

# Cache for probe embeddings (decoded from pre-computed base64)
_README_PROBE_EMBEDDINGS: "np.ndarray | None" = None
_ANSWER_PROBE_EMBEDDINGS: "np.ndarray | None" = None
_BIGPIC_PROBE_EMBEDDINGS: "np.ndarray | None" = None


def _get_readme_probe_embeddings() -> "np.ndarray":
    """Get pre-computed probe embeddings for README description extraction.

    Uses base64-encoded float16 embeddings from _embedding_data.py,
    avoiding the ~2-3s startup cost of computing embeddings at runtime.

    Returns:
        Normalized probe embeddings array of shape (19, 768).
    """
    global _README_PROBE_EMBEDDINGS

    if _README_PROBE_EMBEDDINGS is None:
        from ._embedding_data import README_PROBES_B64
        _README_PROBE_EMBEDDINGS = _decode_probe_embeddings(
            README_PROBES_B64, len(README_DESCRIPTION_PROBES)
        )

    return _README_PROBE_EMBEDDINGS


def _get_answer_probe_embeddings() -> "np.ndarray":
    """Get pre-computed probe embeddings for config answer patterns.

    Returns:
        Normalized probe embeddings array of shape (41, 768).
    """
    global _ANSWER_PROBE_EMBEDDINGS

    if _ANSWER_PROBE_EMBEDDINGS is None:
        from ._embedding_data import ANSWER_PROBES_B64
        _ANSWER_PROBE_EMBEDDINGS = _decode_probe_embeddings(
            ANSWER_PROBES_B64, len(ANSWER_PATTERNS)
        )

    return _ANSWER_PROBE_EMBEDDINGS


def _get_bigpic_probe_embeddings() -> "np.ndarray":
    """Get pre-computed probe embeddings for big picture questions.

    Returns:
        Normalized probe embeddings array of shape (127, 768).
    """
    global _BIGPIC_PROBE_EMBEDDINGS

    if _BIGPIC_PROBE_EMBEDDINGS is None:
        from ._embedding_data import BIGPIC_PROBES_B64
        _BIGPIC_PROBE_EMBEDDINGS = _decode_probe_embeddings(
            BIGPIC_PROBES_B64, len(BIG_PICTURE_QUESTIONS)
        )

    return _BIGPIC_PROBE_EMBEDDINGS


def _is_readme_line_filterable(line: str) -> bool:
    """Check if a README line should be filtered out before embedding.

    Filters badges, empty lines, pure-image lines, link reference definitions,
    and GitHub callout syntax.
    Does NOT filter HTML with text content (may contain descriptions).

    Args:
        line: The line to check.

    Returns:
        True if the line should be skipped.
    """
    import re

    stripped = line.strip()

    # Skip empty lines
    if not stripped:
        return True

    # Skip badge-only lines: [![alt](url)](link) or ![alt](url)
    if re.match(r"^!?\[!\[.*?\]\(.*?\)\]\(.*?\)$", stripped):
        return True
    if re.match(r"^!\[.*?\]\(.*?\)$", stripped):
        return True

    # Skip pure link lines (often badge URLs)
    if re.match(r"^\[.*?\]\(https?://.*?\)$", stripped):
        return True

    # Skip markdown link reference definitions: [label]: https://...
    # These are common at the top of READMEs but contain no description content
    if re.match(r"^\[.+?\]:\s*https?://", stripped):
        return True

    # Skip GitHub callout syntax: > [!NOTE], > [!IMPORTANT], > [!WARNING], etc.
    # These are typically announcements, not project descriptions
    if re.match(r"^>\s*\[!", stripped):
        return True

    # Skip HTML comments
    if stripped.startswith("<!--") and stripped.endswith("-->"):
        return True

    # Skip lines that are just <img> or <a> tags with image content
    if re.match(r"^<(img|a|picture|source)\s.*?/?>$", stripped, re.IGNORECASE):
        return True

    return False


class ReadmeExtractionDebug:
    """Debug info from README description extraction."""

    def __init__(
        self,
        description: str | None,
        k_scores: list[tuple[int, float]],
        final_k: int,
        stopped_early: bool,
        quality_drop: float | None,
        elapsed_seconds: float,
        lines_processed: int,
    ):
        self.description = description
        self.k_scores = k_scores  # List of (k, best_score) for each k tried
        self.final_k = final_k  # The k value that was used
        self.stopped_early = stopped_early  # True if stopped due to quality drop
        self.quality_drop = quality_drop  # The drop that triggered early stop, if any
        self.elapsed_seconds = elapsed_seconds
        self.lines_processed = lines_processed

    def __repr__(self) -> str:
        return (
            f"ReadmeExtractionDebug(k={self.final_k}, "
            f"stopped_early={self.stopped_early}, "
            f"quality_drop={self.quality_drop}, "
            f"elapsed={self.elapsed_seconds:.2f}s)"
        )


def extract_readme_description_embedding(
    readme_path: Path,
    max_lines: int = 80,
    max_window: int = 15,
    quality_drop_threshold: float = 0.07,
    top_k_probes: int = 3,
    position_bias: float = 0.4,
    debug: bool = False,
) -> str | ReadmeExtractionDebug | None:
    """Extract project description from README using embedding similarity.

    Uses probe embeddings from mission statements of well-known projects to
    identify lines that describe what the project does. Finds the best
    consecutive window of lines (up to max_window) using a sliding window
    approach that stops when quality drops significantly.

    Position bias ensures earlier lines are favored, since descriptions
    typically appear near the top of READMEs, right after the title.

    Args:
        readme_path: Path to the README file.
        max_lines: Maximum lines from README to consider (default 80).
        max_window: Maximum window size k (default 15).
        quality_drop_threshold: Stop when score drops by this fraction (default 0.07).
        top_k_probes: Number of top probe similarities to average (default 3).
        position_bias: Penalty for later lines (default 0.4 = 40% penalty at end).
        debug: If True, return ReadmeExtractionDebug with k-value scores and timing.

    Returns:
        If debug=False: Extracted description string, or None if extraction fails.
        If debug=True: ReadmeExtractionDebug object with description and debug info.
    """
    import time as _time
    start_time = _time.time()

    if not _has_sentence_transformers():
        if debug:
            return ReadmeExtractionDebug(
                description=None, k_scores=[], final_k=0, stopped_early=False,
                quality_drop=None, elapsed_seconds=0, lines_processed=0
            )
        return None

    import numpy as np

    try:
        content = readme_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        if debug:
            return ReadmeExtractionDebug(
                description=None, k_scores=[], final_k=0, stopped_early=False,
                quality_drop=None, elapsed_seconds=_time.time() - start_time,
                lines_processed=0
            )
        return None

    lines = content.split("\n")[:max_lines]

    # Track code block state for filtering
    in_code_block = False
    filtered_lines: list[tuple[int, str]] = []  # (original_idx, line)

    for idx, line in enumerate(lines):
        stripped = line.strip()

        # Track fenced code blocks
        if stripped.startswith("```"):
            in_code_block = not in_code_block
            continue

        # Skip lines inside code blocks
        if in_code_block:
            continue

        # Skip filterable lines (badges, empty, pure images)
        if _is_readme_line_filterable(line):
            continue

        filtered_lines.append((idx, stripped))

    if not filtered_lines:
        if debug:
            return ReadmeExtractionDebug(
                description=None, k_scores=[], final_k=0, stopped_early=False,
                quality_drop=None, elapsed_seconds=_time.time() - start_time,
                lines_processed=0
            )
        return None

    # Load model and get pre-computed probe embeddings
    model = _load_embedding_model()
    probe_embeddings = _get_readme_probe_embeddings()

    # Embed all filtered lines
    line_texts = [line for _, line in filtered_lines]
    line_embeddings = model.encode(line_texts, convert_to_numpy=True)
    line_norms = np.linalg.norm(line_embeddings, axis=1, keepdims=True)
    normalized_lines = line_embeddings / (line_norms + 1e-8)

    # Compute pairwise cosine similarities: (num_lines, num_probes)
    similarities = np.dot(normalized_lines, probe_embeddings.T)

    # Score each line as mean of top-k similarities with probes
    top_k = min(top_k_probes, len(README_DESCRIPTION_PROBES))
    line_scores = np.mean(np.sort(similarities, axis=1)[:, -top_k:], axis=1)

    # Apply position bias - earlier lines are more likely to be descriptions
    # Two-part bias:
    # 1. Title-proximity bonus: lines 1-5 after title get 25% boost (not the title itself)
    # 2. Exponential decay based on ABSOLUTE position (not relative to doc length)
    #    This ensures consistent behavior regardless of README length
    if position_bias > 0 and len(filtered_lines) > 1:
        # Use absolute positions, normalized to a fixed scale (assume ~20 lines is typical)
        # Lines 0-5 get minimal penalty, lines 20+ get maximum penalty
        scale_factor = 20.0  # Typical number of meaningful lines in a README
        absolute_positions = np.arange(len(filtered_lines)) / scale_factor
        # Cap at 1.0 to avoid over-penalizing very long READMEs
        absolute_positions = np.minimum(absolute_positions, 1.0)
        # Exponential decay based on absolute position
        position_weights = np.exp(-position_bias * 2 * absolute_positions)
        # Title-proximity bonus for lines AFTER the title (positions 1-5)
        # The title itself (position 0, usually "# Project") shouldn't get the bonus
        title_bonus = np.ones(len(filtered_lines))
        title_bonus[1:6] = 1.25  # 25% boost for lines 1-5 (right after title)
        line_scores = line_scores * position_weights * title_bonus

    # Sliding window to find best consecutive k lines
    best_window: tuple[int, int] | None = None  # (start_idx, end_idx)
    prev_best_score = -1.0
    k_scores: list[tuple[int, float]] = []  # Track scores for debug
    stopped_early = False
    quality_drop_value: float | None = None
    final_k = 0

    for k in range(1, max_window + 1):
        if k > len(filtered_lines):
            break

        # Find best window of size k
        window_scores = []
        for start in range(len(filtered_lines) - k + 1):
            window_score = float(np.mean(line_scores[start : start + k]))
            window_scores.append((start, window_score))

        if not window_scores:
            break

        # Get best window for this k
        best_start, best_k_score = max(window_scores, key=lambda x: x[1])
        k_scores.append((k, best_k_score))

        # Check for quality drop (only after k=1)
        if k > 1 and prev_best_score > 0:
            drop = (prev_best_score - best_k_score) / prev_best_score
            if drop >= quality_drop_threshold:
                # Quality dropped too much, use previous k
                stopped_early = True
                quality_drop_value = drop
                break

        # Update best
        best_window = (best_start, best_start + k)
        prev_best_score = best_k_score
        final_k = k

    if best_window is None:
        if debug:
            return ReadmeExtractionDebug(
                description=None, k_scores=k_scores, final_k=0, stopped_early=False,
                quality_drop=None, elapsed_seconds=_time.time() - start_time,
                lines_processed=len(filtered_lines)
            )
        return None

    # Extract the winning lines
    start_idx, end_idx = best_window
    selected_lines = [line for _, line in filtered_lines[start_idx:end_idx]]

    # Join and return
    description = " ".join(selected_lines)

    # Cleanup: strip HTML tags and excessive whitespace
    import re
    # Remove HTML tags but keep content
    description = re.sub(r"<[^>]+>", "", description)
    # Collapse whitespace
    description = " ".join(description.split())

    final_description = description if description else None

    if debug:
        return ReadmeExtractionDebug(
            description=final_description,
            k_scores=k_scores,
            final_k=final_k,
            stopped_early=stopped_early,
            quality_drop=quality_drop_value,
            elapsed_seconds=_time.time() - start_time,
            lines_processed=len(filtered_lines)
        )

    return final_description


# ==============================================================================
# Additional Files Semantic Ranking (5W1H probes)
# ==============================================================================

# 5W1H probes for semantic ranking of Additional Files
# These help surface documentation and explanatory files at the top
#
# WARNING: Changes require regenerating embeddings! Run:
#     python scripts/compute_probe_embeddings.py
ADDITIONAL_FILES_PROBES = [
    # Who this thing is for
    (
        "Who this thing is for This project is for people who want a clear, "
        "dependable tool that does one job well, and stays out of the way while "
        "you get on with your work. If you're the kind of person who reads a "
        "README before installing anything, you'll feel at home here. If you're "
        "the kind of person who doesn't, that's fine too: the defaults are "
        "designed to be sensible, the setup aims to be painless, and the 'happy "
        "path' should take you from zero to useful without a scavenger hunt "
        "through configuration files. It's for builders: developers wiring this "
        "into an app, scripting it into a workflow, or integrating it into CI. "
        "It's for maintainers who care about predictable behavior, stable "
        "interfaces, and changes that are explained rather than hand-waved. It's "
        "for curious tinkerers who like to poke at source code, file issues, "
        "propose improvements, or just understand how things work under the hood. "
        "It's also for teams. If you need something that can be documented, "
        "reviewed, tested, and shared without a private onboarding ritual, you're "
        "in the right place. And if you're new to the ecosystem, don't worry: the "
        "docs assume intelligence, not prior knowledge. You'll find examples, "
        "explanations, and reference material aimed at helping you succeed whether "
        "you're experimenting on a weekend or shipping on a deadline. If you want "
        "a tool that respects your time, welcomes contributions, and tries hard to "
        "be boring in production (in the best way), this thing is for you."
    ),
    # What this thing does
    (
        "This project exists to do one thing well: it does what it says it does - "
        "and then gets out of your way. At its core, it's a small, focused tool "
        "that takes an input (whatever 'input' means in your environment), applies "
        "a clear set of rules, and produces an output you can rely on. You can "
        "think of it as a dependable middle layer: it translates intent into "
        "action, turns repetitive steps into a single command, and makes the "
        "common case fast while keeping the uncommon case possible. The 'thing' "
        "here is intentionally general because the shape of your problem might be "
        "different from someone else's. Sometimes that means transforming data, "
        "sometimes orchestrating a workflow, sometimes smoothing over rough edges "
        "between systems that don't naturally fit together. In every case, the "
        "goal is the same: reduce friction, increase consistency, and provide a "
        "simple interface that feels obvious after you've used it once. It's "
        "designed to be practical rather than precious. You should be able to drop "
        "it into an existing setup, configure only what you need, and extend it "
        "when your requirements grow. If you're skimming this documentation, the "
        "takeaway is simple: this tool helps you get from 'I want this done' to "
        "'it's done' with fewer moving parts, fewer surprises, and more control "
        "over the details that matter to you."
    ),
    # When to use this
    (
        "Use this component when you want a small, dependable 'building block' "
        "that does one job well and fits cleanly into a larger system. It's a "
        "good choice in situations where you value clarity over cleverness: you "
        "need behavior that's easy to understand, easy to test, and unlikely to "
        "surprise future readers of your code. If you're looking for something "
        "that can be adopted incrementally - dropped into an existing project "
        "without forcing a redesign - this is an appropriate place to start. This "
        "tool shines when the surrounding requirements are stable or at least "
        "well-bounded. If you can describe the problem in a few sentences and "
        "you'd prefer a straightforward configuration or API surface, you'll "
        "likely find it productive. It's also well-suited for teams: conventions "
        "are explicit, defaults are sensible, and common workflows are documented "
        "so new contributors can get traction quickly. In other words, reach for "
        "it when you want to move fast without creating long-term ambiguity. Avoid "
        "using it when you need heavy customization, unusual edge-case behavior, "
        "or an experimental approach that's still changing week to week. In those "
        "cases, you may be better served by a lower-level primitive or a more "
        "flexible framework. But for most day-to-day tasks - reliable integration, "
        "repeatable outcomes, and maintainable code - this is a solid, practical "
        "option."
    ),
    # Where this comes from
    (
        "Where this thing comes from Every project has an origin story, even if "
        "it starts out as a single line on a sticky note or a half-remembered idea "
        "from a late-night debugging session. This section is the place where we "
        "trace the roots of 'this thing': not as a dramatic tale of destiny, but "
        "as a practical account of why it exists, what problems it was meant to "
        "address, and how its earliest assumptions shaped what you're holding "
        "today. Sometimes a tool appears because a gap kept showing up in real "
        "work - an awkward workflow, a recurring edge case, a missing layer of "
        "glue between two systems that otherwise behave nicely. Sometimes it's "
        "born from curiosity: a desire to see if an approach could be made "
        "simpler, faster, more transparent, or just easier to reuse. And sometimes "
        "the origin is less tidy: a pile of scripts that slowly grew legs, "
        "accumulated tests, acquired a name, and eventually demanded to be treated "
        "like a real project. In open source, provenance matters. Knowing where "
        "something comes from helps you understand its defaults, its trade-offs, "
        "and the kind of contributions that fit its trajectory. It can explain why "
        "certain features are emphasized, why certain decisions are conservative "
        "or bold, and why the project's language and structure look the way they "
        "do. Think of this as the context layer: a map of the initial constraints, "
        "the early use cases, and the motivations that continue to echo through "
        "the codebase. If you're new here, this is your orientation. If you've "
        "been around for a while, it's a reminder of the thread that ties today's "
        "implementation to yesterday's need."
    ),
    # Why this was built
    (
        "Why this thing was built - because a gap showed up, and it kept showing "
        "up. We had a workflow that looked fine on paper: a few scripts here, a "
        "manual checklist there, a half-dozen conventions that lived in someone's "
        "head. It worked until it didn't. Every new contributor had to rediscover "
        "the same sharp edges. Every deployment carried a small, unnecessary "
        "gamble. Every integration required a bespoke fix, and every 'temporary' "
        "workaround became permanent infrastructure. The cost wasn't dramatic in "
        "any single moment; it was the slow accumulation of friction: time lost to "
        "repetition, confidence lost to ambiguity, and opportunities lost because "
        "change felt risky. So this project exists to make the common path the "
        "easy path. It aims to turn tribal knowledge into documented behavior, and "
        "scattered one-off solutions into something coherent and reusable. It is "
        "intentionally general: useful in small prototypes and large systems, in "
        "local development and automated pipelines, in hobby projects and "
        "production services. You should be able to pick it up with minimal "
        "context, apply it to your own constraints, and extend it without "
        "rewriting the world. In short, it was built to reduce surprise. To "
        "replace 'hope it works' with 'we know why it works.' To offer a solid "
        "default, a clear surface area, and an approach that scales with your "
        "needs rather than fighting them."
    ),
    # How this works
    (
        "How this thing works is deliberately simple at the surface, and carefully "
        "engineered underneath. You point it at some input, you tell it what you "
        "want, and it produces an output you can inspect, reuse, or wire into "
        "something larger. There are no secret handshakes: the core behavior is "
        "exposed through a small set of commands and a predictable configuration "
        "layer, so you can get started quickly and still have room to grow into "
        "the deeper features. Conceptually, the system is a pipeline. Data enters "
        "through an adapter that normalizes formats, validates assumptions, and "
        "attaches a bit of metadata so later stages can make good decisions. From "
        "there, a runtime coordinates the actual work: it resolves dependencies, "
        "schedules tasks in the right order, and applies the selected options in a "
        "consistent way. Each step emits clear signals - logs, exit codes, and "
        "structured output - so you can debug problems without guessing. The "
        "important part is that every piece is replaceable. If you don't like the "
        "default parser, swap it. If you need different output, add a formatter. "
        "If your environment is unusual, provide your own transport or storage "
        "backend. Extensions follow the same rules as built-ins: small interfaces, "
        "stable contracts, and failure modes that are explicit rather than "
        "surprising. In short: it takes your intent, turns it into a plan, "
        "executes that plan reliably, and leaves an audit trail you can trust."
    ),
]

# ModernBERT model for Additional Files embedding
_MODERNBERT_MODEL_NAME = "nomic-ai/modernbert-embed-base"
_MODERNBERT_TRUNCATE_DIM = 256

# Cache for 5W1H probe embeddings (ModernBERT)
_ADDITIONAL_FILES_PROBE_EMBEDDINGS: "np.ndarray | None" = None


def _find_git_executable() -> str:
    """Find the full path to git executable.

    Returns:
        Full path to git, or "git" if not found (will fail gracefully).
    """
    import shutil
    return shutil.which("git") or "git"


def _run_git_command(
    args: list[str], cwd: Path, timeout: int = 5
) -> tuple[int, str, str]:
    """Run a git command and return (returncode, stdout, stderr).

    Args:
        args: Git command arguments (without 'git' prefix).
        cwd: Working directory for the command.
        timeout: Timeout in seconds.

    Returns:
        Tuple of (return_code, stdout, stderr).
    """
    import subprocess  # nosec B404 - required for git commands

    git_path = _find_git_executable()
    try:
        result = subprocess.run(  # noqa: S603  # nosec B603 - git_path from shutil.which
            [git_path, *args],
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        return result.returncode, result.stdout, result.stderr
    except Exception:  # pragma: no cover
        return 1, "", ""


def _get_repo_fingerprint(repo_root: Path) -> str:
    """Generate a stable fingerprint for a repository.

    For git repositories, uses the remote origin URL and first commit SHA to create
    a stable identifier that doesn't change when files are modified. This allows
    the cache to be shared across checkouts of the same repo.

    For non-git directories, falls back to hashing the absolute path.

    Args:
        repo_root: Repository root path.

    Returns:
        A 16-character hex fingerprint string.
    """
    import hashlib

    # Check if this is a git repo
    git_dir = repo_root / ".git"
    if git_dir.exists():
        fingerprint_parts = []

        # Get remote origin URL (stable across clones)
        returncode, stdout, _ = _run_git_command(
            ["config", "--get", "remote.origin.url"], cwd=repo_root
        )
        if returncode == 0 and stdout.strip():
            fingerprint_parts.append(stdout.strip())

        # Get first commit SHA (stable identifier for the repo)
        returncode, stdout, _ = _run_git_command(
            ["rev-list", "--max-parents=0", "HEAD"], cwd=repo_root
        )
        if returncode == 0 and stdout.strip():
            # Use first commit if multiple roots
            first_commit = stdout.strip().split("\n")[0]
            fingerprint_parts.append(first_commit)

        if fingerprint_parts:
            combined = ":".join(fingerprint_parts)
            return hashlib.sha256(combined.encode()).hexdigest()[:16]

    # Fallback: hash the absolute path
    abs_path = str(repo_root.resolve())
    return hashlib.sha256(abs_path.encode()).hexdigest()[:16]


def _get_repo_state_hash(repo_root: Path) -> str:
    """Generate a hash of the current repo state including uncommitted changes.

    For git repos: HEAD SHA + hash of diff output + untracked source files
    For non-git: hash of (filepath, size, mtime) tuples for all source files

    This is fast because it doesn't read file contents for unchanged files.

    Args:
        repo_root: Repository root path.

    Returns:
        A 16-character hex state hash string.
    """
    import hashlib

    # Check if this is a git repo
    git_dir = repo_root / ".git"
    if git_dir.exists():
        state_parts = []

        # Get current HEAD SHA
        returncode, stdout, _ = _run_git_command(["rev-parse", "HEAD"], cwd=repo_root)
        if returncode == 0 and stdout.strip():
            state_parts.append(stdout.strip())

        # Get diff of tracked files (staged + unstaged changes)
        returncode, stdout, _ = _run_git_command(
            ["diff", "HEAD"], cwd=repo_root, timeout=30
        )
        if returncode == 0:
            # Hash the diff output
            diff_hash = hashlib.sha256(stdout.encode()).hexdigest()[:8]
            state_parts.append(f"diff:{diff_hash}")

        # Get untracked source files (sorted for determinism)
        returncode, stdout, _ = _run_git_command(
            ["ls-files", "--others", "--exclude-standard"], cwd=repo_root, timeout=30
        )
        if returncode == 0 and stdout.strip():
            # Include mtime of untracked files for change detection
            untracked_info = []
            for line in sorted(stdout.strip().split("\n")):
                file_path = repo_root / line
                if file_path.exists() and file_path.is_file():
                    try:
                        stat = file_path.stat()
                        untracked_info.append(f"{line}:{stat.st_size}:{stat.st_mtime}")
                    except OSError:  # pragma: no cover
                        pass
            if untracked_info:
                untracked_hash = hashlib.sha256(
                    "\n".join(untracked_info).encode()
                ).hexdigest()[:8]
                state_parts.append(f"untracked:{untracked_hash}")

        if state_parts:
            combined = ":".join(state_parts)
            return hashlib.sha256(combined.encode()).hexdigest()[:16]

    # Non-git fallback: hash (path, size, mtime) for all source files
    # This is slower but works for any directory
    file_info = []
    source_extensions = {
        ".py", ".js", ".ts", ".jsx", ".tsx", ".java", ".go", ".rs", ".rb",
        ".c", ".cpp", ".h", ".hpp", ".cs", ".php", ".swift", ".kt", ".scala",
    }
    for f in sorted(repo_root.rglob("*")):
        if f.is_file() and f.suffix in source_extensions:
            # Skip common non-source directories
            rel_parts = f.relative_to(repo_root).parts
            if any(p.startswith(".") or p in ("node_modules", "venv", "__pycache__")
                   for p in rel_parts):
                continue
            try:
                stat = f.stat()
                rel_path = str(f.relative_to(repo_root))
                file_info.append(f"{rel_path}:{stat.st_size}:{stat.st_mtime}")
            except OSError:  # pragma: no cover
                pass

    if file_info:
        combined = "\n".join(file_info)
        return hashlib.sha256(combined.encode()).hexdigest()[:16]

    # Empty directory fallback
    return hashlib.sha256(str(repo_root.resolve()).encode()).hexdigest()[:16]


def _get_xdg_cache_base() -> Path:
    """Get the XDG cache base directory for hypergumbo.

    Returns ~/.cache/hypergumbo/ following XDG Base Directory Specification.
    Uses XDG_CACHE_HOME if set, otherwise defaults to ~/.cache.

    Returns:
        Path to the hypergumbo cache base directory.
    """
    xdg_cache = os.environ.get("XDG_CACHE_HOME")
    if xdg_cache:
        base = Path(xdg_cache)
    else:
        base = Path.home() / ".cache"

    return base / "hypergumbo"


def _get_cache_dir(repo_root: Path) -> Path:
    """Get or create the embedding cache directory for a repository.

    Cache structure:
        ~/.cache/hypergumbo/<fingerprint>/embeddings/

    Embeddings are shared across all repo states since they're keyed by
    file content hash. Only the results are state-specific.

    Args:
        repo_root: Repository root path.

    Returns:
        Path to the embeddings cache directory.
    """
    fingerprint = _get_repo_fingerprint(repo_root)
    cache_base = _get_xdg_cache_base()
    cache_dir = cache_base / fingerprint / "embeddings"

    # Create the full path including parent directories
    cache_dir.mkdir(parents=True, exist_ok=True)
    return cache_dir


def _get_results_cache_dir(repo_root: Path) -> Path:
    """Get or create the results cache directory for current repo state.

    Cache structure:
        ~/.cache/hypergumbo/<fingerprint>/results/<state_hash>/

    Results are cached per-state because they depend on the entire repo
    contents. The state hash changes when any file is modified.

    Args:
        repo_root: Repository root path.

    Returns:
        Path to the results cache directory for current state.
    """
    fingerprint = _get_repo_fingerprint(repo_root)
    state_hash = _get_repo_state_hash(repo_root)
    cache_base = _get_xdg_cache_base()
    cache_dir = cache_base / fingerprint / "results" / state_hash

    # Create the full path including parent directories
    cache_dir.mkdir(parents=True, exist_ok=True)
    return cache_dir


def _compute_file_hash(file_path: Path) -> str:
    """Compute hash of file content for cache invalidation.

    Args:
        file_path: Path to the file.

    Returns:
        Short SHA256 hash of file content, or empty string on error.
    """
    import hashlib

    try:
        content = file_path.read_bytes()
        return hashlib.sha256(content).hexdigest()[:16]
    except OSError:
        return ""


def _load_cached_embedding(cache_dir: Path, file_hash: str) -> "np.ndarray | None":
    """Load embedding from cache if it exists.

    Args:
        cache_dir: Path to cache directory.
        file_hash: Content hash of the file.

    Returns:
        Cached embedding array, or None if not cached.
    """
    if not file_hash:
        return None

    cache_file = cache_dir / f"embed_{file_hash}.npy"
    if cache_file.exists():
        try:
            import numpy as np
            return np.load(cache_file)
        except Exception:
            return None
    return None


def _save_cached_embedding(
    cache_dir: Path, file_hash: str, embedding: "np.ndarray"
) -> None:
    """Save embedding to cache.

    Args:
        cache_dir: Path to cache directory.
        file_hash: Content hash of the file.
        embedding: Embedding array to cache.
    """
    if not file_hash:
        return

    cache_file = cache_dir / f"embed_{file_hash}.npy"
    try:
        import numpy as np
        np.save(cache_file, embedding)
    except Exception:
        pass  # Silently fail if caching doesn't work


def _extract_file_samples(
    file_path: Path,
    num_samples: int = 3,
    sample_size: int = 400,
) -> str:
    """Extract random non-overlapping substrings from first third of file.

    Used to create a representative sample of file content for embedding.
    Strips HTML tags but keeps text content.

    Args:
        file_path: Path to the file.
        num_samples: Number of samples to extract.
        sample_size: Character count per sample.

    Returns:
        Ellipsis-concatenated string of samples, de-HTMLified.
    """
    import random
    import re

    try:
        content = file_path.read_text(encoding='utf-8', errors='replace')
    except OSError:
        return ""

    if not content:
        return ""

    # Use first third of file (most likely to contain description/overview)
    first_third_len = len(content) // 3
    if first_third_len < 100:
        first_third = content  # File is very small, use all of it
    else:
        first_third = content[:first_third_len]

    # De-HTMLify: remove HTML tags but keep content
    first_third = re.sub(r'<[^>]+>', ' ', first_third)
    # Collapse whitespace
    first_third = ' '.join(first_third.split())

    total_needed = sample_size * num_samples
    if len(first_third) <= total_needed:
        return first_third

    # Extract non-overlapping samples with deterministic seeding
    # Use file path hash for reproducibility (not cryptographic, just for stability)
    seed = hash(str(file_path)) % (2**32)
    rng = random.Random(seed)  # noqa: S311 # nosec B311

    samples = []
    available_start = 0

    for _ in range(num_samples):
        # Calculate max start position leaving room for remaining samples
        remaining_samples = num_samples - len(samples) - 1
        max_start = len(first_third) - sample_size - (sample_size * remaining_samples)

        if available_start >= max_start:
            break

        start = rng.randint(available_start, max_start)
        samples.append(first_third[start:start + sample_size])
        available_start = start + sample_size

    return " ... ".join(samples)


def _load_modernbert_model():
    """Load ModernBERT model with truncation dimension.

    Returns:
        SentenceTransformer model configured for 256-dim output.
    """
    from sentence_transformers import SentenceTransformer

    logging.getLogger("sentence_transformers").setLevel(logging.ERROR)
    return SentenceTransformer(
        _MODERNBERT_MODEL_NAME,
        truncate_dim=_MODERNBERT_TRUNCATE_DIM
    )


def _get_additional_files_probe_embeddings() -> "np.ndarray":
    """Get probe embeddings for 5W1H Additional Files ranking.

    Uses pre-computed embeddings from _embedding_data.py if available,
    otherwise computes them at runtime.

    Returns:
        Normalized probe embeddings array of shape (6, 256).
    """
    global _ADDITIONAL_FILES_PROBE_EMBEDDINGS

    if _ADDITIONAL_FILES_PROBE_EMBEDDINGS is None:
        try:
            # Try to load pre-computed embeddings
            from ._embedding_data import ADDITIONAL_FILES_PROBES_B64
            import base64
            import numpy as np

            raw = base64.b64decode(ADDITIONAL_FILES_PROBES_B64)
            arr = np.frombuffer(raw, dtype=np.float16).reshape(
                len(ADDITIONAL_FILES_PROBES), _MODERNBERT_TRUNCATE_DIM
            )
            _ADDITIONAL_FILES_PROBE_EMBEDDINGS = arr.astype(np.float32)
        except (ImportError, AttributeError):
            # Pre-computed embeddings not available, compute at runtime
            if not _has_sentence_transformers():
                import numpy as np
                return np.zeros((len(ADDITIONAL_FILES_PROBES), _MODERNBERT_TRUNCATE_DIM))

            model = _load_modernbert_model()
            import numpy as np
            _ADDITIONAL_FILES_PROBE_EMBEDDINGS = model.encode(
                ADDITIONAL_FILES_PROBES, convert_to_numpy=True
            )

    return _ADDITIONAL_FILES_PROBE_EMBEDDINGS


def embed_file_for_semantic_ranking(
    file_path: Path,
    cache_dir: Path | None = None,
) -> "np.ndarray | None":
    """Embed a file using ModernBERT for semantic ranking.

    Uses 3 random non-overlapping 800-char substrings from first third,
    de-HTMLified and ellipsis-concatenated.

    Args:
        file_path: Path to the file.
        cache_dir: Optional cache directory for embeddings.

    Returns:
        256-dimensional embedding vector, or None if unavailable.
    """
    if not _has_sentence_transformers():
        return None

    # Check cache first
    file_hash = _compute_file_hash(file_path)
    if cache_dir and file_hash:
        cached = _load_cached_embedding(cache_dir, file_hash)
        if cached is not None:
            return cached

    # Extract samples
    sample_text = _extract_file_samples(file_path)
    if not sample_text:
        return None

    # Load model and embed
    model = _load_modernbert_model()
    embedding = model.encode(sample_text, convert_to_numpy=True)

    # Cache result
    if cache_dir and file_hash:
        _save_cached_embedding(cache_dir, file_hash, embedding)

    return embedding


def batch_embed_files(
    file_paths: list[Path],
    cache_dir: Path | None = None,
    batch_size: int = 64,
    progress_callback: "callable | None" = None,
) -> dict[Path, "np.ndarray | None"]:
    """Batch embed multiple files efficiently.

    This is ~5-10x faster than calling embed_file_for_semantic_ranking()
    repeatedly because SentenceTransformers is optimized for batch encoding.

    Args:
        file_paths: List of file paths to embed.
        cache_dir: Optional cache directory for embeddings.
        batch_size: Number of files to encode per batch (default 64).
        progress_callback: Optional callback(current, total) for progress.

    Returns:
        Dict mapping file paths to embeddings (or None for unreadable files).
    """
    if not _has_sentence_transformers():
        return dict.fromkeys(file_paths, None)

    results: dict[Path, "np.ndarray | None"] = {}
    uncached: list[tuple[Path, str, str]] = []  # (path, hash, sample)

    # Phase 1: Check cache, extract samples for uncached files
    for f in file_paths:
        file_hash = _compute_file_hash(f)

        # Check cache first
        if cache_dir and file_hash:
            cached = _load_cached_embedding(cache_dir, file_hash)
            if cached is not None:
                results[f] = cached
                continue

        # Extract sample for uncached file
        sample = _extract_file_samples(f)
        if sample:
            uncached.append((f, file_hash, sample))
        else:
            results[f] = None

    # Phase 2: Batch encode uncached files
    if uncached:
        model = _load_modernbert_model()
        total_uncached = len(uncached)

        for i in range(0, total_uncached, batch_size):
            batch = uncached[i:i + batch_size]
            texts = [sample for _, _, sample in batch]

            # Batch encode
            embeddings = model.encode(texts, convert_to_numpy=True)

            # Store results and cache
            for (f, file_hash, _), emb in zip(batch, embeddings, strict=True):
                results[f] = emb
                if cache_dir and file_hash:
                    _save_cached_embedding(cache_dir, file_hash, emb)

            # Report progress
            if progress_callback:
                done = min(i + batch_size, total_uncached)
                progress_callback(done, total_uncached)

    return results


def compute_5w1h_similarity(
    file_embedding: "np.ndarray",
    probe_embeddings: "np.ndarray | None" = None,
) -> float:
    """Compute aggregate cosine similarity to 5W1H probes.

    Args:
        file_embedding: 256-dim embedding of file content.
        probe_embeddings: Pre-computed probe embeddings (optional).

    Returns:
        Aggregate similarity score (mean of cosine similarities).
    """
    import numpy as np

    if probe_embeddings is None:
        probe_embeddings = _get_additional_files_probe_embeddings()

    if probe_embeddings is None or len(probe_embeddings) == 0:
        return 0.0

    # Normalize embeddings
    file_norm = np.linalg.norm(file_embedding)
    if file_norm < 1e-8:
        return 0.0
    file_normalized = file_embedding / file_norm

    probe_norms = np.linalg.norm(probe_embeddings, axis=1, keepdims=True)
    probe_norms = np.maximum(probe_norms, 1e-8)  # Avoid division by zero
    probe_normalized = probe_embeddings / probe_norms

    # Compute cosine similarities
    similarities = np.dot(probe_normalized, file_normalized)

    # Return mean similarity
    return float(np.mean(similarities))
