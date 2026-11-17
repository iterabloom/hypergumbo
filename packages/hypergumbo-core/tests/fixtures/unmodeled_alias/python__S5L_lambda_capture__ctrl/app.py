# SPDX-License-Identifier: AGPL-3.0-or-later
import os
import socket


def f():
    sock = socket.socket()
    x = ""
    g = lambda: x  # noqa: E731 - the shape under test
    x = os.getenv("API_KEY")
    print(x)
    sock.sendall(x.encode())
