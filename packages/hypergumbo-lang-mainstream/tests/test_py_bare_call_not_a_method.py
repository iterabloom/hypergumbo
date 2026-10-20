# SPDX-License-Identifier: AGPL-3.0-or-later
"""A bare-name call never binds a method (WI-barip).

py.py registered every METHOD in the flat, last-write-wins ``symbol_by_name``
under its BARE short name, so ``open(self.name)`` bound ``ContentFile.open``
(hiding a builtin file read from io-boundary and taint), ``list(v)`` bound
``ExportersMixin.list``, and a module function overwritten by a same-named
method (``to_language``) was lost. Python scoping cannot reach a method from a
function body -- a class body is not an enclosing scope -- so methods are now
keyed by their qualified name (``Class.method``) only. The two consumers that
used the bare key with a qualified-name check (the single-file property-getter
fallback, ``@Registry.register``) read the qualified key; a decorator defined
earlier in the same class body (``@deco`` inside ``class A``), which Python's
class-body namespace DOES reach, is looked up there first.
"""
# covers: packages/hypergumbo-lang-mainstream/src/hypergumbo_lang_mainstream/py.py
from __future__ import annotations

from pathlib import Path

from hypergumbo_lang_mainstream.py import analyze_python, extract_nodes


def _edges(result, edge_type: str = "calls") -> dict[str, list[str]]:
    names = {s.id: s.name for s in result.symbols}
    out: dict[str, list[str]] = {}
    for e in result.edges:
        if e.edge_type == edge_type and e.src in names:
            out.setdefault(names[e.src], []).append(names.get(e.dst, e.dst))
    return out


_SRC = '''\
def to_language(x):
    return x


class ContentFile:
    def open(self, mode="r"):
        return self

    def to_language(self, x):
        return x


class File:
    def read_all(self):
        with open(self.name) as fh:
            return list(fh)


def convert(x):
    return to_language(x)
'''


def test_a_builtin_call_does_not_bind_a_same_named_method(tmp_path: Path) -> None:
    (tmp_path / "m.py").write_text(_SRC)
    calls = _edges(analyze_python(tmp_path))
    assert "ContentFile.open" not in calls.get("File.read_all", [])


def test_a_module_function_is_not_overwritten_by_a_later_method(tmp_path: Path) -> None:
    (tmp_path / "m.py").write_text(_SRC)
    calls = _edges(analyze_python(tmp_path))
    assert calls["convert"] == ["to_language"]


def test_decorators_on_a_class_method_still_resolve(tmp_path: Path) -> None:
    (tmp_path / "r.py").write_text(
        "class Registry:\n"
        "    @staticmethod\n"
        "    def register(f):\n"
        "        return f\n"
        "\n"
        "    def deco(f):\n"
        "        return f\n"
        "\n"
        "    @deco\n"
        "    def wrapped(self):\n"
        "        pass\n"
        "\n"
        "\n"
        "@Registry.register\n"
        "def handler():\n"
        "    pass\n"
    )
    decorated = _edges(analyze_python(tmp_path), "decorated_by")
    assert decorated["handler"] == ["Registry.register"]
    assert decorated["Registry.wrapped"] == ["Registry.deco"]


def test_a_property_getter_resolves_in_single_file_extraction(tmp_path: Path) -> None:
    """``extract_nodes`` has no cross-file index; the getter is found by its
    qualified name. The read emits the hinted unresolved edge the
    inherited_calls linker resolves (WI-gubar)."""
    path = tmp_path / "p.py"
    path.write_text(
        "class C:\n"
        "    @property\n"
        "    def size(self):\n"
        "        return 1\n"
        "\n"
        "\n"
        "def use(c: C):\n"
        "    return c.size\n"
    )
    result = extract_nodes(path)
    use = next(s.id for s in result.symbols if s.name == "use")
    hints = [(e.meta or {}).get("receiver_type_hint") for e in result.edges
             if e.src == use and e.dst.endswith(":size:unresolved")]
    assert hints == ["C"]
