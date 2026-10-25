# SPDX-License-Identifier: AGPL-3.0-or-later
"""TypeScript/JavaScript def/use extractor for intraprocedural dataflow (ADR-0017 §1c).

Extracts variable definitions and uses from TypeScript tree-sitter AST nodes,
and registers the languages that consume them.

JavaScript is a first-class registration here, not an incidental beneficiary:
the extractor is registered under both ``typescript`` and ``javascript``, and
this module registers a ``LanguageDdgSpec`` for each, walking every extension
``taxonomy.LANGUAGES`` files under that language (WI-fovus).
Each spec declares the callable node types the DDG walk will enter —
``function_declaration`` and ``method_definition`` (named and kinded by
``js_ts.py``'s ``jsts_method_name`` / ``jsts_method_kind``), plus the
binding-named ``arrow_function`` / ``function_expression`` / ``function`` /
``generator_function``, walked under the analyzer's own id. Each spec also
names ``program`` in ``module_node_types``, so a file's top-level code is
walked as module code under the file pseudo-symbol (WI-nilir; ddg_build's
"Module Code"). Together these node types decide how much of a file the
data-dependence graph can see.

Handles core patterns:

- ``const``/``let``/``var`` declarations with simple, array, and object destructuring
- Assignment expressions (``x = expr``)
- Augmented assignments (``x += expr``)
- Member expression writes (``this.field = expr``) — mutation of receiver
- Subscript expression writes (``data[idx] = val``) — mutation of object
- ``for...of`` / ``for...in`` loops
- ``return`` statements
- Expression statements (bare function calls)
- Assignments nested in an expression (``while ((m = re.exec(s)) !== null)``,
  ``x = y = e``, ``h(m = g())``) and ``i++``: defined -- weakly where it may
  not run, not at all inside a closure. The rule and its reasons are on
  :class:`_Collector` (WI-tozar).

Complex patterns (optional chaining, spread, computed properties, JSX) are
handled by recursing into the node and collecting identifiers, with two
deliberate exclusions: names in ``_TS_SKIP_NAMES`` (globals and builtins such
as ``console``, ``JSON``, ``Promise``, ``parseInt``) never become uses, and
the callee of a plain call is not a use — in ``foo(a)``, ``a`` is a use and
``foo`` is not.
"""
from __future__ import annotations

from typing import Any, Optional

from hypergumbo_core.cfg import DefUseResult, register_def_use_extractor
from hypergumbo_core.ddg_build import LanguageDdgSpec, register_ddg_language

from .js_ts import jsts_method_kind, jsts_method_name


def _node_text(node: Any, source: bytes) -> str:
    """Extract text from a tree-sitter node."""
    return source[node.start_byte:node.end_byte].decode("utf-8", errors="replace")


def _collect_identifiers(node: Any, source: bytes) -> list[str]:
    """Collect all identifier uses from an expression subtree.

    The bindings nested in the subtree are dropped here; :func:`_reads`
    keeps them.
    """
    collector = _Collector(source)
    collector.walk(node)
    return collector.uses


def _reads(node: Any, source: bytes) -> DefUseResult:
    """The uses of an expression subtree AND the bindings nested inside it."""
    collector = _Collector(source)
    collector.walk(node)
    return DefUseResult(defines=collector.defines, uses=collector.uses)


#: Node types that carry no variable reference.
_NON_USE_TYPES = frozenset({
    "type_identifier", "type_annotation", "type_parameters", "string",
    "template_string", "number", "true", "false", "null", "undefined", "regex",
    "comment",
})

#: Operators whose right operand runs only when the left does not decide.
_SHORT_CIRCUIT_OPERATORS = frozenset({"&&", "||", "??"})

#: Compound assignments that evaluate (and assign) only when the left side
#: does not decide.
_LOGICAL_ASSIGNMENT_OPERATORS = frozenset({"&&=", "||=", "??="})

