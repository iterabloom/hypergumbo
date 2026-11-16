# SPDX-License-Identifier: AGPL-3.0-or-later
"""A call through a nested class binds to the nested class and its method.

``A.B()``, ``A.B().go()``, ``b = A.B(); b.go()``, ``A.B.C().deep()`` and their
spellings from another module used to end on ``external_symbol`` stand-ins
(``python:external:0-0:B``, ``python:pkg.m.A:0-0:B``) although ``B`` and
``B.go`` are symbols of the repository: the resolver knew a class only by a
bare name or by ``module.Class``, and a method only as ``Class.method`` under
the class's SHORT name.

A dotted name is now walked from what its ROOT denotes (a class in scope, a
from-import, an imported module), one member at a time, and a call on what a
call returns is typed the way the assigned form is. Every fixture declares a
twin (``Z.B`` beside ``A.B``, each with its own ``go``) and asserts both
sides, so a table keyed by the short name cannot pass.

The assertions read the ANALYZER's edges (``analyze_python`` /
``extract_nodes``), before any linker: the method-call-recovery linker would
otherwise bind a unique method name by itself and hide a miss.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from hypergumbo_lang_mainstream.py import analyze_python, extract_nodes

M_PY = '''\
class Base:
    def inherited(self):
        return 0


class A:
    class B(Base):
        def go(self):
            return 1

        @staticmethod
        def st():
            return 2

        @classmethod
        def cm(cls):
            return cls()

        class C:
            def deep(self):
                return 3

    def run(self):
        return A.B().go()


class Z:
    class B:
        def go(self):
            return 9

        def st(self):
            return 8

        class C:
            def deep(self):
                return 7


def ctor():
    return A.B()


def chained():
    return A.B().go()


def via_name():
    b = A.B()
    return b.go()


def static_call():
    return A.B.st()


def class_call():
    return A.B.cm()


def deep():
    return A.B.C().deep()


def via_instance():
    b = A.B()
    c = b.C()
    return c.deep()


def other_owner():
    return Z.B().go()


def inherited_call():
    return A.B().inherited()


def inherited_static():
    return A.B.inherited()


def shadowed(A):
    return A.B().go()
'''

USER_PY = '''\
from pkg.m import A
import pkg.m
from pkg import m as mm


def elsewhere():
    return A.B().go()


def elsewhere_name():
    b = A.B()
    return b.go()


def elsewhere_static():
    return A.B.st()


def by_module():
    return pkg.m.A.B().go()


def by_alias():
    return mm.A.B().go()


def deep_elsewhere():
    return A.B.C().deep()
'''


@pytest.fixture(scope="module")
def repo(tmp_path_factory: pytest.TempPathFactory):
    root = tmp_path_factory.mktemp("nested")
    (root / "pkg").mkdir()
    (root / "pkg" / "__init__.py").write_text("")
    (root / "pkg" / "m.py").write_text(M_PY)
    (root / "pkg" / "user.py").write_text(USER_PY)
    return analyze_python(root)


def _sym(res, qualified: str):
    found = [s for s in res.symbols if s.qualified_name == qualified]
    assert len(found) == 1, (qualified, [s.qualified_name for s in res.symbols])
    return found[0]


def _targets(res, caller: str, edge_type: str) -> set[str]:
    """Qualified names of the RESOLVED ``edge_type`` targets of ``caller``."""
    by_id = {s.id: s for s in res.symbols}
    src = _sym(res, caller).id
    return {
        by_id[e.dst].qualified_name
        for e in res.edges
        if e.src == src and e.edge_type == edge_type and e.dst in by_id
    }


def _stand_ins(res, caller: str) -> set[str]:
    """Names of the unresolved callees of ``caller``."""
    src = _sym(res, caller).id
    return {
        e.dst.split(":")[-2]
        for e in res.edges
        if e.src == src and e.edge_type in ("calls", "instantiates")
        and not e.is_resolved
    }


class TestSameFile:
    @pytest.mark.parametrize(
        ("caller", "instantiates", "calls"),
        [
            ("ctor", {"A.B"}, set()),
            ("chained", {"A.B"}, {"A.B.go"}),
            ("via_name", {"A.B"}, {"A.B.go"}),
            ("A.run", {"A.B"}, {"A.B.go"}),
            ("static_call", set(), {"A.B.st"}),
            ("class_call", set(), {"A.B.cm"}),
            ("deep", {"A.B.C"}, {"A.B.C.deep"}),
            ("via_instance", {"A.B", "A.B.C"}, {"A.B.C.deep"}),
            # the twin: the same spelling under the other owner
            ("other_owner", {"Z.B"}, {"Z.B.go"}),
        ],
    )
    def test_the_call_binds_to_the_class_its_chain_names(
        self, repo, caller: str, instantiates: set[str], calls: set[str],
    ) -> None:
        assert _targets(repo, caller, "instantiates") == instantiates
        assert _targets(repo, caller, "calls") == calls
        assert _stand_ins(repo, caller) == set()

    def test_a_parameter_named_like_the_class_is_not_the_class(self, repo) -> None:
        # ``def shadowed(A): A.B().go()`` -- ``A`` is whatever the caller passes.
        assert _targets(repo, "shadowed", "instantiates") == set()
        assert _targets(repo, "shadowed", "calls") == set()

    @pytest.mark.parametrize("caller", ["inherited_call", "inherited_static"])
    def test_an_inherited_member_keeps_its_stand_in_and_names_the_receiver(
        self, repo, caller: str,
    ) -> None:
        # ``inherited`` is declared by a base of ``A.B``: not an own-body
        # member, so the analyzer does not bind it, but the unresolved edge
        # says which class the receiver is, for the linker that walks the MRO.
        src = _sym(repo, caller).id
        edges = [
            e for e in repo.edges
            if e.src == src and e.dst.endswith(":inherited:unresolved")
        ]
        assert len(edges) == 1
        assert not edges[0].is_resolved
        assert (edges[0].meta or {}).get("receiver_type_hint") == "B"
        assert _targets(repo, caller, "calls") == set()


class TestFromAnotherModule:
    @pytest.mark.parametrize(
        ("caller", "instantiates", "calls"),
        [
            ("elsewhere", {"A.B"}, {"A.B.go"}),          # from pkg.m import A
            ("elsewhere_name", {"A.B"}, {"A.B.go"}),
            ("elsewhere_static", set(), {"A.B.st"}),
            ("by_module", {"A.B"}, {"A.B.go"}),          # import pkg.m
            ("by_alias", {"A.B"}, {"A.B.go"}),           # from pkg import m as mm
            ("deep_elsewhere", {"A.B.C"}, {"A.B.C.deep"}),
        ],
    )
    def test_the_chain_is_walked_through_the_import(
        self, repo, caller: str, instantiates: set[str], calls: set[str],
    ) -> None:
        assert _targets(repo, caller, "instantiates") == instantiates
        assert _targets(repo, caller, "calls") == calls
        assert _stand_ins(repo, caller) == set()


class TestCallOnWhatACallReturns:
    """``X().m()`` is typed like ``x = X(); x.m()``, for any class."""

    SRC = (
        "class Client:\n"
        "    def send(self):\n        return 1\n"
        "class Other:\n"
        "    def send(self):\n        return 2\n"
        "def get_client() -> Client:\n"
        "    return Client()\n"
        "def inline():\n"
        "    return Client().send()\n"
        "def through_factory():\n"
        "    return get_client().send()\n"
        "def local_class():\n"
        "    class Local:\n"
        "        def send(self):\n            return 3\n"
        "    return Local().send()\n"
        "def unknown(make):\n"
        "    return make().send()\n"
    )

    @pytest.fixture()
    def res(self, tmp_path: Path):
        f = tmp_path / "m.py"
        f.write_text(self.SRC)
        return extract_nodes(f)

    @pytest.mark.parametrize(
        ("caller", "method"),
        [
            ("inline", "Client.send"),
            ("through_factory", "Client.send"),
            ("local_class", "local_class.Local.send"),
        ],
    )
    def test_the_method_is_the_returned_classs(self, res, caller: str, method: str) -> None:
        assert _targets(res, caller, "calls") - {"get_client"} == {method}

    def test_a_call_of_unknown_result_binds_nothing(self, res) -> None:
        assert _targets(res, "unknown", "calls") == set()
        assert _stand_ins(res, "unknown") == {"send"}

    def test_an_inherited_method_carries_the_returned_classs_id(
        self, tmp_path: Path,
    ) -> None:
        # One class of this name in the file, so the receiver hint may carry
        # its id (the same gate the assigned form passes).
        src = (
            "class Base:\n"
            "    def inherited(self):\n        return 0\n"
            "class Only(Base):\n    pass\n"
            "def use():\n"
            "    return Only().inherited()\n"
        )
        f = tmp_path / "m.py"
        f.write_text(src)
        res = extract_nodes(f)
        [edge] = [
            e for e in res.edges
            if e.src == _sym(res, "use").id and e.dst.endswith(":inherited:unresolved")
        ]
        assert (edge.meta or {}).get("receiver_type_hint") == "Only"
        assert (edge.meta or {}).get("receiver_type_id") == _sym(res, "Only").id


class TestRootMustBeAClass:
    def test_a_dotted_name_rooted_at_a_function_or_variable_resolves_nothing(
        self, tmp_path: Path,
    ) -> None:
        src = (
            "class A:\n"
            "    class B:\n        pass\n"
            "def holder():\n"
            "    class B:\n        pass\n"
            "    return B\n"
            "table = {}\n"
            "def use():\n"
            "    holder.B()\n"        # a function has no nested-class attribute
            "    table.B()\n"         # nor does a module variable
            "    return A.B()\n"
        )
        f = tmp_path / "m.py"
        f.write_text(src)
        res = extract_nodes(f)
        assert _targets(res, "use", "instantiates") == {"A.B"}

    def test_self_reaches_the_bodys_nested_class_chain(self, tmp_path: Path) -> None:
        src = (
            "class A:\n"
            "    class B:\n"
            "        @staticmethod\n"
            "        def make():\n            return 1\n"
            "        class C:\n            pass\n"
            "    def run(self):\n"
            "        self.B.make()\n"
            "        return self.B.C()\n"
            "class Z:\n"
            "    class B:\n"
            "        @staticmethod\n"
            "        def make():\n            return 2\n"
            "        class C:\n            pass\n"
        )
        f = tmp_path / "m.py"
        f.write_text(src)
        res = extract_nodes(f)
        assert _targets(res, "A.run", "calls") == {"A.B.make"}
        assert _targets(res, "A.run", "instantiates") == {"A.B.C"}
