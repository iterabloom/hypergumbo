# SPDX-License-Identifier: AGPL-3.0-or-later
"""ADR-0047 ruling 9 — the developer-audience offer and its recorded decision.

THE THREE CONSTRAINTS ARE THE TESTS. Each is a defect if dropped, and two of
them are about what must NOT happen, which is why they are asserted rather than
described: nothing is ever written into an analysed repository; a decline is
recorded so the offer goes quiet; and it is never raised without a TTY, because
an offer that blocks a CI run or an agent invocation is a defect and not a
courtesy.

WHY THE DECISION IS NOT IN THE TRUST STORE, since the item originally said it
should be. ``backend_trust.record_decision`` refuses any key outside
``BACKENDS_EXECUTING_ANALYSED_CODE`` — ``frozenset({'rust_analyzer'})`` — on the
grounds that a non-executing opt-in is a preference belonging in the config
file, and the config file is the one ADR-0045 says the tool may read and must
not write. The owner ruled for a separate UX-state record (2026-08-28): a UX
decline is categorically not a trust grant, and that refusal is a security guard
that should not be widened to carry a prompt preference. The tests below pin the
separation structurally, not by comment.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from hypergumbo_core.repo_tier_offer import (
    OFFER_REPO_TIER_EXAMPLES,
    developer_checkout,
    examples_destination,
    maybe_offer_repo_tier_examples,
    offer_state_path,
    read_offer_decision,
    record_offer_decision,
    should_offer,
    write_repo_tier_examples,
)


@pytest.fixture()
def env(tmp_path: Path) -> "dict[str, str]":
    return {"XDG_STATE_HOME": str(tmp_path / "state")}


@pytest.fixture()
def home(tmp_path: Path) -> Path:
    (tmp_path / "home" / "hypergumbo").mkdir(parents=True)
    return tmp_path / "home"


# ------------------------------------------------ where the decision lives ---

def test_state_lives_under_xdg_state_home(env, tmp_path) -> None:
    assert offer_state_path(env) == tmp_path / "state" / "hypergumbo" / "offers.json"


def test_state_falls_back_to_the_xdg_default(tmp_path) -> None:
    assert offer_state_path({}, tmp_path) == (
        tmp_path / ".local" / "state" / "hypergumbo" / "offers.json")


def test_it_is_a_sibling_of_the_trust_store_not_a_member(env, tmp_path) -> None:
    """THE RULING, STRUCTURALLY. Same state root, different file: a recorded UX
    answer sits beside grants and never inside the store that holds them."""
    from hypergumbo_core.backend_trust import trust_store_root
    state, trust = offer_state_path(env), trust_store_root(env)
    assert state.parent == trust.parent
    assert trust not in state.parents
    assert state.parent.name == "hypergumbo"


def test_it_is_not_in_the_portable_config_directory(env, tmp_path) -> None:
    """Config is designed to be copied between machines; an answer about THIS
    machine's home directory is not a portable preference."""
    from hypergumbo_core.catalogue_home import user_catalogue_home
    config = user_catalogue_home({"XDG_CONFIG_HOME": str(tmp_path / "cfg")})
    assert config not in offer_state_path(env).parents


def test_the_trust_store_still_refuses_this_key() -> None:
    """The reason a second location exists at all. If this ever starts
    succeeding, the ruling that sent this record elsewhere has changed and this
    module should be revisited rather than silently left in place."""
    from hypergumbo_core.backend_trust import record_decision
    with pytest.raises(ValueError, match="does not execute"):
        record_decision(Path("/tmp"), OFFER_REPO_TIER_EXAMPLES, False)


# ------------------------------------------------ a decision, three states ---

def test_an_unanswered_offer_reads_as_none(env) -> None:
    assert read_offer_decision(OFFER_REPO_TIER_EXAMPLES, env) is None


