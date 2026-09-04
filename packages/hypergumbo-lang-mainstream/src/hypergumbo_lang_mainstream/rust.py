# SPDX-License-Identifier: AGPL-3.0-or-later
"""Rust analysis pass using tree-sitter-rust.

This analyzer uses tree-sitter to parse Rust files and extract:
- Function declarations (fn)
- Struct declarations (struct)
- Enum declarations (enum)
- Impl blocks and their methods
- Trait declarations, including bodyless trait methods (``method`` symbols)
- Named struct fields and enum variants (``field`` symbols)
- Module-level ``const`` / ``static`` (``variable`` symbols)
- Function call relationships
- ``implements`` edges for ``impl Trait for Struct``
- ``module_attr_ref`` edges for bare path-attribute reads
- Import relationships (use statements)
- Axum route handlers (.route("/path", get(handler)))
- Actix-web route handlers (#[get("/path")], #[post("/path")])

If tree-sitter with Rust support is not installed, the analyzer
gracefully degrades and returns an empty result.

How It Works
------------
Uses TreeSitterAnalyzer base class for two-pass orchestration:
1. Pass 1: Extract functions, structs, enums, traits with signatures and annotations;
   extract struct field types for the base-class field type registry
2. Pass 2: Extract call edges through a ladder of resolution strategies
   (1b receiver-typed, 1.5 self.field.method(), 1.8/1.9 impl- and
   trait-scoped, 2 short-name fallback), plus ``implements`` edges,
   ``async_spawn`` call edges for spawned closures, calls appearing only
   inside macro bodies, ``module_attr_ref`` edges, use edges, and Axum
   usage contexts
3. Post-process: Extract decorated_by edges from attribute metadata

Parity with the SCIP backend
----------------------------
Symbol ids and ``stable_id`` values emitted here must be byte-identical to
those the ``rust-analyzer`` SCIP backend assigns the same item (WI-zakub), or
the two backends would double-count every shared Rust symbol in a cached
analysis. ``hypergumbo_lang_mainstream.rust_scip`` re-uses this module's own
signature helpers to guarantee that rather than reimplementing them.

The base class handles grammar checking, parser creation, file discovery,
and result assembly. This module provides only the Rust-specific extraction
logic.

Why This Design
---------------
- TreeSitterAnalyzer eliminates boilerplate orchestration code
- Optional dependency keeps base install lightweight
- Uses tree-sitter-rust package for grammar
- Two-pass allows cross-file call resolution
- Route detection enables `hypergumbo routes` command for Rust

Population of ``is_exported`` follows Rust's visibility rule: an item is
exported only when its declaration carries an unqualified ``pub`` modifier;
``pub(crate)`` / ``pub(super)`` / private items are not exported. Two
constructs cannot follow that rule and do not: a bodyless trait method
carries no visibility modifier of its own and is always marked exported,
and an enum variant inherits the *enum's* modifiers rather than carrying
its own.
"""
from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, ClassVar, Iterator, Optional

from hypergumbo_core.analyzer_disclosure import SUPPRESSED_METHOD_NAMES
from hypergumbo_core.discovery import find_files
from hypergumbo_core.ir import (
    Edge, ExternalRef, Span, Symbol, UsageContext, make_pass_id,
)
from hypergumbo_core.qualified_name_axis import separator_for_language
from hypergumbo_core.analyze.base import (
    constructed_from_callee,
    AnalysisResult,
    FileAnalysis,
    TreeSitterAnalyzer,
    defer_bare_method_call,
    emit_module_attribute_refs,
    find_child_by_type,
    iter_tree,
    make_file_id,
    make_file_stable_id,
    make_symbol_id,
    make_typed_stable_id,
    make_unresolved_edge,
    node_text,
    visibility_from_modifiers,
)
from hypergumbo_core.paths import normalize_path
from hypergumbo_core.analyze.registry import register_analyzer
from hypergumbo_lang_mainstream.symbol_introspection import (
    compute_cyclomatic_complexity,
    extract_preceding_doc_comment,
)

from hypergumbo_core.symbol_resolution import ListNameResolver, LookupResult

if TYPE_CHECKING:
    import tree_sitter
    from hypergumbo_core.ir import AnalysisRun
    from hypergumbo_core.symbol_resolution import NameResolver

PASS_ID = make_pass_id("rust")

# Standard library wrapper types that implement Deref to their inner type.
# When resolving struct field types, these are unwrapped to expose the
# effective dispatch type (e.g. Box<App> → App, Arc<Client> → Client).
_RUST_DEREF_WRAPPERS: frozenset[str] = frozenset({
    "Box", "Arc", "Rc", "Mutex", "RwLock", "RefCell", "Cell",
    "Pin", "MutexGuard", "RwLockReadGuard", "RwLockWriteGuard",
})

# Async spawn functions that schedule a task on an executor.
# When we see e.g. tokio::spawn(my_task), the interesting call target
# is ``my_task``, not ``spawn`` itself.
_SPAWN_FUNCTIONS: frozenset[str] = frozenset({
    "tokio::spawn", "tokio::task::spawn",
    "tokio::task::spawn_blocking",
    "rayon::spawn", "rayon::spawn_fifo",
    "async_std::task::spawn",
    "async_std::task::spawn_blocking",
})

# Axum HTTP method functions that define route handlers
# Used by _extract_axum_usage_contexts for YAML pattern matching
AXUM_HTTP_METHODS = {"get", "post", "put", "patch", "delete", "head", "options", "trace"}


def find_rust_files(repo_root: Path) -> Iterator[Path]:
    """Yield all Rust files in the repository."""
    yield from find_files(repo_root, ["*.rs"])


def _find_child_by_field(node: "tree_sitter.Node", field_name: str) -> Optional["tree_sitter.Node"]:
    """Find child by field name."""
    return node.child_by_field_name(field_name)


def _extract_rust_signature(
    node: "tree_sitter.Node", source: bytes
) -> Optional[str]:
    """Extract function signature from a Rust function_item node.

    Returns a signature string like "(x: i32, y: String) -> bool" or None
    if extraction fails.

    WI-duguk: also accepts ``function_signature_item`` — a trait method with no
    default body. That node carries the same ``parameters`` / ``return_type``
    fields as ``function_item`` and differs only in having no ``body``, which
    this extractor never reads. Nothing else changes: for a trait contract the
    signature is the entire content of the member, so declining to extract it
    would make the emitted symbol strictly less useful than its impl-side
    counterpart.

    Args:
        node: A tree-sitter function_item or function_signature_item node.
        source: Source bytes of the file.
    """
    if node.type not in ("function_item", "function_signature_item"):
        return None  # pragma: no cover

    params_node = _find_child_by_field(node, "parameters")
    if not params_node:
        return None  # pragma: no cover

    # Extract parameters
    param_strs: list[str] = []
    for child in params_node.children:
        if child.type == "parameter":
            # Each parameter has pattern and optional type
            pattern_node = _find_child_by_field(child, "pattern")
            type_node = _find_child_by_field(child, "type")

            if pattern_node and type_node:
                param_name = node_text(pattern_node, source)
                param_type = node_text(type_node, source)
                param_strs.append(f"{param_name}: {param_type}")
            elif pattern_node:  # pragma: no cover
                # No type annotation (rare in Rust)
                param_strs.append(node_text(pattern_node, source))
        elif child.type == "self_parameter":
            # Handle &self, &mut self, self, etc.
            self_text = node_text(child, source)
            param_strs.append(self_text)

    sig = "(" + ", ".join(param_strs) + ")"

    # Extract return type if present
    return_type_node = _find_child_by_field(node, "return_type")
    if return_type_node:
        ret_type = node_text(return_type_node, source)
        # Remove the leading "-> " if tree-sitter includes it
        if ret_type.startswith("-> "):  # pragma: no cover
            ret_type = ret_type[3:]
        sig += f" -> {ret_type}"

    return sig


def normalize_rust_signature(
    signature: str | None,
    type_params: list[str] | None = None,
) -> str | None:
    """Normalize a Rust signature for typed stable_id (ADR-0014 §3)."""
    from hypergumbo_core.analyze.base import normalize_signature_names_first
    return normalize_signature_names_first(
        signature, type_params, return_sep="->", skip_self=True,
    )


_RUST_BUILTIN_RETURN_TYPES = frozenset({
    "i8", "i16", "i32", "i64", "i128", "isize",
    "u8", "u16", "u32", "u64", "u128", "usize",
    "f32", "f64", "bool", "char", "str", "String",
    "()", "Self", "self",
})

_RUST_UNWRAP_WRAPPERS = ("Result", "Option", "Box", "Rc", "Arc")


def _rust_split_first_top_level_arg(s: str) -> str:
    """Return the first comma-separated argument of a generic arg list,
    respecting nested ``<...>`` so ``HashMap<K, V>, E`` yields
    ``HashMap<K, V>`` and not ``HashMap<K``.
    """
    depth = 0
    for i, c in enumerate(s):
        if c == "<":
            depth += 1
        elif c == ">":
            depth -= 1
        elif c == "," and depth == 0:
            return s[:i].strip()
    return s.strip()


def _extract_rust_return_type_name(signature: str | None) -> str | None:
    """Extract the return-type name from a Rust function signature.

    WI-titor (INV-dihos Phase 3) return-type registry entry-point for
    Rust. Signatures follow the form ``(params) -> ReturnType`` produced
    by ``_extract_rust_signature``. The extractor:

    * leaves opaque returns unregistered — ``impl Trait`` and
      ``dyn Trait`` (no concrete name for ``var_types`` to consume);
    * strips references and lifetimes — ``&'a T`` / ``&mut T`` / ``&T`` → ``T``;
    * unwraps the WI-titor Tier 2 wrapper set
      (``Result<T,E>`` / ``Option<T>`` / ``Box<T>`` / ``Rc<T>`` / ``Arc<T>``)
      to the first generic argument, recursing once so nested wrappers
      like ``Box<Result<Client,Error>>`` reduce to ``Client``;
    * strips other generics (``Vec<User>`` → ``Vec``, consistent with
      ADR-0006 §Limitations / ``generic_strip_pattern``);
    * strips module paths (``std::sync::Mutex`` → ``Mutex``) because
      symbols are stored under bare names;
    * filters builtins (``i32`` / ``bool`` / ``()`` / ``String`` etc.)
      and non-identifier residues.

    Returns ``None`` when the residue carries no information the
    receiver-type chain can use.
    """
    if not signature:
        return None
    arrow_idx = signature.find("->")
    if arrow_idx < 0:
        return None
    ret_part = signature[arrow_idx + 2:].strip()
    if not ret_part:
        return None  # pragma: no cover - _extract_rust_signature never emits "-> "

    if ret_part.startswith("impl ") or ret_part.startswith("impl<"):
        return None
    if ret_part.startswith("dyn "):
        return None

    while ret_part.startswith("&"):
        ret_part = ret_part[1:].lstrip()
        if ret_part.startswith("'"):
            space_idx = ret_part.find(" ")
            if space_idx < 0:
                return None  # pragma: no cover - lifetime-only return
            ret_part = ret_part[space_idx + 1:].lstrip()
        if ret_part.startswith("mut "):
            ret_part = ret_part[4:].lstrip()
        if not ret_part:
            return None  # pragma: no cover

    for wrapper in _RUST_UNWRAP_WRAPPERS:
        prefix = wrapper + "<"
        if ret_part.startswith(prefix) and ret_part.endswith(">"):
            inner = ret_part[len(prefix):-1].strip()
            first_arg = _rust_split_first_top_level_arg(inner)
            return _extract_rust_return_type_name(f"-> {first_arg}")

    angle_idx = ret_part.find("<")
    if angle_idx >= 0:
        ret_part = ret_part[:angle_idx]

    if "::" in ret_part:
        ret_part = ret_part.rsplit("::", 1)[-1]

    if not ret_part or ret_part in _RUST_BUILTIN_RETURN_TYPES:
        return None
    if ret_part.replace("_", "").isalnum():
        return ret_part
    return None  # pragma: no cover - non-identifier residue (extractor invariants filter earlier)


def _normalize_rust_type_to_bare_name(type_text: str) -> str | None:
    """Reduce a Rust type expression to a bare type identifier or None.

    WI-titor shared helper used by ``_extract_param_types_rust`` and the
    let-binding var_types walker. Mirrors the residue path of
    ``_extract_rust_return_type_name`` (after the arrow-strip and
    wrapper-unwrap steps): strip references and lifetimes, unwrap
    Result/Option/Box/Rc/Arc, strip non-unwrap generics, strip module
    paths, filter builtins / opaque types / non-identifiers.

    Returns ``None`` when no useful concrete name remains — fail-open per
    ADR-0006's "extra edges are preferable to missing edges, but never
    forge a name we can't justify" posture.
    """
    return _extract_rust_return_type_name(f"-> {type_text.strip()}")


def _extract_param_types_rust(
    node: "tree_sitter.Node", source: bytes
) -> dict[str, str]:
    """Extract parameter ``name -> type`` mapping from a Rust function_item.

    WI-titor (INV-dihos Phase 3) var_types bootstrap. The result feeds
    the file-scoped ``var_types`` dict so method calls on typed
    parameters (``fn process(client: Client) { client.send() }``)
    resolve to ``Client::send`` instead of falling through to the
    short-name ambiguity guard.

    Skips ``self`` / ``&self`` / ``&mut self`` (they're handled
    separately by ``_get_impl_target`` + ``field_type_registry``).
    Type-normalization (reference strip, lifetime strip, generic strip,
    builtin filter) goes through ``_normalize_rust_type_to_bare_name``
    so the same rules apply at param sites and at return sites.
    """
    if node.type != "function_item":
        return {}  # pragma: no cover - caller invariant
    params_node = _find_child_by_field(node, "parameters")
    if not params_node:
        return {}  # pragma: no cover - well-formed function_item always has parameters
    out: dict[str, str] = {}
    for child in params_node.children:
        if child.type != "parameter":
            continue
        pattern_node = _find_child_by_field(child, "pattern")
        type_node = _find_child_by_field(child, "type")
        if not pattern_node or not type_node:
            continue  # pragma: no cover - self_parameter has no pattern; pattern-only params (no annotation) are rare in Rust
        param_name = node_text(pattern_node, source)
        param_type = node_text(type_node, source)
        bare = _normalize_rust_type_to_bare_name(param_type)
        if param_name and bare:
            out[param_name] = bare
    return out


def _extract_enum_variant_field_types_rust(
    root_node: "tree_sitter.Node", source: bytes
) -> dict[str, list[str | None]]:
    """Map ``EnumName::Variant`` -> positional tuple-field types.

    Feeds match-arm / ``if let`` / ``while let`` / destructuring-``let`` binding
    inference (WI-kodap): a local destructured from a tuple-struct enum variant
    (``Cmd::Query(q)``) adopts the variant's field type, so a later
    ``q.method()`` resolves to the concrete impl instead of collapsing to a
    short-name-ambiguous method (zoxide's subcommand dispatch left every
    concrete handler with 0 incoming calls). A position whose type is a builtin
    / opaque / non-identifier normalizes to ``None`` (skipped at bind time, but
    the slot is kept so multi-field patterns stay positionally aligned). Unit
    and struct-like (``Named { x: Foo }``) variants are not indexed — their
    bindings are field-named, not positional.
    """
    result: dict[str, list[str | None]] = {}
    for node in iter_tree(root_node):
        if node.type != "enum_item":
            continue
        enum_name_node = _find_child_by_field(node, "name")
        if enum_name_node is None:  # pragma: no cover - grammar invariant
            continue
        enum_name = node_text(enum_name_node, source)
        for variant in iter_tree(node):
            if variant.type != "enum_variant":
                continue
            v_name_node = find_child_by_type(variant, "identifier")
            ofdl = find_child_by_type(variant, "ordered_field_declaration_list")
            if v_name_node is None or ofdl is None:
                continue  # unit variant or struct-like variant
            field_types = [
                _normalize_rust_type_to_bare_name(node_text(c, source))
                for c in ofdl.children
                if c.is_named and c.type != "visibility_modifier"
            ]
            if field_types:
                variant_name = node_text(v_name_node, source)
                result[f"{enum_name}::{variant_name}"] = field_types
    return result


