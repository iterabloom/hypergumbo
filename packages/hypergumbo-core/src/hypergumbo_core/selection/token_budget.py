# SPDX-License-Identifier: AGPL-3.0-or-later
"""Token estimation and budget management for LLM-aware output.

This module provides utilities for estimating token counts and managing
token budgets across different output formats (Markdown sketches, JSON
behavior maps, tiered output).

How It Works
------------
Token estimation uses a simple character-based heuristic: approximately
4 characters per token. This is a reasonable approximation for English
text with most tokenizers (GPT, Claude, etc.).

estimate_tokens uses ceiling division (a conservative estimate, to avoid
exceeding budgets). estimate_json_tokens serializes with json.dumps
(default separators, no indentation) and uses floor division on the
character count.

Two JSON forms, two estimators. A survey artifact is WRITTEN with
``indent=2, sort_keys=True`` (``safety_zones.user_out_open_json_dump``), a
form about a quarter larger than the default dump. A budget that describes
a file is checked with ``estimate_written_json_tokens``, which sizes
``written_json_text``: the text the writer produces. ``estimate_json_tokens``
is for sizing a record relative to other records, where the form cancels
out. Checking a file's budget with it let a tier pass the check and exceed
its budget on disk (INV-nukuk).

The truncation logic for Markdown is section-aware: it attempts to cut
at ## header boundaries to keep headers with their content, avoiding
orphaned headers.

Why This Design
---------------
- Character-based estimation is fast and doesn't require tokenizer deps
- ~4 chars/token is well-validated empirically for mixed code/English
- Section-aware truncation produces cleaner output for LLM consumption
- Shared constants ensure consistency across sketch/compact/tiered modes
"""

from __future__ import annotations

import json
import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    pass

# Token estimation constant
# ~4 chars per token is a reasonable approximation for mixed code/English
CHARS_PER_TOKEN = 4

# Overhead constants for JSON behavior maps
TOKENS_PER_NODE_OVERHEAD = 50  # JSON structure per node
TOKENS_BEHAVIOR_MAP_OVERHEAD = 200  # Shell overhead (schema, view, metrics)

# Default tier specifications
DEFAULT_TIERS = ["4k", "16k", "64k"]


