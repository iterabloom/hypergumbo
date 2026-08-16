# SPDX-License-Identifier: AGPL-3.0-or-later
"""Python AST analysis pass.

This analyzer uses Python's built-in ast module to extract symbols and
relationships from Python source files, with no external dependencies.

How It Works
------------
Analysis proceeds in two passes for cross-file resolution:

**Pass 1 - Symbol Collection:**
- Parse each .py file with ast.parse()
- Extract top-level functions and classes as symbols
- Extract methods nested inside classes
- Build import mappings for cross-file resolution
- Compute stable_id (signature-based) and shape_id (structure-based)
- Extract rich metadata (decorators, base classes, parameters) per ADR-3aaa

**Pass 2 - Edge Extraction:**
- Walk AST to find function/method call sites
- Resolve callees using local symbols first, then imports
- Detect self.method() calls within classes
- Detect self.field.method() calls using field type inference from __init__
- Detect ClassName() instantiation patterns
- Track return type annotations for variable type inference
- Create import edges from files to imported symbols

Detected Patterns
-----------------
- Function calls: helper(), module.func()
- Method calls: self.method(), obj.method(), self.field.method()
- Class instantiation: ClassName()
- Module attribute reads: os.environ, sys.argv, sys.path — bare
  (non-called) ``imported_module.attribute`` accesses. Emits
  ``module_attr_ref`` edges so IO-primitive catalog ``attributes:``
  entries become reachable by ``io-boundaries`` (WI-guhok).
- Imports: from X import Y, import X
- Django URL patterns: path(), re_path(), url() calls in urls.py
- Flask URL rules: app.add_url_rule() calls

Route Detection Architecture
-----------------------------
Call-based URL routing (Django path(), Flask add_url_rule()) produces two
outputs that serve different downstream consumers:

1. **UsageContext records** — matched by YAML framework patterns (django.yaml,
   flask.yaml) to enrich *handler* symbols with ``concept: route`` metadata.
   This lets the enrichment layer tag view functions as route handlers.

2. **Route symbols** (``kind="route"``) — consumed by the ``route_handler``
   linker to create ``routes_to`` edges from route entities to handler symbols.
   These are first-class nodes in the IR representing the route itself.

Both are derived from the same extraction pass (_extract_django_usage_contexts,
_extract_flask_usage_contexts). Route symbols are created from the UsageContext
metadata at the callsite. This avoids duplicating the AST-walking logic while
preserving both outputs. Go and JS/TS analyzers follow the same dual-output
pattern.

ID Schemes
----------
- **stable_id**: sha256 of signature (param count, arity flags, decorators).
  Survives renames and moves if signature unchanged.
- **shape_id**: sha256 of AST structure (control flow, nesting).
  Detects clones with different variable names.

Rich Metadata (ADR-3aaa)
------------------------
Symbols include structured metadata in `meta` dict:
- **decorators**: List of decorator info with name, args, kwargs.
  Example: `[{"name": "app.get", "args": ["/users"], "kwargs": {"tags": ["api"]}}]`
- **base_classes**: List of base class names for classes.
  Example: `["BaseModel", "Generic[T]"]`
- **parameters**: List of parameter info for functions/methods.
  Example: `[{"name": "x", "type": "int", "default": False}]`

Why This Design
---------------
- Built-in ast module requires no dependencies and handles all Python syntax
- Two-pass approach enables cross-file call resolution via imports
- col_offset == 0 heuristic distinguishes top-level from nested functions
- Import resolution handles both absolute and relative imports
- Rich metadata feeds YAML-driven framework pattern enrichment (ADR-3aaa)
"""
import ast
import hashlib
import warnings
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Callable, Iterator

from hypergumbo_core.dataflow import annotate_dataflow_ast, get_dataflow_config
from hypergumbo_core.discovery import find_files
from hypergumbo_core.ir import AnalysisRun, Edge, ExternalRef, PASS_VERSION, Span, Symbol, UsageContext, make_pass_id
from hypergumbo_core.analyze.base import (
    AnalysisResult,
    assemble_stable_id,
    make_file_stable_id,
    make_route_symbol,
    make_typed_stable_id,
    visibility_from_modifiers,
)
from hypergumbo_core.analyze.registry import register_analyzer
from hypergumbo_lang_mainstream._pyscope import NestedDef, Scope, ScopeStack

if TYPE_CHECKING:
    from hypergumbo_core.symbol_resolution import SymbolResolver


def find_python_files(
    repo_root: Path, max_files: int | None = None
) -> Iterator[Path]:
    """Yield all Python files in the repository, excluding common non-source dirs."""
    yield from find_files(repo_root, ["*.py"], max_files=max_files)


def _python_visibility_modifiers(name: str) -> list[str]:
    """Derive visibility modifiers from Python naming convention.

    Single underscore prefix (``_name``) = private.
    Double underscore prefix without trailing double underscore (``__name``)
    = private (name-mangled).
    Dunders (``__name__``) are special methods, not private.
    No prefix = public (empty list, since Python has no explicit modifier).
    """
    # Strip qualified prefix: "Class._method" → check "_method"
    short = name.rsplit(".", 1)[-1] if "." in name else name
    if short.startswith("_") and not (short.startswith("__") and short.endswith("__")):
        return ["private"]
    return []


def _extract_module_all(tree: "ast.Module") -> frozenset[str] | None:
    """Extract the module-level ``__all__`` name set from *tree*.

    WI-gipag (WI-zimum Phase 2): the module-level ``__all__`` list
    declares the public API surface of a Python module. When present,
    only names in ``__all__`` should be flagged ``is_exported=True``;
    all other top-level symbols remain un-exported regardless of
    naming convention.

    Returns the frozenset of string names in ``__all__`` (possibly
    empty), or ``None`` if the file does not define a module-level
    ``__all__`` assignment. The ``None`` vs empty-set distinction
    matters because the callers use it to decide between
    ``__all__``-driven filtering and the fallback leading-underscore
    rule.

    Supported forms:
    - ``__all__ = ["foo", "bar"]``       (list literal)
    - ``__all__ = ("foo", "bar")``       (tuple literal)
    - ``__all__: list[str] = ["foo"]``   (annotated assignment)

    Non-literal forms (``__all__ = other_module.__all__``,
    ``__all__ += ["baz"]``, list comprehensions) are not
    interpreted — they are rare and would require evaluation. The
    caller treats "unparseable ``__all__``" the same as "no
    ``__all__``" and falls back to the leading-underscore rule.
    """
    for node in tree.body:
        target_name: str | None = None
        value: ast.expr | None = None

        if isinstance(node, ast.Assign) and len(node.targets) == 1:
            target = node.targets[0]
            if isinstance(target, ast.Name) and target.id == "__all__":
                target_name = "__all__"
                value = node.value
        elif isinstance(node, ast.AnnAssign):
            target = node.target
            if isinstance(target, ast.Name) and target.id == "__all__":
                target_name = "__all__"
                value = node.value

        if target_name is None or value is None:
            continue

        if isinstance(value, (ast.List, ast.Tuple)):
            names: list[str] = []
            for elt in value.elts:
                if isinstance(elt, ast.Constant) and isinstance(elt.value, str):
                    names.append(elt.value)
                # Anything non-literal (e.g., a Name reference, a call)
                # makes the __all__ interpretation ambiguous. Return
                # None to fall back to the leading-underscore rule.
                else:
                    return None
            return frozenset(names)
        # __all__ set via a non-literal expression — unparseable.
        return None
    return None


def _is_python_top_level_exported(
    name: str, module_all: frozenset[str] | None,
) -> bool:
    """Return True if *name* is part of the Python module's public API.

    WI-gipag: when the module has an ``__all__`` list, membership in
    ``__all__`` is authoritative. Otherwise, the leading-underscore
    convention applies — names not starting with ``_`` are public.
    Dunders (``__name__``) are never considered exported by this rule;
    they are special methods / module hooks, not user-facing API.
    """
    if module_all is not None:
        return name in module_all
    if name.startswith("_"):
        return False
    return True


def normalize_python_signature(
    signature: str | None,
    type_params: list[str] | None = None,
) -> str | None:
    """Normalize a Python signature for typed stable_id (ADR-0014 §3)."""
    from hypergumbo_core.analyze.base import normalize_signature_names_first
    return normalize_signature_names_first(
        signature, type_params, return_sep="->", skip_self=True,
    )


def _make_symbol_id(path: str, line: int, end_line: int, name: str, kind: str) -> str:
    """Generate location-based ID in format {lang}:{file}:{start}-{end}:{name}:{kind}."""
    return f"python:{path}:{line}-{end_line}:{name}:{kind}"


def _emit_module_level_assign_symbols(
    tree: "ast.Module",
    py_file: Path,
    module_all: frozenset[str] | None,
) -> list[Symbol]:
    """Emit ``Symbol(kind="variable", ...)`` for each top-level binding.

    Without this pass, ``from <mod> import NAME`` for any module-level
    constant (e.g. ``LANGUAGE_ALIASES``, ``PASS_VERSION``) misses the
    cross-file lookup and synthesises a tier-3 ``external_symbol`` —
    151 such ALL-CAPS externals on hypergumbo self-analysis (WI-gafog E2).

    Walks ``tree.body`` (top-level statements only). Handles:

    * ``ast.Assign`` with one or more ``Name`` targets, including
      tuple/list-unpacking targets like ``A, B = 1, 2``.
    * ``ast.AnnAssign`` with a ``Name`` target (``X: int = 1`` or ``X: int``).

    Does NOT emit for:

    * ``ast.AugAssign`` (``X += 1``) — mutation, not a fresh binding.
    * Subscript or attribute targets (``X[0] = 1``, ``X.y = 1``).
    * Names defined inside class or function bodies — those are not
      module-level (the walk only inspects ``tree.body``).
    """
    out: list[Symbol] = []
    for node in tree.body:
        targets: list[ast.Name] = []
        if isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Name):
                    targets.append(t)
                elif isinstance(t, (ast.Tuple, ast.List)):
                    for el in t.elts:
                        if isinstance(el, ast.Name):
                            targets.append(el)
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            targets.append(node.target)
        else:
            continue
        constructed_from = _constructed_from(node)
        for tgt in targets:
            line = tgt.lineno
            end_line = node.end_lineno or line
            span = Span(
                start_line=line,
                end_line=end_line,
                start_col=tgt.col_offset,
                end_col=node.end_col_offset or 0,
            )
            out.append(
                Symbol(
                    id=_make_symbol_id(str(py_file), line, end_line, tgt.id, "variable"),
                    name=tgt.id,
                    kind="variable",
                    language="python",
                    path=str(py_file),
                    span=span,
                    origin="",
                    origin_run_id="",
                    shape_id=_compute_value_shape_id(node, "variable"),
                    meta=(
                        {"constructed_from": constructed_from}
                        if constructed_from
                        else None
                    ),
                    modifiers=_python_visibility_modifiers(tgt.id),
                    is_exported=_is_python_top_level_exported(tgt.id, module_all),
                )
            )
    return out


def _callee_dotted_name(node: ast.AST) -> "str | None":
    """Render a call's callee as a dotted name, or None if it is not one.

    ``FastAPI()`` -> ``"FastAPI"``; ``fastapi.FastAPI()`` ->
    ``"fastapi.FastAPI"``. Qualification is KEPT (WI-nopod): a framework YAML
    keying on a namespaced callee needs it, and stripping it would make
    ``sqlalchemy.orm.declarative_base`` indistinguishable from a same-named
    local. A computed callee (``factories[k]()``) has no dotted name and
    yields None rather than a guess.
    """
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        base = _callee_dotted_name(node.value)
        return f"{base}.{node.attr}" if base else None
    return None


def _constructed_from(node: ast.AST) -> "str | None":
    """The callee a binding's value comes from, for ``meta['constructed_from']``.

    Only an ``ast.Call`` value counts — ``TIMEOUT = 30`` is not a
    construction, and stamping the key on every variable would make it
    useless as a filter. Static analysis cannot tell a constructor from a
    factory (``declarative_base()``), and this deliberately does not try:
    it names the callee, which is what the AST supports and what a framework
    integration author actually keys on.
    """
    value = getattr(node, "value", None)
    if not isinstance(value, ast.Call):
        return None
    return _callee_dotted_name(value.func)


def _make_file_id(path: str) -> str:
    """Generate ID for a Python file node (used as import edge source)."""
    return f"python:{path}:1-1:file:file"


def _make_module_id(module_name: str) -> str:
    """Generate ID for an external module (used as import edge destination)."""
    return f"python:{module_name}:0-0:module:module"


def _extract_return_type_name(signature: str | None) -> str | None:
    """Extract simple return type name from a function signature string.

    Parses signatures like "(x: int) -> MyClass" and returns "MyClass".
    Only handles simple (non-generic) return types — returns None for
    complex types like "Optional[X]", "list[X]", "X | Y", etc.

    Args:
        signature: Function signature string from Symbol.signature.

    Returns:
        The simple class name if found, None otherwise.
    """
    if not signature or " -> " not in signature:
        return None
    ret_part = signature.rsplit(" -> ", 1)[1]
    # Only handle simple names (identifiers), not generics or unions
    if ret_part.isidentifier():
        return ret_part
    return None


def _resolve_return_type_class(
    type_name: str,
    func_symbol: "Symbol",
    local_symbols: dict[str, "Symbol"],
    imports: dict[str, tuple[str, str]],
    global_symbols: dict[tuple[str, str], "Symbol"],
    resolver: "SymbolResolver | None" = None,
    sym_by_path_name: dict[tuple[str, str], "Symbol"] | None = None,
) -> "Symbol | None":
    """Resolve a return type name to a class Symbol.

    Searches for the class in three places (in order):
    1. The caller's local symbols (same file as the call site)
    2. The caller's imports
    3. The function's own module (the return type is usually co-located
       with the function that returns it)

    Only returns symbols with kind == "class".

    Args:
        type_name: Simple class name (e.g., "ServiceClient").
        func_symbol: The function Symbol whose return type we're resolving.
        local_symbols: Symbols defined in the caller's file.
        imports: Import mappings from the caller's file.
        global_symbols: All symbols across the project.
        resolver: Optional SymbolResolver for efficient lookups.

    Returns:
        The class Symbol if found, None otherwise.
    """
    # Check caller's local symbols first
    sym = local_symbols.get(type_name)
    if sym and sym.kind == "class":
        return sym
    # Check caller's imports
    if type_name in imports:
        module_name, original_name = imports[type_name]
        sym = _lookup_symbol_by_module(
            global_symbols, module_name, original_name, resolver=resolver
        )
        if sym and sym.kind == "class":
            return sym
    # Check function's own module — the return type class is typically
    # defined in the same file as the function
    if sym_by_path_name is not None:
        sym = sym_by_path_name.get((func_symbol.path, type_name))
        if sym and sym.kind == "class":
            return sym
    return None


def _lookup_symbol_by_module(
    global_symbols: dict[tuple[str, str], "Symbol"],
    module_name: str,
    symbol_name: str,
    *,
    resolver: "SymbolResolver | None" = None,
) -> "Symbol | None":
    """Look up a symbol with suffix-based module matching.

    When an import says 'from app.crud import X' but the file is registered
    as 'backend.app.crud', exact lookup fails. This function handles such
    cases by trying suffix matching.

    This is a thin wrapper around the shared SymbolResolver. For repeated
    lookups, pass a pre-built resolver for better performance (cached indexes).

    Args:
        global_symbols: Map of (module, name) -> Symbol
        module_name: The module name from the import statement
        symbol_name: The symbol name being imported
        resolver: Optional pre-built SymbolResolver for cached lookups.

    Returns:
        The matching Symbol, or None if not found.
    """
    if resolver is not None:
        result = resolver.lookup(module_name, symbol_name)
        return result.symbol

    # Fallback: use the shared lookup_symbol function (creates new resolver)
    from hypergumbo_core.symbol_resolution import lookup_symbol
    return lookup_symbol(global_symbols, module_name, symbol_name)


# WI-fuvuj: stdlib I/O constructors whose return value's type we can infer
# from the constructor name alone. Key = the qualified constructor name
# (bare for builtins like ``open``; ``module.attr`` for module constructors
# like ``socket.socket``). Value = the catalog module string the inferred
# receiver's method-call dst will carry, so io-boundary's module-filter path
# disambiguates ``f.read()`` / ``s.send()`` into the right boundary bucket
# instead of the undifferentiated ``external_potential`` bucket.
#
# The file-object value MUST be exactly ``"file"`` — it is coordinated with
# the synthetic ``file`` module in the python.yaml catalog (fs_read read/
# readline/readlines, fs_write write/writelines).
# ``pathlib.Path`` needs BOTH keys because the two call forms enter different
# branches below: ``from pathlib import Path`` arrives as a bare ``ast.Name`` and
# ``import pathlib`` as a dotted ``ast.Attribute``. Measured over the corpus, bare is
# 103 of 136 constructor sites (65 reaches) and dotted is 33 (29 reaches), so a
# one-key patch delivers roughly two thirds of the payload and looks complete.
#
# DERIVED FROM THE CATALOGUE, NOT CURATED (INV-linub). The four rows this table
# held by hand were four of the SEVENTEEN receiver types ``python.yaml`` already
# declares in the ``module`` slot of its ``kind: method`` primitives, so fifteen
# types could never be minted and every method hanging off them was structurally
# unreachable — measured at 83 of 215 expressible primitives (38.6%) unreachable
# from an idiomatic call site, 78 of them method-kind
# (``scripts/measure-catalogue-reach.py python``). Two homes for one fact is what
# produced that, so the table now READS the catalogue instead of restating it and
# a type added to the YAML tomorrow is mintable the same day.
#
# DIRECTION, MEASURED: finding-ADDING only. The fifteen newly-mintable types
# carry 43 of 83 python taint SOURCES and 35 of 113 SINKS, and 0 of 4 sanitizers
# — so this cannot arm the WI-fasub barrier arm, where a ``False`` earns
# ``sanitized`` and DROPS a flow from the violation set. The one channel that can
# still cost precision is the standing one: a typed external receiver walks into
# the strip-to-bare-name lookup against the repo's own symbols.
#
# WHY ``io_primitives`` AND NOT ALSO THE TAINT CATALOGUE. The taint catalogue
# names two module strings ``io_primitives`` does not —
# ``cryptography.hazmat.primitives.asymmetric`` and ``...ciphers.aead`` — and
# NEITHER IS A CONSTRUCTIBLE TYPE: their entries carry the class in the NAME slot
# (``AESGCM.decrypt``, ``rsa.generate_private_key``), so the module slot holds a
# module and ``asymmetric(...)`` constructs nothing. Reading that catalogue too
# would therefore add zero usable rows today while requiring a dotted-name-slot
# guard. RE-EVALUATION TRIGGER, so this stays a decision rather than an
# assumption: revisit if a taint entry ever declares a receiver type in the
# module slot with a plain method name beside it.
def _derive_external_constructor_types() -> dict[str, str]:
    """``{constructor key: catalog module}`` for every catalogued receiver type.

    Each type yields TWO keys because the two call forms enter different branches
    of :func:`_external_constructor_type`: ``import smtplib`` arrives as a dotted
    ``ast.Attribute`` and ``from smtplib import SMTP`` as a bare ``ast.Name``.

    A leaf name claimed by two distinct types is WITHHELD rather than resolved to
    whichever the iteration reached first — a silently mis-typed receiver mints a
    boundary and a taint sink for the wrong module, which is the fabricated-finding
    direction. There are no such collisions today; the guard is here so that adding
    a colliding type to the YAML degrades to "unreachable" (safe) instead of
    "attributed to its namesake" (a false positive nobody would look for).
    """
    from hypergumbo_core.io_boundary import load_catalog

    types = {
        p.module for p in load_catalog("python").primitives
        if p.kind == "method" and p.module and "." in p.module
    }
    leaves: dict[str, list[str]] = {}
    for type_name in sorted(types):
        leaves.setdefault(type_name.rsplit(".", 1)[1], []).append(type_name)
    derived: dict[str, str] = {}
    for type_name in sorted(types):
        derived[type_name] = type_name
    for leaf, claimants in leaves.items():
        if len(claimants) == 1:
            derived[leaf] = claimants[0]
    # NOT catalogue-derived: the synthetic ``file`` module carries no dot and
    # hangs off no constructor name the YAML names, so deriving alone would drop
    # ``open`` and take ``f.read()`` / ``f.write()`` with it.
    derived["open"] = "file"
    return derived


EXTERNAL_CONSTRUCTOR_TYPES = _derive_external_constructor_types()

#: Bare-name rows that are REAL BUILTINS, and therefore still trusted when no import
#: binds them.
#:
#: THE DISTINCTION IS LOAD-BEARING AND IT IS WHY THIS SET EXISTS. The bare-name branch
#: used to trust any unbound name on the reasoning "for a bare name that means the
#: builtin". That holds for ``open``. It does not hold for ``Path``, which is not a
#: builtin — an unbound ``Path`` is a locally defined class, a star-import, or a name
#: from a module the analyzer never read. Trusting it would mint an ``fs_write``
#: boundary and a ``host_fs`` taint SINK for any class in the corpus merely named
#: ``Path``, and 254 corpus sites have a constructor name that is also an in-repo class
#: name. So membership here is the PERMITTING case for the unbound path (default-deny);
#: every other row must be positively bound to the module it claims, tightening
#: INV-kipor's check from "not contradicted" to "confirmed".
BUILTIN_CONSTRUCTOR_NAMES: frozenset[str] = frozenset({"open"})

#: Members that RETURN THE RECEIVER'S OWN TYPE, keyed by the exact type string the
#: analyzer puts in a symbol id's module slot. ``__truediv__`` carries the ``/``
#: operator under Python's own name for it, so an operator needs no separate vocabulary.
#:
#: DEFAULT-DENY, AND THAT IS MEASURED RATHER THAN CAUTIOUS. Most members of a typed
#: receiver do not return that type: ``read_text`` → ``str``, ``stat`` →
#: ``os.stat_result``, ``exists`` → ``bool``, ``open`` → a file object,
#: ``glob``/``iterdir`` → iterators, ``name``/``stem``/``suffix``/``as_posix`` → ``str``.
#: Propagating by default scores 25.9% precision on adversarial fixtures (7 of 27 added
#: boundaries correct) and mints 16 false taint sinks including 2 ``database`` and 2
#: ``network``; across 8 Python repos 1,955 of 2,296 (85.1%) hop≥1 propagations are
#: provably wrong or unverifiable, 20 of the 21 provably-wrong ones being a Path→``str``
#: transition. This allowlist scores 85.7% on the same fixtures and costs 3 boundaries out
#: of 427 (0.7%). Enumerating the PERMITTING case is also the standing default-deny rule:
#: a table of blockers fails open the moment the stdlib grows a member.
#:
#: KEYED BY EXACT TYPE STRING, looked up with ``dict``/``in`` and never through
#: :func:`io_boundary._module_matches`. That predicate is permissive by design — it accepts
#: an unqualified reference as a component suffix, so it treats a vendored
#: ``mylib.pathlib.Path`` as the real one and mints an ``fs_write`` boundary plus a
#: ``host_fs`` taint sink for a third-party class that merely shares a name.
#:
#: THE CONCEPT'S HOME IS ``FileAnalysis.method_return_types`` (INV-dihos / WI-kuroj), the
#: language-neutral return-type registry Go and Rust already populate from parsed
#: signatures. This table is the stdlib complement Python needs — the types here ship no
#: source for Pass 1 to parse — so it states the same fact in the same shape (qualified
#: member → returned type) rather than minting a new catalogue for it.
TYPE_PRESERVING_MEMBERS: dict[str, frozenset[str]] = {
    "pathlib.Path": frozenset({
        "__truediv__", "joinpath", "resolve", "absolute", "expanduser",
        "with_name", "with_suffix", "with_stem", "relative_to", "readlink",
    }),
}

# WI-sozoj: Django ORM database-I/O visibility. Django's ORM I/O is invisible to
# the io-boundary detector because it arrives as bare untyped method calls the
# catalog correctly refuses (INV-tapat/INV-maluk): ``.save()``/``.filter()``/
# ``.get()`` on a receiver hypergumbo cannot type; matching them by short name
# would false-positive on every ``dict.get()``/``.save()`` in the corpus. We make
# it visible the SANCTIONED way — TYPE the receiver via a framework-syntax marker
# and emit a ``django.db.models``-module-qualified dst, so io-boundary's
# module-filter path (never the short-name gate) classifies each method as
# db_read/db_write via the python.yaml catalog. This is the WI-fuvuj division
# (producer supplies module IDENTITY; the catalog does the CLASSIFICATION) and
# the receiver-type-inference route python.yaml's WI-harin note reserves for
# exactly this. Framework-SYNTAX recognition in the analyzer, like the Django
# route/signal extraction below — NOT dispatch modelling (that stays in the
# django_orm_dispatch linker, whose orthogonal concern is dispatches_to
# reachability, not io classification).
#
# Two type-verifying markers, each bounded to a closed method set so a non-Django
# homonym stays invisible rather than mis-tagged:
#   * ``<Model>.objects.<method>()`` — the Manager/QuerySet query API. ``.objects``
#     is Django's Manager-descriptor convention; the chained receiver emits no
#     edge otherwise (measured). Catches reads (filter/get/all/...) AND
#     Manager-position writes (create/bulk_create/update/...).
#   * ``self.save()``/``self.delete()`` in a class that DIRECTLY extends
#     ``models.Model`` — the ORM instance-write surface.
# The read/write split lives in the catalog (python.yaml keyed on method name);
# the producer only needs the recognition set. Deferred (share the same
# instance/return-type-inference need, out of scope here): ``instance.save()`` on
# a typed local, SQLAlchemy ``Session.*``, and transitive Model bases.
DJANGO_ORM_MODULE = "django.db.models"
DJANGO_ORM_MANAGER_METHODS = frozenset({
    # reads (classified db_read in python.yaml)
    "all", "filter", "exclude", "get", "count", "exists", "first", "last",
    "values", "values_list", "annotate", "aggregate", "order_by", "distinct",
    "none", "iterator", "earliest", "latest", "in_bulk",
    "select_related", "prefetch_related",
    # writes (classified db_write in python.yaml)
    "create", "bulk_create", "update", "bulk_update", "delete",
    "get_or_create", "update_or_create",
})
DJANGO_ORM_INSTANCE_WRITE_METHODS = frozenset({"save", "delete"})
# DIRECT ``models.Model`` bases only, dotted form only — the unambiguous Django
# idiom (``class Order(models.Model)``). A transitive base or a bare ``Model``
# degrades to invisible (INV-tapat precision-safe: a missed ORM write, never a
# mis-tagged non-ORM call).
DJANGO_MODEL_BASES = frozenset({"models.Model", "django.db.models.Model"})

# Django URL pattern functions (call-based routing)
# These emit UsageContext records for YAML pattern matching (v1.1.x)
DJANGO_URL_FUNCTIONS = {"path", "re_path", "url"}

# Flask/FastAPI call-based URL routing functions.
# Flask's add_url_rule() is the call-based alternative to @app.route().
# FastAPI's add_api_route() registers routes programmatically instead of
# using @router.get() decorators.  Both take a path string as the first
# argument and a handler function as a subsequent argument.
# Flask-RESTful's add_resource() takes the resource class as the first
# argument and URL path(s) as subsequent arguments.
FLASK_URL_FUNCTIONS = {"add_url_rule", "add_api_route", "add_resource"}

# Starlette routing classes. Unlike Flask's call-based functions,
# Starlette's Route(...) and WebSocketRoute(...) are bare constructor calls,
# not method calls on an app or router. We require import-scoped matching
# (the name must be imported from starlette.routing) to avoid false positives
# from any other Route class a repo defines locally.
STARLETTE_ROUTE_FUNCTIONS = {"Route", "WebSocketRoute"}
_STARLETTE_ROUTING_MODULE = "starlette.routing"


def _ast_value_to_python(node: ast.expr) -> str | int | float | bool | list | dict | None:
    """Convert an AST expression to a Python value representation.

    For simple literals, returns the actual value.
    For complex expressions (names, calls, etc.), returns string representation.
    """
    if isinstance(node, ast.Constant):
        # Handle non-JSON-serializable constants
        if node.value is ...:
            return "..."
        if isinstance(node.value, complex):
            return str(node.value)  # "1+2j" format
        if isinstance(node.value, bytes):
            return repr(node.value)  # "b'...'" format
        return node.value
    elif isinstance(node, ast.Name):
        # Variable reference - return name as string
        return node.id
    elif isinstance(node, ast.List):
        return [_ast_value_to_python(elt) for elt in node.elts]
    elif isinstance(node, ast.Tuple):
        return [_ast_value_to_python(elt) for elt in node.elts]
    elif isinstance(node, ast.Dict):
        result = {}
        for k, v in zip(node.keys, node.values, strict=True):
            if k is not None:
                key = _ast_value_to_python(k)
                if isinstance(key, str):
                    result[key] = _ast_value_to_python(v)
        return result
    elif isinstance(node, ast.Attribute):
        # e.g., SomeClass.field -> "SomeClass.field"
        return _format_annotation(node)
    elif isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub):
        # Negative number
        val = _ast_value_to_python(node.operand)
        if isinstance(val, (int, float)):
            return -val
        return f"-{val}"  # pragma: no cover - defensive for non-numeric negation
    elif isinstance(node, ast.Call):
        # Function call as decorator arg, e.g., _add_static_prefix("/health").
        # If the first positional argument is a resolvable literal, return it.
        # This handles wrapper patterns common in Flask/FastAPI where a helper
        # function wraps a route path string.
        if node.args:
            first_arg = _ast_value_to_python(node.args[0])
            if isinstance(first_arg, str) and first_arg != "<complex>":
                return first_arg
        # Fall through to string representation
        return _format_annotation(node) or "<complex>"  # pragma: no cover
    elif isinstance(node, ast.BinOp) and isinstance(node.op, (ast.Add, ast.Sub)):
        # Complex number literal like 1+2j or 1-2j
        left = _ast_value_to_python(node.left)
        right = _ast_value_to_python(node.right)
        # Check if this looks like a complex number (real +/- imaginary)
        if isinstance(left, (int, float)) and isinstance(right, str) and right.endswith("j"):
            op = "+" if isinstance(node.op, ast.Add) else "-"
            return f"({left}{op}{right})"
        # Fall through to string representation for other BinOps
        return _format_annotation(node) or "<binop>"  # pragma: no cover
    else:
        # Complex expression - return string representation
        return _format_annotation(node) or "<complex>"  # pragma: no cover


def _extract_decorator_info(dec: ast.expr) -> dict[str, object]:
    """Extract full decorator information including arguments.

    Returns a dict with:
        name: Decorator name (e.g., "app.get", "dataclass")
        args: List of positional arguments
        kwargs: Dict of keyword arguments
    """
    name = ""
    args: list[object] = []
    kwargs: dict[str, object] = {}

    if isinstance(dec, ast.Name):
        # @decorator
        name = dec.id
    elif isinstance(dec, ast.Attribute):
        # @module.decorator (without call)
        name = _format_annotation(dec)
    elif isinstance(dec, ast.Call):
        # @decorator(...) or @module.decorator(...)
        if isinstance(dec.func, ast.Name):
            name = dec.func.id
        elif isinstance(dec.func, ast.Attribute):
            name = _format_annotation(dec.func)
        else:
            name = "<unknown>"  # pragma: no cover - defensive for unusual decorator forms

        # Extract positional arguments
        for arg in dec.args:
            args.append(_ast_value_to_python(arg))

        # Extract keyword arguments
        for kw in dec.keywords:
            if kw.arg is not None:  # Skip **kwargs unpacking
                kwargs[kw.arg] = _ast_value_to_python(kw.value)

    return {"name": name, "args": args, "kwargs": kwargs}


def _extract_parameters_info(
    args: ast.arguments, exclude_self: bool = False
) -> list[dict[str, object]]:
    """Extract structured parameter information from function arguments.

    Args:
        args: AST arguments node
        exclude_self: If True, skip 'self' and 'cls' parameters

    Returns:
        List of dicts with name, type, and default keys
    """
    params: list[dict[str, object]] = []
    defaults_offset = len(args.args) - len(args.defaults)

    for i, arg in enumerate(args.args):
        if exclude_self and i == 0 and arg.arg in ("self", "cls"):
            continue
        has_default = i >= defaults_offset
        type_str = _format_annotation(arg.annotation) if arg.annotation else None
        params.append({
            "name": arg.arg,
            "type": type_str if type_str else None,
            "default": has_default,
        })

    # Handle *args
    if args.vararg:
        type_str = _format_annotation(args.vararg.annotation) if args.vararg.annotation else None
        params.append({
            "name": f"*{args.vararg.arg}",
            "type": type_str if type_str else None,
            "default": False,
        })

    # Handle **kwargs
    if args.kwarg:
        type_str = _format_annotation(args.kwarg.annotation) if args.kwarg.annotation else None
        params.append({
            "name": f"**{args.kwarg.arg}",
            "type": type_str if type_str else None,
            "default": False,
        })

    return params


def _format_annotation(node: ast.expr) -> str:
    """Format a type annotation node to a readable string."""
    if isinstance(node, ast.Name):
        return node.id
    elif isinstance(node, ast.Constant):
        return repr(node.value)
    elif isinstance(node, ast.Subscript):
        # e.g., List[int], Dict[str, int]
        base = _format_annotation(node.value)
        slice_val = _format_annotation(node.slice)
        return f"{base}[{slice_val}]"
    elif isinstance(node, ast.Tuple):
        # e.g., (int, str) for Dict keys
        elts = [_format_annotation(e) for e in node.elts]
        return ", ".join(elts)
    elif isinstance(node, ast.BinOp) and isinstance(node.op, ast.BitOr):
        # Union types: X | Y
        left = _format_annotation(node.left)
        right = _format_annotation(node.right)
        return f"{left} | {right}"
    elif isinstance(node, ast.Attribute):
        # e.g., typing.Optional
        value = _format_annotation(node.value)
        return f"{value}.{node.attr}"
    else:
        return ""  # pragma: no cover - defensive fallback for unknown AST types


