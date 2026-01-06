"""Selection module for symbol filtering and prioritization.

This module provides shared utilities for selecting and filtering symbols
across different output modes (sketch, compact, tiered JSON).

Submodules
----------
filters : Path classification and symbol kind filtering
    - is_test_path: Detect test files across many languages
    - is_example_path: Detect example/demo directories
    - EXCLUDED_KINDS: Symbol kinds to exclude from output

token_budget : Token estimation and budget management
    - estimate_tokens: Estimate token count for text
    - estimate_json_tokens: Estimate tokens for JSON data
    - truncate_to_tokens: Truncate text to fit token budget
    - parse_tier_spec: Parse tier specs like "4k", "16k"
"""

from .filters import (
    is_test_path,
    is_example_path,
    EXCLUDED_KINDS,
    EXAMPLE_PATH_PATTERNS,
)

from .token_budget import (
    CHARS_PER_TOKEN,
    DEFAULT_TIERS,
    TOKENS_BEHAVIOR_MAP_OVERHEAD,
    TOKENS_PER_NODE_OVERHEAD,
    estimate_json_tokens,
    estimate_tokens,
    parse_tier_spec,
    truncate_to_tokens,
)

__all__ = [
    "CHARS_PER_TOKEN",
    "DEFAULT_TIERS",
    "EXAMPLE_PATH_PATTERNS",
    "EXCLUDED_KINDS",
    "TOKENS_BEHAVIOR_MAP_OVERHEAD",
    "TOKENS_PER_NODE_OVERHEAD",
    "estimate_json_tokens",
    "estimate_tokens",
    "is_example_path",
    "is_test_path",
    "parse_tier_spec",
    "truncate_to_tokens",
]
