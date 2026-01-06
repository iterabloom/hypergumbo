"""Selection module for symbol filtering and prioritization.

This module provides shared utilities for selecting and filtering symbols
across different output modes (sketch, compact, tiered JSON).

Submodules
----------
filters : Path classification and symbol kind filtering
    - is_test_path: Detect test files across many languages
    - is_example_path: Detect example/demo directories
    - EXCLUDED_KINDS: Symbol kinds to exclude from output

language_proportional : Language-stratified symbol selection
    - group_symbols_by_language: Group symbols by source language
    - group_files_by_language: Group files by dominant language
    - allocate_language_budget: Proportional budget allocation
    - select_proportionally: Convenience function for proportional selection

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

from .language_proportional import (
    allocate_language_budget,
    group_files_by_language,
    group_symbols_by_language,
    select_proportionally,
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
    "allocate_language_budget",
    "estimate_json_tokens",
    "estimate_tokens",
    "group_files_by_language",
    "group_symbols_by_language",
    "is_example_path",
    "is_test_path",
    "parse_tier_spec",
    "select_proportionally",
    "truncate_to_tokens",
]
