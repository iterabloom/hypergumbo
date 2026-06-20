# SPDX-License-Identifier: AGPL-3.0-or-later
"""INV-loguk: CMake function()/macro() callables carry non-null CC + LOC.

CMake `function(...)`/`macro(...)` blocks are real callables whose bodies carry
control flow (`if`/`elseif`/`foreach`/`while`). Verified against the real
tree-sitter-cmake grammar via the node-type walker (`cmake` in
`BRANCH_NODE_TYPES`). Non-callable constructs (project/target/...) stay None.
"""
from pathlib import Path

from hypergumbo_lang_mainstream.cmake import analyze_cmake_files

_CALLABLE_KINDS = {"function", "macro"}

_FIXTURE = '''cmake_minimum_required(VERSION 3.10)
project(demo)

function(my_func arg)
  if(arg)
    message("a")
  elseif(NOT arg)
    message("b")
  else()
    message("c")
  endif()
  foreach(x ${ARGN})
    message(${x})
  endforeach()
  while(arg)
    set(arg OFF)
  endwhile()
endfunction()

macro(my_macro arg)
  if(arg)
    message("m")
  endif()
  foreach(x ${ARGN})
    message(${x})
  endforeach()
endmacro()

function(simple_func arg)
  message(${arg})
endfunction()
'''


def _callables_by_name(symbols: list) -> dict:
    return {s.name: s for s in symbols if s.kind in _CALLABLE_KINDS}


def test_cmake_callable_cyclomatic_complexity(tmp_path: Path) -> None:
    (tmp_path / "CMakeLists.txt").write_text(_FIXTURE)
    result = analyze_cmake_files(tmp_path)
    assert not result.skipped
    callables = _callables_by_name(result.symbols)
    # base 1 + if + elseif + foreach + while = 5 (else + closers excluded)
    assert callables["my_func"].cyclomatic_complexity == 5
    # base 1 + if + foreach = 3
    assert callables["my_macro"].cyclomatic_complexity == 3
    # no control flow -> base 1
    assert callables["simple_func"].cyclomatic_complexity == 1


def test_cmake_callables_have_non_null_loc(tmp_path: Path) -> None:
    (tmp_path / "CMakeLists.txt").write_text(_FIXTURE)
    result = analyze_cmake_files(tmp_path)
    for sym in result.symbols:
        if sym.kind in _CALLABLE_KINDS:
            assert sym.cyclomatic_complexity is not None
            assert sym.lines_of_code is not None


def test_cmake_non_callables_stay_null(tmp_path: Path) -> None:
    (tmp_path / "CMakeLists.txt").write_text(_FIXTURE)
    result = analyze_cmake_files(tmp_path)
    non_callables = [s for s in result.symbols if s.kind not in _CALLABLE_KINDS]
    assert non_callables  # project / target symbols present
    for sym in non_callables:
        assert sym.cyclomatic_complexity is None
        assert sym.lines_of_code is None
