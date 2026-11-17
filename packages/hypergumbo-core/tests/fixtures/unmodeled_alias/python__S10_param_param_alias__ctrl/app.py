# SPDX-License-Identifier: AGPL-3.0-or-later
import os
import socket


def f(a, b):
    sock = socket.socket()
    a[0] = os.getenv("API_KEY")
    print(a)
    sock.sendall(a[0].encode())


def main():
    x = [""]
    f(x, x)
