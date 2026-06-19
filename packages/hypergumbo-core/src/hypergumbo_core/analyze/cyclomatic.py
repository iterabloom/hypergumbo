# SPDX-License-Identifier: AGPL-3.0-or-later
"""Grammar-agnostic McCabe cyclomatic-complexity walker + decision-point registry.

Why this lives in ``hypergumbo-core``
-------------------------------------
The cyclomatic-complexity machinery began life inside
``hypergumbo_lang_mainstream.symbol_introspection`` (alongside the
signature/docstring dispatchers). That was fine while only mainstream
analyzers populated ``Symbol.cyclomatic_complexity`` — but ``Symbol.kind``
callables are emitted by analyzers in *other* packages too (``solidity`` in
``hypergumbo-lang-extended1``, ``wgsl`` in ``hypergumbo-lang-common``), and
those packages depend only on ``hypergumbo-core`` — they CANNOT import the
mainstream module. Per INV-loguk ("every function-kind Symbol carries
non-null ``cyclomatic_complexity`` and ``lines_of_code``, regardless of
language"), the walker had to move to the one package every analyzer can
reach. ``symbol_introspection`` re-exports the names defined here, so the
13 mainstream analyzers that ``from ...symbol_introspection import
compute_cyclomatic_complexity`` keep working unchanged.

The walker is deliberately decoupled from any specific grammar: it touches
only ``node.type`` / ``node.children`` / ``node.is_named`` / ``node.text``,
so it is exercised in this package with lightweight duck-typed nodes
(see ``tests/test_cyclomatic.py``) while the per-language node-type names in
``BRANCH_NODE_TYPES`` are verified against *real* tree-sitter grammars in
each analyzer package's own test suite (``test_symbol_introspection.py`` for
the mainstream + bash/c/cpp set, ``test_solidity.py`` / ``test_wgsl.py`` for
the relocated-here additions).

Counting model
--------------
McCabe complexity = 1 (the implicit entry path) + one per decision point.
Decision points are:

- Each occurrence of a node type listed in ``BRANCH_NODE_TYPES[language]``
  (canonical control flow: ``if`` / ``for`` / ``while`` / ``do-while`` /
  ``switch``-case / ``try``-catch / ternary / loop / match-arm). The
  ``else`` arm of an ``if`` is deliberately NOT a decision point (it is the
  alternative path of an already-counted ``if``); an ``else if`` *is*
  counted because every grammar here represents it as a nested ``if``
  node, mirroring ``py.py`` treating each ``elif`` as its own ``ast.If``.
- Each short-circuit logical operator (``&&`` / ``||`` / ``??`` / language
  word-forms) listed in ``SHORT_CIRCUIT_OPS[language]``, mirroring the
  ``ast.BoolOp`` rule ``py.py`` uses (each ``and``/``or`` opens a new path).

Both tables are conservative: a language absent from ``BRANCH_NODE_TYPES``
yields ``None`` (not an exception), so the dispatcher can be wired into
every callable Symbol emit site without per-call language gating, and a
language present in ``BRANCH_NODE_TYPES`` but absent from
``SHORT_CIRCUIT_OPS`` simply does not count short-circuit operators (e.g.
``bash``, whose ``&&``/``||`` live in ``list`` nodes outside the shared
binary-expression scope).
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Final, Optional

if TYPE_CHECKING:  # pragma: no cover - import-time only
    import tree_sitter


# Per-language decision-point node types from each tree-sitter grammar.
# Each occurrence of one of these node types inside a callable subtree adds
# 1 to McCabe cyclomatic complexity (base complexity is 1, the implicit
# entry path). The node-type lists are derived from each grammar's
# ``node-types.json`` / source.c and verified against the real grammar in
# the owning analyzer package's test suite.
BRANCH_NODE_TYPES: Final[dict[str, frozenset[str]]] = {
    "go": frozenset({
        "if_statement", "for_statement", "expression_case", "type_case",
        "communication_case", "default_case",
    }),
    "rust": frozenset({
        "if_expression", "for_expression", "while_expression",
        "loop_expression", "match_arm", "try_expression",
    }),
    "java": frozenset({
        "if_statement", "for_statement", "enhanced_for_statement",
        "while_statement", "do_statement", "switch_label", "catch_clause",
        "ternary_expression",
    }),
    "csharp": frozenset({
        "if_statement", "for_statement", "foreach_statement",
        "while_statement", "do_statement", "switch_section", "catch_clause",
        "conditional_expression",
    }),
    "php": frozenset({
        "if_statement", "for_statement", "foreach_statement",
        "while_statement", "do_statement", "case_statement",
        "catch_clause", "conditional_expression",
    }),
    "javascript": frozenset({
        "if_statement", "for_statement", "for_in_statement",
        "while_statement", "do_statement", "switch_case", "switch_default",
        "catch_clause", "ternary_expression",
    }),
    "typescript": frozenset({
        "if_statement", "for_statement", "for_in_statement",
        "while_statement", "do_statement", "switch_case", "switch_default",
        "catch_clause", "ternary_expression",
    }),
    "ruby": frozenset({
        "if", "unless", "while", "until", "for", "case", "when",
        "rescue", "ternary",
    }),
    "kotlin": frozenset({
        "if_expression", "for_statement", "while_statement",
        "do_while_statement", "when_entry", "catch_block",
    }),
    "swift": frozenset({
        "if_statement", "for_statement", "while_statement",
        "repeat_while_statement", "switch_statement",
        "case_statement", "catch_block", "ternary_expression",
        "guard_statement",
    }),
    # CC-only languages (INV-loguk): a cyclomatic-complexity table but no
    # signature/docstring extractor in symbol_introspection, so they appear
    # here without being in SUPPORTED_LANGUAGES. ``else_clause`` is NOT
    # counted (alternative path of an already-counted ``if``); ``elif_clause``
    # (bash) IS counted, mirroring py.py treating each ``elif`` as an
    # ``ast.If``.
    "bash": frozenset({
        "if_statement", "elif_clause", "for_statement", "while_statement",
        "case_item",
    }),
    "c": frozenset({
        "if_statement", "for_statement", "while_statement", "do_statement",
        "case_statement", "conditional_expression",
    }),
    "cpp": frozenset({
        "if_statement", "for_statement", "for_range_loop", "while_statement",
        "do_statement", "case_statement", "conditional_expression",
        "catch_clause",
    }),
    # INV-loguk slice B (relocated-here additions). Node-type names verified
    # against tree-sitter-solidity / tree-sitter-wgsl (see the owning
    # packages' test_solidity.py / test_wgsl.py). Solidity ``else if`` is a
    # nested ``if_statement`` (``else`` is a bare keyword, no node), so each
    # branch counts once; Solidity has no ``switch``. WGSL ``else if`` is a
    # nested ``if_statement`` inside an ``else_statement`` wrapper (the
    # wrapper is NOT counted); each switch arm — including ``default`` — is a
    # ``case_compound_statement`` (matching c/java counting ``default``);
    # ``loop_statement`` is WGSL's infinite loop, counted like for/while.
    "solidity": frozenset({
        "if_statement", "for_statement", "while_statement",
        "do_while_statement", "catch_clause", "ternary_expression",
    }),
    "wgsl": frozenset({
        "if_statement", "for_statement", "while_statement", "loop_statement",
        "case_compound_statement",
    }),
}


# Short-circuit logical operators per language. Each occurrence inside a
# callable subtree adds 1 to cyclomatic complexity (mirrors the ``ast.BoolOp``
# rule in ``py.py``: each ``and``/``or`` introduces a new short-circuit path).
SHORT_CIRCUIT_OPS: Final[dict[str, frozenset[str]]] = {
    "go": frozenset({"&&", "||"}),
    "rust": frozenset({"&&", "||"}),
    "java": frozenset({"&&", "||"}),
    "csharp": frozenset({"&&", "||"}),
    "php": frozenset({"&&", "||", "and", "or"}),
    "javascript": frozenset({"&&", "||", "??"}),
    "typescript": frozenset({"&&", "||", "??"}),
    "ruby": frozenset({"&&", "||", "and", "or"}),
    "kotlin": frozenset({"&&", "||"}),
    "swift": frozenset({"&&", "||"}),
    # C/C++ ``&&``/``||`` live in ``binary_expression`` nodes (in
    # _BINARY_EXPR_NODE_TYPES). Bash is intentionally absent: its command-list
    # ``&&``/``||`` live in ``list`` nodes (see the bash AST), outside the
    # shared binary-expression scope, so they are not counted (conservative).
    "c": frozenset({"&&", "||"}),
    "cpp": frozenset({"&&", "||"}),
    # Solidity / WGSL short-circuit ``&&``/``||`` also live in
    # ``binary_expression`` nodes (verified by dumping the grammars).
    "solidity": frozenset({"&&", "||"}),
    "wgsl": frozenset({"&&", "||"}),
}


# Node types known to wrap a binary boolean expression across grammars.
# The cyclomatic walker inspects each such node's anonymous (unnamed)
# operator child and bumps complexity when its text matches the language's
# SHORT_CIRCUIT_OPS set.
_BINARY_EXPR_NODE_TYPES: Final[frozenset[str]] = frozenset({
    "binary_expression",  # Go / Rust / C# / PHP / JS / TS / Kotlin / Swift /
                          #   C / C++ / Solidity / WGSL
    "boolean_operator",   # (Python-only — not actually reached here,
                          #  kept defensive)
    "binary",             # Ruby (tree-sitter-ruby names it ``binary``)
})


def compute_cyclomatic_complexity(
    node: "tree_sitter.Node",
    language: str,
) -> Optional[int]:
    """Compute McCabe cyclomatic complexity for a callable subtree.

    Iteratively walks the subtree rooted at *node* and counts decision
    points per the language's tree-sitter grammar. The base complexity is 1
    (the implicit entry path); each branch node and each short-circuit
    operator occurrence adds 1.

    Returns ``None`` for unsupported languages so callers can wire the
    dispatcher into every callable Symbol emit site without per-call
    language gating.

    The walker is iterative (not recursive) to avoid ``RecursionError`` on
    deeply nested code — matching the pattern used by every other
    tree-walking helper in this package (see ``iter_tree``).
    """
    if language not in BRANCH_NODE_TYPES:
        return None
    branch_types = BRANCH_NODE_TYPES[language]
    short_circuit_ops = SHORT_CIRCUIT_OPS.get(language, frozenset())
    complexity = 1
    stack = [node]
    while stack:
        current = stack.pop()
        current_type = current.type
        if current_type in branch_types:
            complexity += 1
        elif current_type in _BINARY_EXPR_NODE_TYPES and short_circuit_ops:
            # Short-circuit operator detection. tree-sitter exposes the
            # operator as an anonymous (unnamed) child of the binary
            # expression — its text is the literal operator string (e.g.
            # ``&&``). We scan the immediate children and bump complexity
            # once per matching operator child. Decoding is best-effort: a
            # non-UTF-8 source slice is skipped silently so the walker never
            # raises on malformed input.
            for child in current.children:
                if child.is_named:
                    continue
                try:
                    op_text = child.text.decode("utf-8", errors="ignore")
                except (AttributeError, UnicodeDecodeError):  # pragma: no cover - defensive
                    continue
                if op_text in short_circuit_ops:
                    complexity += 1
        stack.extend(current.children)
    return complexity
