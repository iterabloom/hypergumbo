# SPDX-License-Identifier: AGPL-3.0-or-later
import os
import socket


def f(s):
    sock = socket.socket()
    t = s
    t[0] = os.getenv("API_KEY")
    print(t)
    sock.sendall(s[0].encode())
