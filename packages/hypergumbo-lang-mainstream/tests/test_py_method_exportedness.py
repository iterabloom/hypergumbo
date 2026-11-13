# SPDX-License-Identifier: AGPL-3.0-or-later
"""Every Python method gets an exportedness verdict, and it is conjunctive.

``Symbol.is_exported`` is tri-state, so ``null`` means "no producer computed
this" (INV-kubup). A method left at ``null`` is unmeasured, and the gap falls
the unsafe way: a PRIVATE-by-name method gets ``False`` downstream from the
``private`` modifier py.py stamps regardless, so the methods that depend on
an explicit verdict are the PUBLIC ones -- the only ones that might be
exported, and the ones that need the enclosing class's verdict. For the
dead-code seed set (WI-zimum) an unmeasured public method is unmeasured
exactly where exportedness changes the answer. (history: WI-kohah)

THE RULE IS CONJUNCTIVE: a public method of an exported class is
reachable from outside the package; a public method of a PRIVATE class is not.
``__all__`` does not list methods, so the class's own membership is the
authority, not the method's.

DUNDERS ARE PUBLIC PROTOCOL, not private -- ``__init__`` of an exported class is
reachable by constructing it. Treating dunders as private would flip a
downstream population rather than merely leave it unfilled, so this is asserted
here explicitly rather than left to follow from the helper.

REUSE, NOT RE-DERIVATION: the private-name convention comes from py.py's own
``_python_visibility_modifiers``, the same call that already fills this symbol's
``modifiers``, so the two facts cannot disagree. (``visibility.py``'s
``_is_python_private_name`` encodes the identical rule separately; the two
must be kept in step.)
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
