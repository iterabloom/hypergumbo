# SPDX-License-Identifier: AGPL-3.0-or-later
import os
import socket


def f():
    sock = socket.socket()
    s = [""]
    t = s
    t = [os.getenv("API_KEY")]
    print(t)
    sock.sendall(s[0].encode())
