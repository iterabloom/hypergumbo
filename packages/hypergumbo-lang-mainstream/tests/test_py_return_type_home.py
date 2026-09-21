# SPDX-License-Identifier: AGPL-3.0-or-later
"""WI-ribak: Python reads a callee's return type from its declared home.

ADR-0058 declared the callable-signature axis and closed its consumer set:
``Symbol.signature`` is a DISPLAY string, and the facts inside it are read
from their declared homes. ``py.py`` was the largest violator -- it parsed
``assigned_class.signature`` to seed ``var_types``, which is how a Python
variable gets a type at all, and therefore what feeds ``receiver_type_hint``
and the method-call-recovery linker.

THE HOME IS ``Symbol.meta["return_type"]`` -- the registered meta key
(``axis_meta_keys.py:1082``) that java, luau and apex already populate, and
whose sibling ``inferred_return_type`` names Python as an intended producer.
NOT ``FileAnalysis.method_return_types``: ``py.py`` has its OWN
``FileAnalysis`` (py.py:2181), a different dataclass from the tree-sitter
base's, and it never reaches the core linker that needs the fact.

EQUIVALENCE WAS MEASURED, NOT ASSUMED: over 33,740 functions in this
repository the AST-derived value and the old signature parse agreed on every
one, 0 disagreements. WI-hopiz is why -- display-mode truncation preserves
the return type rather than cutting it off.

SPLIT OF RESPONSIBILITY. The producer records the declared type VERBATIM,
including ``Optional[Foo]`` and ``list[Foo]``. The narrowness (simple
identifiers only) is the CONSUMER's policy and is unchanged, so this move
neither widens nor narrows what gets inferred.
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from hypergumbo_lang_mainstream.py import analyze_python


def _symbols(root: Path, source: str, name: str = "app.py") -> dict:
    root.mkdir(parents=True, exist_ok=True)
    (root / name).write_text(source, encoding="utf-8")
    result = analyze_python(root)
    return {s.name: s for s in result.symbols}


def _meta(sym) -> dict:
    return sym.meta or {}


class TestTheProducerStampsTheHome:
    def test_an_annotated_function_carries_its_return_type(
        self, tmp_path: Path
    ) -> None:
        syms = _symbols(
            tmp_path / "fn",
            "class Client:\n"
            "    pass\n"
            "\n"
            "def make() -> Client:\n"
            "    return Client()\n",
        )
        assert _meta(syms["make"]).get("return_type") == "Client"

    def test_an_annotated_method_carries_its_return_type(
        self, tmp_path: Path
    ) -> None:
        syms = _symbols(
            tmp_path / "meth",
            "class Store:\n"
            "    pass\n"
            "\n"
            "class Set:\n"
            "    def workspace(self) -> Store:\n"
            "        return self._w\n",
        )
        # methods are keyed by their qualified name, not the bare one
        assert _meta(syms["Set.workspace"]).get("return_type") == "Store"

    def test_an_unannotated_function_has_NO_return_type_KEY(
        self, tmp_path: Path
    ) -> None:
        # ABSENT, not empty. A missing key predates the instrument; a null
        # value would claim the producer looked and found nothing.
        syms = _symbols(
            tmp_path / "bare",
            "def make():\n"
            "    return 1\n",
        )
        assert "return_type" not in _meta(syms["make"])

    def test_a_complex_annotation_is_stored_VERBATIM(
        self, tmp_path: Path
    ) -> None:
        # The home records the DECLARED type; filtering is the consumer's job.
        syms = _symbols(
            tmp_path / "cplx",
            "from typing import Optional\n"
            "class Client:\n"
            "    pass\n"
            "\n"
            "def maybe() -> Optional[Client]:\n"
            "    return None\n",
        )
        assert _meta(syms["maybe"]).get("return_type") == "Optional[Client]"

    def test_an_async_function_is_stamped_too(self, tmp_path: Path) -> None:
        syms = _symbols(
            tmp_path / "async",
            "class Client:\n"
            "    pass\n"
            "\n"
            "async def make() -> Client:\n"
            "    return Client()\n",
        )
        assert _meta(syms["make"]).get("return_type") == "Client"


class TestTheConsumerStillInfers:
    """The behaviour py.py:5881 provided, preserved through the move."""

    def _edges(self, root: Path, source: str) -> list:
        root.mkdir(parents=True, exist_ok=True)
        (root / "app.py").write_text(source, encoding="utf-8")
        return analyze_python(root).edges

    SRC = (
        "class Client:\n"
        "    def send(self):\n"
        "        pass\n"
        "\n"
        "def make() -> Client:\n"
        "    return Client()\n"
        "\n"
        "def run():\n"
        "    c = make()\n"
        "    c.send()\n"
    )

    def test_a_factory_return_types_the_variable(self, tmp_path: Path) -> None:
        edges = self._edges(tmp_path / "infer", self.SRC)
        assert any(
            "Client.send:method" in e.dst for e in edges
        ), "c.send() did not resolve through make()'s declared return type"

    def test_it_no_longer_depends_on_the_DISPLAY_STRING(
        self, tmp_path: Path
    ) -> None:
        """The difference arm, and the whole point of the move.

        Render every signature without its return type. Under the old code
        the inference read that string and would find nothing; reading the
        declared home, it is unaffected.
        """
        real = analyze_python

        def _stripped(node, max_len=240, render_defaults=False):
            return "()"

        with patch(
            "hypergumbo_lang_mainstream.py._format_function_signature",
            side_effect=_stripped,
        ):
            root = tmp_path / "nostr"
            root.mkdir(parents=True, exist_ok=True)
            (root / "app.py").write_text(self.SRC, encoding="utf-8")
            edges = real(root).edges

        assert any(
            "Client.send:method" in e.dst for e in edges
        ), "inference still depends on Symbol.signature"


class TestAForwardReferenceIsStillATypeName:
    """WI-fihun: ``-> "Client"`` names exactly the type it spells.

    ``ast.unparse`` renders a string annotation WITH its quotes, so the
    identifier check rejected it. PEP 484 says a string annotation names the
    type it spells and every type checker resolves it, so unquoting widens
    how a type may be SPELLED, not what counts as one.
    """

    def _edges(self, root: Path, source: str) -> list:
        root.mkdir(parents=True, exist_ok=True)
        (root / "app.py").write_text(source, encoding="utf-8")
        return analyze_python(root).edges

    def _src(self, ret: str) -> str:
        return (
            "class Client:\n"
            "    def send(self):\n"
            "        return 1\n"
            "\n"
            f"def make() -> {ret}:\n"
            "    return Client()\n"
            "\n"
            "def run():\n"
            "    c = make()\n"
            "    return c.send()\n"
        )

    def test_a_quoted_forward_reference_types_the_variable(
        self, tmp_path: Path
    ) -> None:
        edges = self._edges(tmp_path / "fwd", self._src('"Client"'))
        assert any("Client.send:method" in e.dst for e in edges)

    def test_it_agrees_with_the_unquoted_spelling(self, tmp_path: Path) -> None:
        # The control: same type, two spellings, same outcome.
        quoted = self._edges(tmp_path / "q", self._src('"Client"'))
        plain = self._edges(tmp_path / "p", self._src("Client"))
        assert (
            any("Client.send:method" in e.dst for e in quoted)
            == any("Client.send:method" in e.dst for e in plain)
            is True
        )

    def test_a_quoted_GENERIC_still_does_not_type(self, tmp_path: Path) -> None:
        # The difference arm: unquoting must not smuggle generics through.
        edges = self._edges(
            tmp_path / "qg",
            "from typing import Optional\n" + self._src('"Optional[Client]"'),
        )
        assert all("Client.send:method" not in e.dst for e in edges)

    def test_an_empty_string_annotation_does_not_crash(
        self, tmp_path: Path
    ) -> None:
        edges = self._edges(tmp_path / "empty", self._src('""'))
        assert all("Client.send:method" not in e.dst for e in edges)


class TestTheConsumerKeepsItsNarrowness:
    """Neither widened nor narrowed: only simple identifiers infer."""

    def _edges(self, root: Path, source: str) -> list:
        root.mkdir(parents=True, exist_ok=True)
        (root / "app.py").write_text(source, encoding="utf-8")
        return analyze_python(root).edges

    def test_a_generic_return_does_NOT_type_the_variable(
        self, tmp_path: Path
    ) -> None:
        edges = self._edges(
            tmp_path / "gen",
            "from typing import Optional\n"
            "class Client:\n"
            "    def send(self):\n"
            "        pass\n"
            "\n"
            "def make() -> Optional[Client]:\n"
            "    return Client()\n"
            "\n"
            "def run():\n"
            "    c = make()\n"
            "    c.send()\n",
        )
        assert all(
            "Client.send:method" not in e.dst for e in edges
        ), "a generic return type should not infer (unchanged policy)"
