# SPDX-License-Identifier: AGPL-3.0-or-later
"""``hypergumbo trust-backend`` (ADR-0045 rulings 6-8).

The command is the only supported way to make a backend opt-in durable for a
single repository. Before it existed the sole durable route was exporting
``HYPERGUMBO_RUST_ANALYZER=1``, which is global and therefore opts in every
Rust repository the user ever analyses — including ones cloned to audit
(WI-sobig). These tests hold the surface a person actually meets: what it
prints, what it refuses, and that a grant is legible afterwards.
"""

from __future__ import annotations

from pathlib import Path

from hypergumbo_core.cli import main


class TestTheHappyPath:
    def test_grant_then_show_round_trips(
        self, tmp_path: Path, monkeypatch, capsys,
    ) -> None:
        monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
        repo = tmp_path / "repo"
        repo.mkdir()

        assert main(["trust-backend", "rust_analyzer", str(repo)]) == 0
        out = capsys.readouterr().out
        assert "Granted rust_analyzer" in out
        # The consequence must be restated at the moment of granting; this is
        # the point at which the user is agreeing to it.
        assert "build scripts" in out

        assert main(["trust-backend", "rust_analyzer", str(repo), "--show"]) == 0
        assert "GRANTED" in capsys.readouterr().out

    def test_revoke_records_a_refusal_rather_than_deleting(
        self, tmp_path: Path, monkeypatch, capsys,
    ) -> None:
        """A decline is a decision (ruling 8): deleting the record would make
        the nudge start asking again, which is what --revoke is for."""
        monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
        repo = tmp_path / "repo"
        repo.mkdir()
        main(["trust-backend", "rust_analyzer", str(repo)])
        capsys.readouterr()
        assert main(
            ["trust-backend", "rust_analyzer", str(repo), "--revoke"],
        ) == 0
        assert "Declined" in capsys.readouterr().out
        main(["trust-backend", "rust_analyzer", str(repo), "--show"])
        assert "DECLINED" in capsys.readouterr().out

    def test_show_on_an_undecided_repo_is_not_an_error(
        self, tmp_path: Path, monkeypatch, capsys,
    ) -> None:
        monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
        repo = tmp_path / "repo"
        repo.mkdir()
        assert main(
            ["trust-backend", "rust_analyzer", str(repo), "--show"],
        ) == 0
        assert "no decision recorded" in capsys.readouterr().out


class TestWhatItRefuses:
    def test_a_non_executing_backend_is_refused_with_a_pointer(
        self, tmp_path: Path, monkeypatch, capsys,
    ) -> None:
        """Ruling 5 from the user's side: the error must say where the
        setting DOES belong, or the user simply gives up and re-exports the
        global environment variable."""
        monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
        repo = tmp_path / "repo"
        repo.mkdir()
        assert main(["trust-backend", "pyright", str(repo)]) == 2
        err = capsys.readouterr().err
        assert "does not execute" in err
        assert "configuration file" in err


class TestWhatShowSaysAfterAChange:
    """Owner ruling 2026-08-23 (ADR-0045 OQ1), at the surface a person meets.

    This class previously pinned "changed but still GRANTED". The ruling
    inverted that for build.rs and kept it for Cargo.toml, so both directions
    are asserted — a one-sided test here would let "revoke on any change"
    pass while quietly over-implementing the ruling.
    """

    def test_a_changed_build_script_shows_as_revoked_with_the_reason(
        self, tmp_path: Path, monkeypatch, capsys,
    ) -> None:
        monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
        repo = tmp_path / "repo"
        repo.mkdir()
        (repo / "build.rs").write_text("fn main(){}\n")
        main(["trust-backend", "rust_analyzer", str(repo)])
        capsys.readouterr()
        (repo / "build.rs").write_text("fn main(){ something_else(); }\n")
        main(["trust-backend", "rust_analyzer", str(repo), "--show"])
        out = capsys.readouterr().out
        assert "REVOKED" in out
        # Not a bare DECLINED: the user DID say yes, and conflating the two
        # would misreport what actually happened.
        assert "DECLINED" not in out
        assert "build.rs has changed" in out
        # It must say what to do next, or the user is stuck.
        assert "re-run this command" in out

    def test_a_changed_cargo_toml_still_shows_as_granted(
        self, tmp_path: Path, monkeypatch, capsys,
    ) -> None:
        monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
        repo = tmp_path / "repo"
        repo.mkdir()
        (repo / "build.rs").write_text("fn main(){}\n")
        (repo / "Cargo.toml").write_text("[package]\nname='x'\nversion='1'\n")
        main(["trust-backend", "rust_analyzer", str(repo)])
        capsys.readouterr()
        (repo / "Cargo.toml").write_text("[package]\nname='x'\nversion='2'\n")
        main(["trust-backend", "rust_analyzer", str(repo), "--show"])
        out = capsys.readouterr().out
        assert "GRANTED" in out
        assert "REVOKED" not in out
        # Surfaced rather than silent — the change is visible without being
        # made into an interruption.
        assert "Cargo.toml has changed" in out
        assert "does not revoke" in out
