# SPDX-License-Identifier: AGPL-3.0-or-later
"""Repo-level DDG construction: walk a repo, build a CFG per function, solve.

This is the orchestration layer above :mod:`hypergumbo_core.cfg`. ``cfg.py``
knows how to turn *one* function body into a control-flow graph and solve
reaching definitions; this module knows how to find every function in a
repository, do that to each of them, and aggregate the result into the
``(ddg_edges, ddg_symbols, hints_by_caller)`` triple that taint propagation
consumes.

Why This Module Exists
----------------------
The logic lived in ``cli.py`` as ``_build_python_ddg_for_verify_claims``,
which is two problems in one name. ``cli.py`` scopes itself to "argument
parsing and dispatching to command handlers", and a repo-walking analysis
pipeline is not that — it had grown to ~200 lines inside a 10,000-line
module. More importantly the name encoded a hardcoded *language* and a
single *consumer*, and both were load-bearing: the sole call site passed
the literal string ``"python"`` to ``populate_def_use_for_cfg``, so the
Rust and TypeScript def/use extractors — registered, tested, and covered —
were never invoked by any production path at all.

How It Works
------------
A :class:`LanguageDdgSpec` says how to find functions in one language:
which files to walk, which AST node types are function definitions, and
how to name one. Specs are registered into a process-global registry, the
same idiom ``cfg.register_def_use_extractor`` uses, because naming a Go
method requires a receiver-type helper that lives in the *language*
package while this module lives in core. Core defines the registry;
language packages register into it; the caller force-imports them.

``build_repo_ddg()`` then does the same thing for every requested
language: glob the files, parse, walk for function nodes, build the CFG,
populate def/use **with that language's extractor**, and solve.

Symbol Identity
---------------
Function symbol ids are minted with :func:`analyze.base.make_symbol_id`,
so they match what the language analyzer emitted for the same function and
``ddg_symbols`` lines up with the structural BFS's node keys. This is the
same string the old Python code hand-formatted, so Python ids are
unchanged by the move.

The match is approximate in one disclosed way, inherited from the original:
class context is not walked for Python, so a method is keyed as
``...:function`` rather than ``...:method``. Go *is* receiver-aware,
because its spec supplies a namer.

Refinement Hook
---------------
The WI-dilih receiver-hint refinement is genuinely Python-specific — it
reads Python import statements and parameter annotations. It hangs off the
Python spec as an optional callable rather than being hoisted into the
generic loop, so adding a language does not require having an equivalent.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, Optional, Sequence

from .analyze.base import make_symbol_id

logger = logging.getLogger(__name__)

if TYPE_CHECKING:  # pragma: no cover - typing only
    from .cfg import DdgEdge

# Directories never worth walking. verify-claims should not pay to analyze
# third-party or generated code.
_SKIP_DIRS = frozenset({
    ".git", ".venv", "venv", ".tox", "__pycache__",
    ".ci", "node_modules", ".mypy_cache", ".pytest_cache",
    ".ruff_cache", "build", "dist", ".eggs",
})


@dataclass(frozen=True)
class LanguageDdgSpec:
    """How to find and name the functions of one language.

    Attributes:
        language: Language key. Selects the cfg mapping (``cfg_nodes/
            <language>.yaml``) AND the registered def/use extractor, which
            must agree — a spec whose extractor is missing yields CFGs
            with empty defines/uses and therefore zero DDG edges.
        file_glob: ``rglob`` pattern for source files.
        function_node_types: AST node types that introduce a function
            scope. Each is expected to expose ``name`` and ``body`` fields.
        name_for: Optional callable ``(node, source) -> str`` overriding
            the plain ``name`` field. Go uses this to prepend a receiver
            type so method ids match the analyzer's.
        kind_for: Optional callable ``(node) -> str`` choosing the id's
            kind slot; defaults to ``"function"``.
        refine: Optional callable invoked per function to derive extra
            hints; see the module docstring.
    """

    language: str
    file_glob: str
    function_node_types: frozenset[str]
    name_for: Optional[Callable[[Any, bytes], str]] = None
    kind_for: Optional[Callable[[Any], str]] = None
    refine: Optional[Callable[..., dict[tuple[int, str], str]]] = None


@dataclass
class RepoDdg:
    """Aggregated DDG for a repository."""

    ddg_edges: list["DdgEdge"] = field(default_factory=list)
    ddg_symbols: set[str] = field(default_factory=set)
    hints_by_caller: dict[str, dict[tuple[int, str], str]] = field(default_factory=dict)
    #: ``symbol_id -> [(line, defines, uses), ...]`` for every statement the
    #: def/use pass annotated.
    #:
    #: WHY THE EDGE SET IS NOT ENOUGH (INV-sadah). A ``DdgEdge`` says "variable
    #: v defined at line D is used at line U". It does NOT say which variable
    #: defined at U inherited v — and when one line defines two variables, the
    #: §3a walk has to know. ``keep = str(server); path = name`` defines both
    #: ``keep`` and ``path`` at the same line, and only the first derives from
    #: the tainted ``server``; the edge set alone is equally consistent with
    #: either. Statement-level ``defines``/``uses`` resolves it, and the CFG
    #: already carries them — ``populate_def_use_for_cfg`` fills them in one
    #: line above where this is collected, and they were simply discarded.
    stmt_defuse: dict[str, list[tuple[int, tuple[str, ...], tuple[str, ...]]]] = field(
        default_factory=dict,
    )


_DDG_LANGUAGES: dict[str, LanguageDdgSpec] = {}


def register_ddg_language(spec: LanguageDdgSpec) -> None:
    """Register a language spec for repo-level DDG construction."""
    _DDG_LANGUAGES[spec.language] = spec


def get_ddg_language(language: str) -> Optional[LanguageDdgSpec]:
    """Return the registered spec for a language, or None."""
    return _DDG_LANGUAGES.get(language)


def registered_ddg_languages() -> frozenset[str]:
    """Return the set of languages with a registered DDG spec."""
    return frozenset(_DDG_LANGUAGES)


def clear_ddg_languages() -> None:
    """Clear the registry (for tests)."""
    _DDG_LANGUAGES.clear()


def _function_name(node: Any, source: bytes, spec: LanguageDdgSpec) -> Optional[str]:
    """Resolve the display name for a function node."""
    if spec.name_for is not None:
        return spec.name_for(node, source)
    name_node = node.child_by_field_name("name")
    if name_node is None:
        return None
    return source[name_node.start_byte:name_node.end_byte].decode(
        "utf-8", errors="replace",
    )


def _walk_functions(
    node: Any,
    source: bytes,
    spec: LanguageDdgSpec,
    rel_path: str,
    out: RepoDdg,
    deps: dict[str, Any],
    mapping: Any,
    refine_ctx: dict[str, Any],
) -> None:
    """Recurse an AST collecting per-function DDG edges and refinement hints."""
    if node.type in spec.function_node_types:
        name = _function_name(node, source, spec)
        body_node = node.child_by_field_name("body")
        if name is not None and body_node is not None:
            kind = spec.kind_for(node) if spec.kind_for else "function"
            sym_id = make_symbol_id(
                spec.language, rel_path,
                node.start_point[0] + 1, node.end_point[0] + 1,
                name, kind,
            )
            _solve_one_function(
                node, body_node, source, spec, sym_id, out, deps, mapping, refine_ctx,
            )
    for child in node.children:
        _walk_functions(
            child, source, spec, rel_path, out, deps, mapping, refine_ctx,
        )


def _solve_one_function(
    node: Any,
    body_node: Any,
    source: bytes,
    spec: LanguageDdgSpec,
    sym_id: str,
    out: RepoDdg,
    deps: dict[str, Any],
    mapping: Any,
    refine_ctx: dict[str, Any],
) -> None:
    """Build one function's CFG, solve reaching defs, record the result."""
    try:
        cfg = deps["build_function_cfg"](body_node, source, mapping, sym_id)
        deps["populate_def_use_for_cfg"](cfg, body_node, source, spec.language)
        result = deps["solve_reaching_defs"](cfg)
    except Exception:  # pragma: no cover - defensive; skip this function
        return
    if result.bailed_out:
        return
    if result.ddg_edges:
        out.ddg_edges.extend(result.ddg_edges)
        out.ddg_symbols.add(sym_id)
        # Collected only alongside edges: a function with no edges cannot be
        # walked, so its statements would be dead weight in the index.
        stmts = [
            (s.line, tuple(s.defines), tuple(s.uses))
            for block in cfg.blocks.values()
            for s in block.statements
            if s.defines or s.uses
        ]
        if stmts:
            out.stmt_defuse[sym_id] = stmts
    if spec.refine is not None:
        hints = spec.refine(
            node=node,
            body_node=body_node,
            source=source,
            ddg_edges=result.ddg_edges,
            **refine_ctx,
        )
        if hints:
            out.hints_by_caller[sym_id] = hints


