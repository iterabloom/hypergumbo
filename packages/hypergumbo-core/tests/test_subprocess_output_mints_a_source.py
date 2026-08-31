# SPDX-License-Identifier: AGPL-3.0-or-later
"""INV-lozat: a launch that HANDS BACK the child's bytes is a transfer.

ADR-0049 ruling 1 is a question about the RETURN VALUE — *does this call return
a value whose content the far side chose?* ``exec.Command(...).Output()``
answers yes: the bytes are the child's, chosen by a program hypergumbo cannot
see. So the call is a TRANSFER and must mint a taint source, exactly as
``net_recv`` does.

THE DEFECT, as the live repro found it. A Go program whose whole body is::

    out, _ := exec.Command("git", "log", "--format=%s").Output()
    exec.Command("sh", "-c", string(out)).Run()

returned ``confirmed_with_caveats`` for "data from an IPC channel never reaches
a subprocess" — and the caveat NAMED ``os/exec.Cmd.Output`` as a door it could
not see through. The tool was looking straight at the crossing and filing it as
opacity rather than as ingress.

WHY THE ITEM'S OWN DIAGNOSIS IS HALF WRONG, which is why this file pins the
shape rather than one language. INV-lozat says ``subprocess`` is "a SINK-ONLY
boundary in EVERY language" and that "no catalogue in the tree treats [the
return direction] as a source". Two do. ``rust.yaml`` declares
``std::process::Child.wait_with_output``, ``ChildStdout.read`` and
``ChildStderr.read`` under ``ipc_recv``, and its own note grounds that in
``python.yaml``'s ``os.wait`` / ``os.waitpid`` rows — *"Reaping a child collects
its exit status - data received from another process."* So the BOUNDARY QUESTION
the item asks ("which boundary? ipc_recv collapses into untrusted_input") was
already answered in the tree by two shipped catalogues citing each other, and
this change makes the rest of the corpus consistent with them rather than
inventing a vocabulary.

WHAT ``simultaneous`` IS DOING HERE, and why this is not INV-zumin's forbidden
shape. A content-returning launch is BOTH a launch (control leaves, the
arguments are a sink, the callee is opaque) AND a receive (the return value is
the child's). Nothing is undecidable and nothing needs disambiguating — both are
true at once, which is exactly the ``scala.sys.process.Process.apply`` case
:attr:`IoPrimitive.simultaneous` was built for. INV-zumin's ruling forbids
emitting several boundaries where EXACTLY ONE is true and the call site cannot
tell which (WI-lipis's bare-local handle wrapper); it does not forbid recording
two facts that are both true.

THE FAIL-CLOSED CONTROL AND WHY THIS CHANGE NEEDS ONE despite adding findings.
``CATALOG_BOUNDARY_TYPES`` lists ``ipc_recv`` BEFORE ``subprocess``, so the
ipc_recv row is parsed first and ``lookup_with_module`` returns it as the
PRIMARY ``io_boundary``. Had ``compute_boundary_map`` bucketed edges by that
single slot, adding these rows would have moved every ``Cmd.Output`` edge out of
the ``subprocess`` bucket and quietly weakened ``must_not_exist: subprocess`` —
a false all-clear shipped as a side effect of a recall fix.
:func:`test_the_launch_is_still_a_subprocess_chain` is the assertion that it
does not, and it is written to FAIL if the union in ``compute_boundary_map``
("one chain per simultaneously-true boundary", INV-zumin) is ever narrowed back
to the single slot.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional

import pytest

from hypergumbo_core.io_boundary import (
    MULTI_BOUNDARY_REASON_SIMULTANEOUS,
    OPAQUE_BOUNDARIES,
    IoBoundaryCatalog,
    IoPrimitive,
    compute_boundary_map,
    load_catalog,
    multi_boundary_reason,
)
from hypergumbo_core.taint import (
    AUTO_SINK_ZONE_MAP,
    AUTO_SOURCE_LABEL_MAP,
    _derive_auto_imports_from_io_primitives,
)

_CATALOG_DIR = (
    Path(__file__).resolve().parents[1]
    / "src" / "hypergumbo_core" / "io_primitives"
)

#: THE POPULATION, hand-classified against ADR-0049 ruling 1 and pinned here
#: because no predicate in the tree can derive it: whether a call hands back the
#: child's bytes is a fact about a foreign API's signature, not about anything
#: hypergumbo parses. It is a PIN and is disclosed as one — a new language's
#: content-returning launch will not fail this test, it will simply be absent.
#: What the tree-derived test below catches instead is the HALF-DECLARED row,
#: which is the failure that actually recurs.
#:
#: Each entry is ``(language, module, name, kind)``.
CONTENT_RETURNING_LAUNCHES: tuple[tuple[str, str, str, str], ...] = (
    # Returns ``([]byte, error)`` — the child's stdout, in hand.
    ("go", "os/exec.Cmd", "Output", "method"),
    ("go", "os/exec.Cmd", "CombinedOutput", "method"),
    # ``{output, exit_status}``.
    ("elixir", "System", "cmd", "function"),
    ("elixir", "System", "shell", "function"),
    # ``os:cmd/1`` returns the output string; erlang.yaml's own note says so.
    ("erlang", "os", "cmd", "function"),
    # The three that return stdout rather than a status.
    ("python", "subprocess", "check_output", "function"),
    ("python", "subprocess", "getoutput", "function"),
    ("python", "subprocess", "getstatusoutput", "function"),
    # ``Output { stdout, stderr, status }`` — the one-shot form of the
    # ``Child.wait_with_output`` this file's docstring cites as precedent.
    ("rust", "std::process::Command", "output", "method"),
    # ``readProcess`` and friends return the child's stdout as a String.
    ("haskell", "System.Process", "readProcess", "function"),
    ("haskell", "System.Process", "readProcessWithExitCode", "function"),
    ("haskell", "System.Process", "readCreateProcess", "function"),
    ("haskell", "System.Process", "readCreateProcessWithExitCode", "function"),
    # The synchronous three; the async siblings hand back a ChildProcess
    # HANDLE, which is a deferred crossing and deliberately not here.
    ("javascript", "child_process", "execSync", "function"),
    ("javascript", "child_process", "execFileSync", "function"),
    ("javascript", "child_process", "spawnSync", "function"),
    # A lazy Stream of the process's output lines.
    ("scala", "scala.sys.process.ProcessBuilder", "lineStream", "method"),
    ("scala", "scala.sys.process.ProcessBuilder", "lazyLines", "method"),
)

#: THE CONTROL POPULATION — launches whose return value carries NOTHING the
#: child chose (an exit status, an error, unit, or a handle). These must keep
#: minting no source, or the change has stopped being about the return value
#: and started being about the word "subprocess".
#:
#: The handle-returning forms (``Popen``, ``ProcessBuilder.start``,
#: ``Command.spawn``, ``createProcess``, ``popen``) are here for a DIFFERENT
#: reason from the status-returning ones: they are ADR-0049 deferred crossings,
#: the ``net_listen`` shape one channel over, and they are filed separately
#: rather than folded in silently.
CONTENTLESS_LAUNCHES: tuple[tuple[str, str, str, str], ...] = (
    ("go", "os/exec.Cmd", "Run", "method"),        # error
    ("go", "os/exec.Cmd", "Start", "method"),      # error
    ("go", "os/exec", "Command", "function"),      # *Cmd, nothing run yet
    ("python", "subprocess", "call", "function"),          # returncode
    ("python", "subprocess", "check_call", "function"),    # returncode
    ("python", "subprocess", "Popen", "function"),         # HANDLE (deferred)
    ("python", "os", "system", "function"),                # exit status
    ("rust", "std::process::Command", "status", "method"),  # ExitStatus
    ("rust", "std::process::Command", "spawn", "method"),   # HANDLE (deferred)
    ("haskell", "System.Process", "callProcess", "function"),   # ()
    ("haskell", "System.Process", "createProcess", "function"),  # HANDLEs
    ("java", "java.lang.ProcessBuilder", "start", "method"),     # HANDLE
    ("javascript", "child_process", "spawn", "function"),        # HANDLE
    ("c", "stdlib", "system", "function"),                       # exit status
)


def _sources_by_key() -> dict[tuple[str, str, str, str], object]:
    sources, _sinks, _amb = _derive_auto_imports_from_io_primitives(_CATALOG_DIR)
    return {
        (lang, s.module, s.name, s.kind): s
        for lang, entries in sources.items()
        for s in entries
    }


def _sinks_by_key() -> dict[tuple[str, str, str, str], object]:
    _sources, sinks, _amb = _derive_auto_imports_from_io_primitives(_CATALOG_DIR)
    return {
        (lang, s.module, s.name, s.kind): s
        for lang, entries in sinks.items()
        for s in entries
    }


class TestTheReturnValueIsTheQuestion:
    """ADR-0049 ruling 1, applied to the channel the ADR did not walk."""

    @pytest.mark.parametrize("key", CONTENT_RETURNING_LAUNCHES,
                             ids=lambda k: f"{k[0]}:{k[1]}.{k[2]}")
    def test_a_content_returning_launch_mints_an_untrusted_input_source(
        self, key: tuple[str, str, str, str],
    ) -> None:
        """THE DEFECT. The bytes are the child's; the catalogue said nothing."""
        source = _sources_by_key().get(key)
        assert source is not None, (
            f"{key[0]}:{key[1]}.{key[2]} returns bytes the CHILD chose and mints "
            "no taint source. ADR-0049 ruling 1 makes that a transfer, so the "
            "flow it starts is unrepresented — which is what WI-lipis's seven "
            "removed rows were standing in for."
        )
        assert source.taint_label == "untrusted_input"
        # WI-vazal: the label is many-to-one, so the boundary is what a reader
        # uses to tell "a program I launched told me" from "the network told
        # me". Losing it here is what INV-lozat's question 1 was afraid of.
        assert source.source_boundary == "ipc_recv"

    @pytest.mark.parametrize("key", CONTENTLESS_LAUNCHES,
                             ids=lambda k: f"{k[0]}:{k[1]}.{k[2]}")
    def test_a_launch_that_returns_no_content_mints_nothing(
        self, key: tuple[str, str, str, str],
    ) -> None:
        """THE CONTROL, and the one that has to keep costing something.

        Without it "a subprocess row mints a source" would pass just as well as
        "a subprocess row that HANDS BACK BYTES mints a source", and the second
        is the claim. An exit status is not the child's message; a handle is a
        crossing that has not happened yet.
        """
        assert _sources_by_key().get(key) is None, (
            f"{key[0]}:{key[1]}.{key[2]} returns no content the child chose, so "
            "it must mint no source. Minting here would make the rule 'the word "
            "subprocess appears' rather than ADR-0049 ruling 1."
        )

    @pytest.mark.parametrize("key", CONTENT_RETURNING_LAUNCHES,
                             ids=lambda k: f"{k[0]}:{k[1]}.{k[2]}")
    def test_the_launch_is_still_a_subprocess_sink(
        self, key: tuple[str, str, str, str],
    ) -> None:
        """NON-DESTRUCTION on the derivation arm.

        The receive is ADDITIONAL information about the same call. If declaring
        it displaced the launch, ``untrusted-input-no-subprocess`` would lose
        its sink at exactly the sites that just gained a source — the two
        halves of one finding, cancelling.
        """
        sink = _sinks_by_key().get(key)
        assert sink is not None, (
            f"{key[0]}:{key[1]}.{key[2]} stopped being a subprocess sink when it "
            "gained a source row."
        )
        assert sink.zone == "subprocess"

    @pytest.mark.parametrize("key", CONTENT_RETURNING_LAUNCHES,
                             ids=lambda k: f"{k[0]}:{k[1]}.{k[2]}")
    def test_the_launch_is_still_opaque(
        self, key: tuple[str, str, str, str],
    ) -> None:
        """NON-DESTRUCTION on the opacity arm (INV-gahuz).

        Knowing what the child SAID does not mean seeing what it DID.
        ``declares_opaque_crossing`` asks over every row for exactly this
        reason, and this pins that the second row does not race the first.
        """
        lang, module, name, _kind = key
        catalog = load_catalog(lang)
        assert catalog.declares_opaque_crossing(module, name), (
            f"{lang}:{module}.{name} stopped declaring an opaque crossing. "
            "Control still leaves this process; the return value says nothing "
            "about what the program did while it was gone."
        )