def _extract_var_types_rust(
    root_node: "tree_sitter.Node",
    source: bytes,
    method_return_type_registry: dict[str, str] | None,
) -> dict[str, str]:
    """Build a file-scoped ``var_name -> type_name`` map for Rust.

    WI-titor (INV-dihos Phase 3) var_types bootstrap + chained-call
    registry consumption. File-scoped (not method-scoped) matches the
    Java/Kotlin/C#/Go convention per ADR-0006; the documented trade-off
    is occasional false positives when the same variable name carries
    different types across functions (extra edges, never missing edges).

    Sources, in tree walk order (first writer wins, mirrors Go's
    single-assignment posture):

    1. Function parameters from every ``function_item`` (delegated to
       ``_extract_param_types_rust``).
    2. ``let x: Type = ...`` — explicit type annotation, the strongest
       signal.
    3. ``let x = Type::new(...)`` / ``let x = Type::associated_fn(...)``
       — when the callee is a ``scoped_identifier`` whose path resolves
       to a single bare type, ``x`` adopts that type. This is the
       constructor / associated-function convention and matches Go's
       ``s := Server{}`` rule.
    4. ``let x = Type { ... }`` — ``struct_expression`` body, the type
       is the ``name`` field.
    5. ``let x = receiver.method(...)`` — Phase 3 new path: when
       ``receiver`` is already bound in ``var_types`` and the registry
       knows ``ReceiverType::method``'s return type, ``x`` adopts that
       return type. Without the registry, this returns no binding
       (fail-open — matches the pre-WI-titor behavior).

    Returns an empty dict when no patterns match; never raises.
    """
    var_types: dict[str, str] = {}
    registry = method_return_type_registry or {}
    enum_variant_field_types = _extract_enum_variant_field_types_rust(
        root_node, source
    )

    for node in iter_tree(root_node):
        if node.type == "function_item":
            for k, v in _extract_param_types_rust(node, source).items():
                var_types.setdefault(k, v)
        elif node.type == "tuple_struct_pattern":
            # WI-kodap: a tuple-struct enum-variant pattern (match arm, if/while
            # let, or destructuring let) binds each positional local to the
            # variant's field type, so a later `local.method()` resolves to the
            # concrete impl. First-writer-wins, file-scoped (the documented
            # var_types trade-off: extra edges, never missing).
            type_node = _find_child_by_field(node, "type")
            if type_node is None:  # pragma: no cover - grammar invariant
                continue
            field_types = enum_variant_field_types.get(
                node_text(type_node, source)
            )
            if not field_types:
                continue
            # NB: compare by stable node id — child_by_field_name returns a
            # distinct Python wrapper than the same node in ``node.children``,
            # so an ``is`` check would fail to exclude the variant-path node.
            sub_patterns = [
                c
                for c in node.children
                if c.id != type_node.id and c.type not in ("(", ")", ",")
            ]
            for sub, field_type in zip(sub_patterns, field_types, strict=False):
                if field_type is not None and sub.type == "identifier":
                    var_types.setdefault(node_text(sub, source), field_type)
        elif node.type == "let_declaration":
            pattern_node = _find_child_by_field(node, "pattern")
            if pattern_node is None or pattern_node.type != "identifier":
                continue
            var_name = node_text(pattern_node, source)
            if var_name in var_types:
                continue  # first writer wins
            type_node = _find_child_by_field(node, "type")
            if type_node is not None:
                bare = _normalize_rust_type_to_bare_name(
                    node_text(type_node, source)
                )
                if bare:
                    var_types[var_name] = bare
                    continue
            value_node = _find_child_by_field(node, "value")
            if value_node is None:
                continue
            inferred = _infer_type_from_rust_rhs(
                value_node, source, var_types, registry,
            )
            if inferred:
                var_types[var_name] = inferred
    return var_types


def _qualified_rust_type_path(type_text: str, bare: str) -> str | None:
    """Return the WRITTEN module path of a type expression, or None.

    INV-linub L3. ``_normalize_rust_type_to_bare_name`` reduces a type
    expression to its terminal identifier — "strip module paths", per its own
    docstring — which is exactly right for the first-party symbol lookups the
    typed-receiver strategies perform, and exactly wrong for the module slot
    of an edge whose receiver type is EXTERNAL. When the source spells
    ``std::fs::File`` the path is present in the text and is simply discarded.

    This recovers that path WITHOUT re-deriving the type. It is deliberately
    incapable of inventing one: it returns a value only when the written text
    already carries ``::``, and only when the path's terminal segment is the
    ``bare`` name the normalizer produced. That equality check is what binds
    this second reading of the type to the first (LIVE.md's one-fact-two-homes
    rule) — a generic wrapper, an alias, or any shape where the two disagree
    yields None rather than a plausible-looking mismatch.
    """
    text = type_text.strip()
    for prefix in ("&mut ", "&mut", "&"):
        while text.startswith(prefix):
            text = text[len(prefix):].strip()
    while text.startswith("'"):  # lifetime, e.g. `'a str`
        _, _, text = text.partition(" ")
        text = text.strip()
    if text.startswith("mut "):
        text = text[4:].strip()
    text = text.split("<", 1)[0].strip()
    if "::" not in text:
        return None
    if text.rsplit("::", 1)[-1] != bare:
        return None
    return text


def _extract_qualified_var_type_paths(
    root_node: "tree_sitter.Node", source: bytes,
) -> dict[str, str]:
    """File-scoped ``var_name -> WRITTEN qualified type path`` (INV-linub L3).

    A companion to ``_extract_var_types_rust``, not a replacement: that map
    holds BARE names because the typed-receiver strategies look them up in the
    first-party symbol tables, and widening it would change resolution. This
    one is consulted only on the unresolved-external path, where a bare name is
    useless and the module slot would otherwise stay ``external``.

    Covers the three shapes where the source writes the path itself:

      1. ``fn dump(f: &mut std::fs::File)``      — parameter annotation
      2. ``let f: std::fs::File = ...``           — binding annotation
      3. ``let s = std::time::Instant::now();``   — scoped construction

    Shape 3 was the largest single miss in the four-repo measurement (52 of 80
    ``elapsed`` sites). Shapes this does NOT cover — a struct-field receiver
    and a chained receiver — lose the type further upstream, in the receiver
    walk rather than in normalization, and are filed separately.

    First-writer-wins and file-scoped, matching ``_extract_var_types_rust``'s
    documented trade-off exactly, so the two maps never disagree about which
    binding a name refers to.
    """
    paths: dict[str, str] = {}

    def _record(name: str, type_text: str) -> None:
        if name in paths:
            return  # first writer wins, as in _extract_var_types_rust
        bare = _normalize_rust_type_to_bare_name(type_text)
        if not bare:
            return
        qualified = _qualified_rust_type_path(type_text, bare)
        if qualified:
            paths[name] = qualified

    for node in iter_tree(root_node):
        if node.type == "function_item":
            params_node = _find_child_by_field(node, "parameters")
            if params_node is None:  # pragma: no cover - grammar invariant
                continue
            for child in params_node.children:
                if child.type != "parameter":
                    continue
                pattern_node = _find_child_by_field(child, "pattern")
                type_node = _find_child_by_field(child, "type")
                if (  # pragma: no cover - a `parameter` always has both fields
                    pattern_node is None or type_node is None
                ):
                    continue
                if pattern_node.type != "identifier":
                    # A tuple destructure or `_` binds no name to key on.
                    continue
                _record(
                    node_text(pattern_node, source),
                    node_text(type_node, source),
                )
        elif node.type == "let_declaration":
            pattern_node = _find_child_by_field(node, "pattern")
            if pattern_node is None or pattern_node.type != "identifier":
                continue
            var_name = node_text(pattern_node, source)
            type_node = _find_child_by_field(node, "type")
            if type_node is not None:
                _record(var_name, node_text(type_node, source))
                continue
            value_node = _find_child_by_field(node, "value")
            if value_node is None:
                continue
            # Shape 3: `Type::assoc_fn()` where Type is written in full. The
            # constructing call already names the path, so the binding's type
            # is known with no annotation and no `use`. Mirrors the
            # scoped_identifier branch of ``_infer_type_from_rust_rhs`` so the
            # two agree about which node carries the type.
            if value_node.type != "call_expression":
                continue
            func_node = _find_child_by_field(value_node, "function")
            if func_node is None or func_node.type != "scoped_identifier":
                continue
            path_node = _find_child_by_field(func_node, "path")
            if path_node is None:  # pragma: no cover - grammar invariant
                continue
            _record(var_name, node_text(path_node, source))
    return paths


def _infer_type_from_rust_rhs(
    value_node: "tree_sitter.Node",
    source: bytes,
    var_types: dict[str, str],
    method_return_type_registry: dict[str, str],
) -> str | None:
    """Try to infer the type a Rust let-binding RHS would yield.

    Mirrors the union of constructor / struct-expression / chained-call
    rules in ``_extract_var_types_rust``. Returns the bare type name or
    ``None`` (fail-open). Pulled out as a helper so the same logic can
    be reused at edge-extraction time if needed.
    """
    if value_node.type == "struct_expression":
        name_node = _find_child_by_field(value_node, "name")
        if name_node is not None:
            return _normalize_rust_type_to_bare_name(
                node_text(name_node, source)
            )
        return None  # pragma: no cover - struct_expression always has a name field
    if value_node.type == "call_expression":
        func_node = _find_child_by_field(value_node, "function")
        if func_node is None:
            return None  # pragma: no cover - call_expression always has a function field
        if func_node.type == "scoped_identifier":
            path_node = _find_child_by_field(func_node, "path")
            if path_node is not None:
                return _normalize_rust_type_to_bare_name(
                    node_text(path_node, source)
                )
            return None  # pragma: no cover - scoped_identifier always has a path
        if func_node.type == "field_expression":
            value_recv = _find_child_by_field(func_node, "value")
            field_recv = _find_child_by_field(func_node, "field")
            if value_recv is None or field_recv is None:
                return None  # pragma: no cover - field_expression always has both fields
            recv_name = node_text(value_recv, source)
            method_name = node_text(field_recv, source)
            recv_type = var_types.get(recv_name)
            if recv_type:
                key = f"{recv_type}::{method_name}"
                return method_return_type_registry.get(key)
    return None


def _extract_base_type_name(type_node: "tree_sitter.Node", source: bytes) -> str:
    """Extract the base type identifier from a type node.

    Rust type nodes can be complex:
    - type_identifier: simple type like "User" -> return "User"
    - generic_type: "Writer<'a, M, W>" -> extract base type "Writer"
    - reference_type: "&'a M" -> recursively extract inner type "M"
    - scoped_type_identifier: "std::vec::Vec" -> return full path

    Args:
        type_node: A tree-sitter type node.
        source: Source bytes for extracting text.

    Returns:
        The base type identifier string.
    """
    if type_node.type == "type_identifier":
        # Simple type like User
        return node_text(type_node, source)

    if type_node.type == "generic_type":
        # Generic type like Writer<'a, M, W>
        # The 'type' field contains the base type identifier
        base_type = _find_child_by_field(type_node, "type")
        if base_type:
            return _extract_base_type_name(base_type, source)
        # Fallback: return full text
        return node_text(type_node, source)  # pragma: no cover

    if type_node.type == "reference_type":
        # Reference type like &'a M
        # The 'type' field contains the inner type
        inner_type = _find_child_by_field(type_node, "type")
        if inner_type:
            return _extract_base_type_name(inner_type, source)
        # Fallback: return full text
        return node_text(type_node, source)  # pragma: no cover

    if type_node.type == "scoped_type_identifier":
        # Qualified type like std::vec::Vec - keep full path
        return node_text(type_node, source)

    # Other type nodes - return as-is
    return node_text(type_node, source)


def _get_rust_mod_path(
    node: "tree_sitter.Node", source: bytes
) -> list[str]:
    """Walk up the tree to find the enclosing ``mod`` chain (within a single file).

    Returns the module names from outermost to innermost, excluding the
    current node. The crate-level module (``lib.rs`` / ``main.rs``) is
    not represented in the tree-sitter AST — only inline ``mod foo { ... }``
    blocks contribute segments.
    """
    mods: list[str] = []
    current = node.parent
    while current is not None:
        if current.type == "mod_item":
            name_node = _find_child_by_field(current, "name")
            if name_node:
                mods.append(node_text(name_node, source))
        current = current.parent
    return list(reversed(mods))


def _make_rust_qualified_name(
    mod_path: list[str], impl_target: Optional[str], name: str
) -> str:
    """Build a Rust qualified name from mod path + impl target + symbol name.

    Examples:
        - top-level function: ``func_name``
        - method on a type: ``ImplType::method``
        - function inside a mod: ``mod_path::func_name``
        - method inside a mod: ``mod_path::ImplType::method``
    """
    sep = separator_for_language("rust")  # "::"
    parts: list[str] = list(mod_path)
    if impl_target:
        parts.append(impl_target)
    parts.append(name)
    return sep.join(parts)


def _get_impl_target(node: "tree_sitter.Node", source: bytes) -> Optional[str]:
    """Walk up the tree to find the enclosing impl block's target type.

    Args:
        node: The current node.
        source: Source bytes for extracting text.

    Returns:
        The impl target type name, or None if not inside an impl block.
    """
    current = node.parent
    while current is not None:
        if current.type == "impl_item":
            type_node = _find_child_by_field(current, "type")
            if type_node:
                return _extract_base_type_name(type_node, source)
        current = current.parent
    return None


def _get_trait_owner(node: "tree_sitter.Node", source: bytes) -> Optional[str]:
    """Name of the enclosing ``trait_item``, for a member DECLARED in the trait.

    Deliberately separate from :func:`_get_impl_target` rather than folded into
    it. That helper has six callers, most of them in call/receiver resolution
    (``impl_target`` answers "what type is `self` here?"), and a trait is not a
    type a receiver can be — widening it would silently change how those sites
    resolve. This one is used only by the symbol-emission paths, where the
    question is the different one of "what container owns this member?".

    Stops at the first ``impl_item`` and returns ``None``: in
    ``impl Drawable for Service`` the methods belong to **Service**, not to
    Drawable, which is what the impl path has always emitted.
    """
    current = node.parent
    while current is not None:
        if current.type == "impl_item":
            return None
        if current.type == "trait_item":
            name_node = _find_child_by_field(current, "name")
            return node_text(name_node, source) if name_node else None
        current = current.parent
    return None


def _extract_rust_annotations(
    node: "tree_sitter.Node", source: bytes
) -> list[dict[str, object]]:
    """Extract Rust attributes from preceding siblings of a node.

    Rust attributes like #[get("/path")] or #[derive(Debug)] appear as
    `attribute_item` siblings immediately before the declaration they apply to.

    Args:
        node: The declaration node (function_item, struct_item, etc.)
        source: Source bytes for extracting text.

    Returns:
        List of annotation dicts: [{"name": str, "args": list, "kwargs": dict}]
    """
    annotations: list[dict[str, object]] = []

    if node.parent is None:  # pragma: no cover - defensive
        return annotations

    # Find this node's index in parent's children
    parent = node.parent
    node_index = -1
    for i, child in enumerate(parent.children):
        if child == node:
            node_index = i
            break

    if node_index < 0:
        return annotations  # pragma: no cover

    # Walk backwards from this node collecting attribute_items
    # Stop when we hit a non-attribute (another declaration, etc.)
    for i in range(node_index - 1, -1, -1):
        sibling = parent.children[i]
        if sibling.type == "attribute_item":
            # Parse the attribute: #[name(args)] or #[path::to::name(args)]
            attr_text = node_text(sibling, source)
            ann = _parse_rust_attribute(attr_text)
            if ann:
                annotations.append(ann)
        elif sibling.type == "line_comment":
            # Skip comments, they don't break the attribute chain
            continue  # pragma: no cover - rare edge case
        else:
            # Any other node type breaks the chain
            break

    # Reverse to maintain source order (we walked backwards)
    annotations.reverse()
    return annotations


