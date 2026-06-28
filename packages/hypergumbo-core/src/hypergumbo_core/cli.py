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
- **build-grammars**: Build Lean/Wolfram/Circom tree-sitter grammars from source
- **install-gitleaks**: Install gitleaks for secret scanning
- **install-rust-analyzer**: Install rust-analyzer via rustup for the SCIP-backed Rust analyzer (WI-dotud)

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
import shutil
import subprocess  # nosec B404 - subprocess needed for pip commands
import sys
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Set

from rich.console import Console
from rich.table import Table

from . import __version__
from .analyze.all_analyzers import run_all_analyzers
from .analyze.base import (
    dedup_logical_synthetic_identities,
    is_exported_from_modifiers,
    populate_synthetic_class_b_identity,
    split_within_file_stable_id_collisions,
    widen_route_stable_ids,
)
from .behavior_map_io import load_behavior_map
from .catalog import get_default_catalog, is_available, suggest_passes_for_languages
# ADR-0043 §6: finalize() is the single pre-serialization reconcile point. _relativize_ir_paths
# lives there now (finalize sub-step 1 owns it); re-exported here for the Phase B call below
# and for tests/test_cli_relativize_paths.py (which imports it from cli).
from .finalize import FinalizeContext, _relativize_ir_paths, finalize
from .linkers.registry import LinkerContext, run_all_linkers
from .pass_metadata import build_pass_metadata
from .safety_zones import (
    cache_rmtree,
    cache_write,
    user_out_open_json_dump,
    user_out_write,
)
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
import hypergumbo_core.linkers.inherited_calls as _inherited_calls_linker  # noqa: F401
import hypergumbo_core.linkers.js_module as _js_module_linker  # noqa: F401
import hypergumbo_core.linkers.orm as _orm_linker  # noqa: F401
import hypergumbo_core.linkers.pyffi as _pyffi_linker  # noqa: F401
import hypergumbo_core.linkers.ruby_ffi as _ruby_ffi_linker  # noqa: F401
import hypergumbo_core.linkers.type_hierarchy as _type_hierarchy_linker  # noqa: F401
import hypergumbo_core.linkers.vue_component as _vue_component_linker  # noqa: F401
import hypergumbo_core.linkers.view_template as _view_template_linker  # noqa: F401
import hypergumbo_core.linkers.view_template_django as _view_template_django_linker  # noqa: F401
import hypergumbo_core.linkers.view_template_phoenix as _view_template_phoenix_linker  # noqa: F401
import hypergumbo_core.linkers.view_template_spring as _view_template_spring_linker  # noqa: F401
import hypergumbo_core.linkers.view_template_laravel as _view_template_laravel_linker  # noqa: F401
import hypergumbo_core.linkers.vue_template_method as _vue_template_method_linker  # noqa: F401
import hypergumbo_core.linkers.build_target as _build_target_linker  # noqa: F401
import hypergumbo_core.linkers.decorator_dispatch as _decorator_dispatch_linker  # noqa: F401
import hypergumbo_core.linkers.method_call_recovery as _method_call_recovery_linker  # noqa: F401
import hypergumbo_core.linkers.middleware_chain as _middleware_chain_linker  # noqa: F401
import hypergumbo_core.linkers.controller_routes as _controller_routes_linker  # noqa: F401
import hypergumbo_core.linkers.router_routes as _router_routes_linker  # noqa: F401
import hypergumbo_core.linkers.react_component as _react_component_linker  # noqa: F401
import hypergumbo_core.linkers.tauri_ipc as _tauri_ipc_linker  # noqa: F401
import hypergumbo_core.linkers.solidity_abi as _solidity_abi_linker  # noqa: F401
import hypergumbo_core.linkers.wasm_bindgen as _wasm_bindgen_linker  # noqa: F401
import hypergumbo_core.linkers.yjs_crdt as _yjs_crdt_linker  # noqa: F401
import hypergumbo_core.linkers.annotation_convention as _annotation_convention_linker  # noqa: F401
import hypergumbo_core.linkers.crypto_flow as _crypto_flow_linker  # noqa: F401
import hypergumbo_core.linkers.message_dispatch as _message_dispatch_linker  # noqa: F401
import hypergumbo_core.linkers.airflow_framework_dispatch as _airflow_framework_dispatch_linker  # noqa: F401
import hypergumbo_core.linkers.jackson_dispatch as _jackson_dispatch_linker  # noqa: F401
import hypergumbo_core.linkers.kafka_streams_dispatch as _kafka_streams_dispatch_linker  # noqa: F401
import hypergumbo_core.linkers.django_orm_dispatch as _django_orm_dispatch_linker  # noqa: F401
import hypergumbo_core.linkers._third_party_bases as _third_party_bases_linker  # noqa: F401
import hypergumbo_core.linkers.rust_trait_dispatch as _rust_trait_dispatch_linker  # noqa: F401
from .entrypoints import EntrypointKind, detect_entrypoints
from .ir import (
    AnalysisRun, PASS_VERSION,
    Symbol, Edge, apply_external_id_remap, compute_config_fingerprint,
    create_boundary_nodes,
    deduplicate_edges,
    is_external_boundary,
)
from .metrics import compute_metrics
from .profile import detect_profile
from .schema import new_behavior_map
from .sketch import generate_sketch, ConfigExtractionMode, SketchStats, display_representativeness_table
from .slice import SliceQuery, slice_graph, AmbiguousEntryError, rank_slice_nodes
from .selection.filters import is_excluded_kind
from .limits import Limits
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
from .rust_analyzer_install import (
    install_rust_analyzer,
    is_rust_analyzer_available,
    is_rust_analyzer_integration_installed,
    uninstall_rust_analyzer,
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
    1. Cache directory: ~/.cache/hypergumbo/<fingerprint>/results/<state>/<analyzer_identity>/
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

    seen: set[Path] = set()
    deduped: list[Path] = []
    if artifacts:
        for artifact_path in artifacts:
            resolved = artifact_path.resolve()
            if resolved in seen:
                continue
            seen.add(resolved)
            deduped.append(artifact_path)
            if artifact_path in cached_set:
                cached_count += 1
            else:
                generated_count += 1

    # Build summary line
    parts = []
    if generated_count > 0:
        parts.append(f"Generated {generated_count} artifact(s)")
    if cached_count > 0:
        parts.append(f"Using {cached_count} cached")
    if not parts:
        parts.append("Generated 0 artifact(s)")

    print(f"\n[hypergumbo {command}] {', '.join(parts)}", file=file)

    for artifact_path in deduped:
        prefix = "[cached] " if artifact_path in cached_set else ""
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


def _reject_unknown_choice(
    value: str,
    valid: "frozenset[str]",
    *,
    subcommand: str,
    noun: str,
) -> int | None:
    """Reject a CLI filter value not in an enumerable set (INV-fabov family).

    Returns ``None`` when ``value`` is a member of ``valid``; otherwise prints
    a clear error plus a difflib did-you-mean suggestion to stderr and returns
    exit code 2. This is the shared embodiment of the INV-fabov fix-class
    (silent acceptance of invalid filter values) — it mirrors the inline
    ``config <language>`` validation (INV-gufod) so every "unknown <noun>"
    rejection reads identically.
    """
    if value in valid:
        return None
    import difflib
    close = difflib.get_close_matches(value, sorted(valid), n=3, cutoff=0.5)
    print(
        f"hypergumbo {subcommand}: error: '{value}' is not a known {noun}.",
        file=sys.stderr,
    )
    if close:
        print(f"  Did you mean: {', '.join(close)}?", file=sys.stderr)
    return 2


def _validate_require_sections(require_sections: "list[str] | None") -> int | None:
    """Validate ``sketch --require-section`` names (WI-furop / INV-fabov).

    Each value must name a real sketch section; a typo (``Key Sympols``) or a
    wrong case (``key symbols``) otherwise silently buys no budget guarantee.
    Returns ``None`` when every name is valid (or the list is empty/None);
    otherwise returns exit code 2 after printing the first offender's error.
    """
    from .sketch import VALID_SKETCH_SECTIONS

    for section in require_sections or []:
        rc = _reject_unknown_choice(
            section, VALID_SKETCH_SECTIONS, subcommand="sketch", noun="section"
        )
        if rc is not None:
            return rc
    return None


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

    # WI-furop (INV-fabov): reject unknown --require-section names before any
    # analysis, so a typo / wrong case errors loudly instead of silently
    # buying no budget guarantee.
    rc = _validate_require_sections(getattr(args, "require_sections", []))
    if rc is not None:
        return rc

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
        cached_results = load_behavior_map(input_file)

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

    # WI-jupar: one-shot drain of the legacy
    # /tmp/hypergumbo_sketch_compare/ directory. Prior releases wrote
    # the comparison sketches there with no cleanup and shared-across-
    # repos filenames; the path is unambiguously ours so it's safe to
    # remove on first run, releasing the accumulated backlog.
    import shutil
    import tempfile as _tempfile
    _legacy_compare_dir = (
        Path(_tempfile.gettempdir()) / "hypergumbo_sketch_compare"
    )
    if _legacy_compare_dir.exists():
        shutil.rmtree(_legacy_compare_dir, ignore_errors=True)

    # Generate 4x and 16x budget sketches for comparison table
    # Using 4x/16x (instead of 2x) reveals when large files start fitting.
    # WI-fufop: --no-comparison-sketches opts out (batch/scripted single-budget
    # runs don't want the 3x generation cost or the representativeness table).
    no_comparison_sketches = getattr(args, "no_comparison_sketches", False)
    if max_tokens and stats is not None and not no_comparison_sketches:
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

        # WI-jupar: comparison sketches now live in cache_dir alongside
        # the main sketch. No /tmp staging — that path was shared across
        # repos (filename-by-budget collision) and never cleaned up. The
        # cache_dir is per-(repo, state, analyzer-identity), so each
        # repo's comparison sketches stay isolated and ride normal cache
        # lifecycle (cache-status / cache-clear / INV-padum honk).
        if cache_dir is not None:
            cache_4x = cache_dir / sketch_4x_filename
            cache_16x = cache_dir / sketch_16x_filename
            cache_write(cache_4x, sketch_4x)
            cache_write(cache_16x, sketch_16x)
            print(
                f"\nhypergumbo also cached comparison sketches:\n"
                f"  4x budget ({budget_4x:,}t):  {cache_4x}\n"
                f"  16x budget ({budget_16x:,}t): {cache_16x}\n",
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
            cache_write(sketch_cache_path, sketch)
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
            # WI-panih: cache_dir is
            # `<fingerprint>/results/<state_hash>/<analyzer_identity>`,
            # so the walk back to `<fingerprint>` is three parents
            # (was two before the analyzer-identity segment landed).
            fingerprint_dir = cache_dir.parent.parent.parent
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
    enable_handler_slices = not getattr(args, "no_handler_slices", False)
    max_handler_slices = getattr(
        args, "max_handler_slices", _DEFAULT_MAX_HANDLER_SLICES
    )
    gzip_output = getattr(args, "gzip_output", False)

    if gzip_output and out_path is not None and not str(out_path).endswith(".gz"):
        out_path = Path(str(out_path) + ".gz")
        print(
            f"Note: --gzip active, writing to {out_path} "
            f"(appended .gz extension)",
            file=sys.stderr,
        )
    no_sketch_fan_out = getattr(args, "no_sketch_fan_out", False)

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
        enable_handler_slices=enable_handler_slices,
        max_handler_slices=max_handler_slices,
        gzip_output=gzip_output,
        no_sketch_fan_out=no_sketch_fan_out,
    )

    # Output summary (always at the end)
    _print_output_summary("run", artifacts=generated_files)

    # INV-padum: surface cache footprint after every run. The cache just
    # grew (a new state-hash entry was written), so this is when the user
    # is most likely to be in a position to act on the honk.
    _maybe_honk_cache(_get_cache_base())

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
        user_out_write(output_path, output_text)
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

    behavior_map = load_behavior_map(input_path)

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
    user_out_open_json_dump(out_path, output)

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
    all_artifacts = (generated_files + [input_path, out_path]) if not was_cached else [input_path, out_path]
    _print_output_summary("slice", artifacts=all_artifacts, cached_artifacts=cached_set)

    return 0


def cmd_search(args: argparse.Namespace) -> int:
    """Search for symbols by name pattern."""
    repo_root = Path(args.path).resolve()

    # WI-furop (INV-fabov): reject invalid --language / --kind filter values
    # up front -- before the (potentially auto-running) analysis -- so a typo
    # or non-language/non-kind errors loudly (exit 2) instead of silently
    # returning "No symbols found" (exit 0), which is indistinguishable from a
    # real empty result.
    if args.language:
        from .catalog import all_known_languages
        rc = _reject_unknown_choice(
            args.language, all_known_languages(),
            subcommand="search", noun="language",
        )
        if rc is not None:
            return rc
    if args.kind:
        from .symbol_kinds import all_symbol_kind_names
        rc = _reject_unknown_choice(
            args.kind, all_symbol_kind_names(),
            subcommand="search", noun="symbol kind",
        )
        if rc is not None:
            return rc

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
    behavior_map = load_behavior_map(input_path)
    nodes = behavior_map.get("nodes", [])

    # Search pattern (case-insensitive substring match)
    pattern = args.pattern.lower()
    matches = []

    for node in nodes:
        # Skip synthetic boundary nodes — they have no source location
        # and would surface as confusing "<external>" rows. Users searching
        # for `urlopen` want their fetch() call site, not the placeholder.
        if is_external_boundary(node):
            continue
        name = node.get("name", "")
        # Check if pattern matches name (fuzzy substring match)
        if pattern in name.lower():
            # Apply filters
            if args.kind and node.get("kind") != args.kind:
                continue
            if args.language and node.get("language") != args.language:
                continue
            matches.append(node)

    # Apply limit. INV-toniv: report the TOTAL match count, not the
    # post-truncation count (the header was reading len(matches) AFTER the
    # slice, so it always showed min(total, limit) and hid the real total).
    # The argparse type factory rejects limit < 1; the bool() guard keeps a
    # caller-supplied None/0 (e.g. tests) meaning "no limit".
    total = len(matches)
    truncated = bool(args.limit) and total > args.limit
    if truncated:
        matches = matches[: args.limit]

    # Output results
    if not matches:
        print(f"No symbols found matching '{args.pattern}'")
        return 0

    if truncated:
        print(
            f"Found {total} symbol(s) matching '{args.pattern}' "
            f"(showing {args.limit}):\n"
        )
    else:
        print(f"Found {total} symbol(s) matching '{args.pattern}':\n")
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
    artifacts = (generated_files + [input_path]) if not was_cached else [input_path]
    _print_output_summary(
        "search",
        artifacts=artifacts,
        stdout_output=True,
        cached_artifacts=cached_set,
    )
    return 0


# HTTP methods that indicate API routes
HTTP_METHODS = {"get", "post", "put", "patch", "delete", "head", "options"}


# Endpoint-shaped node kinds that aren't HTTP routes but live in the same
# "entrypoint to the codebase" mental space. Used by ``cmd_routes`` to hint
# at related kinds when the routes list is empty — without this, a repo with
# 28 websocket_endpoint and 63 mq_subscriber nodes would print "No API routes
# found" with no signal that any endpoints were detected at all. WI-tidip.
_RELATED_ENDPOINT_KINDS: tuple[str, ...] = (
    "websocket_endpoint",
    "graphql_resolver",
    "db_query",
    "event_publisher",
    "event_subscriber",
    "mq_publisher",
    "mq_subscriber",
    "http_client",
    "subprocess_call",
)


def _count_related_endpoint_kinds(
    nodes: list[dict],
) -> list[tuple[str, int]]:
    """Count nodes by kind for the endpoint-shaped fallback hint.

    Returns a list of ``(kind, count)`` tuples in the canonical order
    declared in ``_RELATED_ENDPOINT_KINDS``, omitting kinds with zero
    matches. Returns an empty list when no related nodes are present so
    callers can leave the existing single-line message unchanged.

    Post-fold lookup (ADR-0027 Phase 3 / audit-findings 0013): Cluster D
    framework-role values (``mq_publisher``, ``websocket_endpoint``,
    ``event_publisher``, ``event_subscriber``, ``http_client``) now ride
    on ``kind="function"`` + ``meta["framework_role"]=<value>``. The
    matching here checks ``meta.framework_role`` first, falling back to
    ``kind`` so legacy unfolded values (``graphql_resolver``,
    ``db_query``, ``subprocess_call``) still match.
    """
    counts: dict[str, int] = dict.fromkeys(_RELATED_ENDPOINT_KINDS, 0)
    for node in nodes:
        framework_role = (node.get("meta") or {}).get("framework_role")
        kind = node.get("kind")
        bucket = framework_role if framework_role in counts else kind
        if bucket in counts:
            counts[bucket] += 1
    return [(k, c) for k in _RELATED_ENDPOINT_KINDS if (c := counts[k]) > 0]


def _route_json_record(route: dict) -> dict:
    """Build a structured route record for ``routes --format json`` (INV-jutuj).

    Mirrors the field-extraction the text renderer does (kind="route" symbols
    carry authoritative ``meta.route_path``/``http_method``; concept-enriched
    symbols carry them under ``meta.concepts[].path``/``method``), so the JSON
    and text views agree on what each route is.
    """
    meta = route.get("meta") or {}
    route_path = None
    method = None
    controller_action = None
    if meta.get("framework_role") == "route":
        route_path = meta.get("route_path")
        method = meta.get("http_method")
    if route_path is None:
        for concept in meta.get("concepts", []) or []:
            if isinstance(concept, dict) and concept.get("concept") == "route":
                route_path = concept.get("path")
                method = concept.get("method")
                controller_action = concept.get("controller_action")
                break
    if controller_action is None:
        controller_action = meta.get("controller_action")
    return {
        "id": route.get("id", ""),
        "name": route.get("name", ""),
        "path": route.get("path", ""),
        "language": route.get("language"),
        "span": route.get("span", {}),
        "method": (method or "").upper(),
        "route_path": route_path,
        "controller_action": controller_action,
    }


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
    behavior_map = load_behavior_map(input_path)
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
        if not is_route and (node.get("meta") or {}).get("framework_role") == "route":
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
        if (node.get("meta") or {}).get("framework_role") == "route":
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

    # INV-jutuj: JSON output (parity with test-coverage / dead-code-maybe).
    # Handles empty and non-empty uniformly; the run summary goes to stderr so
    # stdout stays pure JSON.
    if getattr(args, "format", "text") == "json":
        output = {
            "schema_version": "0.1.0",
            "view": "routes",
            "routes": [_route_json_record(r) for r in routes],
        }
        print(json.dumps(output, indent=2))
        cached_set = {input_path} if was_cached else set()
        artifacts = (generated_files + [input_path]) if not was_cached else [input_path]
        _print_output_summary(
            "routes", artifacts=artifacts, stdout_output=True,
            file=sys.stderr, cached_artifacts=cached_set,
        )
        return 0

    if not routes:
        print("No API routes found in the behavior map.")
        related_counts = _count_related_endpoint_kinds(nodes)
        if related_counts:
            print()
            print("The behavior map contains other endpoint-shaped symbols:")
            for kind, count in related_counts:
                print(f"  - {count} {kind} node(s)")
            print()
            print(
                "To inspect them, view the JSON output (`hypergumbo run`) "
                "or use `hypergumbo explain <name>`."
            )
        cached_set = {input_path} if was_cached else set()
        artifacts = (generated_files + [input_path]) if not was_cached else [input_path]
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

    def _route_sort_key(route: dict) -> tuple:
        # WI-jajas: stable within-file order. Sort by (start_line, method,
        # route_path) so the same logical routes render identically across
        # full vs compact behavior maps (compact reorders nodes by
        # centrality, which previously leaked into the routes display).
        span = route.get("span", {}) or {}
        meta = route.get("meta", {}) or {}
        method = (meta.get("http_method") or "") if meta.get("framework_role") == "route" else ""
        route_path = meta.get("route_path") or ""
        if not method:
            for concept in meta.get("concepts", []) or []:
                if isinstance(concept, dict) and concept.get("concept") == "route":
                    method = concept.get("method") or method
                    route_path = route_path or (concept.get("path") or "")
                    break
        return (span.get("start_line", 0), (method or "").upper(), route_path)

    for file_path in sorted(routes_by_path.keys()):
        file_routes = sorted(routes_by_path[file_path], key=_route_sort_key)
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
            if (route.get("meta") or {}).get("framework_role") == "route":
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
    artifacts = (generated_files + [input_path]) if not was_cached else [input_path]
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


# INV-rarol: explain sections partition by edge TYPE, not just direction, so a
# section's label matches the edge semantics of its entries (a "Called by"
# count must mean callers, not callers+containers+instantiators summed). Maps
# edge_type -> (incoming label, outgoing label). Unmapped types fall back to a
# direction-qualified canonical-name label.
_EXPLAIN_EDGE_LABELS: Dict[str, tuple] = {
    "calls": ("Called by", "Calls"),
    "contains": ("Contained by", "Contains"),
    "instantiates": ("Instantiated by", "Instantiates"),
    "references": ("Referenced by", "References"),
    "module_attr_ref": ("Attr-referenced by", "Attr-references"),
    "extends": ("Extended by", "Extends"),
    "implements": ("Implemented by", "Implements"),
    "inherits": ("Inherited by", "Inherits"),
    "overrides": ("Overridden by", "Overrides"),
    "imports": ("Imported by", "Imports"),
    "decorated_by": ("Decorated by", "Decorates"),
    "dispatches_to": ("Dispatched-to by", "Dispatches to"),
    "uses": ("Used by", "Uses"),
}


def _render_explain_edge_sections(
    items: list,
    direction: str,
    default_label: str,
    show_provenance: bool,
    nodes_by_id: Dict[str, Dict[str, Any]],
) -> None:
    """Print explain edge sections grouped by edge type (INV-rarol).

    ``items`` are the caller/callee tuples
    ``(in_degree, name, path, line, id, node, edge_type, edge_dict)``.
    ``direction`` is ``"in"`` (incoming) or ``"out"`` (outgoing). Each section
    header names the actual relationship (``Called by`` counts only ``calls``;
    ``Instantiated by`` lists ``instantiates``), so the count matches the
    entries' semantics instead of summing mixed edge types under one direction
    label. Types are rendered in a stable (alphabetical) order.
    """
    if not items:
        print(f"  {default_label}: (none)")
        return
    by_type: Dict[str, list] = {}
    for it in items:
        by_type.setdefault(it[6] or "", []).append(it)
    for etype in sorted(by_type):
        group = by_type[etype]
        inc_label, out_label = _EXPLAIN_EDGE_LABELS.get(
            etype, (f"Incoming '{etype}'", f"Outgoing '{etype}'")
        )
        label = inc_label if direction == "in" else out_label
        print(f"  {label} ({len(group)}):")
        for item in group:
            print(f"    - {item[1]} ({item[2]}:{item[3]})")
            if show_provenance:
                _print_edge_provenance(item[7], nodes_by_id)


def _print_edge_provenance(
    edge_dict: Dict[str, Any],
    nodes_by_id: Dict[str, Dict[str, Any]],
) -> None:
    """Print derivation chain details for an edge (--provenance mode)."""
    derived_from = edge_dict.get("derived_from")
    if not derived_from:
        # INV-rarol: --provenance must have a VISIBLE effect even on edges with
        # no derivation chain (previously this returned silently, making
        # `explain --provenance` byte-identical to the no-flag form for the
        # analyzer-produced edges that dominate most symbols). derived_from is
        # recorded only on linker-inferred edges; say so. Extending it to every
        # edge is the deferred structural half (declared-fields:F5).
        print("      (no derivation chain — analyzer-produced edge)")
        return
    resolved = []
    for sym_id in derived_from:
        node = nodes_by_id.get(sym_id)
        if node:
            resolved.append(f"{node.get('name', sym_id)} ({node.get('kind', '?')})")
        else:
            resolved.append(sym_id)
    print(f"      Derived from: {', '.join(resolved)}")


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
    behavior_map = load_behavior_map(input_path)
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
    show_provenance = getattr(args, "provenance", False)

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

        # Show origin passes (PROV wasAttributedTo)
        node_origin = node.get("origin")
        if node_origin:
            if isinstance(node_origin, list):
                print(f"  Origin: {', '.join(node_origin)}")
            else:
                print(f"  Origin: {node_origin}")

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

        # WI-dubum: defer all source dumps until after the call-graph
        # summaries print so 'Called by' / 'Calls' appear at the top of
        # the output rather than after hundreds of source lines. The
        # queried-symbol source is precomputed here and printed below
        # the two summaries.
        queried_symbol_source: Optional[str] = None
        queried_symbol_source_tokens = 0
        if with_source:
            queried_symbol_source = _extract_source_lines(
                repo_root, path, start_line, end_line
            )
            if queried_symbol_source:
                queried_symbol_source_tokens = _estimate_tokens(queried_symbol_source)
                # Queried symbol is always shown; reserve its tokens.
                tokens_used += queried_symbol_source_tokens
                sources_shown.add(symbol_id)

        # Find callers (edges where dst = this symbol)
        # Tuple: (in_degree, name, path, line, src_id, src_node, edge_type, edge_dict)
        callers: list[tuple[int, str, str, int, str, Optional[Dict[str, Any]], str, Dict[str, Any]]] = []
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
                edge_type = edge.get("type", "")
                callers.append((src_in_degree, src_name, src_path, src_line, src_id, src_node, edge_type, edge))

        # Sort callers by in-degree (descending), then by name for stability
        callers.sort(key=lambda x: (-x[0], x[1]))

        # Find callees (edges where src = this symbol)
        # Tuple: (in_degree, name, path, line, dst_id, dst_node, edge_type, edge_dict)
        callees: list[tuple[int, str, str, int, str, Optional[Dict[str, Any]], str, Dict[str, Any]]] = []
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
                edge_type = edge.get("type", "")
                callees.append((dst_in_degree, dst_name, dst_path, edge_line, dst_id, dst_node, edge_type, edge))

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
            for caller_in_degree, caller_name, caller_path, caller_line, caller_id, caller_node, _et, _ed in callers:
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
            for callee_in_degree, callee_name, callee_path, callee_line, callee_id, callee_node, _et, _ed in callees:
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

        # WI-dubum: print both summaries before any source dumps so the
        # call-graph signal isn't buried beneath hundreds of lines of
        # source code.
        # Display incoming/outgoing summaries, partitioned by edge type so each
        # section label matches its entries' relationship (INV-rarol).
        print()
        _render_explain_edge_sections(
            callers, "in", "Called by", show_provenance, nodes_by_id)
        print()
        _render_explain_edge_sections(
            callees, "out", "Calls", show_provenance, nodes_by_id)

        # Now print all source dumps (queried symbol → callers → callees)
        # after both summaries.
        if with_source:
            if queried_symbol_source:
                print(f"\n  Source ({path}:{start_line}-{end_line}):")
                for line in queried_symbol_source.splitlines():
                    print(f"    {line}")
            else:
                print(f"\n  [Source unavailable: {path}]")

        # Show caller sources (after both summaries)
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
    artifacts = (generated_files + [input_path]) if not was_cached else [input_path]
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
    """Build tree-sitter grammars from source (Lean, Wolfram, Circom)."""
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


def _ensure_rust_analyzer_integration_or_exit() -> None:
    """Exit with a clear error when the SCIP integration package is missing.

    WI-jinoh / BUG-06: in the published v4.1.0 ``hypergumbo`` distribution,
    ``hypergumbo-lang-rust-analyzer`` is not in ``Requires-Dist`` and is
    not on PyPI, so ``--backend rust-analyzer`` silently no-ops. This
    helper is the single choke point that turns the silent fall-through
    into a non-zero exit with a message naming the missing package.
    """
    from .rust_analyzer_install import is_rust_analyzer_integration_installed

    if is_rust_analyzer_integration_installed():
        return
    print(
        "hypergumbo: error: --backend rust-analyzer requested but the "
        "hypergumbo-lang-rust-analyzer Python integration package is "
        "not installed.\n"
        "\n"
        "The rustup binary alone is not enough \u2014 the SCIP backend also "
        "needs the Python wrapper, which ships behind the "
        "[rust-analyzer] extra. Install via:\n"
        "  pipx install 'hypergumbo[rust-analyzer]' --force\n"
        "(or 'pipx inject hypergumbo hypergumbo-lang-rust-analyzer' to "
        "add the wrapper to an existing install without reinstalling.)",
        file=sys.stderr,
    )
    sys.exit(2)


def _ensure_rust_analyzer_binary_or_exit() -> None:
    """Exit with a clear error when the rust-analyzer binary is non-functional.

    Companion gate to :func:`_ensure_rust_analyzer_integration_or_exit`.
    Both fire from the ``--backend rust-analyzer`` parse path so the
    user finds out about engagement-blockers at parse time rather than
    after a full analysis silently produces tree-sitter output.

    Failure mode this catches: ``shutil.which("rust-analyzer")`` resolves
    a rustup proxy at ``~/.cargo/bin/rust-analyzer``, but the proxy
    errors with ``error: Unknown binary 'rust-analyzer' in official
    toolchain ...`` because the matching rustup component is not
    installed. The integration-package check passes, the existence
    check passes \u2014 only an actual ``--version`` smoke test surfaces
    the brokenness. Without this gate, ``--backend rust-analyzer``
    silently degraded to tree-sitter and the user thought they got
    SCIP analysis when they didn't.
    """
    from .rust_analyzer_install import is_rust_analyzer_available

    if is_rust_analyzer_available():
        return
    print(
        "hypergumbo: error: --backend rust-analyzer requested but the "
        "rust-analyzer binary is not functional on PATH.\n"
        "\n"
        "A binary path resolves but invoking it with --version fails. "
        "The most common cause is a rustup proxy whose component is "
        "not installed: run 'rustup component add rust-analyzer', or "
        "'hypergumbo install-rust-analyzer'.",
        file=sys.stderr,
    )
    sys.exit(2)


def cmd_install_rust_analyzer(args: argparse.Namespace) -> int:
    """Install rust-analyzer (WI-dotud) or report availability via ``--check``.

    WI-jinoh / BUG-06: ``--check`` reports the status of *both* the rustup
    binary and the ``hypergumbo-lang-rust-analyzer`` Python integration
    package, and the bare ``install-rust-analyzer`` invocation refuses
    to install the binary when the integration package is missing \u2014
    installing the binary alone leaves the SCIP backend a silent no-op.
    """
    from .rust_analyzer_install import (
        install_rust_analyzer,
        is_rust_analyzer_available,
        is_rust_analyzer_integration_installed,
    )

    integration_installed = is_rust_analyzer_integration_installed()

    if args.check:
        available = is_rust_analyzer_available()
        symbol = "\u2713" if available else "\u2717"
        print(
            f"rust-analyzer: {symbol} "
            f"{'installed' if available else 'not installed'}",
        )
        ipkg_symbol = "\u2713" if integration_installed else "\u2717"
        print(
            f"hypergumbo-lang-rust-analyzer: {ipkg_symbol} "
            f"{'installed' if integration_installed else 'not installed'}",
        )
        if not available:
            print("\nRun 'hypergumbo install-rust-analyzer' to install.")
        if not integration_installed:
            print(
                "\nThe SCIP backend also requires the "
                "hypergumbo-lang-rust-analyzer Python integration package, "
                "which ships behind the [rust-analyzer] extra. Install via:\n"
                "  pipx install 'hypergumbo[rust-analyzer]' --force\n"
                "(or 'pipx inject hypergumbo hypergumbo-lang-rust-analyzer' "
                "to add the wrapper to an existing install without "
                "reinstalling.)",
            )
        return 0 if (available and integration_installed) else 1

    if not integration_installed:
        print(
            "hypergumbo: error: cannot enable the SCIP backend \u2014 the "
            "hypergumbo-lang-rust-analyzer Python integration package is "
            "not installed. Installing the rustup binary alone would "
            "leave --backend rust-analyzer as a silent no-op. The "
            "integration package ships behind the [rust-analyzer] extra. "
            "Install via:\n"
            "  pipx install 'hypergumbo[rust-analyzer]' --force\n"
            "(or 'pipx inject hypergumbo hypergumbo-lang-rust-analyzer' "
            "to add the wrapper to an existing install without "
            "reinstalling.)",
            file=sys.stderr,
        )
        return 2

    success = install_rust_analyzer(quiet=args.quiet)
    return 0 if success else 1


def cmd_uninstall_rust_analyzer(args: argparse.Namespace) -> int:
    """Uninstall rust-analyzer via rustup (WI-dotud)."""
    from .rust_analyzer_install import uninstall_rust_analyzer

    success = uninstall_rust_analyzer(quiet=args.quiet)
    return 0 if success else 1


# WI-josif consolidation: add-extras / remove-extras umbrella over the four
# per-component paths (grammars, gitleaks, embeddings, rust-analyzer). Each
# row is (component_name, is_available, install_fn, uninstall_fn). The table
# is the single source of truth — adding a fifth component is a one-line
# change in `_extras_components`. WI-huham's separate install-extras /
# uninstall-extras umbrella was hard-removed in favor of this single table.
def _extras_components() -> list[tuple[str, Callable[..., Any], Callable[..., Any], Callable[..., Any]]]:
    """Return the rows the add-extras / remove-extras umbrella iterates over.

    Names are stable identifiers consumed by ``--skip``; their pretty
    display form (used for ``=== Section ===`` headers) is derived via
    ``_pretty_extras_name``.
    """
    # _is_embeddings_available, _install_embeddings_impl, etc. are
    # module-local helpers; reference via sys.modules so test-patches at
    # ``hypergumbo_core.cli.<name>`` apply during table construction.
    import sys
    cli_mod = sys.modules[__name__]

    return [
        (
            "grammars",
            cli_mod._is_grammars_available,
            cli_mod._install_grammars,
            cli_mod._uninstall_grammars,
        ),
        (
            "gitleaks",
            is_gitleaks_available,
            install_gitleaks,
            uninstall_gitleaks,
        ),
        (
            "embeddings",
            cli_mod._is_embeddings_available,
            cli_mod._install_embeddings_impl,
            cli_mod._uninstall_embeddings_impl,
        ),
        (
            "rust-analyzer",
            cli_mod._is_rust_analyzer_fully_available,
            cli_mod._install_rust_analyzer_with_bug06_gate,
            uninstall_rust_analyzer,
        ),
    ]


def _pretty_extras_name(name: str) -> str:
    """Convert a row name to the display form used in section headers.

    e.g. ``"grammars"`` -> ``"Grammars"``,
         ``"rust-analyzer"`` -> ``"Rust analyzer"``.
    """
    return name.replace("-", " ").capitalize()


def _is_grammars_available() -> bool:
    """All hypergumbo build-from-source grammars present and loadable."""
    return all(check_grammar_availability().values())


def _install_grammars(quiet: bool = False) -> bool:
    """Build all hypergumbo grammars; return True iff every grammar built.

    Preserves the per-grammar failure warning that the pre-consolidation
    ``cmd_add_extras`` printed inline — failures still go to stderr even
    in ``--quiet`` mode so partial-build state is observable.
    """
    results = build_all_grammars(quiet=quiet)
    failed = [name for name, ok in results.items() if not ok]
    if failed:
        print(
            f"Warning: Failed to build grammars: {', '.join(failed)}",
            file=sys.stderr,
        )
        return False
    return True


def _uninstall_grammars(quiet: bool = False) -> bool:
    """pip uninstall the three source-built tree-sitter grammars.

    Targets the packages produced by ``_install_grammars`` /
    ``build_all_grammars`` (lean, wolfram, circom). Mirrors the
    ``_uninstall_embeddings_impl`` shape for table-row uniformity.
    """
    import subprocess  # nosec B404 — pip uninstall
    import sys

    if not quiet:
        print("Removing source-built grammars...")
    cmd = [sys.executable, "-m", "pip", "uninstall", "-y",
           "tree-sitter-lean", "tree-sitter-wolfram", "tree-sitter-circom"]
    try:
        result = subprocess.run(  # nosec B603  # noqa: S603
            cmd, capture_output=True, timeout=300.0,
        )
    except (subprocess.TimeoutExpired, OSError) as exc:
        print(f"Error uninstalling grammars: {exc}", file=sys.stderr)
        return False
    if result.returncode != 0:  # pragma: no cover — pip rarely fails
        return False
    if not quiet:
        print("  Done!")
    return True


def _is_rust_analyzer_fully_available() -> bool:
    """Holistic SCIP-backend readiness check: both the rustup binary AND the
    ``hypergumbo-lang-rust-analyzer`` Python integration package present.

    Used as the rust-analyzer row's ``is_available`` predicate so
    ``add-extras --check`` reports ``rust-analyzer: ✗ not installed`` in
    the edge case where the rustup binary is on PATH but the integration
    package is missing — e.g., a user installed ``rust-analyzer`` via a
    system package manager, or pipx-uninstalled the ``[rust-analyzer]``
    extra after a prior install left the rustup binary behind. Without
    this holistic check, ``--check`` would report ``✓ installed`` on the
    binary alone while ``--backend rust-analyzer`` would still hit the
    BUG-06 runtime gate at use time. The dedicated
    ``install-rust-analyzer --check`` subcommand prints both lines
    separately for richer diagnostics; this predicate collapses them
    into a single boolean for the umbrella's status table.
    """
    return is_rust_analyzer_available() and is_rust_analyzer_integration_installed()


def _install_rust_analyzer_with_bug06_gate(quiet: bool = False) -> bool:
    """Install the rust-analyzer rustup component, gated on BUG-06 / WI-jinoh.

    Mirrors ``cmd_install_rust_analyzer``'s gate: refuses to install the
    rustup binary alone when the ``hypergumbo-lang-rust-analyzer`` Python
    integration package is missing — installing the binary alone would
    leave ``--backend rust-analyzer`` a silent no-op. Returns True on
    soft-skip so ``cmd_add_extras`` doesn't report it as a failure (the
    user got a clear pointer to
    ``pipx install 'hypergumbo[rust-analyzer]' --force`` and there is
    nothing useful for the umbrella to install in this state).
    """
    if not is_rust_analyzer_integration_installed():
        if not quiet:
            print(
                "hypergumbo-lang-rust-analyzer Python integration package is "
                "not installed; skipping rust-analyzer rustup binary install. "
                "Install via:\n"
                "  pipx install 'hypergumbo[rust-analyzer]' --force\n"
                "(or 'pipx inject hypergumbo hypergumbo-lang-rust-analyzer' "
                "to add the wrapper to an existing install without "
                "reinstalling.)",
            )
        return True
    return install_rust_analyzer(quiet=quiet)


def _install_embeddings_impl(quiet: bool = False) -> bool:
    """Thin adapter calling the embeddings installer like the others.

    The embeddings installer is inline in cmd_install_embeddings; this
    wrapper exposes the same (quiet=...) -> bool surface so the
    extras table can treat all three components uniformly.
    """
    # The real install_embeddings function is defined in this file;
    # this adapter is here so _extras_components can reference it via
    # module lookup and keep the table declarative.
    import subprocess  # nosec B404 — pip install
    import sys

    if not quiet:
        print("Installing embedding dependencies...")
    cmd = [sys.executable, "-m", "pip", "install",
           "sentence-transformers", "onnxruntime"]
    try:
        result = subprocess.run(  # nosec B603  # noqa: S603
            cmd, capture_output=True, timeout=600.0,
        )
    except (subprocess.TimeoutExpired, OSError) as exc:
        print(f"Error installing embeddings: {exc}", file=sys.stderr)
        return False
    if result.returncode != 0:
        stderr = result.stderr
        if isinstance(stderr, bytes):
            stderr = stderr.decode("utf-8", errors="replace")
        print(
            f"Error: pip install for embeddings exited "
            f"{result.returncode}. {stderr.strip()}",
            file=sys.stderr,
        )
        return False
    if not quiet:
        print("  Done!")
    return True


def _uninstall_embeddings_impl(quiet: bool = False) -> bool:
    """Thin adapter for the extras table uninstall path."""
    import subprocess  # nosec B404 — pip uninstall
    import sys

    if not quiet:
        print("Removing embedding dependencies...")
    cmd = [sys.executable, "-m", "pip", "uninstall", "-y",
           "sentence-transformers", "onnxruntime"]
    try:
        result = subprocess.run(  # nosec B603  # noqa: S603
            cmd, capture_output=True, timeout=300.0,
        )
    except (subprocess.TimeoutExpired, OSError) as exc:
        print(f"Error uninstalling embeddings: {exc}", file=sys.stderr)
        return False
    if result.returncode != 0:  # pragma: no cover — pip rarely fails
        return False
    if not quiet:
        print("  Done!")
    return True


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


_DEFAULT_HONK_GB = 1.0


def _get_honk_threshold_bytes() -> float | None:
    """INV-padum lifecycle policy: cache honk-threshold in bytes.

    Reads ``HYPERGUMBO_CACHE_HONK_GB``. Default 1.0 GiB.

    Returns:
        Threshold in bytes, or ``None`` when the user has silenced the
        warning (env var ``0``, ``off``, ``none``, ``false``, empty string,
        or a non-positive numeric value).

    Malformed env values fall back to the default with a UserWarning so
    a typo can't crash any CLI surface that calls this on the hot path.
    """
    raw = os.environ.get("HYPERGUMBO_CACHE_HONK_GB")
    if raw is None:
        return _DEFAULT_HONK_GB * (1024 ** 3)
    raw_stripped = raw.strip().lower()
    if raw_stripped in ("", "0", "off", "none", "false"):
        return None
    try:
        value = float(raw_stripped)
    except ValueError:
        import warnings
        warnings.warn(
            f"Invalid HYPERGUMBO_CACHE_HONK_GB={raw!r}; "
            f"falling back to {_DEFAULT_HONK_GB} GB.",
            stacklevel=2,
        )
        return _DEFAULT_HONK_GB * (1024 ** 3)
    if value <= 0:
        return None
    return value * (1024 ** 3)


def _list_repo_breakdown(cache_dir: Path) -> list[dict]:
    """Per-repo cache breakdown, sorted by size descending.

    Each row carries ``fingerprint`` (top-level subdir name), ``size``
    (bytes), ``entries`` (count of state-hash subdirs under ``results/``),
    and ``last_used`` (mtime of the repo subdir).
    """
    rows: list[dict] = []
    for entry in cache_dir.iterdir():
        if not entry.is_dir():
            continue
        results_dir = entry / "results"
        entry_count = 0
        if results_dir.is_dir():
            entry_count = sum(1 for s in results_dir.iterdir() if s.is_dir())
        size = _get_dir_size(entry)
        try:
            mtime = entry.stat().st_mtime
        except OSError:  # pragma: no cover
            mtime = 0.0  # pragma: no cover
        rows.append({
            "fingerprint": entry.name,
            "size": size,
            "entries": entry_count,
            "last_used": mtime,
        })
    rows.sort(key=lambda r: r["size"], reverse=True)
    return rows


def _maybe_honk_cache(
    cache_dir: Path, total_size: int | None = None
) -> None:
    """Emit the INV-padum cache honk to stderr when threshold exceeded.

    No-op when the user has silenced via ``HYPERGUMBO_CACHE_HONK_GB=0``,
    the cache directory does not exist, or total size is below threshold.
    The honk identifies the top consumer and lists the actionable next
    steps (inspect / prune / configure) so the user can decide whether
    to retain or prune without leaving the terminal.
    """
    threshold = _get_honk_threshold_bytes()
    if threshold is None:
        return
    if not cache_dir.exists():
        return
    if total_size is None:
        total_size = _get_dir_size(cache_dir)
    if total_size < threshold:
        return
    rows = _list_repo_breakdown(cache_dir)
    threshold_gb = threshold / (1024 ** 3)
    lines = [
        f"⚠  HG cache is {_format_size(total_size)} "
        f"(threshold: {threshold_gb:.1f} GB)",
    ]
    if rows:
        top = rows[0]
        lines.append(
            f"    Top consumer: {top['fingerprint']} "
            f"({_format_size(top['size'])}, {top['entries']} entries)"
        )
    lines.append("    Inspect:   hypergumbo cache-status --per-repo")
    lines.append(
        "    Prune:     hypergumbo cache-clear --repo <id> --keep-latest 5"
    )
    lines.append(
        "    Configure: HYPERGUMBO_CACHE_HONK_GB=<N>   (0 silences)"
    )
    print("\n".join(lines), file=sys.stderr)


def cmd_cache_status(args: argparse.Namespace) -> int:
    """Show cache status and statistics.

    Reports:
    - Number of cached repo entries
    - Total cache size
    - Cache location

    With ``--per-repo``, additionally lists each top-level repo
    subdirectory with size, state-entry count, and last-used time so
    the user can identify which repo is consuming the cache.

    INV-padum lifecycle policy: at the end of the report, emit the
    honk-threshold warning if total cache size exceeds the configured
    threshold. The warning goes to stderr so it stands out from the
    routine stdout report and pipes / redirects cleanly.
    """
    import time

    cache_dir = _get_cache_base()
    per_repo = getattr(args, "per_repo", False)

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

    if per_repo:
        rows = _list_repo_breakdown(cache_dir)
        print("")
        print("By repo:")
        if not rows:
            print("  (none)")
        else:
            now = time.time()
            for row in rows:
                age_days = int((now - row["last_used"]) / 86400)
                if age_days <= 0:
                    age_label = "today"
                elif age_days == 1:
                    age_label = "1 day ago"
                else:
                    age_label = f"{age_days} days ago"
                print(
                    f"  {row['fingerprint']:<20} "
                    f"{_format_size(row['size']):>10}   "
                    f"{row['entries']:>3} entries   "
                    f"(last used: {age_label})"
                )

    _maybe_honk_cache(cache_dir, total_size=total_size)

    return 0


def cmd_cache_clear(args: argparse.Namespace) -> int:
    """Clear the hypergumbo cache.

    Options:
    - --older-than N: Only remove entries older than N days
    - --dry-run: Show what would be deleted without deleting
    - --repo FINGERPRINT: Restrict deletion to one repo's subtree
    - --keep-latest N: Within --repo, keep the N most recently used
      state-hash subdirs under ``results/`` (INV-padum: targeted prune).
    """
    import time

    cache_dir = _get_cache_base()
    repo = getattr(args, "repo", None)
    keep_latest = getattr(args, "keep_latest", None)

    if keep_latest is not None and repo is None:
        print(
            "Error: --keep-latest requires --repo to scope which repo to prune.",
            file=sys.stderr,
        )
        return 2

    if not cache_dir.exists():
        if not args.quiet:
            print(f"Cache directory does not exist: {cache_dir}")
        return 0

    if repo is not None:
        return _cache_clear_repo(args, cache_dir, repo, keep_latest)

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
            cache_rmtree(entry)
            deleted_count += 1
        except (OSError, PermissionError) as e:  # pragma: no cover
            if not args.quiet:  # pragma: no cover
                print(f"Warning: Could not delete {entry}: {e}")  # pragma: no cover

    if not args.quiet:
        print(f"Deleted {deleted_count} entries ({_format_size(total_size)})")

    return 0


def _cache_clear_repo(
    args: argparse.Namespace,
    cache_dir: Path,
    repo: str,
    keep_latest: int | None,
) -> int:
    """Per-repo cache prune helper for ``cache-clear --repo``.

    Two modes:
    - ``keep_latest is None``: delete the entire repo subdir.
    - ``keep_latest is not None``: delete all but the N most recent
      state-hash subdirs under ``<repo>/results/``.

    Either mode honors ``--dry-run`` and ``--quiet``.
    """
    repo_dir = cache_dir / repo
    if not repo_dir.is_dir():
        if not args.quiet:
            print(f"No cache entries for repo {repo}")
        return 0

    if keep_latest is None:
        size = _get_dir_size(repo_dir)
        if args.dry_run:
            if not args.quiet:
                print(
                    f"Would delete repo {repo} ({_format_size(size)})"
                )
            return 0
        cache_rmtree(repo_dir)
        if not args.quiet:
            print(f"Deleted repo {repo} ({_format_size(size)})")
        return 0

    results_dir = repo_dir / "results"
    if not results_dir.is_dir():
        if not args.quiet:
            print(f"No results entries for repo {repo}")
        return 0

    state_dirs = [d for d in results_dir.iterdir() if d.is_dir()]
    state_dirs.sort(key=lambda d: d.stat().st_mtime, reverse=True)
    to_keep = state_dirs[:keep_latest]
    to_delete = state_dirs[keep_latest:]
    if not to_delete:
        if not args.quiet:
            print(
                f"Repo {repo} has {len(state_dirs)} entries; "
                f"nothing to prune (keep-latest={keep_latest})."
            )
        return 0

    total_size = sum(_get_dir_size(d) for d in to_delete)

    if args.dry_run:
        if not args.quiet:
            print(
                f"Would delete {len(to_delete)} entries from repo {repo} "
                f"({_format_size(total_size)}); keeping {len(to_keep)}."
            )
            for d in to_delete:
                print(f"  {d.name}")
        return 0

    deleted = 0
    for d in to_delete:
        try:
            cache_rmtree(d)
            deleted += 1
        except (OSError, PermissionError) as e:  # pragma: no cover
            if not args.quiet:  # pragma: no cover
                print(  # pragma: no cover
                    f"Warning: Could not delete {d}: {e}", file=sys.stderr
                )

    if not args.quiet:
        print(
            f"Deleted {deleted} entries from repo {repo} "
            f"({_format_size(total_size)}); kept {len(to_keep)}."
        )
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
    """Install (or check) every optional extras component in one call.

    By default builds grammars and installs gitleaks, embeddings, and the
    rust-analyzer rustup component. Components already installed are
    skipped with a message. The rust-analyzer step gates on the SCIP
    integration package (BUG-06 / WI-jinoh) via
    ``_install_rust_analyzer_with_bug06_gate`` so the rustup binary is
    not installed alone when the integration is missing — the user
    gets a pointer to ``pipx install 'hypergumbo[rust-analyzer]'`` instead.

    ``--check`` prints a status table and exits 0 iff every component is
    installed; exit 1 otherwise so scripts can gate on it.

    ``--skip COMPONENT[,COMPONENT...]`` omits the named components from
    both the status table and the install loop.
    """
    skip = set()
    if getattr(args, "skip", None):
        skip = {s.strip() for s in args.skip.split(",") if s.strip()}

    rows = [row for row in _extras_components() if row[0] not in skip]

    if getattr(args, "check", False):
        all_present = True
        for name, row_available, _install, _uninstall in rows:
            available = row_available()
            symbol = "\u2713" if available else "\u2717"
            print(
                f"{name}: {symbol} "
                f"{'installed' if available else 'not installed'}",
            )
            if not available:
                all_present = False
        return 0 if all_present else 1

    exit_code = 0
    for name, row_available, install, _uninstall in rows:
        if not args.quiet:
            print(f"=== {_pretty_extras_name(name)} ===")
        if row_available():
            if not args.quiet:
                print(f"{name} already installed. Skipping.")
        elif not install(quiet=args.quiet):
            exit_code = 1
        if not args.quiet:
            print()

    if not args.quiet:
        print("=== Summary ===")
        print("All extras installed. Run 'hypergumbo remove-extras' to uninstall.")

    return exit_code


def cmd_remove_extras(args: argparse.Namespace) -> int:
    """Uninstall every optional extras component in one call.

    The grammars row pip-uninstalls the three source-built tree-sitter
    grammars (lean, wolfram, circom). Re-adding requires git + a C
    compiler again. The rust-analyzer step removes only the
    rustup-managed binary; the SCIP integration package
    (``hypergumbo-lang-rust-analyzer``) is pipx-extra-managed and removed
    via ``pipx uninstall-injected hypergumbo hypergumbo-lang-rust-analyzer``.

    ``--skip COMPONENT[,COMPONENT...]`` omits the named components.
    """
    skip = set()
    if getattr(args, "skip", None):
        skip = {s.strip() for s in args.skip.split(",") if s.strip()}

    rows = [row for row in _extras_components() if row[0] not in skip]

    exit_code = 0
    for name, row_available, _install, uninstall in rows:
        if not args.quiet:
            print(f"=== {_pretty_extras_name(name)} ===")
        if not row_available():
            if not args.quiet:
                print(f"{name} not installed. Skipping.")
        elif not uninstall(quiet=args.quiet):
            exit_code = 1
        if not args.quiet:
            print()

    if not args.quiet:
        print("=== Summary ===")
        print("Extras removed. hypergumbo will continue to work with core features.")
        print("Run 'hypergumbo add-extras' to reinstall.")

    return exit_code



# Default minimum widths for the Symbol and File columns in `cmd_symbols`.
# Roughly twice what Rich's auto-fit picks in narrow (~80-col) hosts like
# Google Colab — wide enough to show full identifiers and short paths
# without ellipsis on common-case content.
_SYMBOLS_DEFAULT_SYMBOL_WIDTH = 60
_SYMBOLS_DEFAULT_FILE_WIDTH = 80
_SYMBOLS_MAX_COL_WIDTH = 1000
# Rich's per-column padding (default (0, 1) => 2 chars * 6 columns) plus a
# small safety margin, added to the DATA-DRIVEN content widths of the four
# inner columns (Kind / In / Out / Deg) to force the console wide enough that
# none is squeezed. A fixed overhead instead (the old approach) under-budgeted
# the numeric columns once In/Deg hit 4 digits, so Rich proportionally squeezed
# Deg into "10…" — destroying the rank the `symbols` command exists to surface
# (INV-ripoh; the pass-31 face extended the same truncation to the In column).
_SYMBOLS_COLUMN_PADDING = 14


def _symbols_column_config(
    *, col_width: int | None, wrap: bool,
) -> tuple[int, int, str, bool]:
    """Resolve Symbol/File column widths and overflow strategy for ``cmd_symbols``.

    Returns ``(symbol_width, file_width, overflow, no_wrap)``:

    - When ``col_width`` is ``None`` the columns use the wider-than-default
      minimums (60/80) so output stays readable in narrow hosts (Colab).
    - When ``col_width`` is set both columns use it, clamped to
      ``[1, _SYMBOLS_MAX_COL_WIDTH]`` (1000-char sanity bound).
    - ``wrap=True`` switches the overflow strategy to ``"fold"`` (mid-token
      wrap) and disables ``no_wrap``; otherwise content is truncated with
      ``"ellipsis"``.
    """
    if col_width is None:
        symbol_w = _SYMBOLS_DEFAULT_SYMBOL_WIDTH
        file_w = _SYMBOLS_DEFAULT_FILE_WIDTH
    else:
        bounded = max(1, min(col_width, _SYMBOLS_MAX_COL_WIDTH))
        symbol_w = bounded
        file_w = bounded
    overflow = "fold" if wrap else "ellipsis"
    no_wrap = not wrap
    return symbol_w, file_w, overflow, no_wrap


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
    behavior_map = load_behavior_map(input_path)
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

        # Apply filters. WI-jukav slice 2: dual-shape predicate
        # forward-compat with ADR-0027 §"Phase 3" Wave 5 framework_role
        # fold — synthetic post-fold nodes (kind=function|method +
        # meta.framework_role=<excluded role>) are excluded the same as
        # their pre-fold legacy-kind counterparts.
        if is_excluded_kind(kind, node.get("meta")):
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
        artifacts = (generated_files + [input_path]) if not was_cached else [input_path]
        _print_output_summary(
            "symbols", artifacts=artifacts, stdout_output=True, cached_artifacts=cached_set
        )
        return 0

    # Resolve Symbol/File column-width controls. Defaults widen the two
    # text columns so narrow hosts (e.g. Google Colab, where Rich detects
    # ~80 cols) don't squeeze them into ellipsized stubs.
    symbol_width, file_width, overflow, no_wrap_flag = _symbols_column_config(
        col_width=getattr(args, "col_width", None),
        wrap=getattr(args, "wrap", False),
    )

    # Force the console width wide enough to fit Symbol + File at their
    # requested widths plus the four narrow inner columns (Kind / In / Out
    # / Deg) and Rich's per-column padding. In a wider terminal we honor
    # that, so the table can grow naturally; in a narrow terminal (Colab,
    # CI logs) the table overflows the viewport — a horizontal scroll is
    # the correct trade-off when the user has explicitly asked for wide
    # columns.
    detected_width = shutil.get_terminal_size(fallback=(120, 24)).columns
    # Data-driven widths for the four inner columns: each must be at least as
    # wide as its widest cell (or its header), so the required console width
    # reflects the actual values and Rich never squeezes a numeric column into
    # an ellipsis (INV-ripoh). Header labels floor the numeric widths.
    kind_width = max((len(r[1]) for r in display_rows), default=4)
    in_width = max([len("In")] + [len(str(r[2])) for r in display_rows])
    out_width = max([len("Out")] + [len(str(r[3])) for r in display_rows])
    deg_width = max([len("Deg")] + [len(str(r[4])) for r in display_rows])
    required_width = (
        symbol_width + file_width
        + kind_width + in_width + out_width + deg_width
        + _SYMBOLS_COLUMN_PADDING
    )
    console = Console(width=max(detected_width, required_width))
    table = Table(show_header=True, header_style="bold", box=None)

    table.add_column(
        "Symbol", style="cyan",
        width=symbol_width, no_wrap=no_wrap_flag, overflow=overflow,
    )
    table.add_column("Kind", style="green", min_width=kind_width, no_wrap=True)
    table.add_column("In", justify="right", style="yellow",
                     min_width=in_width, no_wrap=True)
    table.add_column("Out", justify="right", style="yellow",
                     min_width=out_width, no_wrap=True)
    table.add_column("Deg", justify="right", style="bold yellow",
                     min_width=deg_width, no_wrap=True)
    table.add_column(
        "File", style="dim",
        width=file_width, no_wrap=no_wrap_flag, overflow=overflow,
    )

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
    artifacts = (generated_files + [input_path]) if not was_cached else [input_path]
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
    behavior_map = load_behavior_map(input_path)
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
        user_out_open_json_dump(out_path, compact_map)
        print(f"Compact behavior map written to: {out_path}")
    else:
        print(json.dumps(compact_map, indent=2, sort_keys=True))

    return 0


def cmd_io_boundaries(args: argparse.Namespace) -> int:
    """Display I/O boundary map for a repository (ADR-0016).

    Identifies call edges that reach I/O primitives and groups them by
    boundary type: ``fs_read``, ``fs_write``, ``net_send``, ``net_recv``,
    ``subprocess``, ``env_read``, ``env_write``, ``ipc_send``, ``ipc_recv``,
    ``browser_storage_write``, ``browser_storage_read``, ``db_read``,
    ``db_write``, ``process_send``, ``logging``. Attribute-style primitives
    (``os.environ``, ``sys.argv``) are included via ``module_attr_ref``
    edges. Loads a cached behavior map or auto-runs analysis if needed.
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

    behavior_map = load_behavior_map(input_path)
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
            # work when the catalog's base differs from the Symbol's language
            # (e.g., catalog aliases like typescript→javascript or
            # groovy→java in io_boundary._CATALOG_ALIASES).
            if catalog.language != lang:
                catalogs[catalog.language] = catalog

    # Extract entrypoint IDs for reverse-trace
    entrypoint_ids = {
        ep.get("symbol_id", ep.get("node_id", ""))
        for ep in behavior_map.get("entrypoints", [])
    }

    # Build node lookup for human-readable caller names AND for IoChain
    # tier surfacing — passing nodes_by_id into compute_boundary_map lets
    # each chain carry its dst's supply_chain.tier so verify-claims /
    # sketch / external consumers can distinguish "first-party calls
    # first-party I/O" from "first-party calls tier-3 wrapper that may
    # reach the network" without per-library catalog growth.
    nodes_by_id: Dict[str, Any] = {n["id"]: n for n in behavior_map.get("nodes", [])}

    # Compute boundary map with entrypoint tracing AND tier lookup
    bmap = compute_boundary_map(
        edges, catalogs,
        entrypoint_ids=entrypoint_ids or None,
        nodes_by_id=nodes_by_id,
    )

    # Apply boundary/primitive/exclude-tests filters
    boundary_filter = getattr(args, "boundary", None)
    primitive_filter = getattr(args, "primitive", None)
    # WI-sifif: production-only is the default. Tests can opt-in to the
    # historical "show everything" behavior by overriding exclude_tests=False;
    # CLI users do the same via --include-tests.
    exclude_tests = getattr(args, "exclude_tests", True)

    from .io_boundary import (
        BoundaryMapEntry,
        _build_reverse_graph,
        compute_leaf_rollups,
    )

    # WI-rubir: when the filter path rebuilds BoundaryMapEntry it must
    # also recompute the WI-darad leaf-caller roll-ups for the surviving
    # chain subset; otherwise leaf_callers / entry_points_per_leaf are
    # silently dropped (they default to empty on the dataclass), and
    # because exclude_tests=True is the default, every normal CLI
    # invocation hits this path.
    reverse_graph_for_filter: Optional[Dict[str, set[str]]] = None

    def _ensure_reverse_graph() -> Dict[str, set[str]]:
        nonlocal reverse_graph_for_filter
        if reverse_graph_for_filter is None:
            reverse_graph_for_filter = _build_reverse_graph(edges)
        return reverse_graph_for_filter

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
            leaf_callers, entry_points_per_leaf = compute_leaf_rollups(
                chains,
                _ensure_reverse_graph(),
                entrypoint_ids or None,
            )
            filtered_entries[btype] = BoundaryMapEntry(
                boundary=entry.boundary,
                chains=chains,
                entry_points=sorted({ep for c in chains for ep in c.entry_points}),
                primitives_used=sorted({c.primitive for c in chains}),
                leaf_callers=leaf_callers,
                entry_points_per_leaf=entry_points_per_leaf,
            )
        else:
            filtered_entries[btype] = entry

    # Output
    if getattr(args, "json_output", False):
        if boundary_filter or primitive_filter or exclude_tests:
            from .io_boundary import IO_BOUNDARIES_SCHEMA_VERSION

            filtered_total = sum(len(e.chains) for e in filtered_entries.values())
            output = {
                # PR-B: pin the io-boundaries envelope schema_version on
                # the filtered path too; the unfiltered path inherits it
                # from ``BoundaryMap.to_dict``.
                "schema_version": IO_BOUNDARIES_SCHEMA_VERSION,
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
        return 0

    # WI-mibag: text-output suppression of the `external_potential`
    # bucket. The bucket can dominate on large repos (kafka had 76K
    # chains of 76K total) and drown out the per-primitive signal —
    # but it remains useful, so JSON callers still get everything and
    # users can opt back in via `--show-external-potential` or by
    # targeting the bucket with `--boundary external_potential`.
    show_ep = getattr(args, "show_external_potential", False)
    ep_targeted = (boundary_filter == "external_potential")
    ep_suppressed_count = 0
    display_entries = filtered_entries
    if not show_ep and not ep_targeted and "external_potential" in filtered_entries:
        ep_suppressed_count = len(
            filtered_entries["external_potential"].chains,
        )
        display_entries = {
            k: v for k, v in filtered_entries.items()
            if k != "external_potential"
        }

    if getattr(args, "by_file", False):
        _print_io_boundaries_by_file(display_entries, nodes_by_id, repo_root)
    else:
        _print_io_boundaries_by_type(display_entries, nodes_by_id, bmap, repo_root)
    if ep_suppressed_count:
        print(
            f"  external_potential: {ep_suppressed_count} chain(s) "
            f"suppressed (pass --show-external-potential to include "
            f"them, or use --boundary external_potential).",
        )
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
            # WI-vumos: tier tag describes the boundary destination, not
            # the caller. Render it on the primitive header line so the
            # referent is unambiguous. All chains hitting the same
            # primitive share the same dst, so we read tier from any one
            # of them.
            prim_tier_tag = ""
            for chain in chains_by_prim[prim]:
                if chain.dst_external_boundary and chain.dst_tier_name:
                    prim_tier_tag = (
                        f"  [tier-{chain.dst_tier} {chain.dst_tier_name}]"
                    )
                    break
            print(f"    {prim} ({count}){risk_flag}{prim_tier_tag}")
            for chain in chains_by_prim[prim]:
                caller = _format_io_caller(chain.io_edge_src, nodes_by_id, repo_root)
                # Plan C, PR C: external_potential chains for in_progress
                # source languages flag the absence-of-catalog-hit as
                # unreliable so the user knows the language's stdlib is
                # not yet provenance-validated. The unreliable flag stays
                # per-caller because it depends on the SOURCE language,
                # not the dst — different callers of the same primitive
                # from different source languages may differ.
                unreliable_tag = (
                    "  [unreliable]"
                    if chain.dst_classification_unreliable
                    else ""
                )
                print(f"      <- {caller}{unreliable_tag}")
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


def _build_python_ddg_for_verify_claims(
    repo_root: Path,
) -> tuple[list, set[str], dict[str, dict[tuple[int, str], str]]]:
    """Build aggregated DDG edges + symbol set + receiver hints for taint analysis.

    Walks the repo for Python source files, parses each with tree-sitter,
    builds a CFG per function via ``build_function_cfg``, runs
    ``solve_reaching_defs`` to extract DDG edges, and runs the WI-dilih
    post-DDG IR refinement pass to derive ``(caller_id → {(line, attr):
    module_hint})`` hints used to rewrite unresolved-external edge dsts.
    Returns ``(ddg_edges, ddg_symbols, hints_by_caller)`` ready to pass
    to ``refine_external_edges`` and ``propagate_taint_ddg``.

    Why this exists: ``propagate_taint_structural`` matches sinks by short
    callee name, so ``dict.get`` collides with ``multiprocessing.Queue.get``
    on per-entry-point safety claims (see docs/hypergumbo.claims.yaml).
    The DDG-aware variant uses receiver-type information from the def/use
    extractors (per ADR-0017 §1b) to disambiguate, eliminating the
    false-positive flood. The refinement pass goes further: when the
    Python analyzer emitted ``python:external:0-0:NAME:unresolved``
    because it couldn't pin a receiver, the DDG plus file-scope imports
    are often enough to rewrite that to e.g.
    ``python:os.environ:0-0:NAME:unresolved``, letting
    ``_sink_module_compatible`` reject the cross-module short-name
    collision that previously fell through its ``external`` exemption.

    Returns ``([], set(), {})`` if tree-sitter / cfg-mapping isn't
    available — the caller falls back to the structural pass in that
    case.
    """
    try:
        import tree_sitter
        from tree_sitter_language_pack import get_language
        from .cfg import (
            build_function_cfg,
            load_cfg_mapping,
            populate_def_use_for_cfg,
            solve_reaching_defs,
        )
        from .taint_refine import (
            extract_python_imports,
            extract_python_param_annotations,
            extract_python_receiver_hints,
        )
        # Force-import the Python def/use extractor so it self-registers.
        # The decorator-registration in py_def_use only fires on first
        # import; nothing else in the verify-claims path imports it.
        import hypergumbo_lang_mainstream.py_def_use  # noqa: F401
    except ImportError:  # pragma: no cover - tree-sitter is a hard dep but defend
        return [], set(), {}

    mapping = load_cfg_mapping("python")
    if mapping is None:  # pragma: no cover - python mapping always ships
        return [], set(), {}

    try:
        lang = get_language("python")
    except Exception:  # pragma: no cover - language pack always provides python
        return [], set(), {}
    parser = tree_sitter.Parser(lang)

    ddg_edges: list = []
    ddg_symbols: set[str] = set()
    hints_by_caller: dict[str, dict[tuple[int, str], str]] = {}

    # Walk all .py files under repo_root. Skip the .venv / .git / .ci /
    # __pycache__ tree-walk skips. Match the analyzer's exclude pattern
    # at a coarse level — verify-claims should not pay the cost of
    # analyzing third-party code.
    skip_dirs = {
        ".git", ".venv", "venv", ".tox", "__pycache__",
        ".ci", "node_modules", ".mypy_cache", ".pytest_cache",
        ".ruff_cache", "build", "dist", ".eggs",
    }
    for py_path in repo_root.rglob("*.py"):
        # Skip anything under an excluded directory.
        if any(part in skip_dirs for part in py_path.parts):
            continue
        try:
            src = py_path.read_bytes()
        except OSError:  # pragma: no cover - defensive
            continue
        # Tree-sitter is robust; if a parse exception ever fires we
        # skip the file rather than abort the whole verify-claims run.
        tree = parser.parse(src)
        rel_path = py_path.relative_to(repo_root).as_posix()
        # File-scope imports feed the WI-dilih refinement: the receiver
        # hints derived per-function need access to the module-bind /
        # from-import maps visible at the file's top level.
        module_imports, imports = extract_python_imports(tree.root_node, src)
        # Visit every function_definition in the file.
        _collect_python_function_ddg(
            tree.root_node, src, mapping, rel_path,
            ddg_edges, ddg_symbols, hints_by_caller,
            module_imports=module_imports,
            imports=imports,
            build_function_cfg=build_function_cfg,
            populate_def_use_for_cfg=populate_def_use_for_cfg,
            solve_reaching_defs=solve_reaching_defs,
            extract_python_receiver_hints=extract_python_receiver_hints,
            extract_python_param_annotations=extract_python_param_annotations,
        )

    return ddg_edges, ddg_symbols, hints_by_caller


def _collect_python_function_ddg(
    node: Any,
    src: bytes,
    mapping: Any,
    rel_path: str,
    ddg_edges: list,
    ddg_symbols: set[str],
    hints_by_caller: dict[str, dict[tuple[int, str], str]],
    *,
    module_imports: dict[str, str],
    imports: dict[str, tuple[str, str]],
    build_function_cfg: Any,
    populate_def_use_for_cfg: Any,
    solve_reaching_defs: Any,
    extract_python_receiver_hints: Any,
    extract_python_param_annotations: Any,
) -> None:
    """Recurse a Python AST collecting per-function DDG edges and refinement hints.

    Matches the symbol-id convention used by hypergumbo's Python analyzer:
    ``python:<rel-path>:<start_line>-<end_line>:<name>:function`` (or
    ``method`` when nested in a class). Approximate match — we don't
    walk class context here — but the structural BFS over `raw_edges`
    keys by the same short-id format, so the aggregated ``ddg_symbols``
    set lines up well enough for propagate_taint_ddg's mixed-coverage
    branch to fire on the right nodes.

    For each function with a non-empty DDG, also runs the WI-dilih
    refinement pass to derive ``{(call_line, attr_name) → module_hint}``
    entries and stores them in ``hints_by_caller[sym_id]``.
    """
    if node.type == "function_definition":
        name_node = node.child_by_field_name("name")
        body_node = node.child_by_field_name("body")
        if name_node is not None and body_node is not None:
            name = src[name_node.start_byte:name_node.end_byte].decode(
                "utf-8", errors="replace",
            )
            start_line = node.start_point[0] + 1
            end_line = node.end_point[0] + 1
            sym_id = (
                f"python:{rel_path}:{start_line}-{end_line}"
                f":{name}:function"
            )
            try:
                cfg = build_function_cfg(body_node, src, mapping, sym_id)
                populate_def_use_for_cfg(cfg, body_node, src, "python")
                result = solve_reaching_defs(cfg)
            except Exception:  # pragma: no cover - defensive
                return  # bail on this function; continue tree walk implicitly skipped
            if not result.bailed_out:
                if result.ddg_edges:
                    ddg_edges.extend(result.ddg_edges)
                    ddg_symbols.add(sym_id)
                # WI-dozon: parameter annotations are extracted even when
                # the DDG is empty — short helpers like `return name.replace(...)`
                # have no def-use edges, but the parameter annotation is
                # exactly the signal that pins the receiver type. Run the
                # refinement whenever annotations OR DDG edges exist.
                param_anns = extract_python_param_annotations(
                    node, src, module_imports, imports,
                )
                if param_anns or result.ddg_edges:
                    fn_hints = extract_python_receiver_hints(
                        body_node, src, module_imports, imports, result.ddg_edges,
                        param_annotations=param_anns,
                    )
                    if fn_hints:
                        hints_by_caller[sym_id] = fn_hints
    # Recurse into children so we pick up nested function definitions.
    for child in node.children:
        _collect_python_function_ddg(
            child, src, mapping, rel_path,
            ddg_edges, ddg_symbols, hints_by_caller,
            module_imports=module_imports,
            imports=imports,
            build_function_cfg=build_function_cfg,
            populate_def_use_for_cfg=populate_def_use_for_cfg,
            solve_reaching_defs=solve_reaching_defs,
            extract_python_receiver_hints=extract_python_receiver_hints,
            extract_python_param_annotations=extract_python_param_annotations,
        )


def cmd_verify_claims(args: argparse.Namespace) -> int:
    """Verify security claims against I/O boundary map and taint flow.

    Loads claims from a YAML file, computes the I/O boundary map, runs
    taint-flow analysis if needed, and checks each claim. Returns exit
    code 1 if any claim is violated. Supports boundary constraints
    (ADR-0016) and taint-flow constraints (ADR-0017).

    Trust zones checked: ``host_fs``, ``network``, ``host_env``, ``ipc``,
    ``browser_storage``, ``relay``. Built-in taint labels: ``host_secret``,
    ``untrusted_input``, ``plaintext``, ``key_material``, ``ciphertext``,
    ``derived_key``. The source and sink catalogs are derived automatically
    from ``io_primitives/*.yaml`` (every write-side primitive is a sink at
    ``trust_level=untrusted``; ``env_read``, ``net_recv``, and ``ipc_recv``
    primitives are sources). YAML files under ``taint_sources/`` and
    ``taint_sanitizers/`` contribute cryptographic labels and sanitizer
    transforms that the auto-layer cannot express.  (Built-in sinks come
    only from the auto-layer above; ``taint_sinks/`` as a shipped
    directory was retired in 51e1d232f3.)

    Project-local catalogs (WI-votan): the ``--taint-sources``,
    ``--taint-sinks``, and ``--taint-sanitizers`` flags each accept a YAML
    file or a directory of YAMLs and are repeatable; the claims YAML may
    carry the same paths under a top-level ``extra_catalogs:`` key with
    ``sources``/``sinks``/``sanitizers`` sub-lists (paths resolve relative
    to the claims-file directory).  User entries whose
    ``(module, name, kind)`` triple matches an auto-derived or built-in
    source/sink replace it; user sanitizers concatenate.  This is the
    supported extension point for declaring project-specific trust zones,
    raising ``trust_level`` on a sink that is safe in context, or adding
    a domain-specific taint source label.
    """
    repo_root = Path(args.path).resolve()
    claims_path = Path(args.claims)

    if not claims_path.exists():
        print(f"Error: Claims file not found: {claims_path}", file=sys.stderr)
        return 1

    # Load claims
    from .verify_claims import (
        ClaimsFileError,
        compute_boundary_coverage,
        load_claims,
        verify_claims as _verify,
    )

    # META-jurig load gate: malformed / typo'd / unknown-vocabulary claims
    # surface as a clean rc=2 error rather than a raw traceback (INV-zurih /
    # WI-fuhaf) or a silent false "confirmed" (INV-gobob / WI-ruzib / WI-bopoz).
    # rc=2 (not 1) keeps a bad-input error distinct from a real violation.
    try:
        claims = load_claims(claims_path)
    except ClaimsFileError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2
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

    behavior_map = load_behavior_map(input_path)
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

    # WI-kajil / INV-bitig: a zero-chain "confirmed" boundary verdict is only
    # trustworthy if the I/O analysis could actually see the I/O. Derive a
    # coverage signal from call-edge production across the supported languages
    # actually present in the repo (languages with an I/O catalog). An empty
    # analysis (no call edges) or a supported language that produced zero call
    # edges (analyzer blind — F69.A1) downgrades a would-be "confirmed"
    # must_not_exist / max_chains verdict to "inconclusive".
    supported_present = languages & set(catalogs)
    coverage = compute_boundary_coverage(raw_edges, supported_present)

    # Run taint-flow analysis if any claims have taint_flow constraints
    taint_findings = None
    # INV-javam: track languages with no taint coverage so callers can
    # distinguish "no taint-flow violations" from "language not analyzed".
    # Without this, taint-flow trivially passes every claim on unsupported
    # languages and the verify-claims output lies by omission.
    unsupported_taint_languages: list[str] = []
    has_taint_claims = any(c.constraint_taint_flow is not None for c in claims)
    taint_catalog = None

    from .taint import (
        TaintCatalogError,
        load_full_taint_catalog,
        propagate_taint_structural,
    )
    from .verify_claims import load_extra_catalog_paths

    # Assemble project-local taint catalog paths from CLI flags and the
    # ``extra_catalogs:`` key in the claims YAML (WI-votan).  INV-hukug: the
    # two are kept as DISTINCT layers — CLI flags (higher) override claims-file
    # extras (lower) on (module, name, kind), which override the built-in
    # catalog.
    cli_sources = [Path(p) for p in (getattr(args, "taint_sources", None) or [])]
    cli_sinks = [Path(p) for p in (getattr(args, "taint_sinks", None) or [])]
    cli_sanitizers = [
        Path(p) for p in (getattr(args, "taint_sanitizers", None) or [])
    ]
    claims_sources, claims_sinks, claims_sanitizers = (
        load_extra_catalog_paths(claims_path)
    )
    any_taint_flags = bool(
        cli_sources or cli_sinks or cli_sanitizers
        or claims_sources or claims_sinks or claims_sanitizers
    )

    # INV-nufob: resolve+validate the taint catalog whenever taint flags are
    # present, even with no taint_flow claim to consume them. Previously this
    # block was gated on ``has_taint_claims``, so ``--taint-sources <bad-path>``
    # with boundary-only claims silently fell through to "all CONFIRMED"
    # (exit 0), and a malformed / wrong-shape taint file crashed with an
    # uncaught traceback. A broken taint config is inconclusive (exit 2),
    # never confirmed (0) or violated (1).
    if has_taint_claims or any_taint_flags:
        try:
            taint_catalog = load_full_taint_catalog(
                extra_source_paths=claims_sources,
                extra_sink_paths=claims_sinks,
                extra_sanitizer_paths=claims_sanitizers,
                cli_source_paths=cli_sources,
                cli_sink_paths=cli_sinks,
                cli_sanitizer_paths=cli_sanitizers,
            )
        except (FileNotFoundError, TaintCatalogError) as exc:
            print(f"Error: {exc}", file=sys.stderr)
            return 2

        if any_taint_flags:
            print(
                "Loaded project-local taint catalog: "
                f"{len(cli_sources) + len(claims_sources)} source path(s), "
                f"{len(cli_sinks) + len(claims_sinks)} sink path(s), "
                f"{len(cli_sanitizers) + len(claims_sanitizers)} sanitizer "
                "path(s). CLI --taint-* entries override claims-file "
                "extra_catalogs entries, which override built-in defaults, "
                "on (module, name, kind) match.",
                file=sys.stderr,
            )

        if any_taint_flags and not has_taint_claims:
            # INV-nufob: flags present but no taint_flow claim to consume them.
            # The catalog was validated above (a bad path already errored with
            # exit 2); warn that it is otherwise unused rather than silently
            # ignoring the flags.
            print(
                "Warning: --taint-sources/--taint-sinks/--taint-sanitizers "
                "(or claims-file extra_catalogs) were provided, but no claim "
                "has a taint_flow constraint, so the taint catalog was "
                "validated but not used.",
                file=sys.stderr,
            )

    if has_taint_claims:
        # Build per-language source/sink/sanitizer tables. Running
        # propagation per-language avoids cross-language short-name
        # collisions (e.g., elixir HTTPoison.get matching every Python
        # .get() call) that would otherwise flood the findings with
        # tens of thousands of false positives on multi-language repos.
        per_lang_sources: dict[str, list] = {}
        per_lang_sinks: dict[str, list] = {}
        per_lang_sanitizers: dict[str, list] = {}
        for lang in sorted(languages):
            src_count = len(taint_catalog.sources_for_language(lang))
            snk_count = len(taint_catalog.sinks_for_language(lang))
            if src_count == 0 and snk_count == 0:
                # Neither sources nor sinks for this language — taint-flow
                # cannot meaningfully analyze it. Surface the gap.
                unsupported_taint_languages.append(lang)
                continue
            per_lang_sources[lang] = taint_catalog.sources_for_language(lang)
            per_lang_sinks[lang] = taint_catalog.sinks_for_language(lang)
            per_lang_sanitizers[lang] = taint_catalog.sanitizers_for_language(lang)

        if per_lang_sources and per_lang_sinks:
            # Per-language propagation. For each language, filter edges
            # to that language (both src and dst share the prefix) and
            # run propagation with its sources/sinks. Findings aggregate
            # across languages. Cross-language bridge edges (where src
            # and dst differ in language) are handled by language-pair
            # linkers separately; this taint pass focuses on
            # within-language flow.
            from .taint import propagate_taint_ddg
            from .taint_refine import refine_external_edges
            ddg_edges, ddg_symbols, hints_by_caller = (
                _build_python_ddg_for_verify_claims(repo_root)
            )
            taint_findings = []
            for lang in sorted(per_lang_sinks):
                lang_prefix = f"{lang}:"
                lang_edges = [
                    e for e in raw_edges
                    if e.get("src", "").startswith(lang_prefix)
                    or e.get("dst", "").startswith(lang_prefix)
                ]
                lang_sources = per_lang_sources.get(lang, [])
                lang_sinks = per_lang_sinks[lang]
                lang_sans = per_lang_sanitizers.get(lang, [])
                if not lang_sources or not lang_sinks:
                    continue
                # WI-dilih: rewrite python:external:0-0:NAME:unresolved
                # dsts to module-resolved form before either propagation
                # pass runs. Refinement is per-§1c-extractor; today only
                # Python contributes hints, but the call is unconditional
                # because refine_external_edges is a no-op when no hints
                # exist for the language's edges.
                if lang == "python" and hints_by_caller:
                    lang_edges = refine_external_edges(
                        lang_edges, hints_by_caller,
                    )
                # WI-razol: pass the language's ambiguous short names so a
                # bare ambiguous callee (str.replace, dict.get) with no module
                # hint is not falsely matched to a sink/source.
                lang_ambiguous = taint_catalog.ambiguous_names_for_language(lang)
                if lang == "python" and ddg_edges:
                    taint_findings.extend(propagate_taint_ddg(
                        ddg_edges, lang_edges, lang_sources, lang_sinks,
                        lang_sans, ddg_symbols=ddg_symbols,
                        ambiguous_names=lang_ambiguous,
                    ))
                else:
                    taint_findings.extend(propagate_taint_structural(
                        lang_edges, lang_sources, lang_sinks, lang_sans,
                        ambiguous_names=lang_ambiguous,
                    ))

    # Verify claims
    verdicts = _verify(
        claims, bmap, taint_findings=taint_findings, coverage=coverage,
    )

    # Output
    if getattr(args, "json_output", False):
        # Preserve the legacy flat-list schema for programmatic consumers;
        # INV-javam's unsupported_taint_languages signal goes to stderr to
        # avoid breaking existing pipelines that parse verify-claims JSON.
        print(json.dumps([v.to_dict() for v in verdicts], indent=2))
    else:
        violated = 0
        inconclusive = 0
        for v in verdicts:
            # ADR-0033 Phase 3 PR4: "inconclusive" verdict now exists
            # for claims that couldn't be machine-checked.
            if v.verdict == "confirmed":
                icon = "✓"
            elif v.verdict == "violated":
                icon = "✗"
            else:  # inconclusive
                icon = "?"
            print(f"  {icon} [{v.claim_id}] {v.claim_text}")
            print(f"    Verdict: {v.verdict}")
            if v.details:
                print(f"    {v.details}")
            if v.verdict == "violated":
                violated += 1
            elif v.verdict == "inconclusive":
                inconclusive += 1
        print()
        summary_parts = []
        if violated:
            summary_parts.append(f"{violated} VIOLATED")
        if inconclusive:
            summary_parts.append(f"{inconclusive} INCONCLUSIVE")
        if not violated and not inconclusive:
            summary_parts.append(f"all {len(verdicts)} CONFIRMED")
        print(f"{', '.join(summary_parts)} (of {len(verdicts)} claim(s))")

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

    # ADR-0033 Phase 3 PR4 / WI-rolol sub-task A: exit codes
    #   0 = all confirmed (or no claims to check)
    #   1 = at least one violated
    #   2 = at least one inconclusive (and zero violated) — distinguishes
    #       "machine-checkable claims all passed" from "couldn't actually
    #       check the claim." INV-bitig P0 silent-confirm.
    has_violations = any(v.verdict == "violated" for v in verdicts)
    has_inconclusive = any(v.verdict == "inconclusive" for v in verdicts)
    if has_violations:
        return 1
    if has_inconclusive:
        return 2
    return 0


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

    from .catalog import all_known_languages

    lang = args.language.lower()
    fmt = args.format

    # INV-gufod: `config <X>` reads per-language config, so X must be a known
    # LANGUAGE — not a framework/linker name or a typo. Previously any string
    # was accepted, silently returning all-null sections with only a stderr
    # warning (exit 0), indistinguishable to a script from a real empty config
    # (e.g. `config airflow-framework-dispatch-linker` looked like success).
    # A real language that simply has no config yaml is still valid (it hits
    # the `not found_any` warning below at exit 0); only non-languages error.
    known_languages = all_known_languages()
    if lang not in known_languages:
        import difflib
        close = difflib.get_close_matches(lang, sorted(known_languages), n=3, cutoff=0.5)
        print(
            f"hypergumbo config: error: '{args.language}' is not a known language.",
            file=sys.stderr,
        )
        if close:
            print(f"  Did you mean: {', '.join(close)}?", file=sys.stderr)
        return 2

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


# WI-hular: per-language test-coverage blind spots.
#
# `hypergumbo test-coverage` is a static analysis. Across UAT 2026-04-13
# (4 languages) it had 100% precision but ~20% recall — i.e., 80% of
# actually-tested functions were correctly recognised, and ~20% of
# tested functions were incorrectly reported as untested. The dispatch
# patterns below are the documented per-language sources of that
# recall gap. Surfacing them in --help, the human output footer, and
# the JSON ``caveats`` field gives users a concrete reason to discount
# raw "untested" counts before acting on them.
_TEST_COVERAGE_PER_LANGUAGE_CAVEATS: dict[str, str] = {
    "java": (
        "Java: Spring MockMvc / Spring test runners and JUnit @ParameterizedTest "
        "providers dispatch through reflection and look like indirection from a "
        "static slice. Controllers exercised only via MockMvc may be reported "
        "untested."
    ),
    "kotlin": (
        "Kotlin: PSI visitor patterns (Detekt / Compiler plugin tests) drive "
        "production code via visitor-pattern reflection that is not visible to "
        "the static call graph. Visitor leaves may be reported untested."
    ),
    "go": (
        "Go: tests that drive code via YAML/JSON reflection (config-driven "
        "table tests, encoding/json round-trip) call deserialization handlers "
        "without a direct call edge. Reflective handlers may be reported "
        "untested."
    ),
    "scala": (
        "Scala: ScalaTest / Specs2 macro-expanded suites and Cats Effect "
        "test runners produce dispatch the static analyzer cannot see. "
        "Effects under IO/Resource may be reported untested."
    ),
    "ruby": (
        "Ruby: RSpec ``described_class`` and Rails fixtures dispatch via "
        "metaprogramming the static analyzer cannot see. Methods called only "
        "through stubbed-class indirection may be reported untested."
    ),
    "python": (
        "Python: pytest fixtures injected by name, ``parametrize`` / "
        "``mark.parametrize`` argument expansion, and patch.object decorators "
        "produce indirect dispatch the static analyzer may not trace. "
        "Fixture-only entry points may be reported untested."
    ),
    "javascript": (
        "JavaScript/Jest: ``describe.each`` / ``it.each``, dynamic "
        "``require``, and ESM tree-shaken re-exports break the static call "
        "graph. Indirect imports may be reported untested."
    ),
    "typescript": (
        "TypeScript/Jest: ``describe.each`` / ``it.each``, dynamic "
        "``require``, and ESM tree-shaken re-exports break the static call "
        "graph. Indirect imports may be reported untested."
    ),
    "csharp": (
        "C#: xUnit ``[Theory]`` / ``[MemberData]`` providers and Moq/NSubstitute "
        "proxy dispatch are reflection-based and invisible to the static call "
        "graph. Methods exercised only through mocks may be reported untested."
    ),
}

_TEST_COVERAGE_RECALL_DISCLAIMER = (
    "test-coverage uses static analysis only and does not execute code. "
    "Empirical false-negative rate ~20% across measured languages "
    "(UAT 2026-04-13, 4 languages, n=hundreds): the tool has high "
    "precision (when it says a function is tested, it is) but limited "
    "recall (some genuinely-tested functions appear in 'Cold Spots' "
    "because the tests reach them via dispatch the static call graph "
    "cannot see). Treat 'untested' as 'unreached by static call graph', "
    "not 'definitely untested'."
)


def _test_coverage_caveats(detected_languages: set[str]) -> dict[str, object]:
    """Return the recall disclaimer and per-language caveats applicable
    to the given detected languages.

    Languages without a documented blind spot are silently skipped so
    output stays focused (no empty caveat lines).
    """
    per_language: dict[str, str] = {}
    for lang in sorted(detected_languages):
        caveat = _TEST_COVERAGE_PER_LANGUAGE_CAVEATS.get(lang)
        if caveat:
            per_language[lang] = caveat
    return {
        "recall_disclaimer": _TEST_COVERAGE_RECALL_DISCLAIMER,
        "per_language": per_language,
    }


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
    behavior_map = load_behavior_map(input_path)
    nodes = behavior_map.get("nodes", [])
    edges = behavior_map.get("edges", [])

    # Build lookup tables
    nodes_by_id = {n["id"]: n for n in nodes}

    # WI-dulav: a symbol counts as a test when EITHER its path matches
    # a known test-path heuristic OR the framework-pattern enrichment
    # layer tagged it with a ``test_*`` concept. The second clause
    # catches the Template-Haskell / QuickCheck pattern where ``prop_*``
    # functions live in the same module as the production code they
    # test and get discovered at compile time via ``$forAllProperties``
    # (shellcheck: 2214 ``prop_*`` functions in ``src/`` got 0% coverage
    # before this). Framework concepts recognised as test: anything with
    # ``concept`` starting with ``test`` — covers ``test_function``,
    # ``test_suite``, ``test_lifecycle``, ``test_fixture``, and
    # language-specific variants emitted by the per-framework YAMLs.
    def _has_test_concept(node: dict) -> bool:
        meta = node.get("meta") or {}
        for c in meta.get("concepts", ()) or ():
            name = c.get("concept") if isinstance(c, dict) else None
            if isinstance(name, str) and name.startswith("test"):
                return True
        return False

    # Identify test symbols (functions/methods in test files OR
    # framework-tagged as tests).
    test_symbols: set[str] = set()
    for node in nodes:
        path = node.get("path", "")
        kind = node.get("kind", "")
        if kind not in ("function", "method"):
            continue
        if _is_test_path(path) or _has_test_concept(node):
            test_symbols.add(node["id"])

    # Identify non-test callable symbols (coverage targets).
    # Tests (by path OR by concept) are never targets — a function
    # cannot simultaneously be the test and the thing-under-test.
    target_symbols: dict[str, dict] = {}
    for node in nodes:
        path = node.get("path", "")
        kind = node.get("kind", "")
        if kind not in ("function", "method"):
            continue
        if _is_test_path(path) or _has_test_concept(node):
            continue
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

    # WI-hular: collect detected languages so caveats only fire for the
    # languages actually present in the analyzed repo.
    detected_languages: set[str] = set()
    for node in nodes:
        lang = node.get("language")
        if lang:
            detected_languages.add(lang)
    caveats = _test_coverage_caveats(detected_languages)

    # Output
    if args.format == "json":
        # JSON output
        output = {
            "schema_version": "0.1.0",
            "view": "test-coverage",
            "caveats": caveats,
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

        # Most-called-from-tests functions. The metric counts inbound
        # call edges from test files, not test functions, so framing it
        # as "redundant tests" inverts the actionable signal (WI-fugut):
        # a high count means a function is widely used in test code,
        # which is the opposite of redundant.
        display_hot = test_dense[:top_n] if top_n else test_dense[:20]
        if display_hot:
            print("\nMost-Called from Tests (functions reached most often by test code)")
            print("-" * 65)
            for density, test_count, loc, target, _ in display_hot:
                name = _format_symbol_display_name(target, target.get("id", ""))
                path = target.get("path", "")
                span = target.get("span", {})
                start = span.get("start_line", 0)
                end = span.get("end_line", 0)
                print(
                    f"  {density:5.2f} edges/LOC  ({test_count:3} test-call edges,"
                    f" {loc:3} LOC)  {path}:{start}-{end}  {name}()"
                )
            print(
                "  (Counts inbound calls from test files; heavy use "
                "means widely-used, not over-tested.)"
            )

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

        # WI-hular: emit the recall disclaimer + per-language blind spots
        # as a footer so users see them every time, not just on --help.
        print("\nCaveats (test-coverage is static analysis only)")
        print("-" * 47)
        print(_TEST_COVERAGE_RECALL_DISCLAIMER)
        per_lang = caveats["per_language"]
        if per_lang:
            print()
            print("Known per-language blind spots in the analyzed repo:")
            for _lang, msg in per_lang.items():
                print(f"  - {msg}")

    # Output summary (to stderr for JSON mode to avoid breaking JSON parsing)
    summary_file = sys.stderr if args.format == "json" else None
    cached_set = {input_path} if was_cached else set()
    artifacts = (generated_files + [input_path]) if not was_cached else [input_path]
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


def production_callables(
    nodes: list[dict],
) -> tuple[dict[str, dict], set[str], set[str]]:
    """Classify function/method symbols for dead-code analysis (docs-prose:F4).

    Returns ``(production_symbols, test_symbols, exported_symbols)``:

    - ``production_symbols`` — non-test function/method nodes keyed by id (the
      dead-code candidate universe; ``dead = production_callables - reachable``).
    - ``test_symbols`` — function/method nodes under a test path.
    - ``exported_symbols`` — production symbols with ``supply_chain.is_exported``
      (public API, reachable by external callers outside the analysis scope —
      WI-zimum).

    Extracted from the inline classification ``cmd_dead_code_maybe`` used to
    build so the seed-cohort math has a single named source of truth.
    """
    production_symbols: dict[str, dict] = {}
    test_symbols: set[str] = set()
    exported_symbols: set[str] = set()
    for node in nodes:
        kind = node.get("kind", "")
        if kind not in ("function", "method"):
            continue
        path = node.get("path", "")
        if _is_test_path(path):
            test_symbols.add(node["id"])
        else:
            production_symbols[node["id"]] = node
            sc = node.get("supply_chain") or {}
            if sc.get("is_exported"):
                exported_symbols.add(node["id"])
    return production_symbols, test_symbols, exported_symbols


def _bfs_reachable(
    seed_ids: set[str], call_graph: dict[str, list[str]],
) -> set[str]:
    """Return all symbol ids reachable from *seed_ids* over *call_graph*."""
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
    return reachable


def cmd_dead_code_maybe(args: argparse.Namespace) -> int:
    """Find potentially dead code: production callables unreachable from seeds.

    Computes: dead = production_callables - reachable_from(seed_set)

    The seed set is selected via ``--seeds`` (default ``production``):
    - ``production``: entrypoints + exported public API (the default headline
      view — dispatch:F2 / 2026-06-10 ruling).
    - ``entrypoints``: CLI mains, HTTP routes, framework hooks only (the strict
      entry-only cohort; surfaced as a disclosure bucket under the default).
    - ``tests``: test functions only.
    - ``exports``: symbols with ``is_exported=True`` (public API, WI-zimum).
    - ``all``: entrypoints + tests + exports.

    ``view_func`` framework-dispatch handlers (WI-vuton) seed every mode. Uses
    BFS over call/dispatches_to/wraps edges; unvisited production callables are
    flagged, ranked by cross-language-hit/shape/FFI signal then LOC. Under the
    default ``production`` view the summary discloses two cohorts the headline
    folds away: ``entrypoint_only_dead`` (the strict ~89%-dead view) and
    ``test_only_reachable`` (functions reachable only once tests are seeded —
    the WI-jufih dead-code-vs-coverage contradiction).
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

    behavior_map = load_behavior_map(input_path)
    nodes = behavior_map.get("nodes", [])
    edges = behavior_map.get("edges", [])
    # Identify production callable symbols (exclude test files); docs-prose:F4.
    production_symbols, test_symbols, exported_symbols = production_callables(nodes)

    if not production_symbols:
        print("No production functions found to analyze.", file=sys.stderr)
        return 0

    # dispatch:F2 — resolve the seed mode. The default is ``production``
    # (entrypoints + exported public API), the 2026-06-10 ruling's headline
    # view: it retires the ~89%-dead entrypoint-only false-positive headline
    # while keeping the strict entrypoint-only view and test-only reachability
    # as labeled disclosure buckets (see the summary). ``--seeds`` defaults to
    # None at the argparse layer so an *omitted* flag (warn-worthy) is
    # distinguishable from an explicit ``--seeds entrypoints``.
    seeds_arg = getattr(args, "seeds", None)
    seeds_defaulted = seeds_arg is None
    seeds_mode = "production" if seeds_defaulted else seeds_arg

    # Component seed sets, composed per mode below. Computed once so the
    # disclosure buckets can re-BFS from alternate cohorts cheaply.
    entrypoint_seed_ids: set[str] = set()
    if seeds_mode in ("production", "entrypoints", "all"):
        from .entrypoints import detect_entrypoints
        from .ir import LEGACY_DESERIALIZED_SENTINEL, Symbol, Edge, Span, _normalize_origin

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
            # WI-higap: this path reconstructs Edges from a previously-saved
            # behavior map. Preserve the original origin / origin_run_id where
            # available, falling back to the deserialization sentinel for
            # legacy maps that pre-date producer fixes.
            ir_edges.append(Edge(
                id=e.get("id", ""),
                src=e.get("src", ""),
                dst=e.get("dst", ""),
                edge_type=e.get("type", "calls"),
                line=e.get("line", 0),
                confidence=e.get("confidence", 0.85),
                origin=_normalize_origin(e.get("origin")) or [LEGACY_DESERIALIZED_SENTINEL],
                origin_run_id=e.get("origin_run_id") or LEGACY_DESERIALIZED_SENTINEL,
            ))

        min_conf = getattr(args, "min_confidence", 0.0)
        for ep in detect_entrypoints(ir_nodes, ir_edges):
            if ep.confidence >= min_conf:
                entrypoint_seed_ids.add(ep.symbol_id)

    # WI-vuton heuristic 2: usage_contexts cross-reference. A symbol that
    # appears as a callable-position (``view_func``) in a usage_context
    # is reached via the framework dispatcher even when no static call
    # edge exists. Route handlers, message-queue handlers, decorator-
    # registered callbacks etc. all surface here; without this seed, they
    # were the single largest FP class in the dead-code-maybe report on
    # hypergumbo self-analysis. Only ``view_func`` (and other future
    # callable-position kinds) seed the BFS — pure name references
    # (``arg_value``) do not represent dispatch sites and should NOT
    # produce reachability claims. Seeded in every mode.
    _CALLABLE_POSITIONS = frozenset({"view_func"})
    view_func_seed_ids: set[str] = set()
    for uc in behavior_map.get("usage_contexts", []) or []:
        if uc.get("position") not in _CALLABLE_POSITIONS:
            continue
        ref = uc.get("symbol_ref")
        if ref and ref in production_symbols:
            view_func_seed_ids.add(ref)

    # Compose the active seed set for the resolved mode.
    seed_ids: set[str] = set(view_func_seed_ids)
    if seeds_mode in ("production", "entrypoints", "all"):
        seed_ids |= entrypoint_seed_ids
    if seeds_mode in ("production", "exports", "all"):
        seed_ids |= exported_symbols
    if seeds_mode in ("tests", "all"):
        seed_ids |= test_symbols

    # BFS from seeds through call-flow edges.
    # calls:          direct function/method calls (post-Phase-3, also covers
    #                 FFI/IPC/RPC bridges via meta['bridge_kind']/['protocol'])
    # dispatches_to:  interface/abstract method → concrete implementation
    #                 (post-Phase-3, also covers HTTP routes via
    #                 meta['dispatch_kind']='route')
    # wraps:          middleware wrapper → inner handler
    _REACHABILITY_EDGE_TYPES = {"calls", "dispatches_to", "wraps"}
    call_graph: dict[str, list[str]] = {}
    for edge in edges:
        if edge.get("type") in _REACHABILITY_EDGE_TYPES:
            src = edge.get("src", "")
            dst = edge.get("dst", "")
            if src and dst:
                call_graph.setdefault(src, []).append(dst)

    reachable = _bfs_reachable(seed_ids, call_graph)

    # dispatch:F2 disclosure buckets (computed for the production headline
    # view). ``entrypoint_only_dead`` re-exposes the strict ~89%-dead cohort
    # the default used to print; ``test_only_reachable`` names production
    # functions dead under the production seeds yet reachable once test code is
    # also seeded — the WI-jufih dead-code-vs-coverage contradiction, disclosed
    # rather than silently absorbed.
    production_keys = set(production_symbols)
    entrypoint_only_dead: int | None = None
    test_only_reachable: int | None = None
    if seeds_mode == "production":
        reachable_entrypoint_only = _bfs_reachable(
            entrypoint_seed_ids | view_func_seed_ids, call_graph,
        )
        entrypoint_only_dead = len(production_keys - reachable_entrypoint_only)
        reachable_with_tests = _bfs_reachable(seed_ids | test_symbols, call_graph)
        test_only_reachable = len(
            (reachable_with_tests - reachable) & production_keys,
        )

    # Dead candidates = production symbols NOT reachable
    dead_candidates = []
    exclude_annotated = getattr(args, "exclude_annotated", False)
    exclude_exports = getattr(args, "exclude_exports", False)

    # WI-rumij: propagate class-level annotations to contained methods.
    # Spring frameworks typically annotate the controller class (@Controller,
    # @RestController, @Service) without re-annotating each handler method,
    # so methods report no annotations and slip past --exclude-annotated.
    # Build a class-meta-index keyed by class symbol ID, then for each
    # method, also check its containing class's meta when the method's own
    # meta is empty.
    class_meta_by_id: dict[str, dict] = {}
    for node in nodes:
        if node.get("kind") in ("class", "interface", "struct", "trait", "enum"):
            class_meta_by_id[node["id"]] = node.get("meta") or {}
    method_to_class: dict[str, str] = {}
    for edge in edges:
        if edge.get("type") == "contains":
            src = edge.get("src", "")
            dst = edge.get("dst", "")
            if src in class_meta_by_id and dst:
                method_to_class[dst] = src

    def _has_annotation_signal(meta: dict) -> bool:
        return bool(meta.get("decorators") or meta.get("annotations")
                    or meta.get("concepts"))

    for sym_id, node in production_symbols.items():
        if sym_id not in reachable:
            # WI-jifup: symbols in generated files are never actionable
            # dead-code targets — you regenerate them, not delete them
            # manually. Unconditional drop (no opt-in flag) because there
            # is no use case in which the user wants "dead generated
            # code" surfaced. Closes the residual leak in openapi-gen
            # utility files (CancelablePromise.ts, request.ts, OpenAPI.ts,
            # ApiError.ts) that bypassed the ranking-side centrality
            # penalty (WI-tizij / WI-vubad) because dead-code-maybe does
            # not use centrality at all.
            sc = node.get("supply_chain") or {}
            if sc.get("is_generated_file"):
                continue
            # --exclude-annotated: skip candidates with decorators,
            # annotations, or framework concepts (these are likely
            # framework-registered callbacks, not linker gaps).
            if exclude_annotated:
                meta = node.get("meta") or {}
                if _has_annotation_signal(meta):
                    continue
                # WI-rumij: check containing class's annotations too
                parent_class_id = method_to_class.get(sym_id)
                if parent_class_id is not None:
                    parent_meta = class_meta_by_id.get(parent_class_id, {})
                    if _has_annotation_signal(parent_meta):
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

    # WI-vuton heuristic 1: polymorphic dispatch demotion. A method
    # ``Sub.foo`` with zero in-edges in the static call graph is almost
    # certainly reached via virtual dispatch whenever a base-class method
    # ``Base.foo`` IS reachable — callers go through the base statically
    # and the runtime picks the override. Pre-fix, every override of a
    # reachable interface method (the most architecturally important
    # code) was flagged dead.
    #
    # Implementation: walk ``extends`` / ``inherits`` / ``implements``
    # edges to find each class's transitive ancestors; for each dead
    # method, look for a same-named (last-segment match) method on any
    # ancestor class that IS reachable. The last-segment match is
    # agnostic of the analyzer's qualified-name convention (e.g.
    # ``Cache.delete`` → ``delete``).
    _INHERITANCE_EDGE_TYPES = frozenset({"extends", "inherits", "implements"})
    extends_graph: dict[str, set[str]] = {}
    for edge in edges:
        if edge.get("type") in _INHERITANCE_EDGE_TYPES:
            esrc = edge.get("src", "")
            edst = edge.get("dst", "")
            if esrc and edst:
                extends_graph.setdefault(esrc, set()).add(edst)

    class_to_methods: dict[str, list[dict]] = {}
    for edge in edges:
        if edge.get("type") != "contains":
            continue
        csrc = edge.get("src", "")
        cdst = edge.get("dst", "")
        if csrc not in class_meta_by_id or not cdst:
            continue
        method_node = production_symbols.get(cdst)
        if method_node is None or method_node.get("kind") != "method":
            continue
        class_to_methods.setdefault(csrc, []).append(method_node)

    def _ancestors_of(class_id: str) -> set[str]:
        seen: set[str] = set()
        stack = [class_id]
        while stack:
            cur = stack.pop()
            for parent in extends_graph.get(cur, ()):
                if parent not in seen:
                    seen.add(parent)
                    stack.append(parent)
        return seen

    def _method_basename(name: str) -> str:
        # WI-kilal: handle Ruby's ``Class#method`` separator in addition to
        # Python/Java/JS's ``Class.method``. Cross-language analyzers use
        # different conventions: Python ``ClassA.foo``, Ruby
        # ``ClassA#foo``. Strip whichever separator appears last in the
        # qualified name so the heuristic can match same-named methods
        # across an inheritance chain regardless of language.
        if not name:  # pragma: no cover - defensive: production callable Symbols always carry a non-empty name (matches the same guard at the caller a few lines below)
            return ""
        last_sep = max(name.rfind("."), name.rfind("#"))
        return name[last_sep + 1:] if last_sep >= 0 else name

    dispatch_inherited_ids: set[str] = set()
    for node in dead_candidates:
        if node.get("kind") != "method":
            continue
        class_id = method_to_class.get(node["id"])
        if not class_id:
            continue
        method_name = _method_basename(node.get("name", ""))
        if not method_name:  # pragma: no cover
            # Defensive guard: production callable Symbols always carry a
            # non-empty name (Span+name are required at construction).
            continue
        for ancestor_id in _ancestors_of(class_id):
            matched = False
            for ancestor_method in class_to_methods.get(ancestor_id, ()):
                if (
                    _method_basename(ancestor_method.get("name", "")) == method_name
                    and ancestor_method["id"] in reachable
                ):
                    dispatch_inherited_ids.add(node["id"])
                    matched = True
                    break
            if matched:
                break

    if dispatch_inherited_ids:
        dead_candidates = [
            n for n in dead_candidates if n["id"] not in dispatch_inherited_ids
        ]

    # Cross-language string collision: check if dead candidate names
    # appear as substrings in files of a different language.  A hit is
    # a near-certain signal of a missing cross-language reference
    # (HTTP path, RPC method, MQ topic, FFI name).
    cross_lang_hits: dict[str, int] = {}
    if dead_candidates:
        cross_lang_hits = _compute_cross_language_hits(
            dead_candidates, repo_root,
        )

    # dispatch:F7 (WI-gavub): consume cross_language_hits as a false-positive
    # DEMOTER, not merely a rank signal. A candidate whose name appears as a
    # string in >= ``--cross-lang-threshold`` other-language files is
    # near-certainly reached via a cross-language path the static call graph
    # misses (framework dispatch like Lit's ``*.render``, an HTTP route, an RPC
    # method, an FFI name) — i.e. NOT dead. The threshold stays > 1 so a single
    # coincidental hit does not exclude (some genuine dead code has low CLH); a
    # threshold <= 0 disables the demoter. Mirrors the dispatch_inherited_ids
    # demoter above (a hard exclusion: no per-candidate dead-confidence score
    # exists to demote proportionally yet).
    cross_lang_threshold = getattr(args, "cross_lang_threshold", 3)
    cross_lang_demoted_ids: set[str] = set()
    if cross_lang_threshold > 0:
        cross_lang_demoted_ids = {
            n["id"] for n in dead_candidates
            if cross_lang_hits.get(n["id"], 0) >= cross_lang_threshold
        }
        if cross_lang_demoted_ids:
            dead_candidates = [
                n for n in dead_candidates
                if n["id"] not in cross_lang_demoted_ids
            ]

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

    if seeds_defaulted:
        print(
            "note: defaulting to --seeds production (entrypoints + exported "
            "public API; dispatch:F2). Use --seeds entrypoints for the strict "
            "entry-only view or --seeds all to also seed tests; the summary's "
            "entrypoint_only_dead / test_only_reachable buckets disclose both "
            "cohorts the default folds away.",
            file=sys.stderr,
        )

    if args.format == "json":
        output = {
            "summary": {
                "total_production_functions": total_production,
                "reachable_functions": total_reachable,
                "dead_candidates": total_dead,
                "seed_count": total_entrypoints,
                "seeds_mode": seeds_mode,
                "seeds_defaulted": seeds_defaulted,
                "dead_percent": round(total_dead / max(total_production, 1) * 100, 1),
                # dispatch:F2 disclosure buckets (non-null only in the
                # production headline view): the strict entrypoint-only dead
                # count, and production functions reachable only once tests are
                # seeded (WI-jufih). null under explicit non-default modes.
                "entrypoint_only_dead": entrypoint_only_dead,
                "test_only_reachable": test_only_reachable,
                # WI-vuton: how many symbols entered the reachable set via
                # framework-dispatch usage_contexts (view_func position) and
                # how many methods were demoted from dead via inheritance-
                # based virtual-dispatch reasoning. Surfaces the FP-class
                # corrections so consumers can audit them.
                "demoted_view_func": len(view_func_seed_ids),
                "demoted_dispatch_inherited": len(dispatch_inherited_ids),
                # dispatch:F7 (WI-gavub): candidates excluded because their name
                # appears in >= cross_lang_threshold other-language files
                # (cross-language dispatch / missing-edge false positives).
                "demoted_cross_language": len(cross_lang_demoted_ids),
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
        if seeds_mode == "production":
            # dispatch:F2 disclosure buckets.
            print(f"  entrypoint-only view would flag: {entrypoint_only_dead}")
            print(f"  reachable only from tests:       {test_only_reachable}")
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


def _positive_result_limit(raw: str) -> int:
    """argparse type for a result-count ``--limit``: require a positive integer.

    INV-toniv: ``search --limit -5`` was silently interpreted as Python
    tail-drop slicing (``matches[:-5]``), and ``--limit 0`` fell through the
    falsy guard and was treated as "no limit". Both are configuration errors —
    a result limit must be >= 1 — and should fail fast with a clear message.
    """
    try:
        value = int(raw)
    except (TypeError, ValueError) as exc:
        raise argparse.ArgumentTypeError(
            f"--limit must be a positive integer, got {raw!r}"
        ) from exc
    if value <= 0:
        raise argparse.ArgumentTypeError(
            f"--limit must be a positive integer, got {value}"
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
        help="Enable debug logging (verbose internal diagnostics)",
    )
    p.add_argument(
        "--backend",
        choices=["tree-sitter", "rust-analyzer"],
        default=None,
        help=(
            "Select the Rust analysis backend. 'rust-analyzer' activates the "
            "SCIP-backed analyzer (requires 'hypergumbo install-rust-analyzer'). "
            "Default: tree-sitter (respects HYPERGUMBO_RUST_ANALYZER if set)."
        ),
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
        "--no-comparison-sketches",
        action="store_true",
        dest="no_comparison_sketches",
        help="Skip the 4x/16x comparison sketches and the representativeness "
             "table (faster; useful for batch/scripted single-budget runs)",
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
  hypergumbo run . --out analysis.json  # Custom output file (plus side-outputs, see below)
  hypergumbo run . --out analysis.json --budgets none --no-handler-slices
                                        # Single output file (no side-outputs)
  hypergumbo run . --compact            # LLM-friendly: top symbols + summary
  hypergumbo run . --first-party-only   # Exclude vendored/external code

Side-outputs alongside --out:
  In addition to the path you pass, `run` writes:
    <stem>.4k.json / .16k.json / .64k.json   compact-tier previews at the same
                                              prefix as --out (see --budgets
                                              or --no-sketch-fan-out)
    <stem>.slices/                            a subdirectory of per-route
                                              handler slices and an index
                                              (see --no-handler-slices,
                                              --max-handler-slices)
  Suppress everything with: --no-sketch-fan-out --no-handler-slices
  (Equivalent: --budgets none --no-handler-slices.)
  Compress all JSON outputs with: --gzip --out foo.json.gz (raw JSON is
  ~95% gzip-reducible on large repos).

After running, use search/explain/slice to query the results:
  hypergumbo sketch .                   # Auto-discovers cached results
  hypergumbo search "parse"             # Find symbols containing "parse"
  hypergumbo explain "main"             # Show callers/callees of main
  hypergumbo slice --entry main         # Extract subgraph from main()

Cache location:
  ~/.cache/hypergumbo/<repo-fingerprint>/results/<state-hash>/<analyzer-identity>/
  Results are cached per repo state AND per analyzer identity (so
  dev edits and stable releases don't poison each other's cache).
  Auto-invalidated when files change."""

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
        help="Output JSON path (default: ~/.cache/hypergumbo/<repo>/<state>/). "
             "Side-outputs are written alongside: compact-tier previews "
             "(<stem>.{4k,16k,64k}.json — see --budgets or "
             "--no-sketch-fan-out) at the same prefix, and a "
             "<stem>.slices/ subdirectory of per-route handler slices "
             "(see --no-handler-slices, --max-handler-slices). Pass "
             "`--no-sketch-fan-out --no-handler-slices` to get exactly "
             "one output file. Pair with `--gzip` to write gzipped "
             "JSON. See the epilog for the full layout.",
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
    p_run.add_argument(
        "--no-handler-slices",
        action="store_true",
        dest="no_handler_slices",
        help="Disable per-route-handler forward slices (WI-sihok). By default "
             "`run` emits slice.handler.<METHOD>.<path>.json for each "
             "detected handler into a ``<out-stem>.slices/`` subdirectory next "
             "to --out (capped at --max-handler-slices). Use this flag to "
             "skip the extra files entirely.",
    )
    p_run.add_argument(
        "--max-handler-slices",
        type=int,
        default=_DEFAULT_MAX_HANDLER_SLICES,
        metavar="N",
        help=f"Maximum per-handler forward slices to emit (default: "
             f"{_DEFAULT_MAX_HANDLER_SLICES}). Overflow handlers are listed in "
             f"the ``<out-stem>.slices/slice.handler.index.json`` companion "
             f"file with pointers to re-derive on demand.",
    )
    # WI-kojob: large repos produce 300MB-500MB raw JSON outputs that
    # gzip to ~5% of their uncompressed size (airflow 320MB → 18MB,
    # kafka 572MB → 34MB). `--gzip` writes the main output and any
    # budget-tier outputs as `.gz`; pair with `--out foo.json.gz`.
    p_run.add_argument(
        "--gzip",
        action="store_true",
        dest="gzip_output",
        default=False,
        help="Write the main output and any budget-tier outputs as "
             "gzipped JSON. Pair with `--out foo.json.gz`. Downstream "
             "tools like `zcat foo.json.gz | jq .` round-trip cleanly.",
    )
    # WI-kojob: `--no-sketch-fan-out` is an explicit named alias for
    # `--budgets none` — surfaced in argparse so it shows up in --help
    # alongside `--no-handler-slices` (the symmetric side-output
    # suppressor).
    p_run.add_argument(
        "--no-sketch-fan-out",
        action="store_true",
        dest="no_sketch_fan_out",
        default=False,
        help="Skip emission of the precomputed sketch-tier preview "
             "files (`<stem>.{4k,16k,64k}.json`). Equivalent to "
             "`--budgets none`; wins over an explicit `--budgets ...` "
             "value.",
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
        help="Entrypoint to slice from: symbol name, file path, node ID, "
             "'module:name' shorthand (e.g. cli:main), or 'auto' to detect "
             "automatically (default: auto)",
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
        type=_positive_result_limit,
        default=20,
        help="Maximum number of results to show; must be a positive integer "
             "(default: 20). The header always reports the total match count.",
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
    p_routes.add_argument(
        "--format",
        choices=["text", "json"],
        default="text",
        help="Output format (default: text). JSON goes to stdout; the run "
             "summary goes to stderr so stdout stays machine-parseable.",
    )
    p_routes.set_defaults(func=cmd_routes)

    # hypergumbo explain
    explain_epilog = """\
Examples:
  hypergumbo explain "main"               # Show what main calls and is called by
  hypergumbo explain "UserService"        # Explain a class
  hypergumbo explain "parse_config"       # Explain a specific function
  hypergumbo explain "foo" --provenance   # Include derivation chains per edge

Shows: Symbol location, origin passes, callers (what calls it), callees (what it calls).
Edge types are shown inline. Use --provenance to see which symbols each linker
consumed to construct each edge (PROV wasDerivedFrom).

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
    p_explain.add_argument(
        "--provenance",
        action="store_true",
        default=False,
        dest="provenance",
        help="Show derivation chains (derived_from) for each edge",
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
        help="Build tree-sitter grammars from source (Lean, Wolfram, Circom)",
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

    # hypergumbo install-rust-analyzer
    p_install_ra = sub.add_parser(
        "install-rust-analyzer",
        help="Install rust-analyzer (via rustup) for the SCIP-backed Rust analyzer",
    )
    p_install_ra.add_argument(
        "--check",
        action="store_true",
        help="Check if rust-analyzer is installed without installing",
    )
    p_install_ra.add_argument(
        "--quiet",
        action="store_true",
        help="Suppress output",
    )
    p_install_ra.set_defaults(func=cmd_install_rust_analyzer)

    # hypergumbo uninstall-rust-analyzer
    p_uninstall_ra = sub.add_parser(
        "uninstall-rust-analyzer",
        help="Uninstall rust-analyzer (removes the rustup component if present)",
    )
    p_uninstall_ra.add_argument(
        "--quiet",
        action="store_true",
        help="Suppress output",
    )
    p_uninstall_ra.set_defaults(func=cmd_uninstall_rust_analyzer)

    # hypergumbo cache-status
    cache_status_epilog = """\
Examples:
  hypergumbo cache-status              # Aggregate report + honk if over threshold
  hypergumbo cache-status --per-repo   # Per-repo breakdown (find the bloat source)

The honk-threshold warning fires when total cache size exceeds 1.0 GB.
Configure via HYPERGUMBO_CACHE_HONK_GB=<N> (set to 0 to silence)."""
    p_cache_status = sub.add_parser(
        "cache-status",
        help="Show cache status and statistics",
        epilog=cache_status_epilog,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p_cache_status.add_argument(
        "--per-repo",
        action="store_true",
        help="List size, entry count, and last-used time per repo subdirectory",
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
  hypergumbo cache-clear                              # Clear entire cache
  hypergumbo cache-clear --older-than 7               # Clear entries older than 7 days
  hypergumbo cache-clear --dry-run                    # Preview what would be deleted
  hypergumbo cache-clear --repo <id>                  # Clear one repo's entire subtree
  hypergumbo cache-clear --repo <id> --keep-latest 5  # Keep 5 newest state entries

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
        "--repo",
        type=str,
        metavar="FINGERPRINT",
        help="Restrict deletion to one repo's subtree (top-level fingerprint dir name)",
    )
    p_cache_clear.add_argument(
        "--keep-latest",
        type=int,
        metavar="N",
        help=(
            "With --repo: keep the N most recent state-hash entries under "
            "<repo>/results/ and delete the rest"
        ),
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

    # hypergumbo add-extras (single umbrella; consolidated from former
    # install-extras umbrella per WI-josif).
    p_add_extras = sub.add_parser(
        "add-extras",
        help="Install all optional extras (grammars, gitleaks, embeddings, rust-analyzer)",
    )
    p_add_extras.add_argument(
        "--check",
        action="store_true",
        help="Print availability table and exit; exit 1 iff any component is missing",
    )
    p_add_extras.add_argument(
        "--skip",
        default=None,
        metavar="COMPONENTS",
        help=(
            "Comma-separated list of components to skip "
            "(grammars,gitleaks,embeddings,rust-analyzer)"
        ),
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
        help="Uninstall optional extras (gitleaks, embeddings, rust-analyzer)",
    )
    p_remove_extras.add_argument(
        "--skip",
        default=None,
        metavar="COMPONENTS",
        help=(
            "Comma-separated list of components to skip "
            "(grammars,gitleaks,embeddings,rust-analyzer)"
        ),
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

Recall caveat (WI-hular): empirical false-negative rate ~20% across
4 languages measured in UAT 2026-04-13. The tool has high precision
but limited recall: tests that reach production code via reflection,
dispatch macros, or visitor patterns produce 'untested' false alarms.
Known per-language blind spots are listed in every text-format run as
a footer, and in JSON output under the 'caveats' field. Treat
'untested' as 'unreached by static call graph', not 'definitely
untested', before taking action on the cold-spot list.

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
        "--seeds", choices=["production", "entrypoints", "tests", "exports", "all"],
        default=None,
        help="Seed set for reachability analysis (default: production = "
             "entrypoints + exported public API; dispatch:F2). 'entrypoints' is "
             "the strict entry-only view (also disclosed as a bucket under the "
             "default). 'exports' uses symbols with is_exported=True. 'all' "
             "combines entrypoints, tests, and exports. Omitting the flag prints "
             "a note and the entrypoint-only / test-only-reachable buckets.",
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
    p_dead_code.add_argument(
        "--cross-lang-threshold", type=int, default=3,
        help="dispatch:F7 (WI-gavub): demote (exclude) candidates whose name "
             "appears in >= N other-language files — near-certain cross-language "
             "dispatch / missing-edge false positives (default: 3). Set <= 0 to "
             "disable the demoter.",
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
  hypergumbo symbols --col-width 200        # Wider Symbol/File columns
  hypergumbo symbols --wrap                 # Wrap long names instead of truncating

Output: Rich table with columns Symbol, Kind, In (in-degree), Out (out-degree),
Deg (total degree), File. Symbol and File columns default to 60 / 80 chars
(use --col-width to override, --wrap to fold long content across lines).
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
    p_symbols.add_argument(
        "--col-width",
        type=int,
        default=None,
        dest="col_width",
        metavar="N",
        help=(
            "Width (chars) for Symbol and File columns. Default: 60 / 80. "
            "Capped at 1000."
        ),
    )
    p_symbols.add_argument(
        "--wrap",
        action="store_true",
        dest="wrap",
        help=(
            "Wrap long names/paths across lines instead of truncating "
            "with an ellipsis."
        ),
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
    # WI-mibag: the `external_potential` bucket dominates display on
    # large repos (kafka: 76K of 76K; airflow: 28K of ~30K). Suppress
    # it from the default text-output view; JSON output continues to
    # include it unconditionally for downstream tools / agents.
    # `--boundary external_potential` overrides the suppression too
    # since targeted filtering is its own opt-in.
    p_io.add_argument(
        "--show-external-potential",
        action="store_true",
        dest="show_external_potential",
        default=False,
        help=(
            "Include the `external_potential` bucket in text output "
            "(hidden by default since it tends to dominate the per-"
            "primitive view; JSON output and `--boundary "
            "external_potential` always include it)."
        ),
    )
    p_io.set_defaults(func=cmd_io_boundaries)

    # hypergumbo verify-claims
    p_config = sub.add_parser(
        "config",
        help="Show per-language configuration (dataflow, IO, function summaries)",
        epilog=(
            "`config` prints the built-in per-language analysis configuration "
            "(dataflow patterns, I/O primitives, function summaries) that ships "
            "with hypergumbo for the named LANGUAGE. It does not analyze a "
            "repository or read a behavior-map substrate, so it accepts neither "
            "a path nor `--input` (unlike the analysis subcommands)."
        ),
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

    verify_claims_epilog = """\
Claims file format (YAML):

  claims:
    - id: SC-001                  # required: unique identifier
      text: No network sends      # required: human-readable description
      constraint:                 # one of two constraint shapes:
        # (a) boundary constraint (ADR-0016):
        boundary: net_send        #   one of: env_read, external_potential,
        must_not_exist: true      #   fs_read, fs_write, ipc_recv, ipc_send,
        # max_chains: 5           #   logging, net_recv, net_send, subprocess,
                                  #   db_read, db_write, env_write,
                                  #   process_send, browser_storage_read/write
        # (b) taint-flow constraint (ADR-0017):
        # taint_flow:
        #   source_taint: untrusted_input
        #   prohibited_sink_zone: host_fs
        #   allowed_sanitizers: []

A top-level `extra_catalogs:` key may declare project-local taint catalogs
(see --taint-sources/--taint-sinks/--taint-sanitizers; WI-votan).

The claims file is validated up front: a malformed YAML, an unexpected
shape, an unknown field name, or a boundary value outside the vocabulary
above produces a clear error and exit code 2 (not a silent pass).

Exit codes: 0 = all confirmed; 1 = at least one violated; 2 = at least one
inconclusive, or the claims file failed validation.
"""
    p_vc = sub.add_parser(
        "verify-claims",
        help="Verify security claims against I/O boundary map and taint flow",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=verify_claims_epilog,
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
    p_vc.add_argument(
        "--taint-sources",
        action="append",
        default=None,
        metavar="PATH",
        help=(
            "Project-local taint source YAML file or directory. "
            "Repeatable. Entries whose (module, name, kind) matches an "
            "auto-derived or built-in source are overridden. (WI-votan)"
        ),
    )
    p_vc.add_argument(
        "--taint-sinks",
        action="append",
        default=None,
        metavar="PATH",
        help=(
            "Project-local taint sink YAML file or directory. "
            "Repeatable. Entries whose (module, name, kind) matches an "
            "auto-derived or built-in sink are overridden. (WI-votan)"
        ),
    )
    p_vc.add_argument(
        "--taint-sanitizers",
        action="append",
        default=None,
        metavar="PATH",
        help=(
            "Project-local taint sanitizer YAML file or directory. "
            "Repeatable. User sanitizers concatenate onto the built-in "
            "list. (WI-votan)"
        ),
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
                   "install-embeddings", "uninstall-embeddings",
                   "install-rust-analyzer", "uninstall-rust-analyzer"]
    for i, cmd in enumerate(extras_cmds):
        _set_subparser_group(sub, cmd, "extras", 1, suborder=i)

    # Build metavar dynamically from the registered subparsers in their
    # declared group/suborder. Previously this was a hardcoded string that
    # silently drifted when new subcommands were added — config,
    # dead-code-maybe, verify-claims, cache-status, and cache-clear were
    # all omitted from `hypergumbo --help` (WI-zunos).
    sub.metavar = "{" + ",".join(
        name for name, _subparser, _group, _is_new_group
        in _get_subparsers_by_group(sub)
    ) + "}"

    return p


# ADR-0027 Phase-2 audit (WI-jukav): all AXIS_PENDING (Cluster G —
# build/config-shape). Forward-compatibility verdict gates on the
# Cluster G audit-findings outcome (already landed in audit-findings
# 0006 with promotions to language_construct, but the registry
# axis-update is Wave 6 follow-through per WI-runod). Until that
# registry update lands, this set is forward-compatible by virtue of
# none of its values being scheduled for fold/rename in Phase 3.
_DEPENDENCY_KINDS = frozenset({"dependency"})


def _classify_symbols(
    symbols: list[Symbol],
    repo_root: Path,
    package_roots: set[Path],
    limits: "Limits | None" = None,
) -> None:
    """Apply supply chain classification to symbols in-place.

    Classifies each symbol's file path and updates supply_chain_tier
    and supply_chain_reason fields.  Symbols that already have a tier
    set by a linker (e.g. npm_package with tier=3) are not reclassified
    — the linker's tier takes precedence.

    Dependency-kind symbols (from Cargo.toml, package.json, etc.) are
    classified as tier 3 (EXTERNAL_DEP) since they represent references
    to external packages, not first-party code.

    INV-virik: when ``limits`` is supplied, ``classify_file`` failures
    (the default-fallback "outside repo" classification, or any uncaught
    ValueError from ``Path.relative_to``) are recorded into
    ``limits.supply_chain.classification_failures`` via
    ``Limits.add_classification_failure``. Pre-Phase-6, the schema
    declared this field but no producer ever wrote to it.
    """
    seen_failures: set[str] = set()
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
        symbol.is_example_file = classification.is_example
        symbol.is_config_file = classification.is_config
        symbol.is_generated_file = classification.is_generated
        # WI-zimum: fold in modifier-derived export signal. The analyzer
        # may have already set Symbol.is_exported at extraction time
        # (WI-gipag: Python __all__; WI-nimug / WI-zimum Phase 2b: TS/JS
        # ``export`` keyword); this step additionally picks up modifier-
        # based signals for Go ("exported"), Rust ("pub"/"pub(...)"),
        # and languages that emit "public" via visibility_from_modifiers.
        symbol.is_exported = (
            symbol.is_exported
            or is_exported_from_modifiers(symbol.modifiers)
        )
        # INV-virik: a fall-through to the "outside repo" default-bucket
        # classification means the path didn't match ANY tier policy.
        # Record it as a classification failure so consumers can see the
        # gap in the limits.supply_chain.classification_failures list.
        if limits is not None and "outside repo" in classification.reason:
            if symbol.path not in seen_failures:
                seen_failures.add(symbol.path)
                limits.add_classification_failure(
                    path=symbol.path,
                    reason=classification.reason,
                )


def _make_ecosystem_classifier() -> Callable[[str, str], Optional[str]]:
    """Build the ADR-0041 §3 ecosystem classifier for boundary nodes.

    Returns a callable ``(language, module) -> 'stdlib' | 'third_party' | None``
    backed by the single-source language stdlib catalog
    (``io_boundary.load_catalog`` / ``IoBoundaryCatalog.is_stdlib_module`` — the
    same catalog the io-boundary closed-world gates consume, per ADR-0041 §3's
    single-source constraint). Returns ``None`` when the language has no
    enumerated stdlib, so an unmatched module is never mislabelled
    ``third_party`` on a language whose stdlib set we don't know. Catalogs are
    loaded lazily and cached per language.
    """
    from .io_boundary import load_catalog
    cache: Dict[str, Any] = {}

    def classify(language: str, module: str) -> Optional[str]:
        if language not in cache:
            cat = load_catalog(language)
            # Usable only when the catalog enumerates the stdlib; otherwise
            # "not in set" is indistinguishable from "stdlib set unknown".
            cache[language] = (
                cat if (cat.stdlib_modules or cat.stdlib_prefixes) else None
            )
        cat = cache[language]
        if cat is None:
            return None
        return "stdlib" if cat.is_stdlib_module(module) else "third_party"

    return classify


def _compute_supply_chain_summary(
    symbols: list[Symbol], derived_paths: list[str]
) -> Dict[str, Any]:
    """Compute supply chain summary from classified symbols.

    Returns a dict with counts per tier plus derived_skipped info. Tier-3
    (external_dep) carries an ``ecosystem`` sub-bucket counting symbols by the
    ADR-0041 §3 ``ecosystem`` provenance class (stdlib / third_party / unknown).
    """
    # Count unique files and symbols per tier
    tier_files: Dict[int, set] = {1: set(), 2: set(), 3: set(), 4: set()}
    tier_symbols: Dict[int, int] = {1: 0, 2: 0, 3: 0, 4: 0}
    # ADR-0041 §3: sub-bucket tier-3 externals by ecosystem provenance class.
    ecosystem_counts: Dict[str, int] = {}

    for symbol in symbols:
        tier = symbol.supply_chain_tier
        tier_files[tier].add(symbol.path)
        tier_symbols[tier] += 1
        if tier == 3:
            eco = (symbol.meta or {}).get("ecosystem") or "unknown"
            ecosystem_counts[eco] = ecosystem_counts.get(eco, 0) + 1

    tier_names = {1: "first_party", 2: "internal_dep", 3: "external_dep"}

    summary: Dict[str, Any] = {}
    for tier, name in tier_names.items():
        summary[name] = {
            "files": len(tier_files[tier]),
            "symbols": tier_symbols[tier],
        }
    # Attach the ecosystem breakdown to the external_dep tier (sorted for
    # deterministic output).
    summary["external_dep"]["ecosystem"] = dict(sorted(ecosystem_counts.items()))

    # Cap derived_skipped paths at 10
    summary["derived_skipped"] = {
        "files": len(tier_files[4]) + len(derived_paths),
        "paths": derived_paths[:10],
    }

    return summary


# ---------------------------------------------------------------------------
# Handler slices (WI-sihok): per-route-handler forward slices emitted by `run`
# ---------------------------------------------------------------------------

# Default cap on per-handler forward slices written alongside the behavior
# map. Matches the philosophy of the tiered sketches (hg.4k/16k/64k.json):
# bounded output without requiring manual selection. Users with more
# handlers can raise the cap via --max-handler-slices or consult
# slice.handler.index.json for the overflow list.
_DEFAULT_MAX_HANDLER_SLICES = 25

# Forward-slice parameters for per-handler slices, matching the tuned
# config used by the bakeoff's top-entry forward slices (see
# scripts/bakeoff-deep around line 2060 and WI-sivun for the hub-threshold
# choice).
_HANDLER_SLICE_MAX_HOPS = 10
_HANDLER_SLICE_MAX_FILES = 200
_HANDLER_SLICE_HUB_THRESHOLD = 100


def _is_route_symbol(symbol: Symbol) -> bool:
    """Return True if the symbol represents a route/handler.

    Uses the same detector as cmd_routes: symbols with kind='route' (produced
    by analyzers that materialize routes directly, e.g. Go) OR symbols whose
    meta.concepts list contains a concept='route' entry (produced by
    framework-YAML concept enrichment, e.g. FastAPI @app.get).
    """
    meta = symbol.meta or {}
    if meta.get("framework_role") == "route":
        return True
    for concept in meta.get("concepts", []) or []:
        if isinstance(concept, dict) and concept.get("concept") == "route":
            return True
    return False


def _is_route_marker(symbol: Symbol) -> bool:
    """Return True for a framework-materialized route *marker* node.

    A route marker (``meta.framework_role == "route"``, e.g. an analyzer's
    standalone ``GET:/health:route`` node) is a registration stub with
    essentially no outbound edges — distinct from the concept-enriched
    *function* handler (matched via ``meta.concepts``), which carries the real
    call graph. When both map to the same ``(method, path)`` slice filename,
    preferring the non-marker keeps the informative slice instead of the
    degenerate marker one (INV-nubub).
    """
    return (symbol.meta or {}).get("framework_role") == "route"


def _extract_route_info(symbol: Symbol) -> dict | None:
    """Pull (method, path) out of a route symbol's metadata.

    Returns None when both lookup sites fail to yield a complete pair —
    downstream code uses the return value to decide whether to emit a
    route-qualified filename or a handler-name fallback. kind='route'
    symbols prefer their authoritative meta.http_method/route_path; other
    route symbols fall back to the first matching concept entry.
    """
    meta = symbol.meta or {}
    if meta.get("framework_role") == "route":
        method = meta.get("http_method")
        path = meta.get("route_path")
        if method and path:
            return {"method": str(method), "path": str(path)}
    for concept in meta.get("concepts", []) or []:
        if isinstance(concept, dict) and concept.get("concept") == "route":
            method = concept.get("method")
            path = concept.get("path")
            if method and path:
                return {"method": str(method), "path": str(path)}
    return None


def _handler_slice_filename(symbol: Symbol, route_info: dict | None) -> str:
    """Build the filename for a handler slice.

    Preferred form is `slice.handler.<METHOD>.<path-sanitized>.json` when
    route metadata is available — (method, path) is framework-agnostic and
    globally unique in practice, which handler names are not. Falls back to
    `slice.handler.<handler-name>.json` when metadata is incomplete.
    """
    if route_info:
        method = _sanitize_filename_part(
            str(route_info["method"]).upper(), max_len=10
        )
        path_part = _sanitize_filename_part(str(route_info["path"]))
        return f"slice.handler.{method}.{path_part}.json"
    name_part = _sanitize_filename_part(symbol.name or "unnamed")
    return f"slice.handler.{name_part}.json"


def _emit_handler_slices(
    behavior_map: dict,
    all_symbols: list[Symbol],
    all_edges: list[Edge],
    repo_root: Path,
    out_dir: Path,
    max_handler_slices: int = _DEFAULT_MAX_HANDLER_SLICES,
    enabled: bool = True,
) -> list[Path]:
    """Emit one forward slice JSON per detected route handler (WI-sihok).

    Writes up to `max_handler_slices` files named
    `slice.handler.<METHOD>.<path>.json` to `out_dir`, plus a companion
    `slice.handler.index.json` that lists *every* detected handler — the
    emitted ones and any dropped by the cap — so LLM/agent consumers can
    discover what is available and what to re-derive on demand. Returns the
    list of written paths (handler slices + index file).

    Detection matches cmd_routes (concept=route OR kind=route) and excludes
    test-file handlers. Handlers are deduplicated by symbol id; when the
    same id is registered under multiple routes, all registrations appear
    in the emitted file's meta.routes list. Slice parameters mirror the
    bakeoff's proven forward-slice tuning.

    When `enabled=False`, returns an empty list without touching the
    filesystem — the caller opts out via --no-handler-slices.
    """
    if not enabled:
        return []

    from .paths import is_test_file
    from .slice import AmbiguousEntryError, SliceQuery, slice_graph

    # Step 1: collect route symbols, excluding test-file handlers. Order is
    # preserved from all_symbols, which run_behavior_map passes in already
    # ranked by centrality — so first-seen is most prominent.
    handlers: list[Symbol] = []
    for sym in all_symbols:
        if not _is_route_symbol(sym):
            continue
        if sym.path and is_test_file(sym.path):
            continue
        handlers.append(sym)

    # Step 2: group by symbol id so a single handler registered under
    # multiple routes emits once with a merged routes list. Preserves the
    # ranked insertion order.
    id_to_routes: dict[str, list[dict]] = {}
    id_order: list[str] = []
    id_to_symbol: dict[str, Symbol] = {}
    for h in handlers:
        if h.id not in id_to_routes:
            id_to_routes[h.id] = []
            id_order.append(h.id)
            id_to_symbol[h.id] = h
        info = _extract_route_info(h)
        if info and info not in id_to_routes[h.id]:
            id_to_routes[h.id].append(info)

    # Step 2b (INV-nubub): collapse distinct ids that resolve to the SAME
    # on-disk slice filename. The framework route marker
    # (meta.framework_role == "route", ~0 outbound edges) and the
    # concept-enriched function handler (the real call graph) for one
    # (method, path) are different ids but produce the same filename; grouping
    # by id alone let both reach the write step, and the second writer
    # silently clobbered the first on disk, leaving only the degenerate marker
    # slice. Keep ONE slice per filename -- prefer the non-marker so the
    # informative function slice wins -- and merge every colliding id's routes
    # into the survivor.
    fname_order: list[str] = []
    fname_to_symbol: dict[str, Symbol] = {}
    fname_to_routes: dict[str, list[dict]] = {}
    for hid in id_order:
        sym = id_to_symbol[hid]
        routes = id_to_routes[hid]
        primary = routes[0] if routes else None
        fname = _handler_slice_filename(sym, primary)
        if fname not in fname_to_routes:
            fname_order.append(fname)
            fname_to_symbol[fname] = sym
            fname_to_routes[fname] = list(routes)
            continue
        if _is_route_marker(fname_to_symbol[fname]) and not _is_route_marker(sym):
            fname_to_symbol[fname] = sym
        for info in routes:
            if info not in fname_to_routes[fname]:
                fname_to_routes[fname].append(info)

    # Pre-compute out-degree for the index file (gives consumers a quick
    # "how many callees does this handler have?" signal without re-scanning).
    out_degree: dict[str, int] = {}
    for e in all_edges:
        out_degree[e.src] = out_degree.get(e.src, 0) + 1

    out_dir.mkdir(parents=True, exist_ok=True)

    written: list[Path] = []
    index_entries: list[dict] = []

    for rank, fname in enumerate(fname_order):
        handler = fname_to_symbol[fname]
        handler_id = handler.id
        routes = fname_to_routes[fname]

        entry: dict = {
            "id": handler_id,
            "name": handler.name,
            "path": handler.path,
            "routes": routes,
            "out_degree": out_degree.get(handler_id, 0),
        }

        if rank >= max_handler_slices:
            entry["emitted"] = False
            entry["reason"] = (
                f"over cap ({max_handler_slices}); re-run with "
                f"hypergumbo slice --entry {handler_id}"
            )
            index_entries.append(entry)
            continue

        query = SliceQuery(
            entrypoint=handler_id,
            max_hops=_HANDLER_SLICE_MAX_HOPS,
            max_files=_HANDLER_SLICE_MAX_FILES,
            min_confidence=0.0,
            exclude_tests=True,
            exclude_utility=False,
            reverse=False,
            max_tier=None,
            language=None,
            hub_threshold=_HANDLER_SLICE_HUB_THRESHOLD,
            exclude_imports=True,
            dataflow=False,
        )
        try:
            result = slice_graph(all_symbols, all_edges, query)
        except AmbiguousEntryError:  # pragma: no cover - defensive: id is an exact match
            entry["emitted"] = False
            entry["reason"] = "ambiguous entry id"
            index_entries.append(entry)
            continue

        filename = fname  # the group key (one slice file per (method, path))
        out_path = out_dir / filename

        node_ids_set = set(result.node_ids)
        edge_ids_set = set(result.edge_ids)
        inline_nodes = [
            n for n in behavior_map.get("nodes", []) if n.get("id") in node_ids_set
        ]
        inline_edges = [
            e for e in behavior_map.get("edges", []) if e.get("id") in edge_ids_set
        ]

        # WI-bujim: append the spec-shape entry to behavior_map["features"]
        # (option (c) — index-only). Full denormalized slice content
        # (inline nodes/edges/meta) lives in the per-slice file written
        # below; features[] holds just IDs + query + summary so consumers
        # can discover what slices exist via the behavior map alone, and
        # diff across commits using the query-derived stable id.
        behavior_map.setdefault("features", []).append(result.to_dict())

        feature_dict = result.to_dict()
        feature_dict["nodes"] = inline_nodes
        feature_dict["edges"] = inline_edges
        feature_dict["meta"] = {
            "entry_kind": "handler",
            "routes": routes,
            "slice_params": {
                "max_hops": _HANDLER_SLICE_MAX_HOPS,
                "max_files": _HANDLER_SLICE_MAX_FILES,
                "hub_threshold": _HANDLER_SLICE_HUB_THRESHOLD,
                "exclude_tests": True,
                "exclude_imports": True,
            },
        }

        output = {
            "schema_version": behavior_map.get("schema_version", "0.1.0"),
            "view": "slice",
            "feature": feature_dict,
        }
        user_out_open_json_dump(out_path, output)

        entry["emitted"] = True
        entry["file"] = filename
        entry["node_count"] = len(result.node_ids)
        entry["edge_count"] = len(result.edge_ids)
        index_entries.append(entry)
        written.append(out_path)

    index_path = out_dir / "slice.handler.index.json"
    user_out_write(
        index_path,
        json.dumps(
            {
                "schema_version": behavior_map.get("schema_version", "0.1.0"),
                "view": "handler_slice_index",
                "max_handler_slices": max_handler_slices,
                "handlers": index_entries,
            },
            indent=2,
            sort_keys=True,
        )
    )
    written.append(index_path)
    return written


# _relativize_ir_paths moved to finalize.py (it is finalize sub-step 1's logic, run once at
# Phase B below and again as an idempotent backstop inside finalize()). Imported at the top.


# RCT-pinned surface — see tests/test_rct_public_api_pinned.py before changing parameter names or defaults.
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
    enable_handler_slices: bool = True,
    max_handler_slices: int = _DEFAULT_MAX_HANDLER_SLICES,
    gzip_output: bool = False,
    no_sketch_fan_out: bool = False,
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
        frameworks: Framework specification (ADR-3aaa):
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
        if not progress or not sys.stderr.isatty():
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
        if not progress or not sys.stderr.isatty():
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
    # WI-jadig: pass profile through so the dispatcher can short-circuit
    # analyzers whose languages have 0 files in this repo (lifecycle policy
    # member of INV-manov — file-presence pre-filter).
    (
        analysis_runs,
        all_symbols,
        all_edges,
        all_usage_contexts,
        limits,
        captured_symbols,
        dependency_manifest,
    ) = run_all_analyzers(
        repo_root, max_files=max_files, profile=behavior_map["profile"],
    )
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

    # WI-hopug: strip the machine-specific absolute ``repo_root`` prefix from
    # all Symbol IDs, Edge endpoints, and UsageContext fields now that the
    # symbol table is fully resolved. Must run before ranking / entrypoints /
    # handler-slice emission so every downstream consumer observes a single,
    # portable set of identifiers.
    _relativize_ir_paths(repo_root, all_symbols, all_edges, all_usage_contexts)

    # Refine framework list using import evidence (post-analysis validation).
    # Frameworks detected from manifests are cross-referenced against actual
    # import edges to distinguish production frameworks from dev/test-only ones.
    if profile.framework_mode == "auto":
        from .profile import refine_frameworks
        profile = refine_frameworks(profile, all_edges, all_symbols)
        behavior_map["profile"] = profile.to_dict()

    # Enrich symbols with framework concept metadata (ADR-3aaa)
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
    from .framework_patterns import (
        expand_class_based_view_routes,
        materialize_route_symbols,
    )
    materialized_routes = materialize_route_symbols(all_symbols)
    if materialized_routes:
        all_symbols.extend(materialized_routes)

    # WI-lojoh: expand Django CBV routes (single ANY route per as_view()
    # registration) into one route per declared HTTP method on the view
    # class. Runs after materialize_route_symbols so any newly minted route
    # symbols can also be expanded.
    cbv_expanded, cbv_removed_ids = expand_class_based_view_routes(all_symbols)
    if cbv_removed_ids:
        all_symbols = [s for s in all_symbols if s.id not in cbv_removed_ids]
    if cbv_expanded:
        all_symbols.extend(cbv_expanded)

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
    for _linker_name, linker_result in run_all_linkers(linker_ctx, limits=limits):
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

    # synthetic:F2 (5a): backstop stable_id / display_label / fingerprint on
    # Class-B synthetic protocol-synth Symbols the linkers left null (post-pass,
    # AFTER linkers extend all_symbols and paths are normalized, BEFORE
    # fingerprint stamping so that pass skips the now-stamped Class-B nodes).
    # Identity-neutral (per-field skip-if-set); closes META-huvuh's producer half.
    populate_synthetic_class_b_identity(all_symbols)

    # ADR-0035 §3 identity reconciliation (closes INV-tazaj's producer half).
    # Runs AFTER the enclosure post-pass (so per-site `uses` edges exist to
    # rewire onto LOGICAL hubs) and BEFORE finalize (R1: set membership is final
    # on finalize entry) and the deduplicate_edges call below (which collapses
    # the now-identical rewired edges). Order matters: dedup the LOGICAL families
    # to one hub node FIRST, then occurrence-index the remaining within-file SITE
    # collisions so each distinct site gets a distinct stable_id.
    dedup_logical_synthetic_identities(all_symbols, all_edges)
    # WI-gokiv (v8): widen LOGICAL route ids with the (now repo-relative) declaring
    # file + language so cross-file / cross-language same-(method,path) routes stop
    # colliding (Wave-2 gate's dominant residual). Runs BEFORE the within-file split
    # so two same-route declarations in one file get the :occ: ordinal afterwards.
    widen_route_stable_ids(all_symbols)
    split_within_file_stable_id_collisions(all_symbols)

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

    # WI-fanun: stamp structural fingerprints on analyzer-produced source
    # Symbols that don't already carry one (toml-v1, json-v1, wgsl-v1 keep
    # their manifest-derived fingerprints unchanged). Centralised in
    # hypergumbo_core.fingerprint so no analyzer change is required.
    from .fingerprint import stamp_symbol_fingerprints
    stamp_symbol_fingerprints(all_symbols, repo_root)

    # Apply supply chain classification to all symbols
    show_progress("Classifying symbols", 60)
    _classify_symbols(all_symbols, repo_root, package_roots, limits=limits)

    # Promote route-bearing symbols from derived (tier 4) to internal (tier 2).
    # Routes represent the API surface and are valuable regardless of whether
    # the code is generated (e.g., go-swagger, protobuf gRPC stubs).
    for s in all_symbols:
        if s.supply_chain_tier == 4:
            is_route = (s.meta or {}).get("framework_role") == "route"
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
        # WI-pozur (ADR-0043 C2 / D-D): boundary synthesis now runs AFTER this
        # filter, so no boundary nodes exist here and `is_external_boundary(s)`
        # is always False — this clause is a defensive no-op. Kept (not deleted)
        # so the carve-out restores itself automatically if synthesis is ever
        # moved back before filtering.
        removed_symbol_ids = {
            s.id for s in all_symbols
            if s.id not in filtered_symbol_ids
            and not is_external_boundary(s)
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
        # ADR-0027 Phase-2 audit (WI-jukav): all members are AXIS_PENDING
        # (Clusters G/H — build/config-shape and domain long-tail) or
        # AXIS_LANGUAGE_CONSTRUCT (Cluster A — ``property``, ``label``,
        # ``heading``, ``paragraph`` per audit-findings 0006/0007). None
        # of these values is scheduled for fold/rename in Phase 3 producer
        # migration; the noise-filtering semantics survive Wave 5
        # unchanged. Forward-compatible.
        _NOISE_KINDS = frozenset({
            # Documentation / config
            "section", "table_array", "code_block",
            "link", "paragraph", "label",
            "setting",
            # CSS structural (degree-0 in behavior maps)
            "class_selector", "id_selector", "rule_set",
            "property", "media", "keyframes", "font_face",
            # Config metadata (degree-0 across all tested repos)
            "pattern",      # .gitignore entries
            "requirement",  # pip requirements.txt entries
        })
        # CSS-family `variable` (custom properties, SCSS / Sass variables) is
        # zero-edge noise and stays excluded. WI-gafog E2: in any other
        # language, `variable` is a real top-level binding (Python module
        # constants, Go top-level `var`, YAML / Make variables) and must
        # remain in the output for cross-file `from <mod> import NAME`
        # resolution.
        _CSS_LANGUAGES = frozenset({"css", "scss", "sass", "less"})

        # INV-bovif: `kind="table"` is overloaded between TOML/INI/properties
        # `[section]` headers (config noise) and SQL `CREATE TABLE` entities
        # (first-class schema constructs). Filter only the config-language
        # producers; SQL tables pass through so the database_query linker
        # can link query call-sites to schema tables. Same shape as the
        # `_CSS_LANGUAGES` carve-out above.
        _TABLE_NOISE_LANGUAGES = frozenset({"toml", "ini", "properties"})

        def _is_noise(sym: "Symbol") -> bool:
            if sym.kind in _NOISE_KINDS:
                return True
            if sym.kind == "variable" and sym.language in _CSS_LANGUAGES:
                return True
            if sym.kind == "table" and sym.language in _TABLE_NOISE_LANGUAGES:
                return True
            # Wave 6 PR 3 fold per audit-findings 0005: ``script`` now
            # emits as ``kind="file"`` + ``meta["entry_role"]="script"``.
            # The legacy literal stays in ``_NOISE_KINDS`` for unmigrated
            # producers; this branch catches the post-fold shape without
            # over-excluding real ``kind="file"`` symbols.
            if sym.kind == "file" and sym.meta:
                if sym.meta.get("entry_role") == "script":
                    return True
            return False

        noise_ids = {s.id for s in all_symbols if _is_noise(s)}
        all_symbols = [s for s in all_symbols if not _is_noise(s)]
        all_edges = [
            e for e in all_edges
            if e.src not in noise_ids and e.dst not in noise_ids
        ]

    # Create boundary nodes for dangling edge endpoints (WI-sikur / INV-miniz).
    # Edges to external functions (stdlib, npm packages, etc.) would otherwise
    # break slice traversal by pointing to nonexistent nodes.
    # WI-fozoh: synthesizer collapses dangling refs by (lang, name, kind) and
    # returns an id_remap so we can rewrite edges to point at the canonical
    # boundary Symbols. Without this rewrite, edges would still reference the
    # original (now-absent) per-reference dangling ids.
    # synthetic:F1 (WI-sijut/WI-mosil): emit a real AnalysisRun for boundary
    # synthesis and stamp its execution_id into the boundary nodes'
    # origin_run_id (the nodes previously carried origin=[] / origin_run_id='').
    # Only record the run when boundary nodes were actually created.
    # WI-pozur (ADR-0043 §4, C2 — Phase E): this block runs HERE, AFTER tier+noise
    # filtering (Phase D), not before. Filtering can newly orphan an edge endpoint
    # (e.g. a tier-4 file whose file-level outgoing edges the src carve-out keeps);
    # running synthesis afterward sees that now-dangling src and mints/remaps a
    # boundary for it, closing the dangling-source class by construction. The
    # node/edge set is final at this point — finalize's (run-lifecycle:F1) R1 entry
    # precondition. (Previously this ran pre-filter, leaving dangling srcs.)
    _boundary_run = AnalysisRun.create(  # nosec B106 — pass_id is a pass identifier, not a password (bandit B106 false-positives on any "pass*" funcarg)
        pass_id="boundary_external_symbol_synthesis", version=PASS_VERSION,
        config_fingerprint=compute_config_fingerprint(
            {"pass_id": "boundary_external_symbol_synthesis"}
        ),
    )
    _boundary_t0 = time.perf_counter()
    boundary, id_remap = create_boundary_nodes(
        all_symbols, all_edges, dependency_manifest=dependency_manifest,
        origin_run_id=_boundary_run.execution_id,
        ecosystem_classifier=_make_ecosystem_classifier(),
    )
    if boundary:
        all_symbols.extend(boundary)
        # INV-gizik: stamp this synthesis pass's duration + node count (it emits
        # external_symbol placeholder Symbols; edge changes are remaps, not new
        # edges, so edges_emitted stays 0).
        _boundary_run.duration_ms = int((time.perf_counter() - _boundary_t0) * 1000)
        _boundary_run.nodes_emitted = len(boundary)
        analysis_runs.append(_boundary_run.to_dict())
    if id_remap:
        all_edges = apply_external_id_remap(all_edges, id_remap)
    _log_memory("after boundary nodes")

    # Rank symbols by importance (centrality + tier weighting) for output ordering
    show_progress("Ranking symbols", 65)
    ranked = rank_symbols(
        all_symbols, all_edges,
        first_party_priority=True,
        min_edge_confidence=0.5,
    )
    ranked_symbols = [r.symbol for r in ranked]
    del ranked  # Free RankedSymbol wrappers

    # Boundary nodes (synthetic Symbols for unresolved external edge endpoints
    # created by ir.create_boundary_nodes per WI-sikur / INV-miniz) are kept
    # in the output. Display surfaces (sketch / compact / search /
    # dead-code / explain) filter them via ir.is_external_boundary so the
    # presentation stays focused on first-party code, but the JSON exposes
    # them so disk-load consumers (slice / verify-claims / test-coverage)
    # can resolve every edge endpoint to a node and reason over the
    # boundary's supply_chain.tier (e.g., tier-3 wrappers that may reach
    # the network).

    # ADR-0043 §6/§6.1: the single finalize() reconcile point. The node/edge set is final
    # here (Phase D filtering + Phase E boundary synthesis + ranking are done — finalize's R1
    # entry precondition). finalize() subsumes the formerly-scattered finalizers in one
    # ordered pass: re-relativize backstop, pass_version backfill (WI-mipul), run_signature
    # recompute (META-hufaz), repo_fingerprint stamp (INV-tofur), skipped→limits, commit-dicts,
    # and the validate_ir call (now structurally last). Budget/compact projections still run
    # after it returns; the tiered projection re-derives its nodes_summary from the FINAL
    # post-shrink arrays via compact.recompute_view_summary (projection:F1 / INV-pazur).
    _fin_ctx = FinalizeContext(
        symbols=ranked_symbols,
        edges=all_edges,
        usage_contexts=all_usage_contexts,
        analysis_runs=analysis_runs,
        behavior_map=behavior_map,
        limits=limits,
        repo_root=repo_root,
        pass_metadata=build_pass_metadata(),
    )
    finalize(_fin_ctx)
    all_nodes = behavior_map["nodes"]
    all_edge_dicts = behavior_map["edges"]
    # Free the UsageContext objects before the expensive downstream passes (metrics,
    # entrypoints, handler-slices, sketch, projections): finalize already serialized them
    # into behavior_map["usage_contexts"], so drop both the local name AND the carrier's
    # reference (otherwise _fin_ctx would pin them until the end of the function).
    _fin_ctx.usage_contexts = []
    del all_usage_contexts

    # Compute metrics from analyzed nodes and edges
    show_progress("Computing metrics", 70)
    behavior_map["metrics"] = compute_metrics(
        all_nodes, all_edge_dicts, profile=behavior_map.get("profile"),
    )

    # Detect and store entrypoints (computed from symbols, persisted for convenience)
    show_progress("Detecting entrypoints", 75)
    entrypoints = detect_entrypoints(all_symbols, all_edges)
    behavior_map["entrypoints"] = [ep.to_dict() for ep in entrypoints]
    del entrypoints  # Free Entrypoint objects

    # WI-sihok: emit per-handler forward slices alongside the main output so
    # "what does this handler touch?" is answerable without a follow-up
    # `hypergumbo slice --entry <handler>` invocation. Uses behavior_map's
    # in-memory node/edge dicts (already ranked) for inlined slice payloads.
    #
    # WI-rimos / UX-A: write the fan-out into ``<stem>.slices/`` next to the
    # ``--out`` target instead of spreading 20-30 ``slice.handler.*.json``
    # files alongside the main result. Co-locating in a stem-derived
    # subdirectory keeps the user's --out directory tidy and prevents
    # successive runs from clobbering each other's slices when --out
    # changes between invocations (e.g. ``--out /tmp/round-01.json`` then
    # ``--out /tmp/round-02.json`` no longer share a slice namespace).
    slice_dir = out_path.parent / f"{out_path.stem}.slices"
    handler_slice_files = _emit_handler_slices(
        behavior_map,
        all_symbols,
        all_edges,
        repo_root,
        slice_dir,
        max_handler_slices=max_handler_slices,
        enabled=enable_handler_slices,
    )
    generated_files.extend(handler_slice_files)

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

        # Pre-compute the Additional-Files centrality scores
        # (`additional_file_centrality_scores`): the symbol-mention centrality of
        # the NON-SOURCE config/doc files surfaced in the sketch's Additional
        # Files section. Pre-computing here avoids expensive ripgrep/regex work
        # during sketch generation.
        #
        # file-anchor:F4 — `content_source_paths` excludes file-kind anchors
        # (only CONTENT nodes count as "source"), so the file-anchor:F1 candidate
        # anchors (minted for these same files at the orchestrator chokepoint)
        # are NOT re-subtracted from the candidate set. That keeps the surface
        # populated AND makes every centrality key a real node path (the WI-rajod
        # subset invariant; `additional_file_candidates` is the shared selector
        # both sites use).
        from .discovery import DEFAULT_EXCLUDES
        from .sketch import ADDITIONAL_FILES_EXCLUDES
        from .taxonomy import additional_file_candidates

        content_source_paths: set[str] = {
            sym.path for sym in all_symbols if sym.path and sym.kind != "file"
        }
        if file_index is not None:
            _all_repo_files = file_index.all_files()
        else:  # pragma: no cover - file_index always set in run_behavior_map
            _all_repo_files = [f for f in repo_root.rglob("*") if f.is_file()]
        candidate_files = additional_file_candidates(
            repo_root, _all_repo_files, content_source_paths,
            list(DEFAULT_EXCLUDES) + ADDITIONAL_FILES_EXCLUDES,
        )

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
            sketch_precomputed["additional_file_centrality_scores"] = {
                str(f.relative_to(repo_root)): score
                for f, score in centrality_result.normalized_scores.items()
            }
        else:  # pragma: no cover - defensive: no candidates or no symbols
            sketch_precomputed["additional_file_centrality_scores"] = {}

        behavior_map["sketch_precomputed"] = sketch_precomputed

    # (skipped→limits drain + behavior_map["limits"] commit moved into finalize() sub-step 6.)

    # Ensure parent directory exists (even if caller gives nested paths later)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    # WI-kojob: `--no-sketch-fan-out` is an explicit named flag that
    # collapses to the same effect as `budgets=none`. The named form
    # is more discoverable in --help and reads cleanly in scripts; it
    # wins over any explicit `budgets=...` value because the user's
    # intent ("don't fan out") is more specific than the budgets
    # detail.
    if no_sketch_fan_out:
        budgets = "none"

    # Generate budget-tiered output files BEFORE compact mode
    # (budget files are always based on full analysis, not compact)
    if budgets != "none":
        budget_specs: list[str]
        if budgets is None or budgets == "default":
            budget_specs = list(DEFAULT_TIERS)
        else:
            budget_specs = [b.strip() for b in budgets.split(",") if b.strip()]

        # WI-kojob: when gzipping, the user's out_path ends in `.gz`
        # (e.g. `output.json.gz`). `generate_tier_filename` splits on
        # the last extension, which would produce `output.json.4k.gz`
        # instead of the natural `output.4k.json.gz`. Compute the
        # budget filename from the un-suffixed stem, then re-append
        # `.gz` so the on-disk layout reads as
        # `<stem>.<tier>.json.gz`.
        if gzip_output and str(out_path).endswith(".gz"):
            budget_base = str(out_path)[: -len(".gz")]
        else:
            budget_base = str(out_path)

        # Generate each budget file from full behavior map
        for budget_spec in budget_specs:
            try:
                target_tokens = parse_tier_spec(budget_spec)
                budget_path = Path(generate_tier_filename(budget_base, budget_spec))
                if gzip_output:
                    budget_path = Path(str(budget_path) + ".gz")
                tiered_map = format_tiered_behavior_map(
                    behavior_map, all_symbols, all_edges, target_tokens
                )
                # WI-kojob: gzip budget tiers when the main output is
                # gzipped. Keeps the on-disk layout consistent: all
                # artifacts produced by one invocation share the same
                # compression mode.
                if gzip_output:
                    from .safety_zones import user_out_open_json_dump_gzip
                    user_out_open_json_dump_gzip(budget_path, tiered_map)
                else:
                    user_out_open_json_dump(budget_path, tiered_map)
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

    # ADR-0033/ADR-0043 §6: validate_ir + the validation_report now run inside finalize()
    # (sub-step 10, structurally last over the final substrate). Only the stderr warning
    # summary remains here (I/O). In compact mode behavior_map was rebound above, but
    # format_compact_behavior_map does dict(behavior_map) so finalize's validation_report is
    # preserved. The shrink-only ratchet gate (tests/test_validation_report_empty.py) is
    # unchanged.
    from .spec_validator import emit_stderr_summary
    emit_stderr_summary(_fin_ctx.violations)

    # Free memory: Symbol/Edge objects no longer needed after tier/compact processing
    # All data is now in behavior_map as dicts. For large repos like tensorflow (154k
    # symbols, 505k edges), this can free several GB of memory before final write.
    del all_symbols
    del all_edges
    del ranked_symbols
    del _fin_ctx  # holds refs to symbols/edges/usage_contexts via the carrier
    gc.collect()
    _log_memory("after cleanup")

    show_progress("Writing output", 95)
    if gzip_output:
        from .safety_zones import user_out_open_json_dump_gzip
        user_out_open_json_dump_gzip(out_path, behavior_map)
    else:
        user_out_open_json_dump(out_path, behavior_map)
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

    # Restore default SIGPIPE behavior so commands like
    # ``hypergumbo explain Symbol | head`` exit quietly when the downstream
    # pipe closes, instead of producing a BrokenPipeError traceback after
    # otherwise-correct output. POSIX-only: signal.SIGPIPE doesn't exist
    # on Windows, where Python uses a different mechanism for closed pipes.
    import signal
    if hasattr(signal, "SIGPIPE"):
        signal.signal(signal.SIGPIPE, signal.SIG_DFL)

    parser = build_parser()

    # Handle default sketch mode: if no subcommand given, insert "sketch"
    if argv is None:
        argv = sys.argv[1:]

    # Handle --help --all: show all subcommand help panels
    if ("--help" in argv or "-h" in argv) and "--all" in argv:
        print_all_help(parser)
        return 0

    subcommands = {"run", "slice", "search", "routes", "explain", "catalog", "config", "sketch", "build-grammars", "install-gitleaks", "uninstall-gitleaks", "cache-status", "cache-clear", "install-embeddings", "uninstall-embeddings", "install-rust-analyzer", "uninstall-rust-analyzer", "add-extras", "remove-extras", "test-coverage", "dead-code-maybe", "symbols", "compact", "io-boundaries", "verify-claims"}

    # WI-balij (UAT UX-04): accept --debug in any position. Strip it here so
    # `hypergumbo sketch . --debug` and `hypergumbo --debug sketch .` both
    # work — argparse otherwise rejects --debug after the subcommand because
    # it's only registered on the root parser.
    debug_flag = False
    if "--debug" in argv:
        debug_flag = True
        argv = [a for a in argv if a != "--debug"]

    # WI-vozof: accept --backend in any position and translate it to the
    # HYPERGUMBO_RUST_ANALYZER env var that the gate reads. The gate itself
    # already knows how to honour either signal; this path is CLI-side sugar
    # so `hypergumbo run . --backend rust-analyzer` works identically to
    # `HYPERGUMBO_RUST_ANALYZER=1 hypergumbo run .`. Matches the --debug
    # stripping pattern so the flag works in any position relative to the
    # subcommand.
    #
    # WI-jinoh / BUG-06: before setting the env var, gate on whether the
    # ``hypergumbo-lang-rust-analyzer`` Python integration package is
    # importable. The published v4.1.0 distribution does not include
    # it; without this gate the SCIP backend silently falls through to
    # tree-sitter and the user has no surface signal. We exit with a
    # clear error here rather than at first-engagement so the user's
    # mental model ("--backend rust-analyzer ran, so the SCIP backend
    # ran") is corrected immediately at parse time.
    for idx in range(len(argv) - 1):
        if argv[idx] == "--backend":
            choice = argv[idx + 1]
            if choice == "rust-analyzer":
                _ensure_rust_analyzer_integration_or_exit()
                _ensure_rust_analyzer_binary_or_exit()
                os.environ["HYPERGUMBO_RUST_ANALYZER"] = "1"
            argv = argv[:idx] + argv[idx + 2:]
            break
        if argv[idx].startswith("--backend="):
            choice = argv[idx].split("=", 1)[1]
            if choice == "rust-analyzer":
                _ensure_rust_analyzer_integration_or_exit()
                _ensure_rust_analyzer_binary_or_exit()
                os.environ["HYPERGUMBO_RUST_ANALYZER"] = "1"
            argv = argv[:idx] + argv[idx + 1:]
            break

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

