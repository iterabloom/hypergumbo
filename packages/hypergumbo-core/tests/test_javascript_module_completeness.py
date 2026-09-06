# SPDX-License-Identifier: AGPL-3.0-or-later
"""JavaScript's ``module_completeness`` grants and the rows they rest on (WI-nolut).

The second leg of WI-lutuh's rust -> javascript -> python order. javascript.yaml
declared ZERO grants, so no node module could ever be an examined negative and
every clean javascript boundary verdict over a repository calling an unrowed
builtin was withheld. A grant says EVERY I/O primitive a module exposes is
catalogued, which is why it is the dangerous direction: a wrong grant
manufactures a false all-clear.

HOW THE GRANTS WERE REACHED. A mechanical probe (``jsleg_completeness_09062026/
probe.js``, node v20.19.6) enumerates each builtin's exports one level deep and
scans every function's JavaScript source for binding reach (``internalBinding``,
``binding.``, ``[kHandle]``, ``fd``, native code) and I/O surface names. It never
grants: a NONE verdict is a candidate, any signal is adjudicated with the source
in view. What it found, and what each grant rests on:

  path      seven rows carried NO signal -- ``join``/``dirname``/... are string
            arithmetic (INV-nular F7, WI-jojaf). The rows are DELETED and the
            module granted; ``resolve`` reads ``process.cwd()``, which is rowed
            on ``process`` where it is performed.
  url       42 pure functions; the two "signals" are the literal ``"http:"`` in
            the legacy ``Url.parse`` source.
  zlib, timers/promises, stream/promises   no signal at all.
  crypto    95 signals, every one ``[kHandle]`` -- a native crypto object, not
            a descriptor. ``randomBytes`` draws OS entropy through the binding;
            granted on the recorded python precedent (``secrets``/``random``/
            ``uuid``), the same reasoning rust's ``HashMap`` grant cites, and
            :class:`TestTheEntropyGrantRestsOnThePythonPrecedent` pins it.
  fs, fs/promises   91 and 13 unrowed exports with binding reach -- open/read/
            write/fstat/symlink/link/truncate/utimes/rm/cp/watch and the fd
            family -- are ROWED first; ``close`` is not (INV-nular F4: a
            lifecycle call transfers nothing); the ``ReadStream``/
            ``WriteStream`` classes constructed directly are a declared
            exclusion (the catalogue keys calls, and ``createReadStream`` /
            ``createWriteStream`` are rowed).
  process   the uid/gid getters, ``cpuUsage``/``memoryUsage``/
            ``resourceUsage``, ``openStdin`` (ipc_recv), ``chdir`` (env_write,
            python's ``os.chdir`` precedent) and ``dlopen`` (fs_read of a path)
            are rowed; ``exit``/``abort``/``kill`` are process control with no
            boundary, declared in the grant's note.

WHAT IS DELIBERATELY WITHHELD. ``readline``: its I/O is the STREAM passed to
``createInterface`` (``process.stdin`` in the common case, a file in others),
INV-zumin's class (b), and the javascript analyzer has no ``io_target_kind``
stamp yet -- rowing it would mint ``untrusted_input`` over every file-backed
reader, and granting it would call a stdin read a non-read. ``http``: ``new
http.Server()`` / ``new http.Agent()`` are constructors the catalogue does not
key, so the module's surface is not fully enumerated. Both stay ungranted and
keep withholding, which is the direction that cannot produce a false clean.

MATCHING IS EXACT: ``fs`` does not vouch for ``fs/promises``; both are audited
and declared, under both spellings the analyzer emits.
"""
from __future__ import annotations

import datetime as _dt
import pathlib

import pytest
import yaml

from hypergumbo_core import io_boundary
from hypergumbo_core.io_boundary import classify_call, load_catalog
from hypergumbo_core.verify_claims import compute_boundary_coverage

GRANTED = [
    ("path", "string arithmetic; F7 rows deleted"),
    ("url", "pure parsing; 'http:' literal is not a call"),
    ("zlib", "compression only"),
    ("timers/promises", "no signal"),
    ("stream/promises", "no signal"),
    ("crypto", "[kHandle] is a native object, entropy on the python precedent"),
    ("fs", "every path- and fd-taking primitive rowed"),
    ("fs/promises", "the slash spelling the analyzer emits for require('fs/promises')"),
    ("fs.promises", "the dotted spelling the rows use"),
    ("process", "getters, openStdin, chdir, dlopen rowed; exit/abort/kill are not I/O"),
]

