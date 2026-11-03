"""Testes da fila do orquestrador: persistência, a ordem da R9.2 e o índice da `master`.

    PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests/mcp -v
"""
import json
import tempfile
import threading
import unittest
from pathlib import Path

from mcp_support import paths  # noqa: F401  (põe o pacote no sys.path)

from brza_mcp import queue as q  # noqa: E402


class AllowAll:
    def decide(self, request, queue):
        return q_decision_admit()


def q_decision_admit():
    from brza_mcp.limits import Decision
    return Decision(True, None, 'ok', {})


def make(path=':memory:'):
    return q.Queue(path)


class ReservationTest(unittest.TestCase):
    """`admit_next` escolhe e reserva no mesmo lock (P2 O-A1, F-A1)."""

    def limits(self, **config):
        from brza_mcp import limits as lim
        return lim.Limits(config, machine=lambda: {})

    def test_the_chosen_request_takes_the_slot_before_the_rest_is_judged(self):
        queue, limits = make(), self.limits(**{'orchestrator.max_workers': 1})
        first = queue.enqueue(task='a', role='consultant')
        second = queue.enqueue(task='b', role='consultant')
        chosen = queue.admit_next(limits)
        self.assertEqual((chosen['id'], chosen['state']), (first, 'running'))
        self.assertEqual(queue.running_count(), 1)
        pend = queue.pending()
        self.assertEqual([(p['id'], p['held_by']) for p in pend], [(second, 'workspace')])
        self.assertIsNone(queue.admit_next(limits))
        self.assertEqual(queue.running_count(), 1)

    def test_next_admissible_also_judges_the_rest_with_the_chosen_in_a_slot_and_leaves_no_trace(self):
        queue, limits = make(), self.limits(**{'orchestrator.max_workers': 1})
        first = queue.enqueue(task='a', role='consultant')
        second = queue.enqueue(task='b', role='consultant')
        self.assertEqual(queue.next_admissible(limits)['id'], first)
        self.assertEqual(queue.get(first)['state'], 'queued')
        self.assertEqual(queue.running_count(), 0)
        self.assertEqual(queue.get(second)['held_by'], 'workspace')

    def test_every_request_over_the_ceiling_is_held_not_only_the_first_after_the_chosen(self):  # P2 r2 A1
        queue, limits = make(), self.limits(**{'orchestrator.max_workers': 2})
        ids = [queue.enqueue(task=f't{i}', role='consultant') for i in range(4)]
        self.assertEqual(queue.next_admissible(limits)['id'], ids[0])
        self.assertEqual([(p['id'], p['held_by']) for p in queue.pending()],
                         [(ids[0], None), (ids[1], None), (ids[2], 'workspace'), (ids[3], 'workspace')])
        self.assertEqual(queue.running_count(), 0)  # a passada não deixa rastro
        self.assertEqual([queue.get(i)['state'] for i in ids], ['queued'] * 4)

    def test_the_same_with_the_sessions_ceiling_of_an_account(self):  # P2 r2 A1
        queue, limits = make(), self.limits(**{'quota.max_sessions_per_account': 2})
        ids = [queue.enqueue(task=f't{i}', role='consultant', account='a1', runtime='claude') for i in range(4)]
        other = queue.enqueue(task='o', role='consultant', account='a2', runtime='claude')
        queue.next_admissible(limits)
        held = {p['id']: p['held_by'] for p in queue.pending()}
        self.assertEqual(held, {ids[0]: None, ids[1]: None, ids[2]: 'account_sessions', ids[3]: 'account_sessions',
                                other: None})

    def test_admit_next_keeps_only_the_first_one_reserved(self):  # P2 r2 A1
        queue, limits = make(), self.limits(**{'orchestrator.max_workers': 2})
        ids = [queue.enqueue(task=f't{i}', role='consultant') for i in range(3)]
        self.assertEqual(queue.admit_next(limits)['id'], ids[0])
        self.assertEqual((queue.running_count(), queue.get(ids[1])['state']), (1, 'queued'))
        self.assertEqual(queue.admit_next(limits)['id'], ids[1])
        self.assertIsNone(queue.admit_next(limits))
        self.assertEqual(queue.get(ids[2])['held_by'], 'workspace')

    def test_mark_running_refuses_a_request_that_is_not_queued(self):
        queue = make()
        rid = queue.enqueue(task='a', role='consultant')
        queue.mark_running(rid)
        with self.assertRaises(ValueError):
            queue.mark_running(rid)
        self.assertEqual(queue.running_count(), 1)
        with self.assertRaises(ValueError):
            queue.mark_running(999)

    def test_release_gives_the_slot_back_and_the_request_goes_to_the_queue(self):
        queue, limits = make(), self.limits()
        rid = queue.enqueue(task='a', role='consultant')
        queue.admit_next(limits)
        queue.release(rid)
        self.assertEqual((queue.get(rid)['state'], queue.running_count()), ('queued', 0))

    def test_an_explicit_empty_blocks_is_not_replaced_by_the_index(self):  # P2 F-A2
        queue = make()
        rid = queue.enqueue(task='t', role='consultant', ref='R', blocks=[], index=lambda r: {'blocks': ['P']})
        self.assertEqual(queue.get(rid)['blocks'], [])
        other = queue.enqueue(task='t', role='consultant', ref='R', index=lambda r: {'blocks': ['P']})
        self.assertEqual(queue.get(other)['blocks'], ['P'])

    def test_running_count_filters_by_account_alone_or_runtime_alone(self):  # P2 F-A4
        queue = make()
        queue.mark_running(queue.enqueue(task='t', role='r', account='a1', runtime='claude'))
        queue.mark_running(queue.enqueue(task='t', role='r', account='a2', runtime='claude'))
        queue.mark_running(queue.enqueue(task='t', role='r', account='a1', runtime='codex'))
        self.assertEqual(queue.running_count(), 3)
        self.assertEqual(queue.running_count(account='a1'), 2)
        self.assertEqual(queue.running_count(runtime='claude'), 2)
        self.assertEqual(queue.running_count('a1', 'claude'), 1)

    def test_the_deadline_is_one_iso_date_only(self):  # P2 O-A8
        queue = make()
        for bad in ('20261201', '2026-W49-2', '2026-12-1'):
            with self.assertRaises(ValueError, msg=bad):
                queue.enqueue(task='t', role='r', deadline=bad)
        rid = queue.enqueue(task='t', role='r', deadline='2026-12-01')
        self.assertEqual(queue.get(rid)['deadline'], '2026-12-01')