def _format_arg(arg: ast.arg) -> str:
    """Format a single function argument."""
    result = arg.arg
    if arg.annotation:
        ann = _format_annotation(arg.annotation)
        if ann:
            result += f": {ann}"
    return result


# WI-hopiz: the DISPLAY Symbol.signature uses real default values and this
# generous cap; the stable_id / typed-normalize input keeps the legacy
# max_len=60 + "=…" form (default args of _format_function_signature) so
# identities do not churn.
_DISPLAY_SIGNATURE_MAX_LEN = 240


def _format_default(node: ast.expr, max_len: int = 32) -> str:
    """Render a parameter default value for the DISPLAY signature (WI-hopiz).

    Unparses the default expression so a consumer sees the real value (``50``,
    ``'hello'``, ``None``) instead of a bare ``…``, bounded to ``max_len`` so a
    pathological default (a big dict / lambda) cannot blow up the line; an
    over-long or unparseable default falls back to ``…``.
    """
    try:
        rendered = ast.unparse(node)
    except Exception:  # pragma: no cover - defensive; unparse is total on valid AST
        return "…"
    return rendered if len(rendered) <= max_len else "…"


def _format_function_signature(
    node: ast.FunctionDef | ast.AsyncFunctionDef,
    max_len: int = 60,
    render_defaults: bool = False,
) -> str:
    """Format a function signature from an AST node.

    Serves two callers with opposite needs (WI-hopiz):

    * **stable_id / typed-normalize input** — the default call (``max_len=60``,
      ``render_defaults=False``). Its output feeds ``normalize_python_signature``
      → ``make_typed_stable_id``, so it MUST stay byte-stable: any change here
      churns Python identities. Defaults render as a bare ``=…`` and the
      over-length cut is deliberately blind (it drops the closing paren, so
      ``normalize_python_signature`` returns ``None`` and the symbol falls back
      to the untyped stable_id — an established behavior).
    * **display ``Symbol.signature``** — called with ``render_defaults=True`` and
      a wide ``max_len``. Renders real default values and, when still over
      length, truncates the parameter list while PRESERVING the return type
      instead of a blind mid-content cut.

    Args:
        node: AST FunctionDef or AsyncFunctionDef node.
        max_len: Maximum length of the rendered signature.
        render_defaults: When True, unparse real default values (display mode).

    Returns:
        Formatted signature string like ``"(x: int, y: str='a') -> bool"``.
    """
    args = node.args
    all_args: list[str] = []

    # Positional-only args (before /)
    for arg in args.posonlyargs:
        all_args.append(_format_arg(arg))

    # Regular args
    for i, arg in enumerate(args.args):
        arg_str = _format_arg(arg)
        # Check for default value
        num_defaults = len(args.defaults)
        num_args = len(args.args)
        default_idx = i - (num_args - num_defaults)
        if 0 <= default_idx < num_defaults:
            arg_str += (
                "=" + _format_default(args.defaults[default_idx])
                if render_defaults
                else "=…"
            )
        all_args.append(arg_str)

    # *args
    if args.vararg:
        all_args.append(f"*{args.vararg.arg}")

    # Keyword-only args
    for i, arg in enumerate(args.kwonlyargs):
        arg_str = _format_arg(arg)
        if i < len(args.kw_defaults) and args.kw_defaults[i] is not None:
            arg_str += (
                "=" + _format_default(args.kw_defaults[i])
                if render_defaults
                else "=…"
            )
        all_args.append(arg_str)

    # **kwargs
    if args.kwarg:
        all_args.append(f"**{args.kwarg.arg}")

    args_str = "(" + ", ".join(all_args) + ")"

    # Add return type annotation if present
    ret_str = ""
    if node.returns:
        ret_type = _format_annotation(node.returns)
        if ret_type:
            ret_str = f" -> {ret_type}"
    sig = args_str + ret_str

    # Truncate if too long
    if len(sig) > max_len:
        if render_defaults:
            # Display mode: keep the return type and mark the elision instead of
            # a blind cut that would drop it (WI-hopiz).
            keep = max(max_len - len(ret_str) - 2, 1)
            sig = args_str[:keep] + "…)" + ret_str
        else:
            sig = sig[:max_len - 1] + "…"

    return sig


def _has_module_level_code(tree: ast.Module) -> bool:
    """Check if a module has executable code at module level.

    Returns True if the module has statements that aren't just imports,
    function/class definitions, or docstrings. These files need a <module>
    pseudo-node so module-level code has an enclosing scope for edges.

    Examples of module-level code:
    - producer.produce(topic, value)  # Function calls
    - config = load_config()          # Assignments
    - if __name__ == '__main__': ...  # Control flow
    """
    for i, node in enumerate(tree.body):
        # Skip docstrings (first constant string expression)
        if i == 0 and isinstance(node, ast.Expr):
            if isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
                continue

        # Skip imports
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            continue

        # Skip function/class definitions
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            continue

        # Skip pass statements
        if isinstance(node, ast.Pass):
            continue

        # Skip type aliases and annotations
        if isinstance(node, ast.AnnAssign):
            continue

        # Any other statement is executable module-level code
        return True

    return False


def _get_file_end_line(source: str) -> int:
    """Get the last line number of a source file."""
    return len(source.splitlines())


def _has_main_guard(tree: ast.Module) -> bool:
    """Check if a module has the `if __name__ == "__main__":` pattern.

    This is a structural entry point indicator for Python scripts.
    The pattern indicates the file is designed to be run as a script.

    Handles both:
    - if __name__ == "__main__":  (standard)
    - if "__main__" == __name__:  (reversed)
    - Single and double quotes

    Returns:
        True if the main guard pattern is detected, False otherwise.
    """
    for node in tree.body:
        if not isinstance(node, ast.If):
            continue

        test = node.test
        if not isinstance(test, ast.Compare):
            continue

        # Check for: __name__ == "__main__"
        if len(test.ops) != 1 or not isinstance(test.ops[0], ast.Eq):
            continue

        left = test.left
        comparators = test.comparators

        if len(comparators) != 1:  # pragma: no cover - defensive: len(ops) == len(comparators) in valid AST
            continue

        right = comparators[0]

        # Pattern 1: __name__ == "__main__"
        if (isinstance(left, ast.Name) and left.id == "__name__" and
                isinstance(right, ast.Constant) and right.value == "__main__"):
            return True

        # Pattern 2: "__main__" == __name__
        if (isinstance(left, ast.Constant) and left.value == "__main__" and
                isinstance(right, ast.Name) and right.id == "__name__"):
            return True

    return False


def _extract_django_usage_contexts(
    tree: ast.Module,
    file_path: str,
    symbol_by_name: dict[str, Symbol],
    local_constants: dict[str, str] | None = None,
    imports: dict[str, tuple[str, str]] | None = None,
    repo_root: Path | None = None,
) -> list[UsageContext]:
    """Extract UsageContext records for Django URL patterns.

    Creates UsageContext records that capture how view functions are used
    in path(), re_path(), url() calls. These are matched against YAML
    patterns in the enrichment phase.

    When ``local_constants`` and ``imports`` are provided, resolves
    dynamic route paths built from string concatenation or module-level
    constants (e.g., ``path(BASE + "/users/", view)``).

    Args:
        tree: The parsed AST module
        file_path: Path to the source file
        symbol_by_name: Lookup table for symbols defined in this file
        local_constants: Module-level string constant assignments
        imports: Imported names for cross-file constant resolution
        repo_root: Repository root for cross-file constant resolution

    Returns:
        List of UsageContext records for Django URL patterns.
    """
    contexts: list[UsageContext] = []

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue

        # Check if it's a Django URL function call
        func_name = None
        if isinstance(node.func, ast.Name):
            func_name = node.func.id
        elif isinstance(node.func, ast.Attribute):
            func_name = node.func.attr

        if func_name not in DJANGO_URL_FUNCTIONS:
            continue

        # Extract the URL pattern from the first argument
        if not node.args:  # pragma: no cover
            continue

        first_arg = node.args[0]
        route_path = _resolve_string_expr(
            first_arg,
            local_constants or {},
            imports,
            repo_root,
        )
        if route_path is None:
            if isinstance(first_arg, ast.JoinedStr):  # pragma: no cover
                continue  # Skip dynamic patterns (f-strings)
            continue  # pragma: no cover - unsupported pattern type

        # Extract view reference from second argument
        view_ref = None
        view_name = None
        is_class_based = False
        if len(node.args) >= 2:
            second_arg = node.args[1]
            if isinstance(second_arg, ast.Attribute):
                # views.user_list -> check if we can resolve it
                view_name = second_arg.attr
            elif isinstance(second_arg, ast.Name):
                # user_list -> check if it's defined locally
                view_name = second_arg.id
                # Try to resolve to a local symbol
                if view_name in symbol_by_name:
                    view_ref = symbol_by_name[view_name].id
            elif isinstance(second_arg, ast.Call):
                # views.LoginView.as_view() -> "LoginView"
                # TemplateView.as_view(template_name='...') -> "TemplateView"
                call_func = second_arg.func
                if isinstance(call_func, ast.Attribute) and call_func.attr == "as_view":
                    is_class_based = True
                    # Extract the class name from the as_view() call
                    if isinstance(call_func.value, ast.Attribute):
                        # views.LoginView.as_view() -> LoginView
                        view_name = call_func.value.attr
                        # Try to resolve to a local symbol (class)
                        if view_name in symbol_by_name:
                            view_ref = symbol_by_name[view_name].id
                    elif isinstance(call_func.value, ast.Name):
                        # LoginView.as_view() -> LoginView
                        view_name = call_func.value.id
                        if view_name in symbol_by_name:
                            view_ref = symbol_by_name[view_name].id

        # Build metadata with args info
        args_values = []
        for arg in node.args:
            if isinstance(arg, ast.Constant):
                args_values.append(arg.value)
            elif isinstance(arg, ast.Name):
                args_values.append(arg.id)
            elif isinstance(arg, ast.Attribute):
                # views.func -> "views.func"
                parts = []
                current: ast.expr = arg
                while isinstance(current, ast.Attribute):
                    parts.append(current.attr)
                    current = current.value
                if isinstance(current, ast.Name):
                    parts.append(current.id)
                args_values.append(".".join(reversed(parts)))
            else:  # pragma: no cover
                args_values.append("<expr>")

        # Normalize route path - ensure it starts with /
        normalized_path = route_path if route_path.startswith("/") else f"/{route_path}"

        span = Span(
            start_line=node.lineno,
            end_line=getattr(node, "end_lineno", node.lineno),
            start_col=getattr(node, "col_offset", 0),
            end_col=getattr(node, "end_col_offset", 0),
        )

        ctx = UsageContext.create(
            kind="call",
            context_name=func_name,
            position="args[1]",
            path=file_path,
            span=span,
            symbol_ref=view_ref,
            metadata={
                "args": args_values,
                "route_path": normalized_path,
                "view_name": view_name,
                "is_class_based_view": is_class_based,
            },
        )
        contexts.append(ctx)

    return contexts


def _collect_module_constants(
    tree: ast.Module,
    repo_root: Path | None = None,
    file_path: Path | None = None,
) -> tuple[dict[str, str], dict[str, tuple[str, str]]]:
    """Collect module-level string constants and import mappings from an AST.

    Scans top-level assignments of the form ``NAME = "literal"`` and
    ``from mod import NAME`` to build lookup tables for constant propagation.
    Relative imports are resolved using ``file_path`` and ``repo_root``.

    Used by ``_scan_router_prefixes`` for APIRouter prefix resolution and
    by route path extraction for dynamic route resolution (e.g.,
    ``path(BASE + "/users/", view)``).

    Args:
        tree: The parsed AST module.
        repo_root: Repository root for resolving relative imports.
        file_path: Path to the source file (for relative import resolution).

    Returns:
        Tuple of (local_constants, imports) where local_constants maps
        variable names to string values and imports maps local names to
        (module_path, original_name) tuples.
    """
    local_constants: dict[str, str] = {}
    for node in ast.iter_child_nodes(tree):
        if (
            isinstance(node, ast.Assign)
            and len(node.targets) == 1
            and isinstance(node.targets[0], ast.Name)
            and isinstance(node.value, ast.Constant)
            and isinstance(node.value.value, str)
        ):
            local_constants[node.targets[0].id] = node.value.value

    imports: dict[str, tuple[str, str]] = {}
    for node in ast.iter_child_nodes(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            module_path = node.module
            if node.level > 0 and file_path is not None:
                pkg_base = file_path.parent
                for _ in range(node.level - 1):
                    pkg_base = pkg_base.parent
                resolved = pkg_base / Path(*node.module.split("."))
                if repo_root is not None:
                    try:
                        rel = resolved.relative_to(repo_root)
                        module_path = ".".join(rel.parts)
                    except ValueError:  # pragma: no cover
                        pass
            for alias in node.names:
                local_name = alias.asname or alias.name
                imports[local_name] = (module_path, alias.name)

    return local_constants, imports


def _scan_router_prefixes(
    tree: ast.Module,
    repo_root: Path | None,
    file_path: Path | None = None,
) -> dict[str, str]:
    """Scan for FastAPI APIRouter(prefix=X) assignments and resolve prefixes.

    Finds assignments like ``v2_router = APIRouter(prefix="/v2")`` and builds
    a mapping from variable name to prefix string.  Handles three cases:

    1. Literal string: ``APIRouter(prefix="/v2")``
    2. Same-file constant: ``PREFIX = "/v2"; APIRouter(prefix=PREFIX)``
    3. Imported constant: ``from pkg.constants import PREFIX; APIRouter(prefix=PREFIX)``
       (requires ``repo_root`` and ``file_path`` to resolve relative imports)

    Args:
        tree: The parsed AST module.
        repo_root: Repository root for finding imported modules.
        file_path: Path to the source file (for resolving relative imports).

    Returns:
        Dict mapping variable name (e.g. "v2_router") to prefix string (e.g. "/v2").
    """
    local_constants, imports = _collect_module_constants(tree, repo_root, file_path)

    prefixes: dict[str, str] = {}

    for node in ast.walk(tree):
        # Match: var = APIRouter(prefix=X)
        if not isinstance(node, ast.Assign):
            continue
        if len(node.targets) != 1 or not isinstance(node.targets[0], ast.Name):
            continue
        if not isinstance(node.value, ast.Call):
            continue
        # Check the call is APIRouter(...)
        call = node.value
        func_name = None
        if isinstance(call.func, ast.Name):
            func_name = call.func.id
        elif isinstance(call.func, ast.Attribute):
            func_name = call.func.attr
        if func_name != "APIRouter":
            continue

        # Extract prefix= keyword argument
        prefix_value: str | None = None
        for kw in call.keywords:
            if kw.arg != "prefix":
                continue
            if isinstance(kw.value, ast.Constant) and isinstance(kw.value.value, str):
                # Case 1: literal string
                prefix_value = kw.value.value
            elif isinstance(kw.value, ast.Name):
                const_name = kw.value.id
                # Case 2: same-file constant
                if const_name in local_constants:
                    prefix_value = local_constants[const_name]
                # Case 3: imported constant
                elif const_name in imports and repo_root is not None:
                    prefix_value = _resolve_imported_string_constant(
                        imports[const_name], repo_root
                    )
            break

        if prefix_value is not None:
            var_name = node.targets[0].id
            prefixes[var_name] = prefix_value

    return prefixes


def _resolve_imported_string_constant(
    import_info: tuple[str, str],
    repo_root: Path,
) -> str | None:
    """Resolve a cross-file imported string constant.

    Given an import like ``from pkg.constants import V2_PREFIX``, finds the
    source file and extracts the value of ``V2_PREFIX = "/v2"``.

    Only resolves simple module-level string literal assignments to keep
    the implementation lightweight and predictable.

    Args:
        import_info: Tuple of (module_path, original_name) from the import.
        repo_root: Repository root for finding source files.

    Returns:
        The string value if found, or None.
    """
    module_path, original_name = import_info
    # Convert dotted module path to file path candidates
    parts = module_path.split(".")
    # Try both direct and src-layout paths
    candidates = [
        repo_root / Path(*parts).with_suffix(".py"),
        repo_root / "src" / Path(*parts).with_suffix(".py"),
    ]
    # Also try as package/__init__.py
    candidates.append(repo_root / Path(*parts) / "__init__.py")
    candidates.append(repo_root / "src" / Path(*parts) / "__init__.py")

    for candidate in candidates:
        if not candidate.is_file():
            continue
        try:
            # utf-8-sig strips leading BOM; Python's own lexer does the same. INV-kitot.
            source = candidate.read_text(encoding="utf-8-sig")
            mod = ast.parse(source)
        except (SyntaxError, UnicodeDecodeError):  # pragma: no cover
            continue
        for node in ast.iter_child_nodes(mod):
            if (
                isinstance(node, ast.Assign)
                and len(node.targets) == 1
                and isinstance(node.targets[0], ast.Name)
                and node.targets[0].id == original_name
                and isinstance(node.value, ast.Constant)
                and isinstance(node.value.value, str)
            ):
                return node.value.value
    return None


def _resolve_string_expr(
    node: ast.expr,
    local_constants: dict[str, str],
    imports: dict[str, tuple[str, str]] | None = None,
    repo_root: Path | None = None,
) -> str | None:
    """Resolve a string expression from an AST node via constant propagation.

    Handles three patterns that commonly appear in route path arguments:

    1. Literal strings: ``"/users"`` → ``"/users"``
    2. Name references: ``BASE_URL`` → value from ``local_constants`` or imports
    3. String concatenation: ``BASE + "/users"`` → recursive resolution

    Recursion is bounded by Python's AST depth (no cycles possible in a
    single expression).  Only resolves module-level string literal
    assignments; dynamic or runtime-computed values return None.

    Args:
        node: The AST expression node to resolve.
        local_constants: Module-level ``NAME = "literal"`` assignments.
        imports: Imported names mapping ``local_name → (module, original)``.
        repo_root: Repository root for cross-file constant resolution.

    Returns:
        The resolved string value, or None if the expression cannot be
        statically resolved.
    """
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.Name):
        name = node.id
        if name in local_constants:
            return local_constants[name]
        if imports and name in imports and repo_root is not None:
            return _resolve_imported_string_constant(imports[name], repo_root)
        return None
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        left = _resolve_string_expr(node.left, local_constants, imports, repo_root)
        right = _resolve_string_expr(node.right, local_constants, imports, repo_root)
        if left is not None and right is not None:
            return left + right
        return None
    return None


def _extract_flask_usage_contexts(
    tree: ast.Module,
    file_path: str,
    symbol_by_name: dict[str, Symbol],
    router_prefixes: dict[str, str] | None = None,
    local_constants: dict[str, str] | None = None,
    imports: dict[str, tuple[str, str]] | None = None,
    repo_root: Path | None = None,
) -> list[UsageContext]:
    """Extract UsageContext records for Flask/FastAPI call-based route registration.

    Creates UsageContext records that capture how view functions are used
    in add_url_rule() and add_api_route() calls. These are matched against
    YAML patterns in the enrichment phase.

    Supported patterns:
    - app.add_url_rule('/users', 'user_list', user_list)
    - app.add_url_rule('/users', view_func=user_list)
    - blueprint.add_url_rule('/items', view_func=get_items, methods=['GET'])
    - router.add_api_route('/path', handler, methods=['GET'])
    - router.add_api_route('/path', handler, response_model=Model)

    When ``router_prefixes`` is provided (from ``_scan_router_prefixes``),
    routes registered on a prefixed APIRouter have the prefix composed with
    the route path.

    When ``local_constants`` and ``imports`` are provided, resolves
    dynamic route paths built from string concatenation or module-level
    constants (e.g., ``add_url_rule(PREFIX + '/users', ...)``).

    Args:
        tree: The parsed AST module
        file_path: Path to the source file
        symbol_by_name: Lookup table for symbols defined in this file
        router_prefixes: Optional mapping of router variable names to their
            APIRouter prefix strings.
        local_constants: Module-level string constant assignments
        imports: Imported names for cross-file constant resolution
        repo_root: Repository root for cross-file constant resolution

    Returns:
        List of UsageContext records for Flask URL patterns.
    """
    contexts: list[UsageContext] = []
    _prefixes = router_prefixes or {}

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue

        # Check if it's a Flask add_url_rule call (app.add_url_rule, bp.add_url_rule)
        func_name = None
        receiver_name = None
        if isinstance(node.func, ast.Attribute):
            func_name = node.func.attr
            if isinstance(node.func.value, ast.Name):
                receiver_name = node.func.value.id

        if func_name not in FLASK_URL_FUNCTIONS:
            continue

        # Flask-RESTful add_resource: first arg is class, second+ is path(s)
        # add_resource(TodoList, '/todos', '/todos/')
        if func_name == "add_resource":
            if len(node.args) < 2:
                continue
            # First arg is the resource class
            resource_arg = node.args[0]
            resource_name = None
            if isinstance(resource_arg, ast.Name):
                resource_name = resource_arg.id
            elif isinstance(resource_arg, ast.Attribute):
                resource_name = resource_arg.attr
            if resource_name is None:
                continue
            resource_ref = None
            if resource_name in symbol_by_name:
                resource_ref = symbol_by_name[resource_name].id
            # Second arg onwards are URL paths
            for path_arg in node.args[1:]:
                if isinstance(path_arg, ast.Constant) and isinstance(path_arg.value, str):
                    rpath = path_arg.value
                    normalized = rpath if rpath.startswith("/") else f"/{rpath}"
                    if receiver_name and receiver_name in _prefixes:
                        prefix = _prefixes[receiver_name].rstrip("/")
                        normalized = prefix + normalized
                    span = Span(
                        start_line=node.lineno,
                        end_line=getattr(node, "end_lineno", node.lineno),
                        start_col=getattr(node, "col_offset", 0),
                        end_col=getattr(node, "end_col_offset", 0),
                    )
                    call_name = f"{receiver_name}.{func_name}" if receiver_name else func_name
                    ctx = UsageContext.create(
                        kind="call",
                        context_name=call_name,
                        position="resource_class",
                        path=file_path,
                        span=span,
                        symbol_ref=resource_ref,
                        metadata={
                            "route_path": normalized,
                            "view_name": resource_name,
                            "args": [resource_name, rpath],
                        },
                    )
                    contexts.append(ctx)
            continue

        # Extract the URL pattern from the first argument
        if not node.args:  # pragma: no cover
            continue

        first_arg = node.args[0]
        route_path = _resolve_string_expr(
            first_arg,
            local_constants or {},
            imports,
            repo_root,
        )
        if route_path is None:
            if isinstance(first_arg, ast.JoinedStr):  # pragma: no cover
                continue  # Skip dynamic patterns (f-strings)
            continue  # pragma: no cover - unsupported pattern type

        # Extract view function - can be:
        # 1. Third positional arg: add_url_rule('/path', 'name', view_func)
        # 2. Second positional arg: add_api_route('/path', handler, ...)
        # 3. view_func keyword arg: add_url_rule('/path', view_func=handler)
        view_ref = None
        view_name = None

        # Check for view_func in keyword arguments
        for kw in node.keywords:
            if kw.arg == "view_func":
                if isinstance(kw.value, ast.Name):
                    view_name = kw.value.id
                    if view_name in symbol_by_name:
                        view_ref = symbol_by_name[view_name].id
                elif isinstance(kw.value, ast.Attribute):
                    view_name = kw.value.attr
                break

        # If not found in kwargs, check positional args.
        # add_api_route: second arg is handler ('/path', handler, ...)
        # add_url_rule: third arg is handler ('/path', 'name', handler)
        handler_arg_idx = 1 if func_name == "add_api_route" else 2
        if view_name is None and len(node.args) > handler_arg_idx:
            handler_arg = node.args[handler_arg_idx]
            if isinstance(handler_arg, ast.Name):
                view_name = handler_arg.id
                if view_name in symbol_by_name:
                    view_ref = symbol_by_name[view_name].id
            elif isinstance(handler_arg, ast.Attribute):
                view_name = handler_arg.attr

        # Extract methods if specified
        methods = None
        for kw in node.keywords:
            if kw.arg == "methods":
                if isinstance(kw.value, ast.List):
                    methods = []
                    for elt in kw.value.elts:
                        if isinstance(elt, ast.Constant) and isinstance(elt.value, str):
                            methods.append(elt.value.upper())

        # Build metadata
        args_values = []
        for arg in node.args:
            if isinstance(arg, ast.Constant):
                args_values.append(arg.value)
            elif isinstance(arg, ast.Name):
                args_values.append(arg.id)
            elif isinstance(arg, ast.Attribute):
                parts = []
                current: ast.expr = arg
                while isinstance(current, ast.Attribute):
                    parts.append(current.attr)
                    current = current.value
                if isinstance(current, ast.Name):
                    parts.append(current.id)
                args_values.append(".".join(reversed(parts)))
            else:  # pragma: no cover
                args_values.append("<expr>")

        # Normalize route path and compose with APIRouter prefix if present
        normalized_path = route_path if route_path.startswith("/") else f"/{route_path}"
        if receiver_name and receiver_name in _prefixes:
            prefix = _prefixes[receiver_name].rstrip("/")
            normalized_path = prefix + normalized_path

        span = Span(
            start_line=node.lineno,
            end_line=getattr(node, "end_lineno", node.lineno),
            start_col=getattr(node, "col_offset", 0),
            end_col=getattr(node, "end_col_offset", 0),
        )

        # Build full call name (e.g., "app.add_url_rule")
        call_name = f"{receiver_name}.{func_name}" if receiver_name else func_name

        # WI-kohav: spec §9 usage_contexts[].metadata carries a single
        # http_method STRING (matching every other language's route extractor);
        # emit one UsageContext per declared method instead of a methods list.
        for _method in (methods or ["GET"]):
            ctx = UsageContext.create(
                kind="call",
                context_name=call_name,
                position="view_func",
                path=file_path,
                span=span,
                symbol_ref=view_ref,
                metadata={
                    "args": args_values,
                    "route_path": normalized_path,
                    "view_name": view_name,
                    "http_method": _method,
                    "receiver": receiver_name,
                },
            )
            contexts.append(ctx)

    return contexts


def _extract_starlette_usage_contexts(
    tree: ast.Module,
    file_path: str,
    symbol_by_name: dict[str, Symbol],
    imports: dict[str, tuple[str, str]] | None = None,
) -> list[UsageContext]:
    """Extract UsageContext records for Starlette ``Route`` / ``WebSocketRoute``.

    Starlette routes are constructor calls — ``Route("/path", handler, methods=[...])``
    and ``WebSocketRoute("/ws", handler)`` — typically passed as a list to a
    ``Starlette(routes=[...])`` constructor or to ``Mount(...)``. We treat both
    classes as route-registration points.

    The match is **import-scoped**: we only emit a UsageContext when the bare
    name (``Route`` / ``WebSocketRoute``) was imported from
    ``starlette.routing`` in this file. ``Route`` is a common class name and
    a global match would cause false positives.

    Args:
        tree: Parsed module AST.
        file_path: Path to the source file.
        symbol_by_name: Lookup table for symbols defined in this file.
        imports: ``{local_name: (module_path, original_name)}`` from
            ``_collect_module_constants``. When None, no contexts are emitted.

    Returns:
        UsageContext records with ``position="view_func"`` and metadata
        ``route_path`` / ``methods`` / ``view_name`` / ``args`` / ``receiver``
        (the imported class name, e.g., ``"Route"`` or ``"WebSocketRoute"``).
    """
    contexts: list[UsageContext] = []
    if not imports:
        return contexts

    # Build the set of locally-bound names that resolve to the Starlette
    # routing classes. Honors aliasing (``from starlette.routing import Route as R``).
    starlette_names: dict[str, str] = {}  # local_name → original_class_name
    for local_name, (module_path, original_name) in imports.items():
        if module_path != _STARLETTE_ROUTING_MODULE:
            continue
        if original_name not in STARLETTE_ROUTE_FUNCTIONS:
            continue
        starlette_names[local_name] = original_name

    if not starlette_names:
        return contexts

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if not isinstance(node.func, ast.Name):
            continue
        local_name = node.func.id
        if local_name not in starlette_names:
            continue
        original_name = starlette_names[local_name]

        if not node.args:  # pragma: no cover - constructor with zero args is invalid
            continue

        # First arg: route path (string literal).
        first_arg = node.args[0]
        if not (isinstance(first_arg, ast.Constant) and isinstance(first_arg.value, str)):
            # Skip dynamic patterns; static analysis can't recover the path.
            continue
        route_path = first_arg.value
        normalized_path = route_path if route_path.startswith("/") else f"/{route_path}"

        # Second arg: handler function.
        view_ref = None
        view_name = None
        if len(node.args) >= 2:
            handler_arg = node.args[1]
            if isinstance(handler_arg, ast.Name):
                view_name = handler_arg.id
                if view_name in symbol_by_name:
                    view_ref = symbol_by_name[view_name].id
            elif isinstance(handler_arg, ast.Attribute):
                view_name = handler_arg.attr

        # Methods: kwarg for Route; synthetic ["WS"] for WebSocketRoute.
        methods: list[str] | None = None
        if original_name == "WebSocketRoute":
            methods = ["WS"]
        else:
            for kw in node.keywords:
                if kw.arg == "methods" and isinstance(kw.value, ast.List):
                    extracted: list[str] = []
                    for elt in kw.value.elts:
                        if isinstance(elt, ast.Constant) and isinstance(elt.value, str):
                            extracted.append(elt.value.upper())
                    if extracted:
                        methods = extracted

        # Build args metadata mirroring Flask's shape.
        args_values: list[str | int | float | bool | None] = []
        for arg in node.args:
            if isinstance(arg, ast.Constant):
                args_values.append(arg.value)
            elif isinstance(arg, ast.Name):
                args_values.append(arg.id)
            elif isinstance(arg, ast.Attribute):
                parts: list[str] = []
                current: ast.expr = arg
                while isinstance(current, ast.Attribute):
                    parts.append(current.attr)
                    current = current.value
                if isinstance(current, ast.Name):
                    parts.append(current.id)
                args_values.append(".".join(reversed(parts)))
            else:  # pragma: no cover - other expr forms (e.g. lambdas)
                args_values.append("<expr>")

        span = Span(
            start_line=node.lineno,
            end_line=getattr(node, "end_lineno", node.lineno),
            start_col=getattr(node, "col_offset", 0),
            end_col=getattr(node, "end_col_offset", 0),
        )
        # WI-kohav: one UsageContext per method with an http_method STRING
        # (spec §9; matches every other language's route extractor).
        for _method in (methods or ["GET"]):
            ctx = UsageContext.create(
                kind="call",
                context_name=original_name,
                position="view_func",
                path=file_path,
                span=span,
                symbol_ref=view_ref,
                metadata={
                    "args": args_values,
                    "route_path": normalized_path,
                    "view_name": view_name,
                    "http_method": _method,
                    "receiver": original_name,
                },
            )
            contexts.append(ctx)

    return contexts


def _extract_py_decorator_names(
    node: ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef,
) -> str:
    """Extract sorted, comma-joined decorator names from an AST node.

    Walks the decorator list and extracts plain names (stripping module
    paths and arguments).  Returns a sorted, comma-joined string suitable
    for inclusion in stable_id formulas.  Returns empty string when no
    decorators are present.
    """
    names: list[str] = []
    for dec in node.decorator_list:
        if isinstance(dec, ast.Name):
            names.append(dec.id)
        elif isinstance(dec, ast.Attribute):
            names.append(dec.attr)
        elif isinstance(dec, ast.Call):
            if isinstance(dec.func, ast.Name):
                names.append(dec.func.id)
            elif isinstance(dec.func, ast.Attribute):
                names.append(dec.func.attr)
    return ",".join(sorted(names))


def _compute_stable_id(
    node: ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef,
    containing_stable_id: str = "",
    *,
    name: str = "",
    qualified_name: str = "",
    occurrence_index: int = 0,
) -> str:
    """Compute a v6 stable_id for a Python function/class/method AST node.

    Delegates to the shared :func:`assemble_stable_id` (ADR-0035 §1), so the Python AST path
    and the tree-sitter ``BaseAnalyzer.compute_stable_id`` path emit the identical formula::

        sha256({kind}:{param_count}:{arity_flags}:{decorators}
               :{containing_stable_id}:{name}:{qualified_name}:{occurrence_index})

    The v5 divergence — this producer folded a class ``body_sig`` (sorted member names) and
    omitted ``qualified_name`` — is gone (WI-gitun / INV-tazaj):

    * ``body_sig`` is DROPPED. It churned the class id on every member add/remove (violating
      §1 "survives body edits"); structural identity is ``shape_id``'s job. With the full scope
      chain in ``qualified_name`` it disambiguated nothing on the measured corpus.
    * ``qualified_name`` carries the FULL enclosing scope chain (enclosing classes → enclosing
      functions → local name), so same-local-name symbols in distinct scopes hash distinctly.

    ``name`` is the bare local name; callers pass the scope-qualified chain as
    ``qualified_name`` (see ``_enclosing_scope_chain``). ``occurrence_index`` is the §1
    within-scope ordinal (``0`` in the carrier).
    """
    is_function = isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    kind = "function" if is_function else "class"

    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        # isinstance (not the is_function bool) so mypy narrows node to the
        # function types that actually carry .args (ClassDef does not).
        args = node.args
        param_count = len(args.args) + len(args.posonlyargs) + len(args.kwonlyargs)
        has_defaults = len(args.defaults) > 0 or len(args.kw_defaults) > 0
        has_varargs = args.vararg is not None
        has_kwargs = args.kwarg is not None
        arity_flags = f"{has_defaults},{has_varargs},{has_kwargs}"
    else:
        # Classes don't carry parameters in the same way.
        param_count = 0
        arity_flags = "False,False,False"

    decorators_str = _extract_py_decorator_names(node)
    return assemble_stable_id(
        kind,
        param_count,
        arity_flags,
        decorators_str,
        containing_stable_id,
        name,
        qualified_name,
        occurrence_index,
    )


