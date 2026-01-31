"""Command-line interface for hypergumbo.

This module provides the main entry point for the hypergumbo CLI, handling
argument parsing and dispatching to the appropriate command handlers.

How It Works
------------
The CLI uses argparse with subcommands for different operations:

- **sketch** (default): Generate token-budgeted Markdown overview
- **run**: Execute full analysis and output behavior map JSON
- **slice**: Extract subgraph from an entry point
- **catalog**: List available analysis passes
- **build-grammars**: Build Lean/Wolfram tree-sitter grammars from source

When no subcommand is given, sketch mode is assumed. This makes the
common case (`hypergumbo .`) as simple as possible.

The `run` command orchestrates all language analyzers and cross-language
linkers, collecting their results into a unified behavior map. Analyzers
run in sequence: Python, HTML, JS/TS, PHP, C, Java. Linkers (JNI, IPC)
run after all analyzers complete to create cross-language edges.

Why This Design
---------------
- Subcommand dispatch keeps each operation isolated and testable
- Default sketch mode optimizes for the common "quick overview" use case
- run_behavior_map() is separate from cmd_run() for testability
- Helper functions (_node_from_dict, _edge_from_dict) enable slice
  to work with previously-generated JSON files
"""
import argparse
import gc
import json
import math
import os
import resource
import sys
from pathlib import Path
from typing import Any, Dict, Optional

from rich.console import Console
from rich.table import Table

from . import __version__
from .analyze.all_analyzers import run_all_analyzers
from .catalog import get_default_catalog, is_available, suggest_passes_for_languages
from .linkers.registry import LinkerContext, run_all_linkers
# Import linker modules to trigger @register_linker decoration (side effect imports)
import hypergumbo_core.linkers.database_query as _database_query_linker  # noqa: F401
import hypergumbo_core.linkers.dependency as _dependency_linker  # noqa: F401
import hypergumbo_core.linkers.event_sourcing as _event_sourcing_linker  # noqa: F401
import hypergumbo_core.linkers.graphql as _graphql_linker  # noqa: F401
import hypergumbo_core.linkers.graphql_resolver as _graphql_resolver_linker  # noqa: F401
import hypergumbo_core.linkers.grpc as _grpc_linker  # noqa: F401
import hypergumbo_core.linkers.http as _http_linker  # noqa: F401
import hypergumbo_core.linkers.ipc as _ipc_linker  # noqa: F401
import hypergumbo_core.linkers.jni as _jni_linker  # noqa: F401
import hypergumbo_core.linkers.message_queue as _message_queue_linker  # noqa: F401
import hypergumbo_core.linkers.openapi as _openapi_linker  # noqa: F401
import hypergumbo_core.linkers.phoenix_ipc as _phoenix_ipc_linker  # noqa: F401
import hypergumbo_core.linkers.route_handler as _route_handler_linker  # noqa: F401
import hypergumbo_core.linkers.subprocess_cli as _subprocess_linker  # noqa: F401
import hypergumbo_core.linkers.swift_objc as _swift_objc_linker  # noqa: F401
import hypergumbo_core.linkers.websocket as _websocket_linker  # noqa: F401
import hypergumbo_core.linkers.inheritance as _inheritance_linker  # noqa: F401
import hypergumbo_core.linkers.type_hierarchy as _type_hierarchy_linker  # noqa: F401
from .entrypoints import detect_entrypoints
from .ir import Symbol, Edge, Span
from .metrics import compute_metrics
from .profile import detect_profile
from .schema import new_behavior_map
from .sketch import generate_sketch, ConfigExtractionMode, SketchStats, display_representativeness_table
from .slice import SliceQuery, slice_graph, AmbiguousEntryError, rank_slice_nodes
from .supply_chain import classify_file, detect_package_roots
from .ranking import (
    rank_symbols, _is_test_path, compute_transitive_test_coverage,
    compute_symbol_mention_centrality_batch, compute_raw_in_degree,
)
from .compact import (
    CompactConfig,
    format_compact_behavior_map,
    format_tiered_behavior_map,
    generate_tier_filename,
    parse_tier_spec,
    DEFAULT_TIERS,
)
from .build_grammars import build_all_grammars, check_grammar_availability
from .framework_patterns import (
    enrich_symbols,
    get_frameworks_dir,
    resolve_deferred_symbol_refs,
)


def _log_memory(label: str) -> None:  # pragma: no cover
    """Log current memory usage if HG_MEMORY_DEBUG is set.

    Uses resource.getrusage to get max RSS (resident set size).
    Output format: "MEMORY: <label>: <MB> MB"

    Only logs if HG_MEMORY_DEBUG environment variable is set.
    """
    if not os.environ.get("HG_MEMORY_DEBUG"):
        return
    # ru_maxrss is in KB on Linux, bytes on macOS
    rss_kb = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    # Normalize to MB (Linux uses KB, macOS uses bytes)
    rss_mb = rss_kb / 1024 if sys.platform != "darwin" else rss_kb / (1024 * 1024)
    print(f"MEMORY: {label}: {rss_mb:.1f} MB", file=sys.stderr)


def _find_git_root(start_path: Path) -> Optional[Path]:
    """Find the git repository root by walking up from start_path.

    Args:
        start_path: Directory to start searching from.

    Returns:
        Path to git root (directory containing .git), or None if not in a git repo.
    """
    current = start_path.resolve()
    while current != current.parent:
        if (current / ".git").exists():
            return current
        current = current.parent
    # Check root directory too (only possible at filesystem root like /)
    if (current / ".git").exists():  # pragma: no cover
        return current  # pragma: no cover
    return None


def _discover_input_file(repo_root: Path) -> Optional[Path]:
    """Auto-discover behavior map file from cache or repo root.

    Search order:
    1. Cache directory: ~/.cache/hypergumbo/<fingerprint>/results/<state>/
    2. Repo root: <repo>/hypergumbo.results.json

    This enables seamless workflow where 'hypergumbo run .' (which caches results)
    is automatically discovered by search/explain/routes/slice/symbols commands.

    Args:
        repo_root: Repository root path.

    Returns:
        Path to behavior map file if found, None otherwise.
    """
    # First, check cache directory (where 'hypergumbo run' saves by default)
    try:
        from .sketch_embeddings import _get_results_cache_dir

        cache_dir = _get_results_cache_dir(repo_root)
        cached_file = cache_dir / "hypergumbo.results.json"
        if cached_file.exists():
            return cached_file
    except Exception:  # pragma: no cover - cache discovery errors
        pass

    # Fall back to repo root (for explicit --out or legacy workflows)
    repo_file = repo_root / "hypergumbo.results.json"
    if repo_file.exists():
        return repo_file

    return None


def _get_or_run_analysis(
    repo_root: Path,
    explicit_input: Optional[str] = None,
    show_progress: bool = True,
) -> tuple[Optional[Path], bool, list[Path]]:
    """Get cached behavior map or run analysis if needed.

    Provides seamless auto-analysis: commands that need a behavior map will
    automatically run 'hypergumbo run' if no cached results exist.

    Args:
        repo_root: Repository root path.
        explicit_input: Explicit --input path (takes precedence).
        show_progress: Whether to show progress during analysis.

    Returns:
        Tuple of (input_path, was_cached, generated_artifacts):
        - input_path: Path to behavior map file, or None if explicit_input not found
        - was_cached: True if using cached results, False if freshly generated
        - generated_artifacts: List of generated file paths (empty if cached)
    """
    # If explicit --input provided, use it directly
    if explicit_input:
        input_path = Path(explicit_input)
        if not input_path.exists():
            return None, False, []
        return input_path, True, []  # Treat explicit input as "cached"

    # Try to discover cached results
    cached_path = _discover_input_file(repo_root)
    if cached_path is not None:
        return cached_path, True, []

    # No cached results - run analysis
    print(
        "[hypergumbo] No cached results found, running analysis...",
        file=sys.stderr,
    )

    generated_files = run_behavior_map(
        repo_root=repo_root,
        out_path=None,  # Use default cache location
        progress=show_progress,
    )

    # Now discover the newly created results
    new_path = _discover_input_file(repo_root)
    if new_path is None:  # pragma: no cover - shouldn't happen
        return None, False, generated_files

    return new_path, False, generated_files


def _print_output_summary(
    command: str,
    artifacts: list[Path] | None = None,
    stdout_output: bool = False,
    file: Any = None,
    embeddings_dir: Path | None = None,
    cached_artifacts: set[Path] | None = None,
) -> None:
    """Print consistent output summary at end of command execution.

    Always prints as the last thing, even if no artifacts generated.

    Args:
        command: The hypergumbo subcommand name (e.g., "sketch", "run")
        artifacts: List of generated file paths (None or empty for stdout-only)
        stdout_output: If True, indicate output went to stdout
        file: Output file (default: sys.stdout). Use sys.stderr for JSON output
            modes to avoid breaking JSON parsing.
        embeddings_dir: If provided, show where embeddings are cached.
        cached_artifacts: Set of artifact paths that were pre-existing (not freshly
            generated). These will be marked with "[cached]" in the output.
    """
    if file is None:
        file = sys.stdout

    cached_set = cached_artifacts or set()
    generated_count = 0
    cached_count = 0

    if artifacts:
        for artifact_path in artifacts:
            if artifact_path in cached_set:
                cached_count += 1
            else:
                generated_count += 1

    # Build summary line
    parts = []
    if generated_count > 0:
        parts.append(f"Generated {generated_count}")
    if cached_count > 0:
        parts.append(f"Using {cached_count} cached")
    if not parts:
        parts.append("Generated 0 artifact(s)")

    print(f"\n[hypergumbo {command}] {', '.join(parts)}", file=file)

    if artifacts:
        for artifact_path in artifacts:
            prefix = "[cached] " if artifact_path in cached_set else ""
            # Show full absolute path for clarity
            print(f"  {prefix}{artifact_path.resolve()}", file=file)
    if stdout_output:
        print("  Output: stdout", file=file)
    if embeddings_dir:  # pragma: no cover - only when embeddings available
        print(f"  Embeddings cached: {embeddings_dir.resolve()}", file=file)


def _generate_sketch_filename(
    tokens: int | None = None,
    exclude_tests: bool = False,
    with_source: bool = False,
) -> str:
    """Generate a descriptive filename for cached sketch.

    The filename encodes the token budget and non-default flags so users
    can easily find the right cached sketch.

    Examples:
        - sketch.md (no budget)
        - sketch.4000.md (4000 token budget)
        - sketch.16000.md (16000 token budget)
        - sketch.4000.notests.md (4000 tokens, exclude_tests=True)
        - sketch.4000.withsource.md (4000 tokens, with_source=True)
        - sketch.4000.notests.withsource.md (both flags)

    Args:
        tokens: Token budget (None for no budget).
        exclude_tests: Whether test files were excluded.
        with_source: Whether source content was included.

    Returns:
        Filename like "sketch.4000.notests.md"
    """
    parts = ["sketch"]

    if tokens is not None:
        parts.append(str(tokens))

    if exclude_tests:
        parts.append("notests")

    if with_source:
        parts.append("withsource")

    return ".".join(parts) + ".md"


