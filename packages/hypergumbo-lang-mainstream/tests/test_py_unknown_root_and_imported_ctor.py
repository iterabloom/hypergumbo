# SPDX-License-Identifier: AGPL-3.0-or-later
"""A receiver of unknown type emits the same edge inline as assigned (INV-luhug),
and an IMPORTED class constructor types its instance (WI-makij, INV-mumov L3).

INV-luhug. ``_unwind_attribute_chain`` returns ``None`` for any chain whose root
is not an ``ast.Name`` — ``items[i].startswith(x)`` (Subscript), ``f().x.y()``
(Call), ``(a + b).c()`` (BinOp) — and the emitting branch declined to emit at
all, while the sibling branch for ``obj.attr.method()`` already emitted the
``python:external:0-0:<method>:unresolved`` placeholder for the "receiver type
genuinely unknown" case. PR #254 pinned that asymmetry with a written
re-evaluation trigger: revisit when a consumer distinguishes "no edge" from "an
edge to an untyped receiver". The ADR-0017 §3a walk is that consumer — a call
with no edge has no ``callees_at`` entry, so the walk records an ESCAPE where a
§4 summary could have accounted for the step; INV-busis measured "no call edge
emitted at all" at 50.0% of call-node escape sites. Same placeholder, same
``call_construct`` stamp, both forms.

WI-makij's own repro, ``Fernet(key).decrypt(token)``, emitted no call edge and
its assigned form ``f = Fernet(key); f.decrypt(token)`` an UNTYPED one, because
only the I/O-catalogue constructor table typed a receiver. An IMPORTED PascalCase
name that is called is a construction — the same convention WI-jubag already
reads to mint ``instantiates`` — and the import IS the binding, so the instance
is typed ``<module>.<Class>`` exactly (``cryptography.fernet.Fernet``), which is
what the four built-in plaintext sources and four sanitizers are keyed on. A
lowercase imported callable is not a construction and types nothing.
"""
from __future__ import annotations

from pathlib import Path

from hypergumbo_core.io_boundary import load_catalog, tag_io_boundaries
from hypergumbo_core.ir import Edge
from hypergumbo_lang_mainstream.py import analyze_python


def _edges(root: Path, source: str) -> list[Edge]:
    root.mkdir(parents=True, exist_ok=True)
    (root / "app.py").write_text(source)
    return analyze_python(root).edges


def _call(edges: list[Edge], method: str) -> Edge:
    hits = [
        e for e in edges
        if e.edge_type == "calls" and e.dst.endswith(f":{method}:unresolved")
    ]
    assert len(hits) == 1, [e.dst for e in edges if method in e.dst]
    return hits[0]


def _slot(edges: list[Edge], method: str) -> str:
    hits = [e.dst for e in edges if e.dst.endswith(f":{method}:unresolved")]
    return hits[0].split(":")[1] if hits else ""


def _tagged(edges: list[Edge]) -> int:
    return tag_io_boundaries(edges, {"python": load_catalog("python")})


class TestUnknownRootEmitsThePlaceholder:
    """INV-luhug: the three filed shapes, each with the assigned form as control."""

    def test_subscript_root(self, tmp_path: Path) -> None:
        edges = _edges(
            tmp_path / "s",
            "def h(items, i, x):\n"
            "    items[i].startswith(x)\n"
            "    it = items[i]\n"
            "    it.endswith(x)\n",
        )
        inline = _call(edges, "startswith")
        assigned = _call(edges, "endswith")
        assert inline.dst == "python:external:0-0:startswith:unresolved"
        assert assigned.dst == "python:external:0-0:endswith:unresolved"
        assert (inline.meta or {}).get("call_construct") == "method"
        assert (assigned.meta or {}).get("call_construct") == "method"

    def test_call_root_of_unknown_type(self, tmp_path: Path) -> None:
        edges = _edges(
            tmp_path / "c",
            "from somewhere import make_thing\n"
            "\n"
            "def h(raw, data):\n"
            "    make_thing(raw).write_text(data)\n"
            "    make_thing(raw).x.y.write_bytes(data)\n",
        )
        assert _call(edges, "write_text").dst == "python:external:0-0:write_text:unresolved"
        assert _call(edges, "write_bytes").dst == "python:external:0-0:write_bytes:unresolved"
        # An untyped placeholder reaches no catalogue row: emission is not recall.
        assert _tagged(edges) == 0

    def test_binop_root(self, tmp_path: Path) -> None:
        edges = _edges(
            tmp_path / "b",
            "def h(a, b):\n"
            "    (a + b).c()\n",
        )
        assert _call(edges, "c").dst == "python:external:0-0:c:unresolved"

    def test_a_typed_non_name_root_is_untouched(self, tmp_path: Path) -> None:
        """The WI-zilag branch still wins for a root the resolver can type."""
        edges = _edges(
            tmp_path / "t",
            "from pathlib import Path\n"
            "\n"
            "def h(raw, data):\n"
            "    (Path(raw) / 'o').write_text(data)\n",
        )
        assert _slot(edges, "write_text") == "pathlib.Path"
        assert _tagged(edges) >= 1


