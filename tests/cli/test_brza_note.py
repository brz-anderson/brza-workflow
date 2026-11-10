"""Testes do brza-note, num brza-workspace de mentira: um clone de uma origin local, com um checador falso, e o
workstream studio num worktree dele, com o plano PLN-001, onde as notas vão.

    python3 -m unittest discover -s tests/cli
"""
import contextlib
import datetime
import io
import json
import os
import runpy
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent.parent
BIN = ROOT / 'bin'
CLIENT = ROOT / 'packages' / 'client' / 'bin'
os.environ['BRZA_REGISTRY'] = '/nonexistent/brza/workspaces.toml'  # nada aqui lê o registro real da máquina
sys.path.insert(0, str(BIN))
import brza_runtime as runtime  # noqa: E402

NOTE = runpy.run_path(str(BIN / 'brza-note'))
# As funções leem os globais do módulo em que nasceram, não da cópia que o run_path devolve.
GLOBALS = NOTE['main'].__globals__
TODAY = datetime.date(2026, 10, 28)


def git(*args, cwd: Path):
    return subprocess.run(['git', '-C', str(cwd), *args], check=True, capture_output=True, text=True).stdout.strip()


class NoteFixture(unittest.TestCase):
    """O workspace de mentira e os ajudantes; sem teste próprio, para o test_brza_note_index.py herdar sem repetir."""

    # A branch da norma e o remote do workspace; com outros valores, o fixture grava o manifesto (A5 do P9)
    BRANCH, REMOTE = 'master', 'origin'
    OWNER = None  # o `[workspace].owner` do manifesto; None, sem manifesto

    def write_manifest(self):
        """O `.brza/workspace.toml` do workspace de mentira, com o owner, a branch e o remote desta classe."""
        manifest = {'schema': 1, 'workspace': {'name': 'ws', 'title': 'Casa', 'owner': self.OWNER or 'Beatriz',
                                               'branch': self.BRANCH, 'remote': self.REMOTE, 'born': '2026-11-01'},
                    'author': {'name': 't', 'email': 't@t', 'login': 't'},
                    'code': {'name': 'casa-app', 'path': str(self.studio), 'github': 't/casa-app'}}
        (self.ws / '.brza').mkdir(exist_ok=True)
        (self.ws / '.brza' / 'workspace.toml').write_text(runtime.brza_root.dump_manifest(
            runtime.brza_root.with_defaults(manifest)))

    # O ambiente da sessão que roda a suíte, que o `brza note commit` leria e gravaria no log do workspace real (X9)
    SESSION_ENV = ('BRZA_WORKSPACE', 'BRZA_STUDIO', 'CLAUDE_CODE_SESSION_ID', 'CLAUDE_CODE_BRIDGE_SESSION_ID',
                   'ORCA_AGENT_SESSION_ID', 'CLAUDE_SESSION_NAME', 'GROK_SESSION_ID', 'MEASURE_DIR',
                   'CLAUDE_PID')

    def setUp(self):
        environment = patch.dict(os.environ)
        environment.start()
        self.addCleanup(environment.stop)
        for key in self.SESSION_ENV:
            os.environ.pop(key, None)
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.base = Path(temp.name).resolve()
        self.origin, self.other, self.ws = self.base / 'origin.git', self.base / 'other', self.base / 'ws'
        self.studio = self.base / 'studio'
        subprocess.run(['git', 'init', '-q', '--bare', '-b', self.BRANCH, str(self.origin)], check=True)
        subprocess.run(['git', 'clone', '-q', '-o', self.REMOTE, str(self.origin), str(self.ws)], check=True,
                       capture_output=True)
        git('checkout', '-q', '-B', self.BRANCH, cwd=self.ws)
        git('config', 'user.name', 't', cwd=self.ws)
        git('config', 'user.email', 't@t', cwd=self.ws)
        (self.ws / 'work' / 'worklogs').mkdir(parents=True)
        (self.ws / 'work' / 'issues').mkdir(parents=True)
        (self.ws / 'README.md').write_text('um\n')
        (self.ws / '.gitignore').write_text('.worktrees/\n.claude/settings.local.json\n')
        self.checker_exit(0)
        if (self.BRANCH, self.REMOTE, self.OWNER) != ('master', 'origin', None):
            self.write_manifest()
        git('add', '.', cwd=self.ws)
        git('commit', '-q', '-m', 'um', cwd=self.ws)
        git('push', '-q', self.REMOTE, f'HEAD:{self.BRANCH}', cwd=self.ws)
        git('branch', '-q', f'--set-upstream-to={self.REMOTE}/{self.BRANCH}', cwd=self.ws)
        for p in (patch.object(runtime, 'WORKSPACE', self.ws), patch.object(runtime, 'STUDIO', self.studio),
                  patch.dict(GLOBALS, {'today': lambda: TODAY})):
            p.start()
            self.addCleanup(p.stop)
        self.tree = self.make_workstream('studio')
        self.add_plan(self.tree, 'PLN-001-x')

    def make_workstream(self, name: str) -> Path:
        """Abre o workstream pelo próprio brza-note e devolve o worktree dele."""
        code, out = self.run_note('workstream', name)
        self.assertEqual(code, 0, out)
        return self.ws / '.worktrees' / name

    def add_plan(self, tree: Path, folder: str):
        """Commita e empurra plans/<folder>/ no workstream do worktree `tree`: o workstream é que tem o plano."""
        plan = tree / 'plans' / folder / f'{"-".join(folder.split("-")[:2])}-plan.md'
        plan.parent.mkdir(parents=True, exist_ok=True)
        plan.write_text('# plano\n')
        git('add', '.', cwd=tree)
        git('commit', '-q', '-m', f'docs: {folder}', cwd=tree)
        git('push', '-q', cwd=tree)

    def checker_exit(self, code: int, root: Path | None = None):
        """Troca o check_notes.py da raiz (o workspace de mentira, ou o worktree do workstream) por um que imprime os
        argumentos e sai com `code`."""
        checker = (root or self.ws) / '.claude' / 'skills' / 'notes-checker' / 'scripts' / 'check_notes.py'
        checker.parent.mkdir(parents=True, exist_ok=True)
        checker.write_text(f'import sys\nprint("checador: saída {code}", *sys.argv[1:])\nsys.exit({code})\n')

    def run_note(self, *argv, env: dict | None = None, cwd: Path | None = None,
                 workstream: str | None = 'studio') -> tuple[int, str]:
        """Roda o main; sem `cwd`, de fora do workspace de mentira, onde o caminho relativo parte da raiz-alvo. Em new,
        commit e plan-open, o --workstream vai junto, a menos que `workstream` seja None."""
        if workstream and argv[0] in ('new', 'commit', 'plan-open', 'review-open', 'migration-open'):
            argv = (*argv, '--workstream', workstream)
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out), \
                patch.dict('os.environ', env or {}), contextlib.chdir(cwd or self.base):
            try:
                code = GLOBALS['main'](list(argv))
            except SystemExit as e:
                code = e.code
        return code, out.getvalue()

    def origin_log(self, branch: str = 'workstream/studio') -> list[str]:
        return git('log', '--format=%s', branch, cwd=self.origin).splitlines()

    def assert_master_got_only_the_index_line(self):
        """A nota vive no workstream; na master só chega a linha do índice (.brza/index.jsonl), num commit à parte."""
        self.assertEqual(self.origin_log('master'), ['índice: issue:a created', 'um'])
        self.assertEqual(git('show', '--name-only', '--format=', 'master', cwd=self.origin).splitlines(),
                         ['.brza/index.jsonl'])

    def committed_files(self, ref: str = 'HEAD') -> list[str]:
        return git('show', '--name-only', '--format=', ref, cwd=self.tree).splitlines()


