# SPDX-License-Identifier: AGPL-3.0-or-later
"""Every ``.h`` is analysed by exactly one of the C, C++ and ObjC analyzers.

WI-somod. ``find_objc_files`` took EVERY ``*.h`` in a repository, and the C or
C++ analyzer took them too, so each header was parsed by two grammars whatever
its dialect. About half the ``.h`` partial-parse rows WI-bulaz made visible were
one dialect's grammar failing on the other's file (AFNetworking: 29 C rows on
ObjC headers; crun: 8 ObjC rows on C headers, from which the ObjC analyzer
emitted nothing). WI-rizas had settled the same collision between C and C++,
with two rules that did not agree: C gave its headers away when the repo held
any ``.hpp``/``.hxx``, C++ took them only when it held a ``.cpp``/``.cc``/
``.cxx``, so a repo with ``.hpp`` and no C++ source left its ``.h`` files to
nobody but ObjC.

Each scenario checks the three analyzers' file finders together, so a header
that two of them claim, or none, fails.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from hypergumbo_lang_mainstream.c import find_c_files
from hypergumbo_lang_mainstream.cpp import find_cpp_files
from hypergumbo_lang_mainstream.header_owner import classify_dot_h_file
from hypergumbo_lang_mainstream.objc import find_objc_files

_OBJC_HEADER = "#import <Foundation/Foundation.h>\n@interface V : NSObject\n@end\n"
_PLAIN_HEADER = "#ifndef X_H\n#define X_H\nint x(void);\n#endif\n"
_OBJC_SOURCE = '#import "V.h"\n@implementation V\n@end\n'


def _write(root: Path, files: dict[str, str]) -> None:
    for rel, text in files.items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(text)


def _owners(root: Path) -> dict[str, list[str]]:
    """header -> the analyzers whose finder yields it."""
    finders = {"c": find_c_files, "cpp": find_cpp_files, "objc": find_objc_files}
    owners: dict[str, list[str]] = {}
    for lang, finder in finders.items():
        for path in finder(root):
            if path.suffix == ".h":
                owners.setdefault(path.relative_to(root).as_posix(), []).append(lang)
    return owners


def _all_headers(root: Path) -> set[str]:
    return {p.relative_to(root).as_posix() for p in root.rglob("*.h")}


@pytest.mark.parametrize(("files", "expected"), [
    pytest.param(
        {"a.c": "int x(void) { return 0; }\n", "a.h": _PLAIN_HEADER},
        {"a.h": ["c"]},
        id="c-only: the ObjC analyzer no longer takes C headers",
    ),
    pytest.param(
        {"V.m": _OBJC_SOURCE, "V.h": _OBJC_HEADER, "Macros.h": _PLAIN_HEADER},
        {"V.h": ["objc"], "Macros.h": ["c"]},
        id="objc-only: an unmarked header is C syntax, so C extracts it",
    ),
    pytest.param(
        {"x.c": "int x(void) { return 0; }\n", "x.h": _PLAIN_HEADER,
         "V.m": _OBJC_SOURCE, "V.h": _OBJC_HEADER},
        {"x.h": ["c"], "V.h": ["objc"]},
        id="c-and-objc: each header to its dialect",
    ),
    pytest.param(
        {"a.cpp": "int x() { return 0; }\n", "a.h": _PLAIN_HEADER,
         "Bridging-Header.h": '#import "a.h"\n'},
        {"a.h": ["cpp"], "Bridging-Header.h": ["objc"]},
        id="cpp-with-an-objc-bridging-header",
    ),
    pytest.param(
        {"lib.hpp": "namespace n { int x(); }\n", "a.h": _PLAIN_HEADER},
        {"a.h": ["cpp"]},
        id="hpp-only: the header C and C++ each left to the other",
    ),
])
def test_every_header_has_exactly_one_owner(
    tmp_path: Path, files: dict[str, str], expected: dict[str, list[str]],
) -> None:
    _write(tmp_path, files)
    owners = _owners(tmp_path)
    assert set(owners) == _all_headers(tmp_path)  # none is left to nobody
    assert owners == expected


@pytest.mark.parametrize("text", [
    "@interface A : NSObject\n@end\n",
    "@protocol P <NSObject>\n@end\n",
    "@class NSString;\n",
    "  @property (nonatomic) int n;\n",
    "#import <Foundation/Foundation.h>\n",
    '# import "x.h"\n',
    "#ifdef __OBJC__\n@class CAMetalLayer;\n#else\ntypedef void CAMetalLayer;\n#endif\n",
])
def test_an_objc_marker_makes_a_header_objc(tmp_path: Path, text: str) -> None:
    (tmp_path / "x.h").write_text(text)
    assert classify_dot_h_file(tmp_path / "x.h") == "objc"


@pytest.mark.parametrize("text", [
    _PLAIN_HEADER,
    "extern NSString * const RACChannelExamples;\n",  # ObjC types, C syntax
    "/* @interface in a comment */\n// #import <x>\nint y;\n",
    '#include <stdio.h>\n#pragma once\nconst char *s = "@interface";\n',
])
def test_c_syntax_is_not_objc(tmp_path: Path, text: str) -> None:
    (tmp_path / "x.h").write_text(text)
    assert classify_dot_h_file(tmp_path / "x.h") == "c_family"


def test_an_unreadable_header_is_c_family(tmp_path: Path) -> None:
    assert classify_dot_h_file(tmp_path / "missing.h") == "c_family"
