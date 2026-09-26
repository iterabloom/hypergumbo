# SPDX-License-Identifier: AGPL-3.0-or-later
"""WI-zilar: both bakeoff scripts tell a crashed (empty) iteration from a result.

``bakeoff-deep``'s end-to-end cases live in ``test_bakeoff_deep_integration.py``
(``TestAnEmptyIterationIsNotAResult``). ``bakeoff-broad``'s batch loop runs the
analysis inline, so its guard is pinned here through the shared predicate, over
the exact directory shape a crash leaves: ``out/cohort-001/iter-001/<repo>/``,
present and empty.
"""

from __future__ import annotations

import importlib.machinery
import importlib.util
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent


def _load(script: str):
    loader = importlib.machinery.SourceFileLoader(
        script.replace("-", "_"), str(REPO_ROOT / "scripts" / script),
    )
    spec = importlib.util.spec_from_loader(loader.name, loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


@pytest.mark.parametrize("script", ["bakeoff-broad", "bakeoff-deep"])
def test_a_crashed_iteration_holds_no_output(tmp_path: Path, script: str) -> None:
    mod = _load(script)
    cohort = tmp_path / "out" / "cohort-001"
    (cohort / "iter-001" / "django").mkdir(parents=True)
    assert mod._holds_output_files(str(cohort)) is False
    (cohort / "iter-001" / "django" / "hg.json").write_text("{}")
    assert mod._holds_output_files(str(cohort)) is True


@pytest.mark.parametrize("script", ["bakeoff-broad", "bakeoff-deep"])
def test_every_output_presence_check_uses_the_predicate(script: str) -> None:
    """The old spelling, ``os.listdir`` as a truth value over an output
    directory, is what an empty ``iter-001/`` satisfied. None may remain."""
    text = (REPO_ROOT / "scripts" / script).read_text()
    assert "os.path.isdir(out_dir) and os.listdir(out_dir)" not in text
    assert "and os.listdir(os.path.join(out_dir, d))" not in text
