# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for ``scripts/archaeology_heuristic.py``: how likely a comment is history.

The score is a property, not a golden value: it stays a probability, each
history signal can only raise it and the rule voice only lower it, a tracker ID
on its own is not history, and the fitted weights still separate the labelled
blocks in ``tests/fixtures/archaeology-corpus.json``.
"""
# covers: scripts/archaeology_heuristic.py
from __future__ import annotations

import importlib.util
import io
import json
import sys
from pathlib import Path
from types import ModuleType

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
_PATH = REPO_ROOT / "scripts" / "archaeology_heuristic.py"
_CORPUS = REPO_ROOT / "tests" / "fixtures" / "archaeology-corpus.json"


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("archaeology_heuristic", _PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["archaeology_heuristic"] = module  # @dataclass needs it
    spec.loader.exec_module(module)
    return module


ah = _load()

HISTORY = (
    "This used to assign, so the last match won. Measured on dev 09921c57a1 "
    "on 2026-08-26: 98,422 calls, 48,516 of them resolved, and the check "
    "was not reached. The first draft used a length heuristic."
)
RULE = (
    "The owner must sit in the child's own file, and its qualified name must "
    "be the prefix, because a namespace can share its name with a class."
)

#: One phrase per history family, each of which must raise the score.
HISTORY_PHRASES = {
    "change_narrative": "This previously returned the first hit.",
    "provenance": "Found in the 2026-09-01 audit, PR #251.",
    "measurement": "Measured on 12 repositories: 63.9% of rows.",
    "numbers": "It saw 1,377 rows and 2188 edges.",
    "past_tense": "It was what they had.",
}


@pytest.mark.parametrize("text", ["", "   ", "#", "x", "1234 5678", HISTORY, RULE, "word " * 5000])
def test_the_score_is_a_probability(text: str) -> None:
    assert 0.0 <= ah.probability(text) <= 1.0


def test_a_history_narrative_scores_above_half() -> None:
    assert ah.probability(HISTORY) > 0.5


def test_a_stated_rule_scores_below_half() -> None:
    assert ah.probability(RULE) < 0.5


@pytest.mark.parametrize("family", sorted(HISTORY_PHRASES))
def test_each_history_signal_raises_the_score(family: str) -> None:
    phrase = HISTORY_PHRASES[family]
    assert ah.assess(phrase).matches.get(family), family
    assert ah.probability(f"{RULE} {phrase}") > ah.probability(RULE)


def test_the_rule_voice_lowers_the_score() -> None:
    assert ah.probability(f"{HISTORY} It must never do so.") < ah.probability(HISTORY)


def test_history_families_weigh_up_and_the_rule_voice_down() -> None:
    assert set(ah.WEIGHTS) == set(ah.FAMILIES)
    assert all(ah.WEIGHTS[f] > 0 for f in HISTORY_PHRASES)
    assert ah.WEIGHTS["rule_voice"] < 0


def test_a_tracker_id_is_not_history() -> None:
    assert ah.probability(f"{RULE} (WI-damir, INV-fazim, ADR-0061)") <= ah.probability(RULE)


def test_an_identifier_suffix_is_not_a_measurement() -> None:
    assert not ah.assess("ADR-0061 and audit-findings 0018 and #:1234").matches.get("numbers")


def _hits(text: str, family: str) -> tuple[str, ...]:
    return ah.assess(text).matches.get(family, ())


@pytest.mark.parametrize("text", [
    "Returns the number of matching items found under root.",
    "Drift is reported as a warning; a gap is reported by the gate.",
    "The pre-commit hook rejects a commit with no sign-off.",
])
def test_what_the_code_does_now_is_not_provenance(text: str) -> None:
    """Searching, reporting and committing are things code DOES; the words
    describe the code, not who changed it when."""
    assert _hits(text, "provenance") == ()


@pytest.mark.parametrize("text, phrase", [
    ("The owner reported it twice.", "reported"),
    ("Fixed in commit 4bb8062cef.", "in commit"),
    ("A crash caught by the nightly run.", "caught"),
])
def test_an_event_is_still_provenance(text: str, phrase: str) -> None:
    assert phrase in _hits(text, "provenance")


def test_a_fraction_below_one_is_a_threshold_not_a_figure() -> None:
    assert _hits("Edges under a 0.85 confidence, or 0.5 by default, are dropped.", "numbers") == ()
    assert _hits("It took 517.7 seconds over 1,377 rows.", "numbers") == ("517.7", "1,377")


def test_the_rule_voice_is_conditional_and_normative() -> None:
    """A rule states a condition and its consequence, or an obligation; the
    emphatic words a story uses as often ("it never fired", "only because")
    are not the rule's voice."""
    assert _hits("If the key is absent, it must not guess; when unsure, it "
                 "cannot, unless told. Otherwise it does not.", "rule_voice") == (
        "If", "must", "when", "cannot", "unless", "Otherwise", "does not")
    assert _hits("It never fired, only because it always kept what it "
                 "deliberately ensured, so that nothing guaranteed it.", "rule_voice") == ()