class TestImportedConstructorTypesItsInstance:
    def test_from_import_inline_and_assigned(self, tmp_path: Path) -> None:
        edges = _edges(
            tmp_path / "f",
            "from cryptography.fernet import Fernet\n"
            "\n"
            "def inline(key, token):\n"
            "    return Fernet(key).decrypt(token)\n"
            "\n"
            "def assigned(key, token):\n"
            "    f = Fernet(key)\n"
            "    return f.decrypt(token)\n",
        )
        decrypts = sorted(
            e.dst for e in edges
            if e.edge_type == "calls" and e.dst.endswith(":decrypt:unresolved")
        )
        assert decrypts == [
            "python:cryptography.fernet.Fernet:0-0:decrypt:unresolved",
            "python:cryptography.fernet.Fernet:0-0:decrypt:unresolved",
        ], decrypts
        for e in edges:
            if e.dst.endswith(":decrypt:unresolved"):
                assert (e.meta or {}).get("call_construct") == "method"
                assert e.dst_ref is not None
                assert e.dst_ref.module_path == "cryptography.fernet.Fernet"

    def test_module_import_dotted_form(self, tmp_path: Path) -> None:
        edges = _edges(
            tmp_path / "d",
            "import cryptography.fernet\n"
            "\n"
            "def h(key, token):\n"
            "    return cryptography.fernet.Fernet(key).decrypt(token)\n",
        )
        assert _slot(edges, "decrypt") == "cryptography.fernet.Fernet"

    def test_the_instance_reaches_the_taint_catalogue(self, tmp_path: Path) -> None:
        """The reason this exists: ``Fernet.decrypt`` is a shipped plaintext SOURCE."""
        from hypergumbo_core.taint import (
            _build_callee_index,
            _match_propagation_entry,
            load_builtin_taint_catalog,
        )

        edges = _edges(
            tmp_path / "k",
            "from cryptography.fernet import Fernet\n"
            "\n"
            "def h(key, token):\n"
            "    return Fernet(key).decrypt(token)\n",
        )
        edge = _call(edges, "decrypt")
        catalog = load_builtin_taint_catalog()
        index = _build_callee_index(catalog.sources_for_language("python"))
        ambiguous = catalog.ambiguous_names_for_language("python")
        matched = _match_propagation_entry(
            index, edge.dst, ambiguous,
            (edge.meta or {}).get("call_construct"),
            is_resolved=edge.is_resolved, language="python",
        )
        assert matched is not None, edge.dst
        assert matched.qualified_name == "cryptography.fernet.Fernet.decrypt"

    def test_a_lowercase_imported_callable_types_nothing(self, tmp_path: Path) -> None:
        edges = _edges(
            tmp_path / "l",
            "from somewhere import make_thing\n"
            "\n"
            "def h(raw, data):\n"
            "    t = make_thing(raw)\n"
            "    t.write_text(data)\n",
        )
        assert _slot(edges, "write_text") == "external"
        assert _tagged(edges) == 0

    def test_the_binding_is_exact_so_a_namesake_names_its_own_module(
        self, tmp_path: Path,
    ) -> None:
        """``from decoy import Path`` constructs a ``decoy.Path``, never ``pathlib.Path``.

        INV-kipor from the other direction: the import is the binding, so the
        instance is typed to the module that actually supplied the name, and a
        catalogued ``pathlib.Path`` row cannot be reached through a namesake.
        """
        edges = _edges(
            tmp_path / "n",
            "from decoy import Path\n"
            "\n"
            "def h(raw, data):\n"
            "    Path(raw).write_text(data)\n",
        )
        assert _slot(edges, "write_text") == "decoy.Path"
        assert _tagged(edges) == 0

    def test_a_project_class_still_resolves_in_repo(self, tmp_path: Path) -> None:
        """An imported PROJECT class keeps the in-repo path (var_types), not a module hint."""
        root = tmp_path / "p"
        root.mkdir()
        (root / "models.py").write_text("class Order:\n    def save(self):\n        pass\n")
        (root / "app.py").write_text(
            "from models import Order\n"
            "\n"
            "def h():\n"
            "    o = Order()\n"
            "    o.save()\n"
            "    Order().save()\n",
        )
        edges = analyze_python(root).edges
        saves = [e for e in edges if e.edge_type == "calls" and "save" in e.dst]
        assert saves, [e.dst for e in edges]
        assert all("models.Order" not in e.dst.split(":")[1] for e in saves), [
            e.dst for e in saves
        ]
