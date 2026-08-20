# SPDX-License-Identifier: AGPL-3.0-or-later
"""SCIP → rust.py stable_id mapping helper (WI-bajuz, ADR-0014 §3).

ADR-0014 §3 governs this helper as amended by ADR-0035 §1: ``name`` and
``qualified_name`` became mandatory stable_id inputs at v5, and the
file anchor at v7. The amendment is what makes the input list below
six items rather than the original three.

How It Works
------------
Given a source blob and the 1-based line range of a function definition, parse
the source with tree-sitter-rust, locate the unique ``function_item`` or
``function_signature_item`` (trait method declarations, WI-duguk) whose line
span matches, and feed the same inputs rust.py uses into
``make_typed_stable_id`` — kind, normalized signature and visibility, plus
``name``, ``qualified_name`` (mandatory since v5 / ADR-0035 §1) and
``file_stable_id`` (v7). The output is byte-for-byte identical to the
stable_id rust.py would assign the same function, provided the caller passes
``rel_path``; without it the file anchor is empty and parity is lost.

The file anchor is ``make_file_stable_id("rust", normalize_path(rel_path))``.
It exists because v7 folded the containing file into stable identity, so two
identically-signed functions in different files no longer collide — which
also means a caller that omits ``rel_path`` is not merely losing precision,
it is computing a different id than rust.py will.

Why This Design
---------------
The rust-analyzer SCIP backend (WI-duzul, shipped) sees every symbol rust.py sees,
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
    make_file_stable_id,
    make_typed_stable_id,
    node_text,
    visibility_from_modifiers,
)
from hypergumbo_core.paths import normalize_path

from hypergumbo_lang_mainstream.rust import (
    _extract_modifiers_rust,
    _extract_rust_signature,
    _find_child_by_field,
    _get_impl_target,
    _get_trait_owner,
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
    rel_path: str = "",
) -> Optional[str]:
    """Derive rust.py-compatible stable_id for the function at the given span.

    Arguments are 1-based inclusive line numbers, matching the convention
    used by ``Symbol.span`` across the codebase and by rust-analyzer's SCIP
    emit (per WI-zakub). Column precision is not required: a function
    definition is uniquely identified by its start line in well-formed Rust.

    ``rel_path`` is the symbol's repo-relative path (WI-bokab v7). It is folded
    into the typed-tier ``file_stable_id`` exactly as ``rust.py`` does — both call
    ``make_file_stable_id("rust", normalize_path(path))`` — so the byte-for-byte
    parity contract survives file-anchoring. Callers MUST pass the SAME repo-relative
    path rust.py sees for the file; an empty ``rel_path`` reproduces the pre-v7
    (file-less) id and would NOT match rust.py's anchored id.

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

    # WI-bokab (v7): the file anchor, byte-identical to rust.py's for the same path.
    file_stable_id = make_file_stable_id("rust", normalize_path(rel_path)) if rel_path else ""

    from hypergumbo_core.analyze.base import iter_tree

    for node in iter_tree(tree.root_node):
        # WI-duguk: `function_signature_item` (a trait method with no default
        # body) is now emitted by rust.py as a `method` owned by its trait, so
        # it must be recomputable here too or the dedup contract breaks for
        # every trait declaration — rust-analyzer's SCIP emit indexes trait
        # method declarations, so these are exactly the symbols both passes see.
        if node.type not in ("function_item", "function_signature_item"):
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
        impl_target = _get_impl_target(node, source)
        # WI-duguk: mirrors rust.py's owner resolution — an impl block wins, a
        # trait owns its own declared members, and only a genuinely free
        # function stays unowned.
        owner = impl_target or _get_trait_owner(node, source)
        kind = "method" if owner else "function"

        # Dedup contract (WI-zakub): name/qualified_name must be byte-identical
        # to what rust.py:_analyze_rust computes for the same function_item, so
        # the resulting stable_id matches. rust.py uses func_name (the bare
        # ``name`` field) and full_name (``{impl_target}::{func_name}`` for
        # impl methods, else func_name).
        name_node = _find_child_by_field(node, "name")
        func_name = node_text(name_node, source) if name_node else ""
        full_name = f"{owner}::{func_name}" if owner else func_name

        return make_typed_stable_id(
            kind, norm_sig, visibility,
            name=func_name, qualified_name=full_name,
            file_stable_id=file_stable_id,
        )

    return None
