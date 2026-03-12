# SPDX-License-Identifier: AGPL-3.0-or-later
"""Branch coverage tests for commonlisp.py analyzer.

Tests specific branch paths in the Common Lisp analyzer that may not be covered
by the main test suite. Focuses on:
- Helper function behavior
- Defun, defmacro, defmethod extraction
- Defvar, defparameter, defconstant extraction
- Defclass and defpackage extraction
- Function call edge extraction
- Use-package edge extraction
- File discovery patterns
"""
from pathlib import Path

from hypergumbo_core.analyze.base import make_file_id, make_symbol_id
from hypergumbo_lang_common.commonlisp import analyze_commonlisp, find_commonlisp_files

def make_lisp_file(tmp_path: Path, name: str, content: str) -> None:
    """Create a Common Lisp file with given content."""
    (tmp_path / name).write_text(content)

class TestCommonLispHelperFunctions:
    """Branch coverage for helper functions."""

    def test_make_symbol_id_format(self) -> None:
        """Test symbol ID format."""
        symbol_id = make_symbol_id("commonlisp", "src/utils.lisp", 10, 15, "my-func", "function")
        assert symbol_id == "commonlisp:src/utils.lisp:10-15:my-func:function"

    def test_make_file_id_format(self) -> None:
        """Test file ID format."""
        file_id = make_file_id("commonlisp", "lib/core.lisp")
        assert file_id == "commonlisp:lib/core.lisp:1-1:file:file"

class TestDefunExtraction:
    """Branch coverage for defun extraction."""

    def test_simple_defun(self, tmp_path: Path) -> None:
        """Test simple defun extraction."""
        make_lisp_file(tmp_path, "funcs.lisp", """
(defun add (x y)
  (+ x y))
""")
        result = analyze_commonlisp(tmp_path)
        assert not result.skipped

        funcs = [s for s in result.symbols if s.kind == "function"]
        assert len(funcs) == 1
        assert funcs[0].name == "add"

    def test_multiple_defuns(self, tmp_path: Path) -> None:
        """Test multiple defun definitions."""
        make_lisp_file(tmp_path, "math.lisp", """
(defun add (x y) (+ x y))
(defun sub (x y) (- x y))
(defun mul (x y) (* x y))
""")
        result = analyze_commonlisp(tmp_path)
        funcs = [s for s in result.symbols if s.kind == "function"]
        names = [f.name for f in funcs]
        assert "add" in names
        assert "sub" in names
        assert "mul" in names

class TestDefmacroExtraction:
    """Branch coverage for defmacro extraction."""

    def test_defmacro(self, tmp_path: Path) -> None:
        """Test defmacro extraction."""
        make_lisp_file(tmp_path, "macros.lisp", """
(defmacro when-positive (x &body body)
  `(when (> ,x 0) ,@body))
""")
        result = analyze_commonlisp(tmp_path)
        macros = [s for s in result.symbols if s.kind == "macro"]
        assert len(macros) == 1
        assert macros[0].name == "when-positive"

class TestDefmethodExtraction:
    """Branch coverage for defmethod extraction."""

    def test_defmethod(self, tmp_path: Path) -> None:
        """Test defmethod extraction."""
        make_lisp_file(tmp_path, "methods.lisp", """
(defmethod process ((obj my-class))
  (format t "Processing ~a~%" obj))
""")
        result = analyze_commonlisp(tmp_path)
        methods = [s for s in result.symbols if s.kind == "method"]
        assert len(methods) == 1
        assert methods[0].name == "process"

class TestDefgenericExtraction:
    """Branch coverage for defgeneric extraction."""

    def test_defgeneric(self, tmp_path: Path) -> None:
        """Test defgeneric extraction."""
        make_lisp_file(tmp_path, "generics.lisp", """
(defgeneric compute (x)
  (:documentation "Generic compute function"))
""")
        result = analyze_commonlisp(tmp_path)
        generics = [s for s in result.symbols if s.kind == "generic"]
        assert len(generics) == 1
        assert generics[0].name == "compute"