def _ast_structure(node: ast.AST) -> str:
    """Generate structural representation of an AST node, ignoring names/literals."""
    parts = [type(node).__name__]

    for child in ast.iter_child_nodes(node):
        # Skip name nodes and constants (we want structure only)
        if isinstance(child, (ast.Name, ast.Constant, ast.arg)):
            parts.append(type(child).__name__)
        else:
            parts.append(_ast_structure(child))

    return f"({','.join(parts)})"


def _compute_shape_id(
    node: ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef,
    kind: str,
) -> str:
    """Compute shape_id based on AST structure (ignores variable names/literals).

    ``sha256(kind:NodeType(ast_structure))`` where the body structure is a
    normalized representation of the control flow and nesting.

    The symbol ``kind`` (``class`` / ``method`` / ``function``) and the concrete
    AST node type are folded into the hashed prefix so that structurally-trivial
    symbols of *different* kinds do not collide (WI-linon). Two defects made this
    necessary: (1) a module-level function and a class method are both
    ``ast.FunctionDef`` with ``self`` absent from the body, so they hashed
    identically; (2) ``ast.AsyncFunctionDef`` is not a subclass of
    ``ast.FunctionDef``, so an async def previously mis-branched into the class
    path and a docstring-only ``async def`` collided with a docstring-only
    ``class``. Using ``type(node).__name__`` in the prefix also discriminates
    sync from async defs of the same kind. Same-kind, same-structure symbols
    still share a shape_id — the one non-redundant capability shape_id adds over
    ``fingerprint`` (clustering structural clones, spec §337/§342).
    """
    body_parts = [_ast_structure(stmt) for stmt in node.body]
    structure = f"{kind}:{type(node).__name__}({','.join(body_parts)})"
    hash_val = hashlib.sha256(structure.encode()).hexdigest()[:16]
    return f"sha256:{hash_val}"


def _compute_value_shape_id(node: ast.AST, kind: str) -> str:
    """Compute shape_id for a body-less value symbol — variable or field (WI-luzut).

    ``_compute_shape_id`` hashes a node's ``.body`` (control-flow skeleton), but
    a module-level assignment or a class attribute has no body; it previously
    fell through to ``shape_id=None``. Its structural shape is the whole
    assignment statement's AST skeleton with identifiers and literals stripped
    (exactly what :func:`_ast_structure` produces), so ``X = 5``
    (``…Constant``), ``X = foo(a)`` (``…Call``), and ``X: int = compute()``
    (``AnnAssign``) get distinct shape_ids. The symbol ``kind``
    (``variable`` / ``field``) is folded into the hashed prefix on the same
    WI-linon discipline as callables, so a module variable and a class field
    with an identical assignment shape do not collide. Two same-shape
    assignments still share a shape_id — the structural-clone signal
    ``shape_id`` exists to provide (WI-vogij; spec §337/§342).
    """
    structure = f"{kind}:{_ast_structure(node)}"
    hash_val = hashlib.sha256(structure.encode()).hexdigest()[:16]
    return f"sha256:{hash_val}"


PASS_ID = make_pass_id("python")


def _compute_cyclomatic_complexity(node: ast.AST) -> int:
    """Compute McCabe cyclomatic complexity for a function or class.

    Cyclomatic complexity = number of decision points + 1.

    Decision points counted:
    - if (each elif counts separately)
    - for loops
    - while loops
    - except handlers
    - boolean operators (and, or)
    - conditional expressions (ternary)
    - match/case statements (Python 3.10+)
    - comprehensions with if clauses

    NOT counted: `with` / `async with` (WI-gapir). A context manager is not a
    branch — it introduces no alternative path through the function, so counting
    it inflates the score above the McCabe definition this docstring, the spec
    and the schema all name, and above what the reference implementations
    (flake8's `mccabe`, radon) report. It had been counted, and on the
    self-corpus that inflated 7.9% of Python functions.

    Returns 1 for straight-line code (no branches).
    """
    complexity = 1  # Base complexity

    for child in ast.walk(node):
        # Conditional statements
        if isinstance(child, ast.If):
            complexity += 1
        # Loops
        elif isinstance(child, (ast.For, ast.While, ast.AsyncFor)):
            complexity += 1
        # Exception handlers (each except clause adds a branch)
        elif isinstance(child, ast.ExceptHandler):
            complexity += 1
        # Boolean operators in conditions
        elif isinstance(child, ast.BoolOp):
            # and/or each add (n-1) where n is number of operands
            complexity += len(child.values) - 1
        # Conditional expressions (ternary: x if cond else y)
        elif isinstance(child, ast.IfExp):
            complexity += 1
        # Comprehensions with if clauses
        elif isinstance(child, ast.comprehension):
            complexity += len(child.ifs)
        # Match/case (Python 3.10+)
        elif isinstance(child, ast.Match):
            # Each case is a branch
            complexity += len(child.cases)

    return complexity


def _compute_line_span(node: ast.AST) -> int:
    """Compute lines of code for a function or class.

    Returns end_line - start_line + 1.
    """
    # Callers pass function/class def nodes; ast.stmt carries lineno/end_lineno
    # (the ast.AST base does not). Narrow without tightening the signature,
    # which would cascade to the ast.AST-typed call sites.
    assert isinstance(node, ast.stmt)
    start = node.lineno
    end = getattr(node, "end_lineno", node.lineno)
    return end - start + 1


@dataclass
class FileAnalysis:
    """Intermediate analysis result for a single file.

    Note on type inference: Variable method calls (e.g., stub.method()) are resolved
    using constructor-only type inference. This tracks types from direct constructor
    calls (stub = Client()) but NOT from function returns (stub = get_client()).
    This covers ~90% of real-world cases with minimal complexity.
    """

    symbols: list[Symbol]
    symbol_by_name: dict[str, Symbol]
    # Maps imported name -> (module_name, original_name)
    imports: dict[str, tuple[str, str]] = field(default_factory=dict)
    # Maps local alias -> module_name for 'import X' and 'import X as Y'
    module_imports: dict[str, str] = field(default_factory=dict)
    # The parsed AST tree (kept to avoid re-parsing)
    tree: ast.AST | None = None
    # Usage contexts for call-based patterns (Django URL patterns, etc.)
    usage_contexts: list[UsageContext] = field(default_factory=list)
    # Original source text (for library_patterns regex scanning during
    # dataflow annotation — see annotate_dataflow_ast).
    source: str = ""
    # INV-mofav: per-enclosing-function inner scope. Maps the enclosing
    # function Symbol's id to {short_name -> nested Symbol}. Lets call
    # resolution see bare-name calls to inner helpers without polluting
    # the flat symbol_by_name dict.
    nested_by_parent_id: dict[str, dict[str, "Symbol"]] = field(default_factory=dict)
    # INV-mofav: maps AST FunctionDef/AsyncFunctionDef node id -> Symbol.
    # Used by edge extraction to resolve the caller Symbol for nested
    # functions (which aren't registered in the flat symbol_by_name dict).
    # AST node ids are stable within a single process; this field is only
    # consumed in the same process that produced the tree.
    func_symbol_by_node_id: dict[int, "Symbol"] = field(default_factory=dict)
    # identity:F1/F4a: maps every function/method Symbol.id to its NEAREST
    # enclosing FUNCTION Symbol.id (ClassDef ancestors are passed through).
    # Materializes the lexical scope chain for _build_scope_stack; unlike
    # nested_by_parent_id it records methods AS CHILDREN (a method's enclosing
    # function is a real scope) though never as a nested-scope VALUE.
    enclosing_func_id: dict[str, str] = field(default_factory=dict)
    # identity:F1/F4a: maps a function/method Symbol.id to the set of names it
    # binds locally (params/assignments/imports/global, minus nonlocal) — the
    # LEGB "L" shadow set consulted by ScopeStack.lookup_enclosing.
    local_names_by_func_id: dict[str, frozenset[str]] = field(default_factory=dict)
    # WI-supat (D3): AUTHORITATIVE method Symbol.id -> enclosing class Symbol.id.
    # Built where both symbols are lexically in hand, so it is immune to the
    # bare-name last-write-wins clobber a symbol_by_name lookup would suffer on
    # same-short-name / nested classes. Lets the Site-1 / Site-3 producers stamp a
    # concrete, CORRECT enclosing_class_id (which the inherited_calls linker uses
    # to resolve a namesake collision precisely instead of biasing to unresolved).
    method_to_enclosing_class_id: dict[str, str] = field(default_factory=dict)


def _detect_source_roots(repo_root: Path) -> list[Path]:
    """Detect every src/ layout source root inside ``repo_root``.

    A *source root* is a directory named ``src`` that:
    1. is not itself a Python package (no ``__init__.py`` in it), and
    2. contains at least one Python package (a child dir with ``__init__.py``).

    Supports both the traditional single-root layout (``repo/src/<pkg>/``)
    and monorepo layouts where each package owns its own src dir
    (``repo/packages/<pkg>/src/<mod>/``, ``repo/libs/<lib>/src/<mod>/``, …).
    Without this multi-root detection a file under
    ``packages/hypergumbo-core/src/hypergumbo_core/taxonomy.py`` would be
    derived as the path-shaped module qualifier
    ``packages.hypergumbo-core.src.hypergumbo_core.taxonomy`` — invalid
    Python (hyphen) and not the real importable name (WI-davan E1).

    Implementation: iterative directory walk. Skips DEFAULT_EXCLUDES
    directories and dot-prefixed dirs to avoid `.git` / `node_modules` /
    build outputs. When a ``src`` directory satisfies both conditions, it
    is collected and not descended into; nested ``src`` directories
    deeper inside another source root are not searched (they would be
    inside the package, not separate roots).

    Returns a list sorted by path (deterministic for tests and consumers).
    """
    from hypergumbo_core.discovery import DEFAULT_EXCLUDES

    skip = set(DEFAULT_EXCLUDES)
    roots: list[Path] = []
    stack: list[Path] = [repo_root]
    while stack:
        cur = stack.pop()
        try:
            entries = list(cur.iterdir())
        except (PermissionError, OSError):  # pragma: no cover
            continue
        for entry in entries:
            if not entry.is_dir():
                continue
            if entry.name in skip or entry.name.startswith("."):
                continue
            if entry.name == "src":
                # Classify and stop descending — either a source root, a
                # package itself, or a dir whose children aren't packages.
                if (entry / "__init__.py").exists():
                    continue
                try:
                    has_pkg = any(
                        (c / "__init__.py").exists()
                        for c in entry.iterdir()
                        if c.is_dir()
                    )
                except (PermissionError, OSError):  # pragma: no cover
                    has_pkg = False
                if has_pkg:
                    roots.append(entry)
                continue
            stack.append(entry)
    return sorted(roots)


def _module_name_from_path(
    py_file: Path,
    repo_root: Path,
    source_roots: list[Path] | None = None,
) -> str:
    """Convert a file path to a Python module name.

    E.g., ``/repo/utils.py`` -> ``utils``, ``/repo/pkg/mod.py`` -> ``pkg.mod``.

    If ``source_roots`` is provided, paths under any source root are
    computed relative to the *most-specific* (longest-path) matching root.
    """
    roots = source_roots or []
    # Pick the most-specific (deepest) source root that contains the file
    matching = [r for r in roots if py_file.is_relative_to(r)]
    if matching:
        best = max(matching, key=lambda r: len(r.parts))
        try:
            rel_path = py_file.relative_to(best)
        except ValueError:  # pragma: no cover
            rel_path = py_file.relative_to(repo_root)
    else:
        try:
            rel_path = py_file.relative_to(repo_root)
        except ValueError:
            rel_path = py_file
    # Remove .py extension and convert path separators to dots
    return str(rel_path.with_suffix("")).replace("/", ".").replace("\\", ".")


def _resolve_relative_import(
    module: str | None, level: int, importing_module: str
) -> str:
    """Resolve a relative import to an absolute module name.

    Args:
        module: The module part of the import (e.g., 'utils' in 'from ..utils import X')
        level: The number of dots (0 for absolute, 1 for '.', 2 for '..', etc.)
        importing_module: The fully qualified name of the importing module

    Returns:
        The resolved absolute module name.

    Example:
        _resolve_relative_import('utils', 2, 'pkg.sub.main') -> 'pkg.utils'
    """
    if level == 0:
        # Absolute import
        return module or ""

    # Split the importing module into parts
    parts = importing_module.split(".")

    # Go up 'level' levels (level=1 means same package, level=2 means parent, etc.)
    # We go up (level) levels from the module's package (excluding the module name itself)
    # So for 'pkg.sub.main' with level=2, we go up 2 from 'pkg.sub' -> 'pkg'
    if level > len(parts):
        # Can't go up that many levels, return as-is
        return module or ""

    base_parts = parts[:-level] if level <= len(parts) else []
    if module:
        base_parts.append(module)

    return ".".join(base_parts)


def _extract_imports(
    tree: ast.AST, importing_module: str
) -> tuple[dict[str, tuple[str, str]], dict[str, str]]:
    """Extract import mappings from AST with relative import resolution.

    Args:
        tree: The parsed AST
        importing_module: The fully qualified name of the importing module

    Returns a tuple of:
        - symbol_imports: dict mapping local name -> (resolved_module_name, original_name)
          For 'from utils import helper', returns {'helper': ('utils', 'helper')}.
          For 'from ..utils import helper' in 'pkg.sub.main', returns {'helper': ('pkg.utils', 'helper')}.
        - module_imports: dict mapping local alias -> module_name
          For 'import demo_pb2_grpc', returns {'demo_pb2_grpc': 'demo_pb2_grpc'}.
          For 'import numpy as np', returns {'np': 'numpy'}.
    """
    symbol_imports: dict[str, tuple[str, str]] = {}
    module_imports: dict[str, str] = {}

    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            resolved_module = _resolve_relative_import(
                node.module, node.level, importing_module
            )
            if resolved_module:  # Skip if we couldn't resolve
                for alias in node.names:
                    local_name = alias.asname if alias.asname else alias.name
                    symbol_imports[local_name] = (resolved_module, alias.name)

        elif isinstance(node, ast.Import):
            # Handle 'import X' and 'import X as Y'
            for alias in node.names:
                module_name = alias.name
                local_name = alias.asname if alias.asname else alias.name
                module_imports[local_name] = module_name
                # WI-zigah: for `import pkg.subpkg` with no alias, Python binds
                # only the top-level `pkg` name. Call sites see
                # `pkg.subpkg.func(...)` where `pkg` is the AST root — so the
                # full dotted `local_name` is dead data no downstream lookup
                # reaches. Record the top-level binding so the chain walker
                # in _process_call can canonicalize the qualified path.
                if alias.asname is None and "." in module_name:
                    top_level = module_name.split(".", 1)[0]
                    module_imports.setdefault(top_level, top_level)

    return symbol_imports, module_imports


def _extract_import_edges(
    tree: ast.AST,
    file_path: str,
    importing_module: str,
    global_symbols: dict[tuple[str, str], Symbol],
    resolver: "SymbolResolver | None" = None,
    *,
    module_to_file_id: dict[str, str],
    run_id: str,
) -> list[Edge]:
    """Extract import edges from AST.

    Creates edges from the importing file to the imported symbols/modules.
    For 'from X import Y', links to the resolved symbol if known, else to module.
    For 'import X', links to the module.

    supply:F4 (INV-nuzas): when an import names an in-tree MODULE rather than a
    resolvable symbol, the edge dst is the module's first-party file-anchor node
    (looked up in ``module_to_file_id``) instead of a dangling ExternalRef that
    would collapse to a phantom ``external_symbol`` boundary node. Genuine
    third-party modules are absent from the map, so they keep their ExternalRef.

    Args:
        tree: The parsed AST
        file_path: Path to the importing file
        importing_module: The fully qualified name of the importing module
        global_symbols: Map of (module, name) -> Symbol for cross-file resolution
        resolver: Optional SymbolResolver for efficient cross-file lookups
        module_to_file_id: Map of in-tree dotted module name -> file-anchor id
            (package names included for ``__init__.py``); empty when the repo has
            no analyzable in-tree modules.

    Returns list of import edges.
    """
    edges = []
    file_id = _make_file_id(file_path)

    for node in ast.walk(tree):
        # Handle 'from X import Y, Z' style imports
        if isinstance(node, ast.ImportFrom):
            resolved_module = _resolve_relative_import(
                node.module, node.level, importing_module
            )
            if resolved_module:
                for alias in node.names:
                    # Try to find the symbol in our global table (with suffix matching)
                    symbol = _lookup_symbol_by_module(
                        global_symbols, resolved_module, alias.name, resolver=resolver
                    )
                    dst_ref: ExternalRef | None
                    if symbol:
                        dst_id = symbol.id
                        # Internal target — Symbol ID is the canonical id; no ExternalRef.
                        dst_ref = None
                    elif (
                        in_repo_fid := (
                            # supply:F4 — `from PKG import SUBMOD` where SUBMOD is
                            # an in-tree submodule (not a symbol), or `from MOD
                            # import X` where MOD is in-tree but X was not pinned
                            # as a symbol. Resolve to the in-tree file node so the
                            # edge does not dangle to a phantom external twin.
                            module_to_file_id.get(f"{resolved_module}.{alias.name}")
                            or module_to_file_id.get(resolved_module)
                        )
                    ) is not None:
                        dst_id = in_repo_fid
                        dst_ref = None
                    else:
                        # External symbol - create a reference ID
                        dst_id = f"python:{resolved_module}:0-0:{alias.name}:symbol"
                        dst_ref = ExternalRef(
                            lang="python",
                            module_path=resolved_module,
                            name=alias.name,
                        )

                    edges.append(Edge.create(
                        src=file_id,
                        dst=dst_id,
                        edge_type="imports",
                        line=node.lineno,
                        evidence_type="ast_import",
                        dst_ref=dst_ref,
                        origin=PASS_ID,
                        origin_run_id=run_id,
                    ))

        # Handle 'import X' and 'import X as Y' style imports
        elif isinstance(node, ast.Import):
            for alias in node.names:
                module_name = alias.name
                # supply:F4 — `import pkg.sub.mod` of an in-tree module resolves
                # to its first-party file-anchor node; otherwise it stays an
                # external module reference.
                in_repo_fid = module_to_file_id.get(module_name)
                import_dst_ref: ExternalRef | None
                if in_repo_fid is not None:
                    dst_id = in_repo_fid
                    import_dst_ref = None
                else:
                    dst_id = _make_module_id(module_name)
                    import_dst_ref = ExternalRef(
                        lang="python",
                        module_path=module_name,
                        name=module_name,
                    )
                edges.append(Edge.create(
                    src=file_id,
                    dst=dst_id,
                    edge_type="imports",
                    line=node.lineno,
                    evidence_type="ast_import",
                    dst_ref=import_dst_ref,
                    origin=PASS_ID,
                    origin_run_id=run_id,
                ))

    return edges


def _resolve_base_class(
    base_name: str,
    child_sym: Symbol,
    class_by_name: dict[str, list[Symbol]],
    sym_file_imports: dict[str, dict[str, tuple[str, str]]],
) -> Symbol | None:
    """Resolve a base class name to a specific Symbol, disambiguating collisions.

    When multiple classes share the same name (e.g., 238 classes named 'Model'
    in Django), uses a priority cascade:

    1. Same-file match: base class defined in the same file as the child
    2. Import match: child's file imports match a candidate's module path
    3. First by ID: deterministic fallback (sorted by symbol ID)

    Args:
        base_name: The base class name to resolve (e.g., 'Model')
        child_sym: The child class symbol (for file context)
        class_by_name: Multi-value lookup: class name -> list of Symbol candidates
        sym_file_imports: Maps symbol ID -> file-level imports dict
            (imported_name -> (module_name, original_name))

    Returns:
        The resolved base class Symbol, or None if no match found.
    """
    candidates = class_by_name.get(base_name)
    if not candidates:
        return None

    if len(candidates) == 1:
        return candidates[0]

    # Extract file path from child symbol ID for same-file check
    child_path = child_sym.path or ""

    # 1. Same-file match: prefer base class in the same file
    same_file = [c for c in candidates if c.path == child_path]
    if len(same_file) == 1:
        return same_file[0]

    # 2. Import match: check if child's file imports resolve to a candidate
    child_imports = sym_file_imports.get(child_sym.id, {})
    if base_name in child_imports:
        import_module, _original_name = child_imports[base_name]
        # Match import module against candidate file paths
        # e.g., import_module="db.models" matches candidate path "db/models.py"
        module_as_path = import_module.replace(".", "/")
        for cand in candidates:
            cand_path = cand.path or ""
            # Check if candidate path contains the module path
            # e.g., "db/models.py" contains "db/models"
            cand_no_ext = cand_path.rsplit(".py", 1)[0]
            if cand_no_ext.endswith(module_as_path):
                return cand

    # 3. Deterministic fallback: first by symbol ID (sorted for stability)
    candidates_sorted = sorted(candidates, key=lambda c: c.id)
    return candidates_sorted[0]


def _base_module_is_in_tree(
    module_path: str,
    submodule_name: str,
    intree_modules: frozenset[str],
) -> bool:
    """Return True if ``module_path`` (or ``module_path.submodule_name``) names an
    in-tree module.

    Mirrors the import-edge resolver's in-tree test (``_extract_import_edges``),
    but tolerates the module-name-form difference between the two import maps:
    ``analysis.imports`` stores a *repo-root-relative* dotted path for RELATIVE
    imports (``_collect_module_constants`` line ~1035), whereas
    ``module_to_file_id`` keys are *source-root-relative* (``_module_name_from_path``).
    The former is a suffix superset of the latter, so a suffix match catches a
    relative-imported in-tree base whose form otherwise would not equal any key.

    Biases to True on any suffix hit by design: a false positive merely DROPS an
    external ``extends`` edge (a small recall loss), whereas a false negative
    would mint a workspace-prefixed phantom ``external_symbol`` — an INV-nuzas
    regression, the failure mode this guard exists to prevent.
    """
    if module_path in intree_modules:
        return True
    if f"{module_path}.{submodule_name}" in intree_modules:
        return True
    parts = module_path.split(".")
    for i in range(1, len(parts)):
        if ".".join(parts[i:]) in intree_modules:
            return True
    return False


def _extract_inheritance_edges(
    symbols: list[Symbol],
    class_by_name: dict[str, list[Symbol]],
    sym_file_imports: dict[str, dict[str, tuple[str, str]]],
    run: AnalysisRun,
    module_to_file_id: dict[str, str],
) -> list[Edge]:
    """Extract extends edges from class inheritance.

    For each class with base_classes metadata, creates extends edges to
    base classes. First-party bases resolve to their in-repo class node; a base
    that resolves to no first-party class (an external/stdlib base like ``Enum``,
    ``Exception``, ``Protocol``) gets an UNRESOLVED-EXTERNAL fallback edge
    (WI-jubag) rather than being dropped by omission — mirroring the landed JS/TS
    A2 change (WI-dutov). Resolved edges enable the type hierarchy linker to
    create dispatches_to edges for polymorphic dispatch; the external edges make
    the type hierarchy honest ("what is this a subclass of" no longer answers
    "nothing" for the 24.8% of Python classes whose bases are all external).

    When multiple classes share the same name (common in large repos like Django
    where 238 test stubs are named 'Model'), uses import-aware disambiguation
    via ``_resolve_base_class()`` to find the correct target.

    Args:
        symbols: All extracted symbols
        class_by_name: Multi-value lookup: class name -> list of Symbol candidates
        sym_file_imports: Maps symbol ID -> file-level imports dict
            (imported local name -> (module_name, original_name))
        run: Current analysis run for provenance
        module_to_file_id: in-tree dotted module name -> file-anchor id; used to
            guard the external fallback so a not-yet-extracted IN-TREE base is
            dropped rather than minted as a workspace-prefixed phantom (INV-nuzas).

    Returns:
        List of extends edges for inheritance relationships
    """
    edges: list[Edge] = []
    intree_modules = frozenset(module_to_file_id)

    for sym in symbols:
        if sym.kind != "class":
            continue

        base_classes = sym.meta.get("base_classes", []) if sym.meta else []
        if not base_classes:
            continue

        child_imports = sym_file_imports.get(sym.id, {})
        for base_class_name in base_classes:
            # Strip generics from base class name (e.g., "Generic[T]" -> "Generic")
            base_name = base_class_name.split("[")[0]

            # Resolve to the correct base class, handling name collisions
            base_sym = _resolve_base_class(
                base_name, sym, class_by_name, sym_file_imports
            )
            if base_sym is not None and base_sym.id != sym.id:
                edges.append(Edge.create(
                    src=sym.id,
                    dst=base_sym.id,
                    edge_type="extends",
                    line=sym.span.start_line if sym.span else 0,
                    origin=PASS_ID,
                    origin_run_id=run.execution_id,
                    evidence_type="ast_extends",
                ))
                continue
            if base_sym is not None:
                # base_sym.id == sym.id: a syntactically self-referential base
                # (``class Foo(Foo)`` — parseable though a runtime NameError).
                # Skip it so it does not fall through to the external fallback.
                continue

            # WI-jubag: the base resolves to no first-party class. Emit an
            # unresolved-external ``extends`` edge so external/stdlib bases are
            # represented rather than dropped. Confidence stays EVIDENCE-DERIVED
            # (ADR-0039): the extends DETECTION is AST-certain (0.95, same as a
            # resolved extends); ``is_resolved=False`` carries the unresolved
            # TARGET. Dotted/qualified bases (``argparse.RawDescriptionHelpFormatter``)
            # need module_imports to name their module and are deferred to the
            # Approach-C core-linker chokepoint — keep the current drop for them.
            if "." in base_name:
                continue

            imported = child_imports.get(base_name)
            if imported is not None:
                module_path, original_name = imported
                # Aliased import (``from x import Base as B``): re-resolve on the
                # ORIGINAL name so an aliased IN-TREE base binds to its real class
                # instead of being declared external.
                if original_name != base_name:
                    re_sym = _resolve_base_class(
                        original_name, sym, class_by_name, sym_file_imports
                    )
                    if re_sym is not None and re_sym.id != sym.id:
                        edges.append(Edge.create(
                            src=sym.id,
                            dst=re_sym.id,
                            edge_type="extends",
                            line=sym.span.start_line if sym.span else 0,
                            origin=PASS_ID,
                            origin_run_id=run.execution_id,
                            evidence_type="ast_extends",
                        ))
                        continue
                # In-tree guard (INV-nuzas): a base imported from an in-tree module
                # that was simply not extracted as a class (a module-level
                # variable, a failed-parse file) must be DROPPED, not minted as a
                # workspace-prefixed phantom external.
                if _base_module_is_in_tree(
                    module_path, original_name, intree_modules
                ):
                    continue
                module_hint = module_path
                canonical = original_name
                dst_ref: ExternalRef | None = ExternalRef(
                    lang="python", module_path=module_hint, name=canonical
                )
            else:
                # Not imported: a builtin base (Exception, str, ValueError, ...).
                module_hint = "external"
                canonical = base_name
                dst_ref = None

            edges.append(Edge.create(
                src=sym.id,
                dst=f"python:{module_hint}:0-0:{canonical}:unresolved",
                edge_type="extends",
                line=sym.span.start_line if sym.span else 0,
                origin=PASS_ID,
                origin_run_id=run.execution_id,
                evidence_type="ast_extends",
                is_resolved=False,
                dst_ref=dst_ref,
            ))

    return edges


