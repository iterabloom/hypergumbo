# SPDX-License-Identifier: AGPL-3.0-or-later
"""Per-repository backend trust grants (ADR-0045 rulings 5-8).

WHAT IS BEING STORED. Enabling the SCIP Rust backend makes indexing execute the
analysed crate's ``build.rs`` and proc macros as the invoking user. Until now
the only durable way to express that was ``export HYPERGUMBO_RUST_ANALYZER=1``
from a shell profile — which is GLOBAL, and therefore opts in every Rust
repository the user ever analyses, including ones cloned specifically to audit.
The trust decision is per-repository; the only persistence available had the
wrong scope for it (WI-sobig).

FOUR PROPERTIES THE TESTS BELOW EXIST TO HOLD, each of which would be easy to
lose in a refactor that "simplified" the store:

1. It lives OUTSIDE ``$XDG_CONFIG_HOME``. ``~/.config`` is designed to be synced
   between machines; a synced grant is the global env var again.
2. It is keyed by RESOLVED ABSOLUTE PATH — not by the repo-fingerprint
   ADR-0013 uses for its cache, which deliberately SHARES across checkouts.
3. A DECLINE is recorded as a decision, not as absence, so the nudge can go
   quiet without anyone being opted in.
4. The grant is refused when the store is writable by the analysed repo's own
   user context — a trust store an automated agent can write is not a trust
   store (ADR-0045 ruling 6, generalising ADR-0013's human-owned config).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from hypergumbo_core.backend_trust import (
    TrustDecision,
    read_decision,
    record_decision,
    trust_store_root,
)


class TestWhereGrantsLive:
    def test_store_honours_xdg_state_home(self, tmp_path: Path) -> None:
        got = trust_store_root(environ={"XDG_STATE_HOME": str(tmp_path)})
        assert got == tmp_path / "hypergumbo" / "trust.d"

    def test_store_falls_back_to_local_state(self, tmp_path: Path) -> None:
        got = trust_store_root(environ={}, home=tmp_path)
        assert got == tmp_path / ".local" / "state" / "hypergumbo" / "trust.d"

    def test_store_is_not_under_the_config_directory(
        self, tmp_path: Path,
    ) -> None:
        """The load-bearing separation, asserted rather than assumed.

        If someone later 'tidies' the store into $XDG_CONFIG_HOME to keep
        hypergumbo's files together, this fails — which is the point. A
        config directory is meant to be portable; a code-execution grant
        must not travel with it.
        """
        from hypergumbo_core.user_config import user_config_path

        environ = {
            "XDG_CONFIG_HOME": str(tmp_path / "cfg"),
            "XDG_STATE_HOME": str(tmp_path / "state"),
        }
        store = trust_store_root(environ=environ)
        config_dir = user_config_path(environ=environ).parent
        assert config_dir not in store.parents and store != config_dir


class TestGrantsAreKeyedByPathNotByRepoIdentity:
    def test_two_checkouts_of_one_repo_are_separate_decisions(
        self, tmp_path: Path,
    ) -> None:
        """Deliberately NOT ADR-0013's repo-fingerprint keying.

        That key (remote URL + first commit SHA) exists so multiple checkouts
        SHARE a cache, which is right for a cache and wrong for trust: two
        clones of one remote can have entirely different working trees, and
        it is the tree whose build.rs runs.
        """
        state = tmp_path / "state"
        a, b = tmp_path / "checkout-a", tmp_path / "checkout-b"
        a.mkdir()
        b.mkdir()
        environ = {"XDG_STATE_HOME": str(state)}
        record_decision(a, "rust_analyzer", True, environ=environ)
        assert read_decision(a, "rust_analyzer", environ=environ).granted is True
        assert read_decision(b, "rust_analyzer", environ=environ) is None

    def test_a_symlinked_path_resolves_to_the_same_decision(
        self, tmp_path: Path,
    ) -> None:
        """Otherwise a grant could be bypassed, or silently duplicated, by
        reaching the same tree through a link."""
        state = tmp_path / "state"
        real = tmp_path / "real"
        real.mkdir()
        link = tmp_path / "link"
        link.symlink_to(real, target_is_directory=True)
        environ = {"XDG_STATE_HOME": str(state)}
        record_decision(real, "rust_analyzer", True, environ=environ)
        assert read_decision(link, "rust_analyzer", environ=environ).granted is True


class TestADeclineIsADecision:
    def test_declining_is_recorded_not_absent(self, tmp_path: Path) -> None:
        state = tmp_path / "state"
        repo = tmp_path / "repo"
        repo.mkdir()
        environ = {"XDG_STATE_HOME": str(state)}
        record_decision(repo, "rust_analyzer", False, environ=environ)
        got = read_decision(repo, "rust_analyzer", environ=environ)
        assert isinstance(got, TrustDecision)
        assert got.granted is False

    def test_no_decision_is_none_not_false(self, tmp_path: Path) -> None:
        """The same three-valued discipline the opt-in resolver uses: absence
        must be distinguishable from refusal, or the nudge cannot tell "never
        asked" from "answered no" and will keep asking."""
        environ = {"XDG_STATE_HOME": str(tmp_path / "state")}
        repo = tmp_path / "repo"
        repo.mkdir()
        assert read_decision(repo, "rust_analyzer", environ=environ) is None


