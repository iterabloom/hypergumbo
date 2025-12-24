import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List

from . import __version__
from .analyze.html import analyze_html
from .analyze.js_ts import analyze_javascript
from .analyze.py import analyze_python
from .catalog import get_default_catalog, is_available
from .entrypoints import detect_entrypoints
from .export import export_capsule
from .ir import Symbol, Edge, Span
from .limits import Limits
from .metrics import compute_metrics
from .plan import generate_plan
from .profile import detect_profile
from .llm_assist import generate_plan_with_fallback, LLMBackend
from .schema import new_behavior_map
from .sketch import generate_sketch
from .slice import SliceQuery, slice_graph


def cmd_sketch(args: argparse.Namespace) -> int:
    """Generate token-budgeted Markdown sketch to stdout."""
    repo_root = Path(args.path).resolve()

    if not repo_root.exists():
        print(f"Error: path does not exist: {repo_root}", file=sys.stderr)
        return 1

    max_tokens = args.tokens if args.tokens else None
    sketch = generate_sketch(repo_root, max_tokens=max_tokens)
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

    run_behavior_map(repo_root=repo_root, out_path=out_path)
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
    query = SliceQuery(
        entrypoint=entry,
        max_hops=args.max_hops,
        max_files=args.max_files,
        min_confidence=args.min_confidence,
        exclude_tests=args.exclude_tests,
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

    print(f"[hypergumbo slice] Wrote slice to {out_path}")
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
    p_sketch.set_defaults(func=cmd_sketch)

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


def run_behavior_map(repo_root: Path, out_path: Path) -> None:
    """
    Run the behavior_map analysis for a repo and write JSON to out_path.
    """
    behavior_map = new_behavior_map()

    # Detect repo profile (languages, frameworks)
    profile = detect_profile(repo_root)
    behavior_map["profile"] = profile.to_dict()

    analysis_runs = []
    all_nodes = []
    all_edges = []
    limits = Limits()

    # Run Python analysis
    py_result = analyze_python(repo_root)
    if py_result.run is not None:
        analysis_runs.append(py_result.run.to_dict())
    all_nodes.extend(s.to_dict() for s in py_result.symbols)
    all_edges.extend(e.to_dict() for e in py_result.edges)

    # Run HTML analysis
    html_result = analyze_html(repo_root)
    if html_result.run is not None:
        analysis_runs.append(html_result.run.to_dict())
    all_nodes.extend(s.to_dict() for s in html_result.symbols)
    all_edges.extend(e.to_dict() for e in html_result.edges)

    # Run JavaScript/TypeScript analysis (optional, requires tree-sitter)
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
            all_nodes.extend(s.to_dict() for s in js_result.symbols)
            all_edges.extend(e.to_dict() for e in js_result.edges)

    behavior_map["analysis_runs"] = analysis_runs
    behavior_map["nodes"] = all_nodes
    behavior_map["edges"] = all_edges

    # Compute metrics from analyzed nodes and edges
    behavior_map["metrics"] = compute_metrics(all_nodes, all_edges)

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

