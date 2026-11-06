"""Testes do claude-brza e do brza-run. Os do brza-run vêm dos do orca-run do workspace de origem. O brza-install tem o
test_brza_install.py.

    python3 -m unittest discover -s tests/cli
"""
import contextlib
import io
import json
import os
import runpy
import shlex
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

HERE = Path(__file__).parent
ROOT = HERE.resolve().parent.parent
BIN = ROOT / 'bin'
CLIENT = ROOT / 'packages' / 'client' / 'bin'
os.environ['BRZA_REGISTRY'] = '/nonexistent/brza/workspaces.toml'  # nada aqui lê o registro real da máquina
WRAPPER = runpy.run_path(str(CLIENT / 'claude-brza'))
RUN = runpy.run_path(str(CLIENT / 'brza-run'))
INSTALL = runpy.run_path(str(BIN / 'brza-install'))
AGENTS = runpy.run_path(str(BIN / 'brza-agents'))
sys.path.insert(0, str(BIN))
import brza_runtime as runtime  # noqa: E402

runtime_agents = runtime.agents_json


def git(*args):
    subprocess.run(['git', *args], check=True, capture_output=True)


MANIFEST = 'schema = 1\n\n[workspace]\nname = "acme-notes"\n\n[code]\nname = "acme-app"\npath = "{code}"\n'


