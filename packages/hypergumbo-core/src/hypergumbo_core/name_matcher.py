# SPDX-License-Identifier: AGPL-3.0-or-later
"""Name-form normalization at matcher boundaries (Level 2 of WI-zigah).

## Why

The analyzer stores symbol metadata — decorators, base_classes, annotations — as
source-form strings. The same canonical entity can be spelled several ways
depending on how it was imported:

- ``from pydantic import BaseModel`` → meta string ``"BaseModel"``
- ``import pydantic`` → ``"pydantic.BaseModel"``
- ``import pydantic as p`` → ``"p.BaseModel"`` (after alias canonicalization,
  analyzers typically resolve this to ``"pydantic.BaseModel"`` — but not
  always, and not across all languages).

Downstream matchers compare these meta strings against canonical-path keys
from YAML framework patterns or catalog entries. Raw ``re.match`` against a
fixed regex misses whichever spelling the pattern did not anticipate —
silent false negatives with wide blast radius (framework concept tagging,
linker target resolution, io-boundary tagging).

## How

``NameMatcher`` wraps a pattern string and provides ``matches(source) -> bool``.
Two modes, chosen by inspecting the pattern:

- **Canonical mode** — pattern is alphanumeric + dots only. The matcher
  splits both sides on ``.`` and checks that one segment list is a suffix of
  the other. ``pydantic.BaseModel`` and ``BaseModel`` match; ``pydantic.BaseModel``
  and ``django.db.models.Model`` do not.
- **Regex mode** — pattern contains any regex metacharacter. The compiled
  regex is tried against the raw source string first, then against the
  terminal dotted segment as a fallback (so an anchored pattern like
  ``^get$`` still matches a source spelling of ``app.get``). Callers that
  need capture groups can call ``match(source)`` for an ``re.Match`` or
  ``None``.

Regex-mode **canonicalization is asymmetric**: we can strip the source
prefix, but we cannot safely strip the pattern prefix (the pattern might
rely on it via an alternation or anchor we don't see). That's a known
limitation of wrapping the existing regex corpus — Level 3 (structured
canonical tuples) is the symmetric fix.
"""

import re

__all__ = ["NameMatcher"]

_REGEX_SPECIALS = frozenset(r"^$()|[]*+?\{}")


def _is_regex_pattern(pattern: str) -> bool:
    """Heuristic: pattern contains any regex metacharacter.

    Plain dotted names (``pydantic.BaseModel``, ``os.path.join``) are the
    canonical-mode case. Anything with anchors, alternation, character
    classes, or escapes is regex mode.
    """
    return any(c in _REGEX_SPECIALS for c in pattern)


class NameMatcher:
    """Match a source-code name spelling against a canonical pattern.

    See module docstring for the canonical/regex mode split and the
    asymmetry caveat in regex mode.
    """

    __slots__ = ("_mode", "_regex", "_segments", "raw")

    def __init__(self, pattern: str):
        self.raw = pattern
        if _is_regex_pattern(pattern):
            self._mode = "regex"
            self._regex: re.Pattern[str] | None = re.compile(pattern)
            self._segments: tuple[str, ...] | None = None
        else:
            self._mode = "canonical"
            self._regex = None
            self._segments = tuple(pattern.split("."))

    def matches(self, source_name: str) -> bool:
        """Return True if ``source_name`` denotes the same entity as the pattern."""
        if self._mode == "regex":
            regex = self._regex
            assert regex is not None  # type narrow
            if regex.search(source_name):
                return True
            # Terminal-segment fallback — covers pattern=bare, source=qualified
            terminal = source_name.rsplit(".", 1)[-1]
            if terminal != source_name:
                return regex.search(terminal) is not None
            return False

        # Canonical mode: one segment list must be a suffix of the other.
        segments = self._segments
        assert segments is not None
        src_segments = tuple(source_name.split("."))
        if len(src_segments) <= len(segments):
            return segments[-len(src_segments):] == src_segments
        return src_segments[-len(segments):] == segments

    def match(self, source_name: str) -> re.Match[str] | None:
        """Return the regex ``Match`` object (regex mode only).

        Callers using regex capture groups (e.g., framework_patterns decorator
        HTTP-method extraction) call this instead of ``matches``. Tries the
        raw source first, then the terminal segment.

        In canonical mode returns ``None`` — there is no regex to produce a
        Match object. Callers that need groups must use regex mode.
        """
        regex = self._regex
        if regex is None:
            return None
        m = regex.match(source_name)
        if m is not None:
            return m
        terminal = source_name.rsplit(".", 1)[-1]
        if terminal != source_name:
            return regex.match(terminal)
        return None
