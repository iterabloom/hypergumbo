# SPDX-License-Identifier: AGPL-3.0-or-later
"""End-to-end taint RECALL corpus — source on disk, verdict out.

WHY THIS EXISTS. Every other taint test in the tree hands ``verify-claims`` a
hand-written behavior map via ``--input``; the analyzers never run. The result
is that nothing in the suite would fail if the pipeline stopped reporting an
entire class of real flows — which is how ten ADR-0017 capabilities came to sit
inert at 100% coverage, each closed ``done`` on that coverage. Component tests
measure components; this module measures the *pipeline*, from Python/Go source
files on disk through analysis, propagation and claim verification to a verdict.

HOW IT DEFENDS AGAINST ITS OWN VACUITY. A corpus of "this must NOT be reported"
assertions passes trivially when the pipeline reports nothing at all (L17), and
that is the exact failure mode here — a broken propagator makes every
precision assertion greener. So each language arm puts the must-find and the
must-not-find shapes in **one repo** and asserts the whole finding set at once.
A pipeline that emits nothing fails the must-find half in the same test. Vacuity
is structurally impossible rather than guarded against.

FIXTURE SHAPES, and why each is what it is:

  reaches.py   source -> wrapper -> sink, a forward call chain.  MUST be found.
               Taint propagation seeds at the source's *caller* and walks the
               call graph forward, so this is the shape that is in scope by
               construction.

  no_sink.py   a source with nothing downstream.  MUST NOT be found. Catches a
               propagator that reports on source presence alone.

  diamond      caller -> source_fn and caller -> sink_fn as *siblings*. This is
               a real flow that the forward-only walk cannot connect, because
               ``reverse_adj`` is built (taint.py) and never read. Recorded as
               ``xfail(strict=True)`` rather than as an assertion of the wrong
               behaviour, so it XPASSes and demands attention the moment
               source-side propagation lands (L19).

CATALOG ENTRIES ARE CHOSEN, NOT ASSUMED. Every source and sink below is
module-qualified and absent from its language's ``ambiguous_names``, verified
against ``load_builtin_taint_catalog()`` before being written here. The first
positive control for this subsystem failed because it used a bare ambiguous
name (``recv``) and a chained call the analyzer does not emit — a clean zero
that looked like a clean bill of health.

PATHS. The corpus is generated into ``tmp_path`` rather than committed under
``tests/fixtures/``. Two independent reasons: a fixture corpus analyzed in
place measures the pipeline's own test-path heuristics rather than the producer
(L20/L21), and ``verify-claims`` now excludes test-sourced flows by default, so
a corpus under a directory named ``fixtures`` would be excluded from its own
verdicts. Symbol-id path slots are repo-relative, so the tmp prefix never
reaches the classifier.
"""
import json
from pathlib import Path

import pytest
import yaml

from hypergumbo_core.cli import cmd_verify_claims


class _Args:
    """Minimal stand-in for the argparse namespace ``cmd_verify_claims`` reads.

    ``input`` is deliberately absent: the command reads it via ``getattr(...,
    None)``, and leaving it unset is what routes the run through
    ``_get_or_run_analysis`` — i.e. through the real analyzers. Setting it is
    what every other taint test in the tree does, and is exactly the thing this
    module exists not to do.
    """

    def __init__(self, path: Path, claims: Path) -> None:
        self.path = str(path)
        self.claims = str(claims)
        self.format = "json"


#: Claim set used by every arm. Both labels and both zones are drawn from the
#: built-in catalog vocabulary, so a claim can never reference something the
#: engine is incapable of producing (a claim naming an unknown label yields a
#: confident, meaningless "confirmed").
_CLAIMS = {
    "claims": [
        {
            "id": "no-untrusted-to-fs",
            "text": "Untrusted input must not reach the filesystem.",
            "constraint": {
                "taint_flow": {
                    "source_taint": "untrusted_input",
                    "prohibited_sink_zone": "host_fs",
                },
            },
        },
    ],
}


def _run(repo: Path, capsys) -> dict:
    """Run the real pipeline over ``repo`` and return the parsed envelope."""
    claims_file = repo / "claims.yaml"
    claims_file.write_text(yaml.dump(_CLAIMS), encoding="utf-8")

    rc = cmd_verify_claims(_Args(repo, claims_file))
    captured = capsys.readouterr().out
    envelope = json.loads(captured)
    return {"rc": rc, "envelope": envelope}


def _verdict(envelope: dict, claim_id: str) -> dict:
    for verdict in envelope["verdicts"]:
        if verdict["claim_id"] == claim_id:
            return verdict
    raise AssertionError(f"claim {claim_id!r} absent from envelope")


def _flow_pairs(verdict: dict) -> set:
    """(source_primitive, sink_primitive) for each evidence row."""
    return {
        (row["source_primitive"], row["sink_primitive"])
        for row in verdict.get("evidence", [])
    }


# --------------------------------------------------------------------------
# Python arm
# --------------------------------------------------------------------------

