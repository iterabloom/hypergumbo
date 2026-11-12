# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for call edges on Haskell zero-argument IO actions.

WHY THIS NEEDS ITS OWN SEAM. Every catalogue is keyed on a callee name, and
the application branch only sees function APPLICATIONS. A zero-argument IO
action is a BARE IDENTIFIER in every position it appears, so without this seam
``getLine`` produces no edge at all while ``readProcess "uname" [] ""`` in the
identical flow shape produces one -- and no correction to a catalogue row's
boundary can make the missing edge appear. The same mechanism covers every
zero-argument row, not only the console reads: ``getArgs``,
``getEnvironment``, ``getCurrentDirectory``, ``getProgName``,
``getCurrentTime``, ``exitFailure``.

WHY THE SCOPE IS ``do``-STATEMENTS PLUS A COMBINATOR ARGUMENT, and not every
position a bare identifier can be executed in. The positions were chosen by
classifying every bare-identifier site as catalogued, a first-party
definition, bound inside the enclosing definition, or another import. The
three covered positions -- ``pat <- act``, a bare ``do`` statement and
``liftIO act`` -- hold the large majority of catalogued sites at the lowest
wrong-edge (locally bound) rates. The infix operands (``<$>``, ``>>=``,
``=<<``, ``<*>``, ``>>``, ``*>``/``<*``) are wrong markedly more often and add
few catalogued sites. The exclusion is a measured trade, not an oversight;
``test_infix_operand_is_deliberately_excluded`` pins it so a change has to
re-measure rather than assume. When re-measuring, deduplicate repositories
symlinked into one another in the corpus, or their sites count twice.

FAIL CLOSED ON A LOCAL BINDING, matching ``_hs_classify_handle_text``'s rule in
the analyzer: this seam ADDS edges, so a name bound inside the enclosing
definition (a ``<-`` binder, a parameter, a ``let``/``where`` binding) is not
guessed at. That costs the ``let g = getLine ; s <- g`` shape, which is a known
and accepted false negative.

(history: INV-fofoj)
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
        """``readProcess`` is an application, seen by the application branch."""
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
        """Not a console read; the mechanism covers every zero-argument action."""
        edges = _calls(
            tmp_path,
            "import System.Exit\nmain = do\n  putStrLn \"x\"\n  exitFailure\n")
        assert "exitFailure" in _callees(edges)


class TestCombinatorArgument:
    """``liftIO act`` / ``void act`` run *act*; the argument is a call, not only the head."""

    def test_void_argument_emits(self, tmp_path: Path) -> None:
        edges = _calls(tmp_path, "main = void getLine\n")
        assert "getLine" in _callees(edges)

    def test_the_combinator_head_still_emits(self, tmp_path: Path) -> None:
        edges = _calls(tmp_path, "main = void getLine\n")
        assert "void" in _callees(edges)

    def test_lift_io_argument_emits(self, tmp_path: Path) -> None:
        """``liftIO getCurrentTime`` is the most common site in this position."""
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
        """``<$>`` is excluded on measurement; see the module docstring."""
        edges = _calls(tmp_path, "main = do\n  c <- lines <$> getContents\n  return c\n")
        assert "getContents" not in _callees(edges)

    def test_return_is_skipped_exactly_as_in_an_application(
        self, tmp_path: Path,
    ) -> None:
        """``return`` is a monadic lift, not a call, in EITHER position.

        The two positions build their edges through one shared emitter, but
        each applies the ``return`` skip itself, so this pins that neither
        side drops it.
        """
        edges = _calls(tmp_path, 'main = do\n  return\n  putStrLn "x"\n')
        assert "return" not in _callees(edges)
        assert "putStrLn" in _callees(edges)

    def test_a_definition_right_hand_side_is_not_a_call(self, tmp_path: Path) -> None:
        """``g = getLine`` names the action; it does not run it."""
        edges = _calls(tmp_path, "g = getLine\nmain = return ()\n")
        assert "getLine" not in _callees(edges)


class TestEdgeShape:
    """Monadic-action edges have the shape of application edges, bar ``call_construct``."""

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
