# SPDX-License-Identifier: AGPL-3.0-or-later
import os
import socket


def keep(r, v):
    r["k"] = v


def f():
    sock = socket.socket()
    reg = {}
    s = [""]
    keep(reg, s)
    s[0] = os.getenv("API_KEY")
    print(s)
    sock.sendall(reg["k"][0].encode())
