# SPDX-License-Identifier: AGPL-3.0-or-later
"""A CI step that installs pytest with nothing else to constrain it pins it.

WHY. The cron's ``test-agent-infra`` step installed ``pytest pytest-xdist ...``
with no version, so a fresh container took the newest release. On 2026-11-16
that was pytest 9.2.0, and pytest 9.2.0 with pytest-xdist 3.8.0 ignores the
grouping ``--dist=loadgroup`` exists for: the probe module that marks itself
``xdist_group`` built its fixture on three workers, and
``test_smart_test_loadgroup.py`` failed there and nowhere else. pytest 9.0.3,
9.1.0 and 9.1.1 build it once.

WHICH LINES. A bare ``pytest`` in a ``pip install`` is harmless when a later
install in the same step (a Woodpecker step, or a GitHub Actions job) brings a
package's ``dev`` extra, because pip re-resolves pytest against that extra.
Today every such later install is ``hypergumbo-tracker[dev...]``, whose
``pytest-textual-snapshot~=1.1`` requires ``syrupy==4.8.0``, which requires
``pytest<9``: the nightly's 3.10 leg logged pytest 9.2.0 being uninstalled
for 8.4.2 by exactly that install. A pytest that nothing installs after it is
what the step runs, so it must carry the spec the packages' dev extras give
(``packages/hypergumbo-core`` and the ``hypergumbo`` meta-package agree on it,
and the second test below holds them to that).

The mypy pin in the same lines is held by ``test_smart_test_mypy_gate.py``;
this file is its pytest counterpart, read from the files rather than from a
list of steps, so a new step that installs pytest bare is caught too.
"""

# covers: .woodpecker/*.yml, .github/workflows/*.yml, packages/hypergumbo-core/pyproject.toml, packages/hypergumbo/pyproject.toml
from __future__ import annotations

import re
import shlex
from pathlib import Path

import pytest

# A HARD import: importorskip could only make this guard skip silently.
import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
CORE_PYPROJECT = REPO_ROOT / "packages" / "hypergumbo-core" / "pyproject.toml"
META_PYPROJECT = REPO_ROOT / "packages" / "hypergumbo" / "pyproject.toml"

#: ``"pytest~=9.0.2"`` inside a dependency list, not ``"pytest-cov..."``.
_PYTEST_REQUIREMENT = re.compile(r'"(pytest(?![\w-])[^"]*)"')
_NAME = re.compile(r"[A-Za-z0-9_.-]+")
#: A local package installed with an extra list that names ``dev``.
_DEV_EXTRA = re.compile(r"packages/[^\[\s]+\[[^\]]*\bdev\b[^\]]*\]")


def _dev_spec(pyproject: Path) -> str:
    text = pyproject.read_text(encoding="utf-8")
    dev = text[text.index("\ndev = ["):]
    dev = dev[:dev.index("]\n") + 1]
    found = _PYTEST_REQUIREMENT.findall(dev)
    assert len(found) == 1, (pyproject, found)
    return found[0]


def _installs(script: str) -> list[list[str]]:
    """The argument lists of every ``pip install`` in a shell script, in order."""
    lines = [line for line in script.splitlines() if not line.lstrip().startswith("#")]
    joined = re.sub(r"\\\n", " ", "\n".join(lines))
    found = []
    for command in re.split(r"&&|\|\||;|\n", joined):
        try:
            words = shlex.split(command)
        except ValueError:  # a quoted `;` split one command in two
            continue
        for at, word in enumerate(words[:-1]):
            if re.fullmatch(r"(\S*/)?pip3?", word) and words[at + 1] == "install":
                found.append(words[at + 2:])
                break
    return found


def _is_pytest(arg: str) -> bool:
    name = _NAME.match(arg)
    return name is not None and name.group(0).lower() == "pytest"