def cmd_sketch(args: argparse.Namespace) -> int:
    """Generate token-budgeted Markdown sketch to stdout."""
    repo_root = Path(args.path).resolve()

    if not repo_root.exists():
        print(f"Error: path does not exist: {repo_root}", file=sys.stderr)
        return 1

    # Warn if analyzing a subdirectory of a git repo
    git_root = _find_git_root(repo_root)
    if git_root is not None and git_root.resolve() != repo_root.resolve():
        # Reconstruct command with original flags but new path
        cmd_parts = ["hypergumbo", "sketch"]
        if args.tokens:
            cmd_parts.extend(["-t", str(args.tokens)])
        if getattr(args, "exclude_tests", False):
            cmd_parts.append("-x")
        cmd_parts.append(str(git_root))
        suggested_cmd = " ".join(cmd_parts)
        print(
            f"NOTE: Your repo root appears to be at {git_root}\n"
            f"      You may want to run: {suggested_cmd}\n",
            file=sys.stderr,
        )

    # Default to 4000 tokens when -t not specified (unified behavior)
    max_tokens = args.tokens if args.tokens else 4000
    exclude_tests = getattr(args, "exclude_tests", False)
    first_party_priority = getattr(args, "first_party_priority", True)
    extra_excludes = getattr(args, "extra_excludes", [])
    verbose = getattr(args, "verbose", False)

    # Convert string mode to enum
    mode_str = getattr(args, "config_extraction_mode", "hybrid")
    config_mode = {
        "heuristic": ConfigExtractionMode.HEURISTIC,
        "embedding": ConfigExtractionMode.EMBEDDING,
        "hybrid": ConfigExtractionMode.HYBRID,
    }.get(mode_str, ConfigExtractionMode.HYBRID)

    # Get embedding-related parameters
    max_config_files = getattr(args, "max_config_files", 15)
    fleximax_lines = getattr(args, "fleximax_lines", 100)
    max_chunk_chars = getattr(args, "max_chunk_chars", 800)
    language_proportional = getattr(args, "language_proportional", False)
    show_progress = getattr(args, "progress", False)
    readme_debug = getattr(args, "readme_debug", False)
    with_source = getattr(args, "with_source", False)

    # Load cached results if --input is provided
    cached_results = None
    input_path = getattr(args, "input", None)
    if input_path:
        input_file = Path(input_path)
        if not input_file.exists():
            print(f"Error: Input file not found: {input_path}", file=sys.stderr)
            return 1
        cached_results = json.loads(input_file.read_text())

        # Warn if results file is older than any source files in repo
        results_mtime = input_file.stat().st_mtime
        newest_source_mtime = 0.0
        for ext in ["*.py", "*.js", "*.ts", "*.tsx", "*.go", "*.rs", "*.java"]:
            for src_file in repo_root.rglob(ext):
                try:
                    src_mtime = src_file.stat().st_mtime
                    if src_mtime > newest_source_mtime:
                        newest_source_mtime = src_mtime
                except OSError:  # pragma: no cover
                    continue
        if newest_source_mtime > results_mtime:
            print(
                f"NOTE: {input_path} may be stale (source files modified since).\n"
                f"      Run 'hypergumbo run' to regenerate.\n",
                file=sys.stderr,
            )

    # If --readme-debug, show README extraction debug info before sketch
    if readme_debug:
        from .sketch import _find_readme_path
        from .sketch_embeddings import extract_readme_description_embedding

        readme_path = _find_readme_path(repo_root)
        if readme_path:
            result = extract_readme_description_embedding(readme_path, debug=True)
            if result:
                print("README Extraction Debug:", file=sys.stderr)
                print(f"  Description: {result.description!r}", file=sys.stderr)
                print(f"  k-scores: {result.k_scores}", file=sys.stderr)
                print(f"  Final k: {result.final_k}", file=sys.stderr)
                print(f"  Stopped early: {result.stopped_early}", file=sys.stderr)
                if result.quality_drop is not None:
                    print(f"  Quality drop: {result.quality_drop:.1%}", file=sys.stderr)  # pragma: no cover - only set on early stop
                print(f"  Lines processed: {result.lines_processed}", file=sys.stderr)
                print(f"  Elapsed: {result.elapsed_seconds:.2f}s", file=sys.stderr)
                print(file=sys.stderr)
        else:
            print("README Extraction Debug: No README found", file=sys.stderr)

    # Get cache directory for artifact discovery
    from .sketch_embeddings import _get_results_cache_dir
    try:
        cache_dir = _get_results_cache_dir(repo_root)
    except Exception:  # pragma: no cover - cache discovery errors
        cache_dir = None

    # Snapshot existing results files BEFORE generating sketch
    # Any results files that existed before are "cached" (reused, not freshly generated)
    pre_existing_results: set[Path] = set()
    if cache_dir is not None:
        try:
            pre_existing_results = set(cache_dir.glob("hypergumbo.results*.json"))
        except Exception:  # pragma: no cover - cache discovery errors
            pass

    # Track stats for representativeness table (always enabled with default budget)
    stats = SketchStats()

    sketch = generate_sketch(
        repo_root,
        max_tokens=max_tokens,
        exclude_tests=exclude_tests,
        first_party_priority=first_party_priority,
        extra_excludes=extra_excludes,
        config_extraction_mode=config_mode,
        verbose=verbose,
        max_config_files=max_config_files,
        fleximax_lines=fleximax_lines,
        max_chunk_chars=max_chunk_chars,
        language_proportional=language_proportional,
        progress=show_progress,
        cached_results=cached_results,
        with_source=with_source,
        stats_out=stats,
    )
    print(sketch)

    # Generate 4x and 16x budget sketches for comparison table
    # Using 4x/16x (instead of 2x) reveals when large files start fitting
    if max_tokens and stats is not None:
        import tempfile

        budget_4x = max_tokens * 4
        budget_16x = max_tokens * 16

        stats_4x = SketchStats()
        stats_16x = SketchStats()

        # Generate 4x budget sketch
        sketch_4x = generate_sketch(
            repo_root,
            max_tokens=budget_4x,
            exclude_tests=exclude_tests,
            first_party_priority=first_party_priority,
            extra_excludes=extra_excludes,
            config_extraction_mode=config_mode,
            verbose=False,
            max_config_files=max_config_files,
            fleximax_lines=fleximax_lines,
            max_chunk_chars=max_chunk_chars,
            language_proportional=language_proportional,
            progress=False,
            cached_results=cached_results,
            with_source=with_source,
            stats_out=stats_4x,
        )

        # Generate 16x budget sketch
        sketch_16x = generate_sketch(
            repo_root,
            max_tokens=budget_16x,
            exclude_tests=exclude_tests,
            first_party_priority=first_party_priority,
            extra_excludes=extra_excludes,
            config_extraction_mode=config_mode,
            verbose=False,
            max_config_files=max_config_files,
            fleximax_lines=fleximax_lines,
            max_chunk_chars=max_chunk_chars,
            language_proportional=language_proportional,
            progress=False,
            cached_results=cached_results,
            with_source=with_source,
            stats_out=stats_16x,
        )

        display_representativeness_table(stats, stats_4x, stats_16x)

        # Save comparison sketches to temp files
        temp_dir = Path(tempfile.gettempdir()) / "hypergumbo_sketch_compare"
        temp_dir.mkdir(parents=True, exist_ok=True)

        sketch_4x_filename = _generate_sketch_filename(
            tokens=budget_4x,
            exclude_tests=exclude_tests,
            with_source=with_source,
        )
        sketch_16x_filename = _generate_sketch_filename(
            tokens=budget_16x,
            exclude_tests=exclude_tests,
            with_source=with_source,
        )

        temp_4x_path = temp_dir / sketch_4x_filename
        temp_16x_path = temp_dir / sketch_16x_filename
        temp_4x_path.write_text(sketch_4x)
        temp_16x_path.write_text(sketch_16x)

        # Show helpful message with copy commands
        if cache_dir is not None:
            cache_4x = cache_dir / sketch_4x_filename
            cache_16x = cache_dir / sketch_16x_filename
            print(
                f"\nhypergumbo also created comparison sketches temporarily:\n"
                f"  4x budget ({budget_4x:,}t):  {temp_4x_path}\n"
                f"  16x budget ({budget_16x:,}t): {temp_16x_path}\n"
                f"\nTo preserve them to cache:\n"
                f"  cp {temp_4x_path} {cache_4x}\n"
                f"  cp {temp_16x_path} {cache_16x}\n",
                file=sys.stderr,
            )

    # Cache the sketch to a file with descriptive name
    sketch_cache_path: Path | None = None
    if cache_dir is not None:
        try:
            sketch_filename = _generate_sketch_filename(
                tokens=max_tokens,
                exclude_tests=exclude_tests,
                with_source=with_source,
            )
            sketch_cache_path = cache_dir / sketch_filename
            sketch_cache_path.write_text(sketch)
        except Exception:  # pragma: no cover - cache write errors shouldn't break sketch
            sketch_cache_path = None

    # Gather artifacts that were generated
    artifacts: list[Path] = []
    embeddings_dir: Path | None = None

    if cache_dir is not None:
        try:
            # Find all results files in cache (both new and existing)
            results_after = set(cache_dir.glob("hypergumbo.results*.json"))
            for f in sorted(results_after):
                artifacts.append(f)

            # Add cached sketch file to artifacts
            if sketch_cache_path is not None and sketch_cache_path.exists():
                artifacts.append(sketch_cache_path)

            # Check for embeddings directory
            fingerprint_dir = cache_dir.parent.parent  # Go from results/<hash> to fingerprint
            embed_dir = fingerprint_dir / "embeddings"
            if embed_dir.exists() and any(embed_dir.iterdir()):  # pragma: no cover
                embeddings_dir = embed_dir  # only when embeddings cached
        except Exception:  # pragma: no cover - cache inspection errors
            pass

    # Output summary (always to stdout at the end)
    # Mark results files that existed before sketch generation as cached
    _print_output_summary(
        "sketch",
        artifacts=artifacts if artifacts else None,
        stdout_output=True,
        embeddings_dir=embeddings_dir,
        cached_artifacts=pre_existing_results,
    )

    return 0


def cmd_run(args: argparse.Namespace) -> int:
    # The positional argument for `run` is called `path` in the parser below.
    repo_root = Path(args.path).resolve()
    out_path = Path(args.out) if args.out else None
    max_tier = getattr(args, "max_tier", None)
    max_files = getattr(args, "max_files", None)
    compact = getattr(args, "compact", False)
    coverage = getattr(args, "coverage", 0.8)
    connectivity = not getattr(args, "no_connectivity", False)
    budgets = getattr(args, "budgets", None)
    extra_excludes = getattr(args, "extra_excludes", [])
    frameworks = getattr(args, "frameworks", None)
    show_progress = getattr(args, "progress", True)

    generated_files = run_behavior_map(
        repo_root=repo_root,
        out_path=out_path,
        max_tier=max_tier,
        max_files=max_files,
        compact=compact,
        coverage=coverage,
        connectivity=connectivity,
        budgets=budgets,
        extra_excludes=extra_excludes,
        frameworks=frameworks,
        progress=show_progress,
    )

    # Output summary (always at the end)
    _print_output_summary("run", artifacts=generated_files)

    return 0


def _node_from_dict(d: Dict[str, Any]) -> Symbol:
    """Reconstruct a Symbol from its dict representation."""
    span_data = d.get("span", {})
    span = Span(
        start_line=span_data.get("start_line", 0),
        end_line=span_data.get("end_line", 0),
        start_col=span_data.get("start_col", 0),
        end_col=span_data.get("end_col", 0),
    )
    return Symbol(
        id=d["id"],
        name=d["name"],
        kind=d["kind"],
        language=d["language"],
        path=d["path"],
        span=span,
        origin=d.get("origin", ""),
        origin_run_id=d.get("origin_run_id", ""),
        stable_id=d.get("stable_id"),
        shape_id=d.get("shape_id"),
        meta=d.get("meta"),  # Preserve metadata for entrypoint detection
        supply_chain_tier=d.get("supply_chain_tier", 1),  # Default tier 1 (first-party)
    )


def _edge_from_dict(d: Dict[str, Any]) -> Edge:
    """Reconstruct an Edge from its dict representation."""
    meta = d.get("meta", {})
    return Edge(
        id=d["id"],
        src=d["src"],
        dst=d["dst"],
        edge_type=d["type"],
        line=d.get("line", 0),
        confidence=d.get("confidence", 0.85),
        origin=d.get("origin", ""),
        origin_run_id=d.get("origin_run_id", ""),
        evidence_type=meta.get("evidence_type", "unknown"),
    )


def _format_symbol_display_name(node: Dict[str, Any] | None, fallback_id: str = "") -> str:
    """Format a symbol for display, handling file-level symbols gracefully.

    File-level symbols (kind="file") represent module-level code (imports,
    top-level statements). Instead of showing the raw symbol ID like
    "python:/path/to/file.py:1-1:file:file", we show "<module level>".

    Args:
        node: The symbol node dict, or None if not found.
        fallback_id: The raw symbol ID to use as fallback.

    Returns:
        A human-readable display name for the symbol.
    """
    if node is None:
        # Node not found - check if fallback_id looks like a file-level symbol
        # Format: {lang}:{path}:{start}-{end}:{kind}:{name}
        if fallback_id.endswith(":file:file"):
            return "<module level>"
        return fallback_id

    kind = node.get("kind", "")
    name = node.get("name", "")

    # File-level symbols have kind="file" and name="file"
    if kind == "file" and name == "file":
        return "<module level>"

    # Normal symbol - use the name, falling back to ID if empty
    return name if name else fallback_id


def _extract_path_from_symbol_id(symbol_id: str) -> str:
    """Extract the file path from a symbol ID.

    Symbol ID format: {lang}:{path}:{start}-{end}:{kind}:{name}
    Example: python:/home/user/project/src/main.py:1-10:foo:function

    Args:
        symbol_id: The full symbol ID string.

    Returns:
        The file path extracted from the ID, or empty string if parsing fails.
    """
    if not symbol_id:
        return ""

    # Split on first colon to separate language from rest
    parts = symbol_id.split(":", 1)
    if len(parts) < 2:
        return ""

    rest = parts[1]  # Everything after "lang:"

    # The path ends before the line range (e.g., ":1-10:")
    # Find the pattern ":digits-digits:" from the end
    import re
    match = re.search(r":(\d+-\d+):[^:]+:[^:]+$", rest)
    if match:
        # Everything before the match is the path
        return rest[: match.start()]

    return ""


def _sanitize_filename_part(s: str, max_len: int = 50) -> str:
    """Sanitize a string for use in a filename.

    Replaces unsafe characters and truncates to max_len.
    """
    import re
    # Replace non-alphanumeric (except dash, underscore, dot) with underscore
    safe = re.sub(r"[^a-zA-Z0-9_.-]", "_", s)
    # Collapse multiple underscores
    safe = re.sub(r"_+", "_", safe)
    # Strip leading/trailing underscores
    safe = safe.strip("_")
    return safe[:max_len] if safe else "unnamed"


