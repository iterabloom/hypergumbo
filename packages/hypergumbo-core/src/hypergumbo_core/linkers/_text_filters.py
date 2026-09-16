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

import contextvars
import logging
from functools import lru_cache
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

# When set, the masker reads/writes parsed trees through this dict instead of
# re-parsing. Populated by ``run_all_linkers`` so multiple linkers running on
# the same file share one parse. ``LinkerContext.parsed_trees`` is the same
# object this var points at — see ``linkers/registry.py``.
#
# Key: ``(absolute_path_str, language)``. Value: ``tree_sitter.Tree``.
_active_parse_cache: contextvars.ContextVar[
    Optional[dict[tuple[str, str], Any]]
] = contextvars.ContextVar("_active_parse_cache", default=None)


def set_active_parse_cache(
    cache: Optional[dict[tuple[str, str], Any]],
) -> contextvars.Token[Optional[dict[tuple[str, str], Any]]]:
    """Bind ``cache`` as the active parse cache for masker calls in scope.

    Returns the contextvar token; call ``reset_active_parse_cache(token)``
    to restore the previous binding.
    """
    return _active_parse_cache.set(cache)


def reset_active_parse_cache(
    token: contextvars.Token[Optional[dict[tuple[str, str], Any]]],
) -> None:
    """Restore the previous parse cache binding."""
    _active_parse_cache.reset(token)


# WI-finij: the per-invocation read log behind ``AnalysisRun.files_analyzed``.
#
# ``derive_silence_reason`` reads ``files_analyzed == 0`` as the POSITIVE claim
# "this pass received zero input files" and stamps ``no_candidate_files``. Zero
# is also the dataclass default, so a linker that read four hundred files and
# never assigned the counter produced that claim falsely — measured on the
# 2026-09-16 self-survey, EIGHT linkers did, reading 14 to 834 files apiece
# (pyffi 389, grpc 397, di-resolution 396, annotation-convention 834,
# wasm-bindgen 14, solidity-abi 14, crypto-flow 17, message-dispatch 17).
#
# Counting here rather than in eighteen linker bodies makes the count a DERIVED
# fact: seventeen linkers already assign it correctly and eighteen do not, which
# is the evidence that "remember to count" does not survive a new linker.
# ``tests/test_linker_file_read_accounting.py`` gates the other half — a direct
# ``Path.read_text`` / ``Path.read_bytes`` inside ``linkers/`` fails there,
# because such a call would bypass this log and re-open the hole.
#
# A SET of path strings, so ``files_analyzed`` counts FILES and a linker that
# reads one file three times still reports one. ``None`` means no linker
# invocation is in scope (direct or test call) and reads go uncounted.
_active_read_log: contextvars.ContextVar[
    Optional[set[str]]
] = contextvars.ContextVar("_active_read_log", default=None)


def set_active_read_log(
    log: Optional[set[str]],
) -> contextvars.Token[Optional[set[str]]]:
    """Bind ``log`` as the read log for counted reads in scope.

    Returns the contextvar token; call ``reset_active_read_log(token)`` to
    restore the previous binding. The reset is mandatory rather than tidy:
    ``run_all_linkers`` dispatches same-priority cohorts to a thread pool whose
    workers are REUSED, so a leaked binding would attribute one linker's reads
    to the next one scheduled on that thread.
    """
    return _active_read_log.set(log)


def reset_active_read_log(token: contextvars.Token[Optional[set[str]]]) -> None:
    """Restore the previous read log binding."""
    _active_read_log.reset(token)


def note_source_read(file_path: Path) -> None:
    """Record ``file_path`` against the active read log, if one is bound.

    Every reader in this module funnels through here. Silent when unbound so
    that direct/test invocation of a linker body behaves exactly as before.
    """
    log = _active_read_log.get()
    if log is not None:
        log.add(str(file_path))


