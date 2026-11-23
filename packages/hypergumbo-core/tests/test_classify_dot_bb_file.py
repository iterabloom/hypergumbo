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


# ---------- classify_dot_inc_file ----------
from hypergumbo_core.discovery import classify_dot_inc_file  # noqa: E402


@pytest.mark.parametrize("text", [
    '# Common settings\n\nSUMMARY = "x"\n',
    'require recipes-core/foo.inc\n',
    'include conf/distro/include/x.inc\n',
    'inherit autotools\n',
    'SRC_URI:append = " file://a.patch"\n',
    'PACKAGECONFIG[ssl] = "--with-ssl,--without-ssl,openssl"\n',
    'do_install:append() {\n    true\n}\n',
    'python do_patch() {\n    pass\n}\n',
    'def helper(d):\n    return 1\n',
    'addtask deploy after do_install\n',
    'export CFLAGS\n',
])
def test_a_bitbake_include_stays_bitbake(tmp_path: Path, text: str) -> None:
    path = tmp_path / "common.inc"
    path.write_text(text)
    assert classify_dot_inc_file(path) == "bitbake"


@pytest.mark.parametrize("text", [
    "/*\n * QEMU RISC-V translation\n */\nstatic bool trans_x(DisasContext *ctx) { return true; }\n",
    "#include <stdio.h>\nint x;\n",
    "#include <stdio.h>\nmain() {\n}\n",  # a directive is not a comment: the K&R main below it would read as a task
    "#define FOO 1\n",
    "// Copyright 2024\nint y;\n",
    "<?php\n$a = 1;\n",
    ";---------\n%macro PUSH 1\n",
    "VGAREG_ACTL_ADDRESS      equ 0x3c0\n",
    ".. -*- rst -*-\n\nTitle\n=====\n",
    "## @file\n#  EDK2 include\n[Defines]\n",
    "",
])
def test_another_include_is_not_bitbake(tmp_path: Path, text: str) -> None:
    path = tmp_path / "trans.c.inc"
    path.write_text(text)
    assert classify_dot_inc_file(path) == "other"


def test_an_unreadable_inc_is_not_bitbake(tmp_path: Path) -> None:
    assert classify_dot_inc_file(tmp_path / "absent.inc") == "other"