def _parse_rust_attribute(attr_text: str) -> Optional[dict[str, object]]:
    """Parse a Rust attribute string into annotation dict.

    Examples:
        #[get("/path")]         -> {"name": "get", "args": ["/path"], "kwargs": {}}
        #[actix_web::get("/")]  -> {"name": "actix_web::get", "args": ["/"], "kwargs": {}}
        #[derive(Debug, Clone)] -> {"name": "derive", "args": ["Debug", "Clone"], "kwargs": {}}
        #[route("/", method = "GET")] -> {"name": "route", "args": ["/"], "kwargs": {"method": "GET"}}

    Args:
        attr_text: Raw attribute text including #[ and ]

    Returns:
        Parsed annotation dict or None if parsing fails.
    """
    # Strip #[ and ] from outer wrapper
    text = attr_text.strip()
    if text.startswith("#[") and text.endswith("]"):
        text = text[2:-1]
    else:
        return None  # pragma: no cover

    # Find the name (before any parentheses)
    paren_pos = text.find("(")
    if paren_pos == -1:
        # No arguments: #[test] or #[cfg(test)]
        return {"name": text.strip(), "args": [], "kwargs": {}}

    name = text[:paren_pos].strip()
    args_str = text[paren_pos + 1:-1] if text.endswith(")") else ""

    # Parse arguments - handle both positional and named
    args: list[str] = []
    kwargs: dict[str, str] = {}

    if args_str:
        # Simple parsing: split by comma, handle quotes
        # This handles common cases like ("/path") or ("/", method = "GET")
        current_arg = ""
        in_string = False
        string_char = ""

        for char in args_str:
            if char in ('"', "'") and not in_string:
                in_string = True
                string_char = char
                current_arg += char
            elif char == string_char and in_string:
                in_string = False
                current_arg += char
            elif char == "," and not in_string:
                arg = current_arg.strip()
                if arg:
                    _add_rust_arg(arg, args, kwargs)
                current_arg = ""
            else:
                current_arg += char

        # Handle last argument
        arg = current_arg.strip()
        if arg:
            _add_rust_arg(arg, args, kwargs)

    return {"name": name, "args": args, "kwargs": kwargs}


def _add_rust_arg(arg: str, args: list[str], kwargs: dict[str, str]) -> None:
    """Add a parsed argument to either args or kwargs list.

    Args:
        arg: The argument string (might be positional or named)
        args: List to append positional args to
        kwargs: Dict to add named args to
    """
    # Check if it's a named argument (contains = outside of string)
    eq_pos = -1
    in_string = False
    for i, char in enumerate(arg):
        if char in ('"', "'"):
            in_string = not in_string
        elif char == "=" and not in_string:
            eq_pos = i
            break

    if eq_pos > 0:
        # Named argument
        key = arg[:eq_pos].strip()
        value = arg[eq_pos + 1:].strip()
        # Strip quotes from value
        if (value.startswith('"') and value.endswith('"')) or \
           (value.startswith("'") and value.endswith("'")):
            value = value[1:-1]
        kwargs[key] = value
    else:
        # Positional argument - strip quotes
        if (arg.startswith('"') and arg.endswith('"')) or \
           (arg.startswith("'") and arg.endswith("'")):
            arg = arg[1:-1]
        args.append(arg)


def _extract_modifiers_rust(node: "tree_sitter.Node", source: bytes) -> list[str]:
    """Extract visibility modifiers from a Rust declaration node.

    Rust tree-sitter uses a ``visibility_modifier`` child node containing
    ``pub`` optionally followed by a scope like ``(crate)`` or ``(super)``.
    Items without ``visibility_modifier`` are private by default.

    Returns e.g. ``["pub"]``, ``["pub(crate)"]``, or ``[]`` (private).
    """
    modifiers: list[str] = []
    for child in node.children:
        if child.type == "visibility_modifier":
            vis_text = child.text.decode("utf-8", errors="replace") if child.text else "pub"
            modifiers.append(vis_text)
    return modifiers


def _enclosing_impl_block_annotations(
    node: "tree_sitter.Node", source: bytes,
) -> list[dict[str, object]]:
    """Collect annotations from the nearest enclosing ``impl_item`` block.

    Rust attribute macros applied to an ``impl`` block (``#[pymethods]``,
    ``#[async_trait]``, ``#[tonic::async_trait]``, ``#[wasm_bindgen]``,
    …) carry semantics that the language inherits to every method
    declared inside the block. Per-function attribute extraction
    misses that inherited annotation (each method's preceding-sibling
    chain only contains attributes immediately above the ``fn``).

    WI-tijim wired this in for PyO3's ``#[pymethods]`` specifically —
    without the propagation, the pyffi linker's PyO3 detector saw 4 of
    ~100 methods on the canonical PyO3 crate (Robyn) and dropped the
    rest, breaking Python→Rust FFI chain tracing through the bridge.

    Mirrors :func:`_is_inside_cfg_test`'s walk-up-to-container pattern.
    Returns annotations in source order from the nearest impl outward
    (so a method inside ``impl<T> impl Outer { impl Inner { fn m() {} } }``
    would pick up Inner's attributes first; in practice impl blocks
    do not nest in Rust, so the loop runs at most once).
    """
    annotations: list[dict[str, object]] = []
    ancestor = node.parent
    while ancestor is not None:
        if ancestor.type == "impl_item":
            annotations.extend(_extract_rust_annotations(ancestor, source))
        ancestor = ancestor.parent
    return annotations


def _is_inside_cfg_test(
    node: "tree_sitter.Node", source: bytes,
) -> bool:
    """Check whether *node* is nested inside a ``#[cfg(test)]`` module.

    Walks ancestor nodes looking for a ``mod_item`` whose preceding sibling
    attributes include ``#[cfg(test)]``.  This catches the idiomatic Rust
    pattern::

        #[cfg(test)]
        mod tests {
            fn helper() { ... }   // <-- should be marked as test code
        }

    Individual functions already carry their own ``#[test]`` annotation, but
    helper functions and other items inside the module do not.  This function
    bridges that gap so that ``is_test_node`` in the slicer correctly
    excludes them from production slices.
    """
    ancestor = node.parent
    while ancestor is not None:
        if ancestor.type == "mod_item":
            for ann in _extract_rust_annotations(ancestor, source):
                name = ann.get("name", "")
                if name == "cfg" and "test" in ann.get("args", []):
                    return True
        ancestor = ancestor.parent
    return False


def _unwrap_deref_type(type_node: "tree_sitter.Node", source: bytes) -> str:
    """Unwrap Deref-implementing wrapper types to get the inner type.

    Recursively strips ``Box<Arc<T>>`` → ``T``.  When the outermost type
    is not a known wrapper (or the node is a plain ``type_identifier``),
    returns the type text as-is.  References (``&T``, ``&mut T``) are
    also unwrapped.

    Args:
        type_node: A tree-sitter type node from a ``field_declaration``.
        source: Source bytes for text extraction.

    Returns:
        The innermost non-wrapper type name string.
    """
    # Reference types: &T, &mut T → unwrap to inner type
    if type_node.type == "reference_type":
        inner = type_node.child_by_field_name("type")
        if inner:
            return _unwrap_deref_type(inner, source)
        return node_text(type_node, source)  # pragma: no cover — defensive

    # Generic type: Box<T>, Arc<Mutex<T>> → check if wrapper
    if type_node.type == "generic_type":
        base = type_node.child_by_field_name("type")
        if base:
            base_name = node_text(base, source)
            if base_name in _RUST_DEREF_WRAPPERS:
                # Find the type_arguments child and extract the first argument
                args_node = find_child_by_type(type_node, "type_arguments")
                if args_node:
                    for child in args_node.children:
                        if child.is_named:
                            return _unwrap_deref_type(child, source)
            return base_name
        return node_text(type_node, source)  # pragma: no cover — defensive

    # type_identifier: plain type name
    if type_node.type == "type_identifier":
        return node_text(type_node, source)

    # scoped_type_identifier: e.g. std::sync::Mutex
    if type_node.type == "scoped_type_identifier":
        return node_text(type_node, source)

    return node_text(type_node, source)


def _extract_struct_field_types(
    tree: "tree_sitter.Tree", source: bytes,
) -> dict[str, dict[str, str]]:
    """Extract field name → type mappings from struct declarations.

    Walks all ``struct_item`` nodes and their ``field_declaration_list``
    children. Wrapper types (``Box``, ``Arc``, etc.) are unwrapped via
    ``_unwrap_deref_type`` so the registry contains effective dispatch types.

    Tuple structs (``struct Foo(Bar)``) are skipped — their fields are
    positional, not named, so ``self.0.method()`` patterns don't apply.

    Args:
        tree: Parsed tree-sitter tree for a single file.
        source: Source bytes for text extraction.

    Returns:
        Mapping of struct_name → {field_name → type_name}.
    """
    result: dict[str, dict[str, str]] = {}

    for node in iter_tree(tree.root_node):
        if node.type != "struct_item":
            continue
        name_node = node.child_by_field_name("name")
        if not name_node:
            continue  # pragma: no cover — defensive; struct_item always has name
        struct_name = node_text(name_node, source)

        # Find the field_declaration_list (named struct, not tuple struct)
        body = find_child_by_type(node, "field_declaration_list")
        if not body:
            continue

        fields: dict[str, str] = {}
        for child in body.children:
            if child.type != "field_declaration":
                continue
            field_name_node = child.child_by_field_name("name")
            field_type_node = child.child_by_field_name("type")
            if field_name_node and field_type_node:
                fname = node_text(field_name_node, source)
                ftype = _unwrap_deref_type(field_type_node, source)
                fields[fname] = ftype

        if fields:
            result[struct_name] = fields

    return result


def _is_rust_module_level_const(node: "tree_sitter.Node") -> bool:
    """True when a ``const_item`` / ``static_item`` is a MODULE-level value
    binding — directly at file scope (``source_file``) or inside a ``mod`` block
    (``declaration_list`` of a ``mod_item``). Excludes function-body locals
    (parent ``block``) and impl-/trait-associated consts (``declaration_list`` of
    an ``impl_item`` / ``trait_item``), mirroring the module-level-only contract
    of the other variable emitters (WI-jusus F5)."""
    parent = node.parent
    if parent is None:  # pragma: no cover - items always have a parent
        return False
    if parent.type == "source_file":
        return True
    if parent.type == "declaration_list" and parent.parent is not None:
        return parent.parent.type == "mod_item"
    return False


