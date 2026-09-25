# SPDX-License-Identifier: AGPL-3.0-or-later
"""A C socket send/recv's boundary is a property of its descriptor's FAMILY.

WI-baran, INV-nular's c member from measurement 0012. ``c.yaml`` had
``sys/socket.{send,sendto,sendmsg}`` under ``net_send`` and
``{recv,recvfrom,recvmsg}`` under ``net_recv`` unconditionally, and tmux --
which opens no AF_INET socket anywhere (client.c:122 and server.c:123 are
AF_UNIX, proc.c:364 is a socketpair) -- reported ``sendmsg`` over its
client/server pair as a NETWORK send (0012, VACUOUS:KIND-MISDECLARED).

WHAT DECIDES IT is the socket family, fixed where the descriptor was created:
``socket(AF_UNIX, ..)`` / ``socketpair(..)`` is process-local (``pipe``),
``socket(AF_INET|AF_INET6, ..)`` is a network stream (``net_stream``). That is
INV-vaduk's rule for ``unistd.read`` / ``write`` -- "an fd's nature is
established where it was opened" -- applied one hop back through the fd
variable, by the same last-binding helper WI-lipis wrote for ``fgets``'s
stream argument. ONE ANSWER OR NONE: a parameter, a struct field, a kernel
socket (``PF_NETLINK``, ``PF_PACKET`` -- neither another process nor the
network) or a family this table does not know stamps nothing, and an
unstamped call classifies exactly as before (``abstains_to: net_*``).

MEASURED BEFORE BUILDING, on the twelve C repositories in the corpus that call
these six functions (324 sites; ``~/hypergumbo_lab_notebook/baran_sockets_09062026``):
the descriptor argument is a bare identifier at 222 sites, a struct field at
77, an array element (``sv[0]`` after ``socketpair``) at 10. tmux's own two
sites pass the descriptor as a PARAMETER (compat/imsg-buffer.c) and abstain
here: the instance that filed the item needs the cross-function hop
(WI-famig), and the item records that.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from hypergumbo_lang_mainstream.c import analyze_c

HDR = "#include <sys/socket.h>\n"


def _kind(tmp_path: Path, source: str, callee: str) -> object:
    """``io_target_kind`` stamped on the call edge for *callee*, or None.

    Keyed on the CALLEE rather than on a boundary tag, as the stream-origin
    tests are: a consumer-side regression must not surface here as "the
    analyzer emitted nothing".
    """
    (tmp_path / "main.c").write_text(source)
    edges = [e for e in analyze_c(tmp_path).edges
             if e.edge_type == "calls" and f":{callee}:" in e.dst]
    assert len(edges) == 1, [e.dst for e in edges]
    return (edges[0].meta or {}).get("io_target_kind")


class TestAUnixSocketIsProcessLocal:
    def test_an_af_unix_binding_stamps_pipe(self, tmp_path):
        assert _kind(tmp_path, HDR + "void f(const char *b) {\n"
                     "  int s = socket(AF_UNIX, SOCK_STREAM, 0);\n"
                     "  send(s, b, 1, 0);\n}\n", "send") == "pipe"

    @pytest.mark.parametrize("family", ["PF_UNIX", "AF_LOCAL", "PF_LOCAL"])
    def test_the_other_spellings_of_local(self, tmp_path, family):
        assert _kind(tmp_path, HDR + "void f(const char *b) {\n"
                     f"  int s = socket({family}, SOCK_DGRAM | SOCK_CLOEXEC, 0);\n"
                     "  sendto(s, b, 1, 0, 0, 0);\n}\n", "sendto") == "pipe"

    def test_an_assignment_inside_a_condition_is_a_binding(self, tmp_path):
        """tmux's own opening shape: ``if ((fd = socket(AF_UNIX, ..)) == -1)``."""
        assert _kind(tmp_path, HDR + "void f(struct msghdr *m) {\n"
                     "  int fd;\n"
                     "  if ((fd = socket(AF_UNIX, SOCK_STREAM, 0)) == -1) return;\n"
                     "  sendmsg(fd, m, 0);\n}\n", "sendmsg") == "pipe"

    def test_a_socketpair_element_stamps_pipe_at_both_ends(self, tmp_path):
        """``socketpair`` binds an ARRAY by argument, not by assignment, so
        the last-binding helper cannot see it; the pair is found by the call."""
        src = (HDR + "void f(char *b) {\n"
               "  int sv[2];\n"
               "  socketpair(AF_UNIX, SOCK_STREAM, 0, sv);\n"
               "  send(sv[0], b, 1, 0);\n"
               "  recv(sv[1], b, 1, 0);\n}\n")
        assert _kind(tmp_path, src, "send") == "pipe"
        assert _kind(tmp_path, src, "recv") == "pipe"

    def test_an_inline_socket_call_is_classified_directly(self, tmp_path):
        assert _kind(tmp_path, HDR + "void f(const char *b) {\n"
                     "  send(socket(AF_UNIX, SOCK_STREAM, 0), b, 1, 0);\n}\n",
                     "send") == "pipe"


