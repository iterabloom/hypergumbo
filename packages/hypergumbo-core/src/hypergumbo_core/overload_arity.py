# SPDX-License-Identifier: AGPL-3.0-or-later
"""Choose among same-named callables by the call's argument count.

THE DEFECT THIS EXISTS FOR. Several analyzers resolve a call by NAME and then
take whatever the name gives them: one symbol (a name-keyed registry keeps the
last one registered), every symbol (Elixir fanned out to each clause of every
arity), or a ``ListNameResolver`` guess among all visible ones (Nim). None of
them looked at how many arguments the call passes, which every one of these
languages uses to pick the callee. So an overload calling its sibling --
``InitKeywords()`` calling ``InitKeywords(is)`` -- was drawn as a call to
itself (WI-hilum, cpp), ``get_timeout()`` inside ``get_timeout/2`` was a
self-loop (WI-rodiz, elixir), and a two-argument ``add`` took a one-parameter
project overload (WI-bivab, nim).

WHERE ARITY IS READ FROM. ``Symbol.meta["parameters"]``, the declared home of
the parameter-arity fact (ADR-0058, :data:`hypergumbo_core.signature_axis.
FACT_HOMES`). It is never parsed back out of ``Symbol.signature``: that string
is a display surface, and the C++ one does not even list a defaulted or a
variadic parameter. A producer builds each entry with :func:`parameter_entry`,
so every language spells "has a default" and "takes any number" the same way.
An entry's ``default`` and ``variadic`` are what :func:`arity_bounds` reads;
``name`` and ``type`` are for readers. Python's entries (``py.py``) predate the
``variadic`` key and spell it ``*args`` in the name; :func:`arity_bounds` reads
that spelling too, so the helper is not wrong about a symbol it was not written
for, though no Python call site consults it.

ABSENT IS NOT EMPTY. A symbol with no ``parameters`` key has an UNKNOWN arity
and is never excluded; ``[]`` is a callable that takes no arguments. Producers
that adopt this helper write the key for every callable, the empty list
included, so the two cannot be confused.

THE CHOICE (:func:`choose_overload`). Candidates arrive in TIERS, nearest
first (cpp: the picked symbol's own file, then the run; nim: the caller's
file, its module, then what it imports). The first tier holding a candidate
whose bounds admit the argument count decides:

* one admits: that one, at full confidence;
* several admit: arity cannot decide. The record says so --
  ``ambiguous=True``, confidence scaled by ``1/sqrt(N)`` exactly as
  ``ListNameResolver`` scales an ambiguous guess -- and the edge is drawn to
  the caller's ``preferred`` candidate when it is among them (so a choice the
  analyzer made by other evidence survives), else the first by position;
* none admits in any tier: ``symbol=None``; the analyzer decides what that
  means in its language (see each analyzer's call path).

An unknown argument count (a spread, a splice, a macro) admits everything, so
the choice degrades to the pre-arity behaviour rather than inventing a fact.

THE SAME FACT IS AN IDENTITY INPUT (:func:`declared_arity`,
:func:`declared_arity_stable_id`; WI-tusad). ADR-0035's producer formula hashes
a callable's parameter count and flags beside its name and scope.
``BaseAnalyzer.compute_stable_id`` reads them from a parameter-list node, and in
many grammars there is none under the definition: Elixir and Erlang clauses,
Haskell and OCaml equations, Clojure arity bodies, and the C family, whose
``parameter_list`` sits under the declarator. Those producers either hashed
``param_count 0`` for every overload (the siblings tie, and the per-file split
pass numbers them by emission order) or computed nothing and rode the backstop
floor (two same-name siblings null each other). The parameters were usually
already known: the producer writes them here for overload selection. So the
identity reads the same home, through one helper, instead of each language
growing its own counter.

:func:`declared_arity` reads ``meta["parameters"]`` (the structured list) and
falls back to ``meta["arity"]`` (:data:`ARITY_KEY`), the bare count a producer
records when it knows how many parameters a callable takes but not what they
are -- a BEAM ``@callback`` head, ``name/arity``. ``meta["params"]`` (a name
list) is not read: it cannot say which parameter has a default or takes any
number, and a producer adopting this helper writes :func:`parameter_entry`
records instead. :func:`declared_arity_stable_id` turns the result into the
ADR-0035 formula, anchored to the declaring file the way ``compute_stable_id``
anchors a top-level symbol:

* no parameter declares a type: the untyped tier, ``assemble_stable_id`` with
  the count and the ``ArityFlags`` string. For the same parameters this is the
  value ``compute_stable_id`` would produce had it found the list. A bare count
  has no flags, and its flags slot is empty rather than a claim of "no
  defaults";
* some parameter declares a type (Julia ``f(x::Int)``, GLSL ``vec2 f(vec2)``):
  the typed tier, ``make_typed_stable_id`` over ``(T1,T2)``, a parameter with
  no declared type spelled ``_``, a default marked ``=`` and a variadic ``*``.
  Same-arity overloads that differ only in type need this; the count alone
  ties them.

HOW A LANGUAGE PLUGS IN. (1) Write ``meta["parameters"]`` for every callable
with :func:`parameter_entry`, passing ``type_`` where the grammar declares one
(the empty list for a callable taking none, never an absent key), or
``meta["arity"]`` when only the count is known. (2) Mint ``stable_id`` at
construction with ``declared_arity_stable_id(kind=..., lang=..., path=<repo-
relative path>, name=..., qualified_name=<full scope chain>,
arity=declared_arity(meta), decorators=..., containing_stable_id=<enclosing
scope's id, if the producer threads one>)``. A callable whose arity is unknown
(``declared_arity`` returns None) takes the zero-parameter untyped formula, the
value ``compute_stable_id`` gives it today. ``behaviours.callback_stable_id``
is the first adopter. ``test_stable_id_floor_gate.py`` is how a migration shows: the
language's floor and null counts fall, and its deletion check passes. Replacing
a floor value or a null this way is no scheme bump (ADR-0035 §6); changing a
value the producer already computes (a C-family overload hashed with
``param_count 0``) is a hash-input change §6 governs. A producer that mints
``analyzer.compute_stable_id(node, ...)`` today moves with
:func:`node_declared_arity_stable_id`, which keeps that method's decorators and
its exact value for a callable declaring no arity, so only the ids of
callables with parameters change (C++ definitions, HLSL, Nim, Ada, Pascal,
SQL: the WI-tusad scheme bump).

SYMBOLS MINTED OUTSIDE THE LANGUAGE ANALYZER (:func:`pass_minted_stable_id`;
INV-fufij). The backstop floor runs in ``analyze.all_analyzers`` before the
linkers, and the ``make_synthetic_symbol_identity`` chokepoint stamps only
``language=None`` stand-ins, so a linker that mints a symbol WITH a language
(the gRPC linker's proto ``interface`` per service, the view-template
linker's ``template``) and sets no ``stable_id`` ships it null; a format pass
that runs as an analyzer but is no language's producer (``manifest_targets``:
a ``Package.swift`` target, a Gradle ``mainClass``) rode the floor key. Each
such site mints at construction through this one helper: the ADR-0035
formula of :func:`scoped_stable_id`, its scope chain LED BY THE MINTING PASS
(``owner/scope.../name`` in the qualified-name slot). The pass
is a scope because the language analyzer may emit its own symbol of the same
kind and name in the same file (the proto analyzer's ``interface S`` beside
the gRPC linker's), and the two are different records. ``scope`` carries what
the pass knows of the enclosing scopes (a proto package). Filling a null or a
floor value this way is no scheme bump (ADR-0035 §6).

:func:`scoped_stable_id` is the same formula for a language producer whose
symbols take no parameters but nest (a Dockerfile ``ARG`` in a build stage, a
CSS selector in an ``@media`` rule): the producer passes its scope chain as a
list and the helper joins it with the name, so no producer spells its own
qualified-name format. :func:`nested_qualified_name` is the same chain for a
producer that already hashed an EMPTY qualified name (Bash, Racket, Scheme, V
methods): it spells the chain only for a nested symbol, so adding the chain in
the scheme bump moved only the ids that tied, never a top-level one;
:func:`nested_member_qualified_name` is that rule for a name that already
spells its nearest owner (C++ ``Inner::go``, D ``Inner.go``).

NESTED TABLE KINDS (:func:`scoped_table_stable_id`; INV-fabih). A table kind
(``class``, ``struct``, ``module``, ``type`` ...; ``analyze.base.is_table_kind``)
takes the backstop's table formula ``{kind}:{language}:{path}:{name}``, which
holds no scope: ``class Inner`` nested in class ``A`` and in class ``B`` of one
file share it, the per-file split pass numbers the two by emission order, and
deleting ``A`` moved ``B.Inner``'s id. A producer whose table-kind symbols
nest mints this helper instead: the same formula with ``scope/.../name`` in the
name slot, which is exactly the table value when ``scope`` is empty, so only
nested declarations (the ones that tied) changed in the WI-tusad scheme bump.
Their members do the same in their own formula: a method or field hashes the
FULL owner chain (``A.Inner.go``), not the nearest owner (``Inner.go``), and a
member of a top-level type keeps its value because there the two agree.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Mapping, Optional, Sequence

from .analyze.base import (
    ArityFlags, assemble_stable_id, is_table_kind, make_declaration_stable_id,
    make_file_stable_id, make_typed_stable_id,
)
from .paths import normalize_path

if TYPE_CHECKING:
    import tree_sitter

    from .analyze.base import TreeSitterAnalyzer
    from .ir import Symbol


#: The ``Symbol.meta`` key holding the structured parameter list.
PARAMETERS_KEY = "parameters"

#: The ``Symbol.meta`` key holding a bare parameter count, written where a
#: producer knows how many parameters a callable takes but not what they are
#: (a BEAM ``@callback``; Erlang ``name/arity``).
ARITY_KEY = "arity"


def parameter_entry(
    name: str,
    type_: Optional[str] = None,
    *,
    default: bool = False,
    variadic: bool = False,
) -> dict[str, Any]:
    """One element of ``meta["parameters"]``, in the shape every producer shares.

    ``variadic`` is written only when true, so an entry for an ordinary
    parameter is exactly the ``{name, type, default}`` triple ``py.py`` has
    always written.
    """
    entry: dict[str, Any] = {"name": name, "type": type_, "default": default}
    if variadic:
        entry["variadic"] = True
    return entry


@dataclass(frozen=True)
class ArityBounds:
    """How many arguments a callable accepts: ``required`` up to ``maximum``.

    ``maximum`` is None when a variadic parameter makes the count unbounded.
    """

    required: int
    maximum: Optional[int]

    def admits(self, arg_count: int) -> bool:
        """True when a call passing ``arg_count`` arguments can bind here."""
        if arg_count < self.required:
            return False
        return self.maximum is None or arg_count <= self.maximum


def bounds_of_entries(entries: Sequence[Any]) -> Optional[ArityBounds]:
    """Arity bounds of a ``meta["parameters"]`` list; None if it is malformed."""
    required = 0
    total = 0
    unbounded = False
    for entry in entries:
        if not isinstance(entry, dict):
            return None
        name = entry.get("name")
        if entry.get("variadic") or (
            isinstance(name, str) and name.startswith("*")
        ):
            unbounded = True
            continue
        total += 1
        if not entry.get("default"):
            required += 1
    return ArityBounds(required=required, maximum=None if unbounded else total)


def arity_bounds(symbol: "Symbol") -> Optional[ArityBounds]:
    """The arity bounds a symbol declares, or None when it declares none.

    None is UNKNOWN, never zero: a symbol whose producer wrote no
    ``parameters`` key may take any number of arguments as far as this
    helper knows.
    """
    entries = (symbol.meta or {}).get(PARAMETERS_KEY)
    if not isinstance(entries, list):
        return None
    return bounds_of_entries(entries)


def narrow_by_arity(
    candidates: Sequence["Symbol"], arg_count: Optional[int],
) -> list["Symbol"]:
    """The candidates a call with ``arg_count`` arguments can bind to.

    A candidate of unknown arity is kept (it cannot be excluded), and an
    unknown ``arg_count`` keeps every candidate.
    """
    if arg_count is None:
        return list(candidates)
    kept: list["Symbol"] = []
    for cand in candidates:
        bounds = arity_bounds(cand)
        if bounds is None or bounds.admits(arg_count):
            kept.append(cand)
    return kept


@dataclass(frozen=True)
class OverloadChoice:
    """The outcome of :func:`choose_overload`.

    ``symbol`` is None when no candidate in any tier admits the call.
    ``admitted`` is the set the choice was made from; ``ambiguous`` is True
    when it held more than one, in which case ``confidence`` is
    ``1/sqrt(len(admitted))`` and ``symbol`` is one of them, not THE callee.
    """

    symbol: Optional["Symbol"]
    confidence: float
    ambiguous: bool
    admitted: tuple["Symbol", ...]


def _position_key(sym: "Symbol") -> tuple[str, int, int]:
    span = sym.span
    return (
        sym.path,
        span.start_line if span is not None else 0,
        span.start_col if span is not None else 0,
    )


def choose_overload(
    tiers: Sequence[Sequence["Symbol"]],
    arg_count: Optional[int],
    *,
    preferred: Optional["Symbol"] = None,
) -> OverloadChoice:
    """Pick the callee among same-named candidates; see the module docstring."""
    for tier in tiers:
        admitted = narrow_by_arity(tier, arg_count)
        if not admitted:
            continue
        if len(admitted) == 1:
            return OverloadChoice(admitted[0], 1.0, False, (admitted[0],))
        ordered = sorted(admitted, key=_position_key)
        pick = preferred if preferred is not None and any(
            c is preferred for c in ordered
        ) else ordered[0]
        return OverloadChoice(
            pick, 1.0 / math.sqrt(len(ordered)), True, tuple(ordered),
        )
    return OverloadChoice(None, 0.0, False, ())


@dataclass(frozen=True)
class DeclaredArity:
    """What a callable's declaration says about its parameters, as identity input.

    ``flags`` is the ``ArityFlags`` string (``"False,False,False"``) when the
    parameters are known, and ``""`` for a bare count, whose defaults and
    variadics were never observed. ``types`` is the per-parameter type
    spelling, empty unless some parameter declares one.
    """

    param_count: int
    flags: str
    types: tuple[str, ...] = ()

    @property
    def type_signature(self) -> str:
        """``(T1,T2)`` for the typed tier, or ``""`` when no type is declared."""
        return f"({','.join(self.types)})" if self.types else ""


def declared_arity_of_entries(entries: Sequence[Any]) -> Optional[DeclaredArity]:
    """:class:`DeclaredArity` of a ``meta["parameters"]`` list; None if malformed.

    Variadic and default are read the way :func:`bounds_of_entries` reads them,
    including Python's ``*args`` / ``**kwargs`` spelling (``**`` is the
    keyword variadic).
    """
    has_defaults = has_varargs = has_kwargs = False
    spelled: list[str] = []
    typed = False
    for entry in entries:
        if not isinstance(entry, dict):
            return None
        name = entry.get("name")
        star = isinstance(name, str) and name.startswith("*")
        kwargs = isinstance(name, str) and name.startswith("**")
        variadic = bool(entry.get("variadic")) or (star and not kwargs)
        default = bool(entry.get("default"))
        has_kwargs = has_kwargs or kwargs
        has_varargs = has_varargs or variadic
        has_defaults = has_defaults or default
        type_ = entry.get("type")
        typed = typed or bool(type_)
        mark = "*" if variadic or kwargs else ""
        spelled.append(f"{mark}{type_ or '_'}{'=' if default else ''}")
    flags = ArityFlags(
        param_count=len(spelled),
        has_defaults=has_defaults,
        has_varargs=has_varargs,
        has_kwargs=has_kwargs,
    )
    return DeclaredArity(
        len(spelled), flags.as_flags_str(), tuple(spelled) if typed else (),
    )


def declared_arity(meta: Optional[Mapping[str, Any]]) -> Optional[DeclaredArity]:
    """The arity a callable's ``meta`` declares, or None when it declares none.

    ``meta["parameters"]`` wins; a bare ``meta["arity"]`` count is the
    fallback. None is UNKNOWN, as for :func:`arity_bounds`.
    """
    if not meta:
        return None
    entries = meta.get(PARAMETERS_KEY)
    if isinstance(entries, list):
        return declared_arity_of_entries(entries)
    count = meta.get(ARITY_KEY)
    if isinstance(count, int) and not isinstance(count, bool) and count >= 0:
        return DeclaredArity(count, "")
    return None


def declared_arity_stable_id(
    *,
    kind: str,
    lang: str,
    path: str,
    name: str,
    qualified_name: str,
    arity: Optional[DeclaredArity],
    decorators: str = "",
    containing_stable_id: str = "",
    result_type: str = "",
) -> str:
    """The ADR-0035 producer stable_id of a callable whose arity came from meta.

    See the module docstring for the tier choice. ``path`` must be
    repo-relative (it is folded in as the file anchor when
    ``containing_stable_id`` is empty, as ``compute_stable_id`` does), and
    ``decorators`` is the sorted, comma-joined modifier list.

    ``result_type`` is for a language whose overload profile includes the
    result (Ada: ``function F return Integer`` / ``function F return Float``
    take no parameter and differ only there). A non-empty one takes the typed
    tier over ``(T1,...)->R``, a parameter with no declared type spelled
    ``_``; an empty one leaves the value as above.
    """
    file_anchor = make_file_stable_id(lang, normalize_path(path))
    containing = containing_stable_id or file_anchor
    if result_type:
        params = (
            arity.types if arity is not None and arity.types
            else ("_",) * (arity.param_count if arity is not None else 0)
        )
        return make_typed_stable_id(
            kind, f"({','.join(params)})->{result_type}", "", containing_stable_id,
            decorators, name=name, qualified_name=qualified_name,
            file_stable_id=file_anchor,
        )
    if arity is not None and arity.types:
        return make_typed_stable_id(
            kind, arity.type_signature, "", containing_stable_id, decorators,
            name=name, qualified_name=qualified_name, file_stable_id=file_anchor,
        )
    if arity is None:
        arity = DeclaredArity(0, ArityFlags(0, False, False, False).as_flags_str())
    return assemble_stable_id(
        kind, arity.param_count, arity.flags, decorators, containing,
        name, qualified_name,
    )


#: Joins a scope chain and the symbol's name in the qualified-name slot
#: :func:`scoped_stable_id` hashes.
SCOPE_SEPARATOR = "/"


def nested_qualified_name(scope: Sequence[str], name: str) -> str:
    """The qualified-name slot for a producer that hashed an EMPTY one.

    Several producers mint ``compute_stable_id`` with no qualified name (Bash,
    Racket, Scheme, V), so two same-name symbols in different scopes of one
    file (``run`` nested in two Bash functions, ``run`` in two Racket
    submodules) tie and the per-file split pass orders them by emission. This
    returns ``scope[0]/.../scope[-1]/name`` for a nested symbol and ``""`` for
    a top-level one, so the scheme bump that adds the chain changes only the
    ids that tied: a top-level symbol keeps the value it had. (A producer
    adopting a chain from scratch uses :func:`scoped_stable_id`, which spells
    the top-level name too.)
    """
    return SCOPE_SEPARATOR.join((*scope, name)) if scope else ""



def nested_member_qualified_name(
    scope: Sequence[str], name: str, separator: str,
) -> str:
    """:func:`nested_qualified_name` for a name that already spells its nearest
    owners (``Inner::go``, ``Inner.go``; INV-fabih).

    Producers that hashed an EMPTY qualified name beside such a name (C++, D)
    tied two nested ``class Inner`` and their members. ``scope`` is the full
    chain of enclosing scopes; the trailing ones ``name`` already spells
    (split on ``separator``) are dropped, and what is left goes through
    :func:`nested_qualified_name`: ``""`` for a top-level declaration and a
    member of a top-level type, ``A/Inner::go`` for a member of a nested one.
    """
    spelled = name.split(separator)[:-1]
    if spelled and list(scope[-len(spelled):]) == spelled:
        scope = scope[:-len(spelled)]
    return nested_qualified_name(scope, name)


def scoped_stable_id(
    *,
    kind: str,
    lang: str,
    path: str,
    name: str,
    scope: Sequence[str] = (),
    arity: Optional[DeclaredArity] = None,
    decorators: str = "",
) -> str:
    """The ADR-0035 producer stable_id of a declaration whose scope chain the
    producer holds as a list: :func:`declared_arity_stable_id` with the
    qualified name ``scope[0]/.../scope[-1]/name``.

    For the producers whose symbols have no parameter list (a Dockerfile
    ``ARG``, a CSS selector, a Makefile target, a TOML table) ``arity`` stays
    None; ``scope`` is what the grammar nests the symbol in (the build stage,
    the ``@media`` query and rule, the conditional branch). Two symbols that
    are still identical after that are a same-scope tie, which the per-file
    split pass orders (ADR-0035 §1). ``path`` must be repo-relative.
    """
    return declared_arity_stable_id(
        kind=kind, lang=lang, path=path, name=name,
        qualified_name=SCOPE_SEPARATOR.join((*scope, name)), arity=arity,
        decorators=decorators,
    )


#: Table kinds whose formula is not ``{kind}:{language}:{path}:{name}``: a file
#: hashes its path alone, a project its name alone. Neither nests.
_UNSCOPED_TABLE_KINDS = frozenset({"file", "project"})


def scoped_table_stable_id(
    *,
    kind: str,
    lang: str,
    path: str,
    name: str,
    scope: Sequence[str] = (),
) -> str:
    """The table-formula stable_id of a table-kind declaration nested in
    ``scope`` (INV-fabih; see the module docstring).

    ``make_declaration_stable_id(kind, lang, path, "scope[0]/.../name")``: the
    backstop's own value for a top-level declaration (empty ``scope``), the
    scope chain in the name slot for a nested one. ``path`` must be
    repo-relative. Raises ``ValueError`` for a kind the backstop has no
    ``{kind}:{language}:{path}:{name}`` table formula for.
    """
    if not is_table_kind(kind) or kind in _UNSCOPED_TABLE_KINDS:
        raise ValueError(f"{kind!r} has no scoped table formula")
    return make_declaration_stable_id(
        kind, lang, normalize_path(path), SCOPE_SEPARATOR.join((*scope, name)),
    )


def pass_minted_stable_id(
    *,
    owner: str,
    kind: str,
    lang: str,
    path: str,
    name: str,
    scope: Sequence[str] = (),
    arity: Optional[DeclaredArity] = None,
) -> str:
    """The ADR-0035 producer stable_id of a symbol a pass outside the language
    analyzer mints (INV-fufij; see the module docstring): :func:`scoped_stable_id`
    with the minting pass as the outermost scope.

    ``owner`` is the minting pass's id (``make_pass_id`` name, which carries no
    version, so a pass upgrade moves no id); ``path`` must be repo-relative.
    """
    return scoped_stable_id(
        kind=kind, lang=lang, path=path, name=name, scope=(owner, *scope),
        arity=arity,
    )


def node_declared_arity_stable_id(
    analyzer: "TreeSitterAnalyzer",
    node: "tree_sitter.Node",
    *,
    kind: str,
    name: str,
    meta: Optional[Mapping[str, Any]],
    file_stable_id: str,
    qualified_name: str = "",
    result_type: str = "",
) -> str:
    """``compute_stable_id`` with the arity read from ``meta`` instead of the node.

    For a producer that already mints ``analyzer.compute_stable_id(node, ...)``
    and whose grammar puts no parameter-list node where that method looks
    (the C family's declarator, Nim, Ada, Pascal, SQL), so every overload
    hashed ``param_count 0`` and the siblings tied. The decorators are the
    ones ``compute_stable_id`` reads from the node, and a callable whose
    ``meta`` declares no arity gets exactly ``compute_stable_id``'s value, so
    only the ids of callables WITH parameters change: the scheme-bump scope.
    ``result_type`` is passed through to :func:`declared_arity_stable_id`
    (Ada functions).
    """
    arity = declared_arity(meta)
    if arity is None:
        return analyzer.compute_stable_id(
            node, kind=kind, name=name, qualified_name=qualified_name,
            file_stable_id=file_stable_id,
        )
    decorators = ",".join(sorted(analyzer._extract_decorator_names(node)))
    return declared_arity_stable_id(
        kind=kind, lang=analyzer.lang, path="", name=name,
        qualified_name=qualified_name, arity=arity, decorators=decorators,
        containing_stable_id=file_stable_id, result_type=result_type,
    )