class NoteTest(NoteFixture):
    # new

    def test_new_worklog_writes_the_skeleton_and_prints_the_path(self):
        code, out = self.run_note('new', 'worklog', 'o-brza-note', env={'CLAUDE_SESSION_NAME': 'pln-002-p6'})
        self.assertEqual(code, 0, out)
        path = self.tree / 'work' / 'worklogs' / '2026-10-28-o-brza-note.md'
        self.assertEqual(out.strip().splitlines()[-1], str(path))
        text = path.read_text()
        self.assertTrue(text.startswith('---\nscope:\n  - workspace\n---\n\n# '))
        self.assertIn('O pedido, em 2026-10-28, na sessão `pln-002-p6`: "', text)  # sem manifesto, sem o dono
        self.assertNotIn('Anderson', text)
        for section in ('## O que se fez', '## O que ficou', '## Fora do workspace'):
            self.assertIn(f'\n{section}\n', text)

    def test_new_worklog_without_session_name_leaves_it_to_write(self):
        with patch.dict('os.environ'):
            os.environ.pop('CLAUDE_SESSION_NAME', None)
            os.environ.pop('ORCA_AGENT_SESSION_ID', None)
            code, out = self.run_note('new', 'worklog', 'sem-nome')
        self.assertEqual(code, 0, out)
        text = (self.tree / 'work' / 'worklogs' / '2026-10-28-sem-nome.md').read_text()
        self.assertIn('na sessão `<a escrever>`', text)

    def fake_sessions(self, mapped: dict, provider: str | None, renamed: str | None):
        asked = []

        def provider_session(chat):
            asked.append(chat)
            return provider

        return asked, {'session_map': lambda: mapped, 'provider_session': provider_session,
                       'chat_name': lambda claude_session: renamed}

    def new_worklog_in_chat(self, mapped: dict, provider: str | None, renamed: str | None) -> tuple[str, list]:
        asked, sessions = self.fake_sessions(mapped, provider, renamed)
        with patch.dict('os.environ', {'ORCA_AGENT_SESSION_ID': 'claude_x'}), \
                patch.dict(GLOBALS, {'sessions_module': lambda: sessions}):
            os.environ.pop('CLAUDE_SESSION_NAME', None)
            code, out = self.run_note('new', 'worklog', 'no-chat')
        self.assertEqual(code, 0, out)
        return (self.tree / 'work' / 'worklogs' / '2026-10-28-no-chat.md').read_text(), asked

    def test_new_worklog_in_a_chat_takes_the_name_from_the_session_map(self):
        text, asked = self.new_worklog_in_chat({'claude_x': {'name': 'pln-coord'}}, 'uuid-x', 'outro')
        self.assertIn('na sessão `pln-coord`', text)
        self.assertEqual(asked, [])

    def test_new_worklog_in_a_chat_out_of_the_map_takes_the_rename(self):
        text, asked = self.new_worklog_in_chat({}, 'uuid-x', 'pln-renomeada')
        self.assertIn('na sessão `pln-renomeada`', text)
        self.assertEqual(asked, ['claude_x'])

    def test_new_worklog_in_a_chat_without_name_leaves_it_to_write(self):
        text, _ = self.new_worklog_in_chat({}, None, None)
        self.assertIn('na sessão `<a escrever>`', text)

    def test_new_worklog_in_a_non_claude_chat_does_not_discover_transcript_name(self):
        """P9/A1: chat de outro runtime não usa o provider_session como transcript Claude."""
        for agent in ('codex', 'grok', 'antigravity'):
            with self.subTest(agent=agent):
                chat = f'{agent}_x'
                asked, sessions = self.fake_sessions({}, 'uuid-b', 'nome-claude')
                with patch.dict('os.environ', {'ORCA_AGENT_SESSION_ID': chat}), \
                        patch.dict(GLOBALS, {'sessions_module': lambda s=sessions: s}):
                    os.environ.pop('CLAUDE_SESSION_NAME', None)
                    code, out = self.run_note('new', 'worklog', f'nao-claude-{agent}')
                self.assertEqual(code, 0, out)
                text = (self.tree / 'work' / 'worklogs' / f'2026-10-28-nao-claude-{agent}.md').read_text()
                self.assertIn('na sessão `<a escrever>`', text)
                self.assertNotIn('nome-claude', text)
                self.assertEqual(asked, [])

        asked, sessions = self.fake_sessions({'chat_codex': {'agent': 'codex'}}, 'uuid-b', 'nome-claude')
        with patch.dict('os.environ', {'ORCA_AGENT_SESSION_ID': 'chat_codex'}), \
                patch.dict(GLOBALS, {'sessions_module': lambda: sessions}):
            os.environ.pop('CLAUDE_SESSION_NAME', None)
            self.assertIsNone(GLOBALS['session_name']())
        self.assertEqual(asked, [])

    def test_empty_session_name_falls_back_to_available_source(self):
        """D10: nome vazio em CLAUDE_SESSION_NAME ou no session_map faz fallback para a fonte seguinte; sem fonte, None."""
        asked, sessions = self.fake_sessions({'claude_x': {'name': '   '}}, 'uuid-x', 'pln-fallback')
        with patch.dict('os.environ', {'CLAUDE_SESSION_NAME': '  ', 'ORCA_AGENT_SESSION_ID': 'claude_x'}), \
                patch.dict(GLOBALS, {'sessions_module': lambda: sessions}):
            self.assertEqual(GLOBALS['session_name'](), 'pln-fallback')
        self.assertEqual(asked, ['claude_x'])
        _, sessions_empty = self.fake_sessions({'claude_x': {'name': ''}}, 'uuid-x', '   ')
        with patch.dict('os.environ', {'CLAUDE_SESSION_NAME': '', 'ORCA_AGENT_SESSION_ID': 'claude_x'}), \
                patch.dict(GLOBALS, {'sessions_module': lambda: sessions_empty}):
            self.assertIsNone(GLOBALS['session_name']())

    def test_session_name_without_the_client_package_is_none(self):
        """R10.1: o núcleo roda sem o pacote do cliente; sem o brza-sessions, o chat não dá nome."""
        with tempfile.TemporaryDirectory() as tmp:
            bin_dir = Path(tmp) / 'bin'
            bin_dir.mkdir()
            with patch.dict('os.environ', {'ORCA_AGENT_SESSION_ID': 'claude_x'}), patch.dict(GLOBALS, {'HERE': bin_dir}):
                os.environ.pop('CLAUDE_SESSION_NAME', None)
                self.assertIsNone(GLOBALS['sessions_module']())
                self.assertIsNone(GLOBALS['session_name']())
                os.environ['CLAUDE_SESSION_NAME'] = 'pln-006-p6'
                self.assertEqual(GLOBALS['session_name'](), 'pln-006-p6')

    def test_new_worklog_without_the_client_package_leaves_the_name_to_write(self):
        with tempfile.TemporaryDirectory() as tmp:
            bin_dir = Path(tmp) / 'bin'
            bin_dir.mkdir()
            with patch.dict('os.environ', {'ORCA_AGENT_SESSION_ID': 'claude_x'}), patch.dict(GLOBALS, {'HERE': bin_dir}):
                os.environ.pop('CLAUDE_SESSION_NAME', None)
                code, out = self.run_note('new', 'worklog', 'sem-pacote')
        self.assertEqual(code, 0, out)
        self.assertIn('na sessão `<a escrever>`', (self.tree / 'work' / 'worklogs' / '2026-10-28-sem-pacote.md').read_text())

    def test_new_issue_has_open_status_today_scope_and_priority(self):
        code, out = self.run_note('new', 'issue', 'algo-quebrado', '--scope', 'workspace,brza-studio',
                                  '--priority', 'high')
        self.assertEqual(code, 0, out)
        text = (self.tree / 'work' / 'issues' / 'algo-quebrado.md').read_text()
        self.assertTrue(text.startswith('---\nstatus: open\ndescription: <uma frase>\nscope:\n  - workspace\n'
                                        '  - brza-studio\nopened: 2026-10-28\npriority: high\n---\n\n# '), text)
        self.assertIn('\n## O pedido\n', text)

    def real_checker(self, root: Path) -> subprocess.CompletedProcess:
        """O checador de verdade do núcleo, na raiz `root` (o do fixture é um falso que sai com o código dado)."""
        script = ROOT / 'core' / '.claude' / 'skills' / 'notes-checker' / 'scripts' / 'check_notes.py'
        return subprocess.run([sys.executable, str(script), '--root', str(root)], capture_output=True, text=True,
                              env={**os.environ, 'PYTHONDONTWRITEBYTECODE': '1'})

    def test_the_new_issue_skeleton_passes_the_real_checker_once_the_h1_is_written(self):
        # o checador cobra `description` das notas criadas depois de 2026-11-01 (A6 do P9): o esqueleto o traz
        code, out = self.run_note('new', 'issue', 'nova-issue', env={'CLAUDE_SESSION_NAME': 's'})
        self.assertEqual(code, 0, out)
        path = self.tree / 'work' / 'issues' / 'nova-issue.md'
        text = path.read_text().replace('opened: 2026-10-28', 'opened: 2026-11-02')
        path.write_text(text.replace('# <H1 a escrever>', '# Uma issue nova'))
        done = self.real_checker(self.tree)
        self.assertNotIn('description-missing', done.stdout + done.stderr)
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)

    def test_new_issue_without_priority_omits_the_field(self):
        code, out = self.run_note('new', 'issue', 'sem-prioridade')
        self.assertEqual(code, 0, out)
        self.assertNotIn('priority', (self.tree / 'work' / 'issues' / 'sem-prioridade.md').read_text())

    def test_new_refuses_an_existing_note_and_keeps_it(self):
        path = self.tree / 'work' / 'issues' / 'ja-existe.md'
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('meu texto\n')
        code, out = self.run_note('new', 'issue', 'ja-existe')
        self.assertEqual(code, 1, out)
        self.assertIn('já existe', out)
        self.assertEqual(path.read_text(), 'meu texto\n')

    def test_new_refuses_a_subject_out_of_kebab_case(self):
        code, _ = self.run_note('new', 'worklog', 'Com Espaço')
        self.assertEqual(code, 2)
        code, _ = self.run_note('new', 'issue', '2026-10-28-com-data')
        self.assertEqual(code, 2)

    # commit

    def write(self, rel: str, text: str = 'texto\n') -> Path:
        path = self.tree / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
        return path

    def test_commit_stops_on_checker_error_and_commits_nothing(self):
        self.write('work/issues/a.md')
        self.checker_exit(1, self.tree)
        head = git('rev-parse', 'HEAD', cwd=self.tree)
        code, out = self.run_note('commit', 'work/issues/a.md', '-m', 'docs: a')
        self.assertEqual(code, 1, out)
        self.assertIn('checador: saída 1', out)
        self.assertEqual(git('rev-parse', 'HEAD', cwd=self.tree), head)
        self.assertEqual(git('diff', '--cached', '--name-only', cwd=self.tree), '')

    def test_commit_takes_only_the_given_paths_even_with_something_else_staged(self):
        self.write('work/issues/a.md')
        self.write('README.md', 'sujo ao lado\n')
        self.write('work/issues/de-outra-sessao.md')
        git('add', 'work/issues/de-outra-sessao.md', cwd=self.tree)
        code, out = self.run_note('commit', 'work/issues/a.md', '-m', 'docs: a', '--no-push')
        self.assertEqual(code, 0, out)
        self.assertEqual(self.committed_files(), ['work/issues/a.md'])
        self.assertEqual(git('log', '-1', '--format=%s', cwd=self.tree), 'docs: a')
        self.assertEqual(git('diff', '--name-only', cwd=self.tree), 'README.md')
        self.assertEqual(git('diff', '--cached', '--name-only', cwd=self.tree), 'work/issues/de-outra-sessao.md')

    def test_commit_path_is_relative_to_the_current_directory_inside_the_workspace(self):
        self.write('work/issues/a.md')
        code, out = self.run_note('commit', 'a.md', '-m', 'docs: a', '--no-push', cwd=self.tree / 'work' / 'issues')
        self.assertEqual(code, 0, out)
        self.assertEqual(self.committed_files(), ['work/issues/a.md'])

    def test_commit_path_is_relative_to_the_target_root_from_outside(self):
        self.write('work/issues/a.md')
        code, out = self.run_note('commit', 'work/issues/a.md', '-m', 'docs: a', '--no-push', cwd=self.base)
        self.assertEqual(code, 0, out)
        self.assertEqual(self.committed_files(), ['work/issues/a.md'])

    def test_commit_refuses_a_path_outside_the_target_root(self):
        outside = self.base / 'fora.md'
        outside.write_text('x\n')
        code, out = self.run_note('commit', str(outside), '-m', 'docs: fora')
        self.assertEqual(code, 2, out)
        code, out = self.run_note('commit', '../fora.md', '-m', 'docs: fora', cwd=self.tree)
        self.assertEqual(code, 2, out)

    def test_commit_pulls_with_rebase_over_a_push_from_elsewhere_and_pushes(self):
        subprocess.run(['git', 'clone', '-q', '-b', 'workstream/studio', str(self.origin), str(self.other)], check=True,
                       capture_output=True)
        (self.other / 'work').mkdir(exist_ok=True)
        (self.other / 'work' / 'outra.md').write_text('de outra sessão\n')
        git('add', '.', cwd=self.other)
        git('-c', 'user.name=t', '-c', 'user.email=t@t', 'commit', '-q', '-m', 'docs: outra', cwd=self.other)
        git('push', '-q', 'origin', 'HEAD:workstream/studio', cwd=self.other)
        self.write('work/issues/a.md')
        self.write('README.md', 'sujo ao lado\n')
        code, out = self.run_note('commit', 'work/issues/a.md', '-m', 'docs: a')
        self.assertEqual(code, 0, out)
        self.assertEqual(self.origin_log()[:2], ['docs: a', 'docs: outra'])
        self.assert_master_got_only_the_index_line()
        self.assertEqual((self.tree / 'README.md').read_text(), 'sujo ao lado\n')

    def test_commit_with_nothing_new_on_the_origin_does_not_pull(self):
        self.write('work/issues/a.md')
        self.write('README.md', 'sujo de outra sessão\n')
        calls, real = [], runtime.git

        def spy(*args, **kwargs):
            calls.append(args)
            return real(*args, **kwargs)

        with patch.object(runtime, 'git', spy):
            code, out = self.run_note('commit', 'work/issues/a.md', '-m', 'docs: a')
        self.assertEqual(code, 0, out)
        self.assertNotIn('pull', [args[0] for args in calls])
        self.assertIn('fetch', [args[0] for args in calls])
        self.assertEqual(self.origin_log()[:1], ['docs: a'])
        self.assertEqual((self.tree / 'README.md').read_text(), 'sujo de outra sessão\n')

    def test_commit_no_push_stops_at_the_commit(self):
        self.write('work/issues/a.md')
        code, out = self.run_note('commit', 'work/issues/a.md', '-m', 'docs: a', '--no-push')
        self.assertEqual(code, 0, out)
        self.assertEqual(self.origin_log(), ['docs: PLN-001-x', 'um'])
        self.assertEqual(git('log', '-1', '--format=%s', cwd=self.tree), 'docs: a')

    def test_commit_with_nothing_changed_in_the_paths_fails(self):
        code, out = self.run_note('commit', 'README.md', '-m', 'docs: nada', '--no-push')
        self.assertEqual(code, 1, out)
        self.assertEqual(git('log', '-1', '--format=%s', cwd=self.tree), 'docs: PLN-001-x')

    def test_commit_pushes_to_the_workstream_branch_and_leaves_the_master_alone(self):
        self.write('work/issues/a.md')
        code, out = self.run_note('commit', 'work/issues/a.md', '-m', 'docs: a')
        self.assertEqual(code, 0, out)
        self.assertIn('(workstream/studio)', out)
        self.assertEqual(self.origin_log()[:2], ['docs: a', 'docs: PLN-001-x'])
        self.assert_master_got_only_the_index_line()

    def test_commit_without_upstream_pushes_with_dash_u(self):
        git('branch', '--unset-upstream', cwd=self.tree)
        self.write('work/issues/a.md')
        code, out = self.run_note('commit', 'work/issues/a.md', '-m', 'docs: a')
        self.assertEqual(code, 0, out)
        self.assertEqual(git('rev-parse', '--abbrev-ref', '@{upstream}', cwd=self.tree), 'origin/workstream/studio')
        self.assertEqual(self.origin_log()[:1], ['docs: a'])

    def test_commit_runs_the_checker_with_the_target_root(self):
        self.write('work/issues/a.md')
        code, out = self.run_note('commit', 'work/issues/a.md', '-m', 'docs: a', '--no-push')
        self.assertEqual(code, 0, out)
        self.assertIn(f'checador: saída 0 --root {self.tree}', out)

    # a raiz-alvo

    def studio_repo(self, branch: str) -> Path:
        """O checkout do brza-studio de mentira, na `branch`, com um commit."""
        subprocess.run(['git', 'init', '-q', '-b', branch, str(self.studio)], check=True)
        git('-c', 'user.name=t', '-c', 'user.email=t@t', 'commit', '-q', '--allow-empty', '-m', 'studio',
            cwd=self.studio)
        return self.studio

    def new_issue_path(self, subject: str, root: Path) -> Path:
        return root / 'work' / 'issues' / f'{subject}.md'

    def test_new_in_a_workstream_worktree_goes_to_that_worktree(self):
        code, out = self.run_note('new', 'issue', 'no-cwd', cwd=self.tree, workstream=None)
        self.assertEqual(code, 0, out)
        self.assertEqual(out.strip().splitlines()[-1], str(self.new_issue_path('no-cwd', self.tree)))

    def test_new_from_a_studio_branch_takes_the_workstream_from_the_branch_name(self):
        self.studio_repo('feat/pln-001-o-slug')
        code, out = self.run_note('new', 'issue', 'do-studio', cwd=self.studio, workstream=None)
        self.assertEqual(code, 0, out)
        self.assertTrue(self.new_issue_path('do-studio', self.tree).exists())

    def test_new_from_a_studio_worktree_takes_the_workstream_from_the_branch_name(self):
        self.studio_repo('master')
        wt = self.base / 'studio-wt'
        git('worktree', 'add', '-q', '-b', 'fix/pln-001-no-worktree', str(wt), cwd=self.studio)
        code, out = self.run_note('new', 'issue', 'do-worktree', cwd=wt, workstream=None)
        self.assertEqual(code, 0, out)
        self.assertTrue(self.new_issue_path('do-worktree', self.tree).exists())

    def test_new_from_a_studio_branch_without_the_plan_number_refuses_and_asks_for_workstream(self):
        self.studio_repo('feat/sem-plano')
        code, out = self.run_note('new', 'issue', 'x', cwd=self.studio, workstream=None)
        self.assertEqual(code, 1, out)
        self.assertIn('--workstream', out)
        self.assertFalse(self.new_issue_path('x', self.tree).exists())

    def test_new_from_a_studio_branch_whose_plan_is_in_no_workstream_refuses_and_asks_for_workstream(self):
        self.studio_repo('feat/pln-007-sem-plano-na-frente')
        code, out = self.run_note('new', 'issue', 'x', cwd=self.studio, workstream=None)
        self.assertEqual(code, 1, out)
        self.assertIn('PLN-007 não está em nenhum workstream', out)
        self.assertIn('--workstream', out)

    def test_new_from_a_studio_branch_with_the_plan_in_two_workstreams_refuses_and_lists_them(self):
        other = self.make_workstream('tools-kit')
        self.add_plan(other, 'PLN-001-y')
        self.studio_repo('feat/pln-001-slug')
        code, out = self.run_note('new', 'issue', 'x', cwd=self.studio, workstream=None)
        self.assertEqual(code, 1, out)
        self.assertIn('mais de um workstream (studio, tools-kit)', out)
        self.assertFalse(self.new_issue_path('x', self.tree).exists())
        self.assertFalse(self.new_issue_path('x', other).exists())

    def test_the_plan_number_is_matched_whole_not_as_a_prefix(self):
        self.add_plan(self.make_workstream('tools-kit'), 'PLN-0011-z')
        self.studio_repo('feat/pln-001-slug')
        code, out = self.run_note('new', 'issue', 'inteiro', cwd=self.studio, workstream=None)
        self.assertEqual(code, 0, out)
        self.assertTrue(self.new_issue_path('inteiro', self.tree).exists())

    def drop_local_studio_workstream(self):
        """O workstream studio só na origin: sem worktree e sem branch local."""
        git('worktree', 'remove', str(self.tree), cwd=self.ws)
        git('branch', '-q', '-D', 'workstream/studio', cwd=self.ws)

    def test_new_from_a_studio_branch_whose_plan_is_only_on_the_origin_asks_to_open_the_worktree(self):
        self.drop_local_studio_workstream()
        self.studio_repo('feat/pln-001-slug')
        code, out = self.run_note('new', 'issue', 'x', cwd=self.studio, workstream=None)
        self.assertEqual(code, 1, out)
        self.assertIn('brza note workstream studio', out)
        code, out = self.run_note('workstream', 'studio')
        self.assertEqual(code, 0, out)
        code, out = self.run_note('new', 'issue', 'depois', cwd=self.studio, workstream=None)
        self.assertEqual(code, 0, out)
        self.assertTrue(self.new_issue_path('depois', self.tree).exists())

    def test_a_local_workstream_with_the_plan_beats_the_ones_only_on_the_origin(self):
        far = self.make_workstream('workspace')
        self.add_plan(far, 'PLN-001-z')
        git('worktree', 'remove', str(far), cwd=self.ws)
        git('branch', '-q', '-D', 'workstream/workspace', cwd=self.ws)
        self.studio_repo('feat/pln-001-slug')
        code, out = self.run_note('new', 'issue', 'local', cwd=self.studio, workstream=None)
        self.assertEqual(code, 0, out)
        self.assertTrue(self.new_issue_path('local', self.tree).exists())

    def test_new_from_a_studio_branch_with_the_plan_in_two_workstreams_only_on_the_origin_lists_them(self):
        other = self.make_workstream('tools-kit')
        self.add_plan(other, 'PLN-001-y')
        self.drop_local_studio_workstream()
        git('worktree', 'remove', str(other), cwd=self.ws)
        git('branch', '-q', '-D', 'workstream/tools-kit', cwd=self.ws)
        self.studio_repo('feat/pln-001-slug')
        code, out = self.run_note('new', 'issue', 'x', cwd=self.studio, workstream=None)
        self.assertEqual(code, 1, out)
        self.assertIn('mais de um workstream (studio, tools-kit)', out)

    def test_workstream_flag_beats_the_worktree_and_the_studio_of_the_cwd(self):
        other = self.make_workstream('tools-kit')
        code, out = self.run_note('new', 'issue', 'na-outra', cwd=self.tree, workstream='tools-kit')
        self.assertEqual(code, 0, out)
        self.assertTrue(self.new_issue_path('na-outra', other).exists())
        self.assertFalse(self.new_issue_path('na-outra', self.tree).exists())
        self.studio_repo('feat/pln-001-slug')
        code, out = self.run_note('new', 'issue', 'na-outra-2', cwd=self.studio, workstream='tools-kit')
        self.assertEqual(code, 0, out)
        self.assertTrue(self.new_issue_path('na-outra-2', other).exists())

    def test_the_master_refuses_new_and_commit_and_says_the_master_keeps_only_the_norm(self):
        code, out = self.run_note('new', 'issue', 'na-master', cwd=self.ws, workstream=None)
        self.assertEqual(code, 1, out)
        self.assertIn('master guarda só a norma', out)
        self.assertIn('--workstream', out)
        self.assertFalse(self.new_issue_path('na-master', self.ws).exists())
        code, out = self.run_note('commit', 'README.md', '-m', 'docs: x', cwd=self.ws, workstream=None)
        self.assertEqual(code, 1, out)
        self.assertIn('master guarda só a norma', out)

    def test_outside_any_repository_without_workstream_refuses(self):
        code, out = self.run_note('new', 'issue', 'solta', workstream=None)
        self.assertEqual(code, 1, out)
        self.assertIn('--workstream', out)

    def test_workstream_flag_without_a_worktree_refuses_with_the_creation_command(self):
        code, out = self.run_note('new', 'issue', 'x', workstream='sem-worktree')
        self.assertEqual(code, 1, out)
        self.assertIn('brza note workstream sem-worktree', out)
        code, out = self.run_note('commit', 'a.md', '-m', 'docs: x', workstream='sem-worktree')
        self.assertEqual(code, 1, out)
        self.assertIn('brza note workstream sem-worktree', out)

    def test_workstream_flag_out_of_kebab_case_is_wrong_usage(self):
        code, _ = self.run_note('new', 'issue', 'x', workstream='Pln_001')
        self.assertEqual(code, 2)

    # workstream

    def test_workstream_creates_the_branch_from_the_fresh_origin_master_and_the_worktree_and_pushes(self):
        subprocess.run(['git', 'clone', '-q', str(self.origin), str(self.other)], check=True, capture_output=True)
        (self.other / 'norma.md').write_text('nova\n')
        git('add', '.', cwd=self.other)
        git('-c', 'user.name=t', '-c', 'user.email=t@t', 'commit', '-q', '-m', 'docs: norma', cwd=self.other)
        git('push', '-q', 'origin', 'HEAD:master', cwd=self.other)
        code, out = self.run_note('workstream', 'workspace')
        self.assertEqual(code, 0, out)
        path = self.ws / '.worktrees' / 'workspace'
        self.assertIn(str(path), out)
        self.assertEqual(git('rev-parse', '--abbrev-ref', 'HEAD', cwd=path), 'workstream/workspace')
        self.assertTrue((path / 'norma.md').exists())
        self.assertEqual(git('rev-parse', '--abbrev-ref', '@{upstream}', cwd=path), 'origin/workstream/workspace')
        self.assertEqual(git('rev-parse', 'workstream/workspace', cwd=self.origin),
                         git('rev-parse', 'master', cwd=self.origin))

    def test_workstream_refuses_an_existing_worktree(self):
        code, out = self.run_note('workstream', 'studio')
        self.assertEqual(code, 1, out)
        self.assertIn('já existe', out)

    def test_workstream_refuses_an_existing_local_branch_without_worktree(self):
        git('worktree', 'remove', str(self.tree), cwd=self.ws)
        code, out = self.run_note('workstream', 'studio')
        self.assertEqual(code, 1, out)
        self.assertIn('workstream/studio', out)
        self.assertFalse(self.tree.exists())

    def test_workstream_reopens_a_branch_that_only_exists_on_the_origin(self):
        self.write('work/issues/a.md')
        code, out = self.run_note('commit', 'work/issues/a.md', '-m', 'docs: a')
        self.assertEqual(code, 0, out)
        git('worktree', 'remove', str(self.tree), cwd=self.ws)
        git('branch', '-q', '-D', 'workstream/studio', cwd=self.ws)
        code, out = self.run_note('workstream', 'studio')
        self.assertEqual(code, 0, out)
        self.assertTrue((self.tree / 'work' / 'issues' / 'a.md').exists())
        self.assertEqual(git('rev-parse', '--abbrev-ref', '@{upstream}', cwd=self.tree), 'origin/workstream/studio')

    def test_workstream_name_out_of_kebab_case_is_wrong_usage(self):
        code, _ = self.run_note('workstream', 'Pln_003')
        self.assertEqual(code, 2)

    # memória

    def local_settings(self, tree: Path) -> dict:
        return json.loads((tree / '.claude' / 'settings.local.json').read_text())

    def test_workstream_links_the_session_memory_without_committing_it(self):
        code, out = self.run_note('workstream', 'workspace')
        self.assertEqual(code, 0, out)
        path = self.ws / '.worktrees' / 'workspace'
        self.assertEqual(self.local_settings(path), {'autoMemoryDirectory': str(path / 'memory')})
        self.assertEqual(list((path / 'memory').iterdir()), [])
        self.assertIn(f'memória:  {path / "memory"}', out)
        self.assertEqual(git('status', '--porcelain', cwd=path), '')
        self.assertEqual(git('rev-parse', 'workstream/workspace', cwd=self.origin),
                         git('rev-parse', 'master', cwd=self.origin))

    def test_the_workstream_reopened_from_the_origin_also_links_the_memory(self):
        git('worktree', 'remove', '--force', str(self.tree), cwd=self.ws)
        git('branch', '-q', '-D', 'workstream/studio', cwd=self.ws)
        code, out = self.run_note('workstream', 'studio')
        self.assertEqual(code, 0, out)
        self.assertEqual(self.local_settings(self.tree), {'autoMemoryDirectory': str(self.tree / 'memory')})
        self.assertTrue((self.tree / 'memory').is_dir())

    def test_memory_setup_links_every_existing_workstream_and_is_idempotent(self):
        two = self.make_workstream('tools-kit')
        for tree in (self.tree, two):
            (tree / '.claude' / 'settings.local.json').unlink()
            (tree / 'memory').rmdir()
        code, out = self.run_note('memory-setup')
        self.assertEqual(code, 0, out)
        lines = out.strip().splitlines()
        self.assertEqual(len(lines), 2, out)
        for name, tree in (('studio', self.tree), ('tools-kit', two)):
            self.assertEqual(self.local_settings(tree), {'autoMemoryDirectory': str(tree / 'memory')})
            self.assertTrue((tree / 'memory').is_dir())
            self.assertIn(f'{name}: memória ligada em {tree / "memory"}', out)
        before = (self.tree / '.claude' / 'settings.local.json').stat().st_mtime_ns
        code, out = self.run_note('memory-setup', cwd=self.tree)
        self.assertEqual(code, 0, out)
        self.assertEqual(len(out.strip().splitlines()), 2, out)
        self.assertEqual(out.count('memória em dia'), 2, out)
        self.assertEqual((self.tree / '.claude' / 'settings.local.json').stat().st_mtime_ns, before)

    def test_memory_setup_merges_the_key_and_keeps_the_others(self):
        settings = self.tree / '.claude' / 'settings.local.json'
        settings.write_text(json.dumps({'permissions': {'allow': ['Bash(ls)']}, 'autoMemoryDirectory': '/velho'}))
        code, out = self.run_note('memory-setup')
        self.assertEqual(code, 0, out)
        self.assertEqual(self.local_settings(self.tree),
                         {'permissions': {'allow': ['Bash(ls)']}, 'autoMemoryDirectory': str(self.tree / 'memory')})

    def test_memory_setup_leaves_unreadable_settings_alone_and_reports_the_failure(self):
        two = self.make_workstream('tools-kit')
        settings = self.tree / '.claude' / 'settings.local.json'
        settings.write_text('{quebrado')
        (two / '.claude' / 'settings.local.json').unlink()
        code, out = self.run_note('memory-setup')
        self.assertEqual(code, 1, out)
        self.assertIn('studio: memória falhou', out)
        self.assertEqual(settings.read_text(), '{quebrado')
        self.assertEqual(self.local_settings(two), {'autoMemoryDirectory': str(two / 'memory')})

    def test_memory_setup_skips_the_master_and_the_workstream_without_a_worktree(self):
        git('worktree', 'remove', '--force', str(self.tree), cwd=self.ws)
        code, out = self.run_note('memory-setup')
        self.assertEqual(code, 0, out)
        self.assertEqual(out.strip(), '')
        self.assertFalse((self.ws / 'memory').exists())

    # o aviso da memória suja no commit

    def dirty_memory(self):
        self.write('memory/sessao.md', 'lembrança\n')
        self.write('.claude/agent-memory/reviewer/achados.md', 'achado\n')

    def test_commit_warns_about_dirty_memory_outside_the_given_paths_and_keeps_the_rest_of_the_output(self):
        self.dirty_memory()
        self.write('work/issues/a.md')
        code, out = self.run_note('commit', 'work/issues/a.md', '-m', 'docs: a', '--no-push')
        self.assertEqual(code, 0, out)
        self.assertEqual(out.strip().splitlines()[-1],
                         'aviso: memória suja fora do commit: .claude/agent-memory/reviewer/achados.md, '
                         'memory/sessao.md')
        self.assertEqual(self.committed_files(), ['work/issues/a.md'])
        self.assertIn('docs: a', out.splitlines()[-2])
        self.assertEqual(git('status', '--porcelain', '-uall', '--', 'memory', cwd=self.tree), '?? memory/sessao.md')

    def test_commit_with_the_memory_paths_leaves_nothing_to_warn_about(self):
        self.dirty_memory()
        self.write('work/issues/a.md')
        code, out = self.run_note('commit', 'work/issues/a.md', 'memory', '.claude/agent-memory', '-m', 'docs: a',
                                  '--no-push')
        self.assertEqual(code, 0, out)
        self.assertNotIn('aviso', out)
        self.assertEqual(sorted(self.committed_files()), ['.claude/agent-memory/reviewer/achados.md',
                                                           'memory/sessao.md', 'work/issues/a.md'])

    def test_commit_warns_only_about_the_memory_file_not_given(self):
        self.dirty_memory()
        code, out = self.run_note('commit', 'memory/sessao.md', '-m', 'docs: memória', '--no-push')
        self.assertEqual(code, 0, out)
        self.assertIn('aviso: memória suja fora do commit: .claude/agent-memory/reviewer/achados.md\n', out)
        self.assertNotIn('memory/sessao.md\n', out.split('aviso')[1])

    def test_commit_warns_about_a_tracked_memory_file_changed_after_the_last_commit(self):
        self.write('memory/sessao.md', 'um\n')
        self.assertEqual(self.run_note('commit', 'memory', '-m', 'docs: memória', '--no-push')[0], 0)
        self.write('memory/sessao.md', 'dois\n')
        self.write('work/issues/a.md')
        code, out = self.run_note('commit', 'work/issues/a.md', '-m', 'docs: a', '--no-push')
        self.assertEqual(code, 0, out)
        self.assertIn('aviso: memória suja fora do commit: memory/sessao.md', out)

    def test_commit_without_dirty_memory_prints_no_warning_and_still_pushes(self):
        self.write('work/issues/a.md')
        code, out = self.run_note('commit', 'work/issues/a.md', '-m', 'docs: a')
        self.assertEqual(code, 0, out)
        self.assertNotIn('aviso', out)
        self.assertEqual(self.origin_log()[0], 'docs: a')

    def test_commit_warns_before_the_push_and_the_push_still_happens(self):
        self.dirty_memory()
        self.write('work/issues/a.md')
        code, out = self.run_note('commit', 'work/issues/a.md', '-m', 'docs: a')
        self.assertEqual(code, 0, out)
        self.assertIn('aviso: memória suja', out)
        self.assertIn('enviado:', out)
        self.assertEqual(self.origin_log()[0], 'docs: a')

    # sync

    def master_commit(self, name: str, text: str = 'da master\n'):
        """Um commit na master da origin, por um clone de outra sessão."""
        if not self.other.exists():
            subprocess.run(['git', 'clone', '-q', str(self.origin), str(self.other)], check=True, capture_output=True)
        (self.other / name).write_text(text)
        git('add', '.', cwd=self.other)
        git('-c', 'user.name=t', '-c', 'user.email=t@t', 'commit', '-q', '-m', f'docs: {name}', cwd=self.other)
        git('push', '-q', 'origin', 'HEAD:master', cwd=self.other)

    def on_origin(self, branch: str, name: str) -> bool:
        return subprocess.run(['git', '-C', str(self.origin), 'cat-file', '-e', f'{branch}:{name}'],
                              capture_output=True).returncode == 0

    def test_sync_merges_the_master_into_every_workstream_and_pushes_the_changed(self):
        two = self.make_workstream('tools-kit')
        self.master_commit('norma.md')
        code, out = self.run_note('sync')
        self.assertEqual(code, 0, out)
        for tree, name in ((self.tree, 'studio'), (two, 'tools-kit')):
            self.assertTrue((tree / 'norma.md').exists())
            self.assertTrue(self.on_origin(f'workstream/{name}', 'norma.md'))
            self.assertIn(f'{name}: mesclado e enviado', out)
        self.assertEqual(len(out.strip().splitlines()), 2)

    def test_sync_with_nothing_new_reports_up_to_date_and_pushes_nothing(self):
        code, out = self.run_note('sync')
        self.assertEqual(code, 0, out)
        self.assertIn('studio: em dia', out)

    def test_sync_no_push_merges_only_locally(self):
        self.master_commit('norma.md')
        code, out = self.run_note('sync', '--no-push')
        self.assertEqual(code, 0, out)
        self.assertIn('studio: mesclado, sem push', out)
        self.assertTrue((self.tree / 'norma.md').exists())
        self.assertFalse(self.on_origin('workstream/studio', 'norma.md'))

    def test_sync_aborts_a_conflict_in_that_worktree_and_goes_on_to_the_next(self):
        two = self.make_workstream('tools-kit')
        self.write('README.md', 'do workstream\n')
        git('commit', '-q', '-am', 'workstream: readme', cwd=self.tree)
        self.master_commit('README.md', 'da master\n')
        code, out = self.run_note('sync')
        self.assertEqual(code, 1, out)
        self.assertIn('studio: conflito', out)
        self.assertIn('tools-kit: mesclado e enviado', out)
        self.assertEqual((self.tree / 'README.md').read_text(), 'do workstream\n')
        self.assertEqual(git('status', '--porcelain', cwd=self.tree), '')
        self.assertFalse((self.tree / git('rev-parse', '--git-path', 'MERGE_HEAD', cwd=self.tree)).exists())
        self.assertEqual((two / 'README.md').read_text(), 'da master\n')
        self.assertTrue(self.on_origin('workstream/tools-kit', 'README.md'))

    def test_sync_skips_the_workstream_without_a_worktree(self):
        git('worktree', 'remove', str(self.tree), cwd=self.ws)
        self.master_commit('norma.md')
        code, out = self.run_note('sync')
        self.assertEqual(code, 0, out)
        self.assertNotIn('studio', out)
        self.assertFalse(self.on_origin('workstream/studio', 'norma.md'))

    def test_sync_runs_from_anywhere(self):
        self.master_commit('norma.md')
        code, out = self.run_note('sync', cwd=self.base)
        self.assertEqual(code, 0, out)
        self.assertTrue((self.tree / 'norma.md').exists())

    # o remote e a branch do manifesto (X8)

    def own_remote_and_branch(self):
        """O manifesto manda `up`/`main`: o remote `up` é outro nome da mesma origin, e a `main` anda à frente da
        master com o arquivo `so-na-main.md`."""
        (self.ws / '.brza').mkdir(exist_ok=True)
        (self.ws / '.brza' / 'workspace.toml').write_text('schema = 1\n\n[workspace]\nbranch = "main"\nremote = "up"\n')
        git('remote', 'add', 'up', str(self.origin), cwd=self.ws)
        subprocess.run(['git', 'clone', '-q', str(self.origin), str(self.other)], check=True, capture_output=True)
        git('checkout', '-q', '-b', 'main', cwd=self.other)
        (self.other / 'so-na-main.md').write_text('main\n')
        git('add', '.', cwd=self.other)
        git('-c', 'user.name=t', '-c', 'user.email=t@t', 'commit', '-q', '-m', 'docs: main', cwd=self.other)
        git('push', '-q', 'origin', 'main', cwd=self.other)

    def test_workstream_branches_from_the_manifest_remote_and_branch(self):
        self.own_remote_and_branch()
        code, out = self.run_note('workstream', 'novo')
        self.assertEqual(code, 0, out)
        path = self.ws / '.worktrees' / 'novo'
        self.assertTrue((path / 'so-na-main.md').exists())
        self.assertIn('da up/main', out)
        self.assertEqual(git('rev-parse', '--abbrev-ref', '@{upstream}', cwd=path), 'up/workstream/novo')

    def test_sync_merges_the_manifest_remote_and_branch(self):
        self.own_remote_and_branch()
        code, out = self.run_note('sync', '--no-push')
        self.assertEqual(code, 0, out)
        self.assertIn('studio: mesclado, sem push', out)
        self.assertTrue((self.tree / 'so-na-main.md').exists())

    def test_merge_conflict_message_names_the_manifest_ref(self):
        self.own_remote_and_branch()
        self.write('so-na-main.md', 'do workstream\n')
        git('add', '.', cwd=self.tree)
        git('commit', '-q', '-m', 'workstream: arquivo', cwd=self.tree)
        code, out = self.run_note('sync', '--no-push')
        self.assertEqual(code, 1, out)
        self.assertIn('o merge com a up/main foi abortado', out)


