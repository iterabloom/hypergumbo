"""Tests for Common Lisp language analyzer.

Common Lisp is a multi-paradigm Lisp dialect with strong metaprogramming.
Key constructs: defun (functions), defpackage (packages), defvar/defparameter (vars),
defmacro (macros), defclass (CLOS classes), defmethod (methods).

Test strategy:
- Basic function detection (defun)
- Package detection
- Variable definitions (defvar, defparameter, defconstant)
- Macro detection
- Class and method detection
- Function calls
"""

from pathlib import Path
from unittest.mock import patch

import pytest

from hypergumbo_lang_common import commonlisp as commonlisp_module
from hypergumbo_lang_common.commonlisp import analyze_commonlisp


def make_lisp_file(tmp: Path, name: str, content: str) -> Path:
    """Create a .lisp file for testing."""
    f = tmp / name
    f.write_text(content, encoding="utf-8")
    return f


class TestCommonLispAnalyzer:
    """Test Common Lisp symbol and edge detection."""

    def test_detects_defun(self, tmp_path: Path) -> None:
        """Detect function definitions with defun."""
        make_lisp_file(
            tmp_path,
            "core.lisp",
            """
(defpackage :myapp
  (:use :cl))

(defun hello (name)
  "Greets a user."
  (format t "Hello, ~A" name))

(defun add (a b)
  (+ a b))
""",
        )
        result = analyze_commonlisp(tmp_path)
        assert not result.skipped

        names = [s.name for s in result.symbols]
        assert "hello" in names
        assert "add" in names

        hello = next(s for s in result.symbols if s.name == "hello")
        assert hello.kind == "function"
        assert hello.language == "commonlisp"

    def test_detects_package(self, tmp_path: Path) -> None:
        """Detect package declarations."""
        make_lisp_file(
            tmp_path,
            "core.lisp",
            """
(defpackage :myapp.core
  (:use :cl)
  (:export :main :process))

(in-package :myapp.core)

(defun process (s)
  (string-trim s))
""",
        )
        result = analyze_commonlisp(tmp_path)
        assert not result.skipped

        # Package should be detected
        names = [s.name for s in result.symbols]
        assert ":myapp.core" in names

        pkg_sym = next(s for s in result.symbols if s.name == ":myapp.core")
        assert pkg_sym.kind == "package"

    def test_detects_defvar(self, tmp_path: Path) -> None:
        """Detect variable definitions with defvar."""
        make_lisp_file(
            tmp_path,
            "config.lisp",
            """
(defvar *config-file* "config.lisp")

(defvar *default-port* 8080)
""",
        )
        result = analyze_commonlisp(tmp_path)
        assert not result.skipped

        names = [s.name for s in result.symbols]
        assert "*config-file*" in names
        assert "*default-port*" in names

        cfg = next(s for s in result.symbols if s.name == "*config-file*")
        assert cfg.kind == "variable"

    def test_detects_defparameter(self, tmp_path: Path) -> None:
        """Detect defparameter definitions."""
        make_lisp_file(
            tmp_path,
            "params.lisp",
            """
(defparameter *debug-mode* nil)
""",
        )
        result = analyze_commonlisp(tmp_path)
        assert not result.skipped

        names = [s.name for s in result.symbols]
        assert "*debug-mode*" in names

        param = next(s for s in result.symbols if s.name == "*debug-mode*")
        assert param.kind == "variable"

    def test_detects_defconstant(self, tmp_path: Path) -> None:
        """Detect constant definitions."""
        make_lisp_file(
            tmp_path,
            "constants.lisp",
            """
(defconstant +pi+ 3.14159)
(defconstant +e+ 2.71828)
""",
        )
        result = analyze_commonlisp(tmp_path)
        assert not result.skipped

        names = [s.name for s in result.symbols]
        assert "+pi+" in names
        assert "+e+" in names

        pi = next(s for s in result.symbols if s.name == "+pi+")
        assert pi.kind == "constant"

    def test_detects_defmacro(self, tmp_path: Path) -> None:
        """Detect macro definitions."""
        make_lisp_file(
            tmp_path,
            "macros.lisp",
            """
(defmacro unless (pred &body body)
  `(if (not ,pred)
     (progn ,@body)))
""",
        )
        result = analyze_commonlisp(tmp_path)
        assert not result.skipped

        names = [s.name for s in result.symbols]
        assert "unless" in names

        mac = next(s for s in result.symbols if s.name == "unless")
        assert mac.kind == "macro"

    def test_detects_defclass(self, tmp_path: Path) -> None:
        """Detect CLOS class definitions."""
        make_lisp_file(
            tmp_path,
            "classes.lisp",
            """
(defclass point ()
  ((x :initarg :x :accessor point-x)
   (y :initarg :y :accessor point-y)))

(defclass colored-point (point)
  ((color :initarg :color :accessor point-color)))
""",
        )
        result = analyze_commonlisp(tmp_path)
        assert not result.skipped

        names = [s.name for s in result.symbols]
        assert "point" in names
        assert "colored-point" in names

        point = next(s for s in result.symbols if s.name == "point")
        assert point.kind == "class"

    def test_detects_defmethod(self, tmp_path: Path) -> None:
        """Detect method definitions."""
        make_lisp_file(
            tmp_path,
            "methods.lisp",
            """
(defmethod distance ((p1 point) (p2 point))
  (sqrt (+ (expt (- (point-x p2) (point-x p1)) 2)
           (expt (- (point-y p2) (point-y p1)) 2))))
""",
        )
        result = analyze_commonlisp(tmp_path)
        assert not result.skipped

        names = [s.name for s in result.symbols]
        assert "distance" in names

        method = next(s for s in result.symbols if s.name == "distance")
        assert method.kind == "method"

    def test_detects_defgeneric(self, tmp_path: Path) -> None:
        """Detect generic function definitions."""
        make_lisp_file(
            tmp_path,
            "generic.lisp",
            """
(defgeneric area (shape)
  (:documentation "Calculate the area of a shape."))
""",
        )
        result = analyze_commonlisp(tmp_path)
        assert not result.skipped

        names = [s.name for s in result.symbols]
        assert "area" in names

        gen = next(s for s in result.symbols if s.name == "area")
        assert gen.kind == "generic"

    def test_detects_defstruct(self, tmp_path: Path) -> None:
        """Detect structure definitions."""
        make_lisp_file(
            tmp_path,
            "structs.lisp",
            """
(defstruct person
  name
  age
  email)
""",
        )
        result = analyze_commonlisp(tmp_path)
        assert not result.skipped

        names = [s.name for s in result.symbols]
        assert "person" in names

        struct = next(s for s in result.symbols if s.name == "person")
        assert struct.kind == "struct"

    def test_detects_function_calls(self, tmp_path: Path) -> None:
        """Detect function call edges."""
        make_lisp_file(
            tmp_path,
            "app.lisp",
            """
(defun helper (x)
  (* x 2))

(defun main ()
  (print (helper 21)))
""",
        )
        result = analyze_commonlisp(tmp_path)
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

    def test_detects_use_package_imports(self, tmp_path: Path) -> None:
        """Detect use-package statements as import edges."""
        make_lisp_file(
            tmp_path,
            "core.lisp",
            """
(use-package :mylib)
(use-package :alexandria)

(defun read-file (path)
  (uiop:read-file-string path))
""",
        )
        result = analyze_commonlisp(tmp_path)
        assert not result.skipped

        imports = [e for e in result.edges if e.edge_type == "imports"]
        imported_names = [e.dst for e in imports]

        # Should import mylib and alexandria
        assert any(":mylib" in dst for dst in imported_names)
        assert any(":alexandria" in dst for dst in imported_names)

    def test_handles_empty_file(self, tmp_path: Path) -> None:
        """Handle empty Common Lisp file gracefully."""
        make_lisp_file(tmp_path, "empty.lisp", "")
        result = analyze_commonlisp(tmp_path)
        assert not result.skipped
        # Empty file should produce no symbols

    def test_handles_syntax_error(self, tmp_path: Path) -> None:
        """Handle files with syntax errors gracefully."""
        make_lisp_file(tmp_path, "bad.lisp", "(defun foo (x")  # Unclosed
        result = analyze_commonlisp(tmp_path)
        # Should not crash, may skip or produce partial results
        assert result is not None

    def test_cross_file_calls(self, tmp_path: Path) -> None:
        """Detect calls across files via two-pass resolution."""
        make_lisp_file(
            tmp_path,
            "utils.lisp",
            """
(defun double (x)
  (* x 2))
""",
        )
        make_lisp_file(
            tmp_path,
            "core.lisp",
            """
(defun quadruple (x)
  (double (double x)))
""",
        )
        result = analyze_commonlisp(tmp_path)
        assert not result.skipped

        edges = result.edges
        call_edges = [e for e in edges if e.edge_type == "calls"]

        # quadruple should call double
        quad = next(s for s in result.symbols if s.name == "quadruple")
        dbl = next(s for s in result.symbols if s.name == "double")
        edge_pairs = [(e.src, e.dst) for e in call_edges]
        assert (quad.id, dbl.id) in edge_pairs

    def test_call_to_unresolved_function(self, tmp_path: Path) -> None:
        """Handle calls to functions we can't resolve (e.g., core library)."""
        make_lisp_file(
            tmp_path,
            "app.lisp",
            """
(defun process (x)
  (print x)
  (format t "~A" x))
""",
        )
        result = analyze_commonlisp(tmp_path)
        assert not result.skipped

        # Should have the process function
        names = [s.name for s in result.symbols]
        assert "process" in names

        # print and format are unresolved (stdlib) - no call edges to them

    def test_skipped_when_unavailable(self, tmp_path: Path) -> None:
        """Analysis skips gracefully when tree-sitter unavailable."""
        make_lisp_file(tmp_path, "core.lisp", "(defpackage :app)")

        with patch.object(
            commonlisp_module,
            "is_commonlisp_tree_sitter_available",
            return_value=False,
        ):
            with pytest.warns(UserWarning, match="Common Lisp analysis skipped"):
                result = commonlisp_module.analyze_commonlisp(tmp_path)

        assert result.skipped is True
        assert "tree-sitter-commonlisp" in result.skip_reason

    def test_multiple_file_extensions(self, tmp_path: Path) -> None:
        """Handle multiple Common Lisp file extensions."""
        # .lisp file
        make_lisp_file(tmp_path, "main.lisp", "(defun main () nil)")

        # .lsp file
        (tmp_path / "utils.lsp").write_text("(defun util () nil)")

        # .cl file
        (tmp_path / "helper.cl").write_text("(defun help () nil)")

        result = analyze_commonlisp(tmp_path)
        assert not result.skipped

        names = [s.name for s in result.symbols]
        assert "main" in names
        assert "util" in names
        assert "help" in names

    def test_asd_system_definition(self, tmp_path: Path) -> None:
        """Handle .asd ASDF system definition files."""
        (tmp_path / "myapp.asd").write_text("""
(defsystem "myapp"
  :version "1.0.0"
  :depends-on ("alexandria" "cl-ppcre"))
""")
        result = analyze_commonlisp(tmp_path)
        assert not result.skipped
        # Should be able to parse .asd files

    def test_case_insensitive_names(self, tmp_path: Path) -> None:
        """Common Lisp is case-insensitive - names should be lowercased."""
        make_lisp_file(
            tmp_path,
            "mixed.lisp",
            """
(defun MyFunction (X Y)
  (+ X Y))

(DEFUN ANOTHER-FUNC ()
  NIL)
""",
        )
        result = analyze_commonlisp(tmp_path)
        assert not result.skipped

        names = [s.name for s in result.symbols]
        # Names should be lowercase
        assert "myfunction" in names
        assert "another-func" in names

    def test_call_inside_uppercase_defun(self, tmp_path: Path) -> None:
        """Detect calls inside uppercase DEFUN functions."""
        make_lisp_file(
            tmp_path,
            "upper.lisp",
            """
(defun helper (x)
  (* x 2))

(DEFUN MAIN-FUNC ()
  (helper 10))
""",
        )
        result = analyze_commonlisp(tmp_path)
        assert not result.skipped

        # main-func should call helper
        edges = result.edges
        call_edges = [e for e in edges if e.edge_type == "calls"]
        assert len(call_edges) >= 1

        main_sym = next(s for s in result.symbols if s.name == "main-func")
        helper_sym = next(s for s in result.symbols if s.name == "helper")
        edge_pairs = [(e.src, e.dst) for e in call_edges]
        assert (main_sym.id, helper_sym.id) in edge_pairs