class WrapperTest(unittest.TestCase):
    """O workspace e o código com nomes que não são os do BrzA: o claude-brza acha os dois pelo manifesto e pelo
    registro, e não pelo nome da pasta."""

    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.base = Path(temp.name).resolve()
        self.workspace, self.studio = self.base / 'acme-notes', self.base / 'acme-app'
        for repo in (self.workspace, self.studio):
            repo.mkdir()
            git('-C', str(repo), 'init', '-q')
            git('-C', str(repo), '-c', 'user.name=t', '-c', 'user.email=t@t', 'commit', '-q', '--allow-empty', '-m', 'x')
        (self.workspace / '.brza').mkdir()
        (self.workspace / '.brza' / 'workspace.toml').write_text(MANIFEST.format(code=self.studio))
        self.registry = self.base / 'config' / 'workspaces.toml'
        self.register(self.workspace, self.studio)
        self.env = {'PATH': '/bin'}
        p = patch.dict(os.environ, {'BRZA_REGISTRY': str(self.registry)})
        p.start()
        self.addCleanup(p.stop)
        os.environ.pop('BRZA_WORKSPACE', None)

    def register(self, root: Path, code: Path):
        self.registry.parent.mkdir(parents=True, exist_ok=True)
        self.registry.write_text(f'[[workspace]]\nroot = "{root}"\ncode = "{code}"\n')

    def test_checkouts_and_worktrees_outside_the_folder_are_found_with_their_role(self):
        outside = self.base / 'orca' / 'workspaces' / 'acme-app' / 'feat'
        outside.parent.mkdir(parents=True)
        git('-C', str(self.studio), 'worktree', 'add', '-q', str(outside))
        (self.studio / 'src').mkdir()
        for path, role in ((self.workspace, 'notes'), (self.studio / 'src', 'code'), (outside, 'code'),
                           (Path(str(self.studio).upper()), 'code')):
            found = WRAPPER['workspace_of'](path, self.env)
            self.assertEqual((found.role, found.root), (role, self.workspace), path)

    def test_other_repos_root_and_unregistered_code_are_not_found(self):
        other = self.base / 'outro-workspace'
        other.mkdir()
        git('-C', str(other), 'init', '-q')
        for path in (other, Path('/'), Path(str(self.studio) + '-outro')):
            self.assertIsNone(WRAPPER['workspace_of'](path, self.env), path)
        self.registry.unlink()
        self.assertIsNone(WRAPPER['workspace_of'](self.studio, self.env))
        self.assertIsNotNone(WRAPPER['workspace_of'](self.workspace, self.env))

    def test_command_adds_settings_only_inside_a_workspace(self):
        args, out = WRAPPER['command']('/x/claude', ['-p', 'oi'], Path('/'), self.env)
        self.assertEqual((args, out), (['/x/claude', '-p', 'oi'], self.env))
        args, out = WRAPPER['command']('/x/claude', ['--input-format', 'stream-json'], self.studio, self.env)
        self.assertEqual(args, ['/x/claude', '--settings', str(self.workspace / '.claude' / 'settings.json'),
                                '--input-format', 'stream-json'])
        self.assertEqual(out['BRZA_WORKSPACE'], str(self.workspace))
        self.assertNotIn('GH_CONFIG_DIR', out)

    def test_the_mcp_token_is_exported_only_when_the_file_exists_and_is_not_empty(self):
        home = self.base / 'brza-home'
        env = {**self.env, 'BRZA_HOME': str(home)}
        self.assertNotIn('BRZA_MCP_TOKEN', WRAPPER['command']('/x/claude', [], self.studio, env)[1])
        (home / 'runtime').mkdir(parents=True)
        (home / 'runtime' / 'mcp.token').write_text('\n')
        self.assertNotIn('BRZA_MCP_TOKEN', WRAPPER['command']('/x/claude', [], self.studio, env)[1])
        (home / 'runtime' / 'mcp.token').write_text('segredo-123\n')
        self.assertEqual(WRAPPER['command']('/x/claude', [], self.studio, env)[1]['BRZA_MCP_TOKEN'], 'segredo-123')
        # fora de workspace, nada muda
        self.assertEqual(WRAPPER['command']('/x/claude', [], Path('/'), env)[1], env)

    def test_the_variable_overrides_the_root_and_the_role_still_comes_from_the_cwd(self):
        other = self.base / 'outro-ws'
        (other / '.brza').mkdir(parents=True)
        (other / '.brza' / 'workspace.toml').write_text('schema = 1\n')
        env = {**self.env, 'BRZA_WORKSPACE': str(other)}
        args, out = WRAPPER['command']('/x/claude', [], self.workspace, env)
        self.assertEqual(args, ['/x/claude'])  # o cwd não é do outro workspace nem do código registrado nele
        env = {**self.env, 'BRZA_WORKSPACE': str(self.workspace)}
        args, out = WRAPPER['command']('/x/claude', [], self.studio, env)
        self.assertEqual(out['BRZA_WORKSPACE'], str(self.workspace))
        self.assertIn('--settings', args)

    def add_agents(self):
        folder = self.workspace / '.claude' / 'agents'
        folder.mkdir(parents=True)
        (folder / 'reviewer.md').write_text('---\ndescription: Revisa\nmodel: opus\nmemory: project\n---\nRevise.\n')
        return json.loads(runtime_agents(self.workspace))

    def settings_args(self):
        return ['--settings', str(self.workspace / '.claude' / 'settings.json')]

    def test_the_code_gets_the_agents_after_settings_and_the_workspace_does_not(self):
        agents = self.add_agents()
        args, _ = WRAPPER['command']('/x/claude', ['-p', 'oi'], self.studio, self.env)
        self.assertEqual(args[:4], ['/x/claude', *self.settings_args(), '--agents'])
        self.assertEqual(json.loads(args[4]), agents)
        self.assertEqual(args[5:], ['-p', 'oi'])
        args, _ = WRAPPER['command']('/x/claude', ['-p', 'oi'], self.workspace, self.env)
        self.assertEqual(args, ['/x/claude', *self.settings_args(), '-p', 'oi'])

    def test_a_code_worktree_outside_the_code_folder_gets_the_agents_too(self):
        self.add_agents()
        outside = self.base / 'orca' / 'workspaces' / 'acme-app' / 'feat'
        outside.parent.mkdir(parents=True)
        git('-C', str(self.studio), 'worktree', 'add', '-q', str(outside))
        args, _ = WRAPPER['command']('/x/claude', [], outside, self.env)
        self.assertIn('--agents', args)
        ws_outside = self.base / 'orca' / 'workspaces' / 'acme-notes' / 'feat'
        ws_outside.parent.mkdir(parents=True)
        git('-C', str(self.workspace), 'worktree', 'add', '-q', str(ws_outside))
        args, _ = WRAPPER['command']('/x/claude', [], ws_outside, self.env)
        self.assertNotIn('--agents', args)
        self.assertIn('--settings', args)

    def test_the_agents_the_user_passed_win_in_both_forms(self):
        self.add_agents()
        for given in (['--agents', '{}', '-p', 'oi'], ['--agents={}', '-p', 'oi']):
            with self.subTest(given=given):
                args, _ = WRAPPER['command']('/x/claude', given, self.studio, self.env)
                self.assertEqual(args, ['/x/claude', *self.settings_args(), *given])

    def test_without_agents_the_code_command_has_only_the_settings(self):
        args, _ = WRAPPER['command']('/x/claude', ['-p', 'oi'], self.studio, self.env)
        self.assertEqual(args, ['/x/claude', *self.settings_args(), '-p', 'oi'])
        args, _ = WRAPPER['command']('/x/claude', ['-p', 'oi'], Path('/'), self.env)
        self.assertEqual(args, ['/x/claude', '-p', 'oi'])

    def test_a_failing_agents_json_does_not_cost_the_settings(self):
        self.add_agents()
        (self.workspace / '.claude' / 'agents' / 'ruim.md').write_bytes(b'\xff\xfe---\n')
        args, _ = WRAPPER['command']('/x/claude', ['-p', 'oi'], self.studio, self.env)
        self.assertEqual(args, ['/x/claude', *self.settings_args(), '-p', 'oi'])

    def test_the_wrapper_has_no_fixed_name_or_path(self):
        source = (CLIENT / 'claude-brza').read_text()
        for fixed in ("'brza-workspace'", "'brza-studio'", 'BRZA_STUDIO', 'def roots', 'def repo_of'):
            self.assertNotIn(fixed, source)

    def test_the_linked_wrapper_runs_the_real_claude_with_the_workspace_of_the_cwd(self):
        """O claude-brza como o app o roda: por um link, com um claude de mentira no PATH, no código registrado."""
        links, real = self.base / 'links', self.base / 'real'
        links.mkdir()
        real.mkdir()
        (links / 'claude-brza').symlink_to(CLIENT / 'claude-brza')
        (real / 'claude').write_text(f'#!{sys.executable}\nimport json, os, sys\n'
                                     "print(json.dumps([sys.argv[1:], os.environ.get('BRZA_WORKSPACE')]))\n")
        (real / 'claude').chmod(0o755)
        env = {**os.environ, 'PATH': f'{real}:/usr/bin:/bin', 'PYTHONDONTWRITEBYTECODE': '1'}
        env.pop('BRZA_WORKSPACE', None)
        for cwd, expected in ((self.studio, [['--settings', str(self.workspace / '.claude' / 'settings.json'), '-p', 'oi'],
                                             str(self.workspace)]),
                              (self.base, [['-p', 'oi'], None])):
            r = subprocess.run([str(links / 'claude-brza'), '-p', 'oi'], capture_output=True, text=True, cwd=str(cwd),
                               env=env)
            self.assertEqual((r.returncode, json.loads(r.stdout)), (0, expected), r.stderr)

    def test_wrapper_skips_itself_in_path(self):
        mine, real = self.base / 'mine', self.base / 'real'
        mine.mkdir()
        real.mkdir()
        (mine / 'claude').symlink_to(CLIENT / 'claude-brza')
        (real / 'claude').write_text('#!/bin/sh\n')
        (real / 'claude').chmod(0o755)
        with patch.dict(os.environ, {'PATH': f'{mine}:{real}'}):
            self.assertEqual(WRAPPER['real_claude'](), str(real / 'claude'))


