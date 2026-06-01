# SPDX-License-Identifier: AGPL-3.0-or-later
"""Shared per-language dispatchers for Symbol.signature and Symbol.docstring.

This module provides a uniform entry point so analyzer code does not have
to know the per-language extraction logic at every Symbol() emit site.
The dispatcher routes to per-language extractors based on the language
string passed at call time.

Why centralized
---------------
- ``Symbol.signature`` was historically extracted by ad-hoc per-analyzer
  helpers (``_extract_go_signature``, ``_extract_csharp_signature``,
  ``_extract_jsts_signature``, ...). Centralising the dispatch makes
  "what languages have signature?" a one-file lookup, and adding a new
  language only requires editing this module (plus its analyzer's
  ``_extract_X_signature`` helper).
- ``Symbol.docstring`` was previously populated by ``py.py`` (via
  ``ast.get_docstring``) and a subset of tree-sitter analyzers that
  called ``populate_docstrings_from_tree`` at end-of-extraction. Per
  ADR-0033 Phase 4 PR3 (INV-jahiv), all 10 mainstream analyzers now
  populate it. The underlying comment-stripping logic lives in
  :func:`hypergumbo_core.analyze.base.extract_doc_comment` — it already
  understands ``//`` (Go), ``///`` (Rust/C#/Swift), ``/** */`` (Java /
  Kotlin / JS / PHP), and ``#`` (Ruby) comment styles via a single
  unified regex. The dispatcher in this module is therefore a thin
  pass-through that validates the language string and delegates.

What this module does NOT do
----------------------------
- It does not duplicate the comment-stripping logic. That lives in
  ``base.extract_doc_comment`` and is reused as-is.
- It does not replace existing per-analyzer ``_extract_X_signature``
  helpers. Those remain the authoritative source of signature strings;
  the dispatcher merely calls them.
- It does not extend signature/docstring to non-callable Symbols (vars,
  type aliases, fields). Callers decide whether the emit site is for a
  callable kind.

Language strings
----------------
Accepted ``language`` values are the same strings used in
``Symbol.language``: ``"go"``, ``"rust"``, ``"java"``, ``"csharp"``,
``"php"``, ``"javascript"``, ``"typescript"``, ``"ruby"``, ``"kotlin"``,
``"swift"``. Unknown languages return ``None`` (no exception) so callers
can safely use the dispatcher unconditionally during a tree walk that
may visit nodes from multiple grammars.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Final, Optional

from hypergumbo_core.analyze.base import extract_doc_comment

if TYPE_CHECKING:  # pragma: no cover - import-time only
    import tree_sitter


# Language strings recognised by the dispatchers. Kept as a module-level
# constant so test code can iterate the supported set.
SUPPORTED_LANGUAGES = frozenset({
    "go",
    "rust",
    "java",
    "csharp",
    "php",
    "javascript",
    "typescript",
    "ruby",
    "kotlin",
    "swift",
})


# Per-language decision-point node types from each tree-sitter grammar.
# Each occurrence of one of these node types inside a callable subtree
# adds 1 to McCabe cyclomatic complexity (base complexity is 1, the
# implicit entry path). The node-type lists are derived from each
# grammar's ``node-types.json`` / source.c — see ADR-0033 Phase 4 PR5
# (INV-? cyclomatic_complexity sweep).
#
# Conservative scope: we count canonical control-flow constructs
# (if / for / while / do-while / switch-case-default / try-catch /
# ternary / loop / match-arm). Logical short-circuit operators
# (``&&`` / ``||`` / ``??``) are counted via the SHORT_CIRCUIT_OPS
# table below — each occurrence adds 1, matching the "and/or each
# create a new path" rule used by ``py.py`` for ``ast.BoolOp``.
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
}


# Short-circuit logical operators per language. Each occurrence inside a
# callable subtree adds 1 to cyclomatic complexity (mirrors the
# ``ast.BoolOp`` rule in ``py.py``: each ``and``/``or`` introduces a
# new short-circuit branch).
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
}


# Node types known to wrap a binary boolean expression across grammars.
# The cyclomatic walker inspects each such node's anonymous (unnamed)
# operator child and bumps complexity when its text matches the
# language's SHORT_CIRCUIT_OPS set.
_BINARY_EXPR_NODE_TYPES: Final[frozenset[str]] = frozenset({
    "binary_expression",  # Go / Rust / C# / PHP / JS / TS / Kotlin / Swift
    "boolean_operator",   # (Python-only — not actually reached here,
                          #  kept defensive)
    "binary",             # Ruby (tree-sitter-ruby names it ``binary``)
})


def extract_signature(
    node: "tree_sitter.Node",
    source: bytes,
    language: str,
) -> Optional[str]:
    """Dispatch to the per-language signature extractor.

    Returns ``None`` for languages with no extractor registered, or when
    the node has no extractable signature (e.g., a field/var Symbol or
    a node type the extractor does not recognise). Callers should pass
    a node corresponding to a callable declaration; the per-language
    helpers gate on the node type and return ``None`` for unsupported
    shapes.
    """
    if language == "go":
        from .go import _extract_go_signature
        return _extract_go_signature(node, source)
    if language == "rust":
        from .rust import _extract_rust_signature
        return _extract_rust_signature(node, source)
    if language == "java":
        from .java import _extract_java_signature
        # The java helper takes an is_constructor flag; infer from node
        # type so the dispatcher API stays uniform.
        is_constructor = node.type == "constructor_declaration"
        return _extract_java_signature(node, source, is_constructor=is_constructor)
    if language == "csharp":
        from .csharp import _extract_csharp_signature
        is_constructor = node.type == "constructor_declaration"
        return _extract_csharp_signature(
            node, source, is_constructor=is_constructor,
        )
    if language == "php":
        from .php import _extract_php_signature
        return _extract_php_signature(node, source)
    if language in ("javascript", "typescript"):
        from .js_ts import _extract_jsts_signature
        return _extract_jsts_signature(node, source)
    if language == "ruby":
        from .ruby import _extract_ruby_signature
        return _extract_ruby_signature(node, source)
    if language == "kotlin":
        from .kotlin import _extract_kotlin_signature
        return _extract_kotlin_signature(node, source)
    if language == "swift":
        from .swift import _extract_swift_signature
        return _extract_swift_signature(node, source)
    return None


def extract_preceding_doc_comment(
    node: "tree_sitter.Node",
    source: bytes,
    language: str,
) -> Optional[str]:
    """Extract the first line of the doc comment immediately preceding ``node``.

    Returns a string truncated to 80 chars (matching ``py.py``'s
    ``ast.get_docstring`` slicing pattern), or ``None`` if there is no
    preceding doc comment.

    Implementation note
    -------------------
    The underlying logic lives in
    :func:`hypergumbo_core.analyze.base.extract_doc_comment`, which
    walks backwards through ``prev_named_sibling`` collecting
    consecutive comment nodes and strips delimiters using a single
    multi-language regex. The dispatcher in this module exists for API
    uniformity with :func:`extract_signature` and to gate on the
    ``SUPPORTED_LANGUAGES`` set — unknown languages return ``None``
    rather than silently delegating, so callers cannot accidentally
    produce docstrings for languages they have not vetted.
    """
    if language not in SUPPORTED_LANGUAGES:
        return None
    return extract_doc_comment(node, source)


def compute_cyclomatic_complexity(
    node: "tree_sitter.Node",
    language: str,
) -> Optional[int]:
    """Compute McCabe cyclomatic complexity for a callable subtree.

    Iteratively walks the subtree rooted at *node* and counts decision
    points per the language's tree-sitter grammar. The base complexity
    is 1 (the implicit entry path); each branch node and each
    short-circuit operator occurrence adds 1.

    Returns ``None`` for unsupported languages so callers can wire the
    dispatcher into every callable Symbol emit site without per-call
    language gating.

    The walker is iterative (not recursive) to avoid ``RecursionError``
    on deeply nested code — matching the pattern used by every other
    tree-walking helper in this package (see ``iter_tree`` /
    ``populate_docstrings_from_tree``).
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
            # expression — its text is the literal operator string
            # (e.g. ``&&``). We scan the immediate children and bump
            # complexity once per matching operator child. Decoding is
            # best-effort: a non-UTF-8 source slice is skipped silently
            # so the walker never raises on malformed input.
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
