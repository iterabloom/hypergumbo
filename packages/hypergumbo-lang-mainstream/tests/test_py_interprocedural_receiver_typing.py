# SPDX-License-Identifier: AGPL-3.0-or-later
"""INV-fibis residual: a receiver that arrives as a bare parameter is typed
from the call sites that pass it.

WHAT WAS MISSING. WI-zilag routed an ANNOTATED parameter's type into the module
slot, so ``def h(p: pathlib.Path, x): p.write_text(x)`` reaches the catalogue.
The residual INV-fibis pinned is the same shape without the annotation::

    import socket

    def send(sock, payload):        # no annotation
        return sock.sendall(payload)

    def main():
        s = socket.socket()
        send(s, b"hi")              # <- the type is RIGHT HERE

``sock`` names no module, so the edge carried the ``external`` placeholder and
every consumer refused it as an untyped method call. Measured on the merged
tree before this change, with a two-arm fixture differing only in the
annotation: ANNOTATED -> ``violated``, 1 net_send chain; UNANNOTATED ->
``inconclusive``, no chain. Exactly one of the two shapes was seen, and the
call site carried the answer for the other.

WHY IT IS THE CALL SITE AND NOT A GUESS. A parameter's type is not inferable
from its body — that is what makes this interprocedural. What IS available is
every argument the repository passes at that position. This types the parameter
only when EVERY observed call site agrees on one module, because a minted hint
is TRUSTED downstream: it bypasses both ``gate_named_entry`` and the
``ambiguous_names`` net by design (routing the hinted path through that gate was
measured to destroy 61.5-87.2% of all reported boundaries for zero gain). So a
wrong hint is a confident false boundary AND a false taint sink, never silence
— the same reason WI-zilag's annotation route is binding-checked.

SCOPE, STATED AS SCOPE RATHER THAN LEFT TO BE DISCOVERED:

* Only calls whose callee is a BARE NAME (``send(...)``) resolvable to an
  in-repo function or method definition. A call through a receiver
  (``obj.send(...)``) is excluded because resolving it needs receiver typing,
  which is the thing being computed.
* Only POSITIONAL arguments. A keyword argument would need the parameter's
  name, which is available, but no measured payload justified the surface.
* Only parameters with NO annotation and no in-repo class type. An annotated
  parameter already has a better answer; an in-repo class is first-party and
  carries no catalogue meaning.
* The argument expression must itself name an external type through an import
  binding — a constructor call (``socket.socket()``, ``requests.Session()``) or
  a local already typed by one. NOT catalogue-gated: ``requests.Session`` has
  zero rows in python.yaml (INV-fotav), and refusing to type it would keep the
  precise case this residual was filed for invisible.
* ONE HOP. A parameter typed by this pass does not itself become evidence for
  a further parameter; that would need a fixed point, and no measurement
  justifies one.
"""

from pathlib import Path

from hypergumbo_core.io_boundary import load_catalog, tag_io_boundaries
from hypergumbo_lang_mainstream.py import analyze_python


def _edges(root: Path, **files: str) -> list:
    root.mkdir(parents=True, exist_ok=True)
    for name, source in files.items():
        (root / f"{name}.py").write_text(source)
    return analyze_python(root).edges


def _slot(edges: list, method: str) -> str:
    """Module segment of the call edge for ``method``, or '' if no edge."""
    hits = [e.dst for e in edges if e.dst.endswith(f":{method}:unresolved")]
    return hits[0].split(":")[1] if hits else ""


def _tagged(edges: list) -> int:
    return tag_io_boundaries(edges, {"python": load_catalog("python")})


class TestTheRepro:
    """The shape INV-fibis pinned, measured on the pre-change tree."""

    def test_a_call_site_types_an_unannotated_receiver(
        self, tmp_path: Path,
    ) -> None:
        """Pre-change this emitted ``python:external:0-0:sendall:unresolved``
        and produced ZERO net_send boundaries."""
        edges = _edges(
            tmp_path / "repro",
            app=(
                "import socket\n"
                "\n"
                "def send(sock, payload):\n"
                "    return sock.sendall(payload)\n"
                "\n"
                "def main():\n"
                "    s = socket.socket()\n"
                "    send(s, b'hi')\n"
            ),
        )
        assert _slot(edges, "sendall") == "socket.socket"
        assert _tagged(edges) >= 1

    def test_the_uncatalogued_third_party_case(self, tmp_path: Path) -> None:
        """``requests.Session`` has ZERO rows in python.yaml (INV-fotav), and
        this pass is deliberately not catalogue-gated: the module slot is filled
        so the uncovered-module disclosure can name it, which is what turns a
        silent ``confirmed`` into an honest ``inconclusive``."""
        edges = _edges(
            tmp_path / "thirdparty",
            app=(
                "import requests\n"
                "\n"
                "def upload(session, url, payload):\n"
                "    return session.post(url, data=payload)\n"
                "\n"
                "def main():\n"
                "    s = requests.Session()\n"
                "    upload(s, 'https://x/y', {})\n"
            ),
        )
        assert _slot(edges, "post") == "requests.Session"

    def test_it_works_across_files(self, tmp_path: Path) -> None:
        """The call site is frequently not in the callee's file; a per-file
        pass would miss the shape entirely."""
        edges = _edges(
            tmp_path / "crossfile",
            lib=(
                "def send(sock, payload):\n"
                "    return sock.sendall(payload)\n"
            ),
            main=(
                "import socket\n"
                "from lib import send\n"
                "\n"
                "def go():\n"
                "    s = socket.socket()\n"
                "    send(s, b'hi')\n"
            ),
        )
        assert _slot(edges, "sendall") == "socket.socket"


