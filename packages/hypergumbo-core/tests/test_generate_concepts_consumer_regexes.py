# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for scripts/generate-concepts consumer-detection regexes.

INV-rikis: the consumer-detection regex set in scripts/generate-concepts
must recognize every place a concept string is read in hypergumbo-core
source. Prior to 2026-04-19 the regexes missed two common patterns that
appear throughout ``hypergumbo_core.entrypoints._detect_from_concepts``
— variable-name comparison (``concept_type == "X"``) and tuple/set/list
membership tests (``concept_type in ("X", "Y")``) — causing ~30
concepts to surface as ``inert`` in docs/CONCEPTS.md despite being live
in production. These tests lock in the broadened regex coverage so the
false-inert blind spot cannot silently regress.

The script lives under ``scripts/`` (not under a package) so we load it
via ``importlib.util.spec_from_file_location`` rather than a plain
import.
"""

from __future__ import annotations

import importlib.util
from importlib.machinery import SourceFileLoader
from pathlib import Path
from types import ModuleType


REPO_ROOT = Path(__file__).resolve().parents[3]
SCRIPT_PATH = REPO_ROOT / "scripts" / "generate-concepts"


def _load_script_module() -> ModuleType:
    """Load scripts/generate-concepts as an importable module.

    The script has no ``.py`` extension, so ``spec_from_file_location`` alone
    returns None; using ``SourceFileLoader`` as the explicit loader bypasses
    the extension check.
    """
    loader = SourceFileLoader("generate_concepts_script", str(SCRIPT_PATH))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    assert spec is not None
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


class TestExtractConsumersFromText:
    """Every concept-consumer shape used in production must be detected."""

    def setup_method(self) -> None:
        self.mod = _load_script_module()

    def test_has_concept_call(self) -> None:
        """``has_concept(x, "route")`` counts as a consumer of ``route``."""
        names = self.mod.extract_consumers_from_text(
            'if has_concept(sym, "route"):\n    pass\n'
        )
        assert "route" in names

    def test_get_concept_call(self) -> None:
        """``get_concept(x, "middleware")`` counts as a consumer of ``middleware``."""
        names = self.mod.extract_consumers_from_text(
            'data = get_concept(sym, "middleware")\n'
        )
        assert "middleware" in names

    def test_get_dict_concept_equality(self) -> None:
        """``.get("concept") == "X"`` is recognized."""
        names = self.mod.extract_consumers_from_text(
            'if c.get("concept") == "task":\n    pass\n'
        )
        assert "task" in names

    def test_bare_concept_equality(self) -> None:
        """Bare ``concept == "X"`` is recognized (legacy shape)."""
        names = self.mod.extract_consumers_from_text(
            'if concept == "route":\n    pass\n'
        )
        assert "route" in names

    def test_concept_variable_equality(self) -> None:
        """Variable-name comparison ``concept_type == "X"`` is recognized
        (INV-rikis regression test)."""
        names = self.mod.extract_consumers_from_text(
            'elif concept_type == "error_handler":\n    pass\n'
        )
        assert "error_handler" in names

    def test_concept_variable_inequality(self) -> None:
        """``concept_type != "X"`` is recognized."""
        names = self.mod.extract_consumers_from_text(
            'if concept_type != "ignored":\n    pass\n'
        )
        assert "ignored" in names

    def test_concept_variable_reverse_order(self) -> None:
        """``"X" == concept_type`` (literal on the left) is recognized."""
        names = self.mod.extract_consumers_from_text(
            'if "something" == concept_type:\n    pass\n'
        )
        assert "something" in names

    def test_other_concept_suffix_variable(self) -> None:
        """``current_concept == "X"`` and ``concept_kind == "X"`` both match."""
        names = self.mod.extract_consumers_from_text(
            'a = current_concept == "foo"\n'
            'b = concept_kind == "bar"\n'
        )
        assert "foo" in names
        assert "bar" in names

    def test_tuple_membership(self) -> None:
        """``concept_type in ("X", "Y")`` flags both X and Y as consumed
        (INV-rikis regression test)."""
        names = self.mod.extract_consumers_from_text(
            'elif concept_type in ("websocket_handler", "websocket_gateway"):\n'
            '    pass\n'
        )
        assert "websocket_handler" in names
        assert "websocket_gateway" in names

    def test_set_membership(self) -> None:
        """``concept_type in {"X", "Y"}`` is also recognized."""
        names = self.mod.extract_consumers_from_text(
            'if concept_type in {"graphql_resolver", "graphql_schema"}:\n'
            '    pass\n'
        )
        assert "graphql_resolver" in names
        assert "graphql_schema" in names

    def test_list_membership(self) -> None:
        """``concept_type in ["X", "Y"]`` is recognized."""
        names = self.mod.extract_consumers_from_text(
            'if concept_type in ["npm_bin", "cargo_binary", "pyproject_script"]:\n'
            '    pass\n'
        )
        assert "npm_bin" in names
        assert "cargo_binary" in names
        assert "pyproject_script" in names

    def test_not_in_membership(self) -> None:
        """``concept_type not in (...)`` is recognized."""
        names = self.mod.extract_consumers_from_text(
            'if concept_type not in ("route", "controller"):\n    pass\n'
        )
        assert "route" in names
        assert "controller" in names

    def test_multiline_tuple_membership(self) -> None:
        """Tuple body split across lines is still extracted (DOTALL)."""
        names = self.mod.extract_consumers_from_text(
            'if concept_type in (\n'
            '    "foo",\n'
            '    "bar",\n'
            '    "baz",\n'
            '):\n'
            '    pass\n'
        )
        assert {"foo", "bar", "baz"} <= names

    def test_unrelated_equality_not_matched(self) -> None:
        """A non-concept variable name must NOT trigger consumer detection."""
        names = self.mod.extract_consumers_from_text(
            'if user_role == "admin":\n    pass\n'
            'if level != "debug":\n    pass\n'
        )
        assert "admin" not in names
        assert "debug" not in names

    def test_unrelated_membership_not_matched(self) -> None:
        """``value in ("a", "b")`` with non-concept var must not match."""
        names = self.mod.extract_consumers_from_text(
            'if status in ("pending", "active"):\n    pass\n'
        )
        assert "pending" not in names
        assert "active" not in names

    def test_empty_text_no_names(self) -> None:
        """Empty input yields empty set."""
        assert self.mod.extract_consumers_from_text("") == set()

    def test_multiple_patterns_one_file(self) -> None:
        """A file mixing several consumer shapes yields the union."""
        names = self.mod.extract_consumers_from_text(
            'if has_concept(sym, "alpha"):\n'
            '    return\n'
            'if concept_type == "beta":\n'
            '    return\n'
            'if concept_type in ("gamma", "delta"):\n'
            '    return\n'
            'if c.get("concept") == "epsilon":\n'
            '    return\n'
        )
        assert names == {"alpha", "beta", "gamma", "delta", "epsilon"}

    def test_production_shape_entrypoints(self) -> None:
        """Regression: the exact shape used in entrypoints.py must surface
        every branch's concept names.

        The snippet below mirrors the production shape (as of 2026-04-19) of
        ``hypergumbo_core.entrypoints._detect_from_concepts``. If this test
        fails, docs/CONCEPTS.md is silently mis-classifying live concepts as
        inert again.
        """
        prod_like = '''
            for concept_dict in concepts_of(sym):
                concept_type = concept_dict.get("concept")
                if concept_type == "route":
                    add_route(sym)
                elif concept_type == "controller":
                    add_controller(sym)
                elif concept_type in ("websocket_handler", "websocket_gateway"):
                    add_websocket(sym)
                elif concept_type == "error_handler":
                    add_error_handler(sym)
                elif concept_type in ("npm_bin", "cargo_binary", "pyproject_script"):
                    add_bin_script(sym)
        '''
        names = self.mod.extract_consumers_from_text(prod_like)
        expected = {
            "route",
            "controller",
            "websocket_handler",
            "websocket_gateway",
            "error_handler",
            "npm_bin",
            "cargo_binary",
            "pyproject_script",
        }
        assert expected <= names


class TestExtractProgrammaticProducersFromText:
    """Concepts emitted as Python dict literals must be detected as producers.

    WI-tasab: py.py's main_guard detector (and future analyzers that construct
    ``{"concept": "X"}`` inline) used to surface as ``ghost`` in docs/CONCEPTS.md
    because scan_producers only looked at framework YAMLs. These tests pin the
    new programmatic-producer scan.
    """

    def setup_method(self) -> None:
        self.mod = _load_script_module()

    def test_double_quoted_concept_literal(self) -> None:
        names = self.mod.extract_programmatic_producers_from_text(
            'meta = {"concepts": [{"concept": "main_guard", "framework": "python"}]}\n'
        )
        assert "main_guard" in names

    def test_single_quoted_concept_literal(self) -> None:
        names = self.mod.extract_programmatic_producers_from_text(
            "meta = {'concepts': [{'concept': 'route'}]}\n"
        )
        assert "route" in names

    def test_multiple_literal_concepts_in_same_file(self) -> None:
        names = self.mod.extract_programmatic_producers_from_text(
            '{"concept": "alpha"}\n'
            '{"concept": "beta"}\n'
        )
        assert names == {"alpha", "beta"}

    def test_variable_concept_value_not_matched(self) -> None:
        """``{"concept": some_var}`` must NOT produce a spurious concept name.

        Variable forms (like framework_patterns.py's reflection back out of
        YAML-driven self.concept) are excluded on purpose — resolving the
        variable would require running the analyzer, and the underlying YAML
        already accounts for the concept.
        """
        names = self.mod.extract_programmatic_producers_from_text(
            '{"concept": self.concept, "framework": fw}\n'
            '{"concept": concept_type}\n'
        )
        assert names == set()

    def test_only_string_literals_are_extracted(self) -> None:
        """Numbers / bool / None following ``"concept":`` are ignored."""
        names = self.mod.extract_programmatic_producers_from_text(
            '{"concept": 42}\n'
            '{"concept": None}\n'
            '{"concept": True}\n'
        )
        assert names == set()

    def test_empty_text_no_names(self) -> None:
        assert self.mod.extract_programmatic_producers_from_text("") == set()


class TestScanProducersIntegration:
    """scan_producers must combine YAML + programmatic scans."""

    def setup_method(self) -> None:
        self.mod = _load_script_module()

    def test_yaml_producer_alone(self, tmp_path: Path) -> None:
        fw_dir = tmp_path / "frameworks"
        fw_dir.mkdir()
        (fw_dir / "myframework.yaml").write_text(
            "linkers:\n  - concept: route\n", encoding="utf-8"
        )
        producers = self.mod.scan_producers(fw_dir)
        assert "route" in producers
        assert "myframework" in producers["route"]

    def test_programmatic_producer_from_source_dir(
        self, tmp_path: Path
    ) -> None:
        """A .py file emitting a literal concept dict is credited as a producer."""
        fw_dir = tmp_path / "frameworks"
        fw_dir.mkdir()
        src_dir = tmp_path / "src"
        src_dir.mkdir()
        (src_dir / "py_analyzer.py").write_text(
            '# analyzer\n'
            'def detect_main_guard():\n'
            '    return {"concepts": [{"concept": "main_guard", "framework": "python"}]}\n',
            encoding="utf-8",
        )
        producers = self.mod.scan_producers(fw_dir, [src_dir])
        assert "main_guard" in producers
        assert "py_analyzer.py" in producers["main_guard"]

    def test_framework_patterns_py_skipped(self, tmp_path: Path) -> None:
        """framework_patterns.py's ``self.concept`` reflection must not be
        credited as a producer — its concept values come from YAML and are
        already counted via the YAML scan. Crediting the file would falsely
        promote every concept that ever flows through the pattern layer."""
        fw_dir = tmp_path / "frameworks"
        fw_dir.mkdir()
        src_dir = tmp_path / "src"
        src_dir.mkdir()
        (src_dir / "framework_patterns.py").write_text(
            '# reflection of YAML values\n'
            'def make_result():\n'
            '    return {"concept": "shouldnotshow"}\n',
            encoding="utf-8",
        )
        producers = self.mod.scan_producers(fw_dir, [src_dir])
        assert "shouldnotshow" not in producers

    def test_concept_utils_helper_skipped(self, tmp_path: Path) -> None:
        """``_concept_utils.py`` and variants are not credited — the helper
        discusses concepts abstractly but does not emit any real one."""
        fw_dir = tmp_path / "frameworks"
        fw_dir.mkdir()
        src_dir = tmp_path / "src"
        src_dir.mkdir()
        (src_dir / "_concept_utils.py").write_text(
            '# utility\n'
            'DEFAULT = {"concept": "also_spurious"}\n',
            encoding="utf-8",
        )
        producers = self.mod.scan_producers(fw_dir, [src_dir])
        assert "also_spurious" not in producers

    def test_no_source_dirs_yields_yaml_only(self, tmp_path: Path) -> None:
        """Omitting source_dirs preserves legacy YAML-only behavior."""
        fw_dir = tmp_path / "frameworks"
        fw_dir.mkdir()
        (fw_dir / "only_yaml.yaml").write_text(
            "linkers:\n  - concept: yaml_only\n", encoding="utf-8"
        )
        # Source dirs omitted — programmatic scan not run.
        producers = self.mod.scan_producers(fw_dir)
        assert "yaml_only" in producers


class TestProducerRegex:
    """The producer regex is unchanged but lock in its behavior so a future
    change to the consumer regex cannot accidentally break producer parsing."""

    def setup_method(self) -> None:
        self.mod = _load_script_module()

    def test_unquoted_concept(self) -> None:
        """``- concept: route`` is recognized."""
        match = self.mod._PRODUCER_RE.match("  - concept: route")
        assert match is not None
        assert match.group(1) == "route"

    def test_quoted_concept(self) -> None:
        """``- concept: "route"`` (quoted) is recognized."""
        match = self.mod._PRODUCER_RE.match('  - concept: "route"')
        assert match is not None
        assert match.group(1) == "route"

    def test_concept_with_trailing_comment(self) -> None:
        """Trailing comments on the same line are tolerated."""
        match = self.mod._PRODUCER_RE.match("  - concept: controller # Rails")
        assert match is not None
        assert match.group(1) == "controller"

    def test_non_concept_line_ignored(self) -> None:
        """Arbitrary YAML keys other than ``concept:`` are ignored."""
        assert self.mod._PRODUCER_RE.match("  - other_field: something") is None


class TestScanConsumersIntegration:
    """Integration: scanning a temporary source tree round-trips through
    scan_consumers."""

    def setup_method(self) -> None:
        self.mod = _load_script_module()

    def test_scan_consumers_flat_file(self, tmp_path: Path) -> None:
        """A single .py file under the scanned dir is picked up."""
        f = tmp_path / "worker.py"
        f.write_text(
            'if concept_type == "background_task":\n    run()\n',
            encoding="utf-8",
        )
        consumers = self.mod.scan_consumers(tmp_path)
        assert "background_task" in consumers
        assert "worker.py" in consumers["background_task"]

    def test_scan_consumers_skips_concept_utils_helper(
        self, tmp_path: Path
    ) -> None:
        """``_concept_utils.py`` must not be counted as a consumer (it is the
        shared utility — counting it would flag every concept it handles as
        live even when no real code reads it)."""
        f = tmp_path / "_concept_utils.py"
        f.write_text(
            'if concept_type == "should_stay_inert":\n    pass\n',
            encoding="utf-8",
        )
        consumers = self.mod.scan_consumers(tmp_path)
        assert "should_stay_inert" not in consumers

    def test_scan_consumers_recurses(self, tmp_path: Path) -> None:
        """Subdirectories are walked recursively."""
        subdir = tmp_path / "linkers"
        subdir.mkdir()
        f = subdir / "foo.py"
        f.write_text(
            'result = has_concept(sym, "nested_one")\n',
            encoding="utf-8",
        )
        consumers = self.mod.scan_consumers(tmp_path)
        assert "nested_one" in consumers
        assert "linkers/foo.py" in consumers["nested_one"]