#: Bodies whose bindings are not this statement's: they run when the closure
#: is CALLED, not where it is created. Walked for uses only, which is what the
#: whole subtree was before (what a closure captures is read where it is made).
_CLOSURE_TYPES = frozenset({
    "arrow_function", "function_expression", "function", "generator_function",
    "function_declaration", "generator_function_declaration",
    "class_declaration", "class", "class_body", "method_definition",
})

#: How a grammar marks a ``?.`` link (see :func:`_short_circuited`).
_OPTIONAL_CHAIN_MARKERS = frozenset({"optional_chain", "?."})

#: The links of one member/call chain, and the field each continues through.
_CHAIN_CONTINUES = {
    "call_expression": "function",
    "member_expression": "object",
    "subscript_expression": "object",
}


def _short_circuited(node: Any) -> bool:
    """True when ``?.`` sits on ``node``'s chain: ``o?.f(...)``, ``o?.p.f(...)``,
    ``f?.(...)``. If the chain short-circuits, the rest of it -- the call's
    arguments, a subscript's index -- is never evaluated. A parenthesis ends
    the chain: ``(o?.p).f(x)`` always evaluates ``x``.

    The marker is spelled two ways: a named ``optional_chain`` child, and --
    in the typescript/tsx grammars, for an optional CALL (``f?.(x)``) only --
    a bare anonymous ``?.`` token."""
    while node is not None and node.type in _CHAIN_CONTINUES:
        if any(child.type in _OPTIONAL_CHAIN_MARKERS for child in node.children):
            return True
        node = node.child_by_field_name(_CHAIN_CONTINUES[node.type])
    return False


class _Scope:
    """Where a nested binding sits, as far as its definition cares.

    ``conditional``: it may not execute (a weak update). ``in_closure``: it
    binds when the closure is called, not here, so it defines nothing here.
    Immutable; each narrowing returns a new scope.
    """

    __slots__ = ("conditional", "in_closure")

    def __init__(self, conditional: bool, in_closure: bool) -> None:
        self.conditional = conditional
        self.in_closure = in_closure

    @property
    def binds(self) -> bool:
        """A binding here defines its target."""
        return not self.in_closure

    def maybe(self) -> "_Scope":
        return _Scope(True, self.in_closure)

    def closure(self) -> "_Scope":
        return _Scope(self.conditional, True)


_ROOT = _Scope(conditional=False, in_closure=False)


