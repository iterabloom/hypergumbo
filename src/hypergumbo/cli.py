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
import json
import sys
from pathlib import Path
from typing import Any, Dict, List

from . import __version__
from .analyze.c import analyze_c
from .analyze.elixir import analyze_elixir
from .analyze.html import analyze_html
from .analyze.java import analyze_java
from .analyze.js_ts import analyze_javascript
from .analyze.php import analyze_php
from .analyze.py import analyze_python
from .analyze.rust import analyze_rust
from .analyze.go import analyze_go
from .analyze.ruby import analyze_ruby
from .analyze.kotlin import analyze_kotlin
from .analyze.swift import analyze_swift
from .analyze.scala import analyze_scala
from .analyze.lua import analyze_lua
from .analyze.dart import analyze_dart
from .analyze.haskell import analyze_haskell
from .analyze.agda import analyze_agda
from .analyze.lean import analyze_lean
from .analyze.wolfram import analyze_wolfram
from .analyze.ocaml import analyze_ocaml
from .analyze.solidity import analyze_solidity
from .analyze.csharp import analyze_csharp
from .analyze.cpp import analyze_cpp
from .analyze.zig import analyze_zig
from .analyze.groovy import analyze_groovy
from .analyze.julia import analyze_julia
from .analyze.bash import analyze_bash
from .analyze.objc import analyze_objc
from .analyze.hcl import analyze_hcl
from .analyze.yaml_ansible import analyze_ansible
from .analyze.sql import analyze_sql_files
from .analyze.dockerfile import analyze_dockerfiles
from .analyze.cuda import analyze_cuda_files
from .analyze.verilog import analyze_verilog_files
from .analyze.cmake import analyze_cmake_files
from .analyze.make import analyze_make_files
from .analyze.vhdl import analyze_vhdl_files
from .analyze.graphql import analyze_graphql_files
from .analyze.nix import analyze_nix_files
from .analyze.glsl import analyze_glsl_files
from .analyze.wgsl import analyze_wgsl_files
from .analyze.xml_config import analyze_xml_files
from .analyze.json_config import analyze_json_files
from .analyze.r_lang import analyze_r_files
from .analyze.fortran import analyze_fortran_files
from .analyze.toml_config import analyze_toml_files
from .analyze.css import analyze_css_files
from .analyze.cobol import analyze_cobol
from .analyze.latex import analyze_latex
from .catalog import get_default_catalog, is_available
from .linkers.dependency import link_dependencies
from .linkers.graphql import link_graphql
from .linkers.graphql_resolver import link_graphql_resolvers
from .linkers.grpc import link_grpc
from .linkers.http import link_http
from .linkers.ipc import link_ipc
from .linkers.jni import link_jni
from .linkers.phoenix_ipc import link_phoenix_ipc
from .linkers.swift_objc import link_swift_objc
from .linkers.websocket import link_websocket
from .linkers.message_queue import link_message_queues
from .linkers.database_query import link_database_queries
from .linkers.event_sourcing import link_events
from .entrypoints import detect_entrypoints
from .export import export_capsule
from .ir import Symbol, Edge, Span
from .limits import Limits
from .metrics import compute_metrics
from .profile import detect_profile
from .llm_assist import generate_plan_with_fallback
from .schema import new_behavior_map
from .sketch import generate_sketch
from .slice import SliceQuery, slice_graph, AmbiguousEntryError, rank_slice_nodes
from .supply_chain import classify_file, detect_package_roots
from .ranking import rank_symbols, _is_test_path
from .compact import (
    format_compact_behavior_map,
    format_tiered_behavior_map,
    generate_tier_filename,
    parse_tier_spec,
    CompactConfig,
    DEFAULT_TIERS,
)
from .build_grammars import build_all_grammars, check_grammar_availability


def cmd_sketch(args: argparse.Namespace) -> int:
    """Generate token-budgeted Markdown sketch to stdout."""
    repo_root = Path(args.path).resolve()

    if not repo_root.exists():
        print(f"Error: path does not exist: {repo_root}", file=sys.stderr)
        return 1

    max_tokens = args.tokens if args.tokens else None
    exclude_tests = getattr(args, "exclude_tests", False)
    first_party_priority = getattr(args, "first_party_priority", True)
    sketch = generate_sketch(
        repo_root,
        max_tokens=max_tokens,
        exclude_tests=exclude_tests,
        first_party_priority=first_party_priority,
    )
    print(sketch)
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

    return 0


def cmd_run(args: argparse.Namespace) -> int:
    # The positional argument for `run` is called `path` in the parser below.
    repo_root = Path(args.path).resolve()
    out_path = Path(args.out)
    max_tier = getattr(args, "max_tier", None)
    max_files = getattr(args, "max_files", None)
    compact = getattr(args, "compact", False)
    coverage = getattr(args, "coverage", 0.8)
    tiers = getattr(args, "tiers", None)
    exclude_tests = getattr(args, "exclude_tests", False)

    run_behavior_map(
        repo_root=repo_root,
        out_path=out_path,
        max_tier=max_tier,
        max_files=max_files,
        compact=compact,
        coverage=coverage,
        tiers=tiers,
        exclude_tests=exclude_tests,
    )
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


