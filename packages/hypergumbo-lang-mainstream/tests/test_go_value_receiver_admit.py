# SPDX-License-Identifier: AGPL-3.0-or-later
"""WI-surid: a call on a VALUE (``x.F()``) reaches a method or a func-typed field.

Go's selector on a value names a method of the value's type or a field of it;
it cannot name a package-level function, a struct or a named type. When the
receiver's type is unknown, the analyzer fell through its guards to the
repo-wide short-name lookup with no filter, and the sole in-repo candidate of
that name was bound whatever its kind: cert-manager's 306
``corev1.SchemeGroupVersion.WithResource("secrets")`` (a method on an external
package variable) went to ``func WithResource`` in ``pkg/logs``. The shared
resolver's ``admit`` hook (WI-jomik, #2004) is the filter; this is its Go
value-receiver adopter, the rule #2006 gave Rust.

A field is admitted only when its declared type is a function type: the
registry ``field_type_registry`` holds (the field symbol's ``signature`` is
display-only, ADR-0058). WI-jalal's review found 43 of 78 field binds on
alertmanager were calls through such fields; the rest were data fields.
"""

from __future__ import annotations

from pathlib import Path

import pytest

_FILES = {
    "go.mod": "module example.com/m\n\ngo 1.21\n",
    "pkg/logs/logs.go": (
        "package logs\n\n"
        "func WithResource(r string) {}\n\n"
        "type Widget struct {\n"
        "\tName    string\n"
        "\tHandler func(string) string\n"
        "}\n\n"
        "type Option func(*Widget)\n\n"
        "func (w *Widget) Frob() {}\n"
    ),
    "app/app.go": (
        "package app\n\n"
        "import (\n"
        '\ttmpltext "text/template"\n\n'
        '\tcorev1 "k8s.io/api/core/v1"\n\n'
        '\t"example.com/m/pkg/logs"\n'
        ")\n\n"
        "func UseFunc() {\n"
        '\tcorev1.SchemeGroupVersion.WithResource("secrets")\n'
        "}\n\n"
        "func UseStruct() {\n"
        "\tcorev1.SchemeGroupVersion.Widget()\n"
        "}\n\n"
        "func UseType() {\n"
        '\tcorev1.SchemeGroupVersion.Option("missingkey=error")\n'
        "}\n\n"
        "func UseDataField() {\n"
        "\tcorev1.SchemeGroupVersion.Name()\n"
        "}\n\n"
        "func UseFuncField() {\n"
        '\tcorev1.SchemeGroupVersion.Handler("x")\n'
        "}\n\n"
        "func UseMethod() {\n"
        "\tcorev1.SchemeGroupVersion.Frob()\n"
        "}\n\n"
        "func UseStdlibResult() {\n"
        '\ttmpltext.New("").Frob()\n'
        "}\n\n"
        "func UsePackage() {\n"
        '\tlogs.WithResource("x")\n'
        "}\n"
    ),
}


@pytest.fixture(scope="module")
def calls(tmp_path_factory: pytest.TempPathFactory) -> dict[str, list[tuple[str, str, bool]]]:
    """``{caller: [(dst name, dst kind or 'unresolved', is_resolved)]}``."""
    pytest.importorskip("tree_sitter_go")
    repo = tmp_path_factory.mktemp("surid")
    for name, text in _FILES.items():
        (repo / name).parent.mkdir(parents=True, exist_ok=True)
        (repo / name).write_text(text)
    with pytest.MonkeyPatch.context() as mp:
        mp.setenv("XDG_CACHE_HOME", str(repo / "cache"))
        from hypergumbo_lang_mainstream.go import analyze_go
        result = analyze_go(repo)
    by_id = {s.id: s for s in result.symbols}
    out: dict[str, list[tuple[str, str, bool]]] = {}
    for e in result.edges:
        if e.edge_type != "calls" or e.src not in by_id:
            continue
        dst = by_id.get(e.dst)
        row = (dst.name, dst.kind, True) if dst else (e.dst.split(":")[-2], "unresolved", False)
        out.setdefault(by_id[e.src].name, []).append(row)
    return out


@pytest.mark.parametrize("caller,name,kind", [
    ("UseFunc", "WithResource", "function"),
    ("UseStruct", "Widget", "struct"),
    ("UseType", "Option", "type"),
    ("UseDataField", "Widget.Name", "field"),
])
def test_a_value_receiver_does_not_bind_an_unreachable_kind(
    calls: dict, caller: str, name: str, kind: str,
) -> None:
    rows = calls.get(caller, [])
    assert (name, kind, True) not in rows, rows
    assert any(r[0] == name.split(".")[-1] and not r[2] for r in rows), rows


@pytest.mark.parametrize("caller,name,kind", [
    ("UseFuncField", "Widget.Handler", "field"),
    ("UseMethod", "Widget.Frob", "method"),
    ("UsePackage", "WithResource", "function"),
])
def test_a_reachable_kind_still_binds(
    calls: dict, caller: str, name: str, kind: str,
) -> None:
    """Controls: a func-typed field and a method on a value, and a package
    function on the package, resolve as before."""
    assert (name, kind, True) in calls.get(caller, []), calls.get(caller)


def test_a_stdlib_call_result_receiver_stays_unresolved(calls: dict) -> None:
    """Not this arm: a receiver that is a stdlib call's result is external
    before any lookup, so ``Frob`` is unresolved with or without the admit."""
    rows = calls.get("UseStdlibResult", [])
    assert ("Frob", "unresolved", False) in rows, rows
    assert not any(r[2] for r in rows), rows
