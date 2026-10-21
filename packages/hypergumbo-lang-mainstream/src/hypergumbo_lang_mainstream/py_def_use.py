# SPDX-License-Identifier: AGPL-3.0-or-later
"""Python def/use extractor for intraprocedural dataflow analysis (ADR-0017 §1c).

Extracts variable definitions (assignments) and uses (reads) from Python
tree-sitter AST nodes. It was the first def/use extractor, validating
the shared CFG infrastructure against hypergumbo's own Python codebase.

How It Works
------------
For each AST node in a function body, the extractor identifies:

- **Defines**: Variables assigned by the node (left side of ``=``, loop
  variables, ``with ... as`` aliases, comprehension variables, ``del``
  targets). Function parameters are not defined by this extractor.
- **Uses**: Variables read by the node (right side of ``=``, function
  call arguments, condition expressions, return values).

The extractor handles Python-specific patterns:
- Simple assignment: ``x = expr`` → defines [x], uses from expr
- Tuple/list unpacking: ``a, b = expr`` → defines [a, b]
- Augmented assignment: ``x += expr`` → defines [x], uses [x] + expr
- For loop: ``for i in items`` → defines [i], uses [items]
- With statement: ``with expr as f`` → defines [f], uses from expr
- Comprehension: ``for x in items`` → defines [x], uses [items]
- Return/yield: ``return expr`` → uses from expr
- Delete: ``del x`` → defines [x] (treated as killing the definition)
- Nested binding: a walrus (``if (m := g()):``, ``h(m := g())``) or a
  chained target (``x = y = e``) defines its name -- weakly (define + use)
  where it may not run, not at all inside a closure. The rule and its
  reasons are on :class:`_Collector` (WI-tozar).

Scope Tracking
--------------
This extractor operates at the statement level within a single function.
It does NOT track nested scopes (closures, nested functions) — those are
separate functions with their own CFGs. Variable names are simple strings;
the reaching-def solver handles aliasing through gen/kill sets.
"""
from __future__ import annotations

from typing import Any, Optional

from hypergumbo_core.cfg import DefUseResult, register_def_use_extractor


def _node_text(node: Any, source: bytes) -> str:
    """Extract text from a tree-sitter node."""
    return source[node.start_byte:node.end_byte].decode("utf-8", errors="replace")


def _collect_identifiers(node: Any, source: bytes) -> list[str]:
    """Recursively collect all identifier names from a node subtree.

    Used for the 'uses' side — collects all variable references in an
    expression tree (right side of assignment, function args, etc.).
    Skips function/method names in call expressions to avoid treating
    the callee as a 'use' of a variable. The bindings nested in the
    subtree are dropped here; :func:`_reads` keeps them.
    """
    collector = _Collector(source)
    collector.walk(node)
    return collector.uses


def _reads(node: Any, source: bytes) -> DefUseResult:
    """The uses of an expression subtree AND the bindings nested inside it."""
    collector = _Collector(source)
    collector.walk(node)
    return DefUseResult(defines=collector.defines, uses=collector.uses)


#: Expressions that evaluate their inner operands zero or more times, so a
#: walrus inside may not run. A comprehension's own walrus binds in the
#: ENCLOSING function (PEP 572), which is why it is a binding here at all;
#: PEP 572 forbids one in the comprehension's first iterable, the only part
#: that always runs.
_COMPREHENSION_TYPES = frozenset({
    "list_comprehension", "set_comprehension", "dictionary_comprehension",
    "generator_expression",
})

#: Bodies whose bindings belong to another scope: a walrus in a lambda binds
#: the lambda's local. Walked for uses only (what they capture is read).
_CLOSURE_TYPES = frozenset({"lambda", "function_definition", "class_definition"})