class _Collector:
    """One expression's uses and nested bindings, in evaluation order.

    THE JAVA/C RULE (WI-tozar): an assignment nested inside an expression --
    in a condition (``while ((m = re.exec(s)) !== null)``), on the right side
    of another assignment (``x = y = e``, ``const a = (b = e)``) -- DEFINES
    its target. Before this only a statement-level assignment did, and a
    nested target was read as a use, so the walk had no definition to chain
    from. ``i++`` / ``--i`` likewise define ``i`` (and read it).

    WHERE THE VALUE ALSO GOES DOES NOT MATTER: ``h(m = src())``, a receiver,
    a return or a container literal define ``m`` like a condition does. It
    shipped as a USE there (CONTAINED ONLY) while the §3a walk could follow
    the heir on such a line without asking where the call took the value;
    WI-jusoj and WI-tinav closed that, and the reasoning and measurement are
    on the Python twin's ``_Collector`` (py_def_use.py).

    Two refinements Java's walker does not make:

    - CONDITIONAL = a WEAK update. A binding that may not execute (the right
      operand of ``&&`` / ``||`` / ``??``, a ternary branch, the right side of
      ``&&=``) is recorded as a define AND a use, the way ``x += e`` is: the
      value bound before still reaches what follows when the binding is
      skipped. A plain define would KILL that value on every path, and the
      walk could then refute a flow that runs whenever the branch is not
      taken.
    - A binding inside a closure stays a USE (see ``_CLOSURE_TYPES``).
    """

    def __init__(self, source: bytes) -> None:
        self.source = source
        self.defines: list[str] = []
        self.uses: list[str] = []

    def bind(self, names: list[str], scope: _Scope) -> None:
        """Record ``names`` as bound by this statement (see the class docstring)."""
        if scope.binds:
            self.defines.extend(names)
        if not scope.binds or scope.conditional:
            self.uses.extend(names)

    def walk(self, node: Any, scope: Optional[_Scope] = None) -> None:
        """Collect uses from ``node`` and the bindings nested in it.

        ``scope`` is where ``node`` sits (see :class:`_Scope`); the default
        is a statement root.
        """
        here = scope or _ROOT
        kind = node.type
        if kind == "identifier":
            name = _node_text(node, self.source)
            if name not in _TS_SKIP_NAMES:
                self.uses.append(name)
            return

        if kind == "this":
            self.uses.append("this")
            return

        if kind in _NON_USE_TYPES:
            return

        if kind == "call_expression":
            # The callee NAME is not a use; a method call reads its receiver.
            func = node.child_by_field_name("function")
            args = node.child_by_field_name("arguments")
            if func:
                if func.type == "member_expression":
                    obj = func.child_by_field_name("object")
                    if obj:
                        self.walk(obj, here)
                elif func.type != "identifier":
                    self.walk(func, here)
            if args:
                self.walk(args, here.maybe() if _short_circuited(node) else here)
            return

        if kind == "assignment_expression":
            left = node.child_by_field_name("left")
            right = node.child_by_field_name("right")
            # A write INTO an object mutates it rather than replacing it, so
            # it reads the object (and index) to find the slot: ``data[i] = v``
            # always, and a NESTED field write (``this.a = this.b = v``) too,
            # as Java models every field write and as this subtree read before
            # it was a binding. (A statement-level ``this.a = v`` keeps its
            # older, strong reading.)
            mutation = left.type == "subscript_expression" or (
                scope is not None and left.type == "member_expression"
            )
            if mutation:
                self.walk(left, here)
            self.walk(right, here)
            names = _collect_pattern_names(left, self.source)
            if not mutation:
                self.bind(names, here)
            elif here.binds:
                self.defines.extend(names)
            return

        if kind in ("augmented_assignment_expression", "update_expression"):
            # ``x += e`` / ``x ??= e`` / ``i++`` read the target before writing
            # it, so the definition is weak by construction.
            if kind == "update_expression":
                target = node.child_by_field_name("argument")
                self.walk(target, here)
            else:
                target = node.child_by_field_name("left")
                operator = _node_text(node.child_by_field_name("operator"), self.source)
                self.uses.extend(_collect_pattern_names(target, self.source))
                right = here.maybe() if operator in _LOGICAL_ASSIGNMENT_OPERATORS else here
                self.walk(node.child_by_field_name("right"), right)
            if here.binds:
                self.defines.extend(_collect_pattern_names(target, self.source))
            return

        if kind == "binary_expression":
            operator = _node_text(node.child_by_field_name("operator"), self.source)
            if operator in _SHORT_CIRCUIT_OPERATORS:
                self.walk(node.child_by_field_name("left"), here)
                self.walk(node.child_by_field_name("right"), here.maybe())
                return

        if kind == "ternary_expression":
            self.walk(node.child_by_field_name("condition"), here)
            self.walk(node.child_by_field_name("consequence"), here.maybe())
            self.walk(node.child_by_field_name("alternative"), here.maybe())
            return

        inner = here.closure() if kind in _CLOSURE_TYPES else here
        for child in node.children:
            if child.is_named:
                self.walk(child, inner)