@pytest.mark.parametrize("accepted", [True, False])
def test_an_answer_round_trips(env, accepted: bool) -> None:
    record_offer_decision(OFFER_REPO_TIER_EXAMPLES, accepted, env)
    assert read_offer_decision(OFFER_REPO_TIER_EXAMPLES, env) is accepted


def test_a_decline_is_distinguishable_from_never_asked(env) -> None:
    """ADR-0045 ruling 8's actual requirement. Folding a decline into a falsy
    'not accepted' would re-ask forever, which is the nag the ruling forbids."""
    record_offer_decision(OFFER_REPO_TIER_EXAMPLES, False, env)
    assert read_offer_decision(OFFER_REPO_TIER_EXAMPLES, env) is False
    assert read_offer_decision("some_other_offer", env) is None


def test_a_corrupt_state_file_does_not_break_a_run(env) -> None:
    """Worst case is being asked again — the pre-feature behaviour. A run that
    dies because a JSON file got truncated would be a worse bug than the one
    this feature fixes."""
    path = offer_state_path(env)
    path.parent.mkdir(parents=True)
    path.write_text("{not json")
    assert read_offer_decision(OFFER_REPO_TIER_EXAMPLES, env) is None


def test_recording_one_answer_keeps_the_others(env) -> None:
    record_offer_decision("other", True, env)
    record_offer_decision(OFFER_REPO_TIER_EXAMPLES, False, env)
    doc = json.loads(offer_state_path(env).read_text())
    assert doc["other"]["accepted"] is True
    assert doc[OFFER_REPO_TIER_EXAMPLES]["accepted"] is False


# --------------------------------------------------------- the three gates ---

def test_no_offer_without_a_tty(env, home) -> None:
    """The constraint whose violation blocks a CI run."""
    assert should_offer(environ=env, home=home, interactive=False) is False


def test_no_offer_without_a_checkout(env, tmp_path) -> None:
    """``~/hypergumbo`` is the deliberate signal. Absent it, there is no
    developer audience to address."""
    bare = tmp_path / "nohome"
    bare.mkdir()
    assert should_offer(environ=env, home=bare, interactive=True) is False


@pytest.mark.parametrize("answer", [True, False])
def test_no_offer_once_answered_either_way(env, home, answer: bool) -> None:
    record_offer_decision(OFFER_REPO_TIER_EXAMPLES, answer, env)
    assert should_offer(environ=env, home=home, interactive=True) is False


def test_offer_when_all_three_hold(env, home) -> None:
    assert should_offer(environ=env, home=home, interactive=True) is True


# ------------------------------------------------------------ what it writes --

def test_examples_go_only_inside_the_destination(tmp_path) -> None:
    """Ruling 9's hard constraint, asserted as containment. This is the
    function whose bug would put a file in somebody's repository."""
    dest = tmp_path / "dest"
    written = write_repo_tier_examples(dest)
    assert written
    for path in written:
        assert dest.resolve() in path.resolve().parents


def test_the_examples_say_they_are_not_loaded(tmp_path) -> None:
    """An example a reader mistakes for live configuration is worse than no
    example: the repo tier does not load by default."""
    written = write_repo_tier_examples(tmp_path / "d")
    text = "\n".join(p.read_text() for p in written)
    assert "not loaded" in text.lower() or "does not load" in text.lower()
    assert "never writes into a repository" in text


def test_writing_twice_does_not_clobber_an_edit(tmp_path) -> None:
    dest = tmp_path / "d"
    first = write_repo_tier_examples(dest)
    first[0].write_text("mine\n")
    second = write_repo_tier_examples(dest)
    assert first[0].read_text() == "mine\n"
    assert first[0] not in second


# ------------------------------------------------------------- the offer ----

def test_accepting_writes_and_records(env, home, capsys) -> None:
    result = maybe_offer_repo_tier_examples(
        environ=env, home=home, interactive=True, ask=lambda _: "y")
    assert result is True
    assert read_offer_decision(OFFER_REPO_TIER_EXAMPLES, env) is True
    assert (examples_destination(home) / "README.md").is_file()


