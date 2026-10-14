# SPDX-License-Identifier: AGPL-3.0-or-later
"""Which of the C, C++ and ObjC analyzers owns a ``.h`` file (WI-somod).

A ``.h`` is claimed by three grammars. Before this module each analyzer
decided on its own: ObjC took every ``*.h`` in the repository, C took them
unless the repo held any C++ file, and C++ took them only if it held a C++
SOURCE file. So every header was parsed twice, and each grammar's failure on
the other dialect's syntax was reported as a partial parse (WI-bulaz measured
about half the ``.h`` rows that way: AFNetworking's C analyzer failed on 29
ObjC headers; crun's ObjC analyzer on 8 C headers, from which it emitted
nothing). The two C/C++ rules also disagreed: a repo with ``.hpp`` files and
no C++ source left its ``.h`` files to neither.

How It Works
------------
One rule, applied by all three finders, so each header has exactly one owner:

1. A header with an ObjC marker at the start of a line is ObjC. The markers
   are syntax no C or C++ grammar parses: ``@interface``, ``@protocol``,
   ``@class``, ``@property``, ``@end``, ``@implementation``, ``@import``, and
   ``#import``. This includes the mixed header that guards ObjC behind
   ``#ifdef __OBJC__`` (``vulkan_metal.h``): only the ObjC grammar parses both
   halves.
2. Otherwise C++ owns it if the repo holds any C++ file (source or
   ``.hpp``/``.hxx``), else C.

An unmarked header goes to C or C++ even in a pure-ObjC project. It is C
syntax by definition, and the ObjC analyzer emits only ObjC constructs, so
giving it to ObjC would drop its declarations: measured, fmdb's
``fts3_tokenizer.h`` holds the ``sqlite3_tokenizer_module`` struct, which only
the C analyzer extracts.

Why content and not repository shape alone. Mixed repositories are common and
large: grpc, pytorch, tensorflow, whisper.cpp and virtualbox all carry ObjC
headers among thousands of C/C++ ones. A repo-level rule would hand one
dialect's headers to the other grammar wholesale. Measured on 27 local repos
with headers and an ObjC signal, every ObjC-repo header the markers miss is
macros or C-syntax declarations (either grammar parses them), and every
C-repo header they hit is a real ObjC or mixed header (Cocoa, Metal, iOS
bridging headers).

What a header's new owner does not extract. The ObjC analyzer emits ObjC
constructs only (classes, protocols, methods, properties), not C function
declarations or typedefs. On AFNetworking the C analyzer's 3 symbols from
ObjC headers go with it: 2 real C function declarations and 1 misparse (an
``NS_ENUM`` member read as a typedef).

The repository fact is read once per finder call with a short-circuiting
glob; the content read is the first 64 KB of the header. A header whose only
ObjC is past that point stays C or C++: whisper.cpp's ``miniaudio.h`` (3.9 MB
of C with an ObjC class near line 35,600) keeps its C to the C++ analyzer and
loses the ObjC analyzer's 7 methods from that class.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Literal

from hypergumbo_core.discovery import find_files

HeaderOwner = Literal["c", "cpp", "objc"]

_OBJC_MARKER = re.compile(
    rb"^[ \t]*(?:@(?:interface|protocol|class|property|end|implementation|import)\b"
    rb"|#[ \t]*import[ \t]*[<\"])",
    re.MULTILINE,
)

_READ_LIMIT = 65536

_CPP_FILES = ["*.cpp", "*.cc", "*.cxx", "*.hpp", "*.hxx"]


def classify_dot_h_file(path: Path) -> Literal["objc", "c_family"]:
    """``objc`` when the header carries ObjC-only syntax, else ``c_family``."""
    try:
        head = path.read_bytes()[:_READ_LIMIT]
    except OSError:
        return "c_family"
    return "objc" if _OBJC_MARKER.search(head) else "c_family"


class HeaderOwnership:
    """The per-repository fact the rule needs, read once."""

    def __init__(self, repo_root: Path) -> None:
        self.has_cpp = any(find_files(repo_root, _CPP_FILES))

    def owner(self, header: Path) -> HeaderOwner:
        if classify_dot_h_file(header) == "objc":
            return "objc"
        return "cpp" if self.has_cpp else "c"


def headers_owned_by(repo_root: Path, lang: HeaderOwner) -> list[Path]:
    """The ``.h`` files ``lang``'s analyzer should parse."""
    ownership = HeaderOwnership(repo_root)
    return [h for h in find_files(repo_root, ["*.h"]) if ownership.owner(h) == lang]