def _collect_pattern_names(node: Any, source: bytes) -> list[str]:
    """Collect variable names from TS assignment/declaration target patterns."""
    if node.type == "identifier":
        return [_node_text(node, source)]

    if node.type == "shorthand_property_identifier_pattern":
        return [_node_text(node, source)]

    if node.type == "array_pattern":
        names: list[str] = []
        for child in node.children:
            if child.is_named:
                names.extend(_collect_pattern_names(child, source))
        return names

    if node.type == "object_pattern":
        names = []
        for child in node.children:
            if child.type == "shorthand_property_identifier_pattern":
                names.append(_node_text(child, source))
            elif child.type == "pair_pattern":
                value = child.child_by_field_name("value")
                if value:
                    names.extend(_collect_pattern_names(value, source))
            elif child.type in ("object_assignment_pattern", "assignment_pattern"):
                names.extend(_collect_pattern_names(child, source))
        return names

    if node.type == "rest_pattern":
        for child in node.children:
            if child.type == "identifier":
                return [_node_text(child, source)]
        return []  # pragma: no cover

    if node.type in ("assignment_pattern", "object_assignment_pattern"):
        left = node.child_by_field_name("left")
        if left:
            return _collect_pattern_names(left, source)
        # object_assignment_pattern may have the identifier as first named child
        for child in node.children:  # pragma: no cover
            if child.type == "identifier":  # pragma: no cover
                return [_node_text(child, source)]  # pragma: no cover
        return []  # pragma: no cover

    if node.type == "member_expression":
        obj = node.child_by_field_name("object")
        if obj and obj.type == "identifier":
            return [_node_text(obj, source)]
        if obj and obj.type == "this":
            return ["this"]
        return []

    if node.type == "subscript_expression":
        obj = node.child_by_field_name("object")
        if obj and obj.type == "identifier":
            return [_node_text(obj, source)]
        return []

    return []


_TS_SKIP_NAMES = frozenset({
    "undefined", "null", "NaN", "Infinity",
    "console", "JSON", "Math", "Date", "Object", "Array", "String",
    "Number", "Boolean", "Symbol", "BigInt", "Promise", "Map", "Set",
    "WeakMap", "WeakSet", "Error", "TypeError", "RangeError",
    "parseInt", "parseFloat", "isNaN", "isFinite",
    "setTimeout", "setInterval", "clearTimeout", "clearInterval",
    "require", "module", "exports", "__dirname", "__filename",
})


@register_def_use_extractor("javascript")
@register_def_use_extractor("typescript")
class TypeScriptDefUseExtractor:
    """Extracts variable definitions and uses from TypeScript/JavaScript AST nodes.

    Registered under BOTH keys off one class because ``_HANDLERS`` is keyed on
    tree-sitter node types and tree-sitter-javascript emits the same ones. A
    second class would be a copy whose only difference is the string it is
    registered under — and a copy is the thing that drifts.

    Each registration gets its own instance with its own ``language`` stamp;
    see ``register_def_use_extractor``.
    """

    language = "typescript"

    def extract(self, node: Any, source: bytes) -> DefUseResult:
        """Return variables defined and used by this AST node."""
        handler = _HANDLERS.get(node.type)
        if handler:
            return handler(node, source)
        # Every identifier is a use, and an assignment nested anywhere in the
        # node (``if ((m = g()) > 0)``) a define.
        return _reads(node, source)


def _handle_lexical_declaration(node: Any, source: bytes) -> DefUseResult:
    """Handle const/let/var declarations."""
    collector = _Collector(source)
    for child in node.children:
        if child.type == "variable_declarator":
            name_node = child.child_by_field_name("name")
            value_node = child.child_by_field_name("value")
            if value_node:
                collector.walk(value_node)
            if name_node:
                collector.defines.extend(_collect_pattern_names(name_node, source))
    return DefUseResult(defines=collector.defines, uses=collector.uses)


def _handle_variable_declaration(node: Any, source: bytes) -> DefUseResult:
    """Handle var declarations (same structure as lexical_declaration)."""
    return _handle_lexical_declaration(node, source)


def _handle_assignment_expression(node: Any, source: bytes) -> DefUseResult:
    """Handle assignment: x = expr, this.field = expr, data[idx] = expr.

    The collector owns the rule, so a statement-level assignment and one
    nested in an expression (``x = y = e``) cannot disagree.
    """
    return _reads(node, source)