def _extract_file_analysis(
    py_file: Path,
    repo_root: Path | None = None,
    source_roots: list[Path] | None = None,
) -> tuple[FileAnalysis | None, str | None]:
    """Extract symbols and imports from a single file.

    Args:
        py_file: Path to the Python file
        repo_root: Repository root for resolving relative imports. If None,
                   relative imports won't be fully resolved.
        source_roots: For src/ layout projects, the source directories
                     (e.g., ``[repo/src]`` or per-package
                     ``[packages/A/src, packages/B/src]``). Used for correct
                     module name calculation.

    Returns (analysis, None) on success, or (None, reason) when the file
    cannot be parsed — the second tuple element carries the
    "<ExceptionType>: <msg>" reason so the orchestrator can route it into
    limits.failed_files (INV-buhur).
    """
    try:
        # utf-8-sig strips leading BOM; Python's own lexer does the same. INV-kitot.
        source = py_file.read_text(encoding="utf-8-sig")
        # Suppress SyntaxWarning from invalid escape sequences in analyzed code.
        # These warnings come from the target codebase, not hypergumbo.
        with warnings.catch_warnings():
            warnings.filterwarnings("ignore", category=SyntaxWarning)
            tree = ast.parse(source, filename=str(py_file))
    except (SyntaxError, UnicodeDecodeError, OSError) as e:
        # OSError covers PermissionError and transient I/O failures from
        # read_text() — e.g. a chmod-000 or root-owned file encountered
        # while scanning a tree. §17 / WI-madal: fail open — skip the
        # unreadable file and record the reason in limits.failed_files
        # rather than letting the exception escape and abort the whole run.
        # ast.parse() performs no I/O, so broadening here only newly catches
        # read errors, not anything from parsing.
        return None, f"{type(e).__name__}: {e}"

    symbols = []
    symbol_by_name: dict[str, Symbol] = {}

    # INV-zudob: compute the file's stable identity once up-front so
    # top-level ClassDef and untyped-function stable_ids can fold it in
    # as their containing identity. Pre-INV-zudob the top-level call
    # sites passed no containing argument, so module identity was
    # silently erased and structurally-identical classes/functions in
    # different files collapsed to one stable_id (18.94% of class nodes
    # on self-analysis). The repo-relative path matches the convention
    # used by ``make_file_stable_id`` elsewhere — see also the
    # ``file_name`` computation for the module Symbol below.
    file_relative_path = str(py_file)
    if repo_root is not None:
        try:
            file_relative_path = str(py_file.relative_to(repo_root))
        except ValueError:  # pragma: no cover - defensive
            pass
    file_containing_id = make_file_stable_id("python", file_relative_path)

    # WI-gipag: extract the module-level __all__ (if any) once up front
    # so each top-level Symbol extraction can decide is_exported without
    # re-walking the tree. None means "no __all__ found" → fall back to
    # the leading-underscore rule.
    module_all = _extract_module_all(tree)

    # INV-hojus: emit the file pseudo-node as kind="file" with the
    # canonical file-id shape so the orchestrator file-symbol synthesizer
    # dedups against it (existing_ids check). Before this fix, every
    # Python file with module-level code got TWO Symbols: a kind="module"
    # node from here AND a kind="file" node from the synthesizer when any
    # edge targeted the file id — 332 paths affected on self-analysis.
    # File-kind is the cross-language canonical for "this file" (see
    # ``analyze.base.make_file_id``); the synthesizer's INV-vaguj fix
    # already established its identity claims (relative path, real
    # end_line). This Symbol provides an enclosing scope for module-level
    # edges so script-only files remain reachable in slice traversal.
    # WI-kazob: the file-kind node carries the module's one-line docstring
    # summary (0/902 file nodes carried one before). py.py is the only
    # producer that can read a Python module docstring — the orchestrator
    # file-symbol synthesizer is language-agnostic — so the file node is
    # emitted whenever the module has executable code OR a docstring. Per
    # the INV-hojus dedup above, broadening the condition only changes WHICH
    # producer emits the single file node (py.py vs the synthesizer); it
    # never doubles it.
    _module_docstring = ast.get_docstring(tree)
    _module_docstring_line = (
        _module_docstring.split("\n")[0].strip()[:80] if _module_docstring else None
    )
    if _has_module_level_code(tree) or _module_docstring:
        end_line = _get_file_end_line(source)
        module_span = Span(
            start_line=1,
            end_line=end_line,
            start_col=0,
            end_col=0,
        )

        # Detect structural entry point: if __name__ == "__main__"
        # This concept enables entrypoint detection for executable Python scripts
        # main_guard indicates "if __name__ == '__main__':" pattern
        module_meta: dict[str, object] | None = None
        if _has_main_guard(tree):
            module_meta = {"concepts": [{"concept": "main_guard", "framework": "python"}]}

        # Match the orchestrator synthesizer's convention: name=path (the
        # repo-relative path when possible). The orchestrator's
        # cli-level path normalize pass strips repo_root from ``.path``
        # but not ``.name``, so the analyzer is responsible for emitting
        # ``name`` already normalized (mirror of INV-vaguj for the
        # analyzer-side producer). Reuses ``file_relative_path`` computed
        # earlier for INV-zudob.
        file_name = file_relative_path

        module_symbol = Symbol(
            id=_make_file_id(str(py_file)),
            name=file_name,
            kind="file",
            language="python",
            path=str(py_file),
            span=module_span,
            origin="",
            origin_run_id="",
            docstring=_module_docstring_line,
            meta=module_meta,
        )
        symbols.append(module_symbol)
        symbol_by_name["<module>"] = module_symbol

    # WI-gafog E2: emit Symbols for module-level NAME = ... so that
    # `from <mod> import NAME` resolves cross-file rather than externalising.
    for cs in _emit_module_level_assign_symbols(tree, py_file, module_all):
        symbols.append(cs)
        symbol_by_name[cs.name] = cs

    # Track functions already processed as methods (to avoid duplicates)
    # Key: (start_line, name) tuple
    processed_functions: set[tuple[int, str]] = set()

    # INV-mofav: build a parent map so each FunctionDef can find its
    # immediate enclosing FunctionDef (if any), and emit a qualified
    # name like `outer.inner` or `outermost.middle.inner`.
    parent_map: dict[int, ast.AST] = {}
    for _p in ast.walk(tree):
        for _c in ast.iter_child_nodes(_p):
            parent_map[id(_c)] = _p
    func_symbol_by_node_id: dict[int, Symbol] = {}
    # WI-supat (D3): authoritative method Symbol.id -> enclosing class Symbol.id,
    # populated at method creation where both symbols are lexically in hand.
    method_to_enclosing_class_id: dict[str, str] = {}

    def _enclosing_function_chain(node: ast.AST) -> list[str]:
        """Return the names of enclosing FunctionDef ancestors, outermost-first.

        Stops at the first non-function parent boundary in either direction:
        class bodies and module level don't extend the chain. Used for
        qualified naming of nested functions per INV-mofav. Class methods
        keep their existing `ClassName.method` naming (computed elsewhere).
        """
        chain: list[str] = []
        current = parent_map.get(id(node))
        while current is not None:
            if isinstance(current, (ast.FunctionDef, ast.AsyncFunctionDef)):
                chain.append(current.name)
            current = parent_map.get(id(current))
        chain.reverse()  # outermost first
        return chain

    def _enclosing_scope_chain(node: ast.AST) -> list[str]:
        """Return ALL enclosing Class/Function ancestor names, outermost-first.

        The stable_id v6 scope chain (ADR-0035 §1): unlike
        ``_enclosing_function_chain`` (functions only — used for nested-function display
        naming, INV-mofav), this also folds enclosing CLASSES, so a class/function defined
        inside a *method* of two different classes (``A.t.Mock`` vs ``B.t.Mock``) gets distinct
        ids. Function-only chains collapse those (they see only ``t``) — WI-gitun's residual.
        """
        chain: list[str] = []
        current = parent_map.get(id(node))
        while current is not None:
            if isinstance(
                current, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)
            ):
                chain.append(current.name)
            current = parent_map.get(id(current))
        chain.reverse()  # outermost first
        return chain

    # Scan for APIRouter prefix assignments (for route path composition)
    router_prefixes = _scan_router_prefixes(tree, repo_root, py_file)

    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef):
            class_name = node.name
            end_line = node.end_lineno or node.lineno
            end_col = node.end_col_offset or 0
            span = Span(
                start_line=node.lineno,
                end_line=end_line,
                start_col=node.col_offset,
                end_col=end_col,
            )

            # Build rich metadata for class (ADR-3aaa)
            class_meta: dict[str, object] = {}

            # Extract decorators with arguments
            if node.decorator_list:
                class_meta["decorators"] = [
                    _extract_decorator_info(dec) for dec in node.decorator_list
                ]

            # Extract base classes
            if node.bases:
                class_meta["base_classes"] = [
                    _format_annotation(base) for base in node.bases
                ]

            _ds = ast.get_docstring(node)
            _ds_line = _ds.split("\n")[0].strip()[:80] if _ds else None
            # WI-gipag: decide is_exported for top-level class symbols via
            # the module's __all__ (if present) or the leading-underscore
            # convention. Only top-level classes are candidates for public
            # API here — nested classes defined inside functions are not
            # externally reachable regardless of naming.
            class_is_exported = (
                node.col_offset == 0
                and _is_python_top_level_exported(node.name, module_all)
            )
            # stable_id v6 (ADR-0035 §1, WI-gitun): fold the FULL enclosing scope chain
            # (enclosing classes + functions) into the IDENTITY only, so two same-named classes
            # in distinct scopes (e.g. function-local ``class Args`` in distinct functions, or a
            # ``class Mock`` inside methods of distinct classes) no longer collapse. The
            # ``qualified_name`` FIELD is left as the bare name — v6 is a stable_id-only change;
            # the field's scope-qualification (and its call-resolution effects) is separate work.
            class_scoped_name = ".".join(_enclosing_scope_chain(node) + [node.name])
            symbol = Symbol(
                id=_make_symbol_id(str(py_file), node.lineno, end_line, node.name, "class"),
                name=node.name,
                qualified_name=node.name,
                kind="class",
                language="python",
                path=str(py_file),
                span=span,
                stable_id=_compute_stable_id(
                    node, containing_stable_id=file_containing_id,
                    name=node.name, qualified_name=class_scoped_name,
                ),
                shape_id=_compute_shape_id(node, "class"),
                cyclomatic_complexity=_compute_cyclomatic_complexity(node),
                line_span=_compute_line_span(node),
                meta=class_meta if class_meta else None,
                docstring=_ds_line,
                modifiers=_python_visibility_modifiers(node.name),
                is_exported=class_is_exported,
            )
            symbols.append(symbol)
            symbol_by_name[node.name] = symbol

            # WI-jusus (emission-parity F5): emit kind="field" Symbols for CLASS
            # ATTRIBUTES — class-body Assign / AnnAssign with Name targets (incl.
            # dataclass fields `x: int` and bare annotations). Instance
            # attributes (`self.x = ...` inside methods) are NOT class-body
            # statements and are out of scope. Identity is class-scoped via the
            # class's file-anchored stable_id (the assemble_stable_id container
            # slot), so same-named fields in different classes/files are distinct.
            for member in node.body:
                attr_names: list[str] = []
                attr_annotation: "ast.expr | None" = None
                if isinstance(member, ast.Assign):
                    for t in member.targets:
                        if isinstance(t, ast.Name):
                            attr_names.append(t.id)
                elif isinstance(member, ast.AnnAssign) and isinstance(member.target, ast.Name):
                    attr_names.append(member.target.id)
                    attr_annotation = member.annotation
                else:
                    continue
                attr_start = member.lineno
                attr_end = member.end_lineno or attr_start
                attr_sig = (
                    _format_annotation(attr_annotation)
                    if attr_annotation is not None else None
                )
                for attr in attr_names:
                    attr_full = f"{class_name}.{attr}"
                    attr_qualified = f"{class_scoped_name}.{attr}"
                    field_sym = Symbol(
                        id=_make_symbol_id(str(py_file), attr_start, attr_end, attr_full, "field"),
                        name=attr_full,
                        kind="field",
                        language="python",
                        path=str(py_file),
                        span=Span(
                            start_line=attr_start,
                            end_line=attr_end,
                            start_col=member.col_offset,
                            end_col=member.end_col_offset or 0,
                        ),
                        origin="",
                        origin_run_id="",
                        shape_id=_compute_value_shape_id(member, "field"),
                        stable_id=assemble_stable_id(
                            "field", 0, "", "",
                            symbol.stable_id or "", attr, attr_qualified, 0,
                        ),
                        signature=attr_sig,
                        modifiers=_python_visibility_modifiers(attr),
                        is_exported=not attr.startswith("_"),
                        qualified_name=attr_qualified,
                    )
                    symbols.append(field_sym)
                    symbol_by_name[attr_full] = field_sym

            # Extract methods inside the class
            for item in node.body:
                if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    method_end_line = item.end_lineno or item.lineno
                    method_end_col = item.end_col_offset or 0
                    method_span = Span(
                        start_line=item.lineno,
                        end_line=method_end_line,
                        start_col=item.col_offset,
                        end_col=method_end_col,
                    )
                    method_name = f"{class_name}.{item.name}"

                    # v8 (WI-bolup): HTTP-verb-named methods of class-based views
                    # (get/post/...) flow through the SAME file-anchored identity
                    # path as every other method — make_typed_stable_id /
                    # _compute_stable_id with containing_stable_id=symbol.stable_id
                    # (the file-anchored parent class). Pre-v8 they were keyed via
                    # the LOGICAL make_route_stable_id(verb, class_scoped_name),
                    # which omits the file, so two same-named top-level CBVs' same
                    # verb method collided cross-file (Wave-2 gate, INV-tazaj). The
                    # route/endpoint signal lives on the SEPARATE kind="route" nodes
                    # (below), never on the method's own identity.
                    # INV-bazij (Phase 6 PR3): the method's qualified name threads
                    # through name/qualified_name so two same-signature test methods
                    # in one class don't collide.
                    sig = _format_function_signature(item)
                    norm_sig = normalize_python_signature(sig)
                    modifiers = _python_visibility_modifiers(method_name)
                    if norm_sig:
                        stable_id = make_typed_stable_id(
                            "method", norm_sig,
                            visibility_from_modifiers(modifiers),
                            symbol.stable_id,
                            _extract_py_decorator_names(item),
                            name=item.name, qualified_name=method_name,
                        )
                    else:
                        stable_id = _compute_stable_id(
                            item, containing_stable_id=symbol.stable_id,
                            name=item.name, qualified_name=method_name,
                        )

                    # Build rich metadata for method (ADR-3aaa)
                    method_meta: dict[str, object] = {}

                    # Extract decorators with arguments
                    if item.decorator_list:
                        method_decorators = [
                            _extract_decorator_info(dec) for dec in item.decorator_list
                        ]
                        method_meta["decorators"] = method_decorators
                        # Check if any decorator references a prefixed APIRouter
                        if router_prefixes:
                            # iterate the typed local, not the object-typed
                            # method_meta["decorators"] lookup
                            for dec_info in method_decorators:
                                name_val = dec_info.get("name", "")
                                dec_name = name_val if isinstance(name_val, str) else ""
                                dot_idx = dec_name.find(".")
                                if dot_idx > 0:
                                    receiver = dec_name[:dot_idx]
                                    if receiver in router_prefixes:
                                        method_meta["router_prefix"] = router_prefixes[receiver]
                                        break

                    # Extract structured parameters (excluding self/cls)
                    params = _extract_parameters_info(item.args, exclude_self=True)
                    if params:
                        method_meta["parameters"] = params

                    _mds = ast.get_docstring(item)
                    _mds_line = _mds.split("\n")[0].strip()[:80] if _mds else None
                    method_symbol = Symbol(
                        id=_make_symbol_id(str(py_file), item.lineno, method_end_line, method_name, "method"),
                        name=method_name,
                        qualified_name=method_name,  # WI-fagab (ADR-0032 sibling field)
                        kind="method",
                        language="python",
                        path=str(py_file),
                        span=method_span,
                        stable_id=stable_id,
                        shape_id=_compute_shape_id(item, "method"),
                        cyclomatic_complexity=_compute_cyclomatic_complexity(item),
                        line_span=_compute_line_span(item),
                        signature=_format_function_signature(
                            item, max_len=_DISPLAY_SIGNATURE_MAX_LEN, render_defaults=True
                        ),
                        docstring=_mds_line,
                        meta=method_meta if method_meta else None,
                        modifiers=_python_visibility_modifiers(method_name),
                    )
                    symbols.append(method_symbol)
                    # Store by short name for self.method() lookups
                    symbol_by_name[item.name] = method_symbol
                    # WI-jafat CHANGE A: also register the method under its AST
                    # node id (collision-immune, mirroring the FunctionDef path
                    # below). Without this, caller resolution in _extract_edges
                    # (the `func_symbol_by_node_id.get(id(node))` lookup) misses
                    # for methods and falls through to the bare-name,
                    # last-write-wins symbol_by_name dict, so same-short-name
                    # sibling methods (to_dict, __init__, ...) own each other's
                    # calls and the overwritten sibling's calls land out-of-span
                    # (506 calls / 1194 combined edges on self-analysis). Keying
                    # on id(item) lets each method own its own call lines; the
                    # bare write above is retained for self.method() (Case 2a).
                    func_symbol_by_node_id[id(item)] = method_symbol
                    # WI-supat (D3): record the AUTHORITATIVE method->enclosing
                    # class link. ``symbol`` is the ClassDef's own class Symbol
                    # (this loop iterates that class's body), so this is exact for
                    # nested / same-short-name classes where a bare-name
                    # symbol_by_name lookup would clobber.
                    method_to_enclosing_class_id[method_symbol.id] = symbol.id
                    # Track as processed to avoid duplicate extraction
                    processed_functions.add((item.lineno, item.name))

        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            # Skip if already processed as a class method
            if (node.lineno, node.name) in processed_functions:
                continue

            # INV-mofav: every FunctionDef / AsyncFunctionDef is emitted as a
            # Symbol, at any nesting depth. Top-level and nested-with-decorator
            # cases are unchanged in name (`node.name`). Nested-undecorated
            # cases use a qualified name `outer.inner` (recursively
            # `outermost.middle.inner`) to disambiguate same-named nested
            # functions in different parents.
            is_top_level = node.col_offset == 0
            func_chain = _enclosing_function_chain(node)
            is_nested = bool(func_chain)
            if is_nested:
                qualified_name = ".".join(func_chain + [node.name])
                immediate_parent_name = func_chain[-1]
            else:
                qualified_name = node.name
                immediate_parent_name = None
            # stable_id v6 (ADR-0035 §1, WI-gitun): identity uses the FULL enclosing scope
            # chain (classes + functions) so a function nested in a method of distinct classes
            # (``A.t.helper`` vs ``B.t.helper``) gets distinct ids; the function-only
            # ``qualified_name`` above is left untouched (display/lookup, not identity).
            scoped_id_name = ".".join(_enclosing_scope_chain(node) + [node.name])

            if True:
                # Track as processed
                processed_functions.add((node.lineno, node.name))
                end_line = node.end_lineno or node.lineno
                end_col = node.end_col_offset or 0
                span = Span(
                    start_line=node.lineno,
                    end_line=end_line,
                    start_col=node.col_offset,
                    end_col=end_col,
                )

                # Build rich metadata for function (ADR-3aaa)
                # Route detection moved to FRAMEWORK_PATTERNS phase
                func_meta: dict[str, object] = {}

                # Extract decorators with arguments
                if node.decorator_list:
                    func_decorators = [
                        _extract_decorator_info(dec) for dec in node.decorator_list
                    ]
                    func_meta["decorators"] = func_decorators
                    # Check if any decorator references a prefixed APIRouter
                    if router_prefixes:
                        # iterate the typed local, not the object-typed
                        # func_meta["decorators"] lookup
                        for dec_info in func_decorators:
                            name_val = dec_info.get("name", "")
                            dec_name = name_val if isinstance(name_val, str) else ""
                            dot_idx = dec_name.find(".")
                            if dot_idx > 0:
                                receiver = dec_name[:dot_idx]
                                if receiver in router_prefixes:
                                    func_meta["router_prefix"] = router_prefixes[receiver]
                                    break

                # Extract structured parameters
                params = _extract_parameters_info(node.args, exclude_self=False)
                if params:
                    func_meta["parameters"] = params

                # Try typed tier first (ADR-0014 §3), fall back to untyped
                func_sig = _format_function_signature(node)
                func_modifiers = _python_visibility_modifiers(node.name)
                norm_sig = normalize_python_signature(func_sig)
                if norm_sig:
                    # INV-zudob: typed tier also threads file identity as
                    # the containing scope for top-level functions, so
                    # two same-signature functions in different modules
                    # get distinct stable_ids.
                    # INV-bazij (Phase 6 PR3): prepend qualified_name into
                    # the normalized signature so two same-signature
                    # top-level functions in the same module split.
                    func_stable_id = make_typed_stable_id(
                        "function", norm_sig,
                        visibility_from_modifiers(func_modifiers),
                        file_containing_id,
                        decorators=_extract_py_decorator_names(node),
                        name=node.name, qualified_name=scoped_id_name,
                    )
                else:
                    # INV-zudob: same threading for the untyped fallback.
                    # INV-bazij: thread qualified_name as the disambiguator
                    # so nested functions and same-named top-level functions
                    # in different modules stay distinct.
                    func_stable_id = _compute_stable_id(
                        node, containing_stable_id=file_containing_id,
                        name=node.name, qualified_name=scoped_id_name,
                    )

                _fds = ast.get_docstring(node)
                _fds_line = _fds.split("\n")[0].strip()[:80] if _fds else None
                # WI-gipag: only top-level functions are candidates for
                # the public API. Nested functions captured here (whether
                # decorated or undecorated under INV-mofav) are never
                # externally reachable via __all__, so is_exported stays
                # False for them.
                func_is_exported = (
                    is_top_level
                    and _is_python_top_level_exported(node.name, module_all)
                )
                # INV-mofav: nested functions stamp the immediate enclosing
                # function name into meta.nesting_parent so consumers can
                # branch on nesting without parsing the qualified `name`.
                if immediate_parent_name is not None:
                    func_meta["nesting_parent"] = immediate_parent_name
                symbol = Symbol(
                    id=_make_symbol_id(str(py_file), node.lineno, end_line, qualified_name, "function"),
                    name=qualified_name,
                    qualified_name=qualified_name,  # WI-fagab (ADR-0032 sibling field)
                    kind="function",
                    language="python",
                    path=str(py_file),
                    span=span,
                    stable_id=func_stable_id,
                    shape_id=_compute_shape_id(node, "function"),
                    meta=func_meta if func_meta else None,
                    cyclomatic_complexity=_compute_cyclomatic_complexity(node),
                    line_span=_compute_line_span(node),
                    signature=_format_function_signature(
                        node, max_len=_DISPLAY_SIGNATURE_MAX_LEN, render_defaults=True
                    ),
                    docstring=_fds_line,
                    modifiers=func_modifiers,
                    is_exported=func_is_exported,
                )
                symbols.append(symbol)
                # Only top-level functions get registered in the flat
                # symbol_by_name dict (which feeds call resolution for
                # bare-name calls at module-level). Nested functions resolve
                # through the per-parent inner_scope map (INV-mofav) to
                # prevent sibling collisions when two parents each define a
                # nested helper of the same short name.
                if not is_nested:
                    symbol_by_name[node.name] = symbol
                func_symbol_by_node_id[id(node)] = symbol

    # Extract usage contexts for call-based frameworks (v1.1.x).
    # UsageContext records feed into YAML-driven enrichment (concept tagging on
    # handler symbols). Route symbols are derived from the same contexts for the
    # route_handler linker, which needs kind="route" symbols to create routes_to
    # edges. See "Route Detection Architecture" in this module's docstring.
    usage_contexts: list[UsageContext] = []
    # Collect module-level string constants for route path resolution.
    # Enables constant propagation: path(BASE + "/users/", view) → "/api/v1/users/".
    route_local_constants, route_imports = _collect_module_constants(
        tree, repo_root, py_file,
    )
    django_contexts = _extract_django_usage_contexts(
        tree, str(py_file), symbol_by_name,
        local_constants=route_local_constants,
        imports=route_imports,
        repo_root=repo_root,
    )
    usage_contexts.extend(django_contexts)
    # router_prefixes already computed above (before the symbol extraction loop)
    flask_contexts = _extract_flask_usage_contexts(
        tree, str(py_file), symbol_by_name, router_prefixes,
        local_constants=route_local_constants,
        imports=route_imports,
        repo_root=repo_root,
    )
    usage_contexts.extend(flask_contexts)
    starlette_contexts = _extract_starlette_usage_contexts(
        tree, str(py_file), symbol_by_name,
        imports=route_imports,
    )
    usage_contexts.extend(starlette_contexts)

    # Create route symbols from Django usage contexts.
    #
    # WI-lojoh: class-based views (registered via Cls.as_view()) get
    # http_method="ANY" so the post-pass `expand_class_based_view_routes`
    # can introspect the view class's get/post/put/patch/delete/head/options
    # methods and emit one route variant per declared method. When the view
    # class lives outside the analyzed repo (e.g. django.contrib.auth.views),
    # the route stays at "ANY" — better than fabricating a wrong "GET".
    # Function-based views keep "GET" (Django dispatches them for any HTTP
    # verb, but GET is the conventional default for static-analysis output).
    for ctx in django_contexts:
        route_path = ctx.metadata.get("route_path", "")
        view_name = ctx.metadata.get("view_name")
        is_cbv = bool(ctx.metadata.get("is_class_based_view"))
        http_method = "ANY" if is_cbv else "GET"
        extra: dict[str, object] = {"view_name": view_name}
        if is_cbv:
            extra["is_class_based_view"] = True
        # WI-zugob: minted through the shared chokepoint. Symbol.name changes
        # from "django:{view}" to "{METHOD} {path}"; the view identity stays in
        # meta["view_name"], which is what every consumer already read.
        symbols.append(make_route_symbol(
            language="python",
            path=str(py_file),
            span=ctx.span,
            method=http_method,
            route_path=route_path,
            origin=PASS_ID,
            # _extract_file_analysis has no AnalysisRun in scope; py.py backfills
            # origin_run_id for every symbol in analyze()
            # (`symbol.origin_run_id = run.execution_id`) — which is how these
            # markers were provenance-stamped before this migration too.
            origin_run_id="",
            extra_meta=extra,
        ))

    # Create route symbols from Starlette Route/WebSocketRoute usage contexts.
    # Starlette routes are constructor calls, not method calls on app/router,
    # so emitting kind="route" here mirrors the Django path rather than the
    # YAML-only path used for Flask add_url_rule / FastAPI add_api_route.
    for ctx in starlette_contexts:
        route_path = ctx.metadata.get("route_path", "")
        view_name = ctx.metadata.get("view_name")
        # WI-kohav: each usage_context now carries a single http_method string
        # (one ctx per method emitted by the producer); wrap in a 1-elem list so
        # the per-method minting below is unchanged.
        methods = [ctx.metadata.get("http_method") or "GET"]
        receiver = ctx.metadata.get("receiver", "Route")
        for method in methods:
            # ADR-0034 / Phase 6 PR6: canonical IDs forbid ``:`` in the
            # name segment (the same character is the segment separator).
            # The method-disambiguated route name embeds the method and
            # path via ``" "`` so the canonical 5-segment shape holds.
            symbols.append(make_route_symbol(
                language="python",
                path=str(py_file),
                span=ctx.span,
                method=method,
                route_path=route_path,
                origin=PASS_ID,
                # _extract_file_analysis has no AnalysisRun in scope; py.py backfills
                # origin_run_id for every symbol in analyze()
                # (`symbol.origin_run_id = run.execution_id`) — which is how these
                # markers were provenance-stamped before this migration too.
                origin_run_id="",
                handler_ref=ctx.symbol_ref,
                extra_meta={
                    "view_name": view_name,
                    "framework": "starlette",
                    "route_class": receiver,
                },
            ))

    # Create route symbols from Flask-RESTful add_resource usage contexts.
    # add_resource registers all HTTP methods the Resource class defines,
    # but we don't know which methods at static analysis time, so we use
    # ANY as the method.
    for ctx in flask_contexts:
        if ctx.position != "resource_class":
            continue
        route_path = ctx.metadata.get("route_path", "")
        view_name = ctx.metadata.get("view_name")
        symbols.append(make_route_symbol(
            language="python",
            path=str(py_file),
            span=ctx.span,
            method="ANY",
            route_path=route_path,
            origin=PASS_ID,
            # _extract_file_analysis has no AnalysisRun in scope; py.py backfills
            # origin_run_id for every symbol in analyze()
            # (`symbol.origin_run_id = run.execution_id`) — which is how these
            # markers were provenance-stamped before this migration too.
            origin_run_id="",
            handler_ref=ctx.symbol_ref,
            extra_meta={"view_name": view_name},
        ))

    # Compute module name for import resolution
    if repo_root is not None:
        importing_module = _module_name_from_path(py_file, repo_root, source_roots)
    else:
        importing_module = py_file.stem  # Fallback to just filename
    symbol_imports, module_imports = _extract_imports(tree, importing_module)

    # INV-mofav: build the per-parent inner scope map. For each emitted
    # function Symbol whose AST node has an enclosing FunctionDef ancestor,
    # register it under its short name in the parent function's scope.
    nested_by_parent_id: dict[str, dict[str, Symbol]] = {}
    # identity:F1/F4a: enclosing_func_id maps every func/method Symbol.id to its
    # nearest enclosing FUNCTION Symbol.id — the SAME parent_map ancestry walk as
    # nested_by_parent_id, but NOT gated on kind (a method's enclosing function
    # IS a real lexical scope). Computed here so _build_scope_stack can
    # materialize the LEGB frame chain without a second walk.
    enclosing_func_id: dict[str, str] = {}
    for _node_id, _sym in func_symbol_by_node_id.items():
        _parent = parent_map.get(_node_id)
        while _parent is not None:
            if isinstance(_parent, (ast.FunctionDef, ast.AsyncFunctionDef)):
                _parent_sym = func_symbol_by_node_id.get(id(_parent))
                if _parent_sym is not None:
                    enclosing_func_id[_sym.id] = _parent_sym.id
                    # WI-jafat CHANGE B: a method must NOT be registered as a
                    # VALUE in any enclosing function's inner scope — otherwise a
                    # method inside a class inside a function would shadow that
                    # function's own nested helper of the same short name at
                    # callee resolution. (A method can still be a PARENT: a
                    # function nested inside a method registers, keyed by the
                    # method's id — the resolution-improving intent of decision
                    # #8, not a regression.) So the nested-scope VALUE record is
                    # gated on kind, but the enclosing_func_id CHILD record above
                    # is not.
                    if _sym.kind != "method":
                        short_name = _sym.name.rsplit(".", 1)[-1]
                        nested_by_parent_id.setdefault(_parent_sym.id, {})[short_name] = _sym
                break
            _parent = parent_map.get(id(_parent))

    # identity:F1/F4a: per-function LEGB "L" shadow sets (needs the AST nodes, so
    # a dedicated walk — func_symbol_by_node_id is keyed by node id only).
    local_names_by_func_id: dict[str, frozenset[str]] = {}
    for _fn_node in ast.walk(tree):
        if isinstance(_fn_node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            _fn_sym = func_symbol_by_node_id.get(id(_fn_node))
            if _fn_sym is not None:
                local_names_by_func_id[_fn_sym.id] = _collect_scope_local_names(_fn_node)

    return FileAnalysis(
        symbols=symbols,
        symbol_by_name=symbol_by_name,
        imports=symbol_imports,
        module_imports=module_imports,
        tree=tree,
        usage_contexts=usage_contexts,
        source=source,
        nested_by_parent_id=nested_by_parent_id,
        func_symbol_by_node_id=func_symbol_by_node_id,
        enclosing_func_id=enclosing_func_id,
        local_names_by_func_id=local_names_by_func_id,
        method_to_enclosing_class_id=method_to_enclosing_class_id,
    ), None


def _collect_call_func_attr_ids(block_nodes: list[ast.AST]) -> set[int]:
    """Return the ``id()``s of Attribute nodes that are the direct callee of a Call.

    An attribute that is a call's ``func`` (``os.getenv(...)``, ``obj.method()``)
    is handled by the calls pipeline; attribute-READ emitters (``module_attr_ref``
    and the WI-gubar ``@property``-read producer) must skip these so they never
    double-emit a read edge for what is really a call callee.
    """
    ids: set[int] = set()
    for root in block_nodes:
        for sub in ast.walk(root):
            if isinstance(sub, ast.Call) and isinstance(sub.func, ast.Attribute):
                ids.add(id(sub.func))
    return ids


def _build_property_getter_index(
    symbols: list[Symbol],
) -> dict[tuple[str, str], Symbol]:
    """Index ``@property`` getters by ``(path, qualified_name)``, first-getter-wins.

    Built from the per-file symbols BEFORE the name-keyed registries
    (``symbol_by_name`` / the ``(path, qualified)`` index) collapse a getter and
    its same-qualified-name ``@x.setter`` / ``@x.deleter`` to a single last-write
    entry. That collapse retains the setter/deleter, whose recorded decorator is
    the dotted ``x.setter`` / ``x.deleter``; it fails
    :func:`_resolve_property_getter`'s bare-``property`` gate, masking the getter
    and dropping the read edge for a read-write property. This dedicated index,
    consulted first, recovers the getter (WI-sizut).
    """
    index: dict[tuple[str, str], Symbol] = {}
    for sym in symbols:
        if sym.kind != "method":
            continue
        for dec in (sym.meta or {}).get("decorators", []):
            if isinstance(dec, dict) and dec.get("name") == "property":
                index.setdefault((sym.path, sym.name), sym)
                break
    return index


def _resolve_property_getter(
    class_symbol: Symbol,
    attr_name: str,
    local_symbols: dict[str, Symbol],
    sym_by_path_name: dict[tuple[str, str], Symbol] | None,
    property_getter_by_path_name: dict[tuple[str, str], Symbol] | None = None,
) -> Symbol | None:
    """Return the class's ``@property`` getter Symbol for ``attr_name``, else None.

    Resolution mirrors the Case 2c method lookup surfaces: the full pipeline's
    ``(path, qualified)`` cross-file index first, then — for single-file
    ``extract_nodes`` where that index is absent and methods are keyed by SHORT
    name — a short-name hit accepted only when its qualified name matches this
    class (guarding the same-short-name-across-classes collision the
    ``(path, qualified)`` index would otherwise disambiguate). Gates on the
    resolved symbol being a *method* carrying the bare ``@property`` decorator
    (``kind == "method"`` and a ``meta['decorators']`` entry whose ``name`` is
    exactly ``"property"`` — a getter, not a ``@x.setter`` whose recorded name
    is the dotted ``"x.setter"``). A plain data field, a non-property method, or
    a missing member returns None, so only a genuine getter invocation (which IS
    a call) emits a ``calls`` edge.
    """
    qualified_name = f"{class_symbol.name}.{attr_name}"
    # WI-sizut: the dedicated getter index (built pre-collapse) is authoritative
    # — it survives a same-qualified-name @x.setter/@x.deleter that would
    # otherwise mask the getter out of the name-keyed indexes below.
    if property_getter_by_path_name is not None:
        pg = property_getter_by_path_name.get((class_symbol.path, qualified_name))
        if pg is not None:
            return pg
    getter: Symbol | None = None
    if sym_by_path_name is not None:
        getter = sym_by_path_name.get((class_symbol.path, qualified_name))
    if getter is None:
        cand = local_symbols.get(attr_name)
        if cand is not None and cand.name == qualified_name:
            getter = cand
    if getter is None or getter.kind != "method":
        return None
    for dec in (getter.meta or {}).get("decorators", []):
        if isinstance(dec, dict) and dec.get("name") == "property":
            return getter
    return None


# ---------------------------------------------------------------------------
# Sub-scope binding analysis (INV-ruluv).
#
# ``process_code_block`` recurses into comprehension / lambda / nested-def
# bodies. Those are NEW binding scopes: a comprehension for-target, a lambda
# parameter, or a nested-def parameter that SHADOWS an outer ``var_types``-typed
# name must NOT inherit the stale outer type — otherwise the producer emits a
# confidently-wrong ``receiver_type_hint`` (a resolved edge to the wrong
# method/getter). These helpers compute the shadow set to prune before
# descending into a sub-scope. Pruning is applied at scope ENTRY (per node),
# not at the child-descent site, so a NESTED comprehension's inner target is
# pruned too (the inner comp reaches the recursion as a block node, not a
# child).
# ---------------------------------------------------------------------------


def _arg_names(args: ast.arguments) -> set[str]:
    """Every parameter name bound by an ``ast.arguments`` (all kinds)."""
    names = {a.arg for a in (*args.posonlyargs, *args.args, *args.kwonlyargs)}
    if args.vararg:
        names.add(args.vararg.arg)
    if args.kwarg:
        names.add(args.kwarg.arg)
    return names


def _collect_target_names(target: ast.expr, out: set[str]) -> None:
    """Gather names bound by a comprehension/assignment target, recursing
    through tuple/list/starred unpacking (``for (a, *rest, (b, c)) in ...``).
    ``Attribute``/``Subscript`` targets bind no new name."""
    if isinstance(target, ast.Name):
        out.add(target.id)
    elif isinstance(target, ast.Starred):
        _collect_target_names(target.value, out)
    elif isinstance(target, (ast.Tuple, ast.List)):
        for elt in target.elts:
            _collect_target_names(elt, out)


def _subscope_bound_names(node: ast.AST) -> set[str]:
    """Names bound in ``node``'s OWN nested scope — the shadow set to prune.

    * Lambda: every parameter name.
    * Comprehension / genexp: every generator for-target name (recursively
      unpacked). Deliberately EXCLUDES walrus (``:=`` / ``ast.NamedExpr``)
      targets — PEP 572 binds those in the ENCLOSING scope, so they keep their
      outer type — and the ``if`` guards / iterables (read positions).
    """
    if isinstance(node, ast.Lambda):
        return _arg_names(node.args)
    if isinstance(node, (ast.ListComp, ast.SetComp,
                         ast.GeneratorExp, ast.DictComp)):
        names: set[str] = set()
        for gen in node.generators:
            _collect_target_names(gen.target, names)
        return names
    return set()  # pragma: no cover - callers gate on comp/lambda nodes


def _comprehension_scope_nodes(node: ast.AST) -> list[ast.AST]:
    """The direct child expressions of a comprehension evaluated in the
    COMPREHENSION's own scope — everything except ``generators[0].iter``, which
    is eagerly evaluated in the ENCLOSING scope before the comp scope exists."""
    if isinstance(node, ast.DictComp):
        parts: list[ast.AST] = [node.key, node.value]
    else:
        parts = [node.elt]  # type: ignore[attr-defined]
    for i, gen in enumerate(node.generators):  # type: ignore[attr-defined]
        parts.append(gen.target)
        if i != 0:  # generators[0].iter is enclosing-scope
            parts.append(gen.iter)
        parts.extend(gen.ifs)
    return parts


def _prune_shadowed(
    var_types: dict[str, "Symbol"],
    external_var_types: dict[str, str],
    shadow: set[str],
) -> tuple[dict[str, "Symbol"], dict[str, str]]:
    """Return ``(var_types, external_var_types)`` copies with ``shadow`` names
    removed (INV-ruluv). Returns the originals unchanged when ``shadow`` is
    empty (e.g. a no-arg lambda) so no-shadow sub-scopes share the dicts."""
    if not shadow:
        return var_types, external_var_types
    return (
        {k: v for k, v in var_types.items() if k not in shadow},
        {k: v for k, v in external_var_types.items() if k not in shadow},
    )


def _extract_edges(
    tree: ast.AST,
    local_symbols: dict[str, Symbol],
    imports: dict[str, tuple[str, str]],
    global_symbols: dict[tuple[str, str], Symbol],
    module_imports: dict[str, str] | None = None,
    resolver: "SymbolResolver | None" = None,
    _sym_by_path_name: dict[tuple[str, str], Symbol] | None = None,
    *,
    run_id: str,
    nested_by_parent_id: dict[str, dict[str, Symbol]] | None = None,
    func_symbol_by_node_id: dict[int, Symbol] | None = None,
    enclosing_func_id: dict[str, str] | None = None,
    local_names_by_func_id: dict[str, frozenset[str]] | None = None,
    method_to_enclosing_class_id: dict[str, str] | None = None,
    module_to_file_id: dict[str, str] | None = None,
    property_getter_by_path_name: dict[tuple[str, str], Symbol] | None = None,
) -> list[Edge]:
    """Extract call and instantiation edges from an AST.

    Resolves both local and cross-file calls/instantiations.

    Handles:
    - Direct calls: helper(), ClassName()
    - Self method calls: self.method()
    - Self field method calls: self.field.method() (using field type inference from __init__)
    - Module-qualified calls: module.ClassName(), module.func()
    - Variable method calls: variable.method() (with constructor-only type inference)

    Type inference sources:
    1. Direct constructor calls: stub = Client() → var_types['stub'] = Client
    2. Return type annotations: stub = get_client() where get_client() -> Client
       → var_types['stub'] = Client (requires annotation on the function)
    3. Parameter type annotations: def f(session: Session) → param maps to Session

    Field type inference tracks self.field assignments in __init__ from typed params
    and constructor calls.

    Args:
        tree: The parsed AST
        local_symbols: Symbols defined in this file
        imports: Symbol imports (from X import Y)
        global_symbols: All symbols across the project
        module_imports: Module imports (import X, import X as Y)
        resolver: Optional SymbolResolver for efficient cross-file lookups
    """
    # tree comes from ast.parse (mode="exec") → always an ast.Module; the
    # ast.AST annotation is loose (FileAnalysis.tree is ast.AST). Narrow it here
    # so the tree.body scans below type-check, without tightening the signature
    # (which would cascade to the ast.AST-typed call sites).
    assert isinstance(tree, ast.Module)
    if module_imports is None:  # pragma: no cover
        module_imports = {}
    if nested_by_parent_id is None:  # pragma: no cover
        nested_by_parent_id = {}
    if func_symbol_by_node_id is None:  # pragma: no cover
        func_symbol_by_node_id = {}
    if enclosing_func_id is None:  # pragma: no cover
        enclosing_func_id = {}
    if local_names_by_func_id is None:  # pragma: no cover
        local_names_by_func_id = {}
    if method_to_enclosing_class_id is None:  # pragma: no cover
        method_to_enclosing_class_id = {}

    # WI-luhah residual: the per-function STATEMENT-bound shadow set that
    # `_enclosing_shadow` unions alongside the LEGB `local_names_by_func_id`.
    # Built here rather than threaded through FileAnalysis because everything
    # it needs (the tree + the node->symbol map) is already in scope, and the
    # set is a shadow concern local to edge extraction — the LEGB set it sits
    # beside must keep excluding def/class names for the lookup to work.
    _stmt_shadow_by_func_id: dict[str, frozenset[str]] = {}
    if func_symbol_by_node_id:
        for _snode in ast.walk(tree):
            if not isinstance(_snode, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            _ssym = func_symbol_by_node_id.get(id(_snode))
            if _ssym is not None:
                _stmt_shadow_by_func_id[_ssym.id] = _collect_statement_binding_names(
                    list(ast.iter_child_nodes(_snode)),
                )

    # WI-supat (D3): per-file class SHORT-NAME multiplicity. A receiver-type id
    # is only trustworthy when its short name resolves to a SINGLE in-file class:
    # with >=2 same-short-name classes the bare-name inference (symbol_by_name is
    # last-write-wins) that produced the receiver's type Symbol could have hit
    # the wrong twin, so the id is omitted and the linker falls back to the safe
    # name+guard path. Counts ClassDef nodes (nested included) so a nested
    # namesake also trips the gate. The ENCLOSING id needs no such gate — it comes
    # from the authoritative method->class map, not a name lookup.
    class_name_counts: dict[str, int] = {}
    for _cnode in ast.walk(tree):
        if isinstance(_cnode, ast.ClassDef):
            class_name_counts[_cnode.name] = class_name_counts.get(_cnode.name, 0) + 1

    edges: list[Edge] = []

    def _emit_function_ref(name_node: ast.Name, caller: Symbol, stack: ScopeStack | None = None) -> None:
        """Emit a 'references' edge if *name_node* resolves to a function/method.

        Used for function references in non-call contexts: call arguments,
        dict values, variable assignments, and collection literals.
        """
        name = name_node.id
        # INV-mofav: enclosing-function scope wins over module scope, mirroring
        # Python's LEGB rule for bare names (step 1-2). Without this, a bare-name
        # reference to a nested helper resolves to a same-named top-level Symbol.
        symbol = stack.lookup_immediate(name) if stack else None
        if symbol is None:
            symbol = local_symbols.get(name)
        if not symbol and name in imports:
            mod_name, original_name = imports[name]
            symbol = _lookup_symbol_by_module(
                global_symbols, mod_name, original_name, resolver=resolver
            )
        # identity:F1/F4a step-4: last-resort enclosing-scope lookup for a bare
        # reference to a helper defined in a grandparent enclosing function.
        # Additive — fires only when unresolved above; returns only functions.
        if symbol is None and stack is not None:
            symbol = stack.lookup_enclosing(name)
        if symbol and symbol.kind in ("function", "method"):
            edges.append(Edge.create(
                src=caller.id,
                dst=symbol.id,
                edge_type="references",
                line=name_node.lineno,
                evidence_type="function_reference",
                origin=PASS_ID,
                origin_run_id=run_id,
            ))

    module_level_vars: dict[str, Symbol] = {
        name: sym for name, sym in local_symbols.items()
        if sym.kind == "variable"
    }

    def _collect_local_bindings(
        func_node: ast.FunctionDef | ast.AsyncFunctionDef,
        include_import_aliases: bool = True,
    ) -> frozenset[str]:
        """Return names bound locally in *func_node* (params + body assignments).

        Used to detect shadows that suppress variable-reference edges.
        Walks the immediate scope only — nested function/class bodies are
        excluded so their locals don't mask the enclosing function's view.

        *include_import_aliases* (default True) controls whether a plain
        ``import X as Y`` alias counts as a binding. For the ``references``
        edges to module-level VARIABLES (``_emit_variable_refs``) it must — a
        local ``import foo as bar`` rebinds ``bar`` off a same-named module
        variable. For the WI-huhum ``module_attr_ref`` retarget it must NOT:
        there ``local_name`` IS a module alias (that is why it is in
        ``module_imports``), and a function-local ``import pkg.mod as m`` — the
        dominant self-corpus shape (``m.CONST`` inside a test method) — is the
        alias we want to resolve, not a shadow of it. ``from``-import value
        rebinds and param/assignment shadows are still collected either way —
        EXCEPT that on the retarget path (``include_import_aliases=False``) a
        ``from``-import that is a *co-referent module alias* (its absolute target
        equals the same name's ``module_imports`` binding) is excluded, since it
        names the very in-tree module the read resolves against, not a value
        shadowing it (INV-nuzas: the ``rust._analyzer`` self-corpus phantom,
        where a sibling method plain-imports the analyzer module and this one
        from-imports it under the same name).
        """
        names: set[str] = set()
        for arg in func_node.args.args:
            names.add(arg.arg)
        for arg in func_node.args.posonlyargs:
            names.add(arg.arg)
        for arg in func_node.args.kwonlyargs:
            names.add(arg.arg)
        if func_node.args.vararg:
            names.add(func_node.args.vararg.arg)
        if func_node.args.kwarg:
            names.add(func_node.args.kwarg.arg)

        scope_boundary = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)

        def _walk_scope(nodes: list[ast.AST]) -> None:
            for node in nodes:
                if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store):
                    names.add(node.id)
                elif isinstance(node, ast.Import):
                    if include_import_aliases:
                        for alias in node.names:
                            names.add(alias.asname or alias.name.split(".")[0])
                elif isinstance(node, ast.ImportFrom):
                    for alias in node.names:
                        bound = alias.asname or alias.name
                        # INV-nuzas / INV-fahub (co-referent module alias): on
                        # the module_attr_ref retarget path
                        # (include_import_aliases=False), a ``from pkg import sub
                        # as m`` that binds the SAME in-tree module already
                        # recorded as a module alias in the FILE-scoped
                        # ``module_imports`` (via a sibling scope's plain
                        # ``import pkg.sub as m`` — module_imports is built by an
                        # ast.walk over the whole tree, so a sibling method's
                        # plain import is visible here) is a co-referent alias,
                        # NOT a value shadow. Excluding it lets the read
                        # ``m.attr`` retarget to the real in-tree symbol instead
                        # of a workspace-prefixed phantom external. A genuine
                        # value rebind (``from pkg import CONST as m``) does not
                        # match ``module_imports[m]`` and still shadows; a later
                        # ``m = ...`` reassignment re-adds ``m`` via the Store
                        # branch above, so it too correctly stays a shadow.
                        # Absolute imports only (level == 0): the node-derived
                        # target is exact per-alias; a relative co-referent
                        # import stays phantom (a safe miss, never a wrong edge).
                        if (
                            not include_import_aliases
                            and node.level == 0
                            and module_imports.get(bound)
                            == f"{node.module}.{alias.name}"
                        ):
                            continue
                        names.add(bound)
                for child in ast.iter_child_nodes(node):
                    if not isinstance(child, scope_boundary):
                        _walk_scope([child])

        _walk_scope(list(ast.iter_child_nodes(func_node)))
        return frozenset(names)

    def _enclosing_shadow(caller_id: str) -> frozenset[str]:
        """WI-luhah gap 1c / INV-fahub: the union of every STRICT enclosing
        function's locally-bound names (the existing LEGB ``local_names_by_func_id``
        set — params / assignments / imports).

        A read ``m.attr`` (``_emit_module_attr_refs``) or bare ``m``
        (``_emit_variable_refs``) inside a NESTED function whose ``m`` is a
        closure-captured enclosing PARAM or local (a value, not the module alias)
        must not retarget to a module symbol. The nested scope's own
        ``_collect_local_bindings`` sees only its immediate bindings and misses
        the enclosing param, so thread the ``enclosing_func_id`` chain and union
        each ancestor's local names (the WI-luhah plan names this set as the
        intended union source). The set also carries an enclosing plain-``import``
        alias, so a nested read of an enclosing *function-local* in-tree import
        stays phantom rather than resolving — an accepted, INV-fahub-safe
        over-approximation (a missed retarget, never a confidently-wrong edge).
        The union also over-shadows an enclosing ``global``/``nonlocal``-declared
        name (another INV-fahub-safe missed retarget).

        The LEGB set alone omits enclosing ``def``/``class`` statement names and
        ``except E as X`` handler names — deliberately, since the lookup resolves
        those to their ``NestedDef`` bindings directly — so each is also unioned
        from the dedicated ``_stmt_shadow_by_func_id`` shadow collector this
        residual called for. Without it, an enclosing ``def cfg(): ...`` whose
        name collides with a module import alias left a nested ``cfg.CONFIG``
        read confidently-wrong-RESOLVED against the module symbol.
        """
        names: set[str] = set()
        cur = enclosing_func_id.get(caller_id)
        while cur is not None:
            names |= local_names_by_func_id.get(cur, frozenset())
            names |= _stmt_shadow_by_func_id.get(cur, frozenset())
            cur = enclosing_func_id.get(cur)
        return frozenset(names)

    def _emit_variable_refs(
        body_nodes: list[ast.AST],
        caller_symbol: Symbol,
        local_bindings: frozenset[str] = frozenset(),
    ) -> None:
        """Emit ``references`` edges for bare-name reads of module-level variables.

        Walks *body_nodes* (skipping nested function/class scopes) and emits
        an edge for each ``ast.Name`` in Load context that resolves to a
        module-level variable Symbol and is not shadowed by a local binding.
        """
        scope_boundary = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)

        def _walk(nodes: list[ast.AST]) -> None:
            for node in nodes:
                if (
                    isinstance(node, ast.Name)
                    and isinstance(node.ctx, ast.Load)
                    and node.id in module_level_vars
                    and node.id not in local_bindings
                ):
                    edges.append(Edge.create(
                        src=caller_symbol.id,
                        dst=module_level_vars[node.id].id,
                        edge_type="references",
                        line=node.lineno,
                        evidence_type="ast_name_read",
                        origin=PASS_ID,
                        origin_run_id=run_id,
                    ))
                for child in ast.iter_child_nodes(node):
                    if not isinstance(child, scope_boundary):
                        _walk([child])

        _walk(body_nodes)

    def _emit_closure_factory_dispatch(
        func_node: ast.FunctionDef | ast.AsyncFunctionDef,
        caller_symbol: Symbol,
        inner_scope: dict[str, Symbol] | None,
    ) -> None:
        """Emit a ``dispatches_to`` edge for a returned directly-nested closure.

        A *closure factory* is a function ``F`` whose body contains
        ``return <bare-name>`` where ``<bare-name>`` resolves to one of F's own
        directly-nested ``FunctionDef`` / ``AsyncFunctionDef`` definitions (the
        canonical ``register_analyzer``-style decorator factory:
        ``def register(...): def decorator(func): ...; return decorator``).

        The returned inner closure is reachable whenever ``F`` is reached at its
        own call / decoration sites, but the reachability BFS in
        ``cli._REACHABILITY_EDGE_TYPES`` only traverses
        ``{calls, dispatches_to, wraps}``. Without this edge the nested closure
        has zero reachability in-edges and ``dead-code-maybe`` falsely flags it
        dead. We emit ``F -> nested`` of type ``dispatches_to`` with
        ``meta["dispatch_kind"] == "closure_factory"`` so the closure inherits
        F's reachability (dispatch:F8 PR-A).

        Scope is narrow on purpose to avoid edge proliferation:

        * Only a *bare* ``ast.Name`` return target counts. A returned call
          (``return f()``), attribute (``return self.x``), parameter, or
          non-nested name emits NO edge — those are not "this function returns
          its own inner closure".
        * Resolution is keyed on ``inner_scope`` (``nested_by_parent_id`` for
          F's symbol id), so it can ONLY match F's directly-nested defs. A
          sibling top-level function of the same name is never matched because
          it lives in ``local_symbols``, not ``inner_scope``.
        * Returns are collected from F's direct body plus the bodies of simple
          ``if`` / ``try`` blocks nested directly inside it (the common
          early-return / try-fallthrough factory shapes), but NOT from nested
          function / class scopes (whose returns belong to a different ``F``).

        Per-target de-duplication is handled by ``Edge.edge_key`` (which keys on
        ``(src, dst, type)`` and excludes the line), so two return statements
        pointing at the same nested closure collapse to one logical edge
        downstream; we still avoid emitting duplicate ``Edge`` objects here by
        tracking the nested symbol ids already linked.
        """
        if not inner_scope:
            return
        scope_boundary = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)

        def _direct_returns(nodes: list[ast.AST]) -> list[ast.Return]:
            """Collect ``Return`` nodes in F's own scope (descending if/try only)."""
            found: list[ast.Return] = []
            for node in nodes:
                if isinstance(node, ast.Return):
                    found.append(node)
                elif isinstance(node, (ast.If, ast.Try)):
                    # Descend into the control-flow block's own statement lists
                    # (body / orelse / handlers / finalbody) — these returns are
                    # still F's. ``ast.iter_child_nodes`` would also surface the
                    # condition expression, which never contains a top-level
                    # Return, so the scope_boundary guard below is sufficient.
                    for child in ast.iter_child_nodes(node):
                        if not isinstance(child, scope_boundary):
                            found.extend(_direct_returns([child]))
            return found

        linked: set[str] = set()
        for ret in _direct_returns(list(func_node.body)):
            value = ret.value
            if not isinstance(value, ast.Name):
                continue
            # ``inner_scope`` (``nested_by_parent_id[F]``) is populated only
            # from ``func_symbol_by_node_id`` values that are NOT methods (the
            # construction at ~py.py:2784 skips ``kind == "method"`` as a
            # value), so every entry is a ``kind == "function"`` nested def. We
            # therefore only need the presence check — a returned name that is
            # not a nested def (parameter, import, sibling top-level function,
            # attribute, call) is absent from ``inner_scope`` and yields None.
            nested = inner_scope.get(value.id)
            if nested is None:
                continue
            if nested.id in linked:
                continue
            linked.add(nested.id)
            edges.append(Edge.create(
                src=caller_symbol.id,
                dst=nested.id,
                edge_type="dispatches_to",
                line=ret.lineno,
                # The return is a bare function *reference* (not a call); the
                # dispatch SHAPE rides on ``meta['dispatch_kind']`` per the
                # axis-registry division of labor (evidence_type = inference
                # pathway; dispatch_kind = dispatch shape). Reusing the
                # registered ``function_reference`` evidence type avoids minting
                # a one-producer heavyweight ADR-0028 axis value.
                evidence_type="function_reference",
                origin=PASS_ID,
                origin_run_id=run_id,
                meta={"dispatch_kind": "closure_factory"},
            ))

    def _emit_module_attr_refs(
        block_nodes: list[ast.AST],
        caller_symbol: Symbol,
        local_bindings: frozenset[str] = frozenset(),
    ) -> None:
        """Emit ``module_attr_ref`` edges for attribute reads on imported modules.

        Targets patterns like ``os.environ[...]``, ``sys.argv``, ``sys.path``:
        an imported module name followed by an attribute access that is NOT
        itself the callable of a function call (those are handled by the
        calls pipeline and matched against the YAML ``functions:``/``methods:``
        entries).  This emission pairs with ``attributes:`` entries in the
        io_primitives YAML catalog, which were previously dead metadata —
        without an edge to match, ``io-boundaries`` silently under-reported
        env_read / ipc_send chains (WI-guhok).

        WI-huhum / INV-nuzas: when the imported module is IN-TREE and the
        attribute names a first-party module-level VARIABLE (``import
        authpkg.config as cfg; cfg.CONFIG``), emit a ``references`` edge to the
        real variable instead of a workspace-prefixed phantom ``external_symbol``
        (the 52 ``module_attr_ref`` residual of INV-nuzas's acceptance-property
        failure). *local_bindings* carries the caller's own bound names so a
        param/local shadowing the module alias stays phantom (INV-fahub).
        """
        # Pre-collect Attribute-node ids that are the direct callee of a Call
        # so we can skip them below — `os.getenv("X")` already produces a
        # `calls` edge and doesn't need a redundant `module_attr_ref`.
        call_func_attr_ids = _collect_call_func_attr_ids(block_nodes)
        # Scope-bounded walk (mirrors _emit_variable_refs): a nested
        # function/class body is a DIFFERENT scope with its own alias bindings
        # and its own _emit_module_attr_refs pass, so descending into it here
        # would (a) mis-attribute a nested read to THIS caller and (b) apply
        # this caller's shadow set to a name the nested scope may rebind — under
        # the WI-huhum retarget that mints a confidently-wrong RESOLVED
        # ``references`` edge (worse than the pre-change dead-end phantom). Reads
        # in nested scopes are emitted, correctly attributed, by their own passes.
        scope_boundary = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)

        def _emit_one(sub: ast.Attribute) -> None:
            if id(sub) in call_func_attr_ids:
                return
            if not isinstance(sub.value, ast.Name):
                return
            local_name = sub.value.id
            if local_name not in module_imports:
                return
            real_module = module_imports[local_name]
            # WI-huhum retarget: an in-tree module VARIABLE resolves to its
            # real node. EXACT module match (global_symbols.get) — NOT the
            # suffix-matching _lookup_symbol_by_module — because on this
            # imported-module surface a suffix match would bind an external
            # ``import json as j; j.X`` to a coincidentally-named in-tree
            # ``pkg/json.py`` (an INV-fahub violation; mirrors WI-hotug CASE A).
            # ``references`` (not ``module_attr_ref``) keeps this taint-safe:
            # module_attr_ref IS in TAINT_CALL_EDGE_TYPES, so retargeting to a
            # live in-tree node would inject a new taint frontier; references is
            # not, and matches _emit_variable_refs' first-party module-variable
            # read. Scope-shadow-guarded so a param/local rebinding the alias
            # stays phantom.
            if local_name not in local_bindings:
                target = global_symbols.get((real_module, sub.attr))
                # INV-nuzas category A: a non-call read of an in-tree
                # module-level FUNCTION (``import pkg.helpers as h; h.compute``
                # used as a value) resolves to the real function, extending
                # WI-huhum's kind=variable retarget. Same exact-match + shadow
                # guards apply; ``references`` stays taint-safe for functions
                # too (references not in TAINT_CALL_EDGE_TYPES; a call
                # ``h.compute()`` is skipped above and handled by the calls
                # pipeline, so this only fires on function-object reads).
                if target is not None and target.kind in ("variable", "function"):
                    edges.append(Edge.create(
                        src=caller_symbol.id,
                        dst=target.id,
                        edge_type="references",
                        line=sub.lineno,
                        evidence_type="ast_name_read",
                        origin=PASS_ID,
                        origin_run_id=run_id,
                    ))
                    return
                # INV-nuzas category B (WI-tanot): the attribute names an in-tree
                # SUBMODULE / subpackage of the imported module (``import
                # hypergumbo_core as hc; hc.linkers``), not a variable/function —
                # resolve it to the submodule's first-party file/package node via
                # ``module_to_file_id`` (the supply-verdict:F4 import mechanism)
                # instead of a workspace-prefixed phantom external. EXACT dotted
                # match only (no suffix), and ``references`` (not module_attr_ref)
                # keeps it taint-safe. The shadow guard (local_bindings) already
                # applied above.
                if module_to_file_id is not None:
                    submodule_fid = module_to_file_id.get(
                        f"{real_module}.{sub.attr}"
                    )
                    if submodule_fid is not None:
                        edges.append(Edge.create(
                            src=caller_symbol.id,
                            dst=submodule_fid,
                            edge_type="references",
                            line=sub.lineno,
                            evidence_type="ast_name_read",
                            origin=PASS_ID,
                            origin_run_id=run_id,
                        ))
                        return
            qname = f"{real_module}.{sub.attr}"
            edges.append(Edge.create(
                src=caller_symbol.id,
                dst=f"python:{real_module}:0-0:{qname}:attribute",
                edge_type="module_attr_ref",
                line=sub.lineno,
                evidence_type="module_attribute_reference",
                origin=PASS_ID,
                origin_run_id=run_id,
            ))

        def _walk(nodes: list[ast.AST]) -> None:
            for node in nodes:
                if isinstance(node, scope_boundary):
                    continue  # a nested scope is emitted by its own pass
                if isinstance(node, ast.Attribute):
                    _emit_one(node)
                _walk(list(ast.iter_child_nodes(node)))

        _walk(list(block_nodes))

    # Helper to extract edges from a code block (function body, module level, etc.)
    def _external_constructor_module(call: ast.Call) -> str | None:
        """This block's import maps, bound to the module-level resolver."""
        return _external_constructor_type(call, imports, module_imports)

    def process_code_block(
        block_nodes: list[ast.AST],
        caller_symbol: Symbol,
        var_types: dict[str, Symbol] | None = None,
        stack: ScopeStack | None = None,
        external_var_types: dict[str, str] | None = None,
    ) -> None:
        """Process AST nodes within a code block, tracking variable types.

        ``stack`` is the caller's materialized LEGB frame chain (identity:F1/F4a;
        ``None`` at module level). Its immediate frame is the enclosing-function
        inner scope (INV-mofav — nested helpers of ``caller_symbol``, consulted
        before ``local_symbols``); its outer frames add the last-resort
        enclosing-scope lookup for bare calls to grandparent helpers. The type
        inference input (``_resolve_call_target``) sees only the immediate frame.

        ``external_var_types`` (WI-fuvuj) maps a local variable name to the
        catalog module string of the I/O object it was assigned from a known
        constructor (``f = open(p)`` → ``{"f": "file"}``). It parallels
        ``var_types`` (which tracks in-repo class types) and lets
        ``_process_call`` emit a module-qualified unresolved dst for method
        calls on those variables.
        """
        if var_types is None:
            var_types = {}
        if external_var_types is None:
            external_var_types = {}

        # WI-hiziz PR-3 (review): the caller method's OWN __init__ field names
        # (from the closure-visible ``class_own_field_names``). The Site-3 emit
        # excludes these so an own field re-declared by the caller's class never
        # resolves against a same-named PARENT field of a different type.
        _own_field_names = (
            class_own_field_names.get(
                caller_symbol.qualified_name.split(".")[-2], frozenset()
            )
            if caller_symbol.kind == "method"
            and "." in (caller_symbol.qualified_name or "")
            else frozenset()
        )

        for node in block_nodes:
            # INV-ruluv: skip a directly-body-nested def/class. It is processed
            # independently by the ast.walk(tree) loop with its OWN caller_symbol
            # and fresh param_types; recursing into it here would attribute its
            # edges to the enclosing caller under a stale var_types (and
            # double-emit). The top-level entry passes the whole ``node.body``,
            # so such a def/class arrives as a top-level block node the
            # child-descent guard below would never test (mirrors
            # ``_emit_module_attr_refs._walk``'s top-of-loop scope-boundary skip).
            if isinstance(
                node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)
            ):
                continue

            # INV-ruluv: a comprehension / lambda is its OWN binding scope. Prune
            # ``var_types`` of the names it binds before recursing into its
            # scope-internal parts, so a target/param that SHADOWS an outer typed
            # name does not inherit the stale outer type (which would emit a
            # confidently-wrong receiver_type_hint). Pruning at scope ENTRY (per
            # node) — not at the child-descent site — is what keeps a NESTED
            # comprehension's inner target pruned: the inner comp arrives here as
            # a block node (via ``_comprehension_scope_nodes``), not as a child.
            if isinstance(
                node, (ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp)
            ):
                _shadow = _subscope_bound_names(node)
                _pruned_vt, _pruned_ext = _prune_shadowed(
                    var_types, external_var_types, _shadow
                )
                # generators[0].iter is eagerly evaluated in the ENCLOSING scope.
                process_code_block(
                    [node.generators[0].iter], caller_symbol, var_types,
                    stack=stack, external_var_types=external_var_types,
                )
                for _part in _comprehension_scope_nodes(node):
                    process_code_block(
                        [_part], caller_symbol, _pruned_vt,
                        stack=stack, external_var_types=_pruned_ext,
                    )
                continue

            if isinstance(node, ast.Lambda):
                _shadow = _subscope_bound_names(node)
                _pruned_vt, _pruned_ext = _prune_shadowed(
                    var_types, external_var_types, _shadow
                )
                # default / kw_default exprs are evaluated in the ENCLOSING scope.
                for _dflt in (
                    *node.args.defaults,
                    *(d for d in node.args.kw_defaults if d is not None),
                ):
                    process_code_block(
                        [_dflt], caller_symbol, var_types,
                        stack=stack, external_var_types=external_var_types,
                    )
                process_code_block(
                    [node.body], caller_symbol, _pruned_vt,
                    stack=stack, external_var_types=_pruned_ext,
                )
                continue

            # Track variable assignments for type inference
            # e.g., stub = EmailServiceStub(channel) -> var_types['stub'] = EmailServiceStub
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Name) and not isinstance(
                        node.value, ast.Call,
                    ):
                        # A derivation whose RHS is not a call at all — ``p = d / "f"``.
                        # The Call branch below runs in-repo class resolution first and
                        # only then asks about external types; an operator has no call
                        # target to resolve, so it needs its own entry point rather than
                        # loosening that branch's guard.
                        derived = _derived_receiver_module(
                            node.value, external_var_types,
                            _external_constructor_module,
                        )
                        if derived is not None:
                            external_var_types[target.id] = derived
                    if isinstance(target, ast.Name) and isinstance(node.value, ast.Call):
                        assigned_class = _resolve_call_target(
                            node.value, local_symbols, imports, global_symbols,
                            module_imports, resolver,
                            inner_scope=stack.immediate_symbols() if stack else None,
                        )
                        if assigned_class and assigned_class.kind == "class":
                            var_types[target.id] = assigned_class
                        elif assigned_class and assigned_class.kind in ("function", "method"):
                            # Return type inference: if the function has a
                            # return type annotation pointing to a class,
                            # track the variable's type from that annotation.
                            ret_name = _extract_return_type_name(
                                assigned_class.signature
                            )
                            if ret_name:
                                ret_class = _resolve_return_type_class(
                                    ret_name, assigned_class, local_symbols,
                                    imports, global_symbols, resolver,
                                    _sym_by_path_name,
                                )
                                if ret_class:
                                    var_types[target.id] = ret_class
                        elif assigned_class is None:
                            # WI-fuvuj: in-repo resolution found no class. If
                            # the RHS is a known I/O constructor (open(...),
                            # socket.socket()), record the inferred receiver
                            # type so method calls on this variable emit a
                            # module-qualified unresolved dst.
                            ext_module = _external_constructor_module(node.value)
                            if ext_module is None:
                                # A derivation from an already-typed receiver is itself
                                # typed (``p = d / "f"``). Constructor first: that answers
                                # "does this BUILD a catalogued object", which is a
                                # different question from "does this PRESERVE a type".
                                ext_module = _derived_receiver_module(
                                    node.value, external_var_types,
                                    _external_constructor_module,
                                )
                            if ext_module is not None:
                                external_var_types[target.id] = ext_module

            # WI-zilag: an ANNOTATED assignment (``d: Path = raw``) types its target
            # exactly the way an annotated parameter does. Excluded from PR #246 on a
            # measurement showing zero payload — that measurement predates #247, and
            # an AnnAssign root now seeds a whole derivation chain, so the exclusion
            # was re-priced rather than re-asserted. Same binding-checked resolver as
            # every other bare-name inference here, so the FP classes it refuses
            # (a same-named class from another library, an in-file class) are refused
            # at this entry point too.
            if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
                ann_hint = _annotation_module_hint(node.annotation)
                if ann_hint is not None:
                    external_var_types[node.target.id] = ann_hint

            # Function reference in assignment RHS: callback = my_func
            if isinstance(node, ast.Assign) and isinstance(node.value, ast.Name):
                _emit_function_ref(node.value, caller_symbol, stack=stack)

            # WI-fuvuj: ``with open(p) as f:`` / ``with socket.socket() as s:``
            # — the dominant I/O constructor idiom. Type the bound name from
            # the context-manager constructor so method calls inside the body
            # emit a module-qualified unresolved dst. The body is still
            # recursed below (the generic ast.iter_child_nodes traversal).
            if isinstance(node, (ast.With, ast.AsyncWith)):
                for item in node.items:
                    if (
                        isinstance(item.optional_vars, ast.Name)
                        and isinstance(item.context_expr, ast.Call)
                    ):
                        ext_module = _external_constructor_module(item.context_expr)
                        if ext_module is not None:
                            external_var_types[item.optional_vars.id] = ext_module

            # Process calls
            if isinstance(node, ast.Call):
                _edges_before_call = len(edges)
                _process_call(
                    node, caller_symbol, local_symbols, imports, global_symbols,
                    module_imports, var_types, edges, resolver,
                    sym_by_path_name=_sym_by_path_name,
                    run_id=run_id,
                    stack=stack,
                    external_var_types=external_var_types,
                    function_aliases=function_aliases,
                    own_field_names=_own_field_names,
                    method_to_enclosing_class_id=method_to_enclosing_class_id,
                    class_name_counts=class_name_counts,
                )
                _stamp_io_mode(edges, _edges_before_call, node)
                # Function references in call arguments: map(transform, items)
                for arg in node.args:
                    if isinstance(arg, ast.Name):
                        _emit_function_ref(arg, caller_symbol, stack=stack)
                for kw in node.keywords:
                    if isinstance(kw.value, ast.Name):
                        _emit_function_ref(kw.value, caller_symbol, stack=stack)

            # WI-gubar (D2): a @property attribute READ (obj.prop) is an
            # ast.Attribute in Load context, NOT an ast.Call, so the calls
            # pipeline never sees it and the getter (Symbol.end_line,
            # ValidationResult.ok) looks dead. Emit an unresolved `calls` edge
            # carrying receiver_type_hint — mirroring the WI-noham Part A method
            # producer — and let the inherited_calls linker mint the resolved
            # edge (strict Site-2 Step-1 finds the getter, kind 'method', on the
            # concrete type). Gated on a var_types-typed INSTANCE receiver whose
            # target attribute is a @property getter; a bare-CLASS receiver
            # (ClassName.prop yields the descriptor, not the value) is
            # deliberately excluded, so no INV-fahub scope guard is needed
            # (var_types is per-function scope-local, unlike the file-global
            # local_symbols the Part A bare-class branch consults).
            # call_construct='method' keeps the unresolved edge taint-safe.
            # Restricted to function/method callers: at MODULE scope
            # caller_symbol is the <module> pseudo-node (kind='file'), and a
            # file-kind src emitting a `calls` edge would introduce a NEW
            # runtime_coherence offender in the (file, python, external_symbol,
            # python) partition — which already carries `imports` — breaking the
            # ADR-0023 §3 shrink-only ratchet (module-level property reads are
            # rare and none of the flagship reads are module-scope).
            if (
                caller_symbol.kind in ("function", "method")
                and isinstance(node, ast.Attribute)
                and isinstance(node.ctx, ast.Load)
                and isinstance(node.value, ast.Name)
                and node.value.id in var_types
                and id(node) not in _call_func_attr_ids
            ):
                _getter = _resolve_property_getter(
                    var_types[node.value.id], node.attr,
                    local_symbols, _sym_by_path_name,
                    property_getter_by_path_name,
                )
                if _getter is not None:
                    _prop_recv = var_types[node.value.id]
                    _prop_meta: dict[str, str] = {
                        "call_construct": "method",
                        "resolution_quality": "type_inferred",
                        "receiver_type_hint": _prop_recv.name,
                    }
                    # WI-supat (D3): stamp the concrete receiver-type id only when
                    # trustworthy (file-unique short name AND not import-shadowed),
                    # as in the method-call Site-2 producer. class_name_counts /
                    # imports / module_imports are closure-visible from
                    # _extract_edges.
                    if _receiver_type_id_trustworthy(
                        _prop_recv, class_name_counts, imports, module_imports,
                        local_symbols,
                    ):
                        _prop_meta["receiver_type_id"] = _prop_recv.id
                    edges.append(Edge.create(
                        src=caller_symbol.id,
                        dst=f"python:external:0-0:{node.attr}:unresolved",
                        edge_type="calls",
                        line=node.lineno,
                        evidence_type="ast_call",
                        is_resolved=False,
                        meta=_prop_meta,
                        origin=PASS_ID,
                        origin_run_id=run_id,
                    ))

            # Function references in dict values: {"GET": handle_get}
            if isinstance(node, ast.Dict):
                for val in node.values:
                    if isinstance(val, ast.Name):
                        _emit_function_ref(val, caller_symbol, stack=stack)

            # Function references in list/tuple: [func_a, func_b]
            if isinstance(node, (ast.List, ast.Tuple)):
                for elt in node.elts:
                    if isinstance(elt, ast.Name):
                        _emit_function_ref(elt, caller_symbol, stack=stack)

            # Recurse into child nodes (but not into nested function defs —
            # those get their own caller_symbol in the outer FunctionDef loop).
            for child in ast.iter_child_nodes(node):
                if not isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                    process_code_block(
                        [child], caller_symbol, var_types,
                        stack=stack,
                        external_var_types=external_var_types,
                    )

    def _extract_param_types(
        func_node: ast.FunctionDef | ast.AsyncFunctionDef,
    ) -> dict[str, Symbol]:
        """Extract type information from function parameter annotations.

        Handles simple annotations like:
        - def f(session: Session) -> session maps to Session class
        - def f(item: Item) -> item maps to Item class

        Does not currently handle:
        - Generic types: Optional[T], List[T], etc.
        - String annotations: "Session"
        """
        param_types: dict[str, Symbol] = {}

        for arg in func_node.args.args + func_node.args.kwonlyargs:
            if arg.annotation is None:
                continue

            param_name = arg.arg
            annotation = arg.annotation

            # Handle simple name annotations: param: ClassName
            if isinstance(annotation, ast.Name):
                type_name = annotation.id

                # Check local symbols first
                class_symbol = local_symbols.get(type_name)
                if class_symbol and class_symbol.kind == "class":
                    param_types[param_name] = class_symbol
                    continue

                # Check imports (with suffix matching)
                if type_name in imports:
                    module_name, original_name = imports[type_name]
                    class_symbol = _lookup_symbol_by_module(
                        global_symbols, module_name, original_name, resolver=resolver
                    )
                    if class_symbol and class_symbol.kind == "class":
                        param_types[param_name] = class_symbol

            # Handle attribute annotations: param: module.ClassName
            elif isinstance(annotation, ast.Attribute) and isinstance(
                annotation.value, ast.Name
            ):
                receiver_name = annotation.value.id
                attr_name = annotation.attr
                if receiver_name in module_imports:
                    module_name = module_imports[receiver_name]
                    class_symbol = _lookup_symbol_by_module(
                        global_symbols, module_name, attr_name, resolver=resolver
                    )
                    if class_symbol and class_symbol.kind == "class":
                        param_types[param_name] = class_symbol

        return param_types

    def _annotation_module_hint(annotation: ast.expr) -> str | None:
        """WI-zilag: the external module a parameter annotation names, or ``None``.

        The counterpart to :func:`_extract_param_types`, which resolves annotations to
        IN-REPO class symbols. This one answers the same question for types the repo does
        not define, so an annotated receiver reaches the I/O catalogue.

        BINDING-CHECKED, and that is the whole design rather than a precaution. A minted
        module hint is trusted downstream — it bypasses both ``gate_named_entry`` and the
        ``ambiguous_names`` net by design (gating the hinted path was measured to destroy
        61.5-87.2% of all reported boundaries for zero gain), so a wrong hint is a
        confident false boundary AND a false taint sink, never silence.

        Emitting the RAW annotation text instead was measured to produce confirmed false
        boundaries: ``conn: Connection`` matched ``sqlite3.Connection.execute`` and minted
        a database-zone taint sink, because ``_module_matches`` accepts an unqualified
        reference as a component suffix. Resolved through its import binding the same
        annotation yields ``sqlalchemy.engine.Connection``, which does not match.

        * ``ast.Name`` (``p: Path``) → whatever an import binds that name to, via
          :func:`_import_binding_for` — the same predicate INV-kipor's constructor gate
          uses. An unbound name is a builtin or a first-party class and returns ``None``.
        * ``ast.Attribute`` (``p: pathlib.Path``) → resolved only when the ROOT is a real
          module import, so an alias expands (``import pathlib as pl`` → ``pl.Path``
          becomes ``pathlib.Path``) and an unimported root is refused.
        * Everything else — ``Optional[X]`` / ``X | None`` (no single type), forward-
          reference strings, generics — returns ``None``. They cannot be pinned to one
          module, which is the same line ``taint_refine``'s WI-dozon pinning draws.
        """
        if isinstance(annotation, ast.Name):
            return _import_binding_for(annotation.id, imports, module_imports)
        if isinstance(annotation, ast.Attribute) and isinstance(
            annotation.value, ast.Name,
        ):
            root = module_imports.get(annotation.value.id)
            if root:
                return f"{root}.{annotation.attr}"
        return None

    def _extract_external_param_types(
        func_node: ast.FunctionDef | ast.AsyncFunctionDef,
        param_types: dict[str, Symbol],
    ) -> dict[str, str]:
        """WI-zilag: parameters whose annotation names an external catalogued type.

        Scoped to PARAMETERS deliberately. Measured over six Python repos: of 95,245
        method-call edges carrying the ``external`` placeholder, 4,160 have a receiver
        with a resolvable annotation and 63 actually reach the catalogue — and 96% of that
        payload is this one shape. ``AnnAssign``, return-annotated factories, attribute
        reads and generics contribute exactly zero, so routing them would add trust
        surface for no recall.

        ``param_types`` wins: a parameter already resolved to an in-repo class is
        first-party and carries no catalogue meaning, so an in-file ``class Path`` is not
        overridden by a same-named import.
        """
        external: dict[str, str] = {}
        for arg in func_node.args.args + func_node.args.kwonlyargs:
            if arg.annotation is None or arg.arg in param_types:
                continue
            hint = _annotation_module_hint(arg.annotation)
            if hint is not None:
                external[arg.arg] = hint
        return external

    def _resolve_decorator_target(
        decorator: ast.expr,
    ) -> Symbol | None:
        """Resolve the target of a decorator expression to a Symbol.

        Handles:
        - @decorator -> decorator function
        - @decorator(args) -> decorator function
        - @module.decorator -> module.decorator function
        - @app.get("/path") -> app.get method
        """
        # For Call decorators, extract the actual function being called
        # @decorator(args) or @app.get("/path")
        if isinstance(decorator, ast.Call):
            decorator = decorator.func

        # Simple name: @decorator or @dataclass
        if isinstance(decorator, ast.Name):
            name = decorator.id
            # Check local symbols
            symbol = local_symbols.get(name)
            if symbol:
                return symbol
            # Check imports (with suffix matching)
            if name in imports:
                module_name, original_name = imports[name]
                return _lookup_symbol_by_module(
                    global_symbols, module_name, original_name, resolver=resolver
                )

        # Attribute: @module.decorator or @app.get
        elif isinstance(decorator, ast.Attribute):
            if isinstance(decorator.value, ast.Name):
                receiver_name = decorator.value.id
                attr_name = decorator.attr

                # Check if receiver is a local class (e.g., @Registry.register)
                # Methods are stored with short name as key, so look up attr_name
                # and verify it's a method of the receiver class
                symbol = local_symbols.get(attr_name)
                if symbol and symbol.name == f"{receiver_name}.{attr_name}":
                    return symbol

                # Check if receiver is an imported module (with suffix matching)
                if receiver_name in module_imports:
                    module_name = module_imports[receiver_name]
                    return _lookup_symbol_by_module(
                        global_symbols, module_name, attr_name, resolver=resolver
                    )

        return None

    def _process_decorators(
        decorated_symbol: Symbol,
        decorator_list: list[ast.expr],
    ) -> None:
        """Create decorated_by edges for each decorator on a symbol."""
        for decorator in decorator_list:
            decorator_symbol = _resolve_decorator_target(decorator)

            # Get the line number from the decorator itself
            line = getattr(decorator, "lineno", 0)

            if decorator_symbol:
                edges.append(Edge.create(
                    src=decorated_symbol.id,
                    dst=decorator_symbol.id,
                    edge_type="decorated_by",
                    line=line,
                    evidence_type="ast_decorator",
                    origin=PASS_ID,
                    origin_run_id=run_id,
                ))
            else:
                # Emit unresolved edge for decorators we can't resolve
                # This helps track framework decorators like @app.get
                if isinstance(decorator, ast.Call):
                    dec_func = decorator.func
                else:
                    dec_func = decorator

                if isinstance(dec_func, ast.Attribute) and isinstance(
                    dec_func.value, ast.Name
                ):
                    receiver_name = dec_func.value.id
                    attr_name = dec_func.attr
                    dst_id = f"python:unresolved:0-0:{receiver_name}.{attr_name}:unresolved"
                    edges.append(Edge.create(
                        src=decorated_symbol.id,
                        dst=dst_id,
                        edge_type="decorated_by",
                        line=line,
                        evidence_type="ast_decorator",
                        is_resolved=False,
                        origin=PASS_ID,
                        origin_run_id=run_id,
                    ))

            # Check for Django signal receiver decorator: @receiver(signal, ...)
            # Creates signal_receiver edges from signal to handler
            _process_signal_receiver(decorated_symbol, decorator, line)

    def _process_signal_receiver(
        decorated_symbol: Symbol,
        decorator: ast.expr,
        line: int,
    ) -> None:
        """Create signal_receiver edges for Django @receiver decorators.

        When a function is decorated with @receiver(signal) or @receiver([sig1, sig2]),
        create signal_receiver edges from each signal to the decorated function.
        """
        # Must be a call: @receiver(signal, ...)
        if not isinstance(decorator, ast.Call):
            return

        # Check if decorator is "receiver"
        dec_func = decorator.func
        decorator_name = None
        if isinstance(dec_func, ast.Name):
            decorator_name = dec_func.id
        elif isinstance(dec_func, ast.Attribute):
            decorator_name = dec_func.attr

        if decorator_name != "receiver":
            return

        # Extract signals from first argument
        if not decorator.args:
            return

        first_arg = decorator.args[0]
        signal_nodes: list[ast.expr] = []

        # Handle @receiver([signal1, signal2])
        if isinstance(first_arg, ast.List):
            signal_nodes = first_arg.elts
        else:
            # Single signal: @receiver(post_save)
            signal_nodes = [first_arg]

        # Create signal_receiver edges for each signal
        for signal_node in signal_nodes:
            signal_symbol = None

            if isinstance(signal_node, ast.Name):
                signal_name = signal_node.id
                signal_symbol = local_symbols.get(signal_name)
                if not signal_symbol and signal_name in imports:
                    module_name, original_name = imports[signal_name]
                    signal_symbol = _lookup_symbol_by_module(
                        global_symbols, module_name, original_name, resolver=resolver
                    )

            if signal_symbol:
                edges.append(Edge.create(
                    src=signal_symbol.id,
                    dst=decorated_symbol.id,
                    edge_type="dispatches_to",
                    line=line,
                    evidence_type="ast_decorator",
                    meta={"framework_dispatch": "django_signal"},
                    origin=PASS_ID,
                    origin_run_id=run_id,
                ))
            elif isinstance(signal_node, ast.Name):
                # Unresolved signal - emit edge anyway for visibility
                dst_id = f"python:unresolved:0-0:{signal_node.id}:signal"
                edges.append(Edge.create(
                    src=dst_id,
                    dst=decorated_symbol.id,
                    edge_type="dispatches_to",
                    line=line,
                    evidence_type="ast_decorator",
                    is_resolved=False,
                    meta={"framework_dispatch": "django_signal"},
                    origin=PASS_ID,
                    origin_run_id=run_id,
                ))

    # Pre-collect class field types for self.field.method() resolution (INV-014).
    # Scans __init__ methods for self.field = param (typed) and self.field = Class()
    # assignments, building a per-class map of field name -> type Symbol.
    class_field_types: dict[str, dict[str, Symbol]] = {}
    # WI-hiziz PR-3 (review): the NAMES of ALL __init__ ``self.X`` targets per
    # class (typed or not), so the Site-3 emit can exclude an OWN field the
    # child assigns from a factory / untyped param (``self.f = make_conn()``) —
    # which ``class_field_types`` (typed-only) misses. An own field is never
    # inherited, so excluding it prevents a confidently-wrong Site-3 resolution
    # to a same-named PARENT field of a different type.
    class_own_field_names: dict[str, frozenset[str]] = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.ClassDef):
            continue
        init_method = None
        for item in node.body:
            if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)) and item.name == "__init__":
                init_method = item
                break
        if init_method is None:
            continue
        init_param_types = _extract_param_types(init_method)
        field_types: dict[str, Symbol] = {}
        own_field_names: set[str] = set()
        for stmt in ast.walk(init_method):
            # WI-sajub: scan both ``self.x = v`` (Assign) and ``self.x: T = v`` /
            # ``self.x: T`` (AnnAssign). The annotated form was previously skipped
            # entirely, so an annotated own field was captured neither into
            # own_field_names (leaving it eligible for a confidently-wrong Site-3
            # hint to a same-named PARENT field of a different type) nor into
            # field_types.
            if isinstance(stmt, ast.Assign):
                assign_targets: list[ast.expr] = list(stmt.targets)
                assign_value: ast.expr | None = stmt.value
            elif isinstance(stmt, ast.AnnAssign):
                assign_targets = [stmt.target]
                assign_value = stmt.value  # None for a bare ``self.x: T``
            else:
                continue
            for target in assign_targets:
                if (
                    isinstance(target, ast.Attribute)
                    and isinstance(target.value, ast.Name)
                    and target.value.id == "self"
                ):
                    field_name = target.attr
                    own_field_names.add(field_name)
                    # self.field = param where param has a type annotation
                    if isinstance(assign_value, ast.Name) and assign_value.id in init_param_types:
                        field_types[field_name] = init_param_types[assign_value.id]
                    # self.field = ClassName()
                    elif isinstance(assign_value, ast.Call):
                        assigned_class = _resolve_call_target(
                            assign_value, local_symbols, imports, global_symbols,
                            module_imports, resolver
                        )
                        if assigned_class and assigned_class.kind == "class":
                            field_types[field_name] = assigned_class
        if own_field_names:
            class_own_field_names[node.name] = frozenset(own_field_names)
        if field_types:
            class_field_types[node.name] = field_types
            # WI-hiziz PR-3 (Site 3): mirror java.py — attach
            # {field: type_short_name} to the class symbol's meta["fields"] so
            # inherited_calls._walk_parents_for_field can resolve a
            # self.field.method() where ``field`` is declared on a PARENT.
            # ``local_symbols`` IS the file's ``symbol_by_name``, so this mutates
            # the same Symbol object emitted in the node list (shared reference).
            # Only ADDS the "fields" key — a class's existing base_classes /
            # decorators meta survives. ``_ft.name`` is the type's full name,
            # matching the linker's ``class_ids_by_name`` keys.
            _cls_sym = local_symbols.get(node.name)
            # Only attach to a genuine class symbol: a same-name method/function
            # that shadows the class in the last-write-wins ``local_symbols`` must
            # not receive a spurious (inert) fields key (review finding). The
            # same-name-CLASS clobber (two classes, one short name → recall loss,
            # not a wrong edge) is a deferred id-keyed follow-up.
            if _cls_sym is None or _cls_sym.kind != "class":  # pragma: no cover
                continue
            if _cls_sym.meta is None:
                _cls_sym.meta = {}
            _cls_sym.meta["fields"] = {
                _fn: _ft.name for _fn, _ft in field_types.items()
            }
            # WI-supat (D3) PR-B: parallel {field: type_id} map so
            # inherited_calls Site-3 can disambiguate a same-short-name field
            # TYPE precisely instead of biasing to unresolved. Per-field gated by
            # the SAME trustworthiness check as receiver_type_id (file-unique type
            # name AND not import-shadowed — the field-type inference is the same
            # bare-name local-first resolution); an untrustworthy entry is omitted
            # so the linker keeps the safe field-type name+guard path. Only added
            # when at least one field type is trustworthy (a java/legacy parent
            # with no field_type_ids stays name-only, and the linker's
            # ``.get("field_type_ids") or {}`` tolerates its absence).
            _field_type_ids = {
                _fn: _ft.id for _fn, _ft in field_types.items()
                if _receiver_type_id_trustworthy(
                    _ft, class_name_counts, imports, module_imports,
                    local_symbols,
                )
            }
            if _field_type_ids:
                _cls_sym.meta["field_type_ids"] = _field_type_ids

    # WI-gulot: resolve module-level function aliases (`f = g` where g is a
    # function/method, incl. an imported g). The LHS is extracted as a
    # kind=variable node, so a call through the alias otherwise dead-ends at a
    # 0-out-degree variable and the target appears uncalled (a dispatch:F3 /
    # INV-pohik instance). This name->target map is consumed by _process_call to
    # resolve an alias call straight to the real body (so callers reach it and
    # the target is genuinely `calls`-reachable — the filed repro's expectation).
    # Scan MODULE-LEVEL statements only (``tree.body``), NOT ``ast.walk`` — a
    # function-local ``f = g`` must not pollute this module-scope map, else a
    # module variable of the same name would wrongly resolve to g (the LHS name
    # alone can't distinguish the two scopes). The call resolver's own
    # ``kind == "variable"`` guard means non-variable entries here are inert.
    function_aliases: dict[str, Symbol] = {}
    for _al_node in tree.body:
        if not (isinstance(_al_node, ast.Assign) and isinstance(_al_node.value, ast.Name)):
            continue
        _rhs_name = _al_node.value.id
        _alias_target = local_symbols.get(_rhs_name)
        if _alias_target is None and _rhs_name in imports:
            _mod, _orig = imports[_rhs_name]
            _alias_target = _lookup_symbol_by_module(
                global_symbols, _mod, _orig, resolver=resolver
            )
        if _alias_target is None or _alias_target.kind not in ("function", "method"):
            continue
        for _al_tgt in _al_node.targets:
            if isinstance(_al_tgt, ast.Name):
                function_aliases[_al_tgt.id] = _alias_target

    # WI-gubar (D2): whole-tree set of Attribute-node ids that are a call
    # callee, so the @property-read producer inside process_code_block skips
    # them (a method-call callee is handled by _process_call, not a read). ids
    # are unique per-parse, so one whole-tree set covers every function body
    # AND the module-level block, despite process_code_block recursing per-node.
    _call_func_attr_ids = _collect_call_func_attr_ids([tree])

    # Process functions (including async functions)
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            # INV-mofav + WI-jafat: every FunctionDef / AsyncFunctionDef walked
            # here is registered in func_symbol_by_node_id — plain functions
            # (top-level and nested) at the elif branch above, and methods via
            # CHANGE A — so the node-id lookup is authoritative. The bare-name
            # fallback below was the path methods took pre-CHANGE-A; it is now a
            # defensive backstop for any future node reaching this loop
            # unregistered, unreachable on the current producer (verified
            # 0/24152 funcdefs on self-analysis), mirroring the
            # func_symbol_by_node_id-None backstop above.
            caller_symbol = func_symbol_by_node_id.get(id(node))
            if caller_symbol is None:  # pragma: no cover - WI-jafat: all FunctionDefs are node-id-registered
                caller_symbol = local_symbols.get(node.name)
            if caller_symbol:
                # Process decorators on the function
                _process_decorators(caller_symbol, node.decorator_list)
                # Extract types from parameter annotations
                param_types = _extract_param_types(node)
                # Merge class field types for self.field.method() resolution
                if caller_symbol.kind == "method":
                    class_name = caller_symbol.name.split(".")[0]
                    if class_name in class_field_types:
                        for fname, fsym in class_field_types[class_name].items():
                            if fname not in param_types:
                                param_types[fname] = fsym
                # INV-mofav: each function's inner_scope contains its nested
                # function helpers, keyed by short name. The RAW dict is kept for
                # closure-factory dispatch (which must see only the caller's OWN
                # inner closures, never a grandparent's); call/reference
                # resolution uses the materialized LEGB stack (identity:F1/F4a).
                inner_scope = nested_by_parent_id.get(caller_symbol.id)
                stack = _build_scope_stack(
                    caller_symbol.id, enclosing_func_id, nested_by_parent_id,
                    local_names_by_func_id,
                )
                _emit_closure_factory_dispatch(node, caller_symbol, inner_scope)
                # WI-luhah gap 1c: add the enclosing-scope binding union so a
                # closure-captured enclosing param/local shadowing a module alias
                # suppresses the (otherwise confidently-wrong) retarget.
                _enclosing = _enclosing_shadow(caller_symbol.id)
                _emit_module_attr_refs(
                    node.body, caller_symbol,
                    local_bindings=_collect_local_bindings(
                        node, include_import_aliases=False
                    ) | _enclosing,
                )
                process_code_block(
                    node.body, caller_symbol, param_types, stack=stack,
                    external_var_types=_extract_external_param_types(
                        node, param_types,
                    ),
                )
                _emit_variable_refs(
                    node.body, caller_symbol,
                    local_bindings=_collect_local_bindings(node) | _enclosing,
                )

        # Process class decorators
        elif isinstance(node, ast.ClassDef):
            class_symbol = local_symbols.get(node.name)
            if class_symbol:
                _process_decorators(class_symbol, node.decorator_list)

    # Process module-level code for <module> pseudo-nodes
    module_symbol = local_symbols.get("<module>")
    if module_symbol:
        # Get top-level statements (excluding function/class defs)
        module_level_nodes = [
            node for node in tree.body
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
        ]
        # WI-luhah gap 2: guard the module-scope retarget with the module-level
        # reassignment set so a module-level `import config as cfg; cfg = ...`
        # rebind suppresses the (otherwise confidently-wrong) `cfg.attr` retarget.
        _emit_module_attr_refs(
            module_level_nodes, module_symbol,
            local_bindings=_collect_module_rebound_names(tree),
        )
        # A single module frame with EMPTY bindings: behaviorally identical to
        # stack=None for every resolution surface (immediate/enclosing lookups
        # return None), but its local_names carry the module-scope rebound names
        # so the WI-noham receiver_type_hint local-class guard also fires at
        # module scope (a module-level `X = f(); X.m()` shadowing class `X`).
        module_stack = ScopeStack(frames=[Scope(
            owner_id=module_symbol.id,
            bindings={},
            local_names=_collect_module_local_names(tree),
        )])
        process_code_block(module_level_nodes, module_symbol, stack=module_stack)
        # WI-luhah gap 2 (references sibling): a module-level import that rebinds
        # a same-named module VARIABLE shadows the bare-name read, so it must not
        # resolve to the variable.
        _emit_variable_refs(
            module_level_nodes, module_symbol,
            local_bindings=_collect_module_import_aliases(tree),
        )

    return edges