class TestTheHalfDeclaredRowIsWhatRecurs:
    """Derived from the tree rather than pinned, because this is the failure
    that repeats: a second boundary added in one YAML section while the marker
    stays on the other, which makes the pair live or inert depending on which
    section a later editor touched."""

    def test_every_dual_declared_launch_declares_simultaneous(self) -> None:
        found: list[str] = []
        for path in sorted(_CATALOG_DIR.glob("*.yaml")):
            catalog = load_catalog(path.stem)
            by_qname: dict[str, set[str]] = {}
            for prim in catalog.primitives:
                by_qname.setdefault(prim.qualified_name, set()).add(prim.boundary)
            for qname, boundaries in sorted(by_qname.items()):
                if not (boundaries & OPAQUE_BOUNDARIES):
                    continue
                if "ipc_recv" not in boundaries:
                    continue
                found.append(f"{path.stem}:{qname}")
                assert (
                    multi_boundary_reason(catalog, qname)
                    == MULTI_BOUNDARY_REASON_SIMULTANEOUS
                ), (
                    f"{path.stem}:{qname} is declared under both an opaque "
                    "launch boundary and ipc_recv, which is the genuinely-both "
                    "shape, but does not declare `simultaneous: true`. Without "
                    "the marker `io_boundaries` is not written and the call is "
                    "reported under whichever row parsed first."
                )
        assert found, (
            "no primitive anywhere declares both a launch and a receive, so "
            "this test is vacuous and INV-lozat's gap is open again."
        )

    def test_the_shape_spans_more_than_one_language(self) -> None:
        """The standing two-language rule. A design stated in one language's
        terms is a workaround wearing a design's name."""
        langs = {
            lang for (lang, _m, _n, _k) in CONTENT_RETURNING_LAUNCHES
        }
        assert len(langs) >= 2, langs


