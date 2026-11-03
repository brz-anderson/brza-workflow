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


class IndexFixture(NoteFixture):
    """O workspace com o índice da master vazio e os ajudantes; sem teste próprio, como o NoteFixture."""

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


class IndexTest(IndexFixture):
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
        self.write_manifest()  # a BRZA_WORKSPACE só vale com manifesto (A5: sem o legado de nome fixo)
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
        # D7: o primeiro commit registra a passagem de reserva (sem commit) para nota materializada (com commit)
        self.assertEqual(len(self.origin_index()), 2)
        self.assertEqual(self.origin_index()[-1]['event'], 'changed')
        self.assertTrue(self.origin_index()[-1].get('commit'))
        # segundo commit sem mudar frontmatter não acrescenta linha
        self.write('plans/PLN-001-novo/PLN-001-plan.md',
                   '---\nstatus: open\nphase: interview\ndescription: Faz isto.\nopened: 2026-10-31\n---\n\n'
                   '# PLN-001: O plano\n\n- **Retomar:** y\n')
        self.commit_note('plans/PLN-001-novo')
        self.assertEqual(len(self.origin_index()), 2)
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


class IndexRepairTest(IndexFixture):
    """O `--rebuild` que repara o `changed` perdido (A4 do P9) e a recusa do id que é de outro workstream (A7)."""

    def issue_text(self, status='open', h1='Uma'):
        return note_text(status=status, h1=h1)

    def push_other_commit(self):
        """Um commit com push que não toca a nota: o `changed_notes` lê só o HEAD, e a mudança sem linha fica."""
        self.write('work/worklogs/2026-10-31-w.md', '---\nscope:\n  - workspace\n---\n\n# W\n')
        code, out = self.commit_note('work/worklogs/2026-10-31-w.md')
        self.assertEqual(code, 0, out)

    def test_rebuild_appends_the_changed_line_that_a_no_push_commit_left_out(self):
        self.write('work/issues/uma-issue.md', self.issue_text())
        self.commit_note('work/issues/uma-issue.md')
        self.write('work/issues/uma-issue.md', self.issue_text(status='in_progress'))
        code, out = self.run_note('commit', 'work/issues/uma-issue.md', '-m', 'docs: andamento', '--no-push')
        self.assertEqual(code, 0, out)
        self.push_other_commit()
        self.assertEqual([x['status'] for x in self.origin_index()], ['open'])  # o aviso manda rodar o --rebuild
        code, out = self.run_note('index', '--rebuild', workstream=None)
        self.assertEqual(code, 0, out)
        self.assertNotIn('em dia', out)
        lines = [x for x in self.origin_index() if x['id'] == 'issue:uma-issue']
        self.assertEqual([(x['event'], x['status'], x['workstream']) for x in lines],
                         [('created', 'open', 'studio'), ('changed', 'in_progress', 'studio')])
        self.assertEqual(GLOBALS['current_state'](git('show', f'master:{INDEX}', cwd=self.origin))
                         ['issue:uma-issue']['status'], 'in_progress')

    def test_rebuild_after_the_repair_is_idempotent(self):
        self.write('work/issues/uma-issue.md', self.issue_text())
        self.commit_note('work/issues/uma-issue.md')
        self.write('work/issues/uma-issue.md', self.issue_text(h1='Outro título'))
        self.run_note('commit', 'work/issues/uma-issue.md', '-m', 'x', '--no-push')
        self.push_other_commit()
        self.run_note('index', '--rebuild', workstream=None)
        before = self.origin_index()
        code, out = self.run_note('index', '--rebuild', workstream=None)
        self.assertEqual(code, 0, out)
        self.assertIn('em dia', out)
        self.assertEqual(self.origin_index(), before)

    def test_rebuild_repairs_the_phase_of_a_plan_whose_index_line_failed(self):
        self.run_note('plan-open', 'novo', '--title', 'O plano', '--description', 'Faz isto.')
        self.write('plans/PLN-001-novo/PLN-001-plan.md',
                   '---\nstatus: in_progress\nphase: execution\ndescription: Faz isto.\nopened: 2026-10-31\n---\n\n'
                   '# PLN-001: O plano\n\n- **Retomar:** x\n')
        self.run_note('commit', 'plans/PLN-001-novo', '-m', 'x', '--no-push')
        self.push_other_commit()
        self.run_note('index', '--rebuild', workstream=None)
        last = [x for x in self.origin_index() if x['id'] == 'PLN-001'][-1]
        self.assertEqual((last['event'], last['status'], last['phase']), ('changed', 'in_progress', 'execution'))

    def test_rebuild_leaves_alone_the_id_that_the_index_gives_to_another_workstream(self):
        tools = self.make_workstream('tools')
        self.write('work/issues/dupla.md', self.issue_text(h1='Do studio'))
        self.commit_note('work/issues/dupla.md')
        self.write('work/issues/dupla.md', self.issue_text(h1='Da tools'), tree=tools)
        git('add', '.', cwd=tools)
        git('commit', '-q', '-m', 'dupla na tools', cwd=tools)
        git('push', '-q', cwd=tools)
        before = self.origin_index()
        self.run_note('index', '--rebuild', workstream=None)
        self.run_note('index', '--rebuild', workstream=None)
        mine = [x for x in self.origin_index() if x['id'] == 'issue:dupla']
        self.assertEqual([(x['event'], x['workstream'], x['title']) for x in mine], [('created', 'studio', 'Do studio')])
        self.assertEqual([x for x in self.origin_index() if x['id'] != 'PLN-001'],
                         [x for x in before if x['id'] != 'PLN-001'])

    # o id de outro workstream

    def test_commit_refuses_a_new_note_whose_id_belongs_to_another_workstream(self):
        tools = self.make_workstream('tools')
        self.write('work/issues/uma-issue.md', self.issue_text(h1='Do studio'))
        self.commit_note('work/issues/uma-issue.md')
        index = self.origin_index()
        self.write('work/issues/uma-issue.md', self.issue_text(h1='Outra, da tools'), tree=tools)
        before = git('rev-parse', 'HEAD', cwd=tools)
        code, out = self.run_note('commit', 'work/issues/uma-issue.md', '-m', 'docs: repetida', workstream='tools')
        self.assertEqual(code, 1, out)
        self.assertIn('recusado', out)
        self.assertIn('issue:uma-issue', out)
        self.assertIn('studio', out)
        self.assertEqual(git('rev-parse', 'HEAD', cwd=tools), before)  # nada commitado
        self.assertEqual(git('status', '--porcelain', '--untracked-files=all', cwd=tools), '?? work/issues/uma-issue.md')
        self.assertEqual(self.origin_index(), index)  # e o índice não mudou: a issue do studio segue vigente
        self.assertNotIn('docs: repetida', self.origin_log('workstream/tools'))

    def test_commit_refuses_a_new_decision_whose_id_belongs_to_another_workstream(self):
        tools = self.make_workstream('tools')
        self.write('work/decisions/d.md', note_text(h1='A decisão'))
        self.commit_note('work/decisions/d.md')
        self.write('work/decisions/d.md', note_text(h1='Outra'), tree=tools)
        code, out = self.run_note('commit', 'work/decisions/d.md', '-m', 'x', workstream='tools')
        self.assertEqual(code, 1, out)
        self.assertIn('decision:d', out)

    def test_commit_of_a_change_to_the_note_the_workstream_already_owns_is_not_refused(self):
        tools = self.make_workstream('tools')
        self.write('work/issues/minha.md', self.issue_text(), tree=tools)
        code, out = self.run_note('commit', 'work/issues/minha.md', '-m', 'x', workstream='tools')
        self.assertEqual(code, 0, out)
        self.write('work/issues/minha.md', self.issue_text(status='done'), tree=tools)
        code, out = self.run_note('commit', 'work/issues/minha.md', '-m', 'y', workstream='tools')
        self.assertEqual(code, 0, out)
        self.assertEqual([x['event'] for x in self.origin_index()], ['created', 'changed'])

    def test_commit_of_a_note_moved_from_another_workstream_is_not_refused(self):
        # A nota saiu do studio (apagada e commitada lá): o id continua o da criação, e o workstream passa a ser o novo.
        tools = self.make_workstream('tools')
        self.write('work/issues/mudou.md', self.issue_text())
        self.commit_note('work/issues/mudou.md')
        git('rm', '-q', 'work/issues/mudou.md', cwd=self.tree)
        code, out = self.commit_note('work/issues/mudou.md')
        self.assertEqual(code, 0, out)
        self.write('work/issues/mudou.md', self.issue_text(h1='Mudou de workstream'), tree=tools)
        code, out = self.run_note('commit', 'work/issues/mudou.md', '-m', 'docs: veio do studio', workstream='tools')
        self.assertEqual(code, 0, out)
        last = self.origin_index()[-1]
        self.assertEqual((last['event'], last['id'], last['workstream']), ('changed', 'issue:mudou', 'tools'))

    def test_the_check_of_the_id_is_skipped_with_no_push_and_without_a_remote_index(self):
        tools = self.make_workstream('tools')
        self.write('work/issues/uma-issue.md', self.issue_text())
        self.commit_note('work/issues/uma-issue.md')
        self.write('work/issues/uma-issue.md', self.issue_text(h1='Outra'), tree=tools)
        code, out = self.run_note('commit', 'work/issues/uma-issue.md', '-m', 'x', '--no-push', workstream='tools')
        self.assertEqual(code, 0, out)  # sem push, o commit não escreve no índice nem consulta a origin


