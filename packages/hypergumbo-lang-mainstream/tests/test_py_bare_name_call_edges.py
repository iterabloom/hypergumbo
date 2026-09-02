# SPDX-License-Identifier: AGPL-3.0-or-later
"""INV-foluz: a bare-name call must emit an edge, whatever the callee is FOR.

THE DEFECT. ``py.py``'s terminal bare-``Name`` call arm emitted
``python:builtins:0-0:<name>:unresolved`` only when the name was in
:data:`BUILTIN_CONSTRUCTOR_NAMES` — a set whose *purpose* is I/O classification,
derived from ``io_primitives/python.yaml``. So the permitting condition was
"this builtin is a catalogued I/O primitive", and every callee needing an edge
for a NON-I/O reason was invisible by construction. Measured on the production
analyzer before the fix:

    helper(x)                 bare name DEFINED in file    EDGE
    os.remove(x)              module-qualified             EDGE
    x.decode()                simple-name receiver         EDGE
    open(p)                   builtin, WI-mitul's fix      EDGE
    print(x)                  builtin                      none
    len(items)                builtin                      none
    input()                   builtin                      none
    eval(x)                   builtin                      none
    getattr(x, "y")           builtin                      none
    undefined_fn(x)           unresolvable bare name       none

WHY IT IS NOT ABOUT I/O. ADR-0017 section 4 function summaries are how the
section 3a walk decides a use TERMINATES the taint rather than propagating it —
the difference between an accounted-for step and an ESCAPE. The section 4 index
holds a terminating summary for ``print`` under BOTH keys (``print`` and
``builtins.print``) and it could never be applied, because no edge was emitted
at a ``print()`` call site so the callee never reached ``callees_at``. This was
the LARGEST single cause of section 3a escapes: 50.0% of them (INV-busis, 26
sites, dev ecb954eb05).

THE FIX SPLITS TWO QUESTIONS ONE SET WAS ANSWERING. "Is this name a real
builtin?" is answered from :mod:`builtins` itself, which is what the arm's dst
slot actually asserts when it writes ``module_path="builtins"``. "Is it locally
rebound?" stays with the existing INV-kipor binding check. Keeping them fused is
what made a catalogue of I/O primitives the gate on a question about the Python
language.

WHY THE REBINDING CONTROL IS LOAD-BEARING, and why the gate was NOT simply
dropped. The arm's own comment records the case that justifies it: pretix's
``StreamWriter = codecs.getwriter('utf-8'); StreamWriter(data)`` is a LOCAL
REBINDING, and an ungated arm mints ``python:builtins:0-0:StreamWriter:unresolved``
— a fabricated builtin. :class:`TestLocalRebindingIsRefused` pins that it stays
refused; if it ever stops being refused, this fix has traded one defect for the
one it was built on top of.
"""

from __future__ import annotations

import builtins
from pathlib import Path

import pytest

from hypergumbo_lang_mainstream.py import (
    BUILTIN_CONSTRUCTOR_NAMES,
    PY_BUILTIN_CALLABLES,
    analyze_python,
)


FIXTURE = '''\
import os
import codecs


def helper(x):
    return x


def run(p, items, i, x, data):
    helper(x)
    os.remove(x)
    x.decode()
    open(p)
    print(x)
    len(items)
    input()
    eval(x)
    getattr(x, "y")
    undefined_fn(x)


def rebound(data):
    StreamWriter = codecs.getwriter('utf-8')
    StreamWriter(data)
'''


@pytest.fixture(scope="module")
def edges_by_line(tmp_path_factory: pytest.TempPathFactory) -> dict[int, list[str]]:
    """Run the PRODUCTION analyzer over the fixture; index dsts by call line.

    The fixture gets its own directory. An earlier probe pointed the analyzer at
    a scratch dir that also held the probe script, and the two files' line
    numbers collided — which read exactly like edges the fixture had produced.
    """
    repo = tmp_path_factory.mktemp("foluz_repo")
    src = repo / "fixture.py"
    src.write_text(FIXTURE)

    result = analyze_python(repo)
    by_line: dict[int, list[str]] = {}
    for edge in result.edges:
        if str(src) not in edge.src:
            continue
        by_line.setdefault(edge.line, []).append(edge.dst)
    return by_line


# Line numbers are DERIVED from the fixture text, never hand-counted: a
# hand-counted extent in a sibling rust fixture was off by one and surfaced as a
# CONTROL failing, which looked exactly like the fix breaking an earlier PR.
def _line_of(needle: str) -> int:
    for index, text in enumerate(FIXTURE.splitlines(), start=1):
        if text.strip() == needle:
            return index
    raise AssertionError(f"fixture has no line {needle!r}")  # pragma: no cover


