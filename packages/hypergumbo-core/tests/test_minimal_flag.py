# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for ``--minimal`` on the implicit-auto-analysis subcommands (WI-bikod).

WHAT ``--minimal`` IS FOR. Ten subcommands auto-run a full analysis when no
cached survey exists, by routing through ``_get_or_run_analysis``. That helper
called ``run_survey`` with no side-emission flags at all, so a caller who typed
``slice --files`` paid for survey's whole deliverable: three budget-tier preview
files, up to 25 per-route handler slices, and the sketch pre-computation block
(embedding-model config extraction + README extraction + symbol-mention
centrality over candidate files). ``slice --files`` reads none of it — only
``nodes`` and ``edges``. Measured on this monorepo, declining all three took a
cold analysis 517.7s → 455.0s.

WHY IT IS OPT-IN AND NOT THE DEFAULT. The owner's standing steer on the adjacent
surface (WI-pijal) is that those files earn their place; the 2026-08-17 ruling on
this item was to add an explicit flag so the skip happens "if that flag is
included (and only then)". So the defaults are untouched, and every assertion
about default behavior below is load-bearing rather than incidental.

THE PARITY TEST IS THE POINT. ``test_every_auto_analysis_command_accepts_minimal``
does not hardcode the ten command names — it walks the real parser, follows each
subparser's ``func`` default to the handler, and asks whether that handler's
source calls ``_get_or_run_analysis``. Any future subcommand that starts
auto-running an analysis is therefore required to offer the flag, and will fail
here if it does not. A hardcoded list would have gone stale silently, which is the
failure mode this file exists to prevent.
"""
from __future__ import annotations

import argparse
import inspect
from pathlib import Path
from typing import Any, Dict

import pytest

from hypergumbo_core import cli


def _subparser_map() -> Dict[str, argparse.ArgumentParser]:
    """Every subcommand name → its parser, from the real CLI definition."""
    parser = cli.build_parser()
    out: Dict[str, argparse.ArgumentParser] = {}
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            out.update(action.choices)
    return out


def _option_strings(parser: argparse.ArgumentParser) -> set[str]:
    return {opt for action in parser._actions for opt in action.option_strings}


def _auto_analysis_commands() -> Dict[str, argparse.ArgumentParser]:
    """Subcommands whose handler can implicitly run a full analysis.

    Derived, not listed: the handler is reached through the subparser's own
    ``func`` default, and "can auto-run" is decided by whether its source calls
    ``_get_or_run_analysis``.
    """
    found: Dict[str, argparse.ArgumentParser] = {}
    for name, sub in _subparser_map().items():
        handler = sub.get_default("func")
        if handler is None:  # pragma: no cover - every subcommand sets func
            continue
        try:
            source = inspect.getsource(handler)
        except (OSError, TypeError):  # pragma: no cover - handlers are real defs
            continue
        if "_get_or_run_analysis" in source:
            found[name] = sub
    return found


def test_the_derivation_finds_the_expected_population() -> None:
    """Control: the derivation must actually select commands.

    Without this, a broken derivation returning {} would make the parity test
    below pass vacuously — it would assert a property of the empty set.
    """
    commands = _auto_analysis_commands()
    assert len(commands) >= 8, (
        f"derivation selected only {sorted(commands)} — if the helper was "
        "renamed, the parity test below is now vacuous"
    )
    # slice is the motivating case and must be in any correct derivation.
    assert "slice" in commands


@pytest.mark.parametrize("name", sorted(_auto_analysis_commands()))
def test_every_auto_analysis_command_accepts_minimal(name: str) -> None:
    """Any command that can implicitly run an analysis must offer --minimal."""
    sub = _auto_analysis_commands()[name]
    assert "--minimal" in _option_strings(sub), (
        f"`{name}` can auto-run a full analysis but has no --minimal, so its "
        "users cannot decline side outputs the command never reads"
    )


def test_survey_does_not_offer_minimal() -> None:
    """`survey` keeps its own explicit flags; --minimal is not duplicated there.

    Pinned as a deliberate boundary, not an oversight: `survey` already exposes
    --budgets / --no-sketch-fan-out / --no-handler-slices, and its user typed the
    verb that produces those artifacts. --minimal exists for the callers that did
    NOT ask for a survey.
    """
    subs = _subparser_map()
    assert "--minimal" not in _option_strings(subs["survey"])
    assert "--no-handler-slices" in _option_strings(subs["survey"])


def test_minimal_defaults_to_false() -> None:
    parser = cli.build_parser()
    args = parser.parse_args(["slice", "--entry", "x"])
    assert getattr(args, "minimal", None) is False
    args = parser.parse_args(["slice", "--entry", "x", "--minimal"])
    assert args.minimal is True


class _RunSurveyRecorder:
    """Captures the kwargs ``_get_or_run_analysis`` hands to ``run_survey``."""

    def __init__(self, out_path: Path) -> None:
        self.calls: list[dict[str, Any]] = []
        self._out_path = out_path

    def __call__(self, **kwargs: Any) -> list[Path]:
        self.calls.append(kwargs)
        self._out_path.write_text("{}")
        return [self._out_path]


def _record_run_survey(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, *, minimal: bool,
) -> dict[str, Any]:
    """Drive _get_or_run_analysis past the cache and capture the run_survey call."""
    repo = tmp_path / "repo"
    repo.mkdir()
    survey = tmp_path / cli.CANONICAL_SURVEY_FILENAME
    recorder = _RunSurveyRecorder(survey)
    monkeypatch.setattr(cli, "run_survey", recorder)
    # Force the cache-miss branch: no cached survey anywhere.
    monkeypatch.setattr(cli, "_discover_input_file", lambda _root: None)
    cli._get_or_run_analysis(repo, show_progress=False, minimal=minimal)
    assert len(recorder.calls) == 1
    return recorder.calls[0]


def test_minimal_declines_all_three_side_outputs(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    """--minimal must turn off the fan-out, the slices AND the sketch block.

    All three are asserted together because each is a separate ``run_survey``
    parameter: passing two of the three would still emit files, and the whole
    point of the flag is that one word covers the set.
    """
    kwargs = _record_run_survey(monkeypatch, tmp_path, minimal=True)
    assert kwargs["no_sketch_fan_out"] is True
    assert kwargs["enable_handler_slices"] is False
    assert kwargs["include_sketch_precomputed"] is False


def test_without_minimal_the_defaults_are_untouched(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    """The default path must not acquire the skip as a side effect.

    This is the assertion that keeps the change opt-in. If it ever fails, every
    consumer of a cached survey silently lost the sketch block.
    """
    kwargs = _record_run_survey(monkeypatch, tmp_path, minimal=False)
    assert kwargs.get("no_sketch_fan_out", False) is False
    assert kwargs.get("enable_handler_slices", True) is True
    assert kwargs.get("include_sketch_precomputed", True) is True


def test_minimal_is_a_no_op_when_a_cached_survey_exists(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    """With a cache hit there is nothing to skip — and no analysis to run.

    Worth pinning because it bounds what a user should expect: --minimal changes
    what an analysis WRITES, so on a warm tree it changes nothing at all.
    """
    repo = tmp_path / "repo"
    repo.mkdir()
    cached = tmp_path / cli.CANONICAL_SURVEY_FILENAME
    cached.write_text("{}")
    calls: list[dict[str, Any]] = []
    monkeypatch.setattr(cli, "run_survey", lambda **kw: calls.append(kw) or [])
    monkeypatch.setattr(cli, "_discover_input_file", lambda _root: cached)

    path, was_cached, generated = cli._get_or_run_analysis(
        repo, show_progress=False, minimal=True,
    )
    assert (path, was_cached, generated) == (cached, True, [])
    assert calls == []
