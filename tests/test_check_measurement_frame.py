# SPDX-License-Identifier: AGPL-3.0-or-later
"""Every measurement record declares the frame it was produced under (INV-duvup).

Frame rule F8 — "the headline must declare its own frame" — was ratified
2026-08-25 with NOTHING ENFORCING IT. That is the shape of the original defect,
not a detail of it: measurements 0001 / 0004 / 0005 each changed the estimator
or the cohort or both, none said so, and all three ended up disclaiming
comparability with the one before. The question the taint campaign exists to
answer — is precision improving? — became structurally unanswerable while every
individual measurement stayed sound.

WHY A GATE AND NOT A CHECKLIST. A rule only a careful author obeys is how the
drift happened the first time. The frame lived in a lab-notebook file, so it
could not be cited by a record, could not be diffed against the code it
measures, and could not fail anything. ADR-0048 moves it into the repository
and §A3 makes F8 executable.

WHAT THE GATE CANNOT DO, stated so nobody reads more into a green run: it
checks that a record DECLARES its frame, not that the frame was FOLLOWED. A
record claiming `seed: 20260825` when the draw was actually "the first seven"
passes. Declaration is the precondition for catching that by reading; it is not
a substitute for reading.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.helpers_measurement_frame import load_gate

REPO_ROOT = Path(__file__).resolve().parent.parent
GATE = load_gate(REPO_ROOT / "scripts" / "check-measurement-frame")

_GOOD = """# 0009 — a measurement

## Frame

- unit: situation (row rate reported beside it)
- allocation: M=7 situations x R=16 repositories
- seed: 20260825
- cohort: frame_08252026/COHORT.json
- claim_set: the seven generic claims
- rubric: measurement 0001, cited verbatim
- analyzer_sha: a738c503b9
- language_scope: go, python, rust; EXCLUDED java/scala/kotlin/objc/swift (INV-linub)

