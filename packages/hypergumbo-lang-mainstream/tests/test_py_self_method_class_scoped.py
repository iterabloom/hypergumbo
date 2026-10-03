# SPDX-License-Identifier: AGPL-3.0-or-later
"""WI-kutal: ``self.method()`` resolves through the CLASS ``self`` denotes,
never through the file-wide bare-name table.

Before this item, ``_process_call`` Case 2a answered ``self.<m>()`` with
``local_symbols.get(m)`` -- the flat, last-write-wins ``symbol_by_name`` dict
that holds every method of every class in the file under its SHORT name, next
to every module-level function. So the LAST ``def m`` in the file answered for
every ``self.m()`` in it: a class's own ``save`` lost to a later unrelated
class's ``save``, a class with no ``save`` at all bound to one, a nested class
bound to its outer class's method, and a module-level ``def save()`` answered
an instance call.

The rule now, by POSITION (LIVE.md §3, INV-mozas / INV-midag):

* the class is the one lexically enclosing the method whose ``self`` the call
  reads -- the innermost function frame that BINDS ``self`` (so a closure
  reads its method's ``self``, and a nested function with its own ``self``
  param does not);
* a ``@staticmethod``'s ``self`` and a module function's ``self`` denote no
  class (an under-determined receiver, INV-fahub), so they resolve nothing;
* the method is looked up in THAT class's own body only. A miss falls through
  to the unresolved edge carrying the Site-1 ``enclosing_class`` hint, so an
  inherited method -- same-file or cross-file -- is resolved by the
  ``inherited_calls`` linker's MRO walk, which is where the INV-guviv
  external-base gate lives.

Every two-candidate fixture asserts BOTH sides (LIVE.md §1 rule 6): a
name-keyed table keeps one symbol per name, so asserting one side only can be
vacuous.
"""

from __future__ import annotations

import json
from pathlib import Path

from hypergumbo_lang_mainstream.py import extract_nodes


def _analyze(tmp_path: Path, src: str):
    f = tmp_path / "m.py"
    f.write_text(src)
    return extract_nodes(f)


def _sym(res, name: str, line: int | None = None):
    found = [
        s for s in res.symbols
        if s.name == name and (line is None or s.span.start_line == line)
    ]
    assert len(found) == 1, (name, line, [(s.name, s.span.start_line) for s in res.symbols])
    return found[0]


def _self_calls_from(res, caller_id: str, attr: str):
    """Every ``calls`` edge for ``attr`` out of ``caller_id`` (resolved or not)."""
    by_id = {s.id: s for s in res.symbols}
    out = []
    for e in res.edges:
        if e.edge_type != "calls" or e.src != caller_id:
            continue
        if e.is_resolved:
            dst = by_id.get(e.dst)
            if dst is not None and dst.name.rsplit(".", 1)[-1] == attr:
                out.append(e)
        elif e.dst.endswith(f":{attr}:unresolved"):
            out.append(e)
    return out


def _single(edges):
    assert len(edges) == 1, edges
    return edges[0]


