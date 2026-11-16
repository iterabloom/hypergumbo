# SPDX-License-Identifier: AGPL-3.0-or-later
# covers: packages/hypergumbo-core/src/hypergumbo_core/sketch.py
"""A larger sketch budget shows more, never less (ADR-0005).

With source, Entry Points was held to 2,000 tokens and Data Models to 1,500
at every budget, converted to 57 entries and 100 models by a constant cost
per entry. A 128k sketch showed the same 57 entry points as an 8k one (11%
of podman's, 13% of sqlalchemy's), and Source Files Content printed the
first files whole when they fit what was left, so two long files used up a
128k budget and files a 32k sketch had excerpted were gone.

The three rules that replace this are tested on their own, then together on
a generated repository at four budgets.
"""

from __future__ import annotations

import itertools
import random
import re
from pathlib import Path

import pytest

from hypergumbo_core import entrypoints
from hypergumbo_core.sketch import (
    _WITH_SOURCE_DATA_MODELS,
    _WITH_SOURCE_ENTRY_POINTS,
    _content_plan,
    _largest_fitting,
    _listing_budget,
    generate_sketch,
)

FLOOR = 500


@pytest.mark.parametrize(("share", "remaining", "with_source", "expected"), [
    (2100, 6400, False, 2100),       # no source: the section's share
    (2100, 6400, True, 2000),        # 8k with source: the floor, as before
    (1500, 4500, True, 1500),        # a small sketch: the share is under the floor
    (10000, 30000, True, 3900),      # 32k: 13% of what is left
    (40000, 120000, True, 15600),    # 128k: 13% of what is left
])
def test_listing_budget(share: int, remaining: int, with_source: bool, expected: int) -> None:
    assert _listing_budget(share, remaining, with_source, _WITH_SOURCE_ENTRY_POINTS) == expected