def _extract_symbols_from_file(
    tree: "tree_sitter.Tree",
    source: bytes,
    file_path: str,
    run_id: str,
) -> FileAnalysis:
    """Extract symbols from a single Rust file.

    Uses iterative tree traversal to avoid RecursionError on deeply nested code.
    """
    analysis = FileAnalysis()
    # WI-bokab (v7): file-identity anchor for this file's symbols. ``file_path`` is
    # the repo-relative path (the extract override passes ``rel_path``). Folded into
    # make_typed_stable_id's containing slot so same-name functions/methods in
    # different files hash distinctly. MUST byte-match rust_scip's anchor for the same
    # file (WI-zakub parity) — both use make_file_stable_id("rust", normalize_path(p)).
    file_stable_id = make_file_stable_id("rust", normalize_path(file_path))

    for node in iter_tree(tree.root_node):
        # Function declaration
        if node.type == "function_item":
            name_node = _find_child_by_field(node, "name")
            if name_node:
                func_name = node_text(name_node, source)
                impl_target = _get_impl_target(node, source)
                # WI-duguk: a trait method with a DEFAULT BODY parses as a
                # `function_item` inside the `trait_item`, so it reached here
                # with no impl target and was emitted as a bare `function` —
                # indistinguishable from a module-level free function, and
                # unrooted by the containment linker (whose parent extraction
                # needs a separator in the name). The trait owns it.
                owner = impl_target or _get_trait_owner(node, source)
                if owner:
                    full_name = f"{owner}::{func_name}"
                    kind = "method"
                else:
                    full_name = func_name
                    kind = "function"

                start_line = node.start_point[0] + 1
                end_line = node.end_point[0] + 1

                # Extract function signature
                signature = _extract_rust_signature(node, source)

                # Extract annotations for YAML pattern matching
                annotations = _extract_rust_annotations(node, source)

                # WI-tijim: inherit annotations from the enclosing impl
                # block. PyO3 attribute macros (``#[pymethods]``) and
                # async-trait wrappers go on the impl, not on each method.
                if impl_target:
                    for ann in _enclosing_impl_block_annotations(node, source):
                        if ann not in annotations:
                            annotations.append(ann)

                # Inherit #[cfg(test)] from enclosing module so slicer
                # can exclude helper functions inside test modules.
                if _is_inside_cfg_test(node, source):
                    cfg_test_ann = {
                        "name": "cfg", "args": ["test"], "kwargs": {},
                    }
                    if cfg_test_ann not in annotations:
                        annotations = [*annotations, cfg_test_ann]

                meta: dict[str, object] | None = None
                if annotations:
                    meta = {"annotations": annotations}

                modifiers = _extract_modifiers_rust(node, source)

                # Typed stable_id (ADR-0014 §3)
                norm_sig = normalize_rust_signature(signature)
                stable_id = make_typed_stable_id(
                    kind, norm_sig, visibility_from_modifiers(modifiers),
                    name=func_name, qualified_name=full_name,
                    file_stable_id=file_stable_id,
                ) if norm_sig else None

                mod_path = _get_rust_mod_path(node, source)
                # Phase 6 PR6 (ADR-0034 id_format closure): canonical IDs
                # forbid ``:`` in the name segment. Rust impl-method names
                # use ``::`` (e.g., ``MyStruct::method``); the ID segment
                # collapses to ``.`` while Symbol.name and Symbol.qualified_name
                # keep the native ``::`` form.
                id_name_segment = full_name.replace("::", ".")
                symbol = Symbol(
                    id=make_symbol_id("rust", str(file_path), start_line, end_line, id_name_segment, kind),
                    name=full_name,
                    kind=kind,
                    language="rust",
                    path=str(file_path),
                    span=Span(
                        start_line=start_line,
                        end_line=end_line,
                        start_col=node.start_point[1],
                        end_col=node.end_point[1],
                    ),
                    origin=PASS_ID,
                    origin_run_id=run_id,
                    stable_id=stable_id,
                    signature=signature,
                    docstring=extract_preceding_doc_comment(node, source, "rust"),
                    meta=meta,
                    modifiers=modifiers,
                    line_span=end_line - start_line + 1,
                    is_exported="pub" in modifiers,
                    qualified_name=_make_rust_qualified_name(mod_path, owner, func_name),
                    cyclomatic_complexity=compute_cyclomatic_complexity(node, "rust"),
                )
                analysis.symbols.append(symbol)
                analysis.node_for_symbol[symbol.id] = node
                analysis.symbol_by_name[func_name] = symbol
                analysis.symbol_by_name[full_name] = symbol

                # WI-titor (INV-dihos Phase 3): populate the return-type
                # registry so Pass 2 var_types inference can chain through
                # ``let x = receiver.method()``. Keyed by qualified name
                # (``Receiver::method``) for methods and bare name for
                # free functions; first writer wins downstream.
                ret_name = _extract_rust_return_type_name(signature)
                if ret_name:
                    analysis.method_return_types[full_name] = ret_name

        # Struct declaration
        elif node.type == "struct_item":
            name_node = _find_child_by_field(node, "name")
            if name_node:
                struct_name = node_text(name_node, source)
                start_line = node.start_point[0] + 1
                end_line = node.end_point[0] + 1

                # Extract annotations for YAML pattern matching (e.g., derive macros)
                annotations = _extract_rust_annotations(node, source)
                if _is_inside_cfg_test(node, source):
                    cfg_test_ann = {
                        "name": "cfg", "args": ["test"], "kwargs": {},
                    }
                    if cfg_test_ann not in annotations:
                        annotations = [*annotations, cfg_test_ann]
                meta = {"annotations": annotations} if annotations else None

                struct_modifiers = _extract_modifiers_rust(node, source)
                mod_path = _get_rust_mod_path(node, source)
                symbol = Symbol(
                    id=make_symbol_id("rust", str(file_path), start_line, end_line, struct_name, "struct"),
                    name=struct_name,
                    kind="struct",
                    language="rust",
                    path=str(file_path),
                    span=Span(
                        start_line=start_line,
                        end_line=end_line,
                        start_col=node.start_point[1],
                        end_col=node.end_point[1],
                    ),
                    origin=PASS_ID,
                    origin_run_id=run_id,
                    meta=meta,
                    modifiers=struct_modifiers,
                    line_span=end_line - start_line + 1,
                    is_exported="pub" in struct_modifiers,
                    qualified_name=_make_rust_qualified_name(mod_path, None, struct_name),
                )
                analysis.symbols.append(symbol)
                analysis.node_for_symbol[symbol.id] = node
                analysis.symbol_by_name[struct_name] = symbol

                # WI-jusus (emission-parity F5): emit a kind="field" Symbol per
                # NAMED struct field. Tuple structs (`struct W(i32)`) have no
                # field_declaration_list (positional fields) -> no field symbols.
                body = find_child_by_type(node, "field_declaration_list")
                for fdecl in body.children if body else ():
                    if fdecl.type != "field_declaration":
                        continue
                    fname_node = fdecl.child_by_field_name("name")
                    if fname_node is None:
                        continue  # pragma: no cover - a named field always has a name
                    ftype_node = fdecl.child_by_field_name("type")
                    fname = node_text(fname_node, source)
                    ftype = node_text(ftype_node, source) if ftype_node is not None else None
                    f_modifiers = _extract_modifiers_rust(fdecl, source)
                    # Rust member names use ``::`` (like impl methods,
                    # ``MyStruct::method``); the id name-segment collapses
                    # ``::``->``.`` (canonical ids forbid ``:`` in the name slot).
                    f_full = f"{struct_name}::{fname}"
                    f_start = fdecl.start_point[0] + 1
                    f_end = fdecl.end_point[0] + 1
                    f_qualified = _make_rust_qualified_name(mod_path, struct_name, fname)
                    f_sym = Symbol(
                        id=make_symbol_id("rust", str(file_path), f_start, f_end, f_full.replace("::", "."), "field"),
                        name=f_full,
                        kind="field",
                        language="rust",
                        path=str(file_path),
                        span=Span(
                            start_line=f_start,
                            end_line=f_end,
                            start_col=fdecl.start_point[1],
                            end_col=fdecl.end_point[1],
                        ),
                        origin=PASS_ID,
                        origin_run_id=run_id,
                        modifiers=f_modifiers,
                        signature=ftype,
                        stable_id=make_typed_stable_id(
                            "field", ftype or "",
                            visibility_from_modifiers(f_modifiers),
                            name=fname, qualified_name=f_full,
                            file_stable_id=file_stable_id,
                        ),
                        line_span=f_end - f_start + 1,
                        is_exported="pub" in f_modifiers,
                        qualified_name=f_qualified,
                    )
                    analysis.symbols.append(f_sym)
                    analysis.node_for_symbol[f_sym.id] = node
                    analysis.symbol_by_name[f_full] = f_sym

        # Module-level const / static — WI-jusus (emission-parity F5): a
        # kind="variable" Symbol for each top-level or mod-level value binding.
        # Function-body locals and impl-/trait-associated consts are excluded
        # (see _is_rust_module_level_const).
        elif node.type in ("const_item", "static_item") and _is_rust_module_level_const(node):
            name_node = _find_child_by_field(node, "name")
            if name_node:
                var_name = node_text(name_node, source)
                type_node = _find_child_by_field(node, "type")
                var_type = node_text(type_node, source) if type_node is not None else None
                start_line = node.start_point[0] + 1
                end_line = node.end_point[0] + 1
                v_modifiers = _extract_modifiers_rust(node, source)
                mod_path = _get_rust_mod_path(node, source)
                v_qualified = _make_rust_qualified_name(mod_path, None, var_name)
                v_sym = Symbol(
                    id=make_symbol_id("rust", str(file_path), start_line, end_line, var_name, "variable"),
                    name=var_name,
                    kind="variable",
                    language="rust",
                    path=str(file_path),
                    span=Span(
                        start_line=start_line,
                        end_line=end_line,
                        start_col=node.start_point[1],
                        end_col=node.end_point[1],
                    ),
                    origin=PASS_ID,
                    origin_run_id=run_id,
                    modifiers=v_modifiers,
                    meta=(
                        {"constructed_from": _rs_cf}
                        if (_rs_cf := constructed_from_callee(
                            _find_child_by_field(node, "value"), source))
                        else None
                    ),
                    signature=var_type,
                    stable_id=make_typed_stable_id(
                        "variable", var_type or "",
                        visibility_from_modifiers(v_modifiers),
                        name=var_name, qualified_name=v_qualified,
                        file_stable_id=file_stable_id,
                    ),
                    line_span=end_line - start_line + 1,
                    is_exported="pub" in v_modifiers,
                    qualified_name=v_qualified,
                )
                analysis.symbols.append(v_sym)
                analysis.symbol_by_name[var_name] = v_sym

        # Enum declaration
        elif node.type == "enum_item":
            name_node = _find_child_by_field(node, "name")
            if name_node:
                enum_name = node_text(name_node, source)
                start_line = node.start_point[0] + 1
                end_line = node.end_point[0] + 1

                # Extract annotations for YAML pattern matching
                annotations = _extract_rust_annotations(node, source)
                if _is_inside_cfg_test(node, source):
                    cfg_test_ann = {
                        "name": "cfg", "args": ["test"], "kwargs": {},
                    }
                    if cfg_test_ann not in annotations:
                        annotations = [*annotations, cfg_test_ann]
                meta = {"annotations": annotations} if annotations else None

                enum_modifiers = _extract_modifiers_rust(node, source)
                mod_path = _get_rust_mod_path(node, source)
                symbol = Symbol(
                    id=make_symbol_id("rust", str(file_path), start_line, end_line, enum_name, "enum"),
                    name=enum_name,
                    kind="enum",
                    language="rust",
                    path=str(file_path),
                    span=Span(
                        start_line=start_line,
                        end_line=end_line,
                        start_col=node.start_point[1],
                        end_col=node.end_point[1],
                    ),
                    origin=PASS_ID,
                    origin_run_id=run_id,
                    meta=meta,
                    modifiers=enum_modifiers,
                    line_span=end_line - start_line + 1,
                    is_exported="pub" in enum_modifiers,
                    qualified_name=_make_rust_qualified_name(mod_path, None, enum_name),
                )
                analysis.symbols.append(symbol)
                analysis.node_for_symbol[symbol.id] = node
                analysis.symbol_by_name[enum_name] = symbol

                # WI-duguk: emit a kind="field" Symbol per enum variant, the
                # same shape the struct-field loop above uses and the same
                # choice the D and Nim analyzers already made. Without these
                # the enum is a container with nothing in it, so the
                # containment linker roots nothing and `slice --reverse` from
                # the enum returns the container alone. All three variant
                # shapes (unit `Red`, tuple `Green(i32)`, struct
                # `Blue { hue: u8 }`) are the same `enum_variant` node type
                # and differ only in the body they carry, which is why one
                # branch covers them.
                variant_list = find_child_by_type(node, "enum_variant_list")
                for variant in variant_list.children if variant_list else ():
                    if variant.type != "enum_variant":
                        continue
                    vname_node = variant.child_by_field_name("name")
                    if vname_node is None:
                        continue  # pragma: no cover - a variant always names
                    vname = node_text(vname_node, source)
                    v_modifiers = _extract_modifiers_rust(variant, source)
                    # ``::`` matches the impl-method convention and is one of
                    # the containment linker's separators; the id name-segment
                    # collapses ``::``->``.`` (canonical ids forbid ``:``
                    # in the name slot).
                    v_full = f"{enum_name}::{vname}"
                    v_start = variant.start_point[0] + 1
                    v_end = variant.end_point[0] + 1
                    v_sym = Symbol(
                        id=make_symbol_id(
                            "rust", str(file_path), v_start, v_end,
                            v_full.replace("::", "."), "field",
                        ),
                        name=v_full,
                        kind="field",
                        language="rust",
                        path=str(file_path),
                        span=Span(
                            start_line=v_start,
                            end_line=v_end,
                            start_col=variant.start_point[1],
                            end_col=variant.end_point[1],
                        ),
                        origin=PASS_ID,
                        origin_run_id=run_id,
                        modifiers=v_modifiers,
                        stable_id=make_typed_stable_id(
                            "field", "",
                            visibility_from_modifiers(v_modifiers),
                            name=vname, qualified_name=v_full,
                            file_stable_id=file_stable_id,
                        ),
                        line_span=v_end - v_start + 1,
                        # A variant is as public as its enum: Rust has no
                        # per-variant visibility modifier.
                        is_exported="pub" in enum_modifiers,
                        qualified_name=_make_rust_qualified_name(
                            mod_path, enum_name, vname,
                        ),
                    )
                    analysis.symbols.append(v_sym)
                    analysis.node_for_symbol[v_sym.id] = variant
                    analysis.symbol_by_name[v_full] = v_sym

        # WI-duguk: a trait method with NO default body parses as a
        # `function_signature_item`, a node type the walk never handled, so a
        # pure trait contract produced no members at all. Emitted as a
        # `method` owned by the trait, matching both the impl path
        # (`Service::run`) and the default-bodied trait method handled in the
        # `function_item` branch. Associated `const`/`type` items are distinct
        # node types and are deliberately not swept in — this is the callable
        # surface.
        elif node.type == "function_signature_item":
            name_node = _find_child_by_field(node, "name")
            trait_owner = _get_trait_owner(node, source)
            if name_node and trait_owner:
                sig_name = node_text(name_node, source)
                sig_full = f"{trait_owner}::{sig_name}"
                s_start = node.start_point[0] + 1
                s_end = node.end_point[0] + 1
                s_modifiers = _extract_modifiers_rust(node, source)
                mod_path = _get_rust_mod_path(node, source)
                # Identity is computed exactly as the `function_item` branch
                # does — normalized signature in the type slot, `stable_id=None`
                # when normalization fails — because `rust_scip` recomputes
                # these ids from source for SCIP dedup and the two must be
                # byte-identical (WI-zakub parity, gated by
                # test_rust_scip_stable_id.py).
                s_signature = _extract_rust_signature(node, source)
                s_norm_sig = (
                    normalize_rust_signature(s_signature)
                    if s_signature is not None
                    else None
                )
                sig_sym = Symbol(
                    id=make_symbol_id(
                        "rust", str(file_path), s_start, s_end,
                        sig_full.replace("::", "."), "method",
                    ),
                    name=sig_full,
                    kind="method",
                    language="rust",
                    path=str(file_path),
                    span=Span(
                        start_line=s_start,
                        end_line=s_end,
                        start_col=node.start_point[1],
                        end_col=node.end_point[1],
                    ),
                    origin=PASS_ID,
                    origin_run_id=run_id,
                    modifiers=s_modifiers,
                    # The signature IS the whole content of a trait contract.
                    signature=s_signature,
                    stable_id=make_typed_stable_id(
                        "method", s_norm_sig,
                        visibility_from_modifiers(s_modifiers),
                        name=sig_name, qualified_name=sig_full,
                        file_stable_id=file_stable_id,
                    ) if s_norm_sig else None,
                    line_span=s_end - s_start + 1,
                    # Reachable exactly when the trait is; a trait method
                    # carries no visibility modifier of its own.
                    is_exported=True,
                    qualified_name=_make_rust_qualified_name(
                        mod_path, trait_owner, sig_name,
                    ),
                )
                analysis.symbols.append(sig_sym)
                analysis.node_for_symbol[sig_sym.id] = node
                analysis.symbol_by_name[sig_full] = sig_sym

        # Trait declaration
        elif node.type == "trait_item":
            name_node = _find_child_by_field(node, "name")
            if name_node:
                trait_name = node_text(name_node, source)
                start_line = node.start_point[0] + 1
                end_line = node.end_point[0] + 1

                # Extract annotations for YAML pattern matching
                annotations = _extract_rust_annotations(node, source)
                meta = {"annotations": annotations} if annotations else None

                trait_modifiers = _extract_modifiers_rust(node, source)
                mod_path = _get_rust_mod_path(node, source)
                symbol = Symbol(
                    id=make_symbol_id("rust", str(file_path), start_line, end_line, trait_name, "trait"),
                    name=trait_name,
                    kind="trait",
                    language="rust",
                    path=str(file_path),
                    span=Span(
                        start_line=start_line,
                        end_line=end_line,
                        start_col=node.start_point[1],
                        end_col=node.end_point[1],
                    ),
                    modifiers=trait_modifiers,
                    origin=PASS_ID,
                    origin_run_id=run_id,
                    meta=meta,
                    line_span=end_line - start_line + 1,
                    is_exported="pub" in trait_modifiers,
                    qualified_name=_make_rust_qualified_name(mod_path, None, trait_name),
                )
                analysis.symbols.append(symbol)
                analysis.node_for_symbol[symbol.id] = node
                analysis.symbol_by_name[trait_name] = symbol

    # Extract struct field types for base-class field type registry.
    # Deref wrappers (Box, Arc, etc.) are unwrapped during extraction
    # so the registry contains effective dispatch types.
    analysis.class_field_types = _extract_struct_field_types(tree, source)

    return analysis


def _extract_axum_usage_contexts(
    node: "tree_sitter.Node",
    source: bytes,
    file_path: Path,
    symbol_by_name: dict[str, Symbol],
) -> list[UsageContext]:
    """Extract UsageContext records for Axum route registrations.

    Detects patterns like:
    - .route("/path", get(handler))
    - .route("/users", post(create_user).get(list_users))

    Returns a list of UsageContext records for YAML pattern matching.
    """
    contexts: list[UsageContext] = []

    # Use a stack-based approach to process nodes iteratively
    stack = [node]
    while stack:
        current = stack.pop()

        for child in current.children:
            stack.append(child)

            # Look for method call .route(...)
            if child.type == "call_expression":
                func_node = _find_child_by_field(child, "function")

                if func_node and func_node.type == "field_expression":
                    field_node = _find_child_by_field(func_node, "field")

                    if field_node and node_text(field_node, source) == "route":
                        # Found .route() call - extract arguments
                        args_node = find_child_by_type(child, "arguments")
                        if not args_node:  # pragma: no cover
                            continue

                        route_path = None
                        for arg in args_node.children:
                            if arg.type == "string_literal" and route_path is None:
                                route_path = node_text(arg, source).strip('"')
                                break

                        if not route_path:  # pragma: no cover
                            continue

                        # Extract handler calls (get(handler), post(handler), etc.)
                        for arg in args_node.children:
                            if arg.type == "call_expression":
                                _extract_handler_usage_contexts(
                                    arg, source, file_path, route_path,
                                    symbol_by_name, contexts
                                )

    return contexts


def _extract_handler_usage_contexts(
    call_node: "tree_sitter.Node",
    source: bytes,
    file_path: Path,
    route_path: str,
    symbol_by_name: dict[str, Symbol],
    contexts: list[UsageContext],
) -> None:
    """Extract UsageContext from handler chain like get(handler).post(handler2).

    Iteratively traverses chained method calls.
    """
    current_call = call_node
    while current_call is not None and current_call.type == "call_expression":
        func_node = _find_child_by_field(current_call, "function")
        if not func_node:
            break  # pragma: no cover

        next_call = None
        method_name = None
        handler_name = None

        # Check if this is an HTTP method call like get(handler)
        if func_node.type == "identifier":
            method_name = node_text(func_node, source)
            if method_name in AXUM_HTTP_METHODS:
                args_node = find_child_by_type(current_call, "arguments")
                if args_node:
                    for arg in args_node.children:
                        if arg.type == "identifier":
                            handler_name = node_text(arg, source)
                            break

        # Check for chained methods like get(h1).post(h2)
        elif func_node.type == "field_expression":
            field_node = _find_child_by_field(func_node, "field")
            value_node = _find_child_by_field(func_node, "value")

            if field_node:
                method_name = node_text(field_node, source)
                if method_name in AXUM_HTTP_METHODS:
                    args_node = find_child_by_type(current_call, "arguments")
                    if args_node:
                        for arg in args_node.children:
                            if arg.type == "identifier":
                                handler_name = node_text(arg, source)
                                break

            # Continue traversing the chain
            if value_node and value_node.type == "call_expression":
                next_call = value_node

        # Create UsageContext if we found a valid handler
        if method_name and method_name in AXUM_HTTP_METHODS and handler_name:
            # Try to resolve handler to a symbol reference
            handler_ref = None
            if handler_name in symbol_by_name:
                handler_ref = symbol_by_name[handler_name].id

            span = Span(
                start_line=current_call.start_point[0] + 1,
                end_line=current_call.end_point[0] + 1,
                start_col=current_call.start_point[1],
                end_col=current_call.end_point[1],
            )

            ctx = UsageContext.create(
                kind="call",
                context_name=f"route.{method_name}",  # e.g., "route.get", "route.post"
                position="args[last]",
                path=str(file_path),
                span=span,
                symbol_ref=handler_ref,
                metadata={
                    "route_path": route_path,
                    "http_method": method_name.upper(),
                    "handler_name": handler_name,
                },
            )
            contexts.append(ctx)

        current_call = next_call


