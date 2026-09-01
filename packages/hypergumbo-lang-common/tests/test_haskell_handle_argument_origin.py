# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for stamping ``io_target_kind`` from a Haskell handle argument.

WI-lipis, second language. ``System.IO.hGetLine`` is declared under BOTH
``fs_read`` and ``ipc_recv`` because which one is true is a property of its
HANDLE ARGUMENT -- ``hGetLine stdin`` receives from the parent process and
``hGetLine h`` (from ``openFile``) reads a file. One row cannot say that, which
is INV-bagok's statement, and no mode literal settles it, which is why this is
the target-kind seam rather than the mode one.

MEASURED BEFORE BUILDING, with a live control in one cold run over a single
file: ``readProcess`` reaching ``writeFile`` returned VIOLATED while
``hGetLine stdin`` with the identical flow shape returned nothing. The control
matters -- the first Rust probe for this same item used a call form the analyzer
does not detect, so every case read "no finding" and would have confirmed
exactly the wrong conclusion.

THE HANDLE IS ALWAYS THE FIRST ARGUMENT in this family (``hGetLine h``,
``hGet h n``, ``hGetSome h n``), which is why there is no per-function argument
index here and there IS one in the C sibling: C puts the stream third in
``fgets`` and first in ``fscanf``, and a single "last argument" rule would
misread one of them in the direction that invents a crossing.
"""
from pathlib import Path

import pytest

from hypergumbo_lang_common.haskell import (
    _hs_classify_handle_text,
    is_haskell_tree_sitter_available,
)

pytestmark = pytest.mark.skipif(
    not is_haskell_tree_sitter_available(),
    reason="tree-sitter-haskell not installed",
)


def _edges(tmp_path: Path, content: str) -> list:
    """Analyze one Haskell file and return its call edges."""
    from hypergumbo_lang_common.haskell import analyze_haskell

    (tmp_path / "Main.hs").write_text(content)
    result = analyze_haskell(tmp_path)
    return [e for e in result.edges if e.edge_type == "calls"]


def _kind_for(edges: list, callee: str) -> object:
    """The ``io_target_kind`` stamped on the edge calling ``callee``."""
    for e in edges:
        if e.dst.endswith(f":{callee}:function") or f":{callee}:" in e.dst:
            return (e.meta or {}).get("io_target_kind")
    return "NO-EDGE"


class TestClassifyHandleText:
    """The text-to-kind rule, isolated. ONE HOME, so inline and resolved agree."""

    def test_stdin_is_a_standard_stream(self) -> None:
        assert _hs_classify_handle_text("stdin") == "std_stream"

    def test_stdout_and_stderr_are_standard_streams(self) -> None:
        assert _hs_classify_handle_text("stdout") == "std_stream"
        assert _hs_classify_handle_text("stderr") == "std_stream"

    def test_open_file_is_a_host_path(self) -> None:
        assert _hs_classify_handle_text('openFile "/x" ReadMode') == "host_path"

    def test_open_binary_file_is_a_host_path(self) -> None:
        assert _hs_classify_handle_text('openBinaryFile "/x" ReadMode') == (
            "host_path"
        )

    def test_an_unknown_name_is_unclassified(self) -> None:
        """FAIL CLOSED. An unrecognised handle stamps nothing at all."""
        assert _hs_classify_handle_text("h") is None

    def test_a_bare_producer_name_without_application_is_unclassified(
        self,
    ) -> None:
        """``openFile`` passed as a VALUE is not an opened handle."""
        assert _hs_classify_handle_text("openFile") is None

    def test_whitespace_is_tolerated(self) -> None:
        assert _hs_classify_handle_text("  stdin  ") == "std_stream"


class TestStampingFromTheCallSite:
    """End to end through the analyzer, which is what production runs."""

    def test_stdin_handle_stamps_std_stream(self, tmp_path: Path) -> None:
        edges = _edges(tmp_path, (
            "module Main where\n"
            "import System.IO\n"
            "f :: IO ()\n"
            "f = do\n"
            "  s <- hGetLine stdin\n"
            "  writeFile \"/tmp/a\" s\n"
        ))
        assert _kind_for(edges, "hGetLine") == "std_stream"

    def test_open_file_handle_stamps_host_path(self, tmp_path: Path) -> None:
        edges = _edges(tmp_path, (
            "module Main where\n"
            "import System.IO\n"
            "f :: IO ()\n"
            "f = do\n"
            "  h <- openFile \"/etc/motd\" ReadMode\n"
            "  s <- hGetLine h\n"
            "  writeFile \"/tmp/a\" s\n"
        ))
        assert _kind_for(edges, "hGetLine") == "host_path"

    def test_unbound_handle_stamps_nothing(self, tmp_path: Path) -> None:
        """A PARAMETER's origin is not in this function. Abstain."""
        edges = _edges(tmp_path, (
            "module Main where\n"
            "import System.IO\n"
            "f :: Handle -> IO ()\n"
            "f h = do\n"
            "  s <- hGetLine h\n"
            "  writeFile \"/tmp/a\" s\n"
        ))
        assert _kind_for(edges, "hGetLine") is None

    def test_two_argument_reader_uses_the_first_argument(
        self, tmp_path: Path,
    ) -> None:
        """``hGet h n`` -- the handle is first, the count is not a handle."""
        edges = _edges(tmp_path, (
            "module Main where\n"
            "import qualified Data.ByteString as BS\n"
            "f :: IO ()\n"
            "f = do\n"
            "  s <- BS.hGet stdin 10\n"
            "  return ()\n"
        ))
        assert _kind_for(edges, "hGet") == "std_stream"

    def test_a_rebinding_below_the_call_does_not_reach_it(
        self, tmp_path: Path,
    ) -> None:
        """ORDER IS THE POINT: a last-match-in-file scan would read this."""
        edges = _edges(tmp_path, (
            "module Main where\n"
            "import System.IO\n"
            "f :: IO ()\n"
            "f = do\n"
            "  s <- hGetLine h\n"
            "  h <- openFile \"/etc/motd\" ReadMode\n"
            "  return ()\n"
        ))
        assert _kind_for(edges, "hGetLine") is None

    def test_the_nearest_binding_above_the_call_wins(
        self, tmp_path: Path,
    ) -> None:
        """A first-match scan would take the file handle instead of stdin."""
        edges = _edges(tmp_path, (
            "module Main where\n"
            "import System.IO\n"
            "f :: IO ()\n"
            "f = do\n"
            "  h <- openFile \"/etc/motd\" ReadMode\n"
            "  h <- return stdin\n"
            "  s <- hGetLine h\n"
            "  return ()\n"
        ))
        assert _kind_for(edges, "hGetLine") in (None, "std_stream")

    def test_a_non_reader_call_is_not_stamped(self, tmp_path: Path) -> None:
        """Only the handle-taking READ family is a target-kind question."""
        edges = _edges(tmp_path, (
            "module Main where\n"
            "import System.IO\n"
            "f :: IO ()\n"
            "f = writeFile \"/tmp/a\" \"x\"\n"
        ))
        assert _kind_for(edges, "writeFile") is None

    def test_a_reader_with_no_arguments_is_not_stamped(
        self, tmp_path: Path,
    ) -> None:
        """``getLine`` takes no handle; there is nothing to classify."""
        edges = _edges(tmp_path, (
            "module Main where\n"
            "f :: IO ()\n"
            "f = do\n"
            "  s <- getLine\n"
            "  writeFile \"/tmp/a\" s\n"
        ))
        assert _kind_for(edges, "getLine") == "NO-EDGE"


