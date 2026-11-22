# SPDX-License-Identifier: AGPL-3.0-or-later
"""Rows about what a member is called, asked of what the analyzers record.

Eighteen shipped rows anchored a method's name at its start with
``symbol_name: "^..."``. The Python, Ruby, Swift, Scala and Groovy analyzers
name a method with its owner (``TestMath.test_adds``, Ruby
``MathTest#test_adds``), so those rows matched nothing: no test method of a
class was a test function in five languages, and no Falcon responder was a
route handler. The tests that existed for the rows handed them symbols built
by hand, named the way the row's author expected.

Every test here runs the ANALYZER on source and gives its symbols to the
shipped rows (``enrich_symbols``), with paths made relative to the tree as
the pipeline does before it enriches. Each first asserts that the symbol it
is about exists under the name the comment says, so a change in how an
analyzer names things fails here by name and not by silence.

What the rows ask for, per language:

- Python: ``test*`` methods of a class in a test file; a top-level ``test_*``
  function, but not a function nested in one (``outer.inner``).
- Ruby: ``test_*`` methods under ``test/`` or in ``*_test.rb``.
- Swift: ``test*`` and ``setUp`` / ``tearDown`` methods under ``Tests/`` or
  in a ``*Tests.swift`` file.
- Groovy: Spock's fixture methods in a test source set.
- Scala: nothing by name (JUnit's ``@Test`` only).
- Falcon: ``on_<verb>`` responders, also with a suffix, methods only.

A name alone is not a test: the production twins in each fixture
(``Backend.test_connection``, ``TestEventService#test_message_params``,
``Flight.testFlight``, ``Installer.setup``) stay unmarked.

A test function the rows mark is one the ranking demotes. The entry list
takes a ``test_function`` for an entry point and demotes it only where
``paths.is_test_file`` calls its file a test file, so a row whose file
condition were wider than that rule would put tests at the head of the list.
``_enriched`` refuses such a symbol in every fixture here.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from hypergumbo_core.analyze.base import AnalysisResult
from hypergumbo_core.framework_patterns import clear_pattern_cache, enrich_symbols
from hypergumbo_core.ir import Symbol
from hypergumbo_core.paths import is_test_file
from hypergumbo_lang_mainstream.groovy import analyze_groovy
from hypergumbo_lang_mainstream.py import analyze_python
from hypergumbo_lang_mainstream.ruby import analyze_ruby
from hypergumbo_lang_mainstream.scala import analyze_scala
from hypergumbo_lang_mainstream.swift import analyze_swift


def _enriched(
    tmp_path: Path, files: dict[str, str],
    analyze: Callable[[Path], AnalysisResult], *frameworks: str,
) -> dict[tuple[str, str], Symbol]:
    """Analyze ``files`` and enrich the symbols; keyed by (relative path, name)."""
    for relative, source in files.items():
        target = tmp_path / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(source)
    symbols = list(analyze(tmp_path).symbols)
    for symbol in symbols:
        if Path(symbol.path).is_absolute():
            symbol.path = Path(symbol.path).relative_to(tmp_path).as_posix()
    clear_pattern_cache()
    enrich_symbols(symbols, set(frameworks))
    undemoted = [
        f"{s.path}: {s.name}" for s in symbols
        if "test_function" in _concepts(s) and not is_test_file(s.path)
    ]
    assert not undemoted, f"marked a test where the ranking sees none: {undemoted}"
    return {(s.path, s.name): s for s in symbols if s.kind in ("method", "function")}


def _concepts(symbol: Symbol) -> set[str]:
    return {c["concept"] for c in (symbol.meta or {}).get("concepts", [])}


_PY_TEST = (
    "import unittest\n\n\n"
    "class TestMath:\n"
    "    def test_adds(self):\n"
    "        assert 1 + 2 == 3\n\n"
    "    def helper(self):\n"
    "        return 1\n\n\n"
    "class LegacyCase(unittest.TestCase):\n"
    "    def testCamel(self):\n"
    "        self.assertTrue(True)\n\n\n"
    "def test_top_level():\n"
    "    def helper():\n"
    "        return 1\n\n"
    "    def test_inner():\n"
    "        return 2\n\n"
    "    assert helper() + test_inner() == 3\n"
)
_PY_PROD = (
    "class Backend:\n"
    "    def test_connection(self):\n"
    "        return True\n"
)


class TestPython:
    def test_a_test_method_of_a_class_in_a_test_file(self, tmp_path: Path) -> None:
        got = _enriched(tmp_path, {"pkg/tests/test_math.py": _PY_TEST}, analyze_python)

        assert _concepts(got[("pkg/tests/test_math.py", "TestMath.test_adds")]) == {"test_function"}
        assert _concepts(got[("pkg/tests/test_math.py", "LegacyCase.testCamel")]) == {"test_function"}
        assert _concepts(got[("pkg/tests/test_math.py", "TestMath.helper")]) == set()

    def test_each_test_file_name_the_runners_collect(self, tmp_path: Path) -> None:
        files = {
            "test_root.py": _PY_TEST,
            "pkg/math_test.py": _PY_TEST,
            "pkg/tests.py": _PY_TEST,
            "pkg/test/cases.py": _PY_TEST,
        }

        got = _enriched(tmp_path, files, analyze_python)

        for path in files:
            assert _concepts(got[(path, "TestMath.test_adds")]) == {"test_function"}, path

    def test_a_production_method_named_like_a_test_is_not_one(self, tmp_path: Path) -> None:
        files = {
            "app/backend.py": _PY_PROD,
            "app/testing.py": _PY_PROD,
            "app/contest.py": _PY_PROD,
            # Not test files to the ranking, so not asked for either.
            "app/test.py": _PY_PROD,
            "app/load_tests.py": _PY_PROD,
        }

        got = _enriched(tmp_path, files, analyze_python)

        for path in files:
            assert _concepts(got[(path, "Backend.test_connection")]) == set(), path

    def test_a_function_nested_in_a_test_is_not_a_test(self, tmp_path: Path) -> None:
        got = _enriched(tmp_path, {"tests/test_math.py": _PY_TEST}, analyze_python)

        assert _concepts(got[("tests/test_math.py", "test_top_level")]) == {"test_function"}
        assert _concepts(got[("tests/test_math.py", "test_top_level.helper")]) == set()
        assert _concepts(got[("tests/test_math.py", "test_top_level.test_inner")]) == set()


_FALCON = (
    "import falcon\n\n\n"
    "class ThingsResource:\n"
    "    def on_get(self, req, resp):\n"
    "        resp.media = {}\n\n"
    "    def on_post(self, req, resp):\n"
    "        resp.status = falcon.HTTP_201\n\n"
    "    def on_get_collection(self, req, resp):\n"
    "        resp.media = []\n\n"
    "    def on_websocket(self, req, ws):\n"
    "        pass\n\n"
    "    def helper(self):\n"
    "        return 1\n\n"
    "    def fallback_on_get(self):\n"
    "        return 2\n\n\n"
    "def on_get(req, resp):\n"
    "    return None\n"
)


class TestFalcon:
    def test_responders_are_route_handlers(self, tmp_path: Path) -> None:
        got = _enriched(tmp_path, {"app.py": _FALCON}, analyze_python, "falcon")

        assert _concepts(got[("app.py", "ThingsResource.on_get")]) == {"route_handler"}
        assert _concepts(got[("app.py", "ThingsResource.on_post")]) == {"route_handler"}
        assert _concepts(got[("app.py", "ThingsResource.on_websocket")]) == {"websocket_handler"}

    def test_a_suffixed_responder(self, tmp_path: Path) -> None:
        got = _enriched(tmp_path, {"app.py": _FALCON}, analyze_python, "falcon")

        assert _concepts(got[("app.py", "ThingsResource.on_get_collection")]) == {"route_handler"}

    def test_other_methods_and_a_function_of_that_name_are_not(self, tmp_path: Path) -> None:
        got = _enriched(tmp_path, {"app.py": _FALCON}, analyze_python, "falcon")

        assert _concepts(got[("app.py", "ThingsResource.helper")]) == set()
        assert _concepts(got[("app.py", "ThingsResource.fallback_on_get")]) == set()
        assert _concepts(got[("app.py", "on_get")]) == set()

    def test_without_falcon_detected_nothing_is_a_responder(self, tmp_path: Path) -> None:
        got = _enriched(tmp_path, {"app.py": _FALCON}, analyze_python)

        assert _concepts(got[("app.py", "ThingsResource.on_get")]) == set()


_RB_TEST = (
    'require "minitest/autorun"\n\n'
    "module Calc\n"
    "  class MathTest < Minitest::Test\n"
    "    def test_adds\n"
    "      assert_equal 3, 1 + 2\n"
    "    end\n\n"
    "    def helper\n"
    "      1\n"
    "    end\n"
    "  end\n"
    "end\n"
)
_RB_PROD = (
    "module Instagram\n"
    "  class TestEventService\n"
    "    def test_message_params\n"
    "      {}\n"
    "    end\n\n"
    "    def self.test_run\n"
    "      new.test_message_params\n"
    "    end\n"
    "  end\n"
    "end\n"
)
_RB_SPEC = (
    'RSpec.describe "math" do\n'
    '  context "addition" do\n'
    '    it "adds" do\n'
    "      expect(1 + 2).to eq(3)\n"
    "    end\n"
    "  end\n"
    "end\n"
)


class TestRuby:
    def test_a_test_method_under_test_or_in_a_test_file(self, tmp_path: Path) -> None:
        files = {"test/math_test.rb": _RB_TEST, "lib/calc/math_test.rb": _RB_TEST,
                 "test/test_math.rb": _RB_TEST}

        got = _enriched(tmp_path, files, analyze_ruby)

        for path in files:
            assert _concepts(got[(path, "MathTest#test_adds")]) == {"test_function"}, path
            assert _concepts(got[(path, "MathTest#helper")]) == set(), path

    def test_a_production_method_named_like_a_test_is_not_one(self, tmp_path: Path) -> None:
        """Also in a file NAMED ``test_*.rb``: the name of a service class."""
        path = "app/services/instagram/test_event_service.rb"

        got = _enriched(tmp_path, {path: _RB_PROD}, analyze_ruby)

        assert _concepts(got[(path, "TestEventService#test_message_params")]) == set()
        assert _concepts(got[(path, "TestEventService.test_run")]) == set()

    def test_an_rspec_example_is_not_a_symbol(self, tmp_path: Path) -> None:
        """The two RSpec rows describe a call with a block. Pinned so that the
        day ruby.py records examples, this says the rows need a look."""
        got = _enriched(tmp_path, {"spec/math_spec.rb": _RB_SPEC}, analyze_ruby)

        assert got == {}


_SWIFT_TEST = (
    "import XCTest\n\n"
    "final class XTests: XCTestCase {\n"
    "    override func setUp() {}\n"
    "    override func tearDownWithError() throws {}\n"
    "    func testAdds() { XCTAssertEqual(1 + 2, 3) }\n"
    "    func helper() -> Int { 1 }\n"
    "}\n"
)
_SWIFT_PROD = (
    "final class Flight {\n"
    "    func testFlight() -> Bool { true }\n"
    "    func setUp() {}\n"
    "}\n"
)


class TestSwift:
    def test_test_and_lifecycle_methods_of_a_test_target(self, tmp_path: Path) -> None:
        files = {"Tests/XTests/XTests.swift": _SWIFT_TEST, "test/Flow.swift": _SWIFT_TEST,
                 "Checks/XTests.swift": _SWIFT_TEST}

        got = _enriched(tmp_path, files, analyze_swift)

        for path in files:
            assert _concepts(got[(path, "XTests.testAdds")]) == {"test_function"}, path
            assert _concepts(got[(path, "XTests.setUp")]) == {"test_lifecycle"}, path
            assert _concepts(got[(path, "XTests.tearDownWithError")]) == {"test_lifecycle"}, path
            assert _concepts(got[(path, "XTests.helper")]) == set(), path

    def test_a_production_method_named_like_a_test_is_not_one(self, tmp_path: Path) -> None:
        # The second path is an Xcode test group the ranking does not know.
        files = {"Sources/App/Flight.swift": _SWIFT_PROD, "AppUITests/Flow.swift": _SWIFT_PROD,
                 "Sources/Contests/Flight.swift": _SWIFT_PROD}

        got = _enriched(tmp_path, files, analyze_swift)

        for path in files:
            assert _concepts(got[(path, "Flight.testFlight")]) == set(), path
            assert _concepts(got[(path, "Flight.setUp")]) == set(), path


_SCALA = (
    "import org.junit.Test\n\n"
    "class MathSuite extends munit.FunSuite {\n"
    "  @Test def testAdds(): Unit = assert(1 + 2 == 3)\n"
    "  def test(): Unit = ()\n"
    "  def tests(): List[Int] = List(1)\n"
    "  def testServer(): Int = 1\n"
    '  test("adds") { assert(1 + 2 == 3) }\n'
    "}\n\n"
    "def testWith(x: Int): Int = x\n"
)


class TestScala:
    def test_only_the_annotation_makes_a_def_a_test(self, tmp_path: Path) -> None:
        path = "src/test/scala/MathSuite.scala"

        got = _enriched(tmp_path, {path: _SCALA}, analyze_scala)

        assert _concepts(got[(path, "MathSuite.testAdds")]) == {"test_function"}
        for name in ("MathSuite.test", "MathSuite.tests", "MathSuite.testServer", "testWith"):
            assert _concepts(got[(path, name)]) == set(), name


_GROOVY_SPEC = (
    "import spock.lang.Specification\n\n"
    "class MathSpec extends Specification {\n"
    "    def setup() {}\n"
    "    def cleanupSpec() {}\n"
    "    def helper() { 1 }\n"
    "}\n"
)
# The same specification with a feature method after its two fixture methods.
# The grammar does not recover from this arrangement: groovy.py then records
# no class, `setup` and `cleanupSpec` WITHOUT their class, and no `helper`
# (424 of the 2,801 Groovy methods of gradle's testing/ tree are recorded
# without a class). The row has to read both shapes.
_GROOVY_SPEC_WITH_FEATURE = (
    "import spock.lang.Specification\n\n"
    "class MathSpec extends Specification {\n"
    "    def setup() {}\n"
    "    def cleanupSpec() {}\n"
    '    def "should add"() {\n'
    "        expect: 1 + 2 == 3\n"
    "    }\n"
    "    def helper() { 1 }\n"
    "}\n"
)
_GROOVY_PROD = (
    "class Installer {\n"
    "    def setup() { 1 }\n"
    "    def cleanup() { 2 }\n"
    "}\n"
)


class TestGroovy:
    def test_spock_fixture_methods_in_a_test_source_set(self, tmp_path: Path) -> None:
        files = {"src/test/groovy/MathSpec.groovy": _GROOVY_SPEC,
                 "src/integTest/groovy/Flow.groovy": _GROOVY_SPEC,
                 "checks/MathSpec.groovy": _GROOVY_SPEC}

        got = _enriched(tmp_path, files, analyze_groovy)

        for path in files:
            assert _concepts(got[(path, "MathSpec.setup")]) == {"test_lifecycle"}, path
            assert _concepts(got[(path, "MathSpec.cleanupSpec")]) == {"test_lifecycle"}, path
            assert _concepts(got[(path, "MathSpec.helper")]) == set(), path

    def test_a_fixture_method_recorded_without_its_class(self, tmp_path: Path) -> None:
        path = "src/test/groovy/MathSpec.groovy"

        got = _enriched(tmp_path, {path: _GROOVY_SPEC_WITH_FEATURE}, analyze_groovy)

        assert _concepts(got[(path, "setup")]) == {"test_lifecycle"}
        assert _concepts(got[(path, "cleanupSpec")]) == {"test_lifecycle"}

    def test_a_production_setup_method_is_not_a_fixture(self, tmp_path: Path) -> None:
        path = "src/main/groovy/Installer.groovy"

        got = _enriched(tmp_path, {path: _GROOVY_PROD}, analyze_groovy)

        assert _concepts(got[(path, "Installer.setup")]) == set()
        assert _concepts(got[(path, "Installer.cleanup")]) == set()

    def test_a_feature_method_named_by_a_string_is_not_a_symbol(self, tmp_path: Path) -> None:
        """The Spock feature row cannot match while groovy.py records none."""
        path = "src/test/groovy/MathSpec.groovy"

        got = _enriched(tmp_path, {path: _GROOVY_SPEC_WITH_FEATURE}, analyze_groovy)

        assert got, "the fixture produced no method at all"
        assert not [name for _, name in got if "should" in name]