@dataclass
class _Edge:
    """The duck-typed edge ``tag_io_boundaries`` consumes (``edge_type``, not
    ``type``) — the same shape ``test_io_boundary.py`` builds."""

    src: str
    dst: str
    edge_type: str = "calls"
    meta: Optional[Dict[str, Any]] = None


class TestTheBoundaryMapKeepsBothChains:
    """The FAIL-CLOSED control for the primary-slot flip.

    ``ipc_recv`` sorts before ``subprocess`` in ``CATALOG_BOUNDARY_TYPES``, so
    the receive row is what ``lookup_with_module`` returns and the primary
    ``io_boundary`` slot on a ``Cmd.Output`` edge now reads ``ipc_recv``. That
    flip is observable and harmless ONLY because ``compute_boundary_map``
    unions ``io_boundaries`` (INV-zumin). Everything below exists to prove the
    launch chain survives it, because the alternative is a false all-clear
    shipped as a side effect of a recall fix.
    """

    @staticmethod
    def _edge() -> "_Edge":
        return _Edge(
            src="go:/app/main.go:10:relay:function",
            dst="go:/exec.go:1:os/exec.Cmd.Output:method",
            meta={"call_construct": "method"},
        )

    def test_the_launch_is_still_a_subprocess_chain(self) -> None:
        """Over the REAL shipped catalogue, not a hand-built stand-in — the
        assertion is about what this PR wrote into go.yaml."""
        edge = self._edge()
        bmap = compute_boundary_map([edge], {"go": load_catalog("go")})
        assert "subprocess" in set(bmap.entries), (
            "the receive row displaced the launch in the boundary map, so "
            "`must_not_exist: subprocess` now confirms over a live launch."
        )
        assert "ipc_recv" in set(bmap.entries), sorted(bmap.entries)
        # The flip itself, pinned so a reader learns it here rather than from a
        # surprising `io_boundary` value in a behaviour map.
        assert edge.meta is not None
        assert edge.meta["io_boundary"] == "ipc_recv"
        assert edge.meta["io_boundaries"] == ["ipc_recv", "subprocess"]

    def test_the_control_would_fail_without_the_marker(self) -> None:
        """PRE-REGISTERED REFUTATION: a gate that cannot fail proves nothing.

        Declared WITHOUT ``simultaneous``, ``io_boundaries`` is never written,
        the map falls back to the single flipped slot, and the subprocess chain
        does vanish — so the assertion above is discriminating rather than
        incidentally true.
        """
        catalog = IoBoundaryCatalog(
            language="go",
            primitives=[
                IoPrimitive(boundary="ipc_recv", module="os/exec.Cmd",
                            name="Output", kind="method"),
                IoPrimitive(boundary="subprocess", module="os/exec.Cmd",
                            name="Output", kind="method"),
            ],
        )
        bmap = compute_boundary_map([self._edge()], {"go": catalog})
        assert "subprocess" not in set(bmap.entries)
        assert "ipc_recv" in set(bmap.entries)


def test_ipc_recv_still_carries_the_untrusted_input_label() -> None:
    """The premise the whole change rests on, asserted rather than assumed.

    If ``ipc_recv`` ever stops deriving ``untrusted_input`` — or ``subprocess``
    stops deriving its sink zone — every row added for this item goes inert
    with no test failing anywhere near the YAML.
    """
    assert AUTO_SOURCE_LABEL_MAP["ipc_recv"] == "untrusted_input"
    assert AUTO_SINK_ZONE_MAP["subprocess"] == ("subprocess", "untrusted")
