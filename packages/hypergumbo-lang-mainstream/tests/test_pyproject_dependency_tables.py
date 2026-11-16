# SPDX-License-Identifier: AGPL-3.0-or-later
"""Every dependency-declaring table of a ``pyproject.toml`` is read, by both readers.

Two readers look at the same tables: ``toml_config`` emits one
``kind="dependency"`` node per entry, and ``py_deps`` builds the manifest that
stamps an import's ``directness``. Both take the tables from
``py_deps.PYPROJECT_DEPENDENCY_TABLES``. The tests here check, per table of
that list, that each reader names the package it declares; that the role of
the table is on the node as ``meta["dependency_scope"]``; and that a node of
``[project].dependencies``, the one table read before, keeps its identity
whatever other table names the same package.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from hypergumbo_core.analyze.base import (
    make_dependency_stable_id,
    populate_kind_stable_ids,
    split_within_file_stable_id_collisions,
)
from hypergumbo_core.ir import Symbol
from hypergumbo_lang_mainstream.py_deps import (
    ANY_KEY,
    PYPROJECT_DEPENDENCY_TABLES,
    SHAPE_NAMES,
    SHAPE_REQUIREMENTS,
    DependencyTable,
    _load_pyproject,
    declared_distributions,
    is_interpreter_constraint,
    parse_python_dependencies,
    pep508_name,
)
from hypergumbo_lang_mainstream.toml_config import analyze_toml_files


def _dependencies(root: Path, text: str, name: str = "pyproject.toml") -> list[Symbol]:
    """The dependency nodes of one manifest, identities as a run would stamp them."""
    root.mkdir(parents=True, exist_ok=True)
    (root / name).write_text(text)
    symbols = list(analyze_toml_files(root).symbols)
    populate_kind_stable_ids(symbols)
    split_within_file_stable_id_collisions(symbols)
    return [s for s in symbols if s.kind == "dependency"]


def _scope(sym: Symbol) -> str | None:
    return (sym.meta or {}).get("dependency_scope")


def _concrete(table: DependencyTable) -> tuple[str, ...]:
    """The table's path with a real key in place of each wildcard."""
    return tuple("grp" if part == ANY_KEY else part for part in table.path)


def _manifest_declaring(table: DependencyTable, package: str) -> str:
    """A manifest that declares ``package`` in ``table`` and nothing else."""
    path = _concrete(table)
    if table.shape == SHAPE_REQUIREMENTS:
        return f"[{'.'.join(path[:-1])}]\n{path[-1]} = [\"{package}>=1\"]\n"
    return f"[{'.'.join(path)}]\n{package} = \"^1\"\n"


@pytest.mark.parametrize(
    "table", PYPROJECT_DEPENDENCY_TABLES, ids=lambda t: ".".join(t.path),
)
class TestEveryTableIsReadByBothReaders:
    def test_node_reader_emits_the_package_with_the_tables_role(
        self, tmp_path: Path, table: DependencyTable,
    ) -> None:
        deps = _dependencies(tmp_path, _manifest_declaring(table, "somepkg"))
        assert [(d.name, _scope(d)) for d in deps] == [("somepkg", table.scope)]

    def test_manifest_reader_names_the_package(
        self, tmp_path: Path, table: DependencyTable,
    ) -> None:
        (tmp_path / "pyproject.toml").write_text(_manifest_declaring(table, "somepkg"))
        data = _load_pyproject(tmp_path / "pyproject.toml")
        assert data is not None
        assert list(declared_distributions(data)) == [(table, "somepkg")]


