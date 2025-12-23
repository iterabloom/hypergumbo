import argparse
import json
from pathlib import Path
from . import __version__
from .analyze.html import analyze_html
from .analyze.py import analyze_python
from .profile import detect_profile
from .schema import new_behavior_map



def cmd_init(args: argparse.Namespace) -> int:
    repo_root = Path(args.path).resolve()
    capsule_dir = repo_root / ".hypergumbo"
    capsule_dir.mkdir(parents=True, exist_ok=True)

    capsule_path = capsule_dir / "capsule.json"

    # Normalize capabilities into a list
    capabilities = [
        c.strip()
        for c in (args.capabilities or "").split(",")
        if c.strip()
    ]

    capsule = {
        "repo_root": str(repo_root),
        "assistant": args.assistant,
        "llm_input": args.llm_input,
        "capabilities": capabilities,
    }

    capsule_path.write_text(json.dumps(capsule, indent=2))

    print(
        "[hypergumbo init] "
        f"repo_root={repo_root} "
        f"capabilities={','.join(capabilities)} "
        f"assistant={args.assistant} "
        f"llm_input={args.llm_input}"
    )

    return 0


def cmd_run(args: argparse.Namespace) -> int:
    # The positional argument for `run` is called `path` in the parser below.
    repo_root = Path(args.path).resolve()
    out_path = Path(args.out)

    run_behavior_map(repo_root=repo_root, out_path=out_path)
    return 0


def cmd_slice(args: argparse.Namespace) -> int:
    # TODO: implement slicing
    print(f"[hypergumbo slice] entry={args.entry} out={args.out}")
    return 0


def cmd_catalog(args: argparse.Namespace) -> int:
    # TODO: implement catalog listing
    print("[hypergumbo catalog] show_all=" + str(args.show_all))
    return 0


def cmd_export_capsule(args: argparse.Namespace) -> int:
    # TODO: implement export-capsule
    print(f"[hypergumbo export-capsule] shareable={args.shareable} "
          f"out={args.out}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="hypergumbo")
    p.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {__version__}",
        help="Print version and exit",
    )

    sub = p.add_subparsers(dest="command", required=True)

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
        "--entry",
        required=True,
        help="Entrypoint (symbol | file | route) to slice from",
    )
    p_slice.add_argument(
        "--out",
        default="slice.json",
        help="Output JSON path (default: slice.json)",
    )
    p_slice.set_defaults(func=cmd_slice)

    # hypergumbo catalog
    p_catalog = sub.add_parser("catalog", help="Show available passes/packs")
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

    behavior_map["analysis_runs"] = analysis_runs
    behavior_map["nodes"] = all_nodes
    behavior_map["edges"] = all_edges

    # Ensure parent directory exists (even if caller gives nested paths later)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    out_path.write_text(json.dumps(behavior_map, indent=2))


def main(argv=None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)