def cmd_slice(args: argparse.Namespace) -> int:
    """Execute the slice command."""
    path_arg = Path(args.path).resolve()
    out_path_arg = args.out  # Keep as string to detect if default was used

    # Smart detection: if path is a .json file, treat it as --input automatically
    # This provides better UX: `hypergumbo slice results.json` just works
    if path_arg.suffix == ".json" and path_arg.is_file() and not args.input:
        args.input = str(path_arg)
        # Use parent directory as repo_root (or cwd if file is in cwd)
        repo_root = path_arg.parent if path_arg.parent != Path.cwd() else Path.cwd()
    else:
        repo_root = path_arg

    # Get or run analysis (auto-runs if no cached results)
    input_path, was_cached, generated_files = _get_or_run_analysis(
        repo_root,
        explicit_input=args.input,
        show_progress=True,
    )
    if input_path is None:
        print(f"Error: Input file not found: {args.input}", file=sys.stderr)
        return 1

    behavior_map = json.loads(input_path.read_text())

    # Reconstruct Symbol and Edge objects from the behavior map
    nodes = [_node_from_dict(n) for n in behavior_map.get("nodes", [])]
    edges = [_edge_from_dict(e) for e in behavior_map.get("edges", [])]

    # Handle --list-entries: show detected entrypoints and exit
    if args.list_entries:
        entrypoints = detect_entrypoints(nodes, edges)

        # Apply --exclude-tests and --max-tier filters to entrypoint list
        # Build lookup from symbol_id to Symbol for filtering
        symbol_lookup = {node.id: node for node in nodes}
        exclude_tests = getattr(args, "exclude_tests", False)
        max_tier = getattr(args, "max_tier", None)

        filtered_entrypoints = []
        for ep in entrypoints:
            sym = symbol_lookup.get(ep.symbol_id)
            if sym is None:
                continue  # pragma: no cover - symbol should exist

            # Filter out test files if --exclude-tests
            if exclude_tests and sym.path and _is_test_path(sym.path):
                continue

            # Filter out entries with tier > max_tier if --max-tier set
            if max_tier is not None and sym.supply_chain_tier > max_tier:
                continue

            filtered_entrypoints.append(ep)

        if not filtered_entrypoints:
            filter_msg = ""
            if exclude_tests:
                filter_msg += " (--exclude-tests active)"
            if max_tier is not None:
                filter_msg += f" (--max-tier {max_tier} active)"
            print(f"[hypergumbo slice] No entrypoints detected{filter_msg}")
        else:
            filter_msg = ""
            if exclude_tests:
                filter_msg += " [excluding tests]"
            if max_tier is not None:
                filter_msg += f" [max-tier {max_tier}]"
            print(f"[hypergumbo slice] Detected {len(filtered_entrypoints)} entrypoint(s){filter_msg}:")
            for ep in filtered_entrypoints:
                print(f"  [{ep.kind.value}] {ep.label} (confidence: {ep.confidence:.2f})")
                print(f"    {ep.symbol_id}")
        _print_output_summary("slice --list-entries", stdout_output=True)
        return 0

    # Handle --entry auto: use detected entrypoints
    entry = args.entry
    if entry == "auto":
        entrypoints = detect_entrypoints(nodes, edges)
        if not entrypoints:
            print("Error: No entrypoints detected. Use --entry to specify manually.",
                  file=sys.stderr)
            return 1

        # Score entries by both confidence and graph connectivity
        # Well-connected entries produce richer slices
        edge_src_counts: Dict[str, int] = {}
        for e in edges:
            edge_src_counts[e.src] = edge_src_counts.get(e.src, 0) + 1

        def entry_score(ep: Any) -> float:
            """Score = confidence * connectivity_boost.

            connectivity_boost = 1 + log(1 + outgoing_edges)
            This favors well-connected entries while still respecting confidence.
            """
            out_edges = edge_src_counts.get(ep.symbol_id, 0)
            connectivity_boost = 1 + math.log(1 + out_edges)
            return ep.confidence * connectivity_boost

        best = max(entrypoints, key=entry_score)
        entry = best.symbol_id
        out_edges = edge_src_counts.get(entry, 0)
        print(f"[hypergumbo slice] Auto-detected entry: {best.label}")
        print(f"  {entry}")
        if out_edges > 0:
            print(f"  (selected for connectivity: {out_edges} outgoing edges)")

    # Generate output path with entry name if using default
    # This prevents accidental overwrites when slicing different symbols
    if out_path_arg == "slice.json":
        # Extract short name from entry (e.g., "main" from "python:src/main.py:1-5:main:function")
        entry_parts = entry.split(":")
        short_name = entry_parts[-2] if len(entry_parts) >= 2 else entry_parts[0]
        safe_name = _sanitize_filename_part(short_name)
        out_path = Path(f"slice.{safe_name}.json")
    else:
        out_path = Path(out_path_arg)

    # Build slice query
    max_tier = getattr(args, "max_tier", None)
    query = SliceQuery(
        entrypoint=entry,
        max_hops=args.max_hops,
        max_files=args.max_files,
        min_confidence=args.min_confidence,
        exclude_tests=args.exclude_tests,
        reverse=args.reverse,
        max_tier=max_tier,
        language=args.language,
    )

    # Perform slice
    try:
        result = slice_graph(nodes, edges, query)
    except AmbiguousEntryError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1

    # Rank slice nodes by importance (centrality + tier weighting)
    ranked_node_ids = rank_slice_nodes(result, nodes, edges, first_party_priority=True)

    # Build output with ranked node ordering
    feature_dict = result.to_dict()
    feature_dict["node_ids"] = ranked_node_ids  # Replace with ranked order

    # --flat implies --inline (need full objects for external tools)
    use_inline = getattr(args, "inline", False) or getattr(args, "flat", False)

    # If --inline (or --flat), include full node/edge objects for self-contained output
    if use_inline:
        # Filter nodes and edges from behavior map to include only those in slice
        node_ids_set = set(result.node_ids)
        edge_ids_set = set(result.edge_ids)

        # Build lookup for ordering inline nodes by rank
        node_rank = {nid: i for i, nid in enumerate(ranked_node_ids)}

        # Get inline nodes and sort by rank
        inline_nodes = [
            n for n in behavior_map.get("nodes", [])
            if n.get("id") in node_ids_set
        ]
        inline_nodes.sort(key=lambda n: node_rank.get(n.get("id", ""), 999999))
        feature_dict["nodes"] = inline_nodes

        feature_dict["edges"] = [
            e for e in behavior_map.get("edges", [])
            if e.get("id") in edge_ids_set
        ]

    # If --flat, output simple structure (nodes/edges at top level)
    # Otherwise, use standard wrapper structure
    if getattr(args, "flat", False):
        output = {
            "nodes": feature_dict["nodes"],
            "edges": feature_dict["edges"],
        }
    else:
        output = {
            "schema_version": behavior_map.get("schema_version", "0.1.0"),
            "view": "slice",
            "feature": feature_dict,
        }

    # Write output
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(output, indent=2))

    mode = "reverse" if args.reverse else "forward"
    print(f"[hypergumbo slice] Wrote {mode} slice to {out_path}")
    print(f"  entry: {entry}")
    print(f"  nodes: {len(result.node_ids)}")
    print(f"  edges: {len(result.edge_ids)}")
    if result.limits_hit:
        print(f"  limits hit: {', '.join(result.limits_hit)}")

    # Output summary (always at the end)
    cached_set = {input_path} if was_cached else set()
    # Include generated analysis files + the slice output
    all_artifacts = generated_files + [input_path, out_path] if not was_cached else [input_path, out_path]
    _print_output_summary("slice", artifacts=all_artifacts, cached_artifacts=cached_set)

    return 0


def cmd_search(args: argparse.Namespace) -> int:
    """Search for symbols by name pattern."""
    repo_root = Path(args.path).resolve()

    # Get or run analysis (auto-runs if no cached results)
    input_path, was_cached, generated_files = _get_or_run_analysis(
        repo_root,
        explicit_input=args.input,
        show_progress=True,
    )
    if input_path is None:
        print(f"Error: Input file not found: {args.input}", file=sys.stderr)
        return 1

    # Load behavior map
    behavior_map = json.loads(input_path.read_text())
    nodes = behavior_map.get("nodes", [])

    # Search pattern (case-insensitive substring match)
    pattern = args.pattern.lower()
    matches = []

    for node in nodes:
        name = node.get("name", "")
        # Check if pattern matches name (fuzzy substring match)
        if pattern in name.lower():
            # Apply filters
            if args.kind and node.get("kind") != args.kind:
                continue
            if args.language and node.get("language") != args.language:
                continue
            matches.append(node)

    # Apply limit
    if args.limit and len(matches) > args.limit:
        matches = matches[: args.limit]

    # Output results
    if not matches:
        print(f"No symbols found matching '{args.pattern}'")
        return 0

    print(f"Found {len(matches)} symbol(s) matching '{args.pattern}':\n")
    for node in matches:
        name = _format_symbol_display_name(node, node.get("id", ""))
        kind = node.get("kind", "")
        lang = node.get("language", "")
        path = node.get("path", "")
        span = node.get("span", {})
        line = span.get("start_line", 0)

        print(f"  {name} ({kind})")
        print(f"    {path}:{line}")
        print(f"    language: {lang}")
        print()

    # Output summary (always at the end)
    cached_set = {input_path} if was_cached else set()
    artifacts = generated_files + [input_path] if not was_cached else [input_path]
    _print_output_summary(
        "search",
        artifacts=artifacts,
        stdout_output=True,
        cached_artifacts=cached_set,
    )
    return 0


# HTTP methods that indicate API routes
HTTP_METHODS = {"get", "post", "put", "patch", "delete", "head", "options"}


def cmd_routes(args: argparse.Namespace) -> int:
    """Display API routes/endpoints from the behavior map."""
    repo_root = Path(args.path).resolve()

    # Get or run analysis (auto-runs if no cached results)
    input_path, was_cached, generated_files = _get_or_run_analysis(
        repo_root,
        explicit_input=args.input,
        show_progress=True,
    )
    if input_path is None:
        print(f"Error: Input file not found: {args.input}", file=sys.stderr)
        return 1

    # Load behavior map
    behavior_map = json.loads(input_path.read_text())
    nodes = behavior_map.get("nodes", [])

    # Find route handlers - symbols with HTTP method markers in stable_id
    # or route concepts in meta.concepts
    routes: list[dict] = []
    for node in nodes:
        is_route = False

        # Check for route concept in meta.concepts (FRAMEWORK_PATTERNS enrichment)
        # Routes are ONLY detected via concepts - no fallback to legacy fields.
        # If routes aren't showing up, check that YAML patterns are being applied.
        meta = node.get("meta") or {}
        concepts = meta.get("concepts", [])
        for concept in concepts:
            if isinstance(concept, dict) and concept.get("concept") == "route":
                is_route = True
                break

        if is_route:
            # Apply language filter
            if args.language and node.get("language") != args.language:
                continue
            routes.append(node)

    if not routes:
        print("No API routes found in the behavior map.")
        cached_set = {input_path} if was_cached else set()
        artifacts = generated_files + [input_path] if not was_cached else [input_path]
        _print_output_summary(
            "routes", artifacts=artifacts, stdout_output=True, cached_artifacts=cached_set
        )
        return 0

    # Group routes by path
    routes_by_path: dict[str, list[dict]] = {}
    for route in routes:
        path = route.get("path", "unknown")
        if path not in routes_by_path:
            routes_by_path[path] = []
        routes_by_path[path].append(route)

    # Output routes grouped by file
    total_routes = len(routes)
    print(f"Found {total_routes} API route(s):\n")

    for file_path in sorted(routes_by_path.keys()):
        file_routes = routes_by_path[file_path]
        print(f"{file_path}:")
        for route in file_routes:
            name = route.get("name", "")
            span = route.get("span", {})
            line = span.get("start_line", 0)
            meta = route.get("meta", {}) or {}

            # Extract route info from concept metadata (YAML pattern enrichment)
            # No fallback to legacy fields - if enrichment fails, routes should
            # appear with missing info to make the failure visible.
            route_path = None
            method = None
            concepts = meta.get("concepts", [])
            for concept in concepts:
                if isinstance(concept, dict) and concept.get("concept") == "route":
                    route_path = concept.get("path")
                    method = concept.get("method")
                    break

            method = method.upper() if method else ""
            if route_path:
                # Normalize: ensure paths start with /
                # (defense-in-depth; framework_patterns already normalizes)
                if route_path and not route_path.startswith("/"):  # pragma: no cover
                    route_path = "/" + route_path
                print(f"  [{method}] {route_path} -> {name} (line {line})")
            else:
                print(f"  [{method}] {name} (line {line})")
        print()

    # Output summary (always at the end)
    cached_set = {input_path} if was_cached else set()
    artifacts = generated_files + [input_path] if not was_cached else [input_path]
    _print_output_summary(
        "routes", artifacts=artifacts, stdout_output=True, cached_artifacts=cached_set
    )
    return 0


def _extract_source_lines(
    repo_root: Path,
    rel_path: str,
    start_line: int,
    end_line: int,
) -> Optional[str]:
    """Extract source code lines from a file.

    Args:
        repo_root: Repository root directory.
        rel_path: Relative path to the source file.
        start_line: Starting line number (1-indexed).
        end_line: Ending line number (1-indexed, inclusive).

    Returns:
        Source code as string, or None if file not found/unreadable.
    """
    file_path = repo_root / rel_path
    if not file_path.exists():
        return None

    try:
        lines = file_path.read_text(errors="replace").splitlines()
        # Convert to 0-indexed, handle out-of-range
        start_idx = max(0, start_line - 1)
        end_idx = min(len(lines), end_line)
        if start_idx >= len(lines):
            return None  # pragma: no cover - out-of-range line numbers
        return "\n".join(lines[start_idx:end_idx])
    except (OSError, IOError):  # pragma: no cover - rare I/O errors
        return None  # pragma: no cover


def _estimate_tokens(text: str) -> int:
    """Estimate token count using shared heuristic from token_budget module.

    Delegates to the shared implementation for consistency across
    sketch, explain, and other commands.

    Args:
        text: Source text to estimate.

    Returns:
        Estimated token count.
    """
    from .selection.token_budget import estimate_tokens as _shared_estimate_tokens
    return _shared_estimate_tokens(text)


