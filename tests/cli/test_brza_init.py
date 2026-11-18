"""Testes do `brza init` (PLN-006/P5), em diretórios temporários: um brza-workflow de mentira (cópia de `core/`,
`packages/` e `templates/` deste checkout, num repositório git), um repositório de código, o registro e o git config
global trocados. Nada aqui lê o ~/.config, o ~/.gitconfig nem o brza-workspace da máquina.

    python3 -m unittest discover -s tests/cli
"""
import contextlib
import io
import json
import os
import runpy
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent.parent
BIN = ROOT / 'bin'
os.environ['BRZA_REGISTRY'] = '/nonexistent/brza/workspaces.toml'  # nada aqui lê o registro real da máquina
os.environ['BRZA_FOREIGN'] = '/nonexistent/brza/foreign.txt'  # nem os nomes proibidos da máquina
sys.path.insert(0, str(ROOT / 'core' / '.claude' / 'hooks'))
import brza_root  # noqa: E402

INIT = runpy.run_path(str(BIN / 'brza-init'))
init_workspace, InitError, main = INIT['init_workspace'], INIT['InitError'], INIT['main']

IDENTITY = {'GIT_AUTHOR_NAME': 't', 'GIT_AUTHOR_EMAIL': 't@t', 'GIT_COMMITTER_NAME': 't', 'GIT_COMMITTER_EMAIL': 't@t'}
# o que veio de outro workspace: nada disso pode sobrar no que o init escreve (R1.2, R8.2); o resto vem do arquivo da
# máquina, fora do git (D11 do PLN-008), e tem os testes dele no ForeignFileTest
FOREIGN = ('brza-workspace', 'brza-studio', 'Anderson', 'brz-anderson', 'claude-501')
APP_TERMS = ('orca', 'ORCA')  # o texto do app mora só no pacote do cliente (A8 do P9)
CHECKER = ('.claude', 'skills', 'notes-checker', 'scripts', 'check_notes.py')


def git(cwd, *args, env=None):
    done = subprocess.run(['git', '-C', str(cwd), *args], capture_output=True, text=True, env={**os.environ, **IDENTITY, **(env or {})})
    assert done.returncode == 0, done.stderr
    return done.stdout.strip()


def make_repo(path: Path, branch='master', url=None) -> Path:
    path.mkdir(parents=True)
    git(path, 'init', '-q', '-b', branch)
    git(path, 'commit', '-q', '--allow-empty', '-m', 'x')
    if url:
        git(path, 'remote', 'add', 'origin', url)
    return path


def files_of(root: Path):
    return sorted(str(p.relative_to(root)) for p in root.rglob('*') if p.is_file() and '.git' not in p.relative_to(root).parts[:1])