def cmd_slice(args: argparse.Namespace) -> int:
    """Execute the slice command."""
    repo_root = Path(args.path).resolve()
    out_path = Path(args.out)

    # Determine input: use --input if provided, otherwise run analysis
    if args.input:
        input_path = Path(args.input)
        if not input_path.exists():
            print(f"Error: Input file not found: {args.input}", file=sys.stderr)
            return 1
        behavior_map = json.loads(input_path.read_text())
    else:
        # Check for existing results file
        default_results = repo_root / "hypergumbo.results.json"
        if default_results.exists():
            behavior_map = json.loads(default_results.read_text())
        else:
            # Run analysis first
            behavior_map = new_behavior_map()
            profile = detect_profile(repo_root)
            behavior_map["profile"] = profile.to_dict()

            analysis_runs = []
            all_nodes: List[Dict[str, Any]] = []
            all_edges: List[Dict[str, Any]] = []

            py_result = analyze_python(repo_root)
            if py_result.run is not None:
                analysis_runs.append(py_result.run.to_dict())
            all_nodes.extend(s.to_dict() for s in py_result.symbols)
            all_edges.extend(e.to_dict() for e in py_result.edges)

            html_result = analyze_html(repo_root)
            if html_result.run is not None:
                analysis_runs.append(html_result.run.to_dict())
            all_nodes.extend(s.to_dict() for s in html_result.symbols)
            all_edges.extend(e.to_dict() for e in html_result.edges)

            behavior_map["analysis_runs"] = analysis_runs
            behavior_map["nodes"] = all_nodes
            behavior_map["edges"] = all_edges
            behavior_map["metrics"] = compute_metrics(all_nodes, all_edges)
            behavior_map["limits"] = Limits().to_dict()

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
        return 0

    # Handle --entry auto: use detected entrypoints
    entry = args.entry
    if entry == "auto":
        entrypoints = detect_entrypoints(nodes, edges)
        if not entrypoints:
            print("Error: No entrypoints detected. Use --entry to specify manually.",
                  file=sys.stderr)
            return 1
        # Use the highest confidence entrypoint
        best = max(entrypoints, key=lambda e: e.confidence)
        entry = best.symbol_id
        print(f"[hypergumbo slice] Auto-detected entry: {best.label}")
        print(f"  {entry}")

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
        name = node.get("name", "")
        kind = node.get("kind", "")
        lang = node.get("language", "")
        path = node.get("path", "")
        span = node.get("span", {})
        line = span.get("start_line", 0)

        print(f"  {name} ({kind})")
        print(f"    {path}:{line}")
        print(f"    language: {lang}")
        print()

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
    routes: list[dict] = []
    for node in nodes:
        stable_id = node.get("stable_id", "")
        if stable_id:
            # Check if stable_id is an HTTP method or comma-separated list of methods
            # e.g., "get", "post", or "get,post" for DRF @api_view(['GET', 'POST'])
            stable_id_lower = stable_id.lower()
            methods = stable_id_lower.split(",")
            if all(m.strip() in HTTP_METHODS for m in methods):
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
            method = route.get("stable_id", "").upper()
            span = route.get("span", {})
            line = span.get("start_line", 0)
            # Include route path if available
            meta = route.get("meta", {}) or {}
            route_path = meta.get("route_path", "")
            if route_path:
                print(f"  [{method}] {route_path} -> {name} (line {line})")
            else:
                print(f"  [{method}] {name} (line {line})")
        print()

    return 0