def cmd_explain(args: argparse.Namespace) -> int:
    """Explain a symbol with its callers and callees."""
    repo_root = Path(args.path).resolve()

    # Get or run analysis (auto-runs if no cached results)
    input_path, was_cached, generated_files = _get_or_run_analysis(
        repo_root,
        explicit_input=args.input,
        show_progress=True,
    )
    if input_path is None:
        print(f"Error: Input file not found: {args.input}", file=sys.stderr)
        return 1

    # Load behavior map
    behavior_map = json.loads(input_path.read_text())
    nodes = behavior_map.get("nodes", [])
    edges = behavior_map.get("edges", [])

    # Build lookup tables
    nodes_by_id = {n["id"]: n for n in nodes}

    # Compute in-degree for sorting callers/callees by importance
    in_degree: dict[str, int] = {n["id"]: 0 for n in nodes}
    for edge in edges:
        dst = edge.get("dst", "")
        if dst in in_degree:
            in_degree[dst] += 1

    # Get flags
    exclude_tests = getattr(args, "exclude_tests", False)
    with_source = getattr(args, "with_source", False)
    token_budget = getattr(args, "tokens", None)

    # Find matching symbols (case-insensitive exact match on name)
    pattern = args.symbol.lower()
    matches = [n for n in nodes if n.get("name", "").lower() == pattern]

    if not matches:
        print(f"Error: No symbol found matching '{args.symbol}'", file=sys.stderr)
        return 1

    # Display each match
    for i, node in enumerate(matches):
        if i > 0:
            print("\n" + "=" * 60 + "\n")

        symbol_id = node.get("id", "")
        name = node.get("name", "")
        kind = node.get("kind", "")
        lang = node.get("language", "")
        path = node.get("path", "")
        span = node.get("span", {})
        start_line = span.get("start_line", 0)
        end_line = span.get("end_line", 0)

        print(f"{name} ({kind})")
        print(f"  Location: {path}:{start_line}-{end_line}")
        print(f"  Language: {lang}")

        # Show complexity and LOC if available
        complexity = node.get("cyclomatic_complexity")
        loc = node.get("lines_of_code")
        if complexity is not None or loc is not None:
            metrics = []
            if complexity is not None:
                metrics.append(f"complexity: {complexity}")
            if loc is not None:
                metrics.append(f"lines: {loc}")
            print(f"  Metrics: {', '.join(metrics)}")

        # Show supply chain info if available
        supply_chain = node.get("supply_chain", {})
        if supply_chain:
            tier_name = supply_chain.get("tier_name", "")
            reason = supply_chain.get("reason", "")
            if tier_name:
                sc_info = tier_name
                if reason:
                    sc_info += f" ({reason})"
                print(f"  Supply chain: {sc_info}")

        # Track sources shown for deduplication in with_source mode
        sources_shown: set[str] = set()
        tokens_used = 0

        # In with_source mode, show source for queried symbol first
        if with_source:
            symbol_source = _extract_source_lines(repo_root, path, start_line, end_line)
            if symbol_source:
                source_tokens = _estimate_tokens(symbol_source)
                # Always show queried symbol's source (reserve budget)
                if token_budget is None or tokens_used + source_tokens <= token_budget:
                    print(f"\n  Source ({path}:{start_line}-{end_line}):")
                    for line in symbol_source.splitlines():
                        print(f"    {line}")
                    tokens_used += source_tokens
                    sources_shown.add(symbol_id)
                else:
                    # Even with budget, always show queried symbol
                    print(f"\n  Source ({path}:{start_line}-{end_line}):")
                    for line in symbol_source.splitlines():
                        print(f"    {line}")
                    tokens_used += source_tokens
                    sources_shown.add(symbol_id)
            else:
                print(f"\n  [Source unavailable: {path}]")

        # Find callers (edges where dst = this symbol)
        # Tuple: (in_degree, name, path, line, src_id, src_node) - in_degree for sorting
        callers: list[tuple[int, str, str, int, str, Optional[Dict[str, Any]]]] = []
        for edge in edges:
            if edge.get("dst") == symbol_id:
                src_id = edge.get("src", "")
                src_node = nodes_by_id.get(src_id)
                src_name = _format_symbol_display_name(src_node, src_id)
                # Extract path from node, or fall back to parsing the symbol ID
                src_path = (
                    src_node.get("path", "")
                    if src_node
                    else _extract_path_from_symbol_id(src_id)
                )
                # Skip test files if --exclude-tests
                if exclude_tests and _is_test_path(src_path):
                    continue
                src_line = edge.get("line", 0)
                src_in_degree = in_degree.get(src_id, 0)
                callers.append((src_in_degree, src_name, src_path, src_line, src_id, src_node))

        # Sort callers by in-degree (descending), then by name for stability
        callers.sort(key=lambda x: (-x[0], x[1]))

        # Find callees (edges where src = this symbol)
        # Tuple: (in_degree, name, path, line, dst_id, dst_node) - in_degree for sorting
        callees: list[tuple[int, str, str, int, str, Optional[Dict[str, Any]]]] = []
        for edge in edges:
            if edge.get("src") == symbol_id:
                dst_id = edge.get("dst", "")
                dst_node = nodes_by_id.get(dst_id)
                dst_name = _format_symbol_display_name(dst_node, dst_id)
                # Extract path from node, or fall back to parsing the symbol ID
                dst_path = (
                    dst_node.get("path", "")
                    if dst_node
                    else _extract_path_from_symbol_id(dst_id)
                )
                # Skip test files if --exclude-tests
                if exclude_tests and _is_test_path(dst_path):
                    continue
                edge_line = edge.get("line", 0)
                dst_in_degree = in_degree.get(dst_id, 0)
                callees.append((dst_in_degree, dst_name, dst_path, edge_line, dst_id, dst_node))

        # Sort callees by in-degree (descending), then by name for stability
        callees.sort(key=lambda x: (-x[0], x[1]))

        # In with_source mode, prepare all source items first for budget calculation
        # Each item: (in_degree, symbol_id, display_name, path, start, end, is_module_level, source, tokens)
        caller_source_items: list[tuple[int, str, str, str, int, int, bool, str, int]] = []
        callee_source_items: list[tuple[int, str, str, str, int, int, bool, str, int]] = []

        if with_source:
            # Track IDs added to caller list for deduplication
            caller_ids_added: set[str] = set()

            # Prepare caller source items
            for caller_in_degree, caller_name, caller_path, caller_line, caller_id, caller_node in callers:
                if caller_id in sources_shown:
                    continue
                is_module_level = (
                    caller_node is not None and caller_node.get("kind") == "file"
                ) or caller_id.endswith(":file:file")

                if is_module_level:
                    start, end = caller_line, caller_line
                elif caller_node:
                    caller_span = caller_node.get("span", {})
                    start = caller_span.get("start_line", 0)
                    end = caller_span.get("end_line", 0)
                    if not (start and end):  # pragma: no cover
                        continue
                else:  # pragma: no cover
                    continue

                source = _extract_source_lines(repo_root, caller_path, start, end)
                if source:
                    tokens = _estimate_tokens(source)
                    caller_source_items.append((
                        caller_in_degree, caller_id, caller_name, caller_path,
                        start, end, is_module_level, source, tokens
                    ))
                    caller_ids_added.add(caller_id)

            # Prepare callee source items (skip if already in caller list)
            for callee_in_degree, callee_name, callee_path, callee_line, callee_id, callee_node in callees:
                if callee_id in sources_shown or callee_id in caller_ids_added:
                    continue
                is_module_level = (
                    callee_node is not None and callee_node.get("kind") == "file"
                ) or callee_id.endswith(":file:file")

                if is_module_level:
                    start, end = callee_line, callee_line
                elif callee_node:
                    callee_span = callee_node.get("span", {})
                    start = callee_span.get("start_line", 0)
                    end = callee_span.get("end_line", 0)
                    if not (start and end):  # pragma: no cover
                        continue
                else:  # pragma: no cover
                    continue

                source = _extract_source_lines(repo_root, callee_path, start, end)
                if source:
                    tokens = _estimate_tokens(source)
                    callee_source_items.append((
                        callee_in_degree, callee_id, callee_name, callee_path,
                        start, end, is_module_level, source, tokens
                    ))

            # Determine which items to show based on budget
            # Omission order: module-level first, then ascending in-degree (least important first)
            all_items = caller_source_items + callee_source_items
            items_to_show: set[str] = set()  # symbol IDs to show

            if token_budget is None:
                # No budget - show all
                items_to_show = {item[1] for item in all_items}
            else:
                remaining_budget = token_budget - tokens_used
                total_tokens = sum(item[8] for item in all_items)

                if total_tokens <= remaining_budget:
                    # All fit - show all
                    items_to_show = {item[1] for item in all_items}
                else:
                    # Need to omit some. Start with all items, then omit one at a time
                    # in omission order until we fit in budget.
                    # Omission order: module-level first, then ascending in-degree
                    items_to_show = {item[1] for item in all_items}
                    current_total = total_tokens

                    sorted_for_omission = sorted(
                        all_items,
                        key=lambda x: (not x[6], x[0])  # module-level first, then in-degree asc
                    )

                    for item in sorted_for_omission:
                        if current_total <= remaining_budget:
                            break
                        # Omit this item
                        items_to_show.discard(item[1])
                        current_total -= item[8]

        # Display callers
        print()
        if callers:
            print(f"  Called by ({len(callers)}):")
            for _, caller_name, caller_path, caller_line, _, _ in callers:
                print(f"    - {caller_name} ({caller_path}:{caller_line})")
        else:
            print("  Called by: (none)")

        # Show caller sources (after Called by list)
        if with_source and caller_source_items:
            caller_module_omitted = 0
            caller_regular_omitted = 0
            for _item_in_degree, item_id, item_name, item_path, item_start, item_end, is_mod, source, _ in caller_source_items:
                if item_id not in items_to_show:
                    if is_mod:
                        caller_module_omitted += 1
                    else:
                        caller_regular_omitted += 1
                    continue

                loc_str = f"{item_path}:{item_start}" if item_start == item_end else f"{item_path}:{item_start}-{item_end}"
                label = "(module level) " if is_mod else ""
                print(f"\n  Source for {item_name} {label}({loc_str}):")
                for line in source.splitlines():
                    print(f"    {line}")
                sources_shown.add(item_id)

            if caller_module_omitted > 0:
                print(f"\n  [{caller_module_omitted} module-level call(s) omitted for brevity]")
            if caller_regular_omitted > 0:
                print(f"\n  [{caller_regular_omitted} caller source(s) omitted for brevity]")

        # Display callees
        print()
        if callees:
            print(f"  Calls ({len(callees)}):")
            for _, callee_name, callee_path, callee_line, _, _ in callees:
                print(f"    - {callee_name} ({callee_path}:{callee_line})")
        else:
            print("  Calls: (none)")

        # Show callee sources (after Calls list)
        if with_source and callee_source_items:
            callee_module_omitted = 0
            callee_regular_omitted = 0
            for _item_in_degree, item_id, item_name, item_path, item_start, item_end, is_mod, source, _ in callee_source_items:
                if item_id not in items_to_show:
                    if is_mod:
                        callee_module_omitted += 1
                    else:
                        callee_regular_omitted += 1
                    continue

                loc_str = f"{item_path}:{item_start}" if item_start == item_end else f"{item_path}:{item_start}-{item_end}"
                label = "(module level) " if is_mod else ""
                print(f"\n  Source for {item_name} {label}({loc_str}):")
                for line in source.splitlines():
                    print(f"    {line}")
                sources_shown.add(item_id)

            if callee_module_omitted > 0:
                print(f"\n  [{callee_module_omitted} module-level call(s) omitted for brevity]")
            if callee_regular_omitted > 0:
                print(f"\n  [{callee_regular_omitted} callee source(s) omitted for brevity]")

    # Output summary (always at the end)
    cached_set = {input_path} if was_cached else set()
    artifacts = generated_files + [input_path] if not was_cached else [input_path]
    _print_output_summary(
        "explain", artifacts=artifacts, stdout_output=True, cached_artifacts=cached_set
    )
    return 0


def _is_large_directory(path: Path, max_entries: int = 200) -> bool:
    """Check if a directory has too many entries for quick scanning.

    Returns True if the directory has more than max_entries immediate children
    (files + directories). This is a heuristic to avoid scanning $HOME or
    other very large directories.
    """
    try:
        count = 0
        for _ in path.iterdir():
            count += 1
            if count > max_entries:
                return True
        return False
    except (PermissionError, OSError):  # pragma: no cover
        return True  # Treat permission errors as "large" to skip scanning


def cmd_catalog(args: argparse.Namespace) -> int:
    """Display available passes and packs.

    Shows:
    1. Suggested passes based on current repo (if any source files found)
    2. All available passes (core and extra)
    3. Available packs (deprecated)
    4. Available framework YAML patterns (v1.1.x)
    """
    catalog = get_default_catalog()
    cwd = Path.cwd()

    # Check if this is a very large directory (e.g., $HOME) to avoid slow scans
    detected_languages: set[str] = set()
    if _is_large_directory(cwd):
        print("Note: Large directory detected - skipping language suggestions.")
        print("      Run from a specific project directory for suggestions.")
        print()
    else:
        # Detect repo profile using existing language detection
        # Use max_file_size to skip large files - catalog is just for quick hints,
        # not accurate analysis
        profile = detect_profile(cwd, max_file_size=100 * 1024)
        detected_languages = set(profile.languages.keys())

    # Show suggested passes based on detected languages
    suggested = suggest_passes_for_languages(detected_languages)
    if suggested:
        print("Suggested for current repo:")
        for p in suggested:
            avail = is_available(p)
            status = "" if avail else " [not installed]"
            print(f"  - {p.id}: {p.description}{status}")
        print()

    # Show all passes (default behavior now)
    print("Available Passes:")
    for p in catalog.passes:
        avail = is_available(p)
        status = "" if avail else " [not installed]"
        if p.availability == "core":
            print(f"  - {p.id} (core): {p.description}{status}")
        else:
            print(f"  - {p.id} (extra): {p.description}{status}")

    # Show available framework YAML patterns (v1.1.x)
    print()
    print("Available Framework Patterns (v1.1.x):")
    print("  Use --frameworks to specify which patterns to apply.")
    frameworks_dir = get_frameworks_dir()
    if frameworks_dir.exists():
        yaml_files = sorted(frameworks_dir.glob("*.yaml"))
        for yaml_file in yaml_files:
            name = yaml_file.stem
            print(f"  - {name}")
    else:  # pragma: no cover - frameworks dir always exists in installed package
        print("  (No framework patterns found)")

    # Note about deprecated packs (suppress warning for now)
    print()
    print("Note: Packs are deprecated. Use --frameworks instead for semantic")
    print("      detection of routes, controllers, tasks, etc.")

    # Output summary (always at the end)
    _print_output_summary("catalog", stdout_output=True)
    return 0


def cmd_build_grammars(args: argparse.Namespace) -> int:
    """Build tree-sitter grammars from source (Lean, Wolfram)."""
    if args.check:
        # Just check availability
        status = check_grammar_availability()
        all_available = all(status.values())

        print("Grammar availability:")
        for name, available in status.items():
            symbol = "✓" if available else "✗"
            print(f"  {symbol} tree-sitter-{name}")

        if not all_available:
            print("\nRun 'hypergumbo build-grammars' to build missing grammars.")
            return 1
        return 0

    # Build grammars
    results = build_all_grammars(quiet=args.quiet)

    if all(results.values()):
        return 0
    else:
        failed = [name for name, ok in results.items() if not ok]
        print(f"\nFailed to build: {', '.join(failed)}", file=sys.stderr)
        return 1


