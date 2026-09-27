import unittest

import hosting


class HostingTests(unittest.TestCase):
    def test_local_default_is_still_loopback_only(self):
        cfg = hosting.configuration('127.0.0.1', {})
        self.assertIsNone(hosting.authorize({'Host': 'localhost:8769'}, 8769, cfg))
        self.assertEqual(hosting.authorize({'Host': 'evil.example'}, 8769, cfg)[0], 403)

    def test_remote_requires_host_and_long_token(self):
        for env in ({}, {'CORPUS_BACKEND_TOKEN': 'x' * 32}, {'CORPUS_ALLOWED_HOSTS': 'archive.example'}):
            with self.assertRaises(ValueError):
                hosting.configuration('0.0.0.0', env)

    def test_remote_checks_host_and_token_independently(self):
        token = 'test-' * 8
        cfg = hosting.configuration('0.0.0.0', {'CORPUS_BACKEND_TOKEN': token, 'CORPUS_ALLOWED_HOSTS': 'archive.example'})
        self.assertEqual(hosting.authorize({'Host': 'archive.example'}, 8769, cfg)[0], 401)
        self.assertEqual(hosting.authorize({'Host': 'wrong.example', 'X-Corpus-Token': token}, 8769, cfg)[0], 403)
        self.assertEqual(hosting.authorize({'Host': 'localhost:8769'}, 8769, cfg)[0], 401)
        self.assertIsNone(hosting.authorize({'Host': 'archive.example', 'X-Corpus-Token': token}, 8769, cfg))

    def test_host_wildcards_are_rejected(self):
        with self.assertRaises(ValueError):
            hosting.configuration('0.0.0.0', {'CORPUS_BACKEND_TOKEN': 'x' * 32, 'CORPUS_ALLOWED_HOSTS': '*'})


if __name__ == '__main__':
    unittest.main()
