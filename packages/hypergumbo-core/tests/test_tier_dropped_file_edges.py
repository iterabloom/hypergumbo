# SPDX-License-Identifier: AGPL-3.0-or-later
"""A boundary anchor must name the file it stands for, not a shared placeholder.

THE HAZARD. A src of ``python:<external>:0-0:file:external_symbol`` has no path
and span ``0-0``: a reader has no file to open, so an edge anchored there can be
neither confirmed nor refuted, and a taint flow seeded from one names no place.
Worse than pathless, it is shared: ``make_file_id`` puts the literal ``"file"``
in the name slot, so EVERY file anchored that way in a language lands on that
ONE id, a many-to-one bucket. ``ir._dedupe_key`` therefore keeps the path in a
boundary's identity for ``kind="file"`` too. (history: INV-rozob)

WHAT THESE FIXTURES EXERCISE. Each generated-looking file is three lines of
Python::

    \"\"\"@generated\"\"\"

    from datetime import date

Generated-file detection (``supply_chain._has_generated_marker``) is
comment-anchored, so a docstring marker does NOT make the file tier 4: these
files are first-party and keep their own ``kind="file"`` Symbol. And a file the
tier filter does drop has its edges dropped with it (INV-saruf; see
``TestDanglingSourceClosed`` in hypergumbo-lang-mainstream's
``test_phase_de_reorder.py``), so no anchor is needed for it. These tests therefore pin the end-to-end property — no placeholder
anchor, one anchor per file, every src a node — not a tier-dropped path; the
per-file identity rule itself lives in ``ir._dedupe_key``.
"""

import json
from pathlib import Path

import pytest

from hypergumbo_core.cli import run_behavior_map

COLLAPSED_ID = "python:<external>:0-0:file:external_symbol"


@pytest.fixture
def generated_and_plain_repo(tmp_path: Path) -> Path:
    """One file with a ``@generated`` docstring and one ordinary one, both
    importing.

    The plain file is the positive control: an ordinary tier-1 file whose
    edges must anchor on its real ``kind="file"`` Symbol.
    """
    repo = tmp_path / "repo"
    (repo / "src").mkdir(parents=True)
    (repo / "src" / "gen.py").write_text(
        '"""@generated"""\n\nfrom datetime import date\n\n\n'
        "def stamp():\n    return date.today()\n"
    )
    (repo / "src" / "plain.py").write_text(
        "import os\n\n\ndef where():\n    return os.getcwd()\n"
    )
    return repo


@pytest.fixture
def two_generated_repo(tmp_path: Path) -> Path:
    """Two ``@generated``-docstring files, so the MANY-TO-ONE half is testable.

    Pathlessness and sharing are different failures: naming each anchor after its
    file fixes both, but a fix that gave every file one shared *named* id would
    pass the pathless assertions and still lose attribution.
    """
    repo = tmp_path / "two"
    (repo / "src").mkdir(parents=True)
    (repo / "src" / "gen_a.py").write_text('"""@generated"""\n\nfrom datetime import date\n')
    (repo / "src" / "gen_b.py").write_text('"""@generated"""\n\nimport os\n')
    return repo


def _survey(repo: Path, out: Path, **kw) -> dict:
    run_behavior_map(repo, out, include_sketch_precomputed=False, **kw)
    return json.loads(out.read_text())