class TestAnInternetSocketIsANetworkStream:
    @pytest.mark.parametrize("family", ["AF_INET", "AF_INET6", "PF_INET", "PF_INET6"])
    def test_an_inet_binding_stamps_net_stream(self, tmp_path, family):
        assert _kind(tmp_path, HDR + "void f(char *b) {\n"
                     f"  int s = socket({family}, SOCK_STREAM, 0);\n"
                     "  recv(s, b, 1, 0);\n}\n", "recv") == "net_stream"

    def test_accept_inherits_the_listening_sockets_family(self, tmp_path):
        """The accepted connection has the family of the socket it came from:
        one more hop, through ``accept``'s first argument, and no further."""
        assert _kind(tmp_path, HDR + "void f(char *b) {\n"
                     "  int l = socket(AF_INET, SOCK_STREAM, 0);\n"
                     "  int c = accept(l, 0, 0);\n"
                     "  recvfrom(c, b, 1, 0, 0, 0);\n}\n", "recvfrom") == "net_stream"

    def test_accept4_over_a_local_listener_is_a_pipe(self, tmp_path):
        assert _kind(tmp_path, HDR + "void f(struct msghdr *m) {\n"
                     "  int l = socket(AF_UNIX, SOCK_STREAM, 0);\n"
                     "  int c = accept4(l, 0, 0, SOCK_CLOEXEC);\n"
                     "  recvmsg(c, m, 0);\n}\n", "recvmsg") == "pipe"


class TestEveryTransferTakesItsDescriptorFirst:
    @pytest.mark.parametrize("call", [
        "send(s, b, 1, 0)", "sendto(s, b, 1, 0, 0, 0)", "sendmsg(s, 0, 0)",
        "recv(s, b, 1, 0)", "recvfrom(s, b, 1, 0, 0, 0)", "recvmsg(s, 0, 0)",
    ])
    def test_the_six_transfers(self, tmp_path, call):
        callee = call.split("(", 1)[0]
        assert _kind(tmp_path, HDR + "void f(char *b) {\n"
                     "  int s = socket(AF_UNIX, SOCK_STREAM, 0);\n"
                     f"  {call};\n}}\n", callee) == "pipe"


