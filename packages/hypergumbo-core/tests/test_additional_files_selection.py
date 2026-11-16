# SPDX-License-Identifier: AGPL-3.0-or-later
"""Which Additional Files a sketch picks and how much of each it prints
(WI-papup).

Two rules, each measured before it was chosen (the overview order's third,
process files last, is tested in ``test_overview_rank.py``):

- **The centrality list holds documents.** It is one of the three lists the
  section is filled from in turn, and it scores a file by the central
  symbols it mentions per byte. A data or configuration file that holds a
  few symbol names outscored prose about the code. On four repositories 9,
  1, 13 and 9 of the list's first 15 files were not documentation.
- **The README gets a real share of the content.** A file that did not fit
  was cut to the median size of the files printed before it, floor 500
  tokens; the first file has no median, so a README longer than the budget
  was cut to the floor and the next, smaller file printed whole.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from hypergumbo_core.sketch import (
    _content_shares,
    _format_additional_files,
    _select_additional_files,
)


def _write(root: Path, rel: str, text: str) -> Path:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _selected(root: Path, scores: dict[str, float], max_files: int = 10,
              rank_overviews: bool = False) -> list[str]:
    """The selection, by default with the overview order off, so the README
    and the centrality list are all that rank; what is left follows in
    discovery order."""
    source = _write(root, "src/app.py", "def run():\n    return 1\n")
    files, _candidates, _per_file, _degrees = _select_additional_files(
        root, [source], [], {}, max_files=max_files, rank_overviews=rank_overviews,
        cached_centrality_scores=scores)
    return [f.relative_to(root).as_posix() for f in files]


# ── the centrality list ─────────────────────────────────────────────────────

def test_a_document_that_mentions_the_code_is_picked_before_a_data_file_that_lists_it(
        tmp_path: Path) -> None:
    """The catalogue has the higher score, in any language's repository: it
    is nothing but names. The guide is prose about two of them."""
    _write(tmp_path, "README.md", "# Demo\n")
    _write(tmp_path, "catalog.yaml", "functions: [parse_config, run_server, load_plugins]\n")
    _write(tmp_path, "guide.md", "How `parse_config` feeds `run_server`, step by step.\n")
    order = _selected(tmp_path, {"catalog.yaml": 5.0, "guide.md": 1.0})
    assert order.index("guide.md") < order.index("catalog.yaml")
    assert order[:2] == ["README.md", "guide.md"]


def test_a_document_that_mentions_no_symbol_is_not_in_the_centrality_list(
        tmp_path: Path) -> None:
    """With the overview order on, as a sketch runs it. One document
    mentions a symbol; after it, every pick is the overview order's. If the
    centrality list also held the documents that mention nothing (score 0,
    in discovery order), every other pick would come from it.

    The overview order here is by wording (m1, m2, zz) and then by path (a4,
    b5). Discovery order is the file system's; the files are written so
    that sorted, creation and reverse-creation order each differ from it at
    the first pick that would tell."""
    overview = ("This project is a small tool. It is for developers who want "
                "predictable behavior. How it works: you point it at input.")
    _write(tmp_path, "README.md", "# Demo\n")
    _write(tmp_path, "a4.md", "alpha beta gamma\n")
    _write(tmp_path, "m1.md", overview * 3)
    _write(tmp_path, "zz.md", "This project is a small tool.\n")
    _write(tmp_path, "m2.md", overview)
    _write(tmp_path, "b5.md", "delta epsilon zeta\n")
    _write(tmp_path, "docs/guide.md", "Calls `parse_config`.\n")
    order = _selected(tmp_path, {"docs/guide.md": 1.0}, rank_overviews=True)
    assert order == ["README.md", "m1.md", "docs/guide.md", "m2.md", "zz.md", "a4.md", "b5.md"]


def test_documents_in_the_centrality_list_are_ordered_by_score(tmp_path: Path) -> None:
    _write(tmp_path, "README.md", "# Demo\n")
    _write(tmp_path, "a_minor.md", "Mentions `helper` once.\n")
    _write(tmp_path, "b_major.md", "All about `parse_config` and `run_server`.\n")
    order = _selected(tmp_path, {"a_minor.md": 0.2, "b_major.md": 3.0})
    assert order == ["README.md", "b_major.md", "a_minor.md"]


def test_every_candidate_is_still_selected_when_there_is_room(tmp_path: Path) -> None:
    """Control: the rule orders the section; it removes nothing from it."""
    for name in ("README.md", "catalog.yaml", "guide.md", "data.json", "notes.md"):
        _write(tmp_path, name, f"{name} content\n")
    order = _selected(tmp_path, {"catalog.yaml": 5.0})
    assert sorted(order) == ["README.md", "catalog.yaml", "data.json", "guide.md", "notes.md"]


# ── content shares ──────────────────────────────────────────────────────────

def _content(root: Path, files: list[Path], budget: int) -> tuple[str, list[str]]:
    section, included, _degree = _format_additional_files(
        root, [], [], {}, token_budget=budget, include_content=True,
        section_title="Additional Files Content", preselected_files=files)
    return section, [f.relative_to(root).as_posix() for f in included]


def _long_readme(sections: int = 30) -> str:
    return "# Demo\n\nWhat the project is.\n\n" + "\n\n".join(
        f"## Part {i}\n\n" + f"Sentence {i} about the project. " * 12 for i in range(sections))


def test_a_readme_longer_than_the_budget_gets_more_than_the_floor(tmp_path: Path) -> None:
    """Budget 2,000 tokens, README about 3,400, then a 150-token note. The
    README used to be cut to 500 tokens and the note printed whole."""
    readme = _write(tmp_path, "README.md", _long_readme())
    note = _write(tmp_path, "docs/install-on-one-platform.md", "Install note. " * 40)
    section, included = _content(tmp_path, [readme, note], 2000)
    assert included == ["README.md", "docs/install-on-one-platform.md"]
    readme_part = section.split("docs/install-on-one-platform.md")[0]
    assert len(readme_part) // 4 > 1000
    assert "[...truncated...]" in readme_part
    assert len(section) // 4 <= 2000


def test_an_earlier_file_is_cut_less_than_a_later_one(tmp_path: Path) -> None:
    """Three files of 5,700 tokens and a budget of 3,600: none fits, each is
    cut to its share, and the shares fall with position (1,964 / 982 / 654)."""
    files = [_write(tmp_path, name, _long_readme(60)) for name in ("README.md", "b.md", "c.md")]
    section, included = _content(tmp_path, files, 3600)
    assert included == ["README.md", "b.md", "c.md"]
    first, rest = section.split("START of b.md", 1)
    second, third = rest.split("START of c.md", 1)
    assert len(first) > len(second) > len(third) >= 450 * 4
    assert len(section) // 4 <= 3600


def test_a_file_that_fits_is_printed_whole(tmp_path: Path) -> None:
    """Control: shares apply only to a file that does not fit."""
    readme = _write(tmp_path, "README.md", "# Demo\n\nShort.\n")
    note = _write(tmp_path, "notes.md", "A note. " * 50)
    section, included = _content(tmp_path, [readme, note], 2000)
    assert included == ["README.md", "notes.md"]
    assert "[...truncated...]" not in section


@pytest.mark.parametrize(("budget", "count", "expected"), [
    (2000, 5, [1334, 666]),          # a third file's share would be 363
    (6000, 3, [3273, 1637, 1090]),   # all three clear the floor
    (400, 4, [400]),                 # never fewer than one share
    (2000, 1, [2000]),
    (2000, 0, []),
])
def test_content_shares(budget: int, count: int, expected: list[int]) -> None:
    assert _content_shares(budget, count, 500) == expected
