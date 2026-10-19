# SPDX-License-Identifier: AGPL-3.0-or-later
"""The headers a C translation unit can see, transitively (INV-zikul).

WHY THIS EXISTS. An unresolved C call whose short name the catalogue lists as
ambiguous (``send``, ``read``, ``connect``) is stamped with the headers it
could have come from, and that stamp is read twice. Classification asks ANY:
does some header in it declare the name (``send`` via ``sys/socket``)? The
coverage gate asks ALL (INV-zimud): was every header in it enumerated? A
non-match counts as an examined negative only when ALL is true. So a stamp
that leaves a header out is not partial evidence but a false all-clear. The
stamp used to list only the file's own ``#include <...>`` lines. A file with
``#include <errno.h>`` and ``#include "net.h"``, where ``net.h`` includes
``<sys/socket.h>``, therefore stamped a real ``send()`` as ``errno.h``. errno
is granted, nothing in the stamp declares ``send``, and NO-NET-SEND read
CONFIRMED with no caveat. torsocks' ``src/lib/sendto.c:59`` is that shape:
``<sys/socket.h>`` arrives only through ``"torsocks.h"`` and through
``<common/utils.h>`` -> ``"compat.h"``.

WHAT THE STAMP NOW NAMES. Every header the file includes, quoted or angled,
and every header those reach. A header found in the repository is read and
its own includes are added. Every header reached is named as written in the
directive that reached it, including the in-repo ones. The in-repo names are
kept deliberately: a project header can declare a prototype the repository
never defines, so it is a possible home, and no catalogue can enumerate it.
That withholding is correct (owner ruling on INV-dobuh, 2026-10-04). Dropping
in-repo names is the false-clean direction that item was closed against.

ABSENT IS NOT EMPTY. A header that is not read keeps its name in the stamp
with nothing added after it. That covers a header not found in the
repository (a generated ``config.h``, a system header), one past the depth
bound, and one that cannot be read. The name stays, so the gate can vouch
for the header only when a catalogue grant enumerates that exact header,
which is the claim a grant makes about a system header. An in-repo name never
matches a grant, so an unread in-repo header withholds and cannot make the
stamp look complete. The one way around this is a repository file whose path
ends in a granted name (``errno.h``). It would be read, its own includes
added, and its name would still be the granted spelling. That is a name
collision with libc, and nothing can tell the two apart without the build's
``-I`` order.

HOW AN INCLUDE IS RESOLVED. The build's include path is not known, so:

1. a quoted include is looked up next to the including file first, as every
   C preprocessor does;
2. otherwise, quoted or angled, it resolves to EVERY repository file whose
   path ends with the include text at a path-component boundary
   (``<proj/net.h>`` -> ``inc/proj/net.h``).

Several candidates are all read: the compiler picks one, we cannot tell which,
and their union is the over-approximation. Every error this makes ADDS a
header to the stamp. One more disjunct can only add a classification match,
which is loud (a ``violated`` verdict a reader can check), or add an
unenumerated header, which withholds. It can never remove a home, which is
the silent direction. The same holds for a system header that resolves to a
same-named compatibility copy in the repository.

HOW A DIRECTIVE IS READ. Line by line, as the preprocessor reads it:
``#include`` and ``#include_next`` with either delimiter, any spacing. The
analysed file and the headers it reaches are read by the same function, so
the two cannot disagree about what a directive includes. Conditional
directives are all taken (``#ifdef _WIN32`` / ``#else``): a header behind any
branch is one the call could have come from. A directive inside a block
comment is taken too; that only adds. ``#include MACRO`` names no header and
is skipped. The skip is safe because the file's other headers still withhold
unless every one of them is granted. A file whose ONLY include is a macro
reads as having none.

BOUNDS. Each header is read once per run, and a translation unit's walk
visits each header once (a visited set by resolved path). That is what makes
include cycles and include guards harmless: a guarded re-include is the same
file. The depth is bounded by :data:`MAX_INCLUDE_DEPTH`. A header at the bound
is named but not read, which is the absent case above.

WHO USES IT. ``c.py``, for the stamp on an ambiguous libc name and for
WI-rimon's "a header this unit includes declares the call" test. ``cpp.py``
stamps EVERY unresolved call with its own angle includes and has the same
false all-clear, but wiring this in there was measured to grow the survey
2.55x on modsecurity and to cost the method-call recovery linker the calls
it resolves only from the placeholder slot, so it is a separate item.

WHAT THIS DOES NOT DO. It does not read system headers (they are outside the
repository; the catalogue describes what each one declares). It does not
evaluate ``#if``. It does not follow a header reached only through a system
header back into the repository.
"""

from __future__ import annotations

import os
import re
from collections import deque
from pathlib import Path

from hypergumbo_core.discovery import find_files

