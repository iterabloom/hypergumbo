# SPDX-License-Identifier: AGPL-3.0-or-later
"""The order Additional Files takes its "explains the project" picks in.

``overview_rank`` replaced an embedding ranking (WI-patof). These tests pin
the order's four keys one at a time, that it is total and repeatable, and
that it needs no model.
"""

from __future__ import annotations

from collections import Counter
from pathlib import Path

import pytest

from hypergumbo_core import overview_rank, sketch_embeddings
from hypergumbo_core.overview_rank import (
    HEAD_CHARS,
    is_process_file,
    overview_wording_scores,
    rank_overview_files,
)

OVERVIEW = (
    "This project is a small tool. It is for developers who want predictable "
    "behavior. How it works: you point it at some input and it produces output."
)
DATA = "alpha: 1\nbeta: 2\ngamma: [3, 4]\n"


def _write(root: Path, rel: str, text: str) -> Path:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _order(root: Path, *files: Path) -> list[str]:
    return [p.relative_to(root).as_posix() for p in rank_overview_files(list(files), root)]


# ── the four keys, each with the later keys pulling the other way ───────────

def test_documentation_comes_before_configuration(tmp_path: Path) -> None:
    """Role outranks depth and wording: the config file is shallower and is
    written like an overview, and still comes second."""
    doc = _write(tmp_path, "a/b/notes.md", DATA)
    config = _write(tmp_path, "settings.yaml", OVERVIEW)
    assert _order(tmp_path, config, doc) == ["a/b/notes.md", "settings.yaml"]


def test_a_shallower_file_comes_before_a_deeper_one(tmp_path: Path) -> None:
    """Depth outranks wording and path."""
    deep = _write(tmp_path, "a/overview.md", OVERVIEW)
    shallow = _write(tmp_path, "zzz.md", DATA)
    assert _order(tmp_path, deep, shallow) == ["zzz.md", "a/overview.md"]


def test_a_process_file_comes_after_every_other_document(tmp_path: Path) -> None:
    """Process-last outranks depth and wording: the contributing guide is at
    the root and is written like an overview, the design note is two
    directories down and is not, and the design note still comes first."""
    design = _write(tmp_path, "docs/internals/design.md", DATA)
    contributing = _write(tmp_path, "CONTRIBUTING.md", OVERVIEW)
    licence = _write(tmp_path, "LICENSE-APACHE", OVERVIEW)
    assert _order(tmp_path, contributing, licence, design)[0] == "docs/internals/design.md"


def test_role_still_outranks_the_process_rule(tmp_path: Path) -> None:
    """A process document is still documentation: it comes before a
    configuration file."""
    conduct = _write(tmp_path, "docs/CODE_OF_CONDUCT.md", DATA)
    config = _write(tmp_path, "settings.yaml", OVERVIEW)
    assert _order(tmp_path, config, conduct) == ["docs/CODE_OF_CONDUCT.md", "settings.yaml"]


@pytest.mark.parametrize("name", [
    "CONTRIBUTING.md", "contributing.rst", "CODE_OF_CONDUCT.md", "code-of-conduct.txt",
    "SECURITY.md", "SUPPORT.md", "GOVERNANCE.md", "MAINTAINERS", "AUTHORS", "SPONSORS.md",
    "LICENSE", "LICENCE.txt", "LICENSE-MIT", "COPYING.LESSER", "NOTICE", "PATENTS",
    "CHANGELOG.md", "CHANGELOG-1.2.md", "CHANGES.rst", "HISTORY.md", "NEWS",
    "RELEASE_NOTES.md", "docs/release-notes.adoc",
])
def test_names_of_process_files(name: str) -> None:
    assert is_process_file(Path(name))


@pytest.mark.parametrize("name", [
    "README.md", "ARCHITECTURE.md", "docs/design.md", "docs/security-model.md",
    "docs/supported-platforms.md", "docs/license-server-protocol.md",
    "RELEASING.md", "UPGRADING.md", "docs/changes-to-the-parser.md", "authors.py",
    "docs/contributing-a-backend.md", ".history", "security.yaml", "FUNDING.yml",
    "LICENSE-server-notes.md",
])
def test_a_name_that_only_contains_the_word_is_not_a_process_file(name: str) -> None:
    """The rule reads the whole name, of a document. ``docs/security-model.md``
    explains a design; ``authors.py`` is code and ``security.yaml``
    configuration."""
    assert not is_process_file(Path(name))


def test_at_one_depth_overview_wording_decides(tmp_path: Path) -> None:
    """Wording outranks path: the later-sorting name wins."""
    table = _write(tmp_path, "docs/aaa.md", DATA)
    overview = _write(tmp_path, "docs/zzz.md", OVERVIEW)
    # Reach: the two do score differently.
    plain, worded = overview_wording_scores(
        [overview_rank._head_words(table), overview_rank._head_words(overview)])
    assert worded > plain
    assert _order(tmp_path, table, overview) == ["docs/zzz.md", "docs/aaa.md"]


