"""Testes da camada 1 do `brza sync` (PLN-006/P6), com repositórios temporários: um brza-workflow de mentira, com
`core/`, um pacote e uma origin, e um workspace de mentira, com manifesto, origin e a parte local.

    python3 -m unittest discover -s tests/cli
"""
import contextlib
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
os.environ['BRZA_REGISTRY'] = '/nonexistent/brza/workspaces.toml'  # nada aqui lê o registro real da máquina
sys.path.insert(0, str(ROOT / 'core' / '.claude' / 'hooks'))
import brza_root  # noqa: E402

SYNC = runpy.run_path(str(BIN / 'brza-sync'))
sync_core, SyncError, main = SYNC['sync_core'], SYNC['SyncError'], SYNC['main']

MANIFEST = {
    'schema': 1,
    'workspace': {'name': 'acme-notes', 'title': 'Acme', 'owner': 'Ana', 'born': '2026-10-26'},
    'author': {'name': 'Ana Souza', 'email': 'ana@example.com', 'login': 'ana'},
    'code': {'name': 'acme-app', 'path': '~/Projects/acme-app', 'github': 'acme/acme-app'},
    'core': {'ref': 'master', 'packages': ['client']},
}
HOOKS = {'hooks': {'Stop': [{'hooks': [{'type': 'command', 'command': 'measure.py turn || true'}]}]}}
CORE = {
    'core/AGENTS.md': '# {{workspace.name}}\n\nQuem decide: {{workspace.owner}}.\n',
    'core/CLAUDE.md': '@AGENTS.md\n',
    'core/.gitignore': '.worktrees/\n',
    'core/.gitattributes': '.brza/index.jsonl merge=union\n',
    'core/.claude/hooks/measure.py': 'print("mede {{workspace.name}}")\n',
    'core/.claude/skills/plan-runner/SKILL.md': 'Plano de {{author.login}}.\n',
    'core/.claude/settings.hooks.json': json.dumps(HOOKS, indent=2) + '\n',
    'packages/client/core/.claude/rules/brza-client.md': 'Cliente de {{workspace.name}}.\n',
    'packages/client/bin/brza-run': '#!/bin/sh\n',
}
LOCAL = {
    'README.md': 'o meu README\n',
    '.claude/rules/workspace.md': 'regras locais\n',
    '.brza/index.jsonl': '{"id":"PLN-001"}\n',
    '.claude/settings.json': json.dumps({'env': {'A': '1'}, 'hooks': {}}, indent=2) + '\n',
}


def git(*args, cwd: Path) -> str:
    return subprocess.run(['git', '-C', str(cwd), *args], check=True, capture_output=True, text=True).stdout.strip()


def write(root: Path, files: dict):
    for path, text in files.items():
        target = root / path
        if text is None:
            target.unlink()
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text)