def cmd_catalog(args: argparse.Namespace) -> int:
    """Display available passes and packs."""
    catalog = get_default_catalog()

    # Filter passes based on --show-all
    if args.show_all:
        passes = catalog.passes
    else:
        passes = catalog.get_core_passes()

    print("Available Passes:")
    for p in passes:
        avail = is_available(p)
        status = "" if avail else " [not installed]"
        if p.availability == "core":
            print(f"  - {p.id} (core): {p.description}{status}")
        else:
            print(f"  - {p.id} (extra: {p.requires}): {p.description}{status}")

    print()
    print("Available Packs:")
    for pack in catalog.packs:
        print(f"  - {pack.id}: {pack.description}")

    if not args.show_all:
        extras = catalog.get_extra_passes()
        if extras:
            print()
            print(f"Use --show-all to see {len(extras)} additional extra(s)")

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


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="hypergumbo",
        description="Generate behavior maps and sketches for AI coding agents.",
    )
    p.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {__version__}",
        help="Print version and exit",
    )

    sub = p.add_subparsers(dest="command")

    # hypergumbo [path] [-t tokens] (default sketch mode)
    p_sketch = sub.add_parser(
        "sketch",
        help="Generate token-budgeted Markdown sketch (default mode)",
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
    p_sketch.set_defaults(func=cmd_sketch, first_party_priority=True)

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
    p_run = sub.add_parser("run", help="Run analyzer capsule on a repo")
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
        "--tiers",
        type=str,
        default=None,
        help="Generate tiered output files at token budgets. Comma-separated specs "
             "like '4k,16k,64k'. Use 'default' for standard tiers (4k,16k,64k), "
             "'none' to disable. Default: generate tiered files alongside full output.",
    )
    p_run.add_argument(
        "-x", "--exclude-tests",
        action="store_true",
        dest="exclude_tests",
        help="Exclude test files from analysis output",
    )
    p_run.set_defaults(func=cmd_run)

    # hypergumbo slice
    p_slice = sub.add_parser("slice", help="Produce a reduced behavior slice")
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
        "--inline",
        action="store_true",
        help="Include full node/edge objects in output (not just IDs). "
             "Makes slice.json self-contained without needing the behavior map.",
    )
    p_slice.set_defaults(func=cmd_slice)

    # hypergumbo search
    p_search = sub.add_parser("search", help="Search for symbols by name")
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
    p_routes = sub.add_parser("routes", help="Display API routes/endpoints")
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

    # hypergumbo catalog
    p_catalog = sub.add_parser("catalog", help="[stub] Show available passes/packs")
    p_catalog.add_argument(
        "--show-all",
        action="store_true",
        help="Include extras that require optional dependencies",
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
    tiers: str | None = None,
    exclude_tests: bool = False,
) -> None:
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
        tiers: Tiered output specification. Comma-separated tier specs like
            "4k,16k,64k". Use "default" for DEFAULT_TIERS, "none" to disable.
            If None, defaults to generating DEFAULT_TIERS alongside full output.
        exclude_tests: If True, filter out symbols from test files after analysis.
            This removes test helpers and test fixtures from the behavior map.
    """
    behavior_map = new_behavior_map()

    # Detect repo profile (languages, frameworks)
    profile = detect_profile(repo_root)
    behavior_map["profile"] = profile.to_dict()

    # Detect internal package roots for supply chain classification
    package_roots = detect_package_roots(repo_root)

    analysis_runs = []
    all_symbols: list[Symbol] = []
    all_edges: list[Edge] = []
    limits = Limits()
    limits.max_files_per_analyzer = max_files

    # Run Python analysis
    py_result = analyze_python(repo_root, max_files=max_files)
    if py_result.run is not None:
        analysis_runs.append(py_result.run.to_dict())
    all_symbols.extend(py_result.symbols)
    all_edges.extend(py_result.edges)

    # Run HTML analysis
    html_result = analyze_html(repo_root, max_files=max_files)
    if html_result.run is not None:
        analysis_runs.append(html_result.run.to_dict())
    all_symbols.extend(html_result.symbols)
    all_edges.extend(html_result.edges)

    # Run JavaScript/TypeScript/Svelte analysis (optional, requires tree-sitter)
    js_result = analyze_javascript(repo_root, max_files=max_files)
    if js_result.run is not None:
        if js_result.skipped:
            # Track skipped pass in limits
            limits.skipped_passes.append({
                "pass": js_result.run.pass_id,
                "reason": js_result.skip_reason,
            })
        else:
            analysis_runs.append(js_result.run.to_dict())
            all_symbols.extend(js_result.symbols)
            all_edges.extend(js_result.edges)

    # Run PHP analysis (optional, requires tree-sitter-php)
    php_result = analyze_php(repo_root)
    if php_result.run is not None:
        if php_result.skipped:
            limits.skipped_passes.append({
                "pass": php_result.run.pass_id,
                "reason": php_result.skip_reason,
            })
        else:
            analysis_runs.append(php_result.run.to_dict())
            all_symbols.extend(php_result.symbols)
            all_edges.extend(php_result.edges)

    # Run C analysis (optional, requires tree-sitter-c)
    c_symbols: list[Symbol] = []
    c_result = analyze_c(repo_root)
    if c_result.run is not None:
        if c_result.skipped:
            limits.skipped_passes.append({
                "pass": c_result.run.pass_id,
                "reason": c_result.skip_reason,
            })
        else:
            analysis_runs.append(c_result.run.to_dict())
            c_symbols = list(c_result.symbols)
            all_symbols.extend(c_symbols)
            all_edges.extend(c_result.edges)

    # Run Java analysis (optional, requires tree-sitter-java)
    java_symbols: list[Symbol] = []
    java_result = analyze_java(repo_root)
    if java_result.run is not None:
        if java_result.skipped:
            limits.skipped_passes.append({
                "pass": java_result.run.pass_id,
                "reason": java_result.skip_reason,
            })
        else:
            analysis_runs.append(java_result.run.to_dict())
            java_symbols = list(java_result.symbols)
            all_symbols.extend(java_symbols)
            all_edges.extend(java_result.edges)

    # Run Elixir analysis (optional, requires tree-sitter-language-pack)
    elixir_result = analyze_elixir(repo_root)
    if elixir_result.run is not None:
        if elixir_result.skipped:
            limits.skipped_passes.append({
                "pass": elixir_result.run.pass_id,
                "reason": elixir_result.skip_reason,
            })
        else:
            analysis_runs.append(elixir_result.run.to_dict())
            all_symbols.extend(elixir_result.symbols)
            all_edges.extend(elixir_result.edges)

    # Run Rust analysis (optional, requires tree-sitter-rust)
    rust_result = analyze_rust(repo_root)
    if rust_result.run is not None:
        if rust_result.skipped:
            limits.skipped_passes.append({
                "pass": rust_result.run.pass_id,
                "reason": rust_result.skip_reason,
            })
        else:
            analysis_runs.append(rust_result.run.to_dict())
            all_symbols.extend(rust_result.symbols)
            all_edges.extend(rust_result.edges)

    # Run Go analysis (optional, requires tree-sitter-go)
    go_result = analyze_go(repo_root)
    if go_result.run is not None:
        if go_result.skipped:
            limits.skipped_passes.append({
                "pass": go_result.run.pass_id,
                "reason": go_result.skip_reason,
            })
        else:
            analysis_runs.append(go_result.run.to_dict())
            all_symbols.extend(go_result.symbols)
            all_edges.extend(go_result.edges)

    # Run Ruby analysis (optional, requires tree-sitter-ruby)
    ruby_result = analyze_ruby(repo_root)
    if ruby_result.run is not None:
        if ruby_result.skipped:
            limits.skipped_passes.append({
                "pass": ruby_result.run.pass_id,
                "reason": ruby_result.skip_reason,
            })
        else:
            analysis_runs.append(ruby_result.run.to_dict())
            all_symbols.extend(ruby_result.symbols)
            all_edges.extend(ruby_result.edges)

    # Run Kotlin analysis (optional, requires tree-sitter-kotlin)
    kotlin_result = analyze_kotlin(repo_root)
    if kotlin_result.run is not None:
        if kotlin_result.skipped:
            limits.skipped_passes.append({
                "pass": kotlin_result.run.pass_id,
                "reason": kotlin_result.skip_reason,
            })
        else:
            analysis_runs.append(kotlin_result.run.to_dict())
            all_symbols.extend(kotlin_result.symbols)
            all_edges.extend(kotlin_result.edges)

    # Run Swift analysis (optional, requires tree-sitter-swift)
    swift_result = analyze_swift(repo_root)
    if swift_result.run is not None:
        if swift_result.skipped:
            limits.skipped_passes.append({
                "pass": swift_result.run.pass_id,
                "reason": swift_result.skip_reason,
            })
        else:
            analysis_runs.append(swift_result.run.to_dict())
            all_symbols.extend(swift_result.symbols)
            all_edges.extend(swift_result.edges)

    # Run Scala analysis (optional, requires tree-sitter-scala)
    scala_result = analyze_scala(repo_root)
    if scala_result.run is not None:
        if scala_result.skipped:
            limits.skipped_passes.append({
                "pass": scala_result.run.pass_id,
                "reason": scala_result.skip_reason,
            })
        else:
            analysis_runs.append(scala_result.run.to_dict())
            all_symbols.extend(scala_result.symbols)
            all_edges.extend(scala_result.edges)

    # Run Lua analysis (optional, requires tree-sitter-lua)
    lua_result = analyze_lua(repo_root)
    if lua_result.run is not None:
        if lua_result.skipped:
            limits.skipped_passes.append({
                "pass": lua_result.run.pass_id,
                "reason": lua_result.skip_reason,
            })
        else:
            analysis_runs.append(lua_result.run.to_dict())
            all_symbols.extend(lua_result.symbols)
            all_edges.extend(lua_result.edges)

    # Run Dart/Flutter analysis (optional, requires tree-sitter-language-pack)
    dart_result = analyze_dart(repo_root)
    if dart_result.run is not None:
        if dart_result.skipped:  # pragma: no cover - requires missing tree-sitter
            limits.skipped_passes.append({
                "pass": dart_result.run.pass_id,
                "reason": dart_result.skip_reason,
            })
        else:
            analysis_runs.append(dart_result.run.to_dict())
            all_symbols.extend(dart_result.symbols)
            all_edges.extend(dart_result.edges)

    # Run Haskell analysis (optional, requires tree-sitter-haskell)
    haskell_result = analyze_haskell(repo_root)
    if haskell_result.run is not None:
        if haskell_result.skipped:
            limits.skipped_passes.append({
                "pass": haskell_result.run.pass_id,
                "reason": haskell_result.skip_reason,
            })
        else:
            analysis_runs.append(haskell_result.run.to_dict())
            all_symbols.extend(haskell_result.symbols)
            all_edges.extend(haskell_result.edges)

    # Run Agda analysis (optional, requires tree-sitter-agda)
    agda_result = analyze_agda(repo_root)
    if agda_result.run is not None:
        if agda_result.skipped:  # pragma: no cover - agda installed
            limits.skipped_passes.append({
                "pass": agda_result.run.pass_id,
                "reason": agda_result.skip_reason,
            })
        else:
            analysis_runs.append(agda_result.run.to_dict())
            all_symbols.extend(agda_result.symbols)
            all_edges.extend(agda_result.edges)

    # Run Lean analysis (optional, requires tree-sitter-lean built from source)
    lean_result = analyze_lean(repo_root)
    if lean_result.run is not None:
        if lean_result.skipped:  # pragma: no cover - lean not installed
            limits.skipped_passes.append({
                "pass": lean_result.run.pass_id,
                "reason": lean_result.skip_reason,
            })
        else:
            analysis_runs.append(lean_result.run.to_dict())
            all_symbols.extend(lean_result.symbols)
            all_edges.extend(lean_result.edges)

    # Run Wolfram analysis (optional, requires tree-sitter-wolfram built from source)
    wolfram_result = analyze_wolfram(repo_root)
    if wolfram_result.run is not None:
        if wolfram_result.skipped:  # pragma: no cover - wolfram not installed
            limits.skipped_passes.append({
                "pass": wolfram_result.run.pass_id,
                "reason": wolfram_result.skip_reason,
            })
        else:
            analysis_runs.append(wolfram_result.run.to_dict())
            all_symbols.extend(wolfram_result.symbols)
            all_edges.extend(wolfram_result.edges)

    # Run OCaml analysis (optional, requires tree-sitter-ocaml)
    ocaml_result = analyze_ocaml(repo_root)
    if ocaml_result.run is not None:
        if ocaml_result.skipped:
            limits.skipped_passes.append({
                "pass": ocaml_result.run.pass_id,
                "reason": ocaml_result.skip_reason,
            })
        else:
            analysis_runs.append(ocaml_result.run.to_dict())
            all_symbols.extend(ocaml_result.symbols)
            all_edges.extend(ocaml_result.edges)

    # Run Solidity analysis (optional, requires tree-sitter-solidity)
    solidity_result = analyze_solidity(repo_root)
    if solidity_result.run is not None:
        if solidity_result.skipped:  # pragma: no cover - solidity installed
            limits.skipped_passes.append({
                "pass": solidity_result.run.pass_id,
                "reason": solidity_result.skip_reason,
            })
        else:
            analysis_runs.append(solidity_result.run.to_dict())
            all_symbols.extend(solidity_result.symbols)
            all_edges.extend(solidity_result.edges)

    # Run C# analysis (optional, requires tree-sitter-c-sharp)
    csharp_result = analyze_csharp(repo_root)
    if csharp_result.run is not None:
        if csharp_result.skipped:  # pragma: no cover - c-sharp installed
            limits.skipped_passes.append({
                "pass": csharp_result.run.pass_id,
                "reason": csharp_result.skip_reason,
            })
        else:
            analysis_runs.append(csharp_result.run.to_dict())
            all_symbols.extend(csharp_result.symbols)
            all_edges.extend(csharp_result.edges)

    # Run C++ analysis (optional, requires tree-sitter-cpp)
    cpp_result = analyze_cpp(repo_root)
    if cpp_result.run is not None:
        if cpp_result.skipped:  # pragma: no cover - cpp installed
            limits.skipped_passes.append({
                "pass": cpp_result.run.pass_id,
                "reason": cpp_result.skip_reason,
            })
        else:
            analysis_runs.append(cpp_result.run.to_dict())
            all_symbols.extend(cpp_result.symbols)
            all_edges.extend(cpp_result.edges)

    # Run Zig analysis (optional, requires tree-sitter-zig)
    zig_result = analyze_zig(repo_root)
    if zig_result.run is not None:
        if zig_result.skipped:  # pragma: no cover - zig installed
            limits.skipped_passes.append({
                "pass": zig_result.run.pass_id,
                "reason": zig_result.skip_reason,
            })
        else:
            analysis_runs.append(zig_result.run.to_dict())
            all_symbols.extend(zig_result.symbols)
            all_edges.extend(zig_result.edges)

    # Run Groovy analysis (optional, requires tree-sitter-groovy)
    groovy_result = analyze_groovy(repo_root)
    if groovy_result.run is not None:
        if groovy_result.skipped:  # pragma: no cover - groovy installed
            limits.skipped_passes.append({
                "pass": groovy_result.run.pass_id,
                "reason": groovy_result.skip_reason,
            })
        else:
            analysis_runs.append(groovy_result.run.to_dict())
            all_symbols.extend(groovy_result.symbols)
            all_edges.extend(groovy_result.edges)

    # Run Julia analysis (optional, requires tree-sitter-julia)
    julia_result = analyze_julia(repo_root)
    if julia_result.run is not None:
        if julia_result.skipped:  # pragma: no cover - julia installed
            limits.skipped_passes.append({
                "pass": julia_result.run.pass_id,
                "reason": julia_result.skip_reason,
            })
        else:
            analysis_runs.append(julia_result.run.to_dict())
            all_symbols.extend(julia_result.symbols)
            all_edges.extend(julia_result.edges)

    # Run Bash/shell analysis (optional, requires tree-sitter-bash)
    bash_result = analyze_bash(repo_root)
    if bash_result.run is not None:
        if bash_result.skipped:  # pragma: no cover - bash installed
            limits.skipped_passes.append({
                "pass": bash_result.run.pass_id,
                "reason": bash_result.skip_reason,
            })
        else:
            analysis_runs.append(bash_result.run.to_dict())
            all_symbols.extend(bash_result.symbols)
            all_edges.extend(bash_result.edges)

    # Run Objective-C analysis (optional, requires tree-sitter-objc)
    objc_result = analyze_objc(repo_root)
    if objc_result.run is not None:
        if objc_result.skipped:  # pragma: no cover - objc installed
            limits.skipped_passes.append({
                "pass": objc_result.run.pass_id,
                "reason": objc_result.skip_reason,
            })
        else:
            analysis_runs.append(objc_result.run.to_dict())
            all_symbols.extend(objc_result.symbols)
            all_edges.extend(objc_result.edges)

    # Run HCL/Terraform analysis (optional, requires tree-sitter-hcl)
    hcl_result = analyze_hcl(repo_root)
    if hcl_result.run is not None:
        if hcl_result.skipped:  # pragma: no cover - hcl installed
            limits.skipped_passes.append({
                "pass": hcl_result.run.pass_id,
                "reason": hcl_result.skip_reason,
            })
        else:
            analysis_runs.append(hcl_result.run.to_dict())
            all_symbols.extend(hcl_result.symbols)
            all_edges.extend(hcl_result.edges)

    # Run YAML/Ansible analysis (optional, requires tree-sitter-yaml)
    ansible_result = analyze_ansible(repo_root)
    if ansible_result.run is not None:
        if ansible_result.skipped:  # pragma: no cover - yaml installed
            limits.skipped_passes.append({
                "pass": ansible_result.run.pass_id,
                "reason": ansible_result.skip_reason,
            })
        else:
            analysis_runs.append(ansible_result.run.to_dict())
            all_symbols.extend(ansible_result.symbols)
            all_edges.extend(ansible_result.edges)

    # Run SQL analysis (optional, requires tree-sitter-sql)
    sql_result = analyze_sql_files(repo_root)
    if sql_result.run is not None:
        if sql_result.skipped:  # pragma: no cover - sql installed
            limits.skipped_passes.append({
                "pass": sql_result.run.pass_id,
                "reason": sql_result.skip_reason,
            })
        else:
            analysis_runs.append(sql_result.run.to_dict())
            all_symbols.extend(sql_result.symbols)
            all_edges.extend(sql_result.edges)

    # Run Dockerfile analysis (optional, requires tree-sitter-dockerfile)
    dockerfile_result = analyze_dockerfiles(repo_root)
    if dockerfile_result.run is not None:
        if dockerfile_result.skipped:  # pragma: no cover - dockerfile installed
            limits.skipped_passes.append({
                "pass": dockerfile_result.run.pass_id,
                "reason": dockerfile_result.skip_reason,
            })
        else:
            analysis_runs.append(dockerfile_result.run.to_dict())
            all_symbols.extend(dockerfile_result.symbols)
            all_edges.extend(dockerfile_result.edges)

    # Run CUDA analysis (optional, requires tree-sitter-cuda)
    cuda_result = analyze_cuda_files(repo_root)
    if cuda_result.run is not None:
        if cuda_result.skipped:  # pragma: no cover - cuda installed
            limits.skipped_passes.append({
                "pass": cuda_result.run.pass_id,
                "reason": cuda_result.skip_reason,
            })
        else:
            analysis_runs.append(cuda_result.run.to_dict())
            all_symbols.extend(cuda_result.symbols)
            all_edges.extend(cuda_result.edges)

    # Run Verilog/SystemVerilog analysis (optional, requires tree-sitter-verilog)
    verilog_result = analyze_verilog_files(repo_root)
    if verilog_result.run is not None:
        if verilog_result.skipped:  # pragma: no cover - verilog installed
            limits.skipped_passes.append({
                "pass": verilog_result.run.pass_id,
                "reason": verilog_result.skip_reason,
            })
        else:
            analysis_runs.append(verilog_result.run.to_dict())
            all_symbols.extend(verilog_result.symbols)
            all_edges.extend(verilog_result.edges)

    # Run CMake analysis (optional, requires tree-sitter-cmake)
    cmake_result = analyze_cmake_files(repo_root)
    if cmake_result.run is not None:
        if cmake_result.skipped:  # pragma: no cover - cmake installed
            limits.skipped_passes.append({
                "pass": cmake_result.run.pass_id,
                "reason": cmake_result.skip_reason,
            })
        else:
            analysis_runs.append(cmake_result.run.to_dict())
            all_symbols.extend(cmake_result.symbols)
            all_edges.extend(cmake_result.edges)

    # Run Make analysis (optional, requires tree-sitter-make)
    make_result = analyze_make_files(repo_root)
    if make_result.run is not None:
        if make_result.skipped:  # pragma: no cover - make installed
            limits.skipped_passes.append({
                "pass": make_result.run.pass_id,
                "reason": make_result.skip_reason,
            })
        else:
            analysis_runs.append(make_result.run.to_dict())
            all_symbols.extend(make_result.symbols)
            all_edges.extend(make_result.edges)

    # Run VHDL analysis (optional, requires tree-sitter-vhdl)
    vhdl_result = analyze_vhdl_files(repo_root)
    if vhdl_result.run is not None:
        if vhdl_result.skipped:  # pragma: no cover - vhdl installed
            limits.skipped_passes.append({
                "pass": vhdl_result.run.pass_id,
                "reason": vhdl_result.skip_reason,
            })
        else:
            analysis_runs.append(vhdl_result.run.to_dict())
            all_symbols.extend(vhdl_result.symbols)
            all_edges.extend(vhdl_result.edges)

    # Run GraphQL analysis (optional, requires tree-sitter-graphql)
    graphql_result = analyze_graphql_files(repo_root)
    if graphql_result.run is not None:
        if graphql_result.skipped:  # pragma: no cover - graphql installed
            limits.skipped_passes.append({
                "pass": graphql_result.run.pass_id,
                "reason": graphql_result.skip_reason,
            })
        else:
            analysis_runs.append(graphql_result.run.to_dict())
            all_symbols.extend(graphql_result.symbols)
            all_edges.extend(graphql_result.edges)

    # Run Nix analysis (optional, requires tree-sitter-nix)
    nix_result = analyze_nix_files(repo_root)
    if nix_result.run is not None:
        if nix_result.skipped:  # pragma: no cover - nix installed
            limits.skipped_passes.append({
                "pass": nix_result.run.pass_id,
                "reason": nix_result.skip_reason,
            })
        else:
            analysis_runs.append(nix_result.run.to_dict())
            all_symbols.extend(nix_result.symbols)
            all_edges.extend(nix_result.edges)

    # Run GLSL analysis (optional, requires tree-sitter-glsl)
    glsl_result = analyze_glsl_files(repo_root)
    if glsl_result.run is not None:
        if glsl_result.skipped:  # pragma: no cover - glsl installed
            limits.skipped_passes.append({
                "pass": glsl_result.run.pass_id,
                "reason": glsl_result.skip_reason,
            })
        else:
            analysis_runs.append(glsl_result.run.to_dict())
            all_symbols.extend(glsl_result.symbols)
            all_edges.extend(glsl_result.edges)

    # Run WGSL analysis (optional, requires tree-sitter-wgsl)
    wgsl_result = analyze_wgsl_files(repo_root)
    if wgsl_result.run is not None:
        if wgsl_result.skipped:  # pragma: no cover - wgsl installed
            limits.skipped_passes.append({
                "pass": wgsl_result.run.pass_id,
                "reason": wgsl_result.skip_reason,
            })
        else:
            analysis_runs.append(wgsl_result.run.to_dict())
            all_symbols.extend(wgsl_result.symbols)
            all_edges.extend(wgsl_result.edges)

    # Run XML analysis (optional, requires tree-sitter-xml)
    xml_result = analyze_xml_files(repo_root)
    if xml_result.run is not None:
        if xml_result.skipped:  # pragma: no cover - xml installed
            limits.skipped_passes.append({
                "pass": xml_result.run.pass_id,
                "reason": xml_result.skip_reason,
            })
        else:
            analysis_runs.append(xml_result.run.to_dict())
            all_symbols.extend(xml_result.symbols)
            all_edges.extend(xml_result.edges)

    # Run JSON analysis (optional, requires tree-sitter-json)
    json_result = analyze_json_files(repo_root)
    if json_result.run is not None:
        if json_result.skipped:  # pragma: no cover - json installed
            limits.skipped_passes.append({
                "pass": json_result.run.pass_id,
                "reason": json_result.skip_reason,
            })
        else:
            analysis_runs.append(json_result.run.to_dict())
            all_symbols.extend(json_result.symbols)
            all_edges.extend(json_result.edges)

    # Run R analysis (optional, requires tree-sitter-r)
    r_result = analyze_r_files(repo_root)
    if r_result.run is not None:
        if r_result.skipped:  # pragma: no cover - r installed
            limits.skipped_passes.append({
                "pass": r_result.run.pass_id,
                "reason": r_result.skip_reason,
            })
        else:
            analysis_runs.append(r_result.run.to_dict())
            all_symbols.extend(r_result.symbols)
            all_edges.extend(r_result.edges)

    # Run Fortran analysis (optional, requires tree-sitter-fortran)
    fortran_result = analyze_fortran_files(repo_root)
    if fortran_result.run is not None:
        if fortran_result.skipped:  # pragma: no cover - fortran installed
            limits.skipped_passes.append({
                "pass": fortran_result.run.pass_id,
                "reason": fortran_result.skip_reason,
            })
        else:
            analysis_runs.append(fortran_result.run.to_dict())
            all_symbols.extend(fortran_result.symbols)
            all_edges.extend(fortran_result.edges)

    # Run TOML analysis (optional, requires tree-sitter-toml)
    toml_result = analyze_toml_files(repo_root)
    if toml_result.run is not None:
        if toml_result.skipped:  # pragma: no cover - toml installed
            limits.skipped_passes.append({
                "pass": toml_result.run.pass_id,
                "reason": toml_result.skip_reason,
            })
        else:
            analysis_runs.append(toml_result.run.to_dict())
            all_symbols.extend(toml_result.symbols)
            all_edges.extend(toml_result.edges)

    # Run CSS analysis (optional, requires tree-sitter-css)
    css_result = analyze_css_files(repo_root)
    if css_result.run is not None:
        if css_result.skipped:  # pragma: no cover - css installed
            limits.skipped_passes.append({
                "pass": css_result.run.pass_id,
                "reason": css_result.skip_reason,
            })
        else:
            analysis_runs.append(css_result.run.to_dict())
            all_symbols.extend(css_result.symbols)
            all_edges.extend(css_result.edges)

    cobol_result = analyze_cobol(repo_root)
    if not cobol_result.skipped:
        if cobol_result.run is not None:
            analysis_runs.append(cobol_result.run.to_dict())
        all_symbols.extend(cobol_result.symbols)
        all_edges.extend(cobol_result.edges)

    latex_result = analyze_latex(repo_root)
    if not latex_result.skipped:
        if latex_result.run is not None:
            analysis_runs.append(latex_result.run.to_dict())
        all_symbols.extend(latex_result.symbols)
        all_edges.extend(latex_result.edges)

    # Run cross-language linkers

    # JNI linker: connect Java native methods to C implementations
    if java_symbols and c_symbols:
        jni_result = link_jni(java_symbols, c_symbols)
        if jni_result.run is not None:
            analysis_runs.append(jni_result.run.to_dict())
            all_edges.extend(jni_result.edges)

    # IPC linker: detect Electron IPC, postMessage, Web Workers
    ipc_result = link_ipc(repo_root)
    if ipc_result.run is not None:
        analysis_runs.append(ipc_result.run.to_dict())
        all_symbols.extend(ipc_result.symbols)
        all_edges.extend(ipc_result.edges)

    # WebSocket linker: detect Socket.io, native WebSocket, ws package patterns
    ws_result = link_websocket(repo_root)
    if ws_result.run is not None:
        analysis_runs.append(ws_result.run.to_dict())
        all_symbols.extend(ws_result.symbols)
        all_edges.extend(ws_result.edges)

    # Phoenix IPC linker: detect Phoenix Channels and LiveView patterns
    phoenix_result = link_phoenix_ipc(repo_root)
    if phoenix_result.run is not None:
        analysis_runs.append(phoenix_result.run.to_dict())
        all_symbols.extend(phoenix_result.symbols)
        all_edges.extend(phoenix_result.edges)

    # Swift/Objective-C linker: detect @objc, NSObject, bridging headers
    swift_objc_result = link_swift_objc(repo_root)
    if swift_objc_result.run is not None:
        analysis_runs.append(swift_objc_result.run.to_dict())
        all_symbols.extend(swift_objc_result.symbols)
        all_edges.extend(swift_objc_result.edges)

    # gRPC linker: detect gRPC service definitions, stubs, and servers
    grpc_result = link_grpc(repo_root)
    if grpc_result.run is not None:
        analysis_runs.append(grpc_result.run.to_dict())
        all_symbols.extend(grpc_result.symbols)
        all_edges.extend(grpc_result.edges)

    # Message queue linker: detect Kafka, RabbitMQ, SQS, Redis Pub/Sub patterns
    mq_result = link_message_queues(repo_root)
    if mq_result.run is not None:
        analysis_runs.append(mq_result.run.to_dict())
        all_symbols.extend(mq_result.symbols)
        all_edges.extend(mq_result.edges)

    # HTTP linker: connect fetch/requests calls to route handlers
    # Include both kind="route" (Ruby/Go/Rust) and symbols with meta.route_path (Python/JS)
    route_symbols = [
        s for s in all_symbols
        if s.kind == "route" or (s.meta and s.meta.get("route_path"))
    ]
    http_result = link_http(repo_root, route_symbols)
    if http_result.run is not None:
        analysis_runs.append(http_result.run.to_dict())
        all_symbols.extend(http_result.symbols)
        all_edges.extend(http_result.edges)

    # GraphQL linker: connect client queries to schema definitions
    # Get GraphQL operation symbols (query, mutation, subscription)
    graphql_ops = [
        s for s in all_symbols
        if s.language == "graphql" and s.kind in ("query", "mutation", "subscription", "operation")
    ]
    graphql_link_result = link_graphql(repo_root, graphql_ops)
    if graphql_link_result.run is not None:
        analysis_runs.append(graphql_link_result.run.to_dict())
        all_symbols.extend(graphql_link_result.symbols)
        all_edges.extend(graphql_link_result.edges)

    # GraphQL resolver linker: connect resolver implementations to schema types
    # Get GraphQL type and field symbols for linking
    graphql_schema = [
        s for s in all_symbols
        if s.language == "graphql" and s.kind in ("type", "field", "interface")
    ]
    resolver_result = link_graphql_resolvers(repo_root, graphql_schema)
    if resolver_result.run is not None:
        analysis_runs.append(resolver_result.run.to_dict())
        all_symbols.extend(resolver_result.symbols)
        all_edges.extend(resolver_result.edges)

    # Database query linker: connect SQL queries in code to table definitions
    # Get SQL table symbols for linking
    table_symbols = [
        s for s in all_symbols
        if s.language == "sql" and s.kind == "table"
    ]
    db_query_result = link_database_queries(repo_root, table_symbols)
    if db_query_result.run is not None:
        analysis_runs.append(db_query_result.run.to_dict())
        all_symbols.extend(db_query_result.symbols)
        all_edges.extend(db_query_result.edges)

    # Event sourcing linker: detect event publishers and subscribers
    event_result = link_events(repo_root)
    if event_result.run is not None:
        analysis_runs.append(event_result.run.to_dict())
        all_symbols.extend(event_result.symbols)
        all_edges.extend(event_result.edges)

    # Dependency linker: connect import statements to manifest declarations
    # Get TOML dependency symbols for linking
    toml_symbols = [s for s in all_symbols if s.language == "toml"]
    dep_link_result = link_dependencies(
        toml_symbols=toml_symbols,
        code_edges=all_edges,
        code_symbols=all_symbols,
    )
    if dep_link_result.run is not None:
        analysis_runs.append(dep_link_result.run.to_dict())
        all_edges.extend(dep_link_result.edges)

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

    # Convert to dicts for output (in ranked order)
    all_nodes = [s.to_dict() for s in ranked_symbols]
    all_edge_dicts = [e.to_dict() for e in all_edges]

    behavior_map["analysis_runs"] = analysis_runs
    behavior_map["nodes"] = all_nodes
    behavior_map["edges"] = all_edge_dicts

    # Compute metrics from analyzed nodes and edges
    behavior_map["metrics"] = compute_metrics(all_nodes, all_edge_dicts)

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

    # Generate tiered output files BEFORE compact mode
    # (tiered files are always based on full analysis, not compact)
    if tiers != "none":
        tier_specs: list[str]
        if tiers is None or tiers == "default":
            tier_specs = list(DEFAULT_TIERS)
        else:
            tier_specs = [t.strip() for t in tiers.split(",") if t.strip()]

        # Generate each tier file from full behavior map
        for tier_spec in tier_specs:
            try:
                target_tokens = parse_tier_spec(tier_spec)
                tier_path = Path(generate_tier_filename(str(out_path), tier_spec))
                tiered_map = format_tiered_behavior_map(
                    behavior_map, all_symbols, all_edges, target_tokens
                )
                tier_path.write_text(json.dumps(tiered_map, indent=2))
            except ValueError:
                # Skip invalid tier specs silently
                pass

    # Apply compact mode if requested (modifies main output only)
    if compact:
        config = CompactConfig(target_coverage=coverage)
        behavior_map = format_compact_behavior_map(
            behavior_map, all_symbols, all_edges, config
        )

    out_path.write_text(json.dumps(behavior_map, indent=2))


def main(argv=None) -> int:
    parser = build_parser()

    # Handle default sketch mode: if no subcommand given, insert "sketch"
    if argv is None:
        argv = sys.argv[1:]

    subcommands = {"init", "run", "slice", "search", "routes", "catalog", "export-capsule", "sketch", "build-grammars"}

    # If no args, or first arg is not a subcommand (and not a flag), use sketch mode
    if not argv or (argv[0] not in subcommands and not argv[0].startswith("-")):
        argv = ["sketch"] + list(argv)

    args = parser.parse_args(argv)

    if not hasattr(args, "func"):  # pragma: no cover
        parser.print_help()  # pragma: no cover
        return 1  # pragma: no cover

    return args.func(args)

