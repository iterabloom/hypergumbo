# SPDX-License-Identifier: AGPL-3.0-or-later
"""Files a coding-agent tool loads as instructions: found, listed, never quoted.

WHY. A sketch is pasted into a language model, and it quotes the repository:
the README description, docstring summaries and, with source, whole files.
Every sketch says under its title that quoted text is data, not instructions
(``sketch.QUOTED_TEXT_NOTICE``). One class of file is written to be obeyed
by a model: ``AGENTS.md``, ``CLAUDE.md``, ``.github/copilot-instructions.md``,
``.cursor/rules/*.mdc`` and the like, which a coding-agent tool loads into its
model's context as instructions. Quoting such a file hands its instructions
to whatever model reads the sketch. So the sketch names each one, with the
tools that read it, and prints none of its text (``sketch`` calls
:func:`find_instruction_files` once per sketch and holds the paths back in
every section that prints file text).

RECOGNITION IS DATA. Which file names a tool reads is a fact about that
tool, and tools come and go, so the names live in a catalogue keyed by the
tool (``agent_instructions/agent-instruction-files.yaml``, registered in
``yaml_catalogs.YAML_CATALOGS``), not in this module. A pattern is a glob
relative to the repository root, matched segment by segment by
``discovery._path_segments_match`` (``**`` spans directories, ``*`` stays
inside one): ``**/CLAUDE.md`` is read at any depth, ``.cursorrules`` at the
root only. Matching is case-sensitive, as the tools' own lookups are.

IMPORTS ARE THE GENERAL FORM. A name list covers the files a tool reads by
name; a file can also become instructions by being imported. Claude Code and
Gemini CLI expand an ``@path`` reference in a loaded file into the file it
names, and the rest of the tools' files use the same notation to tell the
agent what to read next (rollup's ``AGENTS.md``: "Always read this file first:
@.github/copilot-instructions.md"). So every instruction file's ``@path``
references are followed, transitively, whichever tool reads it; the decisions
at the edges, each tested:

- a reference inside a fenced code block or an inline code span is not an
  import (the tools skip those too, and ``@decorator`` examples live there);
- a relative path resolves against the importing file's directory, an
  absolute one as given; the path is normalised lexically, so a reference is
  held back under the in-repository path the sketch would print it by;
- a target that does not exist, is a directory, or lies outside the
  repository (``../x``, ``/etc/x``, ``~/x``) is not followed and not listed:
  the sketch never prints it, so there is nothing to hold back;
- a cycle (``A`` imports ``B`` imports ``A``) ends because each file is read
  once; a file importing itself records nothing;
- there is no depth limit: Claude Code stops after five hops, but holding a
  sixth file back is the safe side of that difference.

A file the VCS ignores and does not track is left out (``vcs_ignored``),
because the sketch does not describe it (``CLAUDE.local.md`` is usually such
a file).

WHAT IS WALKED. One ``os.walk`` of the repository per sketch, pruned by
``discovery.is_pruned_dir`` with the default excludes (the rule every walker
shares), and not pruned at dot-directories: ``.github``, ``.cursor`` and
``.claude`` are where half the catalogue lives. Each file is compared with a
pattern's first and last segments before the recursive match runs
(:func:`_matches`), so most files cost a few ``fnmatch`` calls.
"""

from __future__ import annotations

import functools
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator, Mapping, Optional

from .yaml_catalogs import CatalogueError, read_catalogue_yaml, reject_unknown_keys

#: The shipped catalogue (``yaml_catalogs`` family ``agent_instructions``).
CATALOGUE_PATH = (
    Path(__file__).parent / "agent_instructions" / "agent-instruction-files.yaml"
)

_WHAT = "agent instruction catalogue"
_TOP_KEYS = frozenset({"provenance", "id", "tools"})
_ROW_KEYS = frozenset({"tool", "files"})

#: Bytes read from one file when looking for its ``@path`` references.
_MAX_IMPORT_SCAN_BYTES = 1024 * 1024

#: Entries the sketch's one-line listing names before it counts the rest.
LISTED_FILES = 8


@dataclass(frozen=True)
class ToolRow:
    """One catalogue row: a tool and the patterns of the files it reads."""

    tool: str
    #: Each pattern split into its ``/`` segments.
    patterns: "tuple[tuple[str, ...], ...]"


