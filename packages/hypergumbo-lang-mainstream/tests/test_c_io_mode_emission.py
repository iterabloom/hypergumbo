# SPDX-License-Identifier: AGPL-3.0-or-later
"""INV-kaduh: a mode-decided primitive must get its mode from the analyzer.

``io_primitives/c.yaml`` declares ``stdio.fopen`` under BOTH ``fs_read`` and
``fs_write`` — the dual classification ``io_mode`` exists to settle, exactly as
python declares ``builtins.open``. The catalogue side was already correct:
handed ``io_mode="w"`` it returns ``fs_write``. Nothing ever handed it one.
``meta["io_mode"]`` was stamped at exactly ONE site in the whole tree
(``py.py``), so ``select_by_mode`` fell through to ``candidates[0]`` and
**every** ``fopen(path, "w")`` in a C or C++ program tagged ``fs_read``.

WHY THAT IS A FALSE CONFIRM AND NOT A RECALL MISS. The call IS classified — as
``fs_read`` — and since INV-buzab a classified call is what ``examined`` means.
So a ``{boundary: fs_write, must_not_exist: true}`` claim over a C program that
writes files with ``fopen`` gets an EXAMINED NEGATIVE for the boundary that is
actually true, rather than the ``inconclusive`` an unanalysable language earns.

THE SECOND CONSUMER FAILS THE OTHER WAY. ``requires_mode`` gates the derived
``host_fs`` sink on the same evidence, and ``resolve_mode_boundary(None)`` is
``fs_read`` — so with no producer the C write sink matched nothing at all,
in every repo, unconditionally. One absent stamp, both directions.

C++ INHERITS THE C CATALOGUE (``_CATALOG_PARENTS['cpp'] = 'c'``) and therefore
inherits the defect, which is why both analyzers are pinned here. Reasoning by
analogy across the two is not permitted in this repo, so each is measured — and
measuring rather than assuming is what found that the two do NOT behave alike
downstream. C's mode now decides its boundary; cpp's stamp lands on the edge and
reaches no catalogue at all, for an unrelated reason filed as INV-funuf. The
last test in this file is the executable disclosure of that gap.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from hypergumbo_core.io_boundary import load_catalog, tag_io_boundaries
from hypergumbo_lang_mainstream.c import analyze_c
from hypergumbo_lang_mainstream.cpp import analyze_cpp

WRITER = """#include <stdio.h>

void dump(const char *path, const char *secret) {
    FILE *f = fopen(path, "w");
    fputs(secret, f);
    fclose(f);
}
"""

READER = """#include <stdio.h>

void slurp(const char *path) {
    FILE *g = fopen(path, "r");
    fgetc(g);
    fclose(g);
}
"""

COMPUTED = """#include <stdio.h>

void maybe(const char *path, const char *m) {
    FILE *f = fopen(path, m);
    fclose(f);
}
"""

TOO_FEW_ARGS = """#include <stdio.h>

