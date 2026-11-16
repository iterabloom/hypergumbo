# SPDX-License-Identifier: AGPL-3.0-or-later
"""``Path(raw).write_text(x)`` — a chain rooted at the CONSTRUCTOR CALL ITSELF.

A constructor call carries a type, and the inline form must reach the same verdict
as the assignment form::

    p = Path(raw)                    # typed, boundary tagged
    p.write_text(data)

    Path(raw).write_text(data)       # SAME I/O, must be typed the same way

TWO GAPS, which is why neither fix alone closes it:

  1. the emission site must pass the ``ctor_type`` resolver when it types the
     receiver, or ``(Path(raw) / "o").write_text(x)`` cannot type its root; and
  2. ``_derived_receiver_module`` answers "what does this DERIVATION preserve", and a
     bare ``Path(raw)`` is not a derivation — it is the root, and no derivation
     branch matches it.

:func:`_receiver_type` answers the question "what external type does this receiver
expression have?", constructor roots included. The emission site and
``_preserved_receiver_type`` both route through that one predicate rather than each
carrying an answer, because separate answers to one question drift apart.

SCOPE. Most call sites whose receiver is itself a call or a ``/`` expression (the
CALL_RESULT_ROOT family) have a receiver of genuinely unknown type; only those rooted
directly at a recognized constructor, or at a derivation of one, are typeable, and
only those are typed here. The unknown rest get an untyped placeholder (see
:class:`TestTheEmissionAsymmetryIsDeliberate`).
``scripts/measure-ctor-root-typing-ab.py`` measures the effect through production.

DIRECTION, both signs, because a ``False`` from the walk earns ``sanitized`` and
DELETES a flow. Typed edges ADD I/O boundaries and taint sinks (more findings), and
they also populate ``callees_at``, which lets ``_use_site_terminates`` return ``True``
more often and produce more ``False`` verdicts (fewer findings).

The binding check is inherited rather than re-implemented: the root goes through
``_external_constructor_module``, so a locally defined ``class Path`` and a
``from decoy_lib import Path`` are refused here exactly as they are at an assignment.
"""

from pathlib import Path

from hypergumbo_core.io_boundary import load_catalog, tag_io_boundaries
from hypergumbo_lang_mainstream.py import analyze_python


def _edges(root: Path, source: str) -> list:
    root.mkdir(parents=True, exist_ok=True)
    (root / "app.py").write_text(source)
    return analyze_python(root).edges


def _tagged(edges: list) -> int:
    return tag_io_boundaries(edges, {"python": load_catalog("python")})


def _slot(edges: list, method: str) -> str:
    """The module slot of the emitted call edge for ``method``, or ``""``."""
    hits = [e.dst for e in edges if e.dst.endswith(f":{method}:unresolved")]
    return hits[0].split(":")[1] if hits else ""


class TestTheConstructorCallItselfIsATypedReceiver:
    """Gap 2 — the root shape, which no derivation branch matches."""

    def test_bare_constructor_root(self, tmp_path: Path) -> None:
        edges = _edges(
            tmp_path / "bare",
            "from pathlib import Path\n"
            "\n"
            "def h(raw, data):\n"
            "    Path(raw).write_text(data)\n",
        )
        assert _slot(edges, "write_text") == "pathlib.Path"
        assert _tagged(edges) >= 1

    def test_dotted_constructor_root(self, tmp_path: Path) -> None:
        edges = _edges(
            tmp_path / "dotted",
            "import pathlib\n"
            "\n"
            "def h(raw):\n"
            "    pathlib.Path(raw).read_text()\n",
        )
        assert _slot(edges, "read_text") == "pathlib.Path"
        assert _tagged(edges) >= 1

    def test_open_result_is_the_synthetic_file_module(self, tmp_path: Path) -> None:
        """``open(p, "w").write(x)`` — the inline idiom must reach python.yaml's
        synthetic ``file`` module just as the assigned form does."""
        edges = _edges(
            tmp_path / "openroot",
            "def h(raw, data):\n"
            '    open(raw, "w").write(data)\n',
        )
        assert _slot(edges, "write") == "file"
        assert _tagged(edges) >= 1