class TestOwnClassWins:
    def test_own_method_beats_later_same_named_method(self, tmp_path: Path) -> None:
        # Order defines save; a LATER class Fee also defines save. Pre-fix the
        # last write (Fee.save) answered Order.finish's self.save().
        src = (
            "class Order:\n"
            "    def save(self):\n"
            "        pass\n"
            "    def finish(self):\n"
            "        self.save()\n"
            "class Fee:\n"
            "    def save(self):\n"
            "        pass\n"
            "    def finish(self):\n"
            "        self.save()\n"
        )
        res = _analyze(tmp_path, src)
        order_save = _sym(res, "Order.save")
        fee_save = _sym(res, "Fee.save")
        e1 = _single(_self_calls_from(res, _sym(res, "Order.finish").id, "save"))
        e2 = _single(_self_calls_from(res, _sym(res, "Fee.finish").id, "save"))
        assert (e1.is_resolved, e1.dst) == (True, order_save.id)
        assert (e2.is_resolved, e2.dst) == (True, fee_save.id)

    def test_class_without_method_never_binds_unrelated_class(
        self, tmp_path: Path
    ) -> None:
        # Direct defines no save and has no base; Fee.save is an unrelated
        # class's method. The call stays unresolved, carrying the Site-1 hint
        # (the enclosing class name AND its authoritative id).
        src = (
            "class Direct:\n"
            "    def go(self):\n"
            "        self.save()\n"
            "class Fee:\n"
            "    def save(self):\n"
            "        pass\n"
        )
        res = _analyze(tmp_path, src)
        direct = _sym(res, "Direct")
        e = _single(_self_calls_from(res, _sym(res, "Direct.go").id, "save"))
        assert e.is_resolved is False
        assert (e.meta or {}).get("enclosing_class") == "Direct"
        assert (e.meta or {}).get("enclosing_class_id") == direct.id

    def test_module_function_namesake_never_answers_self_call(
        self, tmp_path: Path
    ) -> None:
        # A module-level def save() is not an attribute of any instance.
        src = (
            "class Sub:\n"
            "    def run(self):\n"
            "        return self.save()\n"
            "def save():\n"
            "    return 0\n"
        )
        res = _analyze(tmp_path, src)
        e = _single(_self_calls_from(res, _sym(res, "Sub.run").id, "save"))
        assert e.is_resolved is False
        assert (e.meta or {}).get("enclosing_class") == "Sub"

    def test_last_definition_in_one_class_wins(self, tmp_path: Path) -> None:
        # Within ONE class body Python's class dict is last-write-wins too, so
        # the later def is the one an instance call reaches.
        src = (
            "class C:\n"
            "    def m(self):\n"
            "        return 1\n"
            "    def m(self):\n"
            "        return 2\n"
            "    def run(self):\n"
            "        return self.m()\n"
        )
        res = _analyze(tmp_path, src)
        later = _sym(res, "C.m", line=4)
        e = _single(_self_calls_from(res, _sym(res, "C.run").id, "m"))
        assert (e.is_resolved, e.dst) == (True, later.id)


class TestClassFoundByPosition:
    def test_nested_class_does_not_bind_outer_method(self, tmp_path: Path) -> None:
        # Inner's self is an Inner; Outer.save is not on Inner's MRO.
        src = (
            "class Outer:\n"
            "    def save(self):\n"
            "        pass\n"
            "    def keep(self):\n"
            "        self.save()\n"
            "    class Inner:\n"
            "        def run(self):\n"
            "            self.save()\n"
        )
        res = _analyze(tmp_path, src)
        outer_save = _sym(res, "Outer.save")
        inner = _single(_self_calls_from(res, _sym(res, "Inner.run").id, "save"))
        keep = _single(_self_calls_from(res, _sym(res, "Outer.keep").id, "save"))
        assert inner.is_resolved is False
        assert (keep.is_resolved, keep.dst) == (True, outer_save.id)

    def test_same_named_classes_each_bind_their_own(self, tmp_path: Path) -> None:
        # Two classes named A in one file: a NAME cannot tell them apart, the
        # lexical position can.
        src = (
            "def make1():\n"
            "    class A:\n"
            "        def save(self):\n"
            "            pass\n"
            "        def run(self):\n"
            "            self.save()\n"
            "    return A\n"
            "def make2():\n"
            "    class A:\n"
            "        def save(self):\n"
            "            pass\n"
            "        def run(self):\n"
            "            self.save()\n"
            "    return A\n"
        )
        res = _analyze(tmp_path, src)
        save1 = _sym(res, "A.save", line=3)
        save2 = _sym(res, "A.save", line=10)
        e1 = _single(_self_calls_from(res, _sym(res, "A.run", line=5).id, "save"))
        e2 = _single(_self_calls_from(res, _sym(res, "A.run", line=12).id, "save"))
        assert (e1.is_resolved, e1.dst) == (True, save1.id)
        assert (e2.is_resolved, e2.dst) == (True, save2.id)

    def test_closure_reads_its_methods_self(self, tmp_path: Path) -> None:
        # cb binds no self, so self is the closure-captured C instance of the
        # enclosing method: C.helper, not the later Other.helper.
        src = (
            "class C:\n"
            "    def helper(self):\n"
            "        pass\n"
            "    def run(self):\n"
            "        def cb():\n"
            "            self.helper()\n"
            "        return cb\n"
            "class Other:\n"
            "    def helper(self):\n"
            "        pass\n"
        )
        res = _analyze(tmp_path, src)
        c_helper = _sym(res, "C.helper")
        e = _single(_self_calls_from(res, _sym(res, "run.cb").id, "helper"))
        assert (e.is_resolved, e.dst) == (True, c_helper.id)


