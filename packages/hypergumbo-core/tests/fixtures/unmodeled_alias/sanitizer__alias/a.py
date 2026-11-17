# SPDX-License-Identifier: AGPL-3.0-or-later
from cryptography.fernet import Fernet
def reenc(key, ct):
    s = [b""]
    t = s
    t[0] = Fernet(key).decrypt(ct)
    e = Fernet(key).encrypt(t[0])
    with open("out.bin", "wb") as f:
        f.write(e)
        f.write(s[0])
