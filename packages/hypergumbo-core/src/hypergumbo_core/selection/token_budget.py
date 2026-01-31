"""Token estimation and budget management for LLM-aware output.

This module provides utilities for estimating token counts and managing
token budgets across different output formats (Markdown sketches, JSON
behavior maps, tiered output).

How It Works
------------
Token estimation uses a simple character-based heuristic: approximately
4 characters per token. This is a reasonable approximation for English
text with most tokenizers (GPT, Claude, etc.).

For conservative estimates (to avoid exceeding budgets), we use ceiling
division. For JSON output, we serialize to JSON first then count chars.

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


def truncate_to_tokens(text: str, max_tokens: int) -> str:
    """Truncate text to approximately fit within token budget.

    Attempts to truncate at markdown section boundaries (## headers) to
    keep headers with their content. Avoids orphaned headers like
    "## Entry Points" appearing without their content.

    When no markdown sections are found, falls back to paragraph splitting.
    As a last resort, performs hard truncation.

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

    # Find all section starts (lines beginning with ## )
    section_pattern = re.compile(r"^(## .+)$", re.MULTILINE)
    section_starts = [(m.start(), m.group(1)) for m in section_pattern.finditer(text)]

    if not section_starts:
        # No markdown sections, fall back to paragraph splitting
        paragraphs = text.split("\n\n")
        result_parts = []
        current_length = 0

        for para in paragraphs:
            para_with_sep = para + "\n\n"
            if current_length + len(para_with_sep) <= max_chars:
                result_parts.append(para)
                current_length += len(para_with_sep)
            else:
                break

        if result_parts:
            return "\n\n".join(result_parts)
        return text[:max_chars]

    # Extract sections (each section is header + content until next header)
    sections = []
    for i, (start, _header) in enumerate(section_starts):
        if i + 1 < len(section_starts):
            end = section_starts[i + 1][0]
        else:
            end = len(text)
        sections.append(text[start:end].rstrip())

    # Include any content before the first section (like the title)
    prefix = text[: section_starts[0][0]].rstrip() if section_starts[0][0] > 0 else ""

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