class SyncTest(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.base = Path(temp.name).resolve()
        env = {'GIT_AUTHOR_NAME': 't', 'GIT_AUTHOR_EMAIL': 't@t', 'GIT_COMMITTER_NAME': 't',
               'GIT_COMMITTER_EMAIL': 't@t', 'BRZA_REGISTRY': '/nonexistent/brza/workspaces.toml'}
        patcher = patch.dict(os.environ, env)
        patcher.start()
        self.addCleanup(patcher.stop)
        os.environ.pop('BRZA_WORKSPACE', None)
        # o brza-workflow de mentira, com a origin dele
        self.flow_origin, self.flow = self.base / 'flow-origin.git', self.base / 'flow'
        subprocess.run(['git', 'init', '-q', '--bare', '-b', 'master', str(self.flow_origin)], check=True)
        subprocess.run(['git', 'clone', '-q', str(self.flow_origin), str(self.flow)], check=True, capture_output=True)
        self.flow_commit(CORE, 'núcleo')
        # o workspace de mentira, com a origin dele e a parte local
        self.ws_origin, self.ws = self.base / 'ws-origin.git', self.base / 'ws'
        subprocess.run(['git', 'init', '-q', '--bare', '-b', 'master', str(self.ws_origin)], check=True)
        subprocess.run(['git', 'clone', '-q', str(self.ws_origin), str(self.ws)], check=True, capture_output=True)
        write(self.ws, {**LOCAL, brza_root.MANIFEST: brza_root.dump_manifest(MANIFEST)})
        git('add', '.', cwd=self.ws)
        git('commit', '-q', '-m', 'init', cwd=self.ws)
        git('push', '-q', 'origin', 'HEAD:master', cwd=self.ws)
        git('branch', '-q', '--set-upstream-to=origin/master', cwd=self.ws)

    def flow_commit(self, files: dict, message: str, push: bool = True) -> str:
        write(self.flow, files)
        git('add', '-A', cwd=self.flow)
        git('commit', '-q', '-m', message, cwd=self.flow)
        if push:
            git('push', '-q', 'origin', 'HEAD:master', cwd=self.flow)
        return git('rev-parse', 'HEAD', cwd=self.flow)

    def sync(self, **kw):
        lines = []
        kw.setdefault('today', '2026-11-03')
        result = sync_core(self.ws, flow=self.flow, out=lines.append, **kw)
        return result, '\n'.join(lines)

    def read(self, path: str, root: Path | None = None) -> str:
        return ((root or self.ws) / path).read_text()

    def lock(self) -> dict:
        return brza_root.read_lock(self.ws)

    def commit_local(self, files: dict, message: str = 'local'):
        write(self.ws, files)
        git('add', '-A', cwd=self.ws)
        git('commit', '-q', '-m', message, cwd=self.ws)

    def head_files(self) -> list:
        return git('show', '--name-only', '--format=', 'HEAD', cwd=self.ws).splitlines()

    def snapshot(self) -> dict:
        found = {}
        for directory, dirs, files in os.walk(self.ws):
            dirs[:] = [d for d in dirs if d != '.git']
            for name in files:
                path = Path(directory) / name
                found[str(path.relative_to(self.ws))] = path.read_bytes()
        return found

    # o primeiro sync

    def test_first_sync_renders_copies_merges_hooks_writes_the_lock_and_commits(self):
        result, out = self.sync()
        self.assertEqual(result.code, 0, out)
        self.assertEqual(self.read('AGENTS.md'), '# acme-notes\n\nQuem decide: Ana.\n')
        self.assertEqual(self.read('.claude/hooks/measure.py'), 'print("mede {{workspace.name}}")\n')  # .py é cópia
        self.assertEqual(self.read('.claude/skills/plan-runner/SKILL.md'), 'Plano de ana.\n')
        self.assertEqual(self.read('.claude/rules/brza-client.md'), 'Cliente de acme-notes.\n')  # o pacote entra
        self.assertFalse((self.ws / 'bin').exists())  # só o core/ do pacote, não o bin/
        self.assertFalse((self.ws / '.claude' / 'settings.hooks.json').exists())
        settings = json.loads(self.read('.claude/settings.json'))
        self.assertEqual(settings, {'env': {'A': '1'}, **HOOKS})
        lock = self.lock()
        self.assertEqual(lock['commit'], git('rev-parse', 'HEAD', cwd=self.flow))
        self.assertEqual((lock['ref'], lock['synced']), ('master', '2026-11-03'))
        self.assertEqual(lock['files']['AGENTS.md'], brza_root.digest('# acme-notes\n\nQuem decide: Ana.\n'))
        self.assertEqual(lock['files']['.claude/settings.json#hooks'],
                         brza_root.hooks_digest(HOOKS['hooks']))
        self.assertNotIn('.claude/settings.hooks.json', lock['files'])
        short = git('rev-parse', '--short=7', 'HEAD', cwd=self.flow)
        self.assertEqual(git('log', '-1', '--format=%s', cwd=self.ws), f'chore(norma): brza-workflow@{short}')
        self.assertEqual(git('rev-parse', 'HEAD', cwd=self.ws_origin), git('rev-parse', 'HEAD', cwd=self.ws))
        self.assertEqual(git('status', '--porcelain', cwd=self.ws), '')
        self.assertEqual(result.committed, git('rev-parse', 'HEAD', cwd=self.ws))

    def test_the_sync_never_touches_the_local_part(self):
        self.sync()
        for path, text in LOCAL.items():
            if path != '.claude/settings.json':
                self.assertEqual(self.read(path), text)
        self.assertEqual(self.read(brza_root.MANIFEST), brza_root.dump_manifest(MANIFEST))
        self.assertTrue(set(self.head_files()).isdisjoint({'README.md', '.claude/rules/workspace.md',
                                                          '.brza/index.jsonl', brza_root.MANIFEST}))

    def test_a_change_in_the_core_arrives_without_touching_the_local_part(self):  # R1.4
        self.sync()
        self.flow_commit({'core/AGENTS.md': '# {{workspace.name}}\n\nNova regra de {{workspace.owner}}.\n',
                          'core/scripts/novo.py': 'print(1)\n'}, 'muda a norma')
        write(self.ws, {'README.md': 'README local, editado depois\n'})
        git('commit', '-q', '-am', 'local', cwd=self.ws)
        result, out = self.sync()
        self.assertEqual(result.code, 0, out)
        self.assertEqual(self.read('AGENTS.md'), '# acme-notes\n\nNova regra de Ana.\n')
        self.assertEqual(self.read('scripts/novo.py'), 'print(1)\n')
        self.assertEqual(self.read('README.md'), 'README local, editado depois\n')
        self.assertEqual(sorted(self.head_files()), ['.brza/core.lock', 'AGENTS.md', 'scripts/novo.py'])
        self.assertEqual(self.lock()['commit'], git('rev-parse', 'HEAD', cwd=self.flow))
        self.assertEqual(git('status', '--porcelain', cwd=self.ws), '')

    def test_in_sync_writes_and_commits_nothing(self):
        self.sync()
        head = git('rev-parse', 'HEAD', cwd=self.ws)
        result, out = self.sync()
        self.assertEqual(result.code, 0)
        self.assertIn('em dia', out)
        self.assertIsNone(result.committed)
        self.assertEqual(git('rev-parse', 'HEAD', cwd=self.ws), head)

    # o commit de origem sem arquivo do núcleo (A4)

    def flow_changes_outside_the_core(self):
        """O primeiro sync, e depois um commit do brza-workflow que só mexe fora do core/ e dos pacotes."""
        self.sync()
        self.head, self.locked = git('rev-parse', 'HEAD', cwd=self.ws), self.lock()['commit']
        return self.flow_commit({'README.md': 'só o README do brza-workflow\n'}, 'doc: README')

    def test_a_flow_commit_outside_the_core_does_not_rewrite_the_lock_or_commit(self):
        new = self.flow_changes_outside_the_core()
        self.assertNotEqual(new, self.locked)
        result, out = self.sync()
        self.assertEqual(result.code, 0, out)
        self.assertIn('em dia', out)
        self.assertIn('o lock segue em brza-workflow@%s' % self.locked[:7], out)
        self.assertIsNone(result.committed)
        self.assertEqual(git('rev-parse', 'HEAD', cwd=self.ws), self.head)
        self.assertEqual(self.lock()['commit'], self.locked)
        self.assertEqual(git('status', '--porcelain', cwd=self.ws), '')

    def test_dry_run_says_nothing_to_write_and_why_the_lock_stays_when_only_the_origin_commit_moved(self):
        self.flow_changes_outside_the_core()
        before = self.snapshot()
        result, out = self.sync(dry_run=True)
        self.assertEqual(result.code, 0, out)
        self.assertNotIn('escreveria', out)
        self.assertNotIn('apagaria', out)
        self.assertIn('o lock segue em brza-workflow@%s' % self.locked[:7], out)
        self.assertIn('dry-run: nada foi escrito', out)
        self.assertEqual(result.written, [])
        self.assertEqual(self.snapshot(), before)

    def test_dry_run_and_real_agree_when_the_core_did_change(self):
        self.flow_changes_outside_the_core()
        self.flow_commit({'core/AGENTS.md': '# {{workspace.name}}\n\nOutra regra.\n'}, 'muda a norma')
        _, dry = self.sync(dry_run=True)
        self.assertIn('escreveria: AGENTS.md', dry)
        self.assertNotIn('o lock segue', dry)
        result, out = self.sync()
        self.assertEqual(sorted(self.head_files()), ['.brza/core.lock', 'AGENTS.md'])
        self.assertEqual(self.lock()['commit'], git('rev-parse', 'HEAD', cwd=self.flow))
        self.assertNotIn('o lock segue', out)

    def test_the_commit_only_carries_the_synced_paths_even_with_another_file_staged(self):
        write(self.ws, {'work/nota.md': 'x\n'})
        git('add', 'work/nota.md', cwd=self.ws)
        self.sync()
        self.assertNotIn('work/nota.md', self.head_files())
        self.assertEqual(git('diff', '--cached', '--name-only', cwd=self.ws), 'work/nota.md')

    # o arquivo editado (R6.2)

    def edited(self):
        """O primeiro sync, e depois o AGENTS.md editado no workspace e commitado, com a norma andando em dois arquivos."""
        self.sync()
        self.commit_local({'AGENTS.md': '# acme-notes\n\nEditado aqui.\n'}, 'edita a norma')
        self.flow_commit({'core/AGENTS.md': '# {{workspace.name}}\n\nRegra nova do núcleo.\n',
                          'core/CLAUDE.md': '@AGENTS.md\nmais\n'}, 'muda a norma')

    def test_an_edited_file_is_refused_with_the_diff_and_exit_1(self):  # R6.2
        self.edited()
        locked = self.lock()['files']['AGENTS.md']
        result, out = self.sync()
        self.assertEqual(result.code, 1, out)
        self.assertEqual(result.refused, ['AGENTS.md'])
        self.assertEqual(self.read('AGENTS.md'), '# acme-notes\n\nEditado aqui.\n')
        self.assertIn('recusado: AGENTS.md', out)
        self.assertIn('-Editado aqui.', out)
        self.assertIn('+Regra nova do núcleo.', out)
        self.assertIn('--force AGENTS.md', out)
        # o resto andou, e o lock guarda o sha do que foi escrito, não o do refugado
        self.assertEqual(self.read('CLAUDE.md'), '@AGENTS.md\nmais\n')
        self.assertEqual(self.lock()['files']['AGENTS.md'], locked)
        self.assertEqual(sorted(self.head_files()), ['.brza/core.lock', 'CLAUDE.md'])

    def test_a_refused_file_keeps_being_refused_in_the_next_sync(self):
        self.edited()
        self.sync()
        result, out = self.sync()
        self.assertEqual(result.code, 1, out)
        self.assertEqual(self.read('AGENTS.md'), '# acme-notes\n\nEditado aqui.\n')

    def test_force_writes_the_edited_file_and_updates_the_lock(self):  # R6.2
        self.edited()
        result, out = self.sync(force=['AGENTS.md'])
        self.assertEqual(result.code, 0, out)
        self.assertEqual(self.read('AGENTS.md'), '# acme-notes\n\nRegra nova do núcleo.\n')
        self.assertEqual(self.lock()['files']['AGENTS.md'],
                         brza_root.digest('# acme-notes\n\nRegra nova do núcleo.\n'))
        self.assertIn('AGENTS.md', self.head_files())
        result, out = self.sync()
        self.assertEqual((result.code, result.committed), (0, None), out)

    def test_force_of_one_path_does_not_force_another(self):
        self.edited()
        self.commit_local({'CLAUDE.md': 'editado também\n'})
        self.flow_commit({'core/CLAUDE.md': '@AGENTS.md\nmais ainda\n'}, 'de novo')
        result, out = self.sync(force=['AGENTS.md'])
        self.assertEqual(result.refused, ['CLAUDE.md'], out)
        self.assertEqual(self.read('CLAUDE.md'), 'editado também\n')
        self.assertIn('Regra nova', self.read('AGENTS.md'))

    def test_the_same_edit_as_the_core_is_not_a_conflict(self):
        self.sync()
        self.commit_local({'AGENTS.md': '# acme-notes\n\nIgual ao núcleo novo.\n'})
        self.flow_commit({'core/AGENTS.md': '# {{workspace.name}}\n\nIgual ao núcleo novo.\n'}, 'igual')
        result, out = self.sync()
        self.assertEqual(result.code, 0, out)
        self.assertEqual(self.lock()['files']['AGENTS.md'],
                         brza_root.digest('# acme-notes\n\nIgual ao núcleo novo.\n'))

    def test_the_hooks_edited_in_the_workspace_are_refused_and_force_writes_them(self):
        self.sync()
        mine = {'Stop': [{'hooks': [{'type': 'command', 'command': 'meu-hook'}]}]}
        self.commit_local({'.claude/settings.json': json.dumps({'env': {'A': '1'}, 'hooks': mine})})
        self.flow_commit({'core/.claude/settings.hooks.json': json.dumps(
            {'hooks': {'Stop': [{'hooks': [{'type': 'command', 'command': 'novo-hook'}]}]}})}, 'hooks novos')
        result, out = self.sync()
        self.assertEqual(result.code, 1, out)
        self.assertEqual(result.refused, ['.claude/settings.json#hooks'])
        self.assertIn('+', out)
        self.assertIn('novo-hook', out)
        self.assertEqual(json.loads(self.read('.claude/settings.json'))['hooks'], mine)
        result, out = self.sync(force=['.claude/settings.json#hooks'])
        self.assertEqual(result.code, 0, out)
        self.assertEqual(json.loads(self.read('.claude/settings.json'))['hooks']['Stop'][0]['hooks'][0]['command'],
                         'novo-hook')
        self.assertEqual(json.loads(self.read('.claude/settings.json'))['env'], {'A': '1'})

    def test_the_sync_swaps_only_the_hooks_key_of_the_settings(self):
        self.sync()
        self.flow_commit({'core/.claude/settings.hooks.json': json.dumps(
            {'hooks': {'Stop': [{'hooks': [{'type': 'command', 'command': 'outro'}]}]}})}, 'outro hook')
        self.commit_local({'.claude/settings.json': json.dumps({'env': {'B': '2'}, 'extra': 1, **HOOKS})})
        result, out = self.sync()
        self.assertEqual(result.code, 0, out)
        settings = json.loads(self.read('.claude/settings.json'))
        self.assertEqual((settings['env'], settings['extra']), ({'B': '2'}, 1))
        self.assertEqual(settings['hooks']['Stop'][0]['hooks'][0]['command'], 'outro')

    # o lock quebrado

    def test_a_broken_lock_is_not_treated_as_absent(self):
        self.sync()
        self.commit_local({'AGENTS.md': 'editado\n', brza_root.LOCK: 'isto = não [é toml\n'})
        self.flow_commit({'core/AGENTS.md': 'novo\n'}, 'muda')
        before = self.snapshot()
        with self.assertRaises(SyncError) as ctx:
            self.sync()
        self.assertIn('lock está quebrado', str(ctx.exception))
        self.assertEqual(self.snapshot(), before)  # a edição local não foi sobrescrita

    def test_a_lock_out_of_the_schema_is_not_treated_as_absent(self):
        self.sync()
        self.commit_local({brza_root.LOCK: 'commit = "abc"\n'})
        with self.assertRaises(SyncError) as ctx:
            self.sync()
        self.assertIn('quebrado', str(ctx.exception))

    # o que saiu do núcleo

    def test_a_file_that_left_the_core_is_deleted_when_not_edited(self):
        self.flow_commit({'core/scripts/velho.py': 'velho\n'}, 'um script')
        self.sync()
        self.assertTrue((self.ws / 'scripts' / 'velho.py').exists())
        self.flow_commit({'core/scripts/velho.py': None}, 'o script sai')
        result, out = self.sync()
        self.assertEqual((result.code, result.removed), (0, ['scripts/velho.py']), out)
        self.assertFalse((self.ws / 'scripts').exists())  # a pasta vazia sai junto
        self.assertNotIn('scripts/velho.py', self.lock()['files'])
        self.assertIn('scripts/velho.py', self.head_files())
        self.assertEqual(git('ls-files', 'scripts', cwd=self.ws), '')

    def test_a_file_that_left_the_core_stays_when_edited_and_leaves_the_lock(self):
        self.flow_commit({'core/scripts/velho.py': 'velho\n'}, 'um script')
        self.sync()
        self.commit_local({'scripts/velho.py': 'editado aqui\n'})
        self.flow_commit({'core/scripts/velho.py': None}, 'o script sai')
        result, out = self.sync()
        self.assertEqual((result.code, result.kept, result.removed), (0, ['scripts/velho.py'], []), out)
        self.assertEqual(self.read('scripts/velho.py'), 'editado aqui\n')
        self.assertNotIn('scripts/velho.py', self.lock()['files'])
        self.assertIn('mantido:  scripts/velho.py', out)
        result, out = self.sync()  # agora é local: não volta a aparecer
        self.assertNotIn('velho', out)

    def test_force_deletes_a_file_that_left_the_core_even_edited(self):
        self.flow_commit({'core/scripts/velho.py': 'velho\n'}, 'um script')
        self.sync()
        self.commit_local({'scripts/velho.py': 'editado aqui\n'})
        self.flow_commit({'core/scripts/velho.py': None}, 'o script sai')
        result, out = self.sync(force=['scripts/velho.py'])
        self.assertEqual(result.removed, ['scripts/velho.py'], out)
        self.assertFalse((self.ws / 'scripts').exists())

    def test_the_retired_resume_guard_is_deleted_in_the_first_sync(self):
        self.commit_local({'.claude/hooks/resume_guard.py': 'x\n', '.claude/hooks/tests/test_resume_guard.py': 'y\n',
                           '.claude/hooks/tests/test_outro.py': 'z\n'})
        result, out = self.sync()
        self.assertEqual(sorted(result.removed), ['.claude/hooks/resume_guard.py',
                                                  '.claude/hooks/tests/test_resume_guard.py'], out)
        self.assertFalse((self.ws / '.claude/hooks/resume_guard.py').exists())
        self.assertTrue((self.ws / '.claude/hooks/tests/test_outro.py').exists())  # o resto dos testes não é do sync

    # o --dry-run

    def test_dry_run_writes_nothing_and_shows_what_it_would_do(self):
        self.edited()
        before, head = self.snapshot(), git('rev-parse', 'HEAD', cwd=self.ws)
        self.flow_commit({'core/scripts/novo.py': 'x\n'}, 'novo')
        result, out = self.sync(dry_run=True)
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(git('rev-parse', 'HEAD', cwd=self.ws), head)
        self.assertEqual(git('status', '--porcelain', cwd=self.ws), '')
        self.assertEqual(result.code, 1)  # o editado seria recusado
        self.assertIn('escreveria: scripts/novo.py', out)
        self.assertIn('escreveria: CLAUDE.md', out)
        self.assertIn('+mais', out)  # o diff do que mudaria
        self.assertIn('recusado: AGENTS.md', out)
        self.assertIn('dry-run: nada foi escrito', out)
        self.assertIn('scripts/novo.py', result.written)

    def test_dry_run_in_an_empty_workspace_creates_no_file_and_no_lock(self):
        before = self.snapshot()
        result, out = self.sync(dry_run=True)
        self.assertEqual(result.code, 0, out)
        self.assertEqual(self.snapshot(), before)
        self.assertFalse((self.ws / brza_root.LOCK).exists())
        self.assertEqual(git('log', '--format=%s', cwd=self.ws), 'init')

    def test_dry_run_does_not_pull_the_origin(self):
        other = self.base / 'other'
        subprocess.run(['git', 'clone', '-q', str(self.ws_origin), str(other)], check=True, capture_output=True)
        write(other, {'.brza/index.jsonl': '{"id":"PLN-001"}\n{"id":"PLN-002"}\n'})
        git('commit', '-q', '-am', 'índice', cwd=other)
        git('push', '-q', 'origin', 'HEAD:master', cwd=other)
        self.sync(dry_run=True)
        self.assertEqual(self.read('.brza/index.jsonl'), '{"id":"PLN-001"}\n')

    # as guardas e o pull

    def test_the_sync_refuses_outside_the_workspace_branch(self):
        git('checkout', '-q', '-b', 'workstream/x', cwd=self.ws)
        with self.assertRaises(SyncError) as ctx:
            self.sync()
        self.assertIn('workstream/x', str(ctx.exception))
        self.assertFalse((self.ws / 'AGENTS.md').exists())

    def test_the_sync_follows_the_branch_of_the_manifest(self):
        manifest = {**MANIFEST, 'workspace': {**MANIFEST['workspace'], 'branch': 'main'}}
        git('checkout', '-q', '-b', 'main', cwd=self.ws)
        self.commit_local({brza_root.MANIFEST: brza_root.dump_manifest(manifest)})
        git('push', '-q', '-u', 'origin', 'main', cwd=self.ws)
        result, out = self.sync()
        self.assertEqual(result.code, 0, out)
        self.assertEqual(git('rev-parse', 'origin/main', cwd=self.ws), git('rev-parse', 'HEAD', cwd=self.ws))
        self.assertEqual(git('rev-parse', 'origin/master', cwd=self.ws),
                         git('rev-parse', 'master', cwd=self.ws))  # a master antiga ficou onde estava

    def test_the_sync_refuses_an_uncommitted_change_in_the_core_paths(self):
        self.sync()
        write(self.ws, {'AGENTS.md': 'sujo\n'})
        with self.assertRaises(SyncError) as ctx:
            self.sync()
        self.assertIn('AGENTS.md', str(ctx.exception))
        self.assertEqual(self.read('AGENTS.md'), 'sujo\n')

    def test_an_uncommitted_change_outside_the_core_does_not_block(self):
        write(self.ws, {'README.md': 'sujo\n', 'work/nota.md': 'nova\n'})
        result, out = self.sync()
        self.assertEqual(result.code, 0, out)
        self.assertEqual(self.read('README.md'), 'sujo\n')
        self.assertNotIn('work/nota.md', self.head_files())

    def test_the_pull_brings_the_index_lines_pushed_by_another_session(self):
        other = self.base / 'other'
        subprocess.run(['git', 'clone', '-q', str(self.ws_origin), str(other)], check=True, capture_output=True)
        write(other, {'.brza/index.jsonl': '{"id":"PLN-001"}\n{"id":"PLN-002"}\n'})
        git('commit', '-q', '-am', 'índice', cwd=other)
        git('push', '-q', 'origin', 'HEAD:master', cwd=other)
        result, out = self.sync()
        self.assertEqual(result.code, 0, out)
        self.assertEqual(self.read('.brza/index.jsonl'), '{"id":"PLN-001"}\n{"id":"PLN-002"}\n')
        self.assertEqual(git('rev-parse', 'HEAD', cwd=self.ws_origin), git('rev-parse', 'HEAD', cwd=self.ws))  # push ok

    def test_a_diverged_workspace_refuses_the_pull_and_changes_nothing(self):
        other = self.base / 'other'
        subprocess.run(['git', 'clone', '-q', str(self.ws_origin), str(other)], check=True, capture_output=True)
        write(other, {'x.md': 'x\n'})
        git('add', '.', cwd=other)
        git('commit', '-q', '-m', 'x', cwd=other)
        git('push', '-q', 'origin', 'HEAD:master', cwd=other)
        self.commit_local({'y.md': 'y\n'})
        before = self.snapshot()
        with self.assertRaises(SyncError) as ctx:
            self.sync()
        self.assertIn('ff-only', str(ctx.exception))
        self.assertEqual(self.snapshot(), before)

    def test_no_push_commits_but_does_not_push(self):
        result, out = self.sync(push=False)
        self.assertEqual(result.code, 0, out)
        self.assertIn('sem push', out)
        self.assertEqual(git('rev-parse', 'origin/master', cwd=self.ws), git('rev-parse', 'HEAD~', cwd=self.ws))
        self.assertEqual(git('rev-parse', 'HEAD', cwd=self.ws_origin), git('rev-parse', 'HEAD~', cwd=self.ws))

    def test_a_workspace_without_remote_commits_and_skips_the_push(self):
        git('remote', 'remove', 'origin', cwd=self.ws)
        result, out = self.sync()
        self.assertEqual(result.code, 0, out)
        self.assertIn('sem push: o workspace não tem o remote origin', out)
        self.assertIsNotNone(result.committed)

    # o commit do brza-workflow

    def test_the_sync_follows_the_origin_branch_not_a_local_unpushed_commit(self):
        self.flow_commit({'core/AGENTS.md': 'só local\n'}, 'não empurrado', push=False)
        self.sync()
        self.assertEqual(self.read('AGENTS.md'), '# acme-notes\n\nQuem decide: Ana.\n')

    def test_the_sync_fetches_the_flow_origin(self):
        other = self.base / 'flow-other'
        subprocess.run(['git', 'clone', '-q', str(self.flow_origin), str(other)], check=True, capture_output=True)
        write(other, {'core/AGENTS.md': 'da origin\n'})
        git('commit', '-q', '-am', 'origin andou', cwd=other)
        git('push', '-q', 'origin', 'HEAD:master', cwd=other)
        self.sync()
        self.assertEqual(self.read('AGENTS.md'), 'da origin\n')

    def test_the_core_ref_of_the_manifest_picks_the_branch(self):
        git('checkout', '-q', '-b', 'dev', cwd=self.flow)
        self.flow_commit({'core/AGENTS.md': 'do dev\n'}, 'dev', push=False)
        git('push', '-q', 'origin', 'dev', cwd=self.flow)
        manifest = {**MANIFEST, 'core': {**MANIFEST['core'], 'ref': 'dev'}}
        self.commit_local({brza_root.MANIFEST: brza_root.dump_manifest(manifest)})
        self.sync()
        self.assertEqual(self.read('AGENTS.md'), 'do dev\n')
        self.assertEqual(self.lock()['ref'], 'dev')

    def test_ref_pins_one_commit_for_this_round_only(self):
        first = git('rev-parse', 'HEAD', cwd=self.flow)
        self.flow_commit({'core/AGENTS.md': 'segunda\n'}, 'segunda')
        result, out = self.sync(ref=first)
        self.assertEqual(self.read('AGENTS.md'), '# acme-notes\n\nQuem decide: Ana.\n')
        self.assertEqual((self.lock()['commit'], self.lock()['ref']), (first, first))
        self.assertEqual(self.read(brza_root.MANIFEST), brza_root.dump_manifest(MANIFEST))  # não grava no manifesto
        self.sync()  # a rodada seguinte volta ao core.ref
        self.assertEqual(self.read('AGENTS.md'), 'segunda\n')

    def test_an_unknown_ref_refuses(self):
        with self.assertRaises(SyncError) as ctx:
            self.sync(ref='nao-existe')
        self.assertIn('nao-existe', str(ctx.exception))

    def test_a_missing_package_refuses(self):
        manifest = {**MANIFEST, 'core': {'ref': 'master', 'packages': ['fantasma']}}
        self.commit_local({brza_root.MANIFEST: brza_root.dump_manifest(manifest)})
        with self.assertRaises(SyncError) as ctx:
            self.sync()
        self.assertIn('fantasma', str(ctx.exception))

    def test_without_the_package_the_manifest_does_not_receive_its_files(self):
        manifest = {**MANIFEST, 'core': {'ref': 'master'}}
        self.commit_local({brza_root.MANIFEST: brza_root.dump_manifest(manifest)})
        self.sync()
        self.assertFalse((self.ws / '.claude' / 'rules' / 'brza-client.md').exists())

    def test_a_placeholder_without_value_refuses_before_writing(self):
        self.flow_commit({'core/AGENTS.md': '{{workspace.inexistente}}\n'}, 'placeholder ruim')
        before = self.snapshot()
        with self.assertRaises(SyncError) as ctx:
            self.sync()
        self.assertIn('AGENTS.md:1', str(ctx.exception))
        self.assertEqual(self.snapshot(), before)

    def test_a_workspace_without_manifest_refuses(self):
        git('rm', '-q', brza_root.MANIFEST, cwd=self.ws)
        with self.assertRaises(SyncError):
            self.sync()

    # a camada 1 para o `brza init`

    def test_without_git_it_writes_files_and_lock_and_leaves_the_git_alone(self):
        fresh = self.base / 'novo'
        write(fresh, {brza_root.MANIFEST: brza_root.dump_manifest(MANIFEST),
                      '.claude/settings.json': '{\n  "env": {},\n  "hooks": {}\n}\n'})
        lines = []
        result = sync_core(fresh, flow=self.flow, with_git=False, out=lines.append, today='2026-11-03')
        self.assertEqual(result.code, 0, lines)
        self.assertIsNone(result.committed)
        self.assertFalse((fresh / '.git').exists())
        self.assertEqual((fresh / 'AGENTS.md').read_text(), '# acme-notes\n\nQuem decide: Ana.\n')
        self.assertEqual(json.loads((fresh / '.claude/settings.json').read_text())['hooks'], HOOKS['hooks'])
        lock = brza_root.read_lock(fresh)
        self.assertEqual(lock['commit'], git('rev-parse', 'HEAD', cwd=self.flow))
        self.assertIn('AGENTS.md', lock['files'])

    def test_without_git_it_creates_the_settings_when_the_template_has_none(self):
        fresh = self.base / 'novo'
        write(fresh, {brza_root.MANIFEST: brza_root.dump_manifest(MANIFEST)})
        sync_core(fresh, flow=self.flow, with_git=False, out=lambda line: None)
        self.assertEqual(json.loads((fresh / '.claude/settings.json').read_text()), HOOKS)

    def test_the_result_names_the_paths(self):
        result, _ = self.sync()
        self.assertIn('AGENTS.md', result.written)
        self.assertIn('.claude/settings.json', result.written)
        self.assertEqual(result.commit, git('rev-parse', 'HEAD', cwd=self.flow))


class SyncCommandTest(unittest.TestCase):
    """O `bin/brza-sync` de processo, com o cwd no workspace de mentira e o BRZA_WORKFLOW no brza-workflow de mentira."""

    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.base = Path(temp.name).resolve()
        self.env = dict(os.environ, GIT_AUTHOR_NAME='t', GIT_AUTHOR_EMAIL='t@t', GIT_COMMITTER_NAME='t',
                        GIT_COMMITTER_EMAIL='t@t', BRZA_REGISTRY='/nonexistent/brza/workspaces.toml')
        self.env.pop('BRZA_WORKSPACE', None)
        self.flow, self.ws, self.origin = self.base / 'flow', self.base / 'ws', self.base / 'origin.git'
        subprocess.run(['git', 'init', '-q', '-b', 'master', str(self.flow)], check=True)
        write(self.flow, CORE)
        git('add', '.', cwd=self.flow)
        git('commit', '-q', '-m', 'núcleo', cwd=self.flow)
        subprocess.run(['git', 'init', '-q', '--bare', '-b', 'master', str(self.origin)], check=True)
        subprocess.run(['git', 'clone', '-q', str(self.origin), str(self.ws)], check=True, capture_output=True)
        write(self.ws, {**LOCAL, brza_root.MANIFEST: brza_root.dump_manifest(MANIFEST)})
        git('add', '.', cwd=self.ws)
        git('commit', '-q', '-m', 'init', cwd=self.ws)
        git('push', '-q', 'origin', 'HEAD:master', cwd=self.ws)
        self.env['BRZA_WORKFLOW'] = str(self.flow)

    def run_sync(self, *argv, cwd: Path | None = None):
        done = subprocess.run([sys.executable, str(BIN / 'brza-sync'), *argv], cwd=str(cwd or self.ws), env=self.env,
                              capture_output=True, text=True)
        return done.returncode, done.stdout + done.stderr

    def test_the_command_runs_layer_one_and_exits_0(self):
        code, out = self.run_sync('--core-only')
        self.assertEqual(code, 0, out)
        self.assertEqual((self.ws / 'AGENTS.md').read_text(), '# acme-notes\n\nQuem decide: Ana.\n')
        self.assertIn('enviado:  origin/master', out)

    def test_the_command_exits_1_with_a_refused_file(self):
        self.run_sync('--core-only')
        write(self.ws, {'AGENTS.md': 'editado\n'})
        git('commit', '-q', '-am', 'edita', cwd=self.ws)
        code, out = self.run_sync('--core-only')
        self.assertEqual(code, 1, out)
        self.assertIn('recusado: AGENTS.md', out)
        code, out = self.run_sync('--core-only', '--force', 'AGENTS.md')
        self.assertEqual(code, 0, out)
        self.assertEqual((self.ws / 'AGENTS.md').read_text(), '# acme-notes\n\nQuem decide: Ana.\n')

    def test_the_command_finds_the_workspace_from_a_subfolder(self):
        (self.ws / 'work').mkdir()
        code, out = self.run_sync('--core-only', '--no-push', cwd=self.ws / 'work')
        self.assertEqual(code, 0, out)
        self.assertTrue((self.ws / 'AGENTS.md').exists())

    def test_the_command_refuses_outside_a_workspace(self):
        code, out = self.run_sync('--core-only', cwd=self.base)
        self.assertEqual(code, 1)
        self.assertIn('nenhum workspace', out)

    def other_workspace(self) -> Path:
        """Um segundo workspace de mentira, que não é o da BRZA_WORKSPACE."""
        other = self.base / 'ws2'
        subprocess.run(['git', 'init', '-q', '-b', 'master', str(other)], check=True)
        write(other, {**LOCAL, brza_root.MANIFEST: brza_root.dump_manifest(MANIFEST)})
        git('add', '.', cwd=other)
        git('commit', '-q', '-m', 'init', cwd=other)
        return other

    def test_the_command_refuses_when_the_variable_points_to_another_workspace_than_the_cwd(self):  # A1
        other = self.other_workspace()
        heads = {p: git('rev-parse', 'HEAD', cwd=p) for p in (self.ws, other)}
        before = {p: git('status', '--porcelain', cwd=p) for p in (self.ws, other)}
        self.env['BRZA_WORKSPACE'] = str(self.ws)
        for argv in (('--core-only', '--no-push'), ('--dry-run',), ()):
            code, out = self.run_sync(*argv, cwd=other)
            self.assertEqual(code, 1, out)
            self.assertIn('recusado:', out)
            self.assertIn(str(other), out)  # o cwd
            self.assertIn(str(self.ws), out)  # o workspace da variável
        for p in (self.ws, other):
            self.assertEqual(git('rev-parse', 'HEAD', cwd=p), heads[p])
            self.assertEqual(git('status', '--porcelain', cwd=p), before[p])
            self.assertFalse((p / 'AGENTS.md').exists())
            self.assertFalse((p / brza_root.LOCK).exists())

    def test_the_variable_still_applies_outside_any_git_repository(self):  # A1
        self.env['BRZA_WORKSPACE'] = str(self.ws)
        code, out = self.run_sync('--core-only', '--no-push', cwd=self.base)
        self.assertEqual(code, 0, out)
        self.assertTrue((self.ws / 'AGENTS.md').exists())

    def test_the_variable_applies_from_the_workspace_itself(self):  # A1
        self.env['BRZA_WORKSPACE'] = str(self.ws)
        code, out = self.run_sync('--core-only', '--no-push', cwd=self.ws)
        self.assertEqual(code, 0, out)

    def test_the_command_does_not_commit_when_the_flow_moved_outside_the_core(self):  # A4
        self.assertEqual(self.run_sync('--core-only')[0], 0)
        head = git('rev-parse', 'HEAD', cwd=self.ws)
        write(self.flow, {'README.md': 'só o README\n'})
        git('add', '-A', cwd=self.flow)
        git('commit', '-q', '-m', 'doc', cwd=self.flow)
        code, out = self.run_sync('--dry-run')
        self.assertEqual(code, 0, out)
        self.assertNotIn('escreveria', out)
        self.assertIn('o lock segue em', out)
        code, out = self.run_sync('--core-only')
        self.assertEqual(code, 0, out)
        self.assertNotIn('commit:', out)
        self.assertEqual(git('rev-parse', 'HEAD', cwd=self.ws), head)

    def test_the_command_refuses_a_broken_lock_with_exit_1(self):
        write(self.ws, {brza_root.LOCK: 'lixo [\n'})
        git('add', '-A', cwd=self.ws)
        git('commit', '-q', '-m', 'lock quebrado', cwd=self.ws)
        code, out = self.run_sync('--core-only')
        self.assertEqual(code, 1, out)
        self.assertIn('quebrado', out)
        self.assertFalse((self.ws / 'AGENTS.md').exists())

    def test_the_command_dry_run_writes_nothing(self):
        code, out = self.run_sync('--dry-run')
        self.assertEqual(code, 0, out)
        self.assertIn('escreveria: AGENTS.md', out)
        self.assertFalse((self.ws / 'AGENTS.md').exists())

    def test_without_core_only_layer_two_merges_the_norm_into_the_workstreams(self):
        git('worktree', 'add', '-q', '-b', 'workstream/x', str(self.ws / '.worktrees' / 'x'), cwd=self.ws)
        git('push', '-q', '-u', 'origin', 'workstream/x', cwd=self.ws / '.worktrees' / 'x')
        code, out = self.run_sync()
        self.assertEqual(code, 0, out)
        self.assertIn('x: mesclado e enviado', out)
        self.assertTrue((self.ws / '.worktrees' / 'x' / 'AGENTS.md').exists())

    def test_core_only_leaves_the_workstreams_alone(self):
        git('worktree', 'add', '-q', '-b', 'workstream/x', str(self.ws / '.worktrees' / 'x'), cwd=self.ws)
        code, out = self.run_sync('--core-only')
        self.assertEqual(code, 0, out)
        self.assertNotIn('mesclado', out)
        self.assertFalse((self.ws / '.worktrees' / 'x' / 'AGENTS.md').exists())


if __name__ == '__main__':
    unittest.main()
