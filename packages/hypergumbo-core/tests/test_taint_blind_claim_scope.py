# SPDX-License-Identifier: AGPL-3.0-or-later
"""The no-taint-catalogue gate is scoped by a claim's DECLARED sources (WI-rusil).

``_taint_blind_reason`` withheld a clean verdict from EVERY taint claim when
any language in the repo had no taint catalogue and made production calls.
For a claim whose source label a project declares only in python, a php file
cannot be where such a flow STARTS, so the gate asked a question the claim did
not raise.

The rule is about the claim's declared sources, not about reachability: a
launch into another language is a subprocess boundary and produces no call
edge, so "unreachable from the source" would be true by construction and the
gate would go vacuous (the fail-open WI-rusil's description rejects).

This WEAKENS a safety gate, so each test that shows it opening is paired with
one showing it still closed:

* a label the project declares in the uncatalogued language (control 1);
* a built-in label, which any language can originate;
* and the verdict that DOES open says which languages it did not count.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from hypergumbo_core.cli import main

_APP = '''def handler(name):
    return helper(name)


def helper(name):
    return name.upper()
'''

_PHP = '''<?php
function run_tool($x) {
    return strtoupper(trim($x));
}
function main() {
    echo run_tool("a");
}
'''

_PY_ONLY = '''taint_label: app_entry
sources:
  python:
    - module: app
      start_at: callee
      functions:
        - handler
'''

#: The same handler, and a php one: the label CAN start in php.
_WITH_PHP = '''taint_label: mixed_entry
sources:
  python:
    - module: app
      start_at: callee
      functions:
        - handler
  php:
    - module: tool
      start_at: callee
      functions:
        - main
'''

_CLAIMS = '''extra_catalogs:
  sources:
    - py_only.yaml
    - with_php.yaml
claims:
  - id: APP-FS
    text: The app entry never reaches the filesystem.
    constraint:
      taint_flow:
        source_taint: app_entry
        prohibited_sink_zone: host_fs
  - id: MIXED-FS
    text: The mixed entry never reaches the filesystem.
    constraint:
      taint_flow:
        source_taint: mixed_entry
        prohibited_sink_zone: host_fs
  - id: UNTRUSTED-FS
    text: Untrusted input never reaches the filesystem.
    constraint:
      taint_flow:
        source_taint: untrusted_input
        prohibited_sink_zone: host_fs
'''


@pytest.fixture(scope="module")
def verdicts(tmp_path_factory: pytest.TempPathFactory) -> dict[str, dict]:
    root = tmp_path_factory.mktemp("rusil")
    repo = root / "repo"
    (repo / "src").mkdir(parents=True)
    (repo / "src" / "app.py").write_text(_APP)
    (repo / "src" / "tool.php").write_text(_PHP)
    (repo / "py_only.yaml").write_text(_PY_ONLY)
    (repo / "with_php.yaml").write_text(_WITH_PHP)
    (repo / "claims.yaml").write_text(_CLAIMS)
    out = root / "out.json"
    mp = pytest.MonkeyPatch()
    mp.setenv("XDG_CACHE_HOME", str(root / "cache"))
    try:
        import contextlib
        import io

        buf = io.StringIO()
        with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(io.StringIO()):
            main(["verify-claims", str(repo), "--claims", str(repo / "claims.yaml"),
                  "--format", "json"])
        out.write_text(buf.getvalue())
    finally:
        mp.undo()
    envelope = json.loads(out.read_text())
    assert "php" in envelope["unsupported_taint_languages"], envelope
    return {v["claim_id"]: v for v in envelope["verdicts"]}


def test_a_label_declared_only_in_python_is_not_blocked_by_php(
    verdicts: dict[str, dict],
) -> None:
    v = verdicts["APP-FS"]
    assert v["verdict"] in ("confirmed", "confirmed_with_caveats"), v["details"]


def test_the_opened_verdict_names_what_it_did_not_count(
    verdicts: dict[str, dict],
) -> None:
    """A weakened gate must not weaken silently."""
    details = verdicts["APP-FS"]["details"]
    assert "php" in details
    assert "app_entry" in details
    assert "python" in details


def test_a_label_that_can_start_in_php_is_still_blocked(
    verdicts: dict[str, dict],
) -> None:
    """Control 1: without it the change is indistinguishable from deleting
    the gate."""
    v = verdicts["MIXED-FS"]
    assert v["verdict"] == "inconclusive"
    assert "no taint catalogue (php)" in v["details"]


def test_a_built_in_label_is_still_blocked(verdicts: dict[str, dict]) -> None:
    """Any language can originate untrusted input, so the gate is unchanged
    for every built-in label -- the whole of the measured corpus."""
    v = verdicts["UNTRUSTED-FS"]
    assert v["verdict"] == "inconclusive"
    assert "no taint catalogue (php)" in v["details"]


def test_source_origin_languages() -> None:
    from hypergumbo_core.taint import (
        TaintCatalog,
        TaintSource,
        source_origin_languages,
    )

    catalog = TaintCatalog()
    catalog._sources = {
        "python": [TaintSource("app_entry", "app", "handler", "function")],
        "go": [TaintSource("app_entry", "main", "run", "function"),
               TaintSource("host_secret", "os", "Getenv", "function")],
    }
    builtin = frozenset({"host_secret", "untrusted_input"})
    assert source_origin_languages(catalog, builtin, "app_entry") == frozenset({"python", "go"})
    assert source_origin_languages(catalog, builtin, "host_secret") is None
    assert source_origin_languages(catalog, builtin, "untrusted_input") is None


def _call(src: str, dst: str) -> dict:
    return {"type": "calls", "src": src, "dst": dst}


def test_a_language_entered_by_a_direct_call_is_counted() -> None:
    """java and kotlin share a JVM: a flow that starts in java continues into
    kotlin through a plain call, with no crossing any sink zone judges. A
    present edge ADDS the language, transitively."""
    from hypergumbo_core.cli import _languages_a_flow_can_be_in

    edges = [
        _call("java:A.java:1-2:A.f:method", "kotlin:B.kt:1-2:g:function"),
        _call("kotlin:B.kt:1-2:g:function", "php:c.php:1-2:h:function"),
        _call("ruby:d.rb:1-2:k:method", "python:e.py:1-2:m:function"),
        {"type": "imports", "src": "java:A.java:1-1:file:file", "dst": "bash:x.sh:1-1:file:file"},
    ]
    assert _languages_a_flow_can_be_in({"java"}, edges) == frozenset({"java", "kotlin", "php"})
    assert _languages_a_flow_can_be_in({"python"}, edges) == frozenset({"python"})


def test_both_checks_are_scoped_together() -> None:
    """The census and the coverage check walk one edge population (INV-motos):
    a language set aside by the scope blocks through neither."""
    from hypergumbo_core.cli import _taint_blind_reason

    edges = [
        _call("python:app.py:1-2:handler:function", "python:app.py:4-5:helper:function"),
        _call("php:t.php:1-2:main:function", "php:t.php:4-5:run:function"),
    ]
    reason, _ = _taint_blind_reason(True, ["php"], edges, {"python"}, {})
    assert reason is not None and "php" in reason
    scoped, _ = _taint_blind_reason(
        True, ["php"], edges, {"python"}, {}, source_languages=frozenset({"python"}),
    )
    assert scoped is None or "php" not in scoped
