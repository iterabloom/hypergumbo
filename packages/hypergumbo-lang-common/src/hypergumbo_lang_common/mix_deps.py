# SPDX-License-Identifier: AGPL-3.0-or-later
"""Mix dependency manifest parsing (``mix.exs`` + ``mix.lock``).

Parallel of ``py_deps.py`` / ``jvm_deps.py`` for Elixir (WI-juzaj). Builds the
``"hex"`` scoped table of a :class:`DependencyManifest` so that
``ir.create_boundary_nodes`` can stamp an Elixir boundary node
(``Phoenix.LiveView.push_event``, ``:telemetry.execute``) ``direct`` or
``transitive``. Without it every Elixir boundary node on a Phoenix app read
``directness=unknown`` -- and since installed dependency source is off by
default, boundary nodes are the main view of "which dependencies does my code
call, and how much".

Never executes ``mix.exs``
--------------------------
``mix.exs`` is Elixir code; running it (or ``mix deps``) would execute
arbitrary project code and need an Elixir toolchain. It is read statically:
comments are blanked (string-aware), the body of ``defp deps`` (or an inline
``deps: [...]`` keyword in ``project/0``) is bracket-matched, and every
top-level ``{:name, ...}`` tuple in that list is a declared dependency. A
dependency list built dynamically (``deps: deps(Mix.env())`` with branches,
list concatenation from another function) is read only as far as its literal
tuples go; nothing is guessed beyond them.

What counts as direct, transitive, or neither
---------------------------------------------
* Direct: every tuple in the deps list, whatever its options -- ``only:``
  and ``runtime: false`` deps are still declared by the project
  (``{:credo, "~> 1.7", only: [:dev, :test], runtime: false}``), and
  ``git:`` / ``github:`` deps are fetched from outside the repo.
* Neither: ``path:`` and ``in_umbrella:`` deps point at source inside the
  repository (umbrella siblings, local libraries) -- workspace code, not a
  third-party dependency. Same for every ``app: :name`` a walked ``mix.exs``
  declares for itself (umbrella apps), the ADR-0041 D8a workspace-member
  subtraction ``py_deps`` applies to pyproject names.
* Transitive: every other top-level key of ``mix.lock``. The lock key is the
  dependency's app name, which is what module references map to; the Hex
  package name in the lock tuple can differ and is deliberately not used.

The module -> package mapping itself (``Phoenix.LiveView`` ->
``phoenix_live_view``) lives in ``supply_chain.hex_module_package`` so the
manifest owns one rule for every caller.

Walk
----
Every ``mix.exs`` / ``mix.lock`` under the repo (umbrella ``apps/*/``),
skipping ``discovery.manifest_walk_skip()`` names, dot-dirs and directories a
discovery content rule claims (Mix ``deps/`` and ``_build/``), so a fetched
dependency's own ``mix.exs`` cannot declare deps on the project's behalf.
"""
from __future__ import annotations

import re
from pathlib import Path

from hypergumbo_core.supply_chain import DependencyManifest

_DEPS_FN_RE = re.compile(r"\bdefp?\s+deps\b[^\n]*?(?:\bdo\b|,\s*do:)")
_DEPS_KW_RE = re.compile(r"\bdeps:\s*\[")
_TUPLE_HEAD_RE = re.compile(r"\{\s*:([a-z_][a-zA-Z0-9_]*)\s*,?")
_WORKSPACE_OPT_RE = re.compile(r"\b(?:path|in_umbrella):")
_APP_RE = re.compile(r"\bapp:\s*:([a-z_][a-zA-Z0-9_]*)")
_LOCK_KEY_RE = re.compile(r'^\s*"([a-z_][a-zA-Z0-9_]*)"\s*:\s*\{', re.MULTILINE)


def _strip_comments(src: str) -> str:
    """Blank ``#`` comments, leaving ``#`` inside double-quoted strings alone.

    Character-level so ``"~> 1.0" # pinned`` keeps its string and loses the
    comment, while a ``github: "org/repo#branch"`` value survives intact.
    """
    out: list[str] = []
    in_string = False
    in_comment = False
    i = 0
    while i < len(src):
        ch = src[i]
        if in_comment:
            if ch == "\n":
                in_comment = False
                out.append(ch)
            i += 1
            continue
        if in_string:
            out.append(ch)
            if ch == "\\" and i + 1 < len(src):
                out.append(src[i + 1])
                i += 2
                continue
            if ch == '"':
                in_string = False
            i += 1
            continue
        if ch == '"':
            in_string = True
        elif ch == "#":
            in_comment = True
            i += 1
            continue
        out.append(ch)
        i += 1
    return "".join(out)


