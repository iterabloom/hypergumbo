"""Embedding-based utilities for sketch generation.

This module contains optional sentence-transformers-based functionality
for semantic operations in sketch generation. It's separated from
sketch.py to allow coverage to be measured independently when the heavy
dependencies (sentence-transformers, torch) aren't available.

The main entry points are:
- extract_readme_description_embedding(): Extract project description from README
- rank_files_by_embedding_similarity(): Rank Additional Files by semantic relevance
- batch_embed_files(): Batch embed files with caching

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

    The warning is suppressed by setting log level BEFORE importing/loading,
    and by capturing any stdout output during initialization.
    """
    # Suppress warnings BEFORE importing to catch all submodule loggers
    logging.getLogger("sentence_transformers").setLevel(logging.ERROR)
    logging.getLogger("sentence_transformers.SentenceTransformer").setLevel(logging.ERROR)

    from sentence_transformers import SentenceTransformer
    import sys
    import io

    # Capture any stdout during model loading (library prints to stdout)
    old_stdout = sys.stdout
    sys.stdout = io.StringIO()
    try:
        model = SentenceTransformer(_EMBEDDING_MODEL)
    finally:
        sys.stdout = old_stdout
    return model

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
    min_quality_threshold: float = 0.12,
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

    # Merge header lines with their following content
    # Headers alone are structural, not descriptive - expand them to include
    # the paragraph that follows, similar to license chunk expansion.
    merged_lines: list[tuple[int, str]] = []
    i = 0
    while i < len(filtered_lines):
        idx, line = filtered_lines[i]
        if line.startswith("#"):
            # This is a header - merge with following non-header lines
            merged_content = [line]
            j = i + 1
            # Include following lines until we hit another header or run out
            while j < len(filtered_lines):
                next_idx, next_line = filtered_lines[j]
                if next_line.startswith("#"):
                    break  # Stop at next header
                merged_content.append(next_line)
                j += 1
            # Only include the merged chunk if we found content after the header
            if len(merged_content) > 1:
                merged_lines.append((idx, " ".join(merged_content)))
                i = j  # Skip the lines we merged
            else:
                # Header with no following content - skip it entirely
                i += 1
        else:
            # Non-header line - keep as-is
            merged_lines.append((idx, line))
            i += 1

    filtered_lines = merged_lines

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
        # Title-proximity bonus for early lines (positions 0-4)
        # Headers are merged with their following content, so position 0 is
        # typically the title+description chunk. We boost early positions
        # since descriptions usually appear near the top of READMEs.
        title_bonus = np.ones(len(filtered_lines))
        title_bonus[0:5] = 1.25  # 25% boost for lines 0-4
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

    # Reject low-quality descriptions (score below threshold)
    # This prevents non-descriptive content like "pip install foo" from being
    # selected as descriptions when there's no real description paragraph.
    if prev_best_score < min_quality_threshold:
        if debug:
            return ReadmeExtractionDebug(
                description=None, k_scores=k_scores, final_k=final_k,
                stopped_early=stopped_early, quality_drop=quality_drop_value,
                elapsed_seconds=_time.time() - start_time,
                lines_processed=len(filtered_lines)
            )
        return None

    # Extract the winning lines
    start_idx, end_idx = best_window
    selected_lines = [line for _, line in filtered_lines[start_idx:end_idx]]

    # Join and return
    description = " ".join(selected_lines)

    # Handle Markdown soft line breaks: if the description ends mid-sentence
    # (no sentence-ending punctuation), try to extend it with following lines
    # until we reach a sentence boundary or run out of content.
    sentence_endings = (".", "!", "?", ":")
    max_extension_lines = 5  # Limit how far we extend
    extended_count = 0
    current_end_idx = end_idx

    while (
        description
        and not description.rstrip().rstrip(")]}").endswith(sentence_endings)
        and current_end_idx < len(filtered_lines)
        and extended_count < max_extension_lines
    ):
        next_line = filtered_lines[current_end_idx][1]
        # Stop if we hit a header or section break
        if next_line.startswith("#") or next_line.startswith("---"):
            break
        description = description + " " + next_line
        current_end_idx += 1
        extended_count += 1

    # Cleanup: strip HTML tags and excessive whitespace
    import re
    # Remove HTML tags but keep content
    description = re.sub(r"<[^>]+>", "", description)
    # Collapse whitespace
    description = " ".join(description.split())

    # Strip leading markdown header if present (from merged header+content)
    # Headers were merged with content for selection, but the header text
    # itself shouldn't appear in the final elevator pitch.
    if description.startswith("#"):
        # Match header patterns:
        # - "## catatonit ## A container init..." -> "A container init..."
        # - "# Title Some content..." -> "Some content..."
        # The first alternative handles "## Word(s) ##" (closing hash) format
        # The second alternative handles simple "# " prefix
        header_match = re.match(r"^(#+\s+\S+\s*#+\s*|#+\s+)", description)
        if header_match:
            description = description[header_match.end():].lstrip()

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
    # Suppress warnings BEFORE importing to catch all submodule loggers
    logging.getLogger("sentence_transformers").setLevel(logging.ERROR)
    logging.getLogger("sentence_transformers.SentenceTransformer").setLevel(logging.ERROR)

    from sentence_transformers import SentenceTransformer
    import sys
    import io

    # Capture any stdout during model loading (library prints to stdout)
    old_stdout = sys.stdout
    sys.stdout = io.StringIO()
    try:
        model = SentenceTransformer(
            _MODERNBERT_MODEL_NAME,
            truncate_dim=_MODERNBERT_TRUNCATE_DIM
        )
    finally:
        sys.stdout = old_stdout
    return model


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
