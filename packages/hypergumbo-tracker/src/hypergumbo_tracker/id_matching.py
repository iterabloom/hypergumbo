# SPDX-License-Identifier: MPL-2.0
"""Detect tracker item IDs embedded in free-text panes (descriptions, activity).

The tracker TUI needs to turn substrings that look like item IDs into
clickable hotspots inside the Description and Activity panes (WI-sulij).
The pattern must respect the *configured* set of kind prefixes (not a
hardcoded allowlist) so custom kinds ship with the feature for free.

An ID has the shape::

    <prefix>-<proquint>[-<proquint>...]

where ``<prefix>`` is drawn from ``TrackerConfig.kinds`` (e.g. ``WI``,
``INV``, ``META`` in the default config, but any uppercase-ish token is
legal) and each ``<proquint>`` is a five-character consonant-vowel-
consonant-vowel-consonant (CVCVC) syllable using the standard proquint
alphabet (consonants ``bcdfghjklmnpqrstvz``, vowels ``aeiou``). Current
tracker IDs use 8 syllables (derived from the first 128 bits of a
SHA-256 hash via ``proquint.uint2quint`` — see
``store._compute_id``), but historical variants may have fewer, so the
detector matches ``one proquint followed by one or more hyphen-separated
proquints`` (i.e. ≥2 syllables total), which is the minimum that cannot
be confused with a plain English word fragment.

The regex is built once at TUI startup (or on config reload) and used
lazily: the TUI scans only the currently-visible lines, not the full
corpus, so this module returns match objects with positional info the
caller can turn into hotspot spans.

Consumers:

- :func:`build_item_id_pattern` — turns a ``TrackerConfig`` into a
  compiled ``re.Pattern`` bound to that config's kinds.
- :func:`find_item_ids` — enumerate non-overlapping matches in a string,
  skipping matches that collide with an existing hotspot range.

Both functions are pure and do not touch the filesystem or the store.
"""

from __future__ import annotations

import re
from typing import Iterable, NamedTuple

from .models import TrackerConfig

_PROQUINT_CONSONANTS = "bcdfghjklmnpqrstvz"
_PROQUINT_VOWELS = "aeiou"

# A single proquint syllable: consonant-vowel-consonant-vowel-consonant.
_PROQUINT_SYLLABLE = (
    f"[{_PROQUINT_CONSONANTS}][{_PROQUINT_VOWELS}]"
    f"[{_PROQUINT_CONSONANTS}][{_PROQUINT_VOWELS}]"
    f"[{_PROQUINT_CONSONANTS}]"
)


class ItemIdMatch(NamedTuple):
    """A detected item ID occurrence in a text buffer.

    ``start`` and ``end`` are character offsets into the text that was
    scanned (Python slice semantics: ``text[start:end] == item_id``).
    """

    item_id: str
    start: int
    end: int


def build_item_id_pattern(config: TrackerConfig) -> re.Pattern[str]:
    """Compile a regex that matches any configured kind's ID shape.

    The returned pattern matches whole-token item IDs only: the leading
    prefix must not be preceded by an alphanumeric character (so
    ``aWI-foo-bar`` does not match), and the trailing syllable must not
    be followed by one (so ``WI-foo-bara`` — six CVCVC chars after the
    last hyphen — does not match, because the final ``a`` would have to
    be part of the syllable). A hyphen to either side is fine so inline
    cross-references like ``fix-WI-foo-bar-x`` still detect ``WI-foo-bar``
    (the trailing ``-x`` is rejected because ``x`` is a single consonant,
    not a full syllable).

    Raises ``ValueError`` if the config has no kinds — the TUI cannot
    sensibly scan for IDs without at least one prefix.
    """
    prefixes = sorted({k.prefix for k in config.kinds.values() if k.prefix})
    if not prefixes:
        raise ValueError("TrackerConfig has no configured kinds with prefixes")

    prefix_group = "|".join(re.escape(p) for p in prefixes)
    # (?<![A-Za-z0-9]) at the front anchors the prefix to a word-ish
    # boundary that still lets a leading hyphen through. We rely on the
    # same boundary at the tail, expressed via a negated look-ahead.
    pattern = (
        rf"(?<![A-Za-z0-9])"
        rf"(?:{prefix_group})"
        rf"(?:-{_PROQUINT_SYLLABLE}){{2,}}"
        rf"(?![A-Za-z0-9])"
    )
    return re.compile(pattern)


def find_item_ids(
    text: str,
    pattern: re.Pattern[str],
    skip_ranges: Iterable[tuple[int, int]] = (),
) -> list[ItemIdMatch]:
    """Return non-overlapping matches of *pattern* in *text*.

    ``skip_ranges`` is an optional iterable of ``(start, end)`` pairs
    identifying offsets that should not be turned into hotspots (e.g.
    already-linked regions, or regions inside code spans that the TUI
    renders differently). A match whose span intersects any skip range
    is dropped. The list is returned in document order.
    """
    skip = list(skip_ranges)
    out: list[ItemIdMatch] = []
    for m in pattern.finditer(text):
        s, e = m.start(), m.end()
        if any(not (e <= rs or s >= re_) for rs, re_ in skip):
            continue
        out.append(ItemIdMatch(item_id=m.group(0), start=s, end=e))
    return out