class TestTheTableList:
    def test_named_tables_are_all_listed(self) -> None:
        paths = {".".join(t.path) for t in PYPROJECT_DEPENDENCY_TABLES}
        assert {
            "project.dependencies",
            "project.optional-dependencies.*",
            "dependency-groups.*",
            "build-system.requires",
            "tool.poetry.dependencies",
            "tool.poetry.dev-dependencies",
            "tool.poetry.group.*.dependencies",
            "tool.pdm.dev-dependencies.*",
            "tool.uv.dev-dependencies",
            "tool.hatch.envs.*.dependencies",
        } <= paths

    def test_roles_use_the_dependency_scope_vocabulary(self) -> None:
        assert {t.scope for t in PYPROJECT_DEPENDENCY_TABLES} == {
            None, "optional", "dev", "build",
        }
        assert {t.shape for t in PYPROJECT_DEPENDENCY_TABLES} == {
            SHAPE_REQUIREMENTS, SHAPE_NAMES,
        }

    def test_published_tables_are_the_runtime_ones_and_the_extras(self) -> None:
        published = {".".join(t.path) for t in PYPROJECT_DEPENDENCY_TABLES if t.published}
        assert published == {
            "project.dependencies",
            "project.optional-dependencies.*",
            "tool.poetry.dependencies",
        }

    def test_match_is_a_prefix_test_with_wildcards(self) -> None:
        table = DependencyTable(("tool", "x", ANY_KEY, "deps"), SHAPE_NAMES)
        assert table.match(("tool", "x", "any", "deps"))
        assert table.match(("tool", "x", "any", "deps", "pkg", "version"))
        assert not table.match(("tool", "x", "any"))
        assert not table.match(("tool", "y", "any", "deps"))


class TestPep508Name:
    @pytest.mark.parametrize("requirement, name", [
        ("click>=8.0", "click"),
        ("rich==13.0", "rich"),
        ("pydantic~=2.0", "pydantic"),
        ("requests<3.0", "requests"),
        ("hypothesis>4.0", "hypothesis"),
        ("pytest!=7.4.0", "pytest"),
        ("tomli; python_version < '3.11'", "tomli"),
        ("uvicorn[standard]>=0.20", "uvicorn"),
        ("ruamel.yaml", "ruamel.yaml"),
        ("zope.interface>=5", "zope.interface"),
        ("  click  ", "click"),
        ("requests (>=2)", "requests"),
        ("pip @ git+https://example.invalid/pip.git", "pip"),
        ("typing_extensions", "typing_extensions"),
    ])
    def test_name(self, requirement: str, name: str) -> None:
        assert pep508_name(requirement) == name

    @pytest.mark.parametrize("requirement", [
        "", "   ", "-e file:///${PROJECT_ROOT}/sub#egg=sub", "$weird",
    ])
    def test_not_a_requirement(self, requirement: str) -> None:
        assert pep508_name(requirement) is None

    def test_interpreter_constraint(self) -> None:
        assert is_interpreter_constraint("python")
        assert is_interpreter_constraint("Python")
        assert not is_interpreter_constraint("python-dateutil")