@dataclass(frozen=True)
class InstructionFile:
    """A file held back from the sketch, and why."""

    #: Repository-relative, ``/``-separated.
    path: str
    #: The tools whose catalogue rows match the path, in catalogue order.
    tools: "tuple[str, ...]" = ()
    #: The instruction files whose ``@path`` references name this one.
    imported_by: "tuple[str, ...]" = ()

    def reason(self) -> str:
        """``Claude Code; imported by `AGENTS.md``` -- who loads it."""
        parts = []
        if self.tools:
            parts.append(", ".join(self.tools))
        if self.imported_by:
            importers = ", ".join(f"`{path}`" for path in self.imported_by)
            parts.append(f"imported by {importers}")
        return "; ".join(parts)


def _pattern_segments(pattern: object, path: Path) -> "tuple[str, ...]":
    if not isinstance(pattern, str) or not pattern.strip():
        raise CatalogueError(
            f"{_WHAT} {path}: a file pattern must be a non-empty string, got {pattern!r}."
        )
    segments = tuple(pattern.split("/"))
    if pattern.startswith("/") or any(s in ("", ".", "..") for s in segments):
        raise CatalogueError(
            f"{_WHAT} {path}: file pattern {pattern!r} must be relative to the "
            f"repository root, with no empty, '.' or '..' segment."
        )
    return segments


@functools.lru_cache(maxsize=None)
def load_catalogue(path: Path = CATALOGUE_PATH) -> "tuple[ToolRow, ...]":
    """The catalogue's rows, refusing a malformed file naming it."""
    data = read_catalogue_yaml(path, what=_WHAT)
    origin = f"{_WHAT} {path}"
    reject_unknown_keys(data, _TOP_KEYS, origin=origin, where="top-level")
    rows = data.get("tools")
    if not isinstance(rows, list) or not rows:
        raise CatalogueError(f"{origin}: 'tools' must be a non-empty list of rows.")
    out = []
    for row in rows:
        if not isinstance(row, dict):
            raise CatalogueError(f"{origin}: each 'tools' entry must be a mapping, got {row!r}.")
        reject_unknown_keys(row, _ROW_KEYS, origin=origin, where="tools row")
        tool, files = row.get("tool"), row.get("files")
        if not isinstance(tool, str) or not tool.strip():
            raise CatalogueError(f"{origin}: a row's 'tool' must name the tool, got {tool!r}.")
        if not isinstance(files, list) or not files:
            raise CatalogueError(f"{origin}: tool {tool!r} lists no 'files'.")
        out.append(ToolRow(tool, tuple(_pattern_segments(p, path) for p in files)))
    return tuple(out)


def _matches(parts: "tuple[str, ...]", pattern: "tuple[str, ...]") -> bool:
    """``discovery._path_segments_match``, after two cheap rejections.

    Every file of the repository is asked about every pattern, so the first
    and last segments are compared before the recursive match runs: a file
    outside ``.github`` never reaches the ``.github/...`` patterns' matcher,
    and a ``.py`` file never reaches ``**/CLAUDE.md``'s.
    """
    from fnmatch import fnmatchcase

    from .discovery import _path_segments_match

    if pattern[0] != "**" and not fnmatchcase(parts[0], pattern[0]):
        return False
    if pattern[-1] != "**" and not fnmatchcase(parts[-1], pattern[-1]):
        return False
    return _path_segments_match(parts, pattern)


def tools_reading(
    rel_path: str, catalogue: "Optional[tuple[ToolRow, ...]]" = None,
) -> "tuple[str, ...]":
    """The tools whose rows match ``rel_path`` (``/``-separated), in row order."""
    parts = tuple(rel_path.split("/"))
    rows = load_catalogue() if catalogue is None else catalogue
    return tuple(
        row.tool for row in rows
        if any(_matches(parts, pattern) for pattern in row.patterns)
    )


_FENCE = re.compile(r"^ {0,3}(`{3,}|~{3,})")
_CODE_SPAN = re.compile(r"(`+).*?\1")
_IMPORT = re.compile(r"(?:^|(?<=\s))@(\S+)")
_TRAILING = ".,;:!?)]}>\"'"


def import_references(text: str) -> "list[str]":
    """The ``@path`` references in ``text``, outside code blocks and spans.

    An ``@`` starts a reference only at the start of a line or after
    whitespace, so ``user@example.com`` is not one. Trailing sentence
    punctuation is not part of the path ("read @AGENTS.md." names
    ``AGENTS.md``).
    """
    refs: "list[str]" = []
    fence: Optional[str] = None
    for line in text.splitlines():
        opened = _FENCE.match(line)
        if fence is not None:
            if opened and opened.group(1)[0] == fence[0] and len(opened.group(1)) >= len(fence):
                fence = None
            continue
        if opened:
            fence = opened.group(1)
            continue
        for match in _IMPORT.finditer(_CODE_SPAN.sub(" ", line)):
            ref = match.group(1).rstrip(_TRAILING)
            if ref:
                refs.append(ref)
    return refs


