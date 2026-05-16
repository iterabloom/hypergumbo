# SPDX-License-Identifier: AGPL-3.0-or-later
"""Selection module for symbol filtering and prioritization.

This module provides shared utilities for selecting and filtering
symbols across different output modes (sketch, compact, tiered JSON,
behavior-map node-level accounting).

Submodules
----------
filters : Path classification and symbol-kind / framework-role filtering
    - is_test_path: detect test files across many languages
    - is_example_path: detect example/demo directories
    - is_excluded_kind: dual-shape predicate matching either
      ``Symbol.kind in EXCLUDED_KINDS`` or
      ``Symbol.meta["framework_role"] in EXCLUDED_FRAMEWORK_ROLES``
      (ADR-0027 Phase-4b axis split)
    - EXCLUDED_KINDS / EXCLUDED_FRAMEWORK_ROLES: the two filter sets
    - EXAMPLE_PATH_PATTERNS: directory-pattern regexes

language_proportional : Language-stratified symbol selection
    - group_symbols_by_language: group symbols by source language
    - group_files_by_language: group files by dominant language
    - allocate_language_budget: proportional budget allocation
    - select_proportionally: convenience function for proportional selection

token_budget : Token estimation and budget management
    - estimate_tokens: estimate token count for text
    - estimate_json_tokens: estimate tokens for JSON data
    - truncate_to_tokens: truncate text to fit token budget
    - parse_tier_spec: parse tier specs like "4k", "16k"
    - CHARS_PER_TOKEN: the ~4 chars/token heuristic constant
    - DEFAULT_TIERS: default tier ladder (4k / 16k / 64k)
    - TOKENS_BEHAVIOR_MAP_OVERHEAD / TOKENS_PER_NODE_OVERHEAD:
      structural-overhead constants used by node-level budget accounting
      when emitting behavior-map JSON
"""

from .filters import (
    is_test_path,
    is_example_path,
    is_excluded_kind,
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
    "is_excluded_kind",
    "is_test_path",
    "parse_tier_spec",
    "select_proportionally",
    "truncate_to_tokens",
]
