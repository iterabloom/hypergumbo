# SPDX-License-Identifier: AGPL-3.0-or-later
"""The guard over work kept by hand (``hypergumbo_core.kept_work_guard``).

``test_live_tree_is_clean`` is the guard itself: a new function that writes
module state or a lazy attribute, and neither marks its fill nor is exempted
with a reason, fails it. The rest pin each shape the scan recognises, and
each it must not mistake for one, on small sources.
"""
from __future__ import annotations

import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from hypergumbo_core.kept_work_guard import (
    EXEMPT,
    Verdict,
    judge,
    marks_a_fill,
    module_containers,
    report,
    scan_source,
    scanned_files,
)

REPO_ROOT = Path(__file__).resolve().parents[3]


def _writes(source: str) -> set[tuple[str, str, str]]:
    found = scan_source(textwrap.dedent(source), "m.py").writes
    return {(w.kind, w.function.split("::", 1)[1], w.name) for w in found}


# ── the live tree ───────────────────────────────────────────────────────────


def test_live_tree_is_clean() -> None:
    verdict = judge(REPO_ROOT, EXEMPT)
    assert verdict.clean, "\n".join(report(verdict))


def test_every_exemption_carries_its_reason() -> None:
    assert all(reason.strip() for reason in EXEMPT.values())


def test_the_script_says_the_same() -> None:
    done = subprocess.run([sys.executable, str(REPO_ROOT / "scripts" / "check-kept-work")],
                          capture_output=True, text=True)
    assert (done.returncode, done.stdout) == (0, "")


# ── what writes state ───────────────────────────────────────────────────────


def test_module_level_containers() -> None:
    tree = __import__("ast").parse(textwrap.dedent("""
        import collections
        A = {}
        B: dict = dict()
        C = collections.OrderedDict()
        D = [x for x in ()]
        E: int = 1
        F = make()
        G: list
        H = obj.method()
        I = factory()()
    """))
    assert module_containers(tree) == {"A", "B", "C", "D"}


def test_a_global_assigned() -> None:
    assert _writes("""
        _X = None
        def f():
            global _X
            _X = 1
        def g():
            _X = 2
    """) == {("global", "f", "_X")}


def test_a_module_container_changed_in_place() -> None:
    assert _writes("""
        _C = {}
        _L = []
        def store(k):
            _C[k] = 1
        def nested(k):
            _C[k][0] = 1
        def drop(k):
            del _C[k]
        def grow():
            _L.append(1)
        def grow_inner(k):
            _C[k].append(1)
        def merge():
            global _C
            _C |= {}
        def deep(k):
            _C[k].items[0] = 1
    """) == {("mutate", "store", "_C"), ("mutate", "nested", "_C"), ("mutate", "deep", "_C"),
             ("mutate", "drop", "_C"), ("mutate", "grow", "_L"),
             ("mutate", "grow_inner", "_C"), ("global", "merge", "_C")}


def test_a_local_of_the_same_name_or_another_object_is_not_module_state() -> None:
    assert _writes("""
        _C = {}
        def shadowed():
            _C = {}
            _C["k"] = 1
            _C.clear()
            del _C["k"]
        def other(self, d, x):
            d["k"] = 1
            self.items.append(1)
            del x
            del d["k"]
            _C.get("k")
            print(_C)
            make().append(1)
        def augmented():
            total = 0
            total += 1
    """) == set()


def test_an_augmented_container_name_without_global() -> None:
    """``_L += x`` in a function binds a local (and fails when run); the scan
    reports it so the author looks."""
    assert _writes("""
        _L = []
        def f():
            _L += [1]
    """) == {("mutate", "f", "_L")}


@pytest.mark.parametrize("test", [
    "self._x is None", "not self._x", "not hasattr(self, '_x')", "'_x' not in self.__dict__",
])
def test_a_lazy_attribute(test: str) -> None:
    assert _writes(f"""
        class K:
            def get(self):
                if {test}:
                    self._x = compute()
                return self._x
    """) == {("lazy", "K.get", "_x")}


