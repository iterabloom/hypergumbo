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


class TestTheAdvisoryChangeNotice:
    def test_a_changed_build_script_is_reported_on_show(
        self, tmp_path: Path, monkeypatch, capsys,
    ) -> None:
        """ADR-0045 ruling 7 / OQ1 — surfaced, not revoked.

        If OQ1 is ever resolved toward strict revocation, this test and the
        one beside it in test_backend_trust.py are the two that must change,
        and they say so.
        """
        monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
        repo = tmp_path / "repo"
        repo.mkdir()
        (repo / "build.rs").write_text("fn main(){}\n")
        main(["trust-backend", "rust_analyzer", str(repo)])
        capsys.readouterr()
        (repo / "build.rs").write_text("fn main(){ something_else(); }\n")
        main(["trust-backend", "rust_analyzer", str(repo), "--show"])
        out = capsys.readouterr().out
        assert "GRANTED" in out
        assert "have changed" in out
        assert "--revoke" in out
