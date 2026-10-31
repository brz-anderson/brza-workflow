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


class NoteTest(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.base = Path(temp.name).resolve()
        self.origin, self.other, self.ws = self.base / 'origin.git', self.base / 'other', self.base / 'ws'
        subprocess.run(['git', 'init', '-q', '--bare', '-b', 'master', str(self.origin)], check=True)
        subprocess.run(['git', 'clone', '-q', str(self.origin), str(self.ws)], check=True, capture_output=True)
        git('config', 'user.name', 't', cwd=self.ws)
        git('config', 'user.email', 't@t', cwd=self.ws)
        (self.ws / 'work' / 'worklogs').mkdir(parents=True)
        (self.ws / 'work' / 'issues').mkdir(parents=True)
        (self.ws / 'README.md').write_text('um\n')
        (self.ws / '.gitignore').write_text('.worktrees/\n.claude/settings.local.json\n')
        self.checker_exit(0)
        git('add', '.', cwd=self.ws)
        git('commit', '-q', '-m', 'um', cwd=self.ws)
        git('push', '-q', 'origin', 'HEAD:master', cwd=self.ws)
        git('branch', '-q', '--set-upstream-to=origin/master', cwd=self.ws)
        self.studio = self.base / 'studio'
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
        """Roda o main; sem `cwd`, de fora do workspace de mentira, onde o caminho relativo parte da raiz-alvo. Em new e
        commit, o --workstream vai junto, a menos que `workstream` seja None."""
        if workstream and argv[0] in ('new', 'commit'):
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

    def committed_files(self, ref: str = 'HEAD') -> list[str]:
        return git('show', '--name-only', '--format=', ref, cwd=self.tree).splitlines()

    # new

    def test_new_worklog_writes_the_skeleton_and_prints_the_path(self):
        code, out = self.run_note('new', 'worklog', 'o-brza-note', env={'CLAUDE_SESSION_NAME': 'pln-002-p6'})
        self.assertEqual(code, 0, out)
        path = self.tree / 'work' / 'worklogs' / '2026-10-28-o-brza-note.md'
        self.assertEqual(out.strip().splitlines()[-1], str(path))
        text = path.read_text()
        self.assertTrue(text.startswith('---\nscope:\n  - workspace\n---\n\n# '))
        self.assertIn('O pedido de Anderson, em 2026-10-28, na sessão `pln-002-p6`: "', text)
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

    def test_new_issue_has_open_status_today_scope_and_priority(self):
        code, out = self.run_note('new', 'issue', 'algo-quebrado', '--scope', 'workspace,brza-studio',
                                  '--priority', 'high')
        self.assertEqual(code, 0, out)
        text = (self.tree / 'work' / 'issues' / 'algo-quebrado.md').read_text()
        self.assertTrue(text.startswith('---\nstatus: open\nscope:\n  - workspace\n  - brza-studio\n'
                                        'opened: 2026-10-28\npriority: high\n---\n\n# '), text)
        self.assertIn('\n## O pedido\n', text)

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
        self.assertEqual(self.origin_log('master'), ['um'])
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
        self.assertEqual(self.origin_log('master'), ['um'])

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


if __name__ == '__main__':
    unittest.main()