WITHHELD = [
    ("readline", "the stream argument decides; no js io_target_kind stamp"),
    ("http", "new http.Server() / new http.Agent() are not keyed"),
    ("net", "not audited in this leg"),
    ("child_process", "not audited in this leg"),
    ("dns", "not audited in this leg"),
]


@pytest.fixture(scope="module")
def js_catalog():
    catalog = load_catalog("javascript")
    assert catalog is not None
    return catalog


class TestTheAuditedModulesAreDeclared:
    @pytest.mark.parametrize("module,why", GRANTED)
    def test_module_is_enumerated(self, js_catalog, module: str, why: str) -> None:
        assert js_catalog.module_io_is_enumerated(module), (
            f"{module} was audited and adjudicated grantable ({why}) but the "
            f"catalogue does not declare it"
        )

    def test_the_catalogue_cites_its_source(self, js_catalog) -> None:
        """Promotion is a citation check only; the grants are what carry
        coverage. nodejs.org is on the provenance allowlist."""
        assert js_catalog.status == "provenance_declared"


class TestTheRefusalsStayRefused:
    @pytest.mark.parametrize("module,why", WITHHELD)
    def test_module_is_not_declared(self, js_catalog, module: str, why: str) -> None:
        assert not js_catalog.module_io_is_enumerated(module), (
            f"{module} must NOT carry a completeness grant: {why}"
        )

    def test_a_grant_is_exact_and_nothing_is_inferred(self, js_catalog) -> None:
        assert not js_catalog.module_io_is_enumerated("fs/promises/extra")
        assert not js_catalog.module_io_is_enumerated("node:fs")


class TestEveryGrantIsADatedPositiveDeclaration:
    def test_every_yaml_entry_declares_complete_and_a_date(self) -> None:
        path = pathlib.Path(io_boundary.__file__).parent / "io_primitives" / "javascript.yaml"
        raw = yaml.safe_load(path.read_text())
        entries = raw.get("module_completeness") or []
        assert entries, "javascript.yaml must carry module_completeness entries"
        for entry in entries:
            assert entry.get("completeness") == "complete", entry
            _dt.date.fromisoformat(str(entry["retrieved"]))
            assert entry.get("notes"), f"{entry['module']}: a grant names the probe verdict it rests on"
        assert len({e["module"] for e in entries}) == len(entries)


class TestTheEntropyGrantRestsOnThePythonPrecedent:
    @pytest.mark.parametrize("module", ["secrets", "random", "uuid"])
    def test_python_still_grants_the_entropy_consumers(self, module: str) -> None:
        catalog = load_catalog("python")
        assert catalog is not None
        assert catalog.module_io_is_enumerated(module), (
            f"python no longer grants {module}; javascript's crypto grant cites it"
        )


def _cats():
    return {"javascript": load_catalog("javascript"), "python": load_catalog("python")}


def _classified(lang: str, module: str, name: str):
    prim = classify_call(_cats(), f"{lang}:{module}:0-0:{name}:unresolved", {})
    return None if prim is None else prim.boundary


class TestF7PurePathArithmeticIsNoLongerARead:
    """WI-jojaf / INV-nular F7: a path.join classified as a filesystem read and
    counted as an EXAMINED call; os.path.abspath reads only the cwd."""

    @pytest.mark.parametrize("name", ["join", "dirname", "basename", "extname", "relative", "normalize", "resolve"])
    def test_a_javascript_path_helper_classifies_as_nothing(self, name: str) -> None:
        assert _classified("javascript", "path", name) is None

    @pytest.mark.parametrize("module", ["os.path", "posixpath"])
    def test_python_abspath_classifies_as_nothing(self, module: str) -> None:
        assert _classified("python", module, "abspath") is None

    @pytest.mark.parametrize("module", ["os.path", "posixpath"])
    def test_python_realpath_still_reads(self, module: str) -> None:
        """CONTROL: realpath follows symlinks, a real filesystem read."""
        assert _classified("python", module, "realpath") == "fs_read"