def build_repo_ddg(
    repo_root: Path,
    languages: Sequence[str] = ("python",),
) -> RepoDdg:
    """Build the aggregated DDG for a repository.

    Args:
        repo_root: Repository root to walk.
        languages: Language keys to process. Unregistered keys and
            languages with no cfg mapping are skipped silently — the
            caller falls back to structural taint propagation, which is
            the pre-existing behaviour when DDG data is unavailable.

    Returns:
        A :class:`RepoDdg`. Empty when tree-sitter is unavailable.
    """
    out = RepoDdg()
    try:
        import tree_sitter
        from tree_sitter_language_pack import get_language

        from .cfg import (
            build_function_cfg,
            load_cfg_mapping,
            populate_def_use_for_cfg,
            solve_reaching_defs,
        )
    except ImportError:  # pragma: no cover - tree-sitter is a hard dep but defend
        return out

    deps = {
        "build_function_cfg": build_function_cfg,
        "populate_def_use_for_cfg": populate_def_use_for_cfg,
        "solve_reaching_defs": solve_reaching_defs,
    }

    for language in languages:
        spec = _DDG_LANGUAGES.get(language)
        if spec is None:
            continue
        mapping = load_cfg_mapping(language)
        if mapping is None:  # pragma: no cover - shipped mappings always load
            continue
        try:
            ts_language = get_language(language)
        except Exception:  # pragma: no cover - pack always provides these
            logger.debug("no tree-sitter grammar for %s; skipping", language)
            continue
        parser = tree_sitter.Parser(ts_language)
        _walk_language(repo_root, spec, parser, mapping, out, deps)

    return out


