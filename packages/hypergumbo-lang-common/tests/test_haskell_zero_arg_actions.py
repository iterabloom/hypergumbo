# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for call edges on Haskell zero-argument IO actions (INV-fofoj).

THE DEFECT. Every catalogue is keyed on a callee name and the analyzer emitted
a ``calls`` edge only for a function APPLICATION. A zero-argument IO action is
a BARE IDENTIFIER in every position it appears, so ``getLine`` produced no edge
at all while ``readProcess "uname" [] ""`` in the identical flow shape produced
one. The row's boundary was irrelevant: correcting the classification, as
INV-muvis did for haskell's Prelude rows, left the call just as undetectable.

WHY THE SCOPE IS ``do``-STATEMENTS PLUS A COMBINATOR ARGUMENT, and not every
position a bare identifier can be executed in. Measured over the eight
DISTINCT repositories that carry the filed population -- every bare-identifier
site classified, then split by whether the name is catalogued, a module-level
definition in the repo, or bound inside the enclosing definition:

    rule                cat  1stparty  localbnd  otherimp   total   wrong
    R1  ``pat <- act``  267      1172       294       861    2594   11.3%
    R2  bare do-stmt     33       768       164       111    1076   15.2%
    R4  ``liftIO act``  142       164        68        65     439   15.5%
    -- excluded --
    R3  ``<$>``          23       376       530       574    1503   35.3%
    R3  ``>>=``           5       116       119       122     362   32.9%
    R3  ``<*>``           1       149       227       320     697   32.6%
    R3  ``=<<``          11        24        23        30      88   26.1%
    R3  ``>>``           23       321       116        62     522   22.2%
    R3  ``*>``/``<*``     0       501       187       307     995   18.8%

The three covered positions carry 442 of the 505 catalogued sites (87.5%) at
the three lowest wrong-edge rates among rules with any payoff. Every infix
operator together adds the remaining 63 for 3,812 more sites at 18-35% wrong.
The exclusion is a measured trade, not an oversight, and
``test_infix_operand_is_deliberately_excluded`` pins it so a later reader has
to re-measure rather than assume.

EIGHT REPOSITORIES, NOT NINE, AND THE CORRECTION CUTS BOTH THIS TABLE AND THE
ITEM'S OWN FILED POPULATION. ``simplex-chat`` is a SYMLINK into
``cohort6_crypto/simplex-chat/``, and all 267 of ``cohort6_crypto``'s ``.hs``
files live under it -- the two paths are one repository. Counting both put
this table at 794 catalogued sites when it is 505, and INV-fofoj's per-repo
tally reads "cohort6_crypto 6, simplex-chat 6" for what is one set of six, so
its filed 25 sites over 9 repositories is 19 over 8. The corpus-is-symlinked
landmine, in the form that inflates a count rather than hanging a walk.

THE POPULATION IS STILL FAR LARGER THAN THE ITEM FILED. INV-fofoj scoped to
getLine/getChar/getContents/readLn. The same mechanism hides every
zero-argument row in the catalogue -- ``getArgs``, ``getEnvironment``,
``getCurrentDirectory``, ``getProgName``, ``getCurrentTime``, ``exitFailure``
-- which is 505 sites, not 19.

FAIL CLOSED ON A LOCAL BINDING, matching ``_hs_classify_handle_text``'s rule in
this same module: this seam ADDS edges, so a name bound inside the enclosing
definition (a ``<-`` binder, a parameter, a ``let``/``where`` binding) is not
guessed at. That costs the ``let g = getLine ; s <- g`` shape, which is a known
and accepted false negative.
"""
from pathlib import Path

import pytest

from hypergumbo_lang_common.haskell import is_haskell_tree_sitter_available

pytestmark = pytest.mark.skipif(
    not is_haskell_tree_sitter_available(),
    reason="tree-sitter-haskell not installed",
)


def _calls(tmp_path: Path, content: str) -> list:
    """Analyze one Haskell file and return its call edges."""
    from hypergumbo_lang_common.haskell import analyze_haskell

    (tmp_path / "Main.hs").write_text(content)
    return [e for e in analyze_haskell(tmp_path).edges if e.edge_type == "calls"]


def _callees(edges: list) -> set[str]:
    """The name slot of every call edge's destination."""
    return {e.dst.split(":")[-2] for e in edges}


