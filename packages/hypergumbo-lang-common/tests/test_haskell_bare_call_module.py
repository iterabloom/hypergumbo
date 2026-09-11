# SPDX-License-Identifier: AGPL-3.0-or-later
"""WI-lokun: giving a BARE haskell call the module its file already names.

`haskell.py` emits an unresolved call as `haskell:<hint or external>:0-0:
<name>:function`, and the hint was set only by a QUALIFIED import alias. The
`external` sentinel is deliberate — it makes the catalogue tagger use
unfiltered short-name matching — but `gate_named_entry` ends:

    if ambiguous_names and name in ambiguous_names:
        return None

so with no module hint an AMBIGUOUS name reaches nothing while an unambiguous
one still matches. The servant baseline shows the split in one table:
`Prelude.putStrLn` classified with 8 chains and `Prelude.print` was absent,
from the same module, the same files, and the same bare call shape.

SCOPE IS EXACTLY THAT ASYMMETRY. Only a name in `ambiguous_names` is stamped,
because only that population can be helped: stamping an unambiguous name
switches module filtering ON for a call that already classifies, buying
nothing and risking the fallback. WI-lajus is the filed cautionary case —
stamping too broadly suppressed a permissive fallback and moved verdicts the
wrong way — and these tests pin the abstentions as hard as the stamps.

TWO ROUTES COUNT AS EVIDENCE, everything else abstains:
  (a) an unqualified `import M (name)`, which is the route the item filed;
  (b) the IMPLICIT Prelude, which is where the filed INSTANCE lives — `print`
      is catalogued under `Prelude`, and Prelude appears in no import line
      because Haskell imports it implicitly.
"""
from pathlib import Path

import pytest


def _analyze(tmp_path: Path, body: str):
    from hypergumbo_lang_common.haskell import analyze_haskell

    (tmp_path / "Main.hs").write_text(body)
    return analyze_haskell(tmp_path)


def _call_dsts(result) -> list[str]:
    return [e.dst for e in result.edges if e.edge_type == "calls"]


MODULE = """\
module Main where
%s

main :: IO ()
main = do
%s
  return ()
"""


class TestTheFiledInstance:
    """`print err` on servant — the one true R2-route failure of the study."""

    def test_a_bare_print_carries_prelude(self, tmp_path: Path) -> None:
        """Prelude is implicit, so no import line names it — and that is why
        this needed its own route rather than the import-list one."""
        result = _analyze(tmp_path, MODULE % ("", "  print (1 :: Int)"))

        assert "haskell:Prelude:0-0:print:function" in _call_dsts(result)

    def test_the_stamped_module_reaches_its_catalogue_row(
        self, tmp_path: Path,
    ) -> None:
        """End-to-end on the real gate, not a restatement of it.

        The gate is what refused this call, so the test asserts against the
        gate rather than against the id string.
        """
        from hypergumbo_core.io_boundary import gate_named_entry, load_catalog

        catalog = load_catalog("haskell")
        hits = [p for p in catalog.primitives if p.name == "print"]
        assert hits, "catalogue has no print row"

        # Without a module hint the gate refuses it — the defect.
        assert gate_named_entry(
            hits, "print", "external", catalog.ambiguous_names,
        ) is None
        # The analyzer now supplies one, and it names the catalogued module.
        result = _analyze(tmp_path, MODULE % ("", "  print (1 :: Int)"))
        assert "haskell:Prelude:0-0:print:function" in _call_dsts(result)
        assert any(p.module == "Prelude" and p.boundary == "logging"
                   for p in hits)


class TestTheImportListRoute:
    """`import M (name)` then a bare `name` — the route the item filed."""

    def test_an_unqualified_import_list_supplies_the_module(
        self, tmp_path: Path,
    ) -> None:
        result = _analyze(tmp_path, MODULE % (
            "import Network.Socket.ByteString (recv)",
            "  _ <- recv sock 1024",
        ))

        assert "haskell:Network.Socket.ByteString:0-0:recv:function" in _call_dsts(result)

    def test_an_explicit_import_beats_the_implicit_prelude(
        self, tmp_path: Path,
    ) -> None:
        """The hazard case, and the one that decides the rule's order.

        `import Prelude hiding (print)` beside an import that claims `print`
        means the call is NOT Prelude's. Stamping Prelude here would be a
        wrong module hint, which switches module filtering on and kills the
        permissive path — strictly worse than abstaining.
        """
        result = _analyze(tmp_path, MODULE % (
            "import Debug.Trace (print)\nimport Prelude hiding (print)",
            "  print (1 :: Int)",
        ))
        dsts = _call_dsts(result)

        assert "haskell:Debug.Trace:0-0:print:function" in dsts
        assert "haskell:Prelude:0-0:print:function" not in dsts

    def test_a_qualified_import_list_does_not_put_the_bare_name_in_scope(
        self, tmp_path: Path,
    ) -> None:
        """`import qualified M (recv)` requires `M.recv`, so a bare `recv`
        cannot have come from it and must not be attributed to it."""
        result = _analyze(tmp_path, MODULE % (
            "import qualified Network.Socket.ByteString (recv)",
            "  _ <- recv sock 1024",
        ))

        assert "haskell:Network.Socket.ByteString:0-0:recv:function" not in _call_dsts(result)