def _inside(root: Path, candidate: str) -> Optional[str]:
    """``candidate`` (a normalised path) relative to ``root``, or ``None``.

    Tried against ``root`` as given and as resolved, so an absolute reference
    spelled through either form of a symlinked checkout is recognised.
    """
    for base in (str(root), os.path.realpath(root)):
        rel = os.path.relpath(candidate, base)
        if rel != os.curdir and rel != os.pardir and not rel.startswith(os.pardir + os.sep):
            return Path(rel).as_posix()
    return None


def resolve_import(root: Path, importer: str, ref: str) -> Optional[str]:
    """The repository file ``ref`` names from ``importer``, or ``None``.

    ``None`` for a home-relative path, a path outside ``root``, and a target
    that is missing or not a file (the module docstring says why).
    """
    if ref.startswith("~"):
        return None
    base = root / Path(importer).parent
    candidate = os.path.normpath(ref if os.path.isabs(ref) else base / ref)
    rel = _inside(root, candidate)
    if rel is None or not (root / rel).is_file():
        return None
    return rel


def _read_head(path: Path) -> str:
    try:
        with path.open("rb") as handle:
            return handle.read(_MAX_IMPORT_SCAN_BYTES).decode("utf-8", errors="replace")
    except OSError:  # pragma: no cover - unreadable file, OS-dependent
        return ""


def _walk(root: Path) -> "Iterator[str]":
    """Every repository file, ``/``-separated, past the default excludes."""
    from .discovery import _EXACT_DEFAULT_EXCLUDES, _GLOB_DEFAULT_EXCLUDES, is_pruned_dir

    for dirpath, dirnames, filenames in os.walk(root):
        here = Path(dirpath)
        dirnames[:] = sorted(
            d for d in dirnames
            if not is_pruned_dir(d, here / d, _EXACT_DEFAULT_EXCLUDES, _GLOB_DEFAULT_EXCLUDES)
        )
        rel_dir = here.relative_to(root).as_posix()
        for name in sorted(filenames):
            yield name if rel_dir == "." else f"{rel_dir}/{name}"


def find_instruction_files(
    repo_root: Path, catalogue: "Optional[tuple[ToolRow, ...]]" = None,
) -> "dict[str, InstructionFile]":
    """Every instruction file of ``repo_root``, keyed by relative path.

    The catalogue's matches, then their ``@path`` imports, transitively.
    """
    from .discovery import vcs_ignored

    root = Path(repo_root)
    rows = load_catalogue() if catalogue is None else catalogue
    by_name: "dict[str, tuple[str, ...]]" = {}
    for rel in _walk(root):
        tools = tools_reading(rel, rows)
        if tools and not vcs_ignored(root / rel, root):
            by_name[rel] = tools

    imported_by: "dict[str, list[str]]" = {}
    queue = sorted(by_name)
    seen = set(queue)
    while queue:
        importer = queue.pop(0)
        for ref in import_references(_read_head(root / importer)):
            target = resolve_import(root, importer, ref)
            if target is None or target == importer or vcs_ignored(root / target, root):
                continue
            importers = imported_by.setdefault(target, [])
            if importer not in importers:
                importers.append(importer)
            if target not in seen:
                seen.add(target)
                queue.append(target)
    return {
        path: InstructionFile(
            path, by_name.get(path, ()), tuple(imported_by.get(path, ())),
        )
        for path in sorted(seen, key=lambda p: (p.count("/"), p))
    }


def format_instruction_files_disclosure(
    files: "Mapping[str, InstructionFile]",
) -> Optional[str]:
    """The sketch's one-line listing, or ``None`` when there is nothing to list.

    Names at most :data:`LISTED_FILES` files, shallowest first, and counts
    the rest.
    """
    if not files:
        return None
    entries = [f"`{f.path}` ({f.reason()})" for f in files.values()]
    shown = entries[:LISTED_FILES]
    more = len(entries) - len(shown)
    if more:
        shown.append(f"and {more} more")
    return (
        "Agent instruction files, listed and not quoted because a coding-agent "
        f"tool loads them as instructions: {', '.join(shown)}."
    )