def _get_enclosing_function(
    node: "tree_sitter.Node",
    source: bytes,
    local_symbols: dict[str, Symbol],
    span_index: dict[tuple[int, int], Symbol] | None = None,
) -> Optional[Symbol]:
    """Walk up the tree to find the enclosing function.

    Uses a span-based index to avoid the short-name collision where
    ``symbol_by_name["compute"]`` is overwritten by whichever impl
    block is processed last, causing call edges to get the wrong
    ``src``.  Falls back to name-based lookup when no span index is
    provided (backward compatibility).

    Args:
        node: The current node.
        source: Source bytes for extracting text.
        local_symbols: Map of function names to Symbol objects.
        span_index: Optional ``(start_line, end_line) → Symbol`` index.

    Returns:
        The Symbol for the enclosing function, or None if not inside a function.
    """
    current = node.parent
    while current is not None:
        if current.type == "function_item":
            # Prefer span-based lookup (immune to name collisions)
            if span_index is not None:
                start_line = current.start_point[0] + 1
                end_line = current.end_point[0] + 1
                sym = span_index.get((start_line, end_line))
                if sym is not None:
                    return sym
            # Fallback: name-based lookup
            name_node = _find_child_by_field(current, "name")
            if name_node:
                func_name = node_text(name_node, source)
                # Try qualified name first (e.g., "Diff::compute")
                impl_target = _get_impl_target(current, source)
                if impl_target:  # pragma: no cover - defensive fallback
                    qualified = f"{impl_target}::{func_name}"
                    if qualified in local_symbols:
                        return local_symbols[qualified]
                if func_name in local_symbols:
                    return local_symbols[func_name]
        current = current.parent
    return None  # pragma: no cover - defensive


#: Use-tree node types that name an importable path.  ``crate`` / ``super``
#: appear here because a group item may be spelled with either.
_USE_NAME_NODE_TYPES = ("identifier", "scoped_identifier", "crate", "super")


def _join_use_path(prefix: str, segment: str) -> str:
    """Compose a use-path segment onto the prefix its enclosing groups built."""
    return f"{prefix}::{segment}" if prefix else segment


def _use_tree_bindings(
    node: "tree_sitter.Node",
    prefix: str,
    source: bytes,
    out: dict[str, str],
) -> None:
    """Record ``alias -> full path`` for one node of a Rust use-tree.

    ``prefix`` is the path the ENCLOSING groups have accumulated, without a
    trailing ``::``; it is empty at the top of a ``use_declaration``.  The
    walk is recursive because a use-tree nests -- ``use std::{fs::{File}};``
    is a group inside a group -- so the same six node shapes appear at every
    depth and the only thing that changes is the prefix.

    Wildcards are the one shape that deliberately yields nothing: the set of
    names ``use a::*;`` brings into scope is a property of the imported
    module, which the analyzer cannot see, so registering anything here would
    invent a binding rather than record one.  Top-level wildcards have always
    behaved this way; a wildcard INSIDE a group must not suppress its
    siblings.
    """
    kind = node.type

    if kind == "scoped_use_list":
        list_node = find_child_by_type(node, "use_list")
        if list_node is None:  # pragma: no cover - the grammar always pairs them
            return
        # The prefix is read TEXTUALLY, from the start of this node to the
        # opening brace, rather than by enumerating node types.  A use-path
        # prefix may be an ``identifier``, a ``scoped_identifier``, ``crate``,
        # ``self``, ``super``, a repeated ``super::super``, or carry a leading
        # ``::`` -- several node shapes spelling one concept, all of which the
        # source text already spells correctly.
        head = source[node.start_byte : list_node.start_byte].decode(
            "utf-8", errors="replace"
        )
        inner = _join_use_path(prefix, head.strip().rstrip(":"))
        for item in list_node.named_children:
            _use_tree_bindings(item, inner, source, out)
        return

    if kind == "use_as_clause":
        # ``a::b as c`` and ``{self as c}``.  The renamed thing is the first
        # NAMED child; the new name is the last ``identifier``.
        named = node.named_children
        if not named:  # pragma: no cover - the grammar always fills the clause
            return
        path_node = named[0]
        alias_node = None
        for child in node.children:
            if child.type == "identifier":
                alias_node = child
        if alias_node is None:  # pragma: no cover - `as` always names a target
            return
        alias = node_text(alias_node, source)
        # ``use Trait as _;`` imports a trait ANONYMOUSLY -- there is no name
        # to call through, and ``_`` reaches this walk as an ordinary
        # identifier, so trusting the node type would bind the underscore.
        if not alias or alias == "_":
            return
        # A ``self`` here renames the MODULE, so the prefix IS the path;
        # composing it as a segment would build the nonexistent ``a::b::self``.
        full = prefix if path_node.type == "self" else _join_use_path(
            prefix, node_text(path_node, source)
        )
        if full:
            out[alias] = full
        return

    if kind == "self":
        # ``use a::b::{self, C};`` imports the module itself, under its own
        # last segment.  With no prefix (a bare ``use self;``) there is no
        # module to name.
        if prefix:
            out[prefix.rsplit("::", 1)[-1]] = prefix
        return

    if kind in _USE_NAME_NODE_TYPES:
        text = node_text(node, source)
        if text:
            full = _join_use_path(prefix, text)
            out[full.rsplit("::", 1)[-1]] = full
    # Anything else -- ``use_wildcard``, and the ``visibility_modifier`` that
    # ``pub use`` puts beside the use-tree -- binds no name.


def _extract_use_aliases(
    tree: "tree_sitter.Tree",
    source: bytes,
) -> dict[str, str]:
    """Extract use-statement aliases from a parsed Rust tree.

    Maps each imported name to the full path it names, so a later call
    through that name can be split into a module slot and a member slot:

    - ``use crate::module::func;``        -> ``func: crate::module::func``
    - ``use std::io::Write;``             -> ``Write: std::io::Write``
    - ``use foo::bar as baz;``            -> ``baz: foo::bar``
    - ``use std::fs::{File, read};``      -> ``File: std::fs::File``,
      ``read: std::fs::read``

    The last form is why this is a recursive walk rather than three lookups
    (INV-zuvib).  Every non-grouped form is a DIRECT child of
    ``use_declaration``, so the original implementation found them with
    ``find_child_by_type``; a grouped list is wrapped in a ``scoped_use_list``
    one level down, matched none of the three, and registered nothing at all.
    A call through such an import then reached no module slot and matched no
    ``io_primitives`` row -- silently, because the file still parses and the
    call edge is still emitted.

    Returns a dict mapping local alias -> full import path.
    """
    aliases: dict[str, str] = {}
    for node in iter_tree(tree.root_node):
        if node.type != "use_declaration":
            continue
        for child in node.named_children:
            _use_tree_bindings(child, "", source, aliases)
    return aliases


def _extract_macro_call_names(
    token_tree_node: "tree_sitter.Node",
    source: bytes,
) -> list[tuple[str, int]]:
    """Extract call-like patterns from a macro's token_tree.

    Tree-sitter parses macro bodies (tokio::select!, println!, etc.) as flat
    token sequences, not structured AST.  This function pattern-matches
    token sequences to identify likely function/method calls.

    Returns (callee_name, line) pairs.  ``callee_name`` may be qualified
    (``Foo::bar``) or simple (``bar``).
    """
    results: list[tuple[str, int]] = []
    children = token_tree_node.children
    i = 0
    while i < len(children):
        child = children[i]
        # Pattern: identifier ( ... ) — simple call
        # Pattern: identifier :: identifier ( ... ) — scoped call
        # Pattern: self . identifier ( ... ) — method call
        if child.type == "identifier":
            text = node_text(child, source)
            line = child.start_point[0] + 1
            # Look ahead for :: or (
            if i + 1 < len(children):
                nxt = children[i + 1]
                if nxt.type == "::" and i + 3 < len(children):
                    # scoped: Foo::bar(...) or Self::bar(...)
                    name_node = children[i + 2]
                    paren = children[i + 3] if i + 3 < len(children) else None
                    if (
                        name_node.type == "identifier"
                        and paren is not None
                        and paren.type == "token_tree"
                    ):
                        scope = text
                        name = node_text(name_node, source)
                        results.append((f"{scope}::{name}", line))
                        i += 4
                        continue
                elif nxt.type == "token_tree":
                    # simple call: foo(...)
                    results.append((text, line))
                    i += 2
                    continue
        # Handle lowercase self: self.method(...) or self::method(...)
        # Tree-sitter gives lowercase self node type "self", not "identifier"
        elif child.type == "self" and i + 3 < len(children):
            line = child.start_point[0] + 1
            nxt = children[i + 1]
            if nxt.type == ".":
                # self.method(...)
                meth = children[i + 2]
                paren = children[i + 3]
                if meth.type == "identifier" and paren.type == "token_tree":
                    results.append((node_text(meth, source), line))
                    i += 4
                    continue
            # Note: self::foo() means module-relative path, not a method call.
            # It would be handled as a scoped_identifier if tree-sitter
            # parsed it, but inside token_tree it's ambiguous. Skip it.
        # Recurse into nested token_tree (e.g., branches of select!)
        if child.type == "token_tree":
            results.extend(_extract_macro_call_names(child, source))
        i += 1
    return results


