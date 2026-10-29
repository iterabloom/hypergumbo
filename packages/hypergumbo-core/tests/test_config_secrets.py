# SPDX-License-Identifier: AGPL-3.0-or-later
"""The shared sensitive-key test and mask for config analyzers (INV-puvoh)."""
from __future__ import annotations

import pytest

from hypergumbo_core.analyze.config_secrets import (
    MASK,
    is_sensitive_key,
    key_words,
)


class TestKeyWords:
    @pytest.mark.parametrize(
        ("key", "words"),
        [
            ("db_password", ["db", "password"]),
            ("DB_PASSWORD", ["db", "password"]),
            ("apiKey", ["api", "key"]),
            ("APIKey", ["api", "key"]),
            ("spring.datasource.password", ["spring", "datasource", "password"]),
            ("auth-token", ["auth", "token"]),
            ("keyboard_layout", ["keyboard", "layout"]),
            ("", []),
        ],
    )
    def test_splits_on_separators_and_case_humps(self, key: str, words: list[str]) -> None:
        assert key_words(key) == words


class TestIsSensitiveKey:
    @pytest.mark.parametrize(
        "key",
        [
            "password", "db_password", "DB_PASSWORD", "passwd", "db.passwd",
            "pwd", "auth.pwd", "db_pass", "smtp.pass", "stomp_pass", "pass",
            "CONFIG_NOVA_DB_PW", "api_key", "apiKey", "apikey", "secret",
            "client_secret", "secretKey", "secret_token", "secret_key_base",
            "AUTH_TOKEN", "auth", "_auth", "Authorization",
            "proxy_authorization", "access_token", "refresh_tokens",
            "credential", "credentials", "service.credential", "private_key",
            "privateKey", "privkey", "tlskey", "ssh_key", "encryption.key",
            "keys", "passphrase", "ssl.key.passphrase", "dbpassword",
            "mysqlpassword", "clientsecret", "accesstoken",
            # two words that run together into a stem
            "PrivateKeyPem", "aws_access_key_id", "private_key_file",
        ],
    )
    def test_flags_sensitive_keys(self, key: str) -> None:
        assert is_sensitive_key(key) is True, key

    @pytest.mark.parametrize(
        "key",
        [
            "host", "port", "username", "keyboard_layout", "keyboardLayout",
            "author", "commits.author", "authority", "monkey", "turkey",
            "hotkeys", "passage", "bypass", "compass", "tokenizer_path",
            "secretary", "certainty", "privateer", "app.name", "",
            # a short word that LEADS the key does not name its value
            "key_name", "passLoginHint", "ProxyPassReverse",
            "auth-username-form-display-name", "keyword_search_unavailable",
            # a path, an algorithm, a flag or a public certificate
            "cert", "certificate", "clientCertificate", "encryption",
            "private", "ssl.keystore.location", "keyfile",
        ],
    )
    def test_does_not_flag_keys_that_hold_no_secret(self, key: str) -> None:
        assert is_sensitive_key(key) is False, key


class TestMask:
    def test_the_mask_is_free_of_word_characters(self) -> None:
        assert MASK and not any(c.isalnum() for c in MASK)
