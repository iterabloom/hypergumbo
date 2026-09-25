# SPDX-License-Identifier: AGPL-3.0-or-later
"""INV-muvis: a call that can ONLY read standard input must mint untrusted input.

THE DEFECT, AND ITS DIRECTION. ``AUTO_SOURCE_LABEL_MAP`` maps ``net_recv``,
``ipc_recv`` and ``db_read`` to ``untrusted_input``. ``fs_read`` IS NOT IN IT.
So a stdin read declared ``fs_read`` does not mint a weaker source — it mints
NONE, and ``scanf("%s", buf); system(buf);`` in C produces nothing at all. This
is INV-nular's rule ("kind asserted by name, never checked against semantics")
in the RECALL direction, where the ADR-0049 campaign has been sweeping the
precision direction. The two have to be counted separately or the campaign
reports progress while the recall hole grows.

WHAT WAS WRONG, swept over all fifteen catalogues:

    go       fmt.{Scan,Scanln,Scanf}                    ABSENT ENTIRELY
    python   builtins.input                             ABSENT (and
                                                        ambiguous_names asserted
                                                        "not IO boundary")
    c        stdio.{getchar,scanf}                      declared fs_read
    haskell  Prelude.{getContents,readLn,interact}      declared fs_read
             Prelude.{getLine,getChar}                  ABSENT ENTIRELY
             Data.Text.IO.{getContents,getLine}         declared fs_read
    scala    scala.io.StdIn.{readLine,readInt,...}      declared fs_read, under a
                                                        note reading "Scala
                                                        stdlib stdin reading"

THE POSITIVE CONTROLS ARE WHY THIS IS DRIFT AND NOT AN UNMADE DECISION.
``cpp std.cin``, ``c stdio.stdin`` (the handle) and rust's whole
``std::io::Stdin`` surface were already ``ipc_recv``. Same family, same shape,
three languages getting it right and five getting it wrong.

THE SCOPE LINE IS THE ARGUMENT, and ``TestArgumentConditionedReadsDidNotMove``
is the load-bearing half of this file. A read that takes a STREAM
(``fgets(buf, n, fp)``, ``fscanf``, ``System.IO.hGetLine``,
``java.util.Scanner.nextLine``, ``bufio.Reader.ReadString``) crosses whatever
that stream is attached to, which INV-zumin classes as "undecidable at the call
site" and INV-bagok holds open. Those must NOT move here: INV-zumin recorded
that multiplying such rows is "actively wrong ... the expensive direction", and
the remedy is inference (WI-lipis), not a boundary swap. Only reads that take no
stream at all are in scope.
"""

from __future__ import annotations

import pytest

from hypergumbo_core.io_boundary import load_catalog
from hypergumbo_core.taint import AUTO_SOURCE_LABEL_MAP

#: (language, module, callee, kind) that can read ONLY standard input.
UNCONDITIONAL_STDIN: list[tuple[str, str, str, str]] = [
    ("go", "fmt", "Scan", "function"),
    ("go", "fmt", "Scanln", "function"),
    ("go", "fmt", "Scanf", "function"),
    ("python", "builtins", "input", "function"),
    ("c", "stdio", "getchar", "function"),
    ("c", "stdio", "scanf", "function"),
    ("haskell", "Prelude", "getLine", "function"),
    ("haskell", "Prelude", "getChar", "function"),
    ("haskell", "Prelude", "getContents", "function"),
    ("haskell", "Prelude", "readLn", "function"),
    ("haskell", "Prelude", "interact", "function"),
    ("haskell", "Data.Text.IO", "getContents", "function"),
    ("haskell", "Data.Text.IO", "getLine", "function"),
    ("scala", "scala.io.StdIn", "readLine", "function"),
    ("scala", "scala.io.StdIn", "readInt", "function"),
    ("scala", "scala.io.StdIn", "readBoolean", "function"),
]

#: Already correct before this change. Pinned so the family stays one family.
POSITIVE_CONTROLS: list[tuple[str, str, str]] = [
    ("cpp", "std", "cin"),
    ("c", "stdio", "stdin"),
    ("rust", "std::io::Stdin", "read_line"),
    ("rust", "std::io", "stdin"),
]

#: Reads that take a STREAM. Their boundary is a property of the argument
#: (INV-zumin class (b) / INV-bagok), so they must keep the boundary they have.
ARGUMENT_CONDITIONED: list[tuple[str, str, str, str]] = [
    ("c", "stdio", "fgets", "fs_read"),
    ("c", "stdio", "fscanf", "fs_read"),
    ("c", "stdio", "fgetc", "fs_read"),
    ("c", "stdio", "getc", "fs_read"),
    ("c", "stdio", "fread", "fs_read"),
    ("haskell", "System.IO", "hGetLine", "fs_read"),
    ("haskell", "System.IO", "hGetContents", "fs_read"),
    ("java", "java.util.Scanner", "nextLine", "fs_read"),
    ("java", "java.io.BufferedReader", "readLine", "fs_read"),
]


