# SPDX-License-Identifier: AGPL-3.0-or-later
import os
import socket


def f():
    sock = socket.socket()
    d = {}
    t = d.setdefault("k", [""])
    t[0] = os.getenv("API_KEY")
    print(t)
    sock.sendall(t[0].encode())
