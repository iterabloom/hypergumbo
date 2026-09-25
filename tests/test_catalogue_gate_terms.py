# SPDX-License-Identifier: AGPL-3.0-or-later
"""INV-bigaz / INV-dohoj: the catalogue gate was keyed to ONE family of ten.

``smart-test`` unions the tests that read a changed catalogue YAML, and the
union worked — for ``io_primitives``. It was keyed on a regex naming that
directory and its overlays, which is 23 of the 169 YAML files the wheel ships.
Measured on dev 81f1a69f88, a change confined to ``frameworks/`` (107 files),
``dataflow_patterns/``, ``cfg_nodes/``, ``function_summaries/`` or
``taint_sources/`` selected ZERO tests, wrote a 0-test manifest, printed "no
test-relevant files changed (docs/config only)", and CI then skipped pytest.

So the terms are now DERIVED PER FAMILY from ``YAML_CATALOGS`` — the registry
``validate_registry()`` already gates against the tree — rather than written
into the selector. The tests here pin the three properties that makes it
worth: every registered family selects something, the io_primitives selection
never narrows, and an unregistered directory still selects by its own name
instead of selecting nothing.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
HELPER = REPO_ROOT / "scripts" / "catalogue_gate_terms.py"
REGISTRY = (
    REPO_ROOT
    / "packages/hypergumbo-core/src/hypergumbo_core/yaml_catalogs.py"
)

#: What the selector greps for today, written down so a narrowing is visible.
#: These three literals WERE the whole catalogue gate before this change.
HISTORICAL_IO_PRIMITIVES_ARMS = ("load_catalog", "io_primitives", "io_boundary")


def run_helper(changed: list[str], *args: str) -> list[str]:
    """Feed changed paths to the helper and return the terms it emits."""
    proc = subprocess.run(
        [sys.executable, str(HELPER), str(REPO_ROOT), *args],
        input="\n".join(changed),
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stderr
    return [line for line in proc.stdout.splitlines() if line]


def _tests_matching(needle: str) -> set[str]:
    """Every package or root test containing ``needle`` — the script's rule."""
    found = set()
    for base in sorted(REPO_ROOT.glob("packages/*/tests")):
        found |= {
            p.name
            for p in base.glob("test_*.py")
            if needle in p.read_text(encoding="utf-8", errors="ignore")
        }
    found |= {
        p.name
        for p in (REPO_ROOT / "tests").glob("test_*.py")
        if needle in p.read_text(encoding="utf-8", errors="ignore")
    }
    return found


def selection_for(changed: list[str]) -> set[str]:
    """The union smart-test would take for these changed paths."""
    selected: set[str] = set()
    for term in run_helper(changed):
        selected |= _tests_matching(term)
    return selected


def registered_directories() -> list[str]:
    """Every catalogue directory the live registry declares."""
    sys.path.insert(
        0, str(REPO_ROOT / "packages/hypergumbo-core/src")
    )
    try:
        from hypergumbo_core.yaml_catalogs import YAML_CATALOGS
    finally:
        sys.path.pop(0)
    return [spec.directory for spec in YAML_CATALOGS]


def a_yaml_under(directory: str) -> str:
    """A real shipped YAML in ``directory``, so the case is not synthetic."""
    base = (
        REPO_ROOT
        / "packages/hypergumbo-core/src/hypergumbo_core"
        / directory
    )
    files = sorted(base.glob("*.yaml"))
    assert files, f"{directory} ships no YAML — the registry and tree disagree"
    return str(files[0].relative_to(REPO_ROOT))


class TestTheResidualThisCloses:
    """The five directories measured selecting ZERO on 2026-09-16."""

    @pytest.mark.parametrize(
        "directory",
        [
            "frameworks",
            "dataflow_patterns",
            "cfg_nodes",
            "function_summaries",
            "taint_sources",
        ],
    )
    def test_a_change_to_this_family_now_selects_tests(
        self, directory: str
    ) -> None:
        selected = selection_for([a_yaml_under(directory)])
        assert selected, (
            f"a change confined to {directory}/ still selects nothing — "
            "smart-test will write a 0-test manifest and CI will skip pytest"
        )

    def test_frameworks_is_the_largest_family_and_was_wholly_dark(self) -> None:
        """107 of the 169 shipped YAMLs, and the old regex named none of them."""
        old_key = "(io_primitives|io_primitives_overlays)"
        assert "frameworks" not in old_key
        assert selection_for([a_yaml_under("frameworks")])


class TestEveryRegisteredFamilyIsCovered:
    """Derived from the registry, so the next family arrives covered."""

    def test_no_registered_family_selects_nothing(self) -> None:
        empty = [
            d
            for d in registered_directories()
            if not selection_for([a_yaml_under(d)])
        ]
        assert not empty, f"registered families selecting zero tests: {empty}"

    def test_the_terms_come_from_the_registry_not_from_a_list(
        self, tmp_path: Path
    ) -> None:
        """The differential arm: change the registry, the terms change.

        A test that only asserts the live tree passes would also pass against
        a hardcoded roster. This one swaps in a registry naming a loader that
        does not exist anywhere in the real one, and requires the helper to
        follow it."""
        fake = tmp_path / "yaml_catalogs.py"
        fake.write_text(
            "from dataclasses import dataclass\n"
            "@dataclass(frozen=True)\n"
            "class CatalogSpec:\n"
            "    directory: str\n"
            "    loader: str\n"
            "YAML_CATALOGS = (\n"
            '    CatalogSpec(directory="frameworks",'
            ' loader="hypergumbo_core.zzz_not_a_real_loader"),\n'
            ")\n"
        )
        terms = run_helper(
            [a_yaml_under("frameworks")], "--registry", str(fake)
        )
        assert "zzz_not_a_real_loader" in terms
        assert "framework_patterns" not in terms


