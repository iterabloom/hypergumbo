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
construct rule applies. Go, Python and Java never reach the new branch.
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
