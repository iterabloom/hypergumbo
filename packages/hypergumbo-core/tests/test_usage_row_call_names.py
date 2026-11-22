# SPDX-License-Identifier: AGPL-3.0-or-later
# covers: packages/hypergumbo-core/src/hypergumbo_core/framework_patterns.py
"""``usage_row_names_call``: the rows decide which calls an analyzer records.

An analyzer that finds a call handing a local callable to a library asks
whether any ``usage:`` row is written against the call's name, and records a
usage context only then. These tests pin what counts as "a row is written
against it": the row's kind, its name pattern, its file's language, and that
the answer follows the files (the memo is forgotten with the pattern cache).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from hypergumbo_core import framework_patterns
from hypergumbo_core.framework_patterns import (
    clear_pattern_cache,
    get_frameworks_dir,
    usage_row_names_call,
)


@pytest.fixture
def frameworks(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """An empty frameworks directory the loader reads instead of the shipped one."""
    monkeypatch.setattr(framework_patterns, "get_frameworks_dir", lambda: tmp_path)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "no-user-config"))
    clear_pattern_cache()
    yield tmp_path
    clear_pattern_cache()


def _file(directory: Path, stem: str, language: str, rows: str) -> None:
    (directory / f"{stem}.yaml").write_text(
        f"id: {stem}\nlanguage: {language}\npatterns:\n{rows}", encoding="utf-8")


_CALL = """\
  - concept: middleware
    usage:
      kind: "^call$"
      name: "^(app|router)\\\\.use$"
      position: "^args"
"""


def test_a_call_row_names_its_calls_in_its_language(frameworks: Path) -> None:
    _file(frameworks, "webfw", "javascript", _CALL)

    assert usage_row_names_call("app.use", "javascript")
    assert usage_row_names_call("router.use", "javascript")
    # JavaScript and TypeScript are one set of candidate languages.
    assert usage_row_names_call("app.use", "typescript")
    # The name pattern decides, and it is the row's own (anchored here).
    assert not usage_row_names_call("app.used", "javascript")
    assert not usage_row_names_call("chai.use", "javascript")
    # A file of another language is not asked.
    assert not usage_row_names_call("app.use", "python")


def test_a_file_without_a_language_is_asked_for_every_language(frameworks: Path) -> None:
    _file(frameworks, "anyfw", "multi", _CALL)

    assert usage_row_names_call("app.use", "python")
    assert usage_row_names_call("app.use", "go")


def test_rows_that_name_no_call_do_not_count(frameworks: Path) -> None:
    _file(frameworks, "otherfw", "javascript", """\
  - concept: library_export
    usage:
      kind: "^library_export$"
      name: "^default$"
  - concept: http_client
    usage:
      kind: "^call$"
      position: "^args\\\\[0\\\\]$"
  - concept: task
    decorator: "^task$"
""")

    # Another kind of usage, a call row with no name, a definition row.
    assert not usage_row_names_call("default", "javascript")
    assert not usage_row_names_call("anything", "javascript")
    assert not usage_row_names_call("task", "javascript")


def test_a_row_without_a_kind_is_a_call_row(frameworks: Path) -> None:
    _file(frameworks, "loosefw", "python", """\
  - concept: command
    usage:
      name: "^fire\\\\.Fire$"
""")

    assert usage_row_names_call("fire.Fire", "python")


def test_the_users_own_framework_files_are_read(frameworks: Path) -> None:
    """An in-house framework described in ``frameworks.d`` gets its calls
    recorded like a shipped one."""
    channel = frameworks / "no-user-config" / "hypergumbo" / "frameworks.d"
    channel.mkdir(parents=True)
    _file(channel, "inhouse", "python", """\
  - concept: task
    usage:
      kind: "^call$"
      name: "^bus\\\\.subscribe$"
""")
    clear_pattern_cache()

    assert usage_row_names_call("bus.subscribe", "python")
    assert not usage_row_names_call("bus.publish", "python")


def test_the_answer_follows_the_files(frameworks: Path) -> None:
    assert not usage_row_names_call("app.use", "javascript")
    _file(frameworks, "webfw", "javascript", _CALL)
    # Memoised: the file written after the first question is not read ...
    assert not usage_row_names_call("app.use", "javascript")
    # ... until the pattern cache is cleared.
    clear_pattern_cache()
    assert usage_row_names_call("app.use", "javascript")


def test_the_shipped_rows_are_read() -> None:
    """Reach: the function reads the shipped directory when nothing is
    patched. express.yaml's route row names ``app.get``."""
    clear_pattern_cache()
    assert len(list(get_frameworks_dir().glob("*.yaml"))) > 100
    assert usage_row_names_call("app.get", "javascript")
    assert not usage_row_names_call("app.get", "cobol")
    assert not usage_row_names_call("no_row_names_this_call_zz", "javascript")