def unconstrained_pytest_specs(script: str) -> list[str]:
    """Each pytest requirement that no later dev-extra install re-resolves."""
    installs = _installs(script)
    specs = []
    for at, args in enumerate(installs):
        pytests = [a for a in args if _is_pytest(a)]
        later = " ".join(" ".join(a) for a in installs[at:])
        if pytests and not _DEV_EXTRA.search(later):
            specs.extend(pytests)
    return specs


def _steps() -> list[tuple[str, str, str]]:
    """``(file, step, script)``: a Woodpecker step, or a GitHub job's runs."""
    steps = []
    for path in sorted((REPO_ROOT / ".woodpecker").glob("*.yml")):
        for step in yaml.safe_load(path.read_text(encoding="utf-8"))["steps"]:
            steps.append((f".woodpecker/{path.name}", step["name"],
                          "\n".join(step.get("commands") or [])))
    for path in sorted((REPO_ROOT / ".github" / "workflows").glob("*.yml")):
        for name, job in yaml.safe_load(path.read_text(encoding="utf-8"))["jobs"].items():
            runs = [s.get("run", "") for s in job.get("steps", [])]
            steps.append((f".github/workflows/{path.name}", name, "\n".join(runs)))
    return steps


def _unpinned_steps() -> dict[tuple[str, str], list[str]]:
    found = {}
    for path, name, script in _steps():
        specs = unconstrained_pytest_specs(script)
        if specs:
            found[(path, name)] = specs
    return found


def test_the_packages_dev_extras_agree_on_pytest() -> None:
    assert _dev_spec(CORE_PYPROJECT) == _dev_spec(META_PYPROJECT)


def test_every_unconstrained_pytest_install_carries_the_dev_spec() -> None:
    spec = _dev_spec(CORE_PYPROJECT)
    found = _unpinned_steps()
    wrong = {where: specs for where, specs in found.items()
             if specs != [spec] * len(specs)}
    assert wrong == {}, f"each of these must install {spec!r}: {wrong}"


@pytest.mark.parametrize("where", [
    (".woodpecker/full-suite.yml", "test-agent-infra"),
    (".woodpecker/woodpecker.yml", "forge-arms"),
    (".github/workflows/full-suite.yml", "test-agent-infra"),
])
def test_the_steps_that_install_pytest_alone_are_seen(where: tuple[str, str]) -> None:
    """Reach: the scan above could pass by finding nothing."""
    assert where in _unpinned_steps()


@pytest.mark.parametrize("where", [
    (".woodpecker/full-suite.yml", "test-all-packages"),
    (".woodpecker/nightly.yml", "test-matrix"),
    (".woodpecker/woodpecker.yml", "pytest"),
])
def test_a_later_dev_extra_install_is_what_excuses_a_bare_pytest(
    where: tuple[str, str],
) -> None:
    """These install bare pytest first and the tracker's dev extra after it."""
    script = {(p, n): s for p, n, s in _steps()}[where]
    assert any("pytest" in args for args in _installs(script))
    assert unconstrained_pytest_specs(script) == []


@pytest.mark.parametrize("script, expected", [
    ("pip install pytest", ["pytest"]),
    ("pip install --upgrade pip && pip install 'pytest~=9.0.2'", ["pytest~=9.0.2"]),
    ("python -m pip install pytest-cov pytest", ["pytest"]),
    ("pip install pytest\npip install -e \"packages/hypergumbo-tracker[dev,tui]\"", []),
    ("pip install pytest \\\n  -e 'packages/hypergumbo-core[dev]'", []),
    ("pip install -e \"packages/hypergumbo-tracker[tui]\"\npip install pytest", ["pytest"]),
    ("pip install -e packages/hypergumbo-tracker[dev]\npip install pytest", ["pytest"]),
    ("# pip install pytest\npip install jsonschema", []),
    ("pip install pytest-xdist pytest_asyncio", []),
])
def test_the_scan(script: str, expected: list[str]) -> None:
    assert unconstrained_pytest_specs(script) == expected
