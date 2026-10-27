# SPDX-License-Identifier: AGPL-3.0-or-later
"""The same medium gets the same boundary in every catalogue (ADR-0050 §1).

ADR-0050's axiom: a row's primary boundary names the MEDIUM data crosses at the
call site, and in which direction. Whether one row satisfies it is a judgment
made from the language's own documentation, one row at a time. A consequence
of it can be checked mechanically: two primitives whose documentation says
they cross the SAME medium in the same direction must declare the same
boundary, whichever catalogue they are in. ``docs/surveys/axiom-falsifiability.md``
records this as the partial check for the judgment axiom (WI-ziruv).

The family map below IS the judgment, written down once per family so that it
can be reviewed. Each family names a medium and direction, the boundary every
member must carry, and the members. A member is a primitive whose
documentation says it crosses that medium at the call, always or with its
default arguments. A member may declare MORE boundaries than its family's: a
call-site-undecidable arm (erlang ``io.get_line`` may be handed a file
device), or a simultaneous second reading, which is WI-kuzoh's question, not
this one. It may not lack the family's boundary.

NOT MEMBERS, and why:

* Primitives whose medium is fixed by a REQUIRED argument or receiver the
  catalogue cannot see: ``unistd.write``, ``fprintf``,
  ``java.io.PrintStream.println``. Their medium is not a fact of the primitive.
* Rows kept on a boundary for a measured reason other than the medium. Go
  ``exec.Command`` and rust ``Command::new`` only build a process description
  and stay ``subprocess`` under INV-dukam / ADR-0049 ruling 3.

Every member must be present in its catalogue, so a renamed or deleted row
fails here instead of passing vacuously. A member that disagrees with its
family today is a strict xfail naming the item that owns it; when that item's
fix lands the xfail XPASSes and the mark comes off.
"""
# covers: packages/hypergumbo-core/src/hypergumbo_core/io_boundary.py
from __future__ import annotations

from functools import lru_cache

import pytest

from hypergumbo_core.io_boundary import IoBoundaryCatalog, load_catalog

#: family -> (boundary every member carries, members as (language, qualified name)).
MEDIUM_FAMILIES: dict[str, tuple[str, tuple[tuple[str, str], ...]]] = {
    "read the process environment": ("env_read", (
        ("c", "stdlib.getenv"), ("elixir", "System.get_env"), ("erlang", "os.getenv"),
        ("go", "os.Getenv"), ("go", "os.LookupEnv"), ("haskell", "System.Environment.getEnv"),
        ("java", "java.lang.System.getenv"), ("javascript", "process.env"),
        ("kotlin", "java.lang.System.getenv"), ("objc", "NSProcessInfo.environment"),
        ("python", "os.getenv"), ("rust", "std::env.var"), ("scala", "java.lang.System.getenv"),
    )),
    "read a whole file": ("fs_read", (
        ("elixir", "File.read"), ("erlang", "file.read_file"), ("go", "os.ReadFile"),
        ("haskell", "Prelude.readFile"), ("java", "java.nio.file.Files.readAllBytes"),
        ("java", "java.nio.file.Files.readString"), ("javascript", "fs.readFileSync"),
        ("python", "pathlib.Path.read_text"), ("rust", "std::fs.read_to_string"),
    )),
    "delete a file": ("fs_write", (
        ("c", "unistd.unlink"), ("elixir", "File.rm"), ("erlang", "file.delete"),
        ("go", "os.Remove"), ("haskell", "System.Directory.removeFile"),
        ("java", "java.nio.file.Files.delete"), ("javascript", "fs.unlinkSync"),
        ("objc", "NSFileManager.removeItemAtPath:error:"), ("python", "os.remove"),
        ("rust", "std::fs.remove_file"),
    )),
    "read the host's name": ("host_info_read", (
        ("c", "unistd.gethostname"), ("erlang", "inet.gethostname"), ("go", "os.Hostname"),
        ("javascript", "os.hostname"), ("objc", "NSProcessInfo.hostName"),
        ("python", "socket.gethostname"),
    )),
    "read the host's clock": ("host_info_read", (
        ("c", "time.time"), ("elixir", "DateTime.utc_now"), ("erlang", "erlang.system_time"),
        ("go", "time.Now"), ("haskell", "Data.Time.Clock.getCurrentTime"),
        ("java", "java.lang.System.currentTimeMillis"), ("javascript", "Date.now"),
        ("python", "time.time"), ("rust", "std::time::SystemTime.now"), ("swift", "Date.now"),
    )),
    # WI-dozul ruled a DNS lookup net_recv (2026-09-06).
    "resolve a host name": ("net_recv", (
        ("c", "netdb.getaddrinfo"), ("erlang", "inet.getaddr"), ("go", "net.LookupHost"),
        ("javascript", "dns.lookup"), ("python", "socket.getaddrinfo"),
        ("rust", "std::net::ToSocketAddrs.to_socket_addrs"),
    )),
    "connect to a network peer": ("net_send", (
        ("c", "sys/socket.connect"), ("erlang", "gen_tcp.connect"), ("go", "net.Dial"),
        ("java", "java.net.Socket.connect"), ("javascript", "net.Socket.connect"),
        ("python", "socket.create_connection"), ("rust", "std::net::TcpStream.connect"),
    )),
    "accept a network peer": ("net_recv", (
        ("c", "sys/socket.accept"), ("erlang", "gen_tcp.accept"), ("go", "net.Listener.Accept"),
        ("java", "java.net.ServerSocket.accept"), ("python", "socket.socket.accept"),
        ("rust", "std::net::TcpListener.accept"),
    )),
    # ADR-0049 moved server launches to net_listen in four languages.
    "launch a network server": ("net_listen", (
        ("erlang", "httpd.start"), ("go", "net.Listen"), ("go", "net/http.ListenAndServe"),
        ("javascript", "Deno.listen"), ("javascript", "http.createServer"),
        ("javascript", "net.createServer"), ("python", "asyncio.start_server"),
        ("python", "socket.create_server"), ("swift", "NWListener.NWListener"),
    )),
    # audit-findings 0021: the process's own stdout/stderr is logging.
    "write the process's own standard output": ("logging", (
        ("c", "stdio.printf"), ("c", "stdio.putchar"), ("elixir", "IO.inspect"),
        ("elixir", "IO.puts"), ("erlang", "io.format"), ("go", "fmt.Println"),
        ("haskell", "Prelude.putStrLn"), ("javascript", "console.log"),
        ("python", "builtins.print"), ("rust", "std::io.stdout"),
        ("scala", "scala.Predef.println"), ("swift", "Swift.print"),
    )),
    "read the process's own standard input": ("ipc_recv", (
        ("c", "stdio.getchar"), ("elixir", "IO.gets"), ("elixir", "IO.read"),
        ("elixir", "io.get_line"), ("erlang", "io.get_line"), ("go", "fmt.Scanln"),
        ("haskell", "Prelude.getLine"), ("python", "builtins.input"),
        ("python", "sys.stdin.readline"), ("rust", "std::io.stdin"),
        ("scala", "scala.io.StdIn.readLine"),
    )),
}

