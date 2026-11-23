# SPDX-License-Identifier: AGPL-3.0-or-later
# covers: packages/hypergumbo-lang-common/src/hypergumbo_lang_common/cuda.py
"""A CUDA name's layout is not a stable-id input (``LAYOUT_FREE_NAME_LANGUAGES``).

CUDA is C++ with kernels, and its analyzer names a function by its
declarator as written: a qualified declarator split over lines
(``void V ::\\nrun()``) or an operator written with a space (``operator -``)
was another name. A function with typed parameters takes the typed tier,
which hashes every name layout-free; one with none takes the untyped tier,
which hashes its names as written unless the language is in core's
``LAYOUT_FREE_NAME_LANGUAGES``, as C++ is (WI-sidus).
"""
from __future__ import annotations

from pathlib import Path

from hypergumbo_lang_common.cuda import analyze_cuda_files

_ONE = """\
struct V { void run(); };
void V::run() {}
struct W { W operator-() const; bool operator!() const; };
W W::operator-() const { return *this; }
bool W::operator!() const { return false; }
namespace ns { void inner(); }
void ns::inner() {}
__global__ void kern(float *a, int n) {}
"""

_TWO = """\
struct V { void run(); };
void V ::
run() {}
struct W { W operator -() const; bool operator !() const; };
W W::operator -() const { return *this; }
bool W::operator !() const { return false; }
namespace ns { void inner(); }
void ns ::
  inner() {}
__global__ void kern(float* a,
                     int n) {}
"""


def _ids(tmp_path: Path, text: str) -> list[tuple[str, str | None]]:
    """Each function's (kind, stable id), in source order."""
    for old in tmp_path.iterdir():
        old.unlink()
    (tmp_path / "k.cu").write_text(text)
    syms = sorted(analyze_cuda_files(tmp_path).symbols, key=lambda s: s.span.start_line)
    return [(s.kind, s.stable_id) for s in syms if s.kind == "function"]


def test_a_cuda_name_in_another_layout_keeps_its_id(tmp_path: Path) -> None:
    one = _ids(tmp_path, _ONE)
    two = _ids(tmp_path, _TWO)
    assert len(one) == 5 and all(sid for _, sid in one)  # reach
    assert one == two


def test_another_cuda_operator_is_another_id(tmp_path: Path) -> None:
    minus = _ids(tmp_path, "struct W { W operator-() const; };\nW W::operator-() const { return *this; }\n")
    plus = _ids(tmp_path, "struct W { W operator+() const; };\nW W::operator+() const { return *this; }\n")
    assert minus and minus != plus
