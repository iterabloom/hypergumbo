# SPDX-License-Identifier: AGPL-3.0-or-later
"""WI-gabak: a call on an attribute chain rooted at a FROM-imported name keeps
its module slot, so a catalogue row for that object can match the call.

``from flask import request`` binds ``request`` to ``flask.request``; the call
``request.args.get("p")`` is a two-hop chain on it. The chain branch of the
unresolved-call emitter had an arm for a root bound by ``import flask`` /
``import flask as fl`` (slot = the import path plus every hop but the callee,
``flask.request.args``) and none for a root bound by a from-import, which fell
to the ``python:external:0-0:get`` placeholder: receiver gone, no row for
``flask.request.*`` able to match, so Flask's request value could never be an
``untrusted_input`` source (WI-ritog's Flask tier). The maps are
scope-narrowed at the call site (INV-dulum), so a parameter or local named
``request`` never reaches the arm and keeps the placeholder. The same root
extension applies to the bare-read emitter (``request.data`` is a
``module_attr_ref`` on ``flask.request``), fix candidate (2) on the item.
"""

from __future__ import annotations

import contextlib
import io
import json
from pathlib import Path

import pytest
import yaml

from hypergumbo_core.cli import main
from hypergumbo_core.ir import Span, Symbol
from hypergumbo_lang_mainstream.py import _from_import_receiver_path, analyze_python

_FLASK_APP = (
    "from flask import Flask, request\n\n"
    "app = Flask(__name__)\n\n\n"
    '@app.route("/save")\n'
    "def save():\n"
    '    p = request.args.get("p")\n'
    '    with open(p, "w") as f:\n'
    '        f.write("x")\n'
    '    return "ok"\n'
)
_CLAIMS = (
    "claims:\n"
    "  - id: untrusted-input-no-host-fs\n"
    "    text: request data never chooses a file to write\n"
    "    constraint:\n"
    "      taint_flow:\n"
    "        source_taint: untrusted_input\n"
    "        prohibited_sink_zone: host_fs\n"
)


def _module_slot(dst: str) -> str:
    return dst.split(":")[1]


def _get_calls(result, caller_suffix: str):
    return [
        e for e in result.edges
        if e.edge_type == "calls" and e.src.endswith(caller_suffix)
        and e.dst.split(":")[3] == "get"
    ]


def test_a_from_imported_root_chain_keeps_its_module_slot(tmp_path: Path) -> None:
    (tmp_path / "app.py").write_text(_FLASK_APP)
    result = analyze_python(tmp_path)
    [edge] = _get_calls(result, ":save:function")
    assert edge.dst.startswith("python:flask.request.args:0-0:get:"), edge.dst
    assert edge.is_resolved is False


def test_the_three_import_spellings_land_on_one_slot(tmp_path: Path) -> None:
    (tmp_path / "a.py").write_text(
        'import flask\n\ndef save():\n    return flask.request.args.get("p")\n'
    )
    (tmp_path / "b.py").write_text(
        'from flask import request\n\ndef save():\n    return request.args.get("p")\n'
    )
    (tmp_path / "c.py").write_text(
        'import flask as fl\n\ndef save():\n    return fl.request.args.get("p")\n'
    )
    result = analyze_python(tmp_path)
    slots = {
        e.src.split(":")[1].rsplit("/", 1)[-1]: _module_slot(e.dst)
        for e in _get_calls(result, ":save:function")
    }
    assert slots == {
        "a.py": "flask.request.args", "b.py": "flask.request.args", "c.py": "flask.request.args",
    }, slots


def test_a_parameter_named_like_the_import_keeps_the_placeholder(tmp_path: Path) -> None:
    """The control: the arm reads the scope-narrowed import map, so a value
    shadowing the name is not the module object (INV-dulum)."""
    (tmp_path / "app.py").write_text(
        'from flask import request\n\ndef save(request):\n    return request.args.get("p")\n'
    )
    result = analyze_python(tmp_path)
    [edge] = _get_calls(result, ":save:function")
    assert edge.dst.startswith("python:external:0-0:get:"), edge.dst


def test_a_bare_read_of_a_from_imported_object_attribute_is_a_module_attr_ref(
    tmp_path: Path,
) -> None:
    (tmp_path / "app.py").write_text(
        "from flask import request\n\ndef body():\n    return request.data\n"
    )
    result = analyze_python(tmp_path)
    refs = [
        e for e in result.edges
        if e.edge_type == "module_attr_ref" and e.src.endswith(":body:function")
    ]
    assert [(_module_slot(e.dst), e.dst.split(":")[3].rsplit(".", 1)[-1]) for e in refs] == [
        ("flask.request", "data"),
    ], [e.dst for e in result.edges]


