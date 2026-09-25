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

    # MUST FIND: socket.socket.recv (untrusted_input) reaches os.mkdir
    # (host_fs) through a first-party wrapper, along a forward call chain.
    (src / "reaches.py").write_text(
        "import socket\n"
        "import os\n"
        "\n"
        "\n"
        "def persist(name):\n"
        "    os.mkdir(name)\n"
        "\n"
        "\n"
        "async def serve():\n"
        "    server = socket.socket().recv(1024)\n"
        "    persist(str(server))\n"
        "    return server\n",
        encoding="utf-8",
    )

    # MUST NOT FIND: a source with nothing downstream of it. A propagator that
    # keys on source presence rather than on reachability reports this.
    (src / "no_sink.py").write_text(
        "import socket\n"
        "\n"
        "\n"
        "async def listen_only():\n"
        "    return socket.socket().recv(2048)\n",
        encoding="utf-8",
    )

    result = _run(tmp_path, capsys)
    verdict = _verdict(result["envelope"], "no-untrusted-to-fs")

    # RECALL — the reachable flow is present, and the run is therefore not
    # vacuous. Everything below this line is only meaningful because of it.
    assert verdict["verdict"] == "violated"
    assert result["rc"] == 1
    assert ("recv", "mkdir") in _flow_pairs(verdict)

    # PRECISION — exactly one distinct source->sink pair. ``listen_only``
    # contributes a source with no reachable sink and must add nothing.
    assert _flow_pairs(verdict) == {("recv", "mkdir")}


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
    # THE RECEIVER IS AN ANNOTATED PARAMETER, not ``socket.socket()``. With the
    # constructor form, this repo's ONLY call into the ``socket`` module is the
    # constructor itself, which no catalogue row classifies -- so the coverage
    # gate withholds and the verdict is ``inconclusive``, and this test would
    # pass its "no evidence" line while failing its "confirmed" one. The
    # annotated parameter removes the unclassified call and leaves the clean
    # examined negative this test is actually about.
    (src / "listener.py").write_text(
        "import socket\n"
        "\n"
        "\n"
        "def serve(conn: socket.socket):\n"
        "    return conn.recv(1024)\n",
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
        "import socket\n"
        "import os\n"
        "\n"
        "\n"
        "async def obtain():\n"
        "    return socket.socket().recv(1024)\n"
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

    # MUST FIND: net.Conn.Read (untrusted_input) reaches os.WriteFile
    # (host_fs) through a first-party wrapper. The receiver is a DECLARED
    # PARAMETER, which is the form that resolves -- `ln, _ := net.Listen(...)`
    # then `ln.Accept()` does not (WI-lalot), so a fixture written that way
    # would measure the receiver-typing gap rather than the propagator.
    (tmp_path / "reaches.go").write_text(
        "package corpus\n"
        "\n"
        "import (\n"
        "\t\"net\"\n"
        "\t\"os\"\n"
        ")\n"
        "\n"
        "func persist(name string) {\n"
        "\tos.WriteFile(name, []byte(\"x\"), 0644)\n"
        "}\n"
        "\n"
        "func Serve(conn net.Conn) {\n"
        "\tbuf := make([]byte, 16)\n"
        "\tconn.Read(buf)\n"
        "\tpersist(string(buf))\n"
        "}\n",
        encoding="utf-8",
    )

    # MUST NOT FIND: a source with nothing downstream.
    (tmp_path / "no_sink.go").write_text(
        "package corpus\n"
        "\n"
        "import \"net\"\n"
        "\n"
        "func ReadOnly(conn net.Conn) {\n"
        "\tbuf := make([]byte, 16)\n"
        "\tconn.Read(buf)\n"
        "}\n",
        encoding="utf-8",
    )

    result = _run(tmp_path, capsys)
    verdict = _verdict(result["envelope"], "no-untrusted-to-fs")

    assert verdict["verdict"] == "violated"
    assert result["rc"] == 1
    assert ("Read", "WriteFile") in _flow_pairs(verdict)
    assert _flow_pairs(verdict) == {("Read", "WriteFile")}


# --------------------------------------------------------------------------
# ADR-0017 §3a — data-flow adjudication
# --------------------------------------------------------------------------

def _source_names(verdict: dict) -> set:
    """Enclosing function name for each evidence row's source symbol."""
    return {
        row["source_symbol"].split(":")[-2]
        for row in verdict.get("evidence", [])
    }


def _confidence_by_source(verdict: dict) -> dict:
    """``{source function name: (confidence, analysis_method)}``.

    The label is the whole subject of INV-sadah, and until this helper existed
    no test in the corpus read it — the one test named for the ``precise``
    label asserted only recall.
    """
    return {
        row["source_symbol"].split(":")[-2]: (
            row.get("confidence"), row.get("analysis_method"),
        )
        for row in verdict.get("evidence", [])
    }


def test_ddg_labels_a_data_connected_flow_as_precise(
    tmp_path: Path, capsys,
) -> None:
    """§3a earns the precise label on a real data dependence; both flows stay.

    ``connected`` passes the source's return value to the sink; ``disconnected``
    passes an unrelated parameter. Only the first has a data dependence, and
    the walk finds it — but NEITHER is removed, because a sound refutation
    needs ADR-0017 §4 function summaries (§3a step 3) and those have no
    production caller. See ``_ddg_taint_reaches``.

    Both cases live in one repo deliberately: an implementation that stopped
    reporting would satisfy any precision claim while failing recall here.
    """
    src = tmp_path / "src"
    src.mkdir()

    (src / "connected.py").write_text(
        "import socket\n"
        "import os\n"
        "\n"
        "\n"
        "async def connected(name):\n"
        "    server = socket.socket().recv(1024)\n"
        "    os.mkdir(str(server))\n"
        "    return server\n",
        encoding="utf-8",
    )

    (src / "disconnected.py").write_text(
        "import socket\n"
        "import os\n"
        "\n"
        "\n"
        "async def disconnected(name):\n"
        "    server = socket.socket().recv(1024)\n"
        "    os.mkdir(name)\n"
        "    return server\n",
        encoding="utf-8",
    )

    result = _run(tmp_path, capsys)
    verdict = _verdict(result["envelope"], "no-untrusted-to-fs")

    # RECALL — both flows are still reported. Inclusion remains call-graph
    # reachability; §3a confirms, it does not refute.
    assert _source_names(verdict) == {"connected", "disconnected"}

    # AND THE LABEL ITSELF, which this test was named for and did not check.
    # "precise" appeared only in the function name and the docstring; the sole
    # assertion was recall, so the label §3a exists to earn had no test
    # anywhere in the corpus. That is why an unearned one shipped (INV-sadah).
    labels = _confidence_by_source(verdict)
    assert labels["connected"] == ("precise", "ddg"), labels
    assert labels["disconnected"] == ("approximate", "ddg_mixed"), labels


def test_ddg_does_not_credit_a_dependence_across_unrelated_definitions(
    tmp_path: Path, capsys,
) -> None:
    """A shared def_line must not launder taint between unrelated variables.

    THE DEFECT (INV-sadah, found by a review panel). The walk's index was keyed
    ``(symbol_id, def_line)`` with ``DdgEdge.variable`` discarded, so every
    variable defined on one line shared a single entry and their use-sets were
    merged. ``conflated`` below is the minimal real shape::

        6   server = socket.socket().recv(1024)        # the SOURCE
        7   keep = str(server); path = name            # uses server, defines path
        8   os.mkdir(path)                             # the SINK, on `path`

    ``path`` derives from the parameter ``name``, not from ``server`` — there
    is no data dependence from source to sink. But the production DDG emits
    ``server def@6 -> use@7``, ``keep def@7 -> use@9`` and
    ``path def@7 -> use@8``, and a line-keyed index collapses the last two into
    ``def_line 7 -> {8, 9}``. The walk steps 6 -> 7 -> 8, finds the sink line,
    and stamps ``precise``/``ddg`` on a dependence that does not exist.

    WHY VARIABLE-KEYING ALONE IS NOT THE FIX, stated because it looks like it
    should be: ``path`` genuinely IS defined at line 7. Separating the entries
    still leaves the walk asking which variable defined at 7 inherits taint
    from ``server``, and the DDG edge set alone cannot say. Only the
    statement-level pairing answers it — ``keep = str(server)`` uses
    ``server``, ``path = name`` does not.

    THE OTHER TWO ARMS ARE CONTROLS, and they are what stops a fix from
    passing by simply labelling nothing (L57): ``connected`` has a real
    dependence and must KEEP ``precise``; ``disconnected`` has none and must
    stay ``approximate``, which it already did. Exactly one cell may move.
    """
    src = tmp_path / "src"
    src.mkdir()

    (src / "connected.py").write_text(
        "import socket\n"
        "import os\n"
        "\n"
        "\n"
        "async def connected(name):\n"
        "    server = socket.socket().recv(1024)\n"
        "    os.mkdir(str(server))\n"
        "    return server\n",
        encoding="utf-8",
    )
    (src / "disconnected.py").write_text(
        "import socket\n"
        "import os\n"
        "\n"
        "\n"
        "async def disconnected(name):\n"
        "    server = socket.socket().recv(1024)\n"
        "    os.mkdir(name)\n"
        "    return server\n",
        encoding="utf-8",
    )
    (src / "conflated.py").write_text(
        "import socket\n"
        "import os\n"
        "\n"
        "\n"
        "async def conflated(name):\n"
        "    server = socket.socket().recv(1024)\n"
        "    keep = str(server); path = name\n"
        "    os.mkdir(path)\n"
        "    return keep\n",
        encoding="utf-8",
    )

    verdict = _verdict(_run(tmp_path, capsys)["envelope"], "no-untrusted-to-fs")

    # RECALL FIRST — nothing may stop being reported. §3a confirms, it does
    # not refute, so all three flows survive whatever the label says.
    assert _source_names(verdict) == {"connected", "disconnected", "conflated"}

    labels = _confidence_by_source(verdict)
    # The control that must not move.
    assert labels["connected"] == ("precise", "ddg"), labels
    # Already correct, and must stay correct.
    assert labels["disconnected"] == ("approximate", "ddg_mixed"), labels
    # THE DEFECT: no data dependence exists, so no precision may be claimed.
    assert labels["conflated"] == ("approximate", "ddg_mixed"), labels


# --------------------------------------------------------------------------
# Cross-language arm — the adjudication label must be a property of the flow
# --------------------------------------------------------------------------

_JS_LEAK = (
    "const http = require('http');\n"
    "const fs = require('fs');\n"
    "\n"
    "function leak() {\n"
    "  const server = http.createServer();\n"
    "  fs.writeFileSync('/tmp/out.txt', String(server));\n"
    "}\n"
    "\n"
    "module.exports = { leak };\n"
)

#: A Python file with no bearing on the JavaScript flow above. It is here only
#: so the repo contains a language that HAS a def/use extractor, which is the
#: whole variable under test. Its own flow is ``host_secret`` -> ``host_fs``,
#: a different taint label from the claim, so it contributes no evidence row —
#: asserted below rather than assumed.
_UNRELATED_PYTHON = (
    "import os\n"
    "import shutil\n"
    "\n"
    "\n"
    "def stage_home() -> str:\n"
    "    home = os.path.expanduser('~')\n"
    "    target = home + '/staged'\n"
    "    shutil.copy(home, target)\n"
    "    return target\n"
)

#: A Java file that exists ONLY to put a data-flow-INCAPABLE language in the
#: scope table. It deliberately makes no contribution to any verdict — measured,
#: not assumed: run standalone against the claim above it produces a
#: ``confirmed`` verdict with ZERO evidence rows, because nothing resolves
#: ``server.accept()`` to the catalogued ``java.net.ServerSocket.accept``.
#:
#: That is exactly what these tests need. JavaScript held this role until
#: WI-nonad wired it, and the scope tests would otherwise have had no incapable
#: language left to distinguish FROM — which is the difference between a gate
#: and a green tick. Java is the durable choice because it has a cfg mapping and
#: lacks the other three prerequisites, so ``blockers`` is a strict subset.
_JAVA_INCAPABLE = (
    "import java.io.File;\n"
    "import java.net.ServerSocket;\n"
    "\n"
    "public class Leak {\n"
    "    public static void leak() throws Exception {\n"
    "        ServerSocket server = new ServerSocket(8080);\n"
    "        server.accept();\n"
    "        File out = new File(\"/tmp/out.txt\");\n"
    "        out.createNewFile();\n"
    "    }\n"
    "}\n"
)


def test_adjudication_label_does_not_depend_on_another_language(
    tmp_path: Path, capsys,
) -> None:
    """Adding an unrelated-language file must not relabel a JavaScript flow.

    INV-karud clause (a3) requires that a reader can tell flows adjudicated by
    data flow from flows resting on call reachability alone, *from the emitted
    record*. The field carrying that distinction is ``analysis_method``.

    It was not a property of the flow. ``cmd_verify_claims`` builds one DDG for
    the whole repo and then dispatches per language on ``if ddg_edges:`` — a
    repo-global truthiness test — so every language went through
    ``propagate_taint_ddg`` whenever ANY language produced DDG edges. A
    JavaScript function is never in ``ddg_symbols`` (JavaScript has no def/use
    extractor), so the walk could not run and the finding still came out
    ``ddg_mixed``, whose documented meaning is "the walk ran and did not
    confirm". Drop the Python file and the identical JavaScript came out
    ``structural``.

    Two arms differing by one file nobody's claim mentions. This asserts the
    whole record is identical, not just the label, because a relabelling bug
    and a flow-loss bug are both invisible in a single-field comparison (L57:
    when the correct result is "no change", the broken arm can look better).

    WHAT THIS TEST LOST WHEN JAVASCRIPT WAS WIRED (WI-nonad), stated rather
    than quietly absorbed. Its discriminating power came from the flow's own
    language being INCAPABLE — only then does "the label was set by another
    language's presence" produce a visible difference between the arms.
    JavaScript is now capable, so both arms are legitimately ``ddg``, and a
    reintroduced repo-global dispatch would no longer show up here.

    The invariant itself is NOT left unguarded. It is pinned at unit level by
    ``test_taint.py::test_function_the_ddg_never_saw_is_structural_not_mixed``,
    which builds the same shape synthetically — ``ddg_symbols={"other_fn"}``,
    i.e. the DDG has edges but does not cover the source function — and asserts
    ``approximate`` / ``structural``. That test is language-agnostic and cannot
    be invalidated by wiring a language, which makes it the better home for the
    property than a fixture whose validity depended on a language staying
    unwired.

    What is genuinely gone is the END-TO-END confirmation through a real
    incapable language. JavaScript was the only one that both produced a
    fixture flow and lacked an extractor; java, c, php and ruby were each tried
    as replacements and every one yields ZERO evidence rows, so there is no
    drop-in substitute. This test therefore keeps the cross-arm identity
    assertion — which still catches flow loss and any relabelling driven by
    another language's presence — and the scope tests below now carry a real
    incapable language so that half stays exercised end to end.
    """
    js_only = tmp_path / "js_only"
    js_and_py = tmp_path / "js_and_py"
    for repo in (js_only, js_and_py):
        (repo / "src").mkdir(parents=True)
        (repo / "src" / "leak.js").write_text(_JS_LEAK, encoding="utf-8")
    (js_and_py / "src" / "util.py").write_text(
        _UNRELATED_PYTHON, encoding="utf-8",
    )

    alone_env = _run(js_only, capsys)["envelope"]
    mixed_env = _run(js_and_py, capsys)["envelope"]
    alone = _verdict(alone_env, "no-untrusted-to-fs")
    mixed = _verdict(mixed_env, "no-untrusted-to-fs")

    # RECALL FIRST. A vacuous pass here is trivial — two empty evidence lists
    # are also identical — so the flow must be present in both arms before any
    # comparison between them means anything (L17).
    assert _flow_pairs(alone) == {("createServer", "writeFileSync")}
    assert _flow_pairs(mixed) == {("createServer", "writeFileSync")}

    # The Python file contributes no evidence to THIS claim, so the two arms
    # are comparable row-for-row. Stated as an assertion because the whole
    # test rests on it.
    assert alone["evidence_count"] == mixed["evidence_count"] == 1

    # JavaScript IS data-flow capable since WI-nonad, so the walk runs on this
    # flow and confirms it in BOTH arms. These constants used to read
    # ("approximate", "structural") / {"structural": 1}; see the docstring for
    # what that change costs and where the cost is paid.
    assert _confidence_by_source(alone) == {"leak": ("precise", "ddg")}
    assert _confidence_by_source(mixed) == _confidence_by_source(alone)
    assert alone["analysis_methods"] == {"ddg": 1}
    assert mixed["analysis_methods"] == alone["analysis_methods"]

    # POSITIVE CONTROL, and the point of the fix. The two repos DO differ, and
    # that difference must still be visible — it has moved from a field where
    # it was a lie (this flow's adjudication label) to one where it is a fact
    # (which languages were analyzed and what each is capable of). A test that
    # only asserted "nothing changed" would also pass if the scope block had
    # been dropped entirely.
    def _langs(env: dict) -> set:
        return {r["language"] for r in env["dataflow_coverage"]["languages"]}

    assert _langs(alone_env) == {"javascript"}
    assert _langs(mixed_env) == {"javascript", "python"}


# --------------------------------------------------------------------------
# Published scope — INV-karud clause (a3)
# --------------------------------------------------------------------------

def _run_text(repo: Path, capsys) -> str:
    claims_file = repo / "claims.yaml"
    claims_file.write_text(yaml.dump(_CLAIMS), encoding="utf-8")
    args = _Args(repo, claims_file)
    args.format = "text"
    cmd_verify_claims(args)
    return capsys.readouterr().out


def test_published_scope_distinguishes_capable_from_incapable(
    tmp_path: Path, capsys,
) -> None:
    """The emitted record must state the scope, not leave it to assumption.

    Per-flow ``analysis_method`` cannot answer "could this language have been
    adjudicated at all?", and without that a ``structural`` label is
    uninterpretable: it reads as "the walk looked and found nothing" when it
    may mean "nothing here was capable of looking". This asserts languages in
    one repo come out on opposite sides, so a table that hardcoded either
    answer fails.

    THE INCAPABLE EXEMPLAR IS JAVA, not JavaScript. JavaScript held that role
    until WI-nonad wired it, and the swap is deliberate rather than cosmetic: a
    test that distinguishes capable from incapable is vacuous the moment every
    language in its fixture is capable, and flipping the JavaScript assertion
    to ``True`` without adding a replacement would have left exactly that.
    Java is a better long-term exemplar anyway — it has a cfg mapping but no
    ``atomic_statement`` and no extractor, so it exercises the PARTIAL case
    where blockers are a strict subset rather than everything.
    """
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "leak.js").write_text(_JS_LEAK, encoding="utf-8")
    (tmp_path / "src" / "util.py").write_text(
        _UNRELATED_PYTHON, encoding="utf-8",
    )
    (tmp_path / "src" / "Leak.java").write_text(_JAVA_INCAPABLE, encoding="utf-8")

    scope = _run(tmp_path, capsys)["envelope"]["dataflow_coverage"]
    by_lang = {row["language"]: row for row in scope["languages"]}

    assert by_lang["python"]["dataflow_capable"] is True
    assert by_lang["python"]["blockers"] == []
    # WI-nonad: javascript moved to this side of the line, and pinning it here
    # is what stops the wiring from silently regressing.
    assert by_lang["javascript"]["dataflow_capable"] is True
    assert by_lang["javascript"]["blockers"] == []
    assert by_lang["java"]["dataflow_capable"] is False
    # The blockers are the actionable half — "not covered" without saying
    # which of the four independent prerequisites is missing is a status, not
    # a scope.
    assert "def_use_extractor" in by_lang["java"]["blockers"]
    # Java HAS a cfg mapping, so this is the partial case: the missing pieces
    # are named and the present one is not slandered.
    assert "cfg_mapping" not in by_lang["java"]["blockers"]
    # The catalog the uncovered language would have served is the disclosure
    # that matters: 69 Java sinks are unreachable by data flow.
    assert by_lang["java"]["catalog_sinks"] > 50

    # The a2 fact, machine-readable rather than prose (R16). Re-pointed
    # 2026-09-02 when WI-kabif granted §3a removal authority: no flow's
    # INCLUSION is decided by data flow (reachability still decides that, and
    # the walk mints nothing), but the walk may now SUBTRACT a flow it refutes.
    assert scope["inclusion_decided_by"] == (
        "call_graph_reachability_minus_ddg_refutation"
    )
    assert scope["findings_total"] == sum(
        scope["findings_by_analysis_method"].values(),
    )