def _extract_edges_from_file(
    tree: "tree_sitter.Tree",
    source: bytes,
    file_path: str,
    local_symbols: dict[str, Symbol],
    global_symbols: dict[str, Symbol],
    run_id: str,
    resolver: "NameResolver",
    use_aliases: dict[str, str],
    method_resolver: ListNameResolver | None = None,
    span_index: dict[tuple[int, int], Symbol] | None = None,
    field_type_registry: dict[str, dict[str, str]] | None = None,
    analyzer: "RustAnalyzer | None" = None,
    kind_index: dict[str, list[Symbol]] | None = None,
    var_types: dict[str, str] | None = None,
    var_type_paths: dict[str, str] | None = None,
) -> list[Edge]:
    """Extract call and import edges from a file.

    Uses iterative tree traversal to avoid RecursionError on deeply nested code.

    Args:
        use_aliases: Dict mapping local names to import paths for disambiguation.
        field_type_registry: Aggregated struct field types for receiver resolution.
        analyzer: RustAnalyzer instance for resolve_receiver_type() calls.
        method_resolver: Optional ListNameResolver with ambiguity_threshold for
            method-specific lookups.  When provided, used for method calls
            (``foo.bar()``) to guard against 3+ ambiguous candidates.
        span_index: Optional line-span index for enclosing function detection.
            Built from global_symbols to avoid name collisions in symbol_by_name.
        kind_index: Multi-value short-name → [Symbol] index used by the
            impl_item handler for trait-vs-struct disambiguation
            (BUG-04 / WI-milak). Populated by ``RustAnalyzer.register_symbol``.
            When None or empty (synthetic callers that bypass
            ``register_symbol``), the impl_item handler returns no
            trait_sym for any name and the unresolved-trait branch
            handles edge emission.
        var_types: Optional file-scoped ``var_name -> type_name`` map
            from ``_extract_var_types_rust`` (WI-titor / INV-dihos Phase
            3). When provided, ``receiver.method()`` calls whose
            ``receiver`` is a bound variable resolve directly to
            ``{ReceiverType}::{method}`` — a new strategy that fires
            between the self-field path (Strategy 1.5) and the
            short-name fallback (Strategy 2), bypassing the
            generic-trait-method blocklist for receivers with known
            types.
    """
    _caller_path = str(file_path)
    edges: list[Edge] = []
    file_id = make_file_id("rust", str(file_path))
    _var_types: dict[str, str] = var_types or {}
    _var_type_paths: dict[str, str] = var_type_paths or {}
    # WI-milak / BUG-04: hoisted out of the iter_tree loop so the
    # impl_item ``_lookup_trait`` closure doesn't trigger ruff B023
    # (function-definition-does-not-bind-loop-variable). The contents
    # don't change across nodes within a single file.
    _trait_kind_index = kind_index if kind_index is not None else {}

    for node in iter_tree(tree.root_node):
        # Detect trait implementations: impl Trait for Struct → implements edge
        if node.type == "impl_item":
            trait_node = node.child_by_field_name("trait")
            type_node = node.child_by_field_name("type")
            if trait_node and type_node:
                trait_name = _extract_base_type_name(trait_node, source)
                impl_type_name = _extract_base_type_name(type_node, source)
                if trait_name and impl_type_name:
                    # WI-kahaz: the LHS of ``impl X for Y`` (``X``) must resolve
                    # to a trait. Without this discipline, when a project also
                    # defines a non-trait symbol named ``X`` (e.g. candle's
                    # marker ``struct Clone;`` in cuda_backend/mod.rs:85),
                    # ``impl Clone for Y`` binds to the struct and emits a
                    # spurious confidence-0.95 ``Y implements struct-X`` edge.
                    # Same structural class as WI-zozuz BUG-03 (the
                    # inheritance-linker analogue, fixed in
                    # ``linkers/inheritance.py`` via Rust kind discipline).
                    #
                    # WI-milak / BUG-04: the kind-discipline guard alone is
                    # insufficient when both a trait and a struct of the same
                    # short name exist in different files (candle-core's
                    # ``trait Module`` vs candle-kernels' ``struct Module``).
                    # The single-value ``global_symbols`` dict overwrites on
                    # registration order; the survivor leaks through and the
                    # canonical trait is unreachable. Prefer the kind-segregated
                    # multi-value index populated by ``register_symbol`` —
                    # it sees every same-named candidate, lets us pick the
                    # trait deterministically (same-file first, then by
                    # stable id), and falls through to ``return None``
                    # rather than to a struct/enum when no trait exists.
                    def _lookup_trait(name: str) -> Optional["Symbol"]:
                        candidates = _trait_kind_index.get(name)
                        if candidates is None:
                            return None
                        traits = [c for c in candidates if c.kind == "trait"]
                        if not traits:
                            # Same-named non-trait candidates exist but no
                            # trait — preserve WI-kahaz: don't fall back
                            # to a struct/enum.
                            return None
                        same_file = [t for t in traits if t.path == file_path]
                        if same_file:
                            return same_file[0]
                        return min(traits, key=lambda s: s.id)

                    trait_sym = _lookup_trait(trait_name)
                    # Fallback: for qualified names like module::Trait, try short name
                    if not trait_sym and "::" in trait_name:
                        short_trait = trait_name.rsplit("::", 1)[-1]
                        trait_sym = _lookup_trait(short_trait)
                    impl_sym = local_symbols.get(impl_type_name) or global_symbols.get(impl_type_name)
                    if trait_sym and impl_sym:
                        edges.append(Edge.create(
                            src=impl_sym.id,
                            dst=trait_sym.id,
                            edge_type="implements",
                            line=node.start_point[0] + 1,
                            evidence_type="trait_impl",
                            origin=PASS_ID,
                            origin_run_id=run_id,
                        ))
                    elif not trait_sym and impl_sym:
                        # Unresolved trait: definition not in analyzed files
                        # (e.g., tonic gRPC traits generated from .proto).
                        # Create a lower-confidence edge so the relationship
                        # is captured even when the trait source isn't available.
                        short_name = trait_name.rsplit("::", 1)[-1] if "::" in trait_name else trait_name
                        # Allow error-related traits through for error types
                        is_exempt = (
                            short_name in _ERROR_TRAIT_EXEMPTIONS
                            and _is_error_type_name(impl_type_name)
                        )
                        if short_name not in _RUST_STD_TRAIT_NAMES or is_exempt:
                            unresolved_id = make_symbol_id(
                                "rust", "unresolved", 0, 0, short_name, "trait",
                            )
                            edges.append(Edge.create(
                                src=impl_sym.id,
                                dst=unresolved_id,
                                edge_type="implements",
                                line=node.start_point[0] + 1,
                                evidence_type="trait_impl",
                                is_resolved=False,
                                # WI-nurun: confidence kept explicit — trait_impl
                                # is single-valued in the derivation table (0.95),
                                # so it cannot express the lower reliability of an
                                # *unresolved* trait impl.
                                confidence=0.70,
                                origin=PASS_ID,
                                origin_run_id=run_id,
                            ))

        # Detect use statements
        elif node.type == "use_declaration":
            # Extract the path being imported
            path_node = find_child_by_type(node, "scoped_identifier")
            if not path_node:
                path_node = find_child_by_type(node, "identifier")
            if not path_node:
                path_node = find_child_by_type(node, "use_wildcard")
            if not path_node:
                path_node = find_child_by_type(node, "use_list")

            if path_node:
                import_path = node_text(path_node, source)
                edges.append(Edge.create(
                    src=file_id,
                    dst=f"rust:{import_path}:0-0:module:module",
                    edge_type="imports",
                    line=node.start_point[0] + 1,
                    evidence_type="use_declaration",
                    origin=PASS_ID,
                    origin_run_id=run_id,
                ))

        # Detect function calls
        elif node.type == "call_expression":
            current_function = _get_enclosing_function(node, source, local_symbols, span_index)
            if current_function is not None:
                func_node = _find_child_by_field(node, "function")
                if func_node:
                    # Unwrap generic_function (turbofish):
                    # x.collect::<Vec<i32>>() wraps a field_expression
                    # inside a generic_function node; similarly
                    # Foo::<T>::bar() wraps a scoped_identifier.
                    inner = func_node
                    if inner.type == "generic_function":
                        for child in inner.children:
                            if child.type in (
                                "identifier", "field_expression",
                                "scoped_identifier",
                            ):
                                inner = child
                                break
                    # Get the function name being called
                    is_method_call = False
                    full_scoped_name = None
                    if inner.type == "identifier":
                        callee_name = node_text(inner, source)
                    elif inner.type == "field_expression":
                        # method call like foo.bar()
                        is_method_call = True
                        field_node = _find_child_by_field(inner, "field")
                        if field_node:
                            callee_name = node_text(field_node, source)
                        else:
                            callee_name = None
                    elif inner.type == "scoped_identifier":
                        # Qualified call like Foo::bar() or
                        # Foo::<T>::bar() (turbofish).
                        # Extract full qualified name for precise lookup.
                        name_node = _find_child_by_field(inner, "name")
                        path_node = _find_child_by_field(inner, "path")
                        if name_node:
                            callee_name = node_text(name_node, source)
                        else:
                            callee_name = node_text(inner, source)
                        # Build clean scoped name stripping type args:
                        # "PublicParams::<E1,E2>::setup" → "PublicParams::setup"
                        if path_node and path_node.type == "generic_type":
                            type_id = next(
                                (c for c in path_node.children
                                 if c.type == "type_identifier"),
                                None,
                            )
                            if type_id and callee_name:
                                full_scoped_name = (
                                    f"{node_text(type_id, source)}"
                                    f"::{callee_name}"
                                )
                        if full_scoped_name is None:
                            full_scoped_name = node_text(inner, source)
                        # Resolve Self:: to actual type name from enclosing impl block
                        if full_scoped_name and full_scoped_name.startswith("Self::"):
                            impl_type = _get_impl_target(node, source)
                            if impl_type:
                                full_scoped_name = f"{impl_type}::{full_scoped_name[6:]}"
                    else:
                        callee_name = None

                    if callee_name:
                        resolved = False
                        # WI-dizag: Strategy 1.5 resolves a self.field
                        # receiver's type and then uses it ONLY to find a
                        # first-party symbol. When the field's type is
                        # EXTERNAL that lookup misses and the type was
                        # dropped, so the call fell through to the
                        # unresolved branch carrying the bare ``external``
                        # placeholder. Carried here so the external branch
                        # below can put it in the module slot -- the same
                        # move PR #595 made for ``_var_types``.
                        field_receiver_type: str | None = None

                        # Async spawn detection: tokio::spawn(task()),
                        # tokio::task::spawn(task()), rayon::spawn(task())
                        # Create a call edge to the spawned task, not to spawn itself.
                        if full_scoped_name in _SPAWN_FUNCTIONS:
                            args_node = _find_child_by_field(node, "arguments")
                            if args_node:
                                for arg_child in args_node.children:
                                    if arg_child.type == "call_expression":
                                        # The spawned task is itself a call — it will
                                        # be processed when iter_tree reaches it, so
                                        # just skip creating an edge to spawn().
                                        pass
                                    elif arg_child.type == "async_block":
                                        # async { ... } — calls inside will be
                                        # visited by iter_tree normally.
                                        pass
                                    elif arg_child.type == "identifier":
                                        # tokio::spawn(my_task) — bare function ref
                                        ref_name = node_text(arg_child, source)
                                        target = local_symbols.get(ref_name)
                                        if target is None:
                                            lr = resolver.lookup(ref_name, caller_path=_caller_path)
                                            if lr.found and lr.symbol is not None:
                                                target = lr.symbol
                                        if target is not None:
                                            edges.append(Edge.create(
                                                src=current_function.id,
                                                dst=target.id,
                                                edge_type="calls",
                                                line=node.start_point[0] + 1,
                                                evidence_type="async_spawn",
                                                origin=PASS_ID,
                                                origin_run_id=run_id,
                                            ))
                            # Don't resolve spawn itself as a callee
                            resolved = True

                        # Strategy 1: Try full scoped name first (e.g., "Diff::compute")
                        # This gives precise resolution for qualified calls.
                        #
                        # INV-fahub Phase A: every bind in this branch resolves a
                        # ``Type::method`` scoped call whose target type the CALL
                        # SITE named explicitly, so the edge carries
                        # ``meta.receiver="qualified"``. Without it the
                        # language-agnostic magnet detector counts a qualified
                        # associated-function call to a method-kind symbol
                        # (rodio's ``SamplesBuffer::new`` <- 26 callers) as a
                        # receiver-blind cross-class magnet even though every one
                        # is correct — the reframe left the detector-side
                        # ``qualified`` exclusion ready and this stamps the
                        # producer half.
                        if not resolved and full_scoped_name and full_scoped_name != callee_name:
                            if full_scoped_name in local_symbols:
                                callee = local_symbols[full_scoped_name]
                                edges.append(Edge.create(
                                    src=current_function.id,
                                    dst=callee.id,
                                    edge_type="calls",
                                    line=node.start_point[0] + 1,
                                    evidence_type="ast_call",
                                    origin=PASS_ID,
                                    origin_run_id=run_id,
                                    meta={"call_construct": "function", "receiver": "qualified"},
                                ))
                                resolved = True
                            else:
                                import_hint = use_aliases.get(callee_name)
                                lookup_result = resolver.lookup(
                                    full_scoped_name, path_hint=import_hint, caller_path=_caller_path,
                                )
                                if lookup_result.found and lookup_result.symbol is not None:
                                    edges.append(Edge.create(
                                        src=current_function.id,
                                        dst=lookup_result.symbol.id,
                                        edge_type="calls",
                                        line=node.start_point[0] + 1,
                                        evidence_type="ast_call",
                                        confidence=0.80 * lookup_result.confidence,
                                        origin=PASS_ID,
                                        origin_run_id=run_id,
                                        meta={"call_construct": "function", "receiver": "qualified"},
                                    ))
                                    resolved = True

                                # Strategy 1b: Strip module prefixes from
                                # the scoped name.  For
                                # codex_agent::CodexAgent::new, try
                                # "CodexAgent::new" before falling back to
                                # bare "new" (which has many ambiguous
                                # candidates).
                                if not resolved:
                                    parts = full_scoped_name.split("::")
                                    for i in range(1, len(parts) - 1):
                                        suffix = "::".join(parts[i:])
                                        if suffix in local_symbols:
                                            callee = local_symbols[suffix]
                                            edges.append(Edge.create(
                                                src=current_function.id,
                                                dst=callee.id,
                                                edge_type="calls",
                                                line=node.start_point[0] + 1,
                                                evidence_type="ast_call",
                                                origin=PASS_ID,
                                                origin_run_id=run_id,
                                                meta={"call_construct": "function", "receiver": "qualified"},
                                            ))
                                            resolved = True
                                            break
                                        lr = resolver.lookup(
                                            suffix, path_hint=import_hint, caller_path=_caller_path,
                                        )
                                        if lr.found and lr.symbol is not None:
                                            edges.append(Edge.create(
                                                src=current_function.id,
                                                dst=lr.symbol.id,
                                                edge_type="calls",
                                                line=node.start_point[0] + 1,
                                                evidence_type="ast_call",
                                                confidence=0.80 * lr.confidence,
                                                origin=PASS_ID,
                                                origin_run_id=run_id,
                                                meta={"call_construct": "function", "receiver": "qualified"},
                                            ))
                                            resolved = True
                                            break

                        # Strategy 1.5: Resolve self.field.method() via
                        # field type registry.  Fires for method calls where
                        # the receiver is a field_expression rooted at self.
                        # Known receiver type makes even blocklisted methods
                        # (e.g. Client::send) unambiguous.
                        if (
                            not resolved
                            and is_method_call
                            and analyzer is not None
                            and field_type_registry
                        ):
                            value_node = inner.child_by_field_name("value")
                            if value_node is not None:
                                impl_target = _get_impl_target(
                                    node, source,
                                )
                                receiver_type = analyzer.resolve_receiver_type(
                                    value_node, source, impl_target,
                                )
                                if receiver_type is not None:
                                    # WI-dizag: keep it whether or not the
                                    # first-party lookup below succeeds.
                                    field_receiver_type = receiver_type
                                    # Strip module prefix from scoped types
                                    # (e.g., "std::sync::Mutex" → "Mutex")
                                    # because symbols are stored with bare names.
                                    bare_type = receiver_type.rsplit("::", 1)[-1]
                                    typed_name = f"{bare_type}::{callee_name}"
                                    target = (
                                        local_symbols.get(typed_name)
                                        or global_symbols.get(typed_name)
                                    )
                                    if target is None:
                                        lookup = resolver.lookup(typed_name, caller_path=_caller_path)
                                        if lookup.found and lookup.symbol:
                                            target = lookup.symbol
                                    if target is not None:
                                        edges.append(Edge.create(
                                            src=current_function.id,
                                            dst=target.id,
                                            edge_type="calls",
                                            line=node.start_point[0] + 1,
                                            evidence_type="ast_call",
                                            origin=PASS_ID,
                                            origin_run_id=run_id,
                                            meta={"call_construct": "method", "receiver": "typed_field"},
                                        ))
                                        resolved = True

                        # WI-dizag shape B: a VAR-ROOTED field chain,
                        # ``h.f.write_all(..)`` with ``h`` a typed parameter or
                        # local. Strategy 1.5 above handles only ``self``-rooted
                        # chains (``resolve_receiver_type`` returns None for a
                        # non-self root), so it left ``field_receiver_type``
                        # None here and the module-slot recovery downstream had
                        # nothing to consume. ``resolve_var_field_chain`` is the
                        # sibling walk: root ``h`` -> ``Holder`` via
                        # ``_var_types``, field ``f`` -> ``File`` via the same
                        # struct field-type registry. It sets the type ONLY;
                        # like #595 it does not set ``has_explicit_binding`` or
                        # ``resolved``, so emission is byte-identical and the
                        # module slot is rebuilt from the recovered type
                        # exactly as the ``self.field`` case is.
                        if (
                            field_receiver_type is None
                            and is_method_call
                            and analyzer is not None
                            and _var_types
                        ):
                            _vnode = inner.child_by_field_name("value")
                            if (
                                _vnode is not None
                                and _vnode.type == "field_expression"
                            ):
                                _vt = analyzer.resolve_var_field_chain(
                                    _vnode, source, _var_types,
                                )
                                if _vt is not None:
                                    field_receiver_type = _vt

                        # Strategy 1.8 (WI-titor / INV-dihos Phase 3):
                        # ``var.method()`` where ``var`` is a typed local
                        # / parameter / let-binding tracked in
                        # ``_var_types``. Resolves the receiver type
                        # without consulting the field_type_registry (so
                        # it covers parameters and let-bindings, not
                        # just ``self.field``). Fires after the
                        # self-field strategy because that one has the
                        # stronger 0.88-confidence ``typed_field``
                        # signal; fires before the short-name fallback
                        # because typed receivers should win short-name
                        # ambiguity and bypass the generic-trait-method
                        # blocklist (the receiver type is known).
                        if (
                            not resolved
                            and is_method_call
                            and _var_types
                        ):
                            value_node = inner.child_by_field_name("value")
                            if value_node is not None and value_node.type == "identifier":
                                recv_name = node_text(value_node, source)
                                recv_type = _var_types.get(recv_name)
                                if recv_type:
                                    typed_name = f"{recv_type}::{callee_name}"
                                    target = (
                                        local_symbols.get(typed_name)
                                        or global_symbols.get(typed_name)
                                    )
                                    if target is None:
                                        lookup = resolver.lookup(
                                            typed_name, caller_path=_caller_path,
                                        )
                                        if lookup.found and lookup.symbol:
                                            target = lookup.symbol
                                    if target is not None:
                                        edges.append(Edge.create(
                                            src=current_function.id,
                                            dst=target.id,
                                            edge_type="calls",
                                            line=node.start_point[0] + 1,
                                            evidence_type="ast_call_type_inferred",
                                            origin=PASS_ID,
                                            origin_run_id=run_id,
                                            meta={
                                                "call_construct": "method",
                                                "receiver": "typed_var",
                                            },
                                        ))
                                        resolved = True

                        # Strategy 1.9: receiver is itself a call expression —
                        # infer its return type and resolve the outer method
                        # against it. `Cmd::parse().run()`: the receiver
                        # `Cmd::parse()` yields `Cmd` (associated-fn path), so
                        # `.run()` resolves to `Cmd::run` (WI-lohup). Reuses the
                        # let-binding RHS type-inference helper, so it also
                        # covers `obj.foo().bar()` when foo's return type is
                        # known. These chained calls leave no intermediate
                        # variable for the var_types walker to type, so the
                        # typed_var strategy above misses them.
                        if not resolved and is_method_call:
                            chained_recv = inner.child_by_field_name("value")
                            if (
                                chained_recv is not None
                                and chained_recv.type == "call_expression"
                            ):
                                recv_type = _infer_type_from_rust_rhs(
                                    chained_recv, source, _var_types,
                                    getattr(
                                        analyzer,
                                        "_method_return_type_registry", None,
                                    ) or {},
                                )
                                if recv_type:
                                    # The inferred receiver type is a concrete
                                    # in-tree type, so its method is keyed by the
                                    # qualified `Type::method` name in the local
                                    # (same-file) or global registry — no resolver
                                    # disambiguation needed (unlike the typed_var
                                    # strategy, which may face short-name ambiguity).
                                    typed_name = f"{recv_type}::{callee_name}"
                                    target = (
                                        local_symbols.get(typed_name)
                                        or global_symbols.get(typed_name)
                                    )
                                    if target is not None:
                                        edges.append(Edge.create(
                                            src=current_function.id,
                                            dst=target.id,
                                            edge_type="calls",
                                            line=node.start_point[0] + 1,
                                            evidence_type="ast_call_type_inferred",
                                            origin=PASS_ID,
                                            origin_run_id=run_id,
                                            meta={
                                                "call_construct": "method",
                                                "receiver": "typed_var",
                                            },
                                        ))
                                        resolved = True

                        # Strategy 2: Fall back to short name
                        if not resolved:
                            if callee_name in local_symbols:
                                callee = local_symbols[callee_name]
                                edges.append(Edge.create(
                                    src=current_function.id,
                                    dst=callee.id,
                                    edge_type="calls",
                                    line=node.start_point[0] + 1,
                                    evidence_type="ast_call",
                                    origin=PASS_ID,
                                    origin_run_id=run_id,
                                    meta={"call_construct": "function"},
                                ))
                            # Check global symbols via resolver
                            else:
                                # Use method_resolver (ambiguity guard: 3+ candidates
                                # → unresolved) for: (a) method calls (foo.bar()),
                                # (b) scoped identifier fallback (Type::new() where
                                # full "Type::new" wasn't found).  Both are
                                # method-like calls that should not resolve to
                                # arbitrary same-name symbols.
                                # INV-fahub bare->method magnet gate: track
                                # whether this candidate came from the bare
                                # (non-method, non-scoped) ``resolver.lookup``
                                # path, plus the enclosing impl type, so a weak
                                # short-name hit on a DIFFERENT impl's method
                                # can be deferred to the inherited_calls Site-1
                                # walker instead of misbound (see below).
                                _used_bare_resolver = False
                                _bare_enclosing_type: str | None = None
                                use_method_guard = (
                                    is_method_call or full_scoped_name is not None
                                )
                                if use_method_guard and method_resolver is not None:
                                    if callee_name in _RUST_GENERIC_TRAIT_METHODS:
                                        # Generic trait methods (.into(), .clone(),
                                        # .len(), etc.) cannot be resolved without
                                        # receiver type info — skip lookup to avoid
                                        # false edges.
                                        lookup_result = LookupResult(symbol=None)
                                    else:
                                        # Pass the caller's directory as path_hint
                                        # to prefer same-module methods over
                                        # cross-module ones with the same name
                                        # (e.g., nova/nifs.rs over
                                        # neutron/nifs.rs when called from nova/).
                                        caller_dir = (
                                            file_path.rsplit("/", 1)[0]
                                            if "/" in file_path else ""
                                        )
                                        lookup_result = method_resolver.lookup(
                                            callee_name,
                                            path_hint=caller_dir if caller_dir else None,
                                            soft_hint=True,
                                        )
                                else:
                                    import_hint = use_aliases.get(callee_name)
                                    lookup_result = resolver.lookup(callee_name, path_hint=import_hint, caller_path=_caller_path)
                                    # The magnet gate applies only to genuinely
                                    # BARE identifier calls (``foo()``). Method /
                                    # scoped calls that fall through here purely
                                    # because no ``method_resolver`` was supplied
                                    # keep their existing suffix resolution —
                                    # production guards those via the
                                    # method_resolver ambiguity threshold.
                                    _used_bare_resolver = not use_method_guard
                                    _bare_enclosing_type = _get_impl_target(node, source)
                                _sym = lookup_result.symbol
                                # INV-fahub: a BARE call (``foo()``) that resolved
                                # only to a DIFFERENT impl's method on weak
                                # short-name (suffix / ambiguous) evidence is a
                                # magnet — withhold it and defer to the
                                # inherited_calls Site-1 walker via
                                # ``enclosing_class`` rather than binding a
                                # high-confidence false edge. Free functions,
                                # same-impl methods, and exact / import-scoped
                                # hits still bind. Same-impl ``self.m()`` /
                                # ``Type::m()`` calls take the method_resolver
                                # guard path, and same-file free functions are
                                # caught by the ``local_symbols`` check above, so
                                # this gate only fires on the cross-impl magnet.
                                _defer = (
                                    _used_bare_resolver
                                    and _sym is not None
                                    and defer_bare_method_call(
                                        _sym.kind, _sym.name,
                                        lookup_result.match_type,
                                        _bare_enclosing_type,
                                        separator="::",
                                    )
                                )
                                if (
                                    lookup_result.found
                                    and _sym is not None
                                    and not _defer
                                ):
                                    confidence = 0.80 * lookup_result.confidence
                                    # WI-fazaj: a scoped ``Type::method()`` call that
                                    # resolves *here* (Strategy 1 missed it; the
                                    # cross-package survey ``method_resolver`` / bare-name
                                    # fallback bound it) still named its target type at the
                                    # call site, so it is a *qualified* call, not a
                                    # receiver-blind magnet — stamp ``receiver="qualified"``
                                    # (mirroring the Strategy-1 sites) so
                                    # ``find_receiver_blind_magnets`` excludes it. The
                                    # branch is reachable only through the full-survey
                                    # method_resolver (verified unreachable across 8
                                    # isolated ``analyze_rust`` scenarios — cross-file,
                                    # nested modules, ``use`` aliases, generics, ``::new``
                                    # ambiguity, re-exports — which all bind via Strategy 1),
                                    # so it carries ``# pragma: no cover``.
                                    _meta = {"call_construct": "function"}
                                    if full_scoped_name is not None:  # pragma: no cover
                                        _meta["receiver"] = "qualified"
                                    edges.append(Edge.create(
                                        src=current_function.id,
                                        dst=_sym.id,
                                        edge_type="calls",
                                        line=node.start_point[0] + 1,
                                        evidence_type="ast_call",
                                        confidence=confidence,
                                        origin=PASS_ID,
                                        origin_run_id=run_id,
                                        meta=_meta,
                                    ))
                                else:
                                    # WI-volob / WI-mafik: consult use_aliases
                                    # to attribute the external call to its
                                    # source module via the structured
                                    # ExternalRef. Three cases:
                                    # (1) qualified ``fs::read_to_string``:
                                    #     ``fs`` is in use_aliases → resolve
                                    #     to ``std::fs`` for the module slot.
                                    # (2) bare ``write`` after ``use std::fs::write``:
                                    #     ``write`` is in use_aliases → split
                                    #     ``std::fs::write`` into module + name.
                                    #     An explicit use binding takes
                                    #     precedence over the generic-trait-
                                    #     method guard — the binding tells
                                    #     us exactly which module owns the
                                    #     callable.
                                    # (3) aliased terminal ``mkdir`` after
                                    #     ``use ... as mkdir``: same as (2),
                                    #     name field gets the underlying
                                    #     ``create_dir``, not the alias.
                                    # All three short-circuit the 6-seg dst
                                    # rebuild path (the parsing-bug class
                                    # documented in WI-tihup foundation).
                                    ext_ref: ExternalRef | None = None
                                    module_hint = "external"
                                    unresolved_name = full_scoped_name or callee_name
                                    has_explicit_binding = False
                                    if full_scoped_name and "::" in full_scoped_name:
                                        head, _, tail = full_scoped_name.partition("::")
                                        if head in use_aliases:
                                            full_head = use_aliases[head]
                                            module_hint = full_head
                                            unresolved_name = tail
                                            ext_ref = ExternalRef(
                                                lang="rust",
                                                module_path=full_head,
                                                name=tail,
                                            )
                                            has_explicit_binding = True
                                    elif callee_name in use_aliases:
                                        full_path = use_aliases[callee_name]
                                        if "::" in full_path:
                                            mod, _, name = full_path.rpartition("::")
                                            module_hint = mod
                                            unresolved_name = name
                                            ext_ref = ExternalRef(
                                                lang="rust",
                                                module_path=mod,
                                                name=name,
                                            )
                                            has_explicit_binding = True
                                    # INV-linub L3: a TYPED receiver whose type
                                    # is EXTERNAL must keep its type. The typed
                                    # strategies above (1.5 / 1.8 / 1.9)
                                    # resolve ``Type::method`` against
                                    # first-party symbols only, so an external
                                    # receiver type misses, ``resolved`` stays
                                    # False, and control arrives here — where
                                    # the module slot is rebuilt from
                                    # ``use_aliases`` alone and a METHOD name is
                                    # never in ``use_aliases``. The receiver
                                    # type computed moments earlier was simply
                                    # dropped. Measured on encrypted-dns-server:
                                    # 203 of 219 method-construct edges carried
                                    # the bare ``external`` placeholder and NOT
                                    # ONE carried a stdlib module, so
                                    # ``_lookup_named_entry`` refused every one
                                    # (correctly — an untyped method call must
                                    # not match a method-kind entry) and the
                                    # method half of rust.yaml was unreachable.
                                    #
                                    # DELIBERATELY NARROW. This changes the
                                    # module SLOT only; it does NOT set
                                    # ``has_explicit_binding``, so which edges
                                    # get emitted — including the
                                    # generic-trait-method suppression below —
                                    # is byte-identical. Widening emission is a
                                    # separate question with a separate
                                    # measurement.
                                    if (
                                        not has_explicit_binding
                                        and is_method_call
                                        and (_var_types or _var_type_paths)
                                    ):
                                        _recv = inner.child_by_field_name("value")
                                        if (
                                            _recv is not None
                                            and _recv.type == "identifier"
                                        ):
                                            _rn = node_text(_recv, source)
                                            _rt = _var_types.get(_rn)
                                            _full = use_aliases.get(_rt) if _rt else None
                                            # The alias must be a PATH. A
                                            # single-segment alias carries no
                                            # module and would put a bare type
                                            # name in the module slot.
                                            if not (_full and "::" in _full):
                                                # ...and when the type was never
                                                # imported BY NAME there is no
                                                # alias to find, however plainly
                                                # the source wrote the path:
                                                # `f: &mut std::fs::File` is
                                                # normalized to `File`, and
                                                # `File` is not in use_aliases
                                                # because nothing was imported.
                                                # Fall back to the path the
                                                # source actually wrote.
                                                _full = _var_type_paths.get(_rn)
                                            if _full and "::" in _full:
                                                module_hint = _full
                                                ext_ref = ExternalRef(
                                                    lang="rust",
                                                    module_path=_full,
                                                    name=callee_name,
                                                )
                                    # WI-dizag: the FIELD receiver's type, when
                                    # the identifier arm above found nothing. The
                                    # arm above requires ``_recv.type ==
                                    # "identifier"``, so ``self.f.write_all(..)``
                                    # never reaches it -- the receiver node is a
                                    # ``field_expression``. Strategy 1.5 already
                                    # resolved that chain through the struct
                                    # field-type registry; this is the same
                                    # module-slot recovery applied to the type it
                                    # computed.
                                    #
                                    # SAME NARROWNESS AS #595: module SLOT only,
                                    # ``has_explicit_binding`` untouched, so which
                                    # edges get emitted -- including the
                                    # generic-trait-method suppression below -- is
                                    # byte-identical.
                                    if (
                                        ext_ref is None
                                        and not has_explicit_binding
                                        and is_method_call
                                        and field_receiver_type
                                    ):
                                        # A scoped type names its own module;
                                        # a bare one needs the use-alias that
                                        # brought it in (``use std::fs::File``).
                                        _ft = field_receiver_type
                                        _fpath = (
                                            _ft if "::" in _ft
                                            else use_aliases.get(_ft)
                                        )
                                        if _fpath and "::" in _fpath:
                                            module_hint = _fpath
                                            ext_ref = ExternalRef(
                                                lang="rust",
                                                module_path=_fpath,
                                                name=callee_name,
                                            )
                                    # INV-pamis: the denylist exists because a
                                    # generic-trait NAME on an untypable receiver
                                    # (``x.clone()``, ``v.into()``) cannot be bound
                                    # honestly and emitting it blind bloated two
                                    # crates' edges by 33-207%. A receiver whose
                                    # type the signature DECLARES (``sock:
                                    # &UdpSocket`` -> ``std::net::UdpSocket``) is the
                                    # evidence the denylist lacks: ``sock.send`` on
                                    # it is the catalogued net_send sink, emitted
                                    # with the type in the slot. Untyped receivers
                                    # keep the denylist and its disclosure.
                                    if (
                                        has_explicit_binding
                                        or ext_ref is not None
                                        or callee_name not in _RUST_GENERIC_TRAIT_METHODS
                                    ):
                                        edges.append(make_unresolved_edge(
                                            "rust", current_function.id, unresolved_name,
                                            node.start_point[0] + 1, PASS_ID, run_id,
                                            module_hint=module_hint,
                                            dst_ref=ext_ref,
                                            # INV-fibis disclosure parity: stamp the
                                            # construct so
                                            # ``verify_claims.untyped_receiver_sites``
                                            # can name this site. rust stamps
                                            # ``call_construct`` on its RESOLVED
                                            # paths in three places and omitted it
                                            # here — on the unresolved-external
                                            # path, which is the population the
                                            # disclosure exists for — so rust
                                            # reached a clean verdict over 34
                                            # catalogued method sinks in silence.
                                            call_construct=(
                                                "method" if is_method_call else None
                                            ),
                                            # INV-fahub: carry the enclosing impl
                                            # type on the deferred magnet so the
                                            # Site-1 inherited_calls walker can
                                            # recover a genuine inherited call.
                                            enclosing_class=(
                                                _bare_enclosing_type if _defer else None
                                            ),
                                        ))

        # Detect calls inside macro bodies (tokio::select!, assert!, etc.).
        # Tree-sitter parses macro bodies as flat token_tree, not structured
        # AST, so call_expression nodes are never created.  We pattern-match
        # token sequences to extract likely calls.
        elif node.type == "macro_invocation":
            current_function = _get_enclosing_function(node, source, local_symbols, span_index)
            if current_function is not None:
                tt = None
                for child in node.children:
                    if child.type == "token_tree":
                        tt = child
                        break
                if tt is not None:
                    for callee_name, call_line in _extract_macro_call_names(tt, source):
                        # Resolve Self:: to actual type
                        if callee_name.startswith("Self::"):
                            impl_type = _get_impl_target(node, source)
                            if impl_type:
                                callee_name = f"{impl_type}::{callee_name[6:]}"
                        # Try qualified name first, then short name
                        target = local_symbols.get(callee_name)
                        if target is None and "::" in callee_name:
                            short = callee_name.rsplit("::", 1)[-1]
                            target = local_symbols.get(short)
                        if target is None:
                            lr = resolver.lookup(callee_name, caller_path=_caller_path)
                            if lr.found and lr.symbol is not None:
                                target = lr.symbol
                        if target is not None:
                            edges.append(Edge.create(
                                src=current_function.id,
                                dst=target.id,
                                edge_type="calls",
                                line=call_line,
                                evidence_type="ast_call",
                                origin=PASS_ID,
                                origin_run_id=run_id,
                                meta={"call_construct": "macro_body"},
                            ))

    # WI-vipur: emit module_attr_ref edges for scoped attribute reads
    # on imported Rust paths (e.g. ``std::env::consts::OS``).  These
    # pair with the ``attributes:`` entries in io_primitives/rust.yaml
    # (``module: std::env, attributes: [consts]``).  Without them the
    # env_read chain for ``consts`` was silently inert on the
    # tree-sitter path — rust-analyzer's semantic backend has its own
    # resolver and is unaffected.  ``std`` is injected as an implicit
    # import (Rust stdlib is in-scope without a ``use`` statement).
    attr_imports = dict(use_aliases)
    attr_imports.setdefault("std", "std")
    file_pseudo_symbol = Symbol(
        id=file_id,
        name=Path(file_path).name,
        kind="module",
        language="rust",
        path=str(file_path),
        span=Span(start_line=0, end_line=0, start_col=0, end_col=0),
        origin=PASS_ID,
        origin_run_id=run_id,
        line_span=1,
    )
    emit_module_attribute_refs(
        tree.root_node,
        source,
        attr_imports,
        file_pseudo_symbol,
        "rust",
        edges,
        node_kinds=("scoped_identifier",),
        object_field_names=("path",),
        property_field_names=("name",),
        pass_id=PASS_ID,
        run_id=run_id,
        call_node_kinds=("call_expression",),
        call_function_field_names=("function",),
        scoped_path=True,
        # INV-pusin: a `use` path and a return-type path are both
        # scoped_identifiers, so the walk emitted them as attribute reads.
        # The `use` case duplicated a fact that already has an `imports`
        # edge, and re-entered the uncatalogued-module gate that
        # deliberately excludes imports -- `use std::fs;` alone put `std`
        # and `std.fs` on a zero-dependency crate's "could not classify"
        # list. `generic_type` catches a scoped path directly under a
        # generic; `scoped_type_identifier` catches the common
        # `std::io::Result<_>` shape.
        skip_context_kinds=("scoped_type_identifier", "generic_type"),
        # INV-pusin, SECOND CLOSURE. `use_declaration` USED TO LIVE IN THE
        # TUPLE ABOVE and it only ever suppressed the one spelling whose
        # path sits directly beneath it (`use std::fs;`). The grammar wraps
        # every other form -- `use std::io::{self, Write};` in
        # `scoped_use_list`, `use std::io::*;` in `use_wildcard`,
        # `use std::fs as f;` in `use_as_clause` -- so six of the fourteen
        # spellings in `_USE_FORMS` (tests/test_rust.py) still emitted
        # reads, and `use std::io::*;` alone put a BARE `std` on the
        # uncatalogued list, which no `module_completeness` entry can ever
        # clear because nobody can audit the whole standard library.
        #
        # It is matched by ANCESTRY here rather than added to the proximate
        # tuple as three more strings: the three wrappers are not the
        # invariant, they are today's spelling of it. The invariant is that
        # a path anywhere inside a `use` is an import.
        skip_ancestor_kinds=("use_declaration",),
        # INV-fafol: anchor each read to the callable that performs it, not to
        # the file. A source and a sink must share a caller to propagate.
        enclosing_symbols=list(local_symbols.values()),
    )

    return edges