class _Collector:
    """One expression's uses and nested bindings, in evaluation order.

    THE JAVA/C RULE (WI-tozar): a binding nested inside an expression -- a
    walrus in a condition, on a right-hand side or in a call's arguments, the
    inner target of ``x = y = e`` -- DEFINES its target. Before this the walk
    below read a walrus target as a use, so ``if (t := src()): sink(t)`` had
    no definition of ``t`` and the walk fell back to ``ddg_mixed`` /
    ``not_attempted``.

    WHERE THE VALUE ALSO GOES DOES NOT MATTER, and that is a ruling the walk
    earned. ``h(w := src())`` both binds ``w`` and hands the value to ``h``;
    it shipped as a USE (CONTAINED ONLY) because the §3a walk followed an heir
    on such a line without asking where the call took the value, so the
    definition gave it a seed it could exhaust: ``acc.push(k = t);
    console.log(k.length); fetch(url + acc[0])`` read
    ``confirmed_with_caveats`` (measured 2026-10-20). WI-jusoj asks that
    question on the source's line and WI-tinav on every use line, so the
    binding is now what it is at run time, a definition. Measured 2026-10-21
    with the restriction removed: that shape and its Python twin stay
    ``violated``, and five Python/JS repos moved no verdict, evidence tuple
    or walk result.

    Two refinements Java's walker does not make:

    - CONDITIONAL = a WEAK update. A binding that may not execute (the right
      operand of ``and`` / ``or``, a branch of ``a if c else b``, anything in
      a comprehension) is recorded as a define AND a use of the name, the way
      ``x += e`` is: the value bound before the statement still reaches what
      follows when the binding is skipped. A plain define would KILL that
      value on every path, and the walk could then refute a flow that runs
      whenever the branch is not taken.
    - A binding inside a closure stays a USE, as the whole subtree was before:
      it is the closure's local, not this statement's.
    """

    def __init__(self, source: bytes) -> None:
        self.source = source
        self.defines: list[str] = []
        self.uses: list[str] = []

    def bind(self, names: list[str], scope: "_Scope") -> None:
        """Record ``names`` as bound by this statement (see the class docstring)."""
        if scope.binds:
            self.defines.extend(names)
        if not scope.binds or scope.conditional:
            self.uses.extend(names)

    def walk(self, node: Any, scope: Optional["_Scope"] = None) -> None:
        """Collect uses from ``node`` and the bindings nested in it.

        ``scope`` is where ``node`` sits (see :class:`_Scope`); the default
        is a statement root.
        """
        here = scope or _ROOT
        kind = node.type
        if kind == "identifier":
            name = _node_text(node, self.source)
            # Skip common built-in names that aren't user variables
            if name not in _BUILTIN_NAMES:
                self.uses.append(name)
            return

        if kind == "call":
            # The function part of a call is not a 'use' of a variable
            # (unless it's a variable holding a callable).
            func_node = node.child_by_field_name("function")
            args_node = node.child_by_field_name("arguments")
            if func_node:
                # For simple identifiers like foo(), skip them (they're callees)
                # For attribute access like obj.method(), collect 'obj' as a use
                if func_node.type == "attribute":
                    obj = func_node.child_by_field_name("object")
                    if obj:
                        self.walk(obj, here)
                elif func_node.type != "identifier":
                    self.walk(func_node, here)
            if args_node:
                self.walk(args_node, here)
            return

        if kind == "named_expression":
            # ``(name := value)``: the value is evaluated, then bound, and it
            # also goes wherever the walrus's own value goes.
            self.walk(node.child_by_field_name("value"), here)
            name = node.child_by_field_name("name")
            self.bind([_node_text(name, self.source)], here)
            return

        if kind == "assignment":
            # A statement root, or the inner link of a chain: ``x = y = e``.
            right = node.child_by_field_name("right")
            if right is not None:
                self.walk(right, here)
            left = node.child_by_field_name("left")
            names = _collect_pattern_names(left, self.source)
            if scope is not None and left.type in ("attribute", "subscript"):
                # A nested ``x = self.a = v`` / ``y = d[k] = v`` link mutates
                # the object rather than replacing it, so it reads the object
                # (and index), as Java models every field write and as this
                # subtree read before it was a binding. (A statement-level
                # ``self.a = v`` keeps its older, strong reading.)
                self.walk(left, here)
                if here.binds:
                    self.defines.extend(names)
            else:
                self.bind(names, here)
            return

        if kind == "boolean_operator":
            # ``a and b`` / ``a or b``: ``b`` runs only when ``a`` does not decide.
            self.walk(node.child_by_field_name("left"), here)
            self.walk(node.child_by_field_name("right"), here.maybe())
            return

        if kind == "conditional_expression":
            # ``body if test else alternative``: only the test always runs.
            # The grammar names none of the three, so position decides.
            seen_if = seen_else = False
            for child in node.children:
                if child.type == "if":
                    seen_if = True
                elif child.type == "else":
                    seen_else = True
                elif child.is_named:
                    is_test = seen_if and not seen_else
                    self.walk(child, here if is_test else here.maybe())
            return

        if kind in _CLOSURE_TYPES:
            inner = here.closure()
        elif kind in _COMPREHENSION_TYPES:
            inner = here.maybe()
        else:
            inner = here
        for child in node.children:
            if child.is_named:
                self.walk(child, inner)


