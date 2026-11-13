# SPDX-License-Identifier: AGPL-3.0-or-later
"""Order non-source files by how likely they are to explain the project.

The sketch's Additional Files section is filled from three lists taken in
turn: the files the README links to, this order, and the files that mention
the most central symbols. This module is the middle one.

How it works
------------
``rank_overview_files`` sorts by, in order:

1. **Role.** Documentation files before configuration files
   (``taxonomy.get_role``).
2. **Depth.** Fewer directories between the file and the repository root
   first.
3. **Overview wording.** Within one role and depth, the BM25 score of the
   file's first ``HEAD_CHARS`` characters (markup tags removed) against
   ``sketch_embeddings.ADDITIONAL_FILES_PROBES``: three paragraphs of generic
   "who is this for / what does it do / how does it work" prose. Document
   frequencies are counted over the files being ranked.
4. **Path**, so the order is total and the same on every run.

Why this design
---------------
The middle list used to be an embedding ranking: every candidate file was
run through a sentence-embedding model and scored by cosine similarity to
the same three paragraphs. Measured on 2026-11-13 (WI-patof; scripts and
results in ``~/hypergumbo_lab_notebook/patof_embedding_prefilter_11132026``):

- **Cost.** About a quarter of a second per candidate on six CPU cores,
  whatever the file's length: 25 s for 153 files, 115 s for 451, 239 s for
  958, to fill at most half of an eight-entry list.
- **It did not reproduce itself.** The model saw three 400-character windows
  of each file at offsets seeded by the file's path. With a different seed,
  the top four kept 3 of 4 files on three repositories and 0 or 1 of 4 on
  this one. Two byte-identical READMEs scored 0.717 and 0.678, ranks 1 and
  21 of 451.
- **It did not find the files authors point to.** Taking the files each
  repository's own README links to as the test, over 46 held-out
  repositories (233 linked files) the order in this module puts 72% of them
  in a repository's top ten; BM25 on content alone 53%; a shuffled list 24%.
  On the six repositories where the embedding was run, it put 4 of 37 linked
  files in the top ten, where role and depth alone put 13.

Where a file sits and what kind of file it is carry the signal; what the
text sounds like is at most a tie-break. Content similarity seeded from 630
real linked documents (258 repositories) instead of the three paragraphs
scored the same as the paragraphs, so no learned table is shipped. A table
of file-name weights learned from the same links (``roadmap``,
``contributing``, ``adopters``...) scored slightly higher on the development
split and the same on the held-out one, and was not adopted: it rewards the
names READMEs link to, which is not the same as the files that explain the
code.

No model is loaded and nothing is cached: the whole order is a few
milliseconds of reading file heads.
"""

from __future__ import annotations

import math
import re
from collections import Counter
from pathlib import Path
from typing import Sequence

from .sketch_embeddings import ADDITIONAL_FILES_PROBES
from .taxonomy import FileRole, get_role

#: Characters read from the start of each file. An overview says what it is
#: near the top; reading more made the measured order no better.
HEAD_CHARS = 4000

#: BM25's usual constants (term-frequency saturation, length normalisation).
_K1 = 1.2
_B = 0.75

#: A word is two or more letters in any script: no digits, no underscores,
#: so identifiers and version numbers in a config file are not "wording".
_WORD = re.compile(r"[^\W\d_]{2,}", re.UNICODE)
_TAG = re.compile(r"<[^>]+>")


def _words(text: str) -> list[str]:
    return _WORD.findall(text.lower())


_QUERY: Counter[str] = Counter(
    word for probe in ADDITIONAL_FILES_PROBES for word in _words(probe)
)


def _head_words(path: Path) -> Counter[str]:
    """The words in the first ``HEAD_CHARS`` characters of ``path``.

    An unreadable file has no words and so no wording score; its role and
    depth still place it.
    """
    try:
        with path.open(encoding="utf-8", errors="replace") as handle:
            text = handle.read(HEAD_CHARS)
    except OSError:
        return Counter()
    return Counter(_words(_TAG.sub(" ", text)))


def overview_wording_scores(heads: Sequence[Counter[str]]) -> list[float]:
    """BM25 of each head against the probe paragraphs.

    A term's weight comes from how many of ``heads`` contain it, so a word
    every candidate uses says nothing and a word few use says a lot. The
    scores are comparable only within one call.
    """
    total = len(heads)
    if not total:
        return []
    document_frequency: Counter[str] = Counter()
    for head in heads:
        document_frequency.update(head.keys())
    lengths = [sum(head.values()) for head in heads]
    average = (sum(lengths) / total) or 1.0
    idf = {
        term: math.log(1 + (total - count + 0.5) / (count + 0.5))
        for term, count in document_frequency.items()
        if term in _QUERY
    }
    scores = []
    for head, length in zip(heads, lengths, strict=True):
        norm = _K1 * (1 - _B + _B * length / average)
        scores.append(sum(
            _QUERY[term] * weight * head[term] * (_K1 + 1) / (head[term] + norm)
            for term, weight in idf.items()
            if term in head
        ))
    return scores


def rank_overview_files(files: Sequence[Path], repo_root: Path) -> list[Path]:
    """``files`` ordered most-likely-to-explain-the-project first.

    Every file is returned exactly once. Files outside ``repo_root`` are
    ordered by their full path, as the deepest.
    """
    keyed = []
    wording = overview_wording_scores([_head_words(path) for path in files])
    for path, score in zip(files, wording, strict=True):
        try:
            relative = path.relative_to(repo_root)
        except ValueError:
            relative = path
        keyed.append((
            get_role(path) != FileRole.DOCUMENTATION,
            len(relative.parts) - 1,
            -score,
            relative.as_posix(),
            path,
        ))
    keyed.sort(key=lambda item: item[:4])
    return [item[4] for item in keyed]