def _collect_scope_local_names(
    func_node: ast.FunctionDef | ast.AsyncFunctionDef,
) -> frozenset[str]:
    """Names bound as a param / assignment / import / ``global`` in *func_node*'s
    OWN body (nested function/class bodies excluded), minus ``nonlocal`` names.

    Feeds ``Scope.local_names`` so the scope-stack enclosing lookup honors LEGB
    local shadowing (identity:F1/F4a): a name in this set shadows any same-named
    def in a further-out scope (Python calls the local/global, not the enclosing
    def). ``def``/``class`` statement names are excluded — those are the
    ``NestedDef`` bindings the lookup resolves to directly. Distinct from the
    nested ``_collect_local_bindings`` (variable-reference shadow suppression,
    which does not treat ``global``/``nonlocal``).
    """
    names: set[str] = set()
    for arg in (
        func_node.args.args
        + func_node.args.posonlyargs
        + func_node.args.kwonlyargs
    ):
        names.add(arg.arg)
    if func_node.args.vararg:
        names.add(func_node.args.vararg.arg)
    if func_node.args.kwarg:
        names.add(func_node.args.kwarg.arg)
    bound, nonlocals = _collect_bound_names(list(ast.iter_child_nodes(func_node)))
    names |= bound
    return frozenset(names - nonlocals)


