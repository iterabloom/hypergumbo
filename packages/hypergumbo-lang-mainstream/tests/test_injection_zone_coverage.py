# SPDX-License-Identifier: AGPL-3.0-or-later
"""A clean verdict on an injection zone says what it could not see (INV-pivam).

``code_execution`` and ``dom_injection`` (ADR-0060) have sinks in some
languages and not others, and within JavaScript some injection shapes emit no
call edge at all. Measured on the shipped CLI before this change, against
"host_secret must not reach code_execution":

    Go repo (no code_execution sink exists for Go)   confirmed  rc 0
    JS  ``new Function(s)()``                        confirmed  rc 0
    JS  ``setTimeout(s, 1)`` with a string           confirmed  rc 0

each with ``caveats: []``. The zone is now judged per language: a language in
the repo with no sink in the claim's zone makes the verdict ``inconclusive``,
and a language whose sinks in that zone miss declared shapes makes a clean
verdict ``confirmed_with_caveats`` naming them.
"""
from __future__ import annotations

import contextlib
import io
import json
from pathlib import Path

import pytest

from hypergumbo_core.cli import main


def _verdict(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, files: dict[str, str],
             zone: str, label: str = "host_secret",
             extra_args: tuple[str, ...] = ()) -> dict:
    repo = tmp_path / "repo"
    repo.mkdir()
    for name, text in files.items():
        (repo / name).write_text(text)
    claims = tmp_path / "claims.yaml"
    claims.write_text(
        "claims:\n  - id: C\n    text: t\n    constraint:\n      taint_flow:\n"
        f"        source_taint: {label}\n        prohibited_sink_zone: {zone}\n"
    )
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(io.StringIO()):
        main(["verify-claims", str(repo), "--claims", str(claims), "--format", "json",
              *extra_args])
    (verdict,) = json.loads(buf.getvalue())["verdicts"]
    return verdict


_GO = ('package main\n\nimport (\n\t"fmt"\n\t"os"\n)\n\n'
       'func run() {\n\ts := os.Getenv("X")\n\tfmt.Println(s)\n}\n')
_PY_EVAL = "import os\n\n\ndef f():\n    x = os.environ['EXPR']\n    return eval(x)\n"
_PY_CLEAN = "def f():\n    return eval('1 + 1')\n"


def _kinds(verdict: dict) -> list[str]:
    return [c["kind"] for c in verdict.get("caveats", [])]