_IN_TREE = {
    "pkg/__init__.py": "",
    "pkg/sub/__init__.py": "CONST = 1\n",
    "pkg/sub/deep.py": "def deep_fn():\n    return 1\n",
    "pkg/sub/core.py": "CONSTANTS = {}\n",
    "pkg/models.py": "class Klass:\n    registry = {}\n    X = 1\n",
    # mlflow's dev/clint shape: a package __init__ re-exporting the rule classes.
    "pkg/rules/__init__.py": "from pkg.rules.k import Rule\n",
    "pkg/rules/k.py": (
        "class Rule:\n    MAP = {}\n\n    @staticmethod\n    def check(n):\n"
        "        return n in Rule.MAP\n"
    ),
    "pkg/ents/__init__.py": "from pkg.ents.loc import Loc\n",
    "pkg/ents/loc.py": "class Loc:\n    @classmethod\n    def from_id(cls, i):\n        return cls()\n",
}


def _write(tmp_path: Path, files: dict[str, str]) -> None:
    for rel, text in files.items():
        p = tmp_path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)


def _edges_from(result, caller_suffix: str, *types: str):
    return [
        e for e in result.edges
        if e.edge_type in types and e.src.endswith(caller_suffix)
    ]


def test_a_one_hop_call_on_a_from_imported_object_already_kept_its_slot(
    tmp_path: Path,
) -> None:
    """The pin the chain arm is made consistent with: one hop had its own
    branch and the slot all along; two hops lost it."""
    (tmp_path / "app.py").write_text(
        "from flask import request\n\ndef body():\n    return request.get_json()\n"
    )
    [edge] = _edges_from(analyze_python(tmp_path), ":body:function", "calls")
    assert edge.dst.startswith("python:flask.request:0-0:get_json:"), edge.dst


def test_a_from_imported_in_tree_class_root_keeps_the_placeholder(tmp_path: Path) -> None:
    """A from-imported CLASS of the project is not a module surface: no
    ``pkg.models.Klass.registry`` slot is minted (the workspace-prefixed
    phantom INV-nuzas forbids)."""
    _write(tmp_path, {
        **_IN_TREE,
        "app.py": "from pkg.models import Klass\n\ndef run():\n    return Klass.registry.get('k')\n",
    })
    result = analyze_python(tmp_path)
    [edge] = _edges_from(result, ":run:function", "calls")
    assert edge.dst.startswith("python:external:0-0:get:"), edge.dst
    assert not [e.dst for e in result.edges if "pkg.models.Klass" in e.dst], [
        e.dst for e in result.edges
    ]


def test_a_from_imported_in_tree_submodule_chain_binds_in_the_tree(tmp_path: Path) -> None:
    """``from pkg import sub; sub.deep.deep_fn()`` is the from-import spelling of
    WI-lovun's ``import pkg.sub as sub`` and binds the same way."""
    _write(tmp_path, {
        **_IN_TREE,
        "app.py": "from pkg import sub\n\ndef run():\n    return sub.deep.deep_fn()\n",
    })
    [edge] = _edges_from(analyze_python(tmp_path), ":run:function", "calls")
    assert edge.dst.endswith("pkg/sub/deep.py:1-2:deep_fn:function"), edge.dst
    assert edge.is_resolved is not False


def test_a_from_imported_in_tree_submodule_constant_is_referenced(tmp_path: Path) -> None:
    """``core.CONSTANTS.values()`` under ``from pkg.sub import core`` answers like
    the dotted spelling: a ``references`` edge to the variable, no slot."""
    _write(tmp_path, {
        **_IN_TREE,
        "app.py": "from pkg.sub import core\n\ndef run():\n    return core.CONSTANTS.values()\n",
    })
    result = analyze_python(tmp_path)
    assert not _edges_from(result, ":run:function", "calls"), [
        e.dst for e in _edges_from(result, ":run:function", "calls")
    ]
    # Two ``references`` to the same variable: the chain arm's (WI-lovun) and the
    # attribute-read retarget's on the inner ``core.CONSTANTS`` node, the pair
    # the ``import pkg.sub.core as core`` spelling has always emitted.
    refs = _edges_from(result, ":run:function", "references")
    assert refs and all(r.dst.endswith(":CONSTANTS:variable") for r in refs), [
        r.dst for r in refs
    ]


