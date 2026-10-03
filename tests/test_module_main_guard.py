# SPDX-License-Identifier: AGPL-3.0-or-later
"""An entry-point module must never answer ``python -m <module>`` with a silent 0.

THE INSTANCE (WI-burol). ``hypergumbo_core/cli.py`` defines ``main()`` but had
no ``if __name__ == "__main__":`` block. ``python -m hypergumbo_core.cli
verify-claims <repo> --claims ... --format json`` therefore imported the module,
ran NO command, and exited 0 with empty stdout and empty stderr. A
JSON-consuming driver read that as "no findings"; one measurement arm of
INV-mumov's verify-claims run was recorded as a silent zero that way. The
package's real module entry point is ``python -m hypergumbo_core``
(``hypergumbo_core/__main__.py``); the ``.cli`` spelling was the trap.

THE MECHANISM, NOT THE NAME. Python executes ANY importable module under
``-m``; one without a ``__main__`` block runs its top level and exits 0. That
is harmless for a library module nobody has reason to run. It is a false
success for a module that LOOKS like a program: one that defines a top-level
``main`` or is the target of a ``[project.scripts]`` console script. Those are
the modules this gate enumerates, across every ``packages/*/src`` tree.

TWO CHECKS, BECAUSE THE PROPERTY IS SEMANTIC.

* Syntactic: each enumerated module carries a top-level
  ``if __name__ == "__main__":`` block.
* Behavioral: ``python -m <module> --help`` run in a subprocess is NOT
  (exit 0 AND empty stdout AND empty stderr). A guard whose body does nothing
  passes the syntactic check and fails this one. A module may answer either
  by running (help text, exit 0) or by refusing loudly (non-zero exit naming
  the right form, as ``hypergumbo_core.cli`` does); both are acceptable, and
  the silent 0 is the one forbidden outcome.

REACH IS ASSERTED FIRST: the enumeration must contain the filed instance, so
a glob or parse change that empties the family cannot pass vacuously.
"""

# covers: packages/*/src/*, packages/*/pyproject.toml

from __future__ import annotations

import ast
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent


def _module_name(path: Path) -> str:
    """Dotted module name of a file under ``packages/<pkg>/src/``."""
    src = next(p for p in path.parents if p.name == "src")
    rel = path.relative_to(src).with_suffix("")
    return ".".join(rel.parts)


def _console_script_modules() -> set[str]:
    """Modules named as ``[project.scripts]`` targets in any package."""
    mods: set[str] = set()
    for pyproject in sorted(REPO_ROOT.glob("packages/*/pyproject.toml")):
        text = pyproject.read_text(encoding="utf-8")
        block = re.search(r"^\[project\.scripts\]\n(.*?)(?=^\[|\Z)", text, re.M | re.S)
        if block is None:
            continue
        for m in re.finditer(r'^\s*[\w.-]+\s*=\s*"([\w.]+):\w+"', block.group(1), re.M):
            mods.add(m.group(1))
    return mods


def _has_main_guard(tree: ast.Module) -> bool:
    for node in tree.body:
        if isinstance(node, ast.If):
            test = ast.unparse(node.test)
            if "__name__" in test and "__main__" in test:
                return True
    return False


def _entry_point_modules() -> dict[str, Path]:
    """Every module under ``packages/*/src`` that looks runnable.

    Runnable = defines a top-level ``main`` function, or is a console-script
    target. ``__main__.py`` files are excluded: they ARE the module entry
    point and are run by ``python -m <package>``, not ``-m <package>.__main__``.
    """
    scripts = _console_script_modules()
    found: dict[str, Path] = {}
    for path in sorted(REPO_ROOT.glob("packages/*/src/**/*.py")):
        if path.name == "__main__.py":
            continue
        name = _module_name(path)
        tree = ast.parse(path.read_text(encoding="utf-8"))
        defines_main = any(
            isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == "main"
            for n in tree.body
        )
        if defines_main or name in scripts:
            found[name] = path
    return found


ENTRY_POINTS = _entry_point_modules()


def test_enumeration_reaches_the_filed_instance() -> None:
    """Reach first: the family must contain the WI-burol module and a script target."""
    assert "hypergumbo_core.cli" in ENTRY_POINTS
    assert "hypergumbo_core.cli" in _console_script_modules()
    assert "hypergumbo_tracker.cli" in ENTRY_POINTS


@pytest.mark.parametrize("module", sorted(ENTRY_POINTS))
def test_entry_point_module_has_main_guard(module: str) -> None:
    tree = ast.parse(ENTRY_POINTS[module].read_text(encoding="utf-8"))
    assert _has_main_guard(tree), (
        f"{module} ({ENTRY_POINTS[module].relative_to(REPO_ROOT)}) defines an "
        "entry point but has no top-level `if __name__ == \"__main__\":` block, "
        f"so `python -m {module}` exits 0 having done nothing (WI-burol)."
    )


def _subprocess_env() -> dict[str, str]:
    """Pin the child to THIS tree's sources, ahead of any editable install."""
    env = dict(os.environ)
    srcs = [str(p) for p in sorted(REPO_ROOT.glob("packages/*/src"))]
    if env.get("PYTHONPATH"):
        srcs.append(env["PYTHONPATH"])
    env["PYTHONPATH"] = os.pathsep.join(srcs)
    return env


@pytest.mark.parametrize("module", sorted(ENTRY_POINTS))
def test_python_dash_m_is_not_a_silent_success(module: str, tmp_path: Path) -> None:
    proc = subprocess.run(
        [sys.executable, "-m", module, "--help"],
        cwd=tmp_path,
        env=_subprocess_env(),
        capture_output=True,
        text=True,
        timeout=180,
    )
    silent_success = (
        proc.returncode == 0 and not proc.stdout.strip() and not proc.stderr.strip()
    )
    assert not silent_success, (
        f"`python -m {module} --help` exited 0 with empty stdout and stderr: "
        "a wrong invocation reported as success (WI-burol)."
    )