@pytest.mark.parametrize("test", [
    "self._x == 1", "self._x is 1", "other._x is None", "not other._x", "not hasattr(self)",
    "not hasattr(self, name)", "0 < self._x < 2", "'_x' not in self.other", "self._x in {}",
    "not callable(self)", "1 not in self.__dict__",
])
def test_not_a_lazy_attribute(test: str) -> None:
    assert _writes(f"""
        class K:
            def get(self):
                if {test}:
                    self._x = compute()
    """) == set()


def test_an_if_that_sets_another_attribute_is_not_lazy() -> None:
    assert _writes("""
        class K:
            def get(self):
                if self._x is None:
                    self._y = 1
                    other._x = 1
    """) == set()


def test_cached_property_in_both_spellings_and_other_decorators() -> None:
    assert _writes("""
        import functools
        from functools import cached_property
        class K:
            @cached_property
            def a(self):
                return 1
            @functools.cached_property
            def b(self):
                return 2
            @functools.lru_cache(maxsize=1)
            def c(self):
                return 3
            @staticmethod
            def d():
                return 4
    """) == {("cached_property", "K.a", "a"), ("cached_property", "K.b", "b")}


def test_nested_and_async_functions_are_judged_on_their_own() -> None:
    assert _writes("""
        _R = {}
        def register(name):
            def decorator(fn):
                _R[name] = fn
                return fn
            return decorator
        async def fill():
            _R["a"] = 1
        def outer():
            class Inner:
                def m(self):
                    _R["b"] = 1
            return lambda: _R.clear()
    """) == {("mutate", "register.<locals>.decorator", "_R"), ("mutate", "fill", "_R"),
             ("mutate", "outer.<locals>.Inner.m", "_R")}


def test_a_closure_a_broader_scope_fixture_hands_out() -> None:
    """The fixture lives across tests, so what its closure fills is handed
    to every later test (INV-kazud): a container the fixture binds, changed
    in place, and a name the closure declares ``nonlocal`` and assigns."""
    assert _writes("""
        import pytest
        @pytest.fixture(scope="module")
        def survey(tmp_path_factory):
            cache: dict = {}
            def run(name):
                if name not in cache:
                    cache[name] = len(name)
                return cache[name]
            return run
        @fixture(scope="session")
        def seen():
            names = set()
            last = None
            def note(name):
                nonlocal last
                names.add(name)
                last = name
            return note
        @pytest.fixture(scope=lambda **kw: "module")
        def chosen():
            kept = []
            def add():
                def deeper():
                    kept.append(1)
                deeper()
            return add
    """) == {("closure", "survey.<locals>.run", "cache"), ("closure", "seen.<locals>.note", "names"),
             ("closure", "seen.<locals>.note", "last"),
             ("closure", "chosen.<locals>.add.<locals>.deeper", "kept")}


def test_a_closure_that_does_not_outlive_one_test_is_not_kept_work() -> None:
    """A function-scoped fixture (bare, or scope='function'), a plain helper,
    and a closure that rebinds the name or writes its own local."""
    assert _writes("""
        import pytest
        @pytest.fixture
        def bare():
            cache = {}
            def run(name):
                cache[name] = 1
            return run
        @pytest.fixture(scope="function")
        def per_test():
            cache = {}
            def run(name):
                cache[name] = 1
            return run
        def helper():
            cache = {}
            def run(name):
                cache[name] = 1
            return run
        @pytest.fixture(scope="module")
        def rebinds():
            cache = {}
            other = 1
            def run(name):
                cache = {}
                cache[name] = 1
                mine = []
                mine.append(other)
            return run
    """) == set()


# ── what marks a fill ───────────────────────────────────────────────────────


def _marked(source: str) -> set[str]:
    marked = scan_source(textwrap.dedent(source), "m.py").marked
    return {key.split("::", 1)[1] for key in marked}


def test_a_fill_is_marked_in_either_spelling() -> None:
    assert _marked("""
        from hypergumbo_core import kept_work
        from hypergumbo_core.kept_work import filling
        _C = {}
        def a(k):
            with kept_work.filling(a, k):
                _C[k] = 1
        async def b(k):
            async with filling(b, k):
                _C[k] = 1
        def c(k):
            with open(k) as f, lock:
                _C[k] = f
        def d(k):
            with other.filling(d, k):
                _C[k] = 1
        def e(k):
            with lock:
                _C[k] = 1
        def f(k):
            with make().filling(f):
                pass
        def g(k):
            def inner():
                with kept_work.filling(g):
                    pass
    """) == {"a", "b", "g.<locals>.inner"}