class NoteManifestNamesTest(NoteFixture):
    """O dono e o nome do código vêm do manifesto, e não do brza-workspace (A9 do P9)."""
    OWNER = 'Beatriz'

    def test_the_worklog_skeleton_names_the_owner_of_the_manifest(self):
        code, out = self.run_note('new', 'worklog', 'um-dia', env={'CLAUDE_SESSION_NAME': 's1'})
        self.assertEqual(code, 0, out)
        text = (self.tree / 'work' / 'worklogs' / '2026-10-28-um-dia.md').read_text()
        self.assertIn('O pedido de Beatriz, em 2026-10-28, na sessão `s1`: "', text)
        self.assertNotIn('Anderson', text)

    def test_the_refusals_name_the_code_of_the_manifest_and_not_the_brza_studio(self):
        subprocess.run(['git', 'init', '-q', '-b', 'feat/sem-plano', str(self.studio)], check=True)
        git('-c', 'user.name=t', '-c', 'user.email=t@t', 'commit', '-q', '--allow-empty', '-m', 'c', cwd=self.studio)
        code, out = self.run_note('new', 'issue', 'x', cwd=self.studio, workstream=None)
        self.assertEqual(code, 1, out)
        self.assertIn('a branch feat/sem-plano do casa-app não traz o plano', out)
        self.assertNotIn('brza-studio', out.replace(str(self.base), ''))
        code, out = self.run_note('new', 'issue', 'x', cwd=self.base, workstream=None)
        self.assertEqual(code, 1, out)
        self.assertIn('nem do casa-app', out)
        self.assertNotIn('brza-studio', out.replace(str(self.base), ''))

    def test_the_help_names_no_workspace_of_the_origin(self):
        code, out = self.run_note('new', '--help', workstream=None)
        self.assertEqual(code, 0, out)
        self.assertNotIn('brza-studio', out)