def _dst_for(edges: list, name: str) -> str:
    """The single destination whose name slot is *name*, or ``"NO-EDGE"``."""
    hits = {e.dst for e in edges if e.dst.split(":")[-2] == name}
    return hits.pop() if len(hits) == 1 else ("NO-EDGE" if not hits else "AMBIGUOUS")


class TestBindArrowPosition:
    """``pat <- action`` runs *action*; a bare identifier there is a call."""

    def test_get_line_bound_with_arrow_emits_a_call(self, tmp_path: Path) -> None:
        edges = _calls(tmp_path, 'main = do\n  s <- getLine\n  writeFile "o" s\n')
        assert "getLine" in _callees(edges)

    def test_the_control_still_emits(self, tmp_path: Path) -> None:
        """``readProcess`` is an application and was never the broken case."""
        edges = _calls(
            tmp_path, 'main = do\n  s <- readProcess "u" [] ""\n  writeFile "o" s\n')
        assert "readProcess" in _callees(edges)

    def test_the_sink_still_emits(self, tmp_path: Path) -> None:
        edges = _calls(tmp_path, 'main = do\n  s <- getLine\n  writeFile "o" s\n')
        assert "writeFile" in _callees(edges)

    def test_an_unqualified_action_lands_on_the_external_module(
        self, tmp_path: Path,
    ) -> None:
        """Prelude is auto-imported, so there is no alias to name a module with.

        ``external`` is what makes the I/O boundary tagger fall back to
        unfiltered short-name matching, which is how the Prelude row matches.
        """
        edges = _calls(tmp_path, "main = do\n  s <- getLine\n  putStrLn s\n")
        assert _dst_for(edges, "getLine") == "haskell:external:0-0:getLine:function"

    def test_a_qualified_action_carries_its_module(self, tmp_path: Path) -> None:
        """``Data.Text.IO`` rows getLine separately from Prelude's."""
        edges = _calls(
            tmp_path,
            "import qualified Data.Text.IO as T\n"
            "main = do\n  s <- T.getLine\n  T.putStrLn s\n",
        )
        assert _dst_for(edges, "getLine") == (
            "haskell:Data.Text.IO:0-0:getLine:function")

    def test_a_first_party_zero_arg_action_resolves(self, tmp_path: Path) -> None:
        """The largest bucket is not the catalogue -- it is in-repo recall."""
        edges = _calls(
            tmp_path,
            'helper = getLine\n'
            'main = do\n  s <- helper\n  writeFile "o" s\n',
        )
        assert "helper" in _callees(edges)


class TestDoStatementPosition:
    """A bare identifier alone on a ``do`` line is executed for its effect."""

    def test_a_discarded_action_emits(self, tmp_path: Path) -> None:
        edges = _calls(tmp_path, 'main = do\n  getLine\n  putStrLn "x"\n')
        assert "getLine" in _callees(edges)

    def test_exit_failure_emits(self, tmp_path: Path) -> None:
        """Not one of the four names the item filed; same mechanism."""
        edges = _calls(
            tmp_path,
            "import System.Exit\nmain = do\n  putStrLn \"x\"\n  exitFailure\n")
        assert "exitFailure" in _callees(edges)


class TestCombinatorArgument:
    """``liftIO act`` / ``void act`` run *act*; the head alone was emitted."""

    def test_void_argument_emits(self, tmp_path: Path) -> None:
        edges = _calls(tmp_path, "main = void getLine\n")
        assert "getLine" in _callees(edges)

    def test_the_combinator_head_still_emits(self, tmp_path: Path) -> None:
        edges = _calls(tmp_path, "main = void getLine\n")
        assert "void" in _callees(edges)

    def test_lift_io_argument_emits(self, tmp_path: Path) -> None:
        """``liftIO getCurrentTime`` is the single most common site measured."""
        edges = _calls(tmp_path, "main = liftIO getCurrentTime\n")
        assert "getCurrentTime" in _callees(edges)

    def test_an_uncatalogued_head_does_not_promote_its_argument(
        self, tmp_path: Path,
    ) -> None:
        """``mapM_ getLine`` does not RUN getLine -- it is passed as a value."""
        edges = _calls(tmp_path, "main = mapM_ getLine\n")
        assert "getLine" not in _callees(edges)


