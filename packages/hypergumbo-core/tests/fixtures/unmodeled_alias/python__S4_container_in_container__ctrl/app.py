# SPDX-License-Identifier: AGPL-3.0-or-later
import os
import socket


def f():
    sock = socket.socket()
    inner = [""]
    outer = [inner]
    inner[0] = os.getenv("API_KEY")
    print(inner)
    sock.sendall(inner[0].encode())
