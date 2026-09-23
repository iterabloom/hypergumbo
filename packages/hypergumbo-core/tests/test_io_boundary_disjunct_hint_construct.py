# SPDX-License-Identifier: AGPL-3.0-or-later
"""INV-nizom: a DISJUNCTIVE module slot is not receiver evidence.

``gate_named_entry`` (io-boundary:F3) refuses to let a *method* call match a
*function*-kind entry, because with no receiver type ``std::future::wait()``
and POSIX ``wait()`` are the same short name. That refusal runs only on the
no-module-hint branch of ``lookup_with_module``; an edge carrying a module hint
returns from the module-filter branch above and its construct is never read.

C++ LANDS ON THE WRONG SIDE OF THAT LINE. ``cpp.py`` sets an unresolved call's
module slot to the comma-joined list of every ``#include`` in the file, and
states the contract itself: *"the semantics is 'this call could be from any of
the included headers'"*. That is an uncertainty set, not a claim about the
receiver — but it tests as a usable hint, so ``fut.wait()`` in a file that
includes ``<sys/wait.h>`` matched ``sys/wait.wait`` (kind=function) and became
a subprocess boundary. Filed on whisper.cpp as 2 of 59 recovered boundaries.

WHY THE OBVIOUS RULE IS WRONG, MEASURED. "Refuse function-kind whenever the
construct is method" destroys real detections, because ``call_construct ==
"method"`` does NOT mean "instance method". Go's grammar spells ``os.Open(p)``
as a selector expression, identical in shape to ``f.Close()``, and the analyzer
stamps both ``method``::

    os.Open(p)   construct=method  module_slot=os        <- package FUNCTION
    f.Close()    construct=method  module_slot=external  <- instance METHOD

Applied to whisper.cpp that rule removed **51** matches: the 2 target false
positives and **49** true ones — ``os.Open``, ``os.Stat``, ``fmt.Fprintln``,
``net/http.NewRequest``, ``logging.exception``, ``os.makedirs``. The module hint
is exactly what disambiguates those, which is why the gate defers to it.

THE DISCRIMINATOR IS THE HINT'S ARITY, and it is already computed.
``_module_hint_candidates`` expands a slot into the spellings it may stand for;
its own docstring notes that "a language emitting ONE module per slot is
unaffected — the parts collapse back to the whole". Measured::

    os / fmt / net/http / java.io.File           -> 1 candidate   definite
    sys/wait.h,condition_variable,future,mutex   -> 6 candidates  disjunction
    stdio.h                                      -> 2 candidates  disjunction

So: when the slot names ONE module it is receiver evidence and decides; when it
expands to several it is file context, no better than no hint at all, and the
construct rule applies. Go and Python never reach the new branch.

JAVA DOES, and that sentence used to name it too. INV-hahak later made java
write a wildcard file's receiver as a disjunction of packages, one per wildcard
plus ``java.lang``. That disjunction names ONE owner in several packages, so it
IS receiver evidence, and :func:`slot_names_one_owner` exempts it (INV-zikab
step 4, the tests at the bottom of this file).
"""
from __future__ import annotations

from hypergumbo_core.io_boundary import load_catalog

_CPP_INCLUDES = "sys/wait.h,condition_variable,future,mutex"


def test_method_call_refused_against_function_kind_under_disjunct_hint() -> None:
    """THE DEFECT: ``fut.wait()`` must not become a subprocess boundary."""
    cat = load_catalog("cpp")
    hit = cat.lookup_with_module("wait", _CPP_INCLUDES, call_construct="method")
    assert hit is None, (
        f"C++ method call matched function-kind {hit.module}.{hit.name}"
    )


def test_free_function_still_matches_under_disjunct_hint() -> None:
    """POSITIVE CONTROL: real POSIX ``wait(&status)`` survives.

    An absent construct stays permissive — the 59 recovered C++ boundaries
    (``getenv``, ``fork``, ``execvp``, the socket set) depend on it.
    """
    cat = load_catalog("cpp")
    hit = cat.lookup_with_module("wait", _CPP_INCLUDES, call_construct=None)
    assert hit is not None and hit.name == "wait" and hit.kind == "function"


def test_single_include_disjunction_also_refused() -> None:
    """A one-``#include`` file is still file context, not receiver evidence.

    ``stdio.h`` expands to two candidates via the ``.h``-stripping rule, so it
    takes the same branch. Pinned because keying on a literal comma instead of
    the expansion would silently exempt this case.
    """
    cat = load_catalog("cpp")
    assert cat.lookup_with_module(
        "fopen", "stdio.h", call_construct="method",
    ) is None
    assert cat.lookup_with_module(
        "fopen", "stdio.h", call_construct=None,
    ) is not None


