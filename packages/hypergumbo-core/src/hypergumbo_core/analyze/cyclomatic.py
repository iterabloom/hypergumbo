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
    # Ruby: count per-arm, not the switch wrapper, matching the arms-only
    # convention used by every other language (c ``case_statement`` / java
    # ``switch_label`` / go ``expression_case``). ``when`` = each case/when arm;
    # ``in_clause`` = each case/in pattern-match arm (Ruby 3). The ``case`` /
    # ``case_match`` wrapper nodes are deliberately NOT counted (they would add
    # a spurious +1 per switch). ``if``/``while``/``for``/etc. are single-
    # condition constructs counted once. The ``is_named`` guard in
    # :func:`compute_cyclomatic_complexity` is essential here: tree-sitter-ruby
    # emits a named construct node AND a same-``.type`` anonymous keyword token
    # for each of these, and only the named one is a decision point.
    "ruby": frozenset({
        "if", "unless", "while", "until", "for", "when", "in_clause",
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
    # INV-loguk slice C (common-package batch 1). Node-type names verified by
    # dumping each real grammar (see each owning analyzer's package tests).
    # Haskell: `conditional` = if/then/else (else is a bare keyword, else-if is
    # a nested `conditional`); `alternative` = one per case-of arm (NOT `case`,
    # which collides with the anonymous `case` keyword token); `guards` = one
    # per ``| ...`` guarded alternative. `match` is NOT counted (it wraps every
    # function clause body). Short-circuit &&/|| are named `operator` children
    # of `infix`, unreachable by the unnamed-child scan — deliberately absent.
    "haskell": frozenset({
        "conditional", "alternative", "guards",
    }),
    # OCaml is expression-oriented: control flow ends in _expression. `match_case`
    # = one per match arm AND per try/with handler arm (try_expression's handlers
    # are match_case nodes, so they count automatically — do NOT add
    # try_expression). `else_clause` is the alternative path (excluded); else-if
    # is a nested `if_expression` (counted). &&/|| are named and_operator/
    # or_operator children, unreachable by the unnamed scan — absent.
    "ocaml": frozenset({
        "if_expression", "for_expression", "while_expression", "match_case",
    }),
    # Julia: `elseif_clause` carries its own condition (counted, like bash elif);
    # `else_clause` is the alternative path (excluded). No switch in core Julia.
    # &&/|| are named `operator` children of binary_expression, skipped by the
    # unnamed scan — absent.
    "julia": frozenset({
        "if_statement", "elseif_clause", "for_statement", "while_statement",
        "catch_clause", "ternary_expression",
    }),
    # Erlang: `cr_clause` = case AND receive arms (same node type); `if_clause` =
    # each if guard-arm; `receive_after` = the receive ``after`` timeout branch.
    # `function_clause` is NOT counted (the first clause is the base entry path;
    # additional clauses are aggregated by the analyzer's coalescing wiring).
    # `guard_clause` is NOT counted (it qualifies an already-counted arm).
    # andalso/orelse ARE counted (short-circuiting) via SHORT_CIRCUIT_OPS plus
    # the `binary_op_expr` member added to _BINARY_EXPR_NODE_TYPES below; the
    # non-short-circuiting `and`/`or` are excluded.
    "erlang": frozenset({
        "cr_clause", "if_clause", "receive_after",
    }),
    # Dart: `else` is a bare keyword (else-if = nested `if_statement`, counted).
    # Switch arms are `switch_statement_case` / `switch_statement_default` (both
    # counted, incl. default). do-while = `do_statement`; ternary =
    # `conditional_expression`; try/catch = `catch_clause`. Dart has no
    # `binary_expression`; short-circuit &&/|| are dedicated
    # `logical_and_expression` / `logical_or_expression` nodes (one per
    # operator), counted as branch nodes rather than via the operator scan.
    "dart": frozenset({
        "if_statement", "for_statement", "while_statement", "do_statement",
        "switch_statement_case", "switch_statement_default", "catch_clause",
        "conditional_expression", "logical_and_expression",
        "logical_or_expression",
    }),
    # INV-loguk slice C (mainstream-package batch 2). Node-type names verified
    # by dumping each real grammar (see test_symbol_introspection.py).
    # Scala is expression-oriented: control flow ends in _expression; do-while
    # is a distinct `do_while_expression`. `case_clause` = each match arm AND
    # each try/catch handler arm (catch_clause wraps a case_block of
    # case_clauses, like OCaml's match_case — so catch_clause itself is NOT
    # counted). `else` is a bare keyword (else-if = nested if_expression).
    # &&/|| are named operator_identifier children of infix_expression,
    # unreachable by the unnamed scan — absent.
    "scala": frozenset({
        "if_expression", "for_expression", "while_expression",
        "do_while_expression", "case_clause",
    }),
    # Lua: `elseif_statement` carries its own condition (counted, like bash
    # elif); `repeat_statement` is the repeat/until post-test loop. and/or are
    # short-circuit (unnamed children of binary_expression).
    "lua": frozenset({
        "if_statement", "elseif_statement", "for_statement", "while_statement",
        "repeat_statement",
    }),
    # Perl: if/unless => `conditional_statement`; while/until => `loop_statement`;
    # for/foreach => `for_statement`; ternary => `conditional_expression`; the
    # statement-modifier forms are `postfix_conditional_expression` /
    # `postfix_loop_expression`. `elsif` is DELIBERATELY excluded — the grammar
    # gives the named elsif wrapper AND the bare elsif keyword token the same
    # `.type`, so counting it would double-count (same collision class as
    # haskell `case`); an if/elsif chain conservatively counts as 1.
    # &&/||/// are short-circuit (unnamed children of binary_expression); the
    # word-forms and/or live in a different node and are not counted.
    "perl": frozenset({
        "conditional_statement", "for_statement", "loop_statement",
        "conditional_expression", "postfix_conditional_expression",
        "postfix_loop_expression",
    }),
    # Groovy (Java-shaped): `switch_label` = each case incl. default; else-if is
    # a nested if_statement (else is a bare keyword). &&/|| short-circuit via
    # binary_expression.
    "groovy": frozenset({
        "if_statement", "for_statement", "while_statement", "do_statement",
        "switch_label", "catch_clause", "ternary_expression",
    }),
    # PowerShell: `elseif_clause` carries its own condition (counted); `else_clause`
    # wrapper excluded. `switch_clause` = each switch arm incl. default. Both
    # `for_statement` (C-style) and `foreach_statement`. -and/-or are short-circuit
    # but live in `logical_expression` (added to _BINARY_EXPR_NODE_TYPES below);
    # the non-short-circuiting -xor is excluded by omission from SHORT_CIRCUIT_OPS.
    "powershell": frozenset({
        "if_statement", "elseif_clause", "for_statement", "foreach_statement",
        "while_statement", "do_statement", "switch_clause", "catch_clause",
    }),
    # Objective-C (C-family grammar): `case_statement` = each case incl. default;
    # else-if = nested if_statement inside an else_clause wrapper (wrapper
    # excluded). objc adds @try/@catch over C, hence `catch_clause`. &&/||
    # short-circuit via binary_expression.
    "objc": frozenset({
        "if_statement", "for_statement", "while_statement", "do_statement",
        "case_statement", "conditional_expression", "catch_clause",
    }),
    # INV-loguk slice C (extended1-package batch 3). Node-type names verified by
    # dumping each real grammar (see each owning analyzer's package tests).
    # Zig: `switch_case` = each arm incl. `else =>` default; else-if is a nested
    # if_statement (else_clause wrapper not counted). and/or short-circuit via
    # binary_expression.
    "zig": frozenset({
        "if_statement", "for_statement", "while_statement", "switch_case",
    }),
    # Gleam (pure functional): the only control flow is `case`. `case_clause` =
    # each match arm; `case_clause_guard` = a guarded arm's extra predicate
    # (a real fall-through path, counted like haskell `guards`). &&/|| via
    # binary_expression.
    "gleam": frozenset({
        "case_clause", "case_clause_guard",
    }),
    # Nim: tree-sitter-nim reuses keyword strings (`if`/`for`/`while`/`case`/
    # `try`) as BOTH named construct nodes AND anonymous keyword tokens with the
    # same `.type`, so counting those primaries would double-count (same
    # collision class as perl `elsif`, haskell `case`). The collision-free arm
    # nodes are counted instead: `elif_branch` (own condition), `of_branch`
    # (case arm), `except_branch` (try handler). `else_branch`/`finally_branch`
    # excluded. Conservative: a lone if/for/while with no elif/of/except reports
    # CC=1. and/or short-circuit via `infix_expression` (added below).
    "nim": frozenset({
        "elif_branch", "of_branch", "except_branch",
    }),
    # D (C-family): `case_statement` = each case incl. default; do-while =
    # `do_statement`; ternary = `ternary_expression`. Like Dart, D's &&/|| are
    # dedicated `logical_and`/`logical_or_expression` nodes (one per operator),
    # counted as branch nodes rather than via the operator scan.
    "d": frozenset({
        "if_statement", "for_statement", "while_statement", "do_statement",
        "case_statement", "ternary_expression", "logical_and_expression",
        "logical_or_expression",
    }),
    # V: expression-oriented if (`if_expression`); a single `for_statement`
    # subsumes C-style/for-in/while forms. Match arms are `expression_case` +
    # `default_case` (Go-family node names; match_expression wrapper not
    # counted). &&/|| short-circuit via binary_expression.
    "v": frozenset({
        "if_expression", "for_statement", "expression_case", "default_case",
    }),
    # Hack (Java/PHP-shaped): `switch_case`/`switch_default` arms (default
    # counted); try via `catch_clause`. An if/else-if/else chain is one flat
    # `if_statement` in this grammar (else-if not nested), so it counts once.
    # &&/||/?? short-circuit via binary_expression.
    "hack": frozenset({
        "if_statement", "for_statement", "foreach_statement", "while_statement",
        "do_statement", "switch_case", "switch_default", "catch_clause",
        "ternary_expression",
    }),
    # Haxe: this grammar is degenerate — `else if`/do-while/try do not parse,
    # and for/while have NO dedicated node type (they surface only as a generic
    # `keyword` node, deliberately NOT counted to avoid over-counting). So only
    # `conditional_statement` (if) and `case_statement` (switch arms incl.
    # default) are counted; loops are uncounted (CC still non-null). &&/|| are
    # named `operator` children unreachable by the scan — no short-circuit.
    "haxe": frozenset({
        "conditional_statement", "case_statement",
    }),
    # Odin: `else_if_clause` carries its own condition (counted); `else_clause`
    # excluded. `switch_case` = each arm incl. the value-less default `case:`.
    # A single `for_statement` covers C-style and condition-only loops. &&/||
    # short-circuit via binary_expression.
    "odin": frozenset({
        "if_statement", "else_if_clause", "for_statement", "switch_case",
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
    # Erlang's short-circuiting ``andalso``/``orelse`` live as unnamed children
    # of ``binary_op_expr`` (added to _BINARY_EXPR_NODE_TYPES below). The
    # non-short-circuiting ``and``/``or`` always evaluate both operands (no extra
    # path) and are deliberately excluded. (INV-loguk slice C.)
    "erlang": frozenset({"andalso", "orelse"}),
    # INV-loguk slice C batch 2. Lua/Perl/Groovy/Obj-C short-circuit operators
    # live as unnamed children of ``binary_expression`` (already in the set).
    # Lua's word-forms ``and``/``or`` ARE short-circuiting. Perl counts the
    # symbolic ``&&``/``||``/``//`` (defined-or); its word-forms ``and``/``or``
    # live in a different node and are not counted.
    "lua": frozenset({"and", "or"}),
    "perl": frozenset({"&&", "||", "//"}),
    "groovy": frozenset({"&&", "||"}),
    "objc": frozenset({"&&", "||"}),
    # PowerShell's ``-and``/``-or`` live as unnamed children of
    # ``logical_expression`` (added to _BINARY_EXPR_NODE_TYPES below). The
    # non-short-circuiting ``-xor`` is excluded by omission.
    "powershell": frozenset({"-and", "-or"}),
    # INV-loguk slice C batch 3 (extended1). Zig/V/Odin/Hack short-circuit live
    # as unnamed children of ``binary_expression``; Zig uses word-forms
    # ``and``/``or``. Gleam uses symbolic ``&&``/``||``. Hack adds ``??``
    # (null-coalesce, short-circuiting). Nim's ``and``/``or`` live in
    # ``infix_expression`` (added to _BINARY_EXPR_NODE_TYPES below). (D and Haxe
    # are absent — D counts logical_*_expression branch nodes; Haxe's operators
    # are unreachable named children.)
    "zig": frozenset({"and", "or"}),
    "gleam": frozenset({"&&", "||"}),
    "nim": frozenset({"and", "or"}),
    "v": frozenset({"&&", "||"}),
    "hack": frozenset({"&&", "||", "??"}),
    "odin": frozenset({"&&", "||"}),
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
    "binary_op_expr",     # Erlang (andalso / orelse live here, INV-loguk slice C)
    "logical_expression", # PowerShell (-and / -or live here, INV-loguk slice C)
    "infix_expression",   # Nim (and / or live here, INV-loguk slice C batch 3).
                          #   Scala also produces infix_expression but its
                          #   operators are NAMED children and it is absent from
                          #   SHORT_CIRCUIT_OPS, so the scan never runs for it.
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
        # The ``is_named`` guard is essential: several grammars (notably
        # tree-sitter-ruby and tree-sitter-nim) emit BOTH a named construct
        # node AND an anonymous keyword token sharing the same ``.type`` string
        # (e.g. a named ``if`` expression node and the bare ``if`` keyword token
        # are both ``.type == "if"``). Decision points are always *named*
        # construct/clause nodes, so requiring ``is_named`` counts each branch
        # exactly once and prevents the keyword-token double-count. (This is why
        # ``BRANCH_NODE_TYPES["ruby"]`` can safely list ``if``/``while``/etc.)
        if current.is_named and current_type in branch_types:
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
