import argparse
from pathlib import Path
from . import __version__


def cmd_init(args: argparse.Namespace) -> int:
    # TODO: implement capsule initialization
    print(f"[hypergumbo init] repo_root={Path(args.path).resolve()} "
          f"capabilities={args.capabilities} assistant={args.assistant} "
          f"llm_input={args.llm_input}")
    return 0


def cmd_run(args: argparse.Namespace) -> int:
    # TODO: implement capsule execution
    print(f"[hypergumbo run] repo_root={Path(args.path).resolve()} "
          f"out={args.out}")
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


def main(argv=None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())

