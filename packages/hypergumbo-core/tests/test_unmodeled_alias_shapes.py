# SPDX-License-Identifier: AGPL-3.0-or-later
"""Every reproduced alias shape, end to end through ``verify-claims`` (WI-buzup).

Before ADR-0017 §7b's withhold rule, each ``__leak`` fixture below read
``confirmed`` (exit 0; Python ``confirmed_with_caveats``, exit 3, from an
unrelated socket-receiver caveat) while the secret reached the sink through a
name the §3a walk does not follow: the walk exhausted along the written name,
refuted, and REMOVED the flow (``flows_removed_by_walk: 1``). Each now reads
``inconclusive`` (exit 4) with the flow WITHHELD; each ``__ctrl`` (identical
code whose sink reads the walked name) still reads ``violated``. Two shapes
have since left the withheld set because the walk now FOLLOWS them: an
expression-bodied closure's read is recorded as a late read of what it captures
(WI-pubat), so ``_FOLLOWED`` reads ``violated``, the true answer.

The must-still-refute set is the other half of the rule's contract: a plain
rebinding, a value read into a fresh local and passed only to terminating
calls (beads ``platformInfo``), a value received into ``_``, and the two
hypergumbo-tracker functions whose removals the rule must leave alone
(``by, username = resolve_actor(...)``). Each keeps its removal and has
nothing withheld.

Fixtures: ``fixtures/unmodeled_alias/<language>__<shape>__<variant>/``; the
study that found them is the WI-buzup lab note.
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

import pytest

from hypergumbo_core.cli import main

_FIXTURES = Path(__file__).parent / "fixtures" / "unmodeled_alias"


def _claims(label: str, zone: str) -> str:
    return (
        "claims:\n  - id: C\n    text: t\n    constraint:\n      taint_flow:\n"
        f"        source_taint: {label}\n        prohibited_sink_zone: {zone}\n"
    )


def _run(
    tmp_path: Path, fixture: str, label: str, zone: str,
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
    fmt: str = "json",
) -> tuple[int, Any]:
    repo = tmp_path / "repo"
    shutil.copytree(_FIXTURES / fixture, repo)
    claims = tmp_path / "claims.yaml"
    claims.write_text(_claims(label, zone))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    capsys.readouterr()
    rc = main(["verify-claims", str(repo), "--claims", str(claims), "--format", fmt])
    out = capsys.readouterr().out
    return rc, (json.loads(out) if fmt == "json" else out)


def _claim_of(fixture: str) -> tuple[str, str]:
    return ("host_secret", "network") if fixture.startswith("python__") else (
        "host_secret", "host_fs")


_SHAPES = sorted(
    p.name[: -len("__leak")]
    for p in _FIXTURES.iterdir()
    if p.name.endswith("__leak") and "__N2_" not in p.name
)


def test_the_shape_population_is_the_reproduced_one() -> None:
    """41 shape-language pairs reproduced the unearned removal (Go 17,
    Python 13, JavaScript 10, TypeScript 1): the study's 35, plus the
    late-evaluation captures (a Python ``lambda`` and generator expression, a
    JS / TS class field initialiser), plus a Go method value and a Go method
    call through a pointer receiver, which take the variable's address with no
    ``&`` written (WI-bamuj). A lost fixture directory must not shrink it."""
    by_language: dict[str, int] = {}
    for shape in _SHAPES:
        language = shape.split("__")[0]
        by_language[language] = by_language.get(language, 0) + 1
    assert by_language == {"go": 17, "python": 13, "javascript": 10, "typescript": 1}


#: Shapes the walk follows since WI-pubat: ``get = () => x`` / ``g = lambda:
#: x`` made before ``x = secret`` and called at the sink. The closure's body is
#: a recorded read, the later definition gains a late edge into it, and the
#: walk reaches the sink through the call.
_FOLLOWED = ("javascript__S5_closure_capture", "python__S5L_lambda_capture")


@pytest.mark.parametrize("shape", [s for s in _SHAPES if s not in _FOLLOWED])
def test_the_alias_leak_is_withheld(
    tmp_path: Path, shape: str, monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    rc, out = _run(tmp_path, f"{shape}__leak", *_claim_of(shape), monkeypatch, capsys)
    verdict = out["verdicts"][0]
    assert verdict["verdict"] == "inconclusive", verdict
    assert rc == 4
    assert verdict["withheld_flows"] == 1
    assert out["dataflow_coverage"]["flows_withheld_by_walk"] == 1
    assert out["dataflow_coverage"]["flows_removed_by_walk"] == 0
    assert out["dataflow_coverage"]["walk_verdicts"]["withheld"] == 1
    assert "does not follow aliases" in verdict["details"]


@pytest.mark.parametrize("shape", _FOLLOWED)
def test_a_closure_capture_the_walk_follows_violates(
    tmp_path: Path, shape: str, monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    rc, out = _run(tmp_path, f"{shape}__leak", *_claim_of(shape), monkeypatch, capsys)
    verdict = out["verdicts"][0]
    assert verdict["verdict"] == "violated", verdict
    assert rc == 1
    assert out["dataflow_coverage"]["flows_withheld_by_walk"] == 0
    assert out["dataflow_coverage"]["flows_removed_by_walk"] == 0


@pytest.mark.parametrize("shape", _SHAPES)
def test_the_control_still_violates(
    tmp_path: Path, shape: str, monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    rc, out = _run(tmp_path, f"{shape}__ctrl", *_claim_of(shape), monkeypatch, capsys)
    verdict = out["verdicts"][0]
    assert verdict["verdict"] == "violated", verdict
    assert rc == 1
    assert out["dataflow_coverage"]["flows_withheld_by_walk"] == 0


@pytest.mark.parametrize(("fixture", "label", "zone", "removed"), [
    # A plain rebinding of the written name: the alias was not written into.
    ("go__N2_rebind_not_store__leak", "host_secret", "host_fs", 1),
    ("python__N2_rebind_not_store__leak", "host_secret", "network", 1),
    ("javascript__N2_rebind_not_store__leak", "host_secret", "host_fs", 1),
    # beads cmd/bd/doctor/perf.go: a value read into a fresh local, read once.
    ("must_refute__go_beads", "host_description", "logging", 1),
    # A Go method value with a VALUE receiver copies the variable when it is
    # evaluated, so the later assignment does not reach it (WI-bamuj).
    ("must_refute__go_method_value_val", "host_secret", "host_fs", 1),
    # A value received into ``_`` (WI-sitaf): no name is walked at all.
    ("must_refute__go_discard", "host_secret", "host_fs", 2),
    # hypergumbo-tracker cli.py _cmd_init and configure.py run_configure:
    # ``by, username = resolve_actor(...)``, read once, sink elsewhere.
    ("must_refute__py_tracker_init", "host_description", "host_fs", 1),
    ("must_refute__py_tracker_configure", "host_description", "host_fs", 2),
])
def test_a_refutation_over_a_fresh_value_still_removes(
    tmp_path: Path, fixture: str, label: str, zone: str, removed: int,
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    _rc, out = _run(tmp_path, fixture, label, zone, monkeypatch, capsys)
    assert out["dataflow_coverage"]["flows_removed_by_walk"] == removed
    assert out["dataflow_coverage"]["flows_withheld_by_walk"] == 0
    assert out["verdicts"][0]["withheld_flows"] == 0


class TestTheSanitizerArm:
    """WI-fasub's same-function credit, with the plaintext also written raw
    through an alias (``t := s; t[0] = decrypt(); write(encrypt(t[0]));
    write(s[0])``). Before the rule the alias fixture was credited
    ``sanitized`` like the plain one; now its barrier walk is withheld."""

    def _verdict(self, tmp_path: Path, fixture: str, monkeypatch: pytest.MonkeyPatch,
                 capsys: pytest.CaptureFixture[str]) -> dict[str, Any]:
        import hypergumbo_core.taint as taint

        # The shipped Fernet rows are community rows, which may not clear a
        # flow (ADR-0061); vouch for them the way an operator does.
        shipped = Path(taint.__file__).parent / "taint_sanitizers" / "encryption_community.yaml"
        channel = tmp_path / "config" / "hypergumbo" / "taint_sanitizers.d"
        channel.mkdir(parents=True)
        (channel / "encryption.yaml").write_text(
            shipped.read_text().replace("provenance: community\n", ""),
        )
        _rc, out = _run(tmp_path, fixture, "plaintext", "host_fs", monkeypatch, capsys)
        return out["verdicts"][0]

    def test_the_plain_shape_keeps_its_credit(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        verdict = self._verdict(tmp_path, "sanitizer__plain", monkeypatch, capsys)
        assert verdict["sanitized_flows"] == 1
        assert verdict["evidence_count"] == 0

    def test_the_alias_shape_earns_no_credit(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        verdict = self._verdict(tmp_path, "sanitizer__alias", monkeypatch, capsys)
        assert verdict["sanitized_flows"] == 0
        assert verdict["verdict"] == "violated"


def test_the_text_view_states_the_withhold(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    rc, text = _run(tmp_path, "go__S1_slice_copy__leak", "host_secret", "host_fs",
                    monkeypatch, capsys, fmt="text")
    assert rc == 4
    assert "Verdict: inconclusive" in text
    assert "host_secret from os.Getenv stored into `t` at main.go:11" in text
    assert "Flows WITHHELD by the walk this run: 1." in text
    assert "Flows REMOVED by the walk this run: 0." in text
