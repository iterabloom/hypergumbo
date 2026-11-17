# SPDX-License-Identifier: AGPL-3.0-or-later
import os
import socket

G = []


def f():
    sock = socket.socket()
    global G
    s = [""]
    G = s
    s[0] = os.getenv("API_KEY")
    print(s)
    sock.sendall(G[0].encode())