class TestNodeReaderEntryForms:
    def test_poetry_python_key_is_not_a_package(self, tmp_path: Path) -> None:
        deps = _dependencies(tmp_path, """
[tool.poetry.dependencies]
python = "^3.10"
click = "^8"
""")
        assert [d.name for d in deps] == ["click"]

    def test_poetry_table_valued_specs_name_the_key(self, tmp_path: Path) -> None:
        deps = _dependencies(tmp_path, """
[tool.poetry.dependencies]
plain = { version = "^2", extras = ["socks"] }
gitdep = { git = "https://example.invalid/gitdep.git", rev = "abc" }
pathdep = { path = "../pathdep", develop = true }
multi = [
    { version = "<=1.9", python = ">=3.6,<3.8" },
    { version = "^2.0", python = ">=3.8" },
]
""")
        assert [d.name for d in deps] == ["plain", "gitdep", "pathdep", "multi"]
        assert {_scope(d) for d in deps} == {None}
        assert len({d.stable_id for d in deps}) == 4

    def test_poetry_expanded_table_names_one_package(self, tmp_path: Path) -> None:
        deps = _dependencies(tmp_path, """
[tool.poetry.dependencies]
click = "^8"

[tool.poetry.dependencies.expanded]
version = "^2.2"
markers = "python_version < '3.11'"

[tool.poetry.dependencies.empty]
""")
        assert sorted(d.name for d in deps) == ["click", "empty", "expanded"]

    def test_poetry_dotted_keys_name_one_package(self, tmp_path: Path) -> None:
        deps = _dependencies(tmp_path, """
[tool.poetry.dependencies]
foo.version = "^1"
foo.extras = ["x"]
""")
        assert [d.name for d in deps] == ["foo"]

    def test_poetry_array_of_tables_names_one_package(self, tmp_path: Path) -> None:
        """Several constraints on one package, each its own ``[[...]]`` table."""
        deps = _dependencies(tmp_path, """
[[tool.poetry.dependencies.multi]]
version = "<=1.9"
python = ">=3.6,<3.8"

[[tool.poetry.dependencies.multi]]
version = "^2.0"
python = ">=3.8"
""")
        assert [d.name for d in deps] == ["multi"]

    def test_poetry_optional_flag_is_the_optional_role(self, tmp_path: Path) -> None:
        deps = _dependencies(tmp_path, """
[tool.poetry.dependencies]
inline = { version = "^3", optional = true }
required = { version = "^3", optional = false }
dotted.version = "^1"
dotted.optional = true

[tool.poetry.dependencies.expanded]
version = "^2"
optional = true

[tool.poetry.group.dev.dependencies]
grouped = { version = "^1", optional = true }
""")
        assert {d.name: _scope(d) for d in deps} == {
            "inline": "optional",
            "required": None,
            "dotted": "optional",
            "expanded": "optional",
            "grouped": "dev",
        }

    def test_include_group_entry_is_not_a_package(self, tmp_path: Path) -> None:
        deps = _dependencies(tmp_path, """
[dependency-groups]
test = ["pytest>=8", {include-group = "lint"}]
lint = ["ruff"]
""")
        assert sorted(d.name for d in deps) == ["pytest", "ruff"]
        assert {_scope(d) for d in deps} == {"dev"}

    def test_requirement_that_names_no_package_is_skipped(self, tmp_path: Path) -> None:
        deps = _dependencies(tmp_path, """
[tool.pdm.dev-dependencies]
dev = ["-e file:///${PROJECT_ROOT}/sub#egg=sub", "sub @ file:///x", "zope.interface>=5"]
""")
        assert [d.name for d in deps] == ["sub", "zope.interface"]

    def test_inline_and_dotted_spellings_of_a_table(self, tmp_path: Path) -> None:
        deps = _dependencies(tmp_path, """
dependency-groups.test = ["rootdotted"]

[project]
name = "demo"
optional-dependencies.docs = ["dotted"]

[tool.poetry]
dependencies = { inline = "^1", python = "^3.10" }

[tool.poetry.group.dev]
dependencies.grouped = "^1"

[tool.hatch.envs."py3.12"]
dependencies = ["quoted"]
""")
        assert {d.name: _scope(d) for d in deps} == {
            "rootdotted": "dev",
            "dotted": "optional",
            "inline": None,
            "grouped": "dev",
            "quoted": "dev",
        }

    def test_inline_table_spelling_of_the_extras(self, tmp_path: Path) -> None:
        deps = _dependencies(tmp_path, """
[project]
name = "demo"
optional-dependencies = { docs = ["sphinx"], test = ["pytest"] }
""")
        assert {d.name: _scope(d) for d in deps} == {
            "sphinx": "optional", "pytest": "optional",
        }

    @pytest.mark.parametrize("text", [
        '[tool.uv.sources]\nuvdep = { git = "https://example.invalid/uvdep.git" }\n',
        '[tool.setuptools.dynamic]\ndependencies = { file = ["requirements.txt"] }\n',
        '[tool.poetry.extras]\nsocks = ["pysocks"]\n',
        '[tool.uv]\nconstraint-dependencies = ["grpcio<1.65"]\n',
        '[project]\nname = "demo"\ndependencies = "not-an-array"\n',
        '[tool.poetry.group.dev]\noptional = true\n',
        '[[tool.poetry.source]]\nname = "private"\n',
    ])
    def test_table_that_declares_no_package(self, tmp_path: Path, text: str) -> None:
        assert _dependencies(tmp_path, text) == []

    def test_only_a_pyproject_is_read_as_one(self, tmp_path: Path) -> None:
        text = '[project]\ndependencies = ["click"]\n\n[dependency-groups]\ntest = ["pytest"]\n'
        assert _dependencies(tmp_path, text, name="other.toml") == []


