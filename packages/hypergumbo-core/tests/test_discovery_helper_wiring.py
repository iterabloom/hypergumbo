# SPDX-License-Identifier: AGPL-3.0-or-later
"""Every module-level ``find_<lang>_files`` helper is the discovery that runs (WI-riziv).

An analyzer module's ``find_<lang>_files`` helper reads as the analyzer's
file enumeration, and its tests are written against it. But a
``TreeSitterAnalyzer`` subclass that does not override ``_find_source_files``
enumerates with the base ``find_files(repo_root, self.file_patterns)``, so a
helper nothing calls is a second definition that only tests run. Two such
helpers carried filters production never applied: Hack's ``<?hh`` .php
sniffing (INV-pobap) and D's GCC dependency-file filter, which let
``gcc -MMD`` output be parsed as D. Fifty-seven more repeated the class's
patterns and could have drifted the same way.

The rule is structural, so it holds for helpers added later: a helper is
either *called* by production code (an analyzer's own ``analyze`` loop, its
``_find_source_files`` override, another module), or its body *delegates* to
an analyzer's ``_find_source_files``. Being passed as ``register_analyzer(...,
find_files=helper)`` does not count as a call: that feeds only the profile's
file count, and the analyzer may still enumerate something else. Calls
are matched by name, so a helper name defined in two modules (js_ts.py and
svelte.py both define ``find_svelte_files``) counts as wired if either is
called.
"""
from __future__ import annotations

import ast
import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
_HELPER = re.compile(r"find_\w+_files")


def _helpers(tree: ast.Module) -> list[ast.FunctionDef]:
    return [
        n for n in tree.body
        if isinstance(n, ast.FunctionDef) and _HELPER.fullmatch(n.name)
    ]


def _delegates(helper: ast.FunctionDef) -> bool:
    return any(
        isinstance(n, ast.Call)
        and isinstance(n.func, ast.Attribute)
        and n.func.attr == "_find_source_files"
        for n in ast.walk(helper)
    )


def _called_names(tree: ast.Module, skip: dict[str, ast.FunctionDef]) -> set[str]:
    """Names called anywhere in ``tree``, ignoring calls inside the helper itself."""
    called: set[str] = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.Call):
            func = n.func
            name = func.id if isinstance(func, ast.Name) else (
                func.attr if isinstance(func, ast.Attribute) else None
            )
            if name is None:
                continue
            own = skip.get(name)
            if own is not None and own.lineno <= n.lineno <= (own.end_lineno or own.lineno):
                continue
            called.add(name)
    return called


def unwired_helpers(src_roots: list[Path]) -> list[str]:
    """``path:line name`` for each helper that is neither called nor delegating."""
    trees = {
        path: ast.parse(path.read_text(encoding="utf-8"))
        for root in src_roots
        for path in sorted(root.rglob("*.py"))
    }
    defined = {path: _helpers(tree) for path, tree in trees.items()}
    candidates = {
        h.name for helpers in defined.values() for h in helpers if not _delegates(h)
    }
    called: set[str] = set()
    for path, tree in trees.items():
        own = {h.name: h for h in defined[path]}
        called |= _called_names(tree, own) & candidates
    return [
        f"{path}:{h.lineno} {h.name}"
        for path, helpers in defined.items()
        for h in helpers
        if h.name in candidates and h.name not in called
    ]


def test_every_helper_in_the_live_tree_is_wired() -> None:
    roots = sorted(REPO_ROOT.glob("packages/*/src"))
    assert len(roots) > 3
    offenders = unwired_helpers(roots)
    assert offenders == [], (
        "find_*_files helpers that no production code calls and that do not "
        "delegate to an analyzer's _find_source_files -- wire them as the "
        "analyzer's discovery or delete them:\n  " + "\n  ".join(offenders)
    )


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


def test_the_hack_shape_is_flagged(tmp_path: Path) -> None:
    """A filtering helper the analyzer never calls, registered only for the profile."""
    _write(tmp_path / "src" / "lang.py", (
        "def find_lang_files(repo_root):\n"
        "    return [p for p in find_files(repo_root, ['*.x']) if sniff(p)]\n"
        "\n"
        "@register_analyzer('lang', find_files=find_lang_files)\n"
        "def analyze_lang(repo_root):\n"
        "    return find_lang_files  # a reference, not a call\n"
    ))
    assert [o.rsplit(" ", 1)[1] for o in unwired_helpers([tmp_path / "src"])] == [
        "find_lang_files",
    ]


def test_a_call_a_cross_module_call_or_delegation_is_wired(tmp_path: Path) -> None:
    _write(tmp_path / "src" / "a.py", (
        "def find_a_files(repo_root):\n"
        "    return find_a_files(repo_root)  # recursion is not a production call\n"
        "\n"
        "class AAnalyzer:\n"
        "    def _find_source_files(self, repo_root):\n"
        "        yield from find_a_files(repo_root)\n"
    ))
    _write(tmp_path / "src" / "b.py", (
        "def find_b_files(repo_root):\n"
        "    yield from _analyzer._find_source_files(repo_root)\n"
        "\n"
        "def find_c_files(repo_root):\n"
        "    return []\n"
    ))
    _write(tmp_path / "src" / "c.py", "def run(root):\n    return b.find_c_files(root)\n")
    assert unwired_helpers([tmp_path / "src"]) == []