def test_the_with_source_limit_grows_with_the_budget() -> None:
    """The defect was a limit that did not: 2,000 tokens at 8k and at 128k."""
    for rule, share_pct in ((_WITH_SOURCE_ENTRY_POINTS, 33), (_WITH_SOURCE_DATA_MODELS, 20)):
        budgets = [_listing_budget(r * share_pct // 100, r, True, rule)
                   for r in (2000, 6500, 30000, 120000, 500000)]
        assert budgets == sorted(budgets)
        assert budgets[-1] > 10 * rule[1]          # far past the floor at a large budget
        # At 8k: the old limit or the section's share, whichever is smaller.
        assert budgets[1] == min(6500 * share_pct // 100, rule[1])


@pytest.mark.parametrize(("budget", "low", "high", "expected"), [
    (100, 5, 40, 10),      # ten entries of 10 tokens fit
    (1000, 5, 40, 40),     # everything fits
    (20, 5, 40, 5),        # not even the minimum: the minimum is shown
    (105, 5, 40, 10),
    (100, 5, 3, 5),        # fewer entries than the minimum
])
def test_largest_fitting(budget: int, low: int, high: int, expected: int) -> None:
    calls = []

    def cost(count: int) -> int:
        calls.append(count)
        return count * 10

    assert _largest_fitting(cost, budget, low, high) == expected
    assert len(calls) <= 8  # a bisection, not a scan


def test_content_plan_examples() -> None:
    # Every file fits its allowance: all whole.
    assert _content_plan([300, 200, 100], 10_000, FLOOR) == {0: 300, 1: 200, 2: 100}
    # One long file and a budget for half of it: cut, not dropped.
    assert _content_plan([8000], 4000, FLOOR) == {0: 4000}
    # A long first file no longer takes the budget: it is held to its rank's
    # share and the files after it are printed.
    plan = _content_plan([30_000, 9_000, 6_000, 20_000, 3_000], 20_000, FLOOR)
    assert set(plan) == {0, 1, 2, 3, 4}
    assert plan[0] < 30_000 and plan[0] == max(plan.values())
    assert plan[1] == plan[0] // 2 and plan[3] == plan[0] // 4
    # A file whose allowance is under the floor is left out, unless it is
    # shorter than the floor and fits whole.
    plan = _content_plan([4000, 4000, 4000, 200], 1500, FLOOR)
    assert set(plan) == {0, 3}
    assert _content_plan([], 5000, FLOOR) == {}
    assert _content_plan([4000], 0, FLOOR) == {}


def test_a_larger_budget_prints_every_file_a_smaller_one_did_at_no_less_depth() -> None:
    """The property the old rule broke, on 2,000 generated repositories."""
    rng = random.Random(7)  # noqa: S311 - a reproducible test sequence, not a secret
    grew = 0
    for _ in range(2000):
        sizes = [rng.choice([200, 600, 1500, 4000, 12_000, 40_000]) + rng.randint(0, 300)
                 for _ in range(rng.randint(1, 40))]
        previous: dict[int, int] = {}
        for budget in sorted(rng.sample(range(600, 120_000), 4)):
            plan = _content_plan(sizes, budget, FLOOR)
            assert sum(plan.values()) <= budget
            assert all(plan[i] <= sizes[i] for i in plan)
            assert all(plan[i] >= min(FLOOR, sizes[i]) for i in plan)
            assert all(plan.get(i, 0) >= tokens for i, tokens in previous.items())
            grew += len(plan) > len(previous) > 0
            previous = plan
    assert grew > 1000  # reach: budgets did differ in what they printed


# ---- the three rules together, through generate_sketch ----

def _repository(root: Path, modules: int = 90) -> Path:
    for i in range(modules):
        body = "\n".join(f"    step_{j} = {j} * value\n    total += step_{j}" for j in range(60))
        (root / f"tool_{i:02d}.py").write_text(
            f'"""Tool number {i}."""\nfrom dataclasses import dataclass\n\n\n'
            f"@dataclass\nclass Record{i}:\n    value: int\n\n\n"
            f"def main(value: int = {i}) -> int:\n"
            f'    """Run tool {i} from the command line."""\n    total = 0\n{body}\n    return total\n\n\n'
            f'if __name__ == "__main__":\n    main()\n')
    return root


def _shown(sketch: str) -> dict[str, object]:
    sections = {part.split("\n", 1)[0].strip(): part
                for part in re.split(r"(?m)^## ", sketch)[1:]}
    entry_points = sections.get("Entry Points", "")
    data_models = sections.get("Data Models", "")
    return {
        "entries": len(re.findall(r"(?m)^- `", entry_points)),
        "entries_cut": bool(re.search(r"(?m)^- \.\.\. and \d+ more", entry_points)),
        "models": len(re.findall(r"(?m)^  - `", data_models)),
        "models_cut": "more data models" in data_models,
        "files": set(re.findall(r"START of (\S+)", sections.get("Source Files Content", ""))),
    }


@pytest.fixture(scope="module")
def sketches(tmp_path_factory: pytest.TempPathFactory) -> dict[int, dict[str, object]]:
    repo = _repository(tmp_path_factory.mktemp("budget_scaling"))
    # The detector keeps at most 50 entry points of a repository under
    # 10,000 symbols, fewer than the old section limit of 57. Its cap is
    # lifted so that a repository small enough to generate here has more
    # entry points than the old limit showed.
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(entrypoints, "compute_entrypoint_cap", lambda _nodes: 500)
        return {budget: _shown(generate_sketch(repo, max_tokens=budget, with_source=True))
                for budget in (4000, 16000, 64000, 256000)}


def test_reach(sketches: dict[int, dict[str, object]]) -> None:
    """The repository has more entry points and data models than the old
    limits allowed (57 and 100 fit in 2,000 and 1,500 tokens), and the
    smallest budget cannot show them all."""
    assert sketches[4000]["entries_cut"] and sketches[4000]["models_cut"]
    assert 0 < sketches[4000]["entries"] < sketches[256000]["entries"]  # type: ignore[operator]
    assert sketches[256000]["entries"] > 57  # type: ignore[operator]
    assert sketches[4000]["files"]


def test_a_larger_budget_shows_more_entry_points_and_data_models(
        sketches: dict[int, dict[str, object]]) -> None:
    budgets = sorted(sketches)
    for key in ("entries", "models"):
        counts = [sketches[b][key] for b in budgets]
        assert counts == sorted(counts), (key, counts)  # type: ignore[type-var]
        assert counts[-1] > counts[0], (key, counts)    # type: ignore[operator]
    # The old limits: 57 entry points at every budget.
    assert sketches[64000]["entries"] > 57  # type: ignore[operator]
    # At the largest budget nothing is cut.
    assert not sketches[256000]["entries_cut"] and not sketches[256000]["models_cut"]
    assert sketches[256000]["models"] == 90


def test_a_larger_budget_prints_every_source_file_a_smaller_one_printed(
        sketches: dict[int, dict[str, object]]) -> None:
    budgets = sorted(sketches)
    for small, large in itertools.pairwise(budgets):
        assert sketches[small]["files"] <= sketches[large]["files"]  # type: ignore[operator]
    assert len(sketches[256000]["files"]) > len(sketches[4000]["files"])  # type: ignore[arg-type]