def _lookup(lang: str, module: str, name: str, kind: str | None = None):
    catalog = load_catalog(lang, include_defaults=True)
    return catalog.lookup_with_module(
        name, module, call_construct=("method" if kind == "method" else None),
    )


class TestUnconditionalStdinReadsMintUntrustedInput:
    """The whole point: these rows must reach ``untrusted_input``."""

    @pytest.mark.parametrize(
        ("lang", "module", "name", "kind"),
        UNCONDITIONAL_STDIN,
        ids=[f"{l}:{m}.{n}" for l, m, n, _ in UNCONDITIONAL_STDIN],
    )
    def test_the_row_exists_and_reaches_untrusted_input(
        self, lang: str, module: str, name: str, kind: str,
    ) -> None:
        prim = _lookup(lang, module, name, kind)
        assert prim is not None, f"{lang} {module}.{name} is not catalogued at all"
        assert AUTO_SOURCE_LABEL_MAP.get(prim.boundary) == "untrusted_input", (
            f"{lang} {module}.{name} is {prim.boundary!r}, which maps to "
            f"{AUTO_SOURCE_LABEL_MAP.get(prim.boundary)!r} — a stdin read that "
            f"mints no untrusted input"
        )

    @pytest.mark.parametrize(
        ("lang", "module", "name"),
        POSITIVE_CONTROLS,
        ids=[f"{l}:{m}.{n}" for l, m, n in POSITIVE_CONTROLS],
    )
    def test_the_languages_that_were_already_right_stay_right(
        self, lang: str, module: str, name: str,
    ) -> None:
        """These passed before the change. They are the reason it is drift."""
        prim = _lookup(lang, module, name)
        assert prim is not None, f"{lang} {module}.{name} vanished"
        assert AUTO_SOURCE_LABEL_MAP.get(prim.boundary) == "untrusted_input", (
            f"{lang} {module}.{name} regressed to {prim.boundary!r}"
        )


class TestArgumentConditionedReadsDidNotMove:
    """THE SCOPE GUARD. A read that takes a stream is NOT in this item.

    Without this, the cheapest way to make the class above green is to sweep
    every read-shaped name into ``ipc_recv``, which would assert an IPC crossing
    for every ``fgets`` over a file — the false-positive direction INV-zumin
    named "actively wrong" and the one INV-bagok exists to hold open.
    """

    @pytest.mark.parametrize(
        ("lang", "module", "name", "boundary"),
        ARGUMENT_CONDITIONED,
        ids=[f"{l}:{m}.{n}" for l, m, n, _ in ARGUMENT_CONDITIONED],
    )
    def test_stream_taking_reads_keep_their_boundary(
        self, lang: str, module: str, name: str, boundary: str,
    ) -> None:
        prim = _lookup(lang, module, name, "method" if "." in module else None)
        assert prim is not None, f"{lang} {module}.{name} disappeared"
        assert prim.boundary == boundary, (
            f"{lang} {module}.{name} moved to {prim.boundary!r}; its boundary is a "
            f"property of its stream argument (INV-bagok), not of its name"
        )


class TestTheShortNameFallbackCannotCaptureAScanner:
    """``fmt.Scan`` and ``bufio.Scanner.Scan`` share a short name.

    Go resolves a receiver it cannot type to no module at all, and the catalogue
    then falls back to the short name. Adding ``fmt.Scan`` without declaring
    ``Scan`` ambiguous would therefore tag every untyped ``sc.Scan()`` on a
    ``bufio.Scanner`` as an ``ipc_recv`` stdin read — manufacturing exactly the
    false sources this file's own item is the mirror of.
    """

    def test_scan_is_declared_ambiguous(self) -> None:
        assert "Scan" in load_catalog("go", include_defaults=True).ambiguous_names

    def test_a_bare_scan_matches_nothing(self) -> None:
        catalog = load_catalog("go", include_defaults=True)
        assert catalog.lookup_with_module("Scan", None) is None

    def test_but_a_qualified_fmt_scan_still_matches(self) -> None:
        """The negative control has to leave the positive case working."""
        prim = load_catalog("go", include_defaults=True).lookup_with_module(
            "Scan", "fmt",
        )
        assert prim is not None and prim.module == "fmt"
