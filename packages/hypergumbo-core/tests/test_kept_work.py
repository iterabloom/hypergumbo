# SPDX-License-Identifier: AGPL-3.0-or-later
"""The two marks a function that keeps work for later calls puts on itself.

``kept_work.used`` on every call, and ``with kept_work.filling(...)`` around
the work it keeps. With no recorder installed both do nothing; with one, both
are handed to it unchanged. What the recorder does with them is tested with
the recorder (``tests/test_kept_work_contexts.py``).
"""
from __future__ import annotations

import contextlib
import sys
import types
from typing import Any, Hashable, Iterator

import pytest

from hypergumbo_core import kept_work


def _keeper() -> int:
    return 1


class _Recorder:
    def __init__(self) -> None:
        self.calls: list[tuple[str, Any, Hashable]] = []

    def used(self, fn: Any, entry: Hashable) -> None:
        self.calls.append(("used", fn, entry))

    @contextlib.contextmanager
    def filling(self, fn: Any, entry: Hashable) -> Iterator[None]:
        self.calls.append(("in", fn, entry))
        yield
        self.calls.append(("out", fn, entry))


def test_with_no_recorder_both_marks_do_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(kept_work, "HOOK", None)
    assert kept_work.used(_keeper, "a") is None
    with kept_work.filling(_keeper, "a"):
        done = _keeper()
    assert done == 1


def test_an_exception_inside_an_unrecorded_fill_passes_through(
        monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(kept_work, "HOOK", None)
    with pytest.raises(ValueError, match="refused"):
        with kept_work.filling(_keeper):
            raise ValueError("refused")


def test_with_a_recorder_both_marks_are_handed_to_it(monkeypatch: pytest.MonkeyPatch) -> None:
    recorder = _Recorder()
    monkeypatch.setattr(kept_work, "HOOK", recorder)
    kept_work.used(_keeper, ("k", 1))
    with kept_work.filling(_keeper, ("k", 1)):
        recorder.calls.append(("body", _keeper, None))
    kept_work.used(_keeper)
    assert recorder.calls == [("used", _keeper, ("k", 1)), ("in", _keeper, ("k", 1)),
                              ("body", _keeper, None), ("out", _keeper, ("k", 1)),
                              ("used", _keeper, None)]


def test_the_recorder_is_found_when_its_module_is_loaded(monkeypatch: pytest.MonkeyPatch) -> None:
    recorder = _Recorder()
    plugin = types.ModuleType(kept_work.RECORDER_MODULE)
    plugin.KEPT_WORK_RECORDER = recorder  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, kept_work.RECORDER_MODULE, plugin)
    assert kept_work.installed_recorder() is recorder


def test_no_recorder_module_or_one_without_a_recorder_is_none(
        monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delitem(sys.modules, kept_work.RECORDER_MODULE, raising=False)
    assert kept_work.installed_recorder() is None
    monkeypatch.setitem(sys.modules, kept_work.RECORDER_MODULE,
                        types.ModuleType(kept_work.RECORDER_MODULE))
    assert kept_work.installed_recorder() is None