class NoteWithoutManifestNamesTest(NoteFixture):
    def test_the_refusals_say_the_code_when_there_is_no_manifest(self):
        code, out = self.run_note('new', 'issue', 'x', cwd=self.base, workstream=None)
        self.assertEqual(code, 1, out)
        self.assertIn('nem do código', out)
        self.assertNotIn('brza-studio', out.replace(str(self.base), ''))


class NoteWorklogRefsTest(NoteFixture):
    """P1 do PLN-008: preenchimento e validação de refs: no commit de worklogs."""

    def write(self, rel: str, text: str = 'texto\n') -> Path:
        path = self.tree / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
        return path

    def test_commit_fills_refs_from_prose_inline_subitems_links_and_branches_ignoring_fences(self):
        tools = self.make_workstream('tools-kit')
        self.add_plan(tools, 'PLN-002-remoto')
        rev = tools / 'work' / 'reviews' / 'REV-001-parecer.md'
        rev.parent.mkdir(parents=True, exist_ok=True)
        rev.write_text('---\nstatus: done\nopened: 2026-10-28\ncompleted: 2026-10-28\n---\n\n# REV-001: Parecer\n')
        git('add', '.', cwd=tools)
        git('commit', '-q', '-m', 'docs: rev', cwd=tools)
        git('push', '-q', cwd=tools)
        git('fetch', '-q', 'origin', cwd=self.tree)

        self.write('work/issues/MIG-001-mig.md',
                   '---\nstatus: open\ntype: migration\ndescription: Mig.\nopened: 2026-10-28\n---\n\n# MIG-001: Mig\n')
        self.write('work/issues/bug-local.md',
                   '---\nstatus: open\ndescription: Bug.\nopened: 2026-10-28\n---\n\n# Bug local\n')
        w_rel = 'work/worklogs/2026-10-28-dia.md'
        self.write(w_rel, (
            '---\nscope:\n  - workspace\ntags:\n  - tooling\n---\n\n# O dia\n\n'
            'Mexemos em PLN-001/D1, `PLN-002/P3`, [Parecer](../reviews/REV-001-parecer.md), '
            '`MIG-001` e [Bug local](../issues/bug-local.md).\n'
            '```bash\n'
            'echo PLN-999 MIG-999 REV-999\n'
            '```\n'
        ))
        other_w = 'work/worklogs/2026-10-28-outro-sujo.md'
        other_body = '---\nscope:\n  - workspace\n---\n\n# Outro sujo com PLN-001\n'
        self.write(other_w, other_body)

        code, out = self.run_note('commit', 'work/issues/MIG-001-mig.md', 'work/issues/bug-local.md', w_rel,
                                  '-m', 'docs: worklog com refs', '--no-push')
        self.assertEqual(code, 0, out)
        written = (self.tree / w_rel).read_text()
        self.assertIn(
            'refs:\n  - MIG-001\n  - PLN-001\n  - PLN-002\n  - REV-001\n  - issue:bug-local\n',
            written,
        )
        # Idempotência: recalcular sobre o texto já preenchido mantém os mesmos bytes
        self.assertEqual((self.tree / other_w).read_text(), other_body)
        self.assertEqual(sorted(self.committed_files()),
                         ['work/issues/MIG-001-mig.md', 'work/issues/bug-local.md', w_rel])

    def test_commit_rejects_invalid_existing_refs_before_recalculation_without_rewriting_file(self):
        w_rel = 'work/worklogs/2026-10-28-ruim.md'
        original = (
            '---\nscope:\n  - workspace\nrefs:\n  - PLN-999\n---\n\n# Worklog\n\n'
            'O corpo cita apenas PLN-001 válido, mas o refs antigo inválido não pode sumir no recálculo.\n'
        )
        self.write(w_rel, original)
        head = git('rev-parse', 'HEAD', cwd=self.tree)
        code, out = self.run_note('commit', w_rel, '-m', 'docs: ruim', '--no-push')
        self.assertEqual(code, 1, out)
        self.assertIn('PLN-999', out)
        self.assertEqual((self.tree / w_rel).read_text(), original)
        self.assertEqual(git('rev-parse', 'HEAD', cwd=self.tree), head)

    def test_commit_warns_on_unknown_textual_id_and_omits_it_from_refs(self):
        w_rel = 'work/worklogs/2026-10-28-aviso.md'
        self.write(w_rel, (
            '---\nscope:\n  - workspace\n---\n\n# Aviso\n\n'
            'Exemplo hipotético PLN-404 na linha 8 e real PLN-001.\n'
        ))
        code, out = self.run_note('commit', w_rel, '-m', 'docs: aviso', '--no-push')
        self.assertEqual(code, 0, out)
        self.assertIn('aviso', out)
        self.assertIn('PLN-404', out)
        written = (self.tree / w_rel).read_text()
        self.assertIn('refs:\n  - PLN-001\n', written)
        self.assertNotIn('refs:\n  - PLN-404', written)

    def test_commit_ignores_ids_only_in_link_text(self):
        """D3, R14.3: o link conta pelo alvo; o ID que aparece só no texto dele não entra no refs nem gera aviso."""
        self.write('work/reviews/REV-001-parecer.md',
                   '---\nstatus: done\ndescription: Parecer.\nopened: 2026-11-03\ncompleted: 2026-11-03\n---\n\n'
                   '# REV-001: Parecer\n')
        self.write('work/issues/MIG-001-mig.md',
                   '---\nstatus: open\ntype: migration\ndescription: Mig.\nopened: 2026-11-03\n---\n\n# MIG-001: Mig\n')
        w_rel = 'work/worklogs/2026-11-03-link.md'
        self.write(w_rel, (
            '---\nscope:\n  - workspace\n---\n\n# Link\n\n'
            'Aplicada a [MIG-001: Renumeração (REV-001..REV-004)](../issues/MIG-001-mig.md).\n'
            'E [o `REV-001` em crase no título](../issues/MIG-001-mig.md).\n'
        ))
        code, out = self.run_note('commit', 'work/reviews/REV-001-parecer.md', 'work/issues/MIG-001-mig.md', w_rel,
                                  '-m', 'docs: link', '--no-push')
        self.assertEqual(code, 0, out)
        self.assertNotIn('REV-004', out)
        written = (self.tree / w_rel).read_text()
        self.assertIn('refs:\n  - MIG-001\n---', written)

    def test_extractor_matches_the_checker_on_the_d3_table(self):
        """A5 da REV-006: as duas cópias do extrator do D3, a do brza-note e a do checador, dão o mesmo resultado."""
        scripts = ROOT / 'core' / '.claude' / 'skills' / 'notes-checker' / 'scripts'
        checker = runpy.run_path(str(scripts / 'check_notes.py'))
        self.write('work/reviews/REV-001-parecer.md', '# REV-001: Parecer\n')
        self.write('work/issues/MIG-001-mig.md', '---\ntype: migration\n---\n# MIG-001: Mig\n')
        self.write('work/issues/bug-local.md', '# Bug local\n')
        bodies = {
            'subitem': 'PLN-001/D1 fora de cerca.\n',
            'cerca': '```\nPLN-001 e REV-001\n```\n',
            'crase': 'Em crase, `MIG-001/P1`.\n',
            'link': 'Veja [Bug local](../issues/bug-local.md) e [Parecer](../reviews/REV-001-parecer.md#a1).\n',
            'texto-do-link': 'Aplicada a [MIG-001: Renumeração (REV-001..REV-004)](../issues/MIG-001-mig.md).\n'
                             'E [o `REV-001` em crase](../issues/MIG-001-mig.md) e REV-001 na prosa.\n',
            'inexistente': 'Menção a PLN-404 e issue:fantasma.\n',
            'link-quebrado': 'Veja [MIG-001](../issues/nao-existe.md) e [x](https://exemplo.com/PLN-001).\n',
        }
        for name, body in bodies.items():
            path = self.write(f'work/worklogs/2026-11-03-{name}.md', f'---\nscope:\n  - workspace\n---\n\n# {name}\n\n{body}')
            text = path.read_text()
            with self.subTest(name), contextlib.chdir(self.tree):
                ours = GLOBALS['extract_worklog_refs'](text, path, self.tree)
                theirs = checker['extract_worklog_refs'](text, str(path), str(self.tree))
                self.assertEqual(ours, theirs)

    def test_commit_preserves_issue_identity_in_refs_after_rename(self):
        self.write('work/issues/nome-original.md',
                   '---\nstatus: open\ndescription: A issue.\nopened: 2026-10-28\n---\n\n# A issue\n')
        code, out = self.run_note('commit', 'work/issues/nome-original.md', '-m', 'docs: cria issue')
        self.assertEqual(code, 0, out)
        git('mv', 'work/issues/nome-original.md', 'work/issues/nome-renomeado.md', cwd=self.tree)
        code, out = self.run_note('commit', 'work/issues/nome-original.md', 'work/issues/nome-renomeado.md',
                                  '-m', 'docs: renomeia issue')
        self.assertEqual(code, 0, out)
        w_rel = 'work/worklogs/2026-10-28-pos-rename.md'
        self.write(w_rel, '---\nscope:\n  - workspace\n---\n\n# Pós rename\n\nVeja [A issue](../issues/nome-renomeado.md).\n')
        code, out = self.run_note('commit', w_rel, '-m', 'docs: cita issue renomeada', '--no-push')
        self.assertEqual(code, 0, out)
        self.assertIn('refs:\n  - issue:nome-original\n', (self.tree / w_rel).read_text())


