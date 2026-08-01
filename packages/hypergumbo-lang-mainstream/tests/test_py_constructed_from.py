# SPDX-License-Identifier: AGPL-3.0-or-later
"""``meta["constructed_from"]`` — the framework-object binding (WI-nopod).

A whole class of frameworks is configured by *constructing* an object rather
than by decorating or subclassing one: ``app = FastAPI()``,
``app = Flask(__name__)``, ``Base = declarative_base()``,
``runner = CliRunner()``. WI-nopod filed this as "the framework matcher
lacks a call surface". Measured, that is not the defect:

* The matcher HAS a call surface — ``UsagePatternSpec`` matches
  ``UsageContext`` records on ``(kind, name, position)`` and has since
  2026-01-30, four months before the item was filed.
* ADR-3ccc's model is *"a first-party symbol appears **inside** a framework
  call"* — every ``position`` it defines is an input slot (``args[1]``,
  ``args[0].handler``, a map key, ``default``, ``block``). WI-nopod's cases
  invert it: the symbol we want to tag is the call's **result**.
* And nothing recorded the binding at all. `instantiates` says *"this file
  instantiated FastAPI somewhere"* — anchored at the file — while the ``app``
  variable carried no link to ``FastAPI`` whatsoever.

So the fix is a producer one, and the fact goes where hypergumbo already
puts "what type is this symbol related to": ``Symbol.meta``, mirroring
``meta.base_classes``. That choice means the **matcher needs no change** —
the existing ``meta_match`` field keys on it directly, which
``test_meta_match_keys_on_it`` in the core package proves.

Python is a deliberate pilot. Nine analyzers emit ``UsageContext`` and each
carries its own hardcoded call-name allowlist; sweeping all nine before one
is proven is the failure mode this codebase has the most scar tissue about.
"""
from __future__ import annotations

from pathlib import Path


def _symbols(root: Path):
    from hypergumbo_lang_mainstream.py import analyze_python

    return {s.name: s for s in analyze_python(root).symbols}


def test_constructor_binding_is_recorded_on_the_variable(tmp_path: Path) -> None:
    (tmp_path / "main.py").write_text(
        "from fastapi import FastAPI\n"
        "from flask import Flask\n"
        "\n"
        "app = FastAPI()\n"
        "flask_app = Flask(__name__)\n",
    )
    syms = _symbols(tmp_path)
    assert (syms["app"].meta or {}).get("constructed_from") == "FastAPI"
    assert (syms["flask_app"].meta or {}).get("constructed_from") == "Flask"


def test_factory_call_is_recorded_too(tmp_path: Path) -> None:
    """``declarative_base()`` is a factory, not a constructor.

    Static analysis cannot tell them apart — both are "a name is called and
    the result is bound" — and the framework-integration author does not
    care which it is. Recording the callee is the honest thing the AST
    supports; asserting "this is a class" would be a guess.
    """
    (tmp_path / "m.py").write_text(
        "from sqlalchemy.orm import declarative_base\n\nBase = declarative_base()\n",
    )
    assert (_symbols(tmp_path)["Base"].meta or {}).get(
        "constructed_from",
    ) == "declarative_base"


def test_attribute_callee_keeps_its_qualification(tmp_path: Path) -> None:
    """``x = mod.Thing()`` records ``mod.Thing``, not ``Thing``.

    A YAML author keying on a framework's own namespace needs the qualifier;
    stripping it here would make ``sqlalchemy.orm.declarative_base`` and a
    local ``declarative_base`` indistinguishable.
    """
    (tmp_path / "m.py").write_text("import fastapi\n\napp = fastapi.FastAPI()\n")
    assert (_symbols(tmp_path)["app"].meta or {}).get(
        "constructed_from",
    ) == "fastapi.FastAPI"


def test_non_call_assignments_carry_nothing(tmp_path: Path) -> None:
    """Only a call result is a construction. Guards against stamping the key
    on every variable, which would make it useless as a filter."""
    (tmp_path / "m.py").write_text(
        "TIMEOUT = 30\nNAME = 'x'\nitems = []\nalias = TIMEOUT\n",
    )
    for name in ("TIMEOUT", "NAME", "items", "alias"):
        meta = _symbols(tmp_path)[name].meta or {}
        assert "constructed_from" not in meta, f"{name} is not a construction"


def test_multiple_targets_each_record_the_binding(tmp_path: Path) -> None:
    (tmp_path / "m.py").write_text("from x import C\n\na = b = C()\n")
    syms = _symbols(tmp_path)
    for name in ("a", "b"):
        assert (syms[name].meta or {}).get("constructed_from") == "C"


def test_key_survives_the_full_pipeline_and_coexists(tmp_path: Path) -> None:
    """The key reaches the ARTIFACT, alongside meta the pipeline adds later.

    An analyzer-level assertion would not catch the failure that matters: a
    component can be correct in isolation and inert end-to-end. A YAML author
    consumes the emitted behavior map, so that is where the key has to be —
    and it must not displace `visibility_signal`, which a downstream pass
    stamps onto the same dict.
    """
    import json

    from hypergumbo_core.cli import run_behavior_map

    (tmp_path / "m.py").write_text("from x import C\n\napp = C()\n")
    out = tmp_path / "bm.json"
    run_behavior_map(
        repo_root=tmp_path, out_path=out,
        include_sketch_precomputed=False, progress=False,
    )
    nodes = json.loads(out.read_text())["nodes"]
    app = next(n for n in nodes if n["name"] == "app")
    meta = app.get("meta") or {}
    assert meta.get("constructed_from") == "C", f"key lost in the pipeline: {meta}"
    assert "visibility_signal" in meta, f"downstream meta was clobbered: {meta}"


def test_computed_callees_record_nothing(tmp_path: Path) -> None:
    """A callee with no dotted name yields no key, rather than a guess.

    ``factories[key]()`` and ``registry[0].Build()`` are calls whose target
    is only known at runtime. Emitting a partial or invented name here would
    be worse than emitting nothing: a framework YAML keying on
    ``constructed_from`` would match on a fiction, and the miss would be
    silent. Absence is the honest signal.
    """
    (tmp_path / "m.py").write_text(
        "import registry\n"
        "\n"
        "app = registry.factories['web']()\n"
        "other = registry.items[0].Build()\n"
        "made = make()()\n",
    )
    syms = _symbols(tmp_path)
    for name in ("app", "other", "made"):
        meta = syms[name].meta or {}
        assert "constructed_from" not in meta, (
            f"{name} has a computed callee; got {meta.get('constructed_from')!r}"
        )