class InitBase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._flow_dir = tempfile.TemporaryDirectory()
        cls.flow = Path(cls._flow_dir.name).resolve() / 'flow'
        cls.flow.mkdir()
        for name in ('core', 'packages', 'templates'):
            shutil.copytree(ROOT / name, cls.flow / name, ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
        git(cls.flow, 'init', '-q', '-b', 'master')
        git(cls.flow, 'add', '-A')
        git(cls.flow, 'commit', '-q', '-m', 'flow')
        cls.flow_sha = git(cls.flow, 'rev-parse', 'HEAD')

    @classmethod
    def tearDownClass(cls):
        cls._flow_dir.cleanup()

    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.base = Path(temp.name).resolve()
        self.home = self.base / 'home'
        self.home.mkdir()
        self.gitconfig = self.base / 'gitconfig'
        self.gitconfig.write_text('[user]\n\tname = Ana Souza\n\temail = 77+anasouza@users.noreply.github.com\n')
        self.registry = self.base / 'config' / 'brza' / 'workspaces.toml'
        self.env = {'HOME': str(self.home), 'GIT_CONFIG_GLOBAL': str(self.gitconfig), 'GIT_CONFIG_NOSYSTEM': '1',
                    'BRZA_REGISTRY': str(self.registry), 'BRZA_WORKFLOW': str(self.flow),
                    'PYTHONDONTWRITEBYTECODE': '1'}
        patcher = patch.dict(os.environ, self.env)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.code = make_repo(self.base / 'acme-app', url='git@github.com:acme-org/acme-app.git')
        self.root = self.base / 'acme-notes'

    def init(self, root=None, code=None, name='acme-notes', **kw):
        out = []
        result = init_workspace(root or self.root, name=name, code=code or self.code, out=out.append,
                                today='2026-11-01', **kw)
        return result, out

    def run_main(self, *argv):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            code = main([str(a) for a in argv])
        return code, buf.getvalue()

    def registry_text(self):
        return self.registry.read_text() if self.registry.exists() else ''

    def checker(self, root=None):
        root = Path(root or self.root)
        return subprocess.run([sys.executable, str(root.joinpath(*CHECKER)), '--root', str(root)], capture_output=True,
                              text=True, env={**os.environ, 'PYTHONDONTWRITEBYTECODE': '1'})


class AcceptanceTest(InitBase):
    """O aceite da Cobertura: o init num diretório temporário cria o workspace, e o notes-checker roda verde nele."""

    def setUp(self):
        super().setUp()
        self.result, self.out = self.init()

    def test_it_creates_the_norm_the_folders_and_the_hooks(self):
        for path in ('AGENTS.md', 'CLAUDE.md', 'README.md', 'plans/README.md', 'work/README.md', '.gitignore',
                     '.gitattributes', '.claude/settings.json', '.claude/rules/workspace.md', '.brza/workspace.toml',
                     '.brza/index.jsonl', '.brza/core.lock', 'scripts/summarize_measurements.py',
                     'wiki/guides/.gitkeep'):
            self.assertTrue((self.root / path).is_file(), path)
        for genre in ('issues', 'worklogs', 'decisions', 'reviews', 'notes', 'feedbacks'):
            self.assertTrue((self.root / 'work' / genre / '.gitkeep').is_file(), genre)
        for folder in ('.claude/skills', '.claude/agents', '.claude/hooks'):
            self.assertTrue(any((self.root / folder).iterdir()), folder)
        for hook in ('measure.py', 'compact_guard.py', 'fact_audit.py', 'brza_root.py'):
            self.assertTrue((self.root / '.claude' / 'hooks' / hook).is_file(), hook)
        self.assertIn('notes-checker', {p.name for p in (self.root / '.claude' / 'skills').iterdir()})
        self.assertEqual({p.name for p in (self.root / '.claude' / 'agents').iterdir()},
                         {'collector.md', 'designer.md', 'implementer.md', 'reviewer.md'})

    def test_the_claude_md_starts_with_the_agents_md_import(self):
        self.assertTrue((self.root / 'CLAUDE.md').read_text().startswith('@AGENTS.md'))

    def test_the_settings_have_the_hooks_key_and_the_author_env(self):
        settings = json.loads((self.root / '.claude' / 'settings.json').read_text())
        self.assertTrue(settings['hooks'])
        self.assertIn('Stop', settings['hooks'])
        env = settings['env']
        self.assertEqual((env['GIT_AUTHOR_NAME'], env['GIT_COMMITTER_NAME']), ('Ana Souza', 'Ana Souza'))
        self.assertEqual((env['GIT_AUTHOR_EMAIL'], env['GIT_COMMITTER_EMAIL']), ('77+anasouza@users.noreply.github.com',) * 2)

    def test_the_manifest_is_valid_and_dated_today(self):
        manifest = brza_root.read_manifest(self.root)
        self.assertEqual(brza_root.validate_manifest(manifest), [])
        self.assertEqual(manifest['workspace'], {'name': 'acme-notes', 'title': 'acme-notes', 'owner': 'Ana Souza',
                                                 'branch': 'master', 'remote': 'origin', 'born': '2026-11-01'})
        self.assertEqual(manifest['author'], {'name': 'Ana Souza', 'email': '77+anasouza@users.noreply.github.com',
                                              'login': 'anasouza'})
        self.assertEqual(manifest['code']['name'], 'acme-app')
        self.assertEqual(manifest['code']['github'], 'acme-org/acme-app')
        self.assertEqual(manifest['code']['path'], str(self.code))
        self.assertEqual(manifest['core'], {'ref': 'master', 'packages': []})
        self.assertEqual(manifest['checker'], {'worklogs_link_from': '2026-11-01'})

    def test_the_index_is_empty_and_the_lock_follows_the_flow_commit(self):
        self.assertEqual((self.root / '.brza' / 'index.jsonl').read_bytes(), b'')
        lock = brza_root.read_lock(self.root)
        self.assertEqual(lock['commit'], self.flow_sha)
        self.assertIn('AGENTS.md', lock['files'])
        self.assertIn('.claude/settings.json#hooks', lock['files'])

    def test_it_commits_locally_with_the_author_and_does_not_push(self):
        self.assertEqual(git(self.root, 'log', '--format=%s'), 'chore: brza init (brza-workflow@%s)' % self.flow_sha[:7])
        self.assertEqual(git(self.root, 'log', '--format=%an <%ae>'),
                         'Ana Souza <77+anasouza@users.noreply.github.com>')
        self.assertEqual(git(self.root, 'rev-list', '--count', 'HEAD'), '1')
        self.assertEqual(git(self.root, 'status', '--porcelain'), '')
        self.assertEqual(git(self.root, 'branch', '--show-current'), 'master')
        self.assertEqual(git(self.root, 'remote'), '')
        self.assertEqual(self.result.committed, git(self.root, 'rev-parse', 'HEAD'))

    def test_the_notes_checker_runs_green(self):
        done = self.checker()
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertIn('erros: 0', done.stdout)

    def test_nothing_of_the_origin_workspace_is_left_in_what_was_written(self):
        found = {}
        for path in files_of(self.root):
            text = (self.root / path).read_text(encoding='utf-8', errors='ignore')
            text = text.replace(str(self.base), '/dummy/base')
            for term in FOREIGN + APP_TERMS:
                if term in text:
                    found.setdefault(path, []).append(term)
        self.assertEqual(found, {})
        self.assertFalse([p for p in files_of(self.root) if p.endswith('.md')
                          and '{{workspace.' in (self.root / p).read_text()])

    def run_note(self, *argv):
        """O `bin/brza-note` por processo, no workspace criado: o `new` e o `commit` dele são o que o aceite exercita."""
        env = {**os.environ, **IDENTITY, 'BRZA_WORKSPACE': str(self.root), 'PYTHONDONTWRITEBYTECODE': '1'}
        done = subprocess.run([sys.executable, str(BIN / 'brza-note'), *argv], capture_output=True, text=True, env=env,
                              cwd=str(self.root))
        return done.returncode, done.stdout + done.stderr

    def test_an_issue_made_by_brza_note_new_passes_the_checker_and_the_commit(self):
        # o esqueleto do `new` nasce com `description`: sem ele, o checador do núcleo recusa a nota nova (A6 do P9)
        origin = self.base / 'acme-notes.git'
        subprocess.run(['git', 'init', '-q', '--bare', '-b', 'master', str(origin)], check=True)
        git(self.root, 'remote', 'add', 'origin', str(origin))
        git(self.root, 'push', '-q', '-u', 'origin', 'master')
        code, out = self.run_note('workstream', 'app')
        self.assertEqual(code, 0, out)
        code, out = self.run_note('new', 'issue', 'uma-issue', '--workstream', 'app')
        self.assertEqual(code, 0, out)
        issue = self.root / '.worktrees' / 'app' / 'work' / 'issues' / 'uma-issue.md'
        self.assertIn('\ndescription: ', issue.read_text())
        issue.write_text(issue.read_text().replace('# <H1 a escrever>', '# Uma issue de teste'))
        done = self.checker(self.root / '.worktrees' / 'app')
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        code, out = self.run_note('commit', 'work/issues/uma-issue.md', '-m', 'docs: uma issue', '--workstream', 'app')
        self.assertEqual(code, 0, out)
        self.assertNotIn('recusado', out)
        self.assertIn('issue:uma-issue created', out)

    def test_the_readme_is_rendered_and_does_not_copy_the_shell_block(self):
        readme = (self.root / 'README.md').read_text()
        self.assertTrue(readme.startswith('# acme-notes'))
        self.assertIn('acme-app', readme)
        self.assertIn('packages/client/shell/brza.zsh', readme)
        self.assertNotIn('brza_claude()', readme)
        for term in FOREIGN:
            self.assertNotIn(term.lower(), readme.lower())

    def test_it_registers_the_pair_and_prints_the_registry_path(self):
        self.assertEqual(brza_root.read_registry(), [{'root': str(self.root), 'code': str(self.code)}])
        self.assertIn('registro: %s' % self.registry, self.out)

    def test_it_says_the_next_steps(self):
        text = '\n'.join(self.out)
        for step in ('gh repo create anasouza/acme-notes', 'git remote add origin', 'git push -u origin master',
                     'brza note workstream', 'brza.zsh'):
            self.assertIn(step, text)


class RegistryTest(InitBase):
    def test_a_code_already_linked_to_another_workspace_is_refused(self):
        self.init()
        before = self.registry_text()
        other = self.base / 'other-notes'
        with self.assertRaises(InitError) as caught:
            self.init(root=other, name='other-notes')
        self.assertIn(str(self.root), str(caught.exception))
        self.assertFalse(other.exists())
        self.assertEqual(self.registry_text(), before)

    def test_a_worktree_of_the_code_counts_as_the_same_code(self):
        self.init()
        worktree = self.base / 'wt'
        git(self.code, 'worktree', 'add', '-q', '-b', 'x', str(worktree))
        with self.assertRaises(InitError):
            self.init(root=self.base / 'other-notes', name='other', code=worktree)

    def test_a_worktree_of_the_code_is_written_as_its_main_checkout(self):
        worktree = self.base / 'wt'
        git(self.code, 'worktree', 'add', '-q', '-b', 'x', str(worktree))
        self.init(code=worktree)
        self.assertEqual(brza_root.read_manifest(self.root)['code']['path'], str(self.code))
        self.assertEqual(brza_root.read_registry()[0]['code'], str(self.code))

    def test_a_second_workspace_with_another_code_is_added_to_the_registry(self):
        self.init()
        second = make_repo(self.base / 'beta-app')
        self.init(root=self.base / 'beta-notes', name='beta-notes', code=second)
        self.assertEqual([e['root'] for e in brza_root.read_registry()], [str(self.root), str(self.base / 'beta-notes')])

    def test_an_unreadable_registry_is_refused_before_anything_is_written(self):
        self.registry.parent.mkdir(parents=True)
        self.registry.write_text('isto não é toml [[\n')
        with self.assertRaises(InitError):
            self.init()
        self.assertFalse(self.root.exists())

    def test_the_code_path_uses_the_tilde_when_it_is_under_home(self):
        code = make_repo(self.home / 'Projects' / 'acme-app')
        self.init(code=code)
        self.assertEqual(brza_root.read_manifest(self.root)['code']['path'], '~/Projects/acme-app')
        self.assertEqual(brza_root.read_registry()[0]['code'], str(code))


class AuthorTest(InitBase):
    def manifest(self):
        return brza_root.read_manifest(self.root)['author']

    def test_the_login_is_derived_from_the_noreply(self):
        self.init(author_name='Bia', author_email='240569289+bia-dev@users.noreply.github.com')
        self.assertEqual(self.manifest()['login'], 'bia-dev')

    def test_the_login_of_a_noreply_without_the_id(self):
        self.init(author_name='Bia', author_email='bia-dev@users.noreply.github.com')
        self.assertEqual(self.manifest()['login'], 'bia-dev')

    def test_the_login_flag_wins_over_the_noreply(self):
        self.init(author_name='Bia', author_email='1+bia@users.noreply.github.com', author_login='outra')
        self.assertEqual(self.manifest()['login'], 'outra')

    def test_an_email_that_is_not_a_noreply_needs_the_login(self):
        with self.assertRaises(InitError) as caught:
            self.init(author_name='Bia', author_email='bia@example.com')
        self.assertIn('--author-login', str(caught.exception))
        self.assertFalse(self.root.exists())
        self.init(author_name='Bia', author_email='bia@example.com', author_login='bia')
        self.assertEqual(self.manifest(), {'name': 'Bia', 'email': 'bia@example.com', 'login': 'bia'})

    def test_the_default_is_the_global_git_config_and_each_flag_wins_alone(self):
        self.init(author_name='Bia')
        self.assertEqual(self.manifest()['name'], 'Bia')
        self.assertEqual(self.manifest()['email'], '77+anasouza@users.noreply.github.com')

    def test_without_flags_and_without_global_config_it_refuses(self):
        self.gitconfig.write_text('')
        with self.assertRaises(InitError) as caught:
            self.init()
        self.assertIn('--author-name', str(caught.exception))
        self.assertFalse(self.root.exists())
        self.assertEqual(self.registry_text(), '')

    def test_owner_and_title_default_to_the_author_and_the_name(self):
        self.init()
        ws = brza_root.read_manifest(self.root)['workspace']
        self.assertEqual((ws['owner'], ws['title']), ('Ana Souza', 'acme-notes'))
        other = self.base / 'b-notes'
        self.init(root=other, code=make_repo(self.base / 'b-app'), title='Projeto B', owner='Bruno')
        ws = brza_root.read_manifest(other)['workspace']
        self.assertEqual((ws['owner'], ws['title']), ('Bruno', 'Projeto B'))
        self.assertIn('Projeto B', (other / 'README.md').read_text())

    def test_the_commit_identity_comes_from_the_manifest_and_not_from_the_environment(self):
        with patch.dict(os.environ, IDENTITY):
            self.init()
        self.assertEqual(git(self.root, 'log', '--format=%an', env={'GIT_AUTHOR_NAME': 'x'}), 'Ana Souza')


class RefusalTest(InitBase):
    def test_a_directory_that_is_not_empty_is_refused_and_left_alone(self):
        self.root.mkdir()
        (self.root / 'notas.txt').write_text('minha nota\n')
        with self.assertRaises(InitError) as caught:
            self.init()
        self.assertIn('não está vazio', str(caught.exception))
        self.assertEqual(files_of(self.root), ['notas.txt'])
        self.assertFalse((self.root / '.git').exists())
        self.assertEqual(self.registry_text(), '')

    def test_a_file_in_place_of_the_directory_is_refused(self):
        self.root.write_text('x')
        with self.assertRaises(InitError):
            self.init()

    def test_an_existing_empty_directory_is_used(self):
        self.root.mkdir()
        self.init()
        self.assertTrue((self.root / 'AGENTS.md').is_file())

    def test_a_missing_parent_is_created(self):
        self.init(root=self.base / 'a' / 'b' / 'notes')
        self.assertTrue((self.base / 'a' / 'b' / 'notes' / 'AGENTS.md').is_file())

    def test_a_code_that_is_not_a_git_repository_is_refused(self):
        plain = self.base / 'plain'
        plain.mkdir()
        for bad in (plain, self.base / 'nao-existe'):
            with self.assertRaises(InitError) as caught:
                self.init(code=bad)
            self.assertIn('repositório git', str(caught.exception))
        self.assertFalse(self.root.exists())

    def test_a_package_that_the_flow_does_not_have_leaves_nothing_behind(self):
        with self.assertRaises(InitError) as caught:
            self.init(packages=['nope'])
        self.assertIn('nope', str(caught.exception))
        self.assertFalse(self.root.exists())
        self.assertEqual(self.registry_text(), '')

    def test_a_failure_in_an_existing_empty_directory_empties_it_again(self):
        self.root.mkdir()
        with self.assertRaises(InitError):
            self.init(packages=['nope'])
        self.assertEqual(list(self.root.iterdir()), [])

    def test_a_flow_without_templates_is_refused(self):
        empty = self.base / 'empty-flow'
        make_repo(empty)
        with patch.dict(os.environ, {'BRZA_WORKFLOW': str(empty)}):
            with self.assertRaises(InitError) as caught:
                self.init()
        self.assertIn('templates', str(caught.exception))
        self.assertFalse(self.root.exists())


class ManifestChoicesTest(InitBase):
    def test_the_branch_and_the_remote_flags_reach_git_and_the_manifest(self):
        _, out = self.init(branch='main', remote='upstream')
        self.assertEqual(git(self.root, 'branch', '--show-current'), 'main')
        ws = brza_root.read_manifest(self.root)['workspace']
        self.assertEqual((ws['branch'], ws['remote']), ('main', 'upstream'))
        self.assertIn('git push -u upstream main', '\n'.join(out))

    def test_the_code_branch_is_read_from_the_code_repository(self):
        code = make_repo(self.base / 'trunk-app', branch='trunk')
        self.init(code=code)
        manifest = brza_root.read_manifest(self.root)
        self.assertEqual((manifest['code']['branch'], manifest['code']['remote']), ('trunk', 'origin'))

    def test_the_code_branch_flag_wins(self):
        self.init(code_branch='develop')
        self.assertEqual(brza_root.read_manifest(self.root)['code']['branch'], 'develop')

    def test_the_core_ref_is_the_branch_of_the_flow_checkout(self):
        flow = self.base / 'flow-on-a-branch'
        shutil.copytree(self.flow, flow)
        git(flow, 'switch', '-q', '-c', 'feat/x')
        with patch.dict(os.environ, {'BRZA_WORKFLOW': str(flow)}):
            self.init()
        self.assertEqual(brza_root.read_manifest(self.root)['core']['ref'], 'feat/x')
        self.assertEqual(brza_root.read_lock(self.root)['ref'], 'feat/x')

    def test_the_github_comes_from_the_origin_of_the_code_and_falls_back_to_the_author_login(self):
        for name, url, want in (('web1', 'https://github.com/acme-org/web.git', 'acme-org/web'),
                                ('web2', 'git@personal:acme-org/web.git', 'acme-org/web'),
                                ('bare-app', None, 'anasouza/bare-app')):
            with self.subTest(name=name):
                root = self.base / (name + '-notes')
                self.init(root=root, code=make_repo(self.base / name, url=url))
                self.assertEqual(brza_root.read_manifest(root)['code']['github'], want)

    def test_the_github_flag_wins(self):
        self.init(code_github='quem/onde')
        self.assertEqual(brza_root.read_manifest(self.root)['code']['github'], 'quem/onde')

    def test_the_client_package_is_opt_in(self):
        self.init(packages=['client'])
        self.assertEqual(brza_root.read_manifest(self.root)['core']['packages'], ['client'])
        self.assertTrue((self.root / '.claude' / 'rules' / 'brza-client.md').is_file())
        self.assertIn('.claude/rules/brza-client.md', brza_root.read_lock(self.root)['files'])
        done = self.checker()
        self.assertEqual(done.returncode, 0, done.stdout)


class DocstringTest(unittest.TestCase):
    def test_the_sync_and_the_init_say_what_runs_on_the_python_3_9_and_what_needs_the_3_10(self):
        # A11 do P9 e o X6: só os hooks e o brza_root têm de rodar no 3.9; o CLI exige o 3.10
        sync = runpy.run_path(str(BIN / 'brza-sync'))
        for doc in (INIT['__doc__'], sync['__doc__']):
            self.assertIn('3.10', doc)
            self.assertIn('X6', doc)
            self.assertNotIn('Roda no Python 3.9', doc)


class CheckTest(InitBase):
    def flow_with(self, name, rel, extra):
        """Um brza-workflow de mentira com `extra` no fim de `rel`, e o BRZA_WORKFLOW apontando para ele."""
        flow = self.base / name
        shutil.copytree(self.flow, flow)
        target = flow / rel
        target.write_text(target.read_text() + extra)
        git(flow, 'add', '-A')
        git(flow, 'commit', '-q', '-m', name)
        return patch.dict(os.environ, {'BRZA_WORKFLOW': str(flow)})

    def test_the_text_of_the_app_in_the_core_or_the_uid_of_this_machine_fails_the_check(self):
        for extra, term in (('\nA skill orca-chat-visuals.\n', 'orca'), ('\nUm ORCA_CHAT_SCRATCH_DIR.\n', 'ORCA'),
                            ('\nEm /private/tmp/claude-501/.\n', 'claude-501')):
            with self.subTest(term=term), self.flow_with('app-flow', 'core/AGENTS.md', extra):
                with self.assertRaises(INIT['CheckError']) as caught:
                    self.init()
                self.assertIn('AGENTS.md', str(caught.exception))
                self.assertIn(term, str(caught.exception))
                shutil.rmtree(self.root)
                shutil.rmtree(self.base / 'app-flow')

    def test_the_client_package_may_name_the_app_and_the_core_may_not(self):
        rules = ROOT / 'packages' / 'client' / 'core' / '.claude' / 'rules' / 'brza-client.md'
        self.assertIn('orca', rules.read_text())  # o pacote traz o texto do app
        self.init(packages=['client'])  # e a conferência o deixa passar
        self.assertIn('orca', (self.root / '.claude' / 'rules' / 'brza-client.md').read_text())


    def test_a_term_of_the_origin_in_the_written_files_fails_the_check_and_skips_commit_and_registry(self):
        flow = self.base / 'dirty-flow'
        shutil.copytree(self.flow, flow)
        agents = flow / 'core' / 'AGENTS.md'
        agents.write_text(agents.read_text() + '\nVeja o brza-workspace.\n')
        git(flow, 'add', '-A')
        git(flow, 'commit', '-q', '-m', 'sujo')
        with patch.dict(os.environ, {'BRZA_WORKFLOW': str(flow)}):
            with self.assertRaises(InitError) as caught:
                self.init()
        self.assertIn('AGENTS.md', str(caught.exception))
        self.assertIn('brza-workspace', str(caught.exception))
        self.assertTrue((self.root / 'AGENTS.md').is_file())  # o diretório fica, para olhar
        self.assertEqual(git(self.root, 'rev-list', '--all', '--count'), '0')
        self.assertEqual(self.registry_text(), '')

    def test_a_term_that_comes_from_the_manifest_is_not_foreign(self):
        flow = self.base / 'own-flow'
        shutil.copytree(self.flow, flow)
        agents = flow / 'core' / 'AGENTS.md'
        agents.write_text(agents.read_text() + '\nOwner: {{workspace.owner}}\n')
        git(flow, 'add', '-A')
        git(flow, 'commit', '-q', '-m', 'owner')
        with patch.dict(os.environ, {'BRZA_WORKFLOW': str(flow)}):
            self.init(owner='Anderson')
        self.assertIn('Owner: Anderson', (self.root / 'AGENTS.md').read_text())

    def test_a_notes_checker_that_fails_fails_the_init(self):
        flow = self.base / 'broken-flow'
        shutil.copytree(self.flow, flow)
        script = flow / 'core' / '.claude' / 'skills' / 'notes-checker' / 'scripts' / 'check_notes.py'
        script.write_text('import sys\nprint("ERRO falso")\nsys.exit(1)\n')
        git(flow, 'add', '-A')
        git(flow, 'commit', '-q', '-m', 'quebrado')
        with patch.dict(os.environ, {'BRZA_WORKFLOW': str(flow)}):
            with self.assertRaises(InitError) as caught:
                self.init()
        self.assertIn('check_notes', str(caught.exception))
        self.assertEqual(self.registry_text(), '')


class ForeignFileTest(InitBase):
    """Os nomes proibidos que não ficam no código vêm de um arquivo da máquina, fora do git (D11 do PLN-008): um nome
    por linha, com `#` de comentário. Sem o arquivo, a guarda segue só com os nomes do código."""

    flow_with = CheckTest.flow_with

    def machine_file(self, text):
        path = self.base / 'config' / 'brza' / 'foreign.txt'
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
        return patch.dict(os.environ, {'BRZA_FOREIGN': str(path)})

    def test_a_name_from_the_machine_file_in_the_written_files_fails_the_check(self):
        with self.machine_file('# os nomes desta máquina\n\nAcme Corp\n  Globex  \n'), \
                self.flow_with('named-flow', 'core/AGENTS.md', '\nVeja a Globex.\n'):
            with self.assertRaises(INIT['CheckError']) as caught:
                self.init()
        self.assertIn('AGENTS.md', str(caught.exception))
        self.assertIn('Globex', str(caught.exception))
        self.assertEqual(self.registry_text(), '')

    def test_the_names_of_the_code_still_count_with_the_machine_file(self):
        with self.machine_file('Acme Corp\n'), self.flow_with('dirty-flow', 'core/AGENTS.md', '\nVeja o brza-studio.\n'):
            with self.assertRaises(INIT['CheckError']) as caught:
                self.init()
        self.assertIn('brza-studio', str(caught.exception))

    def test_without_the_machine_file_nothing_breaks(self):
        self.assertFalse(Path(os.environ['BRZA_FOREIGN']).exists())
        with self.flow_with('named-flow', 'core/AGENTS.md', '\nVeja a Globex.\n'):
            result, out = self.init()
        self.assertTrue(result.committed)
        self.assertIn('Veja a Globex.', (self.root / 'AGENTS.md').read_text())
        self.assertEqual(INIT['foreign_names'](), list(INIT['FOREIGN']))

    def test_comments_and_blank_lines_are_not_names(self):
        with self.machine_file('# Globex\n\n   \nAcme Corp # o cliente\nInitech\n'):
            names = INIT['foreign_names']()
        self.assertEqual(names, list(INIT['FOREIGN']) + ['Acme Corp', 'Initech'])

    def test_the_file_lives_beside_the_registry(self):
        env = {k: v for k, v in os.environ.items() if k != 'BRZA_FOREIGN'}
        with patch.dict(os.environ, env, clear=True):
            with patch.dict(os.environ, {'XDG_CONFIG_HOME': str(self.base / 'xdg')}):
                self.assertEqual(INIT['foreign_path'](), self.base / 'xdg' / 'brza' / 'foreign.txt')
            os.environ.pop('XDG_CONFIG_HOME', None)
            self.assertEqual(INIT['foreign_path'](), self.home / '.config' / 'brza' / 'foreign.txt')

    def test_an_unreadable_machine_file_is_refused_before_anything_is_written(self):
        folder = self.base / 'config' / 'brza' / 'foreign.txt'
        folder.mkdir(parents=True)
        with patch.dict(os.environ, {'BRZA_FOREIGN': str(folder)}):
            with self.assertRaises(InitError) as caught:
                self.init()
        self.assertIn(str(folder), str(caught.exception))
        self.assertFalse(self.root.exists())
        self.assertEqual(self.registry_text(), '')


class DryRunTest(InitBase):
    def test_nothing_is_written_not_even_the_registry(self):
        result, out = self.init(dry_run=True)
        self.assertFalse(self.root.exists())
        self.assertEqual(self.registry_text(), '')
        self.assertEqual(result.committed, None)
        text = '\n'.join(out)
        self.assertIn('dry-run', text)
        for path in ('AGENTS.md', '.brza/workspace.toml', '.claude/settings.json'):
            self.assertIn(path, text)

    def test_it_still_refuses_what_the_real_run_refuses(self):
        self.root.mkdir()
        (self.root / 'x').write_text('x')
        with self.assertRaises(InitError):
            self.init(dry_run=True)

    def test_the_dry_run_leaves_no_temporary_directory_of_its_own(self):
        with patch.object(tempfile, 'tempdir', str(self.base)):
            before = set(os.listdir(self.base))
            self.init(dry_run=True)
            self.assertEqual(set(os.listdir(self.base)), before)


class CommandTest(InitBase):
    def run_init(self, *argv, cwd=None):
        env = {k: v for k, v in os.environ.items() if k not in ('BRZA_WORKSPACE', 'XDG_CONFIG_HOME')}
        return subprocess.run([sys.executable, str(BIN / 'brza-init'), *map(str, argv)], capture_output=True,
                              text=True, cwd=str(cwd or self.base), env=env)

    def test_the_process_creates_a_workspace_whose_checker_is_green(self):
        done = self.run_init(self.root, '--name', 'acme-notes', '--code', self.code)
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertIn('registro: %s' % self.registry, done.stdout)
        check = self.checker()
        self.assertEqual(check.returncode, 0, check.stdout)
        self.assertEqual(git(self.root, 'status', '--porcelain'), '')

    def test_a_refusal_exits_with_1_and_says_why(self):
        self.root.mkdir()
        (self.root / 'x').write_text('x')
        done = self.run_init(self.root, '--name', 'acme-notes', '--code', self.code)
        self.assertEqual(done.returncode, 1)
        self.assertIn('recusado:', done.stdout)
        self.assertIn('não está vazio', done.stdout)

    def test_name_and_code_are_required(self):
        done = self.run_init(self.root)
        self.assertEqual(done.returncode, 2)
        self.assertIn('--name', done.stderr)

    def test_the_package_flag_repeats_and_dry_run_is_a_flag(self):
        code, out = self.run_main(self.root, '--name', 'n', '--code', self.code, '--package', 'client', '--dry-run',
                                  '--author-login', 'ana')
        self.assertEqual(code, 0, out)
        self.assertIn('dry-run', out)
        self.assertFalse(self.root.exists())

    def test_the_tilde_in_the_directory_is_expanded(self):
        code, out = self.run_main('~/notes-home', '--name', 'n', '--code', self.code)
        self.assertEqual(code, 0, out)
        self.assertTrue((self.home / 'notes-home' / 'AGENTS.md').is_file())


if __name__ == '__main__':
    unittest.main()
