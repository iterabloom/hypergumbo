# SPDX-License-Identifier: AGPL-3.0-or-later
"""WI-vihop: ``fs.promises.<fn>`` must reach the same rows ``fs/promises`` does.

WHAT WAS BROKEN. Node's promise APIs have two spellings and only one worked:

    require('fs/promises').readFile(p)        -> javascript:fs/promises:...  CLASSIFIES
    import { readFile } from 'node:fs/promises' -> javascript:fs/promises:... CLASSIFIES
    fs.promises.readFile(p)   (fs = require('fs'))  -> javascript:external:...  NOTHING
    require('fs').promises.writeFile(p, x)          -> javascript:external:...  NOTHING
    import fs from 'fs'; fs.promises.readFile(p)    -> javascript:external:...  NOTHING

The member CHAIN is the documented pre-ESM spelling and is still common. It
reached the terminal placeholder because ``obj_name`` is only set when the
receiver is a bare ``identifier``; ``fs.promises`` is a ``member_expression``, so
every case that consults ``namespace_imports`` was skipped and the 31 catalogued
``fs.promises`` rows (and 16 ``dns.promises`` rows) were unreachable through it.

THE THIRD SPELLING WAS NOT IN THE FILING. WI-vihop lists the CommonJS forms; the
ESM default import (``import fs from 'fs'``) is the same shape and was equally
broken. ``_extract_namespace_imports`` already binds all three the same way, which
is why one fix covers them.

THE SLOT SPELLING IS REUSED, NOT INVENTED. The direct require emits
``fs/promises`` and production classifies it (both ``fs/promises`` and
``fs.promises`` resolve to the same rows), so the chain emits the SAME string
rather than a second spelling that would have to be kept in step.

SCOPED TO NODE'S DOCUMENTED ``promises`` SUB-NAMESPACE, on parents that really
have one. A general "``<ident>.<prop>`` becomes ``<module>/<prop>``" rule would
emit ``axios/defaults`` for ``axios.defaults.get()`` — a module that does not
exist, which is precisely what INV-fazim refuses.
"""

from pathlib import Path

_CASES = {
    "cjs_alias.js": "const fs = require('fs');\nfs.promises.readFile('f');\n",
    "cjs_inline.js": "require('fs').promises.writeFile('f', 'x');\n",
    "esm_default.js": "import fs from 'fs';\nfs.promises.readFile('f');\n",
    "esm_star.js": "import * as fs from 'node:fs';\nfs.promises.readFile('f');\n",
    "dns_alias.js": "const dns = require('dns');\ndns.promises.resolve('h');\n",
    "control_direct.js": "const p = require('fs/promises');\np.readFile('f');\n",
    "control_plain.js": "const fs = require('fs');\nfs.readFile('f', cb);\n",
}


def _slots(tmp_path: Path) -> dict:
    from hypergumbo_lang_mainstream.js_ts import analyze_javascript

    for name, body in _CASES.items():
        (tmp_path / name).write_text(body)
    result = analyze_javascript(tmp_path)
    assert not result.skipped, "javascript grammar unavailable — refusing a vacuous pass"
    out: dict = {}
    for e in result.edges:
        if e.edge_type != "calls" or not isinstance(e.dst, str):
            continue
        src = e.src if isinstance(e.src, str) else ""
        f = src.split("javascript:", 1)[-1].split(":")[0].split("/")[-1]
        out.setdefault(f, []).append(e.dst.split(":")[1])
    assert out, "no call edges at all — the fixture is wrong"
    return out


class TestTheChainReachesTheSameSlot:
    def test_commonjs_alias(self, tmp_path: Path) -> None:
        assert "fs/promises" in _slots(tmp_path)["cjs_alias.js"]

    def test_commonjs_inline_require(self, tmp_path: Path) -> None:
        assert "fs/promises" in _slots(tmp_path)["cjs_inline.js"]

    def test_esm_default_import(self, tmp_path: Path) -> None:
        """Not in WI-vihop's list, and equally broken."""
        assert "fs/promises" in _slots(tmp_path)["esm_default.js"]

    def test_esm_namespace_import(self, tmp_path: Path) -> None:
        assert "fs/promises" in _slots(tmp_path)["esm_star.js"]

    def test_dns_promises(self, tmp_path: Path) -> None:
        """The analogue LIVE rule 4 asks for: same shape, different module."""
        assert "dns/promises" in _slots(tmp_path)["dns_alias.js"]


class TestTheControlsAreUnmoved:
    def test_the_direct_require_still_works(self, tmp_path: Path) -> None:
        assert "fs/promises" in _slots(tmp_path)["control_direct.js"]

    def test_a_plain_member_call_is_untouched(self, tmp_path: Path) -> None:
        slots = _slots(tmp_path)["control_plain.js"]
        assert "fs" in slots
        assert "fs/promises" not in slots


class TestItReachesTheCATALOGUE:
    """A filled slot is necessary, not sufficient (L5). The point of the change
    is the row, so the row is what is asserted."""

    def test_the_chain_classifies_as_fs_read(self, tmp_path: Path) -> None:
        from hypergumbo_core.io_boundary import classify_call, load_catalog

        cats = {"javascript": load_catalog("javascript")}
        from hypergumbo_lang_mainstream.js_ts import analyze_javascript

        (tmp_path / "a.js").write_text(_CASES["cjs_alias.js"])
        result = analyze_javascript(tmp_path)
        hits = [
            classify_call(cats, e.dst, e.meta or {})
            for e in result.edges
            if isinstance(e.dst, str) and "readFile" in e.dst
        ]
        found = [h for h in hits if h is not None]
        assert found, "the chain still reaches no catalogue row"
        assert found[0].boundary == "fs_read"
        assert found[0].qualified_name == "fs.promises.readFile"


class TestTheDisciplineIsKept:
    def test_an_unlisted_property_invents_no_module(self, tmp_path: Path) -> None:
        """``axios.defaults.get()`` must NOT become ``axios/defaults``: a module
        that does not exist is what INV-fazim refuses."""
        from hypergumbo_lang_mainstream.js_ts import analyze_javascript

        (tmp_path / "z.js").write_text(
            "const axios = require('axios');\naxios.defaults.get('u');\n"
        )
        result = analyze_javascript(tmp_path)
        slots = [e.dst.split(":")[1] for e in result.edges
                 if e.edge_type == "calls" and isinstance(e.dst, str)]
        assert "axios/defaults" not in slots

    def test_a_promises_chain_on_an_unbound_name_invents_nothing(
        self, tmp_path: Path,
    ) -> None:
        """No import binds ``mystery``, so there is no module to qualify to."""
        from hypergumbo_lang_mainstream.js_ts import analyze_javascript

        (tmp_path / "y.js").write_text("mystery.promises.readFile('f');\n")
        result = analyze_javascript(tmp_path)
        slots = [e.dst.split(":")[1] for e in result.edges
                 if e.edge_type == "calls" and isinstance(e.dst, str)]
        assert "mystery/promises" not in slots
