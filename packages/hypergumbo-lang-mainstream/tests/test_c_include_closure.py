# SPDX-License-Identifier: AGPL-3.0-or-later
"""A C call's include stamp names every header it could have come from (INV-zikul).

The stamp on an unresolved ambiguous call (``send``, ``read``, ``connect``)
used to list only the file's own ``#include <...>`` lines. The coverage gate
asks whether EVERY header in the stamp was enumerated (INV-zimud), so a
header missing from the stamp turns an unclassified call into an examined
negative. Live on the shipped CLI, NO-NET-SEND (``net_send must_not_exist``):

* cq1: ``#include <errno.h>`` + ``#include "net.h"`` (net.h includes
  ``<sys/socket.h>``), then ``send()``. Stamp ``errno.h``. CONFIRMED, no
  caveat.
* cq2: only ``#include "net.h"``. Stamp ``external``, the placeholder the
  gate skips. CONFIRMED.
* cq3: ``<errno.h>`` + ``<proj/net.h>`` (in-repo, under ``inc/``).
  Inconclusive, withheld only because ``proj/net`` is unenumerable.

The real send is a violation in all three. The stamp now follows in-repo
headers transitively (``include_closure``).
"""

from __future__ import annotations

import contextlib
import io
import json
from pathlib import Path

import pytest

from hypergumbo_core.cli import main
from hypergumbo_core.module_sets import expand_module_slot
from hypergumbo_lang_mainstream import include_closure
from hypergumbo_lang_mainstream.c import analyze_c
from hypergumbo_lang_mainstream.include_closure import (
    IncludeClosure,
    include_directives,
)

_SEND = 'int run(int fd) { return (int)send(fd, "x", 1, 0); }\n'


def _repo(tmp_path: Path, files: dict[str, str]) -> Path:
    repo = tmp_path / "repo"
    for rel, text in files.items():
        (repo / rel).parent.mkdir(parents=True, exist_ok=True)
        (repo / rel).write_text(text)
    return repo


def _slots(repo: Path, name: str) -> list[list[str]]:
    """The module slot of every unresolved call to ``name``, split."""
    out = []
    for edge in analyze_c(repo).edges:
        parts = edge.dst.split(":")
        if edge.edge_type == "calls" and len(parts) >= 4 and parts[3] == name:
            out.append(expand_module_slot(parts[1]).split(","))
    return out


class TestTheStampFollowsProjectHeaders:
    def test_cq1_a_quoted_header_and_what_it_includes(self, tmp_path: Path) -> None:
        repo = _repo(tmp_path, {
            "main.c": '#include <errno.h>\n#include "net.h"\n' + _SEND,
            "net.h": "#include <sys/socket.h>\n",
        })
        assert _slots(repo, "send") == [["errno.h", "net.h", "sys/socket.h"]]

    def test_cq2_only_a_quoted_header_is_no_longer_the_placeholder(
        self, tmp_path: Path,
    ) -> None:
        repo = _repo(tmp_path, {
            "main.c": '#include "net.h"\n' + _SEND,
            "net.h": "#include <sys/socket.h>\n",
        })
        assert _slots(repo, "send") == [["net.h", "sys/socket.h"]]

    def test_cq3_an_angled_header_found_in_the_repository(
        self, tmp_path: Path,
    ) -> None:
        repo = _repo(tmp_path, {
            "main.c": "#include <errno.h>\n#include <proj/net.h>\n" + _SEND,
            "inc/proj/net.h": "#include <sys/socket.h>\n",
        })
        assert _slots(repo, "send") == [["errno.h", "proj/net.h", "sys/socket.h"]]

    def test_torsocks_two_levels_down(self, tmp_path: Path) -> None:
        """src/lib/sendto.c reaches <sys/socket.h> through <common/utils.h>
        -> "compat.h" (src/common/compat.h:249)."""
        repo = _repo(tmp_path, {
            "src/lib/sendto.c": (
                "#include <assert.h>\n#include <common/utils.h>\n" + _SEND
            ),
            "src/common/utils.h": '#include "compat.h"\n',
            "src/common/compat.h": "#include <sys/socket.h>\n",
        })
        assert _slots(repo, "send") == [
            ["assert.h", "common/utils.h", "compat.h", "sys/socket.h"],
        ]

    def test_a_non_ambiguous_name_still_keeps_the_placeholder(
        self, tmp_path: Path,
    ) -> None:
        """WI-lajus' restriction is unchanged: only ambiguous names are
        stamped, so fprintf keeps the short-name fallback."""
        repo = _repo(tmp_path, {
            "main.c": '#include "log.h"\nint f(void) { return fprintf(0, "x"); }\n',
            "log.h": "#include <stdio.h>\n",
        })
        assert _slots(repo, "fprintf") == [["external"]]

    def test_a_file_with_no_include_at_all_keeps_the_placeholder(
        self, tmp_path: Path,
    ) -> None:
        repo = _repo(tmp_path, {"main.c": _SEND})
        assert _slots(repo, "send") == [["external"]]

    def test_a_shim_elsewhere_does_not_capture_a_call_declared_transitively(
        self, tmp_path: Path,
    ) -> None:
        """WI-rimon asks the same header set: a header reached through a
        project header declares the call just as a direct include does."""
        repo = _repo(tmp_path, {
            "shim.c": "int send(int fd, const char *b, int n, int f) { return 0; }\n",
            "main.c": '#include "net.h"\n' + _SEND,
            "net.h": "#include <sys/socket.h>\n",
        })
        assert _slots(repo, "send") == [["net.h", "sys/socket.h"]]


