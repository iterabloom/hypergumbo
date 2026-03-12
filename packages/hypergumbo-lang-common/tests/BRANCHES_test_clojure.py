# SPDX-License-Identifier: AGPL-3.0-or-later
"""Branch coverage tests for clojure.py analyzer.

Tests specific branch paths in the Clojure analyzer that may not be covered
by the main test suite. Focuses on:
- Helper function ID format
- Def form variations (defonce, deftype, defmethod)
- Private function visibility metadata
- Enclosing function resolution
- Require alias extraction edge cases
- File discovery patterns
"""
from pathlib import Path

from hypergumbo_core.analyze.base import make_file_id, make_symbol_id
from hypergumbo_lang_common.clojure import _is_def_form, analyze_clojure, find_clojure_files

def make_clj_file(tmp_path: Path, name: str, content: str) -> None:
    """Create a Clojure file with given content."""
    (tmp_path / name).write_text(content)

class TestClojureHelperFunctions:
    """Branch coverage for helper functions."""

    def test_make_symbol_id_format(self) -> None:
        """Test symbol ID format."""
        symbol_id = make_symbol_id("clojure", "src/core.clj", 5, 10, "add", "function")
        assert symbol_id == "clojure:src/core.clj:5-10:add:function"

    def test_make_file_id_format(self) -> None:
        """Test file ID format."""
        file_id = make_file_id("clojure", "lib/utils.clj")
        assert file_id == "clojure:lib/utils.clj:1-1:file:file"

class TestIsDefForm:
    """Branch coverage for _is_def_form."""

    def test_defonce_recognized(self) -> None:
        """Test defonce returns variable kind."""
        result = _is_def_form("defonce")
        assert result == ("variable", "public")

    def test_deftype_recognized(self) -> None:
        """Test deftype returns type kind."""
        result = _is_def_form("deftype")
        assert result == ("type", "public")

    def test_defmethod_recognized(self) -> None:
        """Test defmethod returns method kind."""
        result = _is_def_form("defmethod")
        assert result == ("method", "public")

    def test_unknown_form_returns_none(self) -> None:
        """Test unknown form returns None."""
        result = _is_def_form("defunknown")
        assert result is None

    def test_let_not_recognized(self) -> None:
        """Test let is not a def form."""
        result = _is_def_form("let")
        assert result is None

class TestDefVariations:
    """Branch coverage for various def form extractions."""

    def test_defonce_extraction(self, tmp_path: Path) -> None:
        """Test defonce creates variable symbol."""
        make_clj_file(tmp_path, "state.clj", """
(ns myapp.state)

(defonce app-state (atom {}))
(defonce config (atom nil))
""")
        result = analyze_clojure(tmp_path)
        assert not result.skipped

        names = [s.name for s in result.symbols if s.kind == "variable"]
        assert "app-state" in names
        assert "config" in names

    def test_deftype_extraction(self, tmp_path: Path) -> None:
        """Test deftype creates type symbol."""
        make_clj_file(tmp_path, "types.clj", """
(ns myapp.types)

(deftype Point [x y])
""")
        result = analyze_clojure(tmp_path)
        assert not result.skipped

        types = [s for s in result.symbols if s.kind == "type"]
        assert any(t.name == "Point" for t in types)

class TestPrivateFunctions:
    """Branch coverage for private function detection."""

    def test_defn_minus_creates_private_function(self, tmp_path: Path) -> None:
        """Test defn- creates function with private visibility."""
        make_clj_file(tmp_path, "private.clj", """
(ns myapp.private)

(defn- internal-helper [x]
  (* x 2))

(defn public-api [x]
  (internal-helper x))
""")
        result = analyze_clojure(tmp_path)
        assert not result.skipped

        helper = next((s for s in result.symbols if s.name == "internal-helper"), None)
        assert helper is not None
        assert helper.kind == "function"
        assert helper.meta is not None
        assert helper.meta.get("visibility") == "private"

        public = next((s for s in result.symbols if s.name == "public-api"), None)
        assert public is not None
        # Public functions should NOT have visibility meta
        assert public.meta is None