_RUNTIME = """[project]
name = "demo"
dependencies = [
    "requests>=2",
    "click",
    "numpy<2; python_version < '3.9'",
    "numpy>=2; python_version >= '3.9'",
    "zope.interface>=5",
]
"""

_OTHER_TABLES_AFTER = """
[project.optional-dependencies]
docs = ["sphinx", "requests[socks]"]
all = ["sphinx", "requests[socks]"]

[tool.poetry.dependencies]
requests = "^2"
click = { version = "^8" }
numpy = "*"

[tool.poetry.group.dev.dependencies]
requests = "^2"

[tool.hatch.envs.default]
dependencies = ["requests", "numpy"]
"""

_OTHER_TABLES_BEFORE = """[build-system]
requires = ["setuptools", "requests", "numpy", "click"]

[dependency-groups]
test = ["requests", "click", "numpy"]

"""


def _identity(sym: Symbol) -> tuple:
    return (sym.name, sym.stable_id, sym.meta)


class TestExistingIdentitiesDoNotMove:
    """A ``[project].dependencies`` node is the same node whatever else the
    manifest declares, before or after it in the file."""

    def _runtime_nodes(self, deps: list[Symbol], first_line: int) -> list[Symbol]:
        # The runtime table's entries are the five lines after its key.
        return [d for d in deps if first_line <= d.span.start_line < first_line + 5]

    def test_other_tables_after_the_runtime_table(self, tmp_path: Path) -> None:
        alone = _dependencies(tmp_path / "a", _RUNTIME)
        both = _dependencies(tmp_path / "b", _RUNTIME + _OTHER_TABLES_AFTER)
        runtime = self._runtime_nodes(both, 4)
        assert [(d.id, *_identity(d)) for d in runtime] == [
            (d.id, *_identity(d)) for d in alone
        ]
        assert len(both) > len(alone)

    def test_other_tables_before_the_runtime_table(self, tmp_path: Path) -> None:
        alone = _dependencies(tmp_path / "a", _RUNTIME)
        both = _dependencies(tmp_path / "b", _OTHER_TABLES_BEFORE + _RUNTIME)
        shift = _OTHER_TABLES_BEFORE.count("\n")
        runtime = self._runtime_nodes(both, 4 + shift)
        assert [_identity(d) for d in runtime] == [_identity(d) for d in alone]
        assert len(both) == len(alone) + 7

    def test_runtime_node_keeps_the_unscoped_table_value(self, tmp_path: Path) -> None:
        both = _dependencies(
            tmp_path, _OTHER_TABLES_BEFORE + _RUNTIME + _OTHER_TABLES_AFTER)
        by_id = {d.stable_id: d for d in both}
        for name in ("requests", "click"):
            holder = by_id[make_dependency_stable_id("toml", "pyproject.toml", name)]
            assert _scope(holder) is None
            assert holder.span.start_line > _OTHER_TABLES_BEFORE.count("\n")

    def test_every_declaration_is_its_own_identity(self, tmp_path: Path) -> None:
        both = _dependencies(
            tmp_path, _OTHER_TABLES_BEFORE + _RUNTIME + _OTHER_TABLES_AFTER)
        assert all(d.stable_id for d in both)
        assert len({d.stable_id for d in both}) == len(both)
        requests = [d for d in both if d.name == "requests"]
        assert sorted(str(_scope(d)) for d in requests) == [
            "None", "None", "build", "dev", "dev", "dev", "optional", "optional",
        ]

    def test_deleting_one_table_moves_no_other_identity(self, tmp_path: Path) -> None:
        with_docs = _dependencies(tmp_path / "a", """
[project.optional-dependencies]
docs = ["sphinx"]
all = ["sphinx"]
""")
        without_docs = _dependencies(tmp_path / "b", """
[project.optional-dependencies]

all = ["sphinx"]
""")
        assert len(with_docs) == 2
        assert [d.stable_id for d in without_docs] == [with_docs[1].stable_id]