class TestTheAdvisoryHash:
    def test_a_grant_records_the_build_manifest_hash(
        self, tmp_path: Path,
    ) -> None:
        state = tmp_path / "state"
        repo = tmp_path / "repo"
        repo.mkdir()
        (repo / "Cargo.toml").write_text("[package]\nname='x'\n")
        (repo / "build.rs").write_text("fn main(){}\n")
        environ = {"XDG_STATE_HOME": str(state)}
        record_decision(repo, "rust_analyzer", True, environ=environ)
        got = read_decision(repo, "rust_analyzer", environ=environ)
        assert got.manifest_digest
        assert got.manifest_changed is False

    def test_a_changed_build_script_is_flagged_but_does_not_revoke(
        self, tmp_path: Path,
    ) -> None:
        """ADR-0045 ruling 7 and OQ1 — the weakest ruling, pinned so a later
        change to it is deliberate.

        Revoking on every change would re-prompt on an ordinary dependency
        bump, and by this project's own reasoning an alarm that fires when it
        is moot trains people to skim the one that is not. The grant stands
        and the change is surfaced. If OQ1 is resolved toward strictness,
        THIS test is the one that must change.
        """
        state = tmp_path / "state"
        repo = tmp_path / "repo"
        repo.mkdir()
        (repo / "build.rs").write_text("fn main(){}\n")
        environ = {"XDG_STATE_HOME": str(state)}
        record_decision(repo, "rust_analyzer", True, environ=environ)
        (repo / "build.rs").write_text("fn main(){ evil(); }\n")
        got = read_decision(repo, "rust_analyzer", environ=environ)
        assert got.granted is True
        assert got.manifest_changed is True

    def test_a_repo_with_no_build_manifest_has_no_digest(
        self, tmp_path: Path,
    ) -> None:
        state = tmp_path / "state"
        repo = tmp_path / "repo"
        repo.mkdir()
        environ = {"XDG_STATE_HOME": str(state)}
        record_decision(repo, "rust_analyzer", True, environ=environ)
        got = read_decision(repo, "rust_analyzer", environ=environ)
        assert got.manifest_digest == ""
        assert got.manifest_changed is False


class TestOnlyExecutingBackendsNeedAGrant:
    def test_a_non_executing_backend_is_refused_by_the_trust_store(
        self, tmp_path: Path,
    ) -> None:
        """Ruling 5, enforced from the other side.

        pyright executes nothing, so its opt-in is an ordinary preference and
        belongs in config. Accepting it here would give one setting two
        homes, and the second home silently wins.
        """
        environ = {"XDG_STATE_HOME": str(tmp_path / "state")}
        repo = tmp_path / "repo"
        repo.mkdir()
        with pytest.raises(ValueError, match="does not execute"):
            record_decision(repo, "pyright", True, environ=environ)