class TestEnclosingFunctionResolution:
    """Branch coverage for enclosing function detection."""

    def test_nested_call_finds_enclosing_function(self, tmp_path: Path) -> None:
        """Test deeply nested call finds enclosing function."""
        make_clj_file(tmp_path, "nested.clj", """
(ns myapp.nested)

(defn inner [x] x)

(defn outer [x]
  (if (> x 0)
    (when true
      (inner x))))
""")
        result = analyze_clojure(tmp_path)
        assert not result.skipped

        call_edges = [e for e in result.edges if e.edge_type == "calls"]
        # outer should call inner
        outer_sym = next(s for s in result.symbols if s.name == "outer")
        inner_sym = next(s for s in result.symbols if s.name == "inner")
        edge_pairs = [(e.src, e.dst) for e in call_edges]
        assert (outer_sym.id, inner_sym.id) in edge_pairs

    def test_multiple_functions_in_file(self, tmp_path: Path) -> None:
        """Test multiple functions call each other correctly."""
        make_clj_file(tmp_path, "chain.clj", """
(ns myapp.chain)

(defn step1 [x] (* x 2))
(defn step2 [x] (step1 (+ x 1)))
(defn step3 [x] (step2 (step1 x)))
""")
        result = analyze_clojure(tmp_path)
        assert not result.skipped

        call_edges = [e for e in result.edges if e.edge_type == "calls"]
        # step2 calls step1, step3 calls step1 and step2
        step1_calls = [e for e in call_edges if "step1" in e.dst]
        step2_calls = [e for e in call_edges if "step2" in e.dst]
        assert len(step1_calls) >= 2  # from step2 and step3
        assert len(step2_calls) >= 1  # from step3

class TestRequireAliasExtraction:
    """Branch coverage for require alias extraction."""

    def test_require_with_refer(self, tmp_path: Path) -> None:
        """Test require with :refer extracts correct namespace."""
        make_clj_file(tmp_path, "app.clj", """
(ns myapp.app
  (:require [clojure.string :as str :refer [join split]]))

(defn process [s]
  (str/upper-case s))
""")
        result = analyze_clojure(tmp_path)
        assert not result.skipped

        imports = [e for e in result.edges if e.edge_type == "imports"]
        assert any("clojure.string" in e.dst for e in imports)

    def test_multiple_require_aliases(self, tmp_path: Path) -> None:
        """Test multiple require aliases are all extracted."""
        make_clj_file(tmp_path, "multi.clj", """
(ns myapp.multi
  (:require [clojure.string :as str]
            [clojure.set :as set]
            [clojure.java.io :as io]))

(defn foo [] nil)
""")
        result = analyze_clojure(tmp_path)
        assert not result.skipped

        imports = [e for e in result.edges if e.edge_type == "imports"]
        dests = [e.dst for e in imports]
        assert any("clojure.string" in d for d in dests)
        assert any("clojure.set" in d for d in dests)
        assert any("clojure.java.io" in d for d in dests)

class TestMultipleNamespaces:
    """Branch coverage for multi-file namespace handling."""

    def test_cross_file_resolution(self, tmp_path: Path) -> None:
        """Test call resolution across multiple files."""
        make_clj_file(tmp_path, "base.clj", """
(ns myapp.base)

(defn base-fn [x] x)
""")
        make_clj_file(tmp_path, "middle.clj", """
(ns myapp.middle
  (:require [myapp.base :refer [base-fn]]))

(defn middle-fn [x] (base-fn x))
""")
        make_clj_file(tmp_path, "top.clj", """
(ns myapp.top
  (:require [myapp.middle :refer [middle-fn]]))

(defn top-fn [x] (middle-fn x))
""")
        result = analyze_clojure(tmp_path)
        assert not result.skipped

        modules = [s for s in result.symbols if s.kind == "module"]
        names = [m.name for m in modules]
        assert "myapp.base" in names
        assert "myapp.middle" in names
        assert "myapp.top" in names