class _Scope:
    """Where a nested binding sits, as far as its definition cares.

    ``conditional``: it may not execute (a weak update). ``in_closure``: it
    binds the closure's local, not this statement's, so it defines nothing
    here. Immutable; each narrowing returns a new scope.
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


def _collect_pattern_names(node: Any, source: bytes) -> list[str]:
    """Collect variable names from assignment target patterns.

    Handles simple identifiers, tuple/list unpacking, star expressions,
    and subscript/attribute access (for mutation tracking).
    """
    if node.type == "identifier":
        return [_node_text(node, source)]
    elif node.type in ("pattern_list", "tuple_pattern", "list_pattern"):
        names: list[str] = []
        for child in node.children:
            if child.is_named:
                names.extend(_collect_pattern_names(child, source))
        return names
    elif node.type == "list_splat_pattern":
        # *rest in unpacking
        for child in node.children:
            if child.type == "identifier":
                return [_node_text(child, source)]
        return []  # pragma: no cover
    elif node.type == "attribute":
        # obj.attr = ... → mutates obj
        obj = node.child_by_field_name("object")
        if obj and obj.type == "identifier":
            return [_node_text(obj, source)]
        return []
    elif node.type == "subscript":
        # obj[key] = ... → mutates obj
        obj_node = node.child_by_field_name("value")
        if obj_node and obj_node.type == "identifier":
            return [_node_text(obj_node, source)]
        return []
    return []


# Built-in names that should not be treated as user variable uses
_BUILTIN_NAMES = frozenset({
    "True", "False", "None",
    "print", "len", "range", "int", "str", "float", "bool", "list", "dict",
    "set", "tuple", "type", "isinstance", "issubclass", "hasattr", "getattr",
    "setattr", "delattr", "super", "property", "staticmethod", "classmethod",
    "enumerate", "zip", "map", "filter", "sorted", "reversed", "min", "max",
    "sum", "abs", "all", "any", "iter", "next", "open", "input", "id", "hash",
    "repr", "format", "chr", "ord", "hex", "oct", "bin", "pow", "round",
    "divmod", "callable", "vars", "dir", "globals", "locals", "exec", "eval",
    "compile", "breakpoint", "exit", "quit", "help", "object",
    "Exception", "ValueError", "TypeError", "KeyError", "IndexError",
    "AttributeError", "RuntimeError", "StopIteration", "NotImplementedError",
    "ImportError", "FileNotFoundError", "OSError", "IOError",
    "AssertionError", "ZeroDivisionError", "OverflowError",
})


@register_def_use_extractor("python")
class PythonDefUseExtractor:
    """Extracts variable definitions and uses from Python tree-sitter AST nodes.

    Registered as the "python" def/use extractor. Called by the CFG builder
    for each statement node in a Python function body.
    """

    language = "python"

    def extract(self, node: Any, source: bytes) -> DefUseResult:
        """Return variables defined and used by this AST node."""
        handler = _HANDLERS.get(node.type)
        if handler:
            return handler(node, source)
        # Default: every identifier is a use, and a binding nested anywhere
        # in the node (a walrus in a condition or an argument) a define.
        return _reads(node, source)


# ---------------------------------------------------------------------------
# Per-node-type handlers
# ---------------------------------------------------------------------------


def _handle_assignment(node: Any, source: bytes) -> DefUseResult:
    """Handle simple assignment: x = expr, a, b = expr, x = y = expr.

    The collector owns the rule (right side first, then the targets), so the
    statement and a chain's nested inner link cannot disagree.
    """
    return _reads(node, source)


def _handle_augmented_assignment(node: Any, source: bytes) -> DefUseResult:
    """Handle augmented assignment: x += expr (both defines and uses x)."""
    left = node.child_by_field_name("left")
    right = node.child_by_field_name("right")
    left_names = _collect_pattern_names(left, source) if left else []
    collector = _Collector(source)
    collector.uses.extend(left_names)  # x is used (read) before being modified
    if right:
        collector.walk(right)
    collector.defines.extend(left_names)
    return DefUseResult(defines=collector.defines, uses=collector.uses)


def _handle_return(node: Any, source: bytes) -> DefUseResult:
    """Handle return statement: return expr (the value leaves the function)."""
    collector = _Collector(source)
    for child in node.children:
        if child.is_named:
            collector.walk(child, _ROOT)
    return DefUseResult(defines=collector.defines, uses=collector.uses)


def _handle_delete(node: Any, source: bytes) -> DefUseResult:
    """Handle delete: del x (kills the variable's definition)."""
    defines: list[str] = []
    for child in node.children:
        if child.type == "identifier":
            defines.append(_node_text(child, source))
    return DefUseResult(defines=defines)


def _handle_for(node: Any, source: bytes) -> DefUseResult:
    """Handle for loop header: for i in items."""
    return _for_header(node, source)


def _handle_for_in_clause(node: Any, source: bytes) -> DefUseResult:
    """Handle comprehension for clause: for x in items."""
    return _for_header(node, source)


def _for_header(node: Any, source: bytes) -> DefUseResult:
    """The iterable is read (with any binding nested in it), then the target bound."""
    left = node.child_by_field_name("left")
    right = node.child_by_field_name("right")
    reads = _reads(right, source) if right else DefUseResult()
    defines = reads.defines + (_collect_pattern_names(left, source) if left else [])
    return DefUseResult(defines=defines, uses=reads.uses)


def _with_target_names(node: Any, source: bytes) -> list[str]:
    """Names an ``as`` target binds. A parenthesised or bracketed target is a
    ``tuple`` / ``list`` node here, not a ``*_pattern``, so it is walked on its
    own rather than widening :func:`_collect_pattern_names` for assignments."""
    if node.type in ("tuple", "list", "parenthesized_expression"):
        names: list[str] = []
        for child in node.children:
            if child.is_named:
                names.extend(_with_target_names(child, source))
        return names
    return _collect_pattern_names(node, source)


def _handle_with_clause(node: Any, source: bytes) -> DefUseResult:
    """Handle a with header: ``with open(p) as f, lock:`` (WI-simiv).

    Each ``as`` target is DEFINED by the clause and each value expression is
    USED. Before this handler the clause fell to the default rule, which reads
    every identifier as a use, so ``f`` was never defined; and the CFG
    statement for the clause matched no AST node at all, so the clause was
    uncovered code that forfeited its function's refutation. Both are needed
    before a ``with``-written sink can be adjudicated.
    """
    collector = _Collector(source)
    for item in node.children:
        if item.type != "with_item":
            continue
        value = item.child_by_field_name("value")
        if value is None:  # pragma: no cover - grammar always sets it
            continue
        if value.type == "as_pattern":
            target = value.child_by_field_name("alias")
            expr = next((c for c in value.children if c.is_named), None)
            if expr is not None and expr is not target:
                collector.walk(expr)
            if target is not None:
                for child in target.children:
                    if child.is_named:
                        collector.defines.extend(_with_target_names(child, source))
        else:
            collector.walk(value)
    return DefUseResult(defines=collector.defines, uses=collector.uses)


# Map node types to handler functions
_HANDLERS: dict[str, Any] = {
    "with_clause": _handle_with_clause,
    "assignment": _handle_assignment,
    "augmented_assignment": _handle_augmented_assignment,
    "return_statement": _handle_return,
    "delete_statement": _handle_delete,
    "for_statement": _handle_for,
    "for_in_clause": _handle_for_in_clause,
}
