# SPDX-License-Identifier: AGPL-3.0-or-later
"""A call on an in-repo type it does not define reaches the type's LIBRARY ancestor.

THE CONDITION, WHICH IS NOT ONE LANGUAGE'S. An application type derives from a
catalogued library type and does not define the member being called, so the call
runs the library's code:

* Elixir ``defmodule App.Repo do use Ecto.Repo, ... end`` called as
  ``Repo.all(q)`` -- ``use`` injects ``all/2`` into ``App.Repo``, and Ecto.Repo's
  ``__using__`` declares ``@behaviour Ecto.Repo``, so the analyzer records
  ``App.Repo implements Ecto.Repo`` (WI-vitas);
* Python ``class Api(requests.Session)`` called as ``self.get(url)`` or
  ``Api().post(url)``;
* Java ``class R extends FileInputStream`` called as ``read()`` / ``r.read()``;
* JavaScript ``class Conn extends net.Socket`` called as ``c.connect(80)``.

Every one of those calls arrives with the IN-REPO owner (``App.Repo``, ``Api``,
``R``, ``Conn``) and the catalogue keys the row on the LIBRARY owner
(``Ecto.Repo``, ``requests.Session``, ``java.io.FileInputStream``,
``net.Socket``), so the call landed in ``external_potential`` and a security
claim about the boundary read inconclusive -- the Repo calls of every Phoenix
application among them (WI-gabuv).

ONE FACT, STAMPED ONCE, READ BY EVERY CATALOGUE CONSUMER. Finalize (sub-step 7d,
after the edge-resolution verdict and stub demotion) calls
:func:`stamp_external_owner_ancestors`, which writes
``meta["external_owner_ancestors"]`` on each unresolved ``calls`` edge whose owner
qualifies: the external ancestor names, nearest first. The io-boundary matcher
(``io_boundary.classify_call_in_catalog``) and taint's
(``taint._match_propagation_entry``) both call :func:`ancestor_retry_hints`,
which reads the stamp through :func:`external_owner_ancestors` and returns the
call's own module hint followed by each ancestor, nearest first; each is tried
as the module before the usual lookup. A stamp rather than a consumer-side derivation because the consumers
hold different shapes -- io-boundaries real ``Edge`` objects, verify-claims and
taint serialized dicts without the inheritance edges at hand -- and the meta
travels with every one of them. No analyzer changes: the producer reads the
relationship edges and the call-site owner hints every analyzer already emits.

WHICH OWNER A CALL HAS, in this order (the first that names exactly one in-repo
type in the call's language wins; a name shared by two such types names none):

1. the typed receiver -- ``receiver_type_id``, else ``receiver_type_hint``
   (Site 2 of the inherited-calls linker);
2. the module slot of the dst, when it names an in-repo type or module
   (Elixir's ``App.Repo.all``; a static call through a subclass);
3. the enclosing class of a bare / ``self`` / ``this`` call --
   ``enclosing_class_id``, else ``enclosing_class`` (Site 1), and never when
   ``inherited_field_receiver`` says the call goes through a field (Site 3).

The analyzers already confine those hints to the shapes they name (a Python bare
``helper()`` inside a method carries no ``enclosing_class``), which is what the
inherited-calls linker relies on too.

PRECEDENCE, decided here and pinned by tests:

* OWN MEMBER BEATS INHERITED ROW. If the owner or any IN-REPO ancestor declares a
  symbol named for the member (``Owner.member``, also ``#`` / ``::``), nothing is
  stamped: the call runs the application's code. (Such a call is normally bound
  already; this check is what makes the stamp safe when it is not.)
* A ROW KEYED ON THE OWNER AS WRITTEN BEATS AN ANCESTOR'S. The consumers try the
  call's own module first with the short-name fallback withheld, so a user overlay
  that catalogues ``App.Repo`` itself still decides.
* NEAREST ANCESTOR WINS. The walk is breadth-first through in-repo ancestors
  (``extends`` / ``inherits`` / ``implements`` / ``includes``), so a library
  parent one hop up precedes one three hops up.
* TIES AT ONE DEPTH go by relationship, then declaration order: ``extends`` /
  ``inherits`` before ``implements`` before ``includes`` (Java's
  superclass-before-interfaces rule; Python's left-to-right bases), first row
  found wins. Scala's right-to-left trait linearization is NOT modelled; there
  the leftmost external parent with a row wins.
* AN EXTERNAL PARENT IS NAMED AS THE ANALYZER WROTE IT -- ``requests.Session``,
  ``Ecto.Repo``, an unqualified Java ``FileInputStream`` -- and the catalogue's
  own module matching (component-wise, so ``FileInputStream`` reaches
  ``java.io.FileInputStream``) decides; the ancestor retry never uses the
  bare-short-name fallback.

FALSE POSITIVES THIS DOES NOT MAKE. An in-repo class merely NAMED like a library
class (``class Session``) is an in-repo ancestor: its ``extends`` edge resolved
to the in-repo symbol, so the walk continues through it and never treats it as
``requests.Session``. A call whose owner is unknown, ambiguous or external gets no
stamp, so every edge without one classifies exactly as before.

WHAT IT CANNOT SEE. Virtual dispatch downward: a call on a declared type
``MyReader extends InputStream`` that an in-repo SUBCLASS overrides is still
credited to the library row -- the same answer a receiver typed ``InputStream``
already gets. A member injected by ``use X`` where X's ``__using__`` declares no
behaviour has no relationship edge to walk (``USE_IMPLIED_BEHAVIOURS`` in the
Elixir analyzer is the table of the ones that do).
"""
from __future__ import annotations