def _write(root: Path, text: str) -> None:
    (root / "pyproject.toml").write_text(text)


class TestManifestReader:
    def test_dev_and_build_tables_add_to_a_published_manifest(
        self, tmp_path: Path,
    ) -> None:
        _write(tmp_path, """
[build-system]
requires = ["zzbuildonly"]

[project]
name = "demo"
dependencies = ["zzruntime"]

[project.optional-dependencies]
docs = ["zzextra"]

[dependency-groups]
test = ["zzgroup", {include-group = "lint"}]
lint = ["zzlint"]

[tool.poetry.group.dev.dependencies]
zzpoetrygroup = "^1"

[tool.poetry.dev-dependencies]
zzpoetrydev = "*"

[tool.pdm.dev-dependencies]
dev = ["zzpdm", "-e file:///${PROJECT_ROOT}/sub"]

[tool.uv]
dev-dependencies = ["zzuv"]

[tool.uv.sources]
zzsourceonly = { git = "https://example.invalid/x.git" }

[tool.hatch.envs.default]
dependencies = ["zzhatch"]
extra-dependencies = ["zzhatchextra"]
""")
        assert set(parse_python_dependencies(tmp_path).entries) == {
            "zzbuildonly", "zzruntime", "zzextra", "zzgroup", "zzlint",
            "zzpoetrygroup", "zzpoetrydev", "zzpdm", "zzuv", "zzhatch",
            "zzhatchextra",
        }

    @pytest.mark.parametrize("text", [
        '[build-system]\nrequires = ["setuptools>=61", "wheel"]\n',
        '[dependency-groups]\ntest = ["pytest"]\n',
        '[tool.poetry.dependencies]\npython = "^3.10"\n\n'
        '[tool.poetry.group.dev.dependencies]\npytest = "^8"\n',
        '[tool.uv]\ndev-dependencies = ["pytest"]\n',
    ])
    def test_dev_or_build_table_alone_opens_no_manifest(
        self, tmp_path: Path, text: str,
    ) -> None:
        """No published table was read, so no import may be called undeclared."""
        _write(tmp_path, text)
        manifest = parse_python_dependencies(tmp_path)
        assert manifest.entries == {}
        assert manifest.directness_for("python", "anything") is None

    def test_published_table_in_another_manifest_opens_it(self, tmp_path: Path) -> None:
        _write(tmp_path, '[dependency-groups]\ntest = ["zzgroup"]\n')
        (tmp_path / "pkg").mkdir()
        _write(tmp_path / "pkg", '[project]\nname = "pkg"\ndependencies = ["zzruntime"]\n')
        manifest = parse_python_dependencies(tmp_path)
        assert set(manifest.entries) == {"zzgroup", "zzruntime"}
        assert manifest.directness_for("python", "zzgroup") == "direct"
        assert manifest.directness_for("python", "zzother") == "undeclared"

    def test_value_of_the_wrong_type_declares_nothing(self) -> None:
        assert list(declared_distributions({
            "project": {"dependencies": "not a list",
                        "optional-dependencies": ["not", "a", "table"]},
            "dependency-groups": "not a table",
            "build-system": {"requires": [42, None, ""]},
            "tool": {"poetry": {"dependencies": ["not", "a", "table"],
                                "group": {"dev": "not a table"}},
                     "uv": "not a table"},
        })) == []

    def test_names_table_skips_python_and_non_string_keys(self) -> None:
        data = {"tool": {"poetry": {"dependencies": {
            "python": "^3.10", "click": "^8", 123: "x"}}}}
        assert [name for _, name in declared_distributions(data)] == ["click"]