class AgentsCliTest(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.ws = Path(temp.name)

    def run_cli(self, *argv):
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
            code = AGENTS['main'](list(argv))
        return code, out.getvalue()

    def test_prints_the_json_in_one_line(self):
        folder = self.ws / '.claude' / 'agents'
        folder.mkdir(parents=True)
        (folder / 'collector.md').write_text('---\ndescription: Coleta\nmodel: haiku\n---\nColete.\n')
        code, out = self.run_cli('--workspace', str(self.ws))
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out), {'collector': {'description': 'Coleta', 'prompt': 'Colete.',
                                                         'model': 'haiku'}})
        self.assertEqual(len(out.splitlines()), 1)

    def test_without_agents_prints_nothing_and_exits_0(self):
        self.assertEqual(self.run_cli('--workspace', str(self.ws)), (0, ''))

    def test_it_uses_the_workspace_of_the_environment_by_default(self):
        folder = self.ws / '.claude' / 'agents'
        folder.mkdir(parents=True)
        (folder / 'a.md').write_text('---\ndescription: x\n---\ny\n')
        with patch.object(runtime, 'WORKSPACE', self.ws):
            code, out = self.run_cli()
        self.assertEqual((code, list(json.loads(out))), (0, ['a']))

    def test_without_a_workspace_it_refuses_with_exit_1_and_a_clear_message(self):  # A5
        with patch.object(runtime, 'WORKSPACE', None):
            code, out = self.run_cli()  # o stderr vai junto
        self.assertEqual(code, 1)
        self.assertIn('recusado: nenhum workspace achado', out)

    def test_the_installer_picks_it_up_as_a_command(self):
        self.assertTrue((BIN / 'brza-agents').stat().st_mode & 0o111)
        self.assertIn('brza-agents', INSTALL['commands'](BIN))