def estimate_tokens(text: str) -> int:
    """Estimate token count using character-based heuristic.

    Uses ~4 characters per token, which is a reasonable approximation
    for English text with most tokenizers. Uses ceiling division
    to be conservative and avoid exceeding budgets.

    Args:
        text: The text to estimate tokens for.

    Returns:
        Estimated token count (conservative/ceiling estimate).
    """
    if not text:
        return 0
    # Use ceiling division for conservative estimate
    return max(1, (len(text) + CHARS_PER_TOKEN - 1) // CHARS_PER_TOKEN)


def estimate_json_tokens(data: dict) -> int:
    """Estimate tokens for a JSON-serializable dictionary.

    Serializes the data to JSON and counts characters.

    Args:
        data: Dictionary to estimate tokens for.

    Returns:
        Estimated token count.
    """
    json_str = json.dumps(data)
    return len(json_str) // CHARS_PER_TOKEN


def written_json_text(data: object) -> str:
    """The text a survey artifact is written as.

    ``safety_zones.user_out_open_json_dump`` (and its gzip sibling) write
    with exactly these arguments; ``test_tier_budget_written.py`` writes a
    file through the real writer and compares, so the two cannot drift
    apart unnoticed.
    """
    return json.dumps(data, indent=2, sort_keys=True)


def estimate_written_json_tokens(data: object) -> int:
    """Estimate the tokens of ``data`` as a file on disk (floor division).

    ``ensure_ascii`` is the default in both this and the writer, so the
    character count is the byte count.
    """
    return len(written_json_text(data)) // CHARS_PER_TOKEN


_FENCE = re.compile(r"^ {0,3}(```|~~~)", re.MULTILINE)


def _fenced_spans(text: str) -> list[tuple[int, int]]:
    """Character ranges of the fenced code blocks in ``text``.

    A fence opens on a line starting with three backticks or tildes and
    closes on the next line starting with the same marker; one left open
    runs to the end of the text.
    """
    spans: list[tuple[int, int]] = []
    opened: int | None = None
    marker = ""
    for match in _FENCE.finditer(text):
        if opened is None:
            opened, marker = match.start(), match.group(1)
        elif match.group(1) == marker:
            spans.append((opened, match.end()))
            opened = None
    if opened is not None:
        spans.append((opened, len(text)))
    return spans


def _truncate_at_paragraph(text: str, max_chars: int, fences: list[tuple[int, int]]) -> str:
    """``text`` up to the last blank line that fits and is not inside a
    fenced code block; a hard cut when there is none."""
    cut = -1
    at = text.find("\n\n")
    while at != -1 and at + 2 <= max_chars:
        if not any(start < at < end for start, end in fences):
            cut = at
        at = text.find("\n\n", at + 2)
    return text[:cut] if cut > 0 else text[:max_chars]


def truncate_to_tokens(text: str, max_tokens: int) -> str:
    """Truncate text to approximately fit within token budget.

    Attempts to truncate at markdown section boundaries (## headers) to
    keep headers with their content. Avoids orphaned headers like
    "## Entry Points" appearing without their content.

    When no markdown sections are found, falls back to paragraph splitting.
    As a last resort, performs hard truncation.

    A line inside a fenced code block is neither a section header nor a
    paragraph break (WI-papup). A README that shows example output holds
    ``## `` lines that are text of the example; taking them for headers cut
    the README in the middle of its own example and left the fence open.

    Args:
        text: The text to truncate.
        max_tokens: Maximum tokens allowed.

    Returns:
        Truncated text fitting within budget.
    """
    if estimate_tokens(text) <= max_tokens:
        return text

    # Target character count
    max_chars = max_tokens * CHARS_PER_TOKEN
    fences = _fenced_spans(text)

    # Find all section starts (lines beginning with ## ) outside code fences
    section_pattern = re.compile(r"^(## .+)$", re.MULTILINE)
    section_starts = [
        (m.start(), m.group(1)) for m in section_pattern.finditer(text)
        if not any(start < m.start() < end for start, end in fences)
    ]

    if not section_starts:
        # No markdown sections, fall back to paragraph splitting
        return _truncate_at_paragraph(text, max_chars, fences)

    # Extract sections (each section is header + content until next header)
    sections = []
    for i, (start, _header) in enumerate(section_starts):
        if i + 1 < len(section_starts):
            end = section_starts[i + 1][0]
        else:
            end = len(text)
        sections.append(text[start:end].rstrip())

    # Include any content before the first section (like the title). A
    # preamble longer than the budget is cut like a text with no sections;
    # it used to be returned whole, over the budget.
    prefix = text[: section_starts[0][0]].rstrip() if section_starts[0][0] > 0 else ""
    if len(prefix) > max_chars:
        return _truncate_at_paragraph(prefix, max_chars, fences)

    # Build result keeping whole sections
    result_parts = [prefix] if prefix else []
    current_length = len(prefix) + 2 if prefix else 0

    for section in sections:
        section_with_sep = section + "\n\n"
        if current_length + len(section_with_sep) <= max_chars:
            result_parts.append(section)
            current_length += len(section_with_sep)
        else:
            # Can't fit this section, stop here
            break

    if result_parts:
        return "\n\n".join(result_parts)

    # Fallback: hard truncate if nothing fits
    return text[:max_chars]  # pragma: no cover - defensive path


def parse_tier_spec(spec: str) -> int:
    """Parse a tier specification into target tokens.

    Supports formats like "4k", "16K", "64000", "1.5k".

    Args:
        spec: Tier spec like "4k", "16k", "64000", etc.

    Returns:
        Target token count.

    Raises:
        ValueError: If spec cannot be parsed.
    """
    spec = spec.lower().strip()
    if spec.endswith("k"):
        return int(float(spec[:-1]) * 1000)
    return int(spec)