class TestBuiltinCallsEmitAnEdge:
    """Each name INV-foluz measured as silent now reaches ``callees_at``."""

    @pytest.mark.parametrize(
        ("call", "name"),
        [
            ("print(x)", "print"),
            ("len(items)", "len"),
            ("input()", "input"),
            ("eval(x)", "eval"),
            ('getattr(x, "y")', "getattr"),
        ],
    )
    def test_builtin_call_emits_builtins_edge(
        self, edges_by_line: dict[int, list[str]], call: str, name: str
    ) -> None:
        dsts = edges_by_line.get(_line_of(call), [])
        assert f"python:builtins:0-0:{name}:unresolved" in dsts, (
            f"{call} emitted {dsts!r}; INV-foluz requires a builtins edge so a "
            f"section 4 summary for {name!r} can be consulted"
        )


class TestUnresolvableBareNameEmitsAnEdge:
    """A bare name that is neither imported, local, nor a builtin.

    ``external`` is the placeholder the matcher already degrades on, and the one
    the sibling attribute arm uses (WI-fuvuj / INV-mumov). Inventing a module
    here would assert a module that does not exist.
    """

    def test_unknown_bare_call_emits_external_edge(
        self, edges_by_line: dict[int, list[str]]
    ) -> None:
        dsts = edges_by_line.get(_line_of("undefined_fn(x)"), [])
        assert "python:external:0-0:undefined_fn:unresolved" in dsts, (
            f"undefined_fn(x) emitted {dsts!r}; every other analyzer in the "
            f"fleet emits an unresolved edge here via make_unresolved_edge"
        )


class TestLocalRebindingIsRefused:
    """THE CONTROL THIS FIX IS BUILT ON. Do not relax without measuring.

    ``StreamWriter = codecs.getwriter('utf-8')`` binds a local name that shadows
    nothing in :mod:`builtins`; the danger is the general shape, not this name.
    An arm that trusted "unbound" by default minted a fabricated builtin for it
    on the shipping tree.
    """

    def test_locally_rebound_name_mints_no_builtin(
        self, edges_by_line: dict[int, list[str]]
    ) -> None:
        dsts = edges_by_line.get(_line_of("StreamWriter(data)"), [])
        assert not any("builtins" in d for d in dsts), (
            f"StreamWriter(data) emitted {dsts!r} — a local rebinding must "
            f"never be asserted as a builtin"
        )


class TestModuleScopeRebindingIsRefused:
    """The ACTUAL pretix shape, which binds at MODULE scope, not inside a function.

    :class:`TestLocalRebindingIsRefused` puts the rebinding inside the calling
    function, so the enclosing scope's ``local_names`` carry it. The measured
    pretix case does not: ``StreamWriter = codecs.getwriter('utf-8')`` sits at
    module level and is CALLED from inside a function, so a guard that only
    consulted the callee's own frame would miss it. Pinned separately because
    the two reach the refusal by different routes — this one resolves to the
    module-level variable symbol before the builtin arm is ever considered.
    """

    MODULE_SCOPE = '''\
import codecs

StreamWriter = codecs.getwriter('utf-8')


def use(data):
    StreamWriter(data)


print("module level call")
'''

    def test_module_level_rebinding_and_module_level_builtin(
        self, tmp_path: Path
    ) -> None:
        src = tmp_path / "m.py"
        src.write_text(self.MODULE_SCOPE)
        dsts = [
            e.dst for e in analyze_python(tmp_path).edges if str(src) in e.src
        ]

        assert not any("builtins:0-0:StreamWriter" in d for d in dsts), (
            f"module-scope rebinding fabricated a builtin: {dsts!r}"
        )
        assert any("StreamWriter:variable" in d for d in dsts), (
            f"the call should reach the module-level variable symbol: {dsts!r}"
        )
        # A builtin called at MODULE scope still emits — the guard must refuse
        # rebound names, not every call outside a function.
        assert "python:builtins:0-0:print:unresolved" in dsts


