# SPDX-License-Identifier: AGPL-3.0-or-later
"""py.py stamps ``call_arg_shape='literal_only'`` when it can PROVE it (INV-fubag).

The consumer is taint's ``_sink_call_can_carry_taint`` gate, and the reason it
can be a gate rather than a heuristic is that the producer only stamps this when
every argument at the call site is a literal constant — or there are none. A
call like ``tempfile.TemporaryDirectory()`` has no argument that could be the
tainted value and its receiver is a module, so a taint flow into it is not
unlikely but impossible under the model taint itself uses.

MEASURED (docs/measurements/0003): 24 of the 34 adjudicated false positives the
construction-edge widening added were exactly this shape —
``TemporaryDirectory()``, ``TemporaryFile()``, ``NamedTemporaryFile(delete=True)``.

THE ASYMMETRY IS THE POINT. Stamping is opt-in and absence means "cannot prove
it", so every case this producer does not recognise keeps flowing. That makes an
under-stamp a missed precision win and an OVER-stamp a silenced real finding —
the second is a false negative on a security analysis, so the tests below spend
most of their weight on things that must NOT be stamped.
"""

from __future__ import annotations

import ast
from pathlib import Path

from hypergumbo_core.ir import Edge
from hypergumbo_lang_mainstream.py import (
    _call_arg_shape,
    _receiver_cannot_carry_taint,
    _stamp_call_arg_shape,
    extract_nodes,
)


def _shape(tmp_path: Path, src: str, callee: str) -> str | None:
    """The ``call_arg_shape`` on the edge to *callee*, or None."""
    f = tmp_path / "m.py"
    f.write_text(src)
    res = extract_nodes(f)
    hits = [e for e in res.edges if e.dst.endswith(f":{callee}:unresolved")]
    assert hits, f"no edge to {callee}; edges={[e.dst for e in res.edges]}"
    return (hits[0].meta or {}).get("call_arg_shape")


_PREAMBLE = "import tempfile\ndef f(x):\n"


class TestStampedWhenProvable:
    def test_no_arguments_at_all(self, tmp_path: Path) -> None:
        assert _shape(
            tmp_path, _PREAMBLE + "    tempfile.TemporaryDirectory()\n",
            "TemporaryDirectory",
        ) == "literal_only"

    def test_keyword_literal_only(self, tmp_path: Path) -> None:
        """``NamedTemporaryFile(delete=True)`` — a keyword argument IS an
        argument, but a literal one cannot be the tainted value."""
        assert _shape(
            tmp_path, _PREAMBLE + "    tempfile.NamedTemporaryFile(delete=True)\n",
            "NamedTemporaryFile",
        ) == "literal_only"

    def test_positional_literals(self, tmp_path: Path) -> None:
        assert _shape(
            tmp_path, _PREAMBLE + "    tempfile.TemporaryFile('w', 1)\n",
            "TemporaryFile",
        ) == "literal_only"

    def test_a_negative_number_is_conservatively_not_stamped(
        self, tmp_path: Path
    ) -> None:
        """KNOWN CONSERVATIVE GAP, pinned rather than fixed.

        ``-1`` parses as ``ast.UnaryOp(USub, Constant(1))``, not
        ``ast.Constant``, so it falls through to "cannot prove". Semantically it
        is a literal and stamping it would be sound, but the asymmetry that
        makes this gate safe is that unrecognised constructs must not be
        claimed constant. This costs a precision win on
        ``TemporaryFile('w', -1)`` and can never silence a real finding, which
        is the trade this key is designed around. Pinned so that a future
        widening to unary-signed constants is a deliberate change with a test
        to flip, rather than an accident.
        """
        assert _shape(
            tmp_path, _PREAMBLE + "    tempfile.TemporaryFile('w', -1)\n",
            "TemporaryFile",
        ) is None


class TestNotStampedWhenItCannotBeProven:
    """Each of these would be a SILENCED REAL FINDING if stamped."""

    def test_a_bare_name_argument(self, tmp_path: Path) -> None:
        assert _shape(
            tmp_path, _PREAMBLE + "    tempfile.TemporaryDirectory(x)\n",
            "TemporaryDirectory",
        ) is None

    def test_a_keyword_argument_bound_to_a_name(self, tmp_path: Path) -> None:
        assert _shape(
            tmp_path, _PREAMBLE + "    tempfile.TemporaryDirectory(dir=x)\n",
            "TemporaryDirectory",
        ) is None

    def test_a_computed_argument(self, tmp_path: Path) -> None:
        """``os.path.join(d,'tmp.zip')`` is the 0003 ZipFile shape: the value is
        computed, so the producer must not claim it is a literal."""
        assert _shape(
            tmp_path, _PREAMBLE + "    tempfile.TemporaryDirectory(x + '/s')\n",
            "TemporaryDirectory",
        ) is None

    def test_an_fstring_argument(self, tmp_path: Path) -> None:
        """An f-string looks constant and is not — it interpolates."""
        assert _shape(
            tmp_path, _PREAMBLE + "    tempfile.TemporaryDirectory(f'{x}')\n",
            "TemporaryDirectory",
        ) is None

    def test_starargs_hide_the_argument_list(self, tmp_path: Path) -> None:
        """``f(*args)`` passes an unknown number of unknown values."""
        assert _shape(
            tmp_path, _PREAMBLE + "    tempfile.TemporaryDirectory(*x)\n",
            "TemporaryDirectory",
        ) is None

    def test_double_starargs_hide_the_keyword_list(self, tmp_path: Path) -> None:
        assert _shape(
            tmp_path, _PREAMBLE + "    tempfile.TemporaryDirectory(**x)\n",
            "TemporaryDirectory",
        ) is None