class TestTheShapeServantActuallyUses:
    """`import Prelude ()` + `import Prelude.Compat` — the base-compat idiom.

    All three servant sites this cell fixes are written this way, and it is
    NOT the implicit-Prelude case the rule reasons about: `import Prelude ()`
    has an EMPTY import list, which suppresses the implicit Prelude, and the
    name actually arrives re-exported from `Prelude.Compat`.

    Stamping `Prelude` is still right, for a reason worth writing down rather
    than leaving to luck: `Prelude.Compat` re-exports `Prelude.print`
    verbatim, so the function IS Prelude's and the boundary (`logging`) is
    Prelude's. What the rule gets right here it gets right through the
    catalogue's notion of where the function LIVES, not through a claim about
    which import line brought it into scope.

    The precedence still protects the dangerous case: an explicit import that
    CLAIMS the name beats the Prelude route, so a genuinely different `print`
    is attributed to whatever names it.
    """

    def test_the_base_compat_idiom_still_reaches_prelude(
        self, tmp_path: Path,
    ) -> None:
        result = _analyze(tmp_path, MODULE % (
            "import Prelude ()\nimport Prelude.Compat",
            "  print (1 :: Int)",
        ))

        assert "haskell:Prelude:0-0:print:function" in _call_dsts(result)

    def test_an_empty_prelude_list_does_not_count_as_claiming_a_name(
        self, tmp_path: Path,
    ) -> None:
        """`import Prelude ()` registers no explicit claim on anything.

        Pinned because the reader walks `import_list` children: an empty list
        must contribute zero names rather than, say, being mistaken for a
        hiding clause.
        """
        from hypergumbo_lang_common.haskell import _extract_unqualified_imports
        from tree_sitter_language_pack import get_parser

        src = b"module M where\nimport Prelude ()\nimport Prelude.Compat\n"
        explicit, hidden = _extract_unqualified_imports(
            get_parser("haskell").parse(src), src,
        )
        assert explicit == {}
        assert hidden == set()


class TestWhatMustKeepAbstaining:
    """`external` is load-bearing: it is what enables short-name matching."""

    def test_prelude_hiding_with_no_other_claim_stays_external(
        self, tmp_path: Path,
    ) -> None:
        """`import Prelude hiding (print)` and nothing else claims it.

        Whatever `print` now refers to, it is not Prelude's, and the analyzer
        has no evidence for what it IS. Abstain.
        """
        result = _analyze(tmp_path, MODULE % (
            "import Prelude hiding (print)", "  print (1 :: Int)",
        ))

        assert "haskell:external:0-0:print:function" in _call_dsts(result)
        assert "haskell:Prelude:0-0:print:function" not in _call_dsts(result)

    def test_a_gated_name_with_no_route_stays_external(
        self, tmp_path: Path,
    ) -> None:
        """`run` is ambiguous and catalogued, but nothing here names a module."""
        result = _analyze(tmp_path, MODULE % ("", "  run app"))

        assert "haskell:external:0-0:run:function" in _call_dsts(result)

    @pytest.mark.parametrize("name", ["putStrLn", "writeFile", "getLine"])
    def test_an_UNAMBIGUOUS_prelude_name_is_left_alone(
        self, tmp_path: Path, name: str,
    ) -> None:
        """THE CONTROL, and the population a too-broad stamp would damage.

        These are catalogued Prelude names that are NOT in `ambiguous_names`,
        so they already classify through the permissive short-name path — on
        servant `Prelude.putStrLn` carries 8 chains. Stamping them would buy
        nothing and switch module filtering on. They must stay `external`.
        """
        from hypergumbo_lang_common.haskell import (
            HASKELL_GATED_NAMES, HASKELL_PRELUDE_EXPORTS,
        )

        assert name in HASKELL_PRELUDE_EXPORTS
        assert name not in HASKELL_GATED_NAMES

        result = _analyze(tmp_path, MODULE % ("", f"  {name} \"x\""))
        assert f"haskell:external:0-0:{name}:function" in _call_dsts(result)


class TestTheGatedSetIsDerivedNotListed:
    """A row added to `ambiguous_names` later must be covered automatically."""

    def test_gated_names_are_the_catalogued_ambiguous_ones(self) -> None:
        from hypergumbo_core.io_boundary import load_catalog
        from hypergumbo_lang_common.haskell import HASKELL_GATED_NAMES

        catalog = load_catalog("haskell")
        named = {p.name for p in catalog.primitives if p.name}
        assert HASKELL_GATED_NAMES == frozenset(
            named & set(catalog.ambiguous_names or ())
        )
        assert "print" in HASKELL_GATED_NAMES
