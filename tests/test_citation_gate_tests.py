# SPDX-License-Identifier: AGPL-3.0-or-later
"""WI-lujon: a string literal is an edge the import graph cannot see.

``test_module_key_axis.py::test_every_cited_emission_site_still_exists`` pins
that every ``file:line`` citation in ``MODULE_KEY_NOTIONS`` still contains its
quoted anchor. The INV-sihom fix added lines to ``bash.py`` and moved the
redirect emission out from under its citation. The per-PR gate was GREEN: the
citation test lives in hypergumbo-core and names bash.py by STRING, so the
reverse slice never selected it. It fired on the next full-suite cron and was
repaired two firings later by an unrelated PR.

The controls here are built around one fact that is easy to get wrong, and the
item itself got it wrong: ``test_module_key_axis.py`` does not contain the
string ``bash`` at all. The citation lives in the SOURCE the test reads, so the
fix the item proposed -- "select a test file that contains a string literal
naming a changed source path" -- would not have caught its own example.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
HELPER = REPO_ROOT / "scripts" / "citation_gate_tests.py"

# EVERY PATH BELOW IS ASSEMBLED FROM PARTS, and that is load-bearing rather
# than fussy. Written out whole, this file would contain the very slash-path
# literals it is testing for, the gate would correctly select THIS file as a
# citer of bash.py and sketch.py, and two of the controls below -- "a file
# nobody cites selects nothing" and "the selection is exactly the citation
# test" -- would silently become assertions about their own text. They failed
# that way on the first run, which is how this comment came to exist.
_MAINSTREAM = "hypergumbo_lang_mainstream"
_CORE = "hypergumbo_core"

#: The rot, by name. INV-sihom shifted this file's redirect emission.
CITED = "/".join(
    ["packages", "hypergumbo-lang-mainstream", "src", _MAINSTREAM, "bash.py"]
)
#: The module that carries the citation, and the test that checks it.
CITER = "/".join(
    ["packages", "hypergumbo-core", "src", _CORE, "module_key_axis.py"]
)
CITATION_TEST = "/".join(
    ["packages", "hypergumbo-core", "tests", "test_module_key_axis.py"]
)
#: A source no slash-path literal in the tree names.
UNCITED = "/".join(["packages", "hypergumbo-core", "src", _CORE, "sketch.py"])
#: Cited by taint.py and by test_taint.py, which is the direct-citer arm.
CITED_BY_A_TEST = "/".join(
    ["packages", "hypergumbo-core", "src", _CORE, "cli.py"]
)


def run_helper(changed: list[str]) -> list[str]:
    proc = subprocess.run(
        [sys.executable, str(HELPER), str(REPO_ROOT)],
        input="\n".join(changed),
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stderr
    return [line for line in proc.stdout.splitlines() if line]


def _shipped_sources() -> dict:
    """Import-root-relative path -> file, for every shipped Python source.

    ONE GLOB, THE SAME ONE THE HELPER USES. The first cut of this file used
    ``packages/*/src/*/**/*.py`` here and the helper used
    ``packages/*/src/**/*.py``; they agree on Python 3.12 and the extra
    component returns NOTHING on the 3.11 CI runs, so the population came back
    empty and the enumeration test measured a tree it could not see. It failed
    loudly only because it asserts a floor on its own population -- without
    that, "no citers" and "no sources" are the same green.
    """
    found = {}
    for path in REPO_ROOT.glob("packages/*/src/**/*.py"):
        parts = path.parts
        if "src" not in parts:  # pragma: no cover - glob guarantees it
            continue
        found["/".join(parts[parts.index("src") + 1:])] = path
    assert len(found) > 100, (
        f"only {len(found)} shipped sources found -- this is an instrument "
        "fault, not a finding"
    )
    return found


class TestTheRotItselfIsNowCaught:
    """The item's own example, re-run end to end."""

    def test_changing_the_cited_file_selects_the_citation_test(self) -> None:
        assert CITATION_TEST in run_helper([CITED])

    def test_the_citation_is_really_there(self) -> None:
        """Execute the premise rather than assert it."""
        text = (REPO_ROOT / CITER).read_text(encoding="utf-8")
        assert "/".join([_MAINSTREAM, "bash.py"]) in text

    def test_the_proposed_one_hop_fix_would_have_missed_this(self) -> None:
        """Why the gate needs two hops, written down so it is not 'simplified'.

        WI-lujon proposed selecting a TEST that names the changed path. The
        test that rotted names neither the path nor the file's stem -- the
        citation lives in the module it reads. A one-hop gate would have
        shipped, measured clean against a tree where nothing was wrong, and
        left the defect exactly where it was.
        """
        text = (REPO_ROOT / CITATION_TEST).read_text(encoding="utf-8")
        assert "bash" not in text