class TestUnderDeterminedSelf:
    """A ``self`` that does not denote an instance of the enclosing class
    resolves nothing (INV-fahub) -- pre-fix each bound the LAST ``helper`` in
    the file."""

    _TAIL = (
        "class Other:\n"
        "    def helper(self):\n"
        "        pass\n"
    )

    def test_staticmethod_self_param(self, tmp_path: Path) -> None:
        src = (
            "class C:\n"
            "    def helper(self):\n"
            "        pass\n"
            "    @staticmethod\n"
            "    def st(self):\n"
            "        self.helper()\n"
        ) + self._TAIL
        res = _analyze(tmp_path, src)
        e = _single(_self_calls_from(res, _sym(res, "C.st").id, "helper"))
        assert e.is_resolved is False

    def test_module_function_self_param(self, tmp_path: Path) -> None:
        src = (
            "def f(self):\n"
            "    self.helper()\n"
        ) + self._TAIL
        res = _analyze(tmp_path, src)
        e = _single(_self_calls_from(res, _sym(res, "f").id, "helper"))
        assert e.is_resolved is False

    def test_nested_function_own_self_param(self, tmp_path: Path) -> None:
        # inner binds its OWN self: it is whatever the caller passes, not
        # necessarily a C.
        src = (
            "class C:\n"
            "    def helper(self):\n"
            "        pass\n"
            "    def run(self):\n"
            "        def inner(self):\n"
            "            self.helper()\n"
            "        return inner\n"
        ) + self._TAIL
        res = _analyze(tmp_path, src)
        e = _single(_self_calls_from(res, _sym(res, "run.inner").id, "helper"))
        assert e.is_resolved is False


def _behavior_map(tmp_path: Path, files: dict[str, str]) -> dict:
    from hypergumbo_core.cli import run_behavior_map

    for name, text in files.items():
        (tmp_path / name).write_text(text)
    out_path = tmp_path / "out.json"
    run_behavior_map(
        repo_root=tmp_path, out_path=out_path, include_sketch_precomputed=False,
    )
    return json.loads(out_path.read_text())


def _node_id(data: dict, name: str) -> str:
    ids = [n["id"] for n in data["nodes"] if n.get("name") == name]
    assert len(ids) == 1, (name, ids)
    return ids[0]


def _resolved_calls(data: dict, src: str, dst: str) -> list[dict]:
    return [
        e for e in data["edges"]
        if e["type"] == "calls" and e["src"] == src and e["dst"] == dst
        and e.get("is_resolved")
    ]


