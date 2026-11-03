"""Testes de `scripts/migrate_pln008.py` (P5: D4 e D8, usando D3 e D7).

    PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests/cli -p 'test_migrate_pln008.py'
"""

from __future__ import annotations

import importlib.util
import io
import json
import os
import subprocess
import sys
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from test_brza_note import GLOBALS, ROOT, git
from test_brza_note_index import INDEX, IndexFixture

MIGRATE_SCRIPT = ROOT / 'scripts' / 'migrate_pln008.py'


def load_migrate():
    spec = importlib.util.spec_from_file_location('migrate_pln008', str(MIGRATE_SCRIPT))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def review_doc(h1_suffix: str, plan_link: str = '') -> str:
    body = f'Parecer sobre {plan_link}.\n' if plan_link else 'Parecer técnico.\n'
    return (
        '---\n'
        'status: done\n'
        'description: Parecer da revisão.\n'
        'scope:\n'
        '  - workspace\n'
        'opened: 2026-10-31\n'
        'completed: 2026-10-31\n'
        'tags:\n'
        '  - tooling\n'
        '---\n\n'
        f'# REV: {h1_suffix}\n\n'
        f'{body}'
    )


class MigratePln008Test(IndexFixture):
    """Fixture de dois workstreams (`tools-kit` e `workspace`) com as quatro revisões históricas de D4,
    planos e issues referenciados, links relativos e worklogs com `date:`, `tags:` e `refs:` antigo inválido."""

    def setUp(self):
        super().setUp()
        self.mig = load_migrate()
        self.real_checker_script = ROOT / 'core' / '.claude' / 'skills' / 'notes-checker' / 'scripts' / 'check_notes.py'

        # Criar os dois worktrees do mapa D4: `tools-kit` e `workspace`
        self.wt_tools = self.add_workstream_with_real_checker('tools-kit')
        self.wt_workspace = self.add_workstream_with_real_checker('workspace')

        # Popular `tools-kit`: PLN-002, parecer antigo pln-002-os-comandos-brza.md e worklog com date:, tags: e refs inválido
        self.write(
            'plans/PLN-002-os-comandos-brza/PLN-002-plan.md',
            '---\n'
            'status: done\n'
            'phase: closed\n'
            'description: Os comandos brza.\n'
            'origin: request\n'
            'scope:\n'
            '  - workspace\n'
            'opened: 2026-10-29\n'
            'completed: 2026-10-29\n'
            '---\n\n'
            '# PLN-002: Os comandos brza\n\n'
            '- **Retomar:** Fechado.\n\n'
            'Parecer em [REV: a entrega do PLN-002](../../work/reviews/pln-002-os-comandos-brza.md#achados).\n\n'
            '## Worklogs\n\n'
            '- 2026-10-29 — entrega.\n'
            '  [Entrega do PLN-002](../../work/worklogs/2026-10-29-entrega-pln-002.md)\n',
            tree=self.wt_tools,
        )
        self.write(
            'work/reviews/pln-002-os-comandos-brza.md',
            review_doc(
                'a entrega do PLN-002',
                '[PLN-002](../../plans/PLN-002-os-comandos-brza/PLN-002-plan.md)',
            ),
            tree=self.wt_tools,
        )
        self.write(
            'work/worklogs/2026-10-29-entrega-pln-002.md',
            '---\n'
            'date: 2026-10-29\n'
            'scope:\n'
            '  - workspace\n'
            'tags:\n'
            '  - tooling\n'
            '---\n\n'
            '# Entrega do PLN-002\n\n'
            'Conferido com [REV: a entrega do PLN-002](../reviews/pln-002-os-comandos-brza.md) no `PLN-002/P1`.\n',
            tree=self.wt_tools,
        )
        self._commit_setup(self.wt_tools, 'workstream/tools-kit')

        # Popular `workspace`: PLN-005, PLN-006, issue comum, os três pareceres antigos e worklog citando REV-001 e issue
        for num, slug, rev_files in (
            ('005', 'a-confianca-dos-fatos', ['pln-005-a-confianca-dos-fatos.md']),
            ('006', 'o-brza-init', ['pln-006-o-brza-init.md', 'pln-006-a-migracao.md']),
        ):
            links = '\n'.join(f'- [Parecer {f}](../../work/reviews/{f})' for f in rev_files)
            self.write(
                f'plans/PLN-{num}-{slug}/PLN-{num}-plan.md',
                '---\n'
                'status: done\n'
                'phase: closed\n'
                f'description: Plano {num}.\n'
                'origin: request\n'
                'scope:\n'
                '  - workspace\n'
                'opened: 2026-10-31\n'
                'completed: 2026-11-02\n'
                '---\n\n'
                f'# PLN-{num}: Plano {num}\n\n'
                '- **Retomar:** Fechado.\n\n'
                f'{links}\n\n'
                '## Worklogs\n\n'
                '- 2026-11-02 — fechamento.\n'
                '  [Fechamento](../../work/worklogs/2026-11-02-fechamento-workspace.md)\n',
                tree=self.wt_workspace,
            )
        self.write(
            'work/issues/ajuste-no-indice.md',
            '---\n'
            'status: open\n'
            'description: Ajuste pendente no índice.\n'
            'scope:\n'
            '  - workspace\n'
            'opened: 2026-11-02\n'
            'priority: medium\n'
            '---\n\n'
            '# Ajuste no índice\n\n'
            '## O pedido\n\n'
            'Detalhe.\n',
            tree=self.wt_workspace,
        )
        self.write(
            'work/reviews/pln-005-a-confianca-dos-fatos.md',
            review_doc('PLN-005 — a confiança dos fatos do desenho'),
            tree=self.wt_workspace,
        )
        self.write(
            'work/reviews/pln-006-o-brza-init.md',
            review_doc('o brza-workflow dos passos P1–P7 do PLN-006'),
            tree=self.wt_workspace,
        )
        self.write(
            'work/reviews/pln-006-a-migracao.md',
            review_doc('a migração do brza-workspace para cliente do brza-workflow'),
            tree=self.wt_workspace,
        )
        self.write(
            'work/worklogs/2026-11-02-fechamento-workspace.md',
            '---\n'
            'date: 2026-11-02\n'
            'scope:\n'
            '  - workspace\n'
            'tags:\n'
            '  - tooling\n'
            '---\n\n'
            '# Fechamento no workspace\n\n'
            'Revisões [REV-002](../reviews/pln-005-a-confianca-dos-fatos.md), '
            '[REV-003](../reviews/pln-006-o-brza-init.md) e '
            '[REV-004](../reviews/pln-006-a-migracao.md), além de `REV-001`, `MIG-001` e '
            '[ajuste](../issues/ajuste-no-indice.md).\n',
            tree=self.wt_workspace,
        )
        self._commit_setup(self.wt_workspace, 'workstream/workspace')

    def add_workstream_with_real_checker(self, name: str) -> Path:
        tree = self.make_workstream(name)
        checker_dst = tree / '.claude' / 'skills' / 'notes-checker' / 'scripts' / 'check_notes.py'
        checker_dst.parent.mkdir(parents=True, exist_ok=True)
        checker_dst.write_text(self.real_checker_script.read_text(encoding='utf-8'), encoding='utf-8')
        return tree

    def _commit_setup(self, tree: Path, branch: str):
        git('add', '.', cwd=tree)
        git('commit', '-q', '-m', f'setup {branch}', cwd=tree)
        git('push', '-q', '-u', 'origin', branch, cwd=tree)

    def run_migrate(self, **kwargs) -> tuple[int, dict, str]:
        buf = io.StringIO()
        with redirect_stdout(buf):
            code, report = self.mig.migrate(ws_root=self.ws, note_globals=GLOBALS, **kwargs)
        return code, report, buf.getvalue()

    def snapshot(self):
        return {
            'files': {f'{wt.name}/{p.relative_to(wt)}': p.read_bytes()
                      for wt in (self.wt_tools, self.wt_workspace)
                      for zone in ('plans', 'work') for p in (wt / zone).rglob('*.md')},
            'heads': [git('rev-parse', 'HEAD', cwd=wt) for wt in (self.wt_tools, self.wt_workspace)],
            'index': git('show', f'master:{INDEX}', cwd=self.origin),
        }

    def test_script_entrypoint_defaults_to_preview_and_requires_selection_to_apply(self):
        self.write_manifest()
        before = self.snapshot()
        env = {**os.environ, 'BRZA_WORKSPACE': str(self.ws), 'BRZA_STUDIO': str(self.studio)}
        preview = subprocess.run([sys.executable, str(MIGRATE_SCRIPT), '--workstream', 'tools-kit'],
                                 cwd=self.ws, env=env, text=True, capture_output=True)
        self.assertEqual(preview.returncode, 0, preview.stdout + preview.stderr)
        self.assertIn('[PRÉVIA] tools-kit:', preview.stdout)
        apply = subprocess.run([sys.executable, str(MIGRATE_SCRIPT), '--apply'],
                               cwd=self.ws, env=env, text=True, capture_output=True)
        self.assertEqual(apply.returncode, 1, apply.stdout + apply.stderr)
        self.assertIn('--workstream', apply.stdout)
        self.assertEqual(self.snapshot(), before)
        selected = subprocess.run([sys.executable, str(MIGRATE_SCRIPT), '--apply', '--workstream', 'tools-kit'],
                                  cwd=self.ws, env=env, text=True, capture_output=True)
        self.assertEqual(selected.returncode, 0, selected.stdout + selected.stderr)
        self.assertIn('[APLICADO] tools-kit:', selected.stdout)
        self.assertTrue((self.wt_tools / 'work/reviews/REV-001-pln-002-os-comandos-brza.md').is_file())
        self.assertFalse((self.wt_workspace / self.mig.MIG_001_REL).exists())

    def test_invalid_old_refs_refuse_before_any_write_or_reservation(self):
        rel = 'work/worklogs/2026-11-02-fechamento-workspace.md'
        path = self.wt_workspace / rel
        path.write_text(path.read_text().replace('scope:', 'refs: [REV-999]\nscope:', 1))
        self._commit_setup(self.wt_workspace, 'workstream/workspace')
        before = self.snapshot()
        for apply in (False, True):
            code, _, out = self.run_migrate(workstreams=['tools-kit', 'workspace'], apply=apply)
            self.assertEqual(code, 1, out)
            self.assertIn('ref-not-found: workspace:', out)
            self.assertIn('REV-999', out)
            self.assertEqual(self.snapshot(), before)

    def test_apply_requires_explicit_selection(self):
        before = self.snapshot()
        code, _, out = self.run_migrate(apply=True)
        self.assertEqual(code, 1, out)
        self.assertIn('--workstream', out)
        self.assertEqual(self.snapshot(), before)

    def test_dirty_notes_refuse_and_unrelated_staged_file_is_preserved(self):
        rel = self.wt_workspace / 'work/reviews/pln-005-a-confianca-dos-fatos.md'
        original = rel.read_bytes()
        rel.write_bytes(original + b'\nTrabalho alheio.\n')
        before = self.snapshot()
        code, _, out = self.run_migrate(workstreams=['tools-kit', 'workspace'], apply=True)
        self.assertEqual(code, 1, out)
        self.assertIn('alterações sem commit', out)
        self.assertEqual(self.snapshot(), before)
        rel.write_bytes(original)
        extra = self.wt_tools / 'nao-incluir.txt'
        extra.write_text('Trabalho alheio.\n')
        git('add', extra.name, cwd=self.wt_tools)
        code, _, out = self.run_migrate(workstreams=['tools-kit'], apply=True)
        self.assertEqual(code, 0, out)
        self.assertEqual(git('status', '--porcelain', '--', extra.name, cwd=self.wt_tools), 'A  nao-incluir.txt')
        self.assertNotIn(extra.name, git('ls-tree', '--name-only', 'HEAD', cwd=self.wt_tools))

    def test_missing_worktree_in_apply_is_reported_without_creating_it(self):
        code, report, out = self.run_migrate(workstreams=['inexistente'], apply=True)
        self.assertEqual(code, 0, out)
        self.assertEqual(report['missing_worktrees'], ['inexistente'])
        self.assertFalse((self.ws / '.worktrees/inexistente').exists())

    def test_one_workstream_leaves_other_tree_untouched_and_mig_open(self):
        before = {p.relative_to(self.wt_tools): p.read_bytes()
                  for p in self.wt_tools.rglob('*.md')}
        code, report, out = self.run_migrate(workstreams=['workspace'], apply=True)
        self.assertEqual(code, 0, out)
        self.assertEqual(set(report['workstreams']), {'workspace'})
        self.assertEqual({p.relative_to(self.wt_tools): p.read_bytes()
                          for p in self.wt_tools.rglob('*.md')}, before)
        fm = GLOBALS['parse_frontmatter']((self.wt_workspace / self.mig.MIG_001_REL).read_text())
        self.assertEqual(fm['status'], 'open')
        self.assertNotIn('completed', fm)
        self.assertNotIn('opened', fm)

    def test_cross_links_in_both_directions_and_bootstrap_repeated(self):
        tools_path = self.wt_tools / 'work/reviews/pln-002-os-comandos-brza.md'
        ws_path = self.wt_workspace / 'work/reviews/pln-005-a-confianca-dos-fatos.md'
        tools_path.write_text(tools_path.read_text() + '\n[REV: PLN-005 — a confiança dos fatos do desenho](pln-005-a-confianca-dos-fatos.md#achados)\n')
        ws_path.write_text(ws_path.read_text() + '\n[REV: a entrega do PLN-002](pln-002-os-comandos-brza.md)\n')
        self._commit_setup(self.wt_tools, 'workstream/tools-kit')
        self._commit_setup(self.wt_workspace, 'workstream/workspace')
        with redirect_stdout(io.StringIO()):
            GLOBALS['bootstrap_pln008']()
            before = self.origin_index()
            GLOBALS['bootstrap_pln008']()
        self.assertEqual(self.origin_index(), before)
        code, _, out = self.run_migrate(workstreams=['tools-kit', 'workspace'], apply=False)
        self.assertEqual(code, 0, out)
        self.assertIn('links cruzados', out)
        code, _, out = self.run_migrate(workstreams=['tools-kit', 'workspace'], apply=True)
        self.assertEqual(code, 0, out)
        new_tools = self.wt_tools / 'work/reviews/REV-001-pln-002-os-comandos-brza.md'
        new_ws = self.wt_workspace / 'work/reviews/REV-002-pln-005-a-confianca-dos-fatos.md'
        self.assertIn('[REV-002: PLN-005 — a confiança dos fatos do desenho](REV-002-pln-005-a-confianca-dos-fatos.md#achados)', new_tools.read_text())
        self.assertIn('[REV-001: a entrega do PLN-002](REV-001-pln-002-os-comandos-brza.md)', new_ws.read_text())
        before = self.snapshot()
        code, _, out = self.run_migrate(workstreams=['tools-kit', 'workspace'], apply=True)
        self.assertEqual(code, 0, out)
        self.assertEqual(self.snapshot(), before)

    def test_preview_reports_changes_and_missing_worktrees_without_modifying_disk_or_index(self):
        before = self.snapshot()
        before_tools = git('status', '--porcelain', cwd=self.wt_tools)
        before_ws = git('status', '--porcelain', cwd=self.wt_workspace)
        before_head_tools = git('rev-parse', 'HEAD', cwd=self.wt_tools)
        before_head_ws = git('rev-parse', 'HEAD', cwd=self.wt_workspace)

        code, report, out = self.run_migrate(workstreams=['tools-kit', 'workspace', 'inexistente'], apply=False)
        self.assertEqual(code, 0, out)
        self.assertEqual(report['mode'], 'preview')
        self.assertEqual(report['missing_worktrees'], ['inexistente'])
        self.assertIn('worktree ausente: inexistente', out)
        self.assertFalse((self.ws / '.worktrees' / 'inexistente').exists())

        # Prévia detalha renomeações, criação da MIG-001, links e worklogs
        self.assertEqual(len(report['workstreams']['tools-kit']['renamed']), 1)
        self.assertEqual(report['workstreams']['workspace']['created'], [self.mig.MIG_001_REL])
        self.assertEqual(len(report['workstreams']['workspace']['renamed']), 3)
        self.assertIn('work/worklogs/2026-10-29-entrega-pln-002.md', report['workstreams']['tools-kit']['worklogs_updated'])
        self.assertIn('work/worklogs/2026-11-02-fechamento-workspace.md', report['workstreams']['workspace']['worklogs_updated'])

        # Nenhum arquivo nem commit nem linha de índice foi alterado pela prévia
        self.assertEqual(git('status', '--porcelain', cwd=self.wt_tools), before_tools)
        self.assertEqual(git('status', '--porcelain', cwd=self.wt_workspace), before_ws)
        self.assertEqual(git('rev-parse', 'HEAD', cwd=self.wt_tools), before_head_tools)
        self.assertEqual(git('rev-parse', 'HEAD', cwd=self.wt_workspace), before_head_ws)
        self.assertEqual(self.origin_index(), [])
        self.assertEqual(self.snapshot(), before)

    def test_apply_migrates_reviews_links_and_worklogs_and_second_apply_is_noop(self):
        before_fm = GLOBALS['parse_frontmatter']((self.wt_tools / 'work/worklogs/2026-10-29-entrega-pln-002.md').read_text())
        code, report, out = self.run_migrate(workstreams=['tools-kit', 'workspace'], apply=True)
        self.assertEqual(code, 0, out)

        # 1. Os quatro nomes antigos sumiram e os quatro novos existem com H1 `# REV-00N: ...`
        for ws_name, rev_id, old_rel, new_rel in self.mig.REV_MAP:
            wt = self.wt_tools if ws_name == 'tools-kit' else self.wt_workspace
            self.assertFalse((wt / old_rel).exists(), f'{old_rel} deveria ter sido movido')
            self.assertTrue((wt / new_rel).is_file(), f'{new_rel} deveria existir')
            text = (wt / new_rel).read_text(encoding='utf-8')
            self.assertIn(f'\n# {rev_id}: ', text)
            self.assertNotIn('\n# REV: ', text)

        # 2. MIG-001 criada em workspace no molde D1
        mig_path = self.wt_workspace / self.mig.MIG_001_REL
        self.assertTrue(mig_path.is_file())
        mig_text = mig_path.read_text(encoding='utf-8')
        for heading in ('# MIG-001:', '## Estado-alvo', '## Estado atual', '## Passos', '## Critério de conclusão'):
            self.assertIn(heading, mig_text)

        # 3. Links relativos nas árvores donas foram atualizados (preservando âncoras e trocando rótulo igual ao H1 antigo)
        pln002_text = (self.wt_tools / 'plans/PLN-002-os-comandos-brza/PLN-002-plan.md').read_text(encoding='utf-8')
        self.assertIn('[REV-001: a entrega do PLN-002](../../work/reviews/REV-001-pln-002-os-comandos-brza.md#achados)', pln002_text)
        pln006_text = (self.wt_workspace / 'plans/PLN-006-o-brza-init/PLN-006-plan.md').read_text(encoding='utf-8')
        self.assertIn('work/reviews/REV-003-pln-006-o-brza-init.md', pln006_text)
        self.assertIn('work/reviews/REV-004-pln-006-a-migracao.md', pln006_text)

        # 4. Worklogs perderam date, preservaram tags e receberam refs pelo extrator D3.
        wl_tools = (self.wt_tools / 'work/worklogs/2026-10-29-entrega-pln-002.md').read_text(encoding='utf-8')
        fm_tools = GLOBALS['parse_frontmatter'](wl_tools)
        self.assertNotIn('date', fm_tools)
        self.assertEqual(fm_tools.get('tags'), ['tooling'])
        self.assertEqual(fm_tools.get('refs'), ['PLN-002', 'REV-001'])
        self.assertEqual({k: v for k, v in fm_tools.items() if k != 'refs'},
                         {k: v for k, v in before_fm.items() if k not in ('refs', 'date')})

        wl_ws = (self.wt_workspace / 'work/worklogs/2026-11-02-fechamento-workspace.md').read_text(encoding='utf-8')
        fm_ws = GLOBALS['parse_frontmatter'](wl_ws)
        self.assertNotIn('date', fm_ws)
        self.assertEqual(fm_ws.get('tags'), ['tooling'])
        self.assertEqual(
            fm_ws.get('refs'),
            ['MIG-001', 'REV-001', 'REV-002', 'REV-003', 'REV-004', 'issue:ajuste-no-indice'],
        )

        # 5. Estado vigente do índice contém REV-001..REV-004 e MIG-001 materializados com commit
        index_before_second = self.origin_index()
        state = GLOBALS['current_state']('\n'.join(json.dumps(r) for r in index_before_second))
        for k in ('REV-001', 'REV-002', 'REV-003', 'REV-004', 'MIG-001'):
            self.assertIn(k, state)
            self.assertTrue(state[k].get('commit'), f'{k} precisa ter commit após migração')

        head_tools_after_1 = git('rev-parse', 'HEAD', cwd=self.wt_tools)
        head_ws_after_1 = git('rev-parse', 'HEAD', cwd=self.wt_workspace)
        snapshot = self.snapshot()

        # 6. Segunda aplicação com --apply é idempotente: zero arquivos alterados, zero commits e zero linhas no índice
        code2, report2, out2 = self.run_migrate(workstreams=['tools-kit', 'workspace'], apply=True)
        self.assertEqual(code2, 0, out2)
        self.assertEqual(report2['workstreams']['tools-kit']['changed_paths'], [])
        self.assertEqual(report2['workstreams']['workspace']['changed_paths'], [])
        self.assertEqual(git('rev-parse', 'HEAD', cwd=self.wt_tools), head_tools_after_1)
        self.assertEqual(git('rev-parse', 'HEAD', cwd=self.wt_workspace), head_ws_after_1)
        self.assertEqual(self.origin_index(), index_before_second)
        self.assertEqual(self.snapshot(), snapshot)

    def test_bootstrap_collision_refuses_in_preview_and_apply(self):
        # Gravar uma entrada conflitante para REV-001 no índice da origin/master
        conflict = {
            'ts': '2026-11-03T00:00:00-03:00',
            'event': 'created',
            'kind': 'review',
            'id': 'REV-001',
            'path': 'work/reviews/REV-001-outro-parecer.md',
            'title': 'Outro',
            'description': 'Outro',
            'status': 'open',
            'workstream': 'workspace',
            'worktree': '.worktrees/workspace',
        }
        (self.ws / INDEX).write_text(json.dumps(conflict) + '\n', encoding='utf-8')
        git('add', INDEX, cwd=self.ws)
        git('commit', '-q', '-m', 'colisão REV-001', cwd=self.ws)
        git('push', '-q', 'origin', 'HEAD:master', cwd=self.ws)

        code_prev, _, out_prev = self.run_migrate(workstreams=['tools-kit', 'workspace'], apply=False)
        self.assertEqual(code_prev, 1, out_prev)
        self.assertIn('colisão no bootstrap de REV-001', out_prev)

        code_app, _, out_app = self.run_migrate(workstreams=['tools-kit', 'workspace'], apply=True)
        self.assertEqual(code_app, 1, out_app)
        self.assertIn('colisão no bootstrap de REV-001', out_app)
        # Parecer antigo continua intocado em disco
        self.assertTrue((self.wt_tools / 'work/reviews/pln-002-os-comandos-brza.md').is_file())


if __name__ == '__main__':
    unittest.main()