def test_marks_a_fill_reads_one_function() -> None:
    import ast
    fn = ast.parse("def f():\n    with kept_work.filling(f):\n        pass\n").body[0]
    assert isinstance(fn, ast.FunctionDef) and marks_a_fill(fn)


# ── the verdict ─────────────────────────────────────────────────────────────


def _tree(root: Path, files: dict[str, str]) -> Path:
    for rel, text in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(textwrap.dedent(text))
    return root


_KEEPS = """
    from hypergumbo_core import kept_work
    _C = {}
    def marked(k):
        kept_work.used(marked, k)
        with kept_work.filling(marked, k):
            _C[k] = 1
    def fills_only(k):
        with kept_work.filling(fills_only, k):
            _C[k] = 1
    def unmarked(k):
        _C[k] = 1
    def clears():
        _C.clear()
"""


def test_the_three_findings(tmp_path: Path) -> None:
    root = _tree(tmp_path, {"packages/p/src/p/m.py": _KEEPS})
    m = "packages/p/src/p/m.py"
    verdict = judge(root, {f"{m}::clears": "empties it", f"{m}::marked": "both",
                           f"{m}::gone": "renamed since"})
    assert verdict == Verdict(unmarked=(f"{m}::unmarked",),
                              marked_and_exempt=(f"{m}::marked",), stale=(f"{m}::gone",),
                              fills_without_use=(f"{m}::fills_only",))
    assert not verdict.clean
    lines = report(verdict)
    assert [line.split(": ", 1)[1] for line in lines[:4]] == [
        f"{m}::unmarked", f"{m}::marked", f"{m}::gone", f"{m}::fills_only"]
    assert lines[4].startswith("Fix: ")


def test_a_fill_without_a_use_is_a_finding_alone(tmp_path: Path) -> None:
    """A test that is only handed the kept value records no use of it: the
    fill's rows would reach the test that filled and no other."""
    root = _tree(tmp_path, {"packages/p/src/p/m.py": """
        from hypergumbo_core import kept_work
        from hypergumbo_core.kept_work import filling, used
        _C = {}
        def bare(k):
            used(bare, k)
            with filling(bare, k):
                _C[k] = 1
        def fills_only(k):
            with kept_work.filling(fills_only, k):
                _C[k] = 1
        def other_used(k):
            other.used(other_used, k)
            make().used(k)
            print(k)
            with kept_work.filling(other_used, k):
                _C[k] = 1
    """})
    m = "packages/p/src/p/m.py"
    verdict = judge(root, {})
    assert verdict.fills_without_use == (f"{m}::fills_only", f"{m}::other_used")
    assert verdict.unmarked == () and not verdict.clean


def test_clean_says_nothing(tmp_path: Path) -> None:
    root = _tree(tmp_path, {"packages/p/src/p/m.py": _KEEPS})
    m = "packages/p/src/p/m.py"
    verdict = judge(root, {f"{m}::clears": "empties it", f"{m}::unmarked": "a reason",
                           f"{m}::fills_only": "a reason"})
    assert verdict.fills_without_use == (f"{m}::fills_only",)
    verdict = Verdict(unmarked=(), marked_and_exempt=(), stale=(), fills_without_use=())
    assert verdict.clean and report(verdict) == []


def test_what_is_read(tmp_path: Path) -> None:
    root = _tree(tmp_path, {
        "packages/p/src/p/m.py": "", "packages/p/tests/test_m.py": "",
        "packages/p/tests/fixtures/repo/x.py": "", "tests/test_root.py": "",
        "tests/fixtures/y.py": "", "scripts/tool.py": "", "packages/p/docs/conf.py": "",
    })
    assert [p.relative_to(root).as_posix() for p in scanned_files(root)] == [
        "packages/p/src/p/m.py", "packages/p/tests/test_m.py", "tests/test_root.py"]
