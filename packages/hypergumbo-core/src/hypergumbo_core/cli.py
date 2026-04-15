# SPDX-License-Identifier: AGPL-3.0-or-later
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
- **install-gitleaks**: Install gitleaks for secret scanning

When no subcommand is given, sketch mode is assumed. This makes the
common case (`hypergumbo .`) as simple as possible.

The `run` command orchestrates all language analyzers and cross-language
linkers, collecting their results into a unified behavior map. Analyzers
run independently across 100+ languages. Linkers run after all analyzers
complete to create cross-language edges.

Why This Design
---------------
- Subcommand dispatch keeps each operation isolated and testable
- Default sketch mode optimizes for the common "quick overview" use case
- run_behavior_map() is separate from cmd_run() for testability
- Helper functions (Symbol.from_dict, _edge_from_dict) enable slice
  to work with previously-generated JSON files
"""
import argparse
import gc
import json
import math
import os
import resource
import subprocess  # nosec B404 - subprocess needed for pip commands
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

from rich.console import Console
from rich.table import Table

from . import __version__
from .analyze.all_analyzers import run_all_analyzers
from .analyze.base import is_exported_from_modifiers
from .catalog import get_default_catalog, is_available, suggest_passes_for_languages
from .linkers.registry import LinkerContext, run_all_linkers
# Import linker modules to trigger @register_linker decoration (side effect imports)
import hypergumbo_core.linkers.cgo as _cgo_linker  # noqa: F401
import hypergumbo_core.linkers.containment as _containment_linker  # noqa: F401
import hypergumbo_core.linkers.database_query as _database_query_linker  # noqa: F401
import hypergumbo_core.linkers.di_resolution as _di_resolution_linker  # noqa: F401
import hypergumbo_core.linkers.dependency as _dependency_linker  # noqa: F401
import hypergumbo_core.linkers.event_sourcing as _event_sourcing_linker  # noqa: F401
import hypergumbo_core.linkers.graphql as _graphql_linker  # noqa: F401
import hypergumbo_core.linkers.graphql_resolver as _graphql_resolver_linker  # noqa: F401
import hypergumbo_core.linkers.go_cobra as _go_cobra_linker  # noqa: F401
import hypergumbo_core.linkers.go_memberlist as _go_memberlist_linker  # noqa: F401
import hypergumbo_core.linkers.grpc as _grpc_linker  # noqa: F401
import hypergumbo_core.linkers.http as _http_linker  # noqa: F401
import hypergumbo_core.linkers.ipc as _ipc_linker  # noqa: F401
import hypergumbo_core.linkers.jni as _jni_linker  # noqa: F401
import hypergumbo_core.linkers.lua_ffi as _lua_ffi_linker  # noqa: F401
import hypergumbo_core.linkers.message_queue as _message_queue_linker  # noqa: F401
import hypergumbo_core.linkers.napi as _napi_linker  # noqa: F401
import hypergumbo_core.linkers.openapi as _openapi_linker  # noqa: F401
import hypergumbo_core.linkers.otp as _otp_linker  # noqa: F401
import hypergumbo_core.linkers.phoenix_ipc as _phoenix_ipc_linker  # noqa: F401
import hypergumbo_core.linkers.route_handler as _route_handler_linker  # noqa: F401
import hypergumbo_core.linkers.subprocess_cli as _subprocess_linker  # noqa: F401
import hypergumbo_core.linkers.swift_objc as _swift_objc_linker  # noqa: F401
import hypergumbo_core.linkers.websocket as _websocket_linker  # noqa: F401
import hypergumbo_core.linkers.inheritance as _inheritance_linker  # noqa: F401
import hypergumbo_core.linkers.js_module as _js_module_linker  # noqa: F401
import hypergumbo_core.linkers.orm as _orm_linker  # noqa: F401
import hypergumbo_core.linkers.pyffi as _pyffi_linker  # noqa: F401
import hypergumbo_core.linkers.ruby_ffi as _ruby_ffi_linker  # noqa: F401
import hypergumbo_core.linkers.type_hierarchy as _type_hierarchy_linker  # noqa: F401
import hypergumbo_core.linkers.vue_component as _vue_component_linker  # noqa: F401
import hypergumbo_core.linkers.view_template as _view_template_linker  # noqa: F401
import hypergumbo_core.linkers.vue_template_method as _vue_template_method_linker  # noqa: F401
import hypergumbo_core.linkers.build_target as _build_target_linker  # noqa: F401
import hypergumbo_core.linkers.decorator_dispatch as _decorator_dispatch_linker  # noqa: F401
import hypergumbo_core.linkers.middleware_chain as _middleware_chain_linker  # noqa: F401
import hypergumbo_core.linkers.react_component as _react_component_linker  # noqa: F401
import hypergumbo_core.linkers.tauri_ipc as _tauri_ipc_linker  # noqa: F401
import hypergumbo_core.linkers.solidity_abi as _solidity_abi_linker  # noqa: F401
import hypergumbo_core.linkers.wasm_bindgen as _wasm_bindgen_linker  # noqa: F401
import hypergumbo_core.linkers.yjs_crdt as _yjs_crdt_linker  # noqa: F401
import hypergumbo_core.linkers.annotation_convention as _annotation_convention_linker  # noqa: F401
import hypergumbo_core.linkers.crypto_flow as _crypto_flow_linker  # noqa: F401
import hypergumbo_core.linkers.message_dispatch as _message_dispatch_linker  # noqa: F401
from .entrypoints import EntrypointKind, detect_entrypoints
from .ir import Symbol, Edge, create_boundary_nodes, deduplicate_edges
from .metrics import compute_metrics
from .profile import detect_profile
from .schema import new_behavior_map
from .sketch import generate_sketch, ConfigExtractionMode, SketchStats, display_representativeness_table
from .slice import SliceQuery, slice_graph, AmbiguousEntryError, rank_slice_nodes
from .selection.filters import EXCLUDED_KINDS
from .supply_chain import classify_file, detect_package_roots
from .ranking import (
    rank_symbols, _is_test_path, compute_transitive_test_coverage,
    compute_symbol_mention_centrality_batch, compute_raw_in_degree,
)
from .paths import is_test_node as _is_test_node
from .compact import (
    CompactConfig,
    format_compact_behavior_map,
    format_tiered_behavior_map,
    generate_tier_filename,
    parse_tier_spec,
    DEFAULT_TIERS,
)
from .build_grammars import build_all_grammars, check_grammar_availability
from .gitleaks import (
    is_gitleaks_available,
    install_gitleaks,
    uninstall_gitleaks,
    scan_content_cached,
    format_secret_warning,
    get_install_nag,
)
from .framework_patterns import (
    enrich_symbols,
    get_frameworks_dir,
    resolve_deferred_symbol_refs,
)
from .partial_install_warnings import check_partial_install_warnings


def _setup_locale_filtering(
    repo_root: Path,
    locale: str | None,
) -> None:
    """Detect locale documentation directories and configure filtering.

    Scans the repo for translated doc directories (GitLab-style doc-locale/<lang>/
    or FastAPI-style docs/<lang>/). Logs what was found and what decision was made
    to stderr. Sets the global locale excludes so find_files() skips the
    appropriate directories.

    Args:
        repo_root: Repository root path.
        locale: The --locale flag value. None means "use default (exclude
            translations)", a string like "ja-jp" means "swap to that locale".
    """
    from .discovery import detect_locale_dirs, set_locale_excludes

    info = detect_locale_dirs(repo_root)
    if info is None:
        if locale is not None:
            print(
                f"WARNING: --locale {locale} specified but no translated "
                f"documentation directories found in {repo_root}",
                file=sys.stderr,
            )
        return

    # Log what we found
    lang_list = ", ".join(info.languages)
    print(
        f"Locale docs detected ({info.style}): "
        f"{len(info.languages)} language(s) [{lang_list}]",
        file=sys.stderr,
    )

    try:
        excludes = info.excludes_for_locale(locale)
    except ValueError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)

    set_locale_excludes(excludes)

    # Log the decision
    if locale is None:
        excluded_str = ", ".join(d.name for d in excludes)
        print(
            f"  Excluding translated docs: {excluded_str} "
            f"(use --locale <code> to analyze a specific translation)",
            file=sys.stderr,
        )
    else:
        print(
            f"  Using locale '{locale}' instead of primary docs",
            file=sys.stderr,
        )


# =============================================================================
# Custom Help Formatter for Grouped Subcommands
# =============================================================================


class GroupedSubcommandHelpFormatter(argparse.RawDescriptionHelpFormatter):
    """Help formatter that groups subcommands with visual separators.

    Subcommands can be assigned to groups by setting a 'group' attribute on the
    subparser's _ChoicesPseudoAction. Groups are displayed in order by their
    'group_order' attribute, with a visual separator between groups.

    Example:
        sub = parser.add_subparsers()
        p = sub.add_parser('sketch', help='...')
        _set_subparser_group(sub, 'sketch', 'core', 0)
    """

    def _format_action(self, action: argparse.Action) -> str:
        """Format an action, with special handling for subparsers."""
        # Only customize subparser actions
        if not isinstance(action, argparse._SubParsersAction):
            return super()._format_action(action)

        # Get all subactions (the individual subcommands)
        subactions = list(action._get_subactions())

        # Group subactions by their 'group' attribute
        groups: Dict[str, Dict[str, Any]] = {}
        for subaction in subactions:
            group_name = getattr(subaction, "group", "default")
            group_order = getattr(subaction, "group_order", 999)
            suborder = getattr(subaction, "suborder", 0)
            if group_name not in groups:
                groups[group_name] = {"order": group_order, "actions": []}
            groups[group_name]["actions"].append((suborder, subaction))

        # Sort actions within each group by suborder
        for info in groups.values():
            info["actions"].sort(key=lambda x: x[0])
            info["actions"] = [action for _, action in info["actions"]]

        # Sort groups by (order, name)
        sorted_groups = sorted(groups.items(), key=lambda x: (x[1]["order"], x[0]))

        # Build the formatted output
        parts = []

        # Calculate max width for alignment
        max_length = 0
        for _, info in sorted_groups:
            for subaction in info["actions"]:
                invocation = self._format_action_invocation(subaction)
                max_length = max(max_length, len(invocation))

        # Add some padding
        action_width = max_length + 2

        for idx, (_, info) in enumerate(sorted_groups):
            # Add separator between groups (not before first group)
            if idx > 0:
                separator = " " * self._current_indent
                separator += "-" * action_width
                separator += "  "
                separator += "-" * 26
                parts.append(separator + "\n")

            # Format each subaction in this group
            for subaction in info["actions"]:
                parts.append(self._format_subaction(subaction, action_width))

        return "".join(parts)

    def _format_subaction(self, action: argparse.Action, action_width: int) -> str:
        """Format a single subcommand action."""
        # Get the command name
        invocation = self._format_action_invocation(action)

        # Get help text
        help_text = action.help or ""

        # Build the line with proper indentation and alignment
        indent = " " * self._current_indent
        # Pad invocation to action_width for alignment
        padded_invocation = invocation.ljust(action_width)

        return f"{indent}{padded_invocation}{help_text}\n"


def _set_subparser_group(
    subparsers: argparse._SubParsersAction,
    name: str,
    group: str,
    group_order: int,
    suborder: int = 0,
) -> None:
    """Set the group for a subparser by name.

    Args:
        subparsers: The _SubParsersAction from add_subparsers()
        name: The name of the subparser (e.g., 'sketch')
        group: The group name (e.g., 'core', 'extras')
        group_order: Sort order for the group (lower = earlier)
        suborder: Sort order within the group (lower = earlier, default 0)
    """
    # Find the _ChoicesPseudoAction for this subparser
    for choice_action in subparsers._choices_actions:
        if choice_action.dest == name:
            choice_action.group = group
            choice_action.group_order = group_order
            choice_action.suborder = suborder
            return
    # If we get here, the subparser wasn't found (shouldn't happen)
    raise ValueError(f"Subparser '{name}' not found")  # pragma: no cover


def _get_subparsers_by_group(
    subparsers_action: argparse._SubParsersAction,
) -> List[tuple]:
    """Get subparser names ordered by group.

    Returns:
        List of (name, subparser, group, is_new_group) tuples ordered by
        (group_order, suborder) within each group.
    """
    # Build list with group info
    items = []
    for choice_action in subparsers_action._choices_actions:
        name = choice_action.dest
        group = getattr(choice_action, "group", "default")
        order = getattr(choice_action, "group_order", 999)
        suborder = getattr(choice_action, "suborder", 0)
        subparser = subparsers_action.choices.get(name)
        if subparser:
            items.append((order, group, suborder, name, subparser))

    # Sort by (group_order, group, suborder)
    items.sort(key=lambda x: (x[0], x[1], x[2]))

    # Return just (name, subparser) pairs with group info for separators
    result = []
    prev_group = None
    for _order, group, _suborder, name, subparser in items:
        result.append((name, subparser, group, group != prev_group))
        prev_group = group

    return result


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
        - sketch.8000.md (8000 token budget)
        - sketch.16000.md (16000 token budget)
        - sketch.8000.notests.md (8000 tokens, exclude_tests=True)
        - sketch.8000.withsource.md (8000 tokens, with_source=True)
        - sketch.8000.notests.withsource.md (both flags)

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

    # WI-zujum: a single-file argument crashes downstream in
    # _format_structure_tree_fallback (Path.iterdir() raises
    # NotADirectoryError). Reject early with a hint pointing at the
    # likely intent — analyse the parent directory.
    if not repo_root.is_dir():
        parent = repo_root.parent
        print(
            f"Error: {repo_root} is a file, not a directory.\n"
            f"hypergumbo analyses repositories. Try its parent directory:\n"
            f"  hypergumbo {parent}",
            file=sys.stderr,
        )
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

    # Default to 8000 tokens when -t not specified (unified behavior)
    max_tokens = args.tokens if args.tokens else 8000
    exclude_tests = getattr(args, "exclude_tests", False)
    first_party_priority = getattr(args, "first_party_priority", True)
    extra_excludes = getattr(args, "extra_excludes", [])
    locale = getattr(args, "locale", None)
    verbose = getattr(args, "verbose", False)

    # Detect and filter locale documentation directories
    _setup_locale_filtering(repo_root, locale)

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
        require_sections=getattr(args, "require_sections", None) or None,
    )

    # Secret scanning (opt-out with --no-secret-scan).
    # WI-julir: use scan_content_cached so warm sketch runs reuse a prior
    # gitleaks result keyed on the sketch content hash. The per-state
    # cache_dir already lives under the per-repo-state directory, so any
    # source-file change rotates the directory and invalidates the cache.
    no_secret_scan = getattr(args, "no_secret_scan", False)
    if not no_secret_scan:
        if is_gitleaks_available():
            findings = scan_content_cached(sketch, cache_dir)
            if findings:
                print(format_secret_warning(findings), file=sys.stderr)
            else:
                # Always remind that this is best-effort
                print(
                    "\u2139\ufe0f  Secret scan complete (best-effort, not exhaustive).",
                    file=sys.stderr,
                )
        else:
            print(get_install_nag(), file=sys.stderr)

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

    # WI-zujum: same single-file guard as cmd_sketch — analysing a single
    # file is not a supported mode and crashes deeper in the pipeline.
    if not repo_root.exists():
        print(f"Error: path does not exist: {repo_root}", file=sys.stderr)
        return 1
    if not repo_root.is_dir():
        parent = repo_root.parent
        print(
            f"Error: {repo_root} is a file, not a directory.\n"
            f"hypergumbo analyses repositories. Try its parent directory:\n"
            f"  hypergumbo run {parent}",
            file=sys.stderr,
        )
        return 1

    out_path = Path(args.out) if args.out else None
    max_tier = getattr(args, "max_tier", None)
    max_files = getattr(args, "max_files", None)
    max_file_bytes = getattr(args, "max_file_bytes", None)
    compact = getattr(args, "compact", False)
    coverage = getattr(args, "coverage", 0.8)
    connectivity = not getattr(args, "no_connectivity", False)
    budgets = getattr(args, "budgets", None)
    extra_excludes = getattr(args, "extra_excludes", [])
    frameworks = getattr(args, "frameworks", None)
    include_docs = getattr(args, "include_docs", False)
    show_progress = getattr(args, "progress", True)
    locale = getattr(args, "locale", None)

    # Detect and filter locale documentation directories
    _setup_locale_filtering(repo_root, locale)

    generated_files = run_behavior_map(
        repo_root=repo_root,
        out_path=out_path,
        max_tier=max_tier,
        max_files=max_files,
        max_file_bytes=max_file_bytes,
        compact=compact,
        coverage=coverage,
        connectivity=connectivity,
        budgets=budgets,
        extra_excludes=extra_excludes,
        frameworks=frameworks,
        include_docs=include_docs,
        progress=show_progress,
    )

    # Output summary (always at the end)
    _print_output_summary("run", artifacts=generated_files)

    return 0



def _edge_from_dict(d: Dict[str, Any]) -> Edge:
    """Reconstruct an Edge from its dict representation."""
    return Edge.from_dict(d)


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


def _handle_files_mode(
    args: argparse.Namespace,
    nodes: List[Symbol],
    edges: List[Edge],
    repo_root: Path,
) -> int:
    """Handle --files mode: find files that depend on changed files.

    This is used for smart test selection. Given a list of changed files,
    finds all symbols in those files, performs reverse slices to find
    dependent code, and outputs the list of dependent file paths.

    Args:
        args: Parsed command-line arguments (needs args.files, args.output)
        nodes: All symbols from the behavior map
        edges: All edges from the behavior map
        repo_root: Repository root path

    Returns:
        0 on success, 1 on error
    """
    from .paths import normalize_path, path_ends_with

    # Read changed files from the input file
    files_path = Path(args.files)
    if not files_path.exists():
        print(f"Error: Files list not found: {args.files}", file=sys.stderr)
        return 1

    changed_files = [
        line.strip()
        for line in files_path.read_text().splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]

    if not changed_files:
        print("No changed files in input", file=sys.stderr)
        return 1

    # Build lookup structures
    # Map from normalized file path to symbols in that file
    file_to_symbols: Dict[str, List[Symbol]] = {}
    for node in nodes:
        if node.path:
            norm_path = normalize_path(node.path)
            if norm_path not in file_to_symbols:
                file_to_symbols[norm_path] = []
            file_to_symbols[norm_path].append(node)

    # Find symbols in changed files (using path suffix matching for flexibility)
    changed_symbols: List[Symbol] = []
    for changed_file in changed_files:
        changed_norm = normalize_path(changed_file)
        # Try exact match first
        if changed_norm in file_to_symbols:
            changed_symbols.extend(file_to_symbols[changed_norm])
            continue
        # Try suffix match (handles relative vs absolute paths)
        for file_path, symbols in file_to_symbols.items():
            if path_ends_with(file_path, changed_norm) or path_ends_with(changed_norm, file_path):
                changed_symbols.extend(symbols)

    if not changed_symbols:
        # No symbols found in changed files - output empty result
        output_lines: List[str] = []
    else:
        # Perform reverse slices from changed symbols to find dependents
        # Use a set to collect unique dependent files
        dependent_files: Set[str] = set()

        # Build edge index for reverse traversal (target -> sources)
        reverse_edge_index: Dict[str, List[str]] = {}
        for edge in edges:
            if edge.dst not in reverse_edge_index:
                reverse_edge_index[edge.dst] = []
            reverse_edge_index[edge.dst].append(edge.src)

        # Build symbol lookup
        symbol_lookup = {node.id: node for node in nodes}

        # BFS from each changed symbol to find all dependents
        visited: Set[str] = set()
        queue = [sym.id for sym in changed_symbols]

        # Also add the changed files themselves
        for sym in changed_symbols:
            if sym.path:
                dependent_files.add(sym.path)

        max_hops = getattr(args, "max_hops", None) or 10  # Generous default for test selection
        hop_count = 0
        current_level = set(queue)

        while current_level and hop_count < max_hops:
            next_level: Set[str] = set()
            for node_id in current_level:
                if node_id in visited:  # pragma: no cover
                    continue  # Defensive: set deduplication prevents this
                visited.add(node_id)

                # Add this node's file to dependents
                sym = symbol_lookup.get(node_id)
                if sym and sym.path:
                    dependent_files.add(sym.path)

                # Find nodes that call/depend on this node (reverse edges)
                callers = reverse_edge_index.get(node_id, [])
                for caller_id in callers:
                    if caller_id not in visited:
                        next_level.add(caller_id)

            current_level = next_level
            hop_count += 1

        output_lines = sorted(dependent_files)

    # Write output
    output_text = "\n".join(output_lines)
    if output_text:
        output_text += "\n"

    if args.output:
        output_path = Path(args.output)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(output_text)
        print(f"[hypergumbo slice --files] Found {len(output_lines)} dependent files")
        print(f"  Output: {output_path}")
    else:
        # Write to stdout
        sys.stdout.write(output_text)

    return 0


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
    nodes = [Symbol.from_dict(n) for n in behavior_map.get("nodes", [])]
    edges = [_edge_from_dict(e) for e in behavior_map.get("edges", [])]

    # Handle --files mode: find all files that depend on changed files
    # Used for smart test selection
    if getattr(args, "files", None):
        return _handle_files_mode(args, nodes, edges, repo_root)

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

            # Filter out test code if --exclude-tests (checks both path and annotations)
            if exclude_tests and sym.path and _is_test_node(sym.path, sym.meta):
                continue  # pragma: no cover - test penalty in detect_entrypoints filters first

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

        # Apply --exclude-tests and --max-tier filters to entry candidates
        exclude_tests = getattr(args, "exclude_tests", False)
        max_tier = getattr(args, "max_tier", None)
        if exclude_tests or max_tier is not None:
            symbol_lookup = {node.id: node for node in nodes}
            filtered = []
            for ep in entrypoints:
                sym = symbol_lookup.get(ep.symbol_id)
                if sym is None:
                    continue  # pragma: no cover
                if exclude_tests and sym.path and _is_test_node(sym.path, sym.meta):
                    continue  # pragma: no cover - test penalty in detect_entrypoints filters first
                if max_tier is not None and sym.supply_chain_tier > max_tier:
                    continue
                filtered.append(ep)
            entrypoints = filtered

        if not entrypoints:
            print("Error: No entrypoints detected. Use --entry to specify manually.",
                  file=sys.stderr)
            return 1

        # Score entries by both confidence and graph connectivity
        # Well-connected entries produce richer slices
        edge_src_counts: Dict[str, int] = {}
        for e in edges:
            edge_src_counts[e.src] = edge_src_counts.get(e.src, 0) + 1

        # Main functions are canonical application roots and should be
        # preferred over route handlers that may have more edges but
        # lead to dead ends (e.g., V1DeprecationRouter.deprecationHandler
        # in alertmanager had 7 route-node edges but 0 useful call edges).
        _MAIN_KINDS = frozenset({
            EntrypointKind.MAIN_FUNCTION,
            EntrypointKind.CLI_MAIN,
        })

        def entry_score(ep: Any) -> float:
            """Score = confidence * connectivity_boost * kind_boost.

            connectivity_boost = 1 + log(1 + outgoing_edges)
            kind_boost = 2.0 for main functions, 1.0 otherwise

            Main functions get a 2x boost because they are the canonical
            application root.  Route handlers with more edges often point
            to dead-end route nodes rather than useful call chains.
            """
            out_edges = edge_src_counts.get(ep.symbol_id, 0)
            connectivity_boost = 1 + math.log(1 + out_edges)
            kind_boost = 2.0 if ep.kind in _MAIN_KINDS else 1.0
            return ep.confidence * connectivity_boost * kind_boost

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
        # Write to cache dir (like `run`) so slice output doesn't pollute
        # the repo root or bust the results cache via untracked-file hash.
        from .sketch_embeddings import _get_results_cache_dir

        cache_dir = _get_results_cache_dir(repo_root)
        direction = ".reverse" if args.reverse else ""
        out_path = cache_dir / f"slice.{safe_name}{direction}.json"
    else:
        out_path = Path(out_path_arg)

    # Build slice query
    max_tier = getattr(args, "max_tier", None)
    exclude_utility = getattr(args, "exclude_utility", False)
    hub_threshold_raw = getattr(args, "hub_threshold", 50)
    hub_threshold = hub_threshold_raw if hub_threshold_raw else None
    exclude_imports = getattr(args, "exclude_imports", False)
    # When the user doesn't specify --max-hops, leave it as None (unlimited).
    # max_files and hub_threshold are sufficient to bound slice size; an
    # artificial hop limit only causes the slice to undershoot the file budget.
    max_hops = args.max_hops
    query = SliceQuery(
        entrypoint=entry,
        max_hops=max_hops,
        max_files=args.max_files,
        min_confidence=args.min_confidence,
        exclude_tests=args.exclude_tests,
        exclude_utility=exclude_utility,
        reverse=args.reverse,
        max_tier=max_tier,
        language=args.language,
        hub_threshold=hub_threshold,
        exclude_imports=exclude_imports,
        dataflow=getattr(args, "dataflow", False),
    )

    # Perform slice
    try:
        result = slice_graph(nodes, edges, query)
    except AmbiguousEntryError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1

    if not result.entry_nodes:
        print(
            f"Error: No symbol found matching '{query.entrypoint}'",
            file=sys.stderr,
        )
        return 1

    # Rank slice nodes by importance (centrality + tier weighting).
    # For reverse slices, downweight test file callers so production callers
    # rank higher — matches the 90% entrypoint penalty (0.1 multiplier).
    test_weight = 0.1 if query.reverse else None
    ranked_node_ids = rank_slice_nodes(
        result, nodes, edges, first_party_priority=True, test_weight=test_weight,
    )

    # Build output with ranked node ordering
    feature_dict = result.to_dict()
    feature_dict["node_ids"] = ranked_node_ids  # Replace with ranked order

    # --group-by-module validation and implied flags
    group_by_module = getattr(args, "group_by_module", False)
    if group_by_module and getattr(args, "flat", False):
        print("Error: --group-by-module cannot be used with --flat", file=sys.stderr)
        return 1

    # --flat and --group-by-module both imply --inline
    use_inline = (
        getattr(args, "inline", False)
        or getattr(args, "flat", False)
        or group_by_module
    )

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

        feature_dict["edges"] = [
            e for e in behavior_map.get("edges", [])
            if e.get("id") in edge_ids_set
        ]

        if group_by_module:
            # Group nodes by file path
            modules: dict[str, list[dict]] = {}
            for node in inline_nodes:
                path = node.get("path", "<unknown>")
                modules.setdefault(path, []).append(node)

            # Sort modules by best rank (module with highest-ranked node first)
            sorted_modules = dict(sorted(
                modules.items(),
                key=lambda item: node_rank.get(item[1][0].get("id", ""), 999999),
            ))

            feature_dict["modules"] = {
                path: {"node_count": len(mod_nodes), "nodes": mod_nodes}
                for path, mod_nodes in sorted_modules.items()
            }

            # Build module-level edge summary (cross-file only)
            node_to_module = {
                n.get("id", ""): path
                for path, mod_nodes in sorted_modules.items()
                for n in mod_nodes
            }
            module_edge_counts: dict[tuple[str, str], dict] = {}
            for e in feature_dict.get("edges", []):
                src_mod = node_to_module.get(e.get("src", ""))
                dst_mod = node_to_module.get(e.get("dst", ""))
                if src_mod and dst_mod and src_mod != dst_mod:
                    key = (src_mod, dst_mod)
                    if key not in module_edge_counts:
                        module_edge_counts[key] = {"count": 0, "types": set()}
                    module_edge_counts[key]["count"] += 1
                    module_edge_counts[key]["types"].add(e.get("type", ""))
            feature_dict["module_edges"] = [
                {
                    "src_module": s,
                    "dst_module": d,
                    "count": info["count"],
                    "types": sorted(info["types"]),
                }
                for (s, d), info in sorted(module_edge_counts.items())
            ]
        else:
            feature_dict["nodes"] = inline_nodes

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
    out_path.write_text(json.dumps(output, indent=2, sort_keys=True))

    mode = "reverse" if args.reverse else "forward"
    print(f"[hypergumbo slice] Wrote {mode} slice to {out_path}")
    print(f"  entry: {entry}")
    print(f"  nodes: {len(result.node_ids)}")
    print(f"  edges: {len(result.edge_ids)}")
    if group_by_module:
        print(f"  modules: {len(feature_dict.get('modules', {}))}")
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

    from .paths import is_test_file

    # Find route handlers - symbols with route concepts in meta.concepts
    # OR symbols with kind="route" (Go analyzer creates route symbols directly).
    # WI-godos: tests excluded by default; --include-tests opts in.
    # The legacy --exclude-tests flag is preserved as a no-op alias for
    # backward compatibility with existing scripts.
    exclude_tests = not getattr(args, "include_tests", False)
    routes: list[dict] = []
    for node in nodes:
        is_route = False

        # Check 1: concept enrichment (YAML patterns)
        meta = node.get("meta") or {}
        concepts = meta.get("concepts", [])
        for concept in concepts:
            if isinstance(concept, dict) and concept.get("concept") == "route":
                is_route = True
                break

        # Check 2: symbol kind (analyzers create kind="route" symbols directly)
        if not is_route and node.get("kind") == "route":
            is_route = True

        if is_route:
            # Apply language filter
            if args.language and node.get("language") != args.language:
                continue
            # Exclude routes from test files when -x/--exclude-tests is set.
            # Django bakeoff showed 73% of route source files were from test
            # directories — use this flag to filter them out.
            if exclude_tests and is_test_file(node.get("path", "")):
                continue
            routes.append(node)

    # Deduplicate routes by (method, path, file).  Routes from different
    # files are always kept even if they share the same (method, path) —
    # they represent different registrations (e.g. v1 deprecation vs v2
    # go-swagger handlers).  Within a file, only the first entry for each
    # (method, path) is shown.
    seen_route_keys: set[str] = set()
    deduped_routes: list[dict] = []
    for node in routes:
        meta = node.get("meta") or {}
        route_path = None
        method = None
        # For kind="route" symbols, always use meta.route_path/http_method.
        # These are the authoritative values from the analyzer.  Concept
        # enrichment (Phase 3) can attach multiple concepts with different
        # methods when a handler is reused across GET/POST — using the
        # concept method would cause dedup collisions.
        if node.get("kind") == "route":
            route_path = meta.get("route_path")
            method = meta.get("http_method")
        if route_path is None:
            for concept in meta.get("concepts", []):
                if isinstance(concept, dict) and concept.get("concept") == "route":
                    route_path = concept.get("path")
                    method = concept.get("method")
                    break
        if route_path and method:
            key = f"{method.upper()}:{route_path}:{node.get('path', '')}"
            if key in seen_route_keys:
                continue
            seen_route_keys.add(key)
        deduped_routes.append(node)
    routes = deduped_routes

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

            # Extract route info from direct meta fields (kind="route" symbols
            # from analyzers) or concept metadata (YAML pattern enrichment).
            # kind="route" symbols use meta.route_path/http_method as the
            # authoritative source — concept methods can be wrong when a
            # handler is shared across multiple HTTP methods.
            route_path = None
            method = None
            controller_action = None
            if route.get("kind") == "route":
                route_path = meta.get("route_path")
                method = meta.get("http_method")
            if route_path is None:
                concepts = meta.get("concepts", [])
                for concept in concepts:
                    if isinstance(concept, dict) and concept.get("concept") == "route":
                        route_path = concept.get("path")
                        method = concept.get("method")
                        controller_action = concept.get("controller_action")
                        break
            # controller_action may also be in top-level meta (Rails routes)
            if controller_action is None:
                controller_action = meta.get("controller_action")

            # Display label: prefer controller_action, fall back to symbol name
            display_target = controller_action or name

            method = method.upper() if method else ""
            if route_path:
                # Normalize: ensure paths start with /
                # (defense-in-depth; framework_patterns already normalizes)
                if route_path and not route_path.startswith("/"):  # pragma: no cover
                    route_path = "/" + route_path
                print(f"  [{method}] {route_path} -> {display_target} (line {line})")
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

    # Find matching symbols using priority-based matching (same rules as
    # slice --entry for consistency — WI-gipop).
    # Priority: exact ID → exact path → path suffix → exact name → partial name
    spec = args.symbol
    matches = [n for n in nodes if n.get("id") == spec]
    if not matches:
        matches = [n for n in nodes if n.get("path") == spec]
    if not matches and ("/" in spec or "\\" in spec):
        matches = [
            n for n in nodes
            if n.get("path", "").endswith(spec)
            or n.get("path", "").endswith("/" + spec)
        ]
    if not matches:
        matches = [n for n in nodes if n.get("name") == spec]
    if not matches:
        # Case-insensitive name match (original behavior)
        pattern = spec.lower()
        matches = [n for n in nodes if n.get("name", "").lower() == pattern]
    if not matches:
        # Partial name match (contains)
        matches = [n for n in nodes if spec in n.get("name", "")]

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
        profile = detect_profile(cwd)
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


def cmd_install_gitleaks(args: argparse.Namespace) -> int:
    """Install gitleaks for secret scanning."""
    if args.check:
        # Just check availability
        available = is_gitleaks_available()
        symbol = "\u2713" if available else "\u2717"
        print(f"gitleaks: {symbol} {'installed' if available else 'not installed'}")

        if not available:
            print("\nRun 'hypergumbo install-gitleaks' to install.")
            return 1
        return 0

    # Install gitleaks
    success = install_gitleaks(quiet=args.quiet)
    return 0 if success else 1


def cmd_uninstall_gitleaks(args: argparse.Namespace) -> int:
    """Uninstall gitleaks secret scanner."""
    success = uninstall_gitleaks(quiet=args.quiet)
    return 0 if success else 1


def _get_cache_base() -> Path:
    """Get the hypergumbo cache base directory.

    Uses the same XDG-compliant path as sketch_embeddings.
    """
    from .sketch_embeddings import _get_xdg_cache_base

    return _get_xdg_cache_base()


def _format_size(size_bytes: int) -> str:
    """Format bytes into human-readable size."""
    for unit in ["B", "KB", "MB", "GB"]:
        if size_bytes < 1024:
            return f"{size_bytes:.1f} {unit}"
        size_bytes /= 1024
    return f"{size_bytes:.1f} TB"  # pragma: no cover - TB-scale caches extremely rare


def _get_dir_size(path: Path) -> int:
    """Get total size of a directory in bytes."""
    total = 0
    try:
        for entry in path.rglob("*"):
            if entry.is_file():
                try:
                    total += entry.stat().st_size
                except (OSError, PermissionError):  # pragma: no cover
                    pass  # pragma: no cover
    except (OSError, PermissionError):  # pragma: no cover
        pass  # pragma: no cover
    return total


def cmd_cache_status(args: argparse.Namespace) -> int:
    """Show cache status and statistics.

    Reports:
    - Number of cached repo entries
    - Total cache size
    - Cache location
    """
    import time

    cache_dir = _get_cache_base()

    if args.quiet:
        return 0

    if not cache_dir.exists():
        print(f"Cache directory: {cache_dir}")
        print("Status: empty (directory does not exist)")
        print("0 entries, 0 B")
        return 0

    # Count entries and compute size
    entries = [d for d in cache_dir.iterdir() if d.is_dir()]
    entry_count = len(entries)
    total_size = _get_dir_size(cache_dir)

    # Find oldest and newest entries
    if entries:
        mtimes = [(d, d.stat().st_mtime) for d in entries]
        oldest = min(mtimes, key=lambda x: x[1])
        newest = max(mtimes, key=lambda x: x[1])
        now = time.time()
        oldest_age = int((now - oldest[1]) / 86400)  # days
        newest_age = int((now - newest[1]) / 86400)
    else:
        oldest_age = newest_age = 0

    print(f"Cache directory: {cache_dir}")
    print(f"Entries: {entry_count}")
    print(f"Total size: {_format_size(total_size)}")
    if entries:
        print(f"Age range: {newest_age}-{oldest_age} days")

    return 0


def cmd_cache_clear(args: argparse.Namespace) -> int:
    """Clear the hypergumbo cache.

    Options:
    - --older-than N: Only remove entries older than N days
    - --dry-run: Show what would be deleted without deleting
    """
    import shutil
    import time

    cache_dir = _get_cache_base()

    if not cache_dir.exists():
        if not args.quiet:
            print(f"Cache directory does not exist: {cache_dir}")
        return 0

    entries = [d for d in cache_dir.iterdir() if d.is_dir()]
    if not entries:
        if not args.quiet:
            print("Cache is already empty.")
        return 0

    # Filter by age if --older-than is specified
    if args.older_than is not None:
        cutoff = time.time() - (args.older_than * 24 * 60 * 60)
        entries = [d for d in entries if d.stat().st_mtime < cutoff]

    if not entries:
        if not args.quiet:
            print(f"No entries older than {args.older_than} days found.")
        return 0

    total_size = sum(_get_dir_size(e) for e in entries)

    if args.dry_run:
        if not args.quiet:
            print(f"Would delete {len(entries)} entries ({_format_size(total_size)})")
            for entry in entries:
                print(f"  {entry.name}")
        return 0

    # Delete entries
    deleted_count = 0
    for entry in entries:
        try:
            shutil.rmtree(entry)
            deleted_count += 1
        except (OSError, PermissionError) as e:  # pragma: no cover
            if not args.quiet:  # pragma: no cover
                print(f"Warning: Could not delete {entry}: {e}")  # pragma: no cover

    if not args.quiet:
        print(f"Deleted {deleted_count} entries ({_format_size(total_size)})")

    return 0


def _is_embeddings_available() -> bool:
    """Check if sentence-transformers is available."""
    try:
        import sentence_transformers  # noqa: F401

        return True  # pragma: no cover
    except ImportError:  # pragma: no cover
        return False  # pragma: no cover


def _get_embeddings_version() -> str:
    """Get the installed sentence-transformers version."""
    try:
        import sentence_transformers

        return sentence_transformers.__version__  # pragma: no cover
    except (ImportError, AttributeError):  # pragma: no cover
        return "unknown"  # pragma: no cover


def cmd_install_embeddings(args: argparse.Namespace) -> int:
    """Install embedding dependencies (sentence-transformers).

    This enables:
    - Semantic code ranking
    - Elevator pitch generation
    - Config extraction with semantic filtering

    Note: This pulls in PyTorch (~2GB download).
    """
    if args.check:
        available = _is_embeddings_available()
        symbol = "\u2713" if available else "\u2717"
        status = "installed" if available else "not installed"
        print(f"embeddings: {symbol} {status}")
        if available:
            print(f"  sentence-transformers {_get_embeddings_version()}")
        else:
            print("\nRun 'hypergumbo install-embeddings' to install.")
            return 1
        return 0

    if _is_embeddings_available():
        if not args.quiet:
            print(f"Embeddings already installed (sentence-transformers {_get_embeddings_version()})")
        return 0

    if not args.quiet:
        print("Installing embedding dependencies...")
        print("Note: This pulls in PyTorch (~2GB download)")
        print()

    # Install via pip subprocess
    try:
        cmd = [sys.executable, "-m", "pip", "install", "sentence-transformers~=5.2.2"]
        if args.quiet:
            cmd.append("-q")
        result = subprocess.run(cmd, check=False)  # noqa: S603 # nosec B603
        if result.returncode != 0:
            print("Error: pip install failed", file=sys.stderr)
            return 1
    except (subprocess.SubprocessError, OSError) as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1

    if not args.quiet:
        print()
        print("Embedding features are now available:")
        print("  - hypergumbo sketch --elevator-pitch")
        print("  - Semantic code ranking")
        print("  - Config extraction with semantic filtering")

    return 0


def cmd_uninstall_embeddings(args: argparse.Namespace) -> int:
    """Uninstall embedding dependencies (sentence-transformers)."""
    if not _is_embeddings_available():
        if not args.quiet:
            print("Embeddings not installed. Nothing to do.")
        return 0

    if not args.quiet:
        print("Uninstalling sentence-transformers...")

    try:
        cmd = [sys.executable, "-m", "pip", "uninstall", "-y", "sentence-transformers"]
        if args.quiet:
            cmd.append("-q")
        result = subprocess.run(cmd, check=False)  # noqa: S603 # nosec B603
        if result.returncode != 0:
            print("Error: pip uninstall failed", file=sys.stderr)
            return 1
    except (subprocess.SubprocessError, OSError) as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1

    if args.all:
        if not args.quiet:
            print("Removing PyTorch...")
        for pkg in ["torch", "torchvision", "torchaudio"]:
            try:
                subprocess.run(  # noqa: S603 # nosec B603
                    [sys.executable, "-m", "pip", "uninstall", "-y", pkg],
                    capture_output=True,
                    check=False,
                )
            except (subprocess.SubprocessError, OSError):
                pass  # Ignore errors for packages that may not be installed

    if not args.quiet:
        print()
        print("Embeddings uninstalled.")
        print("hypergumbo will continue to work without embedding features.")

    return 0


def cmd_add_extras(args: argparse.Namespace) -> int:
    """Install all optional extras (grammars, gitleaks, embeddings).

    Skips components that are already installed, showing a message for each.
    """
    exit_code = 0

    # 1. Build grammars
    if not args.quiet:
        print("=== Grammars ===")
    status = check_grammar_availability()
    all_grammars_available = all(status.values())

    if all_grammars_available:
        if not args.quiet:
            print("All grammars already built. Skipping.")
    else:
        results = build_all_grammars(quiet=args.quiet)
        if not all(results.values()):
            failed = [name for name, ok in results.items() if not ok]
            print(f"Warning: Failed to build grammars: {', '.join(failed)}", file=sys.stderr)
            exit_code = 1

    if not args.quiet:
        print()

    # 2. Install gitleaks
    if not args.quiet:
        print("=== Gitleaks ===")
    if is_gitleaks_available():
        if not args.quiet:
            print("gitleaks already installed. Skipping.")
    else:
        success = install_gitleaks(quiet=args.quiet)
        if not success:
            exit_code = 1

    if not args.quiet:
        print()

    # 3. Install embeddings
    if not args.quiet:
        print("=== Embeddings ===")
    if _is_embeddings_available():
        if not args.quiet:
            print(f"Embeddings already installed (sentence-transformers {_get_embeddings_version()}). Skipping.")
    else:
        if not args.quiet:
            print("Installing embeddings...")
            print("Note: This pulls in PyTorch (~2GB download)")
            print()
        try:
            cmd = [sys.executable, "-m", "pip", "install", "sentence-transformers~=5.2.2"]
            if args.quiet:
                cmd.append("-q")
            result = subprocess.run(cmd, check=False)  # noqa: S603 # nosec B603
            if result.returncode != 0:
                print("Warning: Failed to install embeddings", file=sys.stderr)
                exit_code = 1
        except (subprocess.SubprocessError, OSError) as e:
            print(f"Warning: Failed to install embeddings: {e}", file=sys.stderr)
            exit_code = 1

    if not args.quiet:
        print()
        print("=== Summary ===")
        print("All extras installed. Run 'hypergumbo remove-extras' to uninstall.")

    return exit_code


def cmd_remove_extras(args: argparse.Namespace) -> int:
    """Uninstall optional extras (gitleaks, embeddings).

    Note: Grammars are not removed as they're just shared libraries.
    """
    exit_code = 0

    # 1. Uninstall gitleaks
    if not args.quiet:
        print("=== Gitleaks ===")
    success = uninstall_gitleaks(quiet=args.quiet)
    if not success:
        exit_code = 1

    if not args.quiet:
        print()

    # 2. Uninstall embeddings
    if not args.quiet:
        print("=== Embeddings ===")
    if not _is_embeddings_available():
        if not args.quiet:
            print("Embeddings not installed. Skipping.")
    else:
        if not args.quiet:
            print("Uninstalling sentence-transformers...")
        try:
            cmd = [sys.executable, "-m", "pip", "uninstall", "-y", "sentence-transformers"]
            if args.quiet:
                cmd.append("-q")
            result = subprocess.run(cmd, check=False)  # noqa: S603 # nosec B603
            if result.returncode != 0:
                print("Warning: Failed to uninstall embeddings", file=sys.stderr)
                exit_code = 1
        except (subprocess.SubprocessError, OSError) as e:
            print(f"Warning: Failed to uninstall embeddings: {e}", file=sys.stderr)
            exit_code = 1

    if not args.quiet:
        print()
        print("=== Summary ===")
        print("Extras removed. hypergumbo will continue to work with core features.")
        print("Run 'hypergumbo add-extras' to reinstall.")

    return exit_code


def cmd_symbols(args: argparse.Namespace) -> int:
    """Display symbol catalog with connectivity information.

    Shows a table of symbols sorted by bidirectional centrality with sink
    dampening.  Connectors (symbols with both incoming and outgoing edges)
    rank above pure sinks (high in-degree but low out-degree, like error
    sentinels and no-op stubs).

    Base formula: ``in_degree * (1 + ln(1 + out_degree))``, same as
    ``ranking.compute_centrality()``.  Additionally, symbols with very low
    out/in ratio get their effective in-degree reduced by a sink dampening
    factor (0.3 for pure sinks, rising to 1.0 at out/in >= 0.33).  This
    prevents trivial stubs with 100+ in-degree from dominating rankings
    over genuine architectural hubs.

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
    edges_raw = behavior_map.get("edges", [])

    # Build node ID set and ID->path mapping for filtering
    node_ids = {n["id"] for n in nodes}
    node_paths: dict[str, str] = {n["id"]: n.get("path", "") for n in nodes}

    # Check exclude_tests flag before computing degrees
    exclude_tests = getattr(args, "exclude_tests", False)

    # Convert to IR objects for rank_symbols()
    ir_symbols = [Symbol.from_dict(n) for n in nodes]
    ir_edges = [Edge.from_dict(e) for e in edges_raw]

    # Get canonical ranking from rank_symbols().
    # min_edge_confidence=0.5 excludes low-confidence inferred edges
    # (ast_method_inferred) that inflate in-degree for common method
    # names via name-collision false positives (e.g. .labels(), .rules()).
    ranked = rank_symbols(ir_symbols, ir_edges, min_edge_confidence=0.5)
    rank_by_id: dict[str, int] = {rs.symbol.id: rs.rank for rs in ranked}

    # Minimum edge confidence for degree *display* counts.
    # Low-confidence inferred edges (ast_method_inferred, <0.5) inflate
    # in-degree for common method names like .Lock(), .get(), .setValue().
    _MIN_EDGE_CONFIDENCE = 0.5

    # Compute in-degree and out-degree for display (In/Out/Deg columns).
    # This filtering is for display counts only — sort order comes from
    # rank_symbols() above.
    in_degree: dict[str, int] = {n["id"]: 0 for n in nodes}
    out_degree: dict[str, int] = {n["id"]: 0 for n in nodes}

    for edge in edges_raw:
        src = edge.get("src", "")
        dst = edge.get("dst", "")

        # Filter low-confidence edges to prevent method name collision
        # artifacts (DirLocker.Lock 255 false in-degree, etc.)
        if edge.get("confidence", 1.0) < _MIN_EDGE_CONFIDENCE:
            continue

        # If excluding tests, skip edges involving test files.
        # Structural edges (extends, implements) are always preserved
        # because they reflect architectural importance of the target
        # (base class / interface), regardless of where the source lives.
        if exclude_tests:
            edge_type = edge.get("type", "")
            if edge_type not in ("extends", "implements"):
                src_path = node_paths.get(src, _extract_path_from_symbol_id(src))
                dst_path = node_paths.get(dst, _extract_path_from_symbol_id(dst))
                if _is_test_path(src_path) or _is_test_path(dst_path):
                    continue

        if src in node_ids:
            out_degree[src] = out_degree.get(src, 0) + 1
        if dst in node_ids:
            in_degree[dst] = in_degree.get(dst, 0) + 1

    # Build list of symbols with their degrees
    # Tuple: (name, kind, in_degree, out_degree, total_degree, path, node_id)
    symbol_rows: list[tuple[str, str, int, int, int, str, str]] = []

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
        if kind in EXCLUDED_KINDS:
            continue
        if args.kind and kind != args.kind:
            continue
        if args.language and lang != args.language:
            continue
        if exclude_tests and _is_test_path(path):
            continue

        symbol_rows.append((name, kind, ind, outd, degree, path, node_id))

    # Sort by canonical rank_symbols() position (lower rank = more important)
    symbol_rows.sort(key=lambda r: (rank_by_id.get(r[6], len(nodes)), r[0]))

    # Apply --max-per-file limit if specified
    max_per_file = getattr(args, "max_per_file", None)
    if max_per_file is not None:
        file_counts: dict[str, int] = {}
        filtered_rows: list[tuple[str, str, int, int, int, str, str]] = []
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
    for name, kind, ind, outd, degree, path, _nid in display_rows:
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
            json.dump(compact_map, f, indent=2, sort_keys=True)
        print(f"Compact behavior map written to: {out_path}")
    else:
        print(json.dumps(compact_map, indent=2, sort_keys=True))

    return 0


