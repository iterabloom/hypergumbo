# SPDX-License-Identifier: AGPL-3.0-or-later
# covers: packages/hypergumbo-core/src/hypergumbo_core/sketch.py
"""The sketch Configuration section prints a manifest value whole or not at all.

The readers used to be regular expressions over the manifest's text. One
stopped at the first quote, so a TOML inline table came out half-read
(``license: { text =``, ``version: {attr =``); none was scoped to the table
that defines a field, so a Maven module printed its parent's artifactId and a
``name`` key of any TOML table could be printed as the project's.

Each test gives a reader the shape that broke it and the plain shape beside
it, and ``_whole`` holds every printed line of every manifest kind to the
rule the defect broke.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from hypergumbo_core.sketch import _extract_config_heuristic, _manifest_value, _toml_table


def _lines(tmp_path: Path, name: str, content: str) -> list[str]:
    path = tmp_path / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)
    lines = [line for line in _extract_config_heuristic(tmp_path)
             if line.startswith(name + ":")]
    for line in lines:
        _whole(line)
    return lines


def _whole(line: str) -> None:
    """No value on the line is cut inside a table, a call or a quote."""
    for opener, closer in ("{}", "()", "[]"):
        assert line.count(opener) == line.count(closer), line
    assert line.count('"') % 2 == 0, line
    assert not line.rstrip().endswith("="), line


@pytest.mark.parametrize(("body", "expected"), [
    ('[project]\nname = "w"\nversion = "1.2"\nlicense = "MIT"\n',
     "pyproject.toml: name: w; version: 1.2; license: MIT"),
    ('[project]\nname = "w"\nversion = "1.2"\nlicense = { text = "AGPL-3.0-or-later" }\n',
     "pyproject.toml: name: w; version: 1.2; license: AGPL-3.0-or-later"),
    ('[project]\nname = "w"\ndynamic = ["version"]\nlicense = {file = "LICENSE"}\n'
     '[tool.setuptools.dynamic]\nversion = {attr = "w.__version__"}\n',
     "pyproject.toml: name: w; version: dynamic; license: file LICENSE"),
    # A licence table holding the whole licence text has no one-line form.
    ('[project]\nname = "w"\nlicense = { text = """line one\nline two""" }\n',
     "pyproject.toml: name: w"),
    # [tool.poetry] is where Poetry projects of the older layout say it.
    ('[tool.poetry]\nname = "w"\nversion = "0.3"\nlicense = "BSD"\n',
     "pyproject.toml: name: w; version: 0.3; license: BSD"),
    # A name or version key of another table is not the project's.
    ('[tool.other]\nname = "not-the-project"\nversion = "9"\n[project]\nname = "w"\n',
     "pyproject.toml: name: w"),
])
def test_pyproject(tmp_path: Path, body: str, expected: str) -> None:
    assert _lines(tmp_path, "pyproject.toml", body) == [expected]


@pytest.mark.parametrize("body", [
    '[tool.ruff]\nname = "not-a-project"\nversion = "9"\n',  # no project table
    'this is = = not toml\n',                                # does not parse
    'project = "a string, not a table"\n',
])
def test_a_pyproject_that_names_no_project_prints_nothing(tmp_path: Path, body: str) -> None:
    assert _lines(tmp_path, "pyproject.toml", body) == []


@pytest.mark.parametrize(("body", "expected"), [
    ('[package]\nname = "c"\nversion = "0.4.1"\nedition = "2021"\nlicense = "MIT"\n',
     "Cargo.toml: name: c; version: 0.4.1; edition: 2021; license: MIT"),
    # A binary target declared first is not the package.
    ('[[bin]]\nname = "tool"\n\n[package]\nname = "c"\nversion.workspace = true\n'
     'license = { workspace = true }\n',
     "Cargo.toml: name: c; version: workspace; license: workspace"),
    ('[workspace]\nmembers = ["a"]\n[workspace.package]\nversion = "2.0"\nedition = "2024"\n',
     "Cargo.toml: version: 2.0; edition: 2024"),
])
def test_cargo(tmp_path: Path, body: str, expected: str) -> None:
    assert _lines(tmp_path, "Cargo.toml", body) == [expected]


@pytest.mark.parametrize(("body", "expected"), [
    ('{"name": "p", "version": "1.0.0", "license": "MIT"}',
     "package.json: name: p; version: 1.0.0; license: MIT"),
    ('{"name": "p", "license": {"type": "ISC", "url": "https://x.example"}}',
     "package.json: name: p; license: ISC"),
    ('{"name": "p", "version": {"major": 1}, "license": ["MIT", "ISC"]}',
     "package.json: name: p"),
    ('{"name": "p", "private": true, "version": ""}', "package.json: name: p"),
])
def test_package_json(tmp_path: Path, body: str, expected: str) -> None:
    assert _lines(tmp_path, "package.json", body) == [expected]


POM = """<?xml version="1.0"?>
<project xmlns="http://maven.apache.org/POM/4.0.0">
  <parent>
    <groupId>org.example</groupId>
    <artifactId>parent-pom</artifactId>
    <version>7.0</version>
  </parent>
  <artifactId>module-a</artifactId>
  <packaging>jar</packaging>
  <dependencies><dependency>
    <groupId>com.other</groupId><artifactId>dep</artifactId><version>1</version>
  </dependency></dependencies>