def test_python_corpus_recall_and_precision(tmp_path: Path, capsys) -> None:
    """The reachable flow is reported; the source-without-sink is not.

    Both halves are asserted against one analysis of one repo, so a pipeline
    that reports nothing fails here rather than passing the precision half.
    """
    src = tmp_path / "src"
    src.mkdir()

    # MUST FIND: asyncio.start_server (untrusted_input) reaches os.mkdir
    # (host_fs) through a first-party wrapper, along a forward call chain.
    (src / "reaches.py").write_text(
        "import asyncio\n"
        "import os\n"
        "\n"
        "\n"
        "def persist(name):\n"
        "    os.mkdir(name)\n"
        "\n"
        "\n"
        "async def serve():\n"
        "    server = await asyncio.start_server(None, '0.0.0.0', 8080)\n"
        "    persist(str(server))\n"
        "    return server\n",
        encoding="utf-8",
    )

    # MUST NOT FIND: a source with nothing downstream of it. A propagator that
    # keys on source presence rather than on reachability reports this.
    (src / "no_sink.py").write_text(
        "import asyncio\n"
        "\n"
        "\n"
        "async def listen_only():\n"
        "    return await asyncio.start_server(None, '0.0.0.0', 9090)\n",
        encoding="utf-8",
    )

    result = _run(tmp_path, capsys)
    verdict = _verdict(result["envelope"], "no-untrusted-to-fs")

    # RECALL — the reachable flow is present, and the run is therefore not
    # vacuous. Everything below this line is only meaningful because of it.
    assert verdict["verdict"] == "violated"
    assert result["rc"] == 1
    assert ("start_server", "mkdir") in _flow_pairs(verdict)

    # PRECISION — exactly one distinct source->sink pair. ``listen_only``
    # contributes a source with no reachable sink and must add nothing.
    assert _flow_pairs(verdict) == {("start_server", "mkdir")}


def test_python_source_without_any_sink_confirms(
    tmp_path: Path, capsys,
) -> None:
    """A repo with a source and no sink at all yields a clean confirmed.

    Distinct from the precision half above: there the sink existed elsewhere in
    the repo and merely was not reachable. Here no host_fs primitive is called
    anywhere, which is the case a zone-presence check would get right and a
    reachability check must also get right.
    """
    src = tmp_path / "src"
    src.mkdir()
    (src / "listener.py").write_text(
        "import asyncio\n"
        "\n"
        "\n"
        "async def serve():\n"
        "    return await asyncio.start_server(None, '0.0.0.0', 8080)\n",
        encoding="utf-8",
    )

    result = _run(tmp_path, capsys)
    verdict = _verdict(result["envelope"], "no-untrusted-to-fs")

    assert verdict["verdict"] == "confirmed"
    assert result["rc"] == 0
    assert verdict["evidence_count"] == 0


@pytest.mark.xfail(
    strict=True,
    reason=(
        "Source-side propagation is unimplemented: the BFS seeds at the "
        "source's caller and walks forward only, so a caller that invokes the "
        "source and the sink as siblings is never connected. reverse_adj is "
        "built in propagate_taint_structural and never read. Remove this "
        "marker when source-side propagation lands (WI-sirod)."
    ),
)
def test_python_sibling_calls_under_one_caller_are_connected(
    tmp_path: Path, capsys,
) -> None:
    """A real flow the forward-only walk cannot see, recorded as a ratchet hole.

    ``handle`` calls the source and then calls the writer; the tainted value
    genuinely reaches the filesystem. Because neither callee calls the other,
    the forward walk from the source's caller never reaches the sink's caller.
    An imperative ``pytest.xfail()`` here could never XPASS and would silently
    disable the very signal this test exists to carry (L19), so the marker is
    declarative and strict.
    """
    src = tmp_path / "src"
    src.mkdir()
    (src / "diamond.py").write_text(
        "import asyncio\n"
        "import os\n"
        "\n"
        "\n"
        "async def obtain():\n"
        "    return await asyncio.start_server(None, '0.0.0.0', 8080)\n"
        "\n"
        "\n"
        "def persist(value):\n"
        "    os.mkdir(value)\n"
        "\n"
        "\n"
        "async def handle():\n"
        "    server = await obtain()\n"
        "    persist(str(server))\n",
        encoding="utf-8",
    )

    result = _run(tmp_path, capsys)
    verdict = _verdict(result["envelope"], "no-untrusted-to-fs")
    assert verdict["verdict"] == "violated"


# --------------------------------------------------------------------------
# Go arm
# --------------------------------------------------------------------------

def test_go_corpus_recall_and_precision(tmp_path: Path, capsys) -> None:
    """Same two properties for Go, the other language with a CFG mapping.

    Go carries 132 catalogued sinks and is one of only two languages whose CFG
    node mapping declares ``atomic_statement``, so it is the second language
    where data-flow work can have any effect at all. Asserting recall here
    keeps a Python-only regression from reading as a whole-pipeline pass.
    """
    (tmp_path / "go.mod").write_text(
        "module example.com/corpus\n\ngo 1.21\n", encoding="utf-8",
    )

    # MUST FIND: net/http.ListenAndServe (untrusted_input) reaches
    # os.WriteFile (host_fs) through a first-party wrapper.
    (tmp_path / "reaches.go").write_text(
        "package corpus\n"
        "\n"
        "import (\n"
        "\t\"net/http\"\n"
        "\t\"os\"\n"
        ")\n"
        "\n"
        "func persist(name string) {\n"
        "\tos.WriteFile(name, []byte(\"x\"), 0644)\n"
        "}\n"
        "\n"
        "func Serve(addr string) {\n"
        "\thttp.ListenAndServe(addr, nil)\n"
        "\tpersist(addr)\n"
        "}\n",
        encoding="utf-8",
    )

    # MUST NOT FIND: a source with nothing downstream.
    (tmp_path / "no_sink.go").write_text(
        "package corpus\n"
        "\n"
        "import \"net/http\"\n"
        "\n"
        "func ListenOnly(addr string) {\n"
        "\thttp.ListenAndServe(addr, nil)\n"
        "}\n",
        encoding="utf-8",
    )

    result = _run(tmp_path, capsys)
    verdict = _verdict(result["envelope"], "no-untrusted-to-fs")

    assert verdict["verdict"] == "violated"
    assert result["rc"] == 1
    assert ("ListenAndServe", "WriteFile") in _flow_pairs(verdict)
    assert _flow_pairs(verdict) == {("ListenAndServe", "WriteFile")}
