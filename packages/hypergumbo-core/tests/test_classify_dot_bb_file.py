# SPDX-License-Identifier: AGPL-3.0-or-later
# covers: packages/hypergumbo-core/src/hypergumbo_core/discovery.py
"""``classify_dot_bb_file``: a ``.bb`` file is a BitBake recipe or a Babashka
(Clojure) script.

Both use the extension, and the BitBake analyzer read every ``.bb``:
metabase's ``bin/*.bb`` Babashka scripts were parsed as BitBake (no symbol)
and never by the Clojure analyzer. A recipe's first statement is an
assignment, an ``inherit`` / ``require`` / ``include`` or a task; a Babashka
script opens with a ``bb`` shebang, a ``;`` comment or a form.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from hypergumbo_core.discovery import classify_dot_bb_file


@pytest.mark.parametrize("text", [
    "#!/usr/bin/env bb\n\n(ns athena)\n",
    "#!/usr/bin/env bb\n",  # a shebang naming bb decides alone
    "#!/usr/local/bin/babashka\nX = 1\n",
    ";; -*- mode: clojure -*-\n\n(ns mage)\n",
    "\n\n(require '[babashka.fs :as fs])\n",
    "#_(comment)\n(ns x)\n",
])
def test_a_babashka_script_is_clojure(tmp_path: Path, text: str) -> None:
    path = tmp_path / "script.bb"
    path.write_text(text)
    assert classify_dot_bb_file(path) == "clojure"


@pytest.mark.parametrize("text", [
    '# Fluent Bit - Yocto / Bitbake\n# ===\n\nSUMMARY = "Fast Log processor"\n',
    "inherit autotools pkgconfig\n",
    "require recipes-core/images/core-image-minimal.bb\n",
    'DESCRIPTION = "x"\nDEPENDS = "zlib"\n',
    "do_install() {\n    install -d ${D}\n}\n",
    "#!/usr/bin/env bash\nSUMMARY = \"x\"\n",  # a shebang naming another program
    "",  # empty: nothing says Clojure
])
def test_a_bitbake_recipe_stays_bitbake(tmp_path: Path, text: str) -> None:
    path = tmp_path / "recipe_1.0.bb"
    path.write_text(text)
    assert classify_dot_bb_file(path) == "bitbake"


def test_an_unreadable_file_is_bitbake(tmp_path: Path) -> None:
    assert classify_dot_bb_file(tmp_path / "absent.bb") == "bitbake"
