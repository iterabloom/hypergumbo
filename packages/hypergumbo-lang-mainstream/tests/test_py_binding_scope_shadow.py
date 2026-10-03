# SPDX-License-Identifier: AGPL-3.0-or-later
"""INV-dulum + WI-safit: a name is resolved through an IMPORT (or as a BUILTIN)
only where no nearer VALUE binding shadows it.

ONE MISTAKE, TWO ROWS. Both defects are name resolution ignoring the scope of
the binding it consults.

* INV-dulum. ``module_imports`` (and the ``from``-import map ``imports``) is
  built by an ``ast.walk`` over the WHOLE FILE, so it is file-scoped. A
  parameter or local named ``socket`` still satisfied ``socket in
  module_imports``, and ``socket.socket(h)`` was typed as the stdlib socket --
  minting a ``net_send`` boundary and a taint sink for an object the caller
  handed in. Measured live before the fix::

      import socket
      def f(socket, h):
          s = socket.socket(h)      # calls python:socket:0-0:socket
          s.send(b"x")              # calls python:socket.socket:0-0:send

* WI-safit. The LEGB shadow the bare-name arms consult is a union over
  ``ScopeStack.frames``, and frames came only from ``FunctionDef`` nodes. A
  LAMBDA parameter contributed nothing, so ``lambda x, len=my_len: len(x)``
  minted ``python:builtins:0-0:len`` -- a FABRICATED BUILTIN, the StreamWriter
  defect the guard exists to prevent.

THE CURE IS ONE SCOPE MODEL, NOT TWO. A lambda (and a comprehension) extends
the caller's frame for its body (``ScopeStack.enter_subscope``), and each frame
records which of its names are bound to a VALUE (``Scope.value_names``), so
``ScopeStack.value_shadowed()`` answers "which import / builtin meanings are NOT
live here" by the same innermost-first LEGB walk the enclosing lookup uses.

THE TRAP, PINNED BY CONTROLS. ``_collect_bound_names`` folds import bindings in
with assignments -- right for def-shadowing, wrong here: a function-local
``import socket`` binds ``socket`` TO THE MODULE and must stay typed. So must a
``global socket`` declaration, and an inner function's own ``import socket``
under an outer parameter of the same name. Every shadow test is paired with a
no-shadow control in the same fixture that MUST stay typed: a control that
cannot fail is not a control, and the reach assertion comes first.
"""

from __future__ import annotations

from pathlib import Path

from hypergumbo_lang_mainstream.py import analyze_python


def _edges(tmp_path: Path, files: dict[str, str]) -> set[tuple[str, str, str]]:
    """``(edge_type, caller short name, dst)`` for every non-structural edge."""
    repo = tmp_path / "r"
    for rel, src in files.items():
        p = repo / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(src)
    out: set[tuple[str, str, str]] = set()
    for e in analyze_python(repo).edges:
        if e.edge_type in ("contains", "imports"):
            continue
        out.add((e.edge_type, e.src.split(":")[-2], e.dst))
    return out


def _from(edges: set[tuple[str, str, str]], caller: str) -> set[str]:
    return {d for _t, c, d in edges if c == caller}


def _typed(dsts: set[str], prefix: str) -> bool:
    return any(d.startswith(prefix) for d in dsts)


SOCKET_FIXTURE = '''\
import socket
import http.client


def param_shadow(socket, h):
    s = socket.socket(h)
    s.send(b"x")


def param_shadow_deep(http, h):
    c = http.client.HTTPConnection(h)
    c.request("GET", "/")


def local_assign_shadow(h, factory):
    socket = factory()
    s = socket.socket(h)
    s.send(b"x")


def chain_root_shadow(socket, h):
    socket.socket(h).send(b"x")


def with_shadow(socket, h):
    with socket.socket(h) as s:
        s.send(b"x")


def outer(socket):
    def inner(h):
        s = socket.socket(h)
        s.send(b"x")
    return inner


def lambda_shadow(h, mk):
    return (lambda socket: socket.socket(h).send(b"x"))(mk)


def control(h):
    s = socket.socket(h)
    s.send(b"x")


def control_deep(h):
    c = http.client.HTTPConnection(h)
    c.request("GET", "/")


def lazy_import(h):
    import socket
    s = socket.socket(h)
    s.send(b"x")


def global_decl(h):
    global socket
    s = socket.socket(h)
    s.send(b"x")


def outer2(socket):
    def inner2(h):
        import socket
        s = socket.socket(h)
        s.send(b"x")
    return inner2


def try_import(h):
    try:
        import socket
    except ImportError:
        socket = None
    s = socket.socket(h)
    s.send(b"x")
'''


