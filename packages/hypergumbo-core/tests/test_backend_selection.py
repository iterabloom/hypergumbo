# SPDX-License-Identifier: AGPL-3.0-or-later
"""Precedence resolution for backend opt-in (ADR-0045 ruling 4, INV-*).

These tests exist because the two-tier version of this decision was already
producing a wrong answer before any config tier was added: ``--backend
tree-sitter`` did not disable a backend enabled via the environment, so the
opt-out the tool advertises in its own warning was inert and the run executed
the analysed repo's ``build.rs`` anyway.

The distinction these tests pin hardest is **explicit-off vs no-opinion**.
Both currently resolve to "do not run the backend", so a test suite that only
checked the boolean outcome would pass with the two collapsed — and would keep
passing right up until a config tier is added, at which point an explicit
``HYPERGUMBO_RUST_ANALYZER=0`` would start losing to a config file that says
on. The three-valued return is the whole point of the module.
"""

from __future__ import annotations

import pytest

from hypergumbo_core.backend_selection import resolve_optin, resolved_backend_set

ENV = "HYPERGUMBO_RUST_ANALYZER"
ON = frozenset({"rust-analyzer", "rust_analyzer", "scip"})
OFF = frozenset({"tree-sitter", "tree_sitter", "default"})


def _resolve(flag=None, environ=None):
    return resolve_optin(
        flag_choice=flag,
        environ={} if environ is None else environ,
        env_var=ENV,
        on_flag_values=ON,
        off_flag_values=OFF,
    )


class TestTheFlagOutranksTheEnvironment:
    """The defect this module was written for, in both directions."""

    def test_flag_off_beats_env_on(self) -> None:
        # The live repro: HYPERGUMBO_RUST_ANALYZER=1 exported globally (the
        # only durable opt-in hypergumbo offers), then opted out for one
        # untrusted repo. Before the fix this resolved to True and the
        # repo's build.rs ran.
        assert _resolve(flag="tree-sitter", environ={ENV: "1"}) is False

    def test_flag_on_beats_env_off(self) -> None:
        assert _resolve(flag="rust-analyzer", environ={ENV: "0"}) is True

    @pytest.mark.parametrize("flag", sorted(OFF))
    def test_every_off_spelling_beats_env_on(self, flag: str) -> None:
        assert _resolve(flag=flag, environ={ENV: "1"}) is False

    @pytest.mark.parametrize("flag", sorted(ON))
    def test_every_on_spelling_beats_env_off(self, flag: str) -> None:
        assert _resolve(flag=flag, environ={ENV: "0"}) is True

    def test_case_and_whitespace_do_not_smuggle_a_flag_past_the_gate(self) -> None:
        assert _resolve(flag=" TREE-SITTER ", environ={ENV: "1"}) is False


class TestExplicitOffIsNotTheSameAsSilence:
    """The three-valued contract, which no current caller can yet observe.

    ``None`` means no tier consulted so far expressed an opinion, so a
    lower-precedence tier (project config, then user config -- ADR-0045
    ruling 4) is still entitled to decide. ``False`` means a tier said no and
    the lower tiers must not be asked. Collapsing them is the bug this module
    exists to prevent, and it is not yet observable through any caller, so it
    is pinned here or nowhere.
    """

    def test_absent_env_is_no_opinion(self) -> None:
        assert _resolve(environ={}) is None

    def test_explicit_env_off_is_a_decision(self) -> None:
        assert _resolve(environ={ENV: "0"}) is False

    def test_a_flag_naming_neither_backend_is_no_opinion_at_the_flag_tier(
        self,
    ) -> None:
        # `--backend other` must fall THROUGH to the env var rather than
        # being read as a refusal; otherwise an unrecognised spelling would
        # silently override an explicit opt-in.
        assert _resolve(flag="other", environ={ENV: "1"}) is True
        assert _resolve(flag="other", environ={}) is None

    def test_no_flag_and_no_env_is_no_opinion(self) -> None:
        assert _resolve() is None


class TestEnvironmentParsingMatchesTheShippedGate:
    """Back-compat: the truthiness rules the gate already used must survive.

    Written by reading gate._is_env_enabled rather than by guessing, because
    a resolver that quietly widened or narrowed the accepted set would change
    who has the backend enabled without anyone asking for it.
    """

    @pytest.mark.parametrize("raw", ["1", "true", "TRUE", "yes", "on", " 1 "])
    def test_truthy_spellings(self, raw: str) -> None:
        assert _resolve(environ={ENV: raw}) is True

    @pytest.mark.parametrize("raw", ["0", "false", "no", "off", " off "])
    def test_falsy_spellings(self, raw: str) -> None:
        assert _resolve(environ={ENV: raw}) is False

    @pytest.mark.parametrize("raw", ["", "garbage", "2", "maybe"])
    def test_unrecognised_values_fail_safe_to_off_not_to_silence(
        self, raw: str,
    ) -> None:
        # Deliberate: the shipped gate treated anything non-truthy as "not
        # enabled". Resolving these to None instead would let a future config
        # tier turn the backend ON for a user whose env var says something
        # the parser did not understand. Fail safe, and fail as a DECISION.
        assert _resolve(environ={ENV: raw}) is False


class TestTheResolvedBackendSet:
    """WI-givib: the set of opt-in backends that WILL RUN for a repository —
    the output of the precedence chain AND the binary being installed —
    is what the results cache must key on. It names what runs, not what
    was asked for: an opt-in with no binary on PATH falls through to the
    tree-sitter arm (WI-luvud), so it must key as tree-sitter-only."""

    def test_opted_in_and_installed_names_the_scip_arm(self, tmp_path) -> None:
        got = resolved_backend_set(
            repo_root=tmp_path, environ={ENV: "1"}, is_available=lambda: True,
        )
        assert got == ("rust_analyzer",)

    def test_opted_in_but_not_installed_is_tree_sitter_only(self, tmp_path) -> None:
        got = resolved_backend_set(
            repo_root=tmp_path, environ={ENV: "1"}, is_available=lambda: False,
        )
        assert got == ()

    def test_silent_tiers_are_tree_sitter_only(self, tmp_path) -> None:
        assert resolved_backend_set(repo_root=tmp_path, environ={}, is_available=lambda: True) == ()

    def test_an_explicit_off_beats_env_on(self, tmp_path) -> None:
        got = resolved_backend_set(
            repo_root=tmp_path, environ={ENV: "1"}, flag_choice="tree-sitter",
            is_available=lambda: True,
        )
        assert got == ()

    def test_a_trust_grant_is_consulted_when_env_is_silent(self, tmp_path) -> None:
        from hypergumbo_core.backend_trust import record_decision
        from hypergumbo_core.analyze.registry import ensure_discovered

        ensure_discovered()
        environ = {"XDG_STATE_HOME": str(tmp_path / "state")}
        repo = tmp_path / "repo"
        repo.mkdir()
        record_decision(repo, "rust_analyzer", True, environ=environ)
        assert resolved_backend_set(repo_root=repo, environ=environ, is_available=lambda: True) == ("rust_analyzer",)

    def test_the_binary_check_is_not_consulted_when_not_opted_in(self, tmp_path) -> None:
        calls: list[int] = []

        def probe() -> bool:
            calls.append(1)
            return True

        assert resolved_backend_set(repo_root=tmp_path, environ={ENV: "0"}, is_available=probe) == ()
        assert calls == []
