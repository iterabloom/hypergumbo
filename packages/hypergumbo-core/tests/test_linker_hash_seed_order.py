# SPDX-License-Identifier: AGPL-3.0-or-later
"""Two linkers that kept "the first" of a set now keep the same one every run.

Both sites iterated a ``set`` and then let a first-writer-wins dedup choose,
so the survivor followed the per-process str-hash salt:

- ``ruby_ffi``: the C files holding ``rb_define_method`` registrations were a
  ``set``; the ``("rb_define", name)`` dedup kept the first file's
  registration, so the edge's ``line`` (and with it the edge id) differed
  between runs when two C files register a function of one name.
- ``di_resolution``: ``_SOURCE_EXTENSIONS`` was ``list(set | set)``, which is
  the order files are scanned in; ``_create_di_edges`` keeps the first binding
  of an (interface method, implementation method) pair, so an interface bound
  in a ``.java`` and a ``.kt`` file got ``confidence`` 0.85 or 0.90 and one
  ``framework_dispatch`` or the other.

One process has one hash salt, so the behavioural tests run each linker in
four processes under fixed, distinct ``PYTHONHASHSEED`` values and require one
answer. Before the fix the four disagreed (ruby_ffi: lines 2, 4, 2, 3;
di_resolution: ``regex:.kt`` once, ``annotation:.java`` three times).
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

from hypergumbo_core.linkers import di_resolution

_SEEDS = ("0", "1", "2", "3")

_RUBY_DRIVER = """
import json, sys
from pathlib import Path
from hypergumbo_core.ir import Span, Symbol
from hypergumbo_core.linkers.ruby_ffi import link_ruby_ffi

root = Path(sys.argv[1])
symbols = []
for i, stem in enumerate(sys.argv[2].split(","), start=1):
    (root / f"{stem}.c").write_text(
        "\\n" * i
        + 'void Init(void) { rb_define_method(cObj, "run", obj_run, 0); }\\n'
        + "static VALUE obj_run(VALUE self) { return self; }\\n"
    )
    symbols.append(Symbol(
        id=f"c:{stem}.c:{i + 2}-{i + 2}:obj_run:function", name="obj_run",
        kind="function", path=f"{stem}.c", language="c",
        span=Span(start_line=i + 2, end_line=i + 2, start_col=0, end_col=0),
    ))
result = link_ruby_ffi(root, [], symbols, [])
print(json.dumps([[e.src, e.dst, e.line, e.id] for e in result.edges]))
"""

_DI_DRIVER = """
import json, sys
from pathlib import Path
from hypergumbo_core.ir import AnalysisRun, Span, Symbol
from hypergumbo_core.linkers import di_resolution as di

root = Path(sys.argv[1])
(root / "Service.java").write_text(
    "@ImplementedBy(ServiceImpl.class)\\npublic interface Service { void run(); }\\n"
)
(root / "Module.scala").write_text(
    "class M { def configure() = bind(Service.class).to(ServiceImpl.class) }\\n"
)
(root / "Wiring.kt").write_text("val m = module { single<Service> { ServiceImpl() } }\\n")

def method(cls, line):
    return Symbol(
        id=f"java:{cls}.java:{line}-{line}:{cls}.run:method", name=f"{cls}.run",
        kind="method", path=f"{cls}.java", language="java", meta={"class": cls},
        span=Span(start_line=line, end_line=line, start_col=0, end_col=0),
    )

bindings = di.extract_bindings_from_source(root)
run = AnalysisRun.create(pass_id=di.PASS_ID, version="1")
edges = di._create_di_edges(bindings, [method("Service", 2), method("ServiceImpl", 3)], run)
print(json.dumps({
    "bindings": [b.source for b in bindings],
    "edges": [[e.meta["framework_dispatch"], e.confidence, e.id] for e in edges],
}))
"""


def _under_each_seed(driver: str, *args: str) -> list[Any]:
    outputs = []
    for seed in _SEEDS:
        env = {**os.environ, "PYTHONHASHSEED": seed}
        proc = subprocess.run(
            [sys.executable, "-c", driver, *args],
            capture_output=True, text=True, env=env, check=True,
        )
        outputs.append(json.loads(proc.stdout))
    return outputs


def test_ruby_rb_define_edge_is_the_first_files_under_four_hash_seeds(
    tmp_path: Path,
) -> None:
    """Six C files register ``obj_run``; the kept registration is alpha.c's."""
    stems = "alpha,bravo,charlie,delta,echo,foxtrot"

    outputs = _under_each_seed(_RUBY_DRIVER, str(tmp_path), stems)

    assert all(out == outputs[0] for out in outputs[1:]), outputs
    ((src, dst, line, _edge_id),) = outputs[0]
    assert src == dst == "c:alpha.c:3-3:obj_run:function"
    assert line == 2  # alpha.c: one leading blank line, then the registration


def test_di_edge_keeps_one_binding_under_four_hash_seeds(tmp_path: Path) -> None:
    """One interface bound in .java, .kt and .scala files: one edge, one source."""
    outputs = _under_each_seed(_DI_DRIVER, str(tmp_path))

    assert all(out == outputs[0] for out in outputs[1:]), outputs
    assert outputs[0]["bindings"] == ["annotation:.java", "regex:.kt", "regex:.scala"]
    ((dispatch, confidence, _edge_id),) = outputs[0]["edges"]
    assert (dispatch, confidence) == ("annotation:.java", 0.85)


def test_di_source_extensions_are_scanned_in_sorted_order() -> None:
    """Nine suffixes: a set comes out sorted in one process in 362880."""
    assert di_resolution._SOURCE_EXTENSIONS == sorted(di_resolution._SOURCE_EXTENSIONS)
    assert len(di_resolution._SOURCE_EXTENSIONS) == 9
    assert di_resolution._SOURCE_GLOBS == [
        f"*{ext}" for ext in di_resolution._SOURCE_EXTENSIONS
    ]
