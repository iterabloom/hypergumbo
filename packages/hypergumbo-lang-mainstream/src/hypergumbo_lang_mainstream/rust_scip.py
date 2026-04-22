# SPDX-License-Identifier: AGPL-3.0-or-later
"""SCIP → rust.py stable_id mapping helper (WI-bajuz, ADR-0014 §3).

How It Works
------------
Given a source blob and the 1-based line range of a function definition, parse
the source with tree-sitter-rust, locate the unique ``function_item`` whose
line span matches, and feed the same (kind, normalized_signature, visibility)
triple rust.py uses into ``make_typed_stable_id``. The output is byte-for-byte
identical to the stable_id rust.py would assign the same function.

Why This Design
---------------
The rust-analyzer SCIP backend (WI-duzul) will see every symbol rust.py sees,
plus extra symbols rust.py cannot resolve (trait-dispatched methods, cross-crate
references). For cross-pass dedup to work, **shared** symbols must carry the
same stable_id under both backends — otherwise every Rust symbol would be
double-counted in cached analyses.

The obvious alternative is to reimplement rust.py's signature extractor on top
of SCIP's ``signature_documentation.text`` string. That path drifts: any future
change to rust.py's extraction logic requires a coordinated edit to a parallel
SCIP-side extractor. Instead, this helper re-uses rust.py's existing helpers
verbatim. The cost is one tree-sitter parse per SCIP symbol the translator
emits; the benefit is guaranteed parity.

WI-zakub established that rust-analyzer's SCIP output uses line/col UTF-8
column units for source spans, and that macro-expanded items do not surface
as SCIP symbols. This helper therefore operates on original-source spans and
does not attempt to model macro expansion.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Optional

from hypergumbo_core.analyze.base import (
    make_typed_stable_id,
    visibility_from_modifiers,
)

from hypergumbo_lang_mainstream.rust import (
    _extract_modifiers_rust,
    _extract_rust_signature,
    _get_impl_target,
    is_rust_tree_sitter_available,
    normalize_rust_signature,
)

if TYPE_CHECKING:
    import tree_sitter


def _parse_rust_source(source: bytes) -> Optional["tree_sitter.Tree"]:
    """Parse Rust source, returning None when tree-sitter-rust is unavailable."""
    if not is_rust_tree_sitter_available():
        return None
    import tree_sitter
    import tree_sitter_rust

    language = tree_sitter.Language(tree_sitter_rust.language())
    parser = tree_sitter.Parser(language)
    return parser.parse(source)


def compute_rust_stable_id_from_source(
    source: bytes,
    start_line: int,
    end_line: int,
) -> Optional[str]:
    """Derive rust.py-compatible stable_id for the function at the given span.

    Arguments are 1-based inclusive line numbers, matching the convention
    used by ``Symbol.span`` across the codebase and by rust-analyzer's SCIP
    emit (per WI-zakub). Column precision is not required: a function
    definition is uniquely identified by its start line in well-formed Rust.

    Returns ``None`` when:

    * tree-sitter-rust is unavailable (opt-in backend, graceful-degrade)
    * no ``function_item`` starts at ``start_line`` and ends at ``end_line``
    * signature extraction fails (rust.py itself would return
      ``stable_id=None`` in this case)

    The returned string is identical to what ``rust.py`` would compute for
    the same function definition, enabling dedup when the SCIP-backed
    rust-analyzer pass and the tree-sitter ``rust.py`` pass both index the
    same workspace.
    """
    tree = _parse_rust_source(source)
    if tree is None:
        return None

    from hypergumbo_core.analyze.base import iter_tree

    for node in iter_tree(tree.root_node):
        if node.type != "function_item":
            continue
        node_start = node.start_point[0] + 1
        node_end = node.end_point[0] + 1
        if node_start != start_line or node_end != end_line:
            continue

        signature = _extract_rust_signature(node, source)
        if signature is None:
            return None  # pragma: no cover
        norm_sig = normalize_rust_signature(signature)
        if norm_sig is None:
            return None  # pragma: no cover

        modifiers = _extract_modifiers_rust(node, source)
        visibility = visibility_from_modifiers(modifiers)
        kind = "method" if _get_impl_target(node, source) else "function"

        return make_typed_stable_id(kind, norm_sig, visibility)

    return None