#: How many include levels below the analysed file are read. Real include
#: chains stay well under this. The bound exists so a pathological graph
#: cannot make one file's walk unbounded; a header past it is still named.
MAX_INCLUDE_DEPTH = 32

#: What can be on the far end of an ``#include`` in a repository. An include
#: of any other extension is not found and stays in the stamp by name, which
#: is the safe direction.
INCLUDABLE_PATTERNS: tuple[str, ...] = (
    "*.h", "*.hh", "*.hpp", "*.hxx", "*.h++", "*.inc", "*.inl", "*.ipp",
    "*.tcc", "*.tpp", "*.def", "*.c", "*.cc", "*.cpp", "*.cxx",
)

_DIRECTIVE = re.compile(
    rb'^[ \t]*#[ \t]*include(?:_next)?[ \t]*(<[^>\r\n]+>|"[^"\r\n]+")',
    re.MULTILINE,
)


def include_directives(source: bytes) -> list[tuple[str, bool]]:
    """``(header, quoted)`` for every ``#include`` line, in source order.

    ``header`` is the text between the delimiters, stripped. A directive
    naming nothing (``#include MACRO``) yields nothing; see the module
    docstring.
    """
    out: list[tuple[str, bool]] = []
    for match in _DIRECTIVE.finditer(source):
        raw = match.group(1)
        header = raw[1:-1].decode("utf-8", "replace").strip()
        if header:
            out.append((header, raw[:1] == b'"'))
    return out


class IncludeClosure:
    """The include graph of one repository for one analysis run.

    Built lazily: the repository's includable files are listed on the first
    lookup that needs them, and each header's directives are read once.
    """

    def __init__(self, repo_root: Path) -> None:
        # Normalised, not resolved: a symlink inside the repository stays a
        # path inside it, and ``find_files`` keys its run index on the root
        # spelling the analyzer was given.
        self._repo_root = repo_root
        self._root = _absolute(repo_root)
        self._by_basename: dict[str, list[Path]] | None = None
        self._directives: dict[Path, list[tuple[str, bool]]] = {}

    def stamp(self, file_path: Path, source: bytes) -> list[str]:
        """Every header ``file_path`` can see, as written, deduplicated.

        The file's own includes come first, in source order, then the
        headers they reach, breadth-first. Empty only when the file has no
        ``#include`` that names a header.
        """
        names: list[str] = []
        seen_names: set[str] = set()
        current = _absolute(file_path)
        visited: set[Path] = {current}
        queue: deque[tuple[Path, str, bool, int]] = deque(
            (current, header, quoted, 1)
            for header, quoted in include_directives(source)
        )
        while queue:
            including, header, quoted, depth = queue.popleft()
            if header not in seen_names:
                seen_names.add(header)
                names.append(header)
            if depth > MAX_INCLUDE_DEPTH:
                continue
            for target in self._resolve(including, header, quoted):
                if target in visited:
                    continue
                visited.add(target)
                queue.extend(
                    (target, h, q, depth + 1) for h, q in self._read(target)
                )
        return names

    def _read(self, header: Path) -> list[tuple[str, bool]]:
        cached = self._directives.get(header)
        if cached is None:
            try:
                cached = include_directives(header.read_bytes())
            except OSError:
                # Unreadable: named, not read -- the absent case.
                cached = []
            self._directives[header] = cached
        return cached

    def _resolve(self, including: Path, header: str, quoted: bool) -> list[Path]:
        """The repository files an include can name; ``[]`` when none."""
        if quoted:
            beside = _absolute(including.parent / header)
            if beside.is_file() and self._inside(beside):
                return [beside]
        parts = [p for p in header.replace("\\", "/").split("/")
                 if p not in ("", ".", "..")]
        if not parts:
            return []
        suffix = "/" + "/".join(parts)
        return [
            path for path in self._index().get(parts[-1], ())
            if ("/" + path.relative_to(self._root).as_posix()).endswith(suffix)
        ]

    def _inside(self, path: Path) -> bool:
        return path == self._root or self._root in path.parents

    def _index(self) -> dict[str, list[Path]]:
        if self._by_basename is None:
            index: dict[str, list[Path]] = {}
            for path in find_files(
                self._repo_root, list(INCLUDABLE_PATTERNS),
                max_file_bytes=1 << 62, on_file_skipped=_ignore_skip,
            ):
                found = _absolute(path)
                index.setdefault(found.name, []).append(found)
            self._by_basename = index
        return self._by_basename


def _absolute(path: Path) -> Path:
    """``path`` made absolute with ``..`` folded, symlinks NOT followed."""
    return Path(os.path.normpath(os.path.abspath(path)))


def _ignore_skip(_path: Path, _size: int, _reason: str) -> None:
    """An includable file is listed, not analysed: a size skip is not news.

    Passed so ``find_files`` does not report the run's oversized files a
    second time through the global skip callback.
    """
