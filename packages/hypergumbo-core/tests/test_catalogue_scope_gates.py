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

import datetime
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

import pytest
import yaml

import hypergumbo_core.io_boundary as _iob
from hypergumbo_core.io_boundary import load_catalog
from hypergumbo_core.yaml_catalogs import YAML_CATALOGS

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
            # 2026-09-08 WI-dozul. <netdb.h> is POSIX.1-2008 (and its
            # getaddrinfo/getnameinfo pair is RFC 3493), sitting beside
            # <sys/socket.h> which is already on this line. It carries the
            # resolver surface, so admitting it is the same decision that
            # admitted the socket API rather than a new one.
            "netdb",
            # 2026-09-29 WI-bulub. <fcntl.h> is POSIX.1 and declares open /
            # openat / creat, whose descriptors <unistd.h>'s read/write (on
            # this line) consume -- the other half of the same file API.
            "fcntl",
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
            # 2026-09-08 WI-dozul: the receiver-qualified rows for net's own
            # Resolver type, listed in full like their siblings below.
            "net.Resolver",
            # Receiver-qualified method rows are listed in full rather than
            # derived by stripping a trailing capitalised segment. A rule
            # would silently admit `grpc.ClientConn`; an enumeration makes
            # every stdlib TYPE a reviewed entry too.
            "crypto/tls.Conn", "log/slog.Logger", "net.Conn", "net.Listener",
            "net/http.Client", "net/http.RoundTripper", "net/http.Transport",
            "net/smtp.Client",
            "os/exec.Cmd", "testing.B", "testing.T",
            # WI-vutav: the two bufio handle TYPES, so the READ one binding
            # after `bufio.NewReader`/`NewScanner` has a row to reach. Both
            # are stdlib (package bufio); the constructors were already in.
            "bufio.Reader", "bufio.Scanner",
            # WI-nunab: the WRITE-side handle types, so a write through the
            # handle's own method (`f.Write(b)`, `w.WriteString(s)`) has a
            # row. Both stdlib: package os's File, package bufio's Writer.
            "bufio.Writer", "os.File",
            # 2026-09-29 WI-nopam (ADR-0061: the built-in line is drawn for
            # every family). The Go packages the TAINT and SUMMARY catalogues
            # name: crypto/aes, crypto/cipher and crypto/rand are the stdlib's
            # cipher and randomness packages, and net/http/fcgi is net/http's
            # FastCGI server. All ship with the Go toolchain.
            "crypto/aes", "crypto/cipher", "crypto/rand", "net/http/fcgi",
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
            "which is why this language is enumerated rather than prefixed. "
            "Data.ByteString's strict/lazy/Char8 SIBLING modules "
            "(Data.ByteString.Lazy, .Char8, .Lazy.Char8) are the same "
            "`bytestring` boot library and are admitted here for the same "
            "reason (WI-zozun): each is a module of a package that ships "
            "with GHC. System.Directory.Extra and System.Process.Text are "
            "NOT admitted — they are the Hackage `extra` and `process-extras` "
            "packages, and live in the community overlay."
        ),
        modules=frozenset({
            "Control.Concurrent", "Control.Exception", "Data.ByteString",
            "Data.ByteString.Char8", "Data.ByteString.Lazy",
            "Data.ByteString.Lazy.Char8",
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
            # 2026-09-08 WI-dozul. `dns` is a Node CORE module -- the same
            # tier as `net`, `dgram` and `http` already on this line -- with
            # `dns/promises` and the `dns.Resolver` class as its two other
            # spellings of the same surface. Nothing here comes through npm.
            "dns", "dns.promises", "dns.Resolver",
            # 2026-09-29 WI-nopam (ADR-0061). Node core modules the catalogue's
            # module_completeness grants name (`url`, `zlib`, `crypto` and the
            # promise spellings of `fs`, `stream` and `timers`), the ECMAScript
            # globals the function summaries name (JSON, String, Array,
            # Promise), Node's `Buffer` global, and `eval`, the global function
            # spelled as its own pseudo-module exactly as `fetch` is. Each is a
            # runtime built-in; none comes through npm.
            "url", "zlib", "crypto", "fs/promises", "stream/promises",
            "timers/promises", "JSON", "String", "Array", "Promise", "Buffer",
            "eval",
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
            "exceptions left to cite. ADDED 2026-09-06 (WI-dupok, the third "
            "completeness leg): cProfile, codecs, ctypes, doctest, filecmp, "
            "glob, mimetypes, multiprocessing, optparse, pprint, pstats, "
            "socket (module level), ssl, sysconfig, traceback, unittest, "
            "xml.dom and their rowed classes -- every one a CPython stdlib "
            "module (sys.stdlib_module_names on 3.12), rowed from the python "
            "probe over the 08-24 scope audit's working set."
        ),
        modules=frozenset({
            "cProfile", "cProfile.Profile", "codecs", "ctypes", "ctypes.cdll",
            "ctypes.util", "doctest", "doctest.DocTestRunner", "filecmp",
            "filecmp.dircmp", "glob", "mimetypes", "mimetypes.MimeTypes",
            "multiprocessing", "multiprocessing.Process", "optparse.OptionParser",
            "optparse.Values", "pprint", "pprint.PrettyPrinter", "pstats.Stats",
            "socket", "ssl", "ssl.SSLContext", "ssl.SSLObject", "ssl.SSLSocket",
            "sysconfig", "traceback", "traceback.TracebackException", "unittest",
            "unittest.TestLoader", "unittest.TextTestRunner", "xml.dom",
            "xml.dom.minidom",
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
            # WI-jabus: the CLASS-QUALIFIED receivers the self-claims gate
            # needed. Each is a CPython class whose MODULE is already admitted
            # two lines above, and each is listed separately for the same
            # reason `pathlib.Path` and `ssl.SSLContext` are: this set is an
            # enumeration, not a prefix rule, because `module_io_is_enumerated`
            # matches EXACTLY and a scope gate that admitted by prefix would
            # disagree with the predicate it exists to police.
            "subprocess.Popen", "tarfile.TarFile", "typing.TextIO",
            "zipfile.ZipFile",
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
        # 2026-09-29 WI-nopam (ADR-0061). `Vec` and `String` are std types the
        # PRELUDE brings into scope under their bare names, which is how the
        # function summaries spell them (`Vec::push`, `String::from`).
        modules=frozenset({"Vec", "String"}),
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
            # 2026-09-29 WI-nopam (ADR-0061). The Foundation types the library
            # signatures return: URL, Data, Pipe and URLSession's four task
            # types, all part of the same OS-shipped framework as URLSession.
            "URL", "Data", "Pipe", "URLSessionDataTask",
            "URLSessionDownloadTask", "URLSessionUploadTask",
            "URLSessionWebSocketTask",
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


# ---------------------------------------------------------------------------
# EVERY FAMILY DECLARES ITS TIER (ADR-0061 ruling 3, WI-nopam).
# ---------------------------------------------------------------------------
#
# ADR-0061 draws the built-in line -- the standard-library line above -- for
# every catalogue family, not only the I/O catalogue, and makes the tier a
# property the FILE declares (`provenance: builtin` or `provenance:
# community`), never one inferred from the directory it sits in. The gate below
# is the other half: a declaration nothing checks is a comment.
#
# It refuses three things:
#   1. a shipped file that declares neither tier;
#   2. a community file with no dated `retrieved:`;
#   3. a BUILT-IN file carrying a row outside the standard-library line.
# The third is the one that matters. A built-in row takes full effect, including
# the effects that make a verdict cleaner, so a third-party row declared
# built-in is exactly the undisclosed vouching ADR-0061 exists to end.
#
# HOW "OUTSIDE THE LINE" IS DECIDED, PER FAMILY. The families spell a library
# differently, so each has an extractor that returns the spellings a file
# names, and every spelling is checked against the SAME `CATALOGUE_SCOPE`:
#   * io_primitives, io_primitives_overlays, taint_sources, taint_sinks: every
#     `module:` value (which includes the io `module_completeness` grants --
#     the rows that turn an unclassified call into an examined negative);
#   * taint_sanitizers, function_summaries: the qualified names;
#   * library_signatures: each key's owner and each returned type;
#   * dataflow_patterns: the module a `library_patterns` regex anchors on
#     (`\bjson\.dump\(`), since the rest match a method name on any receiver;
#   * frameworks: rows are regexes over decorators and names with no module
#     field, so there is no spelling to check. A frameworks file may declare
#     built-in only when `BUILTIN_FRAMEWORK_FILES` names it WITH its reason --
#     the allowlist shape again, failing closed on a new file.
#   * cfg_nodes, url_folding: built-in only (ADR-0061 ruling 1); their rows are
#     grammar node types and hypergumbo's own folding engines.

_PKG_ROOT = Path(_iob.__file__).parent
TIERS = ("builtin", "community")

#: ADR-0061 ruling 1: these families describe grammars and hypergumbo's own
#: engines, not anyone's libraries, so they have the built-in tier only.
BUILTIN_ONLY_FAMILIES = frozenset({"cfg_nodes", "url_folding"})

#: Catalogue language keys that share another language's standard library.
SCOPE_ALIASES = {"typescript": "javascript"}

#: Short spellings a catalogue uses for a standard-library name, each mapped
#: to the name it abbreviates. The gate checks the TARGET against the line, so
#: an entry cannot admit a third-party name: it can only say what a short
#: spelling means.
SHORT_SPELLINGS: dict[str, dict[str, str]] = {
    # Go code names a package by its last path element once imported.
    "go": {
        "exec": "os/exec", "http": "net/http",
        "Listener": "net.Listener", "Cmd": "os/exec.Cmd",
    },
    # Java code names a class by its simple name once imported.
    "java": {
        "DriverManager": "java.sql.DriverManager",
        "Connection": "java.sql.Connection",
        "Statement": "java.sql.Statement",
        "PreparedStatement": "java.sql.PreparedStatement",
        "Files": "java.nio.file.Files",
        "Runtime": "java.lang.Runtime",
        "ProcessBuilder": "java.lang.ProcessBuilder",
        "Process": "java.lang.Process",
        "Socket": "java.net.Socket",
        "ServerSocket": "java.net.ServerSocket",
    },
}

#: Frameworks files that may declare built-in, each with the reason. Everything
#: else in frameworks/ is community: a framework is a third-party library by
#: nature, and a file that MIXES a stdlib idiom with a library one (node-http,
#: test-frameworks, go-encoding-callbacks, web_audio, swiftui) is community as a
#: whole, because a file has one tier.
BUILTIN_FRAMEWORK_FILES: dict[str, str] = {
    "main-functions.yaml": "Each language's own entry-point syntax (func "
    "main, public static void main, fn main); no library is named.",
    "language-conventions.yaml": "Shapes hypergumbo's own analyzers emit for "
    "CUDA, WGSL, COBOL, LaTeX and Starlark; no library is named.",
    "naming-conventions.yaml": "Naming heuristics (*Controller, *Handler) "
    "that match on a symbol's name alone; no library is named.",
    "library-exports.yaml": "Each language's own export syntax (index-file "
    "exports, Go capitalisation, pub items); no library is named.",
    "config-conventions.yaml": "Symbols hypergumbo's own config-file "
    "analyzers emit for package manifests; no library is named.",
    "cocoa.yaml": "UIKit and AppKit lifecycle hooks: Apple system frameworks "
    "that ship with the OS, the same line the objc entry above draws.",
    "jax-rs.yaml": "Jakarta REST annotations. The java entry above admits "
    "jakarta.* and javax.* as what the Java platform IS.",
    "jakarta-cdi.yaml": "Jakarta CDI annotations, jakarta.* / javax.* only; "
    "admitted by the same java line as jax-rs.",
}


def _shipped_files() -> list[tuple[str, Path]]:
    return [
        (spec.directory, path)
        for spec in YAML_CATALOGS
        for path in sorted((_PKG_ROOT / spec.directory).glob("*.yaml"))
    ]


SHIPPED_FILES = _shipped_files()
_IDS = [f"{family}/{path.name}" for family, path in SHIPPED_FILES]


def _data(path: Path) -> dict:
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    assert isinstance(data, dict), f"{path}: not a mapping"
    return data


def _candidates(name: str, qualified: bool) -> list[str]:
    """The spellings to try for ``name``.

    A ``module:`` field is a module, and is checked EXACTLY, as the io gate
    above checks it: a prefix rule would admit Hackage's
    ``System.Directory.Extra`` under ``System.Directory``. A QUALIFIED name
    (``logging.Logger.debug``, ``Vec::push``) does not separate the module from
    the member, so every prefix cut at a ``.`` or ``::`` boundary is a
    candidate. ``/`` is never a cut: ``net/http/fcgi`` must itself be on the Go
    line, because ``golang.org/x/...`` shows a shared path prefix is not a
    shared library.
    """
    if not qualified:
        return [name]
    parts = re.split(r"(\.|::)", name)
    return ["".join(parts[:i]) for i in range(len(parts), 0, -2)]


def _admitted(language: str, name: str, qualified: bool) -> bool:
    language = SCOPE_ALIASES.get(language, language)
    scope = CATALOGUE_SCOPE.get(language)
    short = SHORT_SPELLINGS.get(language, {})
    for candidate in _candidates(name, qualified):
        for spelling in (candidate, short.get(candidate)):
            if spelling is None:
                continue
            if scope is not None and scope.admits(spelling, CATALOGUE_SCOPE):
                return True
            # CPython answers this one itself: the interpreter's own list of
            # its standard-library top-level modules.
            if language == "python" and spelling.split(".")[0] in \
                    sys.stdlib_module_names:
                return True
    return False


def _module_values(node: object) -> list[str]:
    if isinstance(node, dict):
        found = [node["module"]] if isinstance(node.get("module"), str) else []
        for value in node.values():
            found.extend(_module_values(value))
        return found
    if isinstance(node, list):
        return [m for item in node for m in _module_values(item)]
    return []


def _by_language_modules(
    data: dict, section: str,
) -> list[tuple[str, str, bool]]:
    return [
        (lang, module, False)
        for lang, entries in (data.get(section) or {}).items()
        for module in _module_values(entries)
    ]


_ANCHORED_MODULE = re.compile(r"^\\b([A-Za-z_][\w]*)(?:\\\.|:)")


def _spellings(family: str, path: Path) -> list[tuple[str, str, bool]]:
    """``(language, spelling, qualified)`` for every library name a file names."""
    data = _data(path)
    if family in ("io_primitives", "io_primitives_overlays"):
        lang = data.get("language", path.stem)
        return [(lang, m, False) for m in _module_values(data)]
    if family == "taint_sources":
        return _by_language_modules(data, "sources")
    if family == "taint_sinks":
        return _by_language_modules(data, "sinks")
    if family == "taint_sanitizers":
        return [
            (lang, name, True)
            for transform in data.get("transforms") or []
            for lang, names in (transform.get("functions") or {}).items()
            for name in names
        ]
    if family == "function_summaries":
        lang = path.stem.split("_")[0]
        return [(lang, s["function"], True) for s in data.get("summaries") or []]
    if family == "library_signatures":
        lang = data["language"]
        out: list[tuple[str, str, bool]] = []
        for section in ("signatures", "package_variables"):
            for key, returned in (data.get(section) or {}).items():
                out.append((lang, key.rsplit(".", 1)[0], True))
                out.append((lang, returned, True))
        return out
    if family == "dataflow_patterns":
        lang = data["language"]
        return [
            (lang, m.group(1), False)
            for row in data.get("library_patterns") or []
            if (m := _ANCHORED_MODULE.match(row.get("match", "")))
        ]
    return []


@pytest.mark.parametrize("family, path", SHIPPED_FILES, ids=_IDS)
def test_every_shipped_catalogue_file_declares_its_tier(
    family: str, path: Path,
) -> None:
    """ADR-0061 ruling 3: a file says whose word its rows are."""
    declared = _data(path).get("provenance")
    assert declared in TIERS, (
        f"{family}/{path.name} declares provenance {declared!r}. Every shipped "
        f"catalogue file must say `provenance: builtin` (hypergumbo vouches: "
        f"standard-library rows) or `provenance: community` (shipped, not "
        f"maintained, never makes a verdict cleaner) -- ADR-0061 ruling 3."
    )


@pytest.mark.parametrize("family, path", SHIPPED_FILES, ids=_IDS)
def test_a_community_file_is_dated(family: str, path: Path) -> None:
    """A community row is a third-party claim; its date is what lets a reader
    judge how stale it is (ADR-0061 ruling 3)."""
    data = _data(path)
    if data.get("provenance") != "community":
        return
    retrieved = data.get("retrieved")
    if isinstance(retrieved, str):
        retrieved = datetime.date.fromisoformat(retrieved)
    assert isinstance(retrieved, datetime.date), (
        f"{family}/{path.name} is community but has no `retrieved:` date"
    )


@pytest.mark.parametrize("family, path", SHIPPED_FILES, ids=_IDS)
def test_a_builtin_file_names_only_the_standard_library(
    family: str, path: Path,
) -> None:
    """ADR-0061 rulings 1-3: built-in means hypergumbo vouches, and the line
    it vouches up to is the standard library."""
    data = _data(path)
    if data.get("provenance") != "builtin":
        assert family not in BUILTIN_ONLY_FAMILIES, (
            f"{family} has the built-in tier only (ADR-0061 ruling 1)"
        )
        return
    if family == "frameworks":
        assert path.name in BUILTIN_FRAMEWORK_FILES, (
            f"frameworks/{path.name} declares builtin but is not in "
            f"BUILTIN_FRAMEWORK_FILES. A framework is a third-party library; "
            f"declare it community, or add it to that table WITH the reason "
            f"it names no library."
        )
        return
    strays = sorted({
        f"{lang}: {name}" for lang, name, qualified in _spellings(family, path)
        if not _admitted(lang, name, qualified)
    })
    assert strays == [], (
        f"{family}/{path.name} declares builtin but names "
        f"{len(strays)} spelling(s) outside the standard-library line: "
        f"{strays}\n\nHOW TO FIX. Move third-party rows to a file declaring "
        f"`provenance: community` with a `retrieved:` date (ADR-0061). If a "
        f"spelling IS the standard library, add it to CATALOGUE_SCOPE (or "
        f"SHORT_SPELLINGS, for an abbreviation) in this file with the reason."
    )


def test_every_builtin_framework_entry_names_a_builtin_file() -> None:
    """The allowlist's other direction: a stale entry, or one for a file that
    declares community, would be a reason attached to nothing."""
    stale = sorted(
        name for name in BUILTIN_FRAMEWORK_FILES
        if not (_PKG_ROOT / "frameworks" / name).is_file()
        or _data(_PKG_ROOT / "frameworks" / name).get("provenance") != "builtin"
    )
    assert stale == []


def test_every_short_spelling_resolves_inside_the_line() -> None:
    """An abbreviation may say what a short name means; it may not admit one."""
    bad = sorted(
        f"{lang}: {short} -> {full}"
        for lang, table in SHORT_SPELLINGS.items()
        for short, full in table.items()
        if not CATALOGUE_SCOPE[lang].admits(full, CATALOGUE_SCOPE)
    )
    assert bad == []


def test_the_gate_reaches_every_family() -> None:
    """ASSERT REACH: the parametrisation walks every registered family, and
    each one contributes files."""
    reached = {family for family, _ in SHIPPED_FILES}
    assert reached == {spec.directory for spec in YAML_CATALOGS}
