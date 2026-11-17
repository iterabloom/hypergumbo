# SPDX-License-Identifier: AGPL-3.0-or-later
import os
import socket


def f():
    sock = socket.socket()
    inner = [""]
    lst = []
    lst.append(inner)
    inner[0] = os.getenv("API_KEY")
    print(inner)
    sock.sendall(lst[0][0].encode())
