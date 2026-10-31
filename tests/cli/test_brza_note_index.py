"""Testes do índice da master (`.brza/index.jsonl`) no brza-note: o `plan-open` que reserva o número, a linha do
`commit` na origin/master sem tocar o checkout principal, o `merge=union` e o estado vigente, o `index --rebuild`.

    python3 -m unittest discover -s tests/cli
"""
import json
import os
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

from test_brza_note import BIN, GLOBALS, ROOT, NoteFixture, git

GITATTRIBUTES = ROOT / 'core' / '.gitattributes'
INDEX = '.brza/index.jsonl'


def note_text(status='open', description='Uma frase.', h1='O que a nota diz', body='', extra=''):
    fm = f'status: {status}\n' + (f'description: {description}\n' if description else '') + extra
    return f'---\n{fm}opened: 2026-10-31\n---\n\n# {h1}\n\n{body}'


class IndexTest(NoteFixture):
    def setUp(self):
        super().setUp()
        # o checkout principal, na master, com o .gitattributes do núcleo e o índice vazio, como o init deixa
        (self.ws / '.gitattributes').write_text(GITATTRIBUTES.read_text())
        (self.ws / '.brza').mkdir()
        (self.ws / INDEX).write_text('')
        git('add', '.', cwd=self.ws)
        git('commit', '-q', '-m', 'o índice', cwd=self.ws)
        git('push', '-q', 'origin', 'HEAD:master', cwd=self.ws)

    def origin_index(self, ref: str = 'master') -> list[dict]:
        try:
            text = git('show', f'{ref}:{INDEX}', cwd=self.origin)
        except subprocess.CalledProcessError:
            return []
        return [json.loads(line) for line in text.splitlines() if line.strip()]

    def origin_master_log(self) -> list[str]:
        return git('log', '--format=%s', 'master', cwd=self.origin).splitlines()

    def write(self, rel: str, text: str, tree: Path | None = None):
        path = (tree or self.tree) / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)

    def commit_note(self, *rels: str, message: str = 'docs: nota') -> tuple[int, str]:
        return self.run_note('commit', *rels, '-m', message)

    def snapshot_main_checkout(self) -> dict:
        return {'status': git('status', '--porcelain', '--untracked-files=all', cwd=self.ws),
                'head': git('rev-parse', 'HEAD', cwd=self.ws), 'master': git('rev-parse', 'master', cwd=self.ws),
                'readme': (self.ws / 'README.md').read_text()}

    def clone(self, name: str) -> Path:
        path = self.base / name
        subprocess.run(['git', 'clone', '-q', str(self.origin), str(path)], check=True, capture_output=True)
        git('config', 'user.name', 't', cwd=path)
        git('config', 'user.email', 't@t', cwd=path)
        return path

    # plan-open

    def test_plan_open_reserves_the_next_number_with_a_created_line_on_the_origin_master(self):
        code, out = self.run_note('plan-open', 'novo', '--title', 'O plano novo', '--description', 'Faz isto.')
        self.assertEqual(code, 0, out)
        self.assertEqual(out.strip().splitlines()[-2:], ['PLN-001', 'plans/PLN-001-novo/PLN-001-plan.md'])
        (line,) = self.origin_index()
        self.assertEqual({k: line[k] for k in ('event', 'kind', 'id', 'path', 'title', 'description', 'status', 'phase',
                                              'workstream', 'worktree')},
                         {'event': 'created', 'kind': 'plan', 'id': 'PLN-001',
                          'path': 'plans/PLN-001-novo/PLN-001-plan.md', 'title': 'O plano novo',
                          'description': 'Faz isto.', 'status': 'open', 'phase': 'interview',
                          'workstream': 'studio', 'worktree': '.worktrees/studio'})
        self.assertRegex(line['ts'], r'^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}[+-]\d{2}:\d{2}$')

    def test_plan_open_numbers_after_the_highest_plan_of_the_index(self):
        self.run_note('plan-open', 'um', '--title', 'Um', '--description', 'a')
        self.run_note('plan-open', 'dois', '--title', 'Dois', '--description', 'b')
        code, out = self.run_note('plan-open', 'tres', '--title', 'Três', '--description', 'c')
        self.assertEqual(code, 0, out)
        self.assertEqual([x['id'] for x in self.origin_index()], ['PLN-001', 'PLN-002', 'PLN-003'])
        self.assertEqual(self.origin_master_log()[:3], ['índice: PLN-003 created', 'índice: PLN-002 created',
                                                        'índice: PLN-001 created'])

    def test_plan_open_skips_a_number_that_another_session_took_between_the_fetch_and_the_push(self):
        other = self.clone('other')
        real_push, calls = GLOBALS['index_push'], []

        def push_after_a_competitor(repo, sha):
            calls.append(sha)
            if len(calls) == 1:
                with (other / INDEX).open('a') as f:
                    f.write(json.dumps({'ts': '2026-10-31T10:00:00-03:00', 'event': 'created', 'kind': 'plan',
                                        'id': 'PLN-001', 'path': 'plans/PLN-001-rival/PLN-001-plan.md'}) + '\n')
                git('commit', '-q', '-am', 'rival', cwd=other)
                git('push', '-q', 'origin', 'HEAD:master', cwd=other)
            return real_push(repo, sha)

        with patch.dict(GLOBALS, {'index_push': push_after_a_competitor}):
            code, out = self.run_note('plan-open', 'novo', '--title', 'Novo', '--description', 'x')
        self.assertEqual(code, 0, out)
        self.assertEqual(out.strip().splitlines()[-2], 'PLN-002')
        self.assertEqual(len(calls), 2)
        self.assertEqual([x['id'] for x in self.origin_index()], ['PLN-001', 'PLN-002'])

    def test_two_plan_opens_running_at_the_same_time_get_different_numbers(self):
        env = {**os.environ, 'BRZA_WORKSPACE': str(self.ws), 'BRZA_STUDIO': str(self.studio)}
        procs = [subprocess.Popen([sys.executable, str(BIN / 'brza-note'), 'plan-open', f'plano-{n}', '--title', f'P{n}',
                                   '--description', 'x', '--workstream', 'studio'], env=env, cwd=self.base,
                                  stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True) for n in (1, 2)]
        outs = [p.communicate(timeout=60)[0] for p in procs]
        self.assertEqual([p.returncode for p in procs], [0, 0], outs)
        printed = sorted(o.strip().splitlines()[-2] for o in outs)
        self.assertEqual(printed, ['PLN-001', 'PLN-002'], outs)
        self.assertEqual(sorted(x['id'] for x in self.origin_index()), ['PLN-001', 'PLN-002'])

    def test_plan_open_gives_up_after_three_refused_pushes_and_reserves_nothing(self):
        calls = []

        def always_refused(repo, sha):
            calls.append(sha)
            raise GLOBALS['runtime'].BrzaError('git push falhou: rejeitado')

        with patch.dict(GLOBALS, {'index_push': always_refused}):
            code, out = self.run_note('plan-open', 'novo', '--title', 'Novo', '--description', 'x')
        self.assertEqual(code, 1, out)
        self.assertIn('recusado', out)
        self.assertEqual(len(calls), 3)
        self.assertEqual(self.origin_index(), [])

    def test_plan_open_without_a_remote_refuses(self):
        git('remote', 'remove', 'origin', cwd=self.ws)
        code, out = self.run_note('plan-open', 'novo', '--title', 'Novo', '--description', 'x')
        self.assertEqual(code, 1, out)
        self.assertIn('recusado', out)
        self.assertIn('origin', out)

    def test_plan_open_leaves_the_main_checkout_alone_even_dirty(self):
        (self.ws / 'README.md').write_text('sujo\n')
        (self.ws / 'solto.txt').write_text('x\n')
        before = self.snapshot_main_checkout()
        code, out = self.run_note('plan-open', 'novo', '--title', 'Novo', '--description', 'x')
        self.assertEqual(code, 0, out)
        self.assertEqual(self.snapshot_main_checkout(), before)

    def test_plan_open_requires_the_title_and_the_description_and_a_kebab_slug(self):
        for argv in (('plan-open', 'novo', '--title', 'T'), ('plan-open', 'novo', '--description', 'D')):
            code, _ = self.run_note(*argv)
            self.assertEqual(code, 2)
        code, _ = self.run_note('plan-open', 'Novo Plano', '--title', 'T', '--description', 'D')
        self.assertEqual(code, 2)
        self.assertEqual(self.origin_index(), [])

    def test_plan_open_strips_the_pln_prefix_of_the_title(self):
        self.run_note('plan-open', 'novo', '--title', 'PLN-001: O plano', '--description', 'x')
        self.assertEqual(self.origin_index()[0]['title'], 'O plano')

    def test_plan_open_in_an_index_that_does_not_exist_yet_creates_it(self):
        git('rm', '-q', '-r', '.brza', cwd=self.ws)
        git('commit', '-q', '-m', 'sem índice', cwd=self.ws)
        git('push', '-q', 'origin', 'HEAD:master', cwd=self.ws)
        code, out = self.run_note('plan-open', 'novo', '--title', 'Novo', '--description', 'x')
        self.assertEqual(code, 0, out)
        self.assertEqual([x['id'] for x in self.origin_index()], ['PLN-001'])

    # commit

    def test_commit_of_a_new_issue_appends_a_created_line_without_touching_the_dirty_main_checkout(self):
        (self.ws / 'README.md').write_text('sujo\n')
        (self.ws / 'solto.txt').write_text('x\n')
        before = self.snapshot_main_checkout()
        self.write('work/issues/uma.md', note_text(description='A issue.', h1='Uma issue'))
        code, out = self.commit_note('work/issues/uma.md')
        self.assertEqual(code, 0, out)
        (line,) = self.origin_index()
        self.assertEqual((line['event'], line['kind'], line['id'], line['path'], line['title'], line['description'],
                          line['status'], line['workstream']),
                         ('created', 'issue', 'issue:uma', 'work/issues/uma.md', 'Uma issue', 'A issue.', 'open',
                          'studio'))
        self.assertNotIn('phase', line)
        self.assertEqual(line['commit'], git('rev-parse', '--short', 'HEAD', cwd=self.tree))
        self.assertEqual(self.snapshot_main_checkout(), before)
        # a master local fica atrás da origin/master até o próximo pull
        self.assertNotEqual(git('rev-parse', 'master', cwd=self.origin), before['master'])

    def test_commit_with_a_changed_frontmatter_appends_a_changed_line(self):
        self.write('work/decisions/d.md', note_text(description='Decide.', h1='A decisão',
                                                     extra='trigger: "quando vier"\n'))
        self.commit_note('work/decisions/d.md')
        self.write('work/decisions/d.md', note_text(status='done', description='Decide.', h1='A decisão',
                                                     extra='decided: 2026-10-31\ndecided_by: Anderson\n'))
        code, out = self.commit_note('work/decisions/d.md')
        self.assertEqual(code, 0, out)
        lines = self.origin_index()
        self.assertEqual([(x['event'], x['id'], x['status']) for x in lines],
                         [('created', 'decision:d', 'open'), ('changed', 'decision:d', 'done')])

    def test_commit_with_the_same_frontmatter_appends_nothing(self):
        self.write('work/issues/uma.md', note_text(body='um\n'))
        self.commit_note('work/issues/uma.md')
        log = self.origin_master_log()
        self.write('work/issues/uma.md', note_text(body='um\n\ne mais um parágrafo\n'))
        code, out = self.commit_note('work/issues/uma.md')
        self.assertEqual(code, 0, out)
        self.assertEqual(len(self.origin_index()), 1)
        self.assertEqual(self.origin_master_log(), log)

    def test_commit_of_a_changed_h1_or_description_appends_a_changed_line(self):
        self.write('work/issues/uma.md', note_text())
        self.commit_note('work/issues/uma.md')
        self.write('work/issues/uma.md', note_text(h1='Outro título', description='Outra frase.'))
        self.commit_note('work/issues/uma.md')
        last = self.origin_index()[-1]
        self.assertEqual((last['event'], last['title'], last['description']), ('changed', 'Outro título',
                                                                               'Outra frase.'))

    def test_a_rename_appends_a_changed_line_with_the_new_path_and_the_id_of_the_creation(self):
        self.write('work/issues/velho.md', note_text(h1='A issue'))
        self.commit_note('work/issues/velho.md')
        git('mv', 'work/issues/velho.md', 'work/issues/novo-nome.md', cwd=self.tree)
        code, out = self.commit_note('work/issues/velho.md', 'work/issues/novo-nome.md')
        self.assertEqual(code, 0, out)
        last = self.origin_index()[-1]
        self.assertEqual((last['event'], last['id'], last['path']), ('changed', 'issue:velho',
                                                                      'work/issues/novo-nome.md'))
        self.write('work/issues/novo-nome.md', note_text(h1='A issue', body='texto\n'))
        self.commit_note('work/issues/novo-nome.md')
        self.assertEqual(len(self.origin_index()), 2)

    def test_commit_of_a_plan_after_its_plan_open_appends_nothing_when_nothing_differs(self):
        self.run_note('plan-open', 'novo', '--title', 'O plano', '--description', 'Faz isto.')
        self.write('plans/PLN-001-novo/PLN-001-plan.md',
                   '---\nstatus: open\nphase: interview\ndescription: Faz isto.\nopened: 2026-10-31\n---\n\n'
                   '# PLN-001: O plano\n\n- **Retomar:** x\n')
        code, out = self.commit_note('plans/PLN-001-novo')
        self.assertEqual(code, 0, out)
        self.assertEqual(len(self.origin_index()), 1)
        self.write('plans/PLN-001-novo/PLN-001-plan.md',
                   '---\nstatus: in_progress\nphase: execution\ndescription: Faz isto.\nopened: 2026-10-31\n---\n\n'
                   '# PLN-001: O plano\n\n- **Retomar:** x\n')
        self.commit_note('plans/PLN-001-novo')
        last = self.origin_index()[-1]
        self.assertEqual((last['event'], last['id'], last['status'], last['phase']),
                         ('changed', 'PLN-001', 'in_progress', 'execution'))

    def test_commit_of_something_that_is_not_a_plan_issue_or_decision_appends_nothing(self):
        self.write('work/worklogs/2026-10-31-x.md', '---\nscope:\n  - workspace\n---\n\n# Um worklog\n')
        self.write('plans/PLN-001-x/PLN-001-design.md', '# desenho\n')
        code, out = self.commit_note('work/worklogs/2026-10-31-x.md', 'plans/PLN-001-x/PLN-001-design.md')
        self.assertEqual(code, 0, out)
        self.assertEqual(self.origin_index(), [])

    def test_a_note_without_description_enters_the_index_with_the_h1(self):
        self.write('work/issues/antiga.md', note_text(description='', h1='A issue antiga'))
        self.commit_note('work/issues/antiga.md')
        self.assertEqual(self.origin_index()[0]['description'], 'A issue antiga')

    def test_commit_no_push_writes_no_index_line(self):
        self.write('work/issues/uma.md', note_text())
        code, out = self.run_note('commit', 'work/issues/uma.md', '-m', 'x', '--no-push')
        self.assertEqual(code, 0, out)
        self.assertEqual(self.origin_index(), [])

    def test_a_failed_index_push_warns_and_exits_zero_with_the_note_already_on_the_workstream(self):
        hook = self.origin / 'hooks' / 'pre-receive'
        hook.write_text('#!/bin/sh\nwhile read old new ref; do [ "$ref" = refs/heads/master ] && exit 1; done\nexit 0\n')
        hook.chmod(0o755)
        self.write('work/issues/uma.md', note_text())
        code, out = self.commit_note('work/issues/uma.md')
        self.assertEqual(code, 0, out)
        self.assertIn('aviso: índice não gravado; rode brza note index --rebuild', out)
        self.assertIn('docs: nota', self.origin_log())
        self.assertEqual(self.origin_index(), [])

    # o union e o estado vigente

    def test_two_lines_appended_in_parallel_join_by_the_union_without_conflict(self):
        a, b = self.clone('a'), self.clone('b')
        for repo, id_ in ((a, 'PLN-001'), (b, 'PLN-002')):
            with (repo / INDEX).open('a') as f:
                f.write(json.dumps({'ts': '2026-10-31T10:00:00-03:00', 'event': 'created', 'id': id_}) + '\n')
            git('commit', '-q', '-am', id_, cwd=repo)
        git('push', '-q', 'origin', 'HEAD:master', cwd=a)
        git('pull', '-q', '--no-rebase', '--no-edit', cwd=b)
        self.assertEqual(sorted(json.loads(x)['id'] for x in (b / INDEX).read_text().splitlines()),
                         ['PLN-001', 'PLN-002'])

    def test_without_the_gitattributes_the_same_parallel_append_conflicts(self):
        git('rm', '-q', '.gitattributes', cwd=self.ws)
        git('commit', '-q', '-m', 'sem attributes', cwd=self.ws)
        git('push', '-q', 'origin', 'HEAD:master', cwd=self.ws)
        a, b = self.clone('a'), self.clone('b')
        for repo, id_ in ((a, 'PLN-001'), (b, 'PLN-002')):
            with (repo / INDEX).open('a') as f:
                f.write(json.dumps({'id': id_}) + '\n')
            git('commit', '-q', '-am', id_, cwd=repo)
        git('push', '-q', 'origin', 'HEAD:master', cwd=a)
        r = subprocess.run(['git', '-C', str(b), 'pull', '-q', '--no-rebase', '--no-edit'], capture_output=True)
        self.assertNotEqual(r.returncode, 0)

    def test_the_current_state_of_an_id_is_the_line_with_the_highest_ts_wherever_it_sits_in_the_file(self):
        lines = [
            {'ts': '2026-10-31T12:00:00-03:00', 'event': 'changed', 'id': 'PLN-001', 'status': 'in_progress'},
            {'ts': '2026-10-31T10:00:00-03:00', 'event': 'created', 'id': 'PLN-001', 'status': 'open'},
            {'ts': '2026-10-31T13:00:00-03:00', 'event': 'created', 'id': 'issue:x', 'status': 'open'},
            # o fuso muda o texto, não o instante: 16:30+00:00 são 13:30-03:00, antes dos 14:00-03:00, embora o texto
            # dele seja maior
            {'ts': '2026-10-31T14:00:00-03:00', 'event': 'changed', 'id': 'issue:x', 'status': 'done'},
            {'ts': '2026-10-31T16:30:00+00:00', 'event': 'changed', 'id': 'issue:x', 'status': 'open'},
        ]
        text = '\n'.join(json.dumps(x) for x in lines) + '\n'
        state = GLOBALS['current_state'](text)
        self.assertEqual(state['PLN-001']['status'], 'in_progress')
        self.assertEqual(state['issue:x']['status'], 'done')

    def test_the_current_state_on_the_same_ts_is_the_later_line_of_the_file(self):
        ts = '2026-10-31T10:00:00-03:00'
        text = ''.join(json.dumps(x) + '\n' for x in ({'ts': ts, 'id': 'a', 'status': 'open'},
                                                       {'ts': ts, 'id': 'a', 'status': 'done'}))
        self.assertEqual(GLOBALS['current_state'](text)['a']['status'], 'done')

    def test_the_current_state_ignores_blank_and_broken_lines(self):
        text = '\n{"ts":"2026-10-31T10:00:00-03:00","id":"a","status":"open"}\nnão é json\n'
        self.assertEqual(list(GLOBALS['current_state'](text)), ['a'])

    # index --rebuild

    def test_rebuild_adds_a_created_line_for_every_plan_issue_and_decision_of_every_workstream(self):
        tools = self.make_workstream('tools')
        self.write('plans/PLN-001-x/PLN-001-plan.md',
                   '---\nstatus: open\nphase: design\ndescription: O plano.\n---\n\n# PLN-001: O plano um\n\n'
                   '- **Retomar:** x\n')
        git('add', '.', cwd=self.tree)
        git('commit', '-q', '-m', 'p', cwd=self.tree)
        git('push', '-q', cwd=self.tree)
        self.write('work/issues/i.md', note_text(h1='A issue', description='Frase da issue.'), tree=tools)
        self.write('work/decisions/d.md', note_text(status='done', h1='A decisão', description=''), tree=tools)
        self.write('work/worklogs/2026-10-31-w.md', '---\nscope:\n  - workspace\n---\n\n# W\n', tree=tools)
        git('add', '.', cwd=tools)
        git('commit', '-q', '-m', 'n', cwd=tools)
        git('push', '-q', cwd=tools)
        code, out = self.run_note('index', '--rebuild', workstream=None)
        self.assertEqual(code, 0, out)
        by_id = {x['id']: x for x in self.origin_index()}
        self.assertEqual(sorted(by_id), ['PLN-001', 'decision:d', 'issue:i'])
        self.assertEqual(by_id['PLN-001']['workstream'], 'studio')
        self.assertEqual((by_id['PLN-001']['title'], by_id['PLN-001']['phase'], by_id['PLN-001']['description']),
                         ('O plano um', 'design', 'O plano.'))
        self.assertEqual((by_id['issue:i']['workstream'], by_id['issue:i']['description']), ('tools', 'Frase da issue.'))
        self.assertEqual(by_id['decision:d']['description'], 'A decisão')  # sem o campo, o H1
        self.assertTrue(all(x['event'] == 'created' for x in by_id.values()))

    def test_rebuild_is_idempotent_and_keeps_the_ids_already_in_the_index(self):
        self.write('work/issues/i.md', note_text())
        self.commit_note('work/issues/i.md')
        self.run_note('index', '--rebuild', workstream=None)  # o PLN-001 do fixture entra aqui
        before = self.origin_index()
        code, out = self.run_note('index', '--rebuild', workstream=None)
        self.assertEqual(code, 0, out)
        self.assertEqual(self.origin_index(), before)
        self.write('work/issues/j.md', note_text(h1='J'))
        git('add', '.', cwd=self.tree)
        git('commit', '-q', '-m', 'j', cwd=self.tree)
        git('push', '-q', cwd=self.tree)
        self.run_note('index', '--rebuild', workstream=None)
        self.run_note('index', '--rebuild', workstream=None)
        self.assertEqual(sorted(x['id'] for x in self.origin_index()), ['PLN-001', 'issue:i', 'issue:j'])

    def test_rebuild_does_not_duplicate_a_renamed_note(self):
        self.write('work/issues/velho.md', note_text())
        self.commit_note('work/issues/velho.md')
        git('mv', 'work/issues/velho.md', 'work/issues/novo.md', cwd=self.tree)
        self.commit_note('work/issues/velho.md', 'work/issues/novo.md')
        self.run_note('index', '--rebuild', workstream=None)
        self.assertEqual({x['id'] for x in self.origin_index()}, {'PLN-001', 'issue:velho'})

    def test_rebuild_reads_a_workstream_that_only_exists_on_the_origin(self):
        self.write('work/issues/i.md', note_text())
        git('add', '.', cwd=self.tree)
        git('commit', '-q', '-m', 'i', cwd=self.tree)
        git('push', '-q', cwd=self.tree)
        git('worktree', 'remove', '--force', str(self.tree), cwd=self.ws)
        git('branch', '-q', '-D', 'workstream/studio', cwd=self.ws)
        code, out = self.run_note('index', '--rebuild', workstream=None)
        self.assertEqual(code, 0, out)
        self.assertEqual(sorted(x['id'] for x in self.origin_index()), ['PLN-001', 'issue:i'])

    def test_rebuild_with_nothing_missing_writes_no_commit(self):
        self.run_note('index', '--rebuild', workstream=None)
        log = self.origin_master_log()
        code, out = self.run_note('index', '--rebuild', workstream=None)
        self.assertEqual(code, 0, out)
        self.assertIn('em dia', out)
        self.assertEqual(self.origin_master_log(), log)

    def test_index_without_rebuild_is_wrong_usage(self):
        code, _ = self.run_note('index', workstream=None)
        self.assertEqual(code, 2)
