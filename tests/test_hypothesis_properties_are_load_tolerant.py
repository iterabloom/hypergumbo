# SPDX-License-Identifier: AGPL-3.0-or-later
"""Every Hypothesis property in the repo tolerates a loaded machine.

WI-lotig. Hypothesis has two timers that measure the MACHINE as well as the
code, and either one turns a descheduled worker into a red test:

* the per-example ``deadline`` (200 ms by default): one example stalled past
  it fails, the replay runs fast, and the run raises a ``FlakyFailure``
  wrapping ``DeadlineExceeded``;
* the ``too_slow`` health check: input generation that takes over about a
  second for the first examples fails with ``FailedHealthCheck``
  ("Input generation is slow").

Both fired in this repo's parallel runs, on properties whose code cannot fail
nondeterministically: ``test_compile_properties.py::test_idempotency`` (fixed
by WI-ruhul) and ``test_transcript_pipeline_properties.py``'s
``test_output_never_exceeds_input`` (twice on 2026-10-03; the owner reported
one more failure under CI load on 2026-11-11). Neither relaxation changes a property or its example
count.

WHY A GUARD AND NOT A GLOBAL PROFILE. A ``settings.register_profile`` has to be
LOADED to apply, from a conftest every run imports. CI runs each package's
tests alone and the top-level ``tests/`` on their own, so the profile would
need loading in several conftests, and a property added beside a conftest that
forgot it would inherit the defaults again with nothing to say so. An explicit
settings object on each property is visible where the property is read, and
this guard makes it mandatory.

The guard reads source rather than importing the modules: the CI step that
runs ``tests/`` installs no package, so the tracker package's test module
could not be imported there. What it accepts is deliberately narrow -- a
``settings(...)`` call, inline or bound to a module-level name, that says
``deadline=None`` and lists ``HealthCheck.too_slow`` -- so a value it cannot
read is a failure, not a pass.
"""
from __future__ import annotations

import ast
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


def _test_modules() -> list[Path]:
    return sorted(REPO_ROOT.glob("packages/*/tests/*.py")) + sorted(
        (REPO_ROOT / "tests").glob("*.py"))


def _uses_hypothesis(text: str) -> bool:
    return "from hypothesis" in text or "import hypothesis" in text


def _is_load_tolerant(call: ast.AST) -> bool:
    """A ``settings(...)`` call with ``deadline=None`` and ``too_slow`` suppressed."""
    if not (isinstance(call, ast.Call) and isinstance(call.func, ast.Name)
            and call.func.id == "settings"):
        return False
    keywords = {kw.arg: kw.value for kw in call.keywords}
    deadline = keywords.get("deadline")
    if not (isinstance(deadline, ast.Constant) and deadline.value is None):
        return False
    suppressed = keywords.get("suppress_health_check")
    if not isinstance(suppressed, (ast.List, ast.Tuple)):
        return False
    return any(isinstance(item, ast.Attribute) and item.attr == "too_slow"
               and isinstance(item.value, ast.Name) and item.value.id == "HealthCheck"
               for item in suppressed.elts)


def _decorator_name(node: ast.AST) -> str | None:
    target = node.func if isinstance(node, ast.Call) else node
    if isinstance(target, ast.Name):
        return target.id
    if isinstance(target, ast.Attribute):
        return target.attr
    return None


def offending_properties(path: Path) -> list[str]:
    """``@given`` tests in ``path`` that are not load-tolerant, by qualified name."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    tolerant_names = {
        target.id
        for node in tree.body if isinstance(node, ast.Assign) and _is_load_tolerant(node.value)
        for target in node.targets if isinstance(target, ast.Name)
    }
    offending: list[str] = []

    def visit(body: list[ast.stmt], prefix: str) -> None:
        for node in body:
            if isinstance(node, ast.ClassDef):
                visit(node.body, f"{prefix}{node.name}.")
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                decorators = node.decorator_list
                if not any(_decorator_name(d) == "given" for d in decorators):
                    continue
                if not any(
                    _is_load_tolerant(d)
                    or (isinstance(d, ast.Name) and d.id in tolerant_names)
                    for d in decorators
                ):
                    offending.append(f"{prefix}{node.name}")

    visit(tree.body, "")
    return offending


def _property_count(path: Path) -> int:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return sum(
        1 for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and any(_decorator_name(d) == "given" for d in node.decorator_list)
    )


def test_every_property_in_the_repo_is_load_tolerant() -> None:
    hypothesis_modules = [p for p in _test_modules()
                          if _uses_hypothesis(p.read_text(encoding="utf-8"))]
    # REACH: the two modules with properties today, and all 13 of their
    # properties. A guard that found none would pass by being blind.
    names = {p.name for p in hypothesis_modules}
    assert {"test_compile_properties.py", "test_transcript_pipeline_properties.py"} <= names, names
    assert sum(_property_count(p) for p in hypothesis_modules) >= 13
    offending = {
        p.relative_to(REPO_ROOT).as_posix(): bad
        for p in hypothesis_modules if (bad := offending_properties(p))
    }
    assert offending == {}, offending


def _write(tmp_path: Path, source: str) -> Path:
    path = tmp_path / "test_sample.py"
    path.write_text(source)
    return path


def test_control_a_property_with_default_timers_is_flagged(tmp_path: Path) -> None:
    path = _write(tmp_path, (
        "from hypothesis import given, settings, strategies as st\n"
        "class TestX:\n"
        "    @given(st.integers())\n"
        "    @settings(max_examples=50)\n"
        "    def test_p(self, x):\n"
        "        pass\n"
    ))
    assert offending_properties(path) == ["TestX.test_p"]


def test_control_half_a_relaxation_is_flagged(tmp_path: Path) -> None:
    """deadline=None alone still leaves the too_slow check, and vice versa."""
    path = _write(tmp_path, (
        "from hypothesis import HealthCheck, given, settings, strategies as st\n"
        "ONLY_DEADLINE = settings(deadline=None)\n"
        "ONLY_SLOW = settings(suppress_health_check=[HealthCheck.too_slow])\n"
        "@given(st.integers())\n"
        "@ONLY_DEADLINE\n"
        "def test_a(x):\n"
        "    pass\n"
        "@given(st.integers())\n"
        "@ONLY_SLOW\n"
        "def test_b(x):\n"
        "    pass\n"
    ))
    assert offending_properties(path) == ["test_a", "test_b"]


def test_a_shared_or_inline_tolerant_setting_passes(tmp_path: Path) -> None:
    path = _write(tmp_path, (
        "from hypothesis import HealthCheck, given, settings, strategies as st\n"
        "TOLERANT = settings(max_examples=5, deadline=None,\n"
        "                    suppress_health_check=[HealthCheck.too_slow])\n"
        "@given(st.integers())\n"
        "@TOLERANT\n"
        "def test_a(x):\n"
        "    pass\n"
        "@given(st.integers())\n"
        "@settings(deadline=None, suppress_health_check=(HealthCheck.too_slow,))\n"
        "def test_b(x):\n"
        "    pass\n"
    ))
    assert offending_properties(path) == []
