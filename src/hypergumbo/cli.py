"""Command-line interface for hypergumbo.

This module provides the main entry point for the hypergumbo CLI, handling
argument parsing and dispatching to the appropriate command handlers.

How It Works
------------
The CLI uses argparse with subcommands for different operations:

- **sketch** (default): Generate token-budgeted Markdown overview
- **init**: Create .hypergumbo/ capsule with analysis plan
- **run**: Execute full analysis and output behavior map JSON
- **slice**: Extract subgraph from an entry point
- **catalog**: List available analysis passes and packs
- **export-capsule**: Export capsule as shareable tarball
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
import hypergumbo.linkers.database_query as _database_query_linker  # noqa: F401
import hypergumbo.linkers.dependency as _dependency_linker  # noqa: F401
import hypergumbo.linkers.event_sourcing as _event_sourcing_linker  # noqa: F401
import hypergumbo.linkers.graphql as _graphql_linker  # noqa: F401
import hypergumbo.linkers.graphql_resolver as _graphql_resolver_linker  # noqa: F401
import hypergumbo.linkers.grpc as _grpc_linker  # noqa: F401
import hypergumbo.linkers.http as _http_linker  # noqa: F401
import hypergumbo.linkers.ipc as _ipc_linker  # noqa: F401
import hypergumbo.linkers.jni as _jni_linker  # noqa: F401
import hypergumbo.linkers.message_queue as _message_queue_linker  # noqa: F401
import hypergumbo.linkers.openapi as _openapi_linker  # noqa: F401
import hypergumbo.linkers.phoenix_ipc as _phoenix_ipc_linker  # noqa: F401
import hypergumbo.linkers.subprocess_cli as _subprocess_linker  # noqa: F401
import hypergumbo.linkers.swift_objc as _swift_objc_linker  # noqa: F401
import hypergumbo.linkers.websocket as _websocket_linker  # noqa: F401
from .entrypoints import detect_entrypoints
from .export import export_capsule
from .ir import Symbol, Edge, Span
from .metrics import compute_metrics
from .profile import detect_profile
from .llm_assist import generate_plan_with_fallback
from .schema import new_behavior_map
from .sketch import generate_sketch, ConfigExtractionMode
from .slice import SliceQuery, slice_graph, AmbiguousEntryError, rank_slice_nodes
from .supply_chain import classify_file, detect_package_roots
from .ranking import rank_symbols, _is_test_path, compute_transitive_test_coverage
from .compact import (
    format_compact_behavior_map,
    format_tiered_behavior_map,
    generate_tier_filename,
    parse_tier_spec,
    CompactConfig,
    DEFAULT_TIERS,
)
from .build_grammars import build_all_grammars, check_grammar_availability
from .framework_patterns import enrich_symbols, get_frameworks_dir


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


def _print_output_summary(
    command: str,
    artifacts: list[Path] | None = None,
    stdout_output: bool = False,
    file: Any = None,
) -> None:
    """Print consistent output summary at end of command execution.

    Always prints as the last thing, even if no artifacts generated.

    Args:
        command: The hypergumbo subcommand name (e.g., "sketch", "run")
        artifacts: List of generated file paths (None or empty for stdout-only)
        stdout_output: If True, indicate output went to stdout
        file: Output file (default: sys.stdout). Use sys.stderr for JSON output
            modes to avoid breaking JSON parsing.
    """
    if file is None:
        file = sys.stdout

    artifact_count = len(artifacts) if artifacts else 0
    print(f"\n[hypergumbo {command}] Generated {artifact_count} artifact(s)", file=file)

    if artifacts:
        for artifact_path in artifacts:
            print(f"  {artifact_path}", file=file)
    if stdout_output:
        print("  Output: stdout", file=file)


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

    max_tokens = args.tokens if args.tokens else None
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
    )
    print(sketch)

    # Output summary (always to stdout at the end)
    _print_output_summary("sketch", stdout_output=True)

    return 0


def cmd_init(args: argparse.Namespace) -> int:
    repo_root = Path(args.path).resolve()
    capsule_dir = repo_root / ".hypergumbo"
    capsule_dir.mkdir(parents=True, exist_ok=True)

    capsule_path = capsule_dir / "capsule.json"
    plan_path = capsule_dir / "capsule_plan.json"

    # Normalize capabilities into a list
    capabilities = [
        c.strip()
        for c in (args.capabilities or "").split(",")
        if c.strip()
    ]

    # Detect repo profile for plan generation
    profile = detect_profile(repo_root)

    # If no explicit capabilities, use detected languages
    if not capabilities:
        capabilities = list(profile.languages.keys())

    # Generate capsule plan (template or LLM-assisted)
    catalog = get_default_catalog()
    use_llm = args.assistant == "llm"

    # If LLM requested but no backend available, offer interactive setup
    if use_llm:
        from .llm_assist import detect_backend, LLMBackend
        from .user_config import prompt_for_llm_setup

        backend, _ = detect_backend()
        if backend == LLMBackend.NONE and sys.stdin.isatty():
            # Offer to set up LLM backend interactively
            if prompt_for_llm_setup():
                # Re-detect after setup
                backend, _ = detect_backend()

    plan, llm_result = generate_plan_with_fallback(
        profile, catalog, use_llm=use_llm, tier=args.llm_input
    )

    # Build capsule manifest with generation metadata
    capsule = {
        "repo_root": str(repo_root),
        "assistant": args.assistant,
        "llm_input": args.llm_input,
        "capabilities": capabilities,
    }

    # Add LLM generation metadata if attempted
    if llm_result is not None:
        capsule["generator"] = {
            "mode": "llm_assisted" if llm_result.success else "template_fallback",
            "backend": llm_result.backend_used.value if llm_result.backend_used else None,
            "model": llm_result.model_used,
        }
        if not llm_result.success:
            capsule["generator"]["fallback_reason"] = llm_result.error

    capsule_path.write_text(json.dumps(capsule, indent=2))
    plan_path.write_text(json.dumps(plan.to_dict(), indent=2))

    # Print status
    print(
        "[hypergumbo init] "
        f"repo_root={repo_root} "
        f"capabilities={','.join(capabilities)} "
        f"assistant={args.assistant} "
        f"llm_input={args.llm_input}"
    )
    print(f"  Created: {capsule_path}")
    print(f"  Created: {plan_path}")
    print(f"  Passes: {len(plan.passes)}, Packs: {len(plan.packs)}, Rules: {len(plan.rules)}")

    # Print LLM status if attempted
    if llm_result is not None:
        if llm_result.success:
            backend = llm_result.backend_used.value if llm_result.backend_used else "unknown"
            model = llm_result.model_used or "default"
            print(f"  LLM: {backend}/{model} (success)")
        else:
            print(f"  LLM: failed ({llm_result.error}), using template fallback")

    # Output summary (always at the end)
    _print_output_summary("init", artifacts=[capsule_path, plan_path])

    return 0


def cmd_run(args: argparse.Namespace) -> int:
    # The positional argument for `run` is called `path` in the parser below.
    repo_root = Path(args.path).resolve()
    out_path = Path(args.out)
    max_tier = getattr(args, "max_tier", None)
    max_files = getattr(args, "max_files", None)
    compact = getattr(args, "compact", False)
    coverage = getattr(args, "coverage", 0.8)
    budgets = getattr(args, "budgets", None)
    exclude_tests = getattr(args, "exclude_tests", False)
    extra_excludes = getattr(args, "extra_excludes", [])
    frameworks = getattr(args, "frameworks", None)

    generated_files = run_behavior_map(
        repo_root=repo_root,
        out_path=out_path,
        max_tier=max_tier,
        max_files=max_files,
        compact=compact,
        coverage=coverage,
        budgets=budgets,
        exclude_tests=exclude_tests,
        extra_excludes=extra_excludes,
        frameworks=frameworks,
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


def cmd_slice(args: argparse.Namespace) -> int:
    """Execute the slice command."""
    path_arg = Path(args.path).resolve()
    out_path = Path(args.out)

    # Smart detection: if path is a .json file, treat it as --input automatically
    # This provides better UX: `hypergumbo slice results.json` just works
    if path_arg.suffix == ".json" and path_arg.is_file() and not args.input:
        args.input = str(path_arg)
        # Use parent directory as repo_root (or cwd if file is in cwd)
        repo_root = path_arg.parent if path_arg.parent != Path.cwd() else Path.cwd()
    else:
        repo_root = path_arg

    # Determine input file
    if args.input:
        input_path = Path(args.input)
        if not input_path.exists():
            print(f"Error: Input file not found: {args.input}", file=sys.stderr)
            return 1
    else:
        # Look for default results file
        input_path = repo_root / "hypergumbo.results.json"
        if not input_path.exists():
            print(
                "Error: No hypergumbo.results.json found. "
                "Run 'hypergumbo run' first or specify --input.",
                file=sys.stderr,
            )
            return 1

    behavior_map = json.loads(input_path.read_text())

    # Reconstruct Symbol and Edge objects from the behavior map
    nodes = [_node_from_dict(n) for n in behavior_map.get("nodes", [])]
    edges = [_edge_from_dict(e) for e in behavior_map.get("edges", [])]

    # Handle --list-entries: show detected entrypoints and exit
    if args.list_entries:
        entrypoints = detect_entrypoints(nodes, edges)
        if not entrypoints:
            print("[hypergumbo slice] No entrypoints detected")
        else:
            print(f"[hypergumbo slice] Detected {len(entrypoints)} entrypoint(s):")
            for ep in entrypoints:
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

    # If --inline, include full node/edge objects for self-contained output
    if getattr(args, "inline", False):
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
    _print_output_summary("slice", artifacts=[out_path])

    return 0


def cmd_search(args: argparse.Namespace) -> int:
    """Search for symbols by name pattern."""
    repo_root = Path(args.path).resolve()

    # Determine input file
    if args.input:
        input_path = Path(args.input)
        if not input_path.exists():
            print(f"Error: Input file not found: {args.input}", file=sys.stderr)
            return 1
    else:
        # Look for default results file
        input_path = repo_root / "hypergumbo.results.json"
        if not input_path.exists():
            print(
                "Error: No hypergumbo.results.json found. "
                "Run 'hypergumbo run' first or specify --input.",
                file=sys.stderr,
            )
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
    _print_output_summary("search", stdout_output=True)
    return 0


# HTTP methods that indicate API routes
HTTP_METHODS = {"get", "post", "put", "patch", "delete", "head", "options"}


def cmd_routes(args: argparse.Namespace) -> int:
    """Display API routes/endpoints from the behavior map."""
    repo_root = Path(args.path).resolve()

    # Determine input file
    if args.input:
        input_path = Path(args.input)
        if not input_path.exists():
            print(f"Error: Input file not found: {args.input}", file=sys.stderr)
            return 1
    else:
        # Look for default results file
        input_path = repo_root / "hypergumbo.results.json"
        if not input_path.exists():
            print(
                "Error: No hypergumbo.results.json found. "
                "Run 'hypergumbo run' first or specify --input.",
                file=sys.stderr,
            )
            return 1

    # Load behavior map
    behavior_map = json.loads(input_path.read_text())
    nodes = behavior_map.get("nodes", [])

    # Find route handlers - symbols with HTTP method markers in stable_id
    # or route concepts in meta.concepts
    routes: list[dict] = []
    for node in nodes:
        is_route = False

        # Check for route concept in meta.concepts (FRAMEWORK_PATTERNS phase)
        meta = node.get("meta") or {}
        concepts = meta.get("concepts", [])
        for concept in concepts:
            if isinstance(concept, dict) and concept.get("concept") == "route":
                is_route = True
                break

        # Fall back to checking stable_id for HTTP methods (legacy)
        if not is_route:
            stable_id = node.get("stable_id", "")
            if stable_id:
                # Check if stable_id is an HTTP method or comma-separated list of methods
                # e.g., "get", "post", or "get,post" for DRF @api_view(['GET', 'POST'])
                stable_id_lower = stable_id.lower()
                methods = stable_id_lower.split(",")
                if all(m.strip() in HTTP_METHODS for m in methods):
                    is_route = True

        if is_route:
            # Apply language filter
            if args.language and node.get("language") != args.language:
                continue
            routes.append(node)

    if not routes:
        print("No API routes found in the behavior map.")
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

            # Try concept metadata first (FRAMEWORK_PATTERNS phase)
            route_path = None
            method = None
            concepts = meta.get("concepts", [])
            for concept in concepts:
                if isinstance(concept, dict) and concept.get("concept") == "route":
                    route_path = concept.get("path")
                    method = concept.get("method")
                    break

            # Fall back to legacy metadata
            if not route_path:
                route_path = meta.get("route_path", "")
            if not method:
                method = meta.get("http_method") or route.get("stable_id", "")

            method = method.upper() if method else ""
            if route_path:
                print(f"  [{method}] {route_path} -> {name} (line {line})")
            else:
                print(f"  [{method}] {name} (line {line})")
        print()

    # Output summary (always at the end)
    _print_output_summary("routes", stdout_output=True)
    return 0


def cmd_explain(args: argparse.Namespace) -> int:
    """Explain a symbol with its callers and callees."""
    repo_root = Path(args.path).resolve()

    # Determine input file
    if args.input:
        input_path = Path(args.input)
        if not input_path.exists():
            print(f"Error: Input file not found: {args.input}", file=sys.stderr)
            return 1
    else:
        # Look for default results file
        input_path = repo_root / "hypergumbo.results.json"
        if not input_path.exists():
            print(
                "Error: No hypergumbo.results.json found. "
                "Run 'hypergumbo run' first or specify --input.",
                file=sys.stderr,
            )
            return 1

    # Load behavior map
    behavior_map = json.loads(input_path.read_text())
    nodes = behavior_map.get("nodes", [])
    edges = behavior_map.get("edges", [])

    # Build lookup tables
    nodes_by_id = {n["id"]: n for n in nodes}

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

        # Find callers (edges where dst = this symbol)
        callers = []
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
                src_line = edge.get("line", 0)
                callers.append((src_name, src_path, src_line))

        # Find callees (edges where src = this symbol)
        callees = []
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
                edge_line = edge.get("line", 0)
                callees.append((dst_name, dst_path, edge_line))

        # Display callers
        print()
        if callers:
            print(f"  Called by ({len(callers)}):")
            for caller_name, caller_path, caller_line in callers:
                print(f"    - {caller_name} ({caller_path}:{caller_line})")
        else:
            print("  Called by: (none)")

        # Display callees
        if callees:
            print(f"  Calls ({len(callees)}):")
            for callee_name, callee_path, callee_line in callees:
                print(f"    - {callee_name} ({callee_path}:{callee_line})")
        else:
            print("  Calls: (none)")

    # Output summary (always at the end)
    _print_output_summary("explain", stdout_output=True)
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


def cmd_export_capsule(args: argparse.Namespace) -> int:
    """Export the capsule as a tarball."""
    repo_root = Path(args.path).resolve()
    out_path = Path(args.out)
    capsule_dir = repo_root / ".hypergumbo"

    # Check if capsule exists
    if not capsule_dir.exists():
        print(f"Error: No capsule found at {capsule_dir}", file=sys.stderr)
        print("Run 'hypergumbo init' first to create a capsule.", file=sys.stderr)
        return 1

    export_capsule(repo_root, out_path, shareable=args.shareable)

    mode = "shareable" if args.shareable else "full"
    print(f"[hypergumbo export-capsule] Exported {mode} capsule to {out_path}")
    if args.shareable:
        print("  Privacy redactions applied (see SHAREABLE.txt in archive)")

    # Output summary (always at the end)
    _print_output_summary("export-capsule", artifacts=[out_path])

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

    # Determine input file
    if args.input:
        input_path = Path(args.input)
        if not input_path.exists():
            print(f"Error: Input file not found: {args.input}", file=sys.stderr)
            return 1
    else:
        input_path = repo_root / "hypergumbo.results.json"
        if not input_path.exists():
            print(
                "Error: No hypergumbo.results.json found. "
                "Run 'hypergumbo run' first or specify --input.",
                file=sys.stderr,
            )
            return 1

    # Load behavior map
    behavior_map = json.loads(input_path.read_text())
    nodes = behavior_map.get("nodes", [])
    edges = behavior_map.get("edges", [])

    # Build node ID set for filtering
    node_ids = {n["id"] for n in nodes}

    # Compute in-degree and out-degree for each node
    in_degree: dict[str, int] = {n["id"]: 0 for n in nodes}
    out_degree: dict[str, int] = {n["id"]: 0 for n in nodes}

    for edge in edges:
        src = edge.get("src", "")
        dst = edge.get("dst", "")
        if src in node_ids:
            out_degree[src] = out_degree.get(src, 0) + 1
        if dst in node_ids:
            in_degree[dst] = in_degree.get(dst, 0) + 1

    # Build list of symbols with their degrees
    # Tuple: (name, kind, in_degree, out_degree, total_degree, path)
    symbol_rows: list[tuple[str, str, int, int, int, str]] = []
    exclude_tests = getattr(args, "exclude_tests", False)

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
        _print_output_summary("symbols", stdout_output=True)
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
    _print_output_summary("symbols", stdout_output=True)
    return 0


def cmd_test_coverage(args: argparse.Namespace) -> int:
    """Estimate test coverage by analyzing which functions are called by tests.

    Identifies:
    - Hot spots: Functions called by many tests (potential redundancy)
    - Cold spots: Functions not called by any tests (need coverage)
    """
    repo_root = Path(args.path).resolve()

    # Determine input file
    if args.input:
        input_path = Path(args.input)
        if not input_path.exists():
            print(f"Error: Input file not found: {args.input}", file=sys.stderr)
            return 1
    else:
        input_path = repo_root / "hypergumbo.results.json"
        if not input_path.exists():
            print(
                "Error: No hypergumbo.results.json found. "
                "Run 'hypergumbo run' first or specify --input.",
                file=sys.stderr,
            )
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
    _print_output_summary("test-coverage", stdout_output=True, file=summary_file)
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

    sub = p.add_subparsers(dest="command")

    # hypergumbo [path] [-t tokens] (default sketch mode)
    sketch_epilog = """\