void broken(const char *path) {
    FILE *f = fopen(path);
}
"""

_ANALYZERS = {
    "c": (analyze_c, "leak.c"),
    "cpp": (analyze_cpp, "leak.cpp"),
}


def _tagged_edges(tmp_path: Path, language: str, source: str) -> list:
    analyze, filename = _ANALYZERS[language]
    (tmp_path / filename).write_text(source)
    result = analyze(tmp_path)
    tag_io_boundaries(result.edges, {language: load_catalog(language)})
    return result.edges


def _fopen_edge(tmp_path: Path, language: str, source: str):
    """The single ``fopen`` call edge, found by CALLEE rather than by tag.

    Deliberately not keyed on ``io_primitive``: cpp does not reach the
    catalogue at all (INV-funuf, below), and a helper that filtered on the tag
    would silently find nothing there and report it as "no fopen".
    """
    edges = [e for e in _tagged_edges(tmp_path, language, source)
             if e.edge_type == "calls" and ":fopen:" in e.dst]
    assert len(edges) == 1, [e.dst for e in edges]
    return edges[0]


@pytest.mark.parametrize("language", ["c", "cpp"])
class TestTheAnalyzerStampsTheMode:
    """The PRODUCER half — what INV-kaduh is actually about.

    Asserted on the edge rather than on the boundary tag because the two
    languages differ downstream, and pinning the producer here is what keeps
    that difference visible instead of letting cpp look "not implemented".
    """

    def test_write_mode_is_recorded(
        self, tmp_path: Path, language: str,
    ) -> None:
        edge = _fopen_edge(tmp_path, language, WRITER)
        assert (edge.meta or {}).get("io_mode") == "w"

    def test_read_mode_is_recorded(
        self, tmp_path: Path, language: str,
    ) -> None:
        """The control that separates a fix from a blanket flip.

        Without it, stamping ``"w"`` unconditionally would pass the test
        above and manufacture an fs_write for every read in the corpus.
        """
        edge = _fopen_edge(tmp_path, language, READER)
        assert (edge.meta or {}).get("io_mode") == "r"

    def test_a_computed_mode_records_absence_rather_than_guessing(
        self, tmp_path: Path, language: str,
    ) -> None:
        """``fopen(path, m)`` is ignorance, and ignorance licenses nothing.

        ``resolve_mode_boundary`` applies the language default; inventing
        ``"w"`` on suspicion would rebuild the false-positive population this
        machinery exists to remove. Same rule py.py already follows.
        """
        edge = _fopen_edge(tmp_path, language, COMPUTED)
        assert (edge.meta or {}).get("io_mode") is None

    def test_a_call_with_too_few_arguments_stamps_nothing(
        self, tmp_path: Path, language: str,
    ) -> None:
        """``fopen(path)`` — arity the declared position does not reach.

        Malformed C, and the grammar parses it anyway, which is the point: an
        analyzer reads whatever is on disk, including code that does not
        compile. Indexing past the end here would raise mid-analysis and take
        the whole run down for one bad file.
        """
        edge = _fopen_edge(tmp_path, language, TOO_FEW_ARGS)
        assert (edge.meta or {}).get("io_mode") is None

    def test_a_neighbouring_call_is_not_stamped(
        self, tmp_path: Path, language: str,
    ) -> None:
        """The stamp is scoped to the call that carries the mode.

        ``fputs``/``fclose`` sit in the same function and are singly
        classified; a stamp that leaked onto them would be inert today and
        load-bearing the moment either became dual-classified.
        """
        stamped = {
            e.dst.split(":")[-2]
            for e in _tagged_edges(tmp_path, language, WRITER)
            if (e.meta or {}).get("io_mode") is not None
        }
        assert stamped == {"fopen"}


class TestTheBoundaryFollowsTheMode:
    """The CONSUMER half, C only — and why cpp is absent from this class.

    C reaches the catalogue through the no-module-context arm (its dst carries
    no module slot), which is exactly the arm that used to skip the mode: both
    other arms in ``lookup_with_module`` called ``select_by_mode`` and this one
    handed the gate its candidates untouched. So the analyzer stamp alone moved
    NOTHING until the arm was narrowed — a predicate is inert until every call
    site passes it, and this pair of assertions is what proves the whole chain
    runs rather than just the producer.
    """

    def test_write_mode_tags_fs_write(self, tmp_path: Path) -> None:
        """The filed defect. This tagged ``fs_read`` before the fix."""
        edge = _fopen_edge(tmp_path, "c", WRITER)
        assert (edge.meta or {}).get("io_boundary") == "fs_write"

    def test_read_mode_still_tags_fs_read(self, tmp_path: Path) -> None:
        edge = _fopen_edge(tmp_path, "c", READER)
        assert (edge.meta or {}).get("io_boundary") == "fs_read"

    def test_a_computed_mode_falls_back_to_the_language_default(
        self, tmp_path: Path,
    ) -> None:
        edge = _fopen_edge(tmp_path, "c", COMPUTED)
        assert (edge.meta or {}).get("io_boundary") == "fs_read"


def test_cpp_stamps_a_mode_that_currently_reaches_no_catalogue(
    tmp_path: Path,
) -> None:
    """DISCLOSURE, not an assertion of correctness — INV-funuf.

    The cpp stamp above is real and correct and presently INERT, and saying so
    in an executable place beats saying it in a commit message. cpp.py sets the
    dst's module slot to the comma-joined list of the file's ``#include``s and
    keeps the header FILENAME (``stdio.h``), while ``c.yaml`` declares the
    header STEM (``stdio``), so ``lookup_with_module``'s exact module match
    refuses every C-stdlib call. Measured on whisper.cpp: 6 of 86 reachable
    primitives match today, and the 80 missed include ``getenv``,
    ``socket``/``connect``/``send``, and ``fork``/``execvp``.

    WHEN INV-funuf LANDS THIS TEST GOES RED. That is the intent: it is a
    tripwire holding the disclosure to the truth, so the fix cannot quietly
    leave a stale "cpp is blind" claim in the tree. Replace it then with the
    boundary assertions in ``TestTheBoundaryFollowsTheMode``.
    """
    edge = _fopen_edge(tmp_path, "cpp", WRITER)
    assert (edge.meta or {}).get("io_mode") == "w"
    assert (edge.meta or {}).get("io_boundary") is None
    assert "stdio.h" in edge.dst


@pytest.mark.parametrize("language", ["c", "cpp"])
def test_the_write_sink_is_reachable_at_all(language: str) -> None:
    """Non-vacuity floor for the taint half.

    The sink derivation gates ``stdio.fopen`` on ``requires_mode="fs_write"``.
    That gate is only a discriminator if something can satisfy it; with no
    producer it is blanket suppression, and the two are indistinguishable on
    any corpus whose ``fopen`` calls happen to be reads.
    """
    from hypergumbo_core.taint import load_builtin_taint_catalog

    sinks = load_builtin_taint_catalog().sinks_for_language(language)
    rows = [s for s in sinks if s.qualified_name == "stdio.fopen"]
    assert rows, f"{language} derives no stdio.fopen sink at all"
    assert any(s.requires_mode == "fs_write" for s in rows)
