# SPDX-License-Identifier: AGPL-3.0-or-later
# covers: packages/hypergumbo-core/src/hypergumbo_core/taxonomy.py
"""Every template a view-template linker mints carries a language on the axis.

A template node is a real file of the tree that no analyzer reads: ADR-0031's
Class A, whose ``Symbol.language`` (and the id's language slot, ADR-0034) is
the file's own template language. The view-template linkers stamp it from an
extension table per framework. The Phoenix, Spring and Django tables held
seven labels the language axis (``catalog.all_known_languages``) did not:
``eex``, ``heex``, ``leex``, ``ftl``, ``vm``, ``jsp``, ``jinja``, so each such
template was an ``axis_conformance`` error. They join the axis the way the
Rails labels ``erb`` / ``haml`` / ``slim`` did (WI-novob): a taxonomy
``LanguageSpec`` with the CONFIG role, under the label the linker already
writes, so no template's id or stable id changes.

Each test runs the real linker over a fixture holding one template per entry
of that linker's own table (so a label added to a table later is reached by
the same test), asserts the template was minted (reach), and then that its
language is on the axis and is its id's language slot. The controls are the
labels that were on the axis before (``html``, ``erb``): they keep their
value.
"""
from __future__ import annotations

from pathlib import Path

from hypergumbo_core.catalog import all_known_languages
from hypergumbo_core.ir import Span, Symbol
from hypergumbo_core.linkers import view_template as rails
from hypergumbo_core.linkers import view_template_laravel as laravel
from hypergumbo_core.linkers import view_template_phoenix as phoenix
from hypergumbo_core.linkers import view_template_spring as spring
from hypergumbo_core.linkers.registry import LinkerContext
from hypergumbo_core.linkers.view_template_django import link_view_template_django
from hypergumbo_core.taxonomy import FileRole, LANGUAGES, get_language

#: The seven labels that were off the axis before this change.
_JOINED = ("eex", "heex", "leex", "ftl", "vm", "jsp", "jinja")


def _write(root: Path, rel: str, body: str = "x") -> None:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(body)


def _sym(lang: str, path: str, name: str, kind: str, start: int, end: int,
         meta: dict | None = None) -> Symbol:
    return Symbol(
        id=f"{lang}:{path}:{start}-{end}:{name}:{kind}",
        name=name, kind=kind, language=lang, path=path,
        span=Span(start_line=start, end_line=end, start_col=0, end_col=0),
        origin=lang, meta=meta,
    )


def _templates_by_path(symbols: list[Symbol]) -> dict[str, Symbol]:
    found = {s.path: s for s in symbols if s.kind == "template"}
    assert found, "reach: the fixture minted no template"
    return found


def _assert_on_axis(templates: dict[str, Symbol]) -> None:
    known = all_known_languages()
    for path, t in templates.items():
        assert t.language in known, (path, t.language)
        assert t.id.split(":", 1)[0] == t.language, (path, t.id)


def test_phoenix_templates_carry_axis_languages(tmp_path: Path) -> None:
    ctrl = "lib/my_app_web/controllers/user_controller.ex"
    syms = [_sym("elixir", ctrl, "MyAppWeb.UserController", "module", 1, 50)]
    expected: dict[str, str] = {}
    for i, (ext, lang) in enumerate(phoenix._EXTENSION_LANGUAGE.items()):
        rel = f"lib/my_app_web/templates/user/a{i}{ext}"
        _write(tmp_path, rel)
        expected[rel] = lang
        syms.append(_sym("elixir", ctrl, f"MyAppWeb.UserController.a{i}",
                         "function", 5 + i, 5 + i))
    result = phoenix.link_view_template_phoenix(
        LinkerContext(repo_root=tmp_path, symbols=syms))
    templates = _templates_by_path(result.symbols)
    assert {p: t.language for p, t in templates.items()} == expected
    assert {"eex", "heex", "leex"} <= set(expected.values())
    _assert_on_axis(templates)