def _bracket_body(src: str, open_idx: int) -> str | None:
    """Return the text inside the ``[`` at ``open_idx`` up to its match.

    String-aware so a ``"]"`` inside a version or option string does not end
    the list early. None when the bracket never closes (truncated file).
    """
    depth = 0
    in_string = False
    i = open_idx
    while i < len(src):
        ch = src[i]
        if in_string:
            if ch == "\\":
                i += 2
                continue
            if ch == '"':
                in_string = False
        elif ch == '"':
            in_string = True
        elif ch in "[{":
            depth += 1
        elif ch in "]}":
            depth -= 1
            if depth == 0:
                return src[open_idx + 1:i]
        i += 1
    return None


def _top_level_tuples(body: str) -> list[str]:
    """Split a list body into its top-level ``{...}`` tuple texts."""
    tuples: list[str] = []
    depth = 0
    in_string = False
    start = -1
    i = 0
    while i < len(body):
        ch = body[i]
        if in_string:
            if ch == "\\":
                i += 2
                continue
            if ch == '"':
                in_string = False
        elif ch == '"':
            in_string = True
        elif ch in "[{":
            if depth == 0 and ch == "{":
                start = i
            depth += 1
        elif ch in "]}":
            depth -= 1
            if depth == 0 and ch == "}" and start >= 0:
                tuples.append(body[start:i + 1])
                start = -1
        i += 1
    return tuples


def _deps_list_bodies(src: str) -> list[str]:
    """Find the literal deps list(s) in a comment-stripped ``mix.exs``."""
    bodies: list[str] = []
    for regex in (_DEPS_FN_RE, _DEPS_KW_RE):
        for match in regex.finditer(src):
            open_idx = src.find("[", match.end() - 1)
            if open_idx < 0:
                continue
            body = _bracket_body(src, open_idx)
            if body is not None:
                bodies.append(body)
    return bodies


def parse_mix_exs(src: str) -> tuple[set[str], set[str], set[str]]:
    """Parse ``mix.exs`` text into ``(direct, workspace, own_apps)``.

    ``direct``: declared external deps. ``workspace``: ``path:`` /
    ``in_umbrella:`` deps (in-repo source). ``own_apps``: ``app: :name``
    values, the project's own OTP app name(s).
    """
    clean = _strip_comments(src)
    direct: set[str] = set()
    workspace: set[str] = set()
    for body in _deps_list_bodies(clean):
        for tup in _top_level_tuples(body):
            head = _TUPLE_HEAD_RE.match(tup)
            if head is None:
                continue
            name = head.group(1)
            if _WORKSPACE_OPT_RE.search(tup):
                workspace.add(name)
            else:
                direct.add(name)
    own_apps = set(_APP_RE.findall(clean))
    return direct, workspace, own_apps


def parse_mix_lock(src: str) -> set[str]:
    """Return the top-level dependency keys of a ``mix.lock`` file."""
    return set(_LOCK_KEY_RE.findall(src))


def _find_mix_files(repo_root: Path) -> tuple[list[Path], list[Path]]:
    """Walk ``repo_root`` for ``mix.exs`` and ``mix.lock`` files, sorted."""
    from hypergumbo_core.discovery import manifest_walk_skip, walks_into

    skip = manifest_walk_skip()
    exs: list[Path] = []
    locks: list[Path] = []
    stack: list[Path] = [repo_root]
    while stack:
        cur = stack.pop()
        try:
            entries = list(cur.iterdir())
        except OSError:  # pragma: no cover  # unreadable dir mid-walk
            continue
        for entry in entries:
            if entry.is_file():
                if entry.name == "mix.exs":
                    exs.append(entry)
                elif entry.name == "mix.lock":
                    locks.append(entry)
            elif walks_into(entry, skip):
                stack.append(entry)
    return sorted(exs), sorted(locks)


def _read(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None


def parse_mix_dependencies(repo_root: Path) -> DependencyManifest:
    """Parse every ``mix.exs`` / ``mix.lock`` under ``repo_root``.

    Returns a manifest whose ``scoped["hex"]`` maps dependency app names to
    ``{"direct": bool}``, or an empty manifest when the repo has no Mix
    project. Workspace deps and the project's own apps are removed.
    """
    exs_files, lock_files = _find_mix_files(repo_root)
    direct: set[str] = set()
    workspace: set[str] = set()
    for path in exs_files:
        src = _read(path)
        if src is None:
            continue
        d, w, own = parse_mix_exs(src)
        direct |= d
        workspace |= w | own
    locked: set[str] = set()
    for path in lock_files:
        src = _read(path)
        if src is not None:
            locked |= parse_mix_lock(src)

    table: dict[str, dict[str, bool]] = {}
    for name in locked - direct - workspace:
        table[name] = {"direct": False}
    for name in direct - workspace:
        table[name] = {"direct": True}
    if not table:
        return DependencyManifest()
    return DependencyManifest(scoped={"hex": table})