class NoteOutsideTheWorkspaceTest(NoteFixture):
    """O `brza note` de processo: a BRZA_WORKSPACE que o cwd de outro repositório não pode tomar (A1), e o cwd sem
    workspace nenhum (A5). O manifesto vem do OWNER, para a variável valer."""

    OWNER = 'Beatriz'
    COMMANDS = (('new', 'issue', 'x', '--workstream', 'studio'),
                ('commit', 'README.md', '-m', 'x', '--workstream', 'studio'),
                ('plan-open', 'p', '--title', 'T', '--description', 'd', '--workstream', 'studio'),
                ('index', '--rebuild'), ('workstream', 'novo'), ('memory-setup',), ('sync', '--no-push'))

    def run_process(self, *argv, cwd: Path, **env) -> tuple[int, str]:
        base = {k: v for k, v in os.environ.items() if k not in ('BRZA_WORKSPACE', 'BRZA_STUDIO')}
        done = subprocess.run([sys.executable, str(BIN / 'brza-note'), *argv], cwd=str(cwd), env={**base, **env},
                              capture_output=True, text=True)
        return done.returncode, done.stdout + done.stderr

    def other_workspace(self) -> Path:
        other = self.base / 'ws2'
        subprocess.run(['git', 'init', '-q', '-b', 'master', str(other)], check=True)
        (other / '.brza').mkdir()
        (other / '.brza' / 'workspace.toml').write_text((self.ws / '.brza' / 'workspace.toml').read_text())
        git('add', '.', cwd=other)
        git('-c', 'user.name=t', '-c', 'user.email=t@t', 'commit', '-q', '-m', 'init', cwd=other)
        return other

    def state(self, *repos: Path) -> list[str]:
        return [git('for-each-ref', '--format=%(refname) %(objectname)', cwd=r) + git('worktree', 'list', cwd=r)
                + (git('status', '--porcelain', cwd=r) if r != self.origin else '') for r in (*repos, self.origin)]

    def test_every_command_refuses_when_the_cwd_is_another_workspace_and_changes_nothing(self):  # A1
        other = self.other_workspace()
        before = self.state(self.ws, other)
        for argv in self.COMMANDS:
            code, out = self.run_process(*argv, cwd=other, BRZA_WORKSPACE=str(self.ws))
            self.assertEqual(code, 1, (argv, out))
            self.assertIn('recusado:', out)
            self.assertIn(str(other), out)  # o cwd
            self.assertIn(str(self.ws), out)  # o workspace da variável
            self.assertEqual(self.state(self.ws, other), before, argv)
        self.assertFalse((self.tree / 'work' / 'issues' / 'x.md').exists())

    def test_the_variable_still_applies_outside_any_git_repository(self):  # A1
        code, out = self.run_process('new', 'issue', 'x', '--workstream', 'studio', cwd=self.base,
                                     BRZA_WORKSPACE=str(self.ws))
        self.assertEqual(code, 0, out)
        self.assertTrue((self.tree / 'work' / 'issues' / 'x.md').exists())

    def test_the_variable_applies_from_the_workspace_and_from_its_worktree(self):  # A1
        for cwd in (self.ws, self.tree):
            code, out = self.run_process('new', 'worklog', f'de-{cwd.name}', '--workstream', 'studio', cwd=cwd,
                                         BRZA_WORKSPACE=str(self.ws))
            self.assertEqual(code, 0, out)

    def test_without_a_workspace_every_command_refuses_and_there_is_no_fixed_name(self):  # A5
        home = self.base / 'home'
        (home / 'Projects' / 'brza-workspace').mkdir(parents=True)  # o legado do X10 não o toma mais
        for argv in self.COMMANDS:
            code, out = self.run_process(*argv, cwd=self.base, HOME=str(home))
            self.assertEqual(code, 1, (argv, out))
            self.assertIn('recusado: nenhum workspace achado a partir de', out)
        self.assertEqual(list((home / 'Projects' / 'brza-workspace').iterdir()), [])