def test_published_scope_reaches_the_text_view(tmp_path: Path, capsys) -> None:
    """A disclosure that exists only under ``--json`` is half shipped.

    WI-bifob's exclusion bucket reached the dataclass and never the text
    renderer, so a text reader of a violated claim never learned flows had
    been set aside. This is the same disclosure on the same surface, pinned.

    Carries the Java file for the same reason the test above does: the
    ``def_use_extractor`` assertion is about a BLOCKER string reaching the text
    renderer, and a repo whose languages are all capable has no blockers to
    render — the assertion would fail, and "fixing" it by deleting the line
    would drop the only end-to-end check that blockers reach text at all.
    """
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "leak.js").write_text(_JS_LEAK, encoding="utf-8")
    (tmp_path / "src" / "Leak.java").write_text(_JAVA_INCAPABLE, encoding="utf-8")

    out = _run_text(tmp_path, capsys)

    assert "Data-flow coverage" in out
    assert "javascript" in out
    assert "java" in out
    assert "def_use_extractor" in out
    assert "call-graph reachability" in out


# --------------------------------------------------------------------------
# Published sanitizer scope — INV-karud clause (b)
# --------------------------------------------------------------------------

def test_sanitizer_scope_reaches_both_real_surfaces(
    tmp_path: Path, capsys,
) -> None:
    """The block must arrive through the REAL CLI, on both surfaces.

    ``dataflow_scope`` is unit-tested, and that is not this test's question. A
    unit test of ``dataflow_scope_dict`` passes just as happily when
    ``cmd_verify_claims`` forgets to compute the scope and passes ``None`` —
    the key is still present and every field reads zero. L13: a component
    correct at unit level can be inert in the pipeline, and the way to tell is
    to assert a value only the real wiring can produce.

    So this asserts the catalogue actually reached the emitted record: a
    non-zero entry count and the crypto categories the shipped
    ``taint_sanitizers/encryption.yaml`` declares. The comparison is against
    the loaded catalogue rather than a literal, so extending the catalogue
    moves the test with it.
    """
    from hypergumbo_core.dataflow_scope import (
        SAME_FUNCTION_SANITIZATION_HONOURED_BY,
    )
    from hypergumbo_core.taint import load_builtin_taint_catalog

    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "leak.js").write_text(_JS_LEAK, encoding="utf-8")
    (tmp_path / "src" / "util.py").write_text(
        _UNRELATED_PYTHON, encoding="utf-8",
    )

    scope = _run(tmp_path, capsys)["envelope"]["dataflow_coverage"][
        "sanitizer_scope"
    ]

    catalog = load_builtin_taint_catalog()
    expected = {
        f"{s.input_taint} -> {s.output_taint}"
        for s in catalog.sanitizers_for_language("python")
    }
    assert expected, "non-vacuity: python must declare sanitizers"
    assert scope["total"] > 0, (
        "zero here means the CLI never computed the scope — the exact "
        "failure a unit test of the dict builder cannot see"
    )
    assert expected <= set(scope["taint_categories"])
    assert "plaintext" in scope["sanitizable_labels"]
    assert scope["same_function_honoured_by"] == list(
        SAME_FUNCTION_SANITIZATION_HONOURED_BY
    )
    assert "structural" not in scope["same_function_honoured_by"]

    # The text surface carries the same two limits — the vocabulary one (a
    # zero must read as "not expressible", not "not protected") and the
    # same-function one.
    out = _run_text(tmp_path, capsys)
    assert "Sanitizers:" in out
    assert "not expressible" in out
    assert "SAME function" in out
