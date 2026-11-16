# SPDX-License-Identifier: AGPL-3.0-or-later
"""Test-input directories a repository declares in ``.hypergumbo.toml`` (ADR-0004 point 10).

WHAT THIS IS FOR. A repository can hold a whole project that only its tests
read: a recorded crate replayed by an indexer's tests, a sample project an
end-to-end test builds, a workspace a language server's tests open.
``paths.classify_test_file`` decides test code from the path alone, and such a
tree's path names nothing the rules know (``recorded/``, ``packaging_tests/``,
``projects/``), so its ``main`` functions became entry points, its language
counted in the stack and its tests fed the sketch's Tests line. A census of
661 embedded project trees in 44 repositories found no rule computed from
repository content that separates these trees from first-party code without
demoting first-party packages, so the repository says which they are:

    test_input_dirs = ["packages/x/recorded", "internal/e2e"]

(owner ruling 2026-11-16). A file under a declared directory is classified as
test code exactly as a file under ``tests/`` is, with its own reason,
:data:`paths.TEST_FILE_REASON_DECLARED`, wherever the reason is shown.

HOW EVERY CONSUMER GETS THE SAME ANSWER. The predicates
(``classify_test_file`` / ``is_test_file`` / ``is_test_node`` /
``selection.filters.is_test_path``) are pure path functions with some sixty
call sites, a third of which hold no repository root. So the declaration is
not threaded through them: it is INSTALLED here, as the process's one active
:class:`Declaration`, and ``classify_test_file`` consults it after its path
rules. Every installer restores what it found when its unit of work ends:

- ``cli.run_survey`` (an analysis) from the project config, for the run;
- ``survey_io.reading_substrate`` (a library caller, script or test reading a
  saved map) from the map's ``supply_chain_summary.declared_test_inputs``,
  for its ``with`` block;
- ``cli._load_command_substrate`` (every CLI command that reads a saved map)
  from the map, for the rest of the command; each such command is
  decorated with ``cli._restores_declaration``, which restores on return
  whoever called it (a gate test checks the pairing);
- ``sketch.generate_sketch`` from the map it summarises, for the sketch.

``survey_io.load_substrate`` itself installs nothing. The test trees'
conftests restore ``None`` after every test, so a test that installs one
directly cannot leak into the next.

A module global and not a ``ContextVar``: the analyzers and linkers run in
``ThreadPoolExecutor`` workers, which start with an empty context and would
see no declaration (the process-global ``FileIndex`` is shared the same way,
for the same reason). Two analyses in one process do not leak into each other
because each installs its own (possibly empty) declaration at its start and
restores the previous one on exit.

NO DECLARATION IS A POSITIVE FACT. ``None`` (nothing installed) and a
declaration with no directories both classify nothing. The map says which
happened: ``supply_chain_summary.declared_test_inputs`` is present whenever
the analysed repository has a ``.hypergumbo.toml`` (``dirs: []`` when it
declares none) and absent when it has none, which is also how a map written
before this key existed reads; before it existed no repository could declare
anything, so an absent field means "no declaration" in both cases.

MATCHING. Declared directories are repository-relative POSIX paths, literal
(no globs: a pattern could silently cover first-party code, and a literal
can be refused when it names nothing). A relative path is taken as
repository-relative; an absolute path matches only under the root the
declaration was installed with (an analysis has one; a declaration read from
a map has none, and map node paths are relative). A bare name matches when
it IS a declared top-level directory, which is what ``jvm_deps`` passes for a
child of the repository root.
"""

from __future__ import annotations

import posixpath
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterable, Iterator, Mapping, Optional

#: The ``.hypergumbo.toml`` key. Project tier only (``user_config``).
SETTING = "test_input_dirs"
#: The behavior-map field, under ``supply_chain_summary``.
MAP_FIELD = "declared_test_inputs"
#: Characters that would make an entry a pattern rather than a directory.
_GLOB_CHARACTERS = frozenset("*?[]")