class TestItIsAnEdgeAndNotAWordMatch:
    def test_a_file_nobody_cites_selects_nothing(self) -> None:
        """The non-vacuity control: the gate must be able to return empty."""
        assert not run_helper([UNCITED])

    def test_a_bare_basename_is_not_a_citation(self) -> None:
        """``bash.py`` appears in five test files with nothing to do with it.

        Keying on the basename would trade a precise edge for noise, and noise
        is what gets an over-selecting gate narrowed later by someone who does
        not know why it was wide.
        """
        noisy = {
            p.name
            for p in REPO_ROOT.glob("packages/*/tests/test_*.py")
            if "bash.py" in p.read_text(encoding="utf-8", errors="ignore")
        }
        assert len(noisy) >= 2, noisy
        selected = {Path(p).name for p in run_helper([CITED])}
        assert Path(CITATION_TEST).name in selected
        # The claim is about what a basename must NOT drag in, stated as a
        # disjointness rather than an exact set: the helper's own module
        # docstring cites this same path, so its test is legitimately selected
        # too, and pinning an exact set would make this control a statement
        # about who happens to have written the word down.
        assert not (noisy - {Path(CITATION_TEST).name}) & selected, sorted(
            (noisy - {Path(CITATION_TEST).name}) & selected
        )

    def test_a_file_does_not_cite_itself(self) -> None:
        assert CITER not in run_helper([CITER])


class TestTheSecondHopLandsOnTheRightTests:
    def test_a_test_that_cites_directly_needs_no_second_hop(self) -> None:
        """``test_taint.py`` names the core CLI module by path in its own text.

        Spelled out here, that path would make this file a citer too — which
        is exactly what the guard at the bottom of this module refuses, and
        what it caught on its first run."""
        selected = run_helper([CITED_BY_A_TEST])
        assert "/".join(
            ["packages", "hypergumbo-core", "tests", "test_taint.py"]
        ) in selected

    def test_the_hop_uses_the_import_path_not_the_bare_stem(self) -> None:
        """Measured: the stem reaches 142 test files, the import path 64.

        Both reach the module's own test. The bare stem matches any test that
        merely mentions the word, which is a word match dressed as an edge.
        """
        selected = set(run_helper([CITED_BY_A_TEST]))
        by_stem = {
            p.relative_to(REPO_ROOT).as_posix()
            for p in REPO_ROOT.glob("packages/*/tests/test_*.py")
            if "taint" in p.read_text(encoding="utf-8", errors="ignore")
        }
        assert selected, "the cli.py citation selects nothing"
        assert len(selected) < len(by_stem)


class TestTheWholeClassIsSmallAndKnown:
    def test_the_citation_family_is_enumerable(self) -> None:
        """Sized rather than assumed: slash-path literals naming a shipped
        source appear in a handful of files, which is what makes this gate
        cheap. If this number grows a lot, the hop-2 breadth is worth
        re-measuring rather than inherited."""
        sources = _shipped_sources()
        citers = set()
        for candidate in (
            list(REPO_ROOT.glob("packages/*/src/**/*.py"))
            + list(REPO_ROOT.glob("packages/*/tests/*.py"))
            + list(REPO_ROOT.glob("tests/*.py"))
            + list(REPO_ROOT.glob("scripts/*.py"))
        ):
            text = candidate.read_text(encoding="utf-8", errors="ignore")
            for literal in sources:
                if literal in text and not candidate.as_posix().endswith(literal):
                    citers.add(candidate.name)
                    break
        assert 1 <= len(citers) <= 40, sorted(citers)
        assert "module_key_axis.py" in citers


def test_this_file_is_not_itself_a_citer() -> None:
    """The controls above assert an EXACT selection; that needs this to hold.

    A slash-path literal naming a shipped source, written out whole anywhere in
    this file, makes the gate select this file too and turns the equality
    assertions into statements about their own text. Assembling the paths from
    parts is what prevents that, and this is the check that the next edit does
    not quietly undo it.
    """
    text = Path(__file__).read_text(encoding="utf-8")
    written_out = sorted(
        literal for literal in _shipped_sources() if literal in text
    )
    assert not written_out, written_out