def cmd_io_boundaries(args: argparse.Namespace) -> int:
    """Display I/O boundary map for a repository (ADR-0016).

    Identifies call edges that reach I/O primitives (filesystem, network,
    subprocess, environment) and groups them by boundary type. Loads a
    cached behavior map or auto-runs analysis if needed.
    """
    repo_root = Path(args.path).resolve()

    input_path, was_cached, generated_files = _get_or_run_analysis(
        repo_root,
        explicit_input=getattr(args, "input", None),
        show_progress=True,
    )
    if input_path is None:
        print(
            f"Error: Input file not found: {getattr(args, 'input', None)}",
            file=sys.stderr,
        )
        return 1

    behavior_map = json.loads(input_path.read_text())
    raw_edges = behavior_map.get("edges", [])

    # Build lightweight edge objects for the tagging pass
    from dataclasses import dataclass as _dc

    @_dc
    class _Edge:
        src: str
        dst: str
        edge_type: str
        meta: Optional[Dict[str, Any]] = None

    edges = [
        _Edge(
            src=e.get("src", ""),
            dst=e.get("dst", ""),
            edge_type=e.get("type", ""),
            meta=dict(e.get("meta", {})) if e.get("meta") else None,
        )
        for e in raw_edges
    ]

    # Detect languages in the graph
    from .io_boundary import compute_boundary_map, load_catalog

    languages: set[str] = set()
    for node in behavior_map.get("nodes", []):
        lang = node.get("language")
        if lang:
            languages.add(lang)

    # Load catalogs for detected languages
    # INV-javam: track unsupported languages (no catalog) separately from
    # supported-but-zero-matches languages. The former must be surfaced
    # to callers so "zero I/O detected" isn't silently indistinguishable
    # from "language has no catalog at all".
    catalogs = {}
    unsupported_languages: list[str] = []
    for lang in sorted(languages):
        catalog = load_catalog(lang)
        if not catalog.is_supported:
            unsupported_languages.append(lang)
            continue
        if catalog.primitives:
            catalogs[lang] = catalog
            # Also key by the catalog's base language so edge-prefix lookups
            # work (e.g., nodes say "objective-c" but edges use "objc:" prefix)
            if catalog.language != lang:
                catalogs[catalog.language] = catalog

    # Extract entrypoint IDs for reverse-trace
    entrypoint_ids = {
        ep.get("symbol_id", ep.get("node_id", ""))
        for ep in behavior_map.get("entrypoints", [])
    }

    # Compute boundary map with entrypoint tracing
    bmap = compute_boundary_map(edges, catalogs, entrypoint_ids=entrypoint_ids or None)

    # Build node lookup for human-readable caller names
    nodes_by_id: Dict[str, Any] = {n["id"]: n for n in behavior_map.get("nodes", [])}

    # Apply boundary/primitive/exclude-tests filters
    boundary_filter = getattr(args, "boundary", None)
    primitive_filter = getattr(args, "primitive", None)
    # WI-sifif: production-only is the default. Tests can opt-in to the
    # historical "show everything" behavior by overriding exclude_tests=False;
    # CLI users do the same via --include-tests.
    exclude_tests = getattr(args, "exclude_tests", True)

    from .io_boundary import BoundaryMapEntry

    filtered_entries: Dict[str, BoundaryMapEntry] = {}
    for btype, entry in bmap.entries.items():
        if boundary_filter and btype != boundary_filter:
            continue

        chains = entry.chains

        # Filter by primitive
        if primitive_filter:
            chains = [c for c in chains if c.primitive == primitive_filter]

        # Filter out chains where the source symbol is in a test file
        if exclude_tests:
            def _is_test_chain(chain: Any) -> bool:
                src_node = nodes_by_id.get(chain.io_edge_src)
                if src_node:
                    return _is_test_node(src_node.get("path", ""), src_node.get("meta"))
                return False
            chains = [c for c in chains if not _is_test_chain(c)]

        if not chains and (primitive_filter or exclude_tests):
            continue

        if primitive_filter or exclude_tests:
            filtered_entries[btype] = BoundaryMapEntry(
                boundary=entry.boundary,
                chains=chains,
                entry_points=sorted({ep for c in chains for ep in c.entry_points}),
                primitives_used=sorted({c.primitive for c in chains}),
            )
        else:
            filtered_entries[btype] = entry

    # Output
    if getattr(args, "json_output", False):
        if boundary_filter or primitive_filter or exclude_tests:
            filtered_total = sum(len(e.chains) for e in filtered_entries.values())
            output = {
                "total_io_edges": filtered_total,
                "boundaries": {
                    k: v.to_dict() for k, v in sorted(filtered_entries.items())
                },
            }
        else:
            output = bmap.to_dict()
        # INV-javam: expose unsupported-language signal to programmatic
        # consumers. Empty list when every detected language has a catalog.
        output["unsupported_languages"] = unsupported_languages
        print(json.dumps(output, indent=2, sort_keys=True))
    elif getattr(args, "by_file", False):
        _print_io_boundaries_by_file(filtered_entries, nodes_by_id, repo_root)
        _print_unsupported_languages_notice(unsupported_languages)
    else:
        _print_io_boundaries_by_type(filtered_entries, nodes_by_id, bmap, repo_root)
        _print_unsupported_languages_notice(unsupported_languages)

    return 0