def test_declining_records_and_writes_nothing(env, home) -> None:
    result = maybe_offer_repo_tier_examples(
        environ=env, home=home, interactive=True, ask=lambda _: "")
    assert result is False
    assert read_offer_decision(OFFER_REPO_TIER_EXAMPLES, env) is False
    assert not examples_destination(home).exists()


def test_a_declined_offer_is_never_made_again(env, home) -> None:
    """THE NAG TEST. The second call must not even reach the prompt — an ask
    that fires after a recorded answer is the corrosive shape ADR-0045 ruling 8
    names."""
    maybe_offer_repo_tier_examples(environ=env, home=home, interactive=True,
                                   ask=lambda _: "n")

    def _explode(_prompt: str) -> str:
        raise AssertionError("asked again after a recorded decline")

    assert maybe_offer_repo_tier_examples(
        environ=env, home=home, interactive=True, ask=_explode) is None


def test_a_non_interactive_run_is_never_asked(env, home) -> None:
    def _explode(_prompt: str) -> str:
        raise AssertionError("prompted without a TTY")

    assert maybe_offer_repo_tier_examples(
        environ=env, home=home, interactive=False, ask=_explode) is None
    assert read_offer_decision(OFFER_REPO_TIER_EXAMPLES, env) is None


def test_the_prompt_names_the_exact_path_before_asking(env, home) -> None:
    """``~/hypergumbo`` is a git working tree. Someone answering 'y' must know
    where the files land before they answer, not after."""
    seen: "list[str]" = []
    maybe_offer_repo_tier_examples(
        environ=env, home=home, interactive=True,
        ask=lambda prompt: (seen.append(prompt), "n")[1])
    assert str(examples_destination(home)) in seen[0]
    assert "once" in seen[0]


@pytest.mark.parametrize("answer,expected", [
    ("y", True), ("Y", True), ("yes", True), (" yes ", True),
    ("n", False), ("", False), ("no", False), ("nonsense", False),
])
def test_only_an_affirmative_counts_as_yes(env, home, answer, expected) -> None:
    """Default-to-no. A prompt whose stray keystroke writes files is not an
    offer."""
    assert maybe_offer_repo_tier_examples(
        environ=env, home=home, interactive=True, ask=lambda _: answer
    ) is expected


# ------------------------------------------------------- the main() wrapper --

def test_the_cli_wrapper_is_silent_when_there_is_nothing_to_offer(
    monkeypatch, tmp_path,
) -> None:
    """The production call site. With no checkout and no TTY it must do
    nothing at all — this is the path every ordinary run takes."""
    from hypergumbo_core.cli import _maybe_offer_repo_tier_examples
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    monkeypatch.setattr("sys.stdin", type("S", (), {"isatty": lambda self: False})())
    _maybe_offer_repo_tier_examples()
    assert not (tmp_path / "state").exists()


def test_a_failing_offer_never_breaks_the_run(monkeypatch) -> None:
    """An offer is a courtesy; a courtesy that turns a successful analysis into
    a traceback is strictly worse than no offer. Asserted by making the offer
    itself raise."""
    import hypergumbo_core.cli as cli_mod

    def _boom(**_kwargs: object) -> None:
        raise RuntimeError("state dir unwritable")

    monkeypatch.setattr(cli_mod, "maybe_offer_repo_tier_examples", _boom)
    cli_mod._maybe_offer_repo_tier_examples()  # must not raise


def test_main_calls_the_offer_exactly_once(monkeypatch, tmp_path) -> None:
    """Wired at the dispatch return, AFTER the command's own output, so an
    offer can never interleave with the thing the user actually ran."""
    import hypergumbo_core.cli as cli_mod
    calls: "list[int]" = []
    monkeypatch.setattr(cli_mod, "maybe_offer_repo_tier_examples",
                        lambda **_k: calls.append(1))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    assert cli_mod.main(["catalog-inventory", "--format", "json"]) == 0
    assert len(calls) == 1
