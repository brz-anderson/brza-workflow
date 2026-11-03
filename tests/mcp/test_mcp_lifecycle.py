"""Testes do servidor MCP e do ciclo de vida: o servidor de verdade numa thread (porta efêmera, urllib e http.client),
o plist, o start/stop/status com o launchctl trocado e o `brza-mcp serve` como subprocesso.

    PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests/mcp -v
"""
import contextlib
import http.client
import io
import json
import os
import plistlib
import signal
import socket
import subprocess
import sys
import threading
import time
import unittest
import urllib.request
from pathlib import Path
from subprocess import CompletedProcess
from unittest.mock import patch

from mcp_support import ENTRY, HomeCase, ServerCase, paths, server

from brza_mcp import VERSION, cli, launchd  # noqa: E402


class ProtocolTest(ServerCase):
    def test_initialize_echoes_the_protocol_and_gives_a_session_id(self):
        status, headers, body = self.rpc('initialize', {'protocolVersion': '2025-06-18', 'capabilities': {},
                                                        'clientInfo': {'name': 't', 'version': '1'}})
        self.assertEqual(status, 200)
        self.assertTrue(headers['Mcp-Session-Id'])
        self.assertEqual(body['id'], 1)
        self.assertEqual(body['result']['protocolVersion'], '2025-06-18')
        self.assertEqual(body['result']['capabilities'], {'tools': {}})
        self.assertEqual(body['result']['serverInfo'], {'name': 'brza-orchestrator', 'version': VERSION})

    def test_notifications_are_202_without_body(self):
        status, headers, body = self.rpc('notifications/initialized', id=None)
        self.assertEqual((status, body), (202, None))
        self.assertEqual(headers['Content-Length'], '0')

    def test_ping_is_an_empty_result(self):
        self.assertEqual(self.rpc('ping')[2]['result'], {})

    def test_tools_list_starts_empty(self):
        self.assertEqual(self.rpc('tools/list')[2]['result'], {'tools': []})

    def test_server_discover_is_a_method_not_found_and_the_server_lives(self):
        status, _, body = self.rpc('server/discover')
        self.assertEqual(status, 200)
        self.assertEqual(body['error']['code'], -32601)
        self.assertEqual(self.rpc('ping')[2]['result'], {})

    def test_invalid_json_is_parse_error(self):
        status, _, raw = self.request('POST', '/mcp', body=b'{nao e json')
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(raw)['error']['code'], -32700)

    def test_a_batch_is_invalid_request(self):
        status, _, raw = self.request('POST', '/mcp', body=[{'jsonrpc': '2.0', 'id': 1, 'method': 'ping'}])
        self.assertEqual(json.loads(raw)['error']['code'], -32600)

    def test_a_registered_tool_is_listed_and_called_with_the_session_of_the_request(self):
        seen = []

        def handler(arguments, ctx):
            seen.append((arguments, ctx.session))
            return f'oi {arguments["nome"]}'

        self.registry.register('eco', 'Devolve um oi', handler,
                               {'type': 'object', 'properties': {'nome': {'type': 'string'}}})
        listed = self.rpc('tools/list')[2]['result']['tools']
        self.assertEqual([t['name'] for t in listed], ['eco'])
        self.assertEqual(listed[0]['inputSchema']['type'], 'object')
        _, _, body = self.rpc('tools/call', {'name': 'eco', 'arguments': {'nome': 'Ana'}},
                              headers={'X-Brza-Session': 'sessao-7'})
        self.assertEqual(body['result'], {'content': [{'type': 'text', 'text': 'oi Ana'}], 'isError': False})
        self.assertEqual(seen, [({'nome': 'Ana'}, 'sessao-7')])

    def test_a_missing_session_header_gives_none(self):
        seen = []
        self.registry.register('s', 'x', lambda a, ctx: seen.append(ctx.session) or 'ok')
        self.rpc('tools/call', {'name': 's'})
        self.assertEqual(seen, [None])

    def test_a_failing_handler_is_a_tool_error_and_an_unknown_tool_is_invalid_params(self):
        def boom(arguments, ctx):
            raise RuntimeError('quebrou')

        self.registry.register('boom', 'x', boom)
        result = self.rpc('tools/call', {'name': 'boom'})[2]['result']
        self.assertTrue(result['isError'])
        self.assertIn('quebrou', result['content'][0]['text'])
        self.assertEqual(self.rpc('tools/call', {'name': 'nada'})[2]['error']['code'], -32602)

    def test_a_dict_result_becomes_json_text(self):
        self.registry.register('d', 'x', lambda a, ctx: {'a': 1})
        text = self.rpc('tools/call', {'name': 'd'})[2]['result']['content'][0]['text']
        self.assertEqual(json.loads(text), {'a': 1})

    def test_the_stdlib_queue_is_not_shadowed(self):
        import queue
        self.assertTrue(hasattr(queue, 'Queue'))
        self.assertNotIn('packages/mcp', str(Path(queue.__file__)))


