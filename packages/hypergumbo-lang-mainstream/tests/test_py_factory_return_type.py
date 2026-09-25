# SPDX-License-Identifier: AGPL-3.0-or-later
"""A local assigned from ``Cls.factory()`` or ``obj.build()`` takes the
callee's declared return type (WI-fihun blocker 1).

py.py already types a local from a BARE function call's declared return type
(``c = make()``), reading ``meta["return_type"]`` (WI-ribak). The assignment
path asks ``_resolve_call_target`` for the callee, and that only understood
``Name()`` and ``module.attr()`` -- so ``run = AnalysisRun.create(...)`` and
``c = factory.build()`` left the local untyped, and a later ``run.to_dict()``
fell to the method-call-recovery linker's line-proximity guess (three of
WI-fihun's fourteen wrong edges: ``Limits.to_dict``, ``Span.to_dict``,
``ExternalRef.to_dict``).

The receiver must itself be known: a CLASS in scope for ``Cls.m()``, or a
local ALREADY typed for ``obj.m()``. An untyped receiver resolves nothing,
which the negative control pins.
"""
from __future__ import annotations

from pathlib import Path

from hypergumbo_core.ir import Edge
from hypergumbo_lang_mainstream.py import analyze_python

_SRC = '''
class Run:
    @classmethod
    def create(cls) -> "Run":
        return cls()

    def to_dict(self):
        return {}


class Factory:
    def build(self) -> Run:
        return Run()


def via_classmethod():
    r = Run.create()
    r.to_dict()


def via_instance():
    f = Factory()
    r = f.build()
    r.to_dict()


def untyped_receiver(f):
    r = f.build()
    r.to_dict()
'''


def _edges(root: Path) -> list[Edge]:
    root.mkdir(parents=True, exist_ok=True)
    (root / "mod.py").write_text(_SRC)
    return analyze_python(root).edges


def _to_dict_targets_from(edges: list[Edge], caller: str) -> set[str]:
    return {
        e.dst for e in edges
        if e.edge_type == "calls" and f":{caller}:function" in e.src
        and "to_dict" in e.dst
    }


def test_a_classmethod_factory_types_its_result(tmp_path: Path) -> None:
    targets = _to_dict_targets_from(_edges(tmp_path / "r"), "via_classmethod")
    assert [t for t in targets if t.endswith(":Run.to_dict:method")]


def test_an_instance_factory_types_its_result(tmp_path: Path) -> None:
    targets = _to_dict_targets_from(_edges(tmp_path / "r"), "via_instance")
    assert [t for t in targets if t.endswith(":Run.to_dict:method")]


def test_an_untyped_receiver_still_types_nothing(tmp_path: Path) -> None:
    targets = _to_dict_targets_from(_edges(tmp_path / "r"), "untyped_receiver")
    assert [t for t in targets if t.endswith(":Run.to_dict:method")] == []