def test_definite_go_package_hint_is_untouched() -> None:
    """THE CONTROL THAT KILLED THE FIRST ATTEMPT.

    ``os.Open`` is a package-qualified FUNCTION that Go stamps ``method``,
    because a selector expression is its only spelling. A rule that refused
    function-kind on construct alone removed 49 of these on one repo.
    """
    cat = load_catalog("go")
    for name, module in (("Open", "os"), ("Stat", "os")):
        hit = cat.lookup_with_module(name, module, call_construct="method")
        assert hit is not None, f"destroyed real Go detection {module}.{name}"
        assert hit.kind == "function"


def test_definite_python_module_hint_is_untouched() -> None:
    """Same control on the other language the first attempt damaged."""
    cat = load_catalog("python")
    hit = cat.lookup_with_module("makedirs", "os", call_construct="method")
    assert hit is not None and hit.kind == "function"


def test_method_kind_entry_still_matches_under_definite_hint() -> None:
    """NON-DESTRUCTION: the INV-linub / PR #227 java receiver-typing win."""
    cat = load_catalog("java")
    hit = cat.lookup_with_module(
        "createNewFile", "java.io.File", call_construct="method",
    )
    assert hit is not None and hit.kind == "method"


# ---------------------------------------------------------------------------
# INV-zikab step 4 (ADR-0059): a disjunction of ONE owner's qualifications is
# receiver evidence, not file context.
# ---------------------------------------------------------------------------

#: What java emits for ``System.currentTimeMillis()`` in a jedis file carrying
#: wildcard imports: every package the receiver name ``System`` could come from
#: (INV-hahak's ``_wildcard_candidate_slot``). Copied from the jedis survey.
_JAVA_SYSTEM_DISJUNCTION = (
    "redis.clients.jedis.System,"
    "redis.clients.jedis.mcf.JedisFailoverException.System,java.lang.System"
)


def _java_with_system_statics_as_functions():
    """The java catalogue with ``java.lang.System``'s statics keyed as ADR-0059
    says they are -- called on the class itself, so function-kind. Built in
    memory because the shipped rows move only after this arm is fixed (the
    ledger names INV-pimir as their blocker)."""
    import dataclasses

    from hypergumbo_core.io_primitive_kinds import KIND_FUNCTION, KIND_METHOD

    cat = load_catalog("java")
    prims = [dataclasses.replace(p, kind=KIND_FUNCTION)
             if p.module == "java.lang.System" and p.kind == KIND_METHOD else p
             for p in cat.primitives]
    return dataclasses.replace(cat, primitives=prims, _by_qualified={},
                               _by_qualified_all={}, _by_short={})


def test_a_one_owner_disjunction_names_the_receiver() -> None:
    """THE JEDIS LOSS. Every disjunct is a package-qualified spelling of the one
    name the source wrote before the dot, so the slot says WHICH owner the call
    is on, just less definitely than a single module does. The call is
    ``System.currentTimeMillis()``: a static, called on the class.

    Before step 4 this returned None, and 36 jedis classifications went with it
    the moment the statics were keyed function (measure.py arm B)."""
    cat = _java_with_system_statics_as_functions()
    hit = cat.lookup_with_module(
        "currentTimeMillis", _JAVA_SYSTEM_DISJUNCTION, call_construct="method")
    assert hit is not None and (hit.module, hit.name) == (
        "java.lang.System", "currentTimeMillis")


def test_the_shipped_method_rows_still_match_under_it() -> None:
    """REACH / NON-DESTRUCTION on the shipped catalogue, where the statics are
    still method rows: the arm never touched them, and must not start to."""
    hit = load_catalog("java").lookup_with_module(
        "currentTimeMillis", _JAVA_SYSTEM_DISJUNCTION, call_construct="method")
    assert hit is not None and hit.module == "java.lang.System"


def test_an_include_set_is_still_file_context() -> None:
    """The cpp defect stays fixed: an ``#include`` set names DIFFERENT owners,
    so it is not receiver evidence, whatever else changes."""
    assert load_catalog("cpp").lookup_with_module(
        "wait", _CPP_INCLUDES, call_construct="method") is None


def test_two_headers_with_one_basename_are_the_stated_limit() -> None:
    """THE STATED LIMIT, pinned so it is a visible decision rather than an
    accident: ``wait.h`` and ``sys/wait.h`` share the final component ``wait``,
    so a file including ONLY those two reads as one owner and ``fut.wait()``
    would match. A file calling a method on a ``std::future`` must include
    ``<future>``, which breaks the tie; the census over 21 surveys found no
    cpp slot whose disjuncts share a final component."""
    from hypergumbo_core.io_boundary import slot_names_one_owner

    assert slot_names_one_owner("wait.h,sys/wait.h")
    assert not slot_names_one_owner("sys/wait.h,future")


def test_the_one_owner_predicate() -> None:
    from hypergumbo_core.io_boundary import slot_names_one_owner

    assert slot_names_one_owner(_JAVA_SYSTEM_DISJUNCTION)
    assert slot_names_one_owner("java.util.List,java.lang.List")
    assert not slot_names_one_owner(_CPP_INCLUDES)
    # A single disjunct is not a disjunction: the arm's other condition decides.
    assert not slot_names_one_owner("stdio.h")
    assert not slot_names_one_owner("java.lang.System")