def test_a_bare_read_of_a_from_imported_in_tree_class_member_emits_no_attr_ref(
    tmp_path: Path,
) -> None:
    _write(tmp_path, {
        **_IN_TREE,
        "app.py": "from pkg.models import Klass\n\ndef run():\n    return Klass.X\n",
    })
    result = analyze_python(tmp_path)
    assert not _edges_from(result, ":run:function", "module_attr_ref"), [
        e.dst for e in result.edges
    ]


def test_a_bare_read_through_a_from_imported_in_tree_submodule_references_it(
    tmp_path: Path,
) -> None:
    """``sub.CONST`` under ``from pkg import sub`` takes the WI-huhum retarget the
    ``import pkg.sub as sub`` spelling takes: ``references``, no phantom."""
    _write(tmp_path, {
        **_IN_TREE,
        "app.py": "from pkg import sub\n\ndef run():\n    return sub.CONST\n",
    })
    result = analyze_python(tmp_path)
    assert not _edges_from(result, ":run:function", "module_attr_ref"), [
        e.dst for e in result.edges
    ]
    [ref] = _edges_from(result, ":run:function", "references")
    assert ref.dst.endswith(":CONST:variable"), ref.dst


def test_the_recovery_linker_shape_keeps_the_placeholder(tmp_path: Path) -> None:
    """mlflow's clint: ``rules.Rule.check(n)`` under ``from pkg import rules`` is
    the ``external`` placeholder beside a class hint on the same caller, which
    the method-call-recovery linker (WI-gigoz) resolves downstream; a slot
    under ``pkg.rules.Rule`` took 27 such calls out of its reach."""
    _write(tmp_path, {
        **_IN_TREE,
        "app.py": (
            "from pkg import rules\n\ndef run(n):\n    if rules.Rule.check(n):\n"
            "        return rules.Rule(n)\n    return None\n"
        ),
    })
    result = analyze_python(tmp_path)
    calls = _edges_from(result, ":run:function", "calls", "instantiates")
    assert any(e.dst.startswith("python:external:0-0:check:") for e in calls), [
        e.dst for e in calls
    ]
    assert any(e.dst.endswith(":Rule:class") for e in calls), [e.dst for e in calls]
    assert not [e.dst for e in result.edges if "pkg.rules.Rule" in e.dst]


def test_a_from_imported_in_tree_submodule_classmethod_chain_keeps_the_placeholder(
    tmp_path: Path,
) -> None:
    """``loc.Loc.from_id(i)`` under ``from pkg.ents import loc``: the resolution
    arm cannot bind a classmethod through a module path, and a slot under
    ``pkg.ents.loc.Loc`` would be a phantom of the project (82 on mlflow)."""
    _write(tmp_path, {
        **_IN_TREE,
        "app.py": "from pkg.ents import loc\n\ndef run(i):\n    return loc.Loc.from_id(i)\n",
    })
    result = analyze_python(tmp_path)
    [edge] = _edges_from(result, ":run:function", "calls")
    assert edge.dst.startswith("python:external:0-0:from_id:"), edge.dst
    assert not [e.dst for e in result.edges if "pkg.ents.loc.Loc" in e.dst]


def test_a_re_exported_in_tree_class_root_keeps_the_placeholder(tmp_path: Path) -> None:
    """``from pkg.ents import Loc`` reaches the class through the package
    ``__init__``; the binding is of the project all the same."""
    _write(tmp_path, {
        **_IN_TREE,
        "app.py": "from pkg.ents import Loc\n\ndef run(i):\n    return Loc.registry.get(i)\n",
    })
    result = analyze_python(tmp_path)
    [edge] = _edges_from(result, ":run:function", "calls")
    assert edge.dst.startswith("python:external:0-0:get:"), edge.dst
    assert not [e.dst for e in result.edges if "pkg.ents.Loc" in e.dst]


def test_a_bare_read_through_a_from_imported_in_tree_submodule_that_places_nothing_emits_nothing(
    tmp_path: Path,
) -> None:
    """``sub.MISSING`` under ``from pkg import sub``: no variable, function, class
    or submodule to retarget to, so nothing -- never a phantom under ``pkg.sub``
    (mlflow: 57 under ``mlflow.protos.databricks_pb2`` before this guard)."""
    _write(tmp_path, {
        **_IN_TREE,
        "app.py": "from pkg import sub\n\ndef run():\n    return sub.MISSING\n",
    })
    result = analyze_python(tmp_path)
    assert not _edges_from(result, ":run:function", "module_attr_ref", "references"), [
        e.dst for e in result.edges if e.src.endswith(":run:function")
    ]


