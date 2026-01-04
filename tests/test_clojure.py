"""Tests for Clojure language analyzer.

Clojure is a dynamic, functional Lisp dialect hosted on the JVM.
Key constructs: defn (functions), ns (namespaces), def (vars),
defmacro (macros), defprotocol/defrecord (protocols/types).

Test strategy:
- Basic function detection (defn)
- Namespace detection with require
- Function calls
- Macro detection
- Multimethod detection
"""

from pathlib import Path
from unittest.mock import patch

import pytest

from hypergumbo.analyze import clojure as clojure_module
from hypergumbo.analyze.clojure import analyze_clojure


def make_clj_file(tmp: Path, name: str, content: str) -> Path:
    """Create a .clj file for testing."""
    f = tmp / name
    f.write_text(content, encoding="utf-8")
    return f


class TestClojureAnalyzer:
    """Test Clojure symbol and edge detection."""

    def test_detects_defn(self, tmp_path: Path) -> None:
        """Detect function definitions with defn."""
        make_clj_file(
            tmp_path,
            "core.clj",
            """
(ns myapp.core)

(defn hello
  "Greets a user."
  [name]
  (str "Hello, " name))

(defn add
  [a b]
  (+ a b))
""",
        )
        result = analyze_clojure(tmp_path)
        assert not result.skipped

        names = [s.name for s in result.symbols]
        assert "hello" in names
        assert "add" in names

        hello = next(s for s in result.symbols if s.name == "hello")
        assert hello.kind == "function"
        assert hello.language == "clojure"

    def test_detects_ns(self, tmp_path: Path) -> None:
        """Detect namespace declarations."""
        make_clj_file(
            tmp_path,
            "core.clj",
            """
(ns myapp.core
  (:require [clojure.string :as str]
            [myapp.utils :refer [parse-int]]))

(defn process [s]
  (str/trim s))
""",
        )
        result = analyze_clojure(tmp_path)
        assert not result.skipped

        # Namespace should be detected
        names = [s.name for s in result.symbols]
        assert "myapp.core" in names

        ns_sym = next(s for s in result.symbols if s.name == "myapp.core")
        assert ns_sym.kind == "module"

    def test_detects_def(self, tmp_path: Path) -> None:
        """Detect constant/var definitions with def."""
        make_clj_file(
            tmp_path,
            "config.clj",
            """
(ns myapp.config)

(def config-file "config.edn")

(def default-port 8080)
""",
        )
        result = analyze_clojure(tmp_path)
        assert not result.skipped

        names = [s.name for s in result.symbols]
        assert "config-file" in names
        assert "default-port" in names

        cfg = next(s for s in result.symbols if s.name == "config-file")
        assert cfg.kind == "variable"

    def test_detects_defmacro(self, tmp_path: Path) -> None:
        """Detect macro definitions."""
        make_clj_file(
            tmp_path,
            "macros.clj",
            """
(ns myapp.macros)

(defmacro unless
  [pred & body]
  `(if (not ~pred)
     (do ~@body)))
""",
        )
        result = analyze_clojure(tmp_path)
        assert not result.skipped

        names = [s.name for s in result.symbols]
        assert "unless" in names

        mac = next(s for s in result.symbols if s.name == "unless")
        assert mac.kind == "macro"

    def test_detects_function_calls(self, tmp_path: Path) -> None:
        """Detect function call edges."""
        make_clj_file(
            tmp_path,
            "app.clj",
            """
(ns myapp.app)

(defn helper [x]
  (* x 2))

(defn main []
  (println (helper 21)))
""",
        )
        result = analyze_clojure(tmp_path)
        assert not result.skipped

        # main should call helper
        edges = result.edges
        call_edges = [e for e in edges if e.edge_type == "calls"]
        assert len(call_edges) >= 1

        # Find edge from main to helper
        edge_pairs = [(e.src, e.dst) for e in call_edges]
        main_sym = next(s for s in result.symbols if s.name == "main")
        helper_sym = next(s for s in result.symbols if s.name == "helper")
        assert (main_sym.id, helper_sym.id) in edge_pairs

    def test_detects_require_imports(self, tmp_path: Path) -> None:
        """Detect require statements as import edges."""
        make_clj_file(
            tmp_path,
            "core.clj",
            """
(ns myapp.core
  (:require [clojure.string :as str]
            [clojure.java.io :as io]))

(defn read-file [path]
  (slurp (io/file path)))
""",
        )
        result = analyze_clojure(tmp_path)
        assert not result.skipped

        imports = [e for e in result.edges if e.edge_type == "imports"]
        imported_names = [e.dst for e in imports]

        # Should import clojure.string and clojure.java.io
        assert any("clojure.string" in dst for dst in imported_names)
        assert any("clojure.java.io" in dst for dst in imported_names)

    def test_detects_defprotocol(self, tmp_path: Path) -> None:
        """Detect protocol definitions."""
        make_clj_file(
            tmp_path,
            "protocols.clj",
            """
(ns myapp.protocols)

(defprotocol Greetable
  (greet [this] "Returns a greeting"))
""",
        )
        result = analyze_clojure(tmp_path)
        assert not result.skipped

        names = [s.name for s in result.symbols]
        assert "Greetable" in names

        proto = next(s for s in result.symbols if s.name == "Greetable")
        assert proto.kind == "protocol"

    def test_detects_defrecord(self, tmp_path: Path) -> None:
        """Detect record type definitions."""
        make_clj_file(
            tmp_path,
            "types.clj",
            """
(ns myapp.types)

(defrecord User [name email])
""",
        )
        result = analyze_clojure(tmp_path)
        assert not result.skipped

        names = [s.name for s in result.symbols]
        assert "User" in names

        rec = next(s for s in result.symbols if s.name == "User")
        assert rec.kind == "record"

    def test_detects_defmulti(self, tmp_path: Path) -> None:
        """Detect multimethod definitions."""
        make_clj_file(
            tmp_path,
            "multi.clj",
            """
(ns myapp.multi)

(defmulti area :shape)

(defmethod area :circle [{:keys [radius]}]
  (* Math/PI radius radius))

(defmethod area :rectangle [{:keys [width height]}]
  (* width height))
""",
        )
        result = analyze_clojure(tmp_path)
        assert not result.skipped

        names = [s.name for s in result.symbols]
        assert "area" in names  # The multimethod

        multi = next(s for s in result.symbols if s.name == "area")
        assert multi.kind == "multimethod"

    def test_handles_empty_file(self, tmp_path: Path) -> None:
        """Handle empty Clojure file gracefully."""
        make_clj_file(tmp_path, "empty.clj", "")
        result = analyze_clojure(tmp_path)
        assert not result.skipped
        # Empty file should produce no symbols

    def test_handles_syntax_error(self, tmp_path: Path) -> None:
        """Handle files with syntax errors gracefully."""
        make_clj_file(tmp_path, "bad.clj", "(defn foo [x")  # Unclosed
        result = analyze_clojure(tmp_path)
        # Should not crash, may skip or produce partial results
        assert result is not None

    def test_cross_file_calls(self, tmp_path: Path) -> None:
        """Detect calls across files via two-pass resolution."""
        make_clj_file(
            tmp_path,
            "utils.clj",
            """
(ns myapp.utils)

(defn double [x]
  (* x 2))
""",
        )
        make_clj_file(
            tmp_path,
            "core.clj",
            """
(ns myapp.core
  (:require [myapp.utils :refer [double]]))

(defn quadruple [x]
  (double (double x)))
""",
        )
        result = analyze_clojure(tmp_path)
        assert not result.skipped

        edges = result.edges
        call_edges = [e for e in edges if e.edge_type == "calls"]

        # quadruple should call double
        quad = next(s for s in result.symbols if s.name == "quadruple")
        dbl = next(s for s in result.symbols if s.name == "double")
        edge_pairs = [(e.src, e.dst) for e in call_edges]
        assert (quad.id, dbl.id) in edge_pairs

    def test_simple_require_syntax(self, tmp_path: Path) -> None:
        """Handle simple require syntax without vector brackets."""
        make_clj_file(
            tmp_path,
            "simple.clj",
            """
(ns myapp.simple
  (:require clojure.string
            clojure.set))

(defn foo [] nil)
""",
        )
        result = analyze_clojure(tmp_path)
        assert not result.skipped

        imports = [e for e in result.edges if e.edge_type == "imports"]
        imported_names = [e.dst for e in imports]

        assert any("clojure.string" in dst for dst in imported_names)
        assert any("clojure.set" in dst for dst in imported_names)

    def test_skips_edn_files(self, tmp_path: Path) -> None:
        """EDN data files are skipped (not source code)."""
        # Create an EDN file (data file)
        edn_file = tmp_path / "config.edn"
        edn_file.write_text('{:port 8080 :host "localhost"}')

        # Create a real Clojure file
        make_clj_file(tmp_path, "core.clj", "(ns app.core)\n(defn start [] nil)")

        result = analyze_clojure(tmp_path)
        assert not result.skipped

        # Should have symbols from core.clj but not config.edn
        paths = [s.path for s in result.symbols]
        assert any("core.clj" in p for p in paths)
        # EDN files don't generate symbols (they're data files)

    def test_call_to_unresolved_function(self, tmp_path: Path) -> None:
        """Handle calls to functions we can't resolve (e.g., core library)."""
        make_clj_file(
            tmp_path,
            "app.clj",
            """
(ns myapp.app)

(defn process [x]
  (println x)
  (str x))
""",
        )
        result = analyze_clojure(tmp_path)
        assert not result.skipped

        # Should have the process function
        names = [s.name for s in result.symbols]
        assert "process" in names

        # println and str are unresolved (stdlib) - no call edges to them
        # This tests the branch where callee lookup returns None

    def test_skipped_when_unavailable(self, tmp_path: Path) -> None:
        """Analysis skips gracefully when tree-sitter unavailable."""
        make_clj_file(tmp_path, "core.clj", "(ns app.core)")

        with patch.object(
            clojure_module,
            "is_clojure_tree_sitter_available",
            return_value=False,
        ):
            with pytest.warns(UserWarning, match="Clojure analysis skipped"):
                result = clojure_module.analyze_clojure(tmp_path)

        assert result.skipped is True
        assert "tree-sitter-language-pack" in result.skip_reason