class TestItRefusesWhereItCouldBeWrong:
    """A minted hint is trusted downstream, so every refusal below is the
    difference between silence and a confident false boundary."""

    def test_disagreeing_call_sites_type_nothing(self, tmp_path: Path) -> None:
        """Two call sites, two different types. Picking either would be a coin
        flip stamped as fact."""
        edges = _edges(
            tmp_path / "disagree",
            app=(
                "import socket\n"
                "import pathlib\n"
                "\n"
                "def use(thing, payload):\n"
                "    return thing.write(payload)\n"
                "\n"
                "def a():\n"
                "    use(socket.socket(), b'x')\n"
                "\n"
                "def b():\n"
                "    use(pathlib.Path('/tmp/f'), b'x')\n"
            ),
        )
        assert _slot(edges, "write") == "external"

    def test_an_annotation_wins_over_a_call_site(self, tmp_path: Path) -> None:
        """The declaration is the better evidence and it is checked; a call
        site that disagrees with it must not overwrite it."""
        edges = _edges(
            tmp_path / "annotwins",
            app=(
                "import socket\n"
                "import pathlib\n"
                "\n"
                "def send(sock: pathlib.Path, payload):\n"
                "    return sock.write_text(payload)\n"
                "\n"
                "def main():\n"
                "    send(socket.socket(), 'x')\n"
            ),
        )
        assert _slot(edges, "write_text") == "pathlib.Path"

    def test_an_in_repo_class_argument_types_nothing(
        self, tmp_path: Path,
    ) -> None:
        """A first-party class carries no catalogue meaning, and minting its
        name into the module slot would invite a suffix collision with a
        library type of the same name — the measured WI-zilag failure."""
        edges = _edges(
            tmp_path / "firstparty",
            app=(
                "class Session:\n"
                "    def post(self, url):\n"
                "        return url\n"
                "\n"
                "def upload(session, url):\n"
                "    return session.post(url)\n"
                "\n"
                "def main():\n"
                "    upload(Session(), 'x')\n"
            ),
        )
        assert _slot(edges, "post") in ("", "external")

    def test_a_function_result_is_not_a_type(self, tmp_path: Path) -> None:
        """THE CORPUS FOUND THIS, NOT REVIEW, and the first cut shipped it
        wrong. ``x = json.dumps(y)`` has the identical AST shape to
        ``s = socket.socket()`` — ``module.attr(...)`` — so resolving the
        argument through import bindings alone asserted ``json.dumps`` is a
        TYPE. Measured on poetry: **7 of 19 agreed hints (37%) were function
        results** (``json.dumps``, ``typing.cast``, ``tomlkit.inline_table``,
        ``parse_constraint``, two ``get_*`` helpers).

        Not merely useless: ``io_boundary._module_matches`` does bidirectional
        SUBSTRING matching, so ``json.dumps`` in the module slot can match a
        catalogue module ``json`` — a confident false boundary and a false
        taint sink. An argument must be shown to name a type, either by the
        receiver-type catalogue (which knows ``socket.socket``) or by PEP 8's
        PascalCase class convention (which is what admits
        ``requests.Session``).
        """
        edges = _edges(
            tmp_path / "funcresult",
            app=(
                "import json\n"
                "\n"
                "def use(blob):\n"
                "    return blob.write('x')\n"
                "\n"
                "def main():\n"
                "    b = json.dumps({})\n"
                "    use(b)\n"
            ),
        )
        assert _slot(edges, "write") == "external"

    def test_a_lowercase_type_still_resolves_through_the_catalogue(
        self, tmp_path: Path,
    ) -> None:
        """Positive control for the gate above: the PascalCase rule alone would
        refuse ``socket.socket``, which is lowercase and is the flagship case.
        The catalogued-receiver route runs FIRST precisely so it does not."""
        edges = _edges(
            tmp_path / "lowercase",
            app=(
                "import socket\n"
                "\n"
                "def send(sock, payload):\n"
                "    return sock.sendall(payload)\n"
                "\n"
                "def main():\n"
                "    send(socket.socket(), b'x')\n"
            ),
        )
        assert _slot(edges, "sendall") == "socket.socket"

    def test_a_method_call_site_is_not_used(self, tmp_path: Path) -> None:
        """``obj.send(x)`` needs receiver typing to resolve its callee, which
        is the thing being computed. Excluded rather than guessed."""
        edges = _edges(
            tmp_path / "methodsite",
            app=(
                "import socket\n"
                "\n"
                "def send(sock, payload):\n"
                "    return sock.sendall(payload)\n"
                "\n"
                "class Holder:\n"
                "    def send(self, sock, payload):\n"
                "        return sock.sendall(payload)\n"
                "\n"
                "def main(h):\n"
                "    h.send(socket.socket(), b'x')\n"
            ),
        )
        assert _slot(edges, "sendall") == "external"

    def test_an_UNRESOLVABLE_annotation_still_blocks_the_call_site(
        self, tmp_path: Path,
    ) -> None:
        """An annotation the binding check cannot resolve — a forward
        reference, a generic, a locally-defined name — leaves the parameter
        with NO module hint. The call-site route must still not fill it.

        The author wrote a type there. That the resolver cannot turn it into a
        module is a limitation of the resolver, not permission to substitute a
        guess from somewhere else: if the two disagree the annotation is right
        and the hint is a confident false boundary.
        """
        edges = _edges(
            tmp_path / "unresolvable",
            app=(
                "import socket\n"
                "\n"
                "def send(sock: 'Widget', payload):\n"
                "    return sock.sendall(payload)\n"
                "\n"
                "def main():\n"
                "    send(socket.socket(), b'x')\n"
            ),
        )
        assert _slot(edges, "sendall") == "external"

    def test_a_splatted_call_site_stops_at_the_splat(
        self, tmp_path: Path,
    ) -> None:
        """``send(*args, sock)`` makes every position after the splat
        unknowable — ``sock`` could land anywhere. Positions BEFORE it are
        still sound, so the walk stops at the splat rather than discarding the
        call site."""
        edges = _edges(
            tmp_path / "splat",
            app=(
                "import socket\n"
                "\n"
                "def send(payload, sock):\n"
                "    return sock.sendall(payload)\n"
                "\n"
                "def main(args):\n"
                "    send(*args, socket.socket())\n"
            ),
        )
        assert _slot(edges, "sendall") == "external"

    def test_a_positionally_mismatched_argument_types_nothing(
        self, tmp_path: Path,
    ) -> None:
        """Position is the whole binding here. An argument at position 1 must
        never type the parameter at position 0."""
        edges = _edges(
            tmp_path / "position",
            app=(
                "import socket\n"
                "\n"
                "def send(payload, sock):\n"
                "    return payload.sendall(sock)\n"
                "\n"
                "def main():\n"
                "    send(b'x', socket.socket())\n"
            ),
        )
        assert _slot(edges, "sendall") == "external"


