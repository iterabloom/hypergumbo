# SPDX-License-Identifier: AGPL-3.0-or-later
"""A taint catalogue key nothing reads is refused, not ignored (WI-rinaz).

A user taint source file with ``attributes: [location]`` reported loaded and
contributed nothing: ``_load_source_yaml`` builds only ``functions:`` and
``methods:`` entries, and both affected claims' verdicts were identical to the
baseline. A dropped source or sink is the false-all-clear direction, so every
key the three loaders do not read now raises :class:`TaintCatalogError`, the
claims file's own rule (WI-bopoz). The shipped files must pass the same check.
"""
from __future__ import annotations

import contextlib
import io
from pathlib import Path

import pytest

from hypergumbo_core.cli import main
from hypergumbo_core.taint import (
    _TAINT_SANITIZERS_DIR,
    _TAINT_SINKS_DIR,
    _TAINT_SOURCES_DIR,
    TaintCatalogError,
    load_taint_catalog,
)

_SOURCE = "taint_label: untrusted_input\nsources:\n  javascript:\n    - module: document\n{extra}"
_SINK = "zone: network\ntrust_level: untrusted\nsinks:\n  python:\n    - module: mylib\n      functions: [ship]\n{extra}"
_SANITIZER = "transforms:\n  - input_taint: a\n    output_taint: b\n    functions:\n      python: [x.y]\n{extra}"


def _load(tmp_path: Path, kind: str, text: str) -> None:
    path = tmp_path / f"{kind}.yaml"
    path.write_text(text)
    args: dict[str, list[Path]] = {"sources": [], "sinks": [], "sanitizers": []}
    args[kind].append(path)
    load_taint_catalog(args["sources"], args["sinks"], args["sanitizers"])


def test_an_attributes_key_is_refused_and_says_where_attribute_sources_come_from(tmp_path: Path) -> None:
    with pytest.raises(TaintCatalogError, match=r"'attributes'.*io_primitives") as err:
        _load(tmp_path, "sources", _SOURCE.format(extra="      attributes: [location]\n"))
    assert "sources.javascript entry" in str(err.value)


@pytest.mark.parametrize("kind, text, where", [
    ("sources", _SOURCE.format(extra="      functions: [f]\n") + "label: x\n", "top-level"),
    ("sinks", _SINK.format(extra="      method: [send]\n"), "sinks.python entry"),
    ("sinks", _SINK.format(extra="") + "trust: trusted\n", "top-level"),
    ("sanitizers", _SANITIZER.format(extra="    output: c\n"), "transforms entry"),
    ("sanitizers", _SANITIZER.format(extra="") + "sanitizers: []\n", "top-level"),
])
def test_every_shape_refuses_an_unread_key(tmp_path: Path, kind: str, text: str, where: str) -> None:
    with pytest.raises(TaintCatalogError, match=where):
        _load(tmp_path, kind, text)


def test_a_near_miss_gets_a_suggestion(tmp_path: Path) -> None:
    with pytest.raises(TaintCatalogError, match="Did you mean: methods"):
        _load(tmp_path, "sinks", _SINK.format(extra="      method: [send]\n"))


def test_control_the_read_keys_load(tmp_path: Path) -> None:
    _load(tmp_path, "sources", _SOURCE.format(extra="      functions: [f]\n      notes: n\n"))
    _load(tmp_path, "sinks", "description: d\n" + _SINK.format(extra=""))
    _load(tmp_path, "sanitizers", _SANITIZER.format(extra=""))


def test_every_shipped_file_passes() -> None:
    load_taint_catalog(
        sorted(_TAINT_SOURCES_DIR.glob("*.yaml")),
        sorted(_TAINT_SINKS_DIR.glob("*.yaml")),
        sorted(_TAINT_SANITIZERS_DIR.glob("*.yaml")),
    )


def test_verify_claims_refuses_the_file_with_exit_2(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """End to end: the run is inconclusive (2), never a clean verdict."""
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "app.js").write_text("const x = document.location;\n")
    claims = tmp_path / "claims.yaml"
    claims.write_text(
        "claims:\n  - id: c\n    text: t\n    constraint:\n      taint_flow:\n"
        "        source_taint: untrusted_input\n        prohibited_sink_zone: network\n"
    )
    sources = tmp_path / "src.yaml"
    sources.write_text(_SOURCE.format(extra="      attributes: [location]\n"))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    err = io.StringIO()
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(err):
        rc = main(["verify-claims", str(repo), "--claims", str(claims), "--taint-sources", str(sources)])
    assert rc == 2
    assert "attributes" in err.getvalue()