class SessionEndTest(ServerCase):
    def test_delete_ends_the_session_with_200(self):  # A5
        status, headers, body = self.request('DELETE', '/mcp', headers={'Mcp-Session-Id': 'abc'})
        self.assertEqual((status, body), (200, b''))
        self.assertEqual(headers['Content-Length'], '0')
        self.assertEqual(self.request('DELETE', '/mcp', token=None)[0], 401)


class SseTest(ServerCase):
    def open_sse(self):
        conn = http.client.HTTPConnection('127.0.0.1', self.port, timeout=5)
        conn.request('GET', '/mcp', headers={'Accept': 'text/event-stream', 'Authorization': f'Bearer {self.token}',
                                             'X-Brza-Session': 's'})
        return conn, conn.getresponse()

    def test_the_stream_stays_open_and_sends_keep_alive_comments(self):
        conn, r = self.open_sse()
        self.addCleanup(conn.close)
        self.assertEqual(r.status, 200)
        self.assertEqual(r.getheader('Content-Type'), 'text/event-stream')
        lines = []
        while len([ln for ln in lines if ln.startswith(':')]) < 2:
            ln = r.fp.readline().decode()
            self.assertTrue(ln, 'o stream fechou')
            if ln.strip():
                lines.append(ln)
        self.assertTrue(all(ln.startswith(':') for ln in lines))

    def test_posts_still_work_with_the_stream_open_on_the_same_server_and_keep_alive_connections(self):
        conn, r = self.open_sse()
        self.addCleanup(conn.close)
        r.fp.readline()
        post = http.client.HTTPConnection('127.0.0.1', self.port, timeout=5)
        self.addCleanup(post.close)
        for i in (1, 2):  # duas requisições na mesma conexão: HTTP/1.1 com Content-Length
            post.request('POST', '/mcp', body=json.dumps({'jsonrpc': '2.0', 'id': i, 'method': 'ping'}),
                         headers={'Authorization': f'Bearer {self.token}', 'Content-Type': 'application/json'})
            resp = post.getresponse()
            self.assertEqual(json.loads(resp.read())['id'], i)

    def test_the_stream_closes_clean_when_the_server_stops(self):
        conn, r = self.open_sse()
        self.addCleanup(conn.close)
        r.fp.readline()
        self.server.request_stop()
        self.thread.join(5)
        deadline = time.time() + 5
        data = b'x'
        while data and time.time() < deadline:
            data = r.fp.readline()
        self.assertEqual(data, b'')

    def test_a_get_without_event_stream_is_not_acceptable(self):
        status, _, _ = self.request('GET', '/mcp')
        self.assertEqual(status, 406)


class BindTest(HomeCase):
    def test_a_busy_port_is_an_error_and_never_another_port(self):
        with socket.socket() as busy:
            busy.bind(('127.0.0.1', 0))
            busy.listen()
            port = busy.getsockname()[1]
            with self.assertRaises(OSError):
                server.make_server(port, 'tok', None)
            err = io.StringIO()
            with contextlib.redirect_stderr(err), self.assertRaises(SystemExit) as cm:
                cli.main(['serve', '--port', str(port)])
        self.assertNotEqual(cm.exception.code, 0)
        self.assertIn(str(port), str(cm.exception.code) + err.getvalue())

    def test_it_listens_only_on_loopback(self):
        s = server.make_server(0, 'tok', None)
        self.addCleanup(s.server_close)
        self.assertEqual(s.server_address[0], '127.0.0.1')


class FakeLaunchctl:
    """O launchctl de mentira: guarda as chamadas e o estado carregado; o bootstrap pode ligar um servidor."""

    def __init__(self, on_bootstrap=None):
        self.calls, self.loaded, self.on_bootstrap = [], False, on_bootstrap

    def __call__(self, *args):
        self.calls.append(list(args))
        if args[0] == 'print':
            return CompletedProcess(args, 0 if self.loaded else 113, '', '')
        if args[0] == 'bootstrap':
            self.loaded = True
            if self.on_bootstrap:
                self.on_bootstrap()
        elif args[0] == 'bootout':
            self.loaded = False
        return CompletedProcess(args, 0, '', '')

    def verbs(self):
        return [c[0] for c in self.calls]