class TaskCommitTest(NoteFixture):
    """PLN-012/P3: marco real em repositório temporário e medida sem consumir o transcript."""

    def setUp(self):
        self.environment = patch.dict(os.environ)
        self.environment.start()
        self.addCleanup(self.environment.stop)
        for key in ('CLAUDE_CODE_SESSION_ID', 'CLAUDE_SESSION_NAME', 'ORCA_AGENT_SESSION_ID', 'GROK_SESSION_ID'):
            os.environ.pop(key, None)
        super().setUp()
        os.environ['MEASURE_DIR'] = str(self.ws)
        os.environ['CLAUDE_CONFIG_DIR'] = str(self.base / 'claude')
        self.clock = patch.dict(GLOBALS, {'now': lambda: '2026-11-09T12:10:00+00:00'})
        self.clock.start()
        self.addCleanup(self.clock.stop)
        self.rel = 'work/worklogs/2026-11-09-tarefa.md'
        self.path = self.tree / self.rel
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.original = '---\nscope:\n  - workspace\ncompleted: 2026-11-09\n---\n\n# Tarefa\n\nRegistro.\n'
        self.path.write_text(self.original)
        self.log = self.ws / '.measurements' / 'measurements.jsonl'
        self.log.parent.mkdir(exist_ok=True)

    def events(self):
        return [json.loads(line) for line in self.log.read_text().splitlines()] if self.log.exists() else []

    def seed(self, *rows):
        self.log.write_text(''.join(json.dumps(r) + '\n' for r in rows))

    def close(self, *extra, env=None):
        return self.run_note('commit', self.rel, '-m', 'docs: tarefa', '--no-push', *extra, env=env)

    def transcript(self):
        transcript = self.base / 'claude' / 'projects' / '-workspace' / 'sid.jsonl'
        transcript.parent.mkdir(parents=True)
        prefix = json.dumps({'type': 'user', 'timestamp': '2026-11-09T11:00:00Z',
                             'message': {'content': 'turno anterior'}}) + '\n'
        rows = [{'type': 'user', 'timestamp': '2026-11-09T12:00:00Z', 'message': {'content': 'faça a tarefa'}},
                {'type': 'assistant', 'timestamp': '2026-11-09T12:05:00Z', 'requestId': 'r',
                 'message': {'id': 'r', 'model': 'claude-haiku-4-5', 'content': [],
                             'usage': {'input_tokens': 3000000, 'output_tokens': 0}}}]
        transcript.write_text(prefix + ''.join(json.dumps(r) + '\n' for r in rows))
        state = self.ws / '.measurements' / 'state.json'
        state.write_text(json.dumps({str(transcript): len(prefix.encode()), 'machine:99': {'cpu_self_s': 5}}))
        return transcript, state

    def test_stamps_session_and_writes_task_after_commit_even_without_push(self):
        code, out = self.close(env={'CLAUDE_CODE_SESSION_ID': 'sid', 'ORCA_AGENT_SESSION_ID': 'chat',
                                    'CLAUDE_SESSION_NAME': 'sessao'})
        self.assertEqual(code, 0, out)
        text = self.path.read_text()
        self.assertIn('sessions:\n  - sid', text)
        self.assertNotIn('opened:', text)
        self.assertIn('completed: 2026-11-09', text)
        event, = self.events()
        self.assertEqual(event['event'], 'task')
        self.assertEqual(event['worklogs'], [self.rel])
        self.assertEqual(event['session_id'], 'sid')
        self.assertEqual(event['chat_id'], 'chat')
        self.assertEqual(event['workstream'], 'studio')
        self.assertEqual(event['commit'], git('rev-parse', 'HEAD', cwd=self.tree))
        self.assertIsNone(event['until'])
        self.assertEqual(git('show', f'HEAD:{self.rel}', cwd=self.tree), text.strip())

    def test_without_session_preserves_worklog_bytes_and_writes_null_session(self):
        code, out = self.close(env={'ORCA_AGENT_SESSION_ID': 'codex_chat'})
        self.assertEqual(code, 0, out)
        self.assertEqual(self.path.read_text(), self.original)
        event, = self.events()
        self.assertIsNone(event['session_id'])
        self.assertEqual(event['chat_id'], 'codex_chat')

    def test_grok_session_id_alone_is_stamped_on_the_worklog_and_the_task(self):
        code, out = self.close(env={'GROK_SESSION_ID': 'grok-sid'})
        self.assertEqual(code, 0, out)
        self.assertIn('sessions:\n  - grok-sid', self.path.read_text())
        event, = self.events()
        self.assertEqual(event['session_id'], 'grok-sid')

    def test_claude_session_id_wins_over_grok_session_id(self):
        code, out = self.close(env={'CLAUDE_CODE_SESSION_ID': 'sid', 'GROK_SESSION_ID': 'grok-sid'})
        self.assertEqual(code, 0, out)
        text = self.path.read_text()
        self.assertIn('sessions:\n  - sid', text)
        self.assertNotIn('grok-sid', text)
        self.assertEqual(self.events()[-1]['session_id'], 'sid')

    def test_until_with_only_grok_session_id_is_refused(self):
        code, out = self.close('--until', '12:05', env={'GROK_SESSION_ID': 'grok-sid'})
        self.assertEqual(code, 1, out)
        self.assertIn('sessão Claude', out)
        self.assertEqual(self.path.read_text(), self.original)
        self.assertFalse(self.log.exists())

    def test_current_turn_uses_offset_until_commit_and_preserves_state(self):
        _, state = self.transcript()
        before = state.read_bytes()
        code, out = self.close(env={'CLAUDE_CODE_SESSION_ID': 'sid'})
        self.assertEqual(code, 0, out)
        text = self.path.read_text()
        self.assertIn('Corrido 10 min', text)
        self.assertIn('agente 10 min', text)
        self.assertIn('US$ 3,00', text)
        self.assertIn('1 turno', text)
        self.assertNotIn('sem o turno do commit', text)
        self.assertEqual(state.read_bytes(), before)
        self.assertEqual([r['event'] for r in self.events()], ['task'])

    def test_the_last_section_is_titled_custo_da_tarefa(self):  # R6.1 do PLN-012
        code, out = self.close(env={'CLAUDE_CODE_SESSION_ID': 'sid'})
        self.assertEqual(code, 0, out)
        text = self.path.read_text()
        self.assertEqual(text.count('## Custo da tarefa'), 1)
        self.assertNotIn('## Medida', text)
        self.assertNotIn('\n## ', text.split('## Custo da tarefa', 1)[1])  # a última seção

    def test_a_worklog_with_the_old_medida_title_leaves_with_one_section_with_the_new_title(self):
        self.path.write_text(self.original + '\n## Medida\n\nCorrido 1 min · velho\n')
        code, out = self.close(env={'CLAUDE_CODE_SESSION_ID': 'sid'})
        self.assertEqual(code, 0, out)
        text = self.path.read_text()
        self.assertEqual(text.count('## Custo da tarefa'), 1)
        self.assertNotIn('## Medida', text)
        self.assertNotIn('velho', text)
        self.assertIn('Registro.', text)

    def test_the_task_event_carries_the_pid_of_the_session_claude(self):  # D1; A2 da revisão da entrega
        code, out = self.close(env={'CLAUDE_CODE_SESSION_ID': 'sid', 'CLAUDE_PID': '4242'})
        self.assertEqual(code, 0, out)
        self.assertEqual(self.events()[-1]['pid'], 4242)

    def test_the_cpu_follows_the_number_rule_of_the_section(self):  # D3; A3 da revisão da entrega
        cut = runpy.run_path(str(BIN / 'brza-note'), run_name='brza_note')
        summary, _ = cut['measurement_modules']()
        measure = {'elapsed_s': 600, 'agent_s': 500, 'requester_s': 100, 'pause_s': 0, 'cost_turns': 1,
                   'cost_subagents': 0, 'no_cost_turns': 0, 'no_cost_subagents': 0, 'machine_turns': 1,
                   'cpu_self_s': 27.94, 'cpu_children_s': 1089.43, 'rss_max_mb': 459, 'turns': 1, 'sessions': 1,
                   'runs': 0, 'shared_with': 0}
        event = {'event': 'task', 'timestamp': '2026-11-09T19:48:08-03:00', 'until': None}
        line = cut['measure_line'](measure, event, summary, False)
        self.assertIn('CPU 28 s do `claude` e 18 min dos comandos, RSS até 459 MB', line)

    def test_the_title_inside_a_fenced_block_is_not_the_section(self):  # A2 da revisão do P7
        fenced = '\n```markdown\n## Custo da tarefa\n\nexemplo\n```\n\nDepois da cerca.\n'
        self.path.write_text(self.original + fenced)
        code, out = self.close(env={'CLAUDE_CODE_SESSION_ID': 'sid'})
        self.assertEqual(code, 0, out)
        text = self.path.read_text()
        self.assertIn('Depois da cerca.', text)
        self.assertEqual(text.count('## Custo da tarefa'), 2)
        self.assertTrue(text.split('Depois da cerca.', 1)[1].lstrip().startswith('## Custo da tarefa\n'))

    def test_the_cut_follows_how_fences_open_and_close(self):  # B1 e B2 da revisão do P7
        cut = runpy.run_path(str(BIN / 'brza-note'), run_name='brza_note')['without_cost_section']
        old = '\n\n## Custo da tarefa\n\nvelho\n'
        cases = {
            'aninhada com info string': ('# T\n\n```\n```python\n## Custo da tarefa\n```\n\nfim' + old,
                                         '# T\n\n```\n```python\n## Custo da tarefa\n```\n\nfim'),
            'til dentro de crase': ('# T\n\n```\n~~~\n## Medida\n```\n\nfim' + old, '# T\n\n```\n~~~\n## Medida\n```\n\nfim'),
            'til': ('# T\n~~~\n## Medida\n~~~\nfim' + old, '# T\n~~~\n## Medida\n~~~\nfim'),
            'cerca mais curta não fecha': ('# T\n\n````\n```\n## Custo da tarefa\n````\n\nfim' + old,
                                           '# T\n\n````\n```\n## Custo da tarefa\n````\n\nfim'),
            'cerca que não fecha': ('# T\n\n```\ncodigo' + old, '# T\n\n```\ncodigo'),
            'crase na info string não abre cerca': ('# T\n\n```x``` no texto\n\n```\n## Medida\n```\n\nfim' + old,
                                                    '# T\n\n```x``` no texto\n\n```\n## Medida\n```\n\nfim'),
            'título antigo e CRLF': ('# T\r\n\r\n## Medida  \r\nvelho\r\n', '# T'),
        }
        for name, (text, kept) in cases.items():
            with self.subTest(name):
                self.assertEqual(cut(text), kept)

    def test_recommit_accumulates_sessions_and_replaces_measure_section(self):
        transcript, state = self.transcript()
        self.assertEqual(self.close(env={'CLAUDE_CODE_SESSION_ID': 'sid'})[0], 0)
        state.write_text(json.dumps({str(transcript): transcript.stat().st_size}))
        event = self.events()[0]
        self.seed({'event': 'turn', 'timestamp': event['timestamp'], 'duration_s': 600, 'session_id': 'sid',
                   'estimated_cost_usd': 3, 'workstream': 'studio'}, event)
        self.path.write_text(self.path.read_text().replace('Registro.', 'Registro ampliado.'))
        with patch.dict(GLOBALS, {'now': lambda: '2026-11-09T13:00:00+00:00'}):
            code, out = self.close(env={'CLAUDE_CODE_SESSION_ID': 'sid'})
        self.assertEqual(code, 0, out)
        text = self.path.read_text()
        self.assertEqual(text.count('  - sid'), 1)
        self.assertEqual(text.count('## Custo da tarefa'), 1)
        self.assertNotIn('## Medida', text)
        self.assertIn('Corrido 10 min', text)
        self.assertEqual(len(self.events()), 3)

    def test_missing_transcript_marks_partial_and_uses_closed_turns(self):
        self.seed({'event': 'task', 'timestamp': '2026-11-09T11:00:00Z', 'session_id': 'other',
                   'worklogs': ['work/worklogs/outra.md']},
                  {'event': 'turn', 'timestamp': '2026-11-09T12:05:00Z', 'duration_s': 300,
                   'session_id': 'sid', 'workstream': 'studio', 'estimated_cost_usd': 2.35})
        code, out = self.close(env={'CLAUDE_CODE_SESSION_ID': 'sid'})
        self.assertEqual(code, 0, out)
        self.assertIn('Corrido 5 min', self.path.read_text())
        self.assertIn('sem o turno do commit', self.path.read_text())

    def test_until_outside_window_is_rejected_before_any_worklog_mutation(self):
        self.seed({'event': 'task', 'timestamp': '2026-11-09T12:05:00Z', 'session_id': 'sid',
                   'worklogs': ['work/worklogs/antes.md']})
        before = self.log.read_bytes()
        for until in ('2026-11-09T12:04:00Z', '2026-11-09T12:11:00Z', '25:00', 'ontem'):
            with self.subTest(until=until):
                code, out = self.close('--until', until, env={'CLAUDE_CODE_SESSION_ID': 'sid'})
                self.assertEqual(code, 1, out)
                self.assertEqual(self.path.read_text(), self.original)
                self.assertEqual(self.log.read_bytes(), before)

    def test_until_requires_session_and_worklog(self):
        code, out = self.close('--until', '12:05')
        self.assertEqual(code, 1, out)
        self.assertEqual(self.path.read_text(), self.original)
        self.assertFalse(self.log.exists())
        code, out = self.run_note('commit', 'README.md', '-m', 'docs: nada', '--no-push', '--until', '12:05',
                                  env={'CLAUDE_CODE_SESSION_ID': 'sid'})
        self.assertEqual(code, 1, out)
        self.assertFalse(self.log.exists())

    def test_until_is_stored_as_iso_and_excludes_turn_starting_on_boundary(self):
        self.seed({'event': 'task', 'timestamp': '2026-11-09T11:00:00Z', 'session_id': 'other',
                   'worklogs': ['work/worklogs/outra.md']},
                  {'event': 'turn', 'timestamp': '2026-11-09T12:03:00Z', 'duration_s': 180,
                   'session_id': 'sid', 'estimated_cost_usd': 1},
                  {'event': 'turn', 'timestamp': '2026-11-09T12:09:00Z', 'duration_s': 240,
                   'session_id': 'sid', 'estimated_cost_usd': 4})
        code, out = self.close('--until', '2026-11-09T12:05:00Z', env={'CLAUDE_CODE_SESSION_ID': 'sid'})
        self.assertEqual(code, 0, out)
        self.assertEqual(self.events()[-1]['until'], '2026-11-09T12:05:00+00:00')
        self.assertIn('Corrido 3 min', self.path.read_text())
        self.assertIn('US$ 1,00', self.path.read_text())

    def test_checker_rejection_writes_no_task_event(self):
        self.checker_exit(1, self.tree)
        code, out = self.close(env={'CLAUDE_CODE_SESSION_ID': 'sid'})
        self.assertEqual(code, 1, out)
        self.assertFalse(self.log.exists())

    def test_no_change_writes_no_task_and_does_not_create_change_from_measure_alone(self):
        self.assertEqual(self.close(env={'CLAUDE_CODE_SESSION_ID': 'sid'})[0], 0)
        before = self.path.read_bytes(), self.log.read_bytes()
        with patch.dict(GLOBALS, {'now': lambda: '2026-11-09T13:00:00+00:00'}):
            code, out = self.close(env={'CLAUDE_CODE_SESSION_ID': 'sid'})
        self.assertEqual(code, 1, out)
        self.assertEqual((self.path.read_bytes(), self.log.read_bytes()), before)

    def test_failed_git_commit_writes_no_task(self):
        hook = Path(git('rev-parse', '--git-common-dir', cwd=self.tree)) / 'hooks' / 'pre-commit'
        hook.write_text('#!/bin/sh\nexit 1\n')
        hook.chmod(0o755)
        code, out = self.close(env={'CLAUDE_CODE_SESSION_ID': 'sid'})
        self.assertNotEqual(code, 0, out)
        self.assertFalse(self.log.exists())

    def test_multiple_worklogs_share_measure_using_aggregate(self):
        self.transcript()
        other = 'work/worklogs/2026-11-09-outra.md'
        (self.tree / other).write_text(self.original.replace('# Tarefa', '# Outra'))
        code, out = self.run_note('commit', self.rel, other, '-m', 'docs: duas', '--no-push',
                                  env={'CLAUDE_CODE_SESSION_ID': 'sid'})
        self.assertEqual(code, 0, out)
        for rel in (self.rel, other):
            text = (self.tree / rel).read_text()
            self.assertIn('Corrido 5 min', text)
            self.assertIn('US$ 1,50', text)
            self.assertIn('dividida com 1 outro worklog', text)
        self.assertEqual(self.events()[-1]['worklogs'], sorted([self.rel, other]))

    def test_push_observes_task_already_written_with_successful_commit_hash(self):
        original_push = GLOBALS['push']
        seen = []
        def checked_push(root, branch):
            seen.extend(self.events())
            self.assertEqual(seen[-1]['commit'], git('rev-parse', 'HEAD', cwd=root))
            return original_push(root, branch)
        with patch.dict(GLOBALS, {'push': checked_push}):
            code, out = self.run_note('commit', self.rel, '-m', 'docs: tarefa',
                                      env={'CLAUDE_CODE_SESSION_ID': 'sid'})
        self.assertEqual(code, 0, out)
        self.assertEqual(len(seen), 1)
        self.assertEqual(self.origin_log()[0], 'docs: tarefa')

    def test_missing_state_reads_whole_transcript_without_creating_state(self):
        _, state = self.transcript()
        state.unlink()
        code, out = self.close(env={'CLAUDE_CODE_SESSION_ID': 'sid'})
        self.assertEqual(code, 0, out)
        self.assertIn('Corrido 1 h 10 min', self.path.read_text())
        self.assertFalse(state.exists())

    def test_unreadable_transcript_falls_back_without_blocking_commit(self):
        self.transcript()
        summary, hook = GLOBALS['measurement_modules']()
        hook['read_from'] = lambda *args: (_ for _ in ()).throw(PermissionError('sem acesso'))
        with patch.dict(GLOBALS, {'measurement_modules': lambda: (summary, hook)}):
            code, out = self.close(env={'CLAUDE_CODE_SESSION_ID': 'sid'})
        self.assertEqual(code, 0, out)
        self.assertIn('transcript ilegível', out)
        self.assertIn('sem o turno do commit', self.path.read_text())
        self.assertEqual(len(self.events()), 1)

    def test_current_machine_uses_saved_baseline_without_writing_it(self):
        _, state = self.transcript()
        before = state.read_bytes()
        summary, hook = GLOBALS['measurement_modules']()
        called = []
        def machine(pid, origin, last):
            called.append((pid, origin, last))
            return {'cpu_self_s': .8, 'cpu_children_s': 6.1, 'rss_mb': 357}, {'changed': True}
        hook.update({'claude_pid': lambda: (99, 'env'), 'machine_of': machine})
        with patch.dict(GLOBALS, {'measurement_modules': lambda: (summary, hook)}):
            code, out = self.close(env={'CLAUDE_CODE_SESSION_ID': 'sid'})
        self.assertEqual(code, 0, out)
        self.assertEqual(called, [(99, 'env', {'cpu_self_s': 5})])
        self.assertIn('CPU 1 s do `claude` e 6 s dos comandos', self.path.read_text())  # A3 da revisão da entrega
        self.assertIn('RSS até 357 MB', self.path.read_text())
        self.assertEqual(state.read_bytes(), before)

    def test_accumulation_across_sessions_includes_runs_and_subagent_cost(self):
        self.seed({'event': 'task', 'timestamp': '2026-11-09T10:00:00Z', 'session_id': 'seed', 'worklogs': []},
                  {'event': 'turn', 'timestamp': '2026-11-09T11:00:00Z', 'duration_s': 120,
                   'session_id': 'before', 'estimated_cost_usd': 1, 'waits_s': [30]},
                  {'event': 'subagent', 'timestamp': '2026-11-09T10:59:30Z', 'duration_s': 30,
                   'session_id': 'before', 'estimated_cost_usd': .5},
                  {'event': 'run', 'timestamp': '2026-11-09T10:59:50Z', 'duration_s': 10,
                   'session_id': 'before', 'cpu_s': 120, 'max_rss_mb': 2048},
                  {'event': 'task', 'timestamp': '2026-11-09T11:00:00Z', 'session_id': 'before',
                   'worklogs': [self.rel], 'commit': 'earlier'})
        self.path.write_text(self.original.replace('completed:', 'sessions:\n  - before\ncompleted:'))
        self.transcript()
        code, out = self.close(env={'CLAUDE_CODE_SESSION_ID': 'sid'})
        self.assertEqual(code, 0, out)
        text = self.path.read_text()
        self.assertIn('Corrido 12 min', text)
        self.assertIn('quem pediu 30 s', text)
        self.assertIn('US$ 4,50', text)
        self.assertIn('2 sessões', text)
        self.assertIn('`brza run`: 1 comando, CPU 2 min, pico 2,0 GB', text)
        self.assertIn('sessions:\n  - before\n  - sid', text)

    def test_directory_commit_does_not_reopen_or_rewrite_unchanged_worklog(self):
        old_rel = 'work/worklogs/2026-11-08-antiga.md'
        old = self.tree / old_rel
        old.write_text(self.original.replace('# Tarefa', '# Antiga'))
        git('add', old_rel, cwd=self.tree)
        git('commit', '-q', '-m', 'docs: antiga', '--', old_rel, cwd=self.tree)
        old_bytes = old.read_bytes()
        self.transcript()
        code, out = self.run_note('commit', 'work/worklogs', '-m', 'docs: tarefa', '--no-push',
                                  env={'CLAUDE_CODE_SESSION_ID': 'sid'})
        self.assertEqual(code, 0, out)
        self.assertEqual(old.read_bytes(), old_bytes)
        self.assertEqual(self.events()[-1]['worklogs'], [self.rel])
        self.assertEqual(self.committed_files(), [self.rel])
        self.assertIn('Corrido 10 min', self.path.read_text())

    def test_task_hash_matches_rebased_commit_before_push(self):
        subprocess.run(['git', 'clone', '-q', '-b', 'workstream/studio', str(self.origin), str(self.other)],
                       check=True, capture_output=True)
        (self.other / 'work').mkdir(exist_ok=True)
        (self.other / 'work' / 'outra.md').write_text('outra sessão\n')
        git('add', '.', cwd=self.other)
        git('-c', 'user.name=t', '-c', 'user.email=t@t', 'commit', '-q', '-m', 'docs: outra', cwd=self.other)
        git('push', '-q', 'origin', 'HEAD:workstream/studio', cwd=self.other)
        original_push = GLOBALS['push']
        def check_push(root, branch):
            self.assertEqual(self.events()[-1]['commit'], git('rev-parse', 'HEAD', cwd=root))
            return original_push(root, branch)
        with patch.dict(GLOBALS, {'push': check_push}):
            code, out = self.run_note('commit', self.rel, '-m', 'docs: tarefa',
                                      env={'CLAUDE_CODE_SESSION_ID': 'sid'})
        self.assertEqual(code, 0, out)
        self.assertEqual(self.events()[-1]['commit'], git('rev-parse', 'workstream/studio', cwd=self.origin))

    def test_failed_fetch_keeps_event_for_successful_local_commit(self):
        real = runtime.git
        def fail_fetch(*args, **kwargs):
            if args[0] == 'fetch':
                raise runtime.BrzaError('fetch falhou')
            return real(*args, **kwargs)
        with patch.object(runtime, 'git', fail_fetch):
            code, out = self.run_note('commit', self.rel, '-m', 'docs: tarefa',
                                      env={'CLAUDE_CODE_SESSION_ID': 'sid'})
        self.assertNotEqual(code, 0, out)
        self.assertEqual(self.events()[-1]['commit'], git('rev-parse', 'HEAD', cwd=self.tree))


    def test_hhmm_cut_uses_the_local_day_of_commit(self):
        code, out = self.close('--until', '12:05', env={'CLAUDE_CODE_SESSION_ID': 'sid'})
        self.assertEqual(code, 0, out)
        self.assertEqual(self.events()[-1]['until'], '2026-11-09T12:05:00+00:00')

    def test_push_failure_keeps_exactly_one_event_for_local_commit(self):
        def fail_push(*args):
            raise runtime.BrzaError('push falhou')
        with patch.dict(GLOBALS, {'push': fail_push}):
            code, out = self.run_note('commit', self.rel, '-m', 'docs: tarefa',
                                      env={'CLAUDE_CODE_SESSION_ID': 'sid'})
        self.assertNotEqual(code, 0, out)
        self.assertEqual(len(self.events()), 1)
        self.assertEqual(self.events()[-1]['commit'], git('rev-parse', 'HEAD', cwd=self.tree))



