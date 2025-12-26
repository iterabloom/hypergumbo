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
from .analyze.haskell import analyze_haskell
from .analyze.ocaml import analyze_ocaml
from .analyze.solidity import analyze_solidity
from .analyze.csharp import analyze_csharp
from .analyze.cpp import analyze_cpp
from .analyze.zig import analyze_zig
from .analyze.groovy import analyze_groovy
from .analyze.julia import analyze_julia
from .analyze.bash import analyze_bash
from .catalog import get_default_catalog, is_available
from .linkers.ipc import link_ipc
from .linkers.jni import link_jni
from .linkers.phoenix_ipc import link_phoenix_ipc
from .linkers.websocket import link_websocket
from .entrypoints import detect_entrypoints
from .export import export_capsule
from .ir import Symbol, Edge, Span
from .limits import Limits
from .metrics import compute_metrics
from .profile import detect_profile
from .llm_assist import generate_plan_with_fallback
from .schema import new_behavior_map
from .sketch import generate_sketch
from .slice import SliceQuery, slice_graph
from .supply_chain import classify_file, detect_package_roots


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

    run_behavior_map(repo_root=repo_root, out_path=out_path, max_tier=max_tier)
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
    result = slice_graph(nodes, edges, query)

    # Build output
    output = {
        "schema_version": behavior_map.get("schema_version", "0.1.0"),
        "view": "slice",
        "feature": result.to_dict(),
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
    p_slice.set_defaults(func=cmd_slice)

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
    repo_root: Path, out_path: Path, max_tier: int | None = None
) -> None:
    """
    Run the behavior_map analysis for a repo and write JSON to out_path.

    Args:
        repo_root: Root directory of the repository
        out_path: Path to write the behavior map JSON
        max_tier: Optional maximum supply chain tier (1-4). Symbols with
            tier > max_tier are filtered out. None means no filtering.
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

    # Run Python analysis
    py_result = analyze_python(repo_root)
    if py_result.run is not None:
        analysis_runs.append(py_result.run.to_dict())
    all_symbols.extend(py_result.symbols)
    all_edges.extend(py_result.edges)

    # Run HTML analysis
    html_result = analyze_html(repo_root)
    if html_result.run is not None:
        analysis_runs.append(html_result.run.to_dict())
    all_symbols.extend(html_result.symbols)
    all_edges.extend(html_result.edges)

    # Run JavaScript/TypeScript/Svelte analysis (optional, requires tree-sitter)
    js_result = analyze_javascript(repo_root)
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

    # Apply supply chain classification to all symbols
    _classify_symbols(all_symbols, repo_root, package_roots)

    # Apply max_tier filtering if specified
    if max_tier is not None:
        # Filter symbols by tier
        filtered_symbols = [
            s for s in all_symbols if s.supply_chain_tier <= max_tier
        ]
        filtered_symbol_ids = {s.id for s in filtered_symbols}

        # Filter edges: both src and dst must be in filtered symbols or be file refs
        filtered_edges = [
            e
            for e in all_edges
            if e.src in filtered_symbol_ids
            or e.src.endswith((".py", ".js", ".ts", ".tsx", ".jsx"))
        ]

        all_symbols = filtered_symbols
        all_edges = filtered_edges
        limits.max_tier_applied = max_tier

    # Convert to dicts for output
    all_nodes = [s.to_dict() for s in all_symbols]
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

    out_path.write_text(json.dumps(behavior_map, indent=2))


def main(argv=None) -> int:
    parser = build_parser()

    # Handle default sketch mode: if no subcommand given, insert "sketch"
    if argv is None:
        argv = sys.argv[1:]

    subcommands = {"init", "run", "slice", "catalog", "export-capsule", "sketch"}

    # If no args, or first arg is not a subcommand (and not a flag), use sketch mode
    if not argv or (argv[0] not in subcommands and not argv[0].startswith("-")):
        argv = ["sketch"] + list(argv)

    args = parser.parse_args(argv)

    if not hasattr(args, "func"):  # pragma: no cover
        parser.print_help()  # pragma: no cover
        return 1  # pragma: no cover

    return args.func(args)