class TestModuleImportShadowWithholdsTheType:
    """INV-dulum: a value binding of a module's name withholds the module type."""

    def test_reach_the_no_shadow_controls_are_typed(self, tmp_path: Path) -> None:
        """ASSERT REACH FIRST: if these are not typed, every refusal below is
        vacuously true."""
        e = _edges(tmp_path, {"app.py": SOCKET_FIXTURE})
        assert _typed(_from(e, "control"), "python:socket.socket:0-0:send")
        assert _typed(_from(e, "control"), "python:socket:0-0:socket")
        assert _typed(
            _from(e, "control_deep"), "python:http.client.HTTPConnection:0-0:request",
        )

    def test_a_parameter_shadow_is_not_typed(self, tmp_path: Path) -> None:
        dsts = _from(_edges(tmp_path, {"app.py": SOCKET_FIXTURE}), "param_shadow")
        assert not _typed(dsts, "python:socket"), sorted(dsts)
        # The call is still a call -- on an untyped receiver.
        assert "python:external:0-0:send:unresolved" in dsts, sorted(dsts)

    def test_a_parameter_shadow_at_depth_two_is_not_typed(
        self, tmp_path: Path,
    ) -> None:
        dsts = _from(
            _edges(tmp_path, {"app.py": SOCKET_FIXTURE}), "param_shadow_deep",
        )
        assert not _typed(dsts, "python:http.client"), sorted(dsts)

    def test_a_local_assignment_shadow_is_not_typed(self, tmp_path: Path) -> None:
        dsts = _from(
            _edges(tmp_path, {"app.py": SOCKET_FIXTURE}), "local_assign_shadow",
        )
        assert not _typed(dsts, "python:socket"), sorted(dsts)

    def test_the_chain_root_and_with_forms_are_not_typed(
        self, tmp_path: Path,
    ) -> None:
        e = _edges(tmp_path, {"app.py": SOCKET_FIXTURE})
        for caller in ("chain_root_shadow", "with_shadow"):
            dsts = _from(e, caller)
            assert not _typed(dsts, "python:socket"), (caller, sorted(dsts))

    def test_a_closure_captured_parameter_shadow_is_not_typed(
        self, tmp_path: Path,
    ) -> None:
        dsts = _from(_edges(tmp_path, {"app.py": SOCKET_FIXTURE}), "outer.inner")
        assert not _typed(dsts, "python:socket"), sorted(dsts)

    def test_a_lambda_parameter_shadow_is_not_typed(self, tmp_path: Path) -> None:
        dsts = _from(_edges(tmp_path, {"app.py": SOCKET_FIXTURE}), "lambda_shadow")
        assert not _typed(dsts, "python:socket"), sorted(dsts)


class TestAnImportBindingStaysLive:
    """THE TRAP: an import binding is not a shadow -- it IS the module."""

    def test_a_function_local_import_is_still_typed(self, tmp_path: Path) -> None:
        dsts = _from(_edges(tmp_path, {"app.py": SOCKET_FIXTURE}), "lazy_import")
        assert _typed(dsts, "python:socket.socket:0-0:send"), sorted(dsts)

    def test_a_global_declaration_is_still_typed(self, tmp_path: Path) -> None:
        dsts = _from(_edges(tmp_path, {"app.py": SOCKET_FIXTURE}), "global_decl")
        assert _typed(dsts, "python:socket.socket:0-0:send"), sorted(dsts)

    def test_a_nearer_import_beats_an_outer_parameter(
        self, tmp_path: Path,
    ) -> None:
        dsts = _from(_edges(tmp_path, {"app.py": SOCKET_FIXTURE}), "outer2.inner2")
        assert _typed(dsts, "python:socket.socket:0-0:send"), sorted(dsts)

    def test_an_import_with_a_fallback_assignment_is_still_typed(
        self, tmp_path: Path,
    ) -> None:
        """``try: import socket / except ImportError: socket = None`` -- the
        import is the binding every method call can actually reach (``None``
        has no ``socket`` attribute), so the import wins within its frame."""
        dsts = _from(_edges(tmp_path, {"app.py": SOCKET_FIXTURE}), "try_import")
        assert _typed(dsts, "python:socket.socket:0-0:send"), sorted(dsts)


NAME_FIXTURE = '''\
from pathlib import Path


def name_shadow(Path, raw):
    Path(raw).write_text("x")


def name_control(raw):
    Path(raw).write_text("x")


def builtin_shadow(open, p):
    open(p).read()


def builtin_control(p):
    open(p).read()
'''


