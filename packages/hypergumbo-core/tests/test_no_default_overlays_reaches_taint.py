# SPDX-License-Identifier: AGPL-3.0-or-later
"""``--no-default-overlays`` omits community rows from the taint arm too (INV-fikoh).

ADR-0061 ruling 5: "``--no-default-overlays`` omits community rows from every
family, taint included." The flag removed the shipped community I/O overlays
from the boundary lookup only; the taint catalogue derived its sinks and
sources through ``load_catalog`` without it, so a community ``requests.post``
row still made a ``host_secret -> network`` claim ``violated`` under the flag,
with no community notice and the verdict disclosing nothing (measured on dev
d48540acc7; the end-to-end repro is in the mainstream package's
``test_no_default_overlays_omits_community_taint.py``).

Omitted under the flag, each family by its own switch:
* the community I/O rows the taint sinks and sources derive from
  (``load_builtin_taint_catalog(include_community=False)``);
* the shipped community taint files (``*_community.yaml``);
* the shipped community function summaries
  (``load_function_summaries(include_community=False)``);
* a community-declared overlay the operator's config reaches -- the seeded
  copies in io_primitives.d -- since the tier is the file's own line.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import pytest

import hypergumbo_core.io_boundary as iob
from hypergumbo_core.cli import (
    _loaded_catalogue_files,
    _omit_community_overlays,
    _resolve_io_overlay_origins,
)
from hypergumbo_core.function_summaries import (
    clear_summary_cache,
    load_function_summaries,
)
from hypergumbo_core.taint import load_builtin_taint_catalog

_SHIPPED_HTTP = Path(iob.__file__).parent / "io_primitives_overlays" / \
    "python-http-clients.yaml"


def _modules(entries: list) -> set[str]:
    return {e.module for e in entries}


class TestTheTaintCatalogue:
    def test_community_io_rows_and_community_taint_files_go(self) -> None:
        full = load_builtin_taint_catalog()
        bare = load_builtin_taint_catalog(include_community=False)
        # reach: the default carries both kinds of community row
        assert "requests" in _modules(full._sinks["python"])
        assert "cryptography.fernet" in _modules(full._sources["python"])
        # the flag drops both...
        assert "requests" not in _modules(bare._sinks["python"])
        assert "cryptography.fernet" not in _modules(bare._sources["python"])
        assert not any(
            s.qualified_name.startswith("cryptography.")
            for s in bare._sanitizers.get("python", [])
        )
        # ...and keeps the built-in rows (THE CONTROL).
        assert "crypto/aes" in _modules(bare._sources["go"])
        assert "subprocess" in _modules(bare._sinks["python"])


class TestFunctionSummaries:
    def test_community_summaries_go_and_the_answers_are_cached_apart(self) -> None:
        clear_summary_cache()
        try:
            bare = load_function_summaries(include_community=False)
            full = load_function_summaries()
            assert "django.db.models.filter" in full
            assert "django.db.models.filter" not in bare
            assert "fmt.Printf" in bare  # the built-in rows stay
            assert load_function_summaries(include_community=False) is bare
        finally:
            clear_summary_cache()


class TestTheOperatorsOverlays:
    def test_a_community_overlay_is_omitted_and_named(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str],
    ) -> None:
        seeded = tmp_path / _SHIPPED_HTTP.name
        seeded.write_text(_SHIPPED_HTTP.read_text())
        yours = tmp_path / "mine.yaml"
        yours.write_text("language: python\nstatus: overlay\nretrieved: 2026-01-01\n")
        missing = tmp_path / "missing.yaml"
        kept = _omit_community_overlays(
            [(seeded, "user_channel"), (yours, "config"), (missing, "cli")],
        )
        assert kept == [(yours, "config"), (missing, "cli")]
        err = capsys.readouterr().err
        assert str(seeded) in err and "--no-default-overlays" in err

    def test_nothing_to_omit_prints_nothing(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str],
    ) -> None:
        yours = tmp_path / "mine.yaml"
        yours.write_text("language: python\nstatus: overlay\nretrieved: 2026-01-01\n")
        assert _omit_community_overlays([(yours, "cli")]) == [(yours, "cli")]
        assert capsys.readouterr().err == ""

    def test_the_resolver_applies_it_only_under_the_flag(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        channel = tmp_path / "config" / "hypergumbo" / "io_primitives.d"
        channel.mkdir(parents=True)
        (channel / _SHIPPED_HTTP.name).write_text(_SHIPPED_HTTP.read_text())
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))

        def resolve(flag: bool) -> list[str]:
            args = argparse.Namespace(io_primitives=None, no_default_overlays=flag)
            return [p.name for p, _ in _resolve_io_overlay_origins(args)]

        assert resolve(False) == [_SHIPPED_HTTP.name]
        assert resolve(True) == []


def test_the_verdict_lists_no_community_file_the_run_did_not_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "none"))
    taint = {"taint_sources": [], "taint_sinks": [], "taint_sanitizers": []}

    def names(include: bool) -> set[str]:
        return {f.path.name for f in _loaded_catalogue_files(
            ["python"], [], include_default_overlays=include, taint_paths=taint,
        )}

    assert {"crypto_community.yaml", "python_django.yaml"} <= names(True)
    bare = names(False)
    assert "crypto_community.yaml" not in bare
    assert "python_django.yaml" not in bare
    assert "crypto.yaml" in bare
