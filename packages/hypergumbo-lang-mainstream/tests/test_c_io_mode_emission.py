# SPDX-License-Identifier: AGPL-3.0-or-later
"""INV-kaduh: a mode-decided primitive must get its mode from the analyzer.

``io_primitives/c.yaml`` declares ``stdio.fopen`` under BOTH ``fs_read`` and
``fs_write`` — the dual classification ``io_mode`` exists to settle, exactly as
python declares ``builtins.open``. Handed ``io_mode="w"`` the catalogue returns
``fs_write``; with no ``meta["io_mode"]`` stamp from the analyzer,
``select_by_mode`` falls through to ``candidates[0]`` and every
``fopen(path, "w")`` in a C or C++ program tags ``fs_read``.

WHY A MISSING STAMP IS A FALSE CONFIRM AND NOT A RECALL MISS. The call IS
classified — as ``fs_read`` — and a classified call is what ``examined`` means
(INV-buzab). So a ``{boundary: fs_write, must_not_exist: true}`` claim over a C
program that writes files with ``fopen`` would get an EXAMINED NEGATIVE for the
boundary that is actually true, rather than the ``inconclusive`` an
unanalysable language earns.

THE SECOND CONSUMER FAILS THE OTHER WAY. ``requires_mode_boundary`` gates the derived
``host_fs`` sink on the same evidence, and ``resolve_mode_boundary(None)`` is
``fs_read`` — so with no stamp the C write sink matches nothing at all.
One absent stamp, both directions.

C++ INHERITS THE C CATALOGUE (``_CATALOG_PARENTS['cpp'] = 'c'``), which is why
both analyzers are pinned here. Each is tested rather than assumed by analogy,
because the two do NOT behave alike downstream: C reaches the catalogue with no
module slot, cpp through its ``#include`` module slot
(``TestCppReachesTheCatalogueToo``).
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

    Deliberately not keyed on ``io_primitive``: keying the helper on the tag
    would make it find edges only when tagging already works — so a tagging
    regression would surface here as "no fopen call was emitted", blaming the
    analyzer for a consumer fault. Finding the edge by CALLEE keeps the two
    failures distinguishable.
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
    """The CONSUMER half, C only — cpp takes a different arm and has its own class.

    C reaches the catalogue through the no-module-context arm of
    ``lookup_with_module`` (its dst carries no module slot). That arm must call
    ``select_by_mode`` as the other arms do, or the analyzer stamp moves
    nothing — a predicate is inert until every call site passes it — so these
    assertions prove the whole chain runs rather than just the producer.
    """

    def test_write_mode_tags_fs_write(self, tmp_path: Path) -> None:
        """A write-mode ``fopen`` tags ``fs_write``, not the ``fs_read`` default."""
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


class TestCppReachesTheCatalogueToo:
    """cpp's mode reaches the catalogue too (history: INV-funuf).

    cpp spells the module slot as the comma-joined ``#include`` list with the
    header FILENAME kept (``stdio.h``), which is why this is a genuinely
    different arm from C's: C reaches the catalogue with NO module slot, cpp
    reaches it through a slot the consumer must split and stem.
    """

    def test_write_mode_tags_fs_write(self, tmp_path: Path) -> None:
        edge = _fopen_edge(tmp_path, "cpp", WRITER)
        assert (edge.meta or {}).get("io_mode") == "w"
        assert (edge.meta or {}).get("io_boundary") == "fs_write", (
            "cpp's comma-joined, filename-spelled module slot must now reach "
            "the catalogue stem"
        )

    def test_read_mode_still_tags_fs_read(self, tmp_path: Path) -> None:
        edge = _fopen_edge(tmp_path, "cpp", READER)
        assert (edge.meta or {}).get("io_boundary") == "fs_read"

    def test_the_slot_still_carries_the_header_filename(
        self, tmp_path: Path,
    ) -> None:
        """The PRODUCER keeps the filename; the stemming is consumer-side.

        Worth pinning: if cpp.py emitted the stem instead, the consumer
        expansion would become dead code with no test objecting, and the
        next analyzer to join a slot would find the mechanism gone.
        """
        edge = _fopen_edge(tmp_path, "cpp", WRITER)
        assert "stdio.h" in edge.dst


@pytest.mark.parametrize("language", ["c", "cpp"])
def test_the_write_sink_is_reachable_at_all(language: str) -> None:
    """Non-vacuity floor for the taint half.

    The sink derivation gates ``stdio.fopen`` on ``requires_mode_boundary="fs_write"``.
    That gate is only a discriminator if something can satisfy it; with no
    producer it is blanket suppression, and the two are indistinguishable on
    any corpus whose ``fopen`` calls happen to be reads.
    """
    from hypergumbo_core.taint import load_builtin_taint_catalog

    sinks = load_builtin_taint_catalog().sinks_for_language(language)
    rows = [s for s in sinks if s.qualified_name == "stdio.fopen"]
    assert rows, f"{language} derives no stdio.fopen sink at all"
    assert any(s.requires_mode_boundary == "fs_write" for s in rows)
