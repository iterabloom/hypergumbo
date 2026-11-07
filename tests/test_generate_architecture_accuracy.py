# SPDX-License-Identifier: AGPL-3.0-or-later
"""The numbers and lists in ``docs/ARCHITECTURE.md`` measure what they say.

A review of the generated doc found counts that were each computed from
something other than what their label names:

- "151 analyzers" counted every module in the language packages plus
  core's ``analyze/`` infrastructure; 119 modules register an analyzer.
- "8 convention patterns (always loaded)" counted YAML files carrying
  ``language: multi``; the always-loaded set is
  ``framework_patterns._CONVENTION_PATTERN_IDS`` (11 files).
- Key Abstractions split wrapped docstring lines on ``word:`` and
  published the fragments as fields.
- Most-Connected Symbols ranked builtins (``len``, ``str``) and counted
  test call sites.
- The linker how-to was copied from a docstring whose example could not
  run, with its indentation stripped.
- ``--check`` measured only commit distance, so a count could be wrong
  for 30 commits and still pass.

Each test pins the measured property, not a snapshot of today's number.
"""

from __future__ import annotations

import ast
import importlib.machinery
import importlib.util
import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
CORE_SRC = REPO_ROOT / "packages" / "hypergumbo-core" / "src" / "hypergumbo_core"


@pytest.fixture(scope="module")
def mod():
    script_path = REPO_ROOT / "scripts" / "generate-architecture"
    loader = importlib.machinery.SourceFileLoader(
        "generate_architecture_accuracy_under_test", str(script_path),
    )
    spec = importlib.util.spec_from_loader(loader.name, loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


def _decorated_modules(package_dir: Path, decorator: str) -> int:
    return sum(
        1 for f in package_dir.rglob("*.py")
        if re.search(rf"^@{decorator}\b", f.read_text(encoding="utf-8"), re.M)
    )


def test_analyzer_counts_are_registered_analyzers(mod):
    counts = mod.count_registered_analyzers()

    for short, pkg in [
        ("mainstream", "hypergumbo-lang-mainstream"),
        ("common", "hypergumbo-lang-common"),
        ("extended1", "hypergumbo-lang-extended1"),
        ("rust_analyzer", "hypergumbo-lang-rust-analyzer"),
        ("scip_python", "hypergumbo-lang-scip-python"),
    ]:
        expected = _decorated_modules(REPO_ROOT / "packages" / pkg / "src", "register_analyzer")
        assert expected > 0, pkg  # reach: the package has analyzers to count
        assert counts[short] == expected, short


def test_convention_patterns_are_the_always_loaded_set(mod):
    tree = ast.parse((CORE_SRC / "framework_patterns.py").read_text(encoding="utf-8"))
    ids = next(
        ast.literal_eval(node.value) for node in ast.walk(tree)
        if isinstance(node, ast.AnnAssign)
        and getattr(node.target, "id", "") == "_CONVENTION_PATTERN_IDS"
    )
    yaml_files = list((CORE_SRC / "frameworks").glob("*.yaml"))

    total, convention, framework = mod.count_framework_patterns()

    assert total == len(yaml_files)
    assert convention == len(ids)
    assert framework == total - len(ids)


def test_ir_abstractions_list_each_dataclass_field_once(mod):
    text = mod.extract_ir_abstractions()
    tree = ast.parse((CORE_SRC / "ir.py").read_text(encoding="utf-8"))
    classes = {
        n.name: n for n in tree.body
        if isinstance(n, ast.ClassDef) and n.name in {"Span", "Symbol", "Edge", "AnalysisRun"}
    }
    sections = re.split(r"^### ", text, flags=re.M)

    for name, node in classes.items():
        fields = [
            s.target.id for s in node.body
            if isinstance(s, ast.AnnAssign) and isinstance(s.target, ast.Name)
            and not s.target.id.startswith("_")
        ]
        section = next(s for s in sections if s.startswith(f"{name} "))
        listed = re.findall(r"^- `([^`]+)`", section, flags=re.M)
        assert fields, name  # reach: the class declares fields
        assert listed == fields, name


def test_top_symbols_skip_builtins_and_test_code(mod):
    def node(i, kind="function", test=False, path="pkg/src/a.py"):
        return {"id": i, "name": i, "kind": kind, "path": path,
                "supply_chain": {"is_test_file": test}}

    analysis = {
        "nodes": [
            node("core"), node("helper"),
            node("len", kind="external_symbol", path="<external>"),
            node("test_x", test=True, path="pkg/tests/test_a.py"),
        ],
        "edges": (
            [{"src": "helper", "dst": "core"}]
            + [{"src": "helper", "dst": "len"}] * 50
            + [{"src": "test_x", "dst": "helper"}] * 50
        ),
    }

    names = [s["name"] for s in mod.get_top_symbols(analysis)]

    assert "core" in names  # reach: a production symbol with an in-edge ranks
    assert "len" not in names
    assert "test_x" not in names
    assert "helper" not in names  # its only in-edges come from test code


def test_linker_howto_is_runnable_python(mod):
    howto = mod.extract_linker_howto()
    code = re.search(r"```python\n(.*?)```", howto, re.S)

    assert code, "the how-to carries a code example"
    tree = ast.parse(code.group(1))
    imported = {a.name for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) for a in n.names}
    used = {n.func.id for n in ast.walk(tree)
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)}
    assert {"LinkerActivation", "LinkerRequirement"} <= used
    assert used <= imported | {"link_ipc"}
    assert "cli.py" in howto  # the side-effect import that registers it


def test_module_inventory_skips_private_directories(mod):
    names = [n for mods in mod.categorize_modules().values() for n, _ in mods]

    assert any(".scip." in n for n in names)  # reach: the scip package is scanned
    # Only the directory parts: a private *file* that registers a linker
    # (``linkers._third_party_bases``) and ``__main__`` are listed on purpose.
    assert not any(
        part.startswith("_") for n in names for part in n.split(".")[1:-1]
    )


def test_cli_bucket_names_only_modules_that_exist(mod):
    cli = [n.rsplit(".", 1)[1] for n, _ in mod.categorize_modules()["CLI & I/O"]]

    assert set(cli) == set(mod.CLI_MODULE_NAMES)


def test_python_script_without_shebang_uses_its_docstring(mod, tmp_path):
    script = tmp_path / "helper_lib.py"
    script.write_text('"""Shared helpers for the schema generator."""\n', encoding="utf-8")

    assert mod._extract_script_description(script) == "Shared helpers for the schema generator."


def test_check_reports_a_stale_count(mod):
    expected = mod.cheap_count_lines()
    assert expected  # reach: there are counts to check
    doc = "\n".join(["# Architecture", *expected, "trailing prose"])
    assert mod.stale_count_lines(doc) == []

    first = expected[0]
    mutated = doc.replace(first, first.replace("**", "**9", 1), 1)
    assert mutated != doc

    assert mod.stale_count_lines(mutated) == [first]


def test_missing_package_root_fails_loudly(mod, monkeypatch, tmp_path):
    monkeypatch.setattr(mod, "REPO_ROOT", tmp_path)

    with pytest.raises(FileNotFoundError):
        mod.categorize_modules()