class TestDefvarExtraction:
    """Branch coverage for defvar/defparameter/defconstant extraction."""

    def test_defvar(self, tmp_path: Path) -> None:
        """Test defvar extraction."""
        make_lisp_file(tmp_path, "vars.lisp", """
(defvar *config* nil)
""")
        result = analyze_commonlisp(tmp_path)
        vars = [s for s in result.symbols if s.kind == "variable"]
        assert len(vars) == 1
        assert vars[0].name == "*config*"

    def test_defparameter(self, tmp_path: Path) -> None:
        """Test defparameter extraction."""
        make_lisp_file(tmp_path, "params.lisp", """
(defparameter *debug* t)
""")
        result = analyze_commonlisp(tmp_path)
        vars = [s for s in result.symbols if s.kind == "variable"]
        assert len(vars) == 1
        assert vars[0].name == "*debug*"

    def test_defconstant(self, tmp_path: Path) -> None:
        """Test defconstant extraction."""
        make_lisp_file(tmp_path, "constants.lisp", """
(defconstant +pi+ 3.14159)
""")
        result = analyze_commonlisp(tmp_path)
        constants = [s for s in result.symbols if s.kind == "constant"]
        assert len(constants) == 1
        assert constants[0].name == "+pi+"

class TestDefclassExtraction:
    """Branch coverage for defclass extraction."""

    def test_defclass(self, tmp_path: Path) -> None:
        """Test defclass extraction."""
        make_lisp_file(tmp_path, "classes.lisp", """
(defclass person ()
  ((name :initarg :name :accessor person-name)
   (age :initarg :age :accessor person-age)))
""")
        result = analyze_commonlisp(tmp_path)
        classes = [s for s in result.symbols if s.kind == "class"]
        assert len(classes) == 1
        assert classes[0].name == "person"

class TestDefpackageExtraction:
    """Branch coverage for defpackage extraction."""

    def test_defpackage(self, tmp_path: Path) -> None:
        """Test defpackage extraction."""
        make_lisp_file(tmp_path, "package.lisp", """
(defpackage :myapp
  (:use :cl)
  (:export :main))
""")
        result = analyze_commonlisp(tmp_path)
        packages = [s for s in result.symbols if s.kind == "package"]
        assert len(packages) == 1
        assert packages[0].name == ":myapp"

class TestCallEdges:
    """Branch coverage for call edge extraction."""

    def test_internal_call(self, tmp_path: Path) -> None:
        """Test call to function in same file."""
        make_lisp_file(tmp_path, "app.lisp", """
(defun helper (x) (* x 2))
(defun main () (helper 21))
""")
        result = analyze_commonlisp(tmp_path)
        assert not result.skipped

        call_edges = [e for e in result.edges if e.edge_type == "calls"]
        helper_calls = [e for e in call_edges if "helper" in e.dst]
        assert len(helper_calls) >= 1

    def test_call_chain(self, tmp_path: Path) -> None:
        """Test chain of function calls."""
        make_lisp_file(tmp_path, "chain.lisp", """
(defun step1 (x) (+ x 1))
(defun step2 (x) (step1 (step1 x)))
(defun step3 (x) (step2 x))
""")
        result = analyze_commonlisp(tmp_path)
        call_edges = [e for e in result.edges if e.edge_type == "calls"]
        step1_calls = [e for e in call_edges if "step1" in e.dst]
        assert len(step1_calls) >= 2

class TestUsePackageEdges:
    """Branch coverage for use-package edge extraction."""

    def test_use_package(self, tmp_path: Path) -> None:
        """Test use-package creates import edge."""
        make_lisp_file(tmp_path, "uses.lisp", """
(use-package :alexandria)
""")
        result = analyze_commonlisp(tmp_path)
        import_edges = [e for e in result.edges if e.edge_type == "imports"]
        assert len(import_edges) >= 1