class IndexOtherBranchTest(NoteFixture):
    """Um workspace cuja norma mora na `main` do remote `up` (A5 do P9): o índice e o push seguem o manifesto."""
    BRANCH, REMOTE = 'main', 'up'

    def origin_index(self, ref: str = 'main') -> list[dict]:
        try:
            text = git('show', f'{ref}:{INDEX}', cwd=self.origin)
        except subprocess.CalledProcessError:
            return []
        return [json.loads(line) for line in text.splitlines() if line.strip()]

    def test_plan_open_reserves_the_number_on_the_up_main(self):
        code, out = self.run_note('plan-open', 'novo', '--title', 'Novo', '--description', 'x')
        self.assertEqual(code, 0, out)
        self.assertEqual(out.strip().splitlines()[-2], 'PLN-001')
        self.assertEqual([x['id'] for x in self.origin_index()], ['PLN-001'])
        self.assertEqual(git('log', '-1', '--format=%s', 'main', cwd=self.origin), 'índice: PLN-001 created')

    def test_commit_writes_the_index_line_on_the_up_main(self):
        (self.tree / 'work' / 'issues').mkdir(parents=True, exist_ok=True)
        (self.tree / 'work' / 'issues' / 'uma.md').write_text(note_text(extra='priority: medium\n'))
        code, out = self.run_note('commit', 'work/issues/uma.md', '-m', 'docs: uma')
        self.assertEqual(code, 0, out)
        self.assertNotIn('aviso', out)
        self.assertIn('issue:uma created na up/main', out)
        self.assertEqual([x['id'] for x in self.origin_index()], ['issue:uma'])

    def test_commit_without_upstream_pushes_the_workstream_to_the_manifest_remote(self):
        git('branch', '--unset-upstream', cwd=self.tree)
        (self.tree / 'work' / 'issues').mkdir(parents=True, exist_ok=True)
        (self.tree / 'work' / 'issues' / 'uma.md').write_text(note_text())
        code, out = self.run_note('commit', 'work/issues/uma.md', '-m', 'docs: uma')
        self.assertEqual(code, 0, out)
        self.assertEqual(git('rev-parse', '--abbrev-ref', '@{upstream}', cwd=self.tree), 'up/workstream/studio')

    def test_rebuild_reads_the_workstreams_of_the_manifest_remote(self):
        (self.tree / 'work' / 'issues').mkdir(parents=True, exist_ok=True)
        (self.tree / 'work' / 'issues' / 'i.md').write_text(note_text())
        git('add', '.', cwd=self.tree)
        git('commit', '-q', '-m', 'i', cwd=self.tree)
        git('push', '-q', cwd=self.tree)
        git('worktree', 'remove', '--force', str(self.tree), cwd=self.ws)
        git('branch', '-q', '-D', 'workstream/studio', cwd=self.ws)
        code, out = self.run_note('index', '--rebuild', workstream=None)
        self.assertEqual(code, 0, out)
        self.assertEqual(sorted(x['id'] for x in self.origin_index()), ['PLN-001', 'issue:i'])

    def test_a_missing_remote_is_named_in_the_refusal(self):
        git('remote', 'remove', 'up', cwd=self.ws)
        code, out = self.run_note('plan-open', 'novo', '--title', 'Novo', '--description', 'x')
        self.assertEqual(code, 1, out)
        self.assertIn('recusado', out)
        self.assertIn('remote up', out)


