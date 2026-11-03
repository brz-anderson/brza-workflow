"""Testes dos tetos do orquestrador: workspace, conta (sessões e projeção de uso) e máquina.

    PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests/mcp -v
"""
import json
import sys
import unittest
from pathlib import Path

from mcp_support import paths  # noqa: F401

from brza_mcp import limits as lim  # noqa: E402
from brza_mcp import queue as q  # noqa: E402

FIXTURE = Path(__file__).resolve().parent / 'fixtures' / 'orca-account-list.json'
RESETS = 2_000_000_000_000


class Clock:
    def __init__(self, ms=1_000_000_000_000):
        self.ms = ms

    def __call__(self):
        return self.ms

    def advance(self, minutes):
        self.ms += int(minutes * 60_000)


def snap(used, resets=RESETS, name='session', account='a1', runtime='claude'):
    return {'account': account, 'runtime': runtime, 'windows': [{'name': name, 'usedPercent': used, 'resetsAt': resets}]}


class Usage:
    """Provedor injetável: devolve o instantâneo corrente, ou None (conta sem dado)."""

    def __init__(self, snapshot=None):
        self.snapshot = snapshot

    def __call__(self, account, runtime):
        return self.snapshot


def machine(free=16.0, swap=0.0, cpu=10.0):
    return lambda: {'free_mem_gb': free, 'swap_gb': swap, 'cpu_percent': cpu}


def setup(config, usage=None, mach=None, clock=None):
    queue = q.Queue(':memory:')
    limits = lim.Limits(config, usage=usage or Usage(snap(10)), machine=mach or machine(), clock=clock or Clock())
    return queue, limits


def add(queue, **kw):
    kw.setdefault('task', 't')
    kw.setdefault('role', 'consultant')
    kw.setdefault('account', 'a1')
    kw.setdefault('runtime', 'claude')
    return queue.enqueue(**kw)


class WorkspaceCeilingTest(unittest.TestCase):
    def test_max_workers_holds_and_pending_says_so(self):
        queue, limits = setup({'orchestrator.max_workers': 1})
        first, second = add(queue), add(queue)
        self.assertEqual(queue.next_admissible(limits)['id'], first)
        queue.mark_running(first)
        self.assertIsNone(queue.next_admissible(limits))
        pend = queue.pending()
        self.assertEqual([(p['id'], p['held_by']) for p in pend], [(second, 'workspace')])
        self.assertIn('max_workers', pend[0]['reason'])

    def test_mark_done_frees_the_ceiling(self):
        queue, limits = setup({'orchestrator': {'max_workers': 1}})
        first, second = add(queue), add(queue)
        queue.mark_running(queue.next_admissible(limits)['id'])
        self.assertIsNone(queue.next_admissible(limits))
        queue.mark_done(first)
        self.assertEqual(queue.next_admissible(limits)['id'], second)

    def test_the_tiebreaker_does_not_take_a_slot(self):
        queue, limits = setup({'orchestrator.max_workers': 1})
        tie = add(queue, role='tiebreaker')
        queue.mark_running(tie, counts_as_worker=False)
        add(queue)
        self.assertIsNotNone(queue.next_admissible(limits))

    def test_no_value_means_no_ceiling(self):
        queue, limits = setup({})
        for _ in range(5):
            queue.mark_running(add(queue))
        self.assertIsNotNone(queue.next_admissible(add(queue) and limits))


class AccountSessionsTest(unittest.TestCase):
    def test_sessions_per_account_and_runtime_hold(self):
        queue, limits = setup({'quota.max_sessions_per_account': 1})
        queue.mark_running(add(queue, account='a1'))
        waiting = add(queue, account='a1')
        self.assertIsNone(queue.next_admissible(limits))
        self.assertEqual(queue.pending()[0]['held_by'], 'account_sessions')
        other = add(queue, account='a2')
        self.assertEqual(queue.next_admissible(limits)['id'], other)
        self.assertEqual(waiting, queue.pending()[0]['id'])

    def test_the_smallest_ceiling_wins(self):
        queue, limits = setup({'orchestrator.max_workers': 5, 'quota.max_sessions_per_account': 1})
        queue.mark_running(add(queue))
        add(queue)
        self.assertIsNone(queue.next_admissible(limits))
        self.assertEqual(queue.pending()[0]['held_by'], 'account_sessions')
        queue2, limits2 = setup({'orchestrator.max_workers': 1, 'quota.max_sessions_per_account': 3})
        queue2.mark_running(add(queue2))
        add(queue2)
        queue2.next_admissible(limits2)
        self.assertEqual(queue2.pending()[0]['held_by'], 'workspace')


