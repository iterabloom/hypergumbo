# SPDX-License-Identifier: AGPL-3.0-or-later
"""WI-kalub Step 2: CI-loop commit-status API calls must be failover-aware.

The stop-the-line protocol (ci.yml) reads the Forgejo auto-generated
``Full Test Suite / aggregate`` commit status to decide whether full-suite is
broken. That read — and full-suite's / nightly's status writes and the
"SHA already tested → skip" read — hardcoded ``https://codeberg.org/api/v1``.
Under permanent CI failover the workflows run on the self-hosted Forgejo, whose
auto-status lives on *that* server; a hardcoded codeberg endpoint is a dead
read/write, so stop-the-line silently never blocked (and full-suite re-ran
already-tested SHAs). Every CI-loop status call must instead target the running
server via ``${{ github.server_url }}`` (the form ci.yml already uses elsewhere).

Scope: the CI *dev-loop* workflows only. ``release.yml`` / ``release-mirror.yml``
legitimately reference codeberg — releases are canonical-on-codeberg and
human-gated — so they are intentionally excluded.
"""
from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
WORKFLOWS = REPO_ROOT / ".github" / "workflows"
CI_LOOP_WORKFLOWS = ["ci.yml", "full-suite.yml", "nightly.yml"]

# Any codeberg.org REST-API call (status read/write, SHA-skip). Release endpoints
# live only in release*.yml, which this guard does not scan.
_CODEBERG_API = re.compile(r"codeberg\.org/api")


def test_ci_loop_workflows_exist() -> None:
    for wf in CI_LOOP_WORKFLOWS:
        assert (WORKFLOWS / wf).is_file(), f"{wf} missing"


def test_no_ci_loop_status_call_hardcodes_codeberg() -> None:
    """No CI dev-loop workflow may hardcode a codeberg.org API endpoint for
    commit-status read/write — under failover it targets a dead server."""
    offenders: list[str] = []
    for wf in CI_LOOP_WORKFLOWS:
        text = (WORKFLOWS / wf).read_text()
        for m in _CODEBERG_API.finditer(text):
            line_no = text.count("\n", 0, m.start()) + 1
            line = text[text.rfind("\n", 0, m.start()) + 1:text.find("\n", m.start())].strip()
            offenders.append(f"{wf}:{line_no}: {line}")
    assert not offenders, (
        "CI-loop status API calls must target the running server via "
        "`${{ github.server_url }}/api/v1`, not a hardcoded codeberg.org endpoint "
        "(dead under CI failover — stop-the-line silently never blocks). "
        "Offenders:\n  " + "\n  ".join(offenders)
    )


def test_stop_the_line_read_uses_server_url() -> None:
    """Positive lock: ci.yml's stop-the-line status read anchors on
    ``github.server_url`` so it reads the auto-status from the running server."""
    text = (WORKFLOWS / "ci.yml").read_text()
    assert (
        'API_BASE="${{ github.server_url }}/api/v1/repos/${{ github.repository }}"'
        in text
    ), (
        "ci.yml's stop-the-line read must set "
        'API_BASE="${{ github.server_url }}/api/v1/repos/${{ github.repository }}" '
        "(WI-kalub Step 2) so the Full Test Suite / aggregate auto-status is read "
        "from the server the full-suite actually ran on (selfh under failover)."
    )