def _print_unsupported_languages_notice(
    unsupported_languages: list[str],
) -> None:
    """INV-javam: emit an explicit notice when the repo contains languages
    with no I/O primitive catalog.

    Without this, the human-readable output for an unsupported language
    is indistinguishable from a genuinely I/O-free codebase — and
    downstream taint-flow trivially passes every claim on those
    languages (false security confidence). The notice runs to stderr so
    piping the boundary report to a file / grep / jq is unaffected.
    """
    if not unsupported_languages:
        return
    langs = ", ".join(unsupported_languages)
    print(
        f"\nNote: no I/O primitive catalog for language(s): {langs}. "
        "Zero boundaries reported for these languages does NOT mean "
        "the code is I/O-free — it means hypergumbo cannot detect I/O "
        "for this language yet. (INV-javam)",
        file=sys.stderr,
    )


def _format_io_caller(
    symbol_id: str,
    nodes_by_id: Dict[str, Any],
    repo_root: Optional[Path] = None,
) -> str:
    """Format a symbol ID into a readable 'name (file:line)' string.

    Looks up the symbol in the node index for structured data; falls back
    to parsing the symbol ID string directly.
    """
    node = nodes_by_id.get(symbol_id)
    if node:
        name = node.get("name", "?")
        fpath = node.get("path", "")
        line = node.get("span", {}).get("start_line", "")
        display_path = _relativize(fpath, repo_root)
        if display_path and line:
            return f"{name} ({display_path}:{line})"
        if display_path:
            return f"{name} ({display_path})"
        return name

    # Fallback: parse from symbol ID
    parts = symbol_id.split(":")
    if len(parts) >= 5:
        name = parts[-2]
        raw_path = _extract_path_from_symbol_id(symbol_id)
        display_path = _relativize(raw_path, repo_root) if raw_path else ""
        span = parts[2] if len(parts) >= 3 else ""
        line = span.split("-")[0] if "-" in span else span
        if display_path and line:
            return f"{name} ({display_path}:{line})"
        if display_path:  # pragma: no cover — path extraction requires span pattern
            return f"{name} ({display_path})"
        return name
    return symbol_id


