# SPDX-License-Identifier: AGPL-3.0-or-later
"""One text is parsed as YAML once per process.

``load_catalog`` reads and parses its catalogue file on every call, and one
``verify-claims`` invocation makes about forty such calls for fifteen shipped
files (``load_builtin_taint_catalog`` alone reloads every catalogue, twice).
The parser is PyYAML's pure-Python one: 0.26 s for the fifteen files on an
idle machine, and 30% of the test time of the forty slowest test files
(87,522 documents; ``~/hypergumbo_lab_notebook/slow_tests_11142026``).

``yaml_catalogs.parse_yaml_once`` keeps what was parsed, keyed by the TEXT,
and the catalogue loaders go through it: ``load_yaml_strict`` (the I/O
catalogues and overlays), ``read_catalogue_yaml`` (taint catalogues, library
signatures, function summaries), the dataflow patterns and the catalogue
tiers. What is pinned here is the rule that makes that safe:

* the key is the whole text, so an edited file is a different key and is
  parsed again: nothing has to notice the edit;
* every caller gets its own copy, so one caller changing what it was handed
  changes nobody else's;
* a text that is refused is refused again on every call, naming the file of
  THAT call;
* the store is bounded.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from hypergumbo_core import io_boundary as iob
from hypergumbo_core import yaml_catalogs as yc
from hypergumbo_core.io_boundary import DuplicateYamlKeyError, load_catalog, load_yaml_strict


@pytest.fixture(autouse=True)
def _empty_store():
    yc._PARSED_YAML.clear()
    yield
    yc._PARSED_YAML.clear()


@pytest.fixture
def parses(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Every text that actually reaches the YAML parser."""
    seen: list[str] = []
    real = iob._parse_yaml_strict

    def spy(content: str, origin: str) -> object:
        seen.append(content)
        return real(content, origin)

    monkeypatch.setattr(iob, "_parse_yaml_strict", spy)
    return seen


def test_the_same_text_is_parsed_once(parses: list[str]) -> None:
    text = "a: 1\nb: [x, y]\n"
    first = load_yaml_strict(text, origin="one.yaml")
    second = load_yaml_strict(text, origin="two.yaml")
    assert first == second == {"a": 1, "b": ["x", "y"]}
    assert parses == [text]


def test_each_caller_gets_its_own_copy(parses: list[str]) -> None:
    text = "rows:\n  - {name: open, kinds: [fs_read]}\n"
    first = load_yaml_strict(text, origin="c.yaml")
    first["rows"][0]["kinds"].append("fs_write")
    first["added"] = True
    assert load_yaml_strict(text, origin="c.yaml") == {
        "rows": [{"name": "open", "kinds": ["fs_read"]}]}
    assert len(parses) == 1


def test_an_edited_text_is_parsed_again(parses: list[str]) -> None:
    assert load_yaml_strict("a: 1\n", origin="f.yaml") == {"a": 1}
    assert load_yaml_strict("a: 2\n", origin="f.yaml") == {"a": 2}
    assert load_yaml_strict("a: 1\n", origin="f.yaml") == {"a": 1}
    assert parses == ["a: 1\n", "a: 2\n"]


@pytest.mark.parametrize("text", ["", "# only a comment\n", "null\n"])
def test_an_empty_document_is_kept_too(text: str, parses: list[str]) -> None:
    assert load_yaml_strict(text, origin="e.yaml") is None
    assert load_yaml_strict(text, origin="e.yaml") is None
    assert parses == [text]


def test_a_refused_text_is_refused_every_time_naming_its_own_file(parses: list[str]) -> None:
    text = "a: 1\na: 2\n"
    for origin in ("first.yaml", "second.yaml"):
        with pytest.raises(DuplicateYamlKeyError) as caught:
            load_yaml_strict(text, origin=origin)
        assert origin in str(caught.value)
    assert parses == [text, text]
    assert not yc._PARSED_YAML


def test_the_store_is_bounded(parses: list[str], monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(yc, "_PARSED_YAML_MAX", 3)
    texts = [f"n: {n}\n" for n in range(5)]
    for text in texts:
        load_yaml_strict(text, origin="b.yaml")
    kept = lambda: [text for _parser, text in yc._PARSED_YAML]  # noqa: E731
    assert kept() == texts[2:]
    # The most recently USED text is the last to go.
    load_yaml_strict(texts[2], origin="b.yaml")
    load_yaml_strict("n: new\n", origin="b.yaml")
    assert kept() == [texts[4], texts[2], "n: new\n"]
    # An evicted text is parsed again, with the same answer.
    assert load_yaml_strict(texts[0], origin="b.yaml") == {"n": 0}
    assert parses.count(texts[0]) == 2


def test_a_catalogue_loaded_twice_is_parsed_once(parses: list[str]) -> None:
    first = load_catalog("python")
    count = len(parses)
    assert count >= 1, "the first load must reach the parser"
    second = load_catalog("python")
    assert len(parses) == count
    assert first is not second
    assert first.primitives == second.primitives and first.primitives
    assert first.primitives is not second.primitives


def test_an_edited_overlay_file_is_seen(tmp_path: Path, parses: list[str]) -> None:
    """The production path: the same PATH, loaded before and after an edit."""
    overlay = tmp_path / "overlay.yaml"
    row = ("language: python\nnet_send:\n"
           "  - module: {module}\n    functions: [post]\n")
    overlay.write_text(row.format(module="gamma_http"))
    before = load_catalog("python", overlay_paths=[overlay], include_defaults=False)
    overlay.write_text(row.format(module="delta_http"))
    after = load_catalog("python", overlay_paths=[overlay], include_defaults=False)
    modules_before = {p.module for p in before.primitives}
    modules_after = {p.module for p in after.primitives}
    assert "gamma_http" in modules_before and "delta_http" not in modules_before
    assert "delta_http" in modules_after and "gamma_http" not in modules_after


# --- the plain loader goes through the same store ------------------------------


def test_the_two_parsers_do_not_share_an_answer() -> None:
    """A duplicated key is refused by the strict parser and resolved to the
    last value by the plain one; one must not be handed the other's result."""
    text = "a: 1\na: 2\n"
    assert yc.safe_load_once(text) == {"a": 2}
    with pytest.raises(DuplicateYamlKeyError):
        load_yaml_strict(text, origin="dup.yaml")
    assert yc.safe_load_once(text) == {"a": 2}


def test_a_catalogue_file_read_twice_is_parsed_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    import yaml

    calls: list[str] = []
    real = yaml.safe_load
    monkeypatch.setattr(yaml, "safe_load", lambda text: (calls.append(text), real(text))[1])
    path = tmp_path / "sources.yaml"
    path.write_text("sources:\n  - {name: read}\n")
    first = yc.read_catalogue_yaml(path)
    first["sources"].append("changed by the caller")
    assert yc.read_catalogue_yaml(path) == {"sources": [{"name": "read"}]}
    assert len(calls) == 1
    path.write_text("sources: []\n")
    assert yc.read_catalogue_yaml(path) == {"sources": []}
    assert len(calls) == 2


def test_a_file_that_does_not_parse_is_refused_every_time(tmp_path: Path) -> None:
    path = tmp_path / "broken.yaml"
    path.write_text("a: [unclosed\n")
    for _ in range(2):
        with pytest.raises(yc.CatalogueError) as caught:
            yc.read_catalogue_yaml(path)
        assert str(path) in str(caught.value)
    assert not yc._PARSED_YAML
