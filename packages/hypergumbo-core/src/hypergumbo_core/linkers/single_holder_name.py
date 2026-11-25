# SPDX-License-Identifier: AGPL-3.0-or-later
"""Infrastructure linker: the last resort, a call bound by its name alone.

Why this exists
---------------
ADR-0064 moves the choice of a call's target out of the analyzers: an
analyzer binds on evidence the language itself uses and otherwise reports the
call with facts, and the shared passes decide (``inherited_calls``,
``receiver_type_dispatch``, ``method_call_recovery``). What is left after them
is a call nobody could scope: ``x.drain()`` with an ``x`` of unknown type.

Every analyzer used to answer that with a lookup of the name among
everything. The sample behind ADR-0064 (424 edges, 8 languages, judged in the
source) split that population in two: where SEVERAL declarations hold the
name the bind was wrong 57 times in 120, where ONE does it was wrong 10 times
in 96, and nine of those ten were calls that leave the repository. The first
half is a guess; the second is mostly right, and a dynamically typed language
has little else: on five JavaScript / TypeScript trees, deleting the name
lookup outright lost 11,805 resolved calls, 13 of 15 sampled ones right, and
the name had one holder in 10,652 of them.

So the name keeps ONE use, in one place, for every file whose analyzer has
adopted the pass AND screens the names of the language's own library (its
table, ``LinkerContext.import_scopes``, says so:
``ImportScope.library_names_screened``): a call no earlier linker resolved is bound
to the ONE declaration of the caller's language that has its name, and
marked ``resolved_by="name"``. Several holders: unresolved. A holder is a
METHOD for a call written on a receiver (``x.drain()``) and a FUNCTION for a
bare call (``drain()``), as in the lookups this replaces and the sample that
measured them: ``cd.shout()`` on an imported object is not the module-level
function ``shout`` of the same file, whatever the name.

When the name is refused although one declaration holds it
----------------------------------------------------------
The nine-in-ten wrongs are calls whose own text points away from the
repository. The stub's facts say so, and each refuses:

* ``receiver_type_hint``: the caller named the receiver's type. If the type
  lookup did not bind the call, the type is a library's, or the method is not
  on it. ``List<T>.drain()`` is not the repository's one ``drain``.
* ``enclosing_class`` / ``super_receiver`` / ``inherited_field_receiver``: the
  hierarchy walk has answered.
* ``qualifier`` (``Owner.f()``): the caller named an owner. In a language of
  declared names (the table's modules are namespaces) the type lookup is the
  whole answer. In a language whose imports name files, the name is tried
  only when the owner's first segment is a binding of the file's table, which
  holds bindings to files of the repository only: an imported object or
  instance (``import {store} from './store'; store.drain()``), whose type the
  analyzer could not say. ``console.log()`` binds nothing.
* ``stdlib_member_name``: the analyzer says the language's own library
  defines this name on its types (JavaScript ``push``, ``then``,
  ``forEach``). The library is then a second holder: ``items.push(x)`` on an
  untyped ``items`` is not the repository's one ``push``.
* A stated module (``dst_ref`` with a module path, ``ir.stated_module_of``)
  on a call with no qualifier: the producer said where the callee lives
  (``require('lib').m()``), and it is not here.
* A bare call (no receiver) whose name is not a binding of the file's table:
  in the adopted languages a bare name is lexical, imported or a global, and
  the first two were tried before this pass. A name the table does bind, to a
  file that does not declare it (a re-export through a barrel file), is looked
  up by its ORIGINAL name.

What this does not know: that a receiver's first name is not declared in the
caller's file at all (a global such as ``document``). No stub says so; a
single ``getElementById`` in a repository would be bound to it, unless the
analyzer lists the name as the library's.

Why the analyzer must screen library names first
-------------------------------------------------
Measured on the C# adapter, which has no list of the names the base class
library defines: of 15 calls this pass newly bound, read in the source, 5
were wrong (``string.Join(..)`` on the repository's one ``Join``,
``task.AsTask()``, ``stopwatch.Start()``). JavaScript before its list was
passed on: about 15 wrong of 20 (``items.push(x)``, ``p.catch(f)``). A
receiver nobody could type is, more often than not in a typed language, a
library value. So the pass does nothing for a file whose table does not say
its analyzer screens those names; the JavaScript / TypeScript analyzer marks
them (``stdlib_member_name``), the Rust analyzer withholds the stub
(``analyzer_disclosure.SUPPRESSED_METHOD_NAMES``).

The number
----------
0.7, for every bind made here: the confidence the JavaScript analyzer gave a
method found by name, the lowest of the numbers the replaced lookups used
(owner ruling 4 of ADR-0064: a name-only match keeps its number until the
owner decides what it becomes; none gains).

Order
-----
Priority 36: after ``inherited_calls`` (18), ``receiver_type_dispatch`` (19),
``rust_trait_dispatch`` (23) and ``method_call_recovery`` (35), each of which
has better evidence than a name. A stub one of them resolved is recognised by
the resolved edge that names it in ``derived_from``.
"""