# Compiler-provided attributes that must never resolve to user-defined symbols.
# Covers: testing, conditional compilation, diagnostics, code-generation hints,
# derive macros, linking, FFI, documentation, async runtimes (tokio), and
# serialization (serde).  Proc-macro *crate* attributes (e.g. ``serde``,
# ``tokio``) are included because the crate re-exports only derive/attribute
# macros — a user function named ``serde`` is never the intended target.
_BUILTIN_RUST_ATTRIBUTES: frozenset[str] = frozenset({
    # Testing
    "test", "bench", "ignore", "should_panic",
    # Conditional compilation
    "cfg", "cfg_attr",
    # Derive
    "derive",
    # Diagnostics / lints
    "allow", "warn", "deny", "forbid", "deprecated", "must_use",
    # Code generation
    "inline", "cold", "no_mangle", "track_caller", "target_feature",
    "instruction_set",
    # Linking / FFI
    "link", "link_name", "link_section", "no_link", "export_name",
    "link_ordinal", "no_builtins", "repr", "used",
    # Documentation
    "doc",
    # Module / crate level
    "path", "no_std", "no_implicit_prelude", "macro_use", "macro_export",
    "crate_type", "no_main", "recursion_limit", "type_length_limit",
    # Proc-macro
    "proc_macro", "proc_macro_derive", "proc_macro_attribute",
    # Type system
    "non_exhaustive",
    # Runtime
    "panic_handler", "global_allocator", "windows_subsystem",
    # Common ecosystem proc-macro crate names (not user functions)
    "serde", "tokio", "async_trait", "tracing", "instrument",
})