class Pln008P2IndexAndLsTest(IndexFixture):
    """Testes do P2 do PLN-008: review-open, migration-open, bootstrap atômico, campos D9, aviso de priority e ls."""

    def test_review_open_and_migration_open_reserve_independent_sequences_and_strip_prefixes(self):
        c1, out1 = self.run_note('review-open', 'rev-um', '--title', 'REV-001: Primeira revisão',
                                 '--description', 'Desc rev 1.')
        self.assertEqual(c1, 0, out1)
        self.assertEqual(out1.strip().splitlines()[-2:], ['REV-001', 'work/reviews/REV-001-rev-um.md'])

        c2, out2 = self.run_note('migration-open', 'mig-um', '--title', 'MIG-001: Primeira migração',
                                 '--description', 'Desc mig 1.')
        self.assertEqual(c2, 0, out2)
        self.assertEqual(out2.strip().splitlines()[-2:], ['MIG-001', 'work/issues/MIG-001-mig-um.md'])

        c3, out3 = self.run_note('review-open', 'rev-dois', '--title', 'REV: Segunda revisão',
                                 '--description', 'Desc rev 2.')
        self.assertEqual(c3, 0, out3)
        self.assertEqual(out3.strip().splitlines()[-2:], ['REV-002', 'work/reviews/REV-002-rev-dois.md'])

        by_id = {x['id']: x for x in self.origin_index()}
        self.assertEqual(by_id['REV-001']['kind'], 'review')
        self.assertEqual(by_id['REV-001']['title'], 'Primeira revisão')
        self.assertEqual(by_id['REV-001']['status'], 'in_progress')
        self.assertNotIn('commit', by_id['REV-001'])

        self.assertEqual(by_id['REV-002']['title'], 'Segunda revisão')

        self.assertEqual(by_id['MIG-001']['kind'], 'issue')
        self.assertEqual(by_id['MIG-001']['type'], 'migration')
        self.assertEqual(by_id['MIG-001']['title'], 'Primeira migração')
        self.assertEqual(by_id['MIG-001']['status'], 'open')
        self.assertNotIn('commit', by_id['MIG-001'])

    def test_review_open_and_migration_open_skip_concurrent_numbers_and_give_up_after_three_refusals(self):
        other = self.clone('other')
        real_push, calls = GLOBALS['index_push'], []

        def push_after_rival(repo, sha):
            calls.append(sha)
            if len(calls) == 1:
                with (other / INDEX).open('a') as f:
                    f.write(json.dumps({'ts': '2026-10-31T10:00:00-03:00', 'event': 'created', 'kind': 'review',
                                        'id': 'REV-001', 'path': 'work/reviews/REV-001-rival.md'}) + '\n')
                git('commit', '-q', '-am', 'rival', cwd=other)
                git('push', '-q', 'origin', 'HEAD:master', cwd=other)
            return real_push(repo, sha)

        with patch.dict(GLOBALS, {'index_push': push_after_rival}):
            code, out = self.run_note('review-open', 'minha', '--title', 'Minha', '--description', 'x')
        self.assertEqual(code, 0, out)
        self.assertEqual(out.strip().splitlines()[-2], 'REV-002')

        fail_calls = []

        def always_fail(repo, sha):
            fail_calls.append(sha)
            raise GLOBALS['runtime'].BrzaError('rejeitado')

        with patch.dict(GLOBALS, {'index_push': always_fail}):
            code, out = self.run_note('migration-open', 'falha', '--title', 'Falha', '--description', 'x')
        self.assertEqual(code, 1, out)
        self.assertIn('recusado', out)
        self.assertEqual(len(fail_calls), 3)

    def test_bootstrap_pln008_is_atomic_idempotent_and_refuses_conflicts(self):
        code, out = self.run_note('index', '--bootstrap-pln008', workstream=None)
        self.assertEqual(code, 0, out)
        lines = self.origin_index()
        self.assertEqual([x['id'] for x in lines], ['REV-001', 'REV-002', 'REV-003', 'REV-004', 'MIG-001'])
        self.assertTrue(all('commit' not in x for x in lines))
        log_before = self.origin_master_log()

        # Reexecução com o mesmo mapa é no-op
        code2, out2 = self.run_note('index', '--bootstrap-pln008', workstream=None)
        self.assertEqual(code2, 0, out2)
        self.assertIn('bootstrap em dia', out2)
        self.assertEqual(self.origin_master_log(), log_before)

        # Reserva subsequente parte de REV-005 e MIG-002
        cr, out_r = self.run_note('review-open', 'nova', '--title', 'Nova', '--description', 'x')
        self.assertEqual(cr, 0, out_r)
        self.assertEqual(out_r.strip().splitlines()[-2], 'REV-005')
        cm, out_m = self.run_note('migration-open', 'nova', '--title', 'Nova', '--description', 'x')
        self.assertEqual(cm, 0, out_m)
        self.assertEqual(out_m.strip().splitlines()[-2], 'MIG-002')

        # Colisão com outro workstream/path recusa explicitamente
        other = self.clone('conflict')
        with (other / INDEX).open('a') as f:
            f.write(json.dumps({
                'ts': '2099-01-01T00:00:00-03:00',
                'event': 'changed',
                'kind': 'review',
                'id': 'REV-001',
                'workstream': 'studio',
                'path': 'work/reviews/REV-001-outro.md',
            }) + '\n')
        git('commit', '-q', '-am', 'colisao', cwd=other)
        git('push', '-q', 'origin', 'HEAD:master', cwd=other)
        code3, out3 = self.run_note('index', '--bootstrap-pln008', workstream=None)
        self.assertEqual(code3, 1, out3)
        self.assertIn('colisão no bootstrap de REV-001', out3)

    def test_d9_fields_session_and_priority_warning_on_commit_and_rebuild(self):
        # 1. Aviso de priority ausente em issue/plano aberto sem bloquear commit
        self.write('work/issues/sem-prio.md', note_text(status='open', h1='Sem prioridade'))
        code, out = self.commit_note('work/issues/sem-prio.md')
        self.assertEqual(code, 0, out)
        self.assertIn('aviso: sem priority: work/issues/sem-prio.md', out)

        # 2. Todos os campos D9 na issue (incluindo depends_on lista e estimate_h número) e session (D5)
        issue_rel = 'work/issues/completa.md'
        self.write(
            issue_rel,
            '---\n'
            'status: in_progress\n'
            'description: Issue completa.\n'
            'scope:\n'
            '  - workspace\n'
            'opened: 2026-11-01\n'
            'priority: high\n'
            'blocks: PLN-001\n'
            'deadline: 2026-11-10\n'
            'owner: Anderson\n'
            'type: unknown\n'
            'depends_on:\n'
            '  - issue:sem-prio\n'
            'estimate_h: 2.5\n'
            '---\n\n'
            '# Issue Completa\n\n'
            '## O pedido\nTexto.\n',
        )
        # Fecha a dependência antes para o checador real (se usado) aceitar; no NoteFixture o checador é stub 0
        with patch.dict(os.environ, {'CLAUDE_SESSION_NAME': 'sessao-commit'}):
            code, out = self.commit_note(issue_rel)
        self.assertEqual(code, 0, out)
        self.assertNotIn('aviso: sem priority', out)
        state = GLOBALS['current_state'](git('show', f'master:{INDEX}', cwd=self.origin))
        item = state['issue:completa']
        self.assertEqual(item['opened'], '2026-11-01')
        self.assertEqual(item['priority'], 'high')
        self.assertEqual(item['blocks'], 'PLN-001')
        self.assertEqual(item['deadline'], '2026-11-10')
        self.assertEqual(item['owner'], 'Anderson')
        self.assertEqual(item['type'], 'unknown')
        self.assertEqual(item['depends_on'], ['issue:sem-prio'])
        self.assertEqual(item['estimate_h'], 2.5)
        self.assertEqual(item['session'], 'sessao-commit')

        # 3. Mudança de um único campo D9 (deadline) dispara `changed`
        self.write(issue_rel, (self.tree / issue_rel).read_text().replace('2026-11-10', '2026-11-15'))
        code, out = self.commit_note(issue_rel)
        self.assertEqual(code, 0, out)
        state = GLOBALS['current_state'](git('show', f'master:{INDEX}', cwd=self.origin))
        self.assertEqual(state['issue:completa']['event'], 'changed')
        self.assertEqual(state['issue:completa']['deadline'], '2026-11-15')

        # 4. Plano com estimate_h e remaining_h numéricos + rebuild registrando session da operação (D5)
        plan_rel = 'plans/PLN-001-x/PLN-001-plan.md'
        self.write(
            plan_rel,
            '---\n'
            'status: in_progress\n'
            'phase: execution\n'
            'description: Plano X.\n'
            'opened: 2026-10-30\n'
            'priority: high\n'
            'estimate_h: 8\n'
            'remaining_h: 3.5\n'
            '---\n\n'
            '# PLN-001: Plano X\n',
        )
        git('add', plan_rel, cwd=self.tree)
        git('commit', '-q', '-m', 'docs: plan d9', cwd=self.tree)
        git('push', '-q', cwd=self.tree)

        with patch.dict(os.environ, {'CLAUDE_SESSION_NAME': 'sessao-rebuild'}):
            code, out = self.run_note('index', '--rebuild', workstream=None)
        self.assertEqual(code, 0, out)
        state = GLOBALS['current_state'](git('show', f'master:{INDEX}', cwd=self.origin))
        p_item = state['PLN-001']
        self.assertEqual(p_item['estimate_h'], 8)
        self.assertEqual(p_item['remaining_h'], 3.5)
        self.assertEqual(p_item['priority'], 'high')
        self.assertEqual(p_item['session'], 'sessao-rebuild')

        # Rebuild subsequente é idempotente
        before = self.origin_index()
        code, out = self.run_note('index', '--rebuild', workstream=None)
        self.assertEqual(code, 0, out)
        self.assertEqual(self.origin_index(), before)

    def test_ls_filters_sorts_excludes_unmaterialized_reservations_and_handles_refresh(self):
        # Reserva MIG-001 sem materializar arquivo: não pode aparecer no ls
        self.run_note('migration-open', 'reserva-pura', '--title', 'Reserva pura', '--description', 'Sem arquivo.')

        # Cria issues e decisões com diferentes datas, prioridades, deadlines e status
        self.write(
            'work/issues/i-antiga.md',
            note_text(status='open', h1='Issue Antiga', extra='priority: low\ndeadline: 2026-11-20\n').replace(
                'opened: 2026-10-31', 'opened: 2026-10-10'
            ),
        )
        self.write(
            'work/issues/i-recente.md',
            note_text(status='in_progress', h1='Issue Recente', extra='priority: high\ndeadline: 2026-11-05\n').replace(
                'opened: 2026-10-31', 'opened: 2026-11-02'
            ),
        )
        self.write(
            'work/decisions/d-meio.md',
            note_text(status='in_review', h1='Decisão Meio', extra='priority: medium\n').replace(
                'opened: 2026-10-31', 'opened: 2026-10-20'
            ),
        )
        self.write(
            'work/issues/i-fechada.md',
            note_text(status='done', h1='Issue Fechada', extra='priority: high\n'),
        )
        self.commit_note(
            'work/issues/i-antiga.md',
            'work/issues/i-recente.md',
            'work/decisions/d-meio.md',
            'work/issues/i-fechada.md',
        )
        # Atualiza origin/master local no workspace
        git('fetch', '-q', 'origin', cwd=self.ws)

        # 1. Padrão: apenas abertas (open, in_progress, in_review), ordenadas por opened desc, sem fetch
        calls, real_git = [], GLOBALS['runtime'].git

        def spy_git(*args, **kwargs):
            calls.append(args)
            return real_git(*args, **kwargs)

        with patch.object(GLOBALS['runtime'], 'git', spy_git):
            code, out = self.run_note('ls', workstream=None)
        self.assertEqual(code, 0, out)
        self.assertNotIn('fetch', [a[0] for a in calls])
        rows = [line.split()[0] for line in out.strip().splitlines()[1:]]
        self.assertEqual(rows, ['issue:i-recente', 'decision:d-meio', 'issue:i-antiga'])
        self.assertNotIn('MIG-001', out)
        self.assertNotIn('issue:i-fechada', out)

        # 2. --status all inclui done
        code, out_all = self.run_note('ls', '--status', 'all', workstream=None)
        self.assertEqual(code, 0, out_all)
        rows_all = [line.split()[0] for line in out_all.strip().splitlines()[1:]]
        self.assertEqual(rows_all, ['issue:i-recente', 'issue:i-fechada', 'decision:d-meio', 'issue:i-antiga'])

        # 3. --sort priority (high -> medium -> low, ausente no fim, desempate por ID asc)
        code, out_prio = self.run_note('ls', '--sort', 'priority', workstream=None)
        self.assertEqual(code, 0, out_prio)
        rows_prio = [line.split()[0] for line in out_prio.strip().splitlines()[1:]]
        self.assertEqual(rows_prio, ['issue:i-recente', 'decision:d-meio', 'issue:i-antiga'])

        # 4. --sort deadline (menor data primeiro, sem deadline no fim)
        code, out_dl = self.run_note('ls', '--sort', 'deadline', workstream=None)
        self.assertEqual(code, 0, out_dl)
        rows_dl = [line.split()[0] for line in out_dl.strip().splitlines()[1:]]
        self.assertEqual(rows_dl, ['issue:i-recente', 'issue:i-antiga', 'decision:d-meio'])

        # 5. --refresh busca atualizações remotas; falha de fetch sai com código 1 sem fallback silencioso
        other = self.clone('other-ls')
        with (other / INDEX).open('a') as f:
            f.write(json.dumps({
                'ts': '2026-11-03T10:00:00-03:00',
                'event': 'created',
                'kind': 'issue',
                'id': 'issue:remota',
                'path': 'work/issues/remota.md',
                'title': 'Remota',
                'description': 'Remota.',
                'status': 'open',
                'opened': '2026-11-03',
                'workstream': 'studio',
                'commit': 'abc1234',
            }) + '\n')
        git('commit', '-q', '-am', 'remota', cwd=other)
        git('push', '-q', 'origin', 'HEAD:master', cwd=other)

        # Sem --refresh não vê 'issue:remota'
        _, out_local = self.run_note('ls', workstream=None)
        self.assertNotIn('issue:remota', out_local)
        # Com --refresh vê 'issue:remota' no topo
        code, out_ref = self.run_note('ls', '--refresh', workstream=None)
        self.assertEqual(code, 0, out_ref)
        self.assertEqual(out_ref.strip().splitlines()[1].split()[0], 'issue:remota')

        # Sem remote, --refresh falha explicitamente (sem fallback para o local)
        git('remote', 'remove', 'origin', cwd=self.ws)
        code_fail, out_fail = self.run_note('ls', '--refresh', workstream=None)
        self.assertEqual(code_fail, 1, out_fail)
        self.assertIn('recusado', out_fail)

    def test_ls_fails_explicitly_when_local_index_is_unavailable(self):
        git('rm', '-q', '-r', '.brza', cwd=self.ws)
        git('commit', '-q', '-m', 'sem indice', cwd=self.ws)
        git('push', '-q', 'origin', 'HEAD:master', cwd=self.ws)
        code, out = self.run_note('ls', workstream=None)
        self.assertEqual(code, 1, out)
        self.assertIn('recusado', out)
        self.assertIn('indisponível', out)

    def test_unmaterialized_reservation_does_not_satisfy_refs_or_depends_on(self):
        self.run_note('review-open', 'fantasma', '--title', 'Fantasma', '--description', 'Sem arquivo.')
        self.run_note('migration-open', 'fantasma', '--title', 'Fantasma', '--description', 'Sem arquivo.')
        git('fetch', '-q', 'origin', cwd=self.ws)
        catalog = GLOBALS['build_target_catalog'](self.tree)
        self.assertNotIn('REV-001', catalog['valid_refs'])
        self.assertNotIn('MIG-001', catalog['valid_refs'])
        # Worklog que cita REV-001 ou tem refs: [REV-001] sem arquivo materializado é barrado no commit
        w_rel = 'work/worklogs/2026-10-31-fantasma.md'
        self.write(w_rel, '---\nscope:\n  - workspace\nrefs:\n  - REV-001\n---\n\n# W\n')
        code, out = self.commit_note(w_rel)
        self.assertEqual(code, 1, out)
        self.assertIn('ref-not-found', out)


