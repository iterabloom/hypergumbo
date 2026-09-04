# SPDX-License-Identifier: AGPL-3.0-or-later
"""``obj.attr.method()`` carries the module of ``attr`` when ``obj`` is a project
class whose field the class itself typed (INV-mumov, the attribute-chain slice).

INV-mumov's live gap after PR #231 and PR #752: an attribute-chain receiver
EMITS, but its module slot is the ``external`` placeholder, so a catalogued
method-kind sink reached through the chain stays unmatchable. The class already
types its own fields — ``class_external_field_types[C][field]`` is filled from
``__init__`` (``self.conn = http.client.HTTPConnection(p)``, ``self.sock: socket.socket``)
and reaches ``self.<field>.method()`` INSIDE the class (INV-fibis) — but a
caller holding an instance of ``C`` in a typed local or parameter
(``svc = Service(); svc.conn.request(q)``) never asked that map. Now it does,
through the one receiver-type predicate, so the inline and assigned forms and
a two-hop chain through a project-class field (``app.svc.conn.request``) all
name the same module.
"""
from __future__ import annotations

from pathlib import Path

from hypergumbo_core.io_boundary import load_catalog, tag_io_boundaries
from hypergumbo_core.ir import Edge
from hypergumbo_lang_mainstream.py import analyze_python


def _edges(root: Path, files: dict[str, str]) -> list[Edge]:
    root.mkdir(parents=True, exist_ok=True)
    for name, src in files.items():
        (root / name).write_text(src)
    return analyze_python(root).edges


def _slot(edges: list[Edge], method: str) -> str:
    hits = [e.dst for e in edges if e.edge_type == "calls" and e.dst.endswith(f":{method}:unresolved")]
    assert len(hits) == 1, [e.dst for e in edges if method in e.dst]
    return hits[0].split(":")[1]


def _tagged(edges: list[Edge]) -> int:
    return tag_io_boundaries(edges, {"python": load_catalog("python")})


SERVICE = (
    "import http.client\n"
    "import socket\n"
    "\n"
    "class Service:\n"
    "    def __init__(self, p):\n"
    "        self.conn = http.client.HTTPConnection(p)\n"
    "        self.sock = socket.socket()\n"
)


class TestAttributeChainThroughAProjectClassField:
    def test_local_bound_to_the_class(self, tmp_path: Path) -> None:
        edges = _edges(tmp_path / "loc", {
            "svc.py": SERVICE,
            "app.py": (
                "from svc import Service\n"
                "\n"
                "def run(q, payload):\n"
                "    svc = Service('/tmp/db')\n"
                "    svc.conn.request(q)\n"
                "    svc.sock.sendall(payload)\n"
            ),
        })
        assert _slot(edges, "request") == "http.client.HTTPConnection"
        assert _slot(edges, "sendall") == "socket.socket"
        assert _tagged(edges) >= 2

    def test_annotated_parameter(self, tmp_path: Path) -> None:
        edges = _edges(tmp_path / "param", {
            "svc.py": SERVICE,
            "app.py": (
                "from svc import Service\n"
                "\n"
                "def run(svc: Service, q):\n"
                "    svc.conn.request(q)\n"
            ),
        })
        assert _slot(edges, "request") == "http.client.HTTPConnection"

    def test_two_hops_through_a_project_class_field(self, tmp_path: Path) -> None:
        """``app.svc.conn.request(q)``: ``app.svc`` is a project class, then its field."""
        edges = _edges(tmp_path / "hops", {
            "svc.py": SERVICE,
            "app.py": (
                "from svc import Service\n"
                "\n"
                "class App:\n"
                "    def __init__(self):\n"
                "        self.svc = Service('/tmp/db')\n"
                "\n"
                "def run(app: App, q):\n"
                "    app.svc.conn.request(q)\n"
            ),
        })
        assert _slot(edges, "request") == "http.client.HTTPConnection"

    def test_an_unknown_field_keeps_the_placeholder(self, tmp_path: Path) -> None:
        edges = _edges(tmp_path / "unk", {
            "svc.py": SERVICE,
            "app.py": (
                "from svc import Service\n"
                "\n"
                "def run(svc: Service, q):\n"
                "    svc.other.request(q)\n"
            ),
        })
        assert _slot(edges, "request") == "external"
        # The class's own ``socket.socket()`` construction is catalogued; the
        # chain through the unknown field must not add to it.
        assert not [
            e for e in edges
            if (e.meta or {}).get("io_boundary") and "request" in e.dst
        ]

    def test_a_field_of_a_non_project_receiver_keeps_the_placeholder(
        self, tmp_path: Path,
    ) -> None:
        edges = _edges(tmp_path / "ext", {
            "app.py": (
                "def run(obj, q):\n"
                "    obj.conn.request(q)\n"
            ),
        })
        assert _slot(edges, "request") == "external"