def _handle_augmented_assignment(node: Any, source: bytes) -> DefUseResult:
    """Handle augmented assignment: x += expr (defines and reads x)."""
    return _reads(node, source)


def _handle_return_statement(node: Any, source: bytes) -> DefUseResult:
    """Handle return statement (the value leaves the function)."""
    collector = _Collector(source)
    for child in node.children:
        if child.is_named:
            collector.walk(child, _ROOT)
    return DefUseResult(defines=collector.defines, uses=collector.uses)


def _handle_for_in_statement(node: Any, source: bytes) -> DefUseResult:
    """Handle for...of / for...in: for (const item of items)."""
    left = node.child_by_field_name("left")
    right = node.child_by_field_name("right")
    reads = _reads(right, source) if right else DefUseResult()
    defines = list(reads.defines)
    if left:
        if left.type == "identifier":
            defines.append(_node_text(left, source))
        else:
            defines.extend(_collect_pattern_names(left, source))
    return DefUseResult(defines=defines, uses=reads.uses)


def _handle_expression_statement(node: Any, source: bytes) -> DefUseResult:
    """Handle expression statement wrapper."""
    for child in node.children:
        if child.is_named:
            handler = _HANDLERS.get(child.type)
            if handler:
                return handler(child, source)
            return _reads(child, source)
    return DefUseResult()


_HANDLERS: dict[str, Any] = {
    "lexical_declaration": _handle_lexical_declaration,
    "variable_declaration": _handle_variable_declaration,
    "assignment_expression": _handle_assignment_expression,
    "augmented_assignment_expression": _handle_augmented_assignment,
    "return_statement": _handle_return_statement,
    "for_in_statement": _handle_for_in_statement,
    "expression_statement": _handle_expression_statement,
}


# Registered so that TypeScript's def/use extractor is actually reachable from
# the repo-level DDG builder; without a spec, build_repo_ddg skips the language
# outright and the extractor stays dead no matter how correct it is.
#
# SCOPE, deliberately narrow and disclosed rather than implied:
#
#   * `function_declaration` and `method_definition` (named and kinded through
#     js_ts.py's `jsts_method_name` / `jsts_method_kind`, WI-sakir / WI-jopuf),
#     the binding-named callables (WI-mufag), and top-level `program` code
#     (WI-nilir).
#
#   * TypeScript currently has ZERO catalogued taint sinks (6 sources, 0 sinks),
#     so no TypeScript DDG edge can change a taint verdict today whatever this
#     covers. That is stated so the registration is not mistaken for a taint
#     improvement: it makes the language conform to the wiring gate and be ready
#     when sinks land, and nothing more.
#
#   * JavaScript is now wired too (WI-nonad), which is why there are two specs
#     below rather than one. It matters more than the TypeScript registration
#     it rode in on, and for the opposite reason: TypeScript carries 6 sources
#     and ZERO sinks, JavaScript carries 50 sources and 83 SINKS, so JavaScript
#     is the half of this pair whose DDG edges can actually change a taint
#     verdict. The three keys it needed — mapping, extractor, spec — are all
#     aliases or second registrations rather than new logic, because
#     `cfg_nodes/typescript.yaml` states in its own header that it "also covers
#     JavaScript (same node types in tree-sitter-javascript)". See
#     `cfg._CFG_MAPPING_ALIASES` for why that assertion is encoded once instead
#     of copied into a second YAML.
#
#     NOT A CLAIM ABOUT RECALL. Wiring makes `.js` files reach the machinery;
#     it does not establish that any taint verdict improves. That is an A/B on
#     express (all `.js`) and apollo-server, read per-flow against source, and
#     it is deliberately NOT asserted here.
#
#     One decayed number corrected: an earlier revision of this comment cited
#     apollo-server at 588 DDG edges over 93 symbols and express at ZERO. The
#     express-is-zero half is what this change addresses; the apollo-server
#     figure was measured on an older tree and should be re-measured before
#     being cited, not copied forward.
def _jsts_callable_name(node: Any, source: bytes) -> Optional[str]:
    """Name a walked callable exactly as ``js_ts.py`` names it (WI-sakir).

    A ``method_definition`` takes the analyzer's own shared rule
    (``jsts_method_name``: nearest ``class_declaration``, one level). A
    ``function_declaration`` keeps the bare ``name`` field, which is what the
    analyzer emits for a nested function too -- probed, not assumed.
    """
    if node.type == "method_definition":
        return jsts_method_name(node, source)
    name_node = node.child_by_field_name("name")
    if name_node is None:  # pragma: no cover - grammar always names a declaration
        return None
    return source[name_node.start_byte:name_node.end_byte].decode(
        "utf-8", errors="replace",
    )