def _relativize(path: str, repo_root: Optional[Path]) -> str:
    """Make a path relative to repo_root if possible, for shorter display."""
    if not path or not repo_root:
        return path
    try:
        return str(Path(path).relative_to(repo_root))
    except ValueError:
        return path


def _print_io_boundaries_by_type(
    entries: Dict[str, Any],
    nodes_by_id: Dict[str, Any],
    bmap: Any,
    repo_root: Path,
) -> None:
    """Print I/O boundaries grouped by boundary type with call-site detail."""
    from .io_boundary import is_high_risk

    if not entries:
        print("No I/O boundary calls detected.")
        return

    total = sum(len(e.chains) for e in entries.values())
    print(f"I/O Boundary Map ({total} boundary calls)\n")

    for boundary_type in sorted(entries.keys()):
        entry = entries[boundary_type]
        has_risk = any(is_high_risk(c.primitive) for c in entry.chains)
        risk_marker = " [HIGH RISK]" if has_risk else ""
        print(f"  {boundary_type}: {len(entry.chains)} call(s){risk_marker}")

        # Per-primitive counts and call sites
        prim_counts: Dict[str, int] = {}
        chains_by_prim: Dict[str, list] = {}
        for chain in entry.chains:
            prim_counts[chain.primitive] = prim_counts.get(chain.primitive, 0) + 1
            chains_by_prim.setdefault(chain.primitive, []).append(chain)

        for prim in sorted(prim_counts.keys()):
            count = prim_counts[prim]
            risk_flag = " *** HIGH RISK ***" if is_high_risk(prim) else ""
            print(f"    {prim} ({count}){risk_flag}")
            for chain in chains_by_prim[prim]:
                caller = _format_io_caller(chain.io_edge_src, nodes_by_id, repo_root)
                print(f"      <- {caller}")
                if chain.entry_points:
                    ep_names = [
                        _format_io_caller(ep, nodes_by_id, repo_root)
                        for ep in chain.entry_points
                    ]
                    print(f"         reachable from: {', '.join(ep_names)}")

        if entry.entry_points:
            print(f"    ({len(entry.entry_points)} entry point(s) reach this boundary)")
        print()


