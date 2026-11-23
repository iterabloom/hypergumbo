# SPDX-License-Identifier: AGPL-3.0-or-later
"""Clojure analysis pass using tree-sitter.

This analyzer uses tree-sitter to parse Clojure files and extract:
- Namespace definitions (ns)
- Function definitions (defn, defn-)
- Variable definitions (def, defonce)
- Macro definitions (defmacro)
- Protocol definitions (defprotocol)
- Record/type definitions (defrecord, deftype)
- Multimethod definitions (defmulti) and their implementations (defmethod,
  emitted as ``method`` — a first-class callable kind here)
- Per-symbol ``meta`` (visibility, and ``constructed_from`` for def forms
  whose value is a constructor call), cyclomatic complexity and line span
- Function call relationships: a call in a ``defn`` starts at it; a call
  that runs when the namespace loads (a top-level form, a ``def`` value, a
  script's guarded block) starts at the file node (INV-foluz;
  :func:`_call_anchor`); a call in another body form (``defmethod``,
  ``defmacro``, ...) makes no edge
- A Babashka script's main guard, ``(when (= *file* (System/getProperty
  "babashka.file")) ...)``: the file node carries the ``main_guard`` concept
  (framework ``babashka``), the entry kind of Python's ``if __name__ ==
  "__main__":`` (ADR-0063, a program start)
- Require/import statements
- UsageContext records for framework pattern matching (Ring/Compojure routes)

If tree-sitter with Clojure support is not installed, the analyzer
gracefully degrades and returns an empty result.

How It Works
------------
Uses TreeSitterAnalyzer base class for two-pass orchestration:
1. Pass 1: Parse all files, extract all symbols into global registry
2. Pass 2: Detect calls and resolve against global symbol registry

The base class handles grammar checking, parser creation and result
assembly. This module provides the Clojure-specific extraction logic and
overrides ``_find_source_files`` so ``.edn`` data files are not parsed as
source.

Why This Design
---------------
- TreeSitterAnalyzer eliminates boilerplate orchestration code
- Uses tree-sitter-language-pack for grammar (clojure)
- Two-pass allows cross-file call resolution
- Same pattern as other tree-sitter analyzers for consistency

Clojure-Specific Considerations
-------------------------------
- Clojure is a Lisp with S-expression syntax
- defn creates public functions, defn- creates private functions
- ns declares namespace with :require for imports
- Macros are first-class and common in idiomatic Clojure
- Protocols provide interface-like polymorphism
- Records are value types implementing protocols
- Multimethods provide ad-hoc polymorphism via dispatch function

Callable identity (WI-tusad)
----------------------------
``defn``/``defn-``, ``defmacro``, ``defmethod`` and ``defmulti`` mint the
ADR-0035 producer stable_id at construction, through
``overload_arity.declared_arity_stable_id``, instead of riding the backstop
floor (where two same-name forms in one file null each other). Inputs:

- the scope chain: the var's namespace-qualified name ``ns/name`` (every def
  form interns a namespace-level var, however deeply it is nested), from the
  file's latest ``(ns ...)`` form, or the bare name before any;
- for a ``defmethod``, its dispatch value, appended to that chain with its
  whitespace and commas collapsed (``area :circle``), so the methods of one
  multimethod hash apart and reformatting a vector value moves nothing;
- the parameters, written to ``meta["parameters"]``. A multi-arity form is
  ONE node whose arity bodies merge into one list: the longest fixed body's
  parameters, those past the shortest body's count marked ``default``, and a
  ``& rest`` as the variadic entry. So ``([x] ...) ([x y] ...)`` admits one or
  two arguments, and a later ``(defn run ([x] x) ([x y] ...))`` hashes apart
  from an earlier ``(defn run [x] x)``. Non-contiguous arities (``[x]`` and
  ``[x y z]``) merge to one to three, which ``narrow_by_arity`` reads as a
  superset. A ``defmulti`` declares no parameters (its dispatch function
  does), so it takes the zero-parameter formula.

Visibility is not an input: ``defn`` -> ``defn-`` is the same var.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import TYPE_CHECKING, ClassVar, Iterator, Optional

from hypergumbo_core.discovery import classify_dot_bb_file, find_files
from hypergumbo_core.ir import Edge, Span, StableId, Symbol, UsageContext, make_pass_id
from hypergumbo_core.analyze.base import (
    AnalysisResult,
    FileAnalysis,
    TreeSitterAnalyzer,
    find_child_by_type,
    iter_tree,
    make_file_id,
    make_symbol_id,
    node_text,
)
from hypergumbo_core.analyze.registry import register_analyzer
from hypergumbo_core.axis_meta_keys import write_meta_key
from hypergumbo_core.analyze.cyclomatic import compute_cyclomatic_complexity
from hypergumbo_core.overload_arity import (
    PARAMETERS_KEY, declared_arity, declared_arity_stable_id, parameter_entry,
)

if TYPE_CHECKING:
    import tree_sitter
    from hypergumbo_core.ir import AnalysisRun
    from hypergumbo_core.symbol_resolution import NameResolver

PASS_ID = make_pass_id("clojure")

# INV-loguk: Clojure callable kinds get non-null cyclomatic_complexity /
# line_span via the homoiconic head-symbol walker. Non-callable def forms
# (variable/protocol/record/type/multimethod) and the ns ``module`` symbol
# share the emit site and stay None.
_CLOJURE_CALLABLE_KINDS: frozenset[str] = frozenset({"function", "macro", "method"})


def find_clojure_files(repo_root: Path) -> Iterator[Path]:
    """Yield all Clojure files in the repository.

    The analyzer's own discovery (``_find_source_files``), so the two cannot drift.
    """
    yield from _analyzer._find_source_files(repo_root)




def _get_sym_name(node: "tree_sitter.Node", source: bytes) -> str:
    """Extract symbol name from sym_lit node."""
    name_node = find_child_by_type(node, "sym_name")
    if name_node:
        return node_text(name_node, source)
    return node_text(node, source)  # pragma: no cover - fallback for unusual nodes


def _extract_clojure_signature(
    node: "tree_sitter.Node", source: bytes
) -> Optional[str]:
    """Extract function signature from a Clojure defn form.

    Clojure functions use vector syntax for parameters:
    (defn name [param1 param2] body) -> [param1, param2]
    (defn name [x y & rest] body) -> [x, y, & rest]

    Returns signature string like "[x, y]" or None if not found.
    """
    # Find the vector literal (vec_lit) containing parameters
    # It should be after the function name
    children = [c for c in node.children if c.type not in ("(", ")")]
    if len(children) < 3:
        return None  # pragma: no cover - malformed defn

    # children[0] = defn/defn-, children[1] = name, children[2+] = docstring/params/body
    for i in range(2, len(children)):
        child = children[i]
        if child.type == "vec_lit":
            # Extract parameter names from vector
            params: list[str] = []
            for vec_child in child.children:
                if vec_child.type == "sym_lit":
                    param_name = _get_sym_name(vec_child, source)
                    params.append(param_name)
            if params:
                return "[" + ", ".join(params) + "]"
            return "[]"

    return None  # pragma: no cover - no params found



def _clojure_constructed_from(
    inner: "list[tree_sitter.Node]", source: bytes,
) -> "str | None":
    """The callee of a ``def``'s value form, for ``meta['constructed_from']``.

    ``(def app (make-c))`` -> ``"make-c"``. Clojure has no ``new`` keyword in
    idiomatic code — objects come from factory functions — so the same rule
    the other analyzers apply (record the callee of a call-valued binding)
    lands on the s-expression's head symbol.

    ``inner`` is the def form's children; index 2 is the value form when
    present. A value that is not a call (``(def n 3)``) or whose head is not
    a plain symbol (``(def x ((f) 1))``) yields None rather than a guess.
    """
    if len(inner) < 3:
        return None
    value = inner[2]
    if value.type != "list_lit":
        return None
    head = next(
        (c for c in value.children if c.type not in ("(", ")")), None,
    )
    if head is None or head.type != "sym_lit":
        return None
    return _get_sym_name(head, source) or None



def _clojure_def_meta(
    visibility: str,
    constructed_from: "str | None",
    parameters: "list[dict[str, object]] | None" = None,
) -> "dict[str, object] | None":
    """Assemble a def's meta, omitting the dict entirely when empty.

    Kept as a helper so adding a key later cannot silently drop another —
    the shape that made a conditional literal fragile.
    """
    meta: dict[str, object] = {}
    if visibility == "private":
        meta["visibility"] = visibility
    if constructed_from:
        meta["constructed_from"] = constructed_from
    if parameters is not None:
        meta[PARAMETERS_KEY] = parameters
    return meta or None


#: Kinds whose stable_id the producer mints (see the module docstring).
_CLOJURE_IDENTITY_KINDS: frozenset[str] = frozenset(
    {"function", "macro", "method", "multimethod"}
)

#: Kinds whose form carries a parameter vector (or several arity bodies).
_CLOJURE_PARAMETER_KINDS: frozenset[str] = frozenset(
    {"function", "macro", "method"}
)

#: Children of a parameter vector that are not parameters.
_NON_PARAMETER_NODES: frozenset[str] = frozenset(
    {"[", "]", "meta_lit", "old_meta_lit", "comment", "dis_expr"}
)


def _collapse(text: str) -> str:
    """``text`` with commas (whitespace in Clojure) and runs of space collapsed."""
    return " ".join(text.replace(",", " ").split())


def _vector_parameters(
    vec: "tree_sitter.Node", source: bytes,
) -> list[dict[str, object]]:
    """``meta["parameters"]`` entries of one parameter vector ``[x {:keys [a]} & more]``.

    Each form is one parameter (a destructuring map or vector included, named
    by its collapsed text); the form after ``&`` is the variadic one.
    """
    entries: list[dict[str, object]] = []
    rest = False
    for child in vec.children:
        if child.type in _NON_PARAMETER_NODES:
            continue
        if child.type == "sym_lit":
            name = _get_sym_name(child, source)
            if name == "&":
                rest = True
                continue
        else:
            name = _collapse(node_text(child, source))
        entries.append(parameter_entry(name, variadic=rest))
    return entries


def _merge_arity_bodies(
    bodies: "list[list[dict[str, object]]]",
) -> list[dict[str, object]]:
    """One parameter list for a multi-arity form (see the module docstring)."""
    if len(bodies) == 1:
        return bodies[0]
    fixed = [[e for e in body if not e.get("variadic")] for body in bodies]
    required = min(len(f) for f in fixed)
    longest = max(fixed, key=len)
    merged = [
        parameter_entry(str(e["name"]), default=i >= required)
        for i, e in enumerate(longest)
    ]
    merged.extend(
        next(([e] for body in bodies for e in body if e.get("variadic")), []),
    )
    return merged


def _clojure_parameters(
    forms: "list[tree_sitter.Node]", source: bytes,
) -> "list[dict[str, object]] | None":
    """The parameters of a callable def form from the forms after its name.

    ``forms`` starts after the name (after the dispatch value of a
    ``defmethod``). A docstring and an attribute map are skipped; then either
    one parameter vector or a run of ``([params] body)`` arity bodies follows.
    None when neither does (a malformed or macro-generated form).
    """
    rest = [f for f in forms if f.type not in ("str_lit", "map_lit")]
    if rest and rest[0].type == "vec_lit":
        return _vector_parameters(rest[0], source)
    bodies: list[list[dict[str, object]]] = []
    for form in rest:
        if form.type != "list_lit":
            break
        head = next((c for c in form.children if c.type != "("), None)
        if head is None or head.type != "vec_lit":
            break
        bodies.append(_vector_parameters(head, source))
    return _merge_arity_bodies(bodies) if bodies else None


def _is_def_form(sym_name: str) -> tuple[str, str] | None:
    """Check if a symbol name is a def-like form.

    Returns (kind, "public"|"private") if it's a def form, None otherwise.
    """
    defs = {
        "def": ("variable", "public"),
        "defonce": ("variable", "public"),
        "defn": ("function", "public"),
        "defn-": ("function", "private"),
        "defmacro": ("macro", "public"),
        "defprotocol": ("protocol", "public"),
        "defrecord": ("record", "public"),
        "deftype": ("type", "public"),
        "defmulti": ("multimethod", "public"),
        "defmethod": ("method", "public"),
    }
    return defs.get(sym_name)


def _extract_symbols_from_file(
    tree: "tree_sitter.Tree",
    source: bytes,
    file_path: str,
    run_id: str,
) -> list[Symbol]:
    """Extract all symbols from a parsed Clojure file.

    Detects:
    - ns (namespace)
    - defn, defn- (functions)
    - def, defonce (variables)
    - defmacro (macros)
    - defprotocol (protocols)
    - defrecord, deftype (records/types)
    - defmulti (multimethods)
    """
    symbols: list[Symbol] = []
    current_ns = ""

    for node in iter_tree(tree.root_node):
        if node.type == "list_lit":
            children = node.children
            # Skip parens
            inner = [c for c in children if c.type not in ("(", ")")]

            if inner and inner[0].type == "sym_lit":
                first_sym = _get_sym_name(inner[0], source)

                # Handle ns (namespace) form
                if first_sym == "ns" and len(inner) > 1:
                    if inner[1].type == "sym_lit":
                        ns_name = _get_sym_name(inner[1], source)
                        current_ns = ns_name
                        start_line = node.start_point[0] + 1
                        end_line = node.end_point[0] + 1
                        span = Span(
                            start_line=start_line,
                            end_line=end_line,
                            start_col=node.start_point[1],
                            end_col=node.end_point[1],
                        )
                        sym_id = make_symbol_id("clojure", file_path, start_line, end_line, ns_name, "module")
                        symbols.append(Symbol(
                            id=sym_id,
                            name=ns_name,
                            kind="module",
                            language="clojure",
                            path=file_path,
                            span=span,
                            origin=PASS_ID,
                            origin_run_id=run_id,
                        ))

                # Handle def forms
                def_info = _is_def_form(first_sym)
                if def_info and len(inner) > 1:
                    kind, visibility = def_info
                    if inner[1].type == "sym_lit":
                        def_name = _get_sym_name(inner[1], source)
                        start_line = node.start_point[0] + 1
                        end_line = node.end_point[0] + 1
                        span = Span(
                            start_line=start_line,
                            end_line=end_line,
                            start_col=node.start_point[1],
                            end_col=node.end_point[1],
                        )
                        sym_id = make_symbol_id("clojure", file_path, start_line, end_line, def_name, kind)

                        # Extract signature for functions
                        signature = None
                        if kind == "function":
                            signature = _extract_clojure_signature(node, source)

                        is_callable = kind in _CLOJURE_CALLABLE_KINDS
                        parameters: "list[dict[str, object]] | None" = None
                        stable_id: "StableId | None" = None
                        if kind in _CLOJURE_IDENTITY_KINDS:
                            qualified = (
                                f"{current_ns}/{def_name}" if current_ns else def_name
                            )
                            after_name = inner[2:]
                            if kind == "method" and after_name:
                                qualified += " " + _collapse(
                                    node_text(after_name[0], source),
                                )
                                after_name = after_name[1:]
                            if kind in _CLOJURE_PARAMETER_KINDS:
                                parameters = _clojure_parameters(after_name, source)
                            stable_id = declared_arity_stable_id(
                                kind=kind, lang="clojure", path=file_path,
                                name=def_name, qualified_name=qualified,
                                arity=declared_arity(
                                    None if parameters is None
                                    else {PARAMETERS_KEY: parameters},
                                ),
                            )
                        symbols.append(Symbol(
                            id=sym_id,
                            stable_id=stable_id,
                            name=def_name,
                            kind=kind,
                            language="clojure",
                            path=file_path,
                            span=span,
                            origin=PASS_ID,
                            origin_run_id=run_id,
                            signature=signature,
                            meta=_clojure_def_meta(
                                visibility,
                                _clojure_constructed_from(inner, source),
                                parameters,
                            ),
                            cyclomatic_complexity=(
                                compute_cyclomatic_complexity(node, "clojure")
                                if is_callable else None
                            ),
                            line_span=(
                                end_line - start_line + 1 if is_callable else None
                            ),
                        ))

    return symbols


def _find_enclosing_defn(
    node: "tree_sitter.Node",
    source: bytes,
    local_symbols: dict[str, Symbol],
) -> Symbol | None:
    """Find the enclosing defn/defn- function for a given node.

    Uses local_symbols only since the enclosing function must be in the same file.
    """
    current = node.parent
    while current:
        if current.type == "list_lit":
            children = current.children
            inner = [c for c in children if c.type not in ("(", ")")]
            if inner and inner[0].type == "sym_lit":
                first_sym = _get_sym_name(inner[0], source)
                if first_sym in ("defn", "defn-") and len(inner) > 1:
                    if inner[1].type == "sym_lit":
                        def_name = _get_sym_name(inner[1], source)
                        sym = local_symbols.get(def_name)
                        if sym:
                            return sym
        current = current.parent
    return None


#: Forms whose body does not run when the file loads: it runs when the var
#: they define is invoked (a macro, a method, a test the runner calls), or
#: never (``comment``, the rich-comment block). A call inside one other than
#: ``defn`` / ``defn-`` (whose symbol is the anchor) is left without an
#: anchor, as before: anchoring it on the file would claim it runs at load.
#: A ``fn`` literal is not here: one bound to a top-level name is anchored on
#: the file, INV-foluz's rule for a function literal at module level.
_DEFERRED_BODY_FORMS = frozenset({
    "defmacro", "defmethod", "defmulti", "defprotocol", "defrecord", "deftype",
    "extend-type", "extend-protocol", "reify", "proxy", "deftest", "comment",
})


def _call_anchor(
    node: "tree_sitter.Node", source: bytes,
    local_symbols: dict[str, Symbol], file_id: str,
) -> Optional[str]:
    """The id a call at ``node`` starts at, or ``None`` for no edge.

    Inside a ``defn`` / ``defn-``: that function. Inside another form whose
    body runs later (:data:`_DEFERRED_BODY_FORMS`): ``None``. Anywhere else
    the call runs when the namespace loads (a top-level form, a ``def``
    value, a script's ``(when (= *file* ...) ...)``), so it is anchored on
    the file node (INV-foluz, ``base.file_anchor_symbol``'s rule), which is
    reached when the file is (``anchor_execution``). These calls used to make
    no edge at all.
    """
    caller = _find_enclosing_defn(node, source, local_symbols)
    if caller is not None:
        return caller.id
    current = node.parent
    while current is not None:
        if current.type == "list_lit":
            inner = [c for c in current.children if c.type not in ("(", ")")]
            if inner and inner[0].type == "sym_lit" and (
                _get_sym_name(inner[0], source) in _DEFERRED_BODY_FORMS | {"defn", "defn-"}
            ):
                return None
        current = current.parent
    return file_id


#: Threading macros: each step after the first argument is a call, and a
#: bare symbol step (``(-> z f)``) calls that function with no parentheses.
#: ``cond->`` / ``cond->>`` alternate a test and a step, so only every second
#: argument after the first is a step.
_THREADING_FORMS = frozenset({"->", "->>", "some->", "some->>"})
_COND_THREADING_FORMS = frozenset({"cond->", "cond->>"})


def _threaded_callees(head: str, inner: "list[tree_sitter.Node]") -> "list[tree_sitter.Node]":
    """The bare symbols a threading form calls (``topmost`` in
    ``(-> z (find-pred p) topmost)``). A parenthesised step is a list of its
    own, and the walk reaches it as a call already."""
    if head in _THREADING_FORMS:
        steps = inner[2:]
    elif head in _COND_THREADING_FORMS:
        steps = inner[3::2]
    else:
        return []
    return [step for step in steps if step.type == "sym_lit"]


def _resolve_callee(
    sym_node: "tree_sitter.Node", source: bytes, local_symbols: dict[str, Symbol],
    resolver: "NameResolver", require_aliases: dict[str, str],
) -> "tuple[Symbol, float] | None":
    """The symbol a call's head names, with its resolution score.

    An unqualified name is first the var of this file's namespace of that
    name, as Clojure resolves it (the resolver alone could pick another
    file's same-named ``-main``); then, as for a qualified one
    (``str/join``, its alias read from the ``:require`` aliases), the
    run-wide resolver.
    """
    name = _get_sym_name(sym_node, source)
    ns_node = find_child_by_type(sym_node, "sym_ns")
    if ns_node is None:
        local = local_symbols.get(name)
        if local is not None and local.kind not in ("module", "file"):
            return local, 1.0
    path_hint = require_aliases.get(node_text(ns_node, source)) if ns_node else None
    lookup_result = resolver.lookup(name, path_hint=path_hint)
    if lookup_result.found and lookup_result.symbol:
        return lookup_result.symbol, lookup_result.confidence
    return None


#: Babashka's script guard, the counterpart of Python's
#: ``if __name__ == "__main__":`` (the condition of a top-level ``when`` /
#: ``if``, whitespace collapsed, either operand order).
_BABASHKA_GUARD = re.compile(
    r'^\(= (?:\*file\* \(System/getProperty "babashka\.file"\)'
    r'|\(System/getProperty "babashka\.file"\) \*file\*)\)$'
)


def _has_babashka_main_guard(root: "tree_sitter.Node", source: bytes) -> bool:
    """Whether a top-level ``(when|if (= *file* (System/getProperty
    "babashka.file")) ...)`` runs the file's code only when Babashka runs
    the file as a script."""
    for form in root.children:
        if form.type != "list_lit":
            continue
        inner = [c for c in form.children if c.type not in ("(", ")")]
        if (
            len(inner) >= 2 and inner[0].type == "sym_lit"
            and _get_sym_name(inner[0], source) in ("when", "if")
            and _BABASHKA_GUARD.match(" ".join(node_text(inner[1], source).split()))
        ):
            return True
    return False


def _extract_require_aliases(
    tree: "tree_sitter.Tree",
    source: bytes,
) -> dict[str, str]:
    """Extract namespace aliases from require forms for disambiguation.

    In Clojure:
        (:require [clojure.string :as str]) -> str maps to clojure.string
        (:require [my.util :as u :refer [helper]]) -> u maps to my.util

    Returns a dict mapping alias names to full namespace paths.
    """
    aliases: dict[str, str] = {}

    for node in iter_tree(tree.root_node):
        # Look for kwd_lit with :require
        if node.type == "kwd_lit":
            kwd_name_node = find_child_by_type(node, "kwd_name")
            if kwd_name_node and node_text(kwd_name_node, source) == "require":
                # Found :require - parent should be the list_lit for (:require ...)
                parent = node.parent
                if parent and parent.type == "list_lit":
                    # Iterate siblings (vec_lit nodes are the require specs)
                    for sibling in parent.children:
                        if sibling.type == "vec_lit":
                            # Parse [namespace :as alias]
                            vec_children = sibling.children
                            ns_name: Optional[str] = None
                            alias_name: Optional[str] = None
                            looking_for_alias = False

                            for child in vec_children:
                                if child.type == "sym_lit" and ns_name is None:
                                    ns_name = node_text(child, source)
                                elif child.type == "kwd_lit":
                                    kwd = node_text(child, source)
                                    if kwd == ":as":
                                        looking_for_alias = True
                                    else:
                                        looking_for_alias = False
                                elif child.type == "sym_lit" and looking_for_alias:
                                    alias_name = node_text(child, source)
                                    looking_for_alias = False

                            if ns_name and alias_name:
                                aliases[alias_name] = ns_name

    return aliases


def _extract_edges_from_file(
    tree: "tree_sitter.Tree",
    source: bytes,
    file_path: str,
    file_symbols: list[Symbol],
    resolver: NameResolver,
    run_id: str,
    require_aliases: dict[str, str] | None = None,
) -> list[Edge]:
    """Extract call and import edges from a parsed Clojure file.

    Args:
        require_aliases: Optional dict mapping namespace aliases to full paths.

    Detects:
    - Function calls (list_lit starting with sym_lit)
    - require statements (:require in ns form)
    """
    if require_aliases is None:  # pragma: no cover - defensive default
        require_aliases = {}
    edges: list[Edge] = []
    file_id = make_file_id("clojure", file_path)

    # Build local symbol map for this file (name -> symbol)
    local_symbols = {s.name: s for s in file_symbols}

    for node in iter_tree(tree.root_node):
        if node.type == "list_lit":
            children = node.children
            inner = [c for c in children if c.type not in ("(", ")")]

            if inner and inner[0].type == "sym_lit":
                first_sym = _get_sym_name(inner[0], source)

                # Handle ns :require
                if first_sym == "ns":
                    for child in inner:
                        if child.type == "list_lit":
                            list_inner = [c for c in child.children if c.type not in ("(", ")")]
                            if list_inner and list_inner[0].type == "kwd_lit":
                                kwd_text = node_text(list_inner[0], source)
                                if kwd_text == ":require":
                                    # Process require forms
                                    for req in list_inner[1:]:
                                        if req.type == "vec_lit":
                                            # [namespace :as alias] or [namespace :refer [...]]
                                            vec_inner = [c for c in req.children if c.type not in ("[", "]")]
                                            if vec_inner and vec_inner[0].type == "sym_lit":
                                                req_ns = _get_sym_name(vec_inner[0], source)
                                                module_id = f"clojure:{req_ns}:0-0:module:module"
                                                edge = Edge.create(
                                                    src=file_id,
                                                    dst=module_id,
                                                    edge_type="imports",
                                                    line=req.start_point[0] + 1,
                                                    origin=PASS_ID,
                                                    origin_run_id=run_id,
                                                    evidence_type="require",
                                                )
                                                edges.append(edge)
                                        elif req.type == "sym_lit":
                                            # Simple require: just namespace
                                            req_ns = _get_sym_name(req, source)
                                            module_id = f"clojure:{req_ns}:0-0:module:module"
                                            edge = Edge.create(
                                                src=file_id,
                                                dst=module_id,
                                                edge_type="imports",
                                                line=req.start_point[0] + 1,
                                                origin=PASS_ID,
                                                origin_run_id=run_id,
                                                evidence_type="require",
                                            )
                                            edges.append(edge)

                # Handle function calls (not def forms)
                elif not _is_def_form(first_sym):
                    caller_id = _call_anchor(node, source, local_symbols, file_id)
                    if caller_id:
                        for callee_node in (inner[0], *_threaded_callees(first_sym, inner)):
                            callee = _resolve_callee(
                                callee_node, source, local_symbols, resolver, require_aliases,
                            )
                            if callee is not None:
                                edges.append(Edge.create(
                                    src=caller_id,
                                    dst=callee[0].id,
                                    edge_type="calls",
                                    line=callee_node.start_point[0] + 1,
                                    origin=PASS_ID,
                                    origin_run_id=run_id,
                                    evidence_type="ast_call",
                                    resolution_score=callee[1],
                                    meta={"call_construct": "function"},
                                ))

    return edges


def _extract_usage_contexts(
    root_node: "tree_sitter.Node",
    source: bytes,
    file_path: str,
) -> list[UsageContext]:
    """Extract UsageContext records for function calls in a Clojure file.

    Emits a UsageContext for every S-expression call ``(name args...)``, skipping
    ``def``-family forms (defn, def, defmacro, etc.) and the ``ns`` form since
    those define symbols rather than use frameworks.

    For Ring/Compojure patterns like ``(GET "/users" [] handler)``, the first
    string-literal argument is captured as ``metadata["url"]`` so the
    ring-compojure.yaml ``extract.path = "metadata.url"`` rule can pick it up.
    """
    # Forms that define symbols — not framework usage
    _DEF_FORMS = frozenset({
        "ns", "def", "defonce", "defn", "defn-", "defmacro",
        "defprotocol", "defrecord", "deftype", "defmulti", "defmethod",
    })

    contexts: list[UsageContext] = []

    for node in iter_tree(root_node):
        if node.type != "list_lit":
            continue

        inner = [c for c in node.children if c.type not in ("(", ")")]
        if not inner or inner[0].type != "sym_lit":
            continue

        call_name = _get_sym_name(inner[0], source)

        # Skip def-family forms
        if call_name in _DEF_FORMS:
            continue

        span = Span(
            start_line=node.start_point[0] + 1,
            end_line=node.end_point[0] + 1,
            start_col=node.start_point[1],
            end_col=node.end_point[1],
        )

        # Extract first string-literal argument as url metadata
        metadata: dict[str, object] = {}
        args = inner[1:]  # skip the function name
        if args:
            first_arg = args[0]
            if first_arg.type == "str_lit":
                # Strip surrounding quotes
                raw = node_text(first_arg, source)
                url = raw.strip('"')
                metadata["url"] = url

        ctx = UsageContext.create(
            kind="call",
            context_name=call_name,
            position="args[0]",
            path=file_path,
            span=span,
            metadata=metadata if metadata else None,
        )
        contexts.append(ctx)

    return contexts


class ClojureAnalyzer(TreeSitterAnalyzer):
    """Clojure language analyzer using tree-sitter-language-pack.

    Handles .clj, .cljs, .cljc, and .edn files. EDN files are discovered
    but skipped during symbol extraction (they are data files). The analyzer
    uses create_file_symbols=True so the base class creates file-level symbols
    automatically.
    """

    lang = "clojure"
    file_patterns: ClassVar[list[str]] = ["*.clj", "*.cljs", "*.cljc", "*.edn"]
    #: Files of the current run with a Babashka main guard (Pass 1 -> post_process).
    _main_guard_paths: frozenset[str] = frozenset()
    language_pack_name = "clojure"
    create_file_symbols = True

    def _find_source_files(self, repo_root: Path) -> Iterator[Path]:
        """Yield Clojure source files, skipping .edn data files.

        A ``.bb`` file is a Babashka script when ``classify_dot_bb_file``
        says so (the extension is also BitBake's, whose analyzer reads the
        rest). The analyzer is registered with this finder
        (``find_files=``, INV-hokig) so the profile counts what the pass
        reads: the taxonomy cannot glob ``.bb`` for Clojure, and a tree
        whose only Clojure is Babashka scripts would otherwise count zero
        clojure files and the pass would be skipped.
        """
        for path in find_files(repo_root, [*self.file_patterns, "*.bb"]):
            if path.suffix == ".edn":
                continue
            if path.suffix == ".bb" and classify_dot_bb_file(path) != "clojure":
                continue
            yield path

    def extract_symbols_from_file(
        self, tree: "tree_sitter.Tree", source: bytes,
        file_path: Path, rel_path: str, run: "AnalysisRun",
    ) -> FileAnalysis:
        """Extract symbols from a Clojure file."""
        analysis = FileAnalysis()
        symbols = _extract_symbols_from_file(tree, source, rel_path, run.execution_id)
        analysis.symbols = symbols
        for sym in symbols:
            analysis.symbol_by_name[sym.name] = sym
        if _has_babashka_main_guard(tree.root_node, source):
            self._main_guard_paths = self._main_guard_paths | {rel_path}
        return analysis

    def post_process(
        self,
        symbols: list[Symbol],
        edges: list[Edge],
        usage_contexts: list[UsageContext],
        run: "AnalysisRun",
    ) -> tuple[list[Symbol], list[Edge], list[UsageContext]]:
        """Stamp the ``main_guard`` concept on a guarded script's file node.

        A Babashka script whose top-level code runs under
        :func:`_has_babashka_main_guard` is run as a program the way a Python
        module under ``if __name__ == "__main__":`` is, and takes the same
        concept (ADR-0063's ``main_guard``, a program start; framework
        ``babashka``). The base class creates the file node after Pass 1, so
        the stamp waits for it here.
        """
        for sym in symbols:
            if sym.kind == "file" and sym.path in self._main_guard_paths:
                if sym.meta is None:
                    sym.meta = {}
                write_meta_key(
                    sym.meta, "concepts",
                    [{"concept": "main_guard", "framework": "babashka"}],
                )
        self._main_guard_paths = frozenset()
        return symbols, edges, usage_contexts

    def get_import_aliases(
        self, tree: "tree_sitter.Tree", source: bytes,
    ) -> dict[str, str]:
        """Extract require aliases for disambiguation."""
        return _extract_require_aliases(tree, source)

    def extract_edges_from_file(
        self, tree: "tree_sitter.Tree", source: bytes,
        file_path: Path, rel_path: str,
        local_symbols: dict[str, Symbol], global_symbols: dict[str, Symbol],
        run: "AnalysisRun", import_aliases: dict[str, str],
        resolver: "NameResolver",
    ) -> list[Edge]:
        """Extract call and import edges from a Clojure file."""
        file_symbols = list(local_symbols.values())
        return _extract_edges_from_file(
            tree, source, rel_path, file_symbols,
            resolver, run.execution_id,
            require_aliases=import_aliases,
        )

    def extract_usage_contexts_from_file(
        self, tree: "tree_sitter.Tree", source: bytes,
        file_path: Path, symbol_by_name: dict[str, Symbol],
    ) -> list[UsageContext]:
        """Extract usage contexts for YAML framework pattern matching."""
        return _extract_usage_contexts(
            tree.root_node, source, str(file_path),
        )


_analyzer = ClojureAnalyzer()


def is_clojure_tree_sitter_available() -> bool:
    """Check if tree-sitter with Clojure grammar is available."""
    return _analyzer._check_grammar_available()


@register_analyzer("clojure", find_files=find_clojure_files)
def analyze_clojure(repo_root: Path) -> AnalysisResult:
    """Analyze Clojure files in a repository.

    Returns an AnalysisResult with symbols, edges, and provenance.
    If tree-sitter-language-pack is not available, returns a skipped result.
    """
    return _analyzer.analyze(repo_root)