class TestTheWalk:
    def test_directives_are_read_as_the_preprocessor_reads_them(self) -> None:
        source = (
            b'#include <a.h>\n  #  include   "b/c.h"\n#include_next <d.h>\n'
            b"#include MACRO_HEADER\n#include <>\n"
            b"int x; // #include <not_a_directive.h>\n"
            b"#if 0\n#include <e.h>\n#endif\n"
        )
        assert include_directives(source) == [
            ("a.h", False), ("b/c.h", True), ("d.h", False), ("e.h", False),
        ]

    def test_a_cycle_terminates_and_names_both(self, tmp_path: Path) -> None:
        repo = _repo(tmp_path, {
            "a.h": '#include "b.h"\n#include <sys/socket.h>\n',
            "b.h": '#include "a.h"\n#include <unistd.h>\n',
            "main.c": '#include "a.h"\n#include "a.h"\n',
        })
        main_c = repo / "main.c"
        assert IncludeClosure(repo).stamp(main_c, main_c.read_bytes()) == [
            "a.h", "b.h", "sys/socket.h", "unistd.h",
        ]

    def test_a_header_including_the_analysed_file_does_not_loop(
        self, tmp_path: Path,
    ) -> None:
        repo = _repo(tmp_path, {
            "x.h": '#include "x.h"\n#include "y.h"\n',
            "y.h": '#include "x.h"\n',
        })
        x_h = repo / "x.h"
        assert IncludeClosure(repo).stamp(x_h, x_h.read_bytes()) == ["x.h", "y.h"]

    def test_an_unfound_header_is_named_not_dropped(self, tmp_path: Path) -> None:
        """ABSENT IS NOT EMPTY: a generated config.h is not in the tree, so
        its includes are unknown. Its name stays, and no grant can enumerate
        a project header, so the gate withholds."""
        repo = _repo(tmp_path, {"main.c": '#include "config.h"\n'})
        main_c = repo / "main.c"
        assert IncludeClosure(repo).stamp(main_c, main_c.read_bytes()) == [
            "config.h",
        ]

    def test_an_unreadable_header_is_named_not_dropped(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        repo = _repo(tmp_path, {
            "main.c": '#include "net.h"\n',
            "net.h": "#include <sys/socket.h>\n",
        })
        real = Path.read_bytes

        def read_bytes(self: Path) -> bytes:
            if self.name == "net.h":
                raise PermissionError(self)
            return real(self)

        monkeypatch.setattr(Path, "read_bytes", read_bytes)
        main_c = repo / "main.c"
        assert IncludeClosure(repo).stamp(main_c, real(main_c)) == ["net.h"]

    def test_the_depth_bound_names_the_header_it_stops_at(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(include_closure, "MAX_INCLUDE_DEPTH", 2)
        repo = _repo(tmp_path, {
            "main.c": '#include "a.h"\n',
            "a.h": '#include "b.h"\n',
            "b.h": '#include "c.h"\n',
            "c.h": "#include <sys/socket.h>\n",
        })
        main_c = repo / "main.c"
        assert IncludeClosure(repo).stamp(main_c, main_c.read_bytes()) == [
            "a.h", "b.h", "c.h",
        ]

    def test_a_quoted_include_beside_the_file_wins(self, tmp_path: Path) -> None:
        repo = _repo(tmp_path, {
            "src/main.c": '#include "net.h"\n',
            "src/net.h": "#include <sys/socket.h>\n",
            "other/net.h": "#include <winsock2.h>\n",
        })
        main_c = repo / "src/main.c"
        assert IncludeClosure(repo).stamp(main_c, main_c.read_bytes()) == [
            "net.h", "sys/socket.h",
        ]

    def test_every_candidate_is_read_when_the_include_path_is_unknown(
        self, tmp_path: Path,
    ) -> None:
        """The union over-approximates, and every error it makes ADDS a
        header: a home is never removed."""
        repo = _repo(tmp_path, {
            "src/main.c": "#include <proj/net.h>\n",
            "posix/proj/net.h": "#include <sys/socket.h>\n",
            "win/proj/net.h": "#include <winsock2.h>\n",
            "win/xproj/net.h": "#include <not_at_a_component_boundary.h>\n",
        })
        main_c = repo / "src/main.c"
        stamp = IncludeClosure(repo).stamp(main_c, main_c.read_bytes())
        assert stamp[0] == "proj/net.h"
        assert sorted(stamp[1:]) == ["sys/socket.h", "winsock2.h"]

    def test_an_include_climbing_out_of_the_repository_is_not_read(
        self, tmp_path: Path,
    ) -> None:
        (tmp_path / "outside.h").write_text("#include <sys/socket.h>\n")
        repo = _repo(tmp_path, {"main.c": '#include "../outside.h"\n'})
        main_c = repo / "main.c"
        assert IncludeClosure(repo).stamp(main_c, main_c.read_bytes()) == [
            "../outside.h",
        ]

    def test_a_relative_climb_inside_the_repository_is_read(
        self, tmp_path: Path,
    ) -> None:
        repo = _repo(tmp_path, {
            "src/lib/main.c": '#include "../common/net.h"\n#include "./"\n',
            "src/common/net.h": "#include <sys/socket.h>\n",
        })
        main_c = repo / "src/lib/main.c"
        assert IncludeClosure(repo).stamp(main_c, main_c.read_bytes()) == [
            "../common/net.h", "./", "sys/socket.h",
        ]


_CLAIMS = (
    "claims:\n  - id: NO-NET-SEND\n    text: never sends\n    constraint:\n"
    "      boundary: net_send\n      must_not_exist: true\n"
)


def _verdict(repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> str:
    claims = tmp_path / "claims.yaml"
    claims.write_text(_CLAIMS)
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(io.StringIO()):
        main(["verify-claims", str(repo), "--claims", str(claims),
              "--format", "json"])
    (verdict,) = json.loads(buf.getvalue())["verdicts"]
    return verdict["verdict"]


@pytest.mark.parametrize(("files"), [
    pytest.param({"main.c": '#include <errno.h>\n#include "net.h"\n' + _SEND,
                  "net.h": "#include <sys/socket.h>\n"}, id="cq1"),
    pytest.param({"main.c": '#include "net.h"\n' + _SEND,
                  "net.h": "#include <sys/socket.h>\n"}, id="cq2"),
    pytest.param({"main.c": "#include <errno.h>\n#include <proj/net.h>\n" + _SEND,
                  "inc/proj/net.h": "#include <sys/socket.h>\n"}, id="cq3"),
])
def test_a_real_send_reached_through_a_project_header_is_a_violation(
    files: dict[str, str], tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The behaviour the item states, end to end on the CLI: the call is
    classified as net_send through the header that declares it."""
    repo = _repo(tmp_path, files)
    assert _verdict(repo, tmp_path, monkeypatch) == "violated"
