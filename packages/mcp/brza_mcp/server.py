"""O servidor HTTP do MCP (Streamable HTTP): JSON-RPC em `POST /mcp`, SSE em `GET /mcp` e `GET /health`, tudo com Bearer.

HTTP/1.1 com Content-Length em toda resposta que não é SSE, e uma thread por conexão: o Claude Code mantém um `GET /mcp`
aberto enquanto faz os `POST /mcp`. Escuta só em 127.0.0.1.
"""
from __future__ import annotations

import hmac
import json
import os
import threading
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import VERSION
from .registry import REGISTRY, CallContext

HOST = '127.0.0.1'
SERVER_NAME = 'brza-orchestrator'
FALLBACK_PROTOCOL = '2025-06-18'


def rpc_error(id, code, message):
    return {'jsonrpc': '2.0', 'id': id, 'error': {'code': code, 'message': message}}


class Handler(BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'
    server_version = f'brza-mcp/{VERSION}'

    def log_message(self, format, *args):  # noqa: A002 - o log vai ao stderr do launchd, uma linha por requisição
        if not self.server.quiet:
            super().log_message(format, *args)

    def reply(self, status, payload=None, headers=None):
        body = b'' if payload is None else json.dumps(payload, ensure_ascii=False).encode()
        self.send_response(status)
        if payload is not None:
            self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(body)))
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if self.command != 'HEAD':
            self.wfile.write(body)

    def authorized(self) -> bool:
        scheme, _, given = self.headers.get('Authorization', '').partition(' ')
        ok = scheme == 'Bearer' and hmac.compare_digest(given.strip().encode(), self.server.token.encode())
        if not ok:
            self.reply(401, {'error': 'unauthorized'}, {'WWW-Authenticate': 'Bearer'})
        return ok

    def route(self):
        """Dispara a rota; o corpo do POST é lido antes de responder, para a conexão seguir limpa."""
        length = int(self.headers.get('Content-Length') or 0)
        body = self.rfile.read(length) if length else b''
        if not self.authorized():
            return
        path = self.path.split('?', 1)[0]
        if self.command == 'GET' and path == '/health':
            self.reply(200, {'ok': True, 'pid': os.getpid(), 'version': VERSION})
        elif self.command == 'POST' and path == '/mcp':
            self.post_mcp(body)
        elif self.command == 'GET' and path == '/mcp':
            self.get_mcp()
        elif self.command == 'DELETE' and path == '/mcp':  # o cliente encerra a sessão do Mcp-Session-Id
            self.reply(200)
        else:
            self.reply(404, {'error': 'not found'})

    do_GET = do_POST = do_DELETE = do_PUT = do_HEAD = do_OPTIONS = route

    def post_mcp(self, body: bytes):
        try:
            msg = json.loads(body)
        except ValueError:
            return self.reply(200, rpc_error(None, -32700, 'JSON inválido'))
        if not isinstance(msg, dict):
            return self.reply(200, rpc_error(None, -32600, 'só mensagem única; lote não é aceito'))
        method, id = msg.get('method'), msg.get('id')
        if 'id' not in msg or not isinstance(method, str):  # notificação ou resposta do cliente
            return self.reply(202)
        params = msg.get('params') if isinstance(msg.get('params'), dict) else {}
        headers = {}
        if method == 'initialize':
            headers['Mcp-Session-Id'] = uuid.uuid4().hex
            result = {'protocolVersion': params.get('protocolVersion') or FALLBACK_PROTOCOL,
                      'capabilities': {'tools': {}},
                      'serverInfo': {'name': SERVER_NAME, 'version': VERSION}}
        elif method == 'ping':
            result = {}
        elif method == 'tools/list':
            result = {'tools': self.server.registry.list()}
        elif method == 'tools/call':
            name = params.get('name')
            if not isinstance(name, str) or self.server.registry.get(name) is None:
                return self.reply(200, rpc_error(id, -32602, f'ferramenta desconhecida: {name}'))
            ctx = CallContext(self.headers.get('X-Brza-Session'), dict(self.headers.items()))
            args = params.get('arguments') if isinstance(params.get('arguments'), dict) else {}
            result = self.server.registry.call(name, args, ctx)
        else:
            return self.reply(200, rpc_error(id, -32601, f'método desconhecido: {method}'))
        self.reply(200, {'jsonrpc': '2.0', 'id': id, 'result': result}, headers)

    def get_mcp(self):
        if 'text/event-stream' not in self.headers.get('Accept', ''):
            return self.reply(406, {'error': 'Accept: text/event-stream é obrigatório'})
        self.close_connection = True
        self.send_response(200)
        self.send_header('Content-Type', 'text/event-stream')
        self.send_header('Cache-Control', 'no-cache')
        self.send_header('Connection', 'close')
        self.end_headers()
        stop = self.server.stop_event
        try:
            self.wfile.write(b': connected\n\n')
            self.wfile.flush()
            while not stop.wait(self.server.keepalive):
                self.wfile.write(b': keep-alive\n\n')
                self.wfile.flush()
        except OSError:  # o cliente fechou o stream
            pass


class OrchestratorServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address, token, registry, keepalive):
        super().__init__(address, Handler)
        self.token, self.registry, self.keepalive = token, registry or REGISTRY, keepalive
        self.quiet = False
        self.stop_event = threading.Event()

    def request_stop(self):
        """Acorda os streams SSE e para o serve_forever; seguro de um signal handler e de outra thread."""
        self.stop_event.set()
        threading.Thread(target=self.shutdown, daemon=True).start()


def make_server(port, token, registry=None, keepalive=15.0) -> OrchestratorServer:
    """Escuta em 127.0.0.1:<port> (0 = efêmera). Porta ocupada levanta OSError: não há outra porta de reserva."""
    return OrchestratorServer((HOST, port), token, registry, keepalive)