from collections import deque
from typing import TYPE_CHECKING, Any, Iterable, Mapping, Optional

from .edge_types import INHERITANCE_EDGE_TYPES
from .ir import symbol_name_slot, symbol_path_slot
from .member_names import MEMBER_NAME_SEPARATORS
from .symbol_kinds import type_like_kind_names

if TYPE_CHECKING:  # pragma: no cover - typing only
    from .ir import Edge, Symbol

#: The ``Edge.meta`` key this module writes and the catalogue consumers read.
EXTERNAL_OWNER_ANCESTORS_KEY = "external_owner_ancestors"

#: The kind of an external placeholder node. A parent that IS one is a library
#: type; any other node kind is in-repo and is walked through.
_EXTERNAL_NODE_KIND = "external_symbol"

#: Terminal-slot values an external parent's id may carry: the producer-time
#: ``unresolved`` and the post-remap ``external_symbol`` (WI-pubiv). Not named
#: ``*_KINDS``: ``unresolved`` is a dst-id spelling, not a registered symbol
#: kind (``verify_claims._EXTERNAL_DST_TERMINAL_SLOTS`` records the same
#: two-vocabulary slot).
_EXTERNAL_ID_TERMINAL_SLOTS = frozenset({"unresolved", _EXTERNAL_NODE_KIND})

#: Path-slot values that carry no module (the external sentinel).
_NO_MODULE = frozenset({"", "external", "<external>"})

#: Relationship priority within one depth: a superclass before an interface
#: before a mixin. ``includes`` joins the registry family here for Ruby's
#: ``include`` / ``extend``; only edges whose src is a type are ever walked, so a
#: file-inclusion ``includes`` cannot reach the walk.
_RELATION_PRIORITY: Mapping[str, int] = {
    "extends": 0, "inherits": 0, "implements": 1, "includes": 2,
}
_WALKED_EDGE_TYPES = frozenset(INHERITANCE_EDGE_TYPES | {"includes"})

#: The walk's depth cap, the inherited-calls linker's.
_DEPTH_CAP = 10


def external_owner_ancestors(meta: Optional[Mapping[str, Any]]) -> tuple[str, ...]:
    """The stamped external ancestors of a call's owner, nearest first.

    The one reader of :data:`EXTERNAL_OWNER_ANCESTORS_KEY`, so the io-boundary
    and taint consumers cannot disagree about its shape. Empty when absent or
    malformed: an absent stamp asserts nothing.
    """
    value = (meta or {}).get(EXTERNAL_OWNER_ANCESTORS_KEY)
    if not isinstance(value, list):
        return ()
    return tuple(v for v in value if isinstance(v, str) and v)


def ancestor_retry_hints(
    module_hint: Optional[str], meta: Optional[Mapping[str, Any]],
) -> tuple[Optional[str], ...]:
    """The module hints a catalogue consumer tries, without the short-name fallback,
    BEFORE its usual lookup: the call's own hint, then each stamped ancestor.

    Empty when the edge carries no stamp, so an unstamped call takes exactly the
    path it took before. One helper for io-boundaries and taint, so the two
    consumers apply the same precedence (own owner's row, then nearest ancestor).
    """
    ancestors = external_owner_ancestors(meta)
    return (module_hint, *ancestors) if ancestors else ()


def _owner_kinds() -> frozenset[str]:
    """Kinds that can own a member: every nominal type, plus ``module``.

    ``module`` because an Elixir (or Ruby) module owns its functions and is what
    ``use`` / ``include`` attaches a library to.
    """
    return frozenset(type_like_kind_names() | {"module"})


def _external_parent_name(dst: str) -> Optional[str]:
    """The library name an external parent id spells, or ``None``.

    ``python:requests:0-0:Session:external_symbol`` -> ``requests.Session``;
    ``elixir:external:0-0:Ecto.Repo:unresolved`` -> ``Ecto.Repo``;
    ``java:external:0-0:FileInputStream:external_symbol`` -> ``FileInputStream``.
    A name that already starts with its module is not prefixed twice.
    """
    if dst.rsplit(":", 1)[-1] not in _EXTERNAL_ID_TERMINAL_SLOTS:
        return None
    name = symbol_name_slot(dst)
    if not name:
        return None
    module = symbol_path_slot(dst)
    if module in _NO_MODULE or name.startswith(module + "."):
        return name
    return f"{module}.{name}"