class TestUnprovableOriginsStampNothing:
    """INV-zumin: one answer or none. Every case here classifies exactly as
    it did before the stamp existed (the ``abstains_to: net_*`` row)."""

    def test_a_parameter_descriptor_stamps_nothing(self, tmp_path):
        """tmux's two live sites: ``msgbuf_write(int fd, ..)`` / ``recvmsg(fd, ..)``."""
        assert _kind(tmp_path, HDR + "void f(int fd, struct msghdr *m) {\n"
                     "  sendmsg(fd, m, 0);\n}\n", "sendmsg") is None

    def test_a_struct_field_descriptor_stamps_nothing(self, tmp_path):
        assert _kind(tmp_path, HDR + "struct c { int fd; };\n"
                     "void f(struct c *c, char *b) { recv(c->fd, b, 1, 0); }\n",
                     "recv") is None

    @pytest.mark.parametrize("family", ["PF_NETLINK", "PF_PACKET", "AF_VSOCK", "family"])
    def test_a_kernel_or_unknown_family_stamps_nothing(self, tmp_path, family):
        """Neither another process nor the network: a netlink or packet
        socket talks to the kernel, and a family held in a variable is
        unknown. Both abstain rather than guess."""
        assert _kind(tmp_path, HDR + "void f(char *b, int family) {\n"
                     f"  int s = socket({family}, SOCK_RAW, 0);\n"
                     "  recv(s, b, 1, 0);\n}\n", "recv") is None

    def test_a_descriptor_from_an_unknown_call_stamps_nothing(self, tmp_path):
        assert _kind(tmp_path, HDR + "int open_it(void);\n"
                     "void f(char *b) { int s = open_it(); send(s, b, 1, 0); }\n",
                     "send") is None

    def test_a_member_call_producer_stamps_nothing(self, tmp_path):
        assert _kind(tmp_path, HDR + "struct h { int (*open)(int); };\n"
                     "void f(struct h *h, char *b) {\n"
                     "  int s = h->open(1);\n  send(s, b, 1, 0);\n}\n",
                     "send") is None

    def test_a_non_call_binding_stamps_nothing(self, tmp_path):
        assert _kind(tmp_path, HDR + "void f(char *b, int *fds) {\n"
                     "  int s = fds[0];\n  send(s, b, 1, 0);\n}\n", "send") is None

    def test_accept_over_an_unprovable_listener_stamps_nothing(self, tmp_path):
        assert _kind(tmp_path, HDR + "void f(int l, char *b) {\n"
                     "  int c = accept(l, 0, 0);\n  recv(c, b, 1, 0);\n}\n",
                     "recv") is None

    def test_accept_of_an_accept_stops_after_one_hop(self, tmp_path):
        """The hop is bounded on purpose: a second ``accept`` is not chased."""
        assert _kind(tmp_path, HDR + "void f(char *b) {\n"
                     "  int l = socket(AF_INET, SOCK_STREAM, 0);\n"
                     "  int c = accept(l, 0, 0);\n"
                     "  int d = accept(c, 0, 0);\n"
                     "  recv(d, b, 1, 0);\n}\n", "recv") is None

    def test_a_socketpair_of_a_different_array_is_not_read(self, tmp_path):
        assert _kind(tmp_path, HDR + "void f(char *b, int *other) {\n"
                     "  int sv[2];\n"
                     "  socketpair(AF_UNIX, SOCK_STREAM, 0, sv);\n"
                     "  send(other[0], b, 1, 0);\n}\n", "send") is None

    def test_a_socketpair_below_the_use_is_not_read(self, tmp_path):
        assert _kind(tmp_path, HDR + "void f(char *b) {\n"
                     "  int sv[2];\n"
                     "  send(sv[0], b, 1, 0);\n"
                     "  socketpair(AF_UNIX, SOCK_STREAM, 0, sv);\n}\n", "send") is None

    def test_a_socketpair_call_with_too_few_arguments_is_skipped(self, tmp_path):
        assert _kind(tmp_path, HDR + "void f(char *b) {\n"
                     "  int sv[2];\n"
                     "  socketpair(sv);\n"
                     "  send(sv[0], b, 1, 0);\n}\n", "send") is None

    def test_a_call_with_too_few_arguments_stamps_nothing(self, tmp_path):
        assert _kind(tmp_path, HDR + "void f(void) { send(); }\n", "send") is None

    def test_a_rebinding_below_the_use_is_not_used(self, tmp_path):
        assert _kind(tmp_path, HDR + "void f(char *b) {\n"
                     "  int s = socket(AF_INET, SOCK_STREAM, 0);\n"
                     "  recv(s, b, 1, 0);\n"
                     "  s = socket(AF_UNIX, SOCK_STREAM, 0);\n}\n", "recv") == "net_stream"

    def test_a_write_over_a_socket_is_out_of_scope_here(self, tmp_path):
        """SCOPE PIN, not a verdict: ``unistd.write`` over a socket descriptor
        is INV-vaduk's own residual and is not stamped by this table."""
        assert _kind(tmp_path, HDR + "void f(char *b) {\n"
                     "  int s = socket(AF_UNIX, SOCK_STREAM, 0);\n"
                     "  write(s, b, 1);\n}\n", "write") is None


class TestOriginLookupEdgesThatReturnNothing:
    """The remaining abstention paths, each reached by real C."""

    def test_a_family_computed_by_a_call_stamps_nothing(self, tmp_path):
        """The first argument is cut at depth zero, so ``pick(0)`` is read
        whole -- and, being no family this table knows, abstains."""
        assert _kind(tmp_path, HDR + "int pick(int);\n"
                     "void f(char *b) {\n"
                     "  int s = socket(pick(0), SOCK_RAW, 0);\n"
                     "  recv(s, b, 1, 0);\n}\n", "recv") is None

    def test_a_socketpair_element_outside_any_function_has_no_body(
        self, tmp_path,
    ) -> None:
        """A file-scope initializer has no enclosing ``function_definition``
        and no caller symbol, so the analyzer emits no edge at all; the
        socketpair lookup still runs per CALL and must survive a node with
        no enclosing body rather than raise."""
        (tmp_path / "main.c").write_text(
            HDR + "int sv[2];\nchar b[1];\n"
            "int x = send(sv[0], b, 1, 0);\n"
        )
        edges = [e for e in analyze_c(tmp_path).edges
                 if e.edge_type == "calls" and ":send:" in e.dst]
        assert edges == []
