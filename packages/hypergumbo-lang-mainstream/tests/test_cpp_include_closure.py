# SPDX-License-Identifier: AGPL-3.0-or-later
"""C++'s include stamp names every header the unit can see (INV-zikul).

cpp.py stamps EVERY unresolved call with the file's include set (WI-rupik),
not only the ambiguous names C stamps, so the defect c.py had was wider
here. Live on the shipped CLI before this change:

* pq2: only ``#include "net.h"`` (net.h includes ``<sys/socket.h>``), then
  ``send()``. Placeholder slot; NO-NET-SEND CONFIRMED.
* pq4: ``#include <cstdint>`` + ``#include "util.h"`` (util.h includes
  ``<cstdio>``), then ``fopen(p, "w")``. Slot ``cstdint``; the module filter
  refused the stdio row, cstdint is granted, and NO-FSWRITE read CONFIRMED.
  ``fopen`` is not an ambiguous name, so C never had this half.
"""

from __future__ import annotations

import contextlib
import io
import json
from pathlib import Path

import pytest

from hypergumbo_core.cli import main
from hypergumbo_lang_mainstream.cpp import analyze_cpp

_SEND = 'int run(int fd) { return (int)send(fd, "x", 1, 0); }\n'
_FOPEN = 'void save(const char *p) { FILE *f = fopen(p, "w"); fputs("x", f); }\n'


def _repo(tmp_path: Path, files: dict[str, str]) -> Path:
    repo = tmp_path / "repo"
    for rel, text in files.items():
        (repo / rel).parent.mkdir(parents=True, exist_ok=True)
        (repo / rel).write_text(text)
    return repo


def _slots(repo: Path, name: str) -> list[list[str]]:
    out = []
    for edge in analyze_cpp(repo).edges:
        parts = edge.dst.split(":")
        if edge.edge_type == "calls" and len(parts) >= 4 and parts[3] == name:
            out.append(parts[1].split(","))
    return out


def test_pq2_a_quoted_header_and_what_it_includes(tmp_path: Path) -> None:
    repo = _repo(tmp_path, {
        "main.cpp": '#include "net.h"\n' + _SEND,
        "net.h": "#include <sys/socket.h>\n",
    })
    assert _slots(repo, "send") == [["net.h", "sys/socket.h"]]


def test_pq4_a_non_ambiguous_call_is_stamped_with_its_real_home(
    tmp_path: Path,
) -> None:
    repo = _repo(tmp_path, {
        "main.cpp": '#include <cstdint>\n#include "util.h"\n' + _FOPEN,
        "util.h": "#include <cstdio>\n",
    })
    assert _slots(repo, "fopen") == [["cstdint", "util.h", "cstdio"]]


def test_an_angled_header_found_in_the_repository(tmp_path: Path) -> None:
    repo = _repo(tmp_path, {
        "main.cpp": "#include <cerrno>\n#include <proj/net.hpp>\n" + _SEND,
        "inc/proj/net.hpp": "#include <sys/socket.h>\n",
    })
    assert _slots(repo, "send") == [["cerrno", "proj/net.hpp", "sys/socket.h"]]


def test_a_file_with_no_include_keeps_the_placeholder(tmp_path: Path) -> None:
    """The short-name fallback the sentinel enables is unchanged."""
    repo = _repo(tmp_path, {"main.cpp": _FOPEN})
    assert _slots(repo, "fopen") == [["external"]]


def _verdicts(repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
              ) -> dict[str, str]:
    claims = tmp_path / "claims.yaml"
    claims.write_text(
        "claims:\n"
        "  - id: NO-NET-SEND\n    text: t\n    constraint:\n"
        "      boundary: net_send\n      must_not_exist: true\n"
        "  - id: NO-FSWRITE\n    text: t\n    constraint:\n"
        "      boundary: fs_write\n      must_not_exist: true\n"
    )
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(io.StringIO()):
        main(["verify-claims", str(repo), "--claims", str(claims),
              "--format", "json"])
    return {v["claim_id"]: v["verdict"]
            for v in json.loads(buf.getvalue())["verdicts"]}