class NoteEnvironmentIsolationTest(NoteFixture):
    """X9 do PLN-012: o ambiente da sessão que roda a suíte (BRZA_WORKSPACE, CLAUDE_CODE_SESSION_ID…) não chega ao
    `brza note commit` dos testes, e o evento `task` não vai ao log do workspace real."""

    REAL = {'BRZA_WORKSPACE': None, 'CLAUDE_CODE_SESSION_ID': 'sessao-real', 'ORCA_AGENT_SESSION_ID': 'chat-real',
            'CLAUDE_SESSION_NAME': 'nome-real', 'GROK_SESSION_ID': 'grok-real', 'BRZA_STUDIO': None}

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        temp = tempfile.TemporaryDirectory()
        cls.addClassCleanup(temp.cleanup)
        cls.real_ws = Path(temp.name).resolve() / 'real-ws'
        cls.real_ws.mkdir()
        cls.fake = {**cls.REAL, 'BRZA_WORKSPACE': str(cls.real_ws), 'BRZA_STUDIO': str(cls.real_ws)}
        patcher = patch.dict(os.environ, cls.fake)  # o ambiente herdado da sessão, antes de qualquer setUp
        patcher.start()
        cls.addClassCleanup(patcher.stop)
        os.environ.pop('MEASURE_DIR', None)

    def test_the_session_environment_does_not_reach_the_test(self):
        for key in self.REAL:
            self.assertNotIn(key, os.environ)

    def test_commit_writes_the_task_event_only_inside_the_test_workspace(self):
        tree = self.tree
        rel = 'work/worklogs/2026-11-09-isolada.md'
        (tree / rel).parent.mkdir(parents=True, exist_ok=True)
        (tree / rel).write_text('---\nscope:\n  - workspace\ncompleted: 2026-11-09\n---\n\n# Isolada\n\nRegistro.\n')
        code, out = self.run_note('commit', rel, '-m', 'docs: isolada', '--no-push')
        self.assertEqual(code, 0, out)
        self.assertEqual(list(self.real_ws.iterdir()), [])
        events = [json.loads(line) for line in (self.ws / '.measurements' / 'measurements.jsonl').read_text().splitlines()]
        self.assertEqual([e['event'] for e in events], ['task'])
        self.assertIsNone(events[0]['session_id'])


if __name__ == '__main__':
    unittest.main()
