# SPDX-License-Identifier: AGPL-3.0-or-later
# covers: packages/hypergumbo-core/src/hypergumbo_core/readme_prose.py
# covers: packages/hypergumbo-core/src/hypergumbo_core/sketch.py
"""One rule for which README text can be the project's description.

The sketch's first line used to end with a README callout and its marker
(``... understand a codebase. > Requires Python 3.10+.``) whenever the
embedding extractor ran, because the rule that skips a callout lived in the
heuristic extractor only. Both now read the README through
``readme_prose.description_lines``; these tests hold each extractor to it on
the same fixtures, and hold the rule itself per README format.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from hypergumbo_core import sketch, sketch_embeddings
from hypergumbo_core.readme_prose import description_lines, plain_inline

DESCRIPTION = "Widget is a tool that turns source code into maps."

CALLOUT_AFTER_PROSE = f"""# widget

[![PyPI](https://img.shields.io/pypi/v/widget.svg)](https://pypi.org/project/widget/)

{DESCRIPTION}

```bash
pip install widget
```

> Requires Python 3.10+. For extras, run `widget add-extras`.

> Intel Mac users: see docs/INTEL_MAC.md.

Output:

```bash
# widget
```
"""

TAGLINE_IN_A_BLOCKQUOTE = """# [widget.js](https://widget.example/documentation/)

[![npm](https://img.shields.io/npm/v/widget.svg)](https://npmjs.org/package/widget)

> **A query builder that is _flexible_ and _fun_ to use!**

A batteries-included builder for Node.js.

> Upgrading from 2.x? Read the migration guide first.
"""


def _candidates(text: str) -> str:
    return " ".join(line for _i, line in sketch_embeddings._readme_candidate_lines(text))


def _heuristic(tmp_path: Path, text: str, name: str = "README.md") -> str:
    readme = tmp_path / name
    readme.write_text(text)
    result = sketch._extract_readme_description_heuristic(readme, 300)
    assert result is not None
    return result


def test_both_extractors_drop_a_callout_that_follows_the_description(tmp_path: Path) -> None:
    """The filed defect, on each path. Reach first: the description is there."""
    assert _heuristic(tmp_path, CALLOUT_AFTER_PROSE) == DESCRIPTION
    candidates = _candidates(CALLOUT_AFTER_PROSE)
    assert DESCRIPTION in candidates
    for callout in ("Requires Python", "Intel Mac", ">", "Output:"):
        assert callout not in candidates


def test_both_extractors_keep_a_tagline_written_as_a_blockquote(tmp_path: Path) -> None:
    """A blockquote that is the first prose IS the description; one that
    follows prose is a callout. Skipping every blockquote loses the first."""
    tagline = "**A query builder that is _flexible_ and _fun_ to use!**"
    assert _heuristic(tmp_path, TAGLINE_IN_A_BLOCKQUOTE) == tagline
    candidates = _candidates(TAGLINE_IN_A_BLOCKQUOTE)
    assert tagline in candidates
    assert "> " not in candidates
    assert "Upgrading from 2.x" not in candidates


@pytest.mark.parametrize("embedding_result", [
    None,  # the scorer found nothing: the heuristic answers
    "[widget.js](https://widget.example/documentation/) "
    "**A query builder that is _flexible_ and _fun_ to use!**",
])
def test_the_description_is_printed_without_markup(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
        embedding_result: str | None) -> None:
    """Whichever extractor found it. The scorer needs a model, so its result
    is supplied; what is under test is what happens to a result."""
    (tmp_path / "README.md").write_text(TAGLINE_IN_A_BLOCKQUOTE)
    monkeypatch.setattr(sketch_embeddings, "extract_readme_description_embedding",
                        lambda _path: embedding_result)
    description = sketch._extract_readme_description(tmp_path)
    assert description is not None
    assert description.endswith("A query builder that is flexible and fun to use!")
    for markup in ("**", "_", "](", ">"):
        assert markup not in description


@pytest.mark.parametrize(("name", "text", "callouts"), [
    ("github alert first", "# t\n\n> [!WARNING]\n> Unmaintained.\n\nReal words here.\n",
     ["Unmaintained", "WARNING"]),
    ("nested quote after prose", "# t\n\nReal words here.\n\n> > quoted twice\n", ["quoted twice"]),
    ("empty quote first, callout after prose",
     "# t\n\n>\n\nReal words here.\n\n> Later aside.\n", ["Later aside"]),
    ("rst directives", "widget\n======\n\n.. image:: logo.png\n   :alt: logo\n\n"
     "Real words here.\n\n.. note::\n\n   Needs Python 3.10.\n   Really.\n\nMore.\n",
     ["logo", "Needs Python", "Really"]),
    ("asciidoc paragraph", "= widget\n\nReal words here.\n\nNOTE: Needs Java 17\nor newer.\n\nMore.\n",
     ["Needs Java", "or newer"]),
    ("asciidoc block", "= widget\n\nReal words here.\n\n[WARNING]\n====\nDo not run as root.\n\nEver.\n====\n\nMore.\n",
     ["Do not run", "Ever", "===="]),
    ("asciidoc block as a paragraph", "= widget\n\nReal words here.\n\n[TIP]\nUse the wrapper.\n\nMore.\n",
     ["Use the wrapper", "[TIP]"]),
    ("a label over a code block", "# t\n\nReal words here.\n\nOutput:\n\n```\nx\n```\n\nMore.\n",
     ["Output:"]),
    ("asciidoc block left open", "= widget\n\nReal words here.\n\n[NOTE]\n====\nNever closed.\n",
     ["Never closed"]),
])
def test_a_callout_is_blanked_in_each_format(name: str, text: str, callouts: list[str]) -> None:
    lines = text.split("\n")
    out = description_lines(lines)
    assert len(out) == len(lines), name
    kept = "\n".join(out)
    assert "Real words here." in kept, name
    for callout in callouts:
        assert callout not in kept, (name, callout)
    if "More." in text:
        assert "More." in kept, name


@pytest.mark.parametrize("text", [
    # a sentence that ends a longer paragraph
    "# t\n\nA builder for Node.js,\nfeaturing:\n\n```\nx\n```\n",
    # over a list, not a code block
    "# t\n\nFeatures:\n\n- one\n- two\n",
    # the code block follows with no blank line: the same paragraph in most renderers
    "# t\n\nRun:\n```\nx\n```\n",
    # nothing follows
    "# t\n\nTo be continued:\n",
])
def test_a_colon_alone_does_not_make_a_label(text: str) -> None:
    lines = text.split("\n")
    assert description_lines(lines) == lines


def test_text_under_a_title_underline_does_not_count_as_prose() -> None:
    """A setext or reStructuredText title is text too; if it counted, a
    tagline blockquote under it would be read as a callout."""
    out = description_lines("widget\n======\n\n> The tagline.\n".split("\n"))
    assert "The tagline." in out
    assert not any(line.startswith(">") for line in out)


def test_a_fenced_block_is_left_alone() -> None:
    """A ``>`` inside code is not a blockquote, and code is not prose: a
    blockquote after only a code block is still the first prose."""
    text = "# t\n\n```\n> prompt\nNOTE: literal\n```\n\n~~~\n> more\n~~~\n\n> The tagline.\n\n```\nunclosed\n> x"
    out = description_lines(text.split("\n"))
    assert out[3] == "> prompt"
    assert out[4] == "NOTE: literal"
    assert out[8] == "> more"
    assert out[11] == "The tagline."
    assert out[-1] == "> x"


@pytest.mark.parametrize(("raw", "plain"), [
    ("[knex.js](https://knex.example/) is **fast** and _fun_", "knex.js is fast and fun"),
    ("see [the guide][guide] and ![logo](x.png) more", "see the guide and more"),
    ("keeps snake_case_names, `code_spans` and 2 * 3 * 4", "keeps snake_case_names, `code_spans` and 2 * 3 * 4"),
    ("__strong__ and *emphasis* here", "strong and emphasis here"),
])
def test_plain_inline(raw: str, plain: str) -> None:
    assert plain_inline(raw) == plain


def test_this_repositorys_readme_gives_one_description_on_both_paths() -> None:
    """The live file the defect was seen on."""
    readme = Path(__file__).resolve().parents[3] / "README.md"
    heuristic = sketch._extract_readme_description_heuristic(readme, 300)
    assert heuristic is not None and heuristic.endswith("understand a codebase.")
    candidates = _candidates(readme.read_text())
    assert heuristic in candidates
    assert "Requires Python" not in candidates
