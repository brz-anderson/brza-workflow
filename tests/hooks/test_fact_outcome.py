"""Testes do `scripts/fact_outcome.py`: o desfecho que a sessão registra para cada fato do desenho (`fact_outcome`), a
lista dos fatos a conferir (`--pending`) e a vista de uma devolução (`--list`). O log é montado pelo teste, num
`MEASURE_DIR` temporário.

    python3 -m unittest discover -s .claude/hooks/tests
"""

import contextlib
import importlib.util
import io
import json
import os
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPT = os.path.join(HERE, "..", "..", "core", "scripts", "fact_outcome.py")

AGENT = "a4e396c649819d3fd"  # designer, workspace
OTHER = "a4e3ffff00000000"  # collector, workspace: divide o prefixo `a4e3` com o primeiro
STUDIO = "b77100aa22bb33cc"  # designer, studio


def load(directory):
    os.environ["MEASURE_DIR"] = directory
    spec = importlib.util.spec_from_file_location("fact_outcome", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.measure.workstream_of = lambda cwd: "workspace"
    return module


def fact(agent_id, fact_id, kind, confidence, day="2026-10-30", agent_type="designer", workstream="workspace",
         text=None):
    return {"event": "fact", "timestamp": f"{day}T10:00:00-03:00", "session_id": "s", "agent_id": agent_id,
            "agent_type": agent_type, "workstream": workstream, "fact_id": fact_id, "kind": kind,
            "confidence": confidence, "text": text or f"[{fact_id} · {kind} · {confidence}] o texto de {fact_id}",
            "source": "reply"}


def check(agent_id, fact_id, verdict, cite="ok"):
    return {"event": "fact_check", "timestamp": "2026-10-30T10:05:00-03:00", "agent_id": agent_id,
            "fact_id": fact_id, "verdict": verdict, "cite": cite, "why": "x"}


def outcome(agent_id, fact_id, verdict, ref=None):
    return {"event": "fact_outcome", "timestamp": "2026-10-30T11:00:00-03:00", "agent_id": agent_id,
            "fact_id": fact_id, "verdict": verdict, "why": "x", "ref": ref, "workstream": "workspace"}


FIXTURE = [
    fact(AGENT, "F1", "lido", 90, day="2026-10-28"),
    fact(AGENT, "F2", "inferido", 95, day="2026-10-28"),
    fact(AGENT, "F3", "rodado", 70, day="2026-10-28"),
    fact(AGENT, "C1", "lido", 90, day="2026-10-28"),
    fact(AGENT, "F4", "doc", 85, day="2026-10-28"),
    fact(AGENT, "F5", "inferido", 60, day="2026-10-28"),
    outcome(AGENT, "F5", "holds"),
    fact(OTHER, "F1", "lido", 90, agent_type="collector", day="2026-10-30"),
    fact(STUDIO, "F1", "lido", 50, workstream="studio", day="2026-10-31"),
]


class FactOutcomeTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.addCleanup(os.environ.pop, "MEASURE_DIR", None)
        self.fo = load(self.tmp.name)

    def log(self, rows):
        os.makedirs(self.fo.measure.DIR, exist_ok=True)
        with open(self.fo.measure.LOG, "w", encoding="utf-8") as f:
            for row in rows:
                f.write((row if isinstance(row, str) else json.dumps(row, ensure_ascii=False)) + "\n")

    def events(self, name=None):
        with open(self.fo.measure.LOG, encoding="utf-8") as f:
            rows = [json.loads(line) for line in f if line.strip()]
        return [r for r in rows if name is None or r["event"] == name]

    def run_cli(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            try:
                code = self.fo.main(list(argv))
            except SystemExit as e:  # o parser.error sai com 2
                code = e.code
        return code, out.getvalue(), err.getvalue()


class RegisterTest(FactOutcomeTest):
    def setUp(self):
        super().setUp()
        self.log(FIXTURE)

    def test_registers_by_unique_prefix_with_the_facts_workstream(self):
        self.fo.measure.workstream_of = lambda cwd: "tools-kit"  # o cwd não manda: vale o do fato
        code, out, err = self.run_cli(f"{STUDIO[:3]}:F1", "fails", "a sonda não rodou o hook", "--ref", "PLN-004/R8.1")
        self.assertEqual((code, err), (0, ""))
        self.assertIn("b77100aa22bb33cc:F1", out)
        event = self.events("fact_outcome")[-1]
        self.assertEqual({k: v for k, v in event.items() if k != "timestamp"},
                         {"event": "fact_outcome", "agent_id": STUDIO, "agent_type": "designer", "fact_id": "F1",
                          "verdict": "fails", "why": "a sonda não rodou o hook", "ref": "PLN-004/R8.1",
                          "workstream": "studio"})
        self.assertRegex(event["timestamp"], r"^\d{4}-\d\d-\d\dT")

    def test_ref_defaults_to_null_and_why_is_cut_at_300(self):
        code, _, _ = self.run_cli(f"{AGENT}:F1", "holds", "x" * 400)
        self.assertEqual(code, 0)
        event = self.events("fact_outcome")[-1]
        self.assertIsNone(event["ref"])
        self.assertEqual(len(event["why"]), 300)

    def test_agent_type_comes_from_the_fact(self):
        self.run_cli(f"{OTHER}:F1", "holds", "ok")
        self.assertEqual(self.events("fact_outcome")[-1]["agent_type"], "collector")

    def test_ambiguous_prefix_exits_2_and_lists_candidates(self):
        before = len(self.events())
        code, out, err = self.run_cli("a4e3:F1", "holds", "ok")
        self.assertEqual(code, 2)
        self.assertIn(AGENT, err)
        self.assertIn(OTHER, err)
        self.assertEqual(len(self.events()), before)

    def test_unknown_fact_exits_2(self):
        before = len(self.events())
        for target in (f"{AGENT}:F9", "zzzz:F1"):
            code, out, err = self.run_cli(target, "holds", "ok")
            self.assertEqual(code, 2)
            self.assertTrue(err.strip())
        self.assertEqual(len(self.events()), before)

    def test_invalid_verdict_exits_2(self):
        code, _, err = self.run_cli(f"{AGENT}:F1", "maybe", "ok")
        self.assertEqual(code, 2)
        self.assertIn("holds", err)
        self.assertEqual(self.events("fact_outcome").__len__(), 1)  # só o da fixture

    def test_usage_errors_exit_2(self):
        for argv in ([], [f"{AGENT}:F1"], [f"{AGENT}:F1", "holds"], [AGENT, "holds", "ok"],
                     [f"{AGENT}:X1", "holds", "ok"], [":F1", "holds", "ok"], [f"{AGENT}:F1", "holds", "  "]):
            code, _, err = self.run_cli(*argv)
            self.assertEqual(code, 2, argv)
            self.assertTrue(err.strip(), argv)

    def test_registering_twice_writes_two_events(self):
        self.run_cli(f"{AGENT}:F1", "holds", "primeiro")
        self.run_cli(f"{AGENT}:F1", "fails", "segundo")
        events = [e for e in self.events("fact_outcome") if e["fact_id"] == "F1"]
        self.assertEqual([e["verdict"] for e in events], ["holds", "fails"])

    def test_unreadable_log_exits_2_without_traceback(self):
        os.remove(self.fo.measure.LOG)
        os.mkdir(self.fo.measure.LOG)  # um diretório no lugar do log: a leitura falha
        code, _, err = self.run_cli(f"{AGENT}:F1", "holds", "ok")
        self.assertEqual(code, 2)
        self.assertNotIn("Traceback", err)
        self.assertTrue(err.strip())

    def test_missing_log_is_unknown_fact(self):
        os.remove(self.fo.measure.LOG)
        code, _, err = self.run_cli(f"{AGENT}:F1", "holds", "ok")
        self.assertEqual(code, 2)
        self.assertTrue(err.strip())

    def test_non_json_lines_are_skipped(self):
        self.log(["isto não é json", *FIXTURE])
        self.assertEqual(self.run_cli(f"{AGENT}:F1", "holds", "ok")[0], 0)


class PendingTest(FactOutcomeTest):
    def setUp(self):
        super().setUp()
        self.log(FIXTURE)

    def ids(self, out):
        return [line.split()[0] for line in out.splitlines() if ":" in line.split()[0]]

    def test_filters_inferred_low_confidence_and_cost_without_outcome(self):
        code, out, _ = self.run_cli("--pending")
        self.assertEqual(code, 0)
        # F1 (lido 90) e F4 (doc 85) ficam fora; F5 tem desfecho; o do `studio` é de outro workstream
        self.assertEqual(self.ids(out), ["a4e396c6:F2", "a4e396c6:F3", "a4e396c6:C1"])
        self.assertTrue(out.rstrip().endswith("3 fatos a conferir"))

    def test_line_format(self):
        _, out, _ = self.run_cli("--pending")
        first = out.splitlines()[0]
        self.assertEqual(first, "a4e396c6:F2  2026-10-28  designer  inferido 95  [F2 · inferido · 95] o texto de F2")

    def test_orders_by_timestamp_and_cuts_text_at_160_columns(self):
        long_text = "[F7 · inferido · 40] " + "palavra " * 60
        self.log([fact(STUDIO, "F7", "inferido", 40, day="2026-10-30", workstream="workspace", text=long_text),
                  fact(AGENT, "F2", "inferido", 95, day="2026-10-28")])
        _, out, _ = self.run_cli("--pending")
        lines = out.splitlines()
        self.assertEqual(self.ids(out), ["a4e396c6:F2", "b77100aa:F7"])
        self.assertEqual(len(lines[1]), 160)
        self.assertTrue(lines[1].endswith("…"))

    def test_outcome_removes_whatever_the_verdict(self):
        self.log([*FIXTURE, outcome(AGENT, "F2", "fails"), outcome(AGENT, "C1", "holds")])
        _, out, _ = self.run_cli("--pending")
        self.assertEqual(self.ids(out), ["a4e396c6:F3"])
        self.assertTrue(out.rstrip().endswith("1 fato a conferir"))

    def test_outcome_of_another_agent_with_same_fact_id_does_not_count(self):
        self.log([*FIXTURE, outcome(OTHER, "F2", "holds")])
        _, out, _ = self.run_cli("--pending")
        self.assertIn("a4e396c6:F2", self.ids(out))

    def test_workstream_filter(self):
        _, out, _ = self.run_cli("--pending", "--workstream", "studio")
        self.assertEqual(self.ids(out), ["b77100aa:F1"])

    def test_default_workstream_is_the_cwds(self):
        self.fo.measure.workstream_of = lambda cwd: "studio"
        _, out, _ = self.run_cli("--pending")
        self.assertEqual(self.ids(out), ["b77100aa:F1"])

    def test_all_workstreams(self):
        _, out, _ = self.run_cli("--pending", "--workstream", "all")
        self.assertEqual(self.ids(out), ["a4e396c6:F2", "a4e396c6:F3", "a4e396c6:C1", "b77100aa:F1"])

    def test_since(self):
        _, out, _ = self.run_cli("--pending", "--workstream", "all", "--since", "2026-10-31")
        self.assertEqual(self.ids(out), ["b77100aa:F1"])

    def test_since_must_be_a_date(self):
        code, _, err = self.run_cli("--pending", "--since", "ontem")
        self.assertEqual(code, 2)
        self.assertTrue(err.strip())

    def test_nothing_pending(self):
        _, out, _ = self.run_cli("--pending", "--workstream", "tools-kit")
        self.assertEqual(out.strip(), "nenhum fato a conferir")

    def test_missing_log_is_nothing_pending_with_exit_0(self):
        os.remove(self.fo.measure.LOG)
        code, out, _ = self.run_cli("--pending", "--workstream", "all")
        self.assertEqual((code, out.strip()), (0, "nenhum fato a conferir"))

    def test_unreadable_log_exits_2(self):
        os.remove(self.fo.measure.LOG)
        os.mkdir(self.fo.measure.LOG)
        code, _, err = self.run_cli("--pending")
        self.assertEqual(code, 2)
        self.assertNotIn("Traceback", err)

    def test_workstream_and_since_need_pending(self):
        self.assertEqual(self.run_cli("--list", AGENT, "--workstream", "all")[0], 2)
        self.assertEqual(self.run_cli("--pending", "--list", AGENT)[0], 2)


class ListTest(FactOutcomeTest):
    def setUp(self):
        super().setUp()
        self.log([*FIXTURE,
                  check(AGENT, "F1", "holds", "ok"), check(AGENT, "F1", "fails", "off"),  # vale o último
                  check(AGENT, "F3", "unknown", "ok"),
                  outcome(AGENT, "F3", "fails", "PLN-004/R8.1"), outcome(AGENT, "F3", "holds", "PLN-004/R9"),
                  outcome(AGENT, "F4", "fails"),
                  check(OTHER, "F1", "holds")])

    def test_shows_both_verdicts_and_dashes(self):
        code, out, _ = self.run_cli("--list", AGENT[:6])
        self.assertEqual(code, 0)
        lines = out.splitlines()
        self.assertIn(AGENT, lines[0])
        self.assertIn("designer", lines[0])
        rows = {line.split()[0]: line for line in lines[1:] if line.strip()}
        self.assertEqual(list(rows), ["F1", "F2", "F3", "C1", "F4", "F5"])
        self.assertIn("lido 90", rows["F1"])
        self.assertIn("Haiku fails (cite off)", rows["F1"])  # o último fact_check
        self.assertIn("sessão —", rows["F1"])
        self.assertIn("Haiku —", rows["F2"])
        self.assertIn("Haiku unknown (cite ok)", rows["F3"])
        self.assertIn("sessão holds (PLN-004/R9)", rows["F3"])  # o último fact_outcome, com a ref
        self.assertIn("sessão fails  [F4", rows["F4"])  # sem ref, sem parênteses
        self.assertIn("sessão holds", rows["F5"])
        self.assertIn("[F3 · rodado · 70] o texto de F3", rows["F3"])

    def test_does_not_mix_other_agents(self):
        _, out, _ = self.run_cli("--list", OTHER)
        self.assertEqual(len([line for line in out.splitlines() if line.startswith("F")]), 1)
        self.assertIn("Haiku holds", out)

    def test_ambiguous_and_unknown_prefix_exit_2(self):
        self.assertEqual(self.run_cli("--list", "a4e3")[0], 2)
        code, _, err = self.run_cli("--list", "zzzz")
        self.assertEqual(code, 2)
        self.assertTrue(err.strip())


if __name__ == "__main__":
    unittest.main()
