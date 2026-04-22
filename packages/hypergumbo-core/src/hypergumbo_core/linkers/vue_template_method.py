# SPDX-License-Identifier: AGPL-3.0-or-later
"""Framework linker: Vue template-method for connecting event handlers to script methods.

Vue Single-File Components have template sections that reference methods
defined in the script section. For example, ``@click="handleDelete"`` in
the template calls the ``handleDelete`` method defined in ``<script>``.

The Vue analyzer extracts ``directive`` symbols with ``handler_expression``
metadata (the method name referenced by the event handler). The JS/TS
analyzer independently extracts method/function symbols from the same
``.vue`` file's ``<script>`` block.

How It Works
------------
1. Collect all ``directive`` symbols with ``handler_expression`` metadata
2. Group JS/TS method/function symbols by their normalized file path
3. For each directive, find matching method/function in the same file
4. Create ``template_calls`` edges linking directive to method

Why This Matters
----------------
Without this linker, Vue component methods called only from templates
(not from other JS code) are completely orphaned. On Chatwoot, ~3,139
orphan JS symbols in ``.vue`` files were disconnected because no edges
existed from template event handlers to script methods.

Path Normalization
------------------
Vue analyzer stores relative paths (``src/App.vue``), while JS/TS
analyzer stores absolute paths (``/repo/src/App.vue``). The linker
normalizes both to relative paths using ``repo_root`` for matching.
"""

from __future__ import annotations

from collections import defaultdict
from pathlib import Path

from ..ir import AnalysisRun, Edge, PASS_VERSION, Symbol, make_pass_id
from .registry import LinkerActivation, LinkerContext, LinkerResult, register_linker

PASS_ID = make_pass_id("vue-template-method-linker")

# Symbol kinds that can be template handler targets.
_HANDLER_KINDS = frozenset({"method", "function", "getter", "setter"})


def _normalize_path(path_str: str, repo_root: Path) -> str:
    """Normalize a path to a relative string for comparison.

    Vue directive symbols use relative paths (``src/App.vue``).
    JS/TS symbols use absolute paths (``/repo/src/App.vue``).
    This function strips the repo_root prefix from absolute paths
    and returns a consistent relative string.
    """
    p = Path(path_str)
    if p.is_absolute():
        try:
            return str(p.relative_to(repo_root))
        except ValueError:
            return path_str
    return path_str


def link_vue_template_methods(
    repo_root: Path,
    symbols: list[Symbol],
    edges: list[Edge],
) -> LinkerResult:
    """Link Vue template event handler directives to script methods.

    Args:
        repo_root: Repository root path for normalizing file paths.
        symbols: All symbols from all analyzers.
        edges: Existing edges (not modified).

    Returns:
        LinkerResult with new template_calls edges.
    """
    # Index JS/TS handler-kind symbols by (normalized_path, name).
    methods_by_file: dict[str, dict[str, Symbol]] = defaultdict(dict)
    for sym in symbols:
        if sym.kind in _HANDLER_KINDS and sym.language in ("javascript", "typescript"):
            norm = _normalize_path(sym.path, repo_root)
            methods_by_file[norm][sym.name] = sym

    # Find directives with handler expressions and match to methods.
    new_edges: list[Edge] = []
    for sym in symbols:
        if sym.kind != "directive":
            continue
        meta = sym.meta or {}
        handler_name = meta.get("handler_expression", "")
        if not handler_name:
            continue

        directive_path = _normalize_path(sym.path, repo_root)
        file_methods = methods_by_file.get(directive_path)
        if not file_methods:
            continue

        target = file_methods.get(handler_name)
        if not target:
            continue

        edge = Edge.create(
            src=sym.id,
            dst=target.id,
            edge_type="template_calls",
            line=sym.span.start_line if sym.span else 0,
            origin=PASS_ID,
            origin_run_id="",
            evidence_type="vue_event_handler",
            confidence=0.90,
        )
        new_edges.append(edge)

    run = AnalysisRun.create(pass_id=PASS_ID, version=PASS_VERSION)

    return LinkerResult(
        symbols=[],
        edges=new_edges,
        run=run,
    )


def _count_template_directives(ctx: LinkerContext) -> int:
    """Count directive symbols with handler expressions."""
    count = 0
    for s in ctx.symbols:
        if s.kind == "directive" and (s.meta or {}).get("handler_expression"):
            count += 1
    return count


@register_linker(
    "vue-template-methods",
    priority=26,  # Right after vue-components (25)
    description="Vue template event handler to method binding",
    activation=LinkerActivation(
        frameworks=["vue", "nuxt"],
        language_pairs=[("vue", "javascript"), ("vue", "typescript")],
    ),
)
def link_vue_template_method(ctx: LinkerContext) -> LinkerResult:
    """Linker entry point for registry."""
    return link_vue_template_methods(ctx.repo_root, ctx.symbols, ctx.edges)
