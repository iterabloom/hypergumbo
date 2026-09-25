# SPDX-License-Identifier: AGPL-3.0-or-later
"""Every Python method gets an exportedness verdict, and it is conjunctive
(WI-kohah).

INV-kubup made ``Symbol.is_exported`` tri-state, so ``null`` now honestly means
"no producer computed this". py.py sets it at four sites -- module-level
variable, class, class attribute, module-level function -- and at NONE for a
method, so Python methods were the largest single ``null`` population.

MEASURED BEFORE THE FIX, and this corrected the row's own repro: it was NOT
"all of them". On this repository's survey, 23,063 of 23,731 python methods
(97.2%) carried ``null`` and 668 carried ``False`` -- and the split was exact,
with zero exceptions in either direction: every PRIVATE-by-name method already
had ``False`` (derived downstream from the ``private`` modifier py.py stamps),
every other method had ``null``. So the half that was missing is the PUBLIC
half -- which is precisely the half that needs the enclosing class's verdict.

THE ASYMMETRY RAN THE UNSAFE WAY. The methods that HAD a verdict were the ones
that could never be exported; the ones with NO verdict were the only ones that
might be. For the WI-zimum dead-code seed set that means the seeds were
unmeasured exactly where exportedness changes the answer.

THE RULE IS CONJUNCTIVE, per the row: a public method of an exported class is
reachable from outside the package; a public method of a PRIVATE class is not.
``__all__`` does not list methods, so the class's own membership is the
authority, not the method's.

DUNDERS ARE PUBLIC PROTOCOL, not private -- ``__init__`` of an exported class is
reachable by constructing it. This is the one verdict that CHANGES a downstream
population rather than merely filling it in, so it is asserted here explicitly
rather than left to follow from the helper.

REUSE, NOT RE-DERIVATION: the private-name convention comes from py.py's own
``_python_visibility_modifiers``, the same call that already fills this symbol's
``modifiers``, so the two facts cannot disagree. (``visibility.py``'s
``_is_python_private_name`` encodes the identical rule; that duplication is
pre-existing and is not resolved here.)
"""
from __future__ import annotations

from pathlib import Path

from hypergumbo_core.ir import Symbol
from hypergumbo_lang_mainstream.py import analyze_python


def _methods(root: Path, src: str) -> dict[str, Symbol]:
    root.mkdir(parents=True, exist_ok=True)
    (root / "mod.py").write_text(src)
    return {
        s.name: s for s in analyze_python(root).symbols if s.kind == "method"
    }


_SRC = '''
__all__ = ["Public"]


class Public:
    def __init__(self):
        pass

    def visible(self):
        pass

    def _hidden(self):
        pass


class NotInAll:
    def visible(self):
        pass


class _PrivateClass:
    def visible(self):
        pass

    def _hidden(self):
        pass


def make():
    class Nested:
        def visible(self):
            pass
    return Nested
'''


class TestEveryMethodGetsAVerdict:
    def test_no_method_is_left_unmeasured(self, tmp_path: Path) -> None:
        got = _methods(tmp_path / "all", _SRC)
        assert got, "fixture produced no methods -- assertions would be vacuous"
        unmeasured = [n for n, s in got.items() if s.is_exported is None]
        assert unmeasured == []


class TestTheRuleIsConjunctive:
    def test_public_method_of_exported_class(self, tmp_path: Path) -> None:
        assert _methods(tmp_path / "a", _SRC)["Public.visible"].is_exported is True

    def test_private_method_of_exported_class(self, tmp_path: Path) -> None:
        assert _methods(tmp_path / "b", _SRC)["Public._hidden"].is_exported is False

    def test_public_method_of_private_class(self, tmp_path: Path) -> None:
        """The conjunction's whole point: a public name on a private class."""
        got = _methods(tmp_path / "c", _SRC)
        assert got["_PrivateClass.visible"].is_exported is False

    def test_public_method_of_class_absent_from_dunder_all(
        self, tmp_path: Path
    ) -> None:
        assert _methods(tmp_path / "d", _SRC)["NotInAll.visible"].is_exported is False

    def test_method_of_a_function_local_class(self, tmp_path: Path) -> None:
        """Not top-level, so the class is not exported and neither is its method."""
        assert _methods(tmp_path / "e", _SRC)["Nested.visible"].is_exported is False


class TestDundersAreProtocolNotPrivate:
    def test_init_of_an_exported_class_is_exported(self, tmp_path: Path) -> None:
        assert _methods(tmp_path / "f", _SRC)["Public.__init__"].is_exported is True
