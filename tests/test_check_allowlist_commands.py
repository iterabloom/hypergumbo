# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for scripts/check-allowlist-commands: every allowlist entry's `commands`
names the subcommands that reach it, and only those.

The static half is tested on small package trees written to tmp_path, so each
resolution rule (lazy import, reference-not-call, inherited ``self`` method,
module-level table vs import-time call) is pinned by a case that would go red
without it; then on the live tree, with a positive control that adds one edge
in memory and watches the comparison fail. The trace half is tested on a toy
program (the tracer must see a site line in the process AND in a child), and by
its comparison on canned observations. The full matrix of subcommand runs is
the cron's job, not the suite's.
"""
from __future__ import annotations

import importlib.machinery
import importlib.util
import json
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "scripts" / "check-allowlist-commands"


def _load():
    loader = importlib.machinery.SourceFileLoader("check_allowlist_commands", str(SCRIPT))
    spec = importlib.util.spec_from_loader("check_allowlist_commands", loader)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["check_allowlist_commands"] = mod
    loader.exec_module(mod)
    return mod


gate = _load()


def _tree(tmp_path: Path, files: dict[str, str]) -> Path:
    """Write ``{"pkg/mod.py": src}`` under packages/p/src and return the root."""
    for rel, src in files.items():
        f = tmp_path / "packages" / "p" / "src" / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(textwrap.dedent(src))
    return tmp_path


def _reaches(root: Path, src_unit: str, dst_unit: str) -> bool:
    g = gate.build_graph(root)
    return dst_unit in gate.reach(g.edges, [src_unit])


# --- resolution rules ---------------------------------------------------------


def test_bare_call_and_from_import_across_modules(tmp_path):
    root = _tree(tmp_path, {
        "pkg/__init__.py": "",
        "pkg/a.py": "from .b import helper\ndef entry():\n    return helper()\n",
        "pkg/b.py": "def helper():\n    return sink()\ndef sink():\n    pass\n",
    })
    assert _reaches(root, "pkg.a:entry", "pkg.b:sink")


def test_lazy_import_inside_the_function(tmp_path):
    root = _tree(tmp_path, {
        "pkg/__init__.py": "",
        "pkg/a.py": "def entry():\n    from .b import sink\n    sink()\n",
        "pkg/b.py": "def sink():\n    pass\n",
    })
    assert _reaches(root, "pkg.a:entry", "pkg.b:sink")


def test_a_reference_counts_not_only_a_call(tmp_path):
    """``run = runner or sink`` hands the function on; whoever calls ``run``
    calls it. Row 8's callers are all of this shape."""
    root = _tree(tmp_path, {
        "pkg/__init__.py": "",
        "pkg/a.py": "from .b import sink\ndef entry(runner=None):\n    run = runner or sink\n    return run()\n",
        "pkg/b.py": "def sink():\n    pass\n",
    })
    assert _reaches(root, "pkg.a:entry", "pkg.b:sink")


def test_self_method_resolves_through_a_base_in_another_module(tmp_path):
    root = _tree(tmp_path, {
        "pkg/__init__.py": "",
        "pkg/base.py": "class Base:\n    def make(self):\n        pass\n",
        "pkg/sub.py": "from .base import Base\nclass Sub(Base):\n    def go(self):\n        self.make()\n",
    })
    assert _reaches(root, "pkg.sub:Sub.go", "pkg.base:Base.make")


def test_module_alias_from_sys_modules(tmp_path):
    """``cli_mod = sys.modules[__name__]; cli_mod.f`` is the extras table's shape."""
    root = _tree(tmp_path, {
        "pkg/__init__.py": "",
        "pkg/a.py": "import sys\ndef table():\n    m = sys.modules[__name__]\n    return [m.sink]\ndef sink():\n    pass\n",
    })
    assert _reaches(root, "pkg.a:table", "pkg.a:sink")


