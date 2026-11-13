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
   ``OVERVIEW_PROBES``: six paragraphs of generic "who is this for / what
   does it do / when, where, why, how" prose. Document frequencies are counted
   over the files being ranked.
4. **Path**, so the order is total and the same on every run.

Why this design
---------------
The middle list used to be an embedding ranking: every candidate file was
run through a sentence-embedding model and scored by cosine similarity to
the same six paragraphs. Measured on 2026-11-13 (WI-patof; scripts and
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
real linked documents (258 repositories) instead of the six paragraphs
scored the same as the paragraphs, so no learned table is shipped. A table
of file-name weights learned from the same links (``roadmap``,
``contributing``, ``adopters``...) scored slightly higher on the development
split and the same on the held-out one, and was not adopted: it rewards the
names READMEs link to, which is not the same as the files that explain the
code.

No model is loaded and nothing is cached: the whole order is a few
milliseconds of reading file heads. The embedding code, its per-file cache
and the second model ``install-embeddings`` fetched for it were removed with
the change; the probe paragraphs are the one thing kept from it.
"""

from __future__ import annotations

import math
import re
from collections import Counter
from pathlib import Path
from typing import Sequence

from .taxonomy import FileRole, get_role

#: What an overview sounds like: who the project is for, what it does, when
#: to use it, where it comes from, why it was built, how it works. Written as generic prose so that no project's own vocabulary is
#: in it. They are a BM25 query, so a wording change shifts scores a little
#: and needs nothing regenerated.
OVERVIEW_PROBES: tuple[str, ...] = (
    # Who this thing is for
    (
        "Who this thing is for This project is for people who want a clear, "
        "dependable tool that does one job well, and stays out of the way while "
        "you get on with your work. If you're the kind of person who reads a "
        "README before installing anything, you'll feel at home here. If you're "
        "the kind of person who doesn't, that's fine too: the defaults are "
        "designed to be sensible, the setup aims to be painless, and the 'happy "
        "path' should take you from zero to useful without a scavenger hunt "
        "through configuration files. It's for builders: developers wiring this "
        "into an app, scripting it into a workflow, or integrating it into CI. "
        "It's for maintainers who care about predictable behavior, stable "
        "interfaces, and changes that are explained rather than hand-waved. It's "
        "for curious tinkerers who like to poke at source code, file issues, "
        "propose improvements, or just understand how things work under the hood. "
        "It's also for teams. If you need something that can be documented, "
        "reviewed, tested, and shared without a private onboarding ritual, you're "
        "in the right place. And if you're new to the ecosystem, don't worry: the "
        "docs assume intelligence, not prior knowledge. You'll find examples, "
        "explanations, and reference material aimed at helping you succeed whether "
        "you're experimenting on a weekend or shipping on a deadline. If you want "
        "a tool that respects your time, welcomes contributions, and tries hard to "
        "be boring in production (in the best way), this thing is for you."
    ),
    # What this thing does
    (
        "This project exists to do one thing well: it does what it says it does - "
        "and then gets out of your way. At its core, it's a small, focused tool "
        "that takes an input (whatever 'input' means in your environment), applies "
        "a clear set of rules, and produces an output you can rely on. You can "
        "think of it as a dependable middle layer: it translates intent into "
        "action, turns repetitive steps into a single command, and makes the "
        "common case fast while keeping the uncommon case possible. The 'thing' "
        "here is intentionally general because the shape of your problem might be "
        "different from someone else's. Sometimes that means transforming data, "
        "sometimes orchestrating a workflow, sometimes smoothing over rough edges "
        "between systems that don't naturally fit together. In every case, the "
        "goal is the same: reduce friction, increase consistency, and provide a "
        "simple interface that feels obvious after you've used it once. It's "
        "designed to be practical rather than precious. You should be able to drop "
        "it into an existing setup, configure only what you need, and extend it "
        "when your requirements grow. If you're skimming this documentation, the "
        "takeaway is simple: this tool helps you get from 'I want this done' to "
        "'it's done' with fewer moving parts, fewer surprises, and more control "
        "over the details that matter to you."
    ),
    # When to use this
    (
        "Use this component when you want a small, dependable 'building block' "
        "that does one job well and fits cleanly into a larger system. It's a "
        "good choice in situations where you value clarity over cleverness: you "
        "need behavior that's easy to understand, easy to test, and unlikely to "
        "surprise future readers of your code. If you're looking for something "
        "that can be adopted incrementally - dropped into an existing project "
        "without forcing a redesign - this is an appropriate place to start. This "
        "tool shines when the surrounding requirements are stable or at least "
        "well-bounded. If you can describe the problem in a few sentences and "
        "you'd prefer a straightforward configuration or API surface, you'll "
        "likely find it productive. It's also well-suited for teams: conventions "
        "are explicit, defaults are sensible, and common workflows are documented "
        "so new contributors can get traction quickly. In other words, reach for "
        "it when you want to move fast without creating long-term ambiguity. Avoid "
        "using it when you need heavy customization, unusual edge-case behavior, "
        "or an experimental approach that's still changing week to week. In those "
        "cases, you may be better served by a lower-level primitive or a more "
        "flexible framework. But for most day-to-day tasks - reliable integration, "
        "repeatable outcomes, and maintainable code - this is a solid, practical "
        "option."
    ),
    # Where this comes from
    (
        "Where this thing comes from Every project has an origin story, even if "
        "it starts out as a single line on a sticky note or a half-remembered idea "
        "from a late-night debugging session. This section is the place where we "
        "trace the roots of 'this thing': not as a dramatic tale of destiny, but "
        "as a practical account of why it exists, what problems it was meant to "
        "address, and how its earliest assumptions shaped what you're holding "
        "today. Sometimes a tool appears because a gap kept showing up in real "
        "work - an awkward workflow, a recurring edge case, a missing layer of "
        "glue between two systems that otherwise behave nicely. Sometimes it's "
        "born from curiosity: a desire to see if an approach could be made "
        "simpler, faster, more transparent, or just easier to reuse. And sometimes "
        "the origin is less tidy: a pile of scripts that slowly grew legs, "
        "accumulated tests, acquired a name, and eventually demanded to be treated "
        "like a real project. In open source, provenance matters. Knowing where "
        "something comes from helps you understand its defaults, its trade-offs, "
        "and the kind of contributions that fit its trajectory. It can explain why "
        "certain features are emphasized, why certain decisions are conservative "
        "or bold, and why the project's language and structure look the way they "
        "do. Think of this as the context layer: a map of the initial constraints, "
        "the early use cases, and the motivations that continue to echo through "
        "the codebase. If you're new here, this is your orientation. If you've "
        "been around for a while, it's a reminder of the thread that ties today's "
        "implementation to yesterday's need."
    ),
    # Why this was built
    (
        "Why this thing was built - because a gap showed up, and it kept showing "
        "up. We had a workflow that looked fine on paper: a few scripts here, a "
        "manual checklist there, a half-dozen conventions that lived in someone's "
        "head. It worked until it didn't. Every new contributor had to rediscover "
        "the same sharp edges. Every deployment carried a small, unnecessary "
        "gamble. Every integration required a bespoke fix, and every 'temporary' "
        "workaround became permanent infrastructure. The cost wasn't dramatic in "
        "any single moment; it was the slow accumulation of friction: time lost to "
        "repetition, confidence lost to ambiguity, and opportunities lost because "
        "change felt risky. So this project exists to make the common path the "
        "easy path. It aims to turn tribal knowledge into documented behavior, and "
        "scattered one-off solutions into something coherent and reusable. It is "
        "intentionally general: useful in small prototypes and large systems, in "
        "local development and automated pipelines, in hobby projects and "
        "production services. You should be able to pick it up with minimal "
        "context, apply it to your own constraints, and extend it without "
        "rewriting the world. In short, it was built to reduce surprise. To "
        "replace 'hope it works' with 'we know why it works.' To offer a solid "
        "default, a clear surface area, and an approach that scales with your "
        "needs rather than fighting them."
    ),
    # How this works
    (
        "How this thing works is deliberately simple at the surface, and carefully "
        "engineered underneath. You point it at some input, you tell it what you "
        "want, and it produces an output you can inspect, reuse, or wire into "
        "something larger. There are no secret handshakes: the core behavior is "
        "exposed through a small set of commands and a predictable configuration "
        "layer, so you can get started quickly and still have room to grow into "
        "the deeper features. Conceptually, the system is a pipeline. Data enters "
        "through an adapter that normalizes formats, validates assumptions, and "
        "attaches a bit of metadata so later stages can make good decisions. From "
        "there, a runtime coordinates the actual work: it resolves dependencies, "
        "schedules tasks in the right order, and applies the selected options in a "
        "consistent way. Each step emits clear signals - logs, exit codes, and "
        "structured output - so you can debug problems without guessing. The "
        "important part is that every piece is replaceable. If you don't like the "
        "default parser, swap it. If you need different output, add a formatter. "
        "If your environment is unusual, provide your own transport or storage "
        "backend. Extensions follow the same rules as built-ins: small interfaces, "
        "stable contracts, and failure modes that are explicit rather than "
        "surprising. In short: it takes your intent, turns it into a plan, "
        "executes that plan reliably, and leaves an audit trail you can trust."
    ),
)

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
    word for probe in OVERVIEW_PROBES for word in _words(probe)
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