def _collect_bound_names(
    child_nodes: list[ast.AST],
) -> tuple[set[str], set[str]]:
    """Walk a scope's direct children, collecting names bound by assignment /
    import / ``global`` and, separately, ``nonlocal`` declarations, skipping
    nested function/class subtrees (their own scopes). Shared by
    ``_collect_scope_local_names`` (function scope, which additionally adds
    params) and ``_collect_module_local_names`` (module scope). Returns
    ``(bound, nonlocal)`` so each caller applies its own params/subtraction.
    """
    names: set[str] = set()
    nonlocals: set[str] = set()
    scope_boundary = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)

    def _walk(nodes: list[ast.AST]) -> None:
        for node in nodes:
            if isinstance(node, scope_boundary):
                # A nested function/class is its OWN scope — its locals (and its
                # def/class name) are not this scope's locals. Skip its subtree.
                continue
            if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store):
                names.add(node.id)
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    names.add(alias.asname or alias.name.split(".")[0])
            elif isinstance(node, ast.ImportFrom):
                for alias in node.names:
                    names.add(alias.asname or alias.name)
            elif isinstance(node, ast.Global):
                names.update(node.names)
            elif isinstance(node, ast.Nonlocal):
                nonlocals.update(node.names)
            for child in ast.iter_child_nodes(node):
                _walk([child])

    _walk(child_nodes)
    return names, nonlocals


def _collect_module_local_names(tree: ast.Module) -> frozenset[str]:
    """Module-scope analog of ``_collect_scope_local_names``: names REBOUND at
    module level (assignment targets / imports). Feeds the module frame's
    ``Scope.local_names`` so the WI-noham receiver_type_hint guard suppresses a
    hint when a module-level variable shadows a same-named class (the
    module-scope twin of the per-function local-shadow guard). ``global`` at
    module scope is a no-op and ``nonlocal`` is illegal there, so the nonlocal
    set is empty; ``def``/``class`` statement names are excluded (they are the
    genuine class/function symbols a hint legitimately points at).
    """
    bound, nonlocals = _collect_bound_names(list(ast.iter_child_nodes(tree)))
    return frozenset(bound - nonlocals)


def _collect_statement_binding_names(child_nodes: list[ast.AST]) -> frozenset[str]:
    """Names a scope binds by STATEMENT rather than by an assignment target.

    Three forms, all invisible to a ``ast.Name`` + ``ast.Store`` walk:

    - ``def X`` / ``class X`` — the statement binds ``X`` in the *enclosing*
      scope even though its body is a new one, so the name is collected and
      the body is not descended into.
    - ``except E as X`` — ``ExceptHandler.name`` is a bare ``str``, not a
      ``Name`` node.
    - ``X := ...`` — a walrus target binds in the enclosing scope, including
      when it sits inside a comprehension (which is why comprehensions are
      descended into here, unlike in the Store-walk that must skip them).

    This is deliberately NOT folded into ``_collect_scope_local_names``: that
    set feeds the LEGB *lookup*, which resolves ``def``/``class`` names to
    their ``NestedDef`` bindings directly and would break if they were
    treated as opaque locals. This is a *shadow* set — the distinct
    per-function collector WI-luhah's residual note called for. Every name it
    adds can only turn a retarget resolved -> phantom (a missed retarget),
    never phantom -> resolved, so it is INV-fahub-safe by construction.
    """
    names: set[str] = set()
    own_scopes = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)

    def _walk(nodes: list[ast.AST]) -> None:
        for node in nodes:
            if isinstance(node, own_scopes):
                names.add(node.name)  # binds HERE; its body is another scope
                continue
            if isinstance(node, ast.Lambda):
                continue  # own scope, binds no name in this one
            if isinstance(node, ast.ExceptHandler) and node.name:
                names.add(node.name)
            if isinstance(node, ast.NamedExpr) and isinstance(
                node.target, ast.Name,
            ):
                names.add(node.target.id)
            _walk(list(ast.iter_child_nodes(node)))

    _walk(child_nodes)
    return frozenset(names)


def _collect_module_rebound_names(tree: ast.Module) -> frozenset[str]:
    """Module-level names REASSIGNED via an assignment target (``ast.Store``),
    skipping nested function/class scopes.

    Feeds the module-scope ``module_attr_ref`` retarget shadow (WI-luhah gap 2 /
    INV-fahub): a module-level ``import config as cfg`` followed by ``cfg =
    make_cfg()`` rebinds the alias off its module, so a later ``cfg.CONFIG`` read
    must NOT retarget to the module ``CONFIG``. The module-scope caller otherwise
    passes EMPTY local_bindings, leaving that reassignment unguarded.

    Only reassignment targets are collected — plain ``import config as cfg``
    aliases are deliberately excluded (they are the resolvable alias, the
    load-bearing ``include_import_aliases=False`` distinction from
    ``_collect_local_bindings``; ``_collect_module_local_names`` includes imports
    and so cannot be reused). ``from``-imports need no handling: they populate
    ``symbol_imports`` not ``module_imports``, so a ``from``-imported name never
    reaches the module_attr_ref retarget gate.

    Nested function / class / comprehension / lambda scopes are skipped: a
    comprehension for-target (``[cfg for cfg in ...]``) is comprehension-local
    under Python-3 scoping and must NOT be treated as a module-scope rebind (it
    would over-suppress a valid ``cfg.attr`` retarget).

    The statement-bound forms — ``def cfg`` / ``class cfg`` / ``except E as
    cfg`` / a leaking walrus — bind the name without an ``ast.Store`` target,
    so they are collected by :func:`_collect_statement_binding_names` and
    unioned in. They were WI-luhah's documented pathological residual: each
    genuinely shadows the import alias, and leaving them out minted a
    confidently-wrong RESOLVED retarget to the module symbol.
    """
    own_scopes = (
        ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef,
        ast.Lambda, ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp,
    )
    names: set[str] = set()

    def _walk(nodes: list[ast.AST]) -> None:
        for node in nodes:
            if isinstance(node, own_scopes):
                continue  # nested function/class/comprehension/lambda: own scope
            if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store):
                names.add(node.id)
            for child in ast.iter_child_nodes(node):
                _walk([child])

    _walk(list(ast.iter_child_nodes(tree)))
    return frozenset(names) | _collect_statement_binding_names(
        list(ast.iter_child_nodes(tree)),
    )


def _collect_module_import_aliases(tree: ast.Module) -> frozenset[str]:
    """Module-level names bound by an ``import`` / ``from`` import, skipping
    nested function/class scopes.

    Feeds the module-scope ``references`` retarget shadow (WI-luhah gap 2, the
    ``_emit_variable_refs`` sibling / INV-fahub): a module-level ``import mod as
    X`` (or ``from pkg import mod as X``) rebinds the bare name ``X`` off a
    same-named module-level VARIABLE, so a bare ``X`` read must NOT resolve to
    that variable. This is the *opposite direction* from
    ``_collect_module_rebound_names`` (assignment targets, for the
    ``module_attr_ref`` sibling, where an assignment shadows an import alias):
    the variable-reference retarget's wrong case is an import shadowing a var.

    Flow-insensitive (like the function-scope ``_collect_local_bindings``): a
    name that is *both* an import alias and later reassigned to a value
    (``import os as x; x = f(); use(x)``) is shadowed unconditionally, so the
    read stays phantom instead of resolving to the reassigned variable. That is
    the INV-fahub-safe direction — the opposite choice (drop the shadow when the
    name is also assigned) would re-mint a confidently-wrong edge for the
    ``x = 1; import mod as x; use(x)`` order, which no flow-insensitive walk can
    distinguish.
    """
    scope_boundary = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)
    names: set[str] = set()

    def _walk(nodes: list[ast.AST]) -> None:
        for node in nodes:
            if isinstance(node, scope_boundary):
                continue  # nested scope: its imports are not module-level
            if isinstance(node, ast.Import):
                for alias in node.names:
                    names.add(alias.asname or alias.name.split(".")[0])
            elif isinstance(node, ast.ImportFrom):
                for alias in node.names:
                    names.add(alias.asname or alias.name)
            for child in ast.iter_child_nodes(node):
                _walk([child])

    _walk(list(ast.iter_child_nodes(tree)))
    return frozenset(names)


def _build_scope_stack(
    caller_id: str,
    enclosing_func_id: dict[str, str],
    nested_by_parent_id: dict[str, dict[str, Symbol]],
    local_names_by_func_id: dict[str, frozenset[str]],
) -> ScopeStack:
    """Materialize the caller's LEGB frame chain (identity:F1/F4a).

    Walks ``enclosing_func_id`` from the caller outward to the outermost
    enclosing function, then builds one :class:`Scope` frame per link
    (outermost-first, caller last). Each frame's bindings are the enclosing
    function's nested helpers (``nested_by_parent_id``) wrapped as
    :class:`NestedDef` — the only Binding variant produced in PR-0 — and its
    ``local_names`` carry that function's locally-bound names (for LEGB
    shadowing). A top-level caller yields a single-frame stack, so
    ``lookup_enclosing`` returns ``None`` for every name and resolution stays
    byte-identical to the pre-rewrite path.
    """
    chain = [caller_id]
    cur = caller_id
    while True:
        nxt = enclosing_func_id.get(cur)
        if nxt is None:
            break
        chain.append(nxt)
        cur = nxt
    chain.reverse()  # outermost-first, caller last
    frames = [
        Scope(
            owner_id=fid,
            bindings={
                name: NestedDef(sym)
                for name, sym in nested_by_parent_id.get(fid, {}).items()
            },
            local_names=local_names_by_func_id.get(fid, frozenset()),
        )
        for fid in chain
    ]
    return ScopeStack(frames=frames)


def _unwind_attribute_chain(
    node: ast.Attribute,
) -> tuple[ast.Name, list[str]] | None:
    """Walk an ``ast.Attribute`` chain back to its root ``ast.Name``.

    Given ``a.b.c.d`` — parsed as
    ``Attribute(Attribute(Attribute(Name('a'), 'b'), 'c'), 'd')`` — returns
    ``(Name('a'), ['b', 'c', 'd'])``. Attributes are returned root-to-leaf.

    Returns ``None`` when the chain's root is not an ``ast.Name`` (e.g.,
    ``f().x.y`` roots at a ``Call``, ``(a+b).c`` at a ``BinOp``). Those
    receivers don't participate in import-qualified call resolution and
    would be misresolved if we pretended they did.
    """
    attrs: list[str] = []
    current: ast.expr = node
    while isinstance(current, ast.Attribute):
        attrs.append(current.attr)
        current = current.value
    if not isinstance(current, ast.Name):
        return None
    attrs.reverse()
    return current, attrs