def test_naming_a_module_object_does_not_run_its_functions(tmp_path):
    """Binding a module to a name is not an import: before this rule, the extras
    table's ``cli_mod`` reached every function cli's module level mentions."""
    root = _tree(tmp_path, {
        "pkg/__init__.py": "",
        "pkg/a.py": "import sys\nTABLE = [lambda: sink()]\ndef table():\n    m = sys.modules[__name__]\n    return m\ndef sink():\n    pass\n",
    })
    assert not _reaches(root, "pkg.a:table", "pkg.a:sink")


def test_a_module_level_call_runs_at_import_but_a_table_does_not(tmp_path):
    """Importing ``b`` runs ``boot()``; ``RULES = [rule]`` only hands ``rule`` to
    b's own functions -- the discovery submodule rule is of that shape, and
    treating it as import-time made every command run ``git ls-tree``."""
    root = _tree(tmp_path, {
        "pkg/__init__.py": "",
        "pkg/a.py": "def entry():\n    from . import b\n",
        "pkg/b.py": "def boot():\n    pass\ndef rule():\n    pass\nboot()\nRULES = [rule]\ndef match():\n    return RULES[0]()\n",
        "pkg/c.py": "from .b import match\ndef other():\n    match()\n",
    })
    assert _reaches(root, "pkg.a:entry", "pkg.b:boot")
    assert not _reaches(root, "pkg.a:entry", "pkg.b:rule")
    assert _reaches(root, "pkg.c:other", "pkg.b:rule")


def test_a_common_method_name_on_an_unknown_receiver_is_dropped_and_counted(tmp_path):
    defs = "".join(f"class C{i}:\n    def run(self):\n        pass\n" for i in range(5))
    root = _tree(tmp_path, {
        "pkg/__init__.py": "",
        "pkg/a.py": defs + "def entry(x):\n    x.run()\n",
    })
    g = gate.build_graph(root)
    assert not any(v.endswith(".run") for v in gate.reach(g.edges, ["pkg.a:entry"]))
    assert g.dropped["run"] == 1


# --- subcommands and command strings ------------------------------------------


def test_subcommands_are_read_from_the_parser_source(tmp_path):
    cli = tmp_path / "cli.py"
    cli.write_text(textwrap.dedent("""
        def build_parser(sub):
            p = sub.add_parser("survey", aliases=["run"], help="x")
            p.set_defaults(func=cmd_run)
            q = sub.add_parser(
                "cache-clear",
            )
            q.set_defaults(func=cmd_cache_clear)
    """))
    assert gate.subcommands(cli) == {"survey": "cmd_run", "cache-clear": "cmd_cache_clear"}


ALL = {"sketch", "survey", "slice", "search", "catalog", "config", "trust-backend",
       "compact", "verify-claims", "cache-status"}


@pytest.mark.parametrize("item, want", [
    ("<analysis> with --backend rust-analyzer, or a trust-backend grant (any one suffices)",
     gate.ANALYSIS & ALL),
    ("<any> (--backend; main's pre-scan)", ALL),
    ("catalog (it profiles the current directory)", {"catalog"}),
    ("trust-backend (a grant or a revoke)", {"trust-backend"}),
    ("on a results-cache miss without --minimal: slice, search", {"slice", "search"}),
    ("slice, search (the results-cache lookup imports the module, --minimal included)",
     {"slice", "search"}),
    ("survey --gzip", {"survey"}),
    ("<analysis> with [backends] scip_python in config.toml", gate.ANALYSIS & ALL),
])
def test_command_strings_parse_to_subcommands_not_to_words_in_their_conditions(item, want):
    got, non_cli = gate.parse_commands([item], ALL)
    assert got == want and non_cli == []


def test_non_cli_forms_are_kept_aside():
    got, non_cli = gate.parse_commands(
        ["scripts/smart-test", "library", "python -m hypergumbo_core.test_masking"], ALL)
    assert got == set() and len(non_cli) == 3


def test_analysis_is_the_twelve_that_build_one():
    assert "compact" not in gate.ANALYSIS and "backend-agreement" not in gate.ANALYSIS
    assert len(gate.ANALYSIS) == 12


# --- the comparison -----------------------------------------------------------


def _entry(commands, function="sink", module="pkg.b"):
    return gate.Entry(module=module, function=function, primitive="subprocess.run",
                      commands=commands)