def _is_drive_path(text: str) -> bool:
    """``C:/x`` or ``C:\\x``: absolute on Windows whatever the host is."""
    return len(text) > 2 and text[0].isalpha() and text[1] == ":" and text[2] in "/\\"


@dataclass(frozen=True)
class Declaration:
    """One repository's declared test-input directories.

    ``dirs`` are normalised, de-duplicated and sorted, so two runs over the
    same config classify identically. ``roots`` are the absolute spellings of
    the repository root (as given and resolved) that an absolute path is
    stripped of before matching; empty for a declaration read from a map.
    ``config`` names the file that declared them, for the disclosures.
    """

    dirs: tuple[str, ...]  # axis: free-text — repository-relative directory paths
    roots: tuple[str, ...] = ()  # axis: free-text — absolute repository root spellings
    config: Optional[str] = None  # axis: free-text — the declaring file's basename

    def dir_of(self, path: str) -> Optional[str]:
        """The declared directory holding ``path``, or ``None``."""
        if not self.dirs or not path:
            return None
        rel = path.replace("\\", "/")
        if rel.startswith("/") or _is_drive_path(rel):
            for root in self.roots:
                if rel.startswith(root + "/"):
                    rel = rel[len(root) + 1:]
                    break
            else:
                return None
        while rel.startswith("./"):
            rel = rel[2:]
        for directory in self.dirs:
            if rel == directory or rel.startswith(directory + "/"):
                return directory
        return None


_active: Optional[Declaration] = None


def active_declaration() -> Optional[Declaration]:
    """The declaration ``classify_test_file`` consults now, or ``None``."""
    return _active


def install_declaration(declaration: Optional[Declaration]) -> Optional[Declaration]:
    """Make ``declaration`` the active one and return the one it replaces.

    The caller restores the returned value when its unit of work ends; see
    the module docstring for the three callers.
    """
    global _active
    previous = _active
    _active = declaration
    return previous


@contextmanager
def declared(declaration: Optional[Declaration]) -> Iterator[Optional[Declaration]]:
    """Install ``declaration`` for the ``with`` block, then restore."""
    previous = install_declaration(declaration)
    try:
        yield declaration
    finally:
        install_declaration(previous)


def declared_dir_of(path: str) -> Optional[str]:
    """The active declaration's directory holding ``path``, or ``None``."""
    declaration = _active
    if declaration is None:
        return None
    return declaration.dir_of(path)


def validate_entries(value: list[Any], repo_root: Path) -> tuple[str, ...]:
    """Normalise the configured list, or raise ``ValueError`` naming the entry.

    Refused: a non-string, an empty entry or one naming the root itself, a
    glob pattern, an absolute path, a path that leaves the repository
    (``..``, or a symlink resolving outside it), a path that does not exist,
    and a path that is not a directory. The caller (``user_config``) wraps
    the message in a ``ConfigError`` naming the file, which is exit 2.
    """
    root = Path(repo_root)
    resolved_root = root.resolve()
    seen: set[str] = set()
    for entry in value:
        if not isinstance(entry, str):
            raise ValueError(
                f"entries must be repository-relative directory paths "
                f"(strings), got {entry!r}",
            )
        text = entry.strip()
        if any(ch in _GLOB_CHARACTERS for ch in text):
            raise ValueError(
                f"entry {entry!r} is a pattern; list each directory literally "
                f"(glob patterns are not accepted)",
            )
        if text.startswith("/") or _is_drive_path(text) or Path(text).is_absolute():
            raise ValueError(
                f"entry {entry!r} is absolute; give it relative to the "
                f"repository root (the directory holding this file)",
            )
        normal = posixpath.normpath(text.replace("\\", "/")) if text else "."
        if normal == ".":
            raise ValueError(
                f"entry {entry!r} names the repository root; list the "
                f"directories that hold test input",
            )
        if normal == ".." or normal.startswith("../"):
            raise ValueError(f"entry {entry!r} leaves the repository")
        target = root / normal
        if not target.exists():
            raise ValueError(f"entry {entry!r} does not exist under {root}")
        try:
            target.resolve().relative_to(resolved_root)
        except ValueError:
            raise ValueError(
                f"entry {entry!r} leaves the repository through a symbolic link",
            ) from None
        if not target.is_dir():
            raise ValueError(f"entry {entry!r} is not a directory")
        seen.add(normal)
    return tuple(sorted(seen))