class TestBareNameConstructorShadow:
    """The same rule on the bare-name branch: a ``from`` import and a builtin."""

    def test_reach_the_controls_are_typed(self, tmp_path: Path) -> None:
        e = _edges(tmp_path, {"app.py": NAME_FIXTURE})
        assert _typed(_from(e, "name_control"), "python:pathlib.Path:0-0:write_text")
        assert _typed(_from(e, "builtin_control"), "python:file:0-0:read")

    def test_a_parameter_named_like_a_from_import_is_not_typed(
        self, tmp_path: Path,
    ) -> None:
        dsts = _from(_edges(tmp_path, {"app.py": NAME_FIXTURE}), "name_shadow")
        assert not _typed(dsts, "python:pathlib"), sorted(dsts)

    def test_a_parameter_named_like_a_builtin_constructor_is_not_typed(
        self, tmp_path: Path,
    ) -> None:
        dsts = _from(_edges(tmp_path, {"app.py": NAME_FIXTURE}), "builtin_shadow")
        assert not _typed(dsts, "python:file"), sorted(dsts)
        assert not _typed(dsts, "python:builtins"), sorted(dsts)


INIT_FIXTURE = '''\
import socket


class Shadowed:
    def __init__(self, socket):
        self.s = socket.socket()

    def m(self):
        self.s.send(b"x")


class Control:
    def __init__(self):
        self.s = socket.socket()

    def m(self):
        self.s.send(b"x")
'''


class TestFieldTypingHonorsTheInitScope:
    """The ``__init__`` field pre-pass is the third consumer of the constructor
    resolver; ``self.s = socket.socket()`` under a ``socket`` PARAMETER must not
    type the field."""

    def test_reach_the_control_field_is_typed(self, tmp_path: Path) -> None:
        dsts = _from(_edges(tmp_path, {"app.py": INIT_FIXTURE}), "Control.m")
        assert _typed(dsts, "python:socket.socket:0-0:send"), sorted(dsts)

    def test_a_shadowed_constructor_does_not_type_the_field(
        self, tmp_path: Path,
    ) -> None:
        dsts = _from(_edges(tmp_path, {"app.py": INIT_FIXTURE}), "Shadowed.m")
        assert not _typed(dsts, "python:socket"), sorted(dsts)


INREPO_FILES = {
    "pkg/__init__.py": "",
    "pkg/models.py": "class Order:\n    def save(self):\n        pass\n",
    "app.py": (
        "import os\n"
        "import socket\n"
        "import pkg.models as models\n\n\n"
        "def inrepo_shadow(models):\n    models.Order()\n\n\n"
        "def inrepo_control():\n    models.Order()\n\n\n"
        "def assign_shadow(models):\n    o = models.Order()\n    o.save()\n\n\n"
        "def assign_control():\n    o = models.Order()\n    o.save()\n\n\n"
        "def shape_shadow(socket):\n    socket.sendall(b'lit')\n\n\n"
        "def shape_control():\n    os.system('ls')\n"
    ),
}


class TestInRepoModuleAliasShadow:
    """Case 2b and the assignment resolver consult the same file-scoped map for
    an IN-REPO module; a shadowing parameter must not resolve into it."""

    def test_reach_the_controls_resolve(self, tmp_path: Path) -> None:
        e = _edges(tmp_path, INREPO_FILES)
        assert any(
            t == "instantiates" and d.endswith(":Order:class")
            for t, c, d in e if c == "inrepo_control"
        )
        assert any(d.endswith(":Order.save:method") for d in _from(e, "assign_control"))

    def test_a_shadowed_alias_does_not_resolve_into_the_module(
        self, tmp_path: Path,
    ) -> None:
        e = _edges(tmp_path, INREPO_FILES)
        # Into the module = a dst in pkg/models.py. An honest
        # ``python:external:0-0:Order:unresolved`` (an attribute call on a
        # value) is the correct outcome, not a failure.
        for caller in ("inrepo_shadow", "assign_shadow"):
            dsts = _from(e, caller)
            assert dsts, f"{caller}: no edges at all -- fixture did not reach"
            assert not any("pkg/models.py" in d for d in dsts), (caller, sorted(dsts))