def test_equal_files_fall_back_to_path(tmp_path: Path) -> None:
    b = _write(tmp_path, "docs/b.md", OVERVIEW)
    a = _write(tmp_path, "docs/a.md", OVERVIEW)
    assert _order(tmp_path, b, a) == ["docs/a.md", "docs/b.md"]


# ── total, repeatable, independent of the input order ───────────────────────

def test_the_order_does_not_depend_on_the_order_given(tmp_path: Path) -> None:
    files = [
        _write(tmp_path, "README.md", OVERVIEW), _write(tmp_path, "docs/x.md", DATA),
        _write(tmp_path, "docs/y.md", OVERVIEW), _write(tmp_path, "conf/a.toml", DATA),
        _write(tmp_path, "b.yaml", OVERVIEW), _write(tmp_path, "docs/deep/z.md", OVERVIEW),
    ]
    forward = rank_overview_files(files, tmp_path)
    assert rank_overview_files(list(reversed(files)), tmp_path) == forward
    assert sorted(forward) == sorted(files)  # each file once


def test_the_same_bytes_score_the_same_whatever_the_path(tmp_path: Path) -> None:
    """The embedding sampled a file at offsets seeded by its path, so two
    copies of one README scored 0.717 and 0.678. A score here is a function
    of the bytes and of the set being ranked."""
    long_text = (OVERVIEW + " " + DATA) * 200
    one = _write(tmp_path, "README.md", long_text)
    two = _write(tmp_path, "packages/app/README.md", long_text)
    first, second = overview_wording_scores(
        [overview_rank._head_words(one), overview_rank._head_words(two)])
    assert first == second


def test_no_files_no_order(tmp_path: Path) -> None:
    assert rank_overview_files([], tmp_path) == []
    assert overview_wording_scores([]) == []


# ── what counts as wording ──────────────────────────────────────────────────

def test_only_the_head_of_a_file_is_read(tmp_path: Path) -> None:
    padded = _write(tmp_path, "late.md", "x " * HEAD_CHARS + OVERVIEW)
    assert not set(overview_rank._head_words(padded)) & {"project", "developers"}
    early = _write(tmp_path, "early.md", OVERVIEW + " x" * HEAD_CHARS)
    assert {"project", "developers"} <= set(overview_rank._head_words(early))


def test_markup_tags_are_not_words(tmp_path: Path) -> None:
    page = _write(tmp_path, "index.html", "<section class='project'><p>works</p></section>")
    assert overview_rank._head_words(page) == Counter({"works": 1})


def test_words_are_letters_in_any_script(tmp_path: Path) -> None:
    """Digits, underscores and one-letter fragments are not wording; letters
    outside ASCII are."""
    notes = _write(tmp_path, "notes.md", "проект v2 max_retries=3 x 設計文書")
    assert set(overview_rank._head_words(notes)) == {"проект", "max", "retries", "設計文書"}


def test_the_probes_cover_the_six_questions() -> None:
    assert len(overview_rank.OVERVIEW_PROBES) == 6
    assert all(len(probe) > 400 for probe in overview_rank.OVERVIEW_PROBES)


def test_a_word_every_candidate_uses_decides_less_than_a_rare_one() -> None:
    """Document frequency is counted over the files being ranked."""
    common = Counter({"project": 1})
    rare = Counter({"scavenger": 1})
    assert {"project", "scavenger"} <= set(overview_rank._QUERY)  # reach
    crowd = [Counter({"project": 1}) for _ in range(8)]
    scores = overview_wording_scores([common, rare, *crowd])
    assert scores[1] > scores[0] > 0


def test_a_head_with_none_of_the_probe_words_scores_zero() -> None:
    assert overview_wording_scores([Counter({"qwertyuiop": 3}), Counter()]) == [0.0, 0.0]


# ── the edges ───────────────────────────────────────────────────────────────

def test_an_unreadable_file_is_still_placed(tmp_path: Path) -> None:
    """Role and depth need no read: a path that cannot be opened keeps its
    place among its peers, with no wording score."""
    gone = tmp_path / "docs" / "missing.md"
    real = _write(tmp_path, "docs/real.md", OVERVIEW)
    config = _write(tmp_path, "docs/real.yaml", OVERVIEW)
    assert overview_rank._head_words(gone) == Counter()
    assert _order(tmp_path, config, gone, real) == [
        "docs/real.md", "docs/missing.md", "docs/real.yaml"]


def test_a_file_outside_the_root_is_ordered_by_its_whole_path(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    inside = _write(root, "a/b/c/d/deep.md", DATA)
    outside = _write(tmp_path, "elsewhere.md", DATA)
    ranked = rank_overview_files([outside, inside], root)
    assert set(ranked) == {inside, outside}
    assert len(ranked) == 2


def test_no_embedding_model_is_loaded(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The point of the change: the order costs file reads, never a model."""
    def refuse(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("an embedding model was requested")

    monkeypatch.setattr(sketch_embeddings, "_load_embedding_model", refuse)
    monkeypatch.setattr(sketch_embeddings, "_load_st_model_offline_first", refuse)
    files = [_write(tmp_path, "README.md", OVERVIEW), _write(tmp_path, "docs/a.md", DATA)]
    assert rank_overview_files(files, tmp_path) == files
