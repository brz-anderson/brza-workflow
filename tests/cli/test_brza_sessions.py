"""Testes do brza-sessions.

    python3 -m unittest discover -s tests/cli
"""
import hashlib
import io
import json
import os
import runpy
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

os.environ['BRZA_REGISTRY'] = '/nonexistent/brza/workspaces.toml'  # nada aqui lê o registro real da máquina
SESSIONS = runpy.run_path(str(Path(__file__).resolve().parent.parent.parent / 'packages' / 'client' / 'bin' / 'brza-sessions'))
# As funções leem os globais do módulo em que nasceram, não da cópia que o run_path devolve.
GLOBALS = SESSIONS['main'].__globals__


class RunningTest(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.sessions = Path(temp.name) / 'sessions'
        self.sessions.mkdir()
        self.dirs = patch.dict(GLOBALS, {'config_dirs': lambda: [Path(temp.name)]})
        self.dirs.start()
        self.addCleanup(self.dirs.stop)

    def write(self, name: str, pid: int):
        (self.sessions / f'{pid}.json').write_text(json.dumps({'pid': pid, 'name': name}))

    def test_a_live_session_with_the_name_is_running(self):
        self.write('pln-001-p44-p50', os.getpid())
        self.assertTrue(SESSIONS['running']('pln-001-p44-p50'))
        self.assertFalse(SESSIONS['running']('outra'))

    def test_a_dead_session_is_not_running(self):
        self.write('pln-001-p44-p50', 2 ** 22 + 1)
        self.assertFalse(SESSIONS['running']('pln-001-p44-p50'))


class ChatBlockerTest(unittest.TestCase):
    def blocker(self, checkout: Path, override):
        settings = {'settings': {'agentCmdOverrides': {'claude': override} if override else {}}}
        with patch.dict(GLOBALS, {'rpc': lambda method, params=None: settings}):
            return SESSIONS['chat_blocker'](checkout)

    def test_the_workspace_reads_its_own_settings(self):
        workspace = GLOBALS['WORKSPACE']
        self.assertIsNone(self.blocker(workspace, None))
        self.assertIsNone(self.blocker(workspace / 'plans', None))

    def test_another_checkout_needs_the_wrapper(self):
        studio = Path('/x/brza-studio')
        self.assertIn('vazio', self.blocker(studio, None))
        self.assertIn('não o claude-brza', self.blocker(studio, '/usr/local/bin/claude'))
        self.assertIsNone(self.blocker(studio, '/Users/x/.local/bin/claude-brza'))


class SendTest(unittest.TestCase):
    def test_canonical_json_sorts_keys_and_keeps_accents(self):
        self.assertEqual(SESSIONS['canonicalize']({'b': [1, 'ã'], 'a': None}), '{"a":null,"b":[1,"ã"]}')

    def test_the_message_is_queued_with_the_app_fingerprint(self):
        calls = []

        def rpc(method, params=None):
            calls.append((method, params))
            return {'page': {'fence': 7}} if method == 'agentSession.history' else {}

        with patch.object(sys.modules['brza_runtime'], 'rpc', rpc):
            SESSIONS['send_to_chat']('claude_1', 'faça a tarefa')
        method, params = calls[-1]
        fields = {'body': {'kind': 'message', 'role': 'user', 'blocks': [{'type': 'text', 'text': 'faça a tarefa'}]},
                  'delivery': 'queue-if-active'}
        payload = SESSIONS['canonicalize']({'method': 'agentSession.send', 'sessionId': 'claude_1', 'fields': fields})
        self.assertEqual(method, 'agentSession.send')
        self.assertEqual({k: params[k] for k in fields}, fields)
        self.assertEqual(params['envelope']['expectedRuntimeFence'], 7)
        self.assertEqual(params['envelope']['payloadFingerprint'], hashlib.sha256(payload.encode()).hexdigest())
        self.assertRegex(params['envelope']['clientOperationId'], r'^\d{13}-[0-9a-f]{32}$')



WORKTREE = '15bf3484::/Users/x/brza-workspace'


def tab(chat: str, title: str, state: str | None = 'done') -> dict:
    return {'type': 'agent-session', 'id': f'agent-session:{chat}', 'title': title, 'sessionId': chat,
            **({'chatStatus': {'state': state}} if state else {})}


class FakeApp:
    """O runtime do app: as abas do listAll, o providerSession de cada chat e as chamadas recebidas."""

    def __init__(self, tabs: list[dict], providers: dict[str, str]):
        self.tabs, self.providers, self.calls = tabs, providers, []

    def rpc(self, method, params=None):
        self.calls.append((method, params))
        if method == 'session.tabs.listAll':
            return {'snapshots': [{'worktree': WORKTREE, 'tabs': [*self.tabs, {'type': 'terminal', 'id': 't1'}]}]}
        if method == 'agentSession.history':
            chat = params['sessionId']
            provider = {'providerSession': {'key': 'session_id', 'id': self.providers[chat]}} \
                if chat in self.providers else {}
            return {'page': {'fence': 3}, **provider}
        return {'ok': True, 'closed': True}

    def methods(self) -> list[str]:
        return [method for method, _ in self.calls]


class CommandTest(unittest.TestCase):
    """O list, o send e o close sobre um app falso e transcripts num config temporário."""

    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.config = Path(temp.name)
        self.app = FakeApp([tab('claude_a', 'PLN-002 P7: renomear chat', 'working'), tab('claude_b', 'Config'),
                            tab('claude_c', 'Sem nome', None)],
                           {'claude_a': 'uuid-a', 'claude_b': 'uuid-b', 'claude_c': 'uuid-c'})
        self.transcript('uuid-a', 'primeiro', 'pln-002-p7')
        self.transcript('uuid-b', 'pln-003')
        self.transcript('uuid-c')
        self.map = self.config / '.sessions' / 'sessions.jsonl'
        self.launches = []
        patcher = patch.dict(GLOBALS, {'config_dirs': lambda: [self.config], 'rpc': self.app.rpc,
                                       'SESSION_MAP': self.map, 'brza': self.launch})
        patcher.start()
        self.addCleanup(patcher.stop)
        sent = patch.object(sys.modules['brza_runtime'], 'rpc', self.app.rpc)
        sent.start()
        self.addCleanup(sent.stop)

    def transcript(self, claude_id: str, *names: str):
        folder = self.config / 'projects' / '-Users-x-brza-workspace'
        folder.mkdir(parents=True, exist_ok=True)
        lines = [{'type': 'user', 'message': {'content': 'custom-title no texto não conta'}}]
        lines += [{'type': 'custom-title', 'customTitle': name, 'sessionId': claude_id} for name in names]
        (folder / f'{claude_id}.jsonl').write_text(''.join(json.dumps(line) + '\n' for line in lines))

    def launch(self, *args):
        self.launches.append(args)
        return {'outcome': {'kind': 'structured', 'sessionId': 'claude_n'}}

    def write_map(self, *events: dict):
        self.map.parent.mkdir(parents=True, exist_ok=True)
        with self.map.open('a') as f:
            for event in events:
                f.write(json.dumps({'at': '2026-10-29T12:00:00-03:00', **event}) + '\n')

    def read_map(self) -> list[dict]:
        return [json.loads(line) for line in self.map.read_text().splitlines()] if self.map.exists() else []

    def calls(self, method: str) -> list[dict]:
        return [params for m, params in self.app.calls if m == method]

    def run_main(self, *argv) -> tuple[int, str]:
        out = io.StringIO()
        with patch.object(sys, 'argv', ['brza-sessions', *argv]), redirect_stdout(out):
            code = SESSIONS['main']()
        return code, out.getvalue()

    def test_list_names_each_chat_by_the_last_rename_in_its_transcript(self):
        code, out = self.run_main('list', '--json')
        self.assertEqual(code, 0)
        sessions = json.loads(out)['sessions']
        self.assertEqual([s['name'] for s in sessions], ['pln-002-p7', 'pln-003', None])
        self.assertEqual(sessions[0], {'name': 'pln-002-p7', 'title': 'PLN-002 P7: renomear chat', 'chat': 'claude_a',
                                       'claude_session': 'uuid-a', 'worktree': '/Users/x/brza-workspace',
                                       'state': 'working', 'source': 'transcript', 'opened': None})
        self.assertIsNone(sessions[2]['state'])

    def test_list_prints_one_line_per_chat_with_a_dash_for_no_name(self):
        code, out = self.run_main('list')
        self.assertEqual(code, 0)
        lines = out.splitlines()
        self.assertEqual(len(lines), 3)
        self.assertTrue(lines[1].startswith('pln-003'))
        self.assertTrue(lines[2].startswith('- '))

    def test_a_chat_without_provider_session_is_listed_without_name(self):
        del self.app.providers['claude_c']
        sessions = json.loads(self.run_main('list', '--json')[1])['sessions']
        self.assertEqual((sessions[2]['name'], sessions[2]['claude_session']), (None, None))

    def test_send_queues_the_text_to_the_chat_found_by_name(self):
        code, out = self.run_main('send', 'pln-003', 'faça o P1', '--json')
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out), {'sent': True, 'name': 'pln-003', 'chat': 'claude_b'})
        method, params = self.app.calls[-1]
        self.assertEqual(method, 'agentSession.send')
        self.assertEqual(params['envelope']['sessionId'], 'claude_b')
        self.assertEqual(params['body']['blocks'][0]['text'], 'faça o P1')

    def test_close_calls_both_rpcs_with_the_user_reason_and_never_resumes(self):
        code, out = self.run_main('close', 'pln-003', '--json')
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out), {'closed': True, 'name': 'pln-003', 'chat': 'claude_b'})
        calls = [(m, p) for m, p in self.app.calls if m in ('agentSession.close', 'session.tabs.close')]
        self.assertEqual(calls, [('agentSession.close', {'sessionId': 'claude_b'}),
                                 ('session.tabs.close', {'worktree': 'path:/Users/x/brza-workspace',
                                                         'tabId': 'agent-session:claude_b', 'reason': 'user'})])
        self.assertFalse(any('resume' in m.lower() or 'restart' in m.lower() for m in self.app.methods()))

    def test_close_refuses_a_working_chat_without_force(self):
        code, out = self.run_main('close', 'pln-002-p7', '--json')
        self.assertEqual(code, 1)
        self.assertEqual(json.loads(out), {'closed': False, 'name': 'pln-002-p7', 'chat': 'claude_a',
                                           'state': 'working'})
        self.assertNotIn('agentSession.close', self.app.methods())
        code, _ = self.run_main('close', 'pln-002-p7', '--force')
        self.assertEqual(code, 0)
        self.assertIn('session.tabs.close', self.app.methods())

    def test_an_absent_name_exits_one_with_the_error(self):
        for argv in (('send', 'nenhuma', 'x'), ('close', 'nenhuma')):
            code, out = self.run_main(*argv, '--json')
            self.assertEqual(code, 1)
            self.assertIn('nenhuma', json.loads(out)['error'])
        self.assertNotIn('agentSession.send', self.app.methods())

    def test_a_repeated_name_exits_one_and_lists_the_chats(self):
        self.transcript('uuid-c', 'pln-003')
        code, out = self.run_main('close', 'pln-003')
        self.assertEqual(code, 1)
        self.assertIn('claude_b', out)
        self.assertIn('claude_c', out)
        self.assertNotIn('agentSession.close', self.app.methods())

    def test_an_app_that_does_not_answer_exits_one(self):
        def down(method, params=None):
            raise GLOBALS['BrzaError']('session.tabs.listAll: o runtime não respondeu')

        with patch.dict(GLOBALS, {'rpc': down}):
            code, out = self.run_main('list', '--json')
        self.assertEqual(code, 1)
        self.assertIn('não respondeu', json.loads(out)['error'])

    def test_open_stays_the_default_and_takes_the_explicit_verb(self):
        for argv in (('c3-prova', '--dry-run'), ('open', 'c3-prova', '--dry-run')):
            code, out = self.run_main(*argv)
            self.assertEqual(code, 0)
            self.assertIn('abriria:  c3-prova', out)

    # O --checkout relativo parte da raiz do workspace, e não do cwd (pedido da sessão workspace, 2026-10-31).

    def workspace_clone(self) -> Path:
        """Um workspace de outro nome (`acme-notes`), com o manifesto, num repositório temporário, com o worktree
        `.worktrees/workspace`."""
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.temp = Path(temp.name).resolve()
        root = self.temp / 'acme-notes'
        (root / '.brza').mkdir(parents=True)
        (root / '.brza' / 'workspace.toml').write_text('schema = 1\n[workspace]\nname = "acme-notes"\n')
        env = {**os.environ, 'GIT_AUTHOR_NAME': 't', 'GIT_AUTHOR_EMAIL': 't@t', 'GIT_COMMITTER_NAME': 't',
               'GIT_COMMITTER_EMAIL': 't@t'}
        for args in (('init', '-q', '-b', 'master'), ('add', '.'), ('commit', '-qm', 'norma'),
                     ('worktree', 'add', '-q', '-b', 'workstream/workspace', '.worktrees/workspace')):
            subprocess.run(['git', '-C', str(root), *args], check=True, capture_output=True, env=env)
        return root

    def run_in(self, cwd: Path, *argv) -> tuple[int, str]:
        here = Path.cwd()
        os.chdir(cwd)
        try:
            with patch.dict(GLOBALS, {'chat_blocker': lambda checkout: None}):
                return self.run_main(*argv)
        finally:
            os.chdir(here)

    def test_a_relative_checkout_from_a_code_worktree_outside_its_directory_starts_at_the_workspace_root(self):
        root = self.workspace_clone()
        code = self.temp / 'acme-app'
        code.mkdir()
        env = {**os.environ, 'GIT_AUTHOR_NAME': 't', 'GIT_AUTHOR_EMAIL': 't@t', 'GIT_COMMITTER_NAME': 't',
               'GIT_COMMITTER_EMAIL': 't@t'}
        for args in (('init', '-q', '-b', 'main'), ('commit', '-q', '--allow-empty', '-m', 'x'),
                     ('worktree', 'add', '-q', '-b', 'feat/pln-001-x', str(self.temp / 'elsewhere' / 'feat'))):
            subprocess.run(['git', '-C', str(code), *args], check=True, capture_output=True, env=env)
        registry = self.temp / 'registry.toml'
        registry.write_text(f'[[workspace]]\nroot = "{root}"\ncode = "{code}"\n')
        with patch.dict(os.environ, {'BRZA_REGISTRY': str(registry)}):
            code_exit, out = self.run_in(self.temp / 'elsewhere' / 'feat', 'tools-kit-shot', '--checkout',
                                         '.worktrees/tools-kit')
        self.assertEqual(code_exit, 0, out)
        self.assertEqual(self.read_map()[0]['checkout'], str(root / '.worktrees' / 'tools-kit'))

    def test_a_relative_checkout_from_a_worktree_starts_at_the_workspace_root(self):
        root = self.workspace_clone()
        code, out = self.run_in(root / '.worktrees' / 'workspace', 'tools-kit-shot', '--checkout',
                                '.worktrees/tools-kit')
        self.assertEqual(code, 0)
        (launch,) = self.launches
        self.assertIn(f'path:{root / ".worktrees" / "tools-kit"}', launch)
        self.assertEqual(self.read_map()[0]['checkout'], str(root / '.worktrees' / 'tools-kit'))

    def test_a_relative_checkout_outside_the_workspace_starts_at_the_workspace_variable(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        code, out = self.run_in(Path(temp.name), 'fora', '--checkout', '.worktrees/tools-kit', '--dry-run')
        self.assertEqual(code, 0)
        self.assertIn(f'(chat, {(GLOBALS["WORKSPACE"] / ".worktrees" / "tools-kit").resolve()})', out)

    def test_an_absolute_checkout_stays_as_given(self):
        root = self.workspace_clone()
        target = Path(tempfile.gettempdir()).resolve() / 'outro-checkout'
        code, out = self.run_in(root / '.worktrees' / 'workspace', 'abs', '--checkout', str(target), '--dry-run')
        self.assertEqual(code, 0)
        self.assertIn(f'(chat, {target})', out)

    # O pedido 8 (PLN-002/P9): o /rename opcional, o título da aba e o mapa de sessões.

    def test_open_without_rename_titles_the_tab_and_records_the_map(self):
        code, out = self.run_main('open', 'pln-009', '--title', 'PLN-002 P9: prova')
        self.assertEqual(code, 0)
        self.assertIn('open:     pln-009', out)
        (launch,) = self.launches
        self.assertNotIn('--prompt', launch)
        self.assertEqual(self.calls('agentSession.rename'), [{'sessionId': 'claude_n', 'name': 'PLN-002 P9: prova'}])
        (event,) = self.read_map()
        self.assertEqual({k: v for k, v in event.items() if k != 'at'},
                         {'event': 'open', 'name': 'pln-009', 'title': 'PLN-002 P9: prova', 'chat': 'claude_n',
                          'checkout': str(GLOBALS['WORKSPACE']), 'renamed': False})
        self.assertRegex(event['at'], r'^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}[+-]\d{2}:\d{2}$')

    def test_open_without_title_titles_the_tab_with_the_name(self):
        self.run_main('pln-009')
        self.assertEqual(self.calls('agentSession.rename'), [{'sessionId': 'claude_n', 'name': 'pln-009'}])
        self.assertEqual(self.read_map()[0]['title'], 'pln-009')

    def test_open_without_rename_launches_with_the_task_prompt(self):
        self.run_main('pln-009', '--prompt', 'faça o P9')
        (launch,) = self.launches
        self.assertEqual(launch[launch.index('--prompt') + 1], 'faça o P9')
        self.assertEqual(self.calls('agentSession.send'), [])

    def test_open_with_rename_sends_the_rename_first_and_the_prompt_after(self):
        code, _ = self.run_main('pln-009', '--rename', '--prompt', 'faça o P9')
        self.assertEqual(code, 0)
        (launch,) = self.launches
        self.assertEqual(launch[launch.index('--prompt') + 1], '/rename pln-009')
        (sent,) = self.calls('agentSession.send')
        self.assertEqual(sent['body']['blocks'][0]['text'], 'faça o P9')
        self.assertTrue(self.read_map()[0]['renamed'])

    def test_wait_records_the_claude_session(self):
        self.app.providers['claude_n'] = 'uuid-n'
        code, _ = self.run_main('pln-009', '--wait')
        self.assertEqual(code, 0)
        self.assertEqual([(e['event'], e.get('claude_session')) for e in self.read_map()],
                         [('open', None), ('session', 'uuid-n')])

    def test_wait_with_rename_also_waits_for_the_name(self):
        self.app.providers['claude_n'] = 'uuid-n'
        waited = []
        with patch.dict(GLOBALS, {'wait_for_name': lambda name: waited.append(name) or True}):
            code, _ = self.run_main('pln-009', '--rename', '--wait')
        self.assertEqual((code, waited), (0, ['pln-009']))

    def test_wait_without_the_claude_session_in_time_exits_one(self):
        with patch.dict(GLOBALS, {'WAIT_SECONDS': 0}):
            code, out = self.run_main('pln-009', '--wait')
        self.assertEqual(code, 1)
        self.assertIn('CHECK:', out)
        self.assertEqual([e['event'] for e in self.read_map()], ['open'])

    def test_open_skips_a_name_the_map_holds_with_the_chat_still_open(self):
        self.write_map({'event': 'open', 'name': 'pln-coord', 'title': 'PLN-001: Coordenador', 'chat': 'claude_c',
                        'checkout': '/x', 'renamed': False},
                       {'event': 'open', 'name': 'pln-velho', 'title': 'x', 'chat': 'claude_fechado',
                        'checkout': '/x', 'renamed': False})
        code, out = self.run_main('pln-coord')
        self.assertEqual((code, out.strip()), (0, 'running:  pln-coord'))
        self.assertEqual(self.launches, [])
        code, out = self.run_main('pln-velho', '--dry-run')
        self.assertIn('abriria:  pln-velho', out)

        def down(method, params=None):
            raise GLOBALS['BrzaError']('session.tabs.listAll: o runtime não respondeu')

        with patch.dict(GLOBALS, {'rpc': down}):
            code, out = self.run_main('pln-coord', '--dry-run')
        self.assertIn('abriria:  pln-coord', out)

    def test_list_takes_the_name_from_the_map_before_the_transcript(self):
        self.write_map({'event': 'open', 'name': 'pln-coord', 'title': 'PLN-001: Coordenador', 'chat': 'claude_c',
                        'checkout': '/x', 'renamed': False},
                       {'event': 'open', 'name': 'outro', 'title': 'Config', 'chat': 'claude_b', 'checkout': '/x',
                        'renamed': True})
        sessions = json.loads(self.run_main('list', '--json')[1])['sessions']
        self.assertEqual([(s['name'], s['source'], s['opened']) for s in sessions],
                         [('pln-002-p7', 'transcript', None), ('outro', 'map', '2026-10-29T12:00:00-03:00'),
                          ('pln-coord', 'map', '2026-10-29T12:00:00-03:00')])

    def test_send_title_and_close_find_the_chat_by_the_map(self):
        self.write_map({'event': 'open', 'name': 'pln-coord', 'title': 'Sem nome', 'chat': 'claude_c',
                        'checkout': '/x', 'renamed': False})
        self.assertEqual(self.run_main('send', 'pln-coord', 'oi')[0], 0)
        self.assertEqual(self.calls('agentSession.send')[-1]['envelope']['sessionId'], 'claude_c')
        code, out = self.run_main('title', 'pln-coord', 'PLN-001: Coordenador', '--json')
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out), {'titled': True, 'name': 'pln-coord', 'chat': 'claude_c',
                                           'title': 'PLN-001: Coordenador'})
        self.assertEqual(self.calls('agentSession.rename'),
                         [{'sessionId': 'claude_c', 'name': 'PLN-001: Coordenador'}])
        self.assertEqual(self.run_main('close', 'pln-coord')[0], 0)
        self.assertEqual([(e['event'], e.get('title')) for e in self.read_map()],
                         [('open', 'Sem nome'), ('title', 'PLN-001: Coordenador'), ('close', None)])

    def test_the_map_folds_a_name_reopened_after_a_close(self):
        self.write_map({'event': 'open', 'name': 'x', 'title': 'x', 'chat': 'claude_velho', 'checkout': '/x',
                        'renamed': False},
                       {'event': 'close', 'name': 'x', 'chat': 'claude_velho'},
                       {'event': 'open', 'name': 'x', 'title': 'x', 'chat': 'claude_b', 'checkout': '/x',
                        'renamed': False},
                       {'event': 'title', 'name': 'x', 'chat': 'claude_b', 'title': 'X novo'},
                       {'event': 'session', 'name': 'x', 'chat': 'claude_b', 'claude_session': 'uuid-b'})
        folded = SESSIONS['session_map']()
        self.assertEqual(list(folded), ['claude_b'])
        self.assertEqual((folded['claude_b']['title'], folded['claude_b']['claude_session']), ('X novo', 'uuid-b'))

    def test_a_broken_map_line_is_skipped(self):
        self.map.parent.mkdir(parents=True)
        self.map.write_text('{quebrada\n' + json.dumps({'event': 'open', 'at': 'a', 'name': 'pln-coord', 'title': 't',
                                                         'chat': 'claude_c', 'checkout': '/x', 'renamed': False}) + '\n')
        self.assertEqual(list(SESSIONS['session_map']()), ['claude_c'])


if __name__ == '__main__':
    unittest.main()