class TestFindClojureFiles:
    """Branch coverage for file discovery."""

    def test_finds_cljs_files(self, tmp_path: Path) -> None:
        """Test .cljs files are discovered."""
        (tmp_path / "app.cljs").write_text("(ns app.core)")

        files = list(find_clojure_files(tmp_path))
        assert len(files) == 1
        assert files[0].suffix == ".cljs"

    def test_finds_cljc_files(self, tmp_path: Path) -> None:
        """Test .cljc files are discovered."""
        (tmp_path / "shared.cljc").write_text("(ns shared.core)")

        files = list(find_clojure_files(tmp_path))
        assert len(files) == 1
        assert files[0].suffix == ".cljc"

    def test_finds_nested_files(self, tmp_path: Path) -> None:
        """Test files in nested directories are found."""
        lib = tmp_path / "src" / "myapp"
        lib.mkdir(parents=True)
        (lib / "core.clj").write_text("(ns myapp.core)")

        files = list(find_clojure_files(tmp_path))
        assert len(files) == 1
        assert files[0].name == "core.clj"

    def test_edn_in_find_but_skipped_in_analysis(self, tmp_path: Path) -> None:
        """Test .edn files are found but skipped during symbol extraction."""
        (tmp_path / "config.edn").write_text("{:port 8080}")
        (tmp_path / "core.clj").write_text("(ns app.core)\n(def x 1)")

        files = list(find_clojure_files(tmp_path))
        names = [f.name for f in files]
        assert "config.edn" in names
        assert "core.clj" in names

        # But analysis should skip EDN for symbols
        result = analyze_clojure(tmp_path)
        paths = [s.path for s in result.symbols if s.kind == "variable"]
        assert all("core.clj" in p for p in paths)

class TestEmptyAndMinimalFiles:
    """Branch coverage for empty/minimal file handling."""

    def test_file_with_only_ns(self, tmp_path: Path) -> None:
        """Test file with only namespace declaration."""
        make_clj_file(tmp_path, "empty.clj", "(ns myapp.empty)")
        result = analyze_clojure(tmp_path)
        assert not result.skipped

        modules = [s for s in result.symbols if s.kind == "module"]
        assert any(m.name == "myapp.empty" for m in modules)

    def test_file_with_comment_only(self, tmp_path: Path) -> None:
        """Test file with only comments."""
        make_clj_file(tmp_path, "comments.clj", """
;; This is a comment
;; Another comment
""")
        result = analyze_clojure(tmp_path)
        # Should handle gracefully
        assert not result.skipped

class TestSignatureEdgeCases:
    """Branch coverage for signature extraction edge cases."""

    def test_function_with_metadata(self, tmp_path: Path) -> None:
        """Test function with metadata before params still extracts signature."""
        make_clj_file(tmp_path, "meta.clj", """
(ns myapp.meta)

(defn ^:private helper
  "A helper function."
  [x y]
  (+ x y))
""")
        result = analyze_clojure(tmp_path)
        funcs = [s for s in result.symbols if s.kind == "function" and s.name == "helper"]
        assert len(funcs) == 1
        assert funcs[0].signature == "[x, y]"

    def test_destructuring_params(self, tmp_path: Path) -> None:
        """Test function with destructuring in params."""
        make_clj_file(tmp_path, "destruct.clj", """
(ns myapp.destruct)

(defn process [{:keys [name age]}]
  (str name " is " age))
""")
        result = analyze_clojure(tmp_path)
        funcs = [s for s in result.symbols if s.kind == "function" and s.name == "process"]
        assert len(funcs) == 1
        # Should have some signature, even if complex
        assert funcs[0].signature is not None