class QuotaTest(unittest.TestCase):
    def test_a_single_sample_holds_only_when_already_at_the_cap(self):
        queue, limits = setup({'quota.hard_cap_percent': 90}, usage=Usage(snap(89)))
        add(queue)
        self.assertIsNotNone(queue.next_admissible(limits))
        queue, limits = setup({'quota.hard_cap_percent': 90}, usage=Usage(snap(90)))
        add(queue)
        self.assertIsNone(queue.next_admissible(limits))
        self.assertEqual(queue.pending()[0]['held_by'], 'account_quota')

    def test_the_projection_holds_before_reaching_the_cap(self):
        clock = Clock(RESETS - 10 * 3_600_000)  # 10 h antes do reset
        usage = Usage(snap(10))
        queue, limits = setup({'quota.hard_cap_percent': 90}, usage=usage, clock=clock)
        add(queue)
        self.assertIsNotNone(queue.next_admissible(limits))  # primeira amostra: 10%
        clock.advance(60)
        usage.snapshot = snap(20)  # 10 pontos/h, faltam 9 h: projeta 110%
        decision = queue.next_admissible(limits)
        self.assertIsNone(decision)
        held = queue.pending()[0]
        self.assertEqual(held['held_by'], 'account_quota')
        self.assertIn('session', held['reason'])
        self.assertAlmostEqual(limits.last_measures['quota']['windows'][0]['projected'], 110.0, places=1)

    def test_a_slow_slope_does_not_hold(self):
        clock = Clock(RESETS - 10 * 3_600_000)
        usage = Usage(snap(10))
        queue, limits = setup({'quota.hard_cap_percent': 90}, usage=usage, clock=clock)
        add(queue)
        queue.next_admissible(limits)
        clock.advance(60)
        usage.snapshot = snap(11)  # 1 ponto/h: projeta 20%
        self.assertIsNotNone(queue.next_admissible(limits))

    def test_samples_of_another_reset_are_not_mixed(self):  # P2 F-A3 = O-A9
        clock = Clock(RESETS - 3_600_000)
        usage = Usage(snap(2, resets=RESETS - 1_800_000))  # a janela velha, em 2%
        queue, limits = setup({'quota.hard_cap_percent': 90}, usage=usage, clock=clock)
        add(queue)
        queue.next_admissible(limits)
        clock.advance(10)
        usage.snapshot = snap(80, resets=RESETS)  # a janela virou: misturadas as duas projetam acima de 90
        self.assertIsNotNone(queue.next_admissible(limits))

    def test_the_same_reset_within_a_second_is_the_same_group(self):  # P2 O-A4
        clock = Clock(RESETS - 10 * 3_600_000)
        usage = Usage(snap(10, resets=RESETS))
        queue, limits = setup({'quota.hard_cap_percent': 90}, usage=usage, clock=clock)
        add(queue)
        queue.next_admissible(limits)
        clock.advance(60)
        usage.snapshot = snap(20, resets=RESETS + 500)  # o cliente devolve o resetsAt com ms de diferença
        self.assertIsNone(queue.next_admissible(limits))  # 10 pontos/h: projeta 110%, mesma janela
        self.assertEqual(len(queue.samples('a1', 'claude', 'session', RESETS)), 2)

    def test_a_one_point_step_in_seconds_does_not_hold_the_account(self):  # P2 O-A3
        clock = Clock(RESETS - 10 * 3_600_000)
        usage = Usage({'account': 'a1', 'runtime': 'claude', 'windows': [
            {'name': 'session', 'usedPercent': 10, 'resetsAt': RESETS, 'windowMinutes': 300}]})
        queue, limits = setup({'quota.hard_cap_percent': 90}, usage=usage, clock=clock)
        add(queue)
        queue.next_admissible(limits)
        clock.advance(0.5)
        usage.snapshot['windows'][0]['usedPercent'] = 11  # o inteiro do `usedPercent` deu um degrau
        self.assertIsNotNone(queue.next_admissible(limits))
        self.assertEqual(limits.last_measures['quota']['windows'][0]['projected'], 11)

    def test_a_vanished_window_is_pruned_on_the_next_record(self):  # P2 O-A12
        queue = q.Queue(':memory:')
        queue.record_usage(snap(5, resets=1_000_000), 900_000)
        self.assertEqual(len(queue.samples('a1', 'claude', 'session', 1_000_000)), 1)
        queue.record_usage(snap(7, resets=RESETS), 5_000_000)  # o reset da primeira já passou
        self.assertEqual(queue.samples('a1', 'claude', 'session', 1_000_000), [])
        self.assertEqual(len(queue.samples('a1', 'claude', 'session', RESETS)), 1)

    def test_any_window_over_the_cap_holds(self):
        usage = Usage({'account': 'a1', 'runtime': 'claude', 'windows': [
            {'name': 'session', 'usedPercent': 5, 'resetsAt': RESETS},
            {'name': 'weekly', 'usedPercent': 95, 'resetsAt': RESETS}]})
        queue, limits = setup({}, usage=usage)  # o teto é 90 sem a chave
        add(queue)
        self.assertIsNone(queue.next_admissible(limits))
        self.assertIn('weekly', queue.pending()[0]['reason'])

    def test_the_account_without_usage_admits_inside_the_sessions_ceiling_with_quota_unknown(self):
        queue, limits = setup({'quota.max_sessions_per_account': 1}, usage=Usage(None))
        first = add(queue)
        add(queue)
        got = queue.next_admissible(limits)
        self.assertEqual(got['id'], first)
        decision = limits.decide(queue.get(first), queue)  # a última da passada é a do segundo, que a vaga do primeiro segura
        self.assertEqual(decision.reason, 'quota_unknown')
        self.assertTrue(decision.admit)
        queue.mark_running(first)
        self.assertIsNone(queue.next_admissible(limits))
        self.assertEqual(queue.pending()[0]['held_by'], 'account_sessions')

    def test_a_window_without_resets_at_is_judged_only_by_the_current_value(self):
        usage = Usage({'account': 'a1', 'runtime': 'claude', 'windows': [{'name': 'session', 'usedPercent': 50}]})
        queue, limits = setup({}, usage=usage)
        add(queue)
        self.assertIsNotNone(queue.next_admissible(limits))

    def test_the_orca_fixture_shapes_a_snapshot(self):
        data = json.loads(FIXTURE.read_text())
        self.assertEqual(set(data), {'id', 'ok', 'result', '_meta'})  # o envelope do `--json`
        self.assertTrue(all(e['email'].endswith('@example.com') for e in data['result']['accounts']['claude']))
        claude = data['result']['rateLimits']['claude']
        windows = [{'name': n, 'usedPercent': claude[n]['usedPercent'], 'resetsAt': claude[n]['resetsAt']}
                   for n in ('session', 'weekly', 'fableWeekly') if claude.get(n)]
        usage = Usage({'account': 'a1', 'runtime': 'claude', 'windows': windows})
        queue, limits = setup({}, usage=usage)
        add(queue)
        self.assertIsNotNone(queue.next_admissible(limits))
        self.assertEqual(len(limits.last_measures['quota']['windows']), 3)


