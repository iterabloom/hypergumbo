# SPDX-License-Identifier: AGPL-3.0-or-later
"""WI-damom: the smart-test bed, its harness, and smart-test's target mode.

THREE LAYERS, because each can be wrong while the others look right:

* THE ORACLE (``mutate_source``, ``changed_blocks``, ``readers_of``). A harness
  whose MUST set is empty scores every selector perfect. So the mutation is
  driven on small sources here and the resulting module is EXECUTED: the raise
  has to land inside the changed block, keep ``__doc__`` and compile.
* THE BED. Every path an edit, a reader or a ``should_not`` names has to exist
  in ``tree/``, and every reader glob has to match a file, or a declaration
  scores against nothing and passes vacuously.
* THE WIRING, end to end. The harness runs the REAL smart-test in target mode
  on three edits, each chosen because one stage of the selection is the only
  thing that can produce its result: the coverage union is the only way to
  select the string-dispatch test, narrowing (on by default) the only way to drop the
  import-only one, and both need the index the clean-tree ``--full`` run folds
  (the path that used to fold nothing). The harness exits 2 when no index was
  built.

NEEDS THE INSTALLED PACKAGES (a stable ``hypergumbo`` for the slice, pytest-cov
for the index), so the cron's test-agent-infra step ignores this file and
test-all-packages runs it; tests/test_full_suite_agent_infra_ignores.py pins
the pair.
"""

# covers: scripts/smart-test
from __future__ import annotations

import importlib.machinery
import importlib.util
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
HARNESS = REPO_ROOT / "scripts" / "smart-test-bed"
SMART_TEST = REPO_ROOT / "scripts" / "smart-test"
BED = REPO_ROOT / "tests" / "fixtures" / "smart-test-bed"


def _load_harness():
    loader = importlib.machinery.SourceFileLoader("smart_test_bed", str(HARNESS))
    spec = importlib.util.spec_from_loader("smart_test_bed", loader)
    module = importlib.util.module_from_spec(spec)
    # Registered first: @dataclass resolves its module through sys.modules.
    sys.modules["smart_test_bed"] = module
    loader.exec_module(module)
    return module


bed = _load_harness()


def _load(source: str) -> dict:
    namespace: dict = {}
    exec(compile(source, "<mutant>", "exec"), namespace)  # noqa: S102 - our own fixture text
    return namespace


def _raised(source: str, *call: object) -> str:
    """Load ``source``, then call ``call[0](*call[1:])`` if given; the error text."""
    try:
        namespace = _load(source)
        if call:
            fn = namespace[call[0]]
            for args in call[1:]:
                fn = fn(*args)
    except RuntimeError as exc:
        return str(exc)
    return ""


# ── the oracle ───────────────────────────────────────────────────────────────

def test_a_changed_function_raises_after_its_docstring() -> None:
    base = 'def f(x):\n    """Doc."""\n    return x\n'
    new = 'def f(x):\n    """Doc."""\n    return x + 0\n'
    blocks = bed.changed_blocks("m.py", base, new)
    assert [b.name for b in blocks] == ["f"]
    mutant = bed.mutate_source("m.py", new, blocks)
    assert "m.py:f" in _raised(mutant, "f", (1,))
    assert _load(mutant)["f"].__doc__ == "Doc."


def test_an_unchanged_sibling_is_left_alone() -> None:
    base = "def f():\n    return 1\n\n\ndef g():\n    return 2\n"
    new = "def f():\n    return 1\n\n\ndef g():\n    return 3\n"
    mutant = bed.mutate_source("m.py", new, bed.changed_blocks("m.py", base, new))
    assert _raised(mutant, "f", ()) == ""
    assert "m.py:g" in _raised(mutant, "g", ())


def test_a_module_statement_raises_at_import_after_future_imports() -> None:
    base = '"""Doc."""\nfrom __future__ import annotations\nLIMIT = 10\n'
    new = '"""Doc."""\nfrom __future__ import annotations\nLIMIT = 5 * 2\n'
    blocks = bed.changed_blocks("m.py", base, new)
    assert [b.name for b in blocks] == [bed.MODULE_BLOCK]
    assert "m.py:<module>" in _raised(bed.mutate_source("m.py", new, blocks))


