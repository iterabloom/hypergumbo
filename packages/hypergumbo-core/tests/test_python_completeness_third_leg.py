# SPDX-License-Identifier: AGPL-3.0-or-later
"""The third python completeness leg: rows for the working set, then grants (WI-dupok).

WI-lutuh's order was rust -> javascript -> python. After the 2026-08-15 climb
(77 grants) the 08-24 scope audit still found 41 stdlib module strings reaching
the coverage gate uncatalogued across 11 corpus repositories -- glob, threading,
unittest, struct, pickle, errno, atexit, queue, string, ctypes, multiprocessing,
xml.dom.minidom, socket, ssl and 27 singletons. WI-dupok's own rule sequences
the work: rows FIRST for every I/O-capable module (recall that stands on its
own), grants after, each dated and citing its probe verdict.

THE PROBE (``~/hypergumbo_lab_notebook/dupok_python_09062026/probe_py.py``, the
python twin of the rust-src and node probes): public members one level deep,
``inspect.getsource`` scanned for ``open(`` / ``os.`` / ``sys.std*`` / socket
signals; a C-implemented member is NATIVE and opaque. It never grants. Its
signals are what the rows below answer -- glob walks directories, mimetypes
opens the mime.types files, pprint / traceback / unittest / doctest / optparse
write default streams, cProfile / pstats dump and load paths, codecs.open and
ctypes.CDLL open paths, multiprocessing.Process launches and signals, ssl had
NO rows at all (INV-buzab's live false confirm) -- and the grants are the
modules whose remaining surface is in-memory.

TWO RULES THE 08-15 SURVEY SET AND THIS LEG FOLLOWS. A read from a
CALLER-OPENED object is the caller's I/O (tomllib): pickle.load, marshal.load,
xml.dom.minidom.parse(fileobj) get no row. A WRITE into a caller-opened object
is rowed where the value enters it (json.dump): pickle.dump keeps its fs_write
row.

REFUSED, so the list can be checked: tkinter (a display is a boundary the
vocabulary does not name), socket (its DNS functions are UNRULED -- net_recv
by ADR-0049's question or host_info_read; escalated), ssl (rowed, but the
descriptor plumbing and MemoryBIO were not read one by one), unittest.mock,
multiprocessing's Manager / shared_memory / connection surface.
"""
from __future__ import annotations

import datetime as _dt
import pathlib

import pytest
import yaml

from hypergumbo_core import io_boundary
from hypergumbo_core.io_boundary import classify_call, load_catalog
from hypergumbo_core.verify_claims import compute_boundary_coverage

CATALOGS = {"python": load_catalog("python")}


def _boundary(module: str, name: str) -> str | None:
    dst = f"python:{module}:0-0:{name}:external_symbol"
    primitive = classify_call(CATALOGS, dst, None)
    return primitive.boundary if primitive else None


class TestTheRowsCameFirst:
    """Every SIGNAL member the probe flagged that opens, writes, launches or
    signals is rowed, through the real matcher."""

    @pytest.mark.parametrize("module,name,boundary", [
        ("glob", "glob", "fs_read"), ("glob", "iglob", "fs_read"),
        ("mimetypes", "init", "fs_read"), ("mimetypes", "guess_type", "fs_read"),
        ("mimetypes.MimeTypes", "read", "fs_read"),
        ("filecmp", "cmp", "fs_read"), ("filecmp", "cmpfiles", "fs_read"),
        ("sysconfig", "get_config_h_filename", "fs_read"), ("sysconfig", "get_platform", "env_read"),
        ("cProfile", "run", "fs_write"), ("cProfile.Profile", "dump_stats", "fs_write"),
        ("pstats.Stats", "load_stats", "fs_read"), ("pstats.Stats", "dump_stats", "fs_write"), ("pstats.Stats", "print_stats", "logging"),
        ("xml.dom.minidom", "parse", "fs_read"), ("xml.dom", "getDOMImplementation", "env_read"),
        ("unittest.TestLoader", "discover", "fs_read"), ("unittest", "main", "logging"),
        ("unittest.TextTestRunner", "run", "logging"),
        ("optparse.Values", "read_file", "fs_read"), ("optparse.OptionParser", "print_help", "logging"),
        ("doctest", "testfile", "fs_read"), ("doctest", "testmod", "logging"),
        ("codecs", "open", "fs_read"),
        ("ctypes", "CDLL", "fs_read"), ("ctypes.cdll", "LoadLibrary", "fs_read"), ("ctypes.util", "find_library", "fs_read"),
        ("pprint", "pprint", "logging"), ("pprint.PrettyPrinter", "pprint", "logging"),
        ("traceback", "print_exc", "logging"), ("traceback.TracebackException", "print", "logging"),
        ("multiprocessing.Process", "start", "subprocess"), ("multiprocessing", "Pool", "subprocess"),
        ("multiprocessing.Process", "kill", "ipc_send"),
        ("socket", "create_connection", "net_send"), ("socket", "create_server", "net_listen"),
        ("ssl.SSLSocket", "sendall", "net_send"), ("ssl.SSLSocket", "recv", "net_recv"),
        ("ssl.SSLSocket", "connect", "net_send"), ("ssl.SSLObject", "read", "net_recv"),
        ("ssl", "get_server_certificate", "net_recv"),
        ("ssl.SSLContext", "load_cert_chain", "fs_read"),
    ])
    def test_row_classifies(self, module: str, name: str, boundary: str) -> None:
        assert _boundary(module, name) == boundary

    def test_codecs_open_is_dual_like_builtins_open(self) -> None:
        both = {p.boundary for p in CATALOGS["python"].primitives if p.module == "codecs" and p.name == "open"}
        assert both == {"fs_read", "fs_write"}, both

    def test_the_caller_opened_object_rule_holds(self) -> None:
        """Reads from a caller-opened object are the caller's I/O; the write
        into one is rowed where the value enters it (json.dump's precedent)."""
        assert _boundary("pickle", "load") is None
        assert _boundary("pickle", "dump") == "fs_write"
        assert _boundary("marshal", "load") is None
        assert _boundary("xml.dom.minidom", "parseString") is None


