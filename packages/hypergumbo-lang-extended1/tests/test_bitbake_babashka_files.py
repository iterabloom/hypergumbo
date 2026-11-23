# SPDX-License-Identifier: AGPL-3.0-or-later
# covers: packages/hypergumbo-lang-extended1/src/hypergumbo_lang_extended1/bitbake.py
"""The BitBake analyzer leaves a Babashka ``.bb`` script to the Clojure one.

It read every ``.bb``; metabase's ``bin/*.bb`` Babashka scripts were parsed
as BitBake recipes. ``discovery.classify_dot_bb_file`` now decides, as
``classify_dot_m_file`` does for ``.m``.
"""
from __future__ import annotations

from pathlib import Path

from hypergumbo_lang_extended1.bitbake import analyze_bitbake, find_bitbake_files


def test_a_babashka_script_is_not_a_bitbake_file(tmp_path: Path) -> None:
    (tmp_path / "bin").mkdir()
    (tmp_path / "bin" / "mage.bb").write_text(
        "#!/usr/bin/env bb\n(ns mage)\n(defn run [] (println 1))\n"
    )
    # Control: a recipe, and the other BitBake suffixes, stay.
    (tmp_path / "foo_1.0.bb").write_text('SUMMARY = "foo"\ninherit autotools\n')
    (tmp_path / "foo_%.bbappend").write_text('DEPENDS += "zlib"\n')
    found = {p.relative_to(tmp_path).as_posix() for p in find_bitbake_files(tmp_path)}
    assert found == {"foo_1.0.bb", "foo_%.bbappend"}

    result = analyze_bitbake(tmp_path)
    assert result.run.files_analyzed == 2
    paths = {s.path for s in result.symbols}
    assert any(p.endswith("foo_1.0.bb") for p in paths)  # reach
    assert not any(p.endswith("mage.bb") for p in paths)