# Generic trait method names that create false-positive in-degree when resolved
# via method_resolver.  Without receiver type information, calls like `x.into()`
# or `v.push()` resolve to arbitrary concrete implementations (e.g.,
# `StatusRow::into` absorbing 817 `.into()` edges in penumbra).  These method
# names are blocked from short-name resolution; fully-scoped calls like
# `StatusRow::into()` still resolve via Strategy 1.
#
# INV-polad: THE SET NOW LIVES IN ``hypergumbo_core.analyzer_disclosure`` and is
# imported here rather than declared here.  It is not only a resolution policy:
# ten of these names are methods ``io_primitives/rust.yaml`` declares as I/O
# SINKS (``UdpSocket.send``, ``io::Write.write``, ``TcpStream.read`` ...), so
# the same set decides what ``verify-claims`` must DISCLOSE it did not look at.
# A restated copy in core would be a second home for one fact, and the second
# home is the one that silently goes stale (LIVE.md rule 7).
_RUST_GENERIC_TRAIT_METHODS: frozenset[str] = SUPPRESSED_METHOD_NAMES["rust"]


# Traits that are normally blocklisted but should be allowed through when the
# implementing type is an error type.  Error types commonly implement Display
# (for user-facing messages), From (for error conversion chains), Error (the
# std::error::Error trait itself), and Default.  These impls are architecturally
# meaningful: they define how errors compose and propagate.
_ERROR_TRAIT_EXEMPTIONS: frozenset[str] = frozenset({
    "Display", "From", "Error", "Default",
})


def _is_error_type_name(name: str) -> bool:
    """Check if a type name suggests an error type.

    Heuristic: the name ends with ``Error`` or ``Err`` (e.g. ``ParseError``,
    ``MyErr``).  This covers the overwhelming majority of Rust error types
    without requiring trait resolution.
    """
    return name.endswith("Error") or name.endswith("Err")


# Standard library trait names that should NOT generate unresolved implements
# edges.  These are ubiquitous auto-derived or manually-impl'd traits whose
# definitions are in std/core and won't be in the project's symbol registry.
# Creating unresolved edges for them would be pure noise — a developer never
# needs to know "MyStruct implements Clone" in the call graph.
#
# Exception: traits in _ERROR_TRAIT_EXEMPTIONS are allowed through when the
# implementing type is an error type (see _is_error_type_name).
_RUST_STD_TRAIT_NAMES: frozenset[str] = frozenset({
    # core::marker
    "Copy", "Send", "Sync", "Sized", "Unpin",
    # core::clone
    "Clone",
    # core::cmp
    "PartialEq", "Eq", "PartialOrd", "Ord",
    # core::fmt
    "Debug", "Display",
    # core::hash
    "Hash",
    # core::default
    "Default",
    # core::convert
    "From", "Into", "TryFrom", "TryInto", "AsRef", "AsMut",
    # core::ops
    "Deref", "DerefMut", "Drop", "Add", "Sub", "Mul", "Div", "Rem",
    "Neg", "Not", "BitAnd", "BitOr", "BitXor", "Shl", "Shr",
    "Index", "IndexMut", "Fn", "FnMut", "FnOnce",
    "AddAssign", "SubAssign", "MulAssign", "DivAssign",
    # core::iter
    "Iterator", "IntoIterator", "FromIterator", "ExactSizeIterator",
    "DoubleEndedIterator",
    # core::future
    "Future",
    # std::io
    "Read", "Write", "Seek", "BufRead",
    # std::error
    "Error",
    # serde (extremely common, not architectural)
    "Serialize", "Deserialize", "Serializer", "Deserializer",
    # std::string
    "ToString",
    # std::borrow
    "Borrow", "BorrowMut", "ToOwned",
})


def _extract_attribute_edges(
    symbols: list[Symbol],
    global_symbols: dict[str, Symbol],
    run_id: str,
) -> list[Edge]:
    """Extract decorated_by edges from Rust attribute metadata.

    Creates edges from symbols to their attributes. For example,
    ``#[my_macro]`` on a function creates a ``decorated_by`` edge from the
    function to the macro symbol (if resolvable).

    Built-in compiler attributes (``test``, ``cfg``, ``derive``, ``inline``,
    ``allow``, ``must_use``, …) are **never** resolved against
    ``global_symbols``.  Without this guard, a user-defined function named
    ``test`` would be incorrectly linked to every ``#[test]`` annotation in the
    crate — a common false-positive in test-heavy codebases (WI-votaj).

    Args:
        symbols: All symbols extracted from the codebase.
        global_symbols: Map of symbol names to Symbol objects for resolution.
        run_id: The current analysis run execution ID for provenance.

    Returns:
        List of decorated_by edges.
    """
    edges: list[Edge] = []

    for sym in symbols:
        if sym.meta is None:
            continue

        annotations = sym.meta.get("annotations")
        if not annotations or not isinstance(annotations, list):
            continue

        for annotation in annotations:
            if not isinstance(annotation, dict):  # pragma: no cover
                continue

            attr_name = annotation.get("name")
            if not attr_name or not isinstance(attr_name, str):  # pragma: no cover
                continue

            # Built-in attributes must never resolve to user symbols.
            # Skip entirely — they have no user-space definition, and even
            # unresolved edges create noise (derive gets 175 in-edges).
            # For qualified names like "tracing::instrument", check both
            # the full name and the crate name (first path component).
            if attr_name in _BUILTIN_RUST_ATTRIBUTES:
                continue
            if "::" in attr_name:
                crate_name = attr_name.split("::", 1)[0]
                if crate_name in _BUILTIN_RUST_ATTRIBUTES:
                    continue

            # Try to resolve the attribute to a symbol
            # For qualified names like "actix_web::get", try both full and short name
            attr_sym = global_symbols.get(attr_name)
            if not attr_sym and "::" in attr_name:
                short_name = attr_name.rsplit("::", 1)[-1]
                attr_sym = global_symbols.get(short_name)

            line = sym.span.start_line if sym.span else 0

            if attr_sym:
                # Resolved attribute
                edge = Edge.create(
                    src=sym.id,
                    dst=attr_sym.id,
                    edge_type="decorated_by",
                    line=line,
                    origin=PASS_ID,
                    origin_run_id=run_id,
                    evidence_type="ast_attribute",
                )
                edges.append(edge)
            else:
                # Unresolved attribute - create unresolved edge
                dst_id = f"rust:unresolved:0-0:{attr_name}:unresolved"
                edge = Edge.create(
                    src=sym.id,
                    dst=dst_id,
                    edge_type="decorated_by",
                    line=line,
                    origin=PASS_ID,
                    origin_run_id=run_id,
                    evidence_type="ast_attribute",
                )
                edges.append(edge)

    return edges


class RustAnalyzer(TreeSitterAnalyzer):
    """Rust language analyzer using tree-sitter-rust."""

    lang = "rust"
    file_patterns: ClassVar[list[str]] = ["*.rs"]
    grammar_module = "tree_sitter_rust"

    def extract_symbols_from_file(
        self, tree: "tree_sitter.Tree", source: bytes,
        file_path: Path, rel_path: str, run: "AnalysisRun",
    ) -> FileAnalysis:
        """Extract functions, structs, enums, traits from a Rust file."""
        return _extract_symbols_from_file(tree, source, rel_path, run.execution_id)

    def get_import_aliases(
        self, tree: "tree_sitter.Tree", source: bytes,
    ) -> dict[str, str]:
        """Extract Rust use statement aliases for disambiguation."""
        return _extract_use_aliases(tree, source)

    def register_symbol(
        self, symbol: Symbol, global_symbols: dict,
    ) -> None:
        """Register symbol globally by qualified name.

        Does NOT register by short name for ``::``-qualified symbols
        (e.g., ``Diff::compute``). The suffix index in ``NameResolver``
        handles ``"compute"`` → ``"Diff::compute"`` lookups. Registering
        the short name caused false exact matches when multiple types
        share a method name (the last one registered won the key).

        Also populates a kind-segregated multi-value index
        (``_kind_index_cache``) so the impl_item handler can disambiguate
        ``trait Foo`` from ``struct Foo`` when both register under the
        same short name (BUG-04 / WI-milak).  Without this side index
        the single-value dict overwrite races on insertion order — the
        survivor wins, the loser is silently dropped, and ``impl Foo
        for Bar`` resolves either to the wrong struct (pre-WI-kahaz) or
        to a manufactured unresolved-trait id (post-WI-kahaz) instead of
        the canonical trait. The cache is keyed on ``global_symbols``
        identity to auto-invalidate between ``analyze()`` runs (matches
        the ``_mr_cache`` pattern below).
        """
        global_symbols[symbol.name] = symbol
        cache = getattr(self, "_kind_index_cache", None)
        if cache is None or cache[0] is not global_symbols:
            # Annotate the empty kind-index and assign the tuple directly to the
            # attribute so mypy can infer _kind_index_cache's type (the `cache`
            # local is Any, coming from getattr).
            kind_index: dict[str, list[Symbol]] = {}
            self._kind_index_cache = (global_symbols, kind_index)
            cache = self._kind_index_cache
        cache[1].setdefault(symbol.name, []).append(symbol)

    def extract_edges_from_file(
        self, tree: "tree_sitter.Tree", source: bytes,
        file_path: Path, rel_path: str,
        local_symbols: dict[str, Symbol], global_symbols: dict,
        run: "AnalysisRun", import_aliases: dict[str, str],
        resolver: "NameResolver",
    ) -> list[Edge]:
        """Extract call and use edges from a Rust file."""
        # Build method resolver lazily (once per analyze() call).
        # Keyed on global_symbols identity to invalidate between runs.
        cache = getattr(self, "_mr_cache", None)
        if cache is None or cache[0] is not global_symbols:
            global_methods: dict[str, list[Symbol]] = {}
            for sym in global_symbols.values():
                if sym.kind in ("method", "function"):
                    short = sym.name.split("::")[-1] if "::" in sym.name else sym.name
                    global_methods.setdefault(short, []).append(sym)
            mr = ListNameResolver(global_methods, ambiguity_threshold=3)
            self._mr_cache = (global_symbols, mr)
        else:
            mr = cache[1]

        # Build span index from global_symbols for this file.
        # local_symbols (symbol_by_name) loses entries when short names
        # collide — e.g., free function "caller" is overwritten by
        # method "Foo::caller".  The global registry preserves all
        # qualified names, so filtering by path gives a complete set.
        # Symbols store paths as relative (rel_path), not absolute.
        file_syms = [
            s for s in global_symbols.values()
            if s.path == rel_path and s.kind in ("function", "method")
        ]
        span_idx = {(s.span.start_line, s.span.end_line): s for s in file_syms}

        # WI-milak / BUG-04: pull the kind-segregated multi-value index
        # populated by register_symbol so the impl_item handler can
        # prefer ``trait Foo`` over ``struct Foo`` when both share a
        # short name. Empty-fallback for synthetic callers that bypass
        # register_symbol; those paths fall back to the WI-kahaz
        # single-value lookup.
        kind_cache = getattr(self, "_kind_index_cache", None)
        if kind_cache is not None and kind_cache[0] is global_symbols:
            kind_index = kind_cache[1]
        else:
            kind_index = {}

        # WI-titor: pre-compute file-scoped var_types from let bindings
        # and parameter declarations, consulting the cross-file
        # method-return-type registry for chained method calls. The
        # registry comes from base.py's Pass-1 aggregation step
        # (TreeSitterAnalyzer.analyze §4c).
        method_return_type_registry = getattr(
            self, "_method_return_type_registry", {},
        )
        var_types = _extract_var_types_rust(
            tree.root_node, source, method_return_type_registry,
        )
        var_type_paths = _extract_qualified_var_type_paths(
            tree.root_node, source,
        )

        return _extract_edges_from_file(
            tree, source, rel_path,
            local_symbols, global_symbols,
            run.execution_id, resolver, import_aliases,
            method_resolver=mr,
            span_index=span_idx,
            field_type_registry=self._field_type_registry,
            analyzer=self,
            kind_index=kind_index,
            var_types=var_types,
            var_type_paths=var_type_paths,
        )

    def extract_usage_contexts_from_file(
        self,
        tree: "tree_sitter.Tree",
        source: bytes,
        file_path: Path,
        symbol_by_name: dict[str, Symbol],
    ) -> list[UsageContext]:
        """Extract Axum route usage contexts from a Rust file."""
        return _extract_axum_usage_contexts(
            tree.root_node, source, file_path, symbol_by_name,
        )

    def post_process(
        self,
        symbols: list[Symbol],
        edges: list[Edge],
        usage_contexts: list[UsageContext],
        run: "AnalysisRun",
    ) -> tuple[list[Symbol], list[Edge], list[UsageContext]]:
        """Extract decorated_by edges from attribute metadata."""
        # Build global symbols map for attribute resolution
        global_symbols: dict[str, Symbol] = {}
        for sym in symbols:
            global_symbols[sym.name] = sym
            short_name = sym.name.split("::")[-1] if "::" in sym.name else sym.name
            global_symbols[short_name] = sym

        attribute_edges = _extract_attribute_edges(symbols, global_symbols, run.execution_id)
        edges.extend(attribute_edges)
        return symbols, edges, usage_contexts


_analyzer = RustAnalyzer()


def is_rust_tree_sitter_available() -> bool:
    """Check if tree-sitter with Rust grammar is available."""
    return _analyzer._check_grammar_available()


@register_analyzer("rust")
def analyze_rust(repo_root: Path) -> AnalysisResult:
    """Analyze Rust files in a repository."""
    return _analyzer.analyze(repo_root)