Examples:
  hypergumbo sketch .                   # Current directory, auto budget
  hypergumbo sketch ~/project -t 4000   # 4000-token limit
  hypergumbo sketch . -t 1000 -x        # Brief overview, no tests
  hypergumbo . -t 8000                  # Shorthand (sketch is default)

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
        action="store_true",
        help="Show progress indicator with ETA to stderr",
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
    p_sketch.set_defaults(func=cmd_sketch, first_party_priority=True, language_proportional=True)

    # hypergumbo init
    p_init = sub.add_parser("init", help="Initialize a hypergumbo capsule")
    p_init.add_argument(
        "path",
        nargs="?",
        default=".",
        help="Path to repo root (default: current directory)",
    )
    p_init.add_argument(
        "--capabilities",
        default="",
        help="Comma-separated capabilities (e.g. python,javascript)",
    )
    p_init.add_argument(
        "--assistant",
        choices=["template", "llm"],
        default="template",
        help="Plan assistant mode (default: template)",
    )
    p_init.add_argument(
        "--llm-input",
        choices=["tier0", "tier1", "tier2"],
        default="tier0",
        help="How much repo info may be sent to LLM during init",
    )
    p_init.set_defaults(func=cmd_init)

    # hypergumbo run
    run_epilog = """\
Examples:
  hypergumbo run .                      # Full analysis → hypergumbo.results.json
  hypergumbo run . --out analysis.json  # Custom output file
  hypergumbo run . --compact            # LLM-friendly: top symbols + summary
  hypergumbo run . --first-party-only   # Exclude vendored/external code
  hypergumbo run . -x                   # Exclude test files

After running, use search/explain/slice to query the results:
  hypergumbo search "parse"             # Find symbols containing "parse"
  hypergumbo explain "main"             # Show callers/callees of main
  hypergumbo slice --entry main         # Extract subgraph from main()

Output files:
  - hypergumbo.results.json             # Full behavior map
  - hypergumbo.results.4k.json          # Tiered outputs (auto-generated)
  - hypergumbo.results.16k.json
  - hypergumbo.results.64k.json"""

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
        default="hypergumbo.results.json",
        help="Output JSON path (default: hypergumbo.results.json)",
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
        "-x", "--exclude-tests",
        action="store_true",
        dest="exclude_tests",
        help="Exclude test files from analysis output",
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
    p_run.set_defaults(func=cmd_run)

    # hypergumbo slice
    slice_epilog = """\
Examples:
  hypergumbo slice --entry main              # Forward slice from main()
  hypergumbo slice --entry main --reverse    # What calls main()?
  hypergumbo slice --entry "UserService"     # Slice from a class
  hypergumbo slice --list-entries            # Show detected entry points
  hypergumbo slice --entry auto              # Auto-detect entry point

Use cases:
  - Understand what code main() depends on (forward slice)
  - Find all callers of a function (reverse slice)
  - Extract a focused subgraph for debugging or review

Requires: Run 'hypergumbo run .' first to create behavior map."""

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
        help="Output JSON path (default: slice.json)",
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
    p_slice.set_defaults(func=cmd_slice)

    # hypergumbo search
    search_epilog = """\
Examples:
  hypergumbo search "parse"               # Find symbols containing "parse"
  hypergumbo search "User" --kind class   # Find classes with "User"
  hypergumbo search "test" --limit 50     # Show more results
  hypergumbo search "handle" --language python

Requires: Run 'hypergumbo run .' first to create behavior map."""

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

