# SPDX-License-Identifier: AGPL-3.0-or-later
# covers: packages/hypergumbo-lang-mainstream/src/hypergumbo_lang_mainstream/sql.py
"""Untyped-formula names are hashed without their layout, except content.

SQL names a table, view, function or index by its ``object_reference`` text,
so ``app . users`` and ``app .\\n  v`` were other names than ``app.users`` and
``app.v``. The untyped formula hashes names ``without_layout`` in every
language but those whose names are content (``CONTENT_NAME_LANGUAGES``):
an Ansible task's name is prose, and two spaces in it are its text
(WI-sidus).
"""
from __future__ import annotations

from pathlib import Path

from hypergumbo_lang_mainstream.sql import analyze_sql_files
from hypergumbo_lang_mainstream.yaml_ansible import analyze_ansible


def _sql(tmp_path: Path, text: str) -> list[tuple[str, str | None]]:
    for old in tmp_path.iterdir():
        old.unlink()
    (tmp_path / "s.sql").write_text(text)
    syms = sorted(analyze_sql_files(tmp_path).symbols, key=lambda s: s.span.start_line)
    return [(s.kind, s.stable_id) for s in syms if s.kind in ("table", "view", "function")]


def test_a_spaced_sql_name_keeps_its_id(tmp_path: Path) -> None:
    tight = _sql(tmp_path, "CREATE TABLE app.users (id int);\nCREATE VIEW app.v AS SELECT 1;\n"
                           "CREATE FUNCTION app.f() RETURNS int AS $$ SELECT 1 $$ LANGUAGE sql;\n")
    spaced = _sql(tmp_path, "CREATE TABLE app . users (id int);\nCREATE VIEW app .\n  v AS SELECT 1;\n"
                            "CREATE FUNCTION app . f() RETURNS int AS $$ SELECT 1 $$ LANGUAGE sql;\n")
    assert [k for k, _ in tight] == ["table", "view", "function"] and all(s for _, s in tight)
    assert tight == spaced


def test_another_sql_name_is_another_id(tmp_path: Path) -> None:
    assert _sql(tmp_path, "CREATE TABLE app.users (id int);\n") != _sql(tmp_path, "CREATE TABLE app.user (id int);\n")


def _playbook(tmp_path: Path, name: str) -> list[str | None]:
    for old in tmp_path.iterdir():
        old.unlink()
    (tmp_path / "site.yml").write_text(f"- name: {name}\n  hosts: all\n  tasks:\n    - name: ping\n      ping:\n")
    return [s.stable_id for s in analyze_ansible(tmp_path).symbols if s.kind == "playbook"]


def test_a_content_name_keeps_its_whitespace(tmp_path: Path) -> None:
    one = _playbook(tmp_path, "Install packages")
    two = _playbook(tmp_path, "Install  packages")
    assert one and all(one)  # reach
    assert one != two