## Headline
"""


def _write(tmp_path: Path, name: str, body: str) -> Path:
    d = tmp_path / "docs" / "measurements"
    d.mkdir(parents=True, exist_ok=True)
    p = d / name
    p.write_text(body)
    return p


class TestTheGate:
    def test_a_full_frame_passes(self, tmp_path: Path) -> None:
        _write(tmp_path, "0009-a.md", _GOOD)
        assert GATE.main(["x", str(tmp_path)]) == 0

    def test_a_record_with_no_frame_block_fails(self, tmp_path: Path) -> None:
        _write(tmp_path, "0009-a.md", "# 0009\n\n## Headline\n\n41%\n")
        assert GATE.main(["x", str(tmp_path)]) == 1

    @pytest.mark.parametrize("key", GATE.REQUIRED_KEYS)
    def test_each_required_key_is_actually_required(
        self, tmp_path: Path, key: str,
    ) -> None:
        """Parametrised over the gate's OWN key list rather than a copy here.

        A hardcoded inventory decays: a key added to the gate and not to a
        restated list would be required by nothing. Asking the gate what it
        requires means this test cannot fall behind it.
        """
        body = "\n".join(l for l in _GOOD.splitlines()
                         if not l.startswith(f"- {key}:"))
        _write(tmp_path, "0009-a.md", body)
        assert GATE.main(["x", str(tmp_path)]) == 1, (
            f"dropping `{key}` must fail the gate"
        )

    @pytest.mark.parametrize("placeholder", ["unrecorded", "TBD", "unknown", "n/a"])
    def test_a_placeholder_is_not_a_declaration(
        self, tmp_path: Path, placeholder: str,
    ) -> None:
        """THE LOOPHOLE THAT WOULD HAVE MADE THE GATE DECORATIVE. A required
        key is trivially satisfiable by typing a word into it. The SHA is the
        one that matters: eighteen commits changed taint between one screen and
        the figures published two days later, and `analyzer_sha: unrecorded`
        would let exactly that recur while the gate reported green."""
        body = _GOOD.replace("analyzer_sha: a738c503b9",
                             f"analyzer_sha: {placeholder}")
        _write(tmp_path, "0009-a.md", body)
        assert GATE.main(["x", str(tmp_path)]) == 1

    @pytest.mark.parametrize("number", sorted(GATE.GRANDFATHERED))
    def test_grandfathered_records_are_exempt(
        self, tmp_path: Path, number: str,
    ) -> None:
        _write(tmp_path, f"{number}-old.md", f"# {number}\n\nno frame here\n")
        assert GATE.main(["x", str(tmp_path)]) == 0

    def test_the_first_ungrandfathered_number_is_not_exempt(
        self, tmp_path: Path,
    ) -> None:
        """The list must STOP. If 0009 were exempt too the gate would bind
        nothing that has not already been written."""
        _write(tmp_path, "0009-new.md", "# 0009\n\nno frame here\n")
        assert GATE.main(["x", str(tmp_path)]) == 1

    def test_the_grandfather_list_is_closed_and_reasoned(self) -> None:
        """Each exemption names why it cannot comply, and the list stops at the
        records that predate the gate. A new measurement joining it requires
        editing the script and writing a reason, which is the whole mechanism —
        an exemption that costs nothing is a default."""
        assert set(GATE.GRANDFATHERED) == {
            "0001", "0002", "0003", "0004", "0005", "0007", "0008"}
        assert "0006" not in GATE.GRANDFATHERED, (
            "0006 declares a real frame and is exempt only for analyzer_sha; "
            "whole-record grandfathering would hide the six keys it does state"
        )
        for number, reason in GATE.GRANDFATHERED.items():
            assert len(reason) > 40, f"{number}: exemption needs a real reason"

    def test_an_exempt_key_may_be_spelled_unrecorded(self, tmp_path: Path) -> None:
        """The per-key escape, and it is deliberately narrow: only a key named
        in EXEMPT_KEYS for that exact record, and only the word `unrecorded`."""
        body = _GOOD.replace("# 0009 — a measurement", "# 0006 — a measurement")
        body = body.replace("analyzer_sha: a738c503b9", "analyzer_sha: unrecorded")
        _write(tmp_path, "0006-x.md", body)
        assert GATE.main(["x", str(tmp_path)]) == 0

    def test_an_exempt_key_still_has_to_be_PRESENT(self, tmp_path: Path) -> None:
        """The gap must be visible in the document a human reads, not only in
        the script. Dropping the line entirely is not the same as declaring it
        unrecorded, and only the second is permitted."""
        body = _GOOD.replace("# 0009 — a measurement", "# 0006 — a measurement")
        body = "\n".join(l for l in body.splitlines()
                          if not l.startswith("- analyzer_sha:"))
        _write(tmp_path, "0006-x.md", body)
        assert GATE.main(["x", str(tmp_path)]) == 1

    def test_the_exemption_does_not_leak_to_another_record(
        self, tmp_path: Path,
    ) -> None:
        """0006 may say `unrecorded`; 0009 may not. An exemption keyed on the
        record is the whole point — a global allowance would let every future
        measurement skip the SHA."""
        body = _GOOD.replace("analyzer_sha: a738c503b9", "analyzer_sha: unrecorded")
        _write(tmp_path, "0009-a.md", body)
        assert GATE.main(["x", str(tmp_path)]) == 1

    def test_the_exemption_does_not_leak_to_another_key(
        self, tmp_path: Path,
    ) -> None:
        """0006 is exempt for analyzer_sha ONLY."""
        body = _GOOD.replace("# 0009 — a measurement", "# 0006 — a measurement")
        body = body.replace("seed: 20260825", "seed: unrecorded")
        _write(tmp_path, "0006-x.md", body)
        assert GATE.main(["x", str(tmp_path)]) == 1

    def test_every_key_exemption_names_a_reason(self) -> None:
        for number, keys in GATE.EXEMPT_KEYS.items():
            for key, reason in keys.items():
                assert key in GATE.REQUIRED_KEYS, (
                    f"{number}: exempting `{key}`, which is not required anyway")
                assert len(reason) > 40, f"{number}.{key}: needs a real reason"

    def test_an_empty_measurements_directory_fails_rather_than_passes(
        self, tmp_path: Path,
    ) -> None:
        """A gate that silently passes when it finds nothing to check is how a
        broken path reads as success."""
        (tmp_path / "docs" / "measurements").mkdir(parents=True)
        assert GATE.main(["x", str(tmp_path)]) == 1


class TestTheLiveTree:
    def test_the_shipped_records_pass(self) -> None:
        """The gate holds against the real `docs/measurements/`, so it cannot
        be green only against fixtures."""
        assert GATE.main(["x", str(REPO_ROOT)]) == 0

    def test_the_gate_binds_at_least_one_live_record(self) -> None:
        """A gate with no live subject cannot fire, and a rule enforced only
        against fixtures is the rule F8 already was. 0006 is framed, so the
        shipped tree exercises the real path."""
        framed = [
            r for r in (REPO_ROOT / "docs" / "measurements").glob("[0-9]" * 4 + "-*.md")
            if r.name.split("-", 1)[0] not in GATE.GRANDFATHERED
        ]
        assert framed, "no measurement record is subject to the gate"

    def test_0006_declares_the_allocation_the_ledger_actually_has(self) -> None:
        """The record read `M=7 repositories x R=16 situations`; its own
        adjudication ledger holds 16 repositories with exactly 7 rows each, so
        the LABELS were swapped (the numbers were right). Under frame F2, M is
        situations-per-repo and R is repositories, and equal allocation is what
        makes the pooled rate and the unweighted per-repo mean the same number
        — so which letter means what is not cosmetic."""
        text = (REPO_ROOT / "docs" / "measurements"
                / "0006-taint-precision-under-the-ratified-frame.md").read_text()
        assert "M=7 situations x R=16 repositories" in text
        assert "M=7 repositories x R=16 situations" not in text

    def test_every_shipped_record_is_either_framed_or_grandfathered(self) -> None:
        """No record is invisible to the gate. A file the glob misses would be
        exempt by accident rather than by decision."""
        records = sorted(
            (REPO_ROOT / "docs" / "measurements").glob("[0-9][0-9][0-9][0-9]-*.md"))
        assert records, "the glob must find the shipped records"
        for record in records:
            number = record.name.split("-", 1)[0]
            framed = bool(GATE.frame_block(record.read_text()))
            assert framed or number in GATE.GRANDFATHERED, (
                f"{record.name} declares no frame and is not grandfathered"
            )