def _jsts_callable_kind(node: Any) -> str:
    """``method`` / ``getter`` / ``setter`` via the analyzer's own rule, or
    ``function`` for a declaration."""
    if node.type == "method_definition":
        return jsts_method_kind(node)
    return "function"


# WI-sakir / WI-jopuf: ``method_definition`` is walked, and both hooks come
# from js_ts.py rather than re-deriving its naming here -- the duplication
# WI-jopuf declined to introduce. Before this, a class method was never walked
# at all (dash.js: 0 of 766 methods, 0 of 54 getters reached the DDG).
_JSTS_FUNCTION_NODE_TYPES = frozenset({"function_declaration", "method_definition"})

# WI-mufag: callables js_ts.py names after their BINDING (``const h = () =>``,
# ``_cb_<callee>@<col>``, ``_iife``). Walked under the analyzer's own id for the
# node's span, never a name derived here -- see ddg_build's "Callables Named by
# Their Binding". ``function`` is the older grammar's spelling of
# ``function_expression``.
_JSTS_BOUND_CALLABLE_NODE_TYPES = frozenset({
    "arrow_function", "function_expression", "function", "generator_function",
})

# WI-nilir: top-level code is anchored on the file pseudo-symbol and walked
# under it (ddg_build's "Module Code").
_JSTS_MODULE_NODE_TYPES = frozenset({"program"})

# WI-fovus: neither spec names a glob. Each walks every extension
# ``taxonomy.LANGUAGES`` files under its language -- javascript ``.js`` /
# ``.mjs`` / ``.cjs`` / ``.jsx``, typescript ``.ts`` / ``.tsx`` / ``.mts`` /
# ``.cts`` -- which is the list discovery classifies by and ``js_ts.py``'s
# ``find_js_ts_files`` reads, so a file the analyzer emitted symbols for is a
# file the DDG walks. ``.tsx`` (and ``.jsx``) carry JSX, which the ``typescript``
# grammar cannot parse: ``ddg_build`` parses ``.tsx`` with the ``tsx`` grammar
# (``taxonomy.grammar_for_path``) while keeping ``language="typescript"``, so
# the ids still match the analyzer's. JSX needs no ``cfg_nodes`` entry: a JSX
# element is an EXPRESSION, always inside a statement the mapping already
# records, and its identifiers reach the def/use extractor through the generic
# recursion. A name the extractor misses is caught by ``unaccounted_names``,
# which withholds refutation rather than licensing it.
register_ddg_language(LanguageDdgSpec(
    language="typescript",
    function_node_types=_JSTS_FUNCTION_NODE_TYPES,
    module_node_types=_JSTS_MODULE_NODE_TYPES,
    name_for=_jsts_callable_name,
    kind_for=_jsts_callable_kind,
    bound_callable_node_types=_JSTS_BOUND_CALLABLE_NODE_TYPES,
))

# Same function node types, the javascript grammar (which parses JSX itself).
register_ddg_language(LanguageDdgSpec(
    language="javascript",
    function_node_types=_JSTS_FUNCTION_NODE_TYPES,
    module_node_types=_JSTS_MODULE_NODE_TYPES,
    name_for=_jsts_callable_name,
    kind_for=_jsts_callable_kind,
    bound_callable_node_types=_JSTS_BOUND_CALLABLE_NODE_TYPES,
))
