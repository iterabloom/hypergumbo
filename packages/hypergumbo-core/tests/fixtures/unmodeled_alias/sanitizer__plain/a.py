# SPDX-License-Identifier: AGPL-3.0-or-later
from cryptography.fernet import Fernet
def reenc(key, ct):
    pt = Fernet(key).decrypt(ct)
    e = Fernet(key).encrypt(pt)
    with open("out.bin", "wb") as f:
        f.write(e)
