# SPDX-License-Identifier: AGPL-3.0-or-later
"""INV-tusos: an opt-in analyser backend launches through a declared zone.

``--backend rust-analyzer`` runs ``rust-analyzer scip <workspace>``, which
EXECUTES the analysed project's ``build.rs`` and proc macros; ``--backend
scip-python`` runs ``scip-python index``. Both are reachable from runtime
``cmd_*`` handlers, and both called ``subprocess.run`` through an injectable
local (``run = runner or subprocess.run``), so no edge reached a catalogued
launch and ``runtime-cli-no-subprocess`` said runtime subcommands shell out ONLY
through ``repo_inspection`` -- false for any run with a backend enabled.

They get their OWN zone rather than joining ``repo_inspection``. That zone's
recorded split trigger (``test_repo_inspection_zone.py``, owner 2026-08-11) is
"anything that reads repository CONTENT and acts on it", and indexing does
exactly that; rust-analyzer's indexing runs the project's code. One wrapper per
backend, as ``repo_inspection`` keeps one per activity, so a future claim can
permit the static indexer while prohibiting the one that executes code.
"""
from __future__ import annotations

import ast
import inspect
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from hypergumbo_core import safety_zones

_WRAPPERS = ["backend_index_rust_analyzer", "backend_index_scip_python"]
_DOCS = Path(__file__).resolve().parents[3] / "docs"


@pytest.mark.parametrize("wrapper", _WRAPPERS)
def test_the_wrapper_invokes_the_zone_barrier_once(wrapper: str) -> None:
    """Without the barrier the wrapper reads as the bare call it replaced."""
    tree = ast.parse(inspect.getsource(getattr(safety_zones, wrapper)).lstrip())
    calls = [n for n in ast.walk(tree) if isinstance(n, ast.Call)
             and getattr(n.func, "id", None) == "_safety_zone_barrier"]
    assert len(calls) == 1


@pytest.mark.parametrize("wrapper", _WRAPPERS)
def test_the_wrapper_runs_and_passes_kwargs_through(wrapper: str) -> None:
    result = getattr(safety_zones, wrapper)(
        [sys.executable, "-c", "print('marker')"], capture_output=True, text=True,
        timeout=30,
    )
    assert isinstance(result, subprocess.CompletedProcess)
    assert result.stdout.strip() == "marker"


def test_the_self_catalogue_declares_the_zone() -> None:
    doc = yaml.safe_load((_DOCS / "hypergumbo-self-catalog"
                          / "analyser_backend_sinks.yaml").read_text())
    assert doc["zone"] == "analyser_backend"
    (entry,) = doc["sinks"]["python"]
    assert entry["module"] == "hypergumbo_core.safety_zones"
    assert sorted(entry["functions"]) == sorted(_WRAPPERS)


def test_the_claims_file_loads_the_zone() -> None:
    claims = yaml.safe_load((_DOCS / "hypergumbo.claims.yaml").read_text())
    assert ("hypergumbo-self-catalog/analyser_backend_sinks.yaml"
            in claims["extra_catalogs"]["sinks"])


def test_the_claim_text_names_every_backend_launch_and_its_enablers() -> None:
    """The statement asks the claim to ENUMERATE each launch with the condition
    that enables it, including the scip-python capability probe."""
    claims = yaml.safe_load((_DOCS / "hypergumbo.claims.yaml").read_text())
    (claim,) = [c for c in claims["claims"] if c["id"] == "runtime-cli-no-subprocess"]
    text = " ".join(claim["text"].split())
    for needle in ("rust-analyzer scip", "scip-python index", "scip-python --version",
                   "HYPERGUMBO_RUST_ANALYZER", "HYPERGUMBO_SCIP_PYTHON",
                   ".hypergumbo.toml", "trust-backend", "analyser_backend"):
        assert needle in text, needle