@pytest.mark.parametrize("spelling", [
    "from pkg import models\n\ndef run():\n    return {'n': models.Klass}\n",
    "import pkg.models as models\n\ndef run():\n    return {'n': models.Klass}\n",
])
def test_a_bare_read_of_an_in_tree_class_through_its_module_references_the_class(
    tmp_path: Path, spelling: str,
) -> None:
    """flask-restful's ``fields.String`` in a marshalling dict: a class read as
    a VALUE through its module is a ``references`` to the class, under either
    spelling, not a phantom ``module_attr_ref`` under an in-tree module."""
    _write(tmp_path, {**_IN_TREE, "app.py": spelling})
    result = analyze_python(tmp_path)
    assert not _edges_from(result, ":run:function", "module_attr_ref"), [
        e.dst for e in result.edges
    ]
    [ref] = _edges_from(result, ":run:function", "references")
    assert ref.dst.endswith(":Klass:class"), ref.dst


_FLASK_READ_APP = _FLASK_APP.replace('request.args.get("p")', "request.data")


def _verdicts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *extra: str, app: str = _FLASK_APP,
) -> dict[str, dict]:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "app.py").write_text(app)
    claims = tmp_path / "claims.yaml"
    claims.write_text(_CLAIMS)
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(io.StringIO()):
        main(["verify-claims", str(repo), "--claims", str(claims), "--format", "json", *extra])
    return {v["claim_id"]: v for v in json.loads(buf.getvalue())["verdicts"]}


def test_without_a_row_the_disclosure_still_names_the_handler(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    v = _verdicts(tmp_path, monkeypatch)["untrusted-input-no-host-fs"]
    assert v["verdict"] == "inconclusive", v["details"]
    assert "no taint source represents" in v["details"] and "save" in v["details"], v["details"]


def test_a_vouched_flask_request_row_reaches_the_call(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The 2026-11-03 premise check measured 0 flows with this very row,
    because the receiver was lost before taint. With the slot kept, the row
    matches, the flow is found, and the represented handler leaves the
    disclosure (ADR-0049 ruling 3, as the servlet overlay already does)."""
    yours = tmp_path / "flask_yours.yaml"
    yours.write_text(yaml.safe_dump({
        "language": "python",
        "net_recv": [{"module": "flask.request.args", "methods": ["get"]}],
    }))
    v = _verdicts(
        tmp_path, monkeypatch, "--io-primitives", str(yours), "--no-default-overlays",
    )["untrusted-input-no-host-fs"]
    assert v["verdict"] == "violated", v["details"]
    assert "get" in v["details"], v["details"]


def test_the_guard_builds_its_own_resolver_when_handed_none() -> None:
    """The analyzer passes its shared resolver; without one the guard builds one
    over the registry, as ``_lookup_symbol_by_module`` does, and answers the
    same: an external binding keeps its path, a binding of the tree has none."""
    registry = {
        ("pkg.a", "f"): Symbol(
            id="python:pkg/a.py:1-2:f:function", name="f", kind="function",
            language="python", path="pkg/a.py",
            span=Span(start_line=1, start_col=0, end_line=2, end_col=0),
        ),
    }
    assert _from_import_receiver_path(
        "request", {"request": ("flask", "request")}, registry, None,
    ) == "flask.request"
    assert _from_import_receiver_path("sub", {"sub": ("pkg", "sub")}, registry, None) is None
    assert _from_import_receiver_path("x", {}, registry, None) is None


def test_a_vouched_flask_request_attribute_row_reaches_the_bare_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Fix candidate (2) on the item, end to end: ``p = request.data`` is a
    ``module_attr_ref`` an ``attributes:`` row under ``flask.request`` matches."""
    yours = tmp_path / "flask_yours.yaml"
    yours.write_text(yaml.safe_dump({
        "language": "python",
        "net_recv": [{"module": "flask.request", "attributes": ["data"]}],
    }))
    v = _verdicts(
        tmp_path, monkeypatch, "--io-primitives", str(yours), "--no-default-overlays",
        app=_FLASK_READ_APP,
    )["untrusted-input-no-host-fs"]
    assert v["verdict"] == "violated", v["details"]
    assert "data" in v["details"], v["details"]