class TestFailClosedOnLocalBindings:
    """A name bound inside the definition is not guessed at."""

    def test_an_arrow_bound_name_is_not_a_call(self, tmp_path: Path) -> None:
        edges = _calls(tmp_path, "main = do\n  act <- mkAction\n  act\n")
        assert "act" not in _callees(edges)

    def test_a_parameter_is_not_a_call(self, tmp_path: Path) -> None:
        edges = _calls(tmp_path, "run param = do\n  v <- param\n  putStrLn v\n")
        assert "param" not in _callees(edges)

    def test_a_let_bound_name_is_not_a_call(self, tmp_path: Path) -> None:
        """Accepted false negative: ``getLine`` really does run at ``s <- g``."""
        edges = _calls(tmp_path, "main = do\n  let g = getLine\n  s <- g\n  return s\n")
        assert "g" not in _callees(edges)

    def test_a_lambda_parameter_is_not_a_call(self, tmp_path: Path) -> None:
        edges = _calls(tmp_path, "main = mapM_ (\\cb -> do { r <- cb; return r }) []\n")
        assert "cb" not in _callees(edges)


class TestDeliberateExclusions:
    """Pinned so a later reader must re-measure rather than assume."""

    def test_infix_operand_is_deliberately_excluded(self, tmp_path: Path) -> None:
        """``<$>`` measured 36.7% wrong for 40 catalogued sites. See docstring."""
        edges = _calls(tmp_path, "main = do\n  c <- lines <$> getContents\n  return c\n")
        assert "getContents" not in _callees(edges)

    def test_return_is_skipped_exactly_as_in_an_application(
        self, tmp_path: Path,
    ) -> None:
        """``return`` is a monadic lift, not a call, in EITHER position.

        The application branch has skipped it since the analyzer was written.
        Pinned here because the two positions build their edges through one
        shared emitter now, and a skip that held on only one side of it is
        precisely the drift that emitter exists to prevent.
        """
        edges = _calls(tmp_path, 'main = do\n  return\n  putStrLn "x"\n')
        assert "return" not in _callees(edges)
        assert "putStrLn" in _callees(edges)

    def test_a_definition_right_hand_side_is_not_a_call(self, tmp_path: Path) -> None:
        """``g = getLine`` names the action; it does not run it."""
        edges = _calls(tmp_path, "g = getLine\nmain = return ()\n")
        assert "getLine" not in _callees(edges)


class TestEdgeShape:
    """The added edges must be indistinguishable in kind from existing ones."""

    def test_the_construct_is_named_on_the_edge(self, tmp_path: Path) -> None:
        edges = _calls(tmp_path, "main = do\n  s <- getLine\n  putStrLn s\n")
        edge = next(e for e in edges if e.dst.split(":")[-2] == "getLine")
        assert (edge.meta or {}).get("call_construct") == "monadic_action"

    def test_an_application_still_says_application(self, tmp_path: Path) -> None:
        edges = _calls(tmp_path, 'main = do\n  s <- readProcess "u" [] ""\n  return s\n')
        edge = next(e for e in edges if e.dst.split(":")[-2] == "readProcess")
        assert (edge.meta or {}).get("call_construct") == "application"

    def test_the_caller_is_the_enclosing_definition(self, tmp_path: Path) -> None:
        edges = _calls(tmp_path, "main = do\n  s <- getLine\n  putStrLn s\n")
        edge = next(e for e in edges if e.dst.split(":")[-2] == "getLine")
        assert edge.src.split(":")[-2] == "main"

    def test_the_line_is_the_call_site(self, tmp_path: Path) -> None:
        edges = _calls(tmp_path, "main = do\n  putStrLn \"a\"\n  s <- getLine\n  return s\n")
        edge = next(e for e in edges if e.dst.split(":")[-2] == "getLine")
        assert edge.line == 3