def cmd_symbols(args: argparse.Namespace) -> int:
    """Display symbol catalog with connectivity information.

    Shows a table of symbols sorted by file connectivity (total degree of
    symbols in each file), then by filename, then by individual symbol degree.

    Uses Rich for auto-adjusting column widths and proper text wrapping.
    """
    repo_root = Path(args.path).resolve()

    # Get or run analysis (auto-runs if no cached results)
    input_path, was_cached, generated_files = _get_or_run_analysis(
        repo_root,
        explicit_input=args.input,
        show_progress=True,
    )
    if input_path is None:
        print(f"Error: Input file not found: {args.input}", file=sys.stderr)
        return 1

    # Load behavior map
    behavior_map = json.loads(input_path.read_text())
    nodes = behavior_map.get("nodes", [])
    edges = behavior_map.get("edges", [])

    # Build node ID set and ID->path mapping for filtering
    node_ids = {n["id"] for n in nodes}
    node_paths: dict[str, str] = {n["id"]: n.get("path", "") for n in nodes}

    # Check exclude_tests flag before computing degrees
    exclude_tests = getattr(args, "exclude_tests", False)

    # Compute in-degree and out-degree for each node
    # When exclude_tests is set, skip edges where src or dst is a test path
    in_degree: dict[str, int] = {n["id"]: 0 for n in nodes}
    out_degree: dict[str, int] = {n["id"]: 0 for n in nodes}

    for edge in edges:
        src = edge.get("src", "")
        dst = edge.get("dst", "")

        # If excluding tests, skip edges involving test files
        if exclude_tests:
            src_path = node_paths.get(src, _extract_path_from_symbol_id(src))
            dst_path = node_paths.get(dst, _extract_path_from_symbol_id(dst))
            if _is_test_path(src_path) or _is_test_path(dst_path):
                continue

        if src in node_ids:
            out_degree[src] = out_degree.get(src, 0) + 1
        if dst in node_ids:
            in_degree[dst] = in_degree.get(dst, 0) + 1

    # Build list of symbols with their degrees
    # Tuple: (name, kind, in_degree, out_degree, total_degree, path)
    symbol_rows: list[tuple[str, str, int, int, int, str]] = []

    for node in nodes:
        node_id = node["id"]
        name = _format_symbol_display_name(node, node_id)
        kind = node.get("kind", "")
        path = node.get("path", "")
        lang = node.get("language", "")
        ind = in_degree.get(node_id, 0)
        outd = out_degree.get(node_id, 0)
        degree = ind + outd

        # Apply filters
        if args.kind and kind != args.kind:
            continue
        if args.language and lang != args.language:
            continue
        if exclude_tests and _is_test_path(path):
            continue

        symbol_rows.append((name, kind, ind, outd, degree, path))

    # Compute total degree per file (invisible column for sorting)
    file_total_degree: dict[str, int] = {}
    for _name, _kind, ind, outd, _degree, path in symbol_rows:
        file_total_degree[path] = file_total_degree.get(path, 0) + ind + outd

    # Sort by: total file degree (descending), filename, individual degree (descending)
    symbol_rows.sort(key=lambda r: (-file_total_degree.get(r[5], 0), r[5], -r[4]))

    # Apply --max-per-file limit if specified
    max_per_file = getattr(args, "max_per_file", None)
    if max_per_file is not None:
        file_counts: dict[str, int] = {}
        filtered_rows: list[tuple[str, str, int, int, int, str]] = []
        for row in symbol_rows:
            path = row[5]
            count = file_counts.get(path, 0)
            if count < max_per_file:
                filtered_rows.append(row)
                file_counts[path] = count + 1
        symbol_rows = filtered_rows

    # Output
    total_count = len(symbol_rows)
    limit = args.limit if not args.all else None

    if limit and total_count > limit:
        display_rows = symbol_rows[:limit]
        omitted = total_count - limit
    else:
        display_rows = symbol_rows
        omitted = 0

    if not display_rows:
        print("No symbols found.")
        cached_set = {input_path} if was_cached else set()
        artifacts = generated_files + [input_path] if not was_cached else [input_path]
        _print_output_summary(
            "symbols", artifacts=artifacts, stdout_output=True, cached_artifacts=cached_set
        )
        return 0

    # Create Rich table with auto-adjusting columns
    console = Console()
    table = Table(show_header=True, header_style="bold", box=None)

    # Add columns - Rich handles width automatically
    table.add_column("Symbol", style="cyan", no_wrap=False)
    table.add_column("Kind", style="green")
    table.add_column("In", justify="right", style="yellow")
    table.add_column("Out", justify="right", style="yellow")
    table.add_column("Deg", justify="right", style="bold yellow")
    table.add_column("File", style="dim", no_wrap=False)

    # Add rows
    for name, kind, ind, outd, degree, path in display_rows:
        table.add_row(name, kind, str(ind), str(outd), str(degree), path)

    console.print(table)

    # Show omitted message
    if omitted > 0:
        console.print(
            f"\n[dim]{omitted} additional symbols omitted for brevity; "
            "run with --all to show them[/dim]"
        )

    # Output summary
    cached_set = {input_path} if was_cached else set()
    artifacts = generated_files + [input_path] if not was_cached else [input_path]
    _print_output_summary(
        "symbols", artifacts=artifacts, stdout_output=True, cached_artifacts=cached_set
    )
    return 0


def cmd_compact(args: argparse.Namespace) -> int:
    """Convert a behavior map to compact form with coverage-based truncation.

    Reads an existing behavior map JSON and outputs a compact version with:
    - Top symbols by centrality coverage
    - Summary of omitted symbols (bag-of-words, path patterns, kinds)
    - Induced subgraph edges (only edges between included symbols)

    This is useful for post-processing large behavior maps into LLM-friendly
    formats without re-running the full analysis.
    """
    input_path = Path(args.input).resolve()

    if not input_path.exists():
        print(f"Error: Input file not found: {input_path}", file=sys.stderr)
        return 1

    # Load behavior map
    behavior_map = json.loads(input_path.read_text())
    nodes = behavior_map.get("nodes", [])
    edges_data = behavior_map.get("edges", [])

    # Convert to Symbol and Edge objects for compact module
    symbols = [Symbol.from_dict(n) for n in nodes]
    edges = [Edge.from_dict(e) for e in edges_data]

    # Configure compact mode
    config = CompactConfig(
        target_coverage=args.coverage,
        max_symbols=args.max_symbols,
        min_symbols=args.min_symbols,
    )

    # Use connectivity-aware selection if not disabled
    connectivity_aware = not args.no_connectivity

    # Generate compact behavior map
    compact_map = format_compact_behavior_map(
        behavior_map, symbols, edges, config,
        force_include_entrypoints=True,
        connectivity_aware=connectivity_aware,
    )

    # Output
    out_path = Path(args.out).resolve() if args.out else None

    if out_path:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        with open(out_path, "w") as f:
            json.dump(compact_map, f, indent=2)
        print(f"Compact behavior map written to: {out_path}")
    else:
        print(json.dumps(compact_map, indent=2))

    return 0


def cmd_test_coverage(args: argparse.Namespace) -> int:
    """Estimate test coverage by analyzing which functions are called by tests.

    Identifies:
    - Hot spots: Functions called by many tests (potential redundancy)
    - Cold spots: Functions not called by any tests (need coverage)
    """
    repo_root = Path(args.path).resolve()

    # Get or run analysis (auto-runs if no cached results)
    input_path, was_cached, generated_files = _get_or_run_analysis(
        repo_root,
        explicit_input=args.input,
        show_progress=True,
    )
    if input_path is None:
        print(f"Error: Input file not found: {args.input}", file=sys.stderr)
        return 1

    # Load behavior map
    behavior_map = json.loads(input_path.read_text())
    nodes = behavior_map.get("nodes", [])
    edges = behavior_map.get("edges", [])

    # Build lookup tables
    nodes_by_id = {n["id"]: n for n in nodes}

    # Identify test symbols (functions/methods in test files)
    test_symbols: set[str] = set()
    for node in nodes:
        path = node.get("path", "")
        kind = node.get("kind", "")
        if _is_test_path(path) and kind in ("function", "method"):
            test_symbols.add(node["id"])

    # Identify non-test callable symbols (coverage targets)
    target_symbols: dict[str, dict] = {}
    for node in nodes:
        path = node.get("path", "")
        kind = node.get("kind", "")
        if not _is_test_path(path) and kind in ("function", "method"):
            target_symbols[node["id"]] = node

    if not target_symbols:
        print("No functions found to analyze.", file=sys.stderr)
        return 0

    # Extract call edges for transitive BFS
    call_edges = [
        (edge.get("src", ""), edge.get("dst", ""))
        for edge in edges
        if edge.get("type") == "calls"
    ]

    # Compute transitive test coverage using shared helper
    tests_per_target = compute_transitive_test_coverage(
        test_ids=test_symbols,
        target_ids=set(target_symbols.keys()),
        call_edges=call_edges,
    )

    # Compute metrics
    # test_dense: (test_density, test_count, loc, target, test_names)
    test_dense: list[tuple[float, int, int, dict, list[str]]] = []
    cold_spots: list[tuple[dict, int, int | None]] = []

    for target_id, test_ids in tests_per_target.items():
        target = target_symbols[target_id]
        test_count = len(test_ids)
        loc = target.get("lines_of_code") or 1  # Default to 1 to avoid division by zero

        if test_count == 0:
            # Cold spot - include LOC and complexity for prioritization
            complexity = target.get("cyclomatic_complexity")
            cold_spots.append((target, loc, complexity))
        else:
            # Tested function - calculate test density (tests per LOC)
            test_density = test_count / loc
            test_names = []
            for tid in test_ids:
                test_node = nodes_by_id.get(tid)
                test_names.append(_format_symbol_display_name(test_node, tid))
            test_dense.append((test_density, test_count, loc, target, test_names))

    # Sort hot spots by test density (descending) - tests per LOC
    test_dense.sort(key=lambda x: -x[0])

    # Sort cold spots by LOC (descending) - larger untested functions first
    cold_spots.sort(key=lambda x: -x[1])

    # Apply filters
    min_tests = args.min_tests
    max_tests = args.max_tests
    top_n = args.top

    if min_tests is not None:
        test_dense = [(d, c, loc, t, n) for d, c, loc, t, n in test_dense if c >= min_tests]
    if max_tests is not None:
        test_dense = [(d, c, loc, t, n) for d, c, loc, t, n in test_dense if c <= max_tests]

    # Compute summary stats
    total_functions = len(target_symbols)
    tested_functions = len([h for h in tests_per_target.values() if len(h) > 0])
    untested_functions = total_functions - tested_functions
    coverage_percent = (tested_functions / total_functions * 100) if total_functions > 0 else 0.0
    total_tests = len(test_symbols)

    # Output
    if args.format == "json":
        # JSON output
        output = {
            "schema_version": "0.1.0",
            "view": "test-coverage",
            "summary": {
                "total_functions": total_functions,
                "tested_functions": tested_functions,
                "untested_functions": untested_functions,
                "coverage_percent": round(coverage_percent, 1),
                "total_tests": total_tests,
            },
            "test_dense": [],
            "cold_spots": [],
        }

        for density, test_count, loc, target, test_names in test_dense[:top_n] if top_n else test_dense:
            span = target.get("span", {})
            output["test_dense"].append({
                "id": target["id"],
                "name": target.get("name", ""),
                "path": target.get("path", ""),
                "span": span,
                "test_count": test_count,
                "lines_of_code": loc,
                "test_density": round(density, 2),
                "tests": sorted(test_names),
            })

        for target, loc, complexity in cold_spots[:top_n] if top_n else cold_spots:
            span = target.get("span", {})
            entry: dict[str, object] = {
                "id": target["id"],
                "name": target.get("name", ""),
                "path": target.get("path", ""),
                "span": span,
                "test_count": 0,
            }
            if loc:
                entry["lines_of_code"] = loc
            if complexity:
                entry["cyclomatic_complexity"] = complexity
            output["cold_spots"].append(entry)

        print(json.dumps(output, indent=2))
    else:
        # Human-readable output
        print("Test Coverage Estimate")
        print("=" * 22)
        print(f"Total functions: {total_functions}")
        print(f"Tested: {tested_functions} ({coverage_percent:.1f}%)")
        print(f"Untested: {untested_functions}")
        print(f"Total test functions: {total_tests}")

        # Test-dense functions
        display_hot = test_dense[:top_n] if top_n else test_dense[:20]
        if display_hot:
            print("\nTest-Dense (highest test density - may indicate redundant tests)")
            print("-" * 48)
            for density, test_count, loc, target, _ in display_hot:
                name = _format_symbol_display_name(target, target.get("id", ""))
                path = target.get("path", "")
                span = target.get("span", {})
                start = span.get("start_line", 0)
                end = span.get("end_line", 0)
                print(f"  {density:5.2f} t/LOC  ({test_count:3} tests, {loc:3} LOC)  {path}:{start}-{end}  {name}()")

        # Cold spots
        display_cold = cold_spots[:top_n] if top_n else cold_spots[:20]
        if display_cold:
            print("\nCold Spots (untested - need coverage)")
            print("-" * 37)
            for target, loc, complexity in display_cold:
                name = _format_symbol_display_name(target, target.get("id", ""))
                path = target.get("path", "")
                span = target.get("span", {})
                start = span.get("start_line", 0)
                end = span.get("end_line", 0)
                metrics = []
                if loc:
                    metrics.append(f"{loc} LOC")
                if complexity:
                    metrics.append(f"complexity: {complexity}")
                metrics_str = f"  [{', '.join(metrics)}]" if metrics else ""
                print(f"  {0:3} tests  {path}:{start}-{end}  {name}(){metrics_str}")

        # Show if results were truncated
        if top_n and (len(test_dense) > top_n or len(cold_spots) > top_n):
            print(f"\n(Showing top {top_n}. Use --top to see more.)")

    # Output summary (to stderr for JSON mode to avoid breaking JSON parsing)
    summary_file = sys.stderr if args.format == "json" else None
    cached_set = {input_path} if was_cached else set()
    artifacts = generated_files + [input_path] if not was_cached else [input_path]
    _print_output_summary(
        "test-coverage",
        artifacts=artifacts,
        stdout_output=True,
        file=summary_file,
        cached_artifacts=cached_set,
    )
    return 0