def _cmds():
    return {"alpha": "cmd_alpha", "beta": "cmd_beta"}


def _cli_tree(tmp_path):
    return _tree(tmp_path, {
        "pkg/__init__.py": "",
        "pkg/cli.py": "from .b import sink\ndef cmd_alpha(a):\n    sink()\ndef cmd_beta(a):\n    pass\ndef main():\n    pass\n",
        "pkg/b.py": "def sink():\n    pass\n",
    })


def test_a_command_that_reaches_an_entry_unlisted_fails(tmp_path):
    g = gate.build_graph(_cli_tree(tmp_path))
    report = gate.compare_static([_entry(["beta"])], g, _cmds(), cli_module="pkg.cli", cuts=[])
    assert not report.ok
    assert any(f.kind == "unlisted" and f.command == "alpha" for f in report.findings)
    assert any(f.kind == "unreachable" and f.command == "beta" for f in report.findings)
    assert "cmd_alpha > sink" in report.render()


def test_the_right_list_passes(tmp_path):
    g = gate.build_graph(_cli_tree(tmp_path))
    assert gate.compare_static([_entry(["alpha"])], g, _cmds(), cli_module="pkg.cli", cuts=[]).ok


def test_a_judged_cut_removes_the_path_and_a_stale_cut_fails(tmp_path):
    g = gate.build_graph(_cli_tree(tmp_path))
    cut = gate.Cut(scope=("alpha",), src="pkg.cli:cmd_alpha", dst="pkg.b:sink", reason="r")
    assert gate.compare_static([_entry([])], g, _cmds(), cli_module="pkg.cli", cuts=[cut]).ok
    stale = gate.Cut(scope=("beta",), src="pkg.cli:cmd_beta", dst="pkg.b:sink", reason="r")
    report = gate.compare_static([_entry(["alpha"])], g, _cmds(), cli_module="pkg.cli", cuts=[stale])
    assert not report.ok and any(f.kind == "stale-cut" for f in report.findings)


def test_main_reaches_count_for_every_command(tmp_path):
    root = _tree(tmp_path, {
        "pkg/__init__.py": "",
        "pkg/cli.py": "from .b import sink\ndef cmd_alpha(a):\n    pass\ndef cmd_beta(a):\n    pass\n"
                      "def main():\n    sink()\n    return {'a': cmd_alpha, 'b': cmd_beta}\n",
        "pkg/b.py": "def sink():\n    pass\n",
    })
    g = gate.build_graph(root)
    assert gate.compare_static([_entry(["<any>"])], g, _cmds(), cli_module="pkg.cli", cuts=[]).ok
    report = gate.compare_static([_entry(["alpha"])], g, _cmds(), cli_module="pkg.cli", cuts=[])
    assert any(f.kind == "unlisted" and f.command == "beta" for f in report.findings)


# --- the live tree ------------------------------------------------------------


def test_live_tree_passes():
    report = gate.check_static(REPO_ROOT)
    assert report.ok, report.render()


def test_live_tree_positive_control():
    """One extra edge -- catalog calling the sketch cache write -- must fail the
    live comparison, naming both: a null from the live test means clean, not blind."""
    g = gate.build_graph(REPO_ROOT)
    g.edges["hypergumbo_core.cli:cmd_catalog"].add("hypergumbo_core.safety_zones:cache_write")
    report = gate.check_static(REPO_ROOT, graph=g)
    assert not report.ok
    text = report.render()
    assert "catalog" in text and "cache_write" in text


def test_every_live_cut_is_used():
    """A cut that matches no edge is a stale judgement; check_static reports it,
    and this pins that the shipped table has none."""
    report = gate.check_static(REPO_ROOT)
    assert not [f for f in report.findings if f.kind == "stale-cut"]


# --- the trace half -----------------------------------------------------------