from __future__ import annotations

import time

from .. import resolved_by
from ..ir import (
    PASS_VERSION, AnalysisRun, Edge, Symbol, make_pass_id, stated_module_of,
)
from ..import_scope import MODULES_ARE_FILES, ImportScope
from ..type_visibility import call_language, dotted
from .method_call_recovery import parse_unresolved_name, short_name
from .registry import (
    LinkerActivation,
    LinkerContext,
    LinkerResult,
    register_linker,
)

PASS_ID = make_pass_id("single-holder-name-linker")

#: What a holder of a name is, by how the call is written (module docstring).
_ON_A_RECEIVER, _BARE = "method", "function"

#: Stub facts that refuse the name: the four an earlier pass has already
#: read, and the analyzer's word that the library holds the name too.
_REFUSING_FACTS = (
    "receiver_type_hint", "enclosing_class", "super_receiver",
    "inherited_field_receiver", "stdlib_member_name",
)

_NAME_CONFIDENCE = 0.7


def _holder_to_look_up(edge: Edge, scope: ImportScope) -> tuple[str, str] | None:
    """The (kind, name) whose one holder this stub may be bound to, or
    ``None`` when the stub's own text refuses the name (module docstring)."""
    meta = edge.meta or {}
    if any(meta.get(fact) for fact in _REFUSING_FACTS):
        return None
    callee = parse_unresolved_name(edge.dst)
    if not callee:
        return None
    qualifier = meta.get("qualifier")
    if qualifier:
        owner = dotted(str(qualifier))
        head = owner.partition(".")[0]
        bound_to_the_repository = scope.module_kind == MODULES_ARE_FILES and (
            scope.named(head) is not None or scope.namespace(head) is not None
            or scope.namespace(owner) is not None
        )
        return (_ON_A_RECEIVER, callee) if bound_to_the_repository else None
    if meta.get("call_construct") == "method":
        return None if stated_module_of(edge) is not None else (_ON_A_RECEIVER, callee)
    bound = scope.named(callee)
    return (_BARE, bound.name) if bound is not None else None


@register_linker(
    "single-holder-name-linker",
    priority=36,  # Last of the call resolvers: after method-call-recovery (35).
    description=(
        "Binds a call no earlier pass resolved to the one method (a call on a "
        "receiver) or function (a bare call) of the caller's language that has "
        "its name (ADR-0064); refuses when "
        "the name has several holders or the call's own text points elsewhere."
    ),
    activation=LinkerActivation(always=True),
)
def link_single_holder_name(ctx: LinkerContext) -> LinkerResult:
    """See module docstring for the rule."""
    start_time = time.time()
    run = AnalysisRun.create(pass_id=PASS_ID, version=PASS_VERSION)
    new_edges: list[Edge] = []
    if not ctx.import_scopes:
        return LinkerResult(symbols=[], edges=new_edges, run=run)

    symbol_by_id: dict[str, Symbol] = {s.id: s for s in ctx.symbols}
    holders: dict[tuple[str, str, str], list[Symbol]] = {}
    for symbol in ctx.symbols:
        if symbol.kind in (_ON_A_RECEIVER, _BARE):
            key = (call_language(symbol.language or ""), symbol.kind, short_name(symbol.name))
            holders.setdefault(key, []).append(symbol)

    resolved_stubs: set[str] = set()
    existing_call_pairs: set[tuple[str, str]] = set()
    for edge in ctx.edges:
        if edge.edge_type == "calls" and edge.is_resolved:
            existing_call_pairs.add((edge.src, edge.dst))
            resolved_stubs.update(edge.derived_from or ())

    for edge in ctx.edges:
        if edge.edge_type != "calls" or edge.is_resolved or edge.id in resolved_stubs:
            continue
        caller = symbol_by_id.get(edge.src)
        scope = ctx.import_scopes.get(caller.path) if caller is not None else None
        if caller is None or scope is None or not scope.library_names_screened:
            continue
        wanted = _holder_to_look_up(edge, scope)
        found = holders.get((call_language(caller.language or ""), *wanted), ()) if wanted else ()
        if len(found) != 1 or (edge.src, found[0].id) in existing_call_pairs:
            continue
        new_edges.append(Edge.create(
            src=edge.src, dst=found[0].id, edge_type="calls",
            line=edge.line, confidence=_NAME_CONFIDENCE,
            origin=PASS_ID, origin_run_id=run.execution_id,
            evidence_type="ast_call", is_resolved=True,
            derived_from=[edge.id, found[0].id],
            resolved_by=resolved_by.NAME,
        ))
        existing_call_pairs.add((edge.src, found[0].id))

    run.duration_ms = int((time.time() - start_time) * 1000)
    return LinkerResult(symbols=[], edges=new_edges, run=run)
