"""O `brza-mcp`: serve, start, stop, status e token."""
import argparse
import json
import os
import signal
import sys
import time

from . import launchd, paths, server


def serve(port: int):
    token = paths.ensure_token()
    try:
        srv = server.make_server(port, token)
    except OSError as e:
        sys.exit(f'não consegui escutar em 127.0.0.1:{port}: {e}. A porta é exclusiva, não há outra de reserva')
    actual = srv.server_address[1]
    info = paths.info_path()
    tmp = info.with_suffix('.json.tmp')
    tmp.write_text(json.dumps({'pid': os.getpid(), 'port': actual, 'started': time.time()}))
    tmp.replace(info)
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, lambda *_: srv.request_stop())
    print(f'brza-mcp escutando em 127.0.0.1:{actual}', file=sys.stderr, flush=True)
    try:
        srv.serve_forever()
    finally:
        srv.server_close()
        info.unlink(missing_ok=True)


def main(argv=None):
    ap = argparse.ArgumentParser(prog='brza mcp', description='O servidor MCP residente do BrzA Studio.')
    sub = ap.add_subparsers(dest='cmd', required=True)
    p = sub.add_parser('serve', help='roda o servidor em primeiro plano (o que o launchd executa)')
    p.add_argument('--port', type=int, default=None, help='porta em 127.0.0.1 (padrão 4242)')
    p = sub.add_parser('start', help='grava o agente do launchd, carrega e espera o /health')
    p.add_argument('--timeout', type=float, default=15.0, help='segundos de espera do /health')
    sub.add_parser('stop', help='descarrega o agente')
    sub.add_parser('status', help='agente carregado e /health; sai 0 de pé, 3 parado')
    sub.add_parser('token', help='imprime o token Bearer, criando-o se falta')
    a = ap.parse_args(argv)
    if a.cmd == 'serve':
        serve(paths.default_port() if a.port is None else a.port)
    elif a.cmd == 'start':
        try:
            data = launchd.start(a.timeout)
        except RuntimeError as e:
            sys.exit(str(e))
        print(f'brza-mcp de pé: o /health responde (pid {data["pid"]}, versão {data["version"]})')
    elif a.cmd == 'stop':
        print('brza-mcp parado' if launchd.stop() else 'brza-mcp já estava parado: o agente não está carregado')
    elif a.cmd == 'status':
        loaded, data = launchd.is_loaded(), launchd.health()
        print(f'carregado: {"sim" if loaded else "não"}')
        print(f'health: {"responde (pid %s)" % data["pid"] if data else "não responde"}')
        sys.exit(0 if data else 3)
    elif a.cmd == 'token':
        print(paths.ensure_token())