class TestTheCollapse:

    def test_no_edge_is_anchored_on_the_shared_placeholder(
        self, generated_and_plain_repo: Path, tmp_path: Path
    ) -> None:
        """No edge may anchor on a placeholder with no path."""
        data = _survey(generated_and_plain_repo, tmp_path / "out.json")
        anchored = [e for e in data["edges"] if e["src"] == COLLAPSED_ID]
        assert anchored == [], (
            f"{len(anchored)} edge(s) anchored on a placeholder with no path"
        )

    def test_the_placeholder_node_is_not_minted_at_all(
        self, generated_and_plain_repo: Path, tmp_path: Path
    ) -> None:
        """No consumer should have to learn to ignore this id."""
        data = _survey(generated_and_plain_repo, tmp_path / "out.json")
        assert COLLAPSED_ID not in {n["id"] for n in data["nodes"]}

    def test_the_anchor_names_the_generated_file(
        self, generated_and_plain_repo: Path, tmp_path: Path
    ) -> None:
        """The positive half: the file's import edge is present and its src
        is a location a reader can open."""
        data = _survey(generated_and_plain_repo, tmp_path / "out.json")
        gen_edges = [
            e for e in data["edges"]
            if "gen.py" in e["src"] and e["type"] == "imports"
        ]
        assert gen_edges, "the generated file's import edge was dropped"
        assert all("gen.py" in e["src"] for e in gen_edges)

    def test_two_generated_files_do_not_share_one_anchor(
        self, two_generated_repo: Path, tmp_path: Path
    ) -> None:
        """THE MANY-TO-ONE HALF, which the pathlessness assertions alone do not
        cover. If both files' import edges anchored on the SAME id, a reader
        could not tell which file imported what. This is the assertion that
        fails if someone reintroduces any per-language collapse key."""
        data = _survey(two_generated_repo, tmp_path / "out.json")
        srcs = {e["src"] for e in data["edges"] if e["type"] == "imports"}
        gen_srcs = {s for s in srcs if "gen" in s}
        assert len(gen_srcs) == 2, f"expected one anchor per file, got {gen_srcs}"

    def test_every_surviving_edge_src_resolves_to_a_node(
        self, generated_and_plain_repo: Path, tmp_path: Path
    ) -> None:
        """The general invariant: a src must be in the node set.

        THIS TEST DOES NOT PIN THE PLACEHOLDER DEFECT. Boundary synthesis
        MINTS a node for the placeholder, so a graph anchored on it is
        internally consistent while pointing at a symbol with no path, and
        this passes. It guards against a src left truly dangling (WI-pozur's
        G2 property); do not read a green tick here as coverage of the
        placeholder defect. The assertions above are the pins."""
        data = _survey(generated_and_plain_repo, tmp_path / "out.json")
        node_ids = {n["id"] for n in data["nodes"]}
        dangling = sorted({e["src"] for e in data["edges"]} - node_ids)
        assert dangling == [], f"edge srcs with no node: {dangling}"


class TestItDoesNotOverReach:
    """Positive controls. An ordinary file must be untouched."""

    def test_the_ordinary_file_keeps_its_real_file_symbol(
        self, generated_and_plain_repo: Path, tmp_path: Path
    ) -> None:
        """``plain.py`` is tier 1: it keeps a genuine ``kind="file"`` Symbol and
        is NOT turned into a boundary node."""
        data = _survey(generated_and_plain_repo, tmp_path / "out.json")
        plain = [
            n for n in data["nodes"]
            if n.get("path") == "src/plain.py" and n.get("kind") == "file"
        ]
        assert plain, "the ordinary file lost its file Symbol"
        srcs = [e["src"] for e in data["edges"] if e["type"] == "imports"]
        assert any("plain.py" in s and s.endswith(":file:file") for s in srcs)

    def test_max_tier_4_keeps_the_generated_file_as_a_real_symbol(
        self, generated_and_plain_repo: Path, tmp_path: Path
    ) -> None:
        """With tier 4 included, the file's real Symbol is present and its
        edge anchors on THAT, not on a boundary node."""
        data = _survey(generated_and_plain_repo, tmp_path / "out4.json", max_tier=4)
        node_ids = {n["id"] for n in data["nodes"]}
        assert COLLAPSED_ID not in node_ids
        real = [n for n in node_ids if "gen.py" in n and n.endswith(":file:file")]
        assert real, "the generated file's Symbol should be present at tier 4"
        assert any(e["src"] in real for e in data["edges"])