class TestFindCommonLispFiles:
    """Branch coverage for file discovery."""

    def test_finds_lisp_files(self, tmp_path: Path) -> None:
        """Test .lisp files are discovered."""
        (tmp_path / "test.lisp").write_text("(defun f () 1)")

        files = list(find_commonlisp_files(tmp_path))
        assert len(files) == 1
        assert files[0].suffix == ".lisp"

    def test_finds_lsp_files(self, tmp_path: Path) -> None:
        """Test .lsp files are discovered."""
        (tmp_path / "test.lsp").write_text("(defun f () 1)")

        files = list(find_commonlisp_files(tmp_path))
        assert len(files) == 1
        assert files[0].suffix == ".lsp"

    def test_finds_cl_files(self, tmp_path: Path) -> None:
        """Test .cl files are discovered."""
        (tmp_path / "test.cl").write_text("(defun f () 1)")

        files = list(find_commonlisp_files(tmp_path))
        assert len(files) == 1
        assert files[0].suffix == ".cl"

    def test_finds_asd_files(self, tmp_path: Path) -> None:
        """Test .asd (ASDF system) files are discovered."""
        (tmp_path / "test.asd").write_text("(defsystem :test)")

        files = list(find_commonlisp_files(tmp_path))
        assert len(files) == 1
        assert files[0].suffix == ".asd"

    def test_finds_nested_files(self, tmp_path: Path) -> None:
        """Test files in nested directories are found."""
        lib = tmp_path / "lib" / "utils"
        lib.mkdir(parents=True)
        (lib / "core.lisp").write_text("(defun core () 1)")

        files = list(find_commonlisp_files(tmp_path))
        assert len(files) == 1
        assert files[0].name == "core.lisp"

class TestEmptyAndMinimalFiles:
    """Branch coverage for empty/minimal file handling."""

    def test_empty_file(self, tmp_path: Path) -> None:
        """Test empty Common Lisp file."""
        make_lisp_file(tmp_path, "empty.lisp", "")
        result = analyze_commonlisp(tmp_path)
        # Should handle gracefully
        assert not result.skipped

    def test_comment_only_file(self, tmp_path: Path) -> None:
        """Test file with only comments."""
        make_lisp_file(tmp_path, "comments.lisp", """
;;; This is a comment
;; Another comment
""")
        result = analyze_commonlisp(tmp_path)
        assert not result.skipped

    def test_no_lisp_files(self, tmp_path: Path) -> None:
        """Test directory with no Common Lisp files."""
        result = analyze_commonlisp(tmp_path)
        assert not result.skipped
        # Only file symbols, no code symbols
        code_symbols = [s for s in result.symbols if s.kind != "file"]
        assert len(code_symbols) == 0

class TestCrossFileResolution:
    """Branch coverage for cross-file symbol resolution."""

    def test_two_file_resolution(self, tmp_path: Path) -> None:
        """Test call resolution across two files."""
        make_lisp_file(tmp_path, "utils.lisp", """
(defun helper (x) (* x 2))
""")
        make_lisp_file(tmp_path, "main.lisp", """
(defun main () (helper 21))
""")
        result = analyze_commonlisp(tmp_path)
        assert not result.skipped

        funcs = [s for s in result.symbols if s.kind == "function"]
        names = [f.name for f in funcs]
        assert "helper" in names
        assert "main" in names

        # Call should be resolved
        call_edges = [e for e in result.edges if e.edge_type == "calls"]
        resolved = [e for e in call_edges if "unresolved" not in e.dst]
        assert len(resolved) >= 1

class TestDefstructExtraction:
    """Branch coverage for defstruct extraction."""

    def test_defstruct(self, tmp_path: Path) -> None:
        """Test defstruct extraction."""
        make_lisp_file(tmp_path, "structs.lisp", """
(defstruct point
  x
  y)
""")
        result = analyze_commonlisp(tmp_path)
        structs = [s for s in result.symbols if s.kind == "struct"]
        assert len(structs) == 1
        assert structs[0].name == "point"
