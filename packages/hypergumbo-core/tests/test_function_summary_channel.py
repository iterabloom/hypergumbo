# SPDX-License-Identifier: AGPL-3.0-or-later
"""ADR-0047 ruling 10 — the function_summaries channel, and the gate it needs.

THE CHANNEL IS THE ONE THIS FAMILY MOST OBVIOUSLY WANTS. Its entries describe
callees "whose source is not analyzed" — dependencies — which is exactly where a
user knows something hypergumbo cannot.

THE GATE IS THE ONE IT LEAST OBVIOUSLY NEEDS, and the asymmetry is the whole
reason this shipped separately from the frameworks and dataflow channels. Those
two can only WIDEN recognition. This one can narrow it: ``taint.py``'s own
reasoning is that a false "terminates" lets the walk close a branch that is
really open and DELETES A REAL SECURITY FINDING, while a false "propagates" only
leaves an unknown unknown. A user-supplied TERMINATING summary is therefore a
sanitizer declaration by another name — it removes a flow the tool would
otherwise report — and ``CAVEAT_USER_SUPPLIED_SANITIZER`` already exists for the
structurally identical case, with its exit-3 contract. Granting the channel
without it would re-open INV-buzab's shape on a fresh surface.

WHY THE ATTRIBUTION IS COLLECTED DURING THE WALK rather than read off a finding.
A sanitized flow still surfaces as a finding carrying
``sanitized_by_user_supplied``, so the existing caveat reads it there. A
TERMINATED branch produces NO finding — that is what terminating means — so if
the walk does not record what it credited, nothing downstream can. That is why
``_use_site_terminates`` gained a collector rather than the verdict code gaining
a query.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from hypergumbo_core.function_summaries import (
    clear_summary_cache,
    load_function_summaries,
)


@pytest.fixture(autouse=True)
def _clean():
    clear_summary_cache()
    yield
    clear_summary_cache()


def _write_user_summary(tmp_path: Path, body: str) -> Path:
    channel = tmp_path / "hypergumbo" / "function_summaries.d"
    channel.mkdir(parents=True, exist_ok=True)
    path = channel / "mine.yaml"
    path.write_text(body)
    return path


# ------------------------------------------------------------- the channel --

def test_a_user_summary_is_loaded(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    _write_user_summary(tmp_path, (
        "summaries:\n"
        "  - function: acme.audit.swallow\n"
        "    param_to_return: {}\n"
    ))
    loaded = load_function_summaries()
    assert "acme.audit.swallow" in loaded


def test_provenance_is_stamped_at_load_not_read_from_the_file(
    tmp_path, monkeypatch,
) -> None:
    """A file must not be able to claim it is shipped. The stamp is what the
    caveat rests on, so a YAML key setting it would be a way to silence a
    finding AND the disclosure of having silenced it."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    _write_user_summary(tmp_path, (
        "summaries:\n"
        "  - function: acme.audit.swallow\n"
        "    user_supplied: false\n"
        "    param_to_return: {}\n"
    ))
    assert load_function_summaries()["acme.audit.swallow"].user_supplied is True


def test_shipped_summaries_are_not_marked_user_supplied(
    tmp_path, monkeypatch,
) -> None:
    """CONTROL. If everything read as user-supplied the caveat would fire on
    every run and mean nothing."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "empty"))
    loaded = load_function_summaries()
    assert loaded, "fixture wrong: no shipped summaries"
    assert not any(s.user_supplied for s in loaded.values())


def test_a_user_entry_wins_a_name_collision(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "empty"))
    shipped = load_function_summaries()
    name = next(n for n, s in shipped.items()
                if not s.param_to_return and "." in n)
    clear_summary_cache()
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    _write_user_summary(tmp_path, (
        "summaries:\n"
        f"  - function: {name}\n"
        "    param_to_return: {0: true}\n"
    ))
    mine = load_function_summaries()[name]
    assert mine.user_supplied is True
    assert mine.param_to_return.get(0) is True


def test_an_explicit_search_dir_still_means_that_directory(
    tmp_path, monkeypatch,
) -> None:
    """The user channel joins only the DEFAULT load. A caller asking about one
    particular tree — every existing test does — must keep getting exactly
    that tree, or this change would quietly alter their fixtures."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    _write_user_summary(tmp_path, (
        "summaries:\n  - function: acme.only.mine\n    param_to_return: {}\n"))
    other = tmp_path / "elsewhere"
    other.mkdir()
    assert load_function_summaries(search_dir=other) == {}


# ------------------------------------------------------------ the attribution

def _terminating_summary(name: str, *, user: bool):
    """A summary ``_summary_terminates`` actually accepts.

    Every clause there is a conjunction and any doubt reads as "no", so a
    terminating entry must be side-effecting AND return nothing derived from
    its arguments AND mutate no receiver AND invoke no callback AND transform
    no label. An earlier draft of this helper set only ``param_to_return={}``
    and produced a summary that does NOT terminate — which is the predicate
    being conservative exactly as designed, and worth keeping visible here.
    """
    from hypergumbo_core.function_summaries import FunctionSummary
    return FunctionSummary(function=name, side_effect=True,
                           param_to_return={}, user_supplied=user)


def test_the_walk_records_a_credited_user_summary() -> None:
    from hypergumbo_core.taint import _use_site_terminates
    credited: set[str] = set()
    summaries = {"acme.swallow": _terminating_summary("acme.swallow", user=True)}
    assert _use_site_terminates(
        "sym", 3, {("sym", 3): {"acme.swallow"}}, summaries, credited) is True
    assert credited == {"acme.swallow"}


