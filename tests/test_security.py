import unittest

from werkzeug.security import generate_password_hash

from security import LoginRateLimiter, is_password_hash, parse_users, verify_password, verify_user


class SecurityHelpersTests(unittest.TestCase):
    def test_parses_hashes_that_contain_colons(self):
        password_hash = generate_password_hash('correct horse battery staple')
        users = parse_users(f'hugo:{password_hash}')

        self.assertEqual(users['hugo']['id'], 1)
        self.assertEqual(users['hugo']['password'], password_hash)
        self.assertTrue(users['hugo']['password_hashed'])

    def test_verifies_hashed_and_legacy_passwords(self):
        password_hash = generate_password_hash('secret')

        self.assertTrue(is_password_hash(password_hash))
        self.assertTrue(verify_password(password_hash, 'secret'))
        self.assertFalse(verify_password(password_hash, 'wrong'))
        self.assertTrue(verify_password('legacy-secret', 'legacy-secret'))

    def test_unknown_user_never_authenticates(self):
        users = parse_users(f'hugo:{generate_password_hash("secret")}')

        self.assertIsNone(verify_user(users, 'someone-else', 'secret'))

    def test_rejects_missing_or_malformed_users(self):
        with self.assertRaises(ValueError):
            parse_users('')
        with self.assertRaises(ValueError):
            parse_users('not-a-valid-entry')
        with self.assertRaises(ValueError):
            parse_users('bad user:secret')

    def test_rate_limiter_reopens_after_window(self):
        limiter = LoginRateLimiter(max_attempts=2, window_seconds=10)
        limiter.record_failure('client', now=100)
        limiter.record_failure('client', now=101)

        self.assertEqual(limiter.check('client', now=102), (False, 8))
        self.assertEqual(limiter.check('client', now=111), (True, 0))


if __name__ == '__main__':
    unittest.main()
