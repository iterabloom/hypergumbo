# SPDX-License-Identifier: AGPL-3.0-or-later
"""A community overlay's ``stdlib_other`` is withheld (WI-pofal).

``stdlib_other`` names symbols known NOT to be I/O, and
``_compute_external_potential`` skips them, so the uncatalogued call stops
being reported as "might be I/O". A community (``provenance: community``) file
is one hypergumbo distributes without vouching for, and ADR-0061 ruling 2
denies such a row the REMOVING half: it may add findings, never make the output
cleaner. ``load_catalog`` already withholds such a file's ``module_completeness``
for that reason; ``stdlib_other`` is the same kind of grant and was honoured.

Measured before the fix (``io-boundaries`` on a one-file repo calling
``frobnet.post``): ``external_potential_edges`` 1 with no overlay, 0 with a
community overlay naming ``frobnet.post`` under ``stdlib_other``. No
verify-claims verdict moved: both a ``net_send`` boundary claim and a
``host_secret -> network`` taint claim stayed ``inconclusive`` in all three
arms, because the unclassified-module coverage signal is computed apart from
the external_potential chains. A file of YOUR OWN keeps the grant: you are the
authority on your dependencies, as with ``module_completeness``.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from hypergumbo_core.io_boundary import load_catalog

_ROWS = "stdlib_other:\n  - module: frobnet\n    functions: [post]\n"


def _overlay(tmp_path: Path, community: bool) -> Path:
    path = tmp_path / "overlay.yaml"
    head = "language: python\n"
    if community:
        head += "provenance: community\nretrieved: \"2026-10-28\"\n"
    path.write_text(head + _ROWS)
    return path


def test_a_community_overlay_cannot_declare_a_call_not_io(tmp_path: Path) -> None:
    catalog = load_catalog("python", [_overlay(tmp_path, community=True)])
    assert "frobnet.post" not in catalog.stdlib_other


def test_your_own_overlay_still_can(tmp_path: Path) -> None:
    catalog = load_catalog("python", [_overlay(tmp_path, community=False)])
    assert "frobnet.post" in catalog.stdlib_other


def _external_potential(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, overlay: Path | None,
) -> int:
    import contextlib
    import io
    import json

    from hypergumbo_core.cli import main

    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "app.py").write_text(
        "import frobnet\n\n\ndef go():\n    frobnet.post('x')\n"
    )
    argv = ["io-boundaries", str(repo), "--format", "json"]
    if overlay is not None:
        argv += ["--io-primitives", str(overlay)]
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(io.StringIO()):
        main(argv)
    return json.loads(buf.getvalue())["external_potential_edges"]


@pytest.mark.parametrize("community, expected", [(None, 1), (True, 1), (False, 0)])
def test_the_production_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    community: bool | None, expected: int,
) -> None:
    """``io-boundaries`` still reports the uncatalogued call as one that might
    be I/O under a community overlay, and stops under your own."""
    overlay = None if community is None else _overlay(tmp_path, community)
    assert _external_potential(tmp_path, monkeypatch, overlay) == expected