def test_pq2_the_send_is_a_violation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = _repo(tmp_path, {
        "main.cpp": '#include "net.h"\n' + _SEND,
        "net.h": "#include <sys/socket.h>\n",
    })
    assert _verdicts(repo, tmp_path, monkeypatch)["NO-NET-SEND"] == "violated"


def test_pq4_the_write_is_a_violation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = _repo(tmp_path, {
        "main.cpp": '#include <cstdint>\n#include "util.h"\n' + _FOPEN,
        "util.h": "#include <cstdio>\n",
    })
    assert _verdicts(repo, tmp_path, monkeypatch)["NO-FSWRITE"] == "violated"


def test_a_transitive_in_repo_name_is_left_out_but_still_read(tmp_path: Path) -> None:
    """INV-valom's size bound. ``main.cpp`` includes the in-repo ``a.h``
    directly, which includes the in-repo ``b.h``, which includes ``<cstdio>``.
    ``b.h``'s NAME is left out (``a.h`` already stands for project code at the
    coverage gate, and no catalogue row names it), but ``b.h`` is READ, so
    ``cstdio`` -- the call's real home -- stays in the stamp."""
    repo = _repo(tmp_path, {
        "main.cpp": '#include <cstdint>\n#include "a.h"\n' + _FOPEN,
        "a.h": '#include "b.h"\n',
        "b.h": "#include <cstdio>\n",
    })
    assert _slots(repo, "fopen") == [["cstdint", "a.h", "cstdio"]]


def test_an_angled_in_repo_include_is_a_direct_in_repo_name(tmp_path: Path) -> None:
    """An angle include that resolves to a repository file is in-repo too:
    its own name stays (it is direct), the names it reaches go, and the
    system header it pulls in stays."""
    from hypergumbo_lang_mainstream.include_closure import IncludeClosure

    repo = _repo(tmp_path, {
        "src/main.cpp": "#include <vendor/top.h>\n" + _FOPEN,
        "inc/vendor/top.h": '#include "deep.h"\n',
        "inc/vendor/deep.h": "#include <cstdio>\n",
    })
    closure = IncludeClosure(repo)
    main_cpp = repo / "src/main.cpp"
    source = main_cpp.read_bytes()
    assert closure.stamp(main_cpp, source) == ["vendor/top.h", "deep.h", "cstdio"]
    assert closure.stamp(main_cpp, source, transitive_in_repo=False) == [
        "vendor/top.h", "cstdio"]


def test_a_file_whose_includes_are_all_system_keeps_every_name(tmp_path: Path) -> None:
    from hypergumbo_lang_mainstream.include_closure import IncludeClosure

    repo = _repo(tmp_path, {"main.cpp": "#include <cstdio>\n#include <vector>\n" + _FOPEN})
    main_cpp = repo / "main.cpp"
    assert IncludeClosure(repo).stamp(
        main_cpp, main_cpp.read_bytes(), transitive_in_repo=False,
    ) == ["cstdio", "vector"]


def test_a_direct_call_outside_analyze_reads_the_file_s_own_directory(
    tmp_path: Path,
) -> None:
    """Outside ``analyze()`` there is no run's include graph; the file's own
    directory stands in for the repository, so a quoted neighbour is still
    read."""
    from hypergumbo_core.ir import AnalysisRun
    from hypergumbo_core.symbol_resolution import NameResolver
    from hypergumbo_lang_mainstream.cpp import CppAnalyzer

    repo = _repo(tmp_path, {
        "main.cpp": '#include "util.h"\n' + _FOPEN,
        "util.h": "#include <cstdio>\n",
    })
    analyzer = CppAnalyzer()
    source = (repo / "main.cpp").read_bytes()
    edges = analyzer.extract_edges_from_file(
        analyzer._create_parser().parse(source), source, repo / "main.cpp",
        "main.cpp", {}, {}, AnalysisRun.create(pass_id="t", version="t"), {},
        NameResolver({}),
    )
    slots = [e.dst.split(":")[1].split(",") for e in edges
             if e.edge_type == "calls" and e.dst.split(":")[3] == "fopen"]
    assert slots == [["util.h", "cstdio"]]
