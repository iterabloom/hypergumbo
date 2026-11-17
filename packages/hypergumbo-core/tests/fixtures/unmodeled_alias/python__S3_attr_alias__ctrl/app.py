# SPDX-License-Identifier: AGPL-3.0-or-later
import os
import socket


class Box:
    f = ""


def f():
    sock = socket.socket()
    a = Box()
    b = a
    b.f = os.getenv("API_KEY")
    print(b)
    sock.sendall(b.f.encode())