class TestEnclosingScopeBindingIsRefused:
    """A CLOSURE-CAPTURED name is bound, and the immediate frame cannot see it.

    FOUND BY READING EMITTED ROWS BACK AGAINST SOURCE, not by reasoning about
    the guard. The first cut checked only the calling function's OWN frame, and
    pretix showed two shapes it admitted:

        control/permissions.py:114   function(request, *args, **kw)
        sentry.py:32                 weak_request()

    In both, the callee is a PARAMETER OF AN ENCLOSING function and the call
    sits in a nested one. Emitting there is wrong on its own terms — the callee
    is a value passed in, not an external symbol — but the load-bearing risk is
    the builtin arm: a closure-captured ``open`` or ``input`` would have minted
    a fabricated builtin, which is the ``StreamWriter`` defect reached by a
    different route. The guard is now the union over every LEGB frame.
    """

    CLOSURE = '''\
def decorator(function, open, data):
    def wrapper(request):
        function(request)
        open(data)
        return print(request)
    return wrapper
'''

    @pytest.fixture()
    def dsts(self, tmp_path: Path) -> list[str]:
        src = tmp_path / "c.py"
        src.write_text(self.CLOSURE)
        return [e.dst for e in analyze_python(tmp_path).edges if str(src) in e.src]

    def test_enclosing_param_is_not_an_external_symbol(
        self, dsts: list[str]
    ) -> None:
        assert not any("external:0-0:function" in d for d in dsts), dsts

    def test_enclosing_param_shadowing_a_builtin_mints_no_builtin(
        self, dsts: list[str]
    ) -> None:
        """The one that would have been a fabricated builtin."""
        assert not any("builtins:0-0:open" in d for d in dsts), dsts

    def test_an_unshadowed_builtin_in_the_same_function_still_emits(
        self, dsts: list[str]
    ) -> None:
        """The guard must refuse SHADOWED names, not every call in a closure."""
        assert "python:builtins:0-0:print:unresolved" in dsts, dsts


class TestPreExistingEmissionIsUnchanged:
    """Four controls in the same run, on shapes that already worked."""

    @pytest.mark.parametrize(
        ("call", "expected_fragment"),
        [
            ("helper(x)", ":helper:function"),
            ("os.remove(x)", "python:os:0-0:remove:unresolved"),
            ("x.decode()", "python:external:0-0:decode:unresolved"),
            ("open(p)", "python:builtins:0-0:open:unresolved"),
        ],
    )
    def test_shape_still_emits(
        self,
        edges_by_line: dict[int, list[str]],
        call: str,
        expected_fragment: str,
    ) -> None:
        dsts = edges_by_line.get(_line_of(call), [])
        assert any(expected_fragment in d for d in dsts), (
            f"{call} emitted {dsts!r}, losing {expected_fragment!r}"
        )


class TestPermittingSetIsKeyedOnTheLanguage:
    """The point of the fix: the gate asks about Python, not about I/O."""

    def test_every_permitted_name_is_a_real_builtin(self) -> None:
        """No fabrications: the set cannot admit a name Python does not have."""
        assert PY_BUILTIN_CALLABLES <= set(dir(builtins))

    def test_every_permitted_name_is_callable(self) -> None:
        """``print`` qualifies; ``__debug__`` (a bool) must not."""
        for name in PY_BUILTIN_CALLABLES:
            assert callable(getattr(builtins, name)), name

    def test_the_io_catalogue_set_no_longer_gates_emission(self) -> None:
        """The old gate is a strict SUBSET of the new one.

        ``BUILTIN_CONSTRUCTOR_NAMES`` still exists and still means "bare rows
        that are REAL builtins" for the receiver-typing consumer at
        ``_external_constructor_type``. What changed is that it no longer
        decides whether an edge is emitted.
        """
        assert BUILTIN_CONSTRUCTOR_NAMES < PY_BUILTIN_CALLABLES

    def test_the_names_inv_foluz_named_are_permitted(self) -> None:
        assert {"print", "len", "input", "eval", "getattr"} <= PY_BUILTIN_CALLABLES

    def test_dunder_builtins_are_included(self) -> None:
        """RE-POINTED FROM ITS OPPOSITE, by measurement rather than taste.

        This assertion first read ``not any(n.startswith("_") ...)``, justified
        as "``__import__`` is callable but is not a call shape worth an edge".
        The corpus disagreed: ``__import__`` has 8 call sites in
        ``hypergumbo-core`` alone, and excluding it did not suppress the edge —
        it pushed the call into the RESIDUAL arm, which emitted
        ``python:external:0-0:__import__``. That asserts an unknown external
        callee for a name :mod:`builtins` defines, which is strictly worse than
        either emitting it correctly or not at all.

        The exclusion removed exactly three names; the other two
        (``__build_class__``, ``__loader__``) are compiler hooks nobody writes.
        """
        assert "__import__" in PY_BUILTIN_CALLABLES

    def test_non_callable_builtins_are_excluded(self) -> None:
        """``__debug__`` is a bool and ``__doc__`` a str — neither is a callee."""
        assert "__debug__" not in PY_BUILTIN_CALLABLES
        assert "__doc__" not in PY_BUILTIN_CALLABLES