Requires: Run 'hypergumbo run .' first to create behavior map."""

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

Requires: Run 'hypergumbo run .' first to create behavior map."""

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

    # hypergumbo export-capsule
    p_export = sub.add_parser(
        "export-capsule",
        help="Export capsule in shareable format",
    )
    p_export.add_argument(
        "path",
        nargs="?",
        default=".",
        help="Path to repo root (default: current directory)",
    )
    p_export.add_argument(
        "--shareable",
        action="store_true",
        help="Apply privacy redactions to make capsule safe to share",
    )
    p_export.add_argument(
        "--out",
        default="capsule.tar.gz",
        help="Output tarball path (default: capsule.tar.gz)",
    )
    p_export.set_defaults(func=cmd_export_capsule)

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

Requires: Run 'hypergumbo run .' first to create behavior map."""

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

Requires: Run 'hypergumbo run .' first to create behavior map."""

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
    out_path: Path,
    max_tier: int | None = None,
    max_files: int | None = None,
    compact: bool = False,
    coverage: float = 0.8,
    budgets: str | None = None,
    exclude_tests: bool = False,
    extra_excludes: list[str] | None = None,
    frameworks: str | None = None,
) -> list[Path]:
    """
    Run the behavior_map analysis for a repo and write JSON to out_path.

    Args:
        repo_root: Root directory of the repository
        out_path: Path to write the behavior map JSON
        max_tier: Optional maximum supply chain tier (1-4). Symbols with
            tier > max_tier are filtered out. None means no filtering.
        max_files: Optional maximum files per language analyzer. Limits
            how many files each analyzer processes (for large repos).
        compact: If True, output compact mode with coverage-based truncation
            and bag-of-words summary of omitted items.
        coverage: Target centrality coverage for compact mode (0.0-1.0).
        budgets: Token budget output specification. Comma-separated specs like
            "4k,16k,64k". Use "default" for DEFAULT_TIERS, "none" to disable.
            If None, defaults to generating DEFAULT_TIERS alongside full output.
        exclude_tests: If True, filter out symbols from test files after analysis.
            This removes test helpers and test fixtures from the behavior map.
        extra_excludes: Additional exclude patterns beyond DEFAULT_EXCLUDES.
            Affects profile detection (language stats). Use for excluding
            project-specific files like "*.json" or "vendor".
        frameworks: Framework specification (ADR-0003):
            - None: Auto-detect (default)
            - "none": Skip framework detection
            - "all": Check all frameworks for detected languages
            - "fastapi,celery": Only check specified frameworks

    Returns:
        List of file paths for all generated artifacts (main output + tier files).
    """
    _log_memory("start")
    generated_files: list[Path] = []
    behavior_map = new_behavior_map()

    # Detect repo profile (languages, frameworks)
    profile = detect_profile(repo_root, extra_excludes=extra_excludes, frameworks=frameworks)
    behavior_map["profile"] = profile.to_dict()

    # Detect internal package roots for supply chain classification
    package_roots = detect_package_roots(repo_root)

    # Run all language analyzers using consolidated registry
    # This replaces ~800 lines of repetitive analyzer invocation code
    (
        analysis_runs,
        all_symbols,
        all_edges,
        all_usage_contexts,
        limits,
        captured_symbols,
    ) = run_all_analyzers(repo_root, max_files=max_files)
    _log_memory("after analyzers")

    # Enrich symbols with framework concept metadata (ADR-0003)
    # This applies YAML-based patterns to add concept info (route, model, etc.)
    # to symbols based on their decorators, base classes, annotations, AND
    # usage contexts (v1.1.x) for call-based frameworks like Django URLs.
    detected_frameworks = set(profile.frameworks)
    enrich_symbols(all_symbols, detected_frameworks, all_usage_contexts)

    # Run cross-language linkers
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
    _log_memory("after linkers")

    # Filter out test files if requested
    if exclude_tests:
        # Filter symbols from test files
        filtered_symbols = [s for s in all_symbols if not _is_test_path(s.path)]
        # Get IDs of remaining symbols for edge filtering
        remaining_ids = {s.id for s in filtered_symbols}
        # Filter edges to only include those between remaining symbols
        filtered_edges = [
            e for e in all_edges
            if e.src in remaining_ids and e.dst in remaining_ids
        ]
        all_symbols = filtered_symbols
        all_edges = filtered_edges
        limits.test_files_excluded = True

    # Apply supply chain classification to all symbols
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
    behavior_map["metrics"] = compute_metrics(all_nodes, all_edge_dicts)

    # Detect and store entrypoints (computed from symbols, persisted for convenience)
    entrypoints = detect_entrypoints(all_symbols, all_edges)
    behavior_map["entrypoints"] = [ep.to_dict() for ep in entrypoints]
    del entrypoints  # Free Entrypoint objects

    # Compute supply chain summary
    # Note: derived_paths would be tracked during file discovery in a full implementation
    behavior_map["supply_chain_summary"] = _compute_supply_chain_summary(
        all_symbols, derived_paths=[]
    )

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
            behavior_map, all_symbols, all_edges, config
        )

    # Free memory: Symbol/Edge objects no longer needed after tier/compact processing
    # All data is now in behavior_map as dicts. For large repos like tensorflow (154k
    # symbols, 505k edges), this can free several GB of memory before final write.
    del all_symbols
    del all_edges
    del ranked_symbols
    gc.collect()
    _log_memory("after cleanup")

    with open(out_path, "w") as f:
        json.dump(behavior_map, f, indent=2)
    generated_files.append(out_path)
    _log_memory("after write")

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
    parser = build_parser()

    # Handle default sketch mode: if no subcommand given, insert "sketch"
    if argv is None:
        argv = sys.argv[1:]

    # Handle --help --all: show all subcommand help panels
    if ("--help" in argv or "-h" in argv) and "--all" in argv:
        print_all_help(parser)
        return 0

    subcommands = {"init", "run", "slice", "search", "routes", "explain", "catalog", "export-capsule", "sketch", "build-grammars", "test-coverage", "symbols"}

    # If no args, or first arg is not a subcommand (and not a flag), use sketch mode
    if not argv or (argv[0] not in subcommands and not argv[0].startswith("-")):
        argv = ["sketch"] + list(argv)

    args = parser.parse_args(argv)

    if not hasattr(args, "func"):  # pragma: no cover
        parser.print_help()  # pragma: no cover
        return 1  # pragma: no cover

    return args.func(args)

