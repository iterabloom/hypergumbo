# SPDX-License-Identifier: AGPL-3.0-or-later
"""Which config keys hold secrets, and what their values become in the output.

Config analyzers that print a setting's value into ``Symbol.signature`` or
``Symbol.meta`` (``ini``, ``properties``) must not print a secret, because the
behavior map is written to be pasted into an LLM's context and shared. Each
analyzer used to carry its own keyword list and its own mask, and the two
drifted apart in both directions (INV-puvoh):

- ``ini`` matched keywords as SUBSTRINGS, so ``keyboard_layout`` (``key``),
  ``author`` (``auth``) and ``monkey`` (``key``) were hidden, and it masked as
  ``v[0] + '*' * (len - 2) + v[-1]``, so ``hunter22`` became ``h******2`` --
  the length and two characters of every password it did hide.
- ``properties`` masked with a constant but listed only five keywords, so
  ``db.passwd`` and ``auth.pwd`` printed their values raw.

How it works
------------
A key is split into lower-cased WORDS on separators (``_ . - /`` and the like)
and on case humps (``apiKey``, ``APIKey``). It is sensitive when either

- **any word ends with a long stem** -- ``password``, ``passwd``,
  ``passphrase``, ``secret``, ``token``, ``credential`` and the run-together
  compounds ``apikey`` / ``accesskey`` / ``secretkey`` / ``privatekey`` /
  ``privkey``, tested on each word and on each pair of adjacent words run
  together. A suffix, so an unseparated compound (``dbpassword``,
  ``clientsecret``) is caught, and the pairs catch ``PrivateKeyPem`` and
  ``aws_access_key_id``; ``secret_key_base`` is caught by its first word; not
  a substring, so ``secretary`` and ``tokenizer`` are not; or
- **the LAST word is a short one** -- ``key``, ``pass``, ``pw``, ``pwd``,
  ``auth``, ``authorization``, ``tlskey``, ``sslkey``. As substrings these sit
  inside ordinary words (``monkey``, ``bypass``, ``author``), and even as whole
  words they lead many keys that are not secrets: ``key_name``,
  ``passLoginHint``, ``auth-username-form-display-name`` (a Keycloak message
  bundle has hundreds). The last word is the noun that names the value
  (``api_key``, ``stomp_pass``, ``CONFIG_NOVA_DB_PW``).

Either rule also accepts a plural ``s``. ``cert`` / ``certificate`` /
``private`` / ``encryption``, which ``ini`` used to list, are not secrets on
their own: their values are a path, an algorithm name, a flag or a PUBLIC
certificate, and a private key is caught by ``key`` / ``privatekey``.

The mask is a constant, so it says that a value was withheld and nothing
else about it -- not its length, not whether it was empty.

Measured on the 128 corpus repos holding ``.ini`` / ``.properties`` files
when this was written: the change shows message-bundle labels and paths
(``webauthn_*``, ``commits.author``, ``ssl.keystore.location``) that the
substring rule hid, and hides the ``*_PW`` / ``*_pass`` passwords and the
``passwd`` / ``pwd`` keys that it printed.

Values printed by analyzers that are not config formats are out of scope here:
``scss`` prints ``$var: value``, where ``token`` is design-system vocabulary
(``$token-primary``) and a word-match would hide ordinary colours.
"""
from __future__ import annotations

import re
from itertools import pairwise

#: What a sensitive value is printed as -- whatever the value, so it reveals
#: neither length nor characters.
MASK = "***"

# Any word ending with one of these makes the key sensitive; none of them
# ends an unrelated English word.
_SUFFIX_STEMS = (
    "password", "passwd", "passphrase", "secret", "token", "credential",
    "apikey", "accesskey", "secretkey", "privatekey", "privkey",
)

# Sensitive only as the key's LAST word, the noun that names the value: as
# leading words they begin many keys that hold no secret (``key_name``).
_LAST_WORDS = frozenset({
    "key", "pass", "pw", "pwd", "auth", "authorization", "tlskey", "sslkey",
})

_SEPARATORS = re.compile(r"[^A-Za-z0-9]+")
# An acronym before a capitalised word (``API`` in ``APIKey``), a capitalised
# or lower-case word, a bare acronym, or a run of digits.
_HUMPS = re.compile(r"[A-Z]+(?=[A-Z][a-z])|[A-Z]?[a-z]+|[A-Z]+|[0-9]+")


def key_words(key: str) -> list[str]:
    """``key`` split into lower-cased words on separators and case humps."""
    return [
        word.lower()
        for part in _SEPARATORS.split(key)
        for word in _HUMPS.findall(part)
    ]


def _forms(word: str) -> tuple[str, ...]:
    """``word`` and, when it ends in ``s``, its singular."""
    return (word, word[:-1]) if word.endswith("s") else (word,)


def is_sensitive_key(key: str) -> bool:
    """Whether a config key's value is a secret that must not be printed."""
    words = key_words(key)
    if not words:
        return False
    if any(form in _LAST_WORDS for form in _forms(words[-1])):
        return True
    runs = words + [a + b for a, b in pairwise(words)]
    return any(
        form.endswith(_SUFFIX_STEMS) for run in runs for form in _forms(run)
    )