class MachineTest(unittest.TestCase):
    def test_without_values_it_only_records(self):
        queue, limits = setup({}, mach=machine(free=0.1, swap=50, cpu=100))
        add(queue)
        self.assertIsNotNone(queue.next_admissible(limits))
        m = limits.last_measures['machine']
        self.assertEqual((m['free_mem_gb'], m['swap_gb'], m['cpu_percent']), (0.1, 50, 100))
        self.assertFalse(m['enforced'])

    def test_low_free_memory_holds(self):
        queue, limits = setup({'machine.min_free_mem_gb': 4}, mach=machine(free=2))
        add(queue)
        self.assertIsNone(queue.next_admissible(limits))
        held = queue.pending()[0]
        self.assertEqual(held['held_by'], 'machine')
        self.assertIn('min_free_mem_gb', held['reason'])

    def test_swap_holds(self):
        queue, limits = setup({'machine.max_swap_gb': 2}, mach=machine(swap=3))
        add(queue)
        self.assertIsNone(queue.next_admissible(limits))
        self.assertIn('max_swap_gb', queue.pending()[0]['reason'])

    def test_cpu_holds(self):
        queue, limits = setup({'machine.max_cpu_percent': 80}, mach=machine(cpu=95))
        add(queue)
        self.assertIsNone(queue.next_admissible(limits))
        self.assertIn('max_cpu_percent', queue.pending()[0]['reason'])

    def test_values_inside_the_limits_admit_and_are_enforced(self):
        queue, limits = setup({'machine.min_free_mem_gb': 4, 'machine.max_swap_gb': 2, 'machine.max_cpu_percent': 80})
        add(queue)
        self.assertIsNotNone(queue.next_admissible(limits))
        self.assertTrue(limits.last_measures['machine']['enforced'])

    def test_an_unreadable_measure_does_not_hold(self):
        queue, limits = setup({'machine.min_free_mem_gb': 4},
                              mach=lambda: {'free_mem_gb': None, 'swap_gb': None, 'cpu_percent': None})
        add(queue)
        self.assertIsNotNone(queue.next_admissible(limits))