def test_a_nested_function_raises_only_when_called() -> None:
    base = "def outer(k):\n    def inner(x):\n        return x * k\n\n    return inner\n"
    new = base.replace("x * k", "k * x")
    blocks = bed.changed_blocks("m.py", base, new)
    assert [b.name for b in blocks] == ["outer.inner"]
    mutant = bed.mutate_source("m.py", new, blocks)
    assert _raised(mutant, "outer", (2,)) == ""
    assert "outer.inner" in _raised(mutant, "outer", (2,), (3,))


def test_a_class_body_raise_goes_above_a_decorated_method() -> None:
    base = "class C:\n    A = 1\n\n    @staticmethod\n    def f():\n        return 1\n"
    new = base.replace("A = 1", "A = 2")
    blocks = bed.changed_blocks("m.py", base, new)
    assert [b.name for b in blocks] == ["C"]
    assert "m.py:C" in _raised(bed.mutate_source("m.py", new, blocks))


def test_comments_change_no_block_and_a_shadowing_def_is_one() -> None:
    base = "def f():\n    return 1\n"
    assert bed.changed_blocks("m.py", base, "# note\ndef f():\n    return 1  # why\n") == []
    shadowed = base + "\n\ndef f():\n    return 1\n"
    assert [b.name for b in bed.changed_blocks("m.py", base, shadowed)] == ["f#2"]


def test_a_one_line_body_is_refused_not_mangled() -> None:
    new = "def f(): return 2\n"
    blocks = bed.changed_blocks("m.py", "def f(): return 1\n", new)
    with pytest.raises(bed.BedError, match="one-line body"):
        bed.mutate_source("m.py", new, blocks)


def test_reader_globs_span_directories_but_not_roots() -> None:
    readers = {"t.py": ["packages/*/src/**/*.py"]}
    assert bed.readers_of(["packages/a/src/x/y/z.py"], readers) == {"t.py"}
    assert bed.readers_of(["packages/a/src/z.py"], readers) == {"t.py"}
    assert bed.readers_of(["packages/a/tests/test_z.py"], readers) == set()


def test_an_ambiguous_old_string_is_refused(tmp_path: Path) -> None:
    (tmp_path / "m.py").write_text("x = 1\nx = 1\n")
    edit = {"name": "dup", "ops": [{"file": "m.py", "old": "x = 1\n", "new": "x = 2\n"}]}
    with pytest.raises(bed.BedError, match="2 times"):
        bed.apply_edit(tmp_path, edit)


# ── the bed ──────────────────────────────────────────────────────────────────

def _tree_files() -> set[str]:
    tree = BED / "tree"
    return {p.relative_to(tree).with_suffix("").as_posix() for p in tree.rglob("*.in")}


def test_every_name_the_bed_declares_exists() -> None:
    files = _tree_files()
    edits = bed.load_edits()
    assert len({e["name"] for e in edits}) == len(edits)
    for edit in edits:
        assert edit.get("oracle", "mutate") in ("mutate", "text"), edit["name"]
        for op in edit["ops"]:
            assert op["file"] in files, (edit["name"], op["file"])
        for test in edit.get("must", []) + edit.get("should_not", []):
            assert test in files, (edit["name"], test)
    for test, globs in bed.load_readers().items():
        assert test in files, test
        for glob in globs:
            assert any(bed._glob_regex(glob).match(f) for f in files), glob


def test_every_old_string_applies_once(tmp_path: Path) -> None:
    for edit in bed.load_edits():
        root = tmp_path / edit["name"]
        tree = BED / "tree"
        for src in tree.rglob("*.in"):
            out = root / src.relative_to(tree).with_suffix("")
            out.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(src, out)
        bed.apply_edit(root, edit)


# ── smart-test's target mode ─────────────────────────────────────────────────

def _git_repo(path: Path) -> Path:
    path.mkdir(parents=True)
    (path / "README").write_text("x\n")
    for args in (["init", "-q", "-b", "dev"], ["add", "-A"],
                 ["-c", "user.name=t", "-c", "user.email=t@example.invalid",
                  "commit", "-qm", "base"]):
        subprocess.run(["git", *args], cwd=path, check=True, capture_output=True)
    return path