class TestTheNudgeStopsAskingSomeoneWhoAnswered:
    """ADR-0045 ruling 8, and the defect that showed up while wiring it.

    The suppression is only safe because "enabled" is resolved through the
    whole precedence chain. It briefly was not: ``rust_analyzer_backend_
    enabled`` read the environment variable alone, so a TRUST-GRANTED run
    (no variable set) reported not-enabled, took the nudge branch, and was
    then suppressed by its own recorded decision — a run that WAS executing
    the repository's build scripts printed nothing about it at all. That is
    the exact opposite of what this disclosure is for, so the granted case
    is pinned here rather than left to the wiring.
    """

    def _profile(self):
        from hypergumbo_core.profile import LanguageStats, RepoProfile

        return RepoProfile(languages={"rust": LanguageStats(files=3, loc=120)})

    def test_a_recorded_grant_still_discloses_that_scripts_are_running(
        self, tmp_path: Path,
    ) -> None:
        from hypergumbo_core.partial_install_warnings import (
            check_rust_analyzer_disclosure,
        )

        repo = tmp_path / "repo"
        repo.mkdir()
        got = check_rust_analyzer_disclosure(
            self._profile(), available=True, enabled=True, repo_root=repo,
        )
        assert len(got) == 1
        assert "IS RUNNING" in got[0].message

    @pytest.mark.parametrize("granted", [True, False])
    def test_any_recorded_decision_silences_the_nudge(
        self, tmp_path: Path, monkeypatch, granted: bool,
    ) -> None:
        from hypergumbo_core.partial_install_warnings import (
            check_rust_analyzer_disclosure,
        )

        state = tmp_path / "state"
        repo = tmp_path / "repo"
        repo.mkdir()
        monkeypatch.setenv("XDG_STATE_HOME", str(state))
        record_decision(repo, "rust_analyzer", granted)
        got = check_rust_analyzer_disclosure(
            self._profile(), available=True, enabled=False, repo_root=repo,
        )
        assert got == []

    def test_an_undecided_repo_is_still_nudged(
        self, tmp_path: Path, monkeypatch,
    ) -> None:
        from hypergumbo_core.partial_install_warnings import (
            check_rust_analyzer_disclosure,
        )

        repo = tmp_path / "repo"
        repo.mkdir()
        monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "empty"))
        got = check_rust_analyzer_disclosure(
            self._profile(), available=True, enabled=False, repo_root=repo,
        )
        assert len(got) == 1
        assert "NOT enabled" in got[0].message

    def test_without_a_repo_root_the_nudge_behaves_as_before(
        self, tmp_path: Path,
    ) -> None:
        """A caller with no repository in hand must not silently lose the
        disclosure — absence of a path means "cannot check", not "decided"."""
        from hypergumbo_core.partial_install_warnings import (
            check_rust_analyzer_disclosure,
        )

        got = check_rust_analyzer_disclosure(
            self._profile(), available=True, enabled=False, repo_root=None,
        )
        assert len(got) == 1


class TestTheGrantIsTheLowestTier:
    """Precedence, asserted at the resolver rather than inferred from the CLI."""

    def test_a_grant_enables_when_flag_and_env_are_silent(
        self, tmp_path: Path,
    ) -> None:
        from hypergumbo_core.backend_selection import resolve_rust_analyzer_optin

        state = tmp_path / "state"
        repo = tmp_path / "repo"
        repo.mkdir()
        record_decision(
            repo, "rust_analyzer", True, environ={"XDG_STATE_HOME": str(state)},
        )
        assert resolve_rust_analyzer_optin(
            environ={"XDG_STATE_HOME": str(state)}, repo_root=repo,
        ) is True

    def test_the_environment_outranks_a_grant(self, tmp_path: Path) -> None:
        from hypergumbo_core.backend_selection import (
            RUST_ANALYZER_ENV_VAR,
            resolve_rust_analyzer_optin,
        )

        state = tmp_path / "state"
        repo = tmp_path / "repo"
        repo.mkdir()
        environ = {"XDG_STATE_HOME": str(state), RUST_ANALYZER_ENV_VAR: "0"}
        record_decision(repo, "rust_analyzer", True, environ=environ)
        assert resolve_rust_analyzer_optin(
            environ=environ, repo_root=repo,
        ) is False

    def test_the_flag_outranks_a_grant(self, tmp_path: Path) -> None:
        from hypergumbo_core.backend_selection import resolve_rust_analyzer_optin

        state = tmp_path / "state"
        repo = tmp_path / "repo"
        repo.mkdir()
        environ = {"XDG_STATE_HOME": str(state)}
        record_decision(repo, "rust_analyzer", True, environ=environ)
        assert resolve_rust_analyzer_optin(
            flag_choice="tree-sitter", environ=environ, repo_root=repo,
        ) is False

    def test_no_repo_root_skips_the_tier_rather_than_guessing(self) -> None:
        from hypergumbo_core.backend_selection import resolve_rust_analyzer_optin

        assert resolve_rust_analyzer_optin(environ={}) is None
