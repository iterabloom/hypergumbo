# SPDX-License-Identifier: AGPL-3.0-or-later
import os
import socket


def f():
    sock = socket.socket()
    x = ""
    g = (x for _ in range(1))
    x = os.getenv("API_KEY")
    print(x)
    sock.sendall(x.encode())