class PersistenceTest(unittest.TestCase):
    def test_default_path_is_orchestrator_db_in_the_brza_home(self):
        import os
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {'BRZA_HOME': tmp}):
            self.assertEqual(q.default_db_path(), Path(tmp) / 'orchestrator.db')
            queue = q.Queue()
            queue.enqueue(task='t', role='consultant')
            queue.close()
            self.assertTrue((Path(tmp) / 'orchestrator.db').exists())

    def test_a_request_survives_a_new_connection(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / 'o.db'
            a = make(db)
            rid = a.enqueue(task='ler', role='consultant', blocks=['PLN-1'], priority='high', deadline='2026-12-01')
            a.close()
            b = make(db)
            row = b.get(rid)
            self.assertEqual(row['task'], 'ler')
            self.assertEqual(row['state'], 'queued')
            self.assertEqual(row['blocks'], ['PLN-1'])
            self.assertEqual(row['priority'], 'high')
            self.assertEqual(row['deadline'], '2026-12-01')
            b.close()

    def test_the_invalid_values_are_refused(self):
        queue = make()
        with self.assertRaises(ValueError):
            queue.enqueue(task='t', role='r', priority='urgent')
        with self.assertRaises(ValueError):
            queue.enqueue(task='t', role='r', deadline='amanhã')

    def test_threads_can_share_the_queue(self):
        queue = make()
        ids = []

        def work():
            for _ in range(10):
                ids.append(queue.enqueue(task='t', role='consultant'))

        threads = [threading.Thread(target=work) for _ in range(4)]
        [t.start() for t in threads]
        [t.join() for t in threads]
        self.assertEqual(len(set(ids)), 40)
        self.assertEqual(len(queue.pending()), 40)

    def test_the_gated_request_is_not_admissible_until_it_is_queued(self):
        queue = make()
        rid = queue.enqueue(task='t', role='executor', state='gated')
        self.assertIsNone(queue.next_admissible(AllowAll()))
        queue.set_state(rid, 'queued')
        self.assertEqual(queue.next_admissible(AllowAll())['id'], rid)


class OrderTest(unittest.TestCase):
    def order(self, *requests):
        queue = make()
        ids = [queue.enqueue(task=str(i), role='consultant', **r) for i, r in enumerate(requests)]
        out = []
        while True:
            nxt = queue.next_admissible(AllowAll())
            if not nxt:
                break
            out.append(ids.index(nxt['id']))
            queue.mark_running(nxt['id'])
        return out

    def test_the_closest_deadline_goes_first(self):
        self.assertEqual(self.order({'deadline': '2026-12-10'}, {'deadline': '2026-12-01'}, {}), [1, 0, 2])

    def test_blocks_beats_priority_high(self):
        self.assertEqual(self.order({'priority': 'high'}, {'blocks': ['PLN-3']}), [1, 0])

    def test_deadline_beats_blocks(self):
        self.assertEqual(self.order({'blocks': ['PLN-3']}, {'deadline': '2026-12-01'}), [1, 0])

    def test_priority_order_with_absent_last_then_arrival(self):
        self.assertEqual(self.order({}, {'priority': 'low'}, {'priority': 'medium'}, {'priority': 'high'}, {}),
                         [3, 2, 1, 0, 4])

    def test_arrival_breaks_ties(self):
        self.assertEqual(self.order({'priority': 'high'}, {'priority': 'high'}), [0, 1])


class HoldTest(unittest.TestCase):
    def test_next_admissible_skips_the_held_and_records_who_holds(self):
        from brza_mcp.limits import Decision

        class HoldFirst:
            def decide(self, request, queue):
                if request['task'] == 'a':
                    return Decision(False, 'account_sessions', 'cheio', {'x': 1})
                return Decision(True, None, 'ok', {})

        queue = make()
        a = queue.enqueue(task='a', role='consultant', priority='high')
        b = queue.enqueue(task='b', role='consultant')
        self.assertEqual(queue.next_admissible(HoldFirst())['id'], b)
        pend = {p['id']: p for p in queue.pending()}
        self.assertEqual(pend[a]['held_by'], 'account_sessions')
        self.assertEqual(pend[a]['reason'], 'cheio')
        self.assertIsNone(pend[b]['held_by'])

    def test_mark_running_done_and_failed_move_the_state(self):
        queue = make()
        rid = queue.enqueue(task='t', role='consultant', account='a1', runtime='claude')
        queue.mark_running(rid, session='s1')
        self.assertEqual(queue.get(rid)['state'], 'running')
        self.assertEqual(queue.running_count(), 1)
        queue.mark_done(rid)
        self.assertEqual(queue.get(rid)['state'], 'done')
        self.assertEqual(queue.running_count(), 0)
        rid2 = queue.enqueue(task='t', role='consultant')
        queue.mark_running(rid2)
        queue.mark_done(rid2, failed=True)
        self.assertEqual(queue.get(rid2)['state'], 'failed')

    def test_the_tiebreaker_does_not_count_as_worker(self):
        queue = make()
        rid = queue.enqueue(task='t', role='tiebreaker')
        queue.mark_running(rid, counts_as_worker=False)
        self.assertEqual(queue.running_count(), 0)


INDEX = '\n'.join(json.dumps(x) for x in [
    {'ts': '2026-11-01T10:00:00-03:00', 'event': 'created', 'kind': 'plan', 'id': 'PLN-003',
     'path': 'plans/PLN-003/PLN-003-plan.md', 'status': 'open', 'priority': 'low'},
    {'ts': '2026-11-02T10:00:00-03:00', 'event': 'updated', 'kind': 'plan', 'id': 'PLN-003',
     'path': 'plans/PLN-003/PLN-003-plan.md', 'status': 'open', 'priority': 'high', 'blocks': ['PLN-009'],
     'deadline': '2026-12-15'},
    {'ts': '2026-11-03T01:41:08-03:00', 'event': 'created', 'kind': 'issue', 'id': 'issue:o-design',
     'path': 'work/issues/o-design.md', 'status': 'open', 'workstream': 'brand'},
    {'ts': '2026-11-03T02:00:00-03:00', 'event': 'updated', 'kind': 'plan', 'id': 'PLN-003',
     'path': 'plans/PLN-003/PLN-003-plan.md', 'status': 'open', 'priority': 'medium'},
    'lixo',
]) + '\nnão é json\n'


class IndexTest(unittest.TestCase):
    def reader(self, workspace):
        self.seen = workspace
        return INDEX

    def test_the_log_is_reduced_to_the_last_line_per_id(self):
        entries = q.reduce_index(INDEX)
        self.assertEqual(entries['PLN-003']['priority'], 'medium')
        self.assertNotIn('deadline', entries['PLN-003'])  # a última linha manda; não mescla com as anteriores

    def test_lookup_by_id_and_by_path(self):
        by_id = q.index_fields('PLN-003', '/ws', reader=self.reader)
        self.assertEqual(by_id, {'priority': 'medium'})
        self.assertEqual(self.seen, '/ws')
        by_path = q.index_fields('work/issues/o-design.md', '/ws', reader=self.reader)
        self.assertEqual(by_path, {})
        self.assertEqual(q.index_fields('PLN-404', '/ws', reader=self.reader), {})
        self.assertEqual(q.index_fields(None, '/ws', reader=self.reader), {})

    def test_lookup_by_path_returns_the_fields_of_that_line(self):  # P2 O-A10
        self.assertEqual(q.index_fields('plans/PLN-003/PLN-003-plan.md', '/ws', reader=self.reader),
                         {'priority': 'medium'})

    def test_an_alternative_iso_deadline_in_the_index_is_dropped(self):  # P2 O-A8
        text = json.dumps({'id': 'PLN-9', 'deadline': '20261201'})
        self.assertEqual(q.index_fields('PLN-9', '/ws', reader=lambda w: text), {})

    def test_the_fields_are_accepted_when_the_index_brings_them(self):
        text = json.dumps({'id': 'PLN-9', 'priority': 'high', 'blocks': ['a', 'b'], 'deadline': '2026-12-01'})
        self.assertEqual(q.index_fields('PLN-9', '/ws', reader=lambda w: text),
                         {'priority': 'high', 'blocks': ['a', 'b'], 'deadline': '2026-12-01'})

    def test_an_invalid_value_in_the_index_is_dropped(self):
        text = json.dumps({'id': 'PLN-9', 'priority': 'urgent', 'deadline': 'logo'})
        self.assertEqual(q.index_fields('PLN-9', '/ws', reader=lambda w: text), {})

    def test_a_failing_reader_gives_no_fields(self):
        def boom(w):
            raise OSError('sem git')
        self.assertEqual(q.index_fields('PLN-3', '/ws', reader=boom), {})

    def test_git_reader_runs_git_show_master(self):
        from unittest.mock import patch
        from subprocess import CompletedProcess
        with patch('brza_mcp.queue.subprocess.run',
                   return_value=CompletedProcess([], 0, stdout='{"id":"X"}\n', stderr='')) as run:
            self.assertEqual(q.git_index_reader('/ws'), '{"id":"X"}\n')
        self.assertEqual(run.call_args.args[0], ['git', '-C', '/ws', 'show', 'master:.brza/index.jsonl'])

    def test_enqueue_fills_from_the_index_and_dispatch_wins(self):
        queue = make()
        index = lambda ref: q.index_fields(ref, '/ws', reader=lambda w: INDEX)  # noqa: E731
        a = queue.enqueue(task='t', role='consultant', ref='PLN-003', index=index)
        self.assertEqual(queue.get(a)['priority'], 'medium')
        b = queue.enqueue(task='t', role='consultant', ref='PLN-003', priority='low', index=index)
        self.assertEqual(queue.get(b)['priority'], 'low')
        text = json.dumps({'id': 'PLN-9', 'priority': 'high', 'blocks': ['x'], 'deadline': '2026-12-01'})
        idx2 = lambda ref: q.index_fields(ref, '/ws', reader=lambda w: text)  # noqa: E731
        c = queue.enqueue(task='t', role='consultant', ref='PLN-9', deadline='2026-11-20', index=idx2)
        row = queue.get(c)
        self.assertEqual((row['deadline'], row['blocks'], row['priority']), ('2026-11-20', ['x'], 'high'))


if __name__ == '__main__':
    unittest.main()