def test_the_tracer_records_a_site_line_in_the_process_and_in_a_child(tmp_path):
    if sys.version_info < (3, 12):
        pytest.skip("the tracer needs sys.monitoring (3.12+); the cron step pins 3.12")
    prog = tmp_path / "prog.py"
    prog.write_text(textwrap.dedent("""
        import subprocess, sys
        def site():
            return 1
        site()
        if len(sys.argv) == 1:
            subprocess.run([sys.executable, __file__, "child"], check=True)
    """))
    site_line = 4
    trace_dir = tmp_path / "t"
    trace_dir.mkdir()
    sites = tmp_path / "sites.json"
    sites.write_text(json.dumps({str(prog): {str(site_line): "prog:4"}}))
    env = gate.trace_env(trace_dir, sites)
    subprocess.run([sys.executable, str(prog)], env=env, check=True)
    pids = {json.loads(line)["pid"] for f in trace_dir.glob("*.jsonl")
            for line in f.read_text().splitlines() if json.loads(line).get("site") == "prog:4"}
    assert len(pids) == 2


def test_trace_comparison_flags_an_observed_unlisted_command():
    entries = [_entry(["alpha"], function="sink", module="pkg.b")]
    observed = {"pkg.b:sink": {"alpha": ["alpha-cold"], "beta": ["beta-warm"]}}
    report = gate.compare_trace(entries, observed, {"alpha", "beta"})
    assert not report.ok
    (f,) = report.findings
    assert f.kind == "observed-unlisted" and f.command == "beta" and "beta-warm" in f.detail


def test_stand_ins_reach_the_same_lines_the_real_tools_do(tmp_path):
    """The cron has no rust-analyzer, gitleaks or sentence-transformers; the
    stand-ins exist so those variants still RUN. Their own contract: each is
    on PATH / importable, answers, and loads nothing."""
    stand = gate.make_stand_ins(tmp_path, missing={"rust-analyzer", "gitleaks", "sentence_transformers"})
    env = {"PATH": f"{stand['bin']}", "PYTHONPATH": str(stand["py"])}
    out = subprocess.run(["rust-analyzer", "--version"], env=env, capture_output=True, text=True)
    assert out.returncode == 0 and "stand-in" in out.stdout
    out = subprocess.run(["gitleaks", "stdin"], env=env, capture_output=True, text=True, input="x")
    assert out.returncode == 0 and json.loads(out.stdout) == []
    code = "import sentence_transformers as s; m = s.SentenceTransformer('x', local_files_only=True); print(len(m.encode(['a','b'])))"
    out = subprocess.run([sys.executable, "-c", code], env={**env, "PATH": "/usr/bin:/bin"},
                         capture_output=True, text=True)
    assert out.returncode == 0 and out.stdout.strip() == "2"
    # The model-missing mode: a cache-only load raises, as the real library does.
    missing = "import sentence_transformers as s\ntry:\n    s.SentenceTransformer('x', local_files_only=True)\nexcept OSError:\n    print('missing')"
    out = subprocess.run([sys.executable, "-c", missing], capture_output=True, text=True,
                         env={**env, "PATH": "/usr/bin:/bin", "HG_STAND_IN_MODEL_MISSING": "1"})
    assert out.stdout.strip() == "missing"


# --- CI wiring ----------------------------------------------------------------


def _step(path: Path, name: str) -> dict:
    doc = yaml.safe_load(path.read_text())
    steps = {s["name"]: s for s in doc["steps"]}
    assert name in steps, f"{path.name} has no `{name}` step"
    return steps[name]


def test_per_pr_pipeline_runs_the_static_check_on_source_changes():
    step = _step(REPO_ROOT / ".woodpecker" / "woodpecker.yml", "allowlist-commands")
    assert any("scripts/check-allowlist-commands --static" in c for c in step["commands"])
    include = step["when"][0]["path"]["include"]
    for needed in ("packages/*/src/**", "docs/hypergumbo.io-allowlist.yaml",
                   "scripts/check-allowlist-commands"):
        assert needed in include
    assert not step.get("failure"), "failure: ignore would make it advisory"


def test_cron_pipeline_runs_the_trace_on_312_unconditionally():
    step = _step(REPO_ROOT / ".woodpecker" / "full-suite.yml", "allowlist-commands-trace")
    assert step["image"].startswith("python:3.12")
    assert any("scripts/check-allowlist-commands --trace" in c for c in step["commands"])
    assert "when" not in step and not step.get("failure")
