# SPDX-License-Identifier: AGPL-3.0-or-later
"""A call to a shipped non-I/O taint sink is examined, and opaque (INV-dudal).

``taint_sinks/`` (ADR-0060) ships ``eval``, ``window.eval``,
``document.write`` and ``document.writeln`` for javascript and typescript. The
coverage gate asked only the I/O catalogue about them, and by ADR-0060's own
rule no I/O row ever names them, so each call made its module (``eval``,
``window``, ``document``) "a module the I/O catalog could not classify" and
withheld EVERY clean verdict in the repository, whatever the claim was about.

WHAT CHANGED, AND WHAT DID NOT. A call whose (language, module, name) is
EXACTLY a shipped built-in row of ``taint_sinks/`` is no longer counted as
never-examined. It is not counted as an examined NEGATIVE either: the call
evaluates data as code or writes it into the page as markup, and what that code
or markup goes on to do (a ``fetch``, a ``<script src>``, an ``<img src>``
request) is not in the edge set. So it is reported the way a subprocess launch
is -- a named opaque site that QUALIFIES a clean verdict
(``confirmed_with_caveats``, caveat ``opaque_boundary``) when it is the only
blocker, and leaves the verdict ``inconclusive`` beside a genuinely
uncatalogued module.

EXACT, NEVER BY MODULE. ``document`` is not marked examined: a
``document.getElementById`` call beside ``document.write`` still withholds,
and a row match is required on the module slot AND the name. Only the shipped
built-in files count: an operator's own sink rows (``taint_sinks.d``,
``--taint-sinks``, a claims file) never reach this gate.
"""

from __future__ import annotations

from typing import Any

import pytest

from hypergumbo_core.io_boundary import load_catalog
from hypergumbo_core.taint import shipped_non_boundary_sink_sites
from hypergumbo_core.verify_claims import (
    CAVEAT_OPAQUE_BOUNDARY,
    Claim,
    _opaque_boundary_caveat,
    _uncatalogued_external_modules,
    compute_boundary_coverage,
    verify_claims,
)
from hypergumbo_core.io_boundary import BoundaryMap


def _edge(lang: str, module: str, name: str, *, etype: str = "calls") -> dict[str, Any]:
    return {
        "src": f"{lang}:web/app.js:1-9:run:function",
        "dst": f"{lang}:{module}:0-0:{name}:external_symbol",
        "dst_ref": {"lang": lang, "module_path": module, "name": name},
        "type": etype,
        "meta": {"evidence_lang": lang},
    }


def _catalogs() -> dict[str, Any]:
    js = load_catalog("javascript")
    py = load_catalog("python")
    return {"javascript": js, "typescript": js, "python": py}


SINK_CALLS = [
    ("javascript", "eval", "eval", "eval"),
    ("javascript", "window", "eval", "window.eval"),
    ("javascript", "document", "write", "document.write"),
    ("javascript", "document", "writeln", "document.writeln"),
    ("typescript", "eval", "eval", "eval"),
    ("typescript", "window", "eval", "window.eval"),
    ("typescript", "document", "write", "document.write"),
    ("typescript", "document", "writeln", "document.writeln"),
]


def test_the_rows_read_are_exactly_the_shipped_builtin_files() -> None:
    sites = shipped_non_boundary_sink_sites()
    js = {("eval", "eval"), ("window", "eval"), ("document", "write"),
          ("document", "writeln")}
    assert sites["javascript"] == js
    assert sites["typescript"] == js
    assert sites["python"] == {("builtins", "eval"), ("builtins", "exec"),
                               ("builtins", "compile")}


