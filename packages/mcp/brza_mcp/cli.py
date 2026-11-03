"""O `brza-mcp`: serve, start, stop, status, url, token e config."""
from __future__ import annotations

import argparse
import json
import os
import signal
import sys
import time
import urllib.request

from . import accounts as accounts_mod
from . import cycle, launchd, limits as limits_mod, paths, queue as queue_mod, server, tools
from .registry import REGISTRY


def build(registry, queue=None, accounts=None, run=cycle.default_run, interval=cycle.DEFAULT_INTERVAL, detect=None):
    """Fila, tetos com o provedor de uso das contas, ferramentas e o `Scheduler` (ainda parado). Devolve
    `(serviço, scheduler)`; nos testes, tudo vem injetado."""
    accounts = accounts or accounts_mod.Accounts()
    queue = queue if queue is not None else queue_mod.Queue()
    limits = limits_mod.Limits(usage=accounts.usage)
    service = tools.install(registry, queue=queue, limits=limits)
    scheduler = cycle.Scheduler(queue, limits, run=run, prepare=service.sync_limits, interval=interval,
                                log=lambda m: print(m, file=sys.stderr, flush=True),
                                **({'detect': detect} if detect else {}))
    service.on_queued = scheduler.kick
    return service, scheduler


def serve(port: int):
    token = paths.ensure_token()
    service, scheduler = build(REGISTRY)
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
    scheduler.start()
    try:
        srv.serve_forever()
    finally:
        scheduler.stop()
        srv.server_close()
        info.unlink(missing_ok=True)


def call_config(args, timeout=5.0):
    """A ferramenta `config` do servidor de pé, por HTTP com o token: o resultado decodificado; OSError se está parado."""
    body = {'jsonrpc': '2.0', 'id': 1, 'method': 'tools/call', 'params': {'name': 'config', 'arguments': args}}
    req = urllib.request.Request(f'http://127.0.0.1:{paths.default_port()}/mcp', data=json.dumps(body).encode(),
                                 headers={'Authorization': f'Bearer {paths.read_token()}',
                                          'Content-Type': 'application/json'})
    with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(req, timeout=timeout) as r:
        result = json.load(r)['result']
    text = result['content'][0]['text']
    return result.get('isError', False), (json.loads(text) if not result.get('isError') else text)


def parse_value(text):
    """O valor da linha de comando: JSON quando decodifica (true, 3, 0.5, null), senão o texto."""
    try:
        return json.loads(text)
    except ValueError:
        return text


def config_command(key, value):
    args = {} if key is None else {'key': key} if value is None else {'key': key, 'value': parse_value(value)}
    try:
        is_error, out = call_config(args)
    except (OSError, ValueError, KeyError):
        print('brza-mcp parado: o servidor não responde (brza mcp start)', file=sys.stderr)
        sys.exit(3)
    if is_error:
        sys.exit(f'brza mcp config: {out}')
    print(json.dumps(out, ensure_ascii=False, indent=2))


def main(argv=None):
    ap = argparse.ArgumentParser(prog='brza mcp', description='O servidor MCP residente do BrzA Studio.')
    sub = ap.add_subparsers(dest='cmd', required=True)
    p = sub.add_parser('serve', help='roda o servidor em primeiro plano (o que o launchd executa)')
    p.add_argument('--port', type=int, default=None, help='porta em 127.0.0.1 (padrão 4242)')
    p = sub.add_parser('start', help='grava o agente do launchd, carrega e espera o /health')
    p.add_argument('--timeout', type=float, default=15.0, help='segundos de espera do /health')
    sub.add_parser('stop', help='descarrega o agente')
    sub.add_parser('status', help='agente carregado e /health; sai 0 de pé, 3 parado')
    p = sub.add_parser('url', help='imprime a URL do /mcp, para o .mcp.json')
    p.add_argument('--port', type=int, default=None, help='porta; senão a BRZA_MCP_PORT, o [mcp] port ou 4242')
    sub.add_parser('token', help='imprime o token Bearer, criando-o se falta')
    p = sub.add_parser('config', help='lê ou grava as sobreposições da política no servidor de pé; sai 3 parado')
    p.add_argument('key', nargs='?', help='chave do brza.toml, como policy.enforce')
    p.add_argument('value', nargs='?', help='valor em JSON (false, 3, "opus"); null apaga a sobreposição')
    a = ap.parse_args(argv)
    if a.cmd == 'serve':
        serve(paths.default_port(a.port))
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
        sys.exit(0 if loaded and data else 3)
    elif a.cmd == 'url':
        print(f'http://127.0.0.1:{paths.default_port(a.port)}/mcp')
    elif a.cmd == 'config':
        config_command(a.key, a.value)
    elif a.cmd == 'token':
        print(paths.ensure_token())