class TestNonDestruction:
    """A recall widening must not disturb what already worked (L7)."""

    def test_a_repo_with_no_such_shape_is_unchanged(
        self, tmp_path: Path,
    ) -> None:
        """Positive control: the annotated route still resolves, and nothing
        here depends on the new pass."""
        edges = _edges(
            tmp_path / "unchanged",
            app=(
                "import pathlib\n"
                "\n"
                "def h(p: pathlib.Path, x):\n"
                "    return p.write_text(x)\n"
            ),
        )
        assert _slot(edges, "write_text") == "pathlib.Path"
        assert _tagged(edges) >= 1

    def test_a_local_constructor_receiver_still_resolves(
        self, tmp_path: Path,
    ) -> None:
        """WI-fuvuj's own shape, untouched."""
        edges = _edges(
            tmp_path / "local",
            app=(
                "import socket\n"
                "\n"
                "def h(payload):\n"
                "    s = socket.socket()\n"
                "    return s.sendall(payload)\n"
            ),
        )
        assert _slot(edges, "sendall") == "socket.socket"


class TestTheResidualThisDoesNotClose:
    """Stated so the next reader does not re-derive it."""

    def test_one_hop_only(self, tmp_path: Path) -> None:
        """A parameter typed by this pass is NOT evidence for a further
        parameter. Closing that needs a fixed point over the call graph, and
        no measurement justifies one."""
        edges = _edges(
            tmp_path / "twohop",
            app=(
                "import socket\n"
                "\n"
                "def inner(sock, payload):\n"
                "    return sock.sendall(payload)\n"
                "\n"
                "def outer(sock, payload):\n"
                "    return inner(sock, payload)\n"
                "\n"
                "def main():\n"
                "    outer(socket.socket(), b'x')\n"
            ),
        )
        assert _slot(edges, "sendall") == "external"