def test_matches_name_the_phrases_that_scored() -> None:
    matches = ah.assess(HISTORY).matches
    assert "used to" in matches["change_narrative"]
    assert "2026-08-26" in matches["provenance"]
    assert "rule_voice" not in matches


def test_empty_text_scores_the_prior() -> None:
    result = ah.assess("")
    assert result.words == 0 and result.matches == {}
    assert result.probability < 0.5


def _corpus(kind: str, sample: str | None = None) -> list[dict[str, object]]:
    blocks = json.loads(_CORPUS.read_text(encoding="utf-8"))["blocks"]
    return [b for b in blocks
            if b["kind"] == kind and (sample is None or b["sample"] == sample)]


def test_every_corpus_block_names_its_kind_and_sample() -> None:
    blocks = json.loads(_CORPUS.read_text(encoding="utf-8"))["blocks"]
    assert {(b["kind"], b["sample"]) for b in blocks} == {
        ("comment", "curated"), ("comment", "random"), ("docstring", "random")}


def _auc(scored: list[tuple[float, object]]) -> float:
    """Share of (history, rule) pairs the scores put in the right order."""
    history = [p for p, y in scored if y]
    rules = [p for p, y in scored if not y]
    wins = sum((h > r) + 0.5 * (h == r) for h in history for r in rules)
    return wins / (len(history) * len(rules))


@pytest.mark.parametrize("sample, min_rows, min_auc", [
    ("curated", 100, 0.90),
    ("random", 240, 0.80),
])
def test_the_fitted_weights_still_separate_each_comment_sample(
    sample: str, min_rows: int, min_auc: float
) -> None:
    """Sample by sample over the COMMENT rows, the fit's own data: a blended
    figure would let the half-history curated rows hide the random ones, or
    the reverse. Each must rank history above rule (floors a few points
    under the fit's), separate the two by mean score, and beat calling every
    block the sample's commoner label."""
    blocks = _corpus("comment", sample)
    assert len(blocks) >= min_rows
    scored = [(ah.probability(str(b["text"])), b["label"]) for b in blocks]
    assert _auc(scored) >= min_auc
    history = [p for p, y in scored if y]
    rules = [p for p, y in scored if not y]
    assert sum(history) / len(history) - sum(rules) / len(rules) >= 0.3
    accuracy = sum((p >= 0.5) == bool(y) for p, y in scored) / len(scored)
    commoner = max(len(history), len(rules)) / len(scored)
    assert accuracy > commoner


def test_the_random_comments_are_the_40_word_sample() -> None:
    blocks = _corpus("comment", "random")
    assert {b["label"] for b in blocks} == {0, 1}
    assert min(len(str(b["text"]).split()) for b in blocks) >= 40


def test_the_labelled_docstrings_are_the_held_out_100_word_sample() -> None:
    """No accuracy floor: the weights are not fitted to docstrings, and the
    module docstring says their score is weaker evidence. What is pinned is
    the sample the module docstring describes -- random docstrings of 100 or
    more words, from hypergumbo_core, holding both labels -- so a refit or a
    claim about docstrings is measured against data that is what it says."""
    blocks = _corpus("docstring")
    assert len(blocks) >= 480
    assert {b["sample"] for b in blocks} == {"random"}
    assert {b["label"] for b in blocks} == {0, 1}
    assert all(str(b["origin"]).startswith("hypergumbo_core/") for b in blocks)
    assert min(len(str(b["text"]).split()) for b in blocks) >= 100