def test_a_language_with_no_sink_in_the_zone_withholds_the_verdict(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    verdict = _verdict(tmp_path, monkeypatch, {"a.go": _GO}, "code_execution")
    assert verdict["verdict"] == "inconclusive", verdict["details"]
    assert "go" in verdict["details"] and "code_execution" in verdict["details"]


def test_the_boundary_zones_are_not_judged_this_way(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """THE CONTROL: the same Go repo under an I/O-derived zone is untouched."""
    verdict = _verdict(tmp_path, monkeypatch, {"a.go": _GO}, "host_fs")
    assert verdict["verdict"] in ("confirmed", "confirmed_with_caveats"), verdict["details"]


def test_python_has_no_dom_sink_so_a_dom_claim_is_withheld(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    verdict = _verdict(tmp_path, monkeypatch, {"a.py": _PY_CLEAN}, "dom_injection")
    assert verdict["verdict"] == "inconclusive", verdict["details"]
    assert "python" in verdict["details"]


def test_a_covered_language_keeps_its_clean_verdict_and_no_shape_caveat(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Python evaluation has sinks and no declared unreached shape."""
    verdict = _verdict(tmp_path, monkeypatch, {"a.py": _PY_CLEAN}, "code_execution")
    assert verdict["verdict"] in ("confirmed", "confirmed_with_caveats"), verdict["details"]
    assert "unreached_sink_shapes" not in _kinds(verdict)


def test_a_found_flow_stays_violated_beside_an_uncovered_language(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    verdict = _verdict(tmp_path, monkeypatch, {"a.py": _PY_EVAL, "b.go": _GO},
                       "code_execution")
    assert verdict["verdict"] == "violated", verdict["details"]


@pytest.mark.parametrize("body, shape", [
    ("new Function(s)();", "new Function"),
    ("setTimeout(s, 1);", "setTimeout"),
])
def test_an_unreached_js_shape_is_named_never_silently_confirmed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, body: str, shape: str,
) -> None:
    src = f"function run() {{\n  const s = process.env.X;\n  {body}\n}}\nmodule.exports = {{ run }};\n"
    verdict = _verdict(tmp_path, monkeypatch, {"a.js": src}, "code_execution")
    assert verdict["verdict"] == "confirmed_with_caveats", verdict
    (caveat,) = [c for c in verdict["caveats"] if c["kind"] == "unreached_sink_shapes"]
    assert any(e.startswith("javascript: ") and shape in e for e in caveat["entries"]), caveat


def test_the_dom_zone_names_the_assignment_it_cannot_see(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    src = ('function run() {\n  const s = document.referrer;\n'
           '  document.getElementById("x").innerHTML = s;\n}\nmodule.exports = { run };\n')
    verdict = _verdict(tmp_path, monkeypatch, {"a.js": src}, "dom_injection")
    assert verdict["verdict"] != "confirmed", verdict
    if verdict["verdict"] == "confirmed_with_caveats":
        (caveat,) = [c for c in verdict["caveats"] if c["kind"] == "unreached_sink_shapes"]
        assert any("innerHTML" in e for e in caveat["entries"]), caveat


def test_a_label_declared_only_in_python_does_not_count_go(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """WI-rusil's scoping carries over: a flow of a Python-only label cannot be
    in Go, so Go's missing code_execution sink says nothing about the claim."""
    sources = tmp_path / "sources.yaml"
    sources.write_text(
        "taint_label: build_input\nsources:\n  python:\n"
        "    - module: builtins\n      functions: [input]\n      return_tainted: true\n"
    )
    verdict = _verdict(tmp_path, monkeypatch, {"a.py": _PY_CLEAN, "b.go": _GO},
                       "code_execution", label="build_input",
                       extra_args=("--taint-sources", str(sources)))
    assert verdict["verdict"] in ("confirmed", "confirmed_with_caveats"), verdict["details"]


# INV-dudal: a call to one of these sinks is EXAMINED, and OPAQUE. Before this,
# each call made ``eval`` / ``window`` / ``document`` a module "the I/O catalog
# could not classify", and every claim in the repository read inconclusive.

_CONST_EVAL = "function run() {\n  return eval('1 + 1');\n}\nmodule.exports = { run };\n"
_CONST_WRITE = ("function run() {\n  document.write('<p>hi</p>');\n}\n"
                "module.exports = { run };\n")


@pytest.mark.parametrize("src, site", [
    (_CONST_EVAL, "eval"),
    (_CONST_WRITE, "document.write"),
])
def test_a_sink_call_qualifies_an_unrelated_claim_and_names_itself(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, src: str, site: str,
) -> None:
    verdict = _verdict(tmp_path, monkeypatch, {"a.js": src}, "network")
    assert verdict["verdict"] == "confirmed_with_caveats", verdict["details"]
    (caveat,) = [c for c in verdict["caveats"] if c["kind"] == "opaque_boundary"]
    assert caveat["entries"] == [site]
    assert "could not classify" not in verdict["details"]


def test_a_flow_of_the_claims_own_label_into_the_site_withholds(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``location.hash -> document.write`` under a code-execution claim: the
    markup's script is what the claim forbids, so no caveat stands in for it."""
    src = ("function run() {\n  const s = document.location.hash;\n"
           "  document.write(s);\n}\nmodule.exports = { run };\n")
    verdict = _verdict(tmp_path, monkeypatch, {"a.js": src}, "code_execution",
                       label="untrusted_input")
    assert verdict["verdict"] == "inconclusive", verdict
    assert "untrusted_input data reaches 1 of those site(s) (document.write)" \
        in verdict["details"]


def test_a_flow_into_eval_under_its_own_zone_is_still_found(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """CONTROL: the release is a coverage change; a found flow is untouched."""
    src = ("function run() {\n  const s = document.location.hash;\n"
           "  eval(s);\n}\nmodule.exports = { run };\n")
    verdict = _verdict(tmp_path, monkeypatch, {"a.js": src}, "code_execution",
                       label="untrusted_input")
    assert verdict["verdict"] == "violated", verdict


def test_another_call_into_the_module_still_withholds(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``document`` is not marked examined: only the exact sink call is."""
    src = ("function run() {\n  document.write('<p>hi</p>');\n"
           "  return document.getElementById('x');\n}\nmodule.exports = { run };\n")
    verdict = _verdict(tmp_path, monkeypatch, {"a.js": src}, "network")
    assert verdict["verdict"] == "inconclusive", verdict
    assert "could not classify" in verdict["details"]
    assert "(document)" in verdict["details"]