def _resolve_call_target(
    call_node: ast.Call,
    local_symbols: dict[str, Symbol],
    imports: dict[str, tuple[str, str]],
    global_symbols: dict[tuple[str, str], Symbol],
    module_imports: dict[str, str],
    resolver: "SymbolResolver | None" = None,
    inner_scope: dict[str, Symbol] | None = None,
) -> Symbol | None:
    """Resolve the target of a call expression to a Symbol.

    Handles:
    - ClassName() -> class symbol
    - module.ClassName() -> class symbol in module
    - imported_name() -> resolved symbol

    ``inner_scope`` is the enclosing-function scope (INV-mofav): when the
    bare name resolves to a nested function in the caller's body, it wins
    over a same-named top-level Symbol (Python LEGB rule).
    """
    func = call_node.func

    # Simple name: ClassName() or func()
    if isinstance(func, ast.Name):
        name = func.id
        # INV-mofav: enclosing-function scope wins over module scope.
        if inner_scope is not None:
            symbol = inner_scope.get(name)
            if symbol:
                return symbol
        # Check local symbols
        symbol = local_symbols.get(name)
        if symbol:
            return symbol
        # Check imports (with suffix matching)
        if name in imports:
            module_name, original_name = imports[name]
            return _lookup_symbol_by_module(
                global_symbols, module_name, original_name, resolver=resolver
            )

    # Attribute: module.ClassName() or obj.method()
    elif isinstance(func, ast.Attribute):
        if isinstance(func.value, ast.Name):
            receiver_name = func.value.id
            attr_name = func.attr

            # Check if receiver is an imported module (with suffix matching)
            if receiver_name in module_imports:
                module_name = module_imports[receiver_name]
                return _lookup_symbol_by_module(
                    global_symbols, module_name, attr_name, resolver=resolver
                )

    return None


def _derived_receiver_module(
    value: ast.expr,
    external_var_types: dict[str, str],
    ctor_type: Callable[[ast.Call], "str | None"] | None = None,
) -> str | None:
    """The type an expression yields when it DERIVES from an already-typed receiver.

    ``d / "f.txt"`` and ``d.joinpath("f.txt")`` return a ``pathlib.Path`` when ``d`` is
    one, so the derived value is as good a receiver as its root. PR #246 types the root
    from its annotation; without this the type is lost at the first derivation and
    ``p.write_text(x)`` degrades to the ``external`` placeholder.

    Recursive on the receiver, so a chain works whether it is written as one expression
    (``d / "a" / "b"``) or several statements. Only :data:`TYPE_PRESERVING_MEMBERS` rows
    propagate — see that table for why default-deny here is a measurement rather than a
    precaution, and why the type is compared by exact string.

    Returns ``None`` for every shape that is not an allowlisted derivation of a
    known-typed receiver, which includes numeric ``a / b`` (no hint on the root),
    ``os.path.join`` (a module function, not a receiver derivation), and every member
    that returns something other than the receiver's type.

    ``ctor_type`` resolves a CONSTRUCTOR CALL used as the chain's root —
    ``Path(raw) / "out.txt"`` rather than ``d / "out.txt"``. Measured at 82 of 294
    assign-from-``Path``-constructor sites (28%) across four repos, so it is a real
    share of the shape rather than a rounding error. It is a parameter instead of a
    direct call because the resolver needs this file's per-file import maps, which live
    in an enclosing scope; passing ``None`` keeps the pure-derivation behaviour for any
    caller that has no import context. The :data:`TYPE_PRESERVING_MEMBERS` allowlist
    still gates which members propagate, so widening the ROOT does not widen the
    propagation rule.
    """
    if isinstance(value, ast.BinOp) and isinstance(value.op, ast.Div):
        return _preserved_receiver_type(
            value.left, "__truediv__", external_var_types, ctor_type,
        )
    if isinstance(value, ast.Call) and isinstance(value.func, ast.Attribute):
        return _preserved_receiver_type(
            value.func.value, value.func.attr, external_var_types, ctor_type,
        )
    return None


def _external_constructor_type(
    call: ast.Call,
    imports: dict[str, tuple[str, str]],
    module_imports: dict[str, str],
) -> str | None:
    """WI-fuvuj: if ``call`` is a recognized I/O constructor, return the catalog
    module string for the object it constructs; else ``None``.

    - ``func`` is ``ast.Name`` (e.g. ``open``) → bare constructor name, trusted only
      once the name is confirmed to still mean what the table claims (INV-kipor).
    - ``func`` is ``ast.Attribute`` whose chain roots at a known module import
      (``socket.socket``, ``http.client.HTTPConnection``) → the module the root
      binds to, plus every intervening segment, plus the constructor name.

    WI-lifol: THE ATTRIBUTE BRANCH USED TO REQUIRE A BARE ``ast.Name`` BASE, which
    made a constructor under a DOTTED module structurally unreachable no matter
    what the table held — ``http.client.HTTPConnection(h)`` bases at an
    ``ast.Attribute`` (``http.client``), not a ``Name``. Table coverage and
    reachability are different claims, and the parity test asserting the former
    passed throughout. Generalizing via :func:`_unwind_attribute_chain` SUBSUMES
    the single-segment case rather than sitting beside it: ``socket.socket``
    unwinds to a one-element ``attrs`` and takes the same path, so there is one
    rule here and not two that can drift. Depth is not special-cased anywhere —
    a five-segment type added to the YAML tomorrow resolves without a code change.

    RESIDUAL, STATED RATHER THAN IMPLIED CLOSED: ``module_imports`` is built by an
    ``ast.walk`` over the WHOLE FILE, so it is file-scoped and cannot see a local
    binding that shadows an imported module name. A parameter named ``socket``
    already mistyped ``socket.socket(h)`` as the stdlib type before this change;
    unwinding widens that same exposure to depth >= 2 rather than introducing it.
    Pinned by an ``xfail(strict=True)`` in the receiver-type tests so a scope-aware
    fix goes RED instead of passing unnoticed.

    INV-kipor: the bare-name branch used to emit the catalogued module with no check
    at all, while the attribute branch beside it verified its base against
    ``module_imports``. So ``from decoy_lib import open`` still produced a ``file``
    receiver, and ``v.read()`` on it was reported as an ``fs_read`` boundary — a
    filesystem read invented for an object that is not a file, on the shipping tree.

    A binding to something OTHER than the claimed module withholds the hint; the edge
    then degrades to ``external`` and every consumer refuses it as an untyped method
    call. Unbound is trusted only for a genuine builtin — see
    :data:`BUILTIN_CONSTRUCTOR_NAMES` for why that is not "trust by default". The
    comparison is exact: ``pathlib.Path`` is trusted for ``from pathlib import Path``
    and refused for ``from fastapi import Path``. Aliased imports (``from pathlib
    import Path as P``) miss the table by key and so mint nothing — a false negative,
    in the safe direction.

    LIVES AT MODULE LEVEL because it has two consumers in different scopes: the
    per-block closure that types an ASSIGNMENT (``p = Path(raw)``) and
    :func:`_process_call`, which types a chain ROOT (``Path(raw).write_text(x)``).
    It took its import maps from an enclosing scope while it had one consumer; making
    them parameters is what lets the second consumer share the binding check instead
    of growing a second copy of it.
    """
    func = call.func
    if isinstance(func, ast.Name):
        claimed = EXTERNAL_CONSTRUCTOR_TYPES.get(func.id)
        if claimed is None:
            return None
        bound = _import_binding_for(func.id, imports, module_imports)
        if bound is None:
            return claimed if func.id in BUILTIN_CONSTRUCTOR_NAMES else None
        return claimed if bound == claimed else None
    if isinstance(func, ast.Attribute):
        chain = _unwind_attribute_chain(func)
        if chain is None:
            return None
        root, attrs = chain
        if root.id not in module_imports:
            return None
        module = ".".join([module_imports[root.id], *attrs[:-1]])
        return EXTERNAL_CONSTRUCTOR_TYPES.get(f"{module}.{attrs[-1]}")
    return None


def _receiver_type(
    receiver: ast.expr,
    external_var_types: dict[str, str],
    ctor_type: Callable[[ast.Call], "str | None"] | None = None,
) -> str | None:
    """THE single answer to "what external type does this receiver expression have?".

    Three shapes carry a type, and this is the only place that enumerates them:

    * a bare ``ast.Name`` the tracker already typed (``p`` after ``p = Path(raw)``);
    * an ``ast.Call`` that IS a recognized constructor (``Path(raw)``) — the chain
      ROOT, resolved through ``ctor_type`` so it inherits that resolver's INV-kipor
      binding check rather than re-implementing it; and
    * anything :func:`_derived_receiver_module` recognizes as an allowlisted
      DERIVATION of one of the above (``d / "f"``, ``d.joinpath("f")``).

    IT EXISTS BECAUSE THE ANSWER WAS BEING GIVEN IN TWO PLACES. This computation
    lived privately inside :func:`_preserved_receiver_type`, so the emission site
    could only ask the narrower "what does this DERIVATION preserve?" question and
    a chain rooted directly at a constructor — ``Path(raw).write_text(x)``, which
    is not a derivation of anything — matched no branch and emitted no call edge at
    all. Splitting the question out is what lets both callers ask the right one.
    Keeping it in ONE function is deliberate: four separate answers to "may I trust
    this callee?" have already drifted in this area, with a docstring asserting a
    parity that did not hold, so a second copy here is the failure mode rather than
    a convenience. The behavioural parity test is
    ``TestTheOneReceiverTypePredicate::test_inline_and_assigned_forms_agree``, which
    compares the inline and assigned forms of the same shape — a grep-for-the-call
    test would be satisfiable by a third copy that merely looks right.

    ``ctor_type`` is a parameter rather than a direct call because the resolver needs
    this file's per-file import maps, which live in an enclosing scope. Passing
    ``None`` keeps the pure-derivation behaviour for any caller with no import
    context, which is why widening the root cannot widen anything for such a caller.
    """
    if isinstance(receiver, ast.Name):
        return external_var_types.get(receiver.id)
    if isinstance(receiver, ast.Call) and ctor_type is not None:
        # A constructor call as the chain's root. ``ctor_type`` carries the same
        # binding check every other constructor row goes through, so a locally
        # defined ``class Path`` is refused here exactly as it is at an assignment.
        # Falling through to the derivation resolver keeps ``Path(x).joinpath("y")``
        # working, where the root is a call but not itself a constructor.
        return ctor_type(receiver) or _derived_receiver_module(
            receiver, external_var_types, ctor_type,
        )
    return _derived_receiver_module(receiver, external_var_types, ctor_type)


def _preserved_receiver_type(
    receiver: ast.expr,
    member: str,
    external_var_types: dict[str, str],
    ctor_type: Callable[[ast.Call], "str | None"] | None = None,
) -> str | None:
    """``member``'s return type when invoked on ``receiver``, if it is the same type.

    The type question is delegated to :func:`_receiver_type`; what stays here is the
    PROPAGATION rule, which is a separate decision — widening which receivers carry a
    type must not widen which members preserve it, so :data:`TYPE_PRESERVING_MEMBERS`
    gates this half and nothing else.
    """
    hint = _receiver_type(receiver, external_var_types, ctor_type)
    if hint is None:
        return None
    return hint if member in TYPE_PRESERVING_MEMBERS.get(hint, ()) else None


def _import_binding_for(
    name: str,
    imports: dict[str, tuple[str, str]],
    module_imports: dict[str, str],
) -> str | None:
    """The dotted path ``name`` is import-bound to in this file, else ``None``.

    The single answer to "does an import in this file rebind this bare name, and
    to what?" — the question both bare-name inferences here must ask before
    treating an identifier as evidence: the WI-supat D3 receiver-type guard
    (:func:`_receiver_type_id_trustworthy`) and the WI-fuvuj external-constructor
    inference (``_external_constructor_module``). The two asked it separately and
    one of them simply didn't (INV-kipor), which is the drift this consolidates.

    ``None`` means *unbound*, which for a bare name means the builtin — that is
    what makes plain ``open(p)`` still infer a file receiver.

    WHY CALLERS COMPARE THE RESULT BY EQUALITY AND NOT VIA
    :func:`io_boundary._module_matches`. That predicate answers a deliberately
    permissive question — "could this module *hint* refer to the catalogued
    module?" — and so accepts an unqualified reference as a component suffix of a
    qualified name. Measured, it returns True for ``mylib.pathlib.Path`` against
    ``pathlib.Path`` and for ``mypkg.file`` against ``file``, i.e. it trusts
    exactly the vendored/shadowed bindings a binding check exists to refuse.
    Binding identity is a different question from hint compatibility, so it gets
    its own comparison rather than reusing one whose permissiveness is a feature
    elsewhere.
    """
    binding = imports.get(name)
    if binding is not None:
        module, original = binding
        return f"{module}.{original}" if module else original
    return module_imports.get(name)


def _receiver_type_id_trustworthy(
    recv_sym: Symbol,
    class_name_counts: dict[str, int],
    imports: dict[str, tuple[str, str]],
    module_imports: dict[str, str],
    local_symbols: dict[str, Symbol],
) -> bool:
    """WI-supat (D3): whether a concrete receiver-type id is safe to stamp.

    The Site-2 receiver-type inference is bare-name-based (last-write-wins
    ``symbol_by_name`` / local-first annotation resolution), so a concrete id is
    only trustworthy under BOTH conditions:

    1. **File-unique short name** — with >=2 same-short-name ``ClassDef``s in the
       file the inference could have hit the wrong twin (``class_name_counts``).
    2. **Not import-shadowed** — when the resolved type IS the in-file class of
       that name (``local_symbols.get(name) is recv_sym``) but a same-name import
       exists (``name in imports``/``module_imports``), a later
       ``from x import Name`` rebinds the name at runtime (Python last-binding-
       wins), so the local-first-resolved type is the WRONG class. This is
       *precise*, not blanket: a correctly cross-file-resolved imported type
       (``recv_sym`` is NOT the local symbol) keeps its id, preserving the
       cross-file collision-recovery this feature exists for.

    When it returns ``False`` the producer omits the id and the linker falls back
    to the safe name+ambiguity-guard path (biases to unresolved on a collision).
    """
    name = recv_sym.name
    if class_name_counts.get(name, 0) > 1:
        return False
    if (
        local_symbols.get(name) is recv_sym
        and _import_binding_for(name, imports, module_imports) is not None
    ):
        return False
    return True


def _class_directly_extends_django_model(
    class_short_name: str,
    local_symbols: dict[str, Symbol],
) -> bool:
    """WI-sozoj: True iff the named class DIRECTLY subclasses django ``models.Model``.

    Gates the ORM instance-write re-key (``self.save()``/``self.delete()`` →
    db_write). Deliberately DIRECT-base and dotted-form only
    (``class Order(models.Model)`` / ``class Order(django.db.models.Model)``) —
    the unambiguous Django idiom. A transitive base
    (``Order(LoggedModel)``, ``LoggedModel(models.Model)``) or a bare ``Model``
    base degrades to invisible: INV-tapat precision-safe, a missed ORM write
    rather than a mis-tagged non-ORM call. The reachability linker
    (``django_orm_dispatch``) owns the transitive / short-name-collision case for
    its orthogonal ``dispatches_to`` concern; io classification stays narrow here.

    Reads ``base_classes`` off the file-global class symbol (the same metadata
    the inheritance linker consumes); returns False for an unknown name, a
    non-class symbol, or a class without base metadata.
    """
    sym = local_symbols.get(class_short_name)
    if sym is None or sym.kind != "class" or not sym.meta:
        return False
    bases = sym.meta.get("base_classes") or []
    return any(base in DJANGO_MODEL_BASES for base in bases)


# Where the mode argument sits for each dual-classified primitive, as
# ``short name -> (positional index, keyword name)``.
#
# WHY A TABLE AND NOT A CATALOGUE FIELD. Which names NEED discrimination is
# derived from the catalogue (``mode_discriminated_names``) so the data stays
# the single source of truth. Where the argument SITS is Python call-signature
# knowledge, which belongs with the Python analyzer — a catalogue shared across
# fourteen languages is the wrong home for "``open``'s mode is positional
# argument 1". ``TestCatalogueParity`` enforces that this table covers every
# name the live catalogue flags, so the two cannot drift apart silently.
_MODE_ARG_POSITION: dict[str, tuple[int, str]] = {
    "open": (1, "mode"),
    # 2026-08-15 stdlib climb: the archive/descriptor constructors are
    # dual-classified like open and put mode in the same seat —
    # GzipFile(filename, mode), TarFile(name, mode), ZipFile(file, mode),
    # FileIO(file, mode). gzip.open / tarfile.open share the "open" entry.
    "GzipFile": (1, "mode"),
    "TarFile": (1, "mode"),
    "ZipFile": (1, "mode"),
    "FileIO": (1, "mode"),
}


def _io_mode_literal(call_node: ast.Call) -> str | None:
    """The mode string of an ``open``-style call, when statically knowable.

    Returns ``None`` for a computed mode (``open(p, m)``) and for an absent
    one (``open(p)``). Both are recorded as absence rather than guessed:
    :func:`hypergumbo_core.io_boundary.resolve_mode_boundary` applies the
    language default, and inventing ``"w"`` on suspicion would rebuild the
    false-positive population this machinery exists to remove.
    """
    func = call_node.func
    short = (
        func.id if isinstance(func, ast.Name)
        else func.attr if isinstance(func, ast.Attribute)
        else None
    )
    spec = _MODE_ARG_POSITION.get(short or "")
    if spec is None:
        return None
    position, keyword = spec
    for kw in call_node.keywords:
        if kw.arg == keyword:
            value = kw.value
            if isinstance(value, ast.Constant) and isinstance(value.value, str):
                return value.value
            return None
    if len(call_node.args) > position:
        arg = call_node.args[position]
        if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
            return arg.value
    return None


def _stamp_io_mode(
    edges: list[Edge], first_new: int, call_node: ast.Call,
) -> None:
    """Record the call's mode literal on the edges that call just produced.

    Applied ONCE, at ``_process_call``'s single invocation, over
    ``edges[first_new:]`` — rather than at each of the nine ``Edge.create``
    sites inside it, which is the shape that drifts. Keyed on the exact
    ``ast.Call`` node, not on a line number: ADR-0038's retired classifier was
    line-granular and that is exactly why it mis-stamped.
    """
    mode = _io_mode_literal(call_node)
    if mode is None:
        return
    for edge in edges[first_new:]:
        if edge.meta is None:
            edge.meta = {}
        edge.meta["io_mode"] = mode