SOURCE = '''\
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Docstring, not a comment."""
import os  # trailing comment, not comment-only

# This used to assign, so the last match won.
# Measured on 2026-08-26 across 12 repos.
x = 1
    # an indented single line
s = """
# inside a string, not a comment
"""
'''


def test_comment_blocks_are_the_runs_of_comment_only_lines() -> None:
    blocks = ah.comment_blocks(SOURCE)
    assert [(b.start, b.end) for b in blocks] == [(1, 1), (5, 6), (8, 8)]
    assert blocks[1].text == (
        "This used to assign, so the last match won.\n"
        "Measured on 2026-08-26 across 12 repos."
    )


DOC_SOURCE = '''\
"""Module docstring. This used to be a script."""
# A comment right under the module docstring.
import os


class Thing:
    """Class docstring: it must keep its key."""

    # A comment between the class docstring and the method.
    def method(self):
        """Method docstring.

        # Not a comment: a heading inside the docstring.
        """
        """A second string statement is not a docstring."""

        def inner():
            """Nested function docstring."""

    async def run(self):  # trailing comment
        """Async docstring."""
'''


def test_docstring_blocks_are_every_module_class_and_function_docstring() -> None:
    blocks = ah.docstring_blocks(DOC_SOURCE)
    assert [(b.start, b.end, b.kind) for b in blocks] == [
        (1, 1, "docstring"), (7, 7, "docstring"), (11, 14, "docstring"),
        (18, 18, "docstring"), (21, 21, "docstring"),
    ]
    assert blocks[0].text == "Module docstring. This used to be a script."
    assert blocks[2].text == (
        "Method docstring.\n\n# Not a comment: a heading inside the docstring."
    )


def test_a_comment_beside_a_docstring_stays_out_of_it() -> None:
    docstrings = " ".join(b.text for b in ah.docstring_blocks(DOC_SOURCE))
    assert "A comment right under" not in docstrings
    assert "A comment between" not in docstrings
    assert "trailing comment" not in docstrings


def test_a_hash_line_inside_a_docstring_is_not_a_comment() -> None:
    comments = ah.comment_blocks(DOC_SOURCE)
    assert [(b.start, b.text, b.kind) for b in comments] == [
        (2, "A comment right under the module docstring.", "comment"),
        (9, "A comment between the class docstring and the method.", "comment"),
    ]


def test_all_lists_both_kinds_unmerged_in_line_order() -> None:
    blocks = ah.blocks_of(DOC_SOURCE, "all")
    assert [(b.start, b.kind) for b in blocks] == [
        (1, "docstring"), (2, "comment"), (7, "docstring"), (9, "comment"),
        (11, "docstring"), (18, "docstring"), (21, "docstring"),
    ]
    assert blocks == sorted(ah.comment_blocks(DOC_SOURCE) + ah.docstring_blocks(DOC_SOURCE),
                            key=lambda b: b.start)


def test_each_kind_selects_only_its_own_blocks() -> None:
    assert ah.blocks_of(DOC_SOURCE, "comments") == ah.comment_blocks(DOC_SOURCE)
    assert ah.blocks_of(DOC_SOURCE, "docstrings") == ah.docstring_blocks(DOC_SOURCE)