class MachineReadersTest(unittest.TestCase):
    VM_STAT = ('Mach Virtual Memory Statistics: (page size of 16384 bytes)\n'
               'Pages free:                                   1000.\n'
               'Pages active:                                 400947.\n'
               'Pages inactive:                               500.\n'
               'Pages speculative:                            500.\n')

    def test_free_memory_from_vm_stat(self):
        self.assertAlmostEqual(lim.parse_vm_stat(self.VM_STAT), 2000 * 16384 / 1024 ** 3)
        self.assertIsNone(lim.parse_vm_stat('lixo'))

    def test_swap_from_sysctl(self):
        text = 'vm.swapusage: total = 2048.00M  used = 1207.75M  free = 840.25M  (encrypted)'
        self.assertAlmostEqual(lim.parse_swapusage(text), 1207.75 / 1024)
        self.assertAlmostEqual(lim.parse_swapusage('vm.swapusage: total = 2.00G  used = 1.50G  free = 0.50G'), 1.5)
        self.assertIsNone(lim.parse_swapusage('lixo'))

    def test_cpu_is_the_load_over_the_cpu_count(self):
        self.assertAlmostEqual(lim.cpu_percent(loadavg=lambda: (4.0, 0, 0), cpus=lambda: 8), 50.0)

    def test_the_real_reader_returns_the_three_keys(self):
        m = lim.read_machine()
        self.assertEqual(set(m), {'free_mem_gb', 'swap_gb', 'cpu_percent'})

    @unittest.skipUnless(sys.platform == 'darwin', 'vm_stat e sysctl são do macOS')  # P2 O-A14
    def test_the_real_reader_reads_the_three_values_on_macos(self):
        m = lim.read_machine()
        self.assertTrue(all(v is not None for v in m.values()), m)


class PassTest(unittest.TestCase):
    """Uma passada de `next_admissible` lê o uso de cada (conta, runtime) e a máquina uma vez só (P2 O-A12, P3 O-A9)."""

    def test_the_usage_and_the_machine_are_read_once_per_pass(self):
        calls = {'usage': [], 'machine': 0}

        def usage(account, runtime):
            calls['usage'].append((account, runtime))
            return snap(10, account=account)

        def mach():
            calls['machine'] += 1
            return {'free_mem_gb': 16.0, 'swap_gb': 0.0, 'cpu_percent': 10.0}
        queue = q.Queue(':memory:')
        limits = lim.Limits({'quota.max_sessions_per_account': 9}, usage=usage, machine=mach, clock=Clock())
        for account in ('a1', 'a1', 'a1', 'a2'):
            add(queue, account=account)
        queue.next_admissible(limits)
        self.assertEqual(sorted(calls['usage']), [('a1', 'claude'), ('a2', 'claude')])
        self.assertEqual(calls['machine'], 1)
        self.assertEqual(len(queue.samples('a1', 'claude', 'session', RESETS)), 1)

    def test_the_held_by_is_written_only_when_it_changes(self):  # P3 O-A9
        queue, limits = setup({'orchestrator.max_workers': 1}, usage=Usage(None))
        queue.mark_running(add(queue))
        waiting = add(queue)
        queue.next_admissible(limits)
        updated = queue.get(waiting)['updated_at']
        before = queue._all('SELECT total_changes()')[0][0]
        queue.next_admissible(limits)
        self.assertEqual(queue._all('SELECT total_changes()')[0][0], before)
        self.assertEqual(queue.get(waiting)['updated_at'], updated)


class MeasuresTest(unittest.TestCase):
    def test_every_decision_returns_the_measures(self):
        queue, limits = setup({'orchestrator.max_workers': 3, 'quota.max_sessions_per_account': 2})
        request = queue.get(add(queue))
        decision = limits.decide(request, queue)
        self.assertTrue(decision.admit)
        self.assertEqual(decision.measures['workspace'], {'running': 0, 'max_workers': 3})
        self.assertEqual(decision.measures['account'], {'account': 'a1', 'runtime': 'claude', 'sessions': 0,
                                                         'max_sessions': 2})
        self.assertIn('quota', decision.measures)
        self.assertIn('machine', decision.measures)

    def test_the_held_decision_also_returns_the_measures(self):
        queue, limits = setup({'orchestrator.max_workers': 1})
        queue.mark_running(add(queue))
        decision = limits.decide(queue.get(add(queue)), queue)
        self.assertFalse(decision.admit)
        self.assertEqual(decision.ceiling, 'workspace')
        self.assertEqual(decision.measures['workspace']['running'], 1)


if __name__ == '__main__':
    unittest.main()