class PlistTest(HomeCase):
    def test_the_plist_has_the_label_the_program_the_logs_and_the_environment(self):
        data = plistlib.loads(plistlib.dumps(launchd.build_plist()))
        entry = Path(ENTRY).resolve()
        self.assertEqual(data['Label'], 'com.brza.orchestrator')
        self.assertEqual(data['ProgramArguments'], [sys.executable, str(entry), 'serve'])
        self.assertTrue(data['RunAtLoad'])
        self.assertEqual(data['KeepAlive'], {'SuccessfulExit': False})
        self.assertEqual(data['StandardOutPath'], str(self.home / 'runtime' / 'mcp.out.log'))
        self.assertEqual(data['StandardErrorPath'], str(self.home / 'runtime' / 'mcp.err.log'))
        env = data['EnvironmentVariables']
        self.assertEqual(env['HOME'], str(Path.home()))
        self.assertEqual(env['BRZA_HOME'], str(self.home))
        self.assertEqual(env['PATH'].split(':'), [str(Path.home() / '.local' / 'bin'), '/opt/homebrew/bin',
                                                  '/usr/local/bin', '/usr/bin', '/bin', '/usr/sbin', '/sbin'])

    def test_the_agent_goes_to_the_launch_agents_dir(self):
        self.assertEqual(paths.plist_path(), self.agents / 'com.brza.orchestrator.plist')