class TestCommonLispSignatureExtraction:
    """Tests for Common Lisp function signature extraction."""

    def test_simple_params(self, tmp_path: Path) -> None:
        """Extract signature from simple defun with params."""
        make_lisp_file(
            tmp_path,
            "core.lisp",
            "(defun add (x y) (+ x y))",
        )
        result = analyze_commonlisp(tmp_path)
        funcs = [s for s in result.symbols if s.kind == "function" and s.name == "add"]
        assert len(funcs) == 1
        assert funcs[0].signature is not None
        assert "x" in funcs[0].signature
        assert "y" in funcs[0].signature

    def test_no_params(self, tmp_path: Path) -> None:
        """Extract signature from defun with no params."""
        make_lisp_file(
            tmp_path,
            "core.lisp",
            "(defun no-args () 42)",
        )
        result = analyze_commonlisp(tmp_path)
        funcs = [s for s in result.symbols if s.kind == "function" and s.name == "no-args"]
        assert len(funcs) == 1
        assert funcs[0].signature is not None
        assert "()" in funcs[0].signature

    def test_optional_and_rest_params(self, tmp_path: Path) -> None:
        """Extract signature from defun with &optional and &rest params."""
        make_lisp_file(
            tmp_path,
            "core.lisp",
            "(defun variadic (first &optional second &rest more) nil)",
        )
        result = analyze_commonlisp(tmp_path)
        funcs = [s for s in result.symbols if s.kind == "function" and s.name == "variadic"]
        assert len(funcs) == 1
        sig = funcs[0].signature
        assert sig is not None
        assert "first" in sig

    def test_method_signature(self, tmp_path: Path) -> None:
        """Extract signature from defmethod with specialized params."""
        make_lisp_file(
            tmp_path,
            "methods.lisp",
            "(defmethod greet ((person string)) (format t \"Hello ~A\" person))",
        )
        result = analyze_commonlisp(tmp_path)
        methods = [s for s in result.symbols if s.kind == "method" and s.name == "greet"]
        assert len(methods) == 1
        sig = methods[0].signature
        assert sig is not None
