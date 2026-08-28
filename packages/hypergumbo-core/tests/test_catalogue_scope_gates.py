# SPDX-License-Identifier: AGPL-3.0-or-later
"""One scope gate, every shipped I/O catalogue (ADR-0047 ruling 8, WI-surun).

WHY THIS FILE EXISTS. ADR-0016 §27 scopes the shipped ``io_primitives``
catalogues to a language's standard library, and ADR-0047 ruling 1 KEEPS that
rule for the catalogues while permitting unvouched community rows to ship
*alongside* them as disclosed overlays. Until this file, the rule was enforced
for **6 of 14 languages**, and ADR-0047 names that asymmetry as the mechanical
cause of the drift it exists to correct: the eight languages with no gate
(go, elixir, erlang, haskell, swift, objc, c, cpp) are exactly the ones where
third-party rows accumulated — 155 in elixir, 68 in haskell, 38 in swift, 33 in
go. A gate for one more language would have repeated the mistake at a smaller
scale, so the rule is asserted **once, over every catalogue the tree ships**.

THE TABLE IS THE CURATED LIST, AND THAT IS THE POINT. WI-surun records the
mechanical obstacle to this work: ``IoBoundaryCatalog.is_stdlib_module`` cannot
select the rows to cull, because only ``python.yaml`` declares
``stdlib_modules`` — the predicate returns 100% third-party for elixir, haskell,
swift and go, which is RECOGNITION rather than provenance (the INV-buzab
distinction) and here yields a number that is not imprecise but meaningless. The
selection therefore rests on a hand-curated classification which, before this
file, existed nowhere in the repository except as prose in a tracker item. That
is precisely the shape that lets the next stdlib climb re-create the drift
silently, so the curated list and the gate that enforces it are ONE ARTIFACT
here rather than two that can disagree.

AN ALLOWLIST, NOT A DENYLIST — the direction matters. Three of the six original
gates (java, kotlin, scala) loop over every primitive and assert a namespace
prefix; the other three (python, javascript, rust) name specific third-party
modules and assert their absence. Only the first shape is an invariant: a
denylist is a hardcoded inventory that a NEW third-party module sails straight
through, which is how ``golang.org/x/sys/execabs`` and ``grpc`` landed in a
catalogue whose header asserted stdlib-only. An allowlist fails closed — an
unrecognised module is a failure until a human classifies it — and it decays in
the safe direction, because a legitimate new stdlib module requires a
deliberate, reviewed edit to this table rather than silence.

THE LANGUAGE LIST IS DERIVED FROM THE SHIPPED TREE, not restated. A gate whose
own language list is hardcoded can be evaded by adding a catalogue and not
adding it to the list — which is live today: ``CATALOG_LANGUAGES`` in
``test_io_boundary.py`` names 14 languages and the tree ships 15, so
``bash.yaml`` sits outside both that tuple and the WI-sugav subprocess drift
guard it feeds. Here the parametrisation walks ``io_primitives/*.yaml``, so a
new catalogue is gated the moment it lands.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import pytest

import hypergumbo_core.io_boundary as _iob
from hypergumbo_core.io_boundary import load_catalog

_CATALOG_DIR = Path(_iob.__file__).parent / "io_primitives"

SHIPPED_LANGUAGES: tuple[str, ...] = tuple(
    sorted(p.stem for p in _CATALOG_DIR.glob("*.yaml"))
)


@dataclass(frozen=True)
class Scope:
    """The stdlib line for one language, as modules and/or namespace prefixes.

    ``why`` is mandatory and is not decoration: every entry here is a judgement
    about where a language's standard library ends, and the reasoning is the
    part a future reader needs in order to classify module 16 correctly.
    """

    why: str
    prefixes: tuple[str, ...] = ()
    modules: frozenset[str] = field(default_factory=frozenset)
    inherits: tuple[str, ...] = ()

    def admits(self, module: str, table: "dict[str, Scope]") -> bool:
        if module in self.modules:
            return True
        if any(module.startswith(p) for p in self.prefixes):
            return True
        return any(table[parent].admits(module, table) for parent in self.inherits)


# ---------------------------------------------------------------------------
# THE CURATED LIST (ADR-0047 ruling 8). One entry per shipped catalogue.
# ---------------------------------------------------------------------------

CATALOGUE_SCOPE: dict[str, Scope] = {
    "bash": Scope(
        why=(
            "Synthetic pseudo-modules describing bash's OWN syntax, not "
            "libraries: `redirect` is the `<`/`>`/`>>` operators, `env` is "
            "parameter expansion of an unassigned name, `shell` is "
            "bash-assigned variables. There is no third-party surface to "
            "admit or exclude — a bash 'library' is a sourced file, which "
            "this catalogue deliberately does not model (see the "
            "host_info_read note refusing date(1) as a pseudo-module)."
        ),
        modules=frozenset({"env", "redirect", "shell"}),
    ),
    "c": Scope(
        why=(
            "The C standard library plus POSIX, addressed by HEADER name as "
            "C source spells it (`stdio`, `unistd`, `sys/socket`). C has no "
            "namespace, so the line is an enumeration of headers rather than "
            "a prefix; anything outside it (glibc extensions aside) is a "
            "third-party library."
        ),
        modules=frozenset({
            "dirent", "spawn", "stdio", "stdlib", "sys/socket", "sys/stat",
            "sys/time", "sys/times", "sys/wait", "time", "unistd",
        }),
    ),
    "cpp": Scope(
        why=(
            "The C++ standard library (`std`, `std::chrono::*`) plus "
            "everything C admits, which the loader already merges via "
            "_CATALOG_PARENTS (cpp -> c)."
        ),
        prefixes=("std::", "std"),
        inherits=("c",),
    ),
    "elixir": Scope(
        why=(
            "Elixir's standard library — the capitalised modules shipped by "
            "the Elixir distribution itself — plus every OTP module, which "
            "the loader merges via _CATALOG_PARENTS (elixir -> erlang) and "
            "which the `erlang` entry below enumerates. `:httpc` is the "
            "Elixir spelling of the OTP inets client and is stdlib for the "
            "same reason `httpc` is. EXCLUDED, and this is the largest cull "
            "in the tree: Ecto, Phoenix, Plug, HTTPoison, Req, Tesla, Mint, "
            "Postgrex, MyXQL, Redix, Finch and Oban are all Hex packages. "
            "elixir.yaml's header justified them by a UAT report that a "
            "Phoenix/Ecto repository returned ZERO boundaries; ADR-0047 "
            "keeps that recall by SHIPPING them as a disclosed overlay "
            "rather than by asserting they are the standard library."
        ),
        modules=frozenset({
            "Application", "DateTime", "File", "GenServer", "IO", "Logger",
            "NaiveDateTime", "Path", "Port", "Process", "System", "Task",
            ":httpc",
        }),
        inherits=("erlang",),
    ),
    "erlang": Scope(
        why=(
            "OTP modules, from the kernel / stdlib / ssl / inets / mnesia "
            "applications that ship with every Erlang installation. Erlang "
            "module names are flat and unnamespaced, so the line is an "
            "enumeration; a Hex package's module would simply not be here."
        ),
        modules=frozenset({
            "application", "code", "dets", "erlang", "error_logger", "ets",
            "file", "filelib", "gen_event", "gen_sctp", "gen_server",
            "gen_statem", "gen_tcp", "gen_udp", "global", "httpc", "httpd",
            "inet", "init", "io", "io_lib", "logger", "mnesia", "os",
            "prim_file", "proc_lib", "rpc", "ssl", "supervisor",
        }),
    ),
    "go": Scope(
        why=(
            "The Go standard library, keyed by the package path as the "
            "catalogue spells it (`net/http`, `os/exec`), with a receiver "
            "type appended after a dot for method rows (`net/http.Client`). "
            "EXCLUDED: `golang.org/x/...` is maintained by the Go team but "
            "is NOT the standard library — it is a separately versioned "
            "module, which is the whole distinction — so `golang.org/x/sys/"
            "execabs` and its bare-identifier sibling `unix` go out, as does "
            "`grpc`."
        ),
        modules=frozenset({
            "bufio", "crypto/tls", "filepath", "fmt", "io", "io/ioutil",
            "log", "log/slog", "net", "net/http", "net/smtp", "os",
            "os/exec", "runtime", "syscall", "testing", "time",
            # Receiver-qualified method rows are listed in full rather than
            # derived by stripping a trailing capitalised segment. A rule
            # would silently admit `grpc.ClientConn`; an enumeration makes
            # every stdlib TYPE a reviewed entry too.
            "crypto/tls.Conn", "log/slog.Logger", "net.Conn", "net.Listener",
            "net/http.Client", "net/http.Transport", "net/smtp.Client",
            "os/exec.Cmd", "testing.B", "testing.T",
        }),
    ),
    "haskell": Scope(
        why=(
            "`base` plus the GHC BOOT LIBRARIES — the packages that ship "
            "with every GHC installation and are the closest thing Haskell "
            "has to a standard library, since `base` alone lacks even "
            "file-system convenience: bytestring (Data.ByteString), text "
            "(Data.Text.IO), time (Data.Time.*), directory "
            "(System.Directory) and process (System.Process). EXCLUDED "
            "because they come from Hackage rather than with the compiler: "
            "network (Network.Socket*), http-client / req / http-conduit "
            "(Network.HTTP.*), wai and warp (Network.Wai*), and "
            "typed-process (System.Process.Typed) — the last is the one a "
            "prefix rule would have wrongly admitted under System.Process, "
            "which is why this language is enumerated rather than prefixed."
        ),
        modules=frozenset({
            "Control.Concurrent", "Control.Exception", "Data.ByteString",
            "Data.Text.IO", "Data.Time.Clock", "Data.Time.Clock.System",
            "Debug.Trace", "GHC.Clock", "Prelude", "System.Directory",
            "System.Environment", "System.Exit", "System.IO", "System.Info",
            "System.Process",
        }),
    ),
    "java": Scope(
        why=(
            "The JDK (`java.*`) plus the historically-bundled `javax.*` and "
            "the standardized `jakarta.*`. ADR-0047 is explicit that this is "
            "NOT a carve-out and must not be cited as precedent for one: it "
            "is a statement about what the Java platform IS, not an "
            "exception to the stdlib rule."
        ),
        prefixes=("java.", "javax.", "jakarta."),
    ),
    "javascript": Scope(
        why=(
            "JavaScript has no single stdlib, so the line is RUNTIME "
            "BUILT-INS: Node core modules (`fs`, `http`, `child_process`, "
            "...), browser globals (`fetch`, `WebSocket`, `localStorage`, "
            "`document`, ...) and the `Deno` namespace. Everything reached "
            "through npm is out — axios, node-fetch, express, fastify, koa "
            "and the rest were culled in 864f55ed02 and must not return."
        ),
        modules=frozenset({
            "BroadcastChannel", "Date", "Deno", "EventSource", "WebSocket",
            "XMLHttpRequest", "caches", "child_process", "console", "dgram",
            "dgram.Socket", "document", "fetch", "fs", "fs.promises", "http",
            "https", "indexedDB", "localStorage", "navigator", "net",
            "net.Socket", "os", "path", "performance", "process",
            "sessionStorage", "window",
        }),
    ),
    "kotlin": Scope(
        why=(
            "`kotlin.*` plus everything Java admits, merged by "
            "_CATALOG_PARENTS (kotlin -> java). ktor, kotlin-logging, the "
            "Android SDK and Exposed were culled in 864f55ed02."
        ),
        prefixes=("kotlin",),
        inherits=("java",),
    ),
    "objc": Scope(
        why=(
            "Apple's system frameworks, which are what an Objective-C "
            "standard library IS on the only platforms Objective-C targets: "
            "Foundation and its `NS*` classes, Core Data (NSManagedObject"
            "Context, NSFetchRequest, NSPersistentStoreCoordinator) and "
            "os_log. All ship with the OS rather than through a package "
            "manager, so nothing here is third-party. objc.yaml's header "
            "declares 'common framework APIs', and this entry is that "
            "declaration made checkable — a CocoaPods/SPM dependency would "
            "not be admitted by it."
        ),
        prefixes=("NS",),
        modules=frozenset({"Foundation", "os_log"}),
    ),
    "python": Scope(
        why=(
            "The CPython standard library. python.yaml is the ONE catalogue "
            "that declares its own `stdlib_modules` list, but this gate does "
            "not defer to it: that list is provenance for the modules it "
            "names and says nothing about a module absent from it, and the "
            "four ungoverned rows this cull removes (aiohttp.web, "
            "flask.Flask, ujson, uvicorn) sat inside a `status: complete` "
            "catalogue whose own 300-entry list refutes them. EXCLUDED: "
            "those four, plus `django.db.models` — a documented, "
            "test-enforced carve-out of 29 rows that the owner ruled moves "
            "into the overlays with everything else, so that the rule has no "
            "exceptions left to cite."
        ),
        modules=frozenset({
            "argparse", "argparse.ArgumentParser", "asyncio",
            "asyncio.StreamReader", "asyncio.StreamWriter", "base64",
            "builtins", "contextlib", "csv", "datetime.date",
            "datetime.datetime", "dbm", "fcntl", "file", "fileinput",
            "ftplib.FTP", "grp", "gzip", "http.client.HTTPConnection",
            "http.client.HTTPSConnection", "http.server.HTTPServer",
            "importlib", "importlib.metadata", "importlib.resources",
            "importlib.util", "inspect", "io", "json", "locale", "logging",
            "multiprocessing.Pipe", "multiprocessing.Queue", "os",
            "os.environ", "os.path", "pathlib.Path", "pickle", "platform",
            "posixpath", "pwd", "shelve", "shlex", "shutil", "smtplib.SMTP",
            "socket.socket", "socketserver.TCPServer", "sqlite3",
            "sqlite3.Connection", "sqlite3.Cursor", "subprocess", "sys",
            "sys.stderr", "sys.stdin", "sys.stdout", "tarfile", "tempfile",
            "time", "typing", "urllib.request", "warnings",
            "xml.etree.ElementTree", "xmlrpc.server.SimpleXMLRPCServer",
            "zipfile",
        }),
    ),
    "rust": Scope(
        why=(
            "`std::*`. tokio, hyper and reqwest were culled in 864f55ed02 at "
            "a 91.7% name-for-name mirror rate against still-shipping `std::` "
            "rows — a HIGHER overlap than any row ADR-0047 reconsidered, "
            "which is why 'everyone uses this spelling' is not an argument "
            "the catalogues accept."
        ),
        prefixes=("std::",),
    ),
    "scala": Scope(
        why=(
            "`scala.*` plus everything Java admits, merged by "
            "_CATALOG_PARENTS (scala -> java). akka, pekko, http4s, play, "
            "sttp, fs2, cats and zio were culled in 864f55ed02."
        ),
        prefixes=("scala",),
        inherits=("java",),
    ),
    "swift": Scope(
        why=(
            "The Swift standard library (`Swift`) plus Apple's system "
            "frameworks, which ship with the OS: Foundation (FileManager, "
            "FileHandle, URLSession, Process, Bundle, NotificationCenter, "
            "...), Core Data (NSManagedObjectContext, NSFetchRequest), "
            "SwiftData (ModelContext), Network.framework (NWConnection, "
            "NWListener) and os. EXCLUDED, all reached through Swift "
            "Package Manager: the SwiftNIO family (Channel, "
            "ChannelHandlerContext, ClientBootstrap, ServerBootstrap, "
            "EventLoopGroup, NIOAsyncChannel, NonBlockingFileIO, NIOSSL, "
            "NIOWebSocketServerUpgrader, WebSocket), AsyncHTTPClient, "
            "swift-log (Logger — the catalogue's own note names it) and "
            "swift-distributed-tracing (Tracing). swift.yaml's header "
            "NAMED its third-party scope out loud; ADR-0047's answer is "
            "that an openly-declared divergence is still a divergence, and "
            "the recall it bought is preserved by the overlay."
        ),
        modules=frozenset({
            "Bundle", "CommandLine", "Date", "DispatchTime",
            "DistributedNotificationCenter", "FileHandle", "FileManager",
            "InputStream", "ModelContext", "NSFetchRequest",
            "NSManagedObjectContext", "NWConnection", "NWListener",
            "NotificationCenter", "Process", "ProcessInfo", "Swift",
            "URLRequest", "URLSession", "os",
        }),
    ),
}


def _out_of_scope(language: str) -> list[str]:
    """Modules the shipped catalogue carries that the curated line excludes.

    ``include_defaults=False`` is the whole point of the assertion: after
    ADR-0047 the third-party rows still LOAD, from a disclosed community
    overlay, so a gate that included them would pass while measuring nothing.
    What is being asserted is narrower and is the thing ADR-0016 §27 actually
    says — that the rows hypergumbo VOUCHES for are the standard library.
    """
    scope = CATALOGUE_SCOPE[language]
    catalog = load_catalog(language, include_defaults=False)
    return sorted({
        p.module for p in catalog.primitives
        if not scope.admits(p.module, CATALOGUE_SCOPE)
    })


@pytest.mark.parametrize("language", SHIPPED_LANGUAGES)
def test_shipped_catalogue_ships_only_in_scope_modules(language: str) -> None:
    """ADR-0047 ruling 8: the scope rule holds for EVERY shipped catalogue."""
    strays = _out_of_scope(language)
    assert strays == [], (
        f"{language}.yaml carries {len(strays)} module(s) outside the curated "
        f"stdlib line for {language}: {strays}\n\n"
        f"THE LINE FOR {language.upper()}: {CATALOGUE_SCOPE[language].why}\n\n"
        f"HOW TO FIX. If the module really is third-party, it does not belong "
        f"in a catalogue hypergumbo vouches for — move its rows to a shipped "
        f"community overlay in io_primitives_overlays/ (ADR-0047 ruling 1), "
        f"where they still load by default and are disclosed as unvouched. "
        f"If it really is part of the standard library, add it to "
        f"CATALOGUE_SCOPE[{language!r}] in this file WITH the reasoning, "
        f"because this table is the only written record of where the line is."
    )


@pytest.mark.parametrize("language", SHIPPED_LANGUAGES)
def test_every_shipped_catalogue_has_a_scope_entry(language: str) -> None:
    """No catalogue may ship ungated — the asymmetry ADR-0047 names as cause.

    Parametrised over the SHIPPED TREE rather than a hardcoded language list,
    so adding ``ruby.yaml`` without adding a scope entry fails here instead of
    silently joining the ungated eight.
    """
    assert language in CATALOGUE_SCOPE, (
        f"{language}.yaml ships with no entry in CATALOGUE_SCOPE. Every "
        f"catalogue needs a written stdlib line: ADR-0047 identifies "
        f"'scope gates exist for 6 of 14 languages' as the MECHANICAL CAUSE "
        f"of the third-party drift it corrects, so an ungated catalogue is "
        f"the defect, not merely an omission."
    )


def test_scope_table_has_no_entry_for_a_catalogue_that_does_not_ship() -> None:
    """The other direction: a stale entry names a language the tree dropped."""
    extra = sorted(set(CATALOGUE_SCOPE) - set(SHIPPED_LANGUAGES))
    assert extra == [], (
        f"CATALOGUE_SCOPE names languages with no shipped catalogue: {extra}"
    )


@pytest.mark.parametrize("language", SHIPPED_LANGUAGES)
def test_every_scope_entry_explains_itself(language: str) -> None:
    """``why`` is load-bearing: it is what lets the next reader classify."""
    why = CATALOGUE_SCOPE[language].why
    assert len(why) >= 80, (
        f"CATALOGUE_SCOPE[{language!r}].why is too short to record a "
        f"judgement about where {language}'s standard library ends"
    )
