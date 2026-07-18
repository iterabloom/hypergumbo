# SPDX-License-Identifier: AGPL-3.0-or-later
"""INV-vigaf: extension/UFCS call resolution lives in a shared linker.

The ``receiver_type_dispatch`` linker owns all resolution of non-hierarchy
``x.foo()`` calls — extension methods (``ast_call_extension``) and UFCS free
functions (``ast_call_ufcs``). Language analyzers MUST emit only
``make_unresolved_edge(...)`` + a receiver-type hint; they must NOT resolve such
calls themselves. This is the analyzer→linker contract INV-nilud established for
inheritance, extended to the non-hierarchy family (Kotlin migrated off its
in-analyzer resolver in WI-lodij; D routed through the linker in WI-situj).

This test enforces the analyzer-ownership half **statically**: no producer
source hand-writes ``evidence_type="ast_call_extension"`` / ``"ast_call_ufcs"``.
Those values are stamped exclusively by the linker, via its
``_RECEIVER_META_EVIDENCE`` MetaKey→evidence mapping (``evidence_type=evidence_type``,
a variable — never a literal at a producer site). If a future analyzer resolves
an extension/UFCS call in-line it will hand-write the literal and trip this gate,
directing it to emit unresolved+hint and let the linker resolve instead.

The **positive** half of the invariant (the linker DOES resolve these; Kotlin
and D emit unresolved+hint) is covered by ``test_receiver_type_dispatch.py``,
``test_kotlin.py`` (extension call-site tests), and
``test_d_ufcs_receiver_gating.py``.
"""

from __future__ import annotations

import re
from pathlib import Path

# .../packages/hypergumbo-core/tests/<thisfile> → repo root is parents[3].
_REPO_ROOT = Path(__file__).resolve().parents[3]

_LITERAL_EMISSION = re.compile(
    r"""evidence_type\s*=\s*['"]ast_call_(?:extension|ufcs)['"]""",
)


def _source_files() -> list[Path]:
    files: list[Path] = []
    for pkg_src in sorted(_REPO_ROOT.glob("packages/*/src")):
        files.extend(sorted(pkg_src.rglob("*.py")))
    return files


def test_source_tree_present() -> None:
    """Guard: the glob actually found the package source trees."""
    files = _source_files()
    assert files, f"no package source files found under {_REPO_ROOT}/packages"


def test_no_producer_hardcodes_extension_or_ufcs_evidence() -> None:
    """INV-vigaf: only the linker stamps ast_call_extension / ast_call_ufcs."""
    offenders: list[str] = []
    for path in _source_files():
        for lineno, line in enumerate(
            path.read_text(encoding="utf-8").splitlines(), start=1,
        ):
            if _LITERAL_EMISSION.search(line):
                rel = path.relative_to(_REPO_ROOT)
                offenders.append(f"{rel}:{lineno}: {line.strip()}")
    assert not offenders, (
        "INV-vigaf: ast_call_extension / ast_call_ufcs must be stamped only by "
        "the receiver_type_dispatch linker (via _RECEIVER_META_EVIDENCE), not "
        "hand-written at a producer site — emit make_unresolved_edge + a "
        "receiver_type_hint and let the linker resolve. Offenders:\n"
        + "\n".join(offenders)
    )