class TestADerivationOfAConstructorRoot:
    """Gap 1 — the emission site must ask the constructor resolver."""

    def test_truediv_from_a_constructor_root(self, tmp_path: Path) -> None:
        edges = _edges(
            tmp_path / "div",
            "from pathlib import Path\n"
            "\n"
            "def h(raw, data):\n"
            '    (Path(raw) / "out.txt").write_text(data)\n',
        )
        assert _slot(edges, "write_text") == "pathlib.Path"
        assert _tagged(edges) >= 1

    def test_joinpath_from_a_constructor_root(self, tmp_path: Path) -> None:
        edges = _edges(
            tmp_path / "join",
            "from pathlib import Path\n"
            "\n"
            "def h(raw):\n"
            '    Path(raw).joinpath("out.txt").read_text()\n',
        )
        assert _slot(edges, "read_text") == "pathlib.Path"
        assert _tagged(edges) >= 1

    def test_multi_step_chain_keeps_the_type(self, tmp_path: Path) -> None:
        edges = _edges(
            tmp_path / "multi",
            "from pathlib import Path\n"
            "\n"
            "def h(raw, data):\n"
            '    Path(raw).resolve().joinpath("o.txt").write_text(data)\n',
        )
        assert _slot(edges, "write_text") == "pathlib.Path"
        assert _tagged(edges) >= 1


class TestTheBindingCheckIsInheritedNotBypassed:
    """A constructor ROOT must clear the same INV-kipor binding check an assigned
    constructor does. Typing the root must not become a back door around it."""

    def test_locally_defined_class_named_path(self, tmp_path: Path) -> None:
        edges = _edges(
            tmp_path / "localclass",
            "class Path:\n"
            "    def write_text(self, d): ...\n"
            "\n"
            "def h(raw, data):\n"
            "    Path(raw).write_text(data)\n",
        )
        assert _slot(edges, "write_text") != "pathlib.Path"
        assert _tagged(edges) == 0

    def test_name_bound_to_a_different_module(self, tmp_path: Path) -> None:
        edges = _edges(
            tmp_path / "decoy",
            "from decoy_lib import Path\n"
            "\n"
            "def h(raw, data):\n"
            "    Path(raw).write_text(data)\n",
        )
        assert _slot(edges, "write_text") != "pathlib.Path"
        assert _tagged(edges) == 0

    def test_unknown_constructor_stays_untyped(self, tmp_path: Path) -> None:
        """The bulk of the CALL_RESULT_ROOT family: a receiver of unknown type
        stays untyped and tags no boundary."""
        edges = _edges(
            tmp_path / "unknown",
            "from somewhere import make_thing\n"
            "\n"
            "def h(raw, data):\n"
            "    make_thing(raw).write_text(data)\n",
        )
        assert _slot(edges, "write_text") != "pathlib.Path"
        assert _tagged(edges) == 0

    def test_non_preserving_member_does_not_propagate(self, tmp_path: Path) -> None:
        """``TYPE_PRESERVING_MEMBERS`` still gates DERIVATIONS. ``read_text()``
        returns a ``str``, so a chain through it is not a ``Path`` any more."""
        edges = _edges(
            tmp_path / "nonpreserving",
            "from pathlib import Path\n"
            "\n"
            "def h(raw):\n"
            "    Path(raw).read_text().write_text('x')\n",
        )
        assert _slot(edges, "write_text") != "pathlib.Path"


#: ``""`` (no edge at all) and ``"external"`` (an untyped placeholder edge) are the
#: two spellings of "this receiver has no known type". They differ in EMISSION but not
#: in meaning, and no consumer can tell them apart — ``_module_from_symbol_path``
#: returns ``""`` for the placeholder by documented design.
_UNTYPED = ("", "external")