def build_parser() -> argparse.ArgumentParser:
    # Main parser with comprehensive help
    main_description = """\
Generate codebase summaries for AI assistants and coding agents.

Quick start:
  hypergumbo .              Generate Markdown sketch (paste into ChatGPT/Claude)
  hypergumbo . -t 4000      Limit output to ~4000 tokens
  hypergumbo run .          Full JSON analysis for tooling

Workflow:
  Most users only need 'sketch' (the default). For deeper analysis:
  1. hypergumbo run .       → creates hypergumbo.results.json
  2. hypergumbo search X    → find symbols matching "X"
  3. hypergumbo explain X   → show callers/callees of symbol "X"
  4. hypergumbo slice       → extract subgraph from entry point"""

    main_epilog = """\
Examples:
  hypergumbo ~/myproject                    # Sketch with auto token budget
  hypergumbo ~/myproject -t 8000            # Sketch sized for 8k context
  hypergumbo . -t 4000 -x                   # Exclude test files
  hypergumbo run . --compact                # LLM-friendly JSON output
  hypergumbo slice --entry main --reverse   # Find what calls main()
  hypergumbo routes                         # List API endpoints

Token budget guidelines (for sketch):
  1000    Brief overview (structure only)
  4000    Good balance for most LLMs
  8000    Detailed with many symbols
  16000   Comprehensive (large codebases)

For more help on a command: hypergumbo <command> --help
For help on ALL commands:   hypergumbo --help --all"""

    p = argparse.ArgumentParser(
        prog="hypergumbo",
        description=main_description,
        epilog=main_epilog,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {__version__}",
        help="Print version and exit",
    )
    p.add_argument(
        "--debug",
        action="store_true",
        help="Enable debug logging (shows ripgrep vs Python fallback decisions, etc.)",
    )

    sub = p.add_subparsers(dest="command")

    # hypergumbo [path] [-t tokens] (default sketch mode)
    sketch_epilog = """\
Examples:
  hypergumbo sketch .                   # Auto-runs analysis if needed
  hypergumbo sketch ~/project -t 4000   # 4000-token limit
  hypergumbo sketch . -t 1000 -x        # Brief overview, no tests
  hypergumbo . -t 8000                  # Shorthand (sketch is default)

Caching:
  Results are cached in ~/.cache/hypergumbo/<repo>/<state>/
  First run analyzes the repo; subsequent runs are instant.
  Cache auto-invalidates when source files change.

Token budget guidelines:
  1000    Structure only (files, folders)
  4000    Good balance for most LLMs
  8000    Includes more symbols and docs
  16000   Comprehensive (large context windows)

Output is Markdown, printed to stdout. Pipe to a file or clipboard:
  hypergumbo . -t 4000 > summary.md
  hypergumbo . -t 4000 | pbcopy         # macOS clipboard
  hypergumbo . -t 4000 | xclip -sel c   # Linux clipboard"""

    p_sketch = sub.add_parser(
        "sketch",
        help="Generate token-budgeted Markdown sketch (default mode)",
        epilog=sketch_epilog,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p_sketch.add_argument(
        "path",
        nargs="?",
        default=".",
        help="Path to repo (default: current directory)",
    )
    p_sketch.add_argument(
        "--input",
        type=str,
        default=None,
        metavar="FILE",
        help="Use cached results file instead of re-analyzing (faster)",
    )
    p_sketch.add_argument(
        "-t", "--tokens",
        type=int,
        default=None,
        help="Limit output to approximately N tokens",
    )
    p_sketch.add_argument(
        "-x", "--exclude-tests",
        action="store_true",
        dest="exclude_tests",
        help="Exclude test files from analysis (faster for large codebases)",
    )
    p_sketch.add_argument(
        "--no-first-party-priority",
        action="store_false",
        dest="first_party_priority",
        help="Disable supply chain tier weighting in symbol ranking",
    )
    p_sketch.add_argument(
        "-e", "--exclude",
        action="append",
        default=[],
        dest="extra_excludes",
        metavar="PATTERN",
        help="Additional exclude pattern (can be repeated, e.g. -e '*.json' -e 'vendor')",
    )
    p_sketch.add_argument(
        "--config-extraction",
        choices=["heuristic", "embedding", "hybrid"],
        default="hybrid",
        dest="config_extraction_mode",
        help="Config file extraction mode: heuristic (fast), "
             "embedding (semantic, requires sentence-transformers), "
             "hybrid (heuristics first, then embeddings; default)",
    )
    p_sketch.add_argument(
        "-v", "--verbose",
        action="store_true",
        help="Print progress messages to stderr",
    )
    p_sketch.add_argument(
        "--progress",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Show progress indicator with ETA to stderr (default: on, use --no-progress to disable)",
    )
    p_sketch.add_argument(
        "--readme-debug",
        action="store_true",
        dest="readme_debug",
        help="Show README extraction debug info (k-scores, timing) to stderr",
    )
    p_sketch.add_argument(
        "--max-config-files",
        type=int,
        default=15,
        help="Maximum config files to process in embedding mode (default: 15)",
    )
    p_sketch.add_argument(
        "--fleximax-lines",
        type=int,
        default=100,
        help="Base sample size for log-scaled line sampling (default: 100)",
    )
    p_sketch.add_argument(
        "--max-chunk-chars",
        type=int,
        default=800,
        help="Maximum characters per chunk for embedding (default: 800)",
    )
    p_sketch.add_argument(
        "--no-language-proportional",
        action="store_false",
        dest="language_proportional",
        help="Disable language-proportional symbol selection (enabled by default)",
    )
    p_sketch.add_argument(
        "--with-source",
        action="store_true",
        dest="with_source",
        default=True,
        help="Include source file contents (default: enabled)",
    )
    p_sketch.add_argument(
        "--no-source",
        action="store_false",
        dest="with_source",
        help="Omit source file contents from sketch output",
    )
    p_sketch.set_defaults(func=cmd_sketch, first_party_priority=True, language_proportional=True)

    # hypergumbo run
    run_epilog = """\
Examples:
  hypergumbo run .                      # Full analysis → cached in ~/.cache/hypergumbo/
  hypergumbo run . --out analysis.json  # Custom output file (in cwd)
  hypergumbo run . --compact            # LLM-friendly: top symbols + summary
  hypergumbo run . --first-party-only   # Exclude vendored/external code

After running, use search/explain/slice to query the results:
  hypergumbo sketch .                   # Auto-discovers cached results
  hypergumbo search "parse"             # Find symbols containing "parse"
  hypergumbo explain "main"             # Show callers/callees of main
  hypergumbo slice --entry main         # Extract subgraph from main()

Cache location:
  ~/.cache/hypergumbo/<repo-fingerprint>/results/<state-hash>/
  Results are cached per repo state and auto-invalidated when files change."""

    p_run = sub.add_parser(
        "run",
        help="Run full analysis and save behavior map to JSON",
        epilog=run_epilog,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p_run.add_argument(
        "path",
        nargs="?",
        default=".",
        help="Path to repo root (default: current directory)",
    )
    p_run.add_argument(
        "--out",
        default=None,
        help="Output JSON path (default: ~/.cache/hypergumbo/<repo>/<state>/)",
    )
    p_run.add_argument(
        "--max-tier",
        type=int,
        choices=[1, 2, 3, 4],
        default=None,
        dest="max_tier",
        help="Filter output by supply chain tier (1=first-party, 2=+internal, "
             "3=+external, 4=all). Default: no filtering.",
    )
    p_run.add_argument(
        "--first-party-only",
        action="store_const",
        const=1,
        dest="max_tier",
        help="Only include first-party code (shortcut for --max-tier 1)",
    )
    p_run.add_argument(
        "--max-files",
        type=int,
        default=None,
        dest="max_files",
        help="Maximum files to analyze per language (for large repos)",
    )
    p_run.add_argument(
        "--compact",
        action="store_true",
        help="Compact output: include top symbols by centrality coverage with "
             "bag-of-words summary of omitted items (LLM-friendly)",
    )
    p_run.add_argument(
        "--coverage",
        type=float,
        default=0.8,
        help="Target centrality coverage for --compact mode (0.0-1.0, default: 0.8)",
    )
    p_run.add_argument(
        "--no-connectivity",
        action="store_true",
        dest="no_connectivity",
        help="Disable connectivity-aware selection for --compact mode. "
             "Falls back to centrality-based selection (may produce disconnected "
             "subgraphs where entrypoints have no edges).",
    )
    p_run.add_argument(
        "--budgets",
        type=str,
        default=None,
        dest="budgets",
        help="Generate output files at token budgets. Comma-separated specs "
             "like '4k,16k,64k'. Use 'default' for standard budgets (4k,16k,64k), "
             "'none' to disable. Default: generate budget files alongside full output.",
    )
    p_run.add_argument(
        "--tiers",
        type=str,
        default=None,
        dest="budgets",  # Maps to same dest as --budgets
        help=argparse.SUPPRESS,  # Hidden (deprecated alias for --budgets)
    )
    p_run.add_argument(
        "-e", "--exclude",
        action="append",
        default=[],
        dest="extra_excludes",
        metavar="PATTERN",
        help="Additional exclude pattern (can be repeated, e.g. -e '*.json' -e 'vendor')",
    )
    p_run.add_argument(
        "--frameworks",
        type=str,
        default=None,
        metavar="SPEC",
        help="Framework detection mode: 'none' (skip), 'all' (exhaustive), "
             "or comma-separated list (e.g., 'fastapi,celery'). "
             "Default: auto-detect based on detected languages.",
    )
    p_run.add_argument(
        "--progress",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Show progress indicator with ETA to stderr (default: on, use --no-progress to disable)",
    )
    p_run.set_defaults(func=cmd_run)

    # hypergumbo slice
    slice_epilog = """\
Examples:
  hypergumbo slice --entry main              # Forward slice from main()
  hypergumbo slice --entry main --reverse    # What calls main()?
  hypergumbo slice --entry "UserService"     # Slice from a class
  hypergumbo slice --list-entries            # Show detected entry points
  hypergumbo slice --entry auto              # Auto-detect entry point
  hypergumbo slice --entry main --flat       # Output for external tools

Output format:
  Default: {schema_version, view, feature: {nodes, edges, ...}}
  --inline: Same as default, but feature includes full node/edge objects
  --flat:   {nodes: [...], edges: [...]} - simple format for external tools

Use cases:
  - Understand what code main() depends on (forward slice)
  - Find all callers of a function (reverse slice)
  - Extract a focused subgraph for debugging or review

Auto-discovers cached results from 'hypergumbo run', or specify --input."""

    p_slice = sub.add_parser(
        "slice",
        help="Extract subgraph from an entry point",
        epilog=slice_epilog,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p_slice.add_argument(
        "path",
        nargs="?",
        default=".",
        help="Path to repo root (default: current directory)",
    )
    p_slice.add_argument(
        "--entry",
        default="auto",
        help="Entrypoint to slice from: symbol name, file path, node ID, or 'auto' "
             "to detect automatically (default: auto)",
    )
    p_slice.add_argument(
        "--list-entries",
        action="store_true",
        help="List detected entrypoints and exit (do not slice)",
    )
    p_slice.add_argument(
        "--out",
        default="slice.json",
        help="Output JSON path (default: slice.<entry-name>.json)",
    )
    p_slice.add_argument(
        "--input",
        default=None,
        help="Read from existing behavior map file instead of running analysis",
    )
    p_slice.add_argument(
        "--max-hops",
        type=int,
        default=3,
        help="Maximum traversal depth (default: 3)",
    )
    p_slice.add_argument(
        "--max-files",
        type=int,
        default=20,
        help="Maximum number of files to include (default: 20)",
    )
    p_slice.add_argument(
        "--min-confidence",
        type=float,
        default=0.0,
        help="Minimum edge confidence to follow (default: 0.0)",
    )
    p_slice.add_argument(
        "--exclude-tests",
        action="store_true",
        help="Exclude test files from the slice",
    )
    p_slice.add_argument(
        "--reverse",
        action="store_true",
        help="Reverse slice: find callers of the entry point (what calls X?)",
    )
    p_slice.add_argument(
        "--max-tier",
        type=int,
        choices=[1, 2, 3, 4],
        default=None,
        dest="max_tier",
        help="Stop at supply chain tier boundary (1=first-party only, "
             "2=+internal, 3=+external, 4=all). Default: no tier filtering.",
    )
    p_slice.add_argument(
        "--language",
        default=None,
        help="Filter entry point matches to this language (e.g., python, javascript)",
    )
    p_slice.add_argument(
        "--inline",
        action="store_true",
        help="Include full node/edge objects in output (not just IDs). "
             "Makes slice.json self-contained without needing the behavior map.",
    )
    p_slice.add_argument(
        "--flat",
        action="store_true",
        help="Output flat structure with just nodes/edges arrays at top level. "
             "Useful for external tools expecting {nodes: [...], edges: [...]}. "
             "Implies --inline.",
    )
    p_slice.set_defaults(func=cmd_slice)

    # hypergumbo search
    search_epilog = """\
Examples:
  hypergumbo search "parse"               # Find symbols containing "parse"
  hypergumbo search "User" --kind class   # Find classes with "User"
  hypergumbo search "test" --limit 50     # Show more results
  hypergumbo search "handle" --language python

Auto-discovers cached results from 'hypergumbo run', or specify --input."""

    p_search = sub.add_parser(
        "search",
        help="Find symbols by name pattern",
        epilog=search_epilog,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p_search.add_argument(
        "pattern",
        help="Pattern to search for (case-insensitive substring match)",
    )
    p_search.add_argument(
        "--path",
        default=".",
        help="Path to repo root (default: current directory)",
    )
    p_search.add_argument(
        "--input",
        default=None,
        help="Input behavior map file (default: hypergumbo.results.json)",
    )
    p_search.add_argument(
        "--kind",
        default=None,
        help="Filter by symbol kind (e.g., function, class, method)",
    )
    p_search.add_argument(
        "--language",
        default=None,
        help="Filter by language (e.g., python, javascript)",
    )
    p_search.add_argument(
        "--limit",
        type=int,
        default=20,
        help="Maximum number of results to show (default: 20)",
    )
    p_search.set_defaults(func=cmd_search)

    # hypergumbo routes
    routes_epilog = """\
Examples:
  hypergumbo routes                       # Show all detected endpoints
  hypergumbo routes --language python     # Filter by language

Detects: Flask routes, FastAPI endpoints, Express routes, Django URLs, etc.

Auto-discovers cached results from 'hypergumbo run', or specify --input."""

    p_routes = sub.add_parser(
        "routes",
        help="List detected API routes and endpoints",
        epilog=routes_epilog,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p_routes.add_argument(
        "--path",
        default=".",
        help="Path to repo root (default: current directory)",
    )
    p_routes.add_argument(
        "--input",
        default=None,
        help="Input behavior map file (default: hypergumbo.results.json)",
    )
    p_routes.add_argument(
        "--language",
        default=None,
        help="Filter by language (e.g., python, javascript)",
    )
    p_routes.set_defaults(func=cmd_routes)

    # hypergumbo explain
    explain_epilog = """\
Examples:
  hypergumbo explain "main"               # Show what main calls and is called by
  hypergumbo explain "UserService"        # Explain a class
  hypergumbo explain "parse_config"       # Explain a specific function

Shows: Symbol location, callers (what calls it), callees (what it calls).

Auto-discovers cached results from 'hypergumbo run', or specify --input."""

    p_explain = sub.add_parser(
        "explain",
        help="Show callers and callees of a symbol",
        epilog=explain_epilog,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p_explain.add_argument(
        "symbol",
        help="Symbol name to explain (case-insensitive)",
    )
    p_explain.add_argument(
        "--path",
        default=".",
        help="Path to repo root (default: current directory)",
    )
    p_explain.add_argument(
        "--input",
        default=None,
        help="Input behavior map file (default: hypergumbo.results.json)",
    )
    p_explain.add_argument(
        "-x",
        "--exclude-tests",
        action="store_true",
        dest="exclude_tests",
        help="Exclude callers/callees from test files",
    )
    p_explain.add_argument(
        "--with-source",
        action="store_true",
        dest="with_source",
        default=True,
        help="Show source code for symbol, callers, and callees (default: enabled)",
    )
    p_explain.add_argument(
        "--no-source",
        action="store_false",
        dest="with_source",
        help="Omit source code from explain output",
    )
    p_explain.add_argument(
        "-t",
        "--tokens",
        type=int,
        default=None,
        dest="tokens",
        help="Token budget for source code (omits low-priority sources when exceeded)",
    )
    p_explain.set_defaults(func=cmd_explain)

    # hypergumbo catalog
    catalog_epilog = """\
Examples:
  hypergumbo catalog                      # List all analyzers

Shows which languages and frameworks hypergumbo can analyze.
The output begins with passes suggested for your current directory."""

    p_catalog = sub.add_parser(
        "catalog",
        help="List available language analyzers",
        epilog=catalog_epilog,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p_catalog.set_defaults(func=cmd_catalog)

    # hypergumbo build-grammars
    p_build = sub.add_parser(
        "build-grammars",
        help="Build tree-sitter grammars from source (Lean, Wolfram)",
    )
    p_build.add_argument(
        "--check",
        action="store_true",
        help="Check grammar availability without building",
    )
    p_build.add_argument(
        "--quiet",
        action="store_true",
        help="Suppress output",
    )
    p_build.set_defaults(func=cmd_build_grammars)

    # hypergumbo test-coverage
    test_coverage_epilog = """\
Examples:
  hypergumbo test-coverage .                  # Show coverage summary
  hypergumbo test-coverage . --format json    # JSON output for tooling
  hypergumbo test-coverage . --top 10         # Top 10 hot/cold spots
  hypergumbo test-coverage . --max-tests 0    # Only show untested functions

Analyzes the call graph to estimate which functions are tested.
Does NOT execute code - uses static analysis only.
Language agnostic - works with any language hypergumbo supports.

Auto-discovers cached results from 'hypergumbo run', or specify --input."""

    p_test_cov = sub.add_parser(
        "test-coverage",
        help="Estimate test coverage from call graph (static analysis)",
        epilog=test_coverage_epilog,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p_test_cov.add_argument(
        "path",
        nargs="?",
        default=".",
        help="Path to repo root (default: current directory)",
    )
    p_test_cov.add_argument(
        "--input",
        default=None,
        help="Input behavior map file (default: hypergumbo.results.json)",
    )
    p_test_cov.add_argument(
        "--format",
        choices=["text", "json"],
        default="text",
        help="Output format (default: text)",
    )
    p_test_cov.add_argument(
        "--min-tests",
        type=int,
        default=None,
        help="Only show functions called by at least N tests",
    )
    p_test_cov.add_argument(
        "--max-tests",
        type=int,
        default=None,
        help="Only show functions called by at most N tests (0 = untested only)",
    )
    p_test_cov.add_argument(
        "--top",
        type=int,
        default=None,
        help="Limit output to top N hot/cold spots",
    )
    p_test_cov.set_defaults(func=cmd_test_coverage)

    # hypergumbo symbols
    symbols_epilog = """\
Examples:
  hypergumbo symbols                        # Show top 200 symbols by connectivity
  hypergumbo symbols --all                  # Show all symbols
  hypergumbo symbols -x                     # Exclude test files
  hypergumbo symbols --max-per-file 5       # Max 5 symbols per file
  hypergumbo symbols --max-per-file 3 --all # All files, 3 symbols each
  hypergumbo symbols --kind function        # Only functions
  hypergumbo symbols --language python      # Only Python symbols

Output: Rich table with columns Symbol, Kind, In (in-degree), Out (out-degree),
Deg (total degree), File. Auto-adjusts column widths and wraps long text.
Sorted by file connectivity (hottest files first), then filename, then degree.

Auto-discovers cached results from 'hypergumbo run', or specify --input."""

    p_symbols = sub.add_parser(
        "symbols",
        help="List symbols with connectivity (in-degree, out-degree)",
        epilog=symbols_epilog,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p_symbols.add_argument(
        "--path",
        default=".",
        help="Path to repo root (default: current directory)",
    )
    p_symbols.add_argument(
        "--input",
        default=None,
        help="Input behavior map file (default: hypergumbo.results.json)",
    )
    p_symbols.add_argument(
        "-x", "--exclude-tests",
        action="store_true",
        dest="exclude_tests",
        help="Exclude symbols from test files",
    )
    p_symbols.add_argument(
        "--max-per-file",
        type=int,
        default=None,
        dest="max_per_file",
        metavar="N",
        help="Maximum symbols to show per file (prevents file domination)",
    )
    p_symbols.add_argument(
        "--kind",
        default=None,
        help="Filter by symbol kind (e.g., function, class, method)",
    )
    p_symbols.add_argument(
        "--language",
        default=None,
        help="Filter by language (e.g., python, javascript)",
    )
    p_symbols.add_argument(
        "--limit",
        type=int,
        default=200,
        help="Maximum number of symbols to show (default: 200)",
    )
    p_symbols.add_argument(
        "--all",
        action="store_true",
        dest="all",
        help="Show all symbols (ignore --limit)",
    )
    p_symbols.set_defaults(func=cmd_symbols)

    # hypergumbo compact
    compact_epilog = """\
Examples:
  hypergumbo compact --input hg.json --out hg.compact.json
  hypergumbo compact --input hg.json --max-symbols 50 --coverage 0.9
  hypergumbo compact --input hg.json --no-connectivity

Converts an existing behavior map to compact form with:
- Top symbols by centrality coverage (connectivity-aware selection by default)
- Summary of omitted symbols (bag-of-words, path patterns, kinds)
- Induced subgraph edges (only edges between included symbols)

Useful for post-processing large behavior maps into LLM-friendly formats
without re-running the full analysis."""

    p_compact = sub.add_parser(
        "compact",
        help="Convert behavior map to compact form with coverage-based truncation",
        epilog=compact_epilog,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p_compact.add_argument(
        "--input",
        required=True,
        metavar="FILE",
        help="Input behavior map JSON file (required)",
    )
    p_compact.add_argument(
        "--out",
        default=None,
        metavar="FILE",
        help="Output file (default: print to stdout)",
    )
    p_compact.add_argument(
        "--max-symbols",
        type=int,
        default=100,
        dest="max_symbols",
        help="Maximum symbols to include (default: 100)",
    )
    p_compact.add_argument(
        "--min-symbols",
        type=int,
        default=10,
        dest="min_symbols",
        help="Minimum symbols to include (default: 10)",
    )
    p_compact.add_argument(
        "--coverage",
        type=float,
        default=0.8,
        help="Target centrality coverage 0.0-1.0 (default: 0.8)",
    )
    p_compact.add_argument(
        "--no-connectivity",
        action="store_true",
        dest="no_connectivity",
        help="Disable connectivity-aware selection (may produce disconnected subgraphs)",
    )
    p_compact.set_defaults(func=cmd_compact)

    return p


def _classify_symbols(
    symbols: list[Symbol], repo_root: Path, package_roots: set[Path]
) -> None:
    """Apply supply chain classification to symbols in-place.

    Classifies each symbol's file path and updates supply_chain_tier
    and supply_chain_reason fields.
    """
    for symbol in symbols:
        file_path = repo_root / symbol.path
        classification = classify_file(file_path, repo_root, package_roots)
        symbol.supply_chain_tier = classification.tier.value
        symbol.supply_chain_reason = classification.reason


def _compute_supply_chain_summary(
    symbols: list[Symbol], derived_paths: list[str]
) -> Dict[str, Any]:
    """Compute supply chain summary from classified symbols.

    Returns a dict with counts per tier plus derived_skipped info.
    """
    # Count unique files and symbols per tier
    tier_files: Dict[int, set] = {1: set(), 2: set(), 3: set(), 4: set()}
    tier_symbols: Dict[int, int] = {1: 0, 2: 0, 3: 0, 4: 0}

    for symbol in symbols:
        tier = symbol.supply_chain_tier
        tier_files[tier].add(symbol.path)
        tier_symbols[tier] += 1

    tier_names = {1: "first_party", 2: "internal_dep", 3: "external_dep"}

    summary: Dict[str, Any] = {}
    for tier, name in tier_names.items():
        summary[name] = {
            "files": len(tier_files[tier]),
            "symbols": tier_symbols[tier],
        }

    # Cap derived_skipped paths at 10
    summary["derived_skipped"] = {
        "files": len(tier_files[4]) + len(derived_paths),
        "paths": derived_paths[:10],
    }

    return summary


def run_behavior_map(
    repo_root: Path,
    out_path: Path | None = None,
    max_tier: int | None = None,
    max_files: int | None = None,
    compact: bool = False,
    coverage: float = 0.8,
    connectivity: bool = True,
    budgets: str | None = None,
    extra_excludes: list[str] | None = None,
    frameworks: str | None = None,
    include_sketch_precomputed: bool = True,
    progress: bool = True,
) -> list[Path]:
    """
    Run the behavior_map analysis for a repo and write JSON to out_path.

    Args:
        repo_root: Root directory of the repository
        out_path: Path to write the behavior map JSON. If None, defaults to
            ~/.cache/hypergumbo/<fingerprint>/results/<state_hash>/hypergumbo.results.json
        max_tier: Optional maximum supply chain tier (1-4). Symbols with
            tier > max_tier are filtered out. None means no filtering.
        max_files: Optional maximum files per language analyzer. Limits
            how many files each analyzer processes (for large repos).
        compact: If True, output compact mode with coverage-based truncation
            and bag-of-words summary of omitted items.
        coverage: Target centrality coverage for compact mode (0.0-1.0).
        connectivity: If True (default), use connectivity-aware selection for
            compact mode. Prioritizes nodes that bridge disconnected entrypoints,
            producing well-connected subgraphs instead of isolated high-centrality
            nodes. Set False to use legacy centrality-based selection.
        budgets: Token budget output specification. Comma-separated specs like
            "4k,16k,64k". Use "default" for DEFAULT_TIERS, "none" to disable.
            If None, defaults to generating DEFAULT_TIERS alongside full output.
        extra_excludes: Additional exclude patterns beyond DEFAULT_EXCLUDES.
            Affects profile detection (language stats). Use for excluding
            project-specific files like "*.json" or "vendor".
        frameworks: Framework specification (ADR-0003):
            - None: Auto-detect (default)
            - "none": Skip framework detection
            - "all": Check all frameworks for detected languages
            - "fastapi,celery": Only check specified frameworks
        include_sketch_precomputed: If True (default), pre-extract config_info,
            vocabulary, and readme_description for fast sketch generation.
            Set False to skip this (avoids loading embedding model).
        progress: If True, show progress indicator with ETA to stderr.

    Returns:
        List of file paths for all generated artifacts (main output + tier files).
    """
    import sys
    import time

    # Progress tracking
    start_time = time.time()

    def show_progress(phase: str, pct: int) -> None:  # pragma: no cover
        """Display progress to stderr."""
        if not progress:
            return
        elapsed = time.time() - start_time
        if pct > 0:
            estimated_total = elapsed / (pct / 100)
            remaining = estimated_total - elapsed
            eta_str = f" ETA {remaining:.0f}s" if remaining > 0 else ""
        else:
            eta_str = ""
        sys.stderr.write(f"\r[{pct:3d}%] {phase}...{eta_str}    ")
        sys.stderr.flush()

    def complete_progress() -> None:  # pragma: no cover
        """Show completion message."""
        if not progress:
            return
        elapsed = time.time() - start_time
        sys.stderr.write(f"\r[100%] Complete in {elapsed:.1f}s           \n")
        sys.stderr.flush()

    _log_memory("start")

    # Default to cache directory if no explicit output path provided
    if out_path is None:
        from .sketch_embeddings import _get_results_cache_dir
        cache_dir = _get_results_cache_dir(repo_root)
        out_path = cache_dir / "hypergumbo.results.json"

    generated_files: list[Path] = []
    behavior_map = new_behavior_map()

    # Detect repo profile (languages, frameworks)
    show_progress("Detecting profile", 5)
    profile = detect_profile(repo_root, extra_excludes=extra_excludes, frameworks=frameworks)
    behavior_map["profile"] = profile.to_dict()

    # Detect internal package roots for supply chain classification
    package_roots = detect_package_roots(repo_root)

    # Run all language analyzers using consolidated registry
    # This replaces ~800 lines of repetitive analyzer invocation code
    show_progress("Running analyzers", 10)
    (
        analysis_runs,
        all_symbols,
        all_edges,
        all_usage_contexts,
        limits,
        captured_symbols,
    ) = run_all_analyzers(repo_root, max_files=max_files)
    _log_memory("after analyzers")

    # Resolve deferred symbol references (INV-002 proper fix)
    # UsageContexts extracted during analysis may have symbol_ref=None when
    # the target symbol is in a different file. Now that we have the complete
    # symbol table, resolve these references using multi-strategy lookup.
    show_progress("Resolving symbol references", 48)
    resolution_stats = resolve_deferred_symbol_refs(all_symbols, all_usage_contexts)
    if resolution_stats.total_resolved > 0:
        _log_memory(  # pragma: no cover - debug logging
            f"resolved {resolution_stats.total_resolved}/{resolution_stats.total_unresolved} "
            f"deferred refs (exact={resolution_stats.resolved_exact}, "
            f"suffix={resolution_stats.resolved_suffix})"
        )

    # Enrich symbols with framework concept metadata (ADR-0003)
    # This applies YAML-based patterns to add concept info (route, model, etc.)
    # to symbols based on their decorators, base classes, annotations, AND
    # usage contexts (v1.1.x) for call-based frameworks like Django URLs.
    show_progress("Enriching symbols", 50)
    detected_frameworks = set(profile.frameworks)
    enrich_symbols(all_symbols, detected_frameworks, all_usage_contexts)

    # Run cross-language linkers
    show_progress("Running linkers", 55)
    #
    # Linkers are being migrated to a registry pattern (like analyzers).
    # New linkers should use @register_linker decorator in linkers/registry.py.
    # The registry-based linkers run first, then existing explicit linkers below.
    # Once all linkers are migrated, the explicit calls below can be removed.

    # Run any registry-based linkers (new pattern)
    # This enables new linkers to be added without modifying this file.
    # LinkerContext provides all inputs; each linker picks what it needs.
    linker_ctx = LinkerContext(
        repo_root=repo_root,
        symbols=all_symbols,
        edges=all_edges,
        captured_symbols=captured_symbols,
        detected_frameworks=set(profile.frameworks),
        detected_languages=set(profile.languages.keys()),
    )
    for _linker_name, linker_result in run_all_linkers(linker_ctx):
        if linker_result.run is not None:
            analysis_runs.append(linker_result.run.to_dict())
        all_symbols.extend(linker_result.symbols)
        all_edges.extend(linker_result.edges)
    del linker_ctx, captured_symbols  # Free linker data structures

    # Deduplicate edges by ID (linkers may create duplicate edges)
    seen_edge_ids: set[str] = set()
    unique_edges: list[Edge] = []
    for edge in all_edges:
        if edge.id not in seen_edge_ids:
            seen_edge_ids.add(edge.id)
            unique_edges.append(edge)
    all_edges = unique_edges
    del seen_edge_ids, unique_edges
    _log_memory("after linkers")

    # Apply supply chain classification to all symbols
    show_progress("Classifying symbols", 60)
    _classify_symbols(all_symbols, repo_root, package_roots)

    # Apply max_tier filtering if specified
    if max_tier is not None:
        # Filter symbols by tier
        filtered_symbols = [
            s for s in all_symbols if s.supply_chain_tier <= max_tier
        ]
        filtered_symbol_ids = {s.id for s in filtered_symbols}

        # Filter edges: src must be in filtered symbols OR be a file-level reference
        # File-level import edges have src like "python:path/to/file.py:1-1:file:file"
        # We check for ":file" suffix OR common file extensions in the src path
        def _is_valid_edge_src(src: str) -> bool:
            if src in filtered_symbol_ids:
                return True
            # File-level symbols end with ":file" or ":file:file"
            if src.endswith(":file") or ":file:" in src:
                return True
            # Defensive fallback: check for file extensions in path (unlikely path)
            for ext in (".py:", ".js:", ".ts:", ".tsx:", ".jsx:"):  # pragma: no cover
                if ext in src:
                    return True
            return False  # pragma: no cover

        filtered_edges = [e for e in all_edges if _is_valid_edge_src(e.src)]

        all_symbols = filtered_symbols
        all_edges = filtered_edges
        limits.max_tier_applied = max_tier

    # Rank symbols by importance (centrality + tier weighting) for output ordering
    show_progress("Ranking symbols", 65)
    ranked = rank_symbols(all_symbols, all_edges, first_party_priority=True)
    ranked_symbols = [r.symbol for r in ranked]
    del ranked  # Free RankedSymbol wrappers

    # Convert to dicts for output (in ranked order)
    all_nodes = [s.to_dict() for s in ranked_symbols]
    all_edge_dicts = [e.to_dict() for e in all_edges]

    behavior_map["analysis_runs"] = analysis_runs
    behavior_map["nodes"] = all_nodes
    behavior_map["edges"] = all_edge_dicts
    behavior_map["usage_contexts"] = [uc.to_dict() for uc in all_usage_contexts]
    del all_usage_contexts  # Free UsageContext objects

    # Compute metrics from analyzed nodes and edges
    show_progress("Computing metrics", 70)
    behavior_map["metrics"] = compute_metrics(all_nodes, all_edge_dicts)

    # Detect and store entrypoints (computed from symbols, persisted for convenience)
    show_progress("Detecting entrypoints", 75)
    entrypoints = detect_entrypoints(all_symbols, all_edges)
    behavior_map["entrypoints"] = [ep.to_dict() for ep in entrypoints]
    del entrypoints  # Free Entrypoint objects

    # Compute supply chain summary
    # Note: derived_paths would be tracked during file discovery in a full implementation
    behavior_map["supply_chain_summary"] = _compute_supply_chain_summary(
        all_symbols, derived_paths=[]
    )

    # Pre-extract sketch data (config, vocabulary, readme)
    # This avoids needing to load the embedding model later in sketch mode
    from .sketch import (
        _extract_config_info, _extract_domain_vocabulary, _extract_readme_description,
        ConfigExtractionMode,
    )
    # Pre-extract sketch data (config, vocabulary, readme) if requested
    # This avoids reloading the embedding model when generating sketches later
    if include_sketch_precomputed:
        show_progress("Pre-computing sketch data", 80)
        sketch_precomputed: dict[str, str | list[str] | None] = {}

        # Extract config info using HYBRID mode (best quality, uses embeddings)
        try:
            sketch_precomputed["config_info"] = _extract_config_info(
                repo_root, mode=ConfigExtractionMode.HYBRID
            )
        except Exception:  # pragma: no cover - graceful degradation
            sketch_precomputed["config_info"] = ""

        # Extract domain vocabulary
        sketch_precomputed["vocabulary"] = _extract_domain_vocabulary(repo_root, profile)

        # Extract README description (uses embedding model)
        try:
            sketch_precomputed["readme_description"] = _extract_readme_description(repo_root)
        except Exception:  # pragma: no cover - graceful degradation
            sketch_precomputed["readme_description"] = None

        # Pre-compute centrality scores for Additional Files section
        # This avoids expensive ripgrep/regex operations during sketch generation
        from fnmatch import fnmatch
        from .discovery import DEFAULT_EXCLUDES
        from .sketch import ADDITIONAL_FILES_EXCLUDES
        from .taxonomy import is_additional_file_candidate

        # Extract source file paths from analyzed symbols
        source_paths: set[str] = set()
        for sym in all_symbols:
            if sym.path:
                source_paths.add(sym.path)

        # Collect candidate non-source files (same logic as _format_additional_files)
        all_excludes = list(DEFAULT_EXCLUDES) + ADDITIONAL_FILES_EXCLUDES
        candidate_files: list[Path] = []

        for f in repo_root.rglob("*"):
            if not f.is_file():
                continue
            rel_path = f.relative_to(repo_root)
            rel_str = str(rel_path)

            # Skip source files
            if rel_str in source_paths:
                continue

            # Skip hidden files/directories
            if any(p.startswith(".") for p in rel_path.parts):
                continue  # pragma: no cover - tested in _format_additional_files

            # Role-based filtering (ADR-0004 Phase 4)
            if not is_additional_file_candidate(f):
                continue

            # Pattern-based filtering for boilerplate (same logic as _format_additional_files)
            is_excluded = False
            for pattern in all_excludes:
                if fnmatch(f.name, pattern):
                    is_excluded = True  # pragma: no cover - tested in sketch tests
                    break  # pragma: no cover
                for part in rel_path.parts:
                    if fnmatch(part, pattern):
                        is_excluded = True  # pragma: no cover - tested in sketch tests
                        break  # pragma: no cover
                if is_excluded:  # pragma: no cover
                    break
            if is_excluded:
                continue  # pragma: no cover

            candidate_files.append(f)

        # Compute centrality scores for all candidates
        if candidate_files and all_symbols:
            raw_in_degree = compute_raw_in_degree(all_symbols, all_edges)
            centrality_result = compute_symbol_mention_centrality_batch(
                files=candidate_files,
                symbols=all_symbols,
                in_degree=raw_in_degree,
                min_in_degree=2,
                max_file_size=100 * 1024,
            )
            # Store as relative path strings for JSON serialization
            sketch_precomputed["centrality_scores"] = {
                str(f.relative_to(repo_root)): score
                for f, score in centrality_result.normalized_scores.items()
            }
        else:  # pragma: no cover - defensive: no candidates or no symbols
            sketch_precomputed["centrality_scores"] = {}

        behavior_map["sketch_precomputed"] = sketch_precomputed

    # Record skipped files from analysis runs
    for run in analysis_runs:
        if run.get("files_skipped", 0) > 0:
            limits.partial_results_reason = "some files skipped during analysis"
    behavior_map["limits"] = limits.to_dict()

    # Ensure parent directory exists (even if caller gives nested paths later)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    # Generate budget-tiered output files BEFORE compact mode
    # (budget files are always based on full analysis, not compact)
    if budgets != "none":
        budget_specs: list[str]
        if budgets is None or budgets == "default":
            budget_specs = list(DEFAULT_TIERS)
        else:
            budget_specs = [b.strip() for b in budgets.split(",") if b.strip()]

        # Generate each budget file from full behavior map
        for budget_spec in budget_specs:
            try:
                target_tokens = parse_tier_spec(budget_spec)
                budget_path = Path(generate_tier_filename(str(out_path), budget_spec))
                tiered_map = format_tiered_behavior_map(
                    behavior_map, all_symbols, all_edges, target_tokens
                )
                with open(budget_path, "w") as f:
                    json.dump(tiered_map, f, indent=2)
                generated_files.append(budget_path)
                # Free memory between tiers (helps with large repos like tensorflow)
                del tiered_map
                gc.collect()
            except ValueError:
                # Skip invalid tier specs silently
                pass

    # Apply compact mode if requested (modifies main output only)
    if compact:
        config = CompactConfig(target_coverage=coverage)
        behavior_map = format_compact_behavior_map(
            behavior_map, all_symbols, all_edges, config,
            connectivity_aware=connectivity,
        )

    # Free memory: Symbol/Edge objects no longer needed after tier/compact processing
    # All data is now in behavior_map as dicts. For large repos like tensorflow (154k
    # symbols, 505k edges), this can free several GB of memory before final write.
    del all_symbols
    del all_edges
    del ranked_symbols
    gc.collect()
    _log_memory("after cleanup")

    show_progress("Writing output", 95)
    with open(out_path, "w") as f:
        json.dump(behavior_map, f, indent=2)
    generated_files.append(out_path)
    _log_memory("after write")

    complete_progress()
    return generated_files


def print_all_help(parser: argparse.ArgumentParser) -> None:
    """Print help for main parser and all subcommands."""
    # Print main help
    parser.print_help()
    print("\n" + "=" * 78)
    print("DETAILED SUBCOMMAND HELP")
    print("=" * 78)

    # Get subparsers
    # pylint: disable=protected-access
    subparsers_action = None
    for action in parser._subparsers._actions:
        if isinstance(action, argparse._SubParsersAction):
            subparsers_action = action
            break

    if subparsers_action is None:
        return  # pragma: no cover

    # Print help for each subcommand
    for name, subparser in sorted(subparsers_action.choices.items()):
        print(f"\n{'─' * 78}")
        print(f"  hypergumbo {name}")
        print("─" * 78)
        subparser.print_help()


def main(argv=None) -> int:
    import logging

    parser = build_parser()

    # Handle default sketch mode: if no subcommand given, insert "sketch"
    if argv is None:
        argv = sys.argv[1:]

    # Handle --help --all: show all subcommand help panels
    if ("--help" in argv or "-h" in argv) and "--all" in argv:
        print_all_help(parser)
        return 0

    subcommands = {"run", "slice", "search", "routes", "explain", "catalog", "sketch", "build-grammars", "test-coverage", "symbols", "compact"}

    # If no args, or first arg is not a subcommand (and not a flag), use sketch mode
    if not argv or (argv[0] not in subcommands and not argv[0].startswith("-")):
        argv = ["sketch"] + list(argv)

    args = parser.parse_args(argv)

    # Configure logging if --debug is set
    if getattr(args, "debug", False):
        logging.basicConfig(
            level=logging.DEBUG,
            format="[%(name)s] %(levelname)s: %(message)s",
            stream=sys.stderr,
        )

    if not hasattr(args, "func"):  # pragma: no cover
        parser.print_help()  # pragma: no cover
        return 1  # pragma: no cover

    return args.func(args)