@contextlib.contextmanager
def quiet_terminal(temp):
    """O zsh sem o ~/.zshrc de quem roda o teste, e a cópia da saída que o pty escreve direto no fd 1 calada."""
    saved, devnull = os.dup(1), os.open(os.devnull, os.O_WRONLY)
    os.dup2(devnull, 1)
    try:
        with patch.dict(os.environ, {'ORCA_CHAT_SCRATCH_DIR': temp, 'SHELL': '/bin/zsh', 'ZDOTDIR': temp}):
            yield
    finally:
        os.dup2(saved, 1)
        os.close(saved)
        os.close(devnull)


@contextlib.contextmanager
def no_workspace():
    """Sem a BRZA_WORKSPACE de quem roda o teste: a linha `run` não vai ao workspace real."""
    with patch.dict(os.environ):
        os.environ.pop('BRZA_WORKSPACE', None)
        yield


def fake_workspace(base: Path) -> Path:
    """Um workspace mínimo, com o manifesto, para a BRZA_WORKSPACE apontar."""
    (base / '.brza').mkdir(parents=True)
    (base / '.brza' / 'workspace.toml').write_text('schema = 1\n')
    return base


def measured(workspace: Path) -> list[dict]:
    log = workspace / '.measurements' / 'measurements.jsonl'
    return [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []


class BrzaRunTest(unittest.TestCase):
    def test_clean_strips_color_and_rewritten_lines(self):
        raw = b'\x1b[32mok\x1b[0m\r\n10%\r50%\r100%\r\n\x1b]0;titulo\x07\x1b>\x1b(Bfim\r\n'
        self.assertEqual(RUN['clean'](raw), 'ok\n100%\nfim\n')

    def test_tail_keeps_last_lines_within_the_byte_cap(self):
        text = '\n'.join(f'linha {i}' for i in range(500)) + '\n'
        shown, cut = RUN['tail'](text)
        self.assertEqual((shown.split('\n')[0], shown.split('\n')[-1], cut), ('linha 300', 'linha 499', 300))
        shown, cut = RUN['tail']('\n'.join(['x' * 1000] * 50))
        self.assertLessEqual(len(shown.encode()), RUN['TAIL_BYTES'])
        self.assertEqual(cut, 50 - len(shown.split('\n')))
        self.assertEqual(RUN['tail']('  \n'), ('', 0))

    def test_fence_outgrows_backticks_in_the_output(self):
        self.assertEqual(RUN['fenced']('a ```b``` ````c', 'bash'), '`````bash\na ```b``` ````c\n`````')

    def test_run_sets_shell_sessions_disable(self):
        with tempfile.TemporaryDirectory() as temp, quiet_terminal(temp),                 contextlib.redirect_stdout(io.StringIO()):
            RUN['run_here']('echo ok', Path(temp))
            self.assertEqual(os.environ.get('SHELL_SESSIONS_DISABLE'), '1')

    def test_report_points_to_the_full_output_only_when_cut(self):
        log = Path('/tmp/r/output.log')
        self.assertEqual(RUN['report']('ls', 0, '', log), '```bash\nls\n```\n```\n(sem saída)\n```\nSaiu com 0.')
        long = RUN['report']('make', 2, '\n'.join(map(str, range(201))), log)
        self.assertTrue(long.endswith('Saiu com 2. Cortei as 1 linhas antes destas; '
                                      'a saída inteira está em `/tmp/r/output.log`.'))

    def test_send_mutation_matches_the_app_fingerprint(self):
        # impressão digital conferida com o structuredAgentSessionPayloadFingerprint do brza-studio no node
        text = 'Rodei pelo ▶:\n```bash\nls\t-la\n```\n\x01 ção 😀   "aspas" \\ fim'
        params = RUN['send_mutation']('claude_x', text, 1, 1792900000000, '0' * 32)
        self.assertEqual(params['envelope'], {
            'sessionId': 'claude_x', 'clientOperationId': '1792900000000-' + '0' * 32, 'expectedRuntimeFence': 1,
            'payloadFingerprint': '6c1f3d3de96998c2cb1c2ed99684ed68b1cdea5a8bc9bff9955819b4535bfb31'})
        self.assertEqual(params['delivery'], 'queue-if-active')

    def test_chat_mode_runs_here_and_sends_the_blocks(self):
        calls = []

        def rpc(method, params):
            calls.append((method, params))
            return {'page': {'fence': 7}} if method == 'agentSession.history' else {}

        with tempfile.TemporaryDirectory() as temp, no_workspace(), quiet_terminal(temp), \
                patch.object(RUN['runtime'], 'rpc', rpc), contextlib.redirect_stdout(io.StringIO()), \
                contextlib.redirect_stderr(io.StringIO()):
            code = RUN['run_in_chat']("printf 'um\\ndois\\n'; exit 3", 'claude_x')
        self.assertEqual(code, 3)
        method, params = calls[1]
        self.assertEqual((method, params['envelope']['expectedRuntimeFence']), ('agentSession.send', 7))
        self.assertEqual(params['body']['blocks'][0]['text'],
                         "Rodei pelo ▶:\n\n```bash\nprintf 'um\\ndois\\n'; exit 3\n```\n```\num\ndois\n```\nSaiu com 3.")

    def tab_run(self, command, env=None, result=None, cwd=HERE):
        """O `brza run -- <comando>` com a aba de mentira: ela roda o --command na hora, ou, com `result`, só grava o
        result.json e o output.log de fixture. O ambiente vem sem a BRZA_WORKSPACE, a menos que `env` a traga, para
        nada gravar no workspace de quem roda o teste."""
        calls = []

        def brza(*args):
            calls.append(args)
            if args[:2] == ('terminal', 'create'):
                inner = args[args.index('--command') + 1]
                if result is None:
                    # a aba do app digita o --command num shell: aqui ele roda na hora
                    subprocess.run(['/bin/sh', '-c', inner], capture_output=True)
                else:
                    words = shlex.split(inner)
                    path = Path(words[words.index('--result') + 1])
                    (path.parent / 'output.log').write_text(result.pop('output', ''))
                    path.write_text(json.dumps(result))
            return {'terminal': {'handle': 'term-1'}} if args[1] == 'create' else {'terminals': []}

        out, err = io.StringIO(), io.StringIO()
        clean = {k: v for k, v in os.environ.items()
                 if k not in ('BRZA_WORKSPACE', 'CLAUDE_CODE_SESSION_ID', 'ORCA_AGENT_SESSION_ID')}
        with tempfile.TemporaryDirectory() as temp, patch.dict(os.environ, {**clean, **(env or {})}, clear=True), \
                quiet_terminal(temp), patch.object(RUN['runtime'], 'brza', brza), contextlib.redirect_stdout(out), \
                contextlib.redirect_stderr(err):
            code = RUN['run_in_tab'](command, cwd, poll=0)
        self.last_stderr = err.getvalue()
        return code, calls, out.getvalue()

    def test_tab_mode_prints_blocks_and_closes_on_success(self):
        code, calls, out = self.tab_run(['echo', "'a b'", '&&', 'pwd'])
        self.assertEqual(code, 0)
        self.assertEqual(out, f"```bash\necho 'a b' && pwd\n```\n```\na b\n{HERE}\n```\nSaiu com 0.\n")
        create = calls[0]
        top = subprocess.run(['git', '-C', str(HERE), 'rev-parse', '--show-toplevel'],
                             capture_output=True, text=True).stdout.strip()
        self.assertEqual(create[create.index('--worktree') + 1], f'path:{top}')
        self.assertEqual(calls[-1], ('terminal', 'close', '--terminal', 'term-1'))

    def test_tab_mode_keeps_the_tab_on_failure(self):
        code, calls, out = self.tab_run(['exit 4'])
        self.assertEqual(code, 4)
        self.assertTrue(out.endswith('Saiu com 4.\n'))
        self.assertNotIn('close', [c[1] for c in calls])

    # D5 — o brza run mede

    def test_run_here_measures_the_duration_the_cpu_and_the_peak_of_the_command(self):
        burn = (f"{shlex.quote(sys.executable)} -c 'x = bytearray(80 * 2**20); "
                "import time; t = time.process_time()\nwhile time.process_time() - t < 0.3: pass'")
        with tempfile.TemporaryDirectory() as temp, quiet_terminal(temp):
            code, output, metrics = RUN['run_here'](burn, Path(temp))
        self.assertEqual((code, output.strip()), (0, ''))
        self.assertEqual(set(metrics), {'duration_s', 'cpu_s', 'max_rss_mb'})
        self.assertGreater(metrics['duration_s'], 0.3)
        self.assertGreaterEqual(metrics['cpu_s'], 0.25)
        self.assertGreaterEqual(metrics['max_rss_mb'], 80)  # o ru_maxrss é bytes no macOS, KB no Linux
        self.assertLess(metrics['max_rss_mb'], 4096)

    def test_run_for_result_keeps_the_code_and_adds_the_metrics(self):
        with tempfile.TemporaryDirectory() as temp, quiet_terminal(temp):
            result = Path(temp) / 'result.json'
            code = RUN['run_for_result']('exit 5', result)
            saved = json.loads(result.read_text())
        self.assertEqual((code, saved['code']), (5, 5))
        self.assertEqual(list(saved), ['code', 'duration_s', 'cpu_s', 'max_rss_mb'])
        self.assertGreater(saved['duration_s'], 0)

    def test_tab_mode_writes_the_run_line_with_the_session_of_the_environment(self):
        with tempfile.TemporaryDirectory() as temp:
            ws = fake_workspace(Path(temp) / 'ws')
            long = ['pnpm', 'test', 'x' * 300]
            code, calls, out = self.tab_run(long, env={'BRZA_WORKSPACE': str(ws), 'CLAUDE_CODE_SESSION_ID': 'sess-1'},
                                            result={'code': 0, 'duration_s': 312.4, 'cpu_s': 845.2, 'max_rss_mb': 1840,
                                                    'output': 'ok\n'})
            lines = measured(ws)
        self.assertEqual(code, 0)
        self.assertEqual(out, f"```bash\n{' '.join(long)}\n```\n```\nok\n```\nSaiu com 0.\n")
        self.assertEqual(calls[-1], ('terminal', 'close', '--terminal', 'term-1'))
        self.assertEqual(len(lines), 1)
        line = lines[0]
        self.assertEqual(list(line), ['event', 'timestamp', 'session_id', 'chat_id', 'mode', 'cwd', 'workstream',
                                      'command', 'code', 'duration_s', 'cpu_s', 'max_rss_mb'])
        self.assertEqual({k: line[k] for k in ('event', 'session_id', 'chat_id', 'mode', 'cwd', 'code', 'duration_s',
                                               'cpu_s', 'max_rss_mb')},
                         {'event': 'run', 'session_id': 'sess-1', 'chat_id': None, 'mode': 'tab', 'cwd': str(HERE),
                          'code': 0, 'duration_s': 312.4, 'cpu_s': 845.2, 'max_rss_mb': 1840})
        self.assertEqual(line['command'], ' '.join(long)[:200])
        self.assertRegex(line['timestamp'], r'^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d[+-]\d\d:\d\d$')

    def test_tab_mode_measures_the_real_command_and_keeps_the_tab_on_failure(self):
        with tempfile.TemporaryDirectory() as temp:
            ws = fake_workspace(Path(temp) / 'ws')
            code, calls, out = self.tab_run(['exit 4'], env={'BRZA_WORKSPACE': str(ws)})
            lines = measured(ws)
        self.assertEqual(code, 4)
        self.assertTrue(out.endswith('Saiu com 4.\n'))
        self.assertNotIn('close', [c[1] for c in calls])
        self.assertEqual([(l['code'], l['session_id'], l['mode']) for l in lines], [(4, None, 'tab')])
        self.assertGreater(lines[0]['duration_s'], 0)

    def test_the_line_carries_the_workstream_of_the_cwd(self):
        with tempfile.TemporaryDirectory() as temp:
            ws = fake_workspace(Path(temp).resolve() / 'ws')
            git('-C', str(ws), 'init', '-q', '-b', 'workstream/demo')
            git('-C', str(ws), '-c', 'user.name=t', '-c', 'user.email=t@t', 'commit', '-q', '--allow-empty', '-m', 'x')
            self.tab_run(['true'], env={'BRZA_WORKSPACE': str(ws)}, cwd=ws)
            lines = measured(ws)
        self.assertEqual([(l['workstream'], l['cwd']) for l in lines], [('demo', str(ws))])
        self.assertGreater(lines[0]['duration_s'], 0)
        for key in ('cpu_s', 'max_rss_mb'):
            self.assertIsInstance(lines[0][key], (int, float))
            self.assertGreaterEqual(lines[0][key], 0)

    def test_without_a_workspace_there_is_no_line_and_a_warning_on_stderr(self):
        code, calls, out = self.tab_run(['echo', 'oi'], env={'CLAUDE_CODE_SESSION_ID': 'sess-1'})
        self.assertEqual(code, 0)
        self.assertEqual(out, '```bash\necho oi\n```\n```\noi\n```\nSaiu com 0.\n')
        self.assertEqual(calls[-1], ('terminal', 'close', '--terminal', 'term-1'))
        self.assertIn('brza-run: sem workspace', self.last_stderr)

    def test_an_unwritable_log_warns_and_changes_nothing_else(self):
        with tempfile.TemporaryDirectory() as temp:
            ws = fake_workspace(Path(temp) / 'ws')
            (ws / '.measurements').write_text('não é pasta')
            code, calls, out = self.tab_run(['echo', 'oi'], env={'BRZA_WORKSPACE': str(ws)})
        self.assertEqual((code, out), (0, '```bash\necho oi\n```\n```\noi\n```\nSaiu com 0.\n'))
        self.assertEqual(calls[-1], ('terminal', 'close', '--terminal', 'term-1'))
        self.assertIn('brza-run: não gravei a medida', self.last_stderr)

    def chat_run(self, provider):
        calls = []

        def rpc(method, params):
            calls.append((method, params))
            if method == 'agentSession.history':
                if provider is None and any(c[0] == 'agentSession.send' for c in calls):
                    return None
                if provider is OSError:
                    raise RUN['runtime'].BrzaError('o runtime não respondeu')
                return {'page': {'fence': 7}, 'providerSession': provider}
            return {}

        with tempfile.TemporaryDirectory() as temp:
            ws = fake_workspace(Path(temp) / 'ws')
            with patch.dict(os.environ, {'BRZA_WORKSPACE': str(ws)}), quiet_terminal(temp), \
                    patch.object(RUN['runtime'], 'rpc', rpc), contextlib.redirect_stdout(io.StringIO()), \
                    contextlib.redirect_stderr(io.StringIO()):
                code = RUN['run_in_chat']('exit 2', 'claude_x')
            return code, measured(ws)

    def test_chat_mode_writes_the_line_with_the_chat_and_its_provider_session(self):
        code, lines = self.chat_run({'id': 'sess-9'})
        self.assertEqual(code, 2)
        self.assertEqual([(l['event'], l['mode'], l['chat_id'], l['session_id'], l['code']) for l in lines],
                         [('run', 'chat', 'claude_x', 'sess-9', 2)])
        self.assertEqual(set(lines[0]) >= {'duration_s', 'cpu_s', 'max_rss_mb'}, True)

    def test_chat_mode_without_the_app_writes_a_null_session(self):
        code, lines = self.chat_run(OSError)
        self.assertEqual(code, 2)
        self.assertEqual([(l['chat_id'], l['session_id']) for l in lines], [('claude_x', None)])

    def test_chat_mode_with_a_null_rpc_result_still_writes_the_line_and_returns_the_code(self):
        code, lines = self.chat_run(None)
        self.assertEqual(code, 2)
        self.assertEqual([(l['chat_id'], l['session_id']) for l in lines], [('claude_x', None)])


if __name__ == '__main__':
    unittest.main()