</project>
"""


def test_a_maven_module_prints_its_own_coordinates(tmp_path: Path) -> None:
    """groupId and version are inherited from the parent, as Maven resolves
    them; the artifactId is the module's, never the parent's."""
    assert _lines(tmp_path, "pom.xml", POM) == [
        "pom.xml: groupId: org.example; artifactId: module-a; version: 7.0; packaging: jar"]
    plain = "<project><groupId>g</groupId><artifactId>a</artifactId><version>1</version></project>"
    assert _lines(tmp_path, "pom.xml", plain) == [
        "pom.xml: groupId: g; artifactId: a; version: 1"]


@pytest.mark.parametrize("body", ["<project><artifactId>cut off", "<project></project>"])
def test_a_pom_with_nothing_readable_prints_nothing(tmp_path: Path, body: str) -> None:
    assert _lines(tmp_path, "pom.xml", body) == []


@pytest.mark.parametrize(("name", "body", "expected"), [
    ("build.gradle", "group = 'org.example'\nversion = '1.0'  // released\n",
     "build.gradle: group: org.example; version: 1.0"),
    ("build.gradle.kts", 'plugins { kotlin("jvm") }\ngroup = "org.example"\n'
     'version = findProperty("v") ?: "dev"\n',
     "build.gradle.kts: group: org.example; kotlin"),
    ("build.gradle", "group = rootProject.group\nversion rootProject.version\n"
     "apply plugin: 'java'\n", "build.gradle: java"),
    ("Gemfile", "source 'https://rubygems.org'\nruby '3.3.0'\ngem 'rails'\n",
     "Gemfile: ruby: 3.3.0; rails"),
    ("Gemfile", "ruby File.read('.ruby-version').strip\ngem \"puma\"\n", "Gemfile: puma"),
    ("mix.exs", 'def project do\n  [app: :demo, version: @version, elixir: "~> 1.15"]\nend\n',
     "mix.exs: app: demo; elixir: ~> 1.15"),
    ("go.mod", "module example.com/m\n\ngo 1.22\n", "go.mod: module: example.com/m; go: 1.22"),
])
def test_a_manifest_written_as_code_shows_literals_only(
        tmp_path: Path, name: str, body: str, expected: str) -> None:
    """An expression (``rootProject.group``, ``findProperty("v")``,
    ``@version``, ``File.read(...)``) is not a value the reader can show."""
    assert _lines(tmp_path, name, body) == [expected]


@pytest.mark.parametrize(("raw", "keys", "shown"), [
    ("MIT", (), "MIT"), (" 1.0 ", (), "1.0"), (3, (), "3"), (2.5, (), "2.5"),
    (True, (), None), ("", (), None), ("a\nb", (), None), (None, (), None), (["MIT"], (), None),
    ({"text": "MIT"}, ("text",), "MIT"), ({"text": "MIT"}, (), None),
    ({"text": 3}, ("text",), None), ({"file": "L"}, ("text",), None),
])
def test_manifest_value(raw: object, keys: tuple[str, ...], shown: str | None) -> None:
    assert _manifest_value(raw, table_keys=keys) == shown


def test_toml_table() -> None:
    data = {"a": {"b": {"c": 1}}, "s": "text"}
    assert _toml_table(data, "a", "b") == {"c": 1}
    assert _toml_table(data, "a", "missing") == {}
    assert _toml_table(data, "s") == {}
    assert _toml_table(data, "s", "deeper") == {}
    assert _toml_table(None, "a") == {}