def _print_io_boundaries_by_file(
    entries: Dict[str, Any],
    nodes_by_id: Dict[str, Any],
    repo_root: Path,
) -> None:
    """Print I/O boundaries grouped by source file."""
    from collections import defaultdict

    from .io_boundary import is_high_risk

    if not entries:
        print("No I/O boundary calls detected.")
        return

    # Group all chains by source file
    chains_by_file: Dict[str, list] = defaultdict(list)
    for entry in entries.values():
        for chain in entry.chains:
            raw_path = _extract_path_from_symbol_id(chain.io_edge_src)
            display_path = _relativize(raw_path, repo_root) if raw_path else "unknown"
            chains_by_file[display_path].append(chain)

    total = sum(len(v) for v in chains_by_file.values())
    print(f"I/O Boundary Map by File ({total} boundary calls)\n")

    for filepath in sorted(chains_by_file.keys()):
        file_chains = chains_by_file[filepath]
        has_risk = any(is_high_risk(c.primitive) for c in file_chains)
        risk_marker = " [HIGH RISK]" if has_risk else ""
        print(f"  {filepath}: {len(file_chains)} call(s){risk_marker}")
        for chain in file_chains:
            caller = _format_io_caller(chain.io_edge_src, nodes_by_id, repo_root)
            risk_flag = " *** HIGH RISK ***" if is_high_risk(chain.primitive) else ""
            print(f"    [{chain.boundary}] {chain.primitive} <- {caller}{risk_flag}")
            if chain.entry_points:
                ep_names = [
                    _format_io_caller(ep, nodes_by_id, repo_root)
                    for ep in chain.entry_points
                ]
                print(f"      reachable from: {', '.join(ep_names)}")
        print()


def cmd_verify_claims(args: argparse.Namespace) -> int:
    """Verify security claims against I/O boundary map and taint flow.

    Loads claims from a YAML file, computes the I/O boundary map, runs
    taint-flow analysis if needed, and checks each claim. Returns exit
    code 1 if any claim is violated. Supports boundary constraints
    (ADR-0016) and taint-flow constraints (ADR-0017).
    """
    repo_root = Path(args.path).resolve()
    claims_path = Path(args.claims)

    if not claims_path.exists():
        print(f"Error: Claims file not found: {claims_path}", file=sys.stderr)
        return 1

    # Load claims
    from .verify_claims import load_claims, verify_claims as _verify

    claims = load_claims(claims_path)
    if not claims:
        print("No claims found in file.")
        return 0

    # Get boundary map
    input_path, was_cached, generated_files = _get_or_run_analysis(
        repo_root,
        explicit_input=getattr(args, "input", None),
        show_progress=True,
    )
    if input_path is None:
        print(
            f"Error: Input file not found: {getattr(args, 'input', None)}",
            file=sys.stderr,
        )
        return 1

    behavior_map = json.loads(input_path.read_text())
    raw_edges = behavior_map.get("edges", [])

    from dataclasses import dataclass as _dc

    @_dc
    class _Edge:
        src: str
        dst: str
        edge_type: str
        meta: Optional[dict] = None

    edges = [
        _Edge(
            src=e.get("src", ""),
            dst=e.get("dst", ""),
            edge_type=e.get("type", ""),
            meta=dict(e.get("meta", {})) if e.get("meta") else None,
        )
        for e in raw_edges
    ]

    from .io_boundary import compute_boundary_map, load_catalog

    languages: set[str] = set()
    for node in behavior_map.get("nodes", []):
        lang = node.get("language")
        if lang:
            languages.add(lang)

    catalogs = {}
    for lang in languages:
        catalog = load_catalog(lang)
        if catalog.primitives:
            catalogs[lang] = catalog
            if catalog.language != lang:
                catalogs[catalog.language] = catalog

    # Extract entrypoint IDs for reverse-trace
    vc_entrypoint_ids = {
        ep.get("symbol_id", ep.get("node_id", ""))
        for ep in behavior_map.get("entrypoints", [])
    }
    bmap = compute_boundary_map(edges, catalogs, entrypoint_ids=vc_entrypoint_ids or None)

    # Run taint-flow analysis if any claims have taint_flow constraints
    taint_findings = None
    # INV-javam: track languages with no taint coverage so callers can
    # distinguish "no taint-flow violations" from "language not analyzed".
    # Without this, taint-flow trivially passes every claim on unsupported
    # languages and the verify-claims output lies by omission.
    unsupported_taint_languages: list[str] = []
    has_taint_claims = any(c.constraint_taint_flow is not None for c in claims)
    if has_taint_claims:
        from .taint import load_builtin_taint_catalog, propagate_taint_structural
        taint_catalog = load_builtin_taint_catalog()

        # Also load project-local taint catalogs if specified in claims file
        # (future: --taint-sources, --taint-sinks, --taint-sanitizers args)

        # Collect all sources, sinks, sanitizers across languages
        all_sources = []
        all_sinks = []
        all_sanitizers = []
        for lang in sorted(languages):
            src_count = len(taint_catalog.sources_for_language(lang))
            snk_count = len(taint_catalog.sinks_for_language(lang))
            if src_count == 0 and snk_count == 0:
                # Neither sources nor sinks for this language — taint-flow
                # cannot meaningfully analyze it. Surface the gap.
                unsupported_taint_languages.append(lang)
            all_sources.extend(taint_catalog.sources_for_language(lang))
            all_sinks.extend(taint_catalog.sinks_for_language(lang))
            all_sanitizers.extend(taint_catalog.sanitizers_for_language(lang))

        if all_sources and all_sinks:
            taint_findings = propagate_taint_structural(
                raw_edges, all_sources, all_sinks, all_sanitizers,
            )

    # Verify claims
    verdicts = _verify(claims, bmap, taint_findings=taint_findings)

    # Output
    if getattr(args, "json_output", False):
        # Preserve the legacy flat-list schema for programmatic consumers;
        # INV-javam's unsupported_taint_languages signal goes to stderr to
        # avoid breaking existing pipelines that parse verify-claims JSON.
        print(json.dumps([v.to_dict() for v in verdicts], indent=2))
    else:
        violated = 0
        for v in verdicts:
            icon = "✓" if v.verdict == "confirmed" else "✗"
            print(f"  {icon} [{v.claim_id}] {v.claim_text}")
            print(f"    Verdict: {v.verdict}")
            if v.details:
                print(f"    {v.details}")
            if v.verdict == "violated":
                violated += 1
        print()
        if violated:
            print(f"{violated}/{len(verdicts)} claim(s) VIOLATED")
        else:
            print(f"All {len(verdicts)} claim(s) CONFIRMED")

    # INV-javam: warn to stderr when taint claims were evaluated against a
    # repo whose languages have no taint catalog coverage. Even a "all
    # confirmed" verdict is misleading when the language wasn't analyzed.
    if has_taint_claims and unsupported_taint_languages:
        langs = ", ".join(unsupported_taint_languages)
        print(
            f"\nNote: no taint-flow catalog for language(s): {langs}. "
            "Claims touching these languages are NOT actually verified — "
            "taint-flow has no sources/sinks to trace. Treat 'confirmed' "
            "verdicts on these languages as inconclusive. (INV-javam)",
            file=sys.stderr,
        )

    has_violations = any(v.verdict == "violated" for v in verdicts)
    return 1 if has_violations else 0


def cmd_config(args: argparse.Namespace) -> int:
    """Show per-language configuration (dataflow patterns, IO primitives, etc.).

    Walks YAML config directories within the hypergumbo-core package and
    merges all config sections for the requested language into a single
    document.  Each section is loaded from a ``<lang>.yaml`` file in the
    corresponding config directory; missing files produce ``null`` for
    that section.

    WI-siran: discoverability for per-language config.
    """
    import yaml as _yaml

    lang = args.language.lower()
    fmt = args.format

    # Locate config directories relative to this package
    pkg_root = Path(__file__).parent
    config_dirs = {
        "dataflow_patterns": pkg_root / "dataflow_patterns",
        "io_primitives": pkg_root / "io_primitives",
        "function_summaries": pkg_root / "function_summaries",
    }

    merged: dict[str, Any] = {}
    found_any = False

    for section_name, config_dir in config_dirs.items():
        yaml_path = config_dir / f"{lang}.yaml"
        if yaml_path.exists():
            try:
                content = _yaml.safe_load(yaml_path.read_text())
                merged[section_name] = content
                found_any = True
            except Exception:
                merged[section_name] = None
        else:
            merged[section_name] = None

    if not found_any:
        print(
            f"Warning: no configuration found for language '{lang}'",
            file=sys.stderr,
        )

    if fmt == "json":
        print(json.dumps(merged, indent=2, default=str))
    elif fmt == "yaml":
        print(_yaml.dump(merged, default_flow_style=False, sort_keys=False))
    else:
        # text format: pretty-printed summary
        print(f"Configuration for: {lang}")
        print("=" * 40)
        for section_name, content in merged.items():
            if content is None:
                print(f"\n{section_name}: (not configured)")
            else:
                print(f"\n{section_name}:")
                if isinstance(content, dict):
                    for key in content:
                        if key == "language":
                            continue
                        val = content[key]
                        if isinstance(val, list):
                            print(f"  {key}: {len(val)} rule(s)")
                        else:
                            print(f"  {key}: {val}")  # pragma: no cover
                else:
                    print(f"  ({type(content).__name__})")  # pragma: no cover

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

        print(json.dumps(output, indent=2, sort_keys=True))
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


# WI-hadap H2: FFI decorator / modifier markers. Presence of any of
# these on a dead-code-maybe candidate is a "free hit" — the candidate
# is definitionally FFI-bound, so a missing linker edge is a
# high-confidence linker gap rather than a noise false positive.
#
# Exact-match decorator names (no pattern). Includes:
# - Rust: #[no_mangle], #[pyo3::*], #[napi], #[wasm_bindgen]
# - Python: @ctypes.CFUNCTYPE, @cffi.ffi, @cython.cclass
# - C/C++: JNIEXPORT (declared as a macro, surfaces as modifier/decorator)
# - C#: [DllImport]
# - Go: //export (emitted as a meta field by the Go analyzer)
#
# Match is substring-insensitive on the decorator/modifier name string
# so pkg.attr forms like ``pyo3::pyfunction`` or ``cython.cclass``
# match on their short name prefix.
_FFI_DECORATOR_FRAGMENTS = (
    "no_mangle",
    "pyo3",
    "napi",
    "wasm_bindgen",
    "cfunctype",
    "dllimport",
    "jniexport",
    "cython",
    "cffi",
    "ctypes",
    "nativegen",
)

# Exact-match modifier strings (as emitted by language analyzers).
_FFI_MODIFIER_EXACT = frozenset({
    "extern",
    "extern \"C\"",
    "native",   # Java native methods
    "dllimport",
})


def _compute_ffi_signature_flag(node: dict) -> bool:
    """Return True if *node* has an FFI-signaling decorator or modifier.

    WI-hadap H2: a dead-code-maybe candidate with an FFI signature is
    almost certainly a missing cross-language linker edge — it's a
    "free hit" for the prospector. The symbol declares itself as the
    boundary of a language crossing (Rust #[pyo3], Python ctypes,
    C extern "C", C# [DllImport], Java native, etc.), so if nothing
    in the static call graph reaches it, a linker hasn't closed the
    gap yet.

    Checks the candidate's ``meta.decorators`` (list of
    ``{"name": str, ...}`` dicts) and ``meta.modifiers`` or
    top-level ``modifiers`` list. Match is substring-insensitive on
    the decorator name to cover qualified forms like
    ``pyo3::pyfunction`` or ``cython.cclass``.
    """
    meta = node.get("meta") or {}

    decorators = meta.get("decorators") or []
    if isinstance(decorators, list):
        for dec in decorators:
            if isinstance(dec, dict):
                dec_name = dec.get("name", "")
            elif isinstance(dec, str):
                dec_name = dec
            else:
                dec_name = ""
            dec_lower = dec_name.lower()
            for frag in _FFI_DECORATOR_FRAGMENTS:
                if frag in dec_lower:
                    return True

    modifiers = node.get("modifiers") or meta.get("modifiers") or []
    if isinstance(modifiers, list):
        for mod in modifiers:
            if isinstance(mod, str):
                if mod in _FFI_MODIFIER_EXACT or mod.lower() == "jniexport":
                    return True

    return False


def _compute_path_shape_boost(node: dict) -> int:
    """Boost score for candidates with cross-language path/name shapes.

    Candidates in directories named api/, rpc/, proto/, ffi/, native/,
    bindings/, bridge/, or with names containing handler, _request,
    _response, serialize, to_json, from_json are more likely to be
    missing cross-language linker edges than plain internal functions.

    Returns 1 per matching signal (path or name), capped at 2.
    """
    _PATH_SEGMENTS = {
        "api", "rpc", "proto", "ffi", "native", "bindings", "bridge",
    }
    _NAME_FRAGMENTS = (
        "handler", "_request", "_response", "serialize",
        "to_json", "from_json",
    )

    boost = 0
    path = (node.get("path") or "").lower()
    name = (node.get("name") or "").lower()

    # Path segment check
    path_parts = set(path.replace("\\", "/").split("/"))
    if path_parts & _PATH_SEGMENTS:
        boost += 1

    # Name fragment check
    for frag in _NAME_FRAGMENTS:
        if frag in name:
            boost += 1
            break  # only count one name match

    return boost


def _compute_cross_language_hits(
    dead_candidates: list[dict],
    repo_root: Path,
) -> dict[str, int]:
    """Count cross-language string collisions for dead-code candidates.

    For each dead candidate, checks whether its symbol name appears as a
    substring in files whose language differs from the candidate's language.
    A cross-language hit is a strong signal of a missing linker edge (HTTP
    path, RPC method, MQ topic, FFI name).

    Uses a simple inverted index: collect unique candidate names, then scan
    non-binary files in the repo for occurrences.  Only counts hits in
    files whose extension maps to a different language family.

    Returns mapping of candidate ID → cross-language hit count.
    """
    # Map file extensions to language families (coarse grouping)
    _EXT_TO_LANG: dict[str, str] = {
        ".py": "python", ".pyi": "python",
        ".go": "go",
        ".java": "java", ".kt": "kotlin", ".kts": "kotlin",
        ".js": "javascript", ".jsx": "javascript", ".mjs": "javascript",
        ".ts": "typescript", ".tsx": "typescript",
        ".rs": "rust",
        ".rb": "ruby",
        ".c": "c", ".h": "c",
        ".cpp": "cpp", ".hpp": "cpp", ".cc": "cpp", ".cxx": "cpp",
        ".cs": "csharp",
        ".swift": "swift",
        ".php": "php",
        ".scala": "scala",
        ".ex": "elixir", ".exs": "elixir",
        ".erl": "erlang",
        ".lua": "lua",
        ".yaml": "config", ".yml": "config", ".json": "config",
        ".toml": "config", ".xml": "config", ".html": "config",
    }

    # Collect unique names from dead candidates, grouped by language
    name_to_candidates: dict[str, list[dict]] = {}
    for candidate in dead_candidates:
        name = candidate.get("name", "")
        # Use the short name (after last dot for methods)
        short_name = name.rsplit(".", 1)[-1] if "." in name else name
        # Skip very short names (too many false positives)
        if len(short_name) < 4:
            continue
        name_to_candidates.setdefault(short_name, []).append(candidate)

    if not name_to_candidates:
        return {}

    # Build set of names to search for
    search_names = set(name_to_candidates.keys())

    # Scan repo files for string occurrences
    hits: dict[str, int] = {}
    try:
        for dirpath, _dirnames, filenames in os.walk(repo_root):
            rel_dir = os.path.relpath(dirpath, repo_root)
            # Skip hidden dirs, vendor, node_modules
            # (rel_dir "." is the root itself — not hidden)
            if rel_dir != "." and any(
                part.startswith(".") or part in ("vendor", "node_modules",
                    "third_party", "__pycache__")
                for part in rel_dir.split(os.sep)
            ):
                continue
            for fname in filenames:
                ext = os.path.splitext(fname)[1].lower()
                file_lang = _EXT_TO_LANG.get(ext)
                if not file_lang:
                    continue
                fpath = os.path.join(dirpath, fname)
                try:
                    content = open(fpath, encoding="utf-8", errors="ignore").read()
                except OSError:  # pragma: no cover
                    continue
                # Check each candidate name against this file
                for name in search_names:
                    if name in content:
                        for candidate in name_to_candidates[name]:
                            cand_lang = candidate.get("language", "")
                            # Only count if different language
                            if cand_lang and file_lang != cand_lang:
                                hits[candidate["id"]] = (
                                    hits.get(candidate["id"], 0) + 1
                                )
    except OSError:  # pragma: no cover — repo_root unreadable
        pass

    return hits