class TestClojureSignatureExtraction:
    """Tests for Clojure function signature extraction."""

    def test_simple_params(self, tmp_path: Path) -> None:
        """Extract signature from simple defn with params."""
        make_clj_file(
            tmp_path,
            "core.clj",
            "(defn add [x y] (+ x y))",
        )
        result = analyze_clojure(tmp_path)
        funcs = [s for s in result.symbols if s.kind == "function" and s.name == "add"]
        assert len(funcs) == 1
        assert funcs[0].signature == "[x, y]"

    def test_no_params(self, tmp_path: Path) -> None:
        """Extract signature from defn with no params."""
        make_clj_file(
            tmp_path,
            "core.clj",
            "(defn no-args [] 42)",
        )
        result = analyze_clojure(tmp_path)
        funcs = [s for s in result.symbols if s.kind == "function" and s.name == "no-args"]
        assert len(funcs) == 1
        assert funcs[0].signature == "[]"

    def test_rest_params(self, tmp_path: Path) -> None:
        """Extract signature from defn with rest params (& rest)."""
        make_clj_file(
            tmp_path,
            "core.clj",
            "(defn variadic [first & rest] (cons first rest))",
        )
        result = analyze_clojure(tmp_path)
        funcs = [s for s in result.symbols if s.kind == "function" and s.name == "variadic"]
        assert len(funcs) == 1
        assert funcs[0].signature == "[first, &, rest]"

    def test_docstring_before_params(self, tmp_path: Path) -> None:
        """Extract signature with docstring before params."""
        make_clj_file(
            tmp_path,
            "core.clj",
            '''
(defn documented
  "A documented function."
  [x]
  x)
''',
        )
        result = analyze_clojure(tmp_path)
        funcs = [s for s in result.symbols if s.kind == "function" and s.name == "documented"]
        assert len(funcs) == 1
        assert funcs[0].signature == "[x]"
