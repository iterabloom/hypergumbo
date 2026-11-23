# SPDX-License-Identifier: AGPL-3.0-or-later
# covers: packages/hypergumbo-lang-mainstream/src/hypergumbo_lang_mainstream/ruby.py
"""A ``require_relative`` ends on the required file's node when the tree holds it.

Ruby reads ``require_relative "lib/helper"`` next to the requiring file and
adds ``.rb``. The analyzer built the dst from the string as written,
``ruby:lib/helper:0-0:file:file``, so the edge ended on a stand-in although
``rb/lib/helper.rb`` was in the tree. Now it is looked up
(``edge_target.in_tree_file_id``); a file the tree does not hold keeps that
stand-in, and ``require`` (read along ``$LOAD_PATH``) is unchanged.
"""
from __future__ import annotations

from pathlib import Path

from hypergumbo_core.analyze.base import make_file_id
from hypergumbo_lang_mainstream.ruby import analyze_ruby


def test_require_relative_reaches_the_file_of_the_tree(tmp_path: Path) -> None:
    (tmp_path / "rb" / "lib").mkdir(parents=True)
    (tmp_path / "rb" / "app.rb").write_text(
        'require_relative "lib/helper"\n'
        "require_relative '../shared.rb'\n"
        'require_relative "missing"\n'
        'require "json"\n'
        "def main; end\n"
    )
    (tmp_path / "rb" / "lib" / "helper.rb").write_text("def helper; end\n")
    (tmp_path / "shared.rb").write_text("def shared; end\n")
    result = analyze_ruby(tmp_path)

    src = make_file_id("ruby", "rb/app.rb")
    imports = {e.dst: e.is_resolved for e in result.edges if e.src == src and e.edge_type == "imports"}
    assert len(imports) == 4  # reach
    assert imports[make_file_id("ruby", "rb/lib/helper.rb")] is True
    assert imports[make_file_id("ruby", "shared.rb")] is True
    # Controls: a missing file and a load-path require keep the stand-in.
    assert "ruby:missing:0-0:file:file" in imports
    assert "ruby:json:0-0:file:file" in imports