def cmd_dead_code_maybe(args: argparse.Namespace) -> int:
    """Find potentially dead code: production callables unreachable from entrypoints.

    Computes: dead = production_callables - reachable_from(seed_set)

    The seed set is configurable via ``--seeds``:
    - ``entrypoints``: CLI mains, HTTP routes, framework hooks (default)
    - ``tests``: test functions only
    - ``exports``: symbols with ``is_exported=True`` (public API, WI-zimum)
    - ``all``: entrypoints + tests + exports

    Uses BFS over call edges from seed symbols.  Functions not visited
    are flagged as potentially dead.  Results are ranked by lines of code
    (larger unreachable functions first).
    """
    repo_root = Path(args.path).resolve()

    input_path, was_cached, generated_files = _get_or_run_analysis(
        repo_root,
        explicit_input=args.input,
        show_progress=True,
    )
    if input_path is None:
        print(f"Error: Input file not found: {args.input}", file=sys.stderr)
        return 1

    behavior_map = json.loads(input_path.read_text())
    nodes = behavior_map.get("nodes", [])
    edges = behavior_map.get("edges", [])
    # Identify production callable symbols (exclude test files)
    production_symbols: dict[str, dict] = {}
    test_symbols: set[str] = set()
    exported_symbols: set[str] = set()
    for node in nodes:
        path = node.get("path", "")
        kind = node.get("kind", "")
        if kind not in ("function", "method"):
            continue
        if _is_test_path(path):
            test_symbols.add(node["id"])
        else:
            production_symbols[node["id"]] = node
            # WI-zimum: is_exported is stored under supply_chain in the
            # behavior map. A production symbol with is_exported=True is
            # part of the public API and should be unconditionally
            # reachable (external callers are outside the analysis scope).
            sc = node.get("supply_chain") or {}
            if sc.get("is_exported"):
                exported_symbols.add(node["id"])

    if not production_symbols:
        print("No production functions found to analyze.", file=sys.stderr)
        return 0

    # Build seed set based on --seeds flag
    seed_ids: set[str] = set()
    seeds_mode = getattr(args, "seeds", "entrypoints")

    if seeds_mode in ("entrypoints", "all"):
        from .entrypoints import detect_entrypoints
        from .ir import Symbol, Edge, Span

        # Convert dict nodes/edges to IR objects for detect_entrypoints
        ir_nodes = []
        for n in nodes:
            span_data = n.get("span", {})
            sym = Symbol(
                id=n["id"],
                name=n.get("name", ""),
                kind=n.get("kind", ""),
                language=n.get("language", ""),
                path=n.get("path", ""),
                span=Span(
                    start_line=span_data.get("start_line", 0),
                    end_line=span_data.get("end_line", 0),
                    start_col=span_data.get("start_col", 0),
                    end_col=span_data.get("end_col", 0),
                ),
                meta=n.get("meta"),
            )
            ir_nodes.append(sym)

        ir_edges = []
        for e in edges:
            ir_edges.append(Edge(
                id=e.get("id", ""),
                src=e.get("src", ""),
                dst=e.get("dst", ""),
                edge_type=e.get("type", "calls"),
                line=e.get("line", 0),
                confidence=e.get("confidence", 0.85),
            ))

        min_conf = getattr(args, "min_confidence", 0.0)
        entrypoints = detect_entrypoints(ir_nodes, ir_edges)
        for ep in entrypoints:
            if ep.confidence >= min_conf:
                seed_ids.add(ep.symbol_id)

    if seeds_mode in ("tests", "all"):
        seed_ids.update(test_symbols)

    # WI-zimum: exported symbols (public API) as seeds.
    if seeds_mode in ("exports", "all"):
        seed_ids.update(exported_symbols)

    # BFS from seeds through call-flow edges.
    # calls:          direct function/method calls
    # dispatches_to:  interface/abstract method → concrete implementation
    # routes_to:      HTTP route registration → handler function
    # wraps:          middleware wrapper → inner handler
    _REACHABILITY_EDGE_TYPES = {"calls", "dispatches_to", "routes_to", "wraps"}
    call_graph: dict[str, list[str]] = {}
    for edge in edges:
        if edge.get("type") in _REACHABILITY_EDGE_TYPES:
            src = edge.get("src", "")
            dst = edge.get("dst", "")
            if src and dst:
                call_graph.setdefault(src, []).append(dst)

    reachable: set[str] = set()
    queue = list(seed_ids)
    visited: set[str] = set(seed_ids)
    while queue:
        current = queue.pop()
        reachable.add(current)
        for neighbor in call_graph.get(current, []):
            if neighbor not in visited:
                visited.add(neighbor)
                queue.append(neighbor)

    # Dead candidates = production symbols NOT reachable
    dead_candidates = []
    exclude_annotated = getattr(args, "exclude_annotated", False)
    exclude_exports = getattr(args, "exclude_exports", False)
    for sym_id, node in production_symbols.items():
        if sym_id not in reachable:
            # --exclude-annotated: skip candidates with decorators,
            # annotations, or framework concepts (these are likely
            # framework-registered callbacks, not linker gaps).
            if exclude_annotated:
                meta = node.get("meta") or {}
                if (meta.get("decorators") or meta.get("annotations")
                        or meta.get("concepts")):
                    continue
            # WI-zafab filter 3: skip candidates that are part of the
            # repo's public API (Symbol.is_exported=True). Exported
            # symbols are reachable by external callers outside the
            # analysis scope — an unreached exported symbol is a
            # definitional false positive for the linker-gap bucket.
            if exclude_exports:
                sc = node.get("supply_chain") or {}
                if sc.get("is_exported"):
                    continue
            dead_candidates.append(node)

    # Cross-language string collision: check if dead candidate names
    # appear as substrings in files of a different language.  A hit is
    # a near-certain signal of a missing cross-language reference
    # (HTTP path, RPC method, MQ topic, FFI name).
    cross_lang_hits: dict[str, int] = {}
    if dead_candidates:
        cross_lang_hits = _compute_cross_language_hits(
            dead_candidates, repo_root,
        )

    # Path/name shape boost: candidates in cross-language directories
    # (api/, rpc/, proto/, ffi/, native/, bindings/, bridge/) or with
    # cross-language naming conventions (handler, _request, _response,
    # serialize, to_json) are more likely to be missing linker edges.
    shape_boosts: dict[str, int] = {}
    ffi_flags: dict[str, bool] = {}
    for node in dead_candidates:
        boost = _compute_path_shape_boost(node)
        if boost > 0:
            shape_boosts[node["id"]] = boost
        # WI-hadap H2: FFI signature auto-flag. An FFI-shaped candidate
        # is a "free hit" — definitional cross-language boundary.
        if _compute_ffi_signature_flag(node):
            ffi_flags[node["id"]] = True

    # FFI-flagged candidates get an additive rank boost so they
    # surface at the top of the "missing linker edge" list even when
    # their cross-language string-hits count is low.
    _FFI_RANK_BOOST = 10

    # Sort by (cross-lang hits + shape boost + FFI boost) desc, then LOC desc
    dead_candidates.sort(
        key=lambda n: (
            -(
                cross_lang_hits.get(n["id"], 0)
                + shape_boosts.get(n["id"], 0)
                + (_FFI_RANK_BOOST if ffi_flags.get(n["id"]) else 0)
            ),
            -(n.get("lines_of_code") or 1),
        ),
    )

    # Summary stats
    total_production = len(production_symbols)
    total_reachable = len(reachable & set(production_symbols.keys()))
    total_dead = len(dead_candidates)
    total_entrypoints = len(seed_ids)

    if args.format == "json":
        output = {
            "summary": {
                "total_production_functions": total_production,
                "reachable_functions": total_reachable,
                "dead_candidates": total_dead,
                "seed_count": total_entrypoints,
                "seeds_mode": seeds_mode,
                "dead_percent": round(total_dead / max(total_production, 1) * 100, 1),
            },
            "dead_candidates": [
                {
                    "name": n.get("name", ""),
                    "path": n.get("path", ""),
                    "language": n.get("language", ""),
                    "kind": n.get("kind", ""),
                    "lines_of_code": n.get("lines_of_code"),
                    "span": n.get("span"),
                    "id": n["id"],
                    "cross_language_hits": cross_lang_hits.get(n["id"], 0),
                    "path_shape_boost": shape_boosts.get(n["id"], 0),
                    "ffi_signature": ffi_flags.get(n["id"], False),
                }
                for n in dead_candidates
            ],
        }
        print(json.dumps(output, indent=2))
    else:
        # Text format
        print(f"Dead Code Analysis (seeds: {seeds_mode})")
        print(f"{'=' * 50}")
        print(f"Production functions: {total_production}")
        print(f"Entrypoints/seeds:    {total_entrypoints}")
        print(f"Reachable:            {total_reachable}")
        print(f"Potentially dead:     {total_dead} "
              f"({total_dead / max(total_production, 1) * 100:.1f}%)")
        print()

        if dead_candidates:
            print("Potentially dead functions (by LOC, largest first):")
            print(f"{'─' * 70}")
            for n in dead_candidates[:50]:
                name = n.get("name", "?")
                path = n.get("path", "?")
                loc = n.get("lines_of_code") or "?"
                print(f"  {name:<30} {path:<30} {loc:>5} LOC")

            if len(dead_candidates) > 50:  # pragma: no cover
                print(f"  ... and {len(dead_candidates) - 50} more")
        else:
            print("No potentially dead functions found.")

    return 0


def _positive_token_budget(raw: str) -> int:
    """argparse type for --tokens: require a positive integer.

    WI-pokor (UAT BUG-02+03): ``-t 0`` was silently treated as "no budget"
    and produced the default 8000-token sketch; negative values produced
    a header-only output with exit code 0. Both are configuration errors
    and should fail fast with a clear message.
    """
    try:
        value = int(raw)
    except (TypeError, ValueError) as exc:
        raise argparse.ArgumentTypeError(
            f"token budget must be a positive integer, got {raw!r}"
        ) from exc
    if value <= 0:
        raise argparse.ArgumentTypeError(
            f"token budget must be a positive integer, got {value}"
        )
    return value


def _add_path_argument(parser: argparse.ArgumentParser) -> None:
    """Standard repo-path argument shared by all subcommands (WI-munuv).

    Each subcommand accepts both forms interchangeably:

        hypergumbo <cmd> /path/to/repo       # positional
        hypergumbo <cmd> --path /path/...    # flag

    Both default to ``None`` here; the post-process in ``main()``
    resolves to ``"."`` when neither is set, and reports an error
    when both are set explicitly. Keeping the destination split
    (``path`` for positional, ``_path_flag`` for the option) is the
    only way to register both — argparse rejects two adds with the
    same dest. The post-process reunifies them so cmd functions can
    keep reading ``args.path`` unchanged.
    """
    parser.add_argument(
        "path",
        nargs="?",
        default=None,
        help="Path to repo root (default: current directory)",
    )
    parser.add_argument(
        "--path",
        dest="_path_flag",
        default=None,
        help="Path to repo root (alternative to positional argument)",
    )


