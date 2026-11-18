# SPDX-License-Identifier: AGPL-3.0-or-later
"""The wall-time line a smart-test run prints against its 60 s local target.

It gates nothing, so the ways it can be wrong are all in what it SAYS: a run
over the target that reads as under it, parts that do not add up to the total
with the difference hidden, "no junit report" printed as "0 tests", or the
slowest test left out of exactly the line that exists to name it.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from hypergumbo_core.wall_time import TARGET_SECONDS, SuiteTimes, junit_times, report

JUNIT = """<testsuites><testsuite>
<testcase classname="pkg.tests.test_a" name="test_quick" time="0.25"/>
<testcase classname="pkg.tests.test_a" name="test_slow" time="18.66"/>
<testcase classname="pkg.tests.test_b" name="test_other" time="1.0"/>
<testcase classname="pkg.tests.test_b" name="test_no_time"/>
<testcase classname="pkg.tests.test_b" name="test_bad_time" time="soon"/>
</testsuite></testsuites>
"""


def test_the_target_is_the_owners_sixty_seconds() -> None:
    assert TARGET_SECONDS == 60


def test_a_junit_report_gives_the_count_the_sum_and_the_slowest_test(tmp_path: Path) -> None:
    path = tmp_path / "junit.xml"
    path.write_text(JUNIT)
    times = junit_times(path)
    assert times == SuiteTimes(count=5, seconds=pytest.approx(19.91),
                              slowest=("pkg.tests.test_a::test_slow", 18.66))


def test_no_report_is_none_and_an_empty_one_is_zero_tests(tmp_path: Path) -> None:
    """ "Did not look" and "nothing ran" are different answers."""
    assert junit_times(tmp_path / "absent.xml") is None
    broken = tmp_path / "broken.xml"
    broken.write_text("<testsuites><testsuite>")
    assert junit_times(broken) is None
    empty = tmp_path / "empty.xml"
    empty.write_text("<testsuites><testsuite/></testsuites>")
    assert junit_times(empty) == SuiteTimes(count=0, seconds=0.0, slowest=None)
    nameless = tmp_path / "nameless.xml"
    nameless.write_text('<testsuites><testsuite><testcase time="2"/></testsuite></testsuites>')
    assert junit_times(nameless) == SuiteTimes(count=1, seconds=2.0, slowest=("?::?", 2.0))


TIMES = SuiteTimes(count=268, seconds=97.4, slowest=("pkg.tests.test_a::test_slow", 18.66))


def test_under_the_target_it_says_the_total_and_its_parts() -> None:
    line = report(total=41, parts={"the type gate": 6, "the stored graph": 2, "the tests": 19},
                  tests=TIMES)
    # 14 s are in none of the timed parts: said, not hidden.
    assert line == ("smart-test: 41 s to the end of the tests (local target 60 s): "
                    "the tests 19 s, the rest 14 s, the type gate 6 s, the stored graph 2 s "
                    "(268 tests, 97 test-seconds)")


def test_exactly_on_the_target_is_not_over_it() -> None:
    assert "OVER" not in report(total=60, parts={"the tests": 60}, tests=TIMES)
    assert "1 s OVER the 60 s local target" in report(total=61, parts={"the tests": 61}, tests=TIMES)


def test_over_the_target_it_says_by_how_much_and_names_the_slowest_test() -> None:
    line = report(total=263, parts={"the type gate": 5, "the analysis": 0, "the tests": 244},
                  tests=TIMES)
    assert line == ("smart-test: 263 s to the end of the tests, 203 s OVER the 60 s local target. "
                    "Largest first: the tests 244 s, the rest 14 s, the type gate 5 s "
                    "(268 tests, 97 test-seconds). Slowest test: 18.7 s pkg.tests.test_a::test_slow")


def test_without_a_junit_report_it_says_so_and_names_no_test() -> None:
    line = report(total=90, parts={"the tests": 80}, tests=None)
    assert line.endswith("Largest first: the tests 80 s, the rest 10 s (no junit report)")
    assert "0 tests" not in line and "Slowest" not in line
    # A report with no test case names no slowest test either.
    none_ran = report(total=90, parts={"the tests": 80},
                      tests=SuiteTimes(count=0, seconds=0.0, slowest=None))
    assert none_ran.endswith("(0 tests, 0 test-seconds)")


def test_a_rest_under_a_second_and_a_part_that_took_no_time_are_left_out() -> None:
    assert report(total=10.4, parts={"the type gate": 0, "the tests": 10}, tests=TIMES) == (
        "smart-test: 10 s to the end of the tests (local target 60 s): the tests 10 s "
        "(268 tests, 97 test-seconds)")
    assert report(total=0.2, parts={}, tests=None, target=5) == (
        "smart-test: 0 s to the end of the tests (local target 5 s): nothing timed (no junit report)")
