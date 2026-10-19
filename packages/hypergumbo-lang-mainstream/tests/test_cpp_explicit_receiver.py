# SPDX-License-Identifier: AGPL-3.0-or-later
"""A C++ member call on an explicit receiver resolves on the receiver's type (WI-lamij).

``impl_->Generate(a, b)`` took ``local_symbols["Generate"]`` -- whichever
same-file method of ANY class registered last -- so the pimpl idiom
(``X::Generate`` forwarding to ``impl_->Generate``) became a false SELF-call:
990 such sites on sherpa-onnx. The receiver is now typed where the source
declares it (a local, a parameter, a field of the caller's class), and an
untyped receiver is never bound by short name nor read as implicit ``this``.
"""
# covers: packages/hypergumbo-lang-mainstream/src/hypergumbo_lang_mainstream/cpp.py
from __future__ import annotations

from pathlib import Path

from hypergumbo_lang_mainstream.cpp import analyze_cpp

_CLASSES = """\
#include <memory>
class Impl { public: int Generate(int a, int b); };
class Model { public: int Forward(int x); };
class Other { public: int Forward(int x); int Generate(int a, int b); };
class Base { public: void Hook(); };
class Derived : public Base { };
class Tts {
 public:
  int Generate(int a, int b) const;
  int Run(int x);
  int Forward(int x);
 private:
  std::unique_ptr<Impl> impl_;
  Model* model_;
};
int Impl::Generate(int a, int b) { return a + b; }
int Model::Forward(int x) { return x; }
int Other::Forward(int x) { return x; }
int Other::Generate(int a, int b) { return a; }
void Base::Hook() { }
"""


def _calls(tmp_path: Path, body: str) -> dict[str, list[tuple[str, dict]]]:
    (tmp_path / "t.cc").write_text(_CLASSES + body)
    result = analyze_cpp(tmp_path)
    names = {s.id: s.name for s in result.symbols}
    out: dict[str, list[tuple[str, dict]]] = {}
    for e in result.edges:
        if e.edge_type == "calls" and e.src in names:
            out.setdefault(names[e.src], []).append((names.get(e.dst, e.dst), dict(e.meta or {})))
    return out


def test_a_pimpl_forward_is_not_a_self_call(tmp_path: Path) -> None:
    calls = _calls(tmp_path, (
        "int Tts::Generate(int a, int b) const { return impl_->Generate(a, b); }\n"
        "int Tts::Run(int x) { return model_->Forward(x); }\n"
    ))
    assert [t for t, _ in calls["Tts::Generate"]] == ["Impl::Generate"]
    assert [t for t, _ in calls["Tts::Run"]] == ["Model::Forward"]


def test_a_local_or_parameter_names_its_class(tmp_path: Path) -> None:
    calls = _calls(tmp_path, (
        "int f() { Model m; return m.Forward(1); }\n"
        "int g(Model* p, const Other& o) { return p->Forward(1) + o.Forward(2); }\n"
    ))
    assert [t for t, _ in calls["f"]] == ["Model::Forward"]
    assert sorted(t for t, _ in calls["g"]) == ["Model::Forward", "Other::Forward"]


def test_an_inherited_member_is_hinted_with_the_receiver_type(tmp_path: Path) -> None:
    calls = _calls(tmp_path, "void h(Derived d) { d.Hook(); }\n")
    ((target, meta),) = calls["h"]
    assert target.endswith(":0-0:Hook:unresolved")
    assert meta.get("receiver_type_hint") == "Derived"
    assert meta.get("call_construct") == "method"


def test_an_untyped_receiver_is_not_bound_by_short_name(tmp_path: Path) -> None:
    """Not to the caller's own class (the pimpl self-call), and not stamped
    ``enclosing_class``: the call is not on implicit ``this``."""
    calls = _calls(tmp_path, (
        "int Tts::Forward(int x) { auto w = make(); return w->Forward(x); }\n"
    ))
    targets = dict(calls["Tts::Forward"])
    assert "Tts::Forward" not in targets and "Model::Forward" not in targets
    # The module slot is the file's #include set (WI-rupik), here ``memory``.
    (meta,) = [m for t, m in targets.items() if t.endswith(":0-0:Forward:unresolved")]
    assert meta.get("call_construct") == "method"
    assert meta.get("enclosing_class") is None


def test_implicit_and_explicit_this_still_bind_the_own_class(tmp_path: Path) -> None:
    calls = _calls(tmp_path, (
        "int Tts::Forward(int x) { return x; }\n"
        "int Tts::Run(int x) { return Forward(x) + this->Forward(x); }\n"
    ))
    assert [t for t, _ in calls["Tts::Run"]] == ["Tts::Forward", "Tts::Forward"]


def test_a_smart_pointer_s_own_member_is_not_the_pointee_s(tmp_path: Path) -> None:
    """``p.reset()`` is the unique_ptr's; ``p->Generate()`` is the Impl's."""
    calls = _calls(tmp_path, (
        "int k(std::unique_ptr<Impl> p) { p.reset(); return p->Generate(1, 2); }\n"
    ))
    targets = [t for t, _ in calls["k"]]
    assert "Impl::Generate" in targets
    assert not any("reset" in t and "Impl" in t for t in targets)


def test_a_wrapped_declarator_and_an_inline_member_type_the_receiver(tmp_path: Path) -> None:
    """A function returning a pointer wraps its ``function_declarator``; a
    member defined in its class body finds its fields through that body."""
    calls = _calls(tmp_path, (
        "Model* make(Impl* p) { p->Generate(1, 2); return nullptr; }\n"
        "class Holder {\n"
        "  Model* m_;\n"
        "  int Go() { return m_->Forward(1); }\n"
        "};\n"
    ))
    assert [t for t, _ in calls["make"]] == ["Impl::Generate"]
    assert [t for t, _ in calls["Holder::Go"]] == ["Model::Forward"]
