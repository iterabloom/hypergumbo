# SPDX-License-Identifier: AGPL-3.0-or-later
"""Infrastructure linker helper: mask docstring/comment regions for regex linkers.

Many protocol/framework linkers run regex pattern detectors directly against
file contents. Their pattern strings — describing code shapes like
``producer.send('topic', msg)`` — also tend to appear in module docstrings
and comments that document the very patterns being detected. The result is
phantom node emission from prose.

This helper parses the file with tree-sitter and replaces comment and
Python-docstring byte ranges with spaces (newlines preserved so existing
line counters in linkers stay correct), then returns the masked string.
Non-mask paths (missing grammar, parse failure, unknown language) return
content unchanged so the masker only ever removes false positives — never
real detections.

The Python-docstring rule is positional: a ``string`` node whose direct
parent is ``module`` or ``block`` AND which is that parent's first named
child is treated as a docstring. Regular string literals are preserved,
because several linkers (``database_query``, ``graphql``, ``openapi``)
rely on matching inside literals.
"""
from __future__ import annotations

import logging
from functools import lru_cache
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# Extension → tree-sitter-language-pack language name. Covers extensions that
# appear in the 24 linkers' file-discovery patterns. Unknown extensions return
# None, in which case the masker falls through and returns content unchanged.
_EXTENSION_TO_LANGUAGE: dict[str, str] = {
    ".py": "python",
    ".js": "javascript",
    ".mjs": "javascript",
    ".cjs": "javascript",
    ".jsx": "javascript",
    ".ts": "typescript",
    ".tsx": "tsx",
    ".java": "java",
    ".kt": "kotlin",
    ".kts": "kotlin",
    ".scala": "scala",
    ".rb": "ruby",
    ".go": "go",
    ".rs": "rust",
    ".c": "c",
    ".h": "c",
    ".cpp": "cpp",
    ".cc": "cpp",
    ".cxx": "cpp",
    ".hpp": "cpp",
    ".hh": "cpp",
    ".cs": "csharp",
    ".swift": "swift",
    ".m": "objc",
    ".mm": "objc",
    ".php": "php",
    ".lua": "lua",
    ".proto": "proto",
    ".sol": "solidity",
}


def language_from_path(file_path: Path) -> Optional[str]:
    """Return tree-sitter language name for the file extension, or None."""
    return _EXTENSION_TO_LANGUAGE.get(file_path.suffix.lower())

_DOC_COMMENT_TYPES = frozenset({
    "comment",
    "block_comment",
    "line_comment",
    "multiline_comment",
})

_PYTHON_BLOCK_PARENTS = frozenset({"module", "block"})


@lru_cache(maxsize=64)
def _get_parser(language: str):
    """Return a tree-sitter Parser for the language, or None if unavailable.

    Cached so repeated calls within a single ``hypergumbo run`` don't reload
    the same grammar. The cache survives the process; correctness does not
    depend on it.
    """
    if not language or language == "unknown":
        return None
    try:
        from tree_sitter import Parser
        from tree_sitter_language_pack import get_language

        lang = get_language(language)
        return Parser(lang)
    except Exception:
        return None


def _collect_mask_ranges(
    root, language: str, mask_string_literals_too: bool
) -> list[tuple[int, int]]:
    ranges: list[tuple[int, int]] = []
    stack = [root]
    while stack:
        node = stack.pop()
        if node is None:  # pragma: no cover - defensive
            continue
        ntype = node.type
        if ntype in _DOC_COMMENT_TYPES:
            ranges.append((node.start_byte, node.end_byte))
            continue
        if language == "python" and ntype == "string":
            parent = node.parent
            if parent is not None and parent.type in _PYTHON_BLOCK_PARENTS:
                first_named = parent.named_child(0)
                if first_named is not None and first_named.id == node.id:
                    ranges.append((node.start_byte, node.end_byte))
                    continue
        if mask_string_literals_too and ntype == "string":
            ranges.append((node.start_byte, node.end_byte))
            continue
        for i in range(node.child_count):
            stack.append(node.child(i))
    return ranges


def mask_doc_regions(
    content: str,
    language: Optional[str],
    *,
    mask_string_literals_too: bool = False,
) -> str:
    """Return ``content`` with comments (and Python docstrings) masked to spaces.

    Args:
        content: Source text as a Python string.
        language: tree-sitter-language-pack language name (e.g. ``"python"``,
            ``"javascript"``). When unsupported / missing / falsy, the function
            returns the input unchanged (fail-closed).
        mask_string_literals_too: When True, every ``string`` node is masked.
            Default False to preserve linkers that match inside literals.

    The mask never reduces detections. All failure modes return the original
    content; only successfully-identified comment/docstring ranges are removed.
    """
    if not content:
        return content
    parser = _get_parser(language) if language else None
    if parser is None:
        if language:
            logger.debug(
                "mask_doc_regions: no parser for language %r; returning content unchanged",
                language,
            )
        return content
    try:
        source_bytes = content.encode("utf-8", errors="replace")
        tree = parser.parse(source_bytes)
    except Exception:  # pragma: no cover - defensive; parse rarely raises
        return content

    ranges = _collect_mask_ranges(tree.root_node, language, mask_string_literals_too)
    if not ranges:
        return content

    buf = bytearray(source_bytes)
    space = ord(b" ")
    newline = ord(b"\n")
    for start, end in ranges:
        end = min(end, len(buf))
        for i in range(start, end):
            if buf[i] != newline:
                buf[i] = space
    return buf.decode("utf-8", errors="replace")


def read_masked_source(
    file_path: Path,
    *,
    encoding: str = "utf-8",
    errors: str = "replace",
    language: Optional[str] = None,
) -> str:
    """Read ``file_path`` and return its content with doc regions masked.

    Drop-in replacement for ``file_path.read_text(...)`` in linkers. When
    ``language`` is omitted, it's inferred from the file extension.
    """
    content = file_path.read_text(encoding=encoding, errors=errors)
    if language is None:
        language = language_from_path(file_path)
    return mask_doc_regions(content, language)