class TestBindingLookupEdgesThatReturnNothing:
    """The abstention paths, reached by real Haskell rather than by a mock.

    Both must stamp NOTHING. This seam ADDS findings, so a handle whose origin
    the analyzer cannot name has to leave the call classified exactly as the
    catalogue's first-declared row says.
    """

    def test_a_parameterised_definition_still_resolves_its_bindings(
        self, tmp_path: Path,
    ) -> None:
        """``f p = do ...`` is a ``function`` node, NOT a ``bind``.

        This is the COMMON shape for the code this seam is about -- a Handle is
        usually opened from a path the function was handed -- and searching only
        ``bind`` abstained on every one of them, losing the origin silently.
        Found by a coverage gap, not by reading the grammar, which is why it is
        pinned here rather than left to the two zero-argument cases above.
        """
        edges = _edges(tmp_path, (
            "module Main where\n"
            "import System.IO\n"
            "f :: FilePath -> IO String\n"
            "f p = do\n"
            "  h <- openFile p ReadMode\n"
            "  hGetLine h\n"
        ))
        assert _kind_for(edges, "hGetLine") == "host_path"

    def test_a_handle_parameter_still_abstains(self, tmp_path: Path) -> None:
        """The fix above must NOT make a parameter resolvable.

        ``f q = hGetLine q`` has an enclosing definition now, but ``q`` is bound
        by the parameter list and has no origin in this scope. Abstaining is the
        right answer and INV-zumin forbids the alternative (one answer per call
        site or none).
        """
        edges = _edges(tmp_path, (
            "module Main where\n"
            "import System.IO\n"
            "f :: Handle -> IO String\n"
            "f q = hGetLine q\n"
        ))
        assert _kind_for(edges, "hGetLine") is None

    def test_a_definition_is_not_read_as_a_value_binding(
        self, tmp_path: Path,
    ) -> None:
        """``f = ...`` binds no value in ``f``'s own scope.

        Haskell uses ``bind`` for BOTH a definition and a ``do`` statement, and
        only the second binds a value here. Without the ``<-`` check the
        enclosing definition would match its own name and hand back its entire
        right-hand side as the handle's origin.
        """
        edges = _edges(tmp_path, (
            "module Main where\n"
            "import System.IO\n"
            "f = g\n"
            "  where g = hGetLine f\n"
        ))
        assert _kind_for(edges, "hGetLine") is None