def test_spring_templates_carry_axis_languages(tmp_path: Path) -> None:
    src = "src/main/java/com/ex/UserController.java"
    body = ["@Controller", "public class UserController {"]
    syms: list[Symbol] = []
    expected: dict[str, str] = {}
    n = 0
    for root, exts in spring._TEMPLATE_ROOTS:
        for ext, lang in exts:
            n += 1
            rel = f"{root}/users/v{n}{ext}"
            _write(tmp_path, rel)
            expected[rel] = lang
            start = len(body) + 1
            body += [f'    @GetMapping("/v{n}")', f"    public String v{n}() {{",
                     f'        return "users/v{n}";', "    }"]
            syms.append(_sym(
                "java", src, f"com.ex.UserController.v{n}", "method",
                start + 1, start + 3,
                meta={"decorators": [
                    {"name": "GetMapping", "args": [f"/v{n}"], "kwargs": {}}]},
            ))
    body.append("}")
    _write(tmp_path, src, "\n".join(body) + "\n")
    syms.insert(0, _sym(
        "java", src, "com.ex.UserController", "class", 1, len(body),
        meta={"decorators": [{"name": "Controller", "args": [], "kwargs": {}}]},
    ))
    result = spring.link_view_template_spring(
        LinkerContext(repo_root=tmp_path, symbols=syms))
    templates = _templates_by_path(result.symbols)
    assert {p: t.language for p, t in templates.items()} == expected
    assert {"ftl", "vm", "jsp", "html"} <= set(expected.values())
    _assert_on_axis(templates)


def test_django_templates_carry_axis_languages(tmp_path: Path) -> None:
    exts = {".html": "html", ".jinja": "jinja", ".jinja2": "jinja", ".j2": "jinja"}
    lines = ["from django.shortcuts import render", ""]
    syms: list[Symbol] = []
    for i, ext in enumerate(exts):
        _write(tmp_path, f"templates/t{i}{ext}")
        start = len(lines) + 1
        lines += [f"def v{i}(request):",
                  f'    return render(request, "t{i}{ext}")', ""]
        syms.append(_sym("python", "app/views.py", f"v{i}", "function",
                         start, start + 1))
    _write(tmp_path, "app/views.py", "\n".join(lines))
    result = link_view_template_django(
        LinkerContext(repo_root=tmp_path, symbols=syms))
    templates = _templates_by_path(result.symbols)
    assert {p: t.language for p, t in templates.items()} == {
        f"templates/t{i}{ext}": lang for i, (ext, lang) in enumerate(exts.items())
    }
    _assert_on_axis(templates)


def test_rails_templates_keep_their_axis_languages(tmp_path: Path) -> None:
    """Control: the Rails labels were on the axis already and do not change."""
    ctrl = _sym("ruby", "app/controllers/users_controller.rb", "UsersController",
                "class", 1, 50, meta={"base_classes": ["ApplicationController"]})
    syms = [ctrl]
    expected: dict[str, str] = {}
    for i, (ext, lang) in enumerate(rails._EXTENSION_LANGUAGE.items()):
        rel = f"app/views/users/a{i}{ext}"
        _write(tmp_path, rel)
        expected[rel] = lang
        syms.append(_sym("ruby", "app/controllers/users_controller.rb",
                         f"UsersController#a{i}", "method", 5 + i, 5 + i))
    result = rails.link_view_templates(tmp_path, syms, [])
    templates = _templates_by_path(result.symbols)
    assert {p: t.language for p, t in templates.items()} == expected
    assert "erb" in expected.values()
    _assert_on_axis(templates)


def test_laravel_table_labels_are_on_the_axis() -> None:
    """Laravel's labels (``blade``, ``php``) were on the axis already."""
    known = all_known_languages()
    assert [lang for _, lang in laravel._TEMPLATE_EXTENSIONS
            if lang not in known] == []


def test_joined_labels_are_config_languages_of_their_extensions() -> None:
    """Each joined label is a CONFIG LanguageSpec (the WI-novob precedent:
    a template is markup with embedded logic, and no analyzer reads it), and
    discovery labels a template file with the same word the linker stamps."""
    for lang in _JOINED:
        assert LANGUAGES[lang].roles == FileRole.CONFIG, lang
    for path, lang in (
        ("show.html.eex", "eex"), ("show.text.eex", "eex"),
        ("index.html.heex", "heex"), ("edit.html.leex", "leex"),
        ("v.ftl", "ftl"), ("v.ftlh", "ftl"), ("v.vm", "vm"), ("v.jsp", "jsp"),
        ("p.jinja", "jinja"), ("p.jinja2", "jinja"), ("p.j2", "jinja"),
    ):
        assert get_language(Path(path)) == lang, path
    # Control: an extension of an older template language keeps its label.
    assert get_language(Path("show.html.erb")) == "erb"
    assert get_language(Path("show.html")) == "html"