GRANTED = [
    "errno", "xml", "xml.etree", "xml.dom", "xml.dom.minidom", "glob", "mimetypes", "mimetypes.MimeTypes",
    "filecmp", "filecmp.dircmp", "sysconfig", "pprint", "pprint.PrettyPrinter", "traceback",
    "traceback.TracebackException", "threading", "cProfile", "cProfile.Profile", "pstats", "pstats.Stats",
    "struct", "binascii", "operator", "marshal", "zlib", "decimal", "enum", "string", "atexit", "queue",
    "pickle", "codecs", "ctypes", "ctypes.util", "optparse", "optparse.OptionParser", "optparse.Values",
    "doctest", "doctest.DocTestRunner", "unittest", "unittest.TestLoader", "unittest.TextTestRunner",
    "multiprocessing.Process", "multiprocessing.Pool",
]
WITHHELD = [
    ("tkinter", "GUI I/O to a display; out of scope"),
    ("socket", "DNS resolvers unruled: net_recv or host_info_read"),
    ("ssl", "rowed but not read one by one; PARTIAL"),
    ("unittest.mock", "patches module state; not read"),
    ("multiprocessing", "Manager / shared_memory / connection not read"),
    ("select", "not in this leg's working set"),
]


class TestTheGrants:
    @pytest.mark.parametrize("module", GRANTED)
    def test_is_enumerated(self, module: str) -> None:
        assert CATALOGS["python"].module_io_is_enumerated(module), module

    @pytest.mark.parametrize("module,why", WITHHELD)
    def test_is_not_enumerated(self, module: str, why: str) -> None:
        assert not CATALOGS["python"].module_io_is_enumerated(module), (module, why)

    def test_every_new_entry_is_dated_today_and_carries_its_verdict(self) -> None:
        path = pathlib.Path(io_boundary.__file__).parent / "io_primitives" / "python.yaml"
        raw = yaml.safe_load(path.read_text())
        entries = {e["module"]: e for e in raw.get("module_completeness") or []}
        for module in GRANTED:
            e = entries[module]
            assert e.get("completeness") == "complete", module
            assert _dt.date.fromisoformat(str(e["retrieved"])) == _dt.date(2026, 9, 6), module
            assert e.get("notes"), f"{module}: a grant names the probe verdict it rests on"
        assert len({e["module"] for e in raw["module_completeness"]}) == len(raw["module_completeness"])


def _call_into(module: str, name: str) -> list[dict]:
    return [
        {"src": "python:src/app.py:1-1:file:file", "dst": f"python:{module}:0-0:{name}:external_symbol", "type": "imports"},
        {"src": "python:src/app.py:3-5:run:function", "dst": f"python:{module}:0-0:{name}:external_symbol", "type": "calls"},
    ]


class TestTheGrantsPermitACleanVerdict:
    def test_a_granted_module_is_adjudicable(self) -> None:
        coverage = compute_boundary_coverage(_call_into("struct", "pack"), {"python"}, CATALOGS)
        assert coverage.complete is True, coverage.reason

    def test_a_withheld_module_still_withholds(self) -> None:
        coverage = compute_boundary_coverage(_call_into("tkinter", "Tk"), {"python"}, CATALOGS)
        assert coverage.complete is False and "tkinter" in coverage.reason

    def test_a_third_party_package_still_withholds(self) -> None:
        coverage = compute_boundary_coverage(_call_into("httpx", "get"), {"python"}, CATALOGS)
        assert coverage.complete is False and "httpx" in coverage.reason
