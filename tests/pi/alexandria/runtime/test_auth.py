"""Small policy tests for remote-token authorization without network access."""

import unittest

from pi.alexandria.runtime.auth import _normalized_scopes


class AuthPolicyTests(unittest.TestCase):
    def test_normalizes_space_delimited_scopes(self):
        self.assertEqual(
            _normalized_scopes("openid  alexandria:read offline_access"),
            ["openid", "alexandria:read", "offline_access"],
        )

    def test_normalizes_list_scopes(self):
        self.assertEqual(
            _normalized_scopes(["alexandria:read", "offline_access"]),
            ["alexandria:read", "offline_access"],
        )

    def test_rejects_malformed_scope_claims(self):
        for value in (None, 1, {"alexandria:read"}, ["ok", 1]):
            with self.subTest(value=value):
                self.assertEqual(_normalized_scopes(value), [])
