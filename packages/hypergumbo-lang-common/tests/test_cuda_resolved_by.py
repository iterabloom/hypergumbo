# SPDX-License-Identifier: AGPL-3.0-or-later
"""``resolved_by`` on CUDA's call edges (audit 0030, WI-hirol).

The one site binds a call (and a kernel launch) through the run-wide
resolver with no hint: the lookup's own value, ``name``. A miss is the
unresolved stand-in and carries no mark.
"""
from pathlib import Path

from hypergumbo_lang_common.cuda import analyze_cuda_files


def test_resolver_route_is_name_and_miss_is_unmarked(tmp_path: Path) -> None:
    (tmp_path / "k.cu").write_text(
        "__device__ float helper(float x) { return x; }\n"
        "__global__ void kern(float* a) { a[0] = helper(a[0]); }\n"
        "void host(float* a) { kern<<<1, 1>>>(a); cudaDeviceSynchronize(); }\n"
    )
    result = analyze_cuda_files(tmp_path)
    by_dst = {
        e.dst.split(":")[-2]: e for e in result.edges
        if e.edge_type == "calls"
    }
    assert by_dst["helper"].meta["resolved_by"] == "name"
    assert by_dst["kern"].meta["resolved_by"] == "name"
    unresolved = [e for e in result.edges if e.edge_type == "calls" and not e.is_resolved]
    assert unresolved
    assert all("resolved_by" not in (e.meta or {}) for e in unresolved)
