"""Testes da autenticação do servidor MCP: o token Bearer em toda rota, e o arquivo do token com 0600.

    PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests/mcp -v
"""
import json
import stat
import unittest

from mcp_support import HomeCase, ServerCase, paths


class TokenFileTest(HomeCase):
    def test_the_token_is_created_with_0600_and_the_runtime_dir_is_private(self):
        token = paths.ensure_token()
        self.assertGreaterEqual(len(token), 40)
        self.assertEqual(stat.S_IMODE(paths.token_path().stat().st_mode), 0o600)
        self.assertEqual(paths.token_path(), self.home / 'runtime' / 'mcp.token')
        self.assertEqual(stat.S_IMODE(paths.runtime_dir().stat().st_mode), 0o700)

    def test_the_existing_token_is_reused(self):
        first = paths.ensure_token()
        self.assertEqual(paths.ensure_token(), first)
        paths.token_path().write_text('guardado\n')
        self.assertEqual(paths.ensure_token(), 'guardado')

    def test_an_empty_token_file_is_replaced(self):
        paths.runtime_dir().mkdir(parents=True)
        paths.token_path().write_text('')
        self.assertTrue(paths.ensure_token())

    def test_the_home_is_the_brza_home_variable_or_dot_brza(self):
        self.assertEqual(paths.brza_home(), self.home)
        import os
        from unittest.mock import patch
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop('BRZA_HOME')
            self.assertEqual(paths.brza_home(), paths.Path.home() / '.brza')


class AuthTest(ServerCase):
    def assert_401(self, **kw):
        status, headers, body = self.request('GET', '/health', **kw)
        self.assertEqual(status, 401)
        self.assertEqual(headers['WWW-Authenticate'], 'Bearer')
        self.assertIn('error', json.loads(body))

    def test_no_header_is_401(self):
        self.assert_401(token=None)

    def test_a_wrong_token_is_401(self):
        self.assert_401(token='errado')

    def test_a_wrong_scheme_is_401(self):
        self.assert_401(token=None, headers={'Authorization': f'Basic {self.token}'})
        self.assert_401(token=None, headers={'Authorization': self.token})

    def test_the_right_token_is_200(self):
        status, _, body = self.request('GET', '/health')
        self.assertEqual(status, 200)
        data = json.loads(body)
        self.assertTrue(data['ok'])
        self.assertIsInstance(data['pid'], int)
        self.assertIn('version', data)

    def test_every_route_needs_the_token(self):
        for method, path in (('POST', '/mcp'), ('GET', '/mcp'), ('GET', '/qualquer'), ('GET', '/health')):
            with self.subTest(route=f'{method} {path}'):
                status, _, _ = self.request(method, path, body=b'{}' if method == 'POST' else None, token=None)
                self.assertEqual(status, 401)

    def test_the_unknown_route_is_a_json_404(self):
        status, headers, body = self.request('GET', '/nada')
        self.assertEqual(status, 404)
        self.assertIn('error', json.loads(body))
        self.assertEqual(headers['Content-Length'], str(len(body)))


if __name__ == '__main__':
    unittest.main()