def test_a_non_builtin_file_in_the_directory_is_not_read(
    tmp_path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """ADR-0061: the provenance LINE decides the tier, never the directory. A
    community row can add findings; it cannot exempt a call from the gate."""
    from hypergumbo_core import taint

    (tmp_path / "a.yaml").write_text(
        "provenance: builtin\nzone: code_execution\nsinks:\n  javascript:\n"
        "    - module: eval\n      functions: [eval]\n")
    (tmp_path / "b.yaml").write_text(
        "provenance: community\nzone: code_execution\nsinks:\n  javascript:\n"
        "    - module: vm\n      functions: [runInThisContext]\n")
    monkeypatch.setattr(taint, "_TAINT_SINKS_DIR", tmp_path)
    assert shipped_non_boundary_sink_sites() == {
        "javascript": frozenset({("eval", "eval")})}


@pytest.mark.parametrize("lang, module, name, site", SINK_CALLS)
def test_a_shipped_sink_call_is_not_a_never_examined_module(
    lang: str, module: str, name: str, site: str,
) -> None:
    """THE DEFECT: each of these alone withheld every claim in the repo."""
    edge = _edge(lang, module, name)
    assert _uncatalogued_external_modules([edge], _catalogs()) == []


@pytest.mark.parametrize("lang, module, name, site", SINK_CALLS)
def test_it_is_reported_as_a_named_opaque_site_that_qualifies(
    lang: str, module: str, name: str, site: str,
) -> None:
    """NON-DESTRUCTION: the call moves channel; it is never dropped."""
    cov = compute_boundary_coverage([_edge(lang, module, name)], {lang}, _catalogs())
    assert cov.complete is False
    assert cov.opaque_sites == [site]
    assert cov.qualifying_only is True
    assert "could not classify" not in cov.reason
    assert site in cov.reason


def test_the_module_is_not_marked_examined() -> None:
    """``document.getElementById`` is not a sink row, so ``document`` still
    withholds -- and the write beside it then cannot qualify the verdict."""
    other = _edge("javascript", "document", "getElementById")
    cov = compute_boundary_coverage(
        [_edge("javascript", "document", "write"), other], {"javascript"}, _catalogs(),
    )
    assert cov.complete is False
    assert cov.qualifying_only is False
    assert "document" in cov.reason and "could not classify" in cov.reason
    assert _uncatalogued_external_modules([other], _catalogs()) == ["document"]


@pytest.mark.parametrize("module, name, etype, reported", [
    ("document", "write2", "calls", "document"),           # a different name
    ("mydoc.document", "write", "calls", "mydoc.document"),  # a longer module slot
    ("documents", "write", "calls", "documents"),           # not component-bounded
    ("window.frames", "eval", "calls", "window.frames"),
    # An attribute READ of the function, not a call of it: the slot spelling
    # differs (``document.write`` as the name), and nothing was evaluated.
    ("document", "document.write", "module_attr_ref", "document"),
])
def test_only_an_exact_row_match_is_released(
    module: str, name: str, etype: str, reported: str,
) -> None:
    edge = _edge("javascript", module, name, etype=etype)
    assert _uncatalogued_external_modules([edge], _catalogs()) == [reported]


def test_the_language_must_be_the_rows_language() -> None:
    """A ``document.write`` spelled in a language with no such row is not one."""
    from hypergumbo_core.io_boundary import IoBoundaryCatalog, IoPrimitive

    go = IoBoundaryCatalog(language="go", primitives=[
        IoPrimitive(boundary="fs_write", module="os", name="WriteFile", kind="function"),
    ])
    edge = _edge("go", "document", "write")
    assert _uncatalogued_external_modules([edge], {"go": go}) == ["document"]


def test_python_builtins_are_unchanged() -> None:
    """``builtins`` is enumerated (INV-bofab's dated grant), so a Python eval
    was already an examined negative before this change and stays one: the
    release only ever replaces an UNKNOWN, never a grant."""
    edge = _edge("python", "builtins", "eval")
    cov = compute_boundary_coverage([edge], {"python"}, _catalogs())
    assert cov.complete is True
    assert cov.opaque_sites == []


def test_the_caveat_names_what_cannot_be_seen_for_each_kind_of_site() -> None:
    launch_only = _opaque_boundary_caveat(["subprocess.run"])["detail"]
    assert "cannot see inside a launched program" in launch_only
    assert "markup" not in launch_only

    evaluation = _opaque_boundary_caveat(["document.write", "eval"])
    assert evaluation["kind"] == CAVEAT_OPAQUE_BOUNDARY
    assert evaluation["entries"] == ["document.write", "eval"]
    assert "launched program" not in evaluation["detail"]
    assert "evaluated as code or written into the page as markup" in evaluation["detail"]
    assert "2 call site(s)" in evaluation["detail"]

    both = _opaque_boundary_caveat(["eval", "subprocess.run"])["detail"]
    assert "launched program" in both and "markup" in both


def test_a_boundary_claim_is_qualified_not_withheld() -> None:
    """End to end through ``verify_claims``: ``inconclusive`` before,
    ``confirmed_with_caveats`` naming the site after."""
    edges = [_edge("javascript", "document", "write")]
    cov = compute_boundary_coverage(edges, {"javascript"}, _catalogs())
    claim = Claim(id="NO-NET", text="t", constraint_boundary="net_send",
                  constraint_must_not_exist=True)
    (verdict,) = verify_claims([claim], BoundaryMap(), coverage=cov)
    assert verdict.verdict == "confirmed_with_caveats", verdict.details
    (caveat,) = [c for c in verdict.caveats if c["kind"] == CAVEAT_OPAQUE_BOUNDARY]
    assert caveat["entries"] == ["document.write"]


def _finding(label: str, module: str, prim: str, *, sanitized: bool = False):
    from types import SimpleNamespace

    # A DOM-zone flow: never the code_execution claim's own finding, so the
    # claim arm filters it out on ``sink_zone`` before reading anything else.
    return SimpleNamespace(taint_label=label, sink_module=module,
                           sink_primitive=prim, sanitized=sanitized,
                           sink_zone="dom_injection")


def test_a_taint_claim_whose_data_reaches_the_site_is_withheld() -> None:
    """The qualification is for code the analysis did not see. When it HAS seen
    this claim's own label flow into the evaluator (``location.hash ->
    document.write`` under a code-execution claim is a DOM-XSS shape), a
    caveat would sit where a refusal belongs."""
    from hypergumbo_core.verify_claims import _withhold_for_tainted_evaluation

    reason, opaque = _withhold_for_tainted_evaluation(
        "untrusted_input", [_finding("untrusted_input", "document", "write")],
        "blind", ["document.write", "subprocess.run"],
    )
    assert opaque is None
    assert "untrusted_input data reaches 1 of those site(s) (document.write)" in reason


@pytest.mark.parametrize("findings, sites", [
    ([_finding("host_secret", "document", "write")], ["document.write"]),  # another label
    ([_finding("untrusted_input", "document", "write", sanitized=True)],
     ["document.write"]),                                                  # sanitized
    ([_finding("untrusted_input", "subprocess", "run")], ["subprocess.run"]),  # a launch
    ([], ["eval"]),                                                        # no flow at all
])
def test_otherwise_the_qualification_stands(findings, sites) -> None:
    from hypergumbo_core.verify_claims import _withhold_for_tainted_evaluation

    assert _withhold_for_tainted_evaluation(
        "untrusted_input", findings, "blind", sites) == ("blind", sites)


def test_the_withholding_is_wired_into_verify_claims() -> None:
    from hypergumbo_core.verify_claims import TaintFlowConstraint

    claim = Claim(id="CE", text="t", constraint_taint_flow=TaintFlowConstraint(
        source_taint="untrusted_input", prohibited_sink_zone="code_execution"))
    common: dict[str, Any] = {"blind_reason": "the analysis evaluates ...",
                              "blind_opaque_sites": ["document.write"]}
    (withheld,) = verify_claims(
        [claim], BoundaryMap(),
        taint_findings=[_finding("untrusted_input", "document", "write")], **common)
    assert withheld.verdict == "inconclusive"
    (qualified,) = verify_claims([claim], BoundaryMap(), taint_findings=[], **common)
    assert qualified.verdict == "confirmed_with_caveats"
