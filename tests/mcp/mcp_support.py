"""Apoio dos testes do MCP: o servidor de verdade numa thread, na porta efêmera, e um cliente http.client.

Nada aqui toca o ~/.brza, o ~/Library/LaunchAgents nem o launchctl reais: o BRZA_HOME e o BRZA_LAUNCH_AGENTS_DIR são
diretórios temporários.
"""
import http.client
import json
import os
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent.parent
PACKAGE = ROOT / 'packages' / 'mcp'
ENTRY = PACKAGE / 'bin' / 'brza-mcp'
sys.path.insert(0, str(PACKAGE))
os.environ['PYTHONDONTWRITEBYTECODE'] = '1'

from brza_mcp import paths, server  # noqa: E402
from brza_mcp.registry import ToolRegistry  # noqa: E402


class HomeCase(unittest.TestCase):
    """Um BRZA_HOME e um diretório de LaunchAgents temporários."""

    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.tmp = Path(temp.name).resolve()
        self.home = self.tmp / 'brza'
        self.agents = self.tmp / 'LaunchAgents'
        p = patch.dict(os.environ, {'BRZA_HOME': str(self.home), 'BRZA_LAUNCH_AGENTS_DIR': str(self.agents)})
        p.start()
        self.addCleanup(p.stop)
        for var in ('BRZA_MCP_PORT', 'BRZA_WORKSPACE'):
            os.environ.pop(var, None)
        cwd = os.getcwd()  # fora de qualquer workspace: o brza_root.find do cwd não acha nada
        os.chdir(self.tmp)
        self.addCleanup(os.chdir, cwd)


class ServerCase(HomeCase):
    """O servidor de verdade numa thread, com o token gerado no BRZA_HOME temporário."""
    keepalive = 0.2

    def setUp(self):
        super().setUp()
        self.token = paths.ensure_token()
        self.registry = ToolRegistry()
        self.server = server.make_server(0, self.token, self.registry, keepalive=self.keepalive)
        self.server.quiet = True
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever, kwargs={'poll_interval': 0.05}, daemon=True)
        self.thread.start()
        self.addCleanup(self.stop_server)

    def stop_server(self):
        self.server.request_stop()
        self.thread.join(5)
        self.server.server_close()

    def request(self, method, path, body=None, headers=None, token='default'):
        """(status, headers, corpo em bytes) de uma requisição com conexão própria."""
        conn = http.client.HTTPConnection('127.0.0.1', self.port, timeout=5)
        try:
            h = dict(headers or {})
            if token == 'default':
                token = self.token
            if token is not None:
                h.setdefault('Authorization', f'Bearer {token}')
            data = body if isinstance(body, (bytes, type(None))) else json.dumps(body).encode()
            if data is not None:
                h.setdefault('Content-Type', 'application/json')
            conn.request(method, path, body=data, headers=h)
            r = conn.getresponse()
            return r.status, dict(r.getheaders()), r.read()
        finally:
            conn.close()

    def rpc(self, method, params=None, id=1, headers=None):
        msg = {'jsonrpc': '2.0', 'method': method}
        if id is not None:
            msg['id'] = id
        if params is not None:
            msg['params'] = params
        status, hdrs, raw = self.request('POST', '/mcp', msg, headers)
        return status, hdrs, (json.loads(raw) if raw else None)
