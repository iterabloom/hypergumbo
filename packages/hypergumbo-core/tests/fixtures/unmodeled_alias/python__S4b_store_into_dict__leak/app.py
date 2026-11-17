# SPDX-License-Identifier: AGPL-3.0-or-later
import os
import socket


def f():
    sock = socket.socket()
    inner = [""]
    reg = {}
    reg["k"] = inner
    inner[0] = os.getenv("API_KEY")
    print(inner)
    sock.sendall(reg["k"][0].encode())
