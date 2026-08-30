# SPDX-License-Identifier: AGPL-3.0-or-later
"""INV-muvis, END TO END: the stdin rows that a real program can actually reach.

WHY THIS FILE EXISTS SEPARATELY FROM THE CATALOGUE TEST. ``hypergumbo-core``'s
``test_unconditional_stdin_reads.py`` asks the catalogue a question — "is this
row declared, and does its boundary mint untrusted input". That is a NECESSARY
condition and not a sufficient one: a row can be present, correct, and never
matched by anything a program emits. Measurement 0009 exists because that gap
is real, and WI-vutav was blocked by exactly it in Go.

So the retag was measured on a run, per language, and IT DOES NOT REACH ALL
FIVE. Fixtures in ``~/hypergumbo_lab_notebook/vutav_reads_08302026/reach/``:

    c        scanf / getchar        FIRES  -> ipc_recv   (pinned below)
    go       fmt.Scan               FIRES  -> ipc_recv   (pinned below)
    scala    scala.io.StdIn.readLine  correct, NOT REACHED — the analyzer emits
             ``scala:external:0-0:readLine`` and ``readLine`` is in scala's
             ``ambiguous_names``, so the no-module gate withholds it. Unchanged
             by the retag: it was equally unreachable as ``fs_read``.
    haskell  Prelude.getLine        correct, NO CALL EDGE AT ALL — the analyzer
             emits nothing for ``name <- getLine`` in a do-block bind.
    python   builtins.input         correct, NO CALL EDGE — INV-foluz, confirmed
             on a run rather than inferred.

Three of the five rows are therefore CORRECTNESS-ONLY today. That is worth
shipping — a row that is right and unreachable becomes useful the day its
analyzer is fixed, and a row that is wrong stays wrong — but it must not be
reported as a five-language recall fix, and this docstring is where the
distinction is kept so the next reader does not have to re-derive it.

Only the two live arms are pinned. Pinning the three dead ones would be pinning
a bug in place.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from hypergumbo_core.io_boundary import classify_call, load_catalog


@pytest.fixture()
def c_available():
    from hypergumbo_core.analyze.base import is_grammar_available

    if not is_grammar_available("tree_sitter_c"):
        pytest.skip("C tree-sitter grammar not installed")


@pytest.fixture()
def go_available():
    from hypergumbo_core.analyze.base import is_grammar_available

    if not is_grammar_available("tree_sitter_go"):
        pytest.skip("Go tree-sitter grammar not installed")


def _boundaries(result, lang: str, names: set[str]) -> dict[str, str | None]:
    """``{short callee name: boundary}`` for the call edges we care about."""
    catalogs = {lang: load_catalog(lang, include_defaults=True)}
    out: dict[str, str | None] = {}
    for edge in result.edges:
        if edge.edge_type not in ("calls", "instantiates"):
            continue
        slots = edge.dst.split(":")
        short = (slots[-2] if len(slots) > 3 else slots[-1]).split(".")[-1]
        if short in names:
            prim = classify_call(catalogs, edge.dst, edge.meta, dst_ref=edge.dst_ref)
            out[short] = prim.boundary if prim is not None else None
    return out


class TestCStdinReadsMintUntrustedInput:
    """``scanf`` and ``getchar`` take no stream, and C is the language where the
    unsafe idiom is commonest: read into a buffer, hand it to ``system``."""

    SOURCE = '''\
#include <stdio.h>
#include <stdlib.h>

int main(void) {
    char buf[128];
    scanf("%127s", buf);
    int c = getchar();
    system(buf);
    return c;
}
'''

    def test_scanf_and_getchar_are_ipc_recv(
        self, tmp_path: Path, c_available: None,
    ) -> None:
        from hypergumbo_lang_mainstream.c import analyze_c

        (tmp_path / "main.c").write_text(self.SOURCE)
        result = analyze_c(tmp_path)
        assert not result.skipped
        got = _boundaries(result, "c", {"scanf", "getchar"})
        assert got.get("scanf") == "ipc_recv", f"scanf classified {got.get('scanf')!r}"
        assert got.get("getchar") == "ipc_recv", (
            f"getchar classified {got.get('getchar')!r}"
        )

    def test_the_stream_taking_sibling_is_still_a_file_read(
        self, tmp_path: Path, c_available: None,
    ) -> None:
        """SCOPE CONTROL. ``fgets`` takes a ``FILE *`` and must not have moved."""
        from hypergumbo_lang_mainstream.c import analyze_c

        (tmp_path / "main.c").write_text('''\
#include <stdio.h>

int main(void) {
    char buf[128];
    FILE *fp = fopen("/etc/hosts", "r");
    fgets(buf, 128, fp);
    return 0;
}
''')
        result = analyze_c(tmp_path)
        assert not result.skipped
        got = _boundaries(result, "c", {"fgets"})
        assert got.get("fgets") == "fs_read", f"fgets classified {got.get('fgets')!r}"


class TestGoFmtScanMintsUntrustedInput:
    """``fmt.Scan`` reads standard input; ``bufio.Scanner.Scan`` does not.

    The second test is the one that matters. Go leaves an untyped receiver with
    no module hint at all, so without ``Scan`` in ``ambiguous_names`` every
    ``sc.Scan()`` in every Go program would have picked up an ``ipc_recv`` stdin
    read through the short-name fallback — manufacturing false taint sources,
    which is the mirror of the recall defect this row was added to fix.
    """

    def test_fmt_scan_is_ipc_recv(self, tmp_path: Path, go_available: None) -> None:
        from hypergumbo_lang_mainstream.go import analyze_go

        (tmp_path / "go.mod").write_text("module probe\n\ngo 1.21\n")
        (tmp_path / "main.go").write_text('''\
package main

import (
\t"fmt"
\t"os"
)

func main() {
\tvar name string
\tfmt.Scan(&name)
\tos.WriteFile(name, []byte("x"), 0644)
}
''')
        result = analyze_go(tmp_path)
        assert not result.skipped
        got = _boundaries(result, "go", {"Scan"})
        assert got.get("Scan") == "ipc_recv", f"fmt.Scan classified {got.get('Scan')!r}"

    def test_an_untyped_scanner_scan_is_not_captured(
        self, tmp_path: Path, go_available: None,
    ) -> None:
        from hypergumbo_lang_mainstream.go import analyze_go

        (tmp_path / "go.mod").write_text("module probe\n\ngo 1.21\n")
        (tmp_path / "main.go").write_text('''\
package main

import (
\t"bufio"
\t"os"
)

func consume(sc *bufio.Scanner) {
\tfor sc.Scan() {
\t\tos.WriteFile(sc.Text(), []byte("x"), 0644)
\t}
}
''')
        result = analyze_go(tmp_path)
        assert not result.skipped
        got = _boundaries(result, "go", {"Scan"})
        assert got.get("Scan") is None, (
            f"bufio.Scanner.Scan was captured as {got.get('Scan')!r} — the short-name "
            f"fallback reached fmt.Scan"
        )
