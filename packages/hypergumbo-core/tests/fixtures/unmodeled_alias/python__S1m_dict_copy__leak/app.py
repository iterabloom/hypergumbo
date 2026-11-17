# SPDX-License-Identifier: AGPL-3.0-or-later
import os
import socket


def f():
    sock = socket.socket()
    m = {}
    n = m
    n["k"] = os.getenv("API_KEY")
    print(n)
    sock.sendall(m["k"].encode())
