# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for scripts/check-io-allowlist (WI-vumum).

The gate is an INDEPENDENT syntactic check on hypergumbo's own I/O surface:
every call site of a risky primitive in ``packages/*/src`` must be listed in
``docs/hypergumbo.io-allowlist.yaml``. These tests pin three things:

* the SCANNER — alias resolution, open-mode handling, receiver-agnostic
  method names, and (the reason it is an AST walk and not a grep) that
  primitive names held as DATA are not call sites;
* the COMPARISON — a new site, a stale entry, a count change and an
  unreviewed entry each fail, and the output names exactly what to add or
  remove;
* the LIVE TREE — the committed allowlist matches ``packages/*/src`` today,
  plus a positive control showing an unlisted ``subprocess.run`` fails it.
"""

# covers: scripts/check-io-allowlist, docs/hypergumbo.io-allowlist.yaml, .woodpecker/*.yml, .github/workflows/*.yml
from __future__ import annotations

import importlib.machinery
import importlib.util
import shutil
import sys
import textwrap
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "scripts" / "check-io-allowlist"


def _load():
    loader = importlib.machinery.SourceFileLoader("check_io_allowlist", str(SCRIPT))
    spec = importlib.util.spec_from_loader("check_io_allowlist", loader)
    mod = importlib.util.module_from_spec(spec)
    # dataclasses resolves annotations through sys.modules[cls.__module__].
    sys.modules["check_io_allowlist"] = mod
    loader.exec_module(mod)
    return mod


gate = _load()


def _scan(src: str, module: str = "pkg.mod"):
    return gate.scan_source(textwrap.dedent(src), module)


def _prims(src: str) -> list[str]:
    return sorted(s.primitive for s in _scan(src))


# --- alias resolution -------------------------------------------------------


def test_from_import_alias_is_resolved():
    sites = _scan("""
        from subprocess import run as r
        def go():
            r(["git", "status"])
    """)
    assert [(s.primitive, s.family, s.function) for s in sites] == [
        ("subprocess.run", "process", "go")
    ]


def test_module_alias_is_resolved():
    assert _prims("""
        import os as _os
        _os.remove("x")
    """) == ["os.remove"]


def test_dotted_import_binds_head():
    assert _prims("""
        import urllib.request
        urllib.request.urlopen("https://example.invalid")
    """) == ["urllib.request.urlopen"]


def test_function_local_import_is_resolved():
    assert _prims("""
        def f():
            import shutil as sh
            sh.rmtree("d")
    """) == ["shutil.rmtree"]


def test_reference_without_call_counts():
    """``runner=subprocess.run`` hands the primitive to someone else to call;
    counting only Call nodes would miss it entirely."""
    assert _prims("""
        import subprocess
        def f(runner=subprocess.run):
            return runner
    """) == ["subprocess.run"]


def test_annotation_is_not_a_site():
    assert _prims("""
        import socket
        def f(s: socket.socket) -> socket.socket:
            return s
    """) == []


def test_non_primitive_attribute_of_risky_module_is_not_a_site():
    assert _prims("""
        import subprocess
        try:
            pass
        except subprocess.CalledProcessError:
            x = subprocess.PIPE
    """) == []


# --- catalogue strings ------------------------------------------------------


def test_primitive_names_as_data_are_not_sites():
    """The reason this is an AST walk. io_boundary / axis_meta_keys and the
    YAML catalogues hold primitive names as strings; a grep counts them."""
    assert _prims('''
        """Calls subprocess.run and os.remove and Path.write_text."""
        PRIMS = ["subprocess.run", "os.remove", "write_text", "eval"]
        ROW = {"module": "pickle", "name": "loads"}
    ''') == []


# --- receiver-agnostic method names -----------------------------------------


def test_distinctive_method_name_matches_any_receiver():
    assert _prims("""
        def f(p, q):
            p.write_text("x")
            q.unlink()
            p.parent.mkdir(parents=True)
    """) == ["*.mkdir", "*.unlink", "*.write_text"]


def test_path_constructor_receiver_is_matched_via_method_name():
    assert _prims("""
        from pathlib import Path
        Path("a").write_bytes(b"")
    """) == ["*.write_bytes"]


def test_ambiguous_method_name_only_on_path_constructor():
    """``.replace`` is overwhelmingly ``str.replace``; only a receiver that is
    syntactically a ``Path(...)`` result counts."""
    assert _prims("""
        from pathlib import Path
        def f(s, p):
            s.replace("a", "b")
            Path(p).replace("dst")
    """) == ["pathlib.Path().replace"]


def test_qualified_match_wins_over_method_name():
    assert _prims("""
        import os
        os.mkdir("d")
    """) == ["os.mkdir"]


# --- open modes -------------------------------------------------------------


@pytest.mark.parametrize("call,expected", [
    ('open(p)', []),
    ('open(p, "rb")', []),
    ('open(p, encoding="utf-8")', []),
    ('open(p, "w")', ["builtins.open[w]"]),
    ('open(p, mode="a")', ["builtins.open[w]"]),
    ('open(p, "r+")', ["builtins.open[w]"]),
    ('open(p, m)', ["builtins.open[?]"]),
    ('p.open("x")', ["*.open[w]"]),
    ('p.open()', []),
    ('p.open(mode=m)', ["*.open[?]"]),
])
def test_open_mode(call, expected):
    assert _prims(f"def f(p, m):\n    {call}\n") == expected


def test_star_args_make_mode_unknown():
    assert _prims("def f(p, a, k):\n    open(p, *a)\n    open(p, **k)\n") == [
        "builtins.open[?]", "builtins.open[?]"]


def test_open_like_reference_counts_with_unknown_mode():
    assert _prims("""
        import gzip
        from star import *
        def f(p, opener=gzip.open, o2=open):
            return opener(p)
    """) == ["builtins.open[?]", "gzip.open[?]"]


def test_write_mode_archive_open():
    assert _prims("""
        import tarfile, zipfile, gzip
        tarfile.open(p)
        tarfile.open(p, "w:gz")
        zipfile.ZipFile(p, "w")
        gzip.open(p, "wt")
    """) == ["gzip.open[w]", "tarfile.open[w]", "zipfile.ZipFile[w]"]


# --- dynamic import / yaml / eval ------------------------------------------


def test_dynamic_import_only_when_non_literal():
    assert _prims("""
        import importlib
        importlib.import_module("json")
        importlib.import_module(name)
        __import__("os")
        __import__(n)
    """) == ["builtins.__import__[dynamic]", "importlib.import_module[dynamic]"]


def test_yaml_safe_loader_is_not_a_site():
    assert _prims("""
        import yaml
        yaml.load(s, Loader=yaml.CSafeLoader)
        yaml.load(s, yaml.SafeLoader)
        yaml.load(s)
        yaml.unsafe_load(s)
    """) == ["yaml.load", "yaml.unsafe_load"]


def test_sqlite_readonly_uri_is_not_a_site():
    assert _prims("""
        import sqlite3
        sqlite3.connect(f"file:{p}?mode=ro", uri=True)
        sqlite3.connect("file:x?mode=ro", uri=True)
        sqlite3.connect(f"file:{p}?mode=ro")
        sqlite3.connect(str(p))
    """) == ["sqlite3.connect", "sqlite3.connect"]


def test_builtin_eval_exec_compile():
    assert _prims("""
        eval(x)
        exec(y)
        compile(z, "<s>", "exec")
    """) == ["builtins.compile", "builtins.eval", "builtins.exec"]


def test_shadowed_builtin_is_still_conservatively_counted():
    """No scope analysis: a local ``def open`` does not hide the builtin.
    Documented over-approximation, reviewed in the allowlist."""
    assert _prims("""
        def open(p, m):
            pass
        open("x", "w")
    """) == ["builtins.open[w]"]


def test_prefix_module_counts_calls_not_references():
    assert _prims("""
        import huggingface_hub
        huggingface_hub.snapshot_download("x")
        try:
            pass
        except huggingface_hub.utils.HfHubHTTPError:
            pass
    """) == ["huggingface_hub.snapshot_download"]


# --- qualnames --------------------------------------------------------------


def test_enclosing_qualname():
    sites = _scan("""
        import os
        os.remove("a")
        class C:
            def m(self):
                def inner():
                    os.remove("b")
                inner()
        async def g():
            os.remove("c")
    """)
    assert sorted(s.function for s in sites) == ["<module>", "C.m.inner", "g"]


def test_module_name_for_path():
    root = Path("/r")
    p = root / "packages" / "hypergumbo-core" / "src" / "hypergumbo_core" / "x" / "y.py"
    assert gate.module_name_for(p, root) == ("hypergumbo-core", "hypergumbo_core.x.y")
    init = root / "packages" / "pkg" / "src" / "pkg_mod" / "__init__.py"
    assert gate.module_name_for(init, root) == ("pkg", "pkg_mod")


# --- comparison --------------------------------------------------------------


def _site(primitive="subprocess.run", function="go", module="pkg.mod",
          family="process", line=3):
    return gate.Site(module=module, function=function, primitive=primitive,
                     family=family, line=line, package="pkg")


def _allow(entries):
    return gate.parse_allowlist({"version": 1, "modules": entries})


GOOD = {
    "pkg.mod": {
        "commands": ["sketch"],
        "sites": [{
            "function": "go", "primitive": "subprocess.run",
            "family": "process", "count": 1, "zone": "repo_inspection",
            "target": "git", "purpose": "fingerprint the repo",
        }],
    },
}


def test_compare_clean():
    report = gate.compare([_site()], _allow(GOOD))
    assert report.ok
    assert report.render() == ""


def test_compare_new_site_names_exactly_what_to_add():
    report = gate.compare([_site(), _site(primitive="os.system", line=9)],
                          _allow(GOOD))
    assert not report.ok
    text = report.render()
    assert "NEW" in text and "os.system" in text and "pkg.mod" in text
    assert "line 9" in text
    assert "--update" in text


def test_compare_stale_entry_names_exactly_what_to_remove():
    report = gate.compare([], _allow(GOOD))
    assert not report.ok
    text = report.render()
    assert "STALE" in text and "subprocess.run" in text and "go" in text


def test_compare_count_change():
    report = gate.compare([_site(), _site(line=5)], _allow(GOOD))
    assert not report.ok
    assert "count 1 -> 2" in report.render()


def test_unreviewed_entry_fails():
    bad = {"pkg.mod": {"sites": [dict(GOOD["pkg.mod"]["sites"][0],
                                      purpose="TODO")]}}
    report = gate.compare([_site()], _allow(bad))
    assert not report.ok
    text = report.render()
    assert "INCOMPLETE" in text and "purpose" in text and "commands" in text


def test_unknown_zone_fails():
    bad = {"pkg.mod": dict(GOOD["pkg.mod"], sites=[
        dict(GOOD["pkg.mod"]["sites"][0], zone="anywhere")])}
    report = gate.compare([_site()], _allow(bad))
    assert "zone 'anywhere'" in report.render()


def test_analysed_repo_zone_requires_finding():
    bad = {"pkg.mod": dict(GOOD["pkg.mod"], sites=[
        dict(GOOD["pkg.mod"]["sites"][0], zone="analysed_repo")])}
    report = gate.compare([_site()], _allow(bad))
    assert not report.ok
    assert "finding" in report.render()


def test_process_entry_requires_target():
    entry = dict(GOOD["pkg.mod"]["sites"][0])
    del entry["target"]
    report = gate.compare([_site()], _allow({"pkg.mod": dict(
        GOOD["pkg.mod"], sites=[entry])}))
    assert "target" in report.render()


def test_module_defaults_are_inherited():
    allow = _allow({"pkg.mod": {
        "commands": ["sketch"], "zone": "repo_inspection",
        "purpose": "module-wide reason",
        "sites": [{"function": "go", "primitive": "subprocess.run",
                   "family": "process", "count": 1, "target": "git"}],
    }})
    assert gate.compare([_site()], allow).ok


def test_duplicate_key_rejected():
    entry = GOOD["pkg.mod"]["sites"][0]
    with pytest.raises(gate.AllowlistError, match="duplicate"):
        _allow({"pkg.mod": dict(GOOD["pkg.mod"], sites=[entry, entry])})


@pytest.mark.parametrize("doc", [None, [], {"version": 2, "modules": {}},
                                 {"version": 1, "modules": []},
                                 {"version": 1, "modules": {"m": {"sites": [{}]}}},
                                 {"version": 1, "modules": {"m": []}}])
def test_malformed_allowlist_rejected(doc):
    with pytest.raises(gate.AllowlistError):
        gate.parse_allowlist(doc)


# --- update ------------------------------------------------------------------


def test_update_preserves_review_and_marks_new(tmp_path):
    allow = _allow(GOOD)
    sites = [_site(), _site(), _site(primitive="os.system", function="h")]
    doc = gate.updated_document(sites, allow)
    mods = doc["modules"]["pkg.mod"]
    assert mods["commands"] == ["sketch"]
    by_prim = {s["primitive"]: s for s in mods["sites"]}
    assert by_prim["subprocess.run"]["count"] == 2
    assert by_prim["subprocess.run"]["purpose"] == "fingerprint the repo"
    assert by_prim["os.system"]["purpose"] == "TODO"
    # Round-trips through the parser, and the TODO keeps the gate red.
    out = tmp_path / "a.yaml"
    gate.write_allowlist(out, doc)
    reparsed = gate.load_allowlist(out)
    assert not gate.compare(sites, reparsed).ok


def test_written_allowlist_has_no_yaml_aliases(tmp_path):
    shared = ["sketch"]
    doc = {"version": 1, "modules": {
        "a": {"commands": shared, "sites": []},
        "b": {"commands": shared, "sites": []}}}
    out = tmp_path / "a.yaml"
    gate.write_allowlist(out, doc)
    assert "&id" not in out.read_text() and "*id" not in out.read_text()


def test_module_finding_is_inherited():
    allow = _allow({"pkg.mod": dict(GOOD["pkg.mod"], finding="INV-x")})
    assert allow.entries[("pkg.mod", "go", "subprocess.run")]["finding"] == "INV-x"


def test_summary_counts_findings_and_unlisted():
    allow = _allow({"pkg.mod": dict(GOOD["pkg.mod"], finding="INV-x")})
    text = gate.render_summary([_site(), _site(primitive="os.system")], allow)
    assert "INV-x 1" in text and "(unlisted) 1" in text


def test_update_drops_stale():
    doc = gate.updated_document([], _allow(GOOD))
    assert doc["modules"] == {}


# --- main / exit codes -------------------------------------------------------


def _mini_repo(tmp_path: Path, body: str) -> Path:
    src = tmp_path / "packages" / "pkg" / "src" / "pkg_mod"
    src.mkdir(parents=True)
    (src / "__init__.py").write_text(textwrap.dedent(body))
    return tmp_path


def test_main_exit_codes(tmp_path, capsys):
    repo = _mini_repo(tmp_path, """
        import subprocess
        def go():
            subprocess.run(["git"])
    """)
    allow = repo / "allow.yaml"
    assert gate.main(["--root", str(repo), "--allowlist", str(allow)]) == 2
    assert gate.main(["--root", str(repo), "--allowlist", str(allow),
                      "--update"]) == 1  # written, but TODOs remain
    doc = yaml.safe_load(allow.read_text())
    site = doc["modules"]["pkg_mod"]["sites"][0]
    site.update(purpose="probe", target="git", zone="repo_inspection",
                commands=["sketch"])
    allow.write_text(yaml.safe_dump(doc))
    assert gate.main(["--root", str(repo), "--allowlist", str(allow)]) == 0
    capsys.readouterr()
    assert gate.main(["--root", str(repo), "--allowlist", str(allow),
                      "--list"]) == 0
    assert "pkg_mod" in capsys.readouterr().out
    assert gate.main(["--root", str(repo), "--allowlist", str(allow),
                      "--summary"]) == 0
    summary = capsys.readouterr().out
    assert "process" in summary and "repo_inspection 1" in summary


def test_main_syntax_error_is_infrastructure(tmp_path):
    repo = _mini_repo(tmp_path, "def (:\n")
    allow = repo / "allow.yaml"
    allow.write_text("version: 1\nmodules: {}\n")
    assert gate.main(["--root", str(repo), "--allowlist", str(allow)]) == 2


def test_main_malformed_allowlist_is_infrastructure(tmp_path):
    repo = _mini_repo(tmp_path, "x = 1\n")
    allow = repo / "allow.yaml"
    allow.write_text("version: 7\n")
    assert gate.main(["--root", str(repo), "--allowlist", str(allow)]) == 2


# --- the live tree -----------------------------------------------------------


def test_live_tree_passes():
    sites = gate.scan_tree(REPO_ROOT)
    report = gate.compare(sites, gate.load_allowlist(gate.DEFAULT_ALLOWLIST))
    assert report.ok, report.render()


def test_live_tree_positive_control(tmp_path):
    """An unlisted ``subprocess.run`` added to a copy of a real module must
    fail the gate — a null from the live test means clean, not blind."""
    victim = (REPO_ROOT / "packages" / "hypergumbo-core" / "src"
              / "hypergumbo_core" / "safety_zones.py")
    copy = tmp_path / "packages" / "hypergumbo-core" / "src" / "hypergumbo_core"
    copy.mkdir(parents=True)
    shutil.copy(victim, copy / "safety_zones.py")
    allow = gate.load_allowlist(gate.DEFAULT_ALLOWLIST)
    baseline = gate.scan_tree(tmp_path)
    expected = {k: e for k, e in allow.entries.items()
                if k[0] == "hypergumbo_core.safety_zones"}
    trimmed = gate.Allowlist(entries=expected)
    assert gate.compare(baseline, trimmed).ok
    with (copy / "safety_zones.py").open("a") as fh:
        fh.write("\n\ndef sneaky():\n    subprocess.run(['curl', 'x'])\n")
    report = gate.compare(gate.scan_tree(tmp_path), trimmed)
    assert not report.ok
    assert "sneaky" in report.render() and "subprocess.run" in report.render()


# --- CI wiring ---------------------------------------------------------------
# The gate is only a gate if a pipeline that can block a merge runs it. The
# merge-blocking pipeline is .woodpecker/woodpecker.yml (auto-pr polls its
# context); the cron arm is unconditional so a path the filter misses still
# fails within one cadence.


def _step(path: Path, name: str) -> dict:
    doc = yaml.safe_load(path.read_text())
    steps = {s["name"]: s for s in doc["steps"]}
    assert name in steps, f"{path.name} has no `{name}` step"
    return steps[name]


def test_per_pr_pipeline_runs_the_gate_on_source_changes():
    step = _step(REPO_ROOT / ".woodpecker" / "woodpecker.yml", "io-allowlist")
    assert any("scripts/check-io-allowlist" in c for c in step["commands"])
    include = step["when"][0]["path"]["include"]
    for needed in ("packages/*/src/**", "docs/hypergumbo.io-allowlist.yaml",
                   "scripts/check-io-allowlist"):
        assert needed in include
    assert not step.get("failure"), "failure: ignore would make it advisory"


def test_cron_pipeline_runs_the_gate_unconditionally():
    step = _step(REPO_ROOT / ".woodpecker" / "full-suite.yml", "io-allowlist")
    assert any("scripts/check-io-allowlist" in c for c in step["commands"])
    assert "when" not in step and not step.get("failure")


def test_github_workflows_run_the_gate():
    ci = (REPO_ROOT / ".github" / "workflows" / "ci.yml").read_text()
    assert "io-allowlist-gate:" in ci and "./scripts/check-io-allowlist" in ci
    assert "needs.io-allowlist-gate.result }}\" == \"failure\"" in ci
    full = (REPO_ROOT / ".github" / "workflows" / "full-suite.yml").read_text()
    assert "./scripts/check-io-allowlist" in full