def _walk_language(
    repo_root: Path,
    spec: LanguageDdgSpec,
    parser: Any,
    mapping: Any,
    out: RepoDdg,
    deps: dict[str, Any],
) -> None:
    """Walk every source file of one language under ``repo_root``."""
    for path in repo_root.rglob(spec.file_glob):
        if any(part in _SKIP_DIRS for part in path.parts):
            continue
        try:
            source = path.read_bytes()
        except OSError:  # pragma: no cover - defensive
            continue
        tree = parser.parse(source)
        rel_path = path.relative_to(repo_root).as_posix()
        refine_ctx = _refine_context(spec, tree, source)
        _walk_functions(
            tree.root_node, source, spec, rel_path, out, deps, mapping, refine_ctx,
        )


def _refine_context(spec: LanguageDdgSpec, tree: Any, source: bytes) -> dict[str, Any]:
    """Build the per-file context the refinement hook needs, if any."""
    if spec.refine is None:
        return {}
    from .taint_refine import extract_python_imports

    module_imports, imports = extract_python_imports(tree.root_node, source)
    return {"module_imports": module_imports, "imports": imports}


def _python_refine(
    *,
    node: Any,
    body_node: Any,
    source: bytes,
    ddg_edges: list["DdgEdge"],
    module_imports: dict[str, str],
    imports: dict[str, tuple[str, str]],
) -> dict[tuple[int, str], str]:
    """WI-dilih receiver-hint refinement for Python.

    WI-dozon: parameter annotations are extracted even when the DDG is
    empty — a short helper like ``return name.replace(...)`` has no
    def-use edges, but the annotation is exactly the signal that pins the
    receiver type.
    """
    from .taint_refine import (
        extract_python_param_annotations,
        extract_python_receiver_hints,
    )

    param_anns = extract_python_param_annotations(node, source, module_imports, imports)
    if not param_anns and not ddg_edges:
        return {}
    return extract_python_receiver_hints(
        body_node, source, module_imports, imports, ddg_edges,
        param_annotations=param_anns,
    )


register_ddg_language(LanguageDdgSpec(
    language="python",
    file_glob="*.py",
    function_node_types=frozenset({"function_definition"}),
    refine=_python_refine,
))