def read_source_text(
    file_path: Path,
    *,
    encoding: str = "utf-8",
    errors: Optional[str] = None,
) -> str:
    """Read ``file_path`` as text, counted, WITHOUT masking doc regions.

    For the linkers whose subject matter IS the masked region:
    ``annotation_convention`` scans for ``@hg:`` directives that live in
    comments, so routing it through :func:`read_masked_source` would blank out
    precisely what it looks for. Use this when a linker must see comments or
    docstrings; use :func:`read_masked_source` otherwise.

    ``errors`` defaults to ``None`` — STRICT decoding, matching
    ``Path.read_text``'s own default — so converting a call site to this
    function changes only the accounting. Two ``js_module`` sites read
    ``tsconfig``/``vite`` configs strictly and catch ``OSError`` alone; a
    silently-substituting default here would turn their decode failures into
    mis-parsed config instead of the error they expect.
    """
    note_source_read(file_path)
    return file_path.read_text(encoding=encoding, errors=errors)


def read_source_bytes(file_path: Path) -> bytes:
    """Read ``file_path`` as bytes, counted.

    For linkers that hand raw bytes to a tree-sitter parser rather than
    matching regexes against text; masking does not apply to them.
    """
    note_source_read(file_path)
    return file_path.read_bytes()


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


def js_ts_language_from_path(file_path: Path) -> str:
    """Return the JS/TS analyzer's language tag for ``file_path`` (INV-tofun).

    Mirrors ``hypergumbo_lang_mainstream.js_ts._get_language_for_file``:
    ``.ts``/``.tsx`` are ``typescript``; every other extension is
    ``javascript``. Linkers that fabricate synthetic stand-ins from JS/TS
    source use this so a stand-in discovered in a ``.ts`` file carries the same
    language the analyzer assigns to real declarations in that file — the value
    feeds ``Symbol.discovery_language`` and the canonical id's first segment.

    Distinct from :func:`language_from_path`, which returns tree-sitter grammar
    names (``tsx`` for ``.tsx``) and ``None`` for unknown extensions. This
    helper instead reproduces the analyzer's coarser typescript/javascript
    split, because the governing invariant is *consistency with the analyzer's
    tag*, not independent extension correctness.
    """
    if file_path.suffix.lower() in (".ts", ".tsx"):
        return "typescript"
    return "javascript"

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
                # Tree-sitter Python lists comments as named children,
                # so a leading SPDX/license header (the convention in this
                # repo) would otherwise displace the docstring out of the
                # "first named child" slot. Skip leading comments when
                # locating the docstring position.
                matched = False
                for i in range(parent.named_child_count):
                    sibling = parent.named_child(i)
                    if sibling is None:  # pragma: no cover - defensive
                        continue
                    if sibling.type in _DOC_COMMENT_TYPES:
                        continue
                    if sibling.id == node.id:
                        ranges.append((node.start_byte, node.end_byte))
                        matched = True
                    break
                if matched:
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
    cache_key: Optional[tuple[str, str]] = None,
) -> str:
    """Return ``content`` with comments (and Python docstrings) masked to spaces.

    Args:
        content: Source text as a Python string.
        language: tree-sitter-language-pack language name (e.g. ``"python"``,
            ``"javascript"``). When unsupported / missing / falsy, the function
            returns the input unchanged (fail-closed).
        mask_string_literals_too: When True, every ``string`` node is masked.
            Default False to preserve linkers that match inside literals.
        cache_key: Optional ``(path, language)`` key for the active parse cache
            (see ``set_active_parse_cache``). When provided, the masker reuses
            an already-parsed tree if one is registered, and stores its own
            parse result for later linkers in the same run.

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

    source_bytes = content.encode("utf-8", errors="replace")
    cache = _active_parse_cache.get()
    tree = None
    if cache is not None and cache_key is not None:
        tree = cache.get(cache_key)
    if tree is None:
        try:
            tree = parser.parse(source_bytes)
        except Exception:  # pragma: no cover - defensive; parse rarely raises
            return content
        if cache is not None and cache_key is not None:
            cache[cache_key] = tree

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
    ``language`` is omitted, it's inferred from the file extension. Reuses
    a previously-parsed tree from the active parse cache if one is available,
    keyed by ``(str(file_path), language)``.
    """
    note_source_read(file_path)
    content = file_path.read_text(encoding=encoding, errors=errors)
    if language is None:
        language = language_from_path(file_path)
    cache_key = (str(file_path), language) if language else None
    return mask_doc_regions(content, language, cache_key=cache_key)