class _Index:
    """The graph lookups one stamping pass needs, built once."""

    def __init__(self, symbols: "Iterable[Symbol]", edges: "Iterable[Edge]") -> None:
        owner_kinds = _owner_kinds()
        self.by_id: dict[str, "Symbol"] = {}
        self.owners_by_name: dict[tuple[str, str], list["Symbol"]] = {}
        self.names: set[tuple[str, str]] = set()
        for sym in symbols:
            self.by_id[sym.id] = sym
            key = (sym.language or "", sym.name)
            self.names.add(key)
            if sym.kind in owner_kinds:
                self.owners_by_name.setdefault(key, []).append(sym)
        self.parents: dict[str, list[tuple[int, int, str]]] = {}
        for order, edge in enumerate(edges):
            if edge.edge_type in _WALKED_EDGE_TYPES:
                self.parents.setdefault(edge.src, []).append(
                    (_RELATION_PRIORITY[edge.edge_type], order, edge.dst),
                )
        for entries in self.parents.values():
            entries.sort()

    def owner_by_id(self, sym_id: Any, language: str) -> Optional["Symbol"]:
        """The in-repo owner a hint id names, if it names one in ``language``."""
        sym = self.by_id.get(sym_id) if isinstance(sym_id, str) else None
        if sym is None or sym.language != language or sym.kind not in _owner_kinds():
            return None
        return sym

    def owner_by_name(self, name: Any, language: str) -> Optional["Symbol"]:
        """The ONE in-repo owner named ``name`` in ``language``, else ``None``."""
        if not isinstance(name, str) or not name:
            return None
        found = self.owners_by_name.get((language, name), [])
        return found[0] if len(found) == 1 else None


def _call_owner(edge: "Edge", index: _Index, language: str) -> Optional["Symbol"]:
    """The in-repo type a call is made on, by the module docstring's order."""
    meta = edge.meta or {}
    if "receiver_type_id" in meta or "receiver_type_hint" in meta:
        return (index.owner_by_id(meta.get("receiver_type_id"), language)
                or index.owner_by_name(meta.get("receiver_type_hint"), language))
    module = symbol_path_slot(edge.dst)
    if module not in _NO_MODULE:
        by_module = index.owner_by_name(module, language)
        if by_module is not None:
            return by_module
    if "inherited_field_receiver" in meta:
        return None
    return (index.owner_by_id(meta.get("enclosing_class_id"), language)
            or index.owner_by_name(meta.get("enclosing_class"), language))


def _declares(index: _Index, owner: "Symbol", member: str) -> bool:
    """Does ``owner`` declare a symbol for ``member`` under any separator?"""
    return any(
        (owner.language or "", f"{owner.name}{sep}{member}") in index.names
        for sep in MEMBER_NAME_SEPARATORS
    )


def _ancestors_for(index: _Index, owner: "Symbol", member: str) -> list[str]:
    """External ancestors of ``owner`` nearest first, or ``[]`` if one declares it.

    Breadth-first through in-repo ancestors. An in-repo class on the chain that
    declares the member ends everything -- own member beats inherited row, however
    near the library parent is.
    """
    found: list[str] = []
    visited = {owner.id}
    queue: deque[tuple["Symbol", int]] = deque([(owner, 0)])
    while queue:
        current, depth = queue.popleft()
        if _declares(index, current, member):
            return []
        if depth >= _DEPTH_CAP:
            continue
        for _priority, _order, parent_id in index.parents.get(current.id, ()):
            if parent_id in visited:
                continue
            visited.add(parent_id)
            parent = index.by_id.get(parent_id)
            if parent is not None and parent.kind != _EXTERNAL_NODE_KIND:
                queue.append((parent, depth + 1))
                continue
            name = _external_parent_name(parent_id)
            if name is not None and name not in found:
                found.append(name)
    return found


def stamp_external_owner_ancestors(
    symbols: "Iterable[Symbol]", edges: "list[Edge]",
) -> int:
    """Stamp :data:`EXTERNAL_OWNER_ANCESTORS_KEY` on qualifying calls; return how many.

    Only an UNRESOLVED ``calls`` edge is considered: a resolved one is bound to an
    in-repo symbol already. Idempotent -- a re-run recomputes the same list.
    """
    index = _Index(symbols, edges)
    stamped = 0
    for edge in edges:
        if edge.edge_type != "calls" or edge.is_resolved:
            continue
        member = symbol_name_slot(edge.dst).rsplit(".", 1)[-1]
        language = edge.dst.split(":", 1)[0]
        owner = _call_owner(edge, index, language) if member else None
        if owner is None:
            continue
        ancestors = _ancestors_for(index, owner, member)
        if not ancestors:
            continue
        if edge.meta is None:
            edge.meta = {}
        edge.meta[EXTERNAL_OWNER_ANCESTORS_KEY] = ancestors
        stamped += 1
    return stamped
