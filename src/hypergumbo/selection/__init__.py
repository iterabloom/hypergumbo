"""Selection module for symbol filtering and prioritization.

This module provides shared utilities for selecting and filtering symbols
across different output modes (sketch, compact, tiered JSON).

Submodules
----------
filters : Path classification and symbol kind filtering
    - is_test_path: Detect test files across many languages
    - is_example_path: Detect example/demo directories
    - EXCLUDED_KINDS: Symbol kinds to exclude from output
"""

from .filters import (
    is_test_path,
    is_example_path,
    EXCLUDED_KINDS,
    EXAMPLE_PATH_PATTERNS,
)

__all__ = [
    "EXAMPLE_PATH_PATTERNS",
    "EXCLUDED_KINDS",
    "is_example_path",
    "is_test_path",
]