def _effective_type(edges: list, method: str) -> str:
    """The module slot, with both spellings of "untyped" collapsed."""
    slot = _slot(edges, method)
    return "" if slot in _UNTYPED else slot


class TestTheOneReceiverTypePredicate:
    """Both consumers route through :func:`_receiver_type`. A grep-for-the-call test
    is satisfiable by a third copy that looks right, so this asserts BEHAVIOUR: the
    inline form and the assigned form of the same shape must reach the same verdict.

    THE PROPERTY IS THE TYPE AND THE BOUNDARY, NOT THE EDGE COUNT: both spellings
    of "untyped" (:data:`_UNTYPED`) count as the same verdict here, and
    :class:`TestTheEmissionAsymmetryIsDeliberate` pins the unknown-type emission
    separately.
    """

    SHAPES = (
        ("from pathlib import Path", "Path(raw)", "write_text(data)"),
        ("import pathlib", "pathlib.Path(raw)", "read_text()"),
        ("from pathlib import Path", '(Path(raw) / "o.txt")', "write_text(data)"),
        ("from pathlib import Path", 'Path(raw).joinpath("o")', "read_bytes()"),
        ("class Path:\n    def write_text(self, d): ...", "Path(raw)",
         "write_text(data)"),
        ("from decoy_lib import Path", "Path(raw)", "write_text(data)"),
        ("from somewhere import make_thing", "make_thing(raw)", "write_text(data)"),
    )

    def test_inline_and_assigned_forms_agree(self, tmp_path: Path) -> None:
        for i, (prelude, ctor, call) in enumerate(self.SHAPES):
            method = call.split("(")[0]
            inline = _edges(
                tmp_path / f"inline{i}",
                f"{prelude}\n\ndef h(raw, data):\n    {ctor}.{call}\n",
            )
            assigned = _edges(
                tmp_path / f"assigned{i}",
                f"{prelude}\n\ndef h(raw, data):\n"
                f"    p = {ctor}\n    p.{call}\n",
            )
            assert _effective_type(inline, method) == _effective_type(
                assigned, method), (
                f"shape {i} ({ctor}.{call}) disagrees on TYPE: "
                f"inline={_slot(inline, method)!r} "
                f"assigned={_slot(assigned, method)!r}"
            )
            assert _tagged(inline) == _tagged(assigned), (
                f"shape {i} ({ctor}.{call}) disagrees on BOUNDARIES: "
                f"inline={_tagged(inline)} assigned={_tagged(assigned)}"
            )


class TestTheEmissionAsymmetryIsDeliberate:
    """For a receiver of UNKNOWN type, the inline and assigned forms emit the SAME
    untyped ``external`` placeholder edge.

    Recall cannot tell "no edge" from the placeholder — an untyped placeholder
    reaches no catalogue row, and both forms tag zero boundaries — but the
    ADR-0017 §3a walk can: a call with no edge has no ``callees_at`` entry, so the
    walk cannot ask whether the callee consumes the value and records an ESCAPE
    where a §4 summary could have accounted for the step. So the inline form must
    not go silent. The placeholder's effect shows up in edge rows and walk
    escapes, not findings (``test_py_unknown_root_and_imported_ctor.py``).
    (history: INV-luhug, INV-busis, INV-foluz)
    """

    def test_unknown_receiver_emits_the_same_placeholder_inline_and_assigned(
        self, tmp_path: Path,
    ) -> None:
        src = "from somewhere import make_thing\n\ndef h(raw, data):\n"
        inline = _edges(tmp_path / "i", src + "    make_thing(raw).write_text(data)\n")
        assigned = _edges(
            tmp_path / "a",
            src + "    p = make_thing(raw)\n    p.write_text(data)\n",
        )
        assert _slot(inline, "write_text") == "external"
        assert _slot(assigned, "write_text") == "external"
        assert _tagged(inline) == _tagged(assigned) == 0