class TestInheritedThroughTheLinker:
    """A same-file INHERITED method now reaches the ``inherited_calls``
    linker's MRO walk instead of being claimed by name in the analyzer."""

    def test_same_file_ancestor_resolves_via_mro_walk(self, tmp_path: Path) -> None:
        data = _behavior_map(tmp_path, {"m.py": (
            "class Base:\n"
            "    def pop(self):\n"
            "        pass\n"
            "class Sub2(Base):\n"
            "    def run(self):\n"
            "        self.pop()\n"
        )})
        calls = _resolved_calls(
            data, _node_id(data, "Sub2.run"), _node_id(data, "Base.pop"),
        )
        assert len(calls) == 1
        assert "inherited-calls-linker" in (calls[0].get("origin") or [])

    def test_same_file_builtin_base_shadow_mints_no_edge(
        self, tmp_path: Path
    ) -> None:
        # INV-guviv, same-file shape: Sub's real MRO is [Sub, dict, Base], so
        # self.pop() is dict.pop. Pre-fix Case 2a bound Base.pop by name before
        # the linker's builtin-base gate could see the call. The positive
        # control (Sub2, in-tree base only) proves the walk is reachable.
        data = _behavior_map(tmp_path, {"m.py": (
            "class Base:\n"
            "    def pop(self):\n"
            "        pass\n"
            "class Sub(dict, Base):\n"
            "    def run(self):\n"
            "        self.pop()\n"
            "class Sub2(Base):\n"
            "    def run(self):\n"
            "        self.pop()\n"
        )})
        base_pop = _node_id(data, "Base.pop")
        assert _resolved_calls(data, _node_id(data, "Sub.run"), base_pop) == []
        assert len(_resolved_calls(data, _node_id(data, "Sub2.run"), base_pop)) == 1


_DJANGO_MODELS = (
    "from django.db import models\n"
    "class Base(models.Model):\n"
    "    def save(self, *a, **k):\n"
    "        super().save(*a, **k)\n"
    "class Child(Base):\n"
    "    def go(self):\n"
    "        self.save()\n"
    "class Plain(models.Model):\n"
    "    def go(self):\n"
    "        self.save()\n"
)


class TestDjangoInstanceWriteAfterClassScoping:
    """``Plain.go``'s ``self.save()`` used to bind ``Base.save`` -- an
    unrelated model's override -- by bare name, so it never reached the ORM
    instance-write re-key. ``Child.go``'s reached the override correctly, and
    that bare-name bind was also what kept it from being re-keyed a second time
    (its write is typed at ``Base.save``'s ``super()`` call). Both sides are
    asserted: the write re-key fires for Plain and is refused for Child."""

    def test_plain_model_save_is_the_orm_write(self, tmp_path: Path) -> None:
        res = _analyze(tmp_path, _DJANGO_MODELS)
        e = _single(_self_calls_from(res, _sym(res, "Plain.go").id, "save"))
        assert e.is_resolved is False
        assert e.dst == "python:django.db.models:0-0:save:unresolved"
        assert (e.meta or {}).get("framework_dispatch") == "django_orm"

    def test_inherited_override_is_not_rekeyed(self, tmp_path: Path) -> None:
        res = _analyze(tmp_path, _DJANGO_MODELS)
        e = _single(_self_calls_from(res, _sym(res, "Child.go").id, "save"))
        assert e.is_resolved is False
        assert e.dst == "python:external:0-0:save:unresolved"
        assert "framework_dispatch" not in (e.meta or {})
        assert (e.meta or {}).get("enclosing_class") == "Child"

    def test_inherited_override_resolves_through_the_linker(
        self, tmp_path: Path
    ) -> None:
        data = _behavior_map(tmp_path, {"m.py": _DJANGO_MODELS})
        base_save = _node_id(data, "Base.save")
        assert len(_resolved_calls(data, _node_id(data, "Child.go"), base_save)) == 1
        assert _resolved_calls(data, _node_id(data, "Plain.go"), base_save) == []


class TestModuleLevelBlock:
    def test_file_without_functions_is_analyzed(self, tmp_path: Path) -> None:
        # The module-level block has no frame binding ``self`` and, in a file
        # with no function at all, runs before any per-function state exists.
        # A first cut computed the self-method table in the per-function loop
        # and crashed the whole Python pass here (NameError) on Django.
        res = _analyze(tmp_path, "import os\nos.getcwd()\nprint(1)\n")
        assert [e for e in res.edges if e.edge_type == "calls"]