def declaration_for_repo(
    repo_root: Path, dirs: Iterable[str], config: Optional[str],
) -> Declaration:
    """The declaration an analysis of ``repo_root`` installs."""
    root = Path(repo_root)
    roots = {str(root.absolute()).replace("\\", "/").rstrip("/"),
             str(root.resolve()).replace("\\", "/").rstrip("/")}
    return Declaration(dirs=tuple(sorted(set(dirs))), roots=tuple(sorted(roots)), config=config)


def declaration_from_map(
    behavior_map: Mapping[str, Any], repo_root: Optional[Path] = None,
) -> Optional[Declaration]:
    """The declaration a saved map carries, or ``None`` when it carries none.

    ``repo_root``, when given, lets absolute paths match too (the sketch
    re-reads files from disk); map node paths are relative and need none.
    """
    summary = (behavior_map.get("supply_chain_summary") or {}).get(MAP_FIELD)
    if not isinstance(summary, Mapping):
        return None
    dirs = [
        d["path"] for d in summary.get("dirs") or []
        if isinstance(d, Mapping) and isinstance(d.get("path"), str)
    ]
    config = summary.get("config") if isinstance(summary.get("config"), str) else None
    if repo_root is not None:
        return declaration_for_repo(repo_root, dirs, config)
    return Declaration(dirs=tuple(sorted(set(dirs))), config=config)


def declared_summary(
    declaration: Declaration,
    files: Iterable[Path],
    path_rule_reason: Callable[[str], Optional[str]],
) -> dict[str, Any]:
    """``supply_chain_summary.declared_test_inputs`` for one analysis.

    ``{config, setting, dirs: [{path, files}], files}``. A directory's
    ``files`` counts the files the walk found under it that ONLY the
    declaration classifies, i.e. that ``path_rule_reason`` (the path rules of
    ``classify_test_file``) does not already call test code: the number of
    files the declaration changed. A file under two nested declared
    directories counts once, under the first in sorted order (the outer one).
    ``dirs`` is not capped: it is how a re-reading command recovers the
    declaration.
    """
    counts = dict.fromkeys(declaration.dirs, 0)
    for path in files:
        posix = str(path).replace("\\", "/")
        directory = declaration.dir_of(posix)
        if directory is None:
            continue
        if path_rule_reason(posix) is None:
            counts[directory] += 1
    return {
        "config": declaration.config,
        "setting": SETTING,
        "dirs": [{"path": d, "files": counts[d]} for d in declaration.dirs],
        "files": sum(counts.values()),
    }


def format_declared_disclosure(summary: Optional[Mapping[str, Any]]) -> Optional[str]:
    """The one-line disclosure, or ``None`` when nothing is declared.

    ``Classified as test input because .hypergumbo.toml declares it
    (test_input_dirs): internal/e2e/ (12 files), recorded/ (3 files) (ADR-0004).``
    -- naming at most three directories, like the lines beside it. The count
    is of files only the declaration classifies.
    """
    if not summary or not summary.get("dirs"):
        return None
    dirs = list(summary["dirs"])
    parts = []
    for entry in dirs[:3]:
        n = int(entry.get("files", 0))
        parts.append(f"{entry.get('path')}/ ({n} file{'' if n == 1 else 's'})")
    if len(dirs) > 3:
        parts.append(f"{len(dirs) - 3} more")
    config = summary.get("config") or ".hypergumbo.toml"
    setting = summary.get("setting") or SETTING
    total = int(summary.get("files", 0))
    return (
        f"Classified as test input because {config} declares it ({setting}), "
        f"{total} file{'' if total == 1 else 's'}: {', '.join(parts)} (ADR-0004)."
    )