def build_parser() -> argparse.ArgumentParser:
    # Main parser with comprehensive help
    main_description = """\
Generate codebase summaries for AI assistants and coding agents.

Quick start:
  hypergumbo .              Generate Markdown sketch (~8000 tokens default)
  hypergumbo . -t 16000     Larger sketch with more detail
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
        formatter_class=GroupedSubcommandHelpFormatter,
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
  hypergumbo sketch .                   # Auto-runs analysis if needed (~8000 tokens)
  hypergumbo sketch ~/project -t 16000  # Larger sketch with more detail
  hypergumbo sketch . -t 4000 -x        # Brief overview, no tests
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
    _add_path_argument(p_sketch)
    p_sketch.add_argument(
        "--input",
        type=str,
        default=None,
        metavar="FILE",
        help="Use cached results file instead of re-analyzing (faster)",
    )
    p_sketch.add_argument(
        "-t", "--tokens",
        type=_positive_token_budget,
        default=None,
        help="Limit output to approximately N tokens (must be a positive integer)",
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
    p_sketch.add_argument(
        "--no-secret-scan",
        action="store_true",
        dest="no_secret_scan",
        help="Skip secret scanning (not recommended)",
    )
    p_sketch.add_argument(
        "--locale",
        type=str,
        default=None,
        metavar="CODE",
        help="Analyze translated docs for this locale instead of English "
             "(e.g., --locale ja-jp). By default, translated documentation "
             "directories are excluded to avoid processing duplicate content.",
    )
    p_sketch.add_argument(
        "--require-section",
        action="append",
        default=[],
        dest="require_sections",
        metavar="NAME",
        help="Require a section even under budget pressure "
             "(repeatable; e.g., --require-section 'Key Symbols'). "
             "Valid: Entry Points, Data Models, Source Files, Key Symbols, "
             "Additional Files, Source Files Content, Additional Files Content",
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
    _add_path_argument(p_run)
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
             "3=+external, 4=+derived). Default: 3 (excludes derived/minified).",
    )
    p_run.add_argument(
        "--first-party-only",
        action="store_const",
        const=1,
        dest="max_tier",
        help="Only include first-party code (shortcut for --max-tier 1)",
    )
    p_run.add_argument(
        "--include-docs",
        action="store_true",
        default=False,
        dest="include_docs",
        help="Include documentation/config/CSS/metadata nodes (markdown sections, "
             "TOML tables, CSS selectors, .gitignore patterns, npm scripts, pip "
             "requirements) in output. By default these are excluded to reduce noise.",
    )
    p_run.add_argument(
        "--max-files",
        type=int,
        default=None,
        dest="max_files",
        help="Maximum files to analyze per language (for large repos)",
    )
    p_run.add_argument(
        "--max-file-bytes",
        type=int,
        default=None,
        dest="max_file_bytes",
        help="Skip files exceeding this size in bytes (default: no limit)",
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
    p_run.add_argument(
        "--locale",
        type=str,
        default=None,
        metavar="CODE",
        help="Analyze translated docs for this locale instead of English "
             "(e.g., --locale ja-jp). By default, translated documentation "
             "directories are excluded to avoid processing duplicate content.",
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
  hypergumbo slice --files changed.txt       # Find files affected by changes

Output format:
  Default: {schema_version, view, feature: {nodes, edges, ...}}
  --inline: Same as default, but feature includes full node/edge objects
  --flat:   {nodes: [...], edges: [...]} - simple format for external tools
  --files:  List of dependent file paths (for smart test selection)

Use cases:
  - Understand what code main() depends on (forward slice)
  - Find all callers of a function (reverse slice)
  - Extract a focused subgraph for debugging or review
  - Smart test selection: find tests affected by changed files

Auto-discovers cached results from 'hypergumbo run', or specify --input."""

    p_slice = sub.add_parser(
        "slice",
        help="Extract subgraph from an entry point",
        epilog=slice_epilog,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    _add_path_argument(p_slice)
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
        default=None,
        help="Maximum traversal depth (default: unlimited, bounded by --max-files)",
    )
    p_slice.add_argument(
        "--max-files",
        type=int,
        default=100,
        help="Maximum number of files to include (default: 100)",
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
        "--exclude-utility",
        action="store_true",
        help="Exclude utility files (docs, examples, scripts) from the slice",
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
        "--hub-threshold",
        type=int,
        default=50,
        dest="hub_threshold",
        help="Prune hub nodes: nodes with more outgoing (forward) or incoming "
             "(reverse) edges than this threshold are included but not traversed. "
             "Prevents slice explosion through high-degree utility functions "
             "(default: 50). Use --hub-threshold 0 to disable.",
    )
    p_slice.add_argument(
        "--exclude-imports",
        action="store_true",
        dest="exclude_imports",
        help="Exclude import/module edges from both traversal and output. "
             "Produces a call-graph-only slice, removing file-level package "
             "dependencies that can constitute 60%%+ of edges in large codebases.",
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
    p_slice.add_argument(
        "--group-by-module",
        action="store_true",
        dest="group_by_module",
        help="Group output nodes by file/module path. Implies --inline. "
             "Adds 'modules' dict (path → nodes) and 'module_edges' summary.",
    )
    p_slice.add_argument(
        "--dataflow",
        action="store_true",
        help="Only follow edges where a write/mutate at the source connects to "
             "a read at the destination (ADR-0015). Produces tighter slices of "
             "actual data dependencies. Edges without access_mode metadata are "
             "still followed.",
    )
    p_slice.add_argument(
        "--files",
        default=None,
        metavar="FILE",
        help="File containing list of changed paths (one per line). "
             "Finds all symbols in these files and performs reverse slice to "
             "identify dependent files. Used for smart test selection.",
    )
    p_slice.add_argument(
        "--output",
        default=None,
        metavar="FILE",
        help="Output file for --files mode (list of dependent file paths). "
             "If not specified, writes to stdout.",
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
    _add_path_argument(p_search)
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
    _add_path_argument(p_routes)
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
    # WI-godos: routes excludes test-file routes by default (UAT DQ-02
    # found 14% of plausible's reported routes were from tests). Use
    # --include-tests to opt back in. The `-x` / `--exclude-tests` flag
    # is kept as a no-op alias so existing scripts don't break.
    p_routes.add_argument(
        "--include-tests",
        action="store_true",
        dest="include_tests",
        help="Include routes from test files (default: excluded)",
    )
    p_routes.add_argument(
        "-x",
        "--exclude-tests",
        action="store_true",
        dest="exclude_tests",
        help="(deprecated; excluded by default) Exclude routes from test files",
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
    _add_path_argument(p_explain)
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
        type=_positive_token_budget,
        default=None,
        dest="tokens",
        help="Token budget for source code (must be a positive integer; omits low-priority sources when exceeded)",
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

    # hypergumbo install-gitleaks
    p_gitleaks = sub.add_parser(
        "install-gitleaks",
        help="Install gitleaks for secret scanning",
    )
    p_gitleaks.add_argument(
        "--check",
        action="store_true",
        help="Check if gitleaks is installed without installing",
    )
    p_gitleaks.add_argument(
        "--quiet",
        action="store_true",
        help="Suppress output",
    )
    p_gitleaks.set_defaults(func=cmd_install_gitleaks)

    # hypergumbo uninstall-gitleaks
    p_uninstall_gitleaks = sub.add_parser(
        "uninstall-gitleaks",
        help="Uninstall gitleaks secret scanner",
    )
    p_uninstall_gitleaks.add_argument(
        "--quiet",
        action="store_true",
        help="Suppress output",
    )
    p_uninstall_gitleaks.set_defaults(func=cmd_uninstall_gitleaks)

    # hypergumbo cache-status
    p_cache_status = sub.add_parser(
        "cache-status",
        help="Show cache status and statistics",
    )
    p_cache_status.add_argument(
        "--quiet",
        action="store_true",
        help="Suppress output",
    )
    p_cache_status.set_defaults(func=cmd_cache_status)

    # hypergumbo cache-clear
    cache_clear_epilog = """\
Examples:
  hypergumbo cache-clear                  # Clear entire cache
  hypergumbo cache-clear --older-than 7   # Clear entries older than 7 days
  hypergumbo cache-clear --dry-run        # Preview what would be deleted

The cache stores analysis results and embeddings for each repository.
Clearing it forces re-analysis on next run (slower but ensures fresh results)."""

    p_cache_clear = sub.add_parser(
        "cache-clear",
        help="Clear the hypergumbo cache",
        epilog=cache_clear_epilog,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p_cache_clear.add_argument(
        "--older-than",
        type=int,
        metavar="DAYS",
        help="Only remove entries older than N days",
    )
    p_cache_clear.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be deleted without deleting",
    )
    p_cache_clear.add_argument(
        "--quiet",
        action="store_true",
        help="Suppress output",
    )
    p_cache_clear.set_defaults(func=cmd_cache_clear)

    # hypergumbo install-embeddings
    p_install_embeddings = sub.add_parser(
        "install-embeddings",
        help="Install embedding dependencies (sentence-transformers)",
    )
    p_install_embeddings.add_argument(
        "--check",
        action="store_true",
        help="Check if embeddings are installed without installing",
    )
    p_install_embeddings.add_argument(
        "--quiet",
        action="store_true",
        help="Suppress output",
    )
    p_install_embeddings.set_defaults(func=cmd_install_embeddings)

    # hypergumbo uninstall-embeddings
    p_uninstall_embeddings = sub.add_parser(
        "uninstall-embeddings",
        help="Uninstall embedding dependencies",
    )
    p_uninstall_embeddings.add_argument(
        "--all",
        action="store_true",
        help="Also remove PyTorch (~2GB)",
    )
    p_uninstall_embeddings.add_argument(
        "--quiet",
        action="store_true",
        help="Suppress output",
    )
    p_uninstall_embeddings.set_defaults(func=cmd_uninstall_embeddings)

    # hypergumbo add-extras
    p_add_extras = sub.add_parser(
        "add-extras",
        help="Install all optional extras (grammars, gitleaks, embeddings)",
    )
    p_add_extras.add_argument(
        "--quiet",
        action="store_true",
        help="Suppress output",
    )
    p_add_extras.set_defaults(func=cmd_add_extras)

    # hypergumbo remove-extras
    p_remove_extras = sub.add_parser(
        "remove-extras",
        help="Uninstall optional extras (gitleaks, embeddings)",
    )
    p_remove_extras.add_argument(
        "--quiet",
        action="store_true",
        help="Suppress output",
    )
    p_remove_extras.set_defaults(func=cmd_remove_extras)

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
    _add_path_argument(p_test_cov)
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

    # hypergumbo dead-code-maybe
    p_dead_code = sub.add_parser(
        "dead-code-maybe",
        help="Find potentially dead code unreachable from entrypoints",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    _add_path_argument(p_dead_code)
    p_dead_code.add_argument(
        "--input", default=None,
        help="Input behavior map file (default: auto-detect cached results)",
    )
    p_dead_code.add_argument(
        "--format", choices=["text", "json"], default="text",
        help="Output format (default: text)",
    )
    p_dead_code.add_argument(
        "--seeds", choices=["entrypoints", "tests", "exports", "all"],
        default="entrypoints",
        help="Seed set for reachability analysis (default: entrypoints). "
             "'exports' uses symbols with is_exported=True (public API). "
             "'all' combines entrypoints, tests, and exports.",
    )
    p_dead_code.add_argument(
        "--min-confidence", type=float, default=0.0,
        help="Minimum entrypoint confidence threshold (default: 0.0)",
    )
    p_dead_code.add_argument(
        "--exclude-annotated", action="store_true", default=False,
        help="Exclude candidates with decorators, annotations, or framework concepts "
             "(these are likely framework-registered, not linker gaps)",
    )
    p_dead_code.add_argument(
        "--exclude-exports", action="store_true", default=False,
        help="WI-zafab filter 3: exclude candidates whose is_exported=True "
             "(public API — reachable by external callers outside the analysis scope)",
    )
    p_dead_code.set_defaults(func=cmd_dead_code_maybe)

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
    _add_path_argument(p_symbols)
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

    # hypergumbo io-boundaries
    io_boundaries_epilog = """\
Examples:
  hypergumbo io-boundaries .                          # Production-only IO map
  hypergumbo io-boundaries . --include-tests          # Also include test files
  hypergumbo io-boundaries . --json                   # JSON output
  hypergumbo io-boundaries . --input hg.json          # From existing analysis
  hypergumbo io-boundaries . --by-file                # Group by file
  hypergumbo io-boundaries . --boundary subprocess    # Filter to subprocess calls
  hypergumbo io-boundaries . --primitive os.execv     # Filter to specific primitive

Identifies call edges that reach I/O primitives (filesystem, network,
subprocess, environment) and groups them by boundary type. Test files
are excluded by default — pass --include-tests to see them. See ADR-0016."""

    p_io = sub.add_parser(
        "io-boundaries",
        help="Show I/O boundary map (ADR-0016)",
        epilog=io_boundaries_epilog,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    _add_path_argument(p_io)
    p_io.add_argument(
        "--input",
        default=None,
        help="Input behavior map file (default: auto-discover or run analysis)",
    )
    p_io.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help="Output as JSON",
    )
    p_io.add_argument(
        "--by-file",
        action="store_true",
        dest="by_file",
        help="Group output by source file instead of boundary type",
    )
    p_io.add_argument(
        "--boundary",
        default=None,
        metavar="TYPE",
        help="Filter to a specific boundary type (e.g., fs_write, subprocess)",
    )
    p_io.add_argument(
        "--primitive",
        default=None,
        metavar="NAME",
        help="Filter to a specific primitive (e.g., subprocess.run, os.execv)",
    )
    # WI-sifif: production-only is now the default for io-boundaries.
    # Test files are noise for understanding production IO behavior — on
    # alertmanager, env_read reported 9 chains with 7 of them in test files.
    # `--exclude-tests` is kept as a no-op for backward compatibility with
    # users/scripts already passing it; `--include-tests` flips back to the
    # historical "show everything" behavior.
    p_io.add_argument(
        "-x",
        "--exclude-tests",
        action="store_true",
        dest="exclude_tests",
        default=True,
        help="(default behavior — kept for backward compatibility)",
    )
    p_io.add_argument(
        "--include-tests",
        action="store_false",
        dest="exclude_tests",
        help=(
            "Include I/O boundary chains originating from test files "
            "(default: production-only)"
        ),
    )
    p_io.set_defaults(func=cmd_io_boundaries)

    # hypergumbo verify-claims
    p_config = sub.add_parser(
        "config",
        help="Show per-language configuration (dataflow, IO, function summaries)",
    )
    p_config.add_argument(
        "language",
        help="Language name (e.g., go, python, java, rust)",
    )
    p_config.add_argument(
        "--format",
        choices=["json", "yaml", "text"],
        default="text",
        help="Output format (default: text)",
    )
    p_config.set_defaults(func=cmd_config)

    p_vc = sub.add_parser(
        "verify-claims",
        help="Verify security claims against I/O boundary map and taint flow",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p_vc.add_argument(
        "--claims",
        required=True,
        metavar="FILE",
        help="YAML file with security claims to verify",
    )
    _add_path_argument(p_vc)
    p_vc.add_argument(
        "--input",
        default=None,
        help="Input behavior map file (default: auto-discover or run analysis)",
    )
    p_vc.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help="Output as JSON",
    )
    p_vc.set_defaults(func=cmd_verify_claims)

    # Assign subcommands to groups for help formatting
    # Core analysis commands (group_order=0) - ordered by suborder
    core_cmds = ["sketch", "run", "slice", "search", "routes", "explain",
                 "catalog", "config", "test-coverage", "dead-code-maybe",
                 "symbols", "compact", "io-boundaries", "verify-claims"]
    for i, cmd in enumerate(core_cmds):
        _set_subparser_group(sub, cmd, "core", 0, suborder=i)

    # Extras/installation commands (group_order=1) - ordered by suborder
    extras_cmds = ["add-extras", "remove-extras", "build-grammars",
                   "install-gitleaks", "uninstall-gitleaks",
                   "install-embeddings", "uninstall-embeddings"]
    for i, cmd in enumerate(extras_cmds):
        _set_subparser_group(sub, cmd, "extras", 1, suborder=i)

    # Set custom metavar to control the order in usage line
    sub.metavar = (
        "{sketch,run,slice,search,routes,explain,catalog,test-coverage,"
        "symbols,compact,io-boundaries,add-extras,remove-extras,"
        "build-grammars,install-gitleaks,uninstall-gitleaks,"
        "install-embeddings,uninstall-embeddings}"
    )

    return p


_DEPENDENCY_KINDS = frozenset({
    "dependency", "devDependency", "dev-dependency", "build-dependency",
})


def _classify_symbols(
    symbols: list[Symbol], repo_root: Path, package_roots: set[Path]
) -> None:
    """Apply supply chain classification to symbols in-place.

    Classifies each symbol's file path and updates supply_chain_tier
    and supply_chain_reason fields.  Symbols that already have a tier
    set by a linker (e.g. npm_package with tier=3) are not reclassified
    — the linker's tier takes precedence.

    Dependency-kind symbols (from Cargo.toml, package.json, etc.) are
    classified as tier 3 (EXTERNAL_DEP) since they represent references
    to external packages, not first-party code.
    """
    for symbol in symbols:
        if symbol.supply_chain_tier != 1 or symbol.supply_chain_reason:
            continue
        # Dependency declarations are external references, not source code
        if symbol.kind in _DEPENDENCY_KINDS:
            symbol.supply_chain_tier = 3
            symbol.supply_chain_reason = "dependency declaration (external)"
            continue
        file_path = repo_root / symbol.path
        classification = classify_file(file_path, repo_root, package_roots)
        symbol.supply_chain_tier = classification.tier.value
        symbol.supply_chain_reason = classification.reason
        symbol.is_test_file = classification.is_test
        symbol.is_generated_file = classification.is_generated
        # WI-zimum: fold in modifier-derived export signal. The analyzer
        # may have already set Symbol.is_exported at extraction time
        # (WI-gipag: Python __all__, future: TS/JS export keyword);
        # this step additionally picks up modifier-based signals for
        # Go ("exported"), Rust ("pub"/"pub(...)"), and languages that
        # emit "public" via visibility_from_modifiers.
        symbol.is_exported = (
            symbol.is_exported
            or is_exported_from_modifiers(symbol.modifiers)
        )


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
    max_file_bytes: int | None = None,
    compact: bool = False,
    coverage: float = 0.8,
    connectivity: bool = True,
    budgets: str | None = None,
    extra_excludes: list[str] | None = None,
    frameworks: str | None = None,
    include_docs: bool = False,
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
        include_docs: If True, include non-code node kinds in output. Default
            False excludes documentation (section, heading, paragraph, etc.),
            config (setting, config, table), and CSS structural nodes
            (class_selector, id_selector, rule_set, property, media, keyframes,
            font_face) to reduce degree-0 noise.
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

    # Build a shared file index from a single os.walk() pass.
    # This replaces 80+ redundant rglob() calls across analyzers,
    # profile detection, and linkers — ~75% of uncached runtime.
    from hypergumbo_core.discovery import (
        DEFAULT_EXCLUDES, FileIndex, set_file_index, set_max_file_bytes,
    )
    show_progress("Indexing files", 2)
    combined_excludes = list(DEFAULT_EXCLUDES)
    if extra_excludes:
        combined_excludes.extend(extra_excludes)
    file_index = FileIndex.build(repo_root, excludes=combined_excludes)
    set_file_index(file_index)

    # Detect repo profile (languages, frameworks) with LOC counting.
    # Analyzers will read the same files shortly, so OS cache is warm.
    show_progress("Detecting profile", 5)
    profile = detect_profile(repo_root, extra_excludes=extra_excludes, frameworks=frameworks, count_loc=True)
    behavior_map["profile"] = profile.to_dict()

    # Detect internal package roots for supply chain classification
    package_roots = detect_package_roots(repo_root)

    # Set global file size limit so all analyzers using find_files()
    # automatically skip oversized files (e.g., minified JS, huge HTML).
    set_max_file_bytes(max_file_bytes)

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
        dependency_manifest,
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

    # Refine framework list using import evidence (post-analysis validation).
    # Frameworks detected from manifests are cross-referenced against actual
    # import edges to distinguish production frameworks from dev/test-only ones.
    if profile.framework_mode == "auto":
        from .profile import refine_frameworks
        profile = refine_frameworks(profile, all_edges, all_symbols)
        behavior_map["profile"] = profile.to_dict()

    # Enrich symbols with framework concept metadata (ADR-0003)
    # This applies YAML-based patterns to add concept info (route, model, etc.)
    # to symbols based on their decorators, base classes, annotations, AND
    # usage contexts (v1.1.x) for call-based frameworks like Django URLs.
    show_progress("Enriching symbols", 50)
    detected_frameworks = set(profile.frameworks)
    enrich_symbols(all_symbols, detected_frameworks, all_usage_contexts)

    # Materialize route symbols from enriched concept metadata (WI-lodik).
    # Annotation-based frameworks (JAX-RS, Spring MVC, ASP.NET) tag handler
    # methods with concept=route but don't create kind="route" IR nodes.
    # This step creates those nodes so the route_handler linker can produce
    # routes_to edges.
    from .framework_patterns import materialize_route_symbols
    materialized_routes = materialize_route_symbols(all_symbols)
    if materialized_routes:
        all_symbols.extend(materialized_routes)

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

    # Normalize linker-produced symbol/usage-context paths (same as analyzers
    # in run_all_analyzers — linkers can also produce absolute paths).
    from .paths import normalize_path as _norm_path

    _root_prefix = _norm_path(str(repo_root)).rstrip("/") + "/"
    for sym in all_symbols:
        normed = _norm_path(sym.path)
        if normed.startswith(_root_prefix):
            sym.path = normed[len(_root_prefix):]
    for uc in all_usage_contexts:
        normed = _norm_path(uc.path)
        if normed.startswith(_root_prefix):
            uc.path = normed[len(_root_prefix):]  # pragma: no cover

    # Check for partial installation issues (ADR-0010 Item 8)
    # Emit warnings for: unanalyzed files, partial linker requirements
    check_partial_install_warnings(profile, linker_ctx, emit_warnings=True)

    del linker_ctx, captured_symbols  # Free linker data structures

    # Deduplicate edges by edge_key (src + dst + type, ignoring line) and
    # remove self-loops (src == dst) which inflate centrality without adding
    # useful connectivity. Common sources: visitor patterns, name collisions.
    # Using edge_key instead of edge.id catches duplicate relationships at
    # different call sites (e.g., 24 calls from InitialSchema#up to
    # Provisioner#create_table in postal).
    all_edges = deduplicate_edges(all_edges, remove_self_loops=True)
    _log_memory("after linkers")

    # Create boundary nodes for dangling edge endpoints (WI-sikur / INV-miniz).
    # Edges to external functions (stdlib, npm packages, etc.) would otherwise
    # break slice traversal by pointing to nonexistent nodes.
    boundary = create_boundary_nodes(
        all_symbols, all_edges, dependency_manifest=dependency_manifest,
    )
    if boundary:
        all_symbols.extend(boundary)
    _log_memory("after boundary nodes")

    # Apply supply chain classification to all symbols
    show_progress("Classifying symbols", 60)
    _classify_symbols(all_symbols, repo_root, package_roots)

    # Promote route-bearing symbols from derived (tier 4) to internal (tier 2).
    # Routes represent the API surface and are valuable regardless of whether
    # the code is generated (e.g., go-swagger, protobuf gRPC stubs).
    for s in all_symbols:
        if s.supply_chain_tier == 4:
            is_route = s.kind == "route"
            if not is_route:
                for concept in (s.meta or {}).get("concepts", []):
                    if isinstance(concept, dict) and concept.get("concept") == "route":
                        is_route = True
                        break
            if is_route:
                s.supply_chain_tier = 2
                s.supply_chain_reason = "route promoted from derived"

    # Apply tier filtering: always exclude DERIVED (tier 4) unless --max-tier 4.
    # DERIVED files are minified/bundled/generated artifacts whose symbols distort
    # centrality rankings and inflate edge counts via false-positive name collisions.
    effective_tier = max_tier if max_tier is not None else 3
    if effective_tier < 4:
        # Filter symbols by tier
        filtered_symbols = [
            s for s in all_symbols if s.supply_chain_tier <= effective_tier
        ]
        filtered_symbol_ids = {s.id for s in filtered_symbols}

        # Filter edges: src must be in filtered symbols (or file-level ref),
        # AND dst must not reference a node that was explicitly removed by
        # tier filtering. Edges whose dst is an unresolved external reference
        # (never in the node set) are kept — they represent real dependencies.
        # Exclude boundary nodes from "removed" set — they're synthetic
        # endpoints for external references and should be treated as if they
        # don't exist for tier filtering purposes (same as pre-boundary-node
        # behavior where unresolved IDs simply weren't in the symbol set).
        removed_symbol_ids = {
            s.id for s in all_symbols
            if s.id not in filtered_symbol_ids
            and not (s.meta and s.meta.get("external_boundary"))
        }

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

        filtered_edges = [
            e
            for e in all_edges
            if _is_valid_edge_src(e.src) and e.dst not in removed_symbol_ids
        ]

        all_symbols = filtered_symbols
        all_edges = filtered_edges
        limits.max_tier_applied = effective_tier

    # Exclude non-code node kinds by default.  Documentation/config nodes
    # (markdown sections, TOML tables, INI settings), CSS structural nodes
    # (selectors, properties, media queries), and config-metadata nodes
    # (.gitignore patterns, npm scripts) are typically degree-0 and add
    # noise without architectural insight.
    if not include_docs:
        _NOISE_KINDS = frozenset({
            # Documentation / config
            "section", "table", "table_array", "code_block",
            "link", "paragraph", "label", "heading",
            "setting", "config",
            # CSS structural (degree-0 in behavior maps)
            "class_selector", "id_selector", "rule_set",
            "property", "media", "keyframes", "font_face",
            "variable",     # CSS custom properties / SCSS variables (zero edges)
            # Config metadata (degree-0 across all tested repos)
            "pattern",      # .gitignore entries
            "script",       # npm scripts / pyproject.toml entry points
            "requirement",  # pip requirements.txt entries
        })
        noise_ids = {s.id for s in all_symbols if s.kind in _NOISE_KINDS}
        all_symbols = [s for s in all_symbols if s.kind not in _NOISE_KINDS]
        all_edges = [
            e for e in all_edges
            if e.src not in noise_ids and e.dst not in noise_ids
        ]

    # Rank symbols by importance (centrality + tier weighting) for output ordering
    show_progress("Ranking symbols", 65)
    ranked = rank_symbols(
        all_symbols, all_edges,
        first_party_priority=True,
        min_edge_confidence=0.5,
    )
    ranked_symbols = [r.symbol for r in ranked]
    del ranked  # Free RankedSymbol wrappers

    # Filter boundary nodes from output.  They exist in `all_symbols` to make
    # edge endpoints resolvable for slice traversal, but shouldn't appear in
    # the behavior map output — they're synthetic, have no source code, and
    # would inflate node counts.  Edges pointing to boundary node IDs are
    # retained (consumers can detect them by the "<external>" path or
    # external_boundary meta flag).
    ranked_symbols = [
        s for s in ranked_symbols
        if not (s.meta and s.meta.get("external_boundary"))
    ]

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

        if file_index is not None:
            _all_repo_files = file_index.all_files()
        else:  # pragma: no cover - file_index always set in run_behavior_map
            _all_repo_files = [f for f in repo_root.rglob("*") if f.is_file()]
        for f in _all_repo_files:
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
                    json.dump(tiered_map, f, indent=2, sort_keys=True)
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
        json.dump(behavior_map, f, indent=2, sort_keys=True)
    generated_files.append(out_path)
    _log_memory("after write")

    # Clear global file index to release memory
    set_file_index(None)

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

    # Group labels for display
    group_labels = {
        "core": "CORE ANALYSIS COMMANDS",
        "extras": "INSTALLATION & MAINTENANCE COMMANDS",
    }

    # Print help for each subcommand, ordered by group
    for name, subparser, group, is_new_group in _get_subparsers_by_group(
        subparsers_action
    ):
        # Print group header when entering a new group
        if is_new_group:
            label = group_labels.get(group, group.upper())
            print(f"\n{'=' * 78}")
            print(f"  {label}")
            print("=" * 78)

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

    subcommands = {"run", "slice", "search", "routes", "explain", "catalog", "config", "sketch", "build-grammars", "install-gitleaks", "uninstall-gitleaks", "cache-status", "cache-clear", "install-embeddings", "uninstall-embeddings", "add-extras", "remove-extras", "test-coverage", "dead-code-maybe", "symbols", "compact", "io-boundaries", "verify-claims"}

    # WI-balij (UAT UX-04): accept --debug in any position. Strip it here so
    # `hypergumbo sketch . --debug` and `hypergumbo --debug sketch .` both
    # work — argparse otherwise rejects --debug after the subcommand because
    # it's only registered on the root parser.
    debug_flag = False
    if "--debug" in argv:
        debug_flag = True
        argv = [a for a in argv if a != "--debug"]

    # WI-balij (UAT UX-03): if the first positional doesn't name a known
    # subcommand AND clearly isn't a path (no path separators, no leading
    # dot/tilde, doesn't exist on disk), the user probably mistyped a
    # subcommand. Surface that with a Did-you-mean suggestion instead of
    # silently inserting "sketch" and reporting "path does not exist".
    if argv and argv[0] not in subcommands and not argv[0].startswith("-"):
        candidate = argv[0]
        looks_like_subcommand_attempt = (
            "/" not in candidate
            and "\\" not in candidate
            and not candidate.startswith(".")
            and not candidate.startswith("~")
            and not Path(candidate).exists()
        )
        if looks_like_subcommand_attempt:
            import difflib
            close = difflib.get_close_matches(
                candidate, sorted(subcommands), n=3, cutoff=0.5,
            )
            print(
                f"hypergumbo: error: '{candidate}' is not a valid subcommand.",
                file=sys.stderr,
            )
            if close:
                print(
                    f"  Did you mean: {', '.join(close)}?",
                    file=sys.stderr,
                )
            print(
                "  Run 'hypergumbo --help' to see the full list.",
                file=sys.stderr,
            )
            return 2

    # If no args, or first arg is not a subcommand (and not a flag), use sketch mode
    if not argv or (argv[0] not in subcommands and not argv[0].startswith("-")):
        argv = ["sketch"] + list(argv)

    args = parser.parse_args(argv)

    # WI-munuv: unify the positional `path` and the `--path` flag.
    # Subcommands that called _add_path_argument accept both forms;
    # this collapses them into a single args.path so cmd functions
    # don't need to know which form was used. Setting both is a user
    # error (ambiguous intent).
    if hasattr(args, "_path_flag"):
        pos = getattr(args, "path", None)
        flag = args._path_flag
        if pos is not None and flag is not None:
            print(
                "hypergumbo: error: provide either positional <path> "
                "OR --path, not both",
                file=sys.stderr,
            )
            return 2
        args.path = pos if pos is not None else (
            flag if flag is not None else "."
        )

    # Configure logging if --debug is set (in any position)
    if debug_flag or getattr(args, "debug", False):
        logging.basicConfig(
            level=logging.DEBUG,
            format="[%(name)s] %(levelname)s: %(message)s",
            stream=sys.stderr,
        )

    if not hasattr(args, "func"):  # pragma: no cover
        parser.print_help()  # pragma: no cover
        return 1  # pragma: no cover

    return args.func(args)