#: Members that disagree with their family today, and the item that owns each.
KNOWN_DISAGREEMENTS: dict[tuple[str, str], str] = {
    ("c", "stdio.printf"): "WI-sofam: printf is rowed fs_write",
    ("c", "stdio.putchar"): "WI-sofam: putchar is rowed fs_write",
    ("elixir", "IO.inspect"): "WI-jofud: IO.inspect is rowed fs_read",
    ("elixir", "IO.gets"): "WI-jofud: IO.gets has no ipc_recv arm",
    ("elixir", "IO.read"): "WI-jofud: IO.read has no ipc_recv arm",
    ("javascript", "Deno.listen"): "WI-vogos: JavaScript server launches are still net_recv",
    ("javascript", "http.createServer"): "WI-vogos: JavaScript server launches are still net_recv",
    ("javascript", "net.createServer"): "WI-vogos: JavaScript server launches are still net_recv",
}


@lru_cache(maxsize=None)
def _catalog(language: str) -> IoBoundaryCatalog:
    """The shipped built-in catalogue, without community overlays."""
    return load_catalog(language, include_defaults=False)


def medium_violations(boundary: str, declared: set[str]) -> list[str]:
    """Why a member's declared boundaries break its family, if they do."""
    if not declared:
        return ["not rowed"]
    if boundary not in declared:
        return [f"declares {sorted(declared)}, not {boundary}"]
    return []


def _members() -> list[object]:
    params = []
    for family, (boundary, members) in MEDIUM_FAMILIES.items():
        for language, qualified in members:
            reason = KNOWN_DISAGREEMENTS.get((language, qualified))
            marks = [pytest.mark.xfail(strict=True, reason=reason)] if reason else []
            params.append(pytest.param(family, boundary, language, qualified, marks=marks,
                                       id=f"{language}:{qualified}"))
    return params


class TestMediumChecker:
    def test_it_reports_a_member_on_another_boundary(self) -> None:
        assert medium_violations("logging", {"fs_write"}) == ["declares ['fs_write'], not logging"]

    def test_it_reports_a_member_that_is_not_rowed(self) -> None:
        assert medium_violations("logging", set()) == ["not rowed"]

    def test_it_accepts_an_extra_arm(self) -> None:
        assert medium_violations("ipc_recv", {"fs_read", "ipc_recv"}) == []


def test_every_family_spans_more_than_one_catalogue() -> None:
    """A family inside one catalogue tests nothing across catalogues."""
    for family, (_, members) in MEDIUM_FAMILIES.items():
        assert len({language for language, _ in members}) >= 5, family


def test_every_known_disagreement_is_a_member() -> None:
    members = {m for _, ms in MEDIUM_FAMILIES.values() for m in ms}
    assert set(KNOWN_DISAGREEMENTS) <= members


@pytest.mark.parametrize(("family", "boundary", "language", "qualified"), _members())
def test_the_same_medium_gets_the_same_boundary(family: str, boundary: str, language: str, qualified: str) -> None:
    declared = _catalog(language).all_boundaries_for(qualified)
    assert medium_violations(boundary, declared) == [], f"{family}: {language} {qualified}"