def _process_call(
    call_node: ast.Call,
    caller_symbol: Symbol,
    local_symbols: dict[str, Symbol],
    imports: dict[str, tuple[str, str]],
    global_symbols: dict[tuple[str, str], Symbol],
    module_imports: dict[str, str],
    var_types: dict[str, Symbol],
    edges: list[Edge],
    resolver: "SymbolResolver | None" = None,
    sym_by_path_name: dict[tuple[str, str], Symbol] | None = None,
    *,
    run_id: str,
    stack: ScopeStack | None = None,
    external_var_types: dict[str, str] | None = None,
    function_aliases: dict[str, Symbol] | None = None,
    own_field_names: frozenset[str] = frozenset(),
    method_to_enclosing_class_id: dict[str, str] | None = None,
    class_name_counts: dict[str, int] | None = None,
) -> None:
    """Process a single call expression and emit appropriate edges.

    Handles:
    - Direct calls: helper(), ClassName()
    - Self method calls: self.method()
    - Self field method calls: self.field.method() (using field type inference)
    - Module-qualified calls: module.ClassName(), module.func()
    - Variable method calls: stub.method() (using var_types for type inference)

    ``stack`` is the caller's materialized LEGB frame chain (identity:F1/F4a):
    bare-name calls resolve through its immediate frame (INV-mofav) before
    ``local_symbols``/imports, then via a last-resort enclosing-scope lookup.

    ``external_var_types`` (WI-fuvuj) maps a local variable name to the
    catalog module string of the I/O object it was constructed from
    (``f = open(p)`` → ``{"f": "file"}``). When a bare ``receiver.method()``
    call's receiver is in this map, the unresolved-edge emit uses a
    module-qualified dst (carrying the inferred module in both the dst id's
    module slot and a structured ``dst_ref``) so io-boundary can classify it.
    """
    if external_var_types is None:  # pragma: no cover - defensive default
        external_var_types = {}
    if method_to_enclosing_class_id is None:  # pragma: no cover - defensive default
        method_to_enclosing_class_id = {}
    if class_name_counts is None:  # pragma: no cover - defensive default
        class_name_counts = {}

    def _ctor_type_here(call: ast.Call) -> str | None:
        """This call site's import maps, bound to the shared constructor resolver.

        Routing through :func:`_external_constructor_type` rather than re-deciding
        here is what keeps the INV-kipor binding check identical for a chain ROOT
        (``Path(raw).write_text(x)``) and an assignment (``p = Path(raw)``) — the
        two shapes are typed in different scopes and must not drift apart.
        """
        return _external_constructor_type(call, imports, module_imports)

    func = call_node.func
    callee_symbol = None
    is_instantiation = False
    evidence_type = "ast_call_direct"
    call_meta: dict[str, str] | None = None

    # Case 1: Simple name calls - helper() or ClassName()
    if isinstance(func, ast.Name):
        callee_name = func.id
        # INV-mofav: enclosing-function scope wins over module scope (step 1-2).
        callee_symbol = stack.lookup_immediate(callee_name) if stack else None
        if callee_symbol is None:
            callee_symbol = local_symbols.get(callee_name)
        # WI-gulot: a module-level `f = g` function alias resolves as a variable;
        # chase it to the aliased function so the call reaches the real body
        # (else it dead-ends at the 0-out-degree variable node).
        if (
            callee_symbol is not None
            and callee_symbol.kind == "variable"
            and function_aliases
            and callee_name in function_aliases
        ):
            callee_symbol = function_aliases[callee_name]

        if callee_symbol and callee_symbol.kind == "class":
            is_instantiation = True
        elif not callee_symbol and callee_name in imports:
            module_name, original_name = imports[callee_name]
            callee_symbol = _lookup_symbol_by_module(
                global_symbols, module_name, original_name, resolver=resolver
            )
            if callee_symbol and callee_symbol.kind == "class":
                is_instantiation = True

        # identity:F1/F4a step-4: last-resort enclosing-scope lookup — resolves a
        # bare call to a helper defined in a GRANDPARENT (or higher) enclosing
        # function that the flat immediate frame missed. Additive: fires only
        # when nothing above resolved, and returns only nested FUNCTIONS (never a
        # class), so is_instantiation stays False and no existing edge changes.
        if callee_symbol is None and stack is not None:
            callee_symbol = stack.lookup_enclosing(callee_name)

    # Case 2: Attribute calls - self.method(), module.ClassName(), variable.method()
    elif isinstance(func, ast.Attribute):
        attr_name = func.attr
        # Cluster 28D (audit-findings 0012): a method call folds to the
        # ``ast_call`` apex + ``meta['call_construct']='method'`` (WI-nibis),
        # not the parked peer ``ast_call_method``.
        evidence_type = "ast_call"
        call_meta = {"call_construct": "method"}

        if isinstance(func.value, ast.Name):
            receiver_name = func.value.id

            # Case 2a: self.method()
            if receiver_name == "self":
                callee_symbol = local_symbols.get(attr_name)

            # Case 2b: module.ClassName() or module.func()
            elif receiver_name in module_imports:
                module_name = module_imports[receiver_name]
                callee_symbol = _lookup_symbol_by_module(
                    global_symbols, module_name, attr_name, resolver=resolver
                )
                if callee_symbol and callee_symbol.kind == "class":
                    is_instantiation = True

            # Case 2c: variable.method() - use type inference
            elif receiver_name in var_types:
                class_symbol = var_types[receiver_name]
                # Look for ClassName.method in local symbols
                qualified_name = f"{class_symbol.name}.{attr_name}"
                callee_symbol = local_symbols.get(qualified_name)
                # If not found locally, try global symbols via index
                if not callee_symbol and sym_by_path_name is not None:
                    callee_symbol = sym_by_path_name.get(
                        (class_symbol.path, qualified_name)
                    )

            # Case 2d: Imported class method calls - Item.model_validate()
            # When Item is imported via "from app.models import Item"
            elif receiver_name in imports:
                module_name, original_name = imports[receiver_name]
                class_symbol = _lookup_symbol_by_module(
                    global_symbols, module_name, original_name, resolver=resolver
                )
                if class_symbol and class_symbol.kind == "class":
                    # Look for ClassName.method (class method/static method)
                    qualified_name = f"{original_name}.{attr_name}"
                    if sym_by_path_name is not None:
                        callee_symbol = sym_by_path_name.get(
                            (class_symbol.path, qualified_name)
                        )

                # Case 2e: Imported submodule calls - crud.create_user()
                # When crud is imported via "from app import crud" (crud is a module)
                # and we call crud.create_user(), we need to look up (app.crud, create_user)
                if not callee_symbol:
                    submodule_name = f"{module_name}.{original_name}"
                    callee_symbol = _lookup_symbol_by_module(
                        global_symbols, submodule_name, attr_name, resolver=resolver
                    )

        # Case 2f: self.field.method() - call on injected dependency (INV-014)
        # Pattern: self.svc.process() where self.svc was assigned from a typed param
        # or constructor call in __init__. Field types are pre-loaded into var_types.
        elif (
            isinstance(func.value, ast.Attribute)
            and isinstance(func.value.value, ast.Name)
            and func.value.value.id == "self"
        ):
            field_name = func.value.attr
            if field_name in var_types:
                class_symbol = var_types[field_name]
                qualified_name = f"{class_symbol.name}.{attr_name}"
                callee_symbol = local_symbols.get(qualified_name)
                if not callee_symbol and sym_by_path_name is not None:
                    callee_symbol = sym_by_path_name.get(
                        (class_symbol.path, qualified_name)
                    )

    # Emit edge if we resolved the callee
    if callee_symbol:
        if is_instantiation:
            edges.append(Edge.create(
                src=caller_symbol.id,
                dst=callee_symbol.id,
                edge_type="instantiates",
                line=call_node.lineno,
                evidence_type="ast_new",
                origin=PASS_ID,
                origin_run_id=run_id,
            ))
        else:
            edges.append(Edge.create(
                src=caller_symbol.id,
                dst=callee_symbol.id,
                edge_type="calls",
                line=call_node.lineno,
                evidence_type=evidence_type,
                meta=call_meta,
                origin=PASS_ID,
                origin_run_id=run_id,
            ))
    else:
        # Emit unresolved edge for attribute calls with known module context
        # This enables cross-language linking and makes the graph more complete
        func = call_node.func
        # Hoisted (WI-hiziz PR-3): the caller's scope-local names + decorator
        # names are the shared INV-fahub guard inputs for BOTH the Site-1
        # self.method() branch and the Site-3 self.field.method() branch below.
        _caller_locals = (
            stack.frames[-1].local_names
            if stack is not None and stack.frames
            else frozenset()
        )
        _caller_decos = {
            d.get("name")
            for d in (caller_symbol.meta or {}).get("decorators", [])
            if isinstance(d, dict)
        }
        # WI-sozoj: Django ORM Manager/QuerySet query dispatch —
        # ``<Model>.objects.<method>()``. The ``.objects`` attribute is Django's
        # Manager-descriptor convention: a near-unique, type-verifying marker
        # (a bare ``.filter()``/``.get()`` collides with dict/cache methods, but
        # ``<x>.objects.filter()`` does not). Emit a ``django.db.models``
        # module-qualified dst so io-boundary's module-filter path (never the
        # short-name gate) classifies each method db_read/db_write via
        # python.yaml. Bounded to the closed ORM method set, so a stray
        # non-Django ``.objects.x()`` stays invisible. This chained receiver
        # (``func.value`` is itself an ``ast.Attribute``) emits no edge in any
        # branch below (measured), so this is net-new emission, not a re-key.
        if (
            isinstance(func, ast.Attribute)
            and isinstance(func.value, ast.Attribute)
            and func.value.attr == "objects"
            and func.attr in DJANGO_ORM_MANAGER_METHODS
        ):
            _orm_method = func.attr
            edges.append(Edge.create(
                src=caller_symbol.id,
                dst=f"python:{DJANGO_ORM_MODULE}:0-0:{_orm_method}:unresolved",
                edge_type="calls",
                line=call_node.lineno,
                evidence_type="ast_call",
                is_resolved=False,
                meta={
                    "call_construct": "method",
                    "framework_dispatch": "django_orm",
                    "resolution_quality": "type_inferred",
                },
                dst_ref=ExternalRef(
                    lang="python", module_path=DJANGO_ORM_MODULE, name=_orm_method
                ),
                origin=PASS_ID,
                origin_run_id=run_id,
            ))
        elif (
            isinstance(func, ast.Attribute)
            and not isinstance(func.value, ast.Name)
            and _receiver_type(
                func.value, external_var_types, _ctor_type_here,
            ) is not None
        ):
            # WI-zilag: an INLINE expression receiver — ``(d / "f").write_text(x)``,
            # ``d.joinpath("f").write_text(x)``. PR #247 taught derivations to keep
            # their type but entered only from an assignment, and the typed-emit
            # branch below is gated on a bare ``ast.Name`` receiver, so an expression
            # receiver never reached it. Same resolver, so the allowlist and the
            # exact-string type comparison apply identically — an expression receiver
            # is not a back door around either.
            #
            # The CONSTRUCTOR ROOT — ``Path(raw).write_text(x)``, ``open(p,"w")
            # .write(x)`` — arrives here too, via ``_receiver_type``. PR #253 taught
            # the resolver that a constructor call carries a type but wired it only
            # into the two ASSIGNMENT sites, so the identical I/O written inline
            # emitted no call edge at all and tagged zero boundaries while the
            # assigned form tagged one. Passing ``_external_constructor_module`` is
            # what closes that: the root inherits its binding check, so a local
            # ``class Path`` and a ``from decoy_lib import Path`` are refused here
            # exactly as they are at an assignment. Measured across seven repos this
            # types 269 of 27,354 call-result-root sites (~1%) and reaches the
            # catalogue on 128 — the rest are receivers of genuinely unknown type,
            # and emitting an untyped edge for those is what PR #231 measured at
            # zero moved findings.
            ext_module = _receiver_type(
                func.value, external_var_types, _ctor_type_here,
            )
            edges.append(Edge.create(
                src=caller_symbol.id,
                dst=f"python:{ext_module}:0-0:{func.attr}:unresolved",
                edge_type="calls",
                line=call_node.lineno,
                evidence_type="ast_call",
                is_resolved=False,
                meta={
                    "call_construct": "method",
                    "resolution_quality": "type_inferred",
                },
                dst_ref=ExternalRef(
                    lang="python", module_path=ext_module or "", name=func.attr,
                ),
                origin=PASS_ID,
                origin_run_id=run_id,
            ))
        elif isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name):
            receiver_name = func.value.id
            attr_name = func.attr

            # Case: module.func() where module is imported but func not found
            if receiver_name in module_imports:
                module_name = module_imports[receiver_name]
                dst_id = f"python:{module_name}:0-0:{attr_name}:unresolved"
                # WI-jubag (instantiates half): module.ClassName() where the member
                # is PascalCase is an external construction (argparse.ArgumentParser,
                # …) — type it ``instantiates`` (``ast_new``, no call_construct meta);
                # snake_case module functions (os.getcwd) stay ``calls``.
                _is_ctor = attr_name[:1].isupper()
                edges.append(Edge.create(
                    src=caller_symbol.id,
                    dst=dst_id,
                    edge_type="instantiates" if _is_ctor else "calls",
                    line=call_node.lineno,
                    evidence_type="ast_new" if _is_ctor else "ast_call",
                    is_resolved=False,
                    meta=None if _is_ctor else {"call_construct": "method"},
                    dst_ref=ExternalRef(
                        lang="python", module_path=module_name, name=attr_name
                    ),
                    origin=PASS_ID,
                    origin_run_id=run_id,
                ))
            # Case: imported_name.method() where imported_name not resolved
            #
            # WI-hotug: the ``receiver_name not in _caller_locals`` guard is
            # LOAD-BEARING (INV-fahub — mirrors the sibling terminal-else
            # ``elif receiver_name not in _caller_locals`` below and
            # ``_emit_variable_refs``). ``imports`` is the FILE-level from-import
            # map, NOT scope-narrowed, so a function PARAMETER or LOCAL rebind that
            # shadows a same-named imported constant (``def h(settings): ...`` over
            # ``from x import settings``; ``CONFIG = raw`` over
            # ``from x import CONFIG``) would otherwise enter this branch and mint
            # a confidently-wrong RESOLVED ``references``→the-module-constant edge
            # (the receiver is the local, not the import — an under-determined
            # bind). Excluding a bound local routes it to the honest generic
            # ``python:external:0-0:<attr>:unresolved`` terminal else.
            elif receiver_name in imports and receiver_name not in _caller_locals:
                module_name, original_name = imports[receiver_name]
                # WI-hotug (CASE B / INV-nuzas): the receiver may be an in-tree
                # module-level VARIABLE imported via ``from x import CONST`` (a
                # dict/list/regex/cache/instance constant) with a BUILTIN method
                # call on it (``CONST.items()/.get()/.match()``). ``.items`` etc.
                # are builtins with no in-tree target, but the RECEIVER is a real
                # first-party symbol — emit a ``references`` edge to the in-tree
                # variable (the caller USES the constant) rather than minting a
                # workspace-prefixed phantom ``external_symbol`` (the INV-nuzas
                # acceptance-property violation). The lookup is import-anchored on
                # the concrete ``(module, original_name)`` binding with
                # ``allow_ambiguous=False`` (biases to unresolved on a same-name
                # collision — INV-fahub); a class receiver (handled by Case 2d
                # above), a submodule receiver, or a None/external lookup all fall
                # through to the unchanged phantom-external emit below.
                _recv_var = _lookup_symbol_by_module(
                    global_symbols, module_name, original_name, resolver=resolver
                )
                if _recv_var is not None and _recv_var.kind == "variable":
                    edges.append(Edge.create(
                        src=caller_symbol.id,
                        dst=_recv_var.id,
                        edge_type="references",
                        line=call_node.lineno,
                        evidence_type="ast_name_read",
                        origin=PASS_ID,
                        origin_run_id=run_id,
                    ))
                else:
                    dst_id = f"python:{module_name}:0-0:{original_name}.{attr_name}:unresolved"
                    edges.append(Edge.create(
                        src=caller_symbol.id,
                        dst=dst_id,
                        edge_type="calls",
                        line=call_node.lineno,
                        evidence_type="ast_call",
                        is_resolved=False,
                        meta={"call_construct": "method"},
                        dst_ref=ExternalRef(
                            lang="python",
                            module_path=module_name,
                            name=f"{original_name}.{attr_name}",
                        ),
                        origin=PASS_ID,
                        origin_run_id=run_id,
                    ))
            # WI-fuvuj: local_var.method() where the receiver was typed by a
            # known I/O constructor (``f = open(p)`` / ``s = socket.socket()``,
            # incl. the ``with ... as`` form). Emit a MODULE-QUALIFIED dst so
            # io-boundary's catalog can disambiguate the method (e.g. file
            # .read() → fs_read, socket.socket.send() → net_send) via the
            # module-filter path — bypassing the ambiguous_names suppression
            # that protects UNtyped receivers. The module is carried in BOTH
            # the dst id's module slot AND the dst_ref because the io-boundary
            # CLI consumer drops dst_ref on serialize/reload and falls back to
            # parsing the dst id.
            elif receiver_name in external_var_types:
                ext_module = external_var_types[receiver_name]
                dst_id = f"python:{ext_module}:0-0:{attr_name}:unresolved"
                edges.append(Edge.create(
                    src=caller_symbol.id,
                    dst=dst_id,
                    edge_type="calls",
                    line=call_node.lineno,
                    evidence_type="ast_call",
                    is_resolved=False,
                    meta={"call_construct": "method", "resolution_quality": "type_inferred"},
                    dst_ref=ExternalRef(
                        lang="python", module_path=ext_module, name=attr_name
                    ),
                    origin=PASS_ID,
                    origin_run_id=run_id,
                ))
            # Case: local_var.method() where type cannot be inferred.
            # Emit unresolved edge using the attribute name so that IO
            # boundary analysis and taint-flow analysis can detect it.
            # Lower confidence since we don't know the receiver type.
            else:
                dst_id = f"python:external:0-0:{attr_name}:unresolved"
                # WI-javus: resolution_quality is NOT stamped here — it is derived
                # AFTER the hint chain below, honestly, from whether a receiver
                # type/hint was actually established. Stamping ``type_inferred``
                # unconditionally (as before) mislabeled the give-up branch — whose
                # own comment reads "type cannot be inferred" — as a success; on
                # pretix that was ~62% of the ``type_inferred`` population.
                unresolved_meta = {
                    "call_construct": "method",
                }
                # WI-noham Part A: when the receiver's type is GENUINELY
                # inferred, stamp a receiver_type_hint so the inherited_calls
                # linker's strict INV-fahub Site-2 mode can resolve the method
                # on the concrete type. Two inferred sources: a var_types-tracked
                # variable (param annotation / constructor / return-type), whose
                # method Case 2c could not resolve directly (inherited, or a
                # cross-file miss in single-file analysis); or a bare LOCAL class
                # name used as a receiver (``Foo.bar()`` — a static/classmethod
                # call py.py has no direct case for). The edge STAYS
                # is_resolved=False with an unchanged dst — the linker is the
                # sole minter of the resolved edge (taint-safe by construction).
                # An untyped / duck receiver gets NO hint: INV-fahub mandates
                # biasing to unresolved rather than binding to an arbitrary
                # same-named internal def.
                #
                # SCOPE GUARD (INV-fahub): the local-class branch reads the
                # FILE-GLOBAL ``local_symbols`` (symbol_by_name), so a bare-name
                # receiver that is a function-LOCAL binding (param or assignment)
                # shadowing a module-level class would otherwise resolve to that
                # class — an under-determined receiver. We suppress the hint when
                # ``receiver_name`` is bound in the caller's own scope (the same
                # shadow signal ``_emit_variable_refs`` consults), using the
                # already-materialized LEGB frame's ``local_names``. The
                # var_types branch needs no such guard: var_types is built
                # per-function, so it is already scope-local.
                # WI-hiziz PR-2 (Site 1): a bare ``self.method()`` call that
                # Case 2a could not resolve in-file is a cross-file INHERITED
                # method (or an absent one). Stamp the DIRECT enclosing class
                # short name so the inherited_calls linker's Site-1 resolver
                # walks the method up the class's C3 MRO (Python walker landed in
                # PR-1). This is the LEADING branch of a mutually-exclusive elif
                # chain — it dispatches to Site-1 (enclosing_class), never Site-2
                # (receiver_type_hint, which _try_resolve checks first). Guards,
                # each load-bearing:
                #   * ``kind == "method"`` — crash guard: guarantees a dotted
                #     qualified_name so ``split(".")[-2]`` cannot IndexError (a
                #     module-level fn named-param ``self``, or a nested function,
                #     is kind "function", where the class is unrecoverable).
                #   * ``receiver_name not in var_types`` — an EXPLICIT ``self: T``
                #     annotation is a deliberate static-type declaration whose
                #     methods may live OFF the enclosing class's MRO (the mixin/
                #     host idiom). It must route to Site-2 on ``T`` (the demoted
                #     elif), not Site-1 on the enclosing class — else a legit edge
                #     is lost, or (namesake case) a confidently-wrong 0.90 edge is
                #     minted. Only an UNANNOTATED ``self`` (not in var_types) is
                #     lexically the enclosing class.
                #   * ``"self" in _caller_locals`` — ``self`` must be a bound
                #     local (param), excluding a classmethod that references
                #     ``self`` (``self`` undefined; its param is ``cls``) and a
                #     @staticmethod with no ``self`` param.
                #   * ``"staticmethod" not in _caller_decos`` — a @staticmethod
                #     that DOES declare a ``self`` param (anti-pattern) passes the
                #     locals gate, but its ``self`` is an arbitrary argument, not
                #     an instance of the enclosing class — an under-determined
                #     receiver that INV-fahub requires biasing to unresolved.
                # enclosing_class ONLY — taint-safe (is_resolved stays False, dst
                # unchanged, the linker is the sole minter).
                if (
                    receiver_name == "self"
                    and caller_symbol.kind == "method"
                    and receiver_name not in var_types
                    and "self" in _caller_locals
                    and "staticmethod" not in _caller_decos
                ):
                    unresolved_meta["enclosing_class"] = (
                        caller_symbol.qualified_name.split(".")[-2]
                    )
                    # WI-supat (D3): stamp the AUTHORITATIVE enclosing class id
                    # (the lexical method->class map, clobber-immune) so the
                    # linker resolves a same-short-name / cross-language namesake
                    # precisely instead of biasing to unresolved.
                    _encl_id = method_to_enclosing_class_id.get(caller_symbol.id)
                    if _encl_id is not None:
                        unresolved_meta["enclosing_class_id"] = _encl_id
                elif receiver_name in var_types:
                    _rt = var_types[receiver_name]
                    unresolved_meta["receiver_type_hint"] = _rt.name
                    # WI-supat (D3): stamp the concrete receiver-type id only when
                    # it is trustworthy (file-unique short name AND not shadowed by
                    # a same-name import); else omit and fall back to name+guard.
                    if _receiver_type_id_trustworthy(
                        _rt, class_name_counts, imports, module_imports,
                        local_symbols,
                    ):
                        unresolved_meta["receiver_type_id"] = _rt.id
                elif receiver_name not in _caller_locals:
                    _recv_sym = local_symbols.get(receiver_name)
                    if _recv_sym is not None and _recv_sym.kind == "class":
                        unresolved_meta["receiver_type_hint"] = receiver_name
                        if _receiver_type_id_trustworthy(
                            _recv_sym, class_name_counts, imports,
                            module_imports, local_symbols,
                        ):
                            unresolved_meta["receiver_type_id"] = _recv_sym.id
                # WI-javus: stamp resolution_quality='type_inferred' ONLY when the
                # hint chain above actually established a receiver type — ``self``
                # (enclosing_class), an annotated/constructed var or a bare local
                # class (receiver_type_hint). The give-up fall-through (an untyped /
                # duck receiver, INV-fahub-biased to unresolved) established none, so
                # it carries NO resolution_quality: the field names the resolution
                # pathway (spec §903 / MetaKeySpec), and here there was none. Was
                # previously stamped unconditionally, contradicting the branch's own
                # "type cannot be inferred" semantics on ~62% of these edges.
                if (
                    "enclosing_class" in unresolved_meta
                    or "receiver_type_hint" in unresolved_meta
                ):
                    unresolved_meta["resolution_quality"] = "type_inferred"
                # WI-sozoj: a ``self.save()``/``self.delete()`` whose enclosing
                # class DIRECTLY extends django ``models.Model`` is an ORM
                # instance write — re-key the dst to ``django.db.models`` so
                # io-boundary classifies it db_write. This reads only the
                # ``enclosing_class`` the self-branch above already stamped
                # (present exclusively for the ``self`` receiver, and only when
                # the method stayed unresolved) and leaves that INV-fahub /
                # WI-noham / WI-supat receiver-hint chain untouched — additive,
                # so a non-Django class is byte-identical to before. The
                # module-qualified dst_ref survives serialization for the
                # io-boundary CLI consumer (which reparses the dst id).
                _orm_dst_ref: ExternalRef | None = None
                if (
                    attr_name in DJANGO_ORM_INSTANCE_WRITE_METHODS
                    and unresolved_meta.get("enclosing_class") is not None
                    and _class_directly_extends_django_model(
                        unresolved_meta["enclosing_class"], local_symbols
                    )
                ):
                    dst_id = f"python:{DJANGO_ORM_MODULE}:0-0:{attr_name}:unresolved"
                    unresolved_meta["framework_dispatch"] = "django_orm"
                    _orm_dst_ref = ExternalRef(
                        lang="python", module_path=DJANGO_ORM_MODULE, name=attr_name
                    )
                edges.append(Edge.create(
                    src=caller_symbol.id,
                    dst=dst_id,
                    edge_type="calls",
                    line=call_node.lineno,
                    evidence_type="ast_call",
                    is_resolved=False,
                    meta=unresolved_meta,
                    dst_ref=_orm_dst_ref,
                    origin=PASS_ID,
                    origin_run_id=run_id,
                ))
        elif (
            isinstance(func, ast.Attribute)
            and isinstance(func.value, ast.Attribute)
            and isinstance(func.value.value, ast.Name)
            and func.value.value.id == "self"
        ):
            # WI-hiziz PR-3 (Site 3): self.field.method() that Case 2f could not
            # resolve. An INHERITED field lives in class_field_types[parent] and
            # is never merged into this method's var_types (only the class's OWN
            # fields are), so it misses Case 2f and lands here. Stamp
            # inherited_field_receiver + enclosing_class so the inherited_calls
            # Site-3 resolver walks the enclosing class's PARENTS for the field's
            # type and resolves the method there (ast_call_inherited_field @0.80).
            # Taint-safe: is_resolved stays False, dst is an unchanged external
            # unresolved id, the linker is the sole minter. Guards mirror the
            # Site-1 branch (each load-bearing): kind=="method" (dotted
            # qualified_name for split('.')[-2]); "self" not in var_types (an
            # annotated ``def m(self: T)`` binds self to T, whose fields differ
            # from the LEXICAL enclosing class — route away); "self" in
            # _caller_locals (excludes a classmethod referencing self);
            # "staticmethod" not in _caller_decos (a staticmethod's declared self
            # is under-determined); and the OWN-field exclusion the linker's
            # parent-only walk assumes — ``field_name not in var_types`` (typed
            # own fields) AND ``field_name not in own_field_names`` (EVERY
            # __init__ self.X target, incl. an untyped/factory ``self.f =
            # make_conn()`` that var_types misses). An own field is never
            # inherited, so this blocks the shadow FP where the child re-declares
            # a parent field name with a different type.
            field_name = func.value.attr
            method_name = func.attr
            if (
                caller_symbol.kind == "method"
                and "self" not in var_types
                and "self" in _caller_locals
                and "staticmethod" not in _caller_decos
                and field_name not in var_types
                and field_name not in own_field_names
            ):
                _site3_meta: dict[str, str] = {
                    "call_construct": "method",
                    "inherited_field_receiver": field_name,
                    "enclosing_class": (
                        caller_symbol.qualified_name.split(".")[-2]
                    ),
                }
                # WI-supat (D3): the authoritative enclosing-class id (same
                # contract as Site-1) lets the linker start the parent-field walk
                # from exactly the caller's lexical class, skipping the enclosing
                # ambiguity guard on a same-short-name collision.
                _encl_id = method_to_enclosing_class_id.get(caller_symbol.id)
                if _encl_id is not None:
                    _site3_meta["enclosing_class_id"] = _encl_id
                edges.append(Edge.create(
                    src=caller_symbol.id,
                    dst=f"python:external:0-0:{method_name}:unresolved",
                    edge_type="calls",
                    line=call_node.lineno,
                    evidence_type="ast_call",
                    is_resolved=False,
                    meta=_site3_meta,
                    origin=PASS_ID,
                    origin_run_id=run_id,
                ))
        elif isinstance(func, ast.Attribute):
            # WI-zigah: multi-segment chain like `urllib.request.urlopen(x)`
            # where func.value is itself an Attribute. Walk back to the root
            # Name, look it up in module_imports, and emit a qualified
            # unresolved edge so io-boundaries and taint-flow can match
            # dotted-submodule stdlib primitives.
            chain = _unwind_attribute_chain(func)
            if chain is not None:
                root_name_node, chain_attrs = chain
                root_name = root_name_node.id
                if root_name in module_imports and len(chain_attrs) >= 2:
                    real_root = module_imports[root_name]
                    submodule = real_root + "." + ".".join(chain_attrs[:-1])
                    callee = chain_attrs[-1]
                    dst_id = f"python:{submodule}:0-0:{callee}:unresolved"
                    edges.append(Edge.create(
                        src=caller_symbol.id,
                        dst=dst_id,
                        edge_type="calls",
                        line=call_node.lineno,
                        evidence_type="ast_call_direct",
                        is_resolved=False,
                        dst_ref=ExternalRef(
                            lang="python", module_path=submodule, name=callee
                        ),
                        origin=PASS_ID,
                        origin_run_id=run_id,
                    ))
                else:
                    # INV-mumov. A chain whose root is NOT an imported module —
                    # `event.organizer.issued_gift_cards.create(...)`, rooted at
                    # a local — reached here and emitted NOTHING. The
                    # single-attribute form `obj.bar()` has emitted an
                    # `external`-module edge for exactly this reason since
                    # WI-fuvuj ("emit unresolved edge using the attribute name
                    # so that IO boundary analysis and taint-flow" can match);
                    # depth was the only difference, and nothing justified it.
                    #
                    # Measured on pretix: four calls to the same Django manager
                    # sink on adjacent lines, two emitting and two silent —
                    # `Item.objects.create(...)` (class-rooted, handled above)
                    # against `event.organizer.issued_gift_cards.create(...)`.
                    #
                    # COSTS TWICE. A function whose sink calls are all
                    # chain-shaped has no sink edge, is never considered, and
                    # its flow is never reported — a false negative. And the
                    # same absence keeps the call out of `callees_at`, so the
                    # §3a walk cannot ask whether the callee consumes the value
                    # and records an ESCAPE instead: measured 2026-08-06, a
                    # substantial share of INV-busis's "genuine non-call escape
                    # sites" are calls sitting in this state.
                    #
                    # `external` rather than a synthesised module path: the
                    # receiver's type is genuinely unknown here, and inventing
                    # `event.organizer.issued_gift_cards` as a module would
                    # assert a module that does not exist and could collide
                    # with a catalogued entry of the same shape. `external` is
                    # the placeholder the matcher already degrades on.
                    #
                    # Placed in the `else` of the module-import branch so the
                    # class-rooted Django marker, which runs earlier and emits a
                    # module-qualified `django.db.models` edge, keeps winning —
                    # pinned by its own test.
                    callee = chain_attrs[-1]
                    edges.append(Edge.create(
                        src=caller_symbol.id,
                        dst=f"python:external:0-0:{callee}:unresolved",
                        edge_type="calls",
                        line=call_node.lineno,
                        evidence_type="ast_call_direct",
                        is_resolved=False,
                        meta={"call_construct": "method"},
                        origin=PASS_ID,
                        origin_run_id=run_id,
                    ))
        elif isinstance(func, ast.Name):
            # WI-zigah: bare call like `urlopen(x)` after
            # `from urllib.request import urlopen`, where Case 1 looked the
            # name up in `imports` but _lookup_symbol_by_module returned None
            # (stdlib/external target). Emit an unresolved edge keyed by the
            # recorded (module, original_name) pair.
            callee_name = func.id
            if callee_name in imports:
                module_name, original_name = imports[callee_name]
                dst_id = f"python:{module_name}:0-0:{original_name}:unresolved"
                # WI-jubag (instantiates half): a bare call to an imported EXTERNAL
                # name whose original name is PascalCase (Python's strong class-
                # naming convention: Path, MagicMock, Popen, ...) is a CONSTRUCTION,
                # not a plain call. Type it ``instantiates`` (evidence ``ast_new``)
                # so external constructions are recorded rather than misfiled as
                # ``calls``; snake_case/lower callables (helpers, factories) stay
                # ``calls``. The target is external/unresolved either way.
                _is_ctor = original_name[:1].isupper()
                edges.append(Edge.create(
                    src=caller_symbol.id,
                    dst=dst_id,
                    edge_type="instantiates" if _is_ctor else "calls",
                    line=call_node.lineno,
                    evidence_type="ast_new" if _is_ctor else "ast_call_direct",
                    is_resolved=False,
                    dst_ref=ExternalRef(
                        lang="python", module_path=module_name, name=original_name
                    ),
                    origin=PASS_ID,
                    origin_run_id=run_id,
                ))
            elif callee_name in BUILTIN_CONSTRUCTOR_NAMES:
                # WI-mitul: a bare builtin I/O constructor (open) — Case 1 found
                # no import so nothing was emitted, leaving the io_primitives/
                # python.yaml `builtins` rows (fs_read/fs_write functions=[open])
                # dead. Emit a calls edge to builtins so open() itself is a
                # visible I/O call in every syntactic form. The receiver's
                # .read()/.write() edges (WI-fuvuj, module=file) are orthogonal
                # to this open()-call edge.
                #
                # GATED ON ``BUILTIN_CONSTRUCTOR_NAMES``, NOT ON THE WHOLE TABLE.
                # This arm asserts the name IS a builtin — it writes the module
                # slot ``builtins`` — and it consults no import binding, so the
                # membership test is the ONLY thing standing between it and a
                # fabricated builtin. It used to test ``EXTERNAL_CONSTRUCTOR_TYPES``
                # under a comment claiming "only the bare key ``open`` can match",
                # which was an unstated dependency on that table being curated
                # down to builtins. Deriving the table from the catalogue added
                # seventeen bare type names and falsified it immediately: pretix's
                # ``StreamWriter = codecs.getwriter('utf-8'); StreamWriter(data)``
                # — a LOCAL rebinding — minted
                # ``python:builtins:0-0:StreamWriter:unresolved``. The sibling
                # ``_external_constructor_type`` refused the very same name a few
                # lines earlier via its INV-kipor binding check; two consumers of
                # one table disagreeing about what membership licenses is the
                # drift this file keeps rediscovering, so the permitting set is
                # now named directly. ``BUILTIN_CONSTRUCTOR_NAMES`` already means
                # exactly "bare rows that are REAL builtins", so this consolidates
                # onto an existing rule rather than minting a third one.
                dst_id = f"python:builtins:0-0:{callee_name}:unresolved"
                edges.append(Edge.create(
                    src=caller_symbol.id,
                    dst=dst_id,
                    edge_type="calls",
                    line=call_node.lineno,
                    evidence_type="ast_call_direct",
                    is_resolved=False,
                    dst_ref=ExternalRef(
                        lang="python", module_path="builtins", name=callee_name
                    ),
                    origin=PASS_ID,
                    origin_run_id=run_id,
                ))


def extract_nodes(py_file: Path, global_symbols: dict[str, Symbol] | None = None) -> AnalysisResult:
    """
    Extract function/class definitions and call edges from a Python file.

    Returns an AnalysisResult with symbols and edges.
    Gracefully handles syntax errors and encoding issues.

    Note: For cross-file call detection, use analyze_python() instead.
    This function only detects intra-file calls for backwards compatibility.
    """
    file_analysis, _ = _extract_file_analysis(py_file)
    if file_analysis is None:
        return AnalysisResult(symbols=[], edges=[], usage_contexts=[])

    # For single-file analysis, only detect local calls.
    # WI-higap: create a run so Edge constructions have a valid origin_run_id.
    run = AnalysisRun.create(pass_id=PASS_ID, version=PASS_VERSION)
    edges = _extract_edges(
        file_analysis.tree, file_analysis.symbol_by_name, {}, {},
        file_analysis.module_imports,
        run_id=run.execution_id,
        nested_by_parent_id=file_analysis.nested_by_parent_id,
        func_symbol_by_node_id=file_analysis.func_symbol_by_node_id,
        enclosing_func_id=file_analysis.enclosing_func_id,
        local_names_by_func_id=file_analysis.local_names_by_func_id,
        method_to_enclosing_class_id=file_analysis.method_to_enclosing_class_id,
        property_getter_by_path_name=_build_property_getter_index(
            file_analysis.symbols
        ),
    )
    return AnalysisResult(
        symbols=file_analysis.symbols,
        edges=edges,
        usage_contexts=file_analysis.usage_contexts,
    )


@register_analyzer("python", supports_max_files=True)
def analyze_python(
    repo_root: Path, max_files: int | None = None
) -> AnalysisResult:
    """
    Analyze all Python files in a repository.

    Returns an AnalysisResult with all detected symbols, edges, and provenance.
    Supports cross-file call detection via import resolution.

    Args:
        repo_root: Root directory of the repository
        max_files: Optional limit on number of files to analyze
    """
    import time

    start_time = time.time()

    # Create analysis run for provenance tracking
    run = AnalysisRun.create(pass_id=PASS_ID, version=PASS_VERSION)

    # Detect src/ layout source roots (PEP 517/518 + monorepo
    # packages/<pkg>/src/<mod>/ layouts — WI-davan E1).
    source_roots = _detect_source_roots(repo_root)

    # First pass: collect all symbols and imports from all files
    file_analyses: dict[Path, FileAnalysis] = {}
    files_skipped = 0
    for py_file in find_python_files(repo_root, max_files=max_files):
        analysis, fail_reason = _extract_file_analysis(py_file, repo_root, source_roots)
        if analysis is not None:
            file_analyses[py_file] = analysis
        else:
            files_skipped += 1
            # INV-buhur: record the dropped file so consumers can detect
            # partially-analyzed repos. Path is repo-relative when possible.
            try:
                rel = str(py_file.relative_to(repo_root))
            except ValueError:  # pragma: no cover  # defensive: should always be under repo_root
                rel = str(py_file)
            run.record_failed_file(rel, fail_reason or "parse failure")

    # Build global symbol table: (module_name, symbol_name) -> Symbol
    global_symbols: dict[tuple[str, str], Symbol] = {}
    for py_file, analysis in file_analyses.items():
        module_name = _module_name_from_path(py_file, repo_root, source_roots)
        # INV-nuzas: a package's __init__.py *is* the package module. A symbol
        # DEFINED in ``pkg/__init__.py`` is importable/callable as ``pkg.<name>``,
        # but _module_name_from_path keys it under ``pkg.__init__`` — so every
        # cross-module call/import to such a symbol missed the (module, name)
        # lookup and leaked to an external_symbol twin (342 first-party ``calls``
        # edges on the self-corpus). Also register __init__-defined symbols under
        # the importable package name. (The re-export aliasing below covers names
        # IMPORTED into __init__; this covers names DEFINED there. The
        # ``pkg.__init__`` key is retained for back-compat — nothing references
        # it, but keeping it makes this strictly additive / zero-regression.)
        keys = [module_name]
        if py_file.name == "__init__.py":
            package_name = module_name.rsplit(".__init__", 1)[0]
            if package_name != module_name:
                keys.append(package_name)
        for symbol in analysis.symbols:
            for key in keys:
                global_symbols[(key, symbol.name)] = symbol

    # Process re-exports from __init__.py files
    # When __init__.py does "from .submodule import helper", add an alias
    # so that "from package import helper" resolves to the real symbol
    for py_file, analysis in file_analyses.items():
        if py_file.name != "__init__.py":
            continue

        module_name = _module_name_from_path(py_file, repo_root, source_roots)
        # Package name is module name without .__init__ suffix
        package_name = module_name.rsplit(".__init__", 1)[0]

        for local_name, (resolved_module, original_name) in analysis.imports.items():
            # Check if this import points to a known symbol (with suffix matching)
            source_symbol = _lookup_symbol_by_module(
                global_symbols, resolved_module, original_name
            )
            if source_symbol:
                # Add alias: (package, local_name) -> source_symbol
                global_symbols[(package_name, local_name)] = source_symbol
                # Mark the source symbol as re-exported from __init__.py
                # so library-export patterns can detect it
                if "re_exported" not in source_symbol.modifiers:
                    source_symbol.modifiers.append("re_exported")

    # WI-hotug PR#2 (CASE A / INV-nuzas): generalize re-export resolution to
    # NON-package facade modules. A plain module (e.g. ``compact.py`` doing
    # ``from .tier import parse_tier_spec``) re-exports exactly like a package
    # ``__init__`` does, so a cross-module ``from compact import parse_tier_spec;
    # parse_tier_spec()`` must resolve to the real function instead of dead-ending
    # at a phantom workspace ``external_symbol`` (self-corpus: 10 imported-function
    # re-export phantoms). Deliberately kept distinct from the __init__ pass above:
    #   * EXACT module match only (``global_symbols.get((resolved_module, name))``),
    #     NOT the suffix-matching ``_lookup_symbol_by_module``. Suffix matching is
    #     the src-layout affordance the __init__ pass needs, but on the newly-opened
    #     non-package surface it would let a facade's ``from json import dumps``
    #     re-export bind to a coincidentally-suffixed in-tree ``pkg/json.py``
    #     (a confidently-wrong INV-fahub violation). Exact match resolves the 10
    #     intra-repo re-exports (all exact) and fails SAFE (to a phantom) otherwise.
    #   * the ``re_exported`` modifier stays __init__-only (above) — visibility.py /
    #     library-exports.yaml consume it as a package-surface signal.
    # The ``(module_name, local_name) in global_symbols`` skip is a single guard
    # doing double duty: a locally-DEFINED same-name symbol (already registered by
    # the build loop) wins over the re-export, and an alias added in a prior
    # iteration is not re-processed. The bounded fixed point (cap 5) chases N-hop
    # re-export chains; because ``.get`` reads the live table, a chain often
    # collapses in one pass, and a chain deeper than the cap fails safe to a phantom.
    for _ in range(5):
        changed = False
        for py_file, analysis in file_analyses.items():
            if py_file.name == "__init__.py":
                continue
            module_name = _module_name_from_path(py_file, repo_root, source_roots)
            for local_name, (resolved_module, original_name) in analysis.imports.items():
                if (module_name, local_name) in global_symbols:
                    continue  # locally defined, or already aliased — leave it
                source_symbol = global_symbols.get((resolved_module, original_name))
                if source_symbol is not None:
                    global_symbols[(module_name, local_name)] = source_symbol
                    changed = True
        if not changed:
            break

    # supply:F4 (INV-nuzas) — map every in-tree dotted module name to its
    # first-party file-anchor id, so imports of workspace-sibling modules resolve
    # to real in-repo nodes instead of dangling to phantom external_symbol
    # boundary nodes. Built from the absolute py_file paths (the same form
    # _make_file_id uses for the import-edge SOURCE); the orchestrator
    # relativizes every id uniformly afterward, so dst and src stay consistent.
    module_to_file_id: dict[str, str] = {}
    for py_file in file_analyses:
        module_name = _module_name_from_path(py_file, repo_root, source_roots)
        file_id = _make_file_id(str(py_file))
        module_to_file_id[module_name] = file_id
        if py_file.name == "__init__.py":
            # A package is importable by its package name (module sans .__init__).
            package_name = module_name.rsplit(".__init__", 1)[0]
            module_to_file_id[package_name] = file_id

    # Create resolver for efficient lookups in Pass 2 (with cached indexes)
    from hypergumbo_core.symbol_resolution import SymbolResolver
    resolver = SymbolResolver(global_symbols)

    # Build (path, name) -> symbol index for O(1) lookups in typed method
    # resolution. Replaces O(n) scans of global_symbols.items() that check
    # sym.path == target_path and sym_name == target_name.
    _sym_by_path_name: dict[tuple[str, str], Symbol] = {}
    for (_mod, sym_name), sym in global_symbols.items():
        key = (sym.path, sym_name)
        # First entry wins (same as the break in the old linear scan)
        if key not in _sym_by_path_name:
            _sym_by_path_name[key] = sym

    # WI-sizut: property-getter index built from the pre-collapse per-file
    # symbols (global_symbols above is already collapsed last-write-wins, so a
    # read-write property's getter is masked there by its @x.setter/@x.deleter).
    _property_getter_by_path_name = _build_property_getter_index(
        [s for a in file_analyses.values() for s in a.symbols]
    )

    # Second pass: extract edges with cross-file resolution
    all_symbols: list[Symbol] = []
    all_edges: list[Edge] = []
    all_usage_contexts: list[UsageContext] = []
    # Load python.yaml dataflow config once for library_patterns fallback
    # in annotate_dataflow_ast (per-file is wasteful — it's a static config).
    py_dataflow_config = get_dataflow_config("python")
    for py_file, analysis in file_analyses.items():
        module_name = _module_name_from_path(py_file, repo_root, source_roots)

        # Set origin on symbols
        for symbol in analysis.symbols:
            symbol.origin = [PASS_ID]
            symbol.origin_run_id = run.execution_id
        all_symbols.extend(analysis.symbols)

        # Extract call edges (WI-higap: run_id plumbed at construction so
        # Edge.__post_init__ enforcement passes without orchestrator backfill).
        call_edges = _extract_edges(
            analysis.tree, analysis.symbol_by_name, analysis.imports, global_symbols,
            analysis.module_imports, resolver, _sym_by_path_name,
            run_id=run.execution_id,
            property_getter_by_path_name=_property_getter_by_path_name,
            nested_by_parent_id=analysis.nested_by_parent_id,
            func_symbol_by_node_id=analysis.func_symbol_by_node_id,
            enclosing_func_id=analysis.enclosing_func_id,
            local_names_by_func_id=analysis.local_names_by_func_id,
            method_to_enclosing_class_id=analysis.method_to_enclosing_class_id,
            module_to_file_id=module_to_file_id,
        )
        # ADR-0015: annotate edges with access_mode from Python AST context.
        # Pass source + python.yaml config so library_patterns (e.g. .append,
        # .write, .send → write) can fall back when the AST positional walk
        # leaves a call edge unclassified.  Without this, PR #2733's
        # library_patterns wiring is dead code for Python.
        call_edges = annotate_dataflow_ast(
            call_edges, analysis.tree,
            source=analysis.source,
            config=py_dataflow_config,
        )
        all_edges.extend(call_edges)

        # Extract import edges
        import_edges = _extract_import_edges(
            analysis.tree, str(py_file), module_name, global_symbols, resolver,
            module_to_file_id=module_to_file_id,
            run_id=run.execution_id,
        )
        all_edges.extend(import_edges)

        # Collect usage contexts (v1.1.x)
        all_usage_contexts.extend(analysis.usage_contexts)

    # Extract inheritance edges (META-001: base_classes metadata -> extends edges)
    # Build multi-value class lookup: name -> list of candidates
    # (single-value dict had last-writer-wins bug: 238 Django 'Model' classes
    # all resolved to a single test stub instead of django.db.models.base.Model)
    class_by_name: dict[str, list[Symbol]] = {}
    for sym in all_symbols:
        if sym.kind == "class":
            class_by_name.setdefault(sym.name, []).append(sym)

    # Build symbol ID -> file-level imports mapping for disambiguation
    sym_file_imports: dict[str, dict[str, tuple[str, str]]] = {}
    for _py_file, analysis in file_analyses.items():
        for sym in analysis.symbols:
            sym_file_imports[sym.id] = analysis.imports

    # Create extends edges with import-aware disambiguation
    inheritance_edges = _extract_inheritance_edges(
        all_symbols, class_by_name, sym_file_imports, run, module_to_file_id
    )
    all_edges.extend(inheritance_edges)

    # Update run metadata
    run.files_analyzed = len(file_analyses)
    run.files_skipped = files_skipped
    run.duration_ms = int((time.time() - start_time) * 1000)

    # Parse pyproject.toml deps for tier-2 classification of boundary
    # nodes (WI-nunuj). Empty manifest → no entries → boundary nodes
    # stay tier 3 (no regression vs. previous behaviour).
    from hypergumbo_lang_mainstream.py_deps import parse_python_dependencies
    py_manifest = parse_python_dependencies(repo_root)

    return AnalysisResult(
        symbols=all_symbols,
        edges=all_edges,
        usage_contexts=all_usage_contexts,
        run=run,
        dependency_manifest=py_manifest if py_manifest.entries else None,
    )