class TestTheReceiverIsPartOfTheQuestion:
    """A method call's RECEIVER can carry the taint even when every argument
    is a literal — so argument-only reasoning is not enough to stamp.

    FOUND BY MEASUREMENT, NOT BY REVIEW. The first cut of this producer keyed
    on arguments alone and stamped pretix's::

        Path(DATA_DIR).mkdir(parents=False, exist_ok=True)   # settings.py:74

    Both arguments are literals, so it claimed the call could not carry taint —
    but ``DATA_DIR`` is an environment read and the receiver decides WHICH
    DIRECTORY IS CREATED. That is a real flow under the rubric taint models
    ("an argument to the sink call **or the receiver of it**"), and stamping it
    silenced six such findings in pretix and three more in mitmproxy
    (``f.write(literal)``, ``logger.warn(literal)``).

    A silenced real finding is the exact failure this key is supposed to be
    incapable of, so the rule is now: stamp only when there is no receiver to
    worry about — a bare ``Name`` call — or when the receiver is provably an
    imported MODULE, which is not a value that taint can reach.
    """

    def test_the_pretix_mkdir_shape_is_refused(self) -> None:
        """``Path(DATA_DIR).mkdir(parents=False, exist_ok=True)`` — the exact
        call that the argument-only rule stamped and thereby silenced.

        Asserted against the decision function rather than through
        ``extract_nodes``: the ``mkdir`` call edge is minted by a later pass,
        not by single-file extraction, so an end-to-end fixture here would
        assert on an empty edge list and pass for the wrong reason.
        """
        call = ast.parse(
            "Path(D).mkdir(parents=False, exist_ok=True)"
        ).body[0].value
        assert isinstance(call, ast.Call)
        # Arguments alone say "safe" — which is exactly the trap.
        assert _call_arg_shape(call) == "literal_only"
        # The receiver is a call result, not a module, so it is refused.
        assert _receiver_cannot_carry_taint(call, {"os": "os"}) is False

    def test_a_module_receiver_is_accepted_by_the_same_function(self) -> None:
        """Discriminating control for the assertion above: same function, a
        receiver that IS an imported module."""
        call = ast.parse("tempfile.TemporaryDirectory()").body[0].value
        assert isinstance(call, ast.Call)
        assert _receiver_cannot_carry_taint(
            call, {"tempfile": "tempfile"}
        ) is True

    def test_a_name_receiver_that_is_not_an_imported_module_is_refused(
        self,
    ) -> None:
        """``logger.warn('literal')`` — a bare name receiver only counts when
        it is a known imported module, so an ordinary object is refused."""
        call = ast.parse("logger.warn('literal')").body[0].value
        assert isinstance(call, ast.Call)
        assert _receiver_cannot_carry_taint(call, {"tempfile": "tempfile"}) is False

    def test_method_call_on_a_local_name_is_not_stamped(self, tmp_path: Path) -> None:
        """``f.write('literal')`` — the file object's path may be tainted."""
        src = (
            "def g(f):\n"
            "    f.write('MITMWEB_STATIC = true;')\n"
        )
        assert _shape(tmp_path, src, "write") is None

    def test_module_receiver_is_still_stamped(self, tmp_path: Path) -> None:
        """POSITIVE CONTROL. Narrowing to module receivers must not make the
        key inert — ``tempfile`` is an imported module, not a value, so it
        cannot be the tainted thing."""
        assert _shape(
            tmp_path, _PREAMBLE + "    tempfile.TemporaryDirectory()\n",
            "TemporaryDirectory",
        ) == "literal_only"

    def test_bare_name_call_is_still_stamped(self, tmp_path: Path) -> None:
        """POSITIVE CONTROL. A bare ``Name`` call has no receiver at all, so
        narrowing to module receivers must not lose it."""
        src = "def f():\n    open('out.txt', 'w')\n"
        assert _shape(tmp_path, src, "open") == "literal_only"

    def test_the_same_bare_call_with_a_name_argument_is_not_stamped(
        self, tmp_path: Path
    ) -> None:
        """The discriminating pair for the control above: identical call, one
        literal argument replaced by a name."""
        src = "def f(x):\n    open(x, 'w')\n"
        assert _shape(tmp_path, src, "open") is None


class TestOnlyCallFamilyEdgesAreStamped:
    """The key's declared scope is the call family, and the stamp honours it.

    ``_process_call`` can append a non-call edge — a ``references`` edge for a
    module-level receiver variable — into the same slice as the call edge being
    stamped. "What can these arguments carry" is not a question about a
    reference, and ``call_arg_shape``'s ``applicable_edge_types`` says so.

    Asserted against the stamping function directly: reaching the ``references``
    branch end-to-end needs a module-level variable resolved through
    ``global_symbols``, and a fixture that elaborate would be testing the
    resolver rather than this filter.
    """

    def test_a_non_call_family_edge_in_the_same_slice_is_skipped(self) -> None:
        call = ast.parse("open('out.txt', 'w')").body[0].value
        assert isinstance(call, ast.Call)
        edges = [
            Edge.create(src="python:m.py:1-2:f:function",
                        dst="python:builtins:0-0:open:unresolved",
                        edge_type="calls", line=2, evidence_type="ast_call",
                        origin="test", origin_run_id="run"),
            Edge.create(src="python:m.py:1-2:f:function",
                        dst="python:m.py:1-1:CONST:variable",
                        edge_type="references", line=2,
                        evidence_type="ast_name_read",
                        origin="test", origin_run_id="run"),
        ]
        _stamp_call_arg_shape(edges, 0, call, {})
        assert (edges[0].meta or {}).get("call_arg_shape") == "literal_only"
        assert (edges[1].meta or {}).get("call_arg_shape") is None
