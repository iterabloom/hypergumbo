# SPDX-License-Identifier: AGPL-3.0-or-later
"""A shipped COMMUNITY row only adds (ADR-0061 rulings 2-3, WI-nopam).

A catalogue file has one tier, declared on its ``provenance:`` line, and a
built-in file may name only the standard library. So the third-party rows that
used to sit inside built-in files moved to companion files declaring
``provenance: community``. Three families merge rows BY KEY, and in each the
move could have changed which row wins a key:

* ``function_summaries`` merges every file into one dict, full name and bare
  short name, in file-name order -- a community file that sorts first would
  have taken the short name a built-in file also claims;
* ``library_signatures`` and ``dataflow_patterns`` read only ``<lang>.yaml``, so
  a companion file would not have been read at all.

The rule these tests pin: community rows load AFTER the built-in rows, take a
key only when no built-in row holds it, and are found by the file's own
``language:`` line and declared tier, never by its name. The user's channel is
not touched here; it still loads last and wins.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

import hypergumbo_core.dataflow as df
import hypergumbo_core.library_signatures as ls
from hypergumbo_core.function_summaries import (
    FunctionSummary,
    clear_summary_cache,
    load_function_summaries,
)
from hypergumbo_core.yaml_catalogs import declares_community


def _summary(name: str, *, terminates: bool) -> str:
    """One summary row: a terminating one, or one that propagates arg 0."""
    if terminates:
        return (f'  - function: "{name}"\n    param_to_return: {{}}\n'
                f'    side_effect: true\n')
    return f'  - function: "{name}"\n    param_to_return: {{0: true}}\n'


def test_declares_community_reads_only_the_provenance_line() -> None:
    assert declares_community({"provenance": "community"})
    assert not declares_community({"provenance": "builtin"})
    assert not declares_community({})
    assert not declares_community(None)


class TestFunctionSummaries:
    def _load(
        self, tmp_path: Path, files: dict[str, str],
    ) -> dict[str, FunctionSummary]:
        for name, body in files.items():
            (tmp_path / name).write_text(body)
        clear_summary_cache()
        try:
            return load_function_summaries(tmp_path)
        finally:
            clear_summary_cache()

    def test_a_community_row_never_displaces_a_builtin_one(
        self, tmp_path: Path,
    ) -> None:
        """The community file SORTS FIRST on purpose: order by name would hand
        it both the full name and the short one."""
        rows = self._load(tmp_path, {
            "a_community.yaml": "provenance: community\nretrieved: 2026-01-01\n"
            "summaries:\n"
            + _summary("pkg.Error", terminates=True)
            + _summary("vendor.Error", terminates=True)
            + _summary("vendor.Only", terminates=True),
            "z_builtin.yaml": "provenance: builtin\nsummaries:\n"
            + _summary("pkg.Error", terminates=False),
        })
        assert rows["pkg.Error"].side_effect is False
        assert rows["Error"].function == "pkg.Error"
        # ...and it still ADDS what no built-in row holds.
        assert rows["vendor.Only"].side_effect is True
        assert rows["vendor.Error"].function == "vendor.Error"

    def test_builtin_files_still_merge_in_name_order(self, tmp_path: Path) -> None:
        """THE CONTROL: between two built-in files nothing changes -- the later
        file's full name wins and the earlier file keeps the short name."""
        rows = self._load(tmp_path, {
            "a.yaml": "provenance: builtin\nsummaries:\n"
            + _summary("pkg.Error", terminates=True),
            "b.yaml": "provenance: builtin\nsummaries:\n"
            + _summary("pkg.Error", terminates=False)
            + _summary("other.Error", terminates=False),
        })
        assert rows["pkg.Error"].side_effect is False
        assert rows["Error"].function == "pkg.Error"


