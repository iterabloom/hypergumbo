# SPDX-License-Identifier: AGPL-3.0-or-later
"""``scripts/show-site``: the code around one reading-sheet reference.

Every expected line below was written by hand from the fixture tree against the
rules the script's module docstring states; the tests pin the rules, not the
script's own output.
"""
from __future__ import annotations

import ast
import importlib.machinery
import importlib.util
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"

FETCH = '''"""Fixture module."""
from __future__ import annotations

import json
from urllib.request import urlopen
from typing import Optional
RELEASES = (
    "https://example.invalid/"
    "latest"
)
LIMIT = 3


def platform_name() -> str:
    """Name the platform."""
    return "linux"


def get_url() -> str:
    """Fetch the URL.

    Two lines of docstring.
    """
    plat = platform_name()
    with urlopen(RELEASES, timeout=LIMIT) as response:
        data = json.loads(response.read())
    return data[plat]


class Box:
    size = 1

    @staticmethod
    def write(path, text):
        def inner(p):
            p.write_text(text)
        inner(path)
        return get_url()


def install() -> Optional[str]:
    return get_url() + Box.write("p", "t")


def shadow(platform_name):
    import json
    global LIMIT
    LIMIT = json.loads(platform_name)
    return LIMIT


HANDLERS = {"url": get_url}
'''

ALLOWLIST = """version: 1
modules:
  hypergumbo_core.fetch:
    zone: install
    target: example.invalid (latest)
    commands:
    - install-thing
    - add-extras
    sites:
    - function: get_url
      primitive: urllib.request.urlopen
      family: network
      count: 1
      purpose: resolve the asset URL
    - function: get_url
      primitive: json.loads
      family: network
      count: 1
      purpose: resolve the asset URL
    - function: Box.write
      primitive: '*.write_text'
      family: fs_write
      count: 1
      zone: user_out
      commands:
      - <analysis>
      purpose: write at a user path
      finding: WI-abcde
    - function: shadow
      primitive: json.load
      family: fs_write
      count: 1
      purpose: a tail that is a prefix of another word
  hypergumbo_core.cli:
    zone: in_process
    sites: []
"""

TOOL = """#!/usr/bin/env python3
def main():
    print(get_url())


def more():
    return Box.write(
        "p",  # a comment inside the call
        "t",
    )


def late():
    from fetch import get_url
    return get_url()
"""


@pytest.fixture(scope="module")
def mod():
    loader = importlib.machinery.SourceFileLoader("show_site", str(SCRIPTS / "show-site"))
    spec = importlib.util.spec_from_loader("show_site", loader)
    module = importlib.util.module_from_spec(spec)
    sys.modules["show_site"] = module
    loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def root(tmp_path_factory) -> Path:
    root = tmp_path_factory.mktemp("repo")
    core = root / "packages" / "hypergumbo-core" / "src" / "hypergumbo_core"
    tracker = root / "packages" / "hypergumbo-tracker" / "src" / "hypergumbo_tracker"
    for d in (core, tracker, root / "docs", root / "scripts"):
        d.mkdir(parents=True)
    (core / "fetch.py").write_text(FETCH)
    (core / "cli.py").write_text("def run():\n    return 1\n\n\ndef other(get_url):\n    return get_url + 1\n")
    (tracker / "cli.py").write_text("def run():\n    return 2\n")
    (tracker / "serve.py").write_text("x = get_url(\n")  # not Python that parses
    (root / "docs" / "hypergumbo.io-allowlist.yaml").write_text(ALLOWLIST)
    (root / "scripts" / "tool").write_text(TOOL)
    (root / "scripts" / "notes.txt").write_text("get_url() is mentioned here\n")
    return root


def run(mod, root, *args, capsys):
    rc = mod.main([*args, "--root", str(root)])
    out, err = capsys.readouterr()
    return rc, out, err


def test_resolves_short_dotted_and_path_forms(mod, root):
    core = root / "packages" / "hypergumbo-core" / "src" / "hypergumbo_core"
    assert mod.resolve(root, "fetch") == (core / "fetch.py", "hypergumbo_core.fetch", "")
    assert mod.resolve(root, "hypergumbo_core.fetch")[1] == "hypergumbo_core.fetch"
    assert mod.resolve(root, "scripts/tool") == (root / "scripts" / "tool", "scripts/tool", "")
    path, name, note = mod.resolve(root, "cli")
    assert (path, name) == (core / "cli.py", "hypergumbo_core.cli")
    assert note == "also matches hypergumbo_tracker.cli"
    with pytest.raises(ValueError, match="no module named nothing"):
        mod.resolve(root, "nothing")
    with pytest.raises(ValueError, match="no file scripts/gone"):
        mod.resolve(root, "scripts/gone")


def test_enclosing_is_the_outermost_def_and_is_found_by_name(mod, root):
    tree = ast.parse(FETCH)
    assert mod.enclosing(tree, 25).name == "get_url"
    assert mod.enclosing(tree, 36).name == "Box.write"  # inside the nested inner()
    assert mod.enclosing(tree, 33).name == "Box.write"  # the decorator line
    assert mod.enclosing(tree, 11) is None
    assert mod.enclosing(tree, "write").name == "Box.write"
    assert mod.enclosing(tree, "Box.write").name == "Box.write"
    assert mod.enclosing(tree, "missing") is None