class LifecycleTest(HomeCase):
    def setUp(self):
        super().setUp()
        self.fake = FakeLaunchctl()
        p = patch.object(launchd, 'run_launchctl', self.fake)
        p.start()
        self.addCleanup(p.stop)
        self.uid = os.getuid()

    def main(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        code = 0
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            try:
                cli.main(list(argv))
            except SystemExit as e:
                code = 1 if isinstance(e.code, str) else (e.code or 0)
                if isinstance(e.code, str):
                    print(e.code, file=err)
        return code, out.getvalue(), err.getvalue()

    def live_server(self):
        """Um servidor numa thread que responde o /health no lugar do agente, na porta do BRZA_MCP_PORT."""
        token = paths.ensure_token()
        s = server.make_server(0, token, None)
        os.environ['BRZA_MCP_PORT'] = str(s.server_address[1])
        s.quiet = True
        t = threading.Thread(target=s.serve_forever, kwargs={'poll_interval': 0.05}, daemon=True)
        t.start()

        def cleanup():
            s.request_stop()
            t.join(5)
            s.server_close()
        self.addCleanup(cleanup)

    def test_start_writes_the_plist_bootstraps_and_waits_for_the_health(self):
        self.fake.on_bootstrap = self.live_server
        code, out, err = self.main('start', '--timeout', '5')
        self.assertEqual(code, 0, err)
        self.assertTrue(paths.plist_path().is_file())
        self.assertIn(['bootstrap', f'gui/{self.uid}', str(paths.plist_path())], self.fake.calls)
        self.assertIn('responde', out)

    def test_start_on_a_loaded_agent_boots_it_out_and_bootstraps_again_to_reread_the_plist(self):  # A6
        self.fake.loaded = True
        self.fake.on_bootstrap = self.live_server
        code, _, err = self.main('start', '--timeout', '5')
        self.assertEqual(code, 0, err)
        verbs = self.fake.verbs()
        self.assertEqual(verbs[verbs.index('bootout'):][:2], ['bootout', 'bootstrap'])
        self.assertNotIn('kickstart', verbs)

    def test_start_without_a_health_before_the_deadline_fails(self):
        os.environ['BRZA_MCP_PORT'] = str(free_port())
        code, _, err = self.main('start', '--timeout', '0.5')
        self.assertEqual(code, 1)
        self.assertIn('health', err)

    def test_a_failing_bootstrap_is_an_error(self):
        def failing(*args):
            self.fake.calls.append(list(args))
            return CompletedProcess(args, 5, '', 'Bootstrap failed: 5')
        with patch.object(launchd, 'run_launchctl', failing):
            code, _, err = self.main('start', '--timeout', '0.5')
        self.assertEqual(code, 1)
        self.assertIn('Bootstrap failed', err)

    def test_stop_boots_the_agent_out(self):
        self.fake.loaded = True
        code, _, _ = self.main('stop')
        self.assertEqual(code, 0)
        self.assertIn(['bootout', f'gui/{self.uid}/com.brza.orchestrator'], self.fake.calls)
        self.assertFalse(self.fake.loaded)

    def test_stop_on_an_unloaded_agent_is_not_an_error(self):
        code, out, _ = self.main('stop')
        self.assertEqual(code, 0)
        self.assertNotIn('bootout', self.fake.verbs())
        self.assertIn('parado', out)

    def test_status_is_3_when_stopped_and_0_when_up(self):
        os.environ['BRZA_MCP_PORT'] = str(free_port())
        code, out, _ = self.main('status')
        self.assertEqual(code, 3)
        self.assertIn('carregado: não', out)
        self.fake.loaded = True
        self.live_server()
        code, out, _ = self.main('status')
        self.assertEqual(code, 0)
        self.assertIn('carregado: sim', out)
        self.assertIn('health: responde', out)

    def test_status_is_3_when_the_health_answers_but_the_agent_is_not_loaded(self):  # A6
        self.live_server()
        code, out, _ = self.main('status')
        self.assertEqual(code, 3)
        self.assertIn('carregado: não', out)
        self.assertIn('health: responde', out)

    def test_status_is_3_when_the_agent_is_loaded_but_the_health_does_not_answer(self):  # A6
        os.environ['BRZA_MCP_PORT'] = str(free_port())
        self.fake.loaded = True
        self.assertEqual(self.main('status')[0], 3)

    def plist_with(self, **env):
        paths.plist_path().parent.mkdir(parents=True, exist_ok=True)
        paths.plist_path().write_bytes(plistlib.dumps({'Label': 'x', 'EnvironmentVariables': env}))

    def test_status_outside_the_workspace_finds_the_port_in_the_runtime_info(self):  # P1 r2 A1
        self.fake.loaded = True
        self.live_server()
        port = os.environ.pop('BRZA_MCP_PORT')
        paths.ensure_runtime()
        paths.info_path().write_text(json.dumps({'pid': 1, 'port': int(port)}))
        code, out, _ = self.main('status')
        self.assertEqual(code, 0, out)
        self.assertIn('health: responde', out)

    def test_status_outside_the_workspace_finds_the_port_in_the_plist(self):  # P1 r2 A1
        self.fake.loaded = True
        self.live_server()
        self.plist_with(BRZA_MCP_PORT=os.environ.pop('BRZA_MCP_PORT'))
        self.assertEqual(self.main('status')[0], 0)

    def test_status_outside_the_workspace_finds_the_toml_port_of_the_plist_workspace(self):  # P1 r2 A1
        self.fake.loaded = True
        self.live_server()
        ws = self.tmp / 'ws'
        ws.mkdir()
        (ws / 'brza.toml').write_text(f'[mcp]\nport = {os.environ.pop("BRZA_MCP_PORT")}\n')
        self.plist_with(BRZA_WORKSPACE=str(ws))
        self.assertEqual(self.main('status')[0], 0)

    def test_stop_with_a_failing_bootout_is_an_error_without_a_traceback(self):  # P1 r2 A2
        self.fake.loaded = True
        real = self.fake

        def failing(*args):
            if args[0] == 'bootout':
                return CompletedProcess(args, 5, '', 'Boot-out failed: 5')
            return real(*args)
        with patch.object(launchd, 'run_launchctl', failing):
            code, _, err = self.main('stop')
        self.assertEqual(code, 1)
        self.assertIn('Boot-out failed', err)

    def test_start_refuses_python_below_3_10_before_any_launchctl_call(self):  # A4
        with patch.object(sys, 'version_info', (3, 9, 6, 'final', 0)):
            code, _, err = self.main('start')
        self.assertEqual(code, 1)
        self.assertIn('3.10', err)
        self.assertNotIn('Traceback', err)
        self.assertEqual(self.fake.calls, [])
        self.assertFalse(paths.plist_path().exists())

    def test_url_prints_the_mcp_url_with_the_port_precedence(self):  # A2
        self.assertEqual(self.main('url')[1].strip(), 'http://127.0.0.1:4242/mcp')
        ws = self.tmp / 'ws'
        ws.mkdir()
        (ws / 'brza.toml').write_text('[mcp]\nport = 4300\n')
        os.environ['BRZA_WORKSPACE'] = str(ws)
        self.assertEqual(self.main('url')[1].strip(), 'http://127.0.0.1:4300/mcp')
        os.environ['BRZA_MCP_PORT'] = '4400'
        self.assertEqual(self.main('url')[1].strip(), 'http://127.0.0.1:4400/mcp')
        self.assertEqual(self.main('url', '--port', '4500')[1].strip(), 'http://127.0.0.1:4500/mcp')

    def test_the_toml_is_read_by_the_hooks_parser_below_python_3_11(self):  # A2
        ws = self.tmp / 'ws'
        ws.mkdir()
        (ws / 'brza.toml').write_text('[mcp]\nport = 4301\n')
        os.environ['BRZA_WORKSPACE'] = str(ws)
        with patch.dict(sys.modules, {'tomllib': None}):
            self.assertEqual(paths.default_port(), 4301)

    def test_a_workspace_without_the_key_or_the_file_keeps_4242(self):  # A2
        ws = self.tmp / 'ws'
        ws.mkdir()
        os.environ['BRZA_WORKSPACE'] = str(ws)
        self.assertEqual(paths.default_port(), 4242)
        (ws / 'brza.toml').write_text('[outro]\nx = 1\n')
        self.assertEqual(paths.default_port(), 4242)

    def test_the_plist_carries_the_workspace_of_the_environment_or_of_the_cwd(self):  # A3
        self.assertNotIn('BRZA_WORKSPACE', launchd.build_plist()['EnvironmentVariables'])
        ws = self.tmp / 'ws'
        (ws / '.brza').mkdir(parents=True)
        (ws / '.brza' / 'workspace.toml').write_text('schema = 1\n\n[workspace]\nname = "w"\n')
        subprocess.run(['git', '-C', str(ws), 'init', '-q'], check=True)
        os.chdir(ws)
        self.assertEqual(launchd.build_plist()['EnvironmentVariables']['BRZA_WORKSPACE'], str(ws.resolve()))
        other = self.tmp / 'outro'
        os.environ['BRZA_WORKSPACE'] = str(other)
        self.assertEqual(launchd.build_plist()['EnvironmentVariables']['BRZA_WORKSPACE'], str(other))

    @unittest.skipUnless(Path('/usr/bin/python3').exists(), 'sem /usr/bin/python3')
    def test_the_package_imports_on_the_system_python(self):  # A4
        r = subprocess.run(['/usr/bin/python3', str(ENTRY), '--help'], capture_output=True, text=True,
                           env={**os.environ, 'PYTHONDONTWRITEBYTECODE': '1'})
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_token_prints_and_creates_it(self):
        code, out, _ = self.main('token')
        self.assertEqual(code, 0)
        self.assertEqual(out.strip(), paths.token_path().read_text().strip())
        self.assertEqual(self.main('token')[1], out)


def free_port() -> int:
    with socket.socket() as s:
        s.bind(('127.0.0.1', 0))
        return s.getsockname()[1]


class ProcessTest(HomeCase):
    def test_serve_as_a_process_answers_the_health_and_stops_clean_on_sigterm(self):
        port = free_port()
        env = {**os.environ, 'BRZA_HOME': str(self.home), 'PYTHONDONTWRITEBYTECODE': '1'}
        proc = subprocess.Popen([sys.executable, str(ENTRY), 'serve', '--port', str(port)], env=env,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        self.addCleanup(lambda: proc.poll() is None and proc.kill())
        token_file = self.home / 'runtime' / 'mcp.token'
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        body = None
        deadline = time.time() + 10
        while time.time() < deadline and body is None:
            try:
                req = urllib.request.Request(f'http://127.0.0.1:{port}/health',
                                             headers={'Authorization': f'Bearer {token_file.read_text().strip()}'})
                body = json.load(opener.open(req, timeout=1))
            except (OSError, ValueError):
                time.sleep(0.1)
        self.assertIsNotNone(body, proc.poll())
        self.assertEqual(body['pid'], proc.pid)
        req = urllib.request.Request(f'http://127.0.0.1:{port}/mcp', headers={
            'Authorization': f'Bearer {token_file.read_text().strip()}', 'Content-Type': 'application/json'},
            data=json.dumps({'jsonrpc': '2.0', 'id': 1, 'method': 'tools/list'}).encode())
        listed = json.load(opener.open(req, timeout=5))['result']['tools']  # P3 O-A6: o serve liga as ferramentas
        self.assertEqual({t['name'] for t in listed}, {'dispatch', 'status', 'config'})
        info = json.loads((self.home / 'runtime' / 'mcp.json').read_text())
        self.assertEqual((info['pid'], info['port']), (proc.pid, port))
        self.assertIn('started', info)
        proc.send_signal(signal.SIGTERM)
        _, err = proc.communicate(timeout=10)
        self.assertEqual(proc.returncode, 0, err)
        self.assertFalse((self.home / 'runtime' / 'mcp.json').exists())


if __name__ == '__main__':
    unittest.main()
