# SPDX-License-Identifier: AGPL-3.0-or-later
"""``Path(raw).write_text(x)`` — a chain rooted at the CONSTRUCTOR CALL ITSELF.

WHAT WAS MISSING, AND WHY IT IS NOT A DUPLICATE OF #253. PR #253 taught the resolver
that a constructor call carries a type, but wired it only into the two ASSIGNMENT call
sites. So the type existed and the emission site never asked for it::

    p = Path(raw)                    # #253: typed, boundary tagged
    p.write_text(data)

    Path(raw).write_text(data)       # SAME I/O, no call edge emitted AT ALL

Measured on the shipping tree before this change: the assignment form tags 1 boundary
and the inline form tags 0, with the ``.write_text`` call producing no edge in any
branch — only the ``instantiates`` edge for ``Path(raw)`` itself survived.

TWO GAPS, NOT ONE, which is why a one-line change could not close it:

  1. the emission site called ``_derived_receiver_module`` WITHOUT the ``ctor_type``
     resolver, so ``(Path(raw) / "o").write_text(x)`` could not type its root; and
  2. ``_derived_receiver_module`` answers "what does this DERIVATION preserve", and a
     bare ``Path(raw)`` is not a derivation — it is the root. No branch matched it.

Gap 2 is closed by extracting :func:`_receiver_type` — the question "what external type
does this receiver expression have?" — which ``_preserved_receiver_type`` was already
answering privately inside its own hint computation. Both callers now route through the
one predicate rather than each carrying an answer, which is the failure mode this area
has already produced four times (``gate_named_entry`` / ``_match_propagation_entry`` /
``_lookup_named_entry`` / ``_register_sanitizer_callers``).

SIZE, with the denominator named. Across pretix, sqlalchemy, mitmproxy, poetry, pyright,
meson and kserve there are 27,354 call sites whose receiver is itself a call or a ``/``
expression (the CALL_RESULT_ROOT family). Only 269 of those are TYPEABLE (257 rooted
directly at a recognized constructor, 12 at a derivation of one) and 128 land on a name
python.yaml admits for that module. So this closes ~0.5% of that family, not the whole
of it — the rest are receivers of genuinely unknown type, and emitting an untyped edge
for them is what PR #231 measured as moving zero findings.

MEASURED THROUGH PRODUCTION, by ``scripts/measure-ctor-root-typing-ab.py``: +125 boundary
chains and +260 typed call edges, with +260 total edges — so every new edge is NET-NEW
and none was re-keyed.

TWO POPULATIONS, AND THEY ARE NOT THE SAME NUMBER. The static census counts 128 reaching
SITES; the A/B counts 125 boundary CHAINS. ``tag_io_boundaries`` collapses same-caller,
same-primitive sites — mitmproxy's ``test_init_dir`` calls ``Path(mydir).exists()`` twice
in one function, and that file is duplicated under ``examples/`` and
``docs/src/examples/``, which accounts for the entire 3-count difference. Neither number
is wrong; quoting one as the other would be.

CONCENTRATION, stated because it limits the claim: meson alone supplies 89 of the 125
chains (71.2%) — mitmproxy 14, poetry 7, kserve 6, sqlalchemy 5, pretix 4, pyright 0.
This is not an evenly distributed win, and it does not support a general "Python gains
~125 boundaries" claim.

DIRECTION, both signs, because since PR #214 a ``False`` earns ``sanitized`` and DELETES
a flow. Typed edges ADD I/O boundaries and taint sinks (more findings), and they also
populate ``callees_at``, which lets ``_use_site_terminates`` return ``True`` more often
and produce more ``False`` verdicts (fewer findings). The suppression direction measured
ZERO on hypergumbo's own claims (``check-self-claims`` byte-identical across the change,
against a snapshot recorded pre-fix). THAT ZERO IS WEAK AND IS NOT EVIDENCE OF SAFETY:
it is one repo, and the barrier arm — the only consumer of a ``False`` — has measured
zero walks on every repo tried, so this change has not been observed under the conditions
where it could suppress.

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
    """Gap 2 — the root shape, which no branch matched."""

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
        """``open(p, "w").write(x)`` — python.yaml's synthetic ``file`` module was
        reachable only through an assignment, so the inline idiom was invisible."""
        edges = _edges(
            tmp_path / "openroot",
            "def h(raw, data):\n"
            '    open(raw, "w").write(data)\n',
        )
        assert _slot(edges, "write") == "file"
        assert _tagged(edges) >= 1


class TestADerivationOfAConstructorRoot:
    """Gap 1 — the resolver could answer, the emission site never asked."""

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
    constructor does. Widening the root must not become a back door around it."""

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
        """The 99.5% of the CALL_RESULT_ROOT family this change deliberately does
        NOT type. Emitting an untyped edge here is what PR #231 measured at zero."""
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

    THE PROPERTY IS THE TYPE AND THE BOUNDARY, NOT THE EDGE COUNT — see
    :class:`TestTheEmissionAsymmetryIsDeliberate` for the one thing that does differ
    and why leaving it that way is the measured choice.
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
    """The one way the two forms still differ, pinned so it is disclosed rather than
    discovered.

    For a receiver of UNKNOWN type the assigned form emitted an untyped ``external``
    placeholder edge (PR #231) and the inline form emitted no edge at all. PR #254
    kept that asymmetry deliberately — the ~27,085 untypeable call-or-``/`` receivers
    across seven repos are what PR #231 measured as moving zero findings — and wrote
    down the re-evaluation trigger: a consumer that distinguishes "no edge" from "an
    edge to an untyped receiver".

    THE TRIGGER FIRED (INV-luhug). The ADR-0017 §3a walk is that consumer: a call
    with no edge has no ``callees_at`` entry, so the walk cannot ask whether the
    callee consumes the value and records an ESCAPE where a §4 summary could have
    accounted for the step. INV-busis measured "no call edge emitted at all" at
    50.0% of call-node escape sites, and INV-foluz closed the bare-builtin half of
    that bucket on the same reasoning. So the inline form now emits the SAME
    placeholder as the assigned form. What has NOT changed is the recall claim:
    an untyped placeholder still reaches no catalogue row, both forms still tag
    zero boundaries, and the cost is priced in edge rows and walk escapes, not
    findings (``test_py_unknown_root_and_imported_ctor.py``).
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