class TestTheNewRowsClassify:
    """Every row added by the audit, exercised through the real matcher."""

    @pytest.mark.parametrize("name,boundary", [
        ("openSync", "fs_read"), ("readSync", "fs_read"), ("fstat", "fs_read"),
        ("opendir", "fs_read"), ("watch", "fs_read"), ("createReadStream", "fs_read"),
        ("rm", "fs_write"), ("cp", "fs_write"), ("symlink", "fs_write"),
        ("truncateSync", "fs_write"), ("writeSync", "fs_write"), ("fsync", "fs_write"),
        ("mkdtemp", "fs_write"), ("utimes", "fs_write"), ("link", "fs_write"),
    ])
    def test_fs(self, name: str, boundary: str) -> None:
        assert _classified("javascript", "fs", name) == boundary

    def test_fs_open_is_declared_in_both_directions_and_falls_back_to_read(self) -> None:
        """python's builtins.open shape: fs_read + fs_write, ONE of each
        direction, so the mode seam owns it; the javascript analyzer stamps
        no io_mode yet, so an unstamped open keeps the first-declared row."""
        catalog = load_catalog("javascript")
        both = {p.boundary for p in catalog.primitives if p.module == "fs" and p.name == "open"}
        assert both == {"fs_read", "fs_write"}, both
        assert _classified("javascript", "fs", "open") == "fs_read"

    @pytest.mark.parametrize("name,boundary", [
        ("open", "fs_read"), ("opendir", "fs_read"), ("statfs", "fs_read"), ("watch", "fs_read"),
        ("symlink", "fs_write"), ("link", "fs_write"), ("truncate", "fs_write"),
        ("utimes", "fs_write"), ("mkdtemp", "fs_write"), ("cp", "fs_write"), ("lchown", "fs_write"),
    ])
    def test_fs_promises_under_the_slash_spelling(self, name: str, boundary: str) -> None:
        assert _classified("javascript", "fs/promises", name) == boundary

    def test_close_is_not_a_boundary(self) -> None:
        """INV-nular F4: a lifecycle call transfers nothing."""
        assert _classified("javascript", "fs", "close") is None
        assert _classified("javascript", "fs", "closeSync") is None

    @pytest.mark.parametrize("name,boundary", [
        ("getuid", "host_info_read"), ("geteuid", "host_info_read"), ("getgid", "host_info_read"),
        ("getgroups", "host_info_read"), ("cpuUsage", "host_info_read"), ("memoryUsage", "host_info_read"),
        ("resourceUsage", "host_info_read"), ("openStdin", "ipc_recv"), ("chdir", "env_write"),
        ("dlopen", "fs_read"),
    ])
    def test_process(self, name: str, boundary: str) -> None:
        assert _classified("javascript", "process", name) == boundary

    def test_process_control_is_not_a_boundary(self) -> None:
        for name in ("exit", "abort", "kill"):
            assert _classified("javascript", "process", name) is None, name


def _js_coverage(edges: list[dict]):
    return compute_boundary_coverage(edges, {"javascript"}, {"javascript": load_catalog("javascript")})


def _call_into(module: str, name: str) -> list[dict]:
    return [
        {"src": "javascript:src/app.js:1-1:file:file",
         "dst": f"javascript:{module}:0-0:{name}:external_symbol", "type": "imports"},
        {"src": "javascript:src/app.js:3-5:run:function",
         "dst": f"javascript:{module}:0-0:{name}:external_symbol", "type": "calls"},
    ]


class TestTheGrantsActuallyPermitACleanVerdict:
    def test_a_granted_module_supports_a_clean_verdict(self) -> None:
        coverage = _js_coverage(_call_into("url", "parse"))
        assert coverage.complete is True, coverage.reason

    def test_the_slash_and_dot_spellings_are_both_adjudicable(self) -> None:
        for module in ("fs/promises", "fs.promises"):
            coverage = _js_coverage(_call_into(module, "opendir"))
            assert coverage.complete is True, (module, coverage.reason)

    def test_a_withheld_module_still_withholds(self) -> None:
        coverage = _js_coverage(_call_into("readline", "createInterface"))
        assert coverage.complete is False
        assert "readline" in coverage.reason

    def test_a_third_party_package_still_withholds(self) -> None:
        coverage = _js_coverage(_call_into("express", "Router"))
        assert coverage.complete is False
        assert "express" in coverage.reason
