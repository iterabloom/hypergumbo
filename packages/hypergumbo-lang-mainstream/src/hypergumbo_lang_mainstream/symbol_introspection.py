# SPDX-License-Identifier: AGPL-3.0-or-later
"""Shared per-language dispatchers for Symbol introspection fields.

This module provides a uniform entry point so analyzer code does not have
to know the per-language extraction logic at every Symbol() emit site.
The dispatcher routes to per-language extractors based on the language
string passed at call time.

Two distinct capability axes
----------------------------
There are *two* independent sets of supported languages here, because the
fields they back are independent capabilities:

- ``SUPPORTED_LANGUAGES`` — languages with a ``signature``/``docstring``
  extractor (the 10 ADR-0033-Phase-4 languages). Gates
  :func:`extract_signature` and :func:`extract_preceding_doc_comment`.
  This set is owned by *this* module.
- ``BRANCH_NODE_TYPES`` — languages with a ``cyclomatic_complexity``
  decision-point table. Gates ``compute_cyclomatic_complexity``. The table
  and the walker now live in
  :mod:`hypergumbo_core.analyze.cyclomatic` (relocated there per INV-loguk
  slice B so analyzer packages outside ``hypergumbo-lang-mainstream`` —
  ``solidity`` in extended1, ``wgsl`` in common — can reach them). They are
  re-exported here for back-compat with the 13 mainstream analyzers that
  ``from ...symbol_introspection import compute_cyclomatic_complexity``.

``BRANCH_NODE_TYPES`` is a *superset*: every signature/docstring language
also computes complexity, but ``bash``/``c``/``cpp``/``solidity``/``wgsl``
compute complexity (INV-loguk) without having a signature/docstring
extractor in this module. Do not conflate the two sets — they are
different questions, and they no longer even live in the same package.

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

from typing import TYPE_CHECKING, Optional

from hypergumbo_core.analyze.base import extract_doc_comment

# The cyclomatic-complexity table + walker were relocated to hypergumbo-core
# (INV-loguk slice B) so analyzer packages outside this one — ``solidity``
# (extended1), ``wgsl`` (common) — can reach them. Re-exported here (see
# ``__all__``) for back-compat with the mainstream analyzers and tests that
# import these names from this module.
from hypergumbo_core.analyze.cyclomatic import (
    BRANCH_NODE_TYPES,
    SHORT_CIRCUIT_OPS,
    compute_cyclomatic_complexity,
)

if TYPE_CHECKING:  # pragma: no cover - import-time only
    import tree_sitter


__all__ = [
    "BRANCH_NODE_TYPES",
    "SHORT_CIRCUIT_OPS",
    "SUPPORTED_LANGUAGES",
    "compute_cyclomatic_complexity",
    "extract_preceding_doc_comment",
    "extract_signature",
]


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