class TestALiteralArgumentOnAShadowedAliasIsNotInert:
    """``_receiver_cannot_carry_taint`` trusts a MODULE receiver. A parameter
    named like a module is a VALUE, which may be tainted, so ``call_arg_shape``
    must not be stamped there -- the silenced-finding direction."""

    def test_shapes(self, tmp_path: Path) -> None:
        repo = tmp_path / "r"
        for rel, src in INREPO_FILES.items():
            p = repo / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(src)
        shapes = {
            e.src.split(":")[-2]: (e.meta or {}).get("call_arg_shape")
            for e in analyze_python(repo).edges
            if e.edge_type == "calls"
            and e.src.split(":")[-2] in ("shape_shadow", "shape_control")
        }
        assert shapes.get("shape_control") == "literal_only", shapes  # reach
        assert "shape_shadow" in shapes, shapes
        assert shapes["shape_shadow"] is None, shapes


LAMBDA_FIXTURE = '''\
def lam(xs, my_len):
    return sorted(xs, key=lambda x, len=my_len: len(x))


def lam_external(orig, ctx):
    return (lambda c, _o=orig: _o(c))(ctx)


def lam_nested(f):
    return (lambda len: (lambda x: len(x)))(f)


def lam_default(xs):
    return (lambda len=len(xs): len)()


def lam_control(xs):
    return sorted(xs, key=lambda x: len(x))
'''


class TestLambdaParametersShadow:
    """WI-safit: a lambda parameter is a binding like any other."""

    def test_reach_the_control_mints_the_real_builtin(self, tmp_path: Path) -> None:
        dsts = _from(_edges(tmp_path, {"app.py": LAMBDA_FIXTURE}), "lam_control")
        assert "python:builtins:0-0:len:unresolved" in dsts, sorted(dsts)

    def test_a_lambda_parameter_named_like_a_builtin_mints_no_builtin(
        self, tmp_path: Path,
    ) -> None:
        dsts = _from(_edges(tmp_path, {"app.py": LAMBDA_FIXTURE}), "lam")
        assert "python:builtins:0-0:len:unresolved" not in dsts, sorted(dsts)
        assert "python:builtins:0-0:sorted:unresolved" in dsts, sorted(dsts)

    def test_a_lambda_parameter_mints_no_external_placeholder(
        self, tmp_path: Path,
    ) -> None:
        """The measured site: test_finalize.py's ``_o(ctx)``."""
        dsts = _from(_edges(tmp_path, {"app.py": LAMBDA_FIXTURE}), "lam_external")
        assert "python:external:0-0:_o:unresolved" not in dsts, sorted(dsts)

    def test_an_outer_lambda_parameter_shadows_inside_a_nested_lambda(
        self, tmp_path: Path,
    ) -> None:
        dsts = _from(_edges(tmp_path, {"app.py": LAMBDA_FIXTURE}), "lam_nested")
        assert "python:builtins:0-0:len:unresolved" not in dsts, sorted(dsts)

    def test_a_default_is_evaluated_in_the_enclosing_scope(
        self, tmp_path: Path,
    ) -> None:
        """``lambda len=len(xs): ...`` -- the DEFAULT calls the real builtin,
        because defaults run before the lambda's scope exists. Pushing the
        frame over the defaults too would lose this edge."""
        dsts = _from(_edges(tmp_path, {"app.py": LAMBDA_FIXTURE}), "lam_default")
        assert "python:builtins:0-0:len:unresolved" in dsts, sorted(dsts)


ATTR_FIXTURE = '''\
import os


def attr_shadow(os):
    return os.environ["X"]


def attr_outer(os):
    def inner():
        return os.environ["X"]
    return inner


def attr_lazy():
    import os
    return os.environ["X"]


def attr_control():
    return os.environ["X"]
'''


class TestModuleAttributeReadShadow:
    """The ``module_attr_ref`` emitter is a fourth consumer of the file-scoped
    ``module_imports``. ``def f(os): os.environ[...]`` emitted
    ``python:os:0-0:os.environ:attribute`` and io-boundaries reported an
    ``env_read`` that does not exist."""

    def test_reach_the_controls_emit(self, tmp_path: Path) -> None:
        e = _edges(tmp_path, {"app.py": ATTR_FIXTURE})
        want = "python:os:0-0:os.environ:attribute"
        assert want in _from(e, "attr_control")
        assert want in _from(e, "attr_lazy"), "a lazy import IS the module"

    def test_a_parameter_named_like_the_module_emits_no_attribute_read(
        self, tmp_path: Path,
    ) -> None:
        e = _edges(tmp_path, {"app.py": ATTR_FIXTURE})
        assert not _typed(_from(e, "attr_shadow"), "python:os"), sorted(
            _from(e, "attr_shadow"))
        assert not _typed(_from(e, "attr_outer.inner"), "python:os"), sorted(
            _from(e, "attr_outer.inner"))