def test_cli_scores_stdin(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setattr(sys, "stdin", io.StringIO(HISTORY))
    assert ah.main([]) == 0
    out = capsys.readouterr().out
    assert out.startswith(f"{ah.probability(HISTORY):.2f}")
    assert "change_narrative: used to" in out


def test_cli_json_for_a_file(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    path = tmp_path / "note.txt"
    path.write_text(RULE, encoding="utf-8")
    assert ah.main(["--json", str(path)]) == 0
    (record,) = json.loads(capsys.readouterr().out)
    assert record["source"] == str(path)
    assert record["probability"] == pytest.approx(ah.probability(RULE), abs=1e-4)
    assert record["matches"]["rule_voice"] == ["must", "must"]


def test_cli_blocks_mode_lists_blocks_over_the_threshold(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = tmp_path / "mod.py"
    path.write_text(SOURCE, encoding="utf-8")
    assert ah.main(["--blocks", "--threshold", "0.5", str(path)]) == 0
    lines = capsys.readouterr().out.splitlines()
    assert len(lines) == 1
    assert f"{path}:5-6" in lines[0] and "This used to assign" in lines[0]


def test_cli_blocks_json(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    path = tmp_path / "mod.py"
    path.write_text(SOURCE, encoding="utf-8")
    assert ah.main(["--min-words", "0", "--blocks", "--json", str(path)]) == 0
    records = json.loads(capsys.readouterr().out)
    assert [(r["start"], r["end"]) for r in records] == [(1, 1), (5, 6), (8, 8)]
    assert records[1]["probability"] > 0.5


def test_cli_refuses_an_unreadable_python_file(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = tmp_path / "broken.py"
    path.write_text('s = """never closed\n', encoding="utf-8")
    assert ah.main(["--blocks", str(path)]) == 2
    assert "broken.py" in capsys.readouterr().err


def test_cli_refuses_a_missing_file(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert ah.main([str(tmp_path / "absent.txt")]) == 2
    assert "absent.txt" in capsys.readouterr().err


def test_cli_text_with_no_signal_prints_only_its_score(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = tmp_path / "plain.txt"
    path.write_text("Count the arms.", encoding="utf-8")
    assert ah.main([str(path)]) == 0
    assert capsys.readouterr().out == f"{ah.probability('Count the arms.'):.2f}  {path}\n"


def test_cli_blocks_mode_shows_an_empty_comment_as_an_empty_first_line(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = tmp_path / "bare.py"
    path.write_text("x = 1\n#\n", encoding="utf-8")
    assert ah.main(["--min-words", "0", "--blocks", str(path)]) == 0
    assert capsys.readouterr().out == f"{ah.probability(''):.2f}  {path}:2-2  comment  \n"


def test_cli_kind_docstrings_scores_docstrings_without_blocks(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = tmp_path / "mod.py"
    path.write_text(DOC_SOURCE, encoding="utf-8")
    assert ah.main(["--min-words", "0", "--kind", "docstrings", "--json", str(path)]) == 0
    records = json.loads(capsys.readouterr().out)
    assert [(r["start"], r["kind"]) for r in records] == [
        (b.start, "docstring") for b in ah.docstring_blocks(DOC_SOURCE)]
    assert records[0]["probability"] == pytest.approx(
        ah.probability("Module docstring. This used to be a script."), abs=1e-4)


def test_cli_kind_all_labels_each_line_with_its_kind(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = tmp_path / "mod.py"
    path.write_text(DOC_SOURCE, encoding="utf-8")
    assert ah.main(["--min-words", "0", "--blocks", "--kind", "all", str(path)]) == 0
    lines = capsys.readouterr().out.splitlines()
    assert [line.split("  ")[2] for line in lines] == [
        b.kind for b in ah.blocks_of(DOC_SOURCE, "all")]
    assert f"{path}:2-2  comment  A comment right under" in lines[1]


def test_cli_blocks_alone_still_means_comments(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = tmp_path / "mod.py"
    path.write_text(DOC_SOURCE, encoding="utf-8")
    assert ah.main(["--min-words", "0", "--blocks", "--json", str(path)]) == 0
    assert {r["kind"] for r in json.loads(capsys.readouterr().out)} == {"comment"}


def test_cli_kind_refuses_a_file_it_cannot_parse(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = tmp_path / "broken.py"
    path.write_text("def f(:\n    pass\n", encoding="utf-8")
    assert ah.main(["--kind", "docstrings", str(path)]) == 2
    assert "broken.py" in capsys.readouterr().err


#: Blocks of 9, 10 and 12 words; the tracker ID in the last is not a word.
SHORT_SOURCE = '''\
# one two three four five six seven eight nine
x = 1
# one two three four five six seven eight nine ten
y = 2
# one two three four five six seven eight nine ten eleven twelve WI-damir
def f():
    """One two three four five six seven eight nine."""
'''


def _block_starts(tmp_path: Path, capsys: pytest.CaptureFixture[str], *args: str) -> list[int]:
    path = tmp_path / "short.py"
    path.write_text(SHORT_SOURCE, encoding="utf-8")
    assert ah.main([*args, "--json", str(path)]) == 0
    return [r["start"] for r in json.loads(capsys.readouterr().out)]


def test_blocks_under_ten_words_are_skipped_by_default(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """One matched word saturates a short block's density, so its score says
    little; ten words is the default floor."""
    assert ah.DEFAULT_MIN_WORDS == 10
    assert _block_starts(tmp_path, capsys, "--kind", "all") == [3, 5]


def test_min_words_sets_the_floor_and_zero_turns_it_off(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert _block_starts(tmp_path, capsys, "--kind", "all", "--min-words", "11") == [5]
    assert _block_starts(tmp_path, capsys, "--kind", "all", "--min-words", "0") == [1, 3, 5, 7]


def test_min_words_counts_words_as_the_score_does(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Twelve words and a tracker ID: twelve, so a floor of 13 drops it."""
    assert _block_starts(tmp_path, capsys, "--blocks", "--min-words", "12") == [5]
    assert _block_starts(tmp_path, capsys, "--blocks", "--min-words", "13") == []


def test_min_words_does_not_filter_stdin(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Stdin is one text, not a file of blocks: it is scored whatever its length."""
    monkeypatch.setattr(sys, "stdin", io.StringIO("It was."))
    assert ah.main(["--min-words", "50"]) == 0
    assert capsys.readouterr().out.startswith(f"{ah.probability('It was.'):.2f}")


#: A short comment and a short docstring that are all history, beside code
#: and blocks of ten or more words that are not.
WHOLE_SOURCE = '''\
"""Module docstring that states a rule and runs to more than ten words in all."""
# Previously this was wrong.
x = 1  # trailing comment previously kept
# The value must stay positive because the caller divides by it in every case.
def f():
    """It was replaced."""
    return x
'''


def test_a_python_file_scored_whole_leaves_out_its_short_blocks() -> None:
    kept = ah.without_short_blocks(WHOLE_SOURCE, 10)
    assert "Previously this was wrong" not in kept
    assert "It was replaced" not in kept
    assert "Module docstring that states a rule" in kept
    assert "The value must stay positive" in kept
    assert "x = 1  # trailing comment previously kept" in kept
    assert "def f():" in kept and "return x" in kept


def test_a_cut_docstring_with_non_ascii_text_leaves_the_code_beside_it() -> None:
    """ast counts columns in UTF-8 bytes; the cut must count characters."""
    source = 'def f(): """Résumé café naïve."""; return 1\n'
    assert ah.without_short_blocks(source, 10) == "def f(): ; return 1\n"


def test_a_floor_of_zero_leaves_a_python_file_whole() -> None:
    assert ah.without_short_blocks(WHOLE_SOURCE, 0) == WHOLE_SOURCE


def test_cli_scores_a_python_file_without_its_short_blocks(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = tmp_path / "mod.py"
    path.write_text(WHOLE_SOURCE, encoding="utf-8")
    assert ah.main(["--json", str(path)]) == 0
    (record,) = json.loads(capsys.readouterr().out)
    assert record["probability"] == pytest.approx(
        ah.probability(ah.without_short_blocks(WHOLE_SOURCE, 10)), abs=1e-4)
    assert record["matches"]["change_narrative"] == ["previously"]  # the trailing one only


def test_cli_scores_a_non_python_file_whole(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = tmp_path / "notes.txt"
    path.write_text(WHOLE_SOURCE, encoding="utf-8")
    assert ah.main(["--json", str(path)]) == 0
    (record,) = json.loads(capsys.readouterr().out)
    assert record["probability"] == pytest.approx(ah.probability(WHOLE_SOURCE), abs=1e-4)


def test_cli_refuses_a_python_file_it_cannot_parse_scored_whole(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = tmp_path / "broken.py"
    path.write_text("def f(:\n    pass\n", encoding="utf-8")
    assert ah.main([str(path)]) == 2
    assert "broken.py" in capsys.readouterr().err


def test_min_words_refuses_a_negative_floor(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exit_info:
        ah.main(["--blocks", "--min-words", "-1", "x.py"])
    assert exit_info.value.code == 2
    assert "--min-words: must be 0 or more" in capsys.readouterr().err