class TestLibrarySignatures:
    @pytest.fixture
    def shipped(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> Iterator[Path]:
        monkeypatch.setattr(ls, "_DIR", tmp_path)
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "no-config"))
        ls.load_library_signatures.cache_clear()
        ls.load_library_package_variables.cache_clear()
        yield tmp_path
        ls.load_library_signatures.cache_clear()
        ls.load_library_package_variables.cache_clear()

    def test_a_community_companion_adds_but_never_displaces(
        self, shipped: Path,
    ) -> None:
        (shipped / "go.yaml").write_text(
            "provenance: builtin\nlanguage: go\n"
            "signatures:\n  net.Listen: net.Listener\n"
            "package_variables:\n  os.Stdin: os.File\n"
        )
        (shipped / "go-vendor.yaml").write_text(
            "provenance: community\nretrieved: 2026-01-01\nlanguage: go\n"
            "signatures:\n  net.Listen: vendor.Listener\n  vendor.Open: vendor.Handle\n"
            "package_variables:\n  os.Stdin: vendor.File\n  vendor.Default: vendor.Client\n"
        )
        sigs = ls.load_library_signatures("go")
        assert sigs == {"net.Listen": "net.Listener", "vendor.Open": "vendor.Handle"}
        pkg = ls.load_library_package_variables("go")
        assert pkg == {"os.Stdin": "os.File", "vendor.Default": "vendor.Client"}

    def test_a_companion_is_found_by_its_language_line_and_tier(
        self, shipped: Path,
    ) -> None:
        """Not by its name: a file named for go but declaring java is java's,
        and a built-in file other than ``<lang>.yaml`` is not a companion."""
        (shipped / "go-misnamed.yaml").write_text(
            "provenance: community\nretrieved: 2026-01-01\nlanguage: java\n"
            "signatures:\n  Files.walk: java.util.stream.Stream\n"
        )
        (shipped / "go-extra.yaml").write_text(
            "provenance: builtin\nlanguage: go\nsignatures:\n  x.Y: x.Z\n"
        )
        assert ls.load_library_signatures("go") == {}
        assert ls.load_library_signatures("java") == {
            "Files.walk": "java.util.stream.Stream",
        }

    def test_the_shipped_django_rows_are_a_community_companion(self) -> None:
        """The live tree: python.yaml is built-in and names no Django row, and
        the Django QuerySet rows still load, from their companion."""
        ls.load_library_signatures.cache_clear()
        builtin = ls._rows_from(ls._DIR / "python.yaml")
        assert not any(k.startswith("django.") for k in builtin)
        assert [p.name for p in ls._community_companions("python")] == [
            "python-django.yaml", "python-stdlib-dropins.yaml",
        ]
        assert ls.load_library_signatures("python")["django.db.models.filter"] \
            == "django.db.models"


class TestDataflowPatterns:
    @pytest.fixture
    def shipped(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> Iterator[Path]:
        monkeypatch.setattr(df, "_DATAFLOW_DIR", tmp_path)
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "no-config"))
        df._config_cache.clear()
        yield tmp_path
        df._config_cache.clear()

    def test_community_library_patterns_are_appended_and_nothing_else(
        self, shipped: Path,
    ) -> None:
        (shipped / "python.yaml").write_text(
            "provenance: builtin\nlanguage: python\nassignments: []\n"
            "library_patterns:\n  - match: '\\bjson\\.dump\\('\n    access_mode: write\n"
        )
        (shipped / "python-vendor.yaml").write_text(
            "provenance: community\nretrieved: 2026-01-01\nlanguage: python\n"
            "assignments:\n  - node: grammar_row_that_must_be_ignored\n"
            "library_patterns:\n  - match: '\\byaml\\.dump\\('\n    access_mode: write\n"
        )
        (shipped / "python-other.yaml").write_text(
            "provenance: builtin\nlanguage: python\n"
            "library_patterns:\n  - match: 'never'\n    access_mode: read\n"
        )
        (shipped / "go-vendor.yaml").write_text(
            "provenance: community\nretrieved: 2026-01-01\nlanguage: go\n"
            "library_patterns:\n  - match: 'not_python'\n    access_mode: read\n"
        )
        config = df.get_dataflow_config("python")
        assert config is not None
        assert [r["match"] for r in config.library_patterns] == [
            r"\bjson\.dump\(", r"\byaml\.dump\(",
        ]
        assert config.assignments == []

    def test_a_language_with_no_companion_is_unchanged(self, shipped: Path) -> None:
        (shipped / "go.yaml").write_text(
            "provenance: builtin\nlanguage: go\n"
            "library_patterns:\n  - match: '\\.Close\\('\n    access_mode: write\n"
        )
        config = df.get_dataflow_config("go")
        assert config is not None
        assert [r["match"] for r in config.library_patterns] == [r"\.Close\("]

    def test_the_shipped_pyyaml_rows_still_classify(self) -> None:
        """The live tree: the four PyYAML rows left python.yaml and still
        reach python's config, classifying exactly as before."""
        df._config_cache.clear()
        try:
            config = df.get_dataflow_config("python")
            assert config is not None
            sites = df.scan_library_patterns(
                "yaml.safe_dump(x, f)\nyaml.safe_load(f)\n", config,
            )
            assert {(s.line, s.access_mode) for s in sites} == {
                (1, "write"), (2, "read"),
            }
        finally:
            df._config_cache.clear()
