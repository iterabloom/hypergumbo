#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Estimate how likely a piece of text is substantially historical exegesis.

A code comment should state what is true of the code beside it: a constraint,
a hazard, why the obvious alternative is wrong. Historical exegesis tells how
the code came to be instead: what it used to do, the bug that changed it, what
a run on some corpus measured, which PR or audit did it. That account belongs
in the tracker and the commit history, which keep it whole; in a comment it
describes a moment, and the code moves on without it. A tracker ID on its own
is a pointer, not history, and scores nothing.

HOW IT SCORES. Six families of regular expressions each count their matches
per 100 words (capped at :data:`CAP`):

* ``change_narrative`` -- a past state set against the present one ("used
  to", "previously", "the first draft", "was replaced");
* ``provenance`` -- where and when the change was made: a date, a PR number,
  a commit, a phase, batch or audit, "reported", "found";
* ``measurement`` -- a figure taken once on some corpus ("measured", "52 of
  52", "63.9%", "on 12 repos");
* ``numbers`` -- figures of three or more digits, comma-grouped or decimal
  (an ID such as ``ADR-0061`` or ``0018``, or a date's year, does not count);
* ``past_tense`` -- narrative auxiliaries ("was", "were", "had");
* ``rule_voice`` -- the present-tense voice of a constraint ("must",
  "never", "because"), the one family that lowers the score.

A logistic model turns the densities into a probability:
``1 / (1 + exp(-(BIAS + sum(WEIGHTS[f] * density[f]))))``. Densities rather
than counts, because "substantially" is a share: a long block that states its
rule and mentions one date is not history, and a short one that is all
anecdote is. Length itself is deliberately not a feature, since a long comment
that states rules is long, not historical.

WHERE THE WEIGHTS COME FROM. They are fitted (L2-regularised logistic
regression, scikit-learn's ``LogisticRegression(C=0.18)`` on the capped
densities) to the labelled COMMENT blocks in
``tests/fixtures/archaeology-corpus.json``, 108 blocks from hypergumbo_core;
five-fold cross-validation classifies 88% of them correctly at 0.5. Most
misses are mixed blocks, a rule wrapped in an incident story, which score
between 0.3 and 0.5. The probability is a calibrated heuristic over that
corpus, not a judgement: read a high-scoring block before cutting it. When the
families change, refit against the comment rows and update :data:`WEIGHTS`
and :data:`BIAS`; the corpus test fails if the fit no longer separates them.

A DOCSTRING SCORE IS WEAK EVIDENCE: USE IT TO PICK WHAT TO READ. The
corpus's docstring rows, a random sample of docstrings of 100 or more words
labelled blind to these scores, are held out of the fit. History is the
minority there, about one docstring in seven, and it is usually woven into a
present-tense rule ("the previous code did X, measured on Y" as the reason
for the rule) rather than set apart, so the densities over-read rules and
under-read that history: about one rule-stating docstring in five scores
over 0.5, and a cutoff high enough to be mostly right finds few of the
historical ones. The limit is the families, not the weights: refitting on
the docstring rows themselves ranks them barely better (cross-validated AUC
0.85 against these weights' 0.83). The sample has no docstring under 100 words, so a short
docstring's score is unmeasured, and there one word ("found", a version
number, an array's dimension) can saturate it.

BLOCKS. Score a Python file one block at a time, not as one text: a whole
file's score is diluted by its code and by the rules around a historical
paragraph. A block is a run of comment-only lines or one docstring (module,
class or function). The two never share text: a ``#`` comment beside a
docstring is not part of it, and a ``#`` line inside a docstring is docstring
text. ``--kind all`` lists both kinds, each scored on its own and labelled.

USAGE.
  archaeology_heuristic.py [FILE ...]           score each file's text (stdin if none)
  archaeology_heuristic.py --blocks FILE.py ... score each comment-only block
  --kind comments|docstrings|all                which blocks to score; implies --blocks
  --threshold P   list only results scoring at least P
  --json          machine-readable output
Exit 0, or 2 when a file cannot be read or (with --blocks) tokenised or parsed.
"""
from __future__ import annotations

import argparse
import ast
import io
import json
import math
import re
import sys
import tokenize
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Optional, Sequence

_I = re.IGNORECASE

#: The signal families, in the order the output lists them.
FAMILIES: dict[str, re.Pattern[str]] = {
    "change_narrative": re.compile(
        r"\bused to\b|\bpreviously\b|\bformerly\b|\boriginally\b|\bno longer\b"
        r"|\buntil (?:now|recently|then)\b|\buntil (?:WI|INV|PR|ADR)\b"
        r"|\bthe (?:old|earlier|previous|original|prior|legacy) (?!value|mode\b)\w+"
        r"|\bfirst (?:draft|cut|version|design|attempt|pass)\b"
        r"|\bpre-(?:fix|fold|change|refactor|#\d+)\b|\bpost-#\d+\b"
        r"|\bbefore (?:this|the) (?:fix|change|PR|rewrite)\b"
        r"|\bwas (?:changed|renamed|removed|dropped|replaced|added|introduced|moved|wrong"
        r"|missing|stale|ungated|unbounded|deleted|rewritten)\b"
        r"|\bthis (?:comment|docstring|note|line) (?:here )?"
        r"(?:used to|read|said|named|claimed|previously)\b"
        r"|\b(?:fossil|restored|re-?measured|landed|merged|lacked|regressed|reverted)\b"
        r"|\b(?:this|that|it) replaced\b|\bthe \w+ (?:this|it) replaced\b|\ba hand-written copy\b",
        _I,
    ),
    "provenance": re.compile(
        r"\b20\d\d-[01]\d-[0-3]\d\b"
        r"|\bPR ?#?\d+\b|(?<![\w/])#\d{3,}\b|\bcommit\b|\bon dev\b"
        r"|\b(?=[0-9a-f]*\d)(?=[0-9a-f]*[a-f])[0-9a-f]{8,40}\b"
        r"|\bPhase[- ]?\d\w*\b|\bslice [A-Z0-9]\b|\bbatch \d+\b|\bWave[- ]?\d+\b|\bPR\d\b"
        r"|\bdogfood\w*|\bsession\b|\bnotebook\w*\b|\bround \d+\b"
        r"|\bthe owner (?:ruled|granted|said|asked|decided|approved|chose)\b"
        r"|\bowner's (?:ruling|decision|call)\b|\bthe audit\b|\baudited\b"
        r"|\b(?:reported|caught|discovered|noticed|found)\b",
        _I,
    ),
    "measurement": re.compile(
        r"\bmeasured\b|\bmeasurement\b|\bcensus\b|\binstrumented\b|\bbakeoff\b|\bcohort\b"
        r"|\b\d[\d,]* of (?:the |its |their )?\d[\d,]*\b|\b\d+(?:\.\d+)?%"
        r"|\b\d[\d,]* ?(?:->|→) ?\d[\d,]*\b"
        r"|\b(?:on|across|in) (?:the )?\d[\d,]* "
        r"(?:repos|repositories|surveys|maps|files|passes|runs)\b",
        _I,
    ),
    "numbers": re.compile(
        r"(?<![\w.\-#:])(?:[1-9]\d{0,2}(?:,\d{3})+|[1-9]\d{2,}|\d+\.\d*[1-9]\d*)(?![\w.\-])"
    ),
    "past_tense": re.compile(r"\b(?:was|were|had|did|became)\b", _I),
    "rule_voice": re.compile(
        r"\b(?:must|never|always|only|cannot|so that|because|otherwise|deliberately"
        r"|do not|does not|is not|are not|keeps?|ensures?|guarantees?)\b",
        _I,
    ),
}

#: Fitted log-odds per unit of density (matches per 100 words); see the module docstring.
WEIGHTS: dict[str, float] = {
    "change_narrative": 1.12,
    "provenance": 0.89,
    "measurement": 0.62,
    "numbers": 0.75,
    "past_tense": 0.40,
    "rule_voice": -0.25,
}
BIAS = -0.69
#: Density ceiling per family, so one repeated phrase cannot decide the score alone.
CAP = 20.0
#: A tracker or ADR ID is a pointer, not prose: it is not counted as a word, so
#: citing one neither dilutes nor adds to any family's density.
_TRACKER_ID = re.compile(r"\b(?:WI|INV|BUG|ADR)-[\w-]+")
#: A word holds at least one letter or digit; bare punctuation is not one.
_WORD = re.compile(r"\S*\w\S*")


@dataclass(frozen=True)
class Assessment:
    """One text's score: the probability, its word count, and what matched."""

    probability: float
    words: int
    matches: dict[str, tuple[str, ...]]


#: What ``--kind`` may select: comment blocks, docstrings, or both.
KINDS = ("comments", "docstrings", "all")


@dataclass(frozen=True)
class Block:
    """One scoreable block of Python source and the lines it spans.

    ``kind`` is ``"comment"`` for a run of consecutive comment-only lines
    (``#`` and leading space stripped) or ``"docstring"`` for one docstring
    (indentation cleaned, as ``inspect.cleandoc`` does).
    """

    start: int
    end: int
    text: str
    kind: str = "comment"


def assess(text: str) -> Assessment:
    """Score ``text``; empty text scores the prior, ``sigmoid(BIAS)``."""
    words = len(_WORD.findall(_TRACKER_ID.sub(" ", text)))
    matches: dict[str, tuple[str, ...]] = {}
    z = BIAS
    for family, pattern in FAMILIES.items():
        found = tuple(m.group(0) for m in pattern.finditer(text))
        if found:
            matches[family] = found
            z += WEIGHTS[family] * min(len(found) * 100.0 / max(words, 1), CAP)
    return Assessment(1.0 / (1.0 + math.exp(-z)), words, matches)


def probability(text: str) -> float:
    """The probability that ``text`` is substantially historical exegesis."""
    return assess(text).probability


_NOT_CODE = {tokenize.COMMENT, tokenize.NL, tokenize.NEWLINE, tokenize.INDENT,
             tokenize.DEDENT, tokenize.ENDMARKER}


def comment_blocks(source: str) -> list[Block]:
    """The comment-only blocks of Python ``source``, each a run of adjacent lines.

    A comment that shares its line with code is not comment-only, and a ``#``
    inside a string is not a comment; tokenising, not a line regex, is what
    tells those apart. Raises ``tokenize.TokenError`` on source it cannot read.
    """
    comments: dict[int, str] = {}
    code: set[int] = set()
    for tok in tokenize.generate_tokens(io.StringIO(source).readline):
        if tok.type == tokenize.COMMENT:
            comments[tok.start[0]] = tok.string
        elif tok.type not in _NOT_CODE:
            code.update(range(tok.start[0], tok.end[0] + 1))
    blocks: list[list[int]] = []
    for line in sorted(set(comments) - code):
        if blocks and line == blocks[-1][-1] + 1:
            blocks[-1].append(line)
        else:
            blocks.append([line])
    return [
        Block(b[0], b[-1], "\n".join(comments[n].lstrip("#:").strip() for n in b))
        for b in blocks
    ]


_HAS_DOCSTRING = (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)


def docstring_blocks(source: str) -> list[Block]:
    """The docstrings of Python ``source``: module, class and function, nested too.

    A docstring is a string literal and a comment is a separate token, so the
    two never share text: a ``#`` comment beside a docstring is not part of it,
    and a ``#`` line inside one (a heading, an example) is docstring text, not
    a comment. Only the FIRST statement of a body is its docstring; a later
    bare string is not. Raises ``SyntaxError`` or ``ValueError`` on source it
    cannot parse.
    """
    found = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, _HAS_DOCSTRING):
            text = ast.get_docstring(node, clean=True)
            if text is not None:
                literal = node.body[0]
                found.append(Block(literal.lineno, literal.end_lineno or literal.lineno,
                                   text, "docstring"))
    return sorted(found, key=lambda b: b.start)


def blocks_of(source: str, kind: str) -> list[Block]:
    """The blocks of ``source`` that ``kind`` (one of :data:`KINDS`) selects, in line order.

    ``all`` lists both kinds side by side, each scored on its own: merging a
    docstring with the comments around it would dilute a historical paragraph
    with the rules beside it, as scoring a whole file does.
    """
    chosen: list[Block] = []
    if kind in ("comments", "all"):
        chosen += comment_blocks(source)
    if kind in ("docstrings", "all"):
        chosen += docstring_blocks(source)
    return sorted(chosen, key=lambda b: b.start)


@dataclass(frozen=True)
class _Scored:
    """One scored input: a whole file or stdin, or one comment block of a file."""

    source: str
    assessment: Assessment
    block: Optional[Block] = None

    def as_json(self) -> dict[str, object]:
        where: dict[str, object] = {"source": self.source}
        if self.block is not None:
            where.update(start=self.block.start, end=self.block.end,
                         kind=self.block.kind, text=self.block.text)
        return {
            **where,
            "probability": round(self.assessment.probability, 4),
            "words": self.assessment.words,
            "matches": {f: list(m) for f, m in self.assessment.matches.items()},
        }

    def render(self) -> str:
        if self.block is not None:
            first = self.block.text.splitlines()[0][:100] if self.block.text else ""
            return (f"{self.assessment.probability:.2f}  "
                    f"{self.source}:{self.block.start}-{self.block.end}  "
                    f"{self.block.kind}  {first}")
        head = f"{self.assessment.probability:.2f}  {self.source}"
        detail = [f"  {f}: {', '.join(m)}" for f, m in self.assessment.matches.items()]
        return "\n".join([head, *detail])


class _Unreadable(Exception):
    """A named input could not be read or tokenised; the message names it."""


def _score(files: Sequence[str], kind: Optional[str]) -> Iterable[_Scored]:
    if not files:
        yield _Scored("<stdin>", assess(sys.stdin.read()))
        return
    for name in files:
        try:
            text = Path(name).read_text(encoding="utf-8")
            found = blocks_of(text, kind) if kind else []
        except (OSError, UnicodeDecodeError, tokenize.TokenError, SyntaxError,
                ValueError) as exc:
            raise _Unreadable(f"{name}: {exc}") from exc
        if not kind:
            yield _Scored(name, assess(text))
        for block in found:
            yield _Scored(name, assess(block.text), block)


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Estimate how likely text is substantially historical exegesis."
    )
    parser.add_argument("files", nargs="*", help="files to score (stdin when none)")
    parser.add_argument("--blocks", action="store_true",
                        help="treat each file as Python and score its blocks one by one "
                             "(comment blocks unless --kind says otherwise)")
    parser.add_argument("--kind", choices=KINDS,
                        help="which blocks to score: comments, docstrings or all; implies --blocks")
    parser.add_argument("--threshold", type=float, default=0.0,
                        help="list only results scoring at least this probability")
    parser.add_argument("--json", action="store_true", help="emit a JSON list")
    args = parser.parse_args(argv)
    try:
        kind = args.kind or ("comments" if args.blocks else None)
        scored = [s for s in _score(args.files, kind)
                  if s.assessment.probability >= args.threshold]
    except _Unreadable as exc:
        print(f"archaeology_heuristic: {exc}", file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps([s.as_json() for s in scored], indent=1, ensure_ascii=False))
    else:
        for s in scored:
            print(s.render())
    return 0


if __name__ == "__main__":
    sys.exit(main())