class TestItNeverNarrows:
    """Widening a selector must not quietly drop what it already caught."""

    def test_io_primitives_still_reaches_every_historical_arm(self) -> None:
        was = set()
        for arm in HISTORICAL_IO_PRIMITIVES_ARMS:
            was |= _tests_matching(arm)
        now = selection_for([a_yaml_under("io_primitives")])
        assert was <= now, sorted(was - now)

    def test_the_overlay_family_still_reaches_them_too(self) -> None:
        was = set()
        for arm in HISTORICAL_IO_PRIMITIVES_ARMS:
            was |= _tests_matching(arm)
        now = selection_for([a_yaml_under("io_primitives_overlays")])
        assert was <= now, sorted(was - now)


class TestFamiliesThatShareALoader:
    """Two directories behind one loader are read by one code path."""

    def test_an_overlay_change_also_names_the_family_it_layers_onto(
        self,
    ) -> None:
        terms = run_helper([a_yaml_under("io_primitives_overlays")])
        assert "io_primitives" in terms, (
            "the overlay arm would narrow: the old selector fired one shared "
            "rule for both io_primitives directories"
        )

    def test_the_two_taint_halves_reach_each_other(self) -> None:
        assert "taint_sanitizers" in run_helper([a_yaml_under("taint_sources")])
        assert "taint_sources" in run_helper([a_yaml_under("taint_sanitizers")])

    def test_a_family_with_a_loader_of_its_own_gains_no_siblings(self) -> None:
        """The control: the rule must not smear every family into every other."""
        terms = run_helper([a_yaml_under("url_folding")])
        assert "io_primitives" not in terms
        assert "frameworks" not in terms


class TestAbsentIsNotEmpty:
    """An unknown directory must over-select, never select nothing."""

    def test_an_unregistered_data_directory_selects_by_its_own_name(
        self,
    ) -> None:
        terms = run_helper(
            [
                "packages/hypergumbo-core/src/hypergumbo_core/"
                "io_primitives/rust.yaml",
                "packages/hypergumbo-core/src/hypergumbo_core/"
                "not_yet_registered/thing.yaml",
            ]
        )
        assert "not_yet_registered" in terms

    def test_a_missing_registry_still_yields_the_directory_name(
        self, tmp_path: Path
    ) -> None:
        terms = run_helper(
            [a_yaml_under("frameworks")],
            "--registry",
            str(tmp_path / "nothing-here.py"),
        )
        assert terms == ["frameworks"]


class TestDataIsWhatIsNotPython:
    """The key is "not Python", not "is YAML" — the extension is not the point.

    ``url_folding/SCOPE.md`` is read on every run that folds a URL, by
    ``get_scoped_languages()``. INV-bigaz's statement is about a non-Python
    input the production code loads at runtime, and a YAML-shaped key selected
    nothing for it — the same "keyed to one instance" shape one level down from
    the defect this helper was written to fix.
    """

    def test_a_runtime_parsed_markdown_input_is_data(self) -> None:
        terms = run_helper(
            [
                "packages/hypergumbo-core/src/hypergumbo_core/"
                "url_folding/SCOPE.md"
            ]
        )
        assert "url_folding" in terms
        assert "load_url_folding_registry" in terms

    def test_it_really_is_read_at_runtime_and_not_just_shipped(self) -> None:
        """The premise, executed rather than asserted."""
        source = (
            REPO_ROOT
            / "packages/hypergumbo-core/src/hypergumbo_core/url_folding/__init__.py"
        ).read_text()
        assert 'scope_path = _url_folding_dir() / "SCOPE.md"' in source
        assert "scope_path.read_text" in source


class TestItSelectsCatalogueDataAndNothingElse:
    def test_a_python_source_file_is_not_a_catalogue_change(self) -> None:
        assert not run_helper(
            [
                "packages/hypergumbo-core/src/hypergumbo_core/io_boundary.py",
            ]
        )

    def test_a_python_file_in_a_subdirectory_is_not_data_either(self) -> None:
        """The widened key's real risk: every linker module is two deep."""
        assert not run_helper(
            [
                "packages/hypergumbo-core/src/hypergumbo_core/"
                "linkers/route_handler.py",
            ]
        )

    def test_a_doc_is_not_a_catalogue_change(self) -> None:
        assert not run_helper(["docs/measurements/0010-shapes.md"])

    def test_a_test_fixture_yaml_is_not_a_catalogue_change(self) -> None:
        assert not run_helper(
            ["packages/hypergumbo-core/tests/fixtures/thing/config.yaml"]
        )

    def test_the_registry_itself_is_where_this_says_it_is(self) -> None:
        """If the registry moves, the helper's default must move with it."""
        assert REGISTRY.exists()