def _tools_repo(tmp_path: Path) -> tuple[Path, Path]:
    """A repo owning a copy of smart-test whose mypy ratchet leaves a marker."""
    tools = _git_repo(tmp_path / "tools")
    (tools / "scripts").mkdir()
    shutil.copy2(SMART_TEST, tools / "scripts" / "smart-test")
    marker = tmp_path / "ratchet-ran"
    ratchet = tools / "scripts" / "check-mypy-ratchet"
    ratchet.write_text(f"#!/usr/bin/env bash\ntouch {marker}\nexit 0\n")
    ratchet.chmod(0o755)
    return tools, marker


def _smart_test(tools: Path, target: Path) -> subprocess.CompletedProcess:
    env = {"PATH": "/usr/bin:/bin", "HOME": str(target),
           "SMART_TEST_TARGET_ROOT": str(target)}
    return subprocess.run([str(tools / "scripts" / "smart-test"), "--manifest"],
                          cwd=target, capture_output=True, text=True, env=env)


def test_target_mode_skips_the_ratchet_and_says_so(tmp_path: Path) -> None:
    tools, marker = _tools_repo(tmp_path)
    done = _smart_test(tools, _git_repo(tmp_path / "target"))
    assert "mypy ratchet: not run" in done.stdout, done.stdout + done.stderr
    assert not marker.exists()


def test_targeting_its_own_checkout_keeps_the_ratchet(tmp_path: Path) -> None:
    """The negative control, and the reason the skip cannot be a bypass."""
    tools, marker = _tools_repo(tmp_path)
    done = _smart_test(tools, tools)
    assert marker.exists(), done.stdout + done.stderr
    assert "mypy ratchet: not run" not in done.stdout


def test_every_helper_resolves_from_the_tools_root() -> None:
    """A helper found under REPO_ROOT is missing in target mode, and every
    helper call is guarded by an existence check, so it would vanish without
    a word: the bed would score a selector smart-test does not run."""
    text = SMART_TEST.read_text(encoding="utf-8")
    assert "$REPO_ROOT/scripts/" not in text
    assert "$REPO_ROOT/.agent/" not in text


def test_the_rank_of_the_first_must_test_is_its_run_position() -> None:
    """WI-fipuf: how soon a failure-first order reaches a test the edit breaks."""
    bed = _load_harness()
    assert bed.first_must_rank(["a", "b", "c"], ["c", "b"]) == 2
    assert bed.first_must_rank(["a"], ["z"]) is None


# ── end to end ───────────────────────────────────────────────────────────────

def test_each_selection_stage_does_its_one_job(tmp_path: Path) -> None:
    out = tmp_path / "scores.json"
    done = subprocess.run(
        [sys.executable, str(HARNESS), "run", "--json", str(out),
         "--edit", "body-edit-c", "--edit", "runtime-dispatch",
         "--edit", "comment-marker", "--edit", "rename"],
        capture_output=True, text=True, cwd=REPO_ROOT)
    assert done.returncode == 0, done.stdout + done.stderr
    scores = {s["edit"]: s for s in json.loads(out.read_text())}
    core = "packages/bed-core/tests/"
    app = "packages/bed-app/tests/"

    # The mutation reaches through A -> B -> C and across the package boundary.
    body = scores["body-edit-c"]
    assert {f"{core}test_chain_a.py", f"{core}test_chain_c.py",
            f"{app}test_cli.py"} <= set(body["must"])
    # Narrowing drops the file that imports the chain but never runs chain_c.
    assert f"{app}test_cli_usage.py" in body["selected"]["union"]
    assert f"{app}test_cli_usage.py" not in body["selected"]["narrow"]

    # Only coverage sees the string dispatch, and only the union acts on it.
    dispatch = scores["runtime-dispatch"]
    assert f"{core}test_registry.py" in dispatch["must"]
    assert f"{core}test_registry.py" not in dispatch["selected"]["manifest"]
    assert f"{core}test_registry.py" in dispatch["selected"]["union"]

    # WI-zunug: a rename changes chain_a's import line, which is import-time
    # code. test_cli_usage only IMPORTS chain_a, so no coverage row ties it to
    # the change, and narrowing dropped it. Changed import-time code now
    # refuses narrowing.
    rename = scores["rename"]
    assert f"{app}test_cli_usage.py" in rename["must"]
    assert f"{app}test_cli_usage.py" in rename["selected"]["narrow"]

    # A text edit is scored by what breaks on the edited tree itself.
    assert f"{core}test_kinds_markers.py" in scores["comment-marker"]["must"]