def test_a_shipped_summary_is_not_recorded() -> None:
    """CONTROL, and the one that keeps the caveat meaningful: the 113 shipped
    summaries terminate constantly and none of them rests on a user's word."""
    from hypergumbo_core.taint import _use_site_terminates
    credited: set[str] = set()
    summaries = {"log.Println": _terminating_summary("log.Println", user=False)}
    assert _use_site_terminates(
        "sym", 3, {("sym", 3): {"log.Println"}}, summaries, credited) is True
    assert credited == set()


def test_nothing_is_recorded_when_the_line_does_not_terminate() -> None:
    """An entry CONSULTED on a line that then escapes was not credited with
    anything, and saying it was would attach a caveat to a run that never
    relied on it."""
    from hypergumbo_core.function_summaries import FunctionSummary
    from hypergumbo_core.taint import _use_site_terminates
    credited: set[str] = set()
    summaries = {
        "acme.swallow": _terminating_summary("acme.swallow", user=True),
        "acme.passes": FunctionSummary(function="acme.passes",
                                       side_effect=True,
                                       param_to_return={0: True},
                                       user_supplied=True),
    }
    assert _use_site_terminates(
        "sym", 3, {("sym", 3): {"acme.swallow", "acme.passes"}},
        summaries, credited) is False
    assert credited == set(), "all-or-nothing: nothing terminated, so nothing was credited"


def test_the_collector_is_optional_and_changes_no_verdict() -> None:
    """PURELY ADDITIVE. With no collector the walk must behave exactly as
    before — the property that makes this safe to add to the function whose
    docstring says a false 'terminates' deletes a real finding."""
    from hypergumbo_core.taint import _use_site_terminates
    summaries = {"acme.swallow": _terminating_summary("acme.swallow", user=True)}
    assert _use_site_terminates(
        "sym", 3, {("sym", 3): {"acme.swallow"}}, summaries) is True


# ------------------------------------------------------------------ the gate

def test_a_credited_user_summary_qualifies_a_clean_taint_verdict() -> None:
    """THE GATE. A clean verdict that rests on the user's own declaration must
    say so, with the same kind the structurally identical sanitizer case uses
    and therefore the same exit-3 contract."""
    from hypergumbo_core.verify_claims import (
        CAVEAT_USER_SUPPLIED_SANITIZER,
        Claim,
        TaintFlowConstraint,
        verify_taint_claim,
    )
    claim = Claim(id="C1", text="untrusted never reaches the filesystem",
                  constraint_taint_flow=TaintFlowConstraint(
                      source_taint="untrusted_input",
                      prohibited_sink_zone="host_fs"))
    plain = verify_taint_claim(claim, [])
    qualified = verify_taint_claim(
        claim, [], credited_user_summaries={"acme.audit.swallow"})
    assert not plain.caveats
    kinds = {c["kind"] for c in qualified.caveats}
    assert CAVEAT_USER_SUPPLIED_SANITIZER in kinds
    detail = next(c for c in qualified.caveats
                  if c["kind"] == CAVEAT_USER_SUPPLIED_SANITIZER)
    assert "acme.audit.swallow" in detail["entries"]
    assert "Run-scoped" in detail["detail"], (
        "the caveat must state its own coarseness; it is not per-flow and "
        "should not be read as if it were"
    )


def test_no_caveat_without_a_credited_user_summary() -> None:
    """CONTROL. An installation where nobody wrote a summary must produce
    byte-identical verdicts to before this feature."""
    from hypergumbo_core.verify_claims import (
        Claim, TaintFlowConstraint, verify_taint_claim,
    )
    claim = Claim(id="C1", text="t", constraint_taint_flow=TaintFlowConstraint(
        source_taint="untrusted_input", prohibited_sink_zone="host_fs"))
    for empty in (None, set()):
        assert not verify_taint_claim(
            claim, [], credited_user_summaries=empty).caveats


def test_a_violated_verdict_never_carries_the_caveat() -> None:
    """THE DISCIPLINE THIS MODULE IS BUILT ON, and a real risk for a caveat
    added late: finding evidence is trustworthy regardless of what went
    unadjudicated, so a ``violated`` verdict must never be qualified. The
    caveat block sits under ``if not violations:`` and this is what keeps it
    there — moving it would attach a "the user's word made this clean" notice
    to a verdict that is not clean."""
    from hypergumbo_core.taint import TaintFlowFinding
    from hypergumbo_core.verify_claims import (
        Claim, TaintFlowConstraint, verify_taint_claim,
    )
    claim = Claim(id="C1", text="untrusted never reaches the filesystem",
                  constraint_taint_flow=TaintFlowConstraint(
                      source_taint="untrusted_input",
                      prohibited_sink_zone="host_fs"))
    finding = TaintFlowFinding(
        taint_label="untrusted_input",
        source_symbol="py:a.py:1-2:read:function",
        source_primitive="input",
        sink_symbol="py:a.py:3-4:write:function",
        sink_primitive="open",
        sink_zone="host_fs",
        sanitized=False,
        confidence="approximate",
        analysis_method="structural",
        path=[],
    )
    verdict = verify_taint_claim(
        claim, [finding], credited_user_summaries={"acme.audit.swallow"})
    assert verdict.verdict == "violated"
    assert not verdict.caveats
