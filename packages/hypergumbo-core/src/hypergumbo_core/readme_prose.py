# SPDX-License-Identifier: AGPL-3.0-or-later
"""Which README text can be a project's description, stated once.

The sketch opens with a one-paragraph description drawn from the README. Two
extractors find it: a heuristic (the first paragraph after the title) and an
embedding scorer that runs first whenever sentence-transformers is installed.
Each used to decide for itself which lines were candidates, and they
disagreed: the heuristic skipped every Markdown blockquote, the scorer skipped
none, so a callout such as ``> Requires Python 3.10+.`` came out as the last
sentence of the description, marker included, on every machine with the
embedding extra and on none without it.

Both extractors now read the README through :func:`description_lines`, so a
line is a candidate for both or for neither, and both results pass through
:func:`plain_inline`, so neither prints markup.

The rule
--------
A callout is an aside to the reader (a requirement, a warning, a pointer to
another document). It is not what the project is, however prominent. The
syntaxes recognised:

- Markdown blockquote: a run of lines starting with ``>``. A blockquote is a
  callout when prose comes before it. When it is the FIRST prose of the file
  it is the project's tagline (``> A SQL query builder that is flexible...``
  under a title and badges), and is kept with its markers removed. So the
  decision is made on where the block stands, never on its being quoted. A
  GitHub alert (``> [!NOTE]``) is a callout wherever it stands.
- reStructuredText: a directive (``.. note::``, ``.. warning::``,
  ``.. image::`` ...) with its indented body. No directive is description
  prose.
- AsciiDoc: an admonition paragraph (``NOTE: ...``, ``TIP:``, ``IMPORTANT:``,
  ``WARNING:``, ``CAUTION:``) and an admonition block (``[NOTE]`` followed by
  a ``====`` delimited block or a paragraph).

Not covered: Org-mode ``#+begin_quote`` and ``#+begin_note`` blocks, and
plain-text READMEs, which have no callout syntax to recognise.

A callout's lines are blanked, not removed, so line numbers and paragraph
breaks stay where the extractors expect them. Text inside a fenced code block
is left alone: neither extractor reads it as prose.
"""

from __future__ import annotations

import re
from collections.abc import Sequence

_FENCE = re.compile(r"^(`{3,}|~{3,})")
_QUOTE_MARKERS = re.compile(r"^\s*(?:>\s?)+")
_GITHUB_ALERT = re.compile(r"^\[!\w+\]")
_RST_DIRECTIVE = re.compile(r"^\.\.\s+[\w-]+::")
_ADMONITION = r"(?:NOTE|TIP|IMPORTANT|WARNING|CAUTION)"
_ADOC_PARAGRAPH = re.compile(rf"^{_ADMONITION}:\s")
_ADOC_BLOCK = re.compile(rf"^\[{_ADMONITION}\]$")
_ADOC_DELIMITER = re.compile(r"^={4,}$")
_UNDERLINE = re.compile(r"^[=\-~^]{3,}$")
_LINK_ONLY = re.compile(r"^!?\[.*\]\([^)]*\)$")
_LINK_DEFINITION = re.compile(r"^\[[^\]]+\]:\s*\S+")


def _is_prose(lines: Sequence[str], index: int) -> bool:
    """Whether ``lines[index]`` is running text a description could start
    with: not blank, a heading, a badge, an image, markup or a link target."""
    text = lines[index].strip()
    if not text or text.startswith(("#", "<", "![", "[![")):
        return False
    if _UNDERLINE.match(text) or _LINK_ONLY.match(text) or _LINK_DEFINITION.match(text):
        return False
    # A setext or reStructuredText title is text with an underline below it.
    following = lines[index + 1].strip() if index + 1 < len(lines) else ""
    return not _UNDERLINE.match(following)


def _paragraph_end(lines: Sequence[str], start: int) -> int:
    """Index of the first blank line at or after ``start`` (or the end)."""
    end = start
    while end < len(lines) and lines[end].strip():
        end += 1
    return end


def description_lines(lines: Sequence[str]) -> list[str]:
    """``lines`` with every callout blanked and a leading blockquote unquoted.

    The result has one entry per input line. See the module docstring for
    the rule.
    """
    out = list(lines)
    seen_prose = False
    i = 0
    while i < len(lines):
        text = lines[i].strip()
        end = i + 1
        blank = False

        fence = _FENCE.match(text)
        if fence:
            while end < len(lines) and not lines[end].strip().startswith(fence.group(1)):
                end += 1
            i = end + 1
            continue

        if text.startswith(">"):
            while end < len(lines) and lines[end].strip().startswith(">"):
                end += 1
            body = [_QUOTE_MARKERS.sub("", lines[k]) for k in range(i, end)]
            said = [b.strip() for b in body if b.strip()]
            if seen_prose or not said or _GITHUB_ALERT.match(said[0]):
                blank = True
            else:
                out[i:end] = body
                seen_prose = True
        elif _RST_DIRECTIVE.match(text):
            while end < len(lines) and (not lines[end].strip() or lines[end][:1] in " \t"):
                end += 1
            blank = True
        elif _ADOC_PARAGRAPH.match(text):
            end = _paragraph_end(lines, i)
            blank = True
        elif _ADOC_BLOCK.match(text):
            if end < len(lines) and _ADOC_DELIMITER.match(lines[end].strip()):
                end += 1
                while end < len(lines) and not _ADOC_DELIMITER.match(lines[end].strip()):
                    end += 1
                end = min(end + 1, len(lines))
            else:
                end = _paragraph_end(lines, i)
            blank = True
        elif _is_prose(lines, i):
            seen_prose = True

        if blank:
            out[i:end] = [""] * (end - i)
        i = end
    return out


_IMAGE = re.compile(r"!\[[^\]]*\]\([^)]*\)")
_LINK = re.compile(r"\[([^\]]+)\]\([^)]*\)")
_REFERENCE_LINK = re.compile(r"\[([^\]]+)\]\[[^\]]*\]")
_STRONG = re.compile(r"(\*\*|__)(?=\S)(.+?)(?<=\S)\1")
_STAR_EMPHASIS = re.compile(r"(?<![\w*])\*(?=[^\s*])(.+?)(?<=[^\s*])\*(?![\w*])")
_UNDERSCORE_EMPHASIS = re.compile(r"(?<!\w)_(?=[^\s_])(.+?)(?<=[^\s_])_(?!\w)")


def plain_inline(text: str) -> str:
    """``text`` with Markdown inline markup resolved to what it displays.

    A link becomes its text, an image nothing, emphasis its content. Code
    spans keep their backticks: the sketch is Markdown and a name in
    backticks is still a name. An underscore inside a word (``snake_case``)
    is not emphasis and stays.
    """
    text = _IMAGE.sub("", text)
    text = _LINK.sub(r"\1", text)
    text = _REFERENCE_LINK.sub(r"\1", text)
    text = _STRONG.sub(r"\2", text)
    text = _STAR_EMPHASIS.sub(r"\1", text)
    text = _UNDERSCORE_EMPHASIS.sub(r"\1", text)
    return re.sub(r"[ \t]{2,}", " ", text).strip()