def test_full_report_for_a_line(mod, root, capsys):
    rc, out, err = run(mod, root, "fetch:25", capsys=capsys)
    assert (rc, err) == (0, "")
    assert out == """\
fetch:25  packages/hypergumbo-core/src/hypergumbo_core/fetch.py
in get_url, lines 19-27

allowlist says (docs/hypergumbo.io-allowlist.yaml):
  network  urllib.request.urlopen x1, json.loads x1  zone install
    target    example.invalid (latest)
    commands  install-thing; add-extras
    purpose   resolve the asset URL

  19  def get_url() -> str:
  20      \"\"\"Fetch the URL.
  21
  22      Two lines of docstring.
  23      \"\"\"
  24      plat = platform_name()
> 25      with urlopen(RELEASES, timeout=LIMIT) as response:
  26          data = json.loads(response.read())
  27      return data[plat]

reads from this module:
   4  json  <- import json
   5  urlopen  <- from urllib.request import urlopen
   7  RELEASES = (
   8      "https://example.invalid/"
   9      "latest"
  10  )
  11  LIMIT = 3
  14  def platform_name() -> str:  3 lines

called or referenced from (packages/*/src and scripts/, by name):
  hypergumbo_core.fetch:38  call in Box.write  return get_url()
  hypergumbo_core.fetch:42  call in install  return get_url() + Box.write("p", "t")
  hypergumbo_core.fetch:52  ref in <module>  HANDLERS = {"url": get_url}
  scripts/tool:3  call in main  print(get_url())
  scripts/tool:15  call in late  return get_url()
  note: hypergumbo_tracker.serve does not parse; not searched
"""


def test_reads_skip_local_rebinding_but_keep_globals_and_stale_check_is_a_word(mod, root, capsys):
    rc, out, _ = run(mod, root, "fetch:48", capsys=capsys)
    assert rc == 0
    assert "in shadow, lines 45-49\n" in out
    assert "  fs_write  json.load x1  zone install\n" in out
    assert "  note: line 48 does not mention load; the line number may be stale\n" in out
    assert "reads from this module:\n  11  LIMIT = 3\n\ncalled or referenced from" in out


def test_method_site_with_overrides_finding_and_several_lines(mod, root, capsys):
    rc, out, _ = run(mod, root, "fetch:36,37", capsys=capsys)
    assert rc == 0
    assert "in Box.write, lines 33-38\n" in out
    assert """\
allowlist says (docs/hypergumbo.io-allowlist.yaml):
  fs_write  *.write_text x1  zone user_out
    target    example.invalid (latest)
    commands  <analysis>
    purpose   write at a user path
    FINDING   WI-abcde
  note: line 37 does not mention write_text; the line number may be stale
""" in out
    assert "> 36              p.write_text(text)\n> 37          inner(path)\n" in out
    assert "reads from this module:\n  19  def get_url() -> str:  9 lines\n" in out
    assert "  hypergumbo_core.fetch:42  call in install  return get_url() + Box.write(\"p\", \"t\")\n" in out
    assert "  scripts/tool:7  call in more  Box.write('p', 't')\n" in out


def test_function_by_name_without_claim_or_callers(mod, root, capsys):
    rc, out, _ = run(mod, root, "fetch:install", capsys=capsys)
    assert rc == 0
    assert "in install, lines 41-42\n" in out
    assert "allowlist: no entry for install in hypergumbo_core.fetch\n" in out
    assert "  41  def install() -> Optional[str]:\n  42      return get_url() + Box.write(\"p\", \"t\")\n" in out
    assert "reads from this module:\n  19  def get_url() -> str:  9 lines\n  30  class Box:  9 lines\n" in out
    assert "called or referenced from (packages/*/src and scripts/, by name):\n  none found\n" in out


def test_module_outside_the_allowlist_says_so(mod, root, capsys):
    rc, out, _ = run(mod, root, "hypergumbo_tracker.cli:2", capsys=capsys)
    assert rc == 0
    assert "allowlist: no entry for run in hypergumbo_tracker.cli\n" in out
    assert "reads from this module:\n  nothing\n" in out


def test_errors_exit_2(mod, root, capsys):
    assert run(mod, root, "fetch", capsys=capsys)[0] == 2
    assert run(mod, root, "fetch:11", capsys=capsys)[0] == 2
    assert run(mod, root, "fetch:999", capsys=capsys)[0] == 2
    assert run(mod, root, "fetch:missing", capsys=capsys)[0] == 2
    assert run(mod, root, "nothing:1", capsys=capsys)[0] == 2
    _, _, err = run(mod, root, "fetch:11", capsys=capsys)
    assert err == "fetch:11 is not inside a function\n"


def test_size_band_the_owner_set():
    lines = (SCRIPTS / "show-site").read_text().count("\n")
    assert 166 <= lines <= 184, lines