SOCK_SERVICE = (
    "import socket\n"
    "\n"
    "class Service:\n"
    "    def __init__(self):\n"
    "        self.sock = socket.socket()\n"
)


class TestFieldPathsFollowTheirRoot:
    def test_rebinding_the_root_purges_its_field_paths(self, tmp_path: Path) -> None:
        """``svc`` rebound to a socket: ``svc.sock`` must not survive the rebind."""
        edges = _edges(tmp_path / "rb", {
            "svc.py": SOCK_SERVICE,
            "app.py": (
                "import socket\n"
                "from svc import Service\n"
                "\n"
                "def run(x):\n"
                "    svc = Service()\n"
                "    svc = socket.socket()\n"
                "    svc.sendall(x)\n"
                "    svc.sock.sendall(x)\n"
            ),
        })
        sends = sorted(e.dst.split(":")[1] for e in edges if e.dst.endswith(":sendall:unresolved"))
        assert sends == ["external", "socket.socket"], sends

    def test_annotated_and_derived_rebinds_purge_too(self, tmp_path: Path) -> None:
        edges = _edges(tmp_path / "rb2", {
            "svc.py": SOCK_SERVICE,
            "app.py": (
                "from pathlib import Path\n"
                "from svc import Service\n"
                "\n"
                "def run(x, d: Path):\n"
                "    a = Service()\n"
                "    a: Path = d\n"
                "    a.sock.sendall(x)\n"
                "    b = Service()\n"
                "    b = d / 'f'\n"
                "    b.sock.sendall(x)\n"
            ),
        })
        sends = [e.dst.split(":")[1] for e in edges if e.dst.endswith(":sendall:unresolved")]
        assert sends == ["external", "external"], sends

    def test_comprehension_shadowing_the_root_prunes_the_path(self, tmp_path: Path) -> None:
        edges = _edges(tmp_path / "sh", {
            "svc.py": SOCK_SERVICE,
            "app.py": (
                "from svc import Service\n"
                "\n"
                "def run(x, items):\n"
                "    svc = Service()\n"
                "    return [svc.sock.sendall(x) for svc in items]\n"
            ),
        })
        sends = [e.dst.split(":")[1] for e in edges if e.dst.endswith(":sendall:unresolved")]
        assert sends == ["external"], sends

    def test_self_field_of_a_project_class_two_hops(self, tmp_path: Path) -> None:
        edges = _edges(tmp_path / "self2", {
            "svc.py": SOCK_SERVICE,
            "app.py": (
                "from svc import Service\n"
                "\n"
                "class App:\n"
                "    def __init__(self):\n"
                "        self.svc = Service()\n"
                "\n"
                "    def run(self, x):\n"
                "        self.svc.sock.sendall(x)\n"
            ),
        })
        assert _slot(edges, "sendall") == "socket.socket"

    def test_chain_rooted_at_a_subscript_stays_with_the_placeholder(self, tmp_path: Path) -> None:
        edges = _edges(tmp_path / "sub", {
            "app.py": (
                "def run(x, items):\n"
                "    items[0].sock.sendall(x)\n"
            ),
        })
        assert _slot(edges, "sendall") == "external"


class TestSingleFileFallback:
    """``extract_nodes`` analyses one file with no repo-wide maps: the per-file
    maps serve, keyed by bare class name, and a same-name twin is refused."""

    def test_same_file_class(self, tmp_path: Path) -> None:
        from hypergumbo_lang_mainstream.py import extract_nodes
        p = tmp_path / "one.py"
        p.write_text(
            SOCK_SERVICE
            + "\n"
            "def run(x):\n"
            "    svc = Service()\n"
            "    svc.sock.sendall(x)\n"
        )
        assert _slot(extract_nodes(p).edges, "sendall") == "socket.socket"

    def test_twin_classes_are_refused(self, tmp_path: Path) -> None:
        from hypergumbo_lang_mainstream.py import extract_nodes
        p = tmp_path / "twin.py"
        p.write_text(
            SOCK_SERVICE
            + "\n"
            "class Service:\n"
            "    def __init__(self):\n"
            "        self.sock = 3\n"
            "\n"
            "def run(x):\n"
            "    svc = Service()\n"
            "    svc.sock.sendall(x)\n"
        )
        assert _slot(extract_nodes(p).edges, "sendall") == "external"

    def test_twin_classes_in_another_file_are_refused_repo_wide(self, tmp_path: Path) -> None:
        """The repo-wide maps drop a same-short-name twin the way the fallback does."""
        edges = _edges(tmp_path / "twin2", {
            "svc.py": (
                SOCK_SERVICE
                + "\n"
                "class Service:\n"
                "    def __init__(self):\n"
                "        self.sock = 3\n"
            ),
            "app.py": (
                "from svc import Service\n"
                "\n"
                "def run(x):\n"
                "    svc = Service()\n"
                "    svc.sock.sendall(x)\n"
            ),
        })
        assert _slot(edges, "sendall") == "external"
