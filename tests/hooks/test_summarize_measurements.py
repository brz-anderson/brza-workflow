"""Testes do `scripts/summarize_measurements.py` (`--by-workstream`, `--facts` e `--by-task`), sobre um log montado pelo teste em
diretório temporário.

    python3 -m unittest discover -s .claude/hooks/tests
"""

import contextlib
import importlib.util
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

import repo_fixture

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPT = os.path.join(HERE, "..", "..", "core", "scripts", "summarize_measurements.py")


def load():
    spec = importlib.util.spec_from_file_location("summarize", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def turn(day, workstream, cost, rounds=None, **extra):
    row = {"event": "turn", "timestamp": f"{day}T10:00:00-03:00", "estimated_cost_usd": cost, "api_calls": 1,
           "rounds": rounds}
    if workstream:
        row["workstream"] = workstream
    return {**row, **extra}


def mem(day, hour, workstream, owner, action, file, kind):
    return {"event": "memory", "timestamp": f"{day}T{hour}:00:00-03:00", "workstream": workstream, "owner": owner,
            "action": action, "file": file, "type": kind}


ROUNDS = {"questions": 4, "recommended": 2, "other_option": 1, "free_text": 1}

ROWS = [
    turn("2026-10-28", "studio", 1.0, ROUNDS),
    turn("2026-10-28", "studio", 3.0, {"questions": 0, "recommended": 0, "other_option": 0, "free_text": 0}),
    turn("2026-10-29", "studio", 2.0),
    turn("2026-10-28", None, 0.5),  # a linha antiga, sem workstream
    {"event": "subagent", "timestamp": "2026-10-28T11:00:00-03:00", "workstream": "studio", "agent_type": "collector",
     "estimated_cost_usd": 0.4},
    {"event": "subagent", "timestamp": "2026-10-28T11:30:00-03:00", "workstream": "studio", "agent_type": "collector",
     "estimated_cost_usd": 0.6},
    {"event": "subagent", "timestamp": "2026-10-28T12:00:00-03:00", "workstream": "studio", "agent_type": "outro:reviewer",
     "estimated_cost_usd": 2.0},
    mem("2026-10-28", "09", "studio", "session", "created", "memory/a.md", "feedback"),
    mem("2026-10-28", "09", "studio", "session", "created", "memory/b.md", "user"),
    mem("2026-10-28", "09", "studio", "session", "updated", "memory/MEMORY.md", "index"),  # o índice não conta
    mem("2026-10-28", "10", "studio", "reviewer", "created", ".claude/agent-memory/reviewer/c.md", "project"),
    mem("2026-10-29", "09", "studio", "session", "updated", "memory/a.md", "feedback"),
    mem("2026-10-29", "10", "studio", "session", "deleted", "memory/b.md", "user"),
    mem("2026-10-29", "11", "tools-kit", "session", "created", "memory/z.md", "reference"),
    {"event": "skill", "timestamp": "2026-10-28T11:00:00-03:00", "skill": "x"},  # ignorada
]


class ByWorkstreamTest(unittest.TestCase):
    def setUp(self):
        self.summarize = load()
        self.days = self.summarize.aggregate_by_workstream(ROWS)

    def test_turns_cost_and_mean_per_workstream_and_day(self):
        day = self.days[("studio", "2026-10-28")]
        self.assertEqual((day["turns"]["n"], day["turns"]["cost"]), (2, 4.0))
        self.assertEqual(self.summarize.mean(day["turns"]), "2.00")
        self.assertEqual(self.days[("studio", "2026-10-29")]["turns"]["n"], 1)

    def test_audit_turn_stays_out_of_the_main_session_cost(self):
        rows = [turn("2026-10-28", "studio", 1.0, model="claude-opus-5-5"),
                turn("2026-10-28", "studio", 0.01, rounds=ROUNDS, model="claude-haiku-5-5", fact_audit=True,
                     skills=["x"])]
        days = self.summarize.aggregate_by_workstream(rows)
        self.assertEqual((days[("studio", "2026-10-28")]["turns"]["n"], days[("studio", "2026-10-28")]["turns"]["cost"]),
                         (1, 1.0))
        self.assertEqual(days[("studio", "2026-10-28")]["rounds"]["questions"], 0)
        agg = self.summarize.aggregate(rows)
        self.assertEqual([k[0] for k in agg["turns"]], ["claude-opus-5-5"])
        self.assertEqual(dict(agg["turns_by_skill"]), {})

    def test_old_line_without_workstream_goes_to_its_bucket(self):
        self.assertEqual(self.days[("(sem workstream)", "2026-10-28")]["turns"]["n"], 1)

    def test_subagent_cost_by_agent_type(self):
        agents = self.days[("studio", "2026-10-28")]["subagents"]
        self.assertEqual((agents["collector"]["n"], round(agents["collector"]["cost"], 2)), (2, 1.0))
        self.assertEqual(agents["reviewer"]["cost"], 2.0)  # o prefixo do plugin sai

    def test_memory_by_owner_with_feedback_apart_and_index_out(self):
        memory = self.days[("studio", "2026-10-28")]["memory"]
        self.assertEqual(memory[("session", True)], {"created": 1, "updated": 0, "deleted": 0})
        self.assertEqual(memory[("session", False)], {"created": 1, "updated": 0, "deleted": 0})  # o índice ficou de fora
        self.assertEqual(memory[("reviewer", False)]["created"], 1)
        later = self.days[("studio", "2026-10-29")]["memory"]
        self.assertEqual(later[("session", True)]["updated"], 1)
        self.assertEqual(later[("session", False)]["deleted"], 1)

    def test_live_memories_at_the_end_of_the_day_are_cumulative_by_events(self):
        self.assertEqual(self.days[("studio", "2026-10-28")]["live"], 3)  # a, b e c
        self.assertEqual(self.days[("studio", "2026-10-29")]["live"], 2)  # b saiu
        self.assertEqual(self.days[("tools-kit", "2026-10-29")]["live"], 1)

    def test_live_carries_over_a_day_without_memory_events(self):
        days = self.summarize.aggregate_by_workstream(ROWS + [turn("2026-10-30", "studio", 1.0)])
        self.assertEqual(days[("studio", "2026-10-30")]["live"], 2)

    def test_rounds_sum_and_percentages(self):
        rounds = self.days[("studio", "2026-10-28")]["rounds"]
        self.assertEqual(rounds, ROUNDS)
        self.assertEqual([self.summarize.pct(rounds[k], rounds["questions"]) for k in
                          ("recommended", "other_option", "free_text")], ["50%", "25%", "25%"])
        self.assertEqual(self.summarize.pct(0, 0), "")

    def test_command_line_prints_the_tables_and_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            with open(os.path.join(tmp, "measurements.jsonl"), "w", encoding="utf-8") as f:
                f.write("\n".join(json.dumps(r) for r in ROWS) + "\nnão é json\n")
            out = io.StringIO()
            with mock.patch.object(sys, "argv", ["summarize", "--dir", tmp, "--by-workstream"]), \
                    contextlib.redirect_stdout(out):
                self.assertEqual(self.summarize.main(), 0)
            text = out.getvalue()
            for expected in ("studio", "(sem workstream)", "2026-10-28", "collector", "feedback", "50%", "ilegível"):
                self.assertIn(expected, text)
            out = io.StringIO()
            with mock.patch.object(sys, "argv", ["summarize", "--dir", tmp, "--by-workstream", "--json"]), \
                    contextlib.redirect_stdout(out):
                self.summarize.main()
            data = json.loads(out.getvalue())
            self.assertIn(("studio", "2026-10-28"), {(d["workstream"], d["day"]) for d in data})

    def test_default_report_is_unchanged_by_the_new_fields(self):
        agg = self.summarize.aggregate(ROWS)
        self.assertEqual(sum(b["n"] for b in agg["turns"].values()), 4)

    def test_compact_guard_is_apart_from_the_resume_guard(self):
        agg = self.summarize.aggregate([
            {"event": "guard", "kind": "resume", "decision": "blocked", "estimated_cost_usd": 1.0},
            {"event": "guard", "kind": "compact", "workstream": "studio", "decision": "blocked"},
            {"event": "guard", "kind": "compact", "workstream": "studio", "decision": "blocked"},
            {"event": "guard", "kind": "compact", "workstream": "studio", "decision": "passed"},
        ])
        self.assertEqual(sum(b["n"] for b in agg["guard"].values()), 1)
        self.assertEqual(agg["compact"], {("studio", "blocked"): 2, ("studio", "passed"): 1})
        self.assertIn({"workstream": "studio", "decision": "blocked", "n": 2},
                      self.summarize.to_json(agg)["compact_guard"])


def route(decision, request=1, held_by=None, resolver=None, **extra):
    return {"event": "route", "timestamp": "2026-11-03T12:00:00-03:00", "workstream": "tools-kit", "caller": "s1",
            "request": request, "decision": decision, "held_by": held_by, "resolver": resolver, **extra}


class RouteTest(unittest.TestCase):
    """As linhas `route` do orquestrador MCP (PLN-009/P6), numa seção própria."""

    ROWS = [route("queued"), route("admitted"), route("held", 2, "workspace"), route("held", 3, "workspace"),
            route("held", 4, "machine"), route("resolved", 5, resolver={"path": "tev1", "confidence": 0.9}),
            route("resolved", 6, resolver={"path": "haiku", "confidence": None}),
            route("resolved", 7, resolver={"path": "tev1", "confidence": 0.7}), route("done"), route("failed", 8)]

    def setUp(self):
        self.summarize = load()

    def test_route_lines_are_counted_by_decision_held_by_and_resolver_path(self):
        agg = self.summarize.aggregate(self.ROWS)
        self.assertEqual(agg["route"]["decision"], {"queued": 1, "admitted": 1, "held": 3, "resolved": 3, "done": 1,
                                                    "failed": 1})
        self.assertEqual(agg["route"]["held_by"], {"workspace": 2, "machine": 1})
        self.assertEqual(agg["route"]["resolver"], {"tev1": 2, "haiku": 1})

    def test_the_other_sections_do_not_change(self):
        with_route = self.summarize.aggregate(ROWS + self.ROWS)
        without = self.summarize.aggregate(ROWS)
        for key in ("agents", "models", "turns", "skills", "commands", "failures", "mcp", "guard", "compact"):
            self.assertEqual(with_route[key], without[key])
        self.assertEqual(without["route"]["decision"], {})

    def test_command_line_prints_the_section_and_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            with open(os.path.join(tmp, "measurements.jsonl"), "w", encoding="utf-8") as f:
                f.write("\n".join(json.dumps(r) for r in self.ROWS) + "\n")
            out = io.StringIO()
            with mock.patch.object(sys, "argv", ["summarize", "--dir", tmp]), contextlib.redirect_stdout(out):
                self.assertEqual(self.summarize.main(), 0)
            text = out.getvalue()
            self.assertIn("Roteamento do orquestrador", text)
            for expected in ("workspace", "machine", "tev1", "haiku", "held"):
                self.assertIn(expected, text)
            out = io.StringIO()
            with mock.patch.object(sys, "argv", ["summarize", "--dir", tmp, "--json"]), contextlib.redirect_stdout(out):
                self.summarize.main()
            data = json.loads(out.getvalue())["route"]
            self.assertEqual(data["held_by"], {"workspace": 2, "machine": 1})
            self.assertEqual(data["resolver"], {"tev1": 2, "haiku": 1})

def fact(day, agent, fact_id, kind, confidence, agent_id="a1", **extra):
    return {"event": "fact", "timestamp": f"{day}T10:00:00-03:00", "agent_id": agent_id, "agent_type": agent,
            "workstream": "studio", "fact_id": fact_id, "kind": kind, "confidence": confidence, "text": "t", **extra}


def check(agent_id, fact_id, verdict, cite="ok", day="2026-10-28", **extra):
    return {"event": "fact_check", "timestamp": f"{day}T10:30:00-03:00", "agent_id": agent_id, "fact_id": fact_id,
            "verdict": verdict, "cite": cite, **extra}


def downgraded(agent_id, fact_id, claimed="holds", day="2026-10-28"):
    """O `fact_check` que o `fact_audit.py` rebaixou: o auditor disse `claimed`, o `read` não casou, o veredito é unknown."""
    return check(agent_id, fact_id, "unknown", day=day, read="x.py", read_ok=False, claimed=claimed)


def outcome(agent_id, fact_id, verdict, day="2026-10-28"):
    return {"event": "fact_outcome", "timestamp": f"{day}T11:00:00-03:00", "agent_id": agent_id, "fact_id": fact_id,
            "verdict": verdict}


def capture(agent, facts=1, malformed=0, unmarked=False, day="2026-10-28"):
    row = {"event": "fact_capture", "timestamp": f"{day}T10:00:00-03:00", "agent_id": "a1", "agent_type": agent,
           "facts": facts, "malformed": malformed}
    if unmarked:
        row["unmarked"] = True
    return row


def audit(cost, error=None, ignored=0, day="2026-10-28", **extra):
    return {"event": "fact_audit", "timestamp": f"{day}T10:40:00-03:00", "agent_id": "a1", "facts": 1, "checked": 1,
            "ignored": ignored, "cost_usd": cost, "error": error, **extra}


class FactsTest(unittest.TestCase):
    def setUp(self):
        self.summarize = load()

    def agg(self, rows, since=""):
        return self.summarize.aggregate_facts(rows, since)

    def line(self, agg, agent, klass, band, kind):
        return agg["rows"][(agent, klass, band, kind)]

    def test_session_verdict_beats_the_haiku_verdict_and_is_the_last_one(self):
        rows = [fact("2026-10-28", "designer", "F1", "lido", 95), check("a1", "F1", "holds"),
                outcome("a1", "F1", "fails"),
                fact("2026-10-28", "designer", "F2", "lido", 95), check("a1", "F2", "fails"),
                check("a1", "F2", "holds"),  # o último do Haiku vale, sem desfecho da sessão
                fact("2026-10-28", "designer", "F3", "lido", 95), outcome("a1", "F3", "fails"),
                outcome("a1", "F3", "holds")]  # o último da sessão vale
        row = self.line(self.agg(rows), "designer", "fato", "90+", "lido")
        self.assertEqual((row["facts"], row["with_outcome"], row["holds"], row["fails"]), (3, 3, 2, 1))

    def test_fact_without_check_or_outcome_counts_only_in_facts(self):
        row = self.line(self.agg([fact("2026-10-28", "designer", "F1", "lido", 90)]), "designer", "fato", "90+", "lido")
        self.assertEqual((row["facts"], row["with_outcome"], row["unknown"], row["holds_pct"],
                          row["mean_confidence"], row["distance"]), (1, 0, 0, None, None, None))

    def test_first_fact_event_of_each_id_is_the_one_that_counts(self):
        rows = [fact("2026-10-28", "designer", "F1", "lido", 95), fact("2026-10-29", "designer", "F1", "inferido", 50),
                fact("2026-10-28", "designer", "F1", "lido", 95, agent_id="a2")]  # outro agente, outro fato
        agg = self.agg(rows)
        self.assertEqual(list(agg["rows"]), [("designer", "fato", "90+", "lido")])
        self.assertEqual(agg["rows"][("designer", "fato", "90+", "lido")]["facts"], 2)

    def test_unknown_is_counted_apart_and_stays_out_of_the_hit_rate(self):
        rows = [fact("2026-10-28", "designer", "F1", "lido", 90), check("a1", "F1", "unknown"),
                fact("2026-10-28", "designer", "F2", "lido", 90), check("a1", "F2", "holds"),
                fact("2026-10-28", "designer", "F3", "lido", 90), check("a1", "F3", "fails")]
        row = self.line(self.agg(rows), "designer", "fato", "90+", "lido")
        self.assertEqual((row["facts"], row["with_outcome"], row["unknown"], row["holds_pct"]), (3, 2, 1, 50.0))

    def test_bands_at_the_limits(self):
        rows = [fact("2026-10-28", "designer", f"F{c}", "lido", c) for c in (79, 80, 89, 90, 100)]
        agg = self.agg(rows)
        self.assertEqual([k[2] for k in agg["rows"]], ["90+", "80–89", "<80"])
        self.assertEqual(agg["rows"][("designer", "fato", "80–89", "lido")]["facts"], 2)
        self.assertEqual(agg["rows"][("designer", "fato", "90+", "lido")]["facts"], 2)
        self.assertEqual(agg["rows"][("designer", "fato", "<80", "lido")]["facts"], 1)

    def test_fact_and_cost_classes_are_separate_rows(self):
        rows = [fact("2026-10-28", "designer", "F1", "inferido", 70), fact("2026-10-28", "designer", "C1", "inferido", 70)]
        self.assertEqual(list(self.agg(rows)["rows"]),
                         [("designer", "fato", "<80", "inferido"), ("designer", "custo", "<80", "inferido")])

    def test_rows_come_sorted_by_role_class_band_and_kind(self):
        rows = [fact("2026-10-28", "designer", "C1", "doc", 85), fact("2026-10-28", "collector", "F2", "inferido", 60),
                fact("2026-10-28", "designer", "F1", "inferido", 95), fact("2026-10-28", "designer", "F3", "rodado", 95),
                fact("2026-10-28", "designer", "F4", "lido", 70), fact("2026-10-28", "designer", "F5", "lido", 85),
                fact("2026-10-28", "outro:collector", "F1", "lido", 99, agent_id="a9")]
        self.assertEqual(list(self.agg(rows)["rows"]), [
            ("collector", "fato", "90+", "lido"), ("collector", "fato", "<80", "inferido"),
            ("designer", "fato", "90+", "rodado"), ("designer", "fato", "90+", "inferido"),
            ("designer", "fato", "80–89", "lido"), ("designer", "fato", "<80", "lido"),
            ("designer", "custo", "80–89", "doc")])

    def test_signed_distance_is_mean_confidence_minus_hit_rate(self):
        over = [fact("2026-10-28", "designer", "F1", "lido", 90), check("a1", "F1", "holds"),
                fact("2026-10-28", "designer", "F2", "lido", 100), check("a1", "F2", "fails")]
        row = self.line(self.agg(over), "designer", "fato", "90+", "lido")
        self.assertEqual((row["holds_pct"], row["mean_confidence"], row["distance"]), (50.0, 95.0, 45.0))
        under = [fact("2026-10-28", "designer", "F1", "inferido", 60), check("a1", "F1", "holds")]
        row = self.line(self.agg(under), "designer", "fato", "<80", "inferido")
        self.assertEqual((row["holds_pct"], row["mean_confidence"], row["distance"]), (100.0, 60.0, -40.0))

    def test_mean_confidence_covers_only_the_facts_with_outcome(self):
        rows = [fact("2026-10-28", "designer", "F1", "lido", 90), check("a1", "F1", "holds"),
                fact("2026-10-28", "designer", "F2", "lido", 99)]  # sem desfecho: fora da média
        row = self.line(self.agg(rows), "designer", "fato", "90+", "lido")
        self.assertEqual((row["facts"], row["mean_confidence"]), (2, 90.0))

    def test_cite_off_is_measured_among_the_facts_with_a_check_and_apart_from_the_verdict(self):
        rows = [fact("2026-10-28", "designer", "F1", "lido", 90), check("a1", "F1", "holds", cite="off"),
                fact("2026-10-28", "designer", "F2", "lido", 90), check("a1", "F2", "holds", cite="ok"),
                fact("2026-10-28", "designer", "F3", "lido", 90), outcome("a1", "F3", "holds"),  # sem check: fora
                fact("2026-10-28", "designer", "F4", "lido", 90), check("a1", "F4", "unknown", cite="off")]
        row = self.line(self.agg(rows), "designer", "fato", "90+", "lido")
        self.assertEqual((row["checked"], row["cite_off"], row["cite_off_pct"]), (3, 2, 100 * 2 / 3))
        self.assertEqual(row["holds"], 3)  # o `cite: off` não muda o veredito
        rows = [fact("2026-10-28", "designer", "F1", "lido", 90), outcome("a1", "F1", "holds")]
        self.assertIsNone(self.line(self.agg(rows), "designer", "fato", "90+", "lido")["cite_off_pct"])

    def test_cite_comes_from_the_last_check(self):
        rows = [fact("2026-10-28", "designer", "F1", "lido", 90), check("a1", "F1", "holds", cite="off"),
                check("a1", "F1", "holds", cite="ok")]
        self.assertEqual(self.line(self.agg(rows), "designer", "fato", "90+", "lido")["cite_off"], 0)

    def test_captures_per_role_with_unmarked_and_malformed(self):
        rows = [capture("designer", 2, 1), capture("designer", 0, 0, unmarked=True),
                capture("outro:designer", 1, 2), capture("collector", 3)]
        agg = self.agg(rows)
        self.assertEqual(agg["captures"]["designer"], {"read": 3, "unmarked": 1, "malformed": 3})
        self.assertEqual(agg["captures"]["collector"], {"read": 1, "unmarked": 0, "malformed": 0})

    def test_audits_sum_the_cost_and_count_errors_and_ignored(self):
        rows = [audit(0.0123, ignored=1), audit(0.0200, error="timeout", ignored=2), audit(None, error="binary_not_found")]
        self.assertEqual(self.agg(rows)["audits"], {"n": 3, "cost": 0.0323, "failed": 2, "ignored": 3, "downgraded": 0})

    def test_audits_sum_the_downgraded_and_the_old_ones_without_the_field_count_zero(self):
        rows = [audit(0.01, downgraded=2), audit(0.01, downgraded=3, day="2026-10-20"), audit(0.01)]
        self.assertEqual(self.agg(rows)["audits"]["downgraded"], 5)
        self.assertEqual(self.agg(rows, since="2026-10-25")["audits"]["downgraded"], 2)

    def test_downgraded_counts_the_facts_whose_last_check_was_read_not_ok_with_a_claimed_other_than_unknown(self):
        rows = [fact("2026-10-28", "designer", "F1", "lido", 95), downgraded("a1", "F1", "holds"),
                fact("2026-10-28", "designer", "F2", "lido", 95), downgraded("a1", "F2", "fails"),
                fact("2026-10-28", "designer", "F3", "lido", 95), downgraded("a1", "F3", "unknown"),  # não é rebaixado
                fact("2026-10-28", "designer", "F4", "lido", 95),
                check("a1", "F4", "holds", read="x.py", read_ok=True),                              # leitura casou
                fact("2026-10-28", "designer", "F5", "lido", 95), check("a1", "F5", "unknown"),    # check antigo
                fact("2026-10-28", "designer", "F6", "lido", 95), downgraded("a1", "F6", "holds"),
                check("a1", "F6", "holds", read="x.py", read_ok=True),                              # vale o último
                fact("2026-10-28", "designer", "F7", "lido", 95)]                                    # sem check
        row = self.line(self.agg(rows), "designer", "fato", "90+", "lido")
        self.assertEqual((row["facts"], row["downgraded"]), (7, 2))

    def test_downgraded_is_counted_by_row_and_a_session_outcome_does_not_hide_it(self):
        rows = [fact("2026-10-28", "designer", "F1", "lido", 95), downgraded("a1", "F1"), outcome("a1", "F1", "holds"),
                fact("2026-10-28", "designer", "F2", "inferido", 60), downgraded("a1", "F2", "fails")]
        agg = self.agg(rows)
        self.assertEqual(self.line(agg, "designer", "fato", "90+", "lido")["downgraded"], 1)
        self.assertEqual(self.line(agg, "designer", "fato", "<80", "inferido")["downgraded"], 1)

    def test_pending_are_inferred_low_or_cost_facts_without_a_session_outcome(self):
        rows = [fact("2026-10-28", "designer", "F1", "inferido", 95),   # pendente: inferido
                fact("2026-10-28", "designer", "F2", "lido", 79),       # pendente: abaixo de 80
                fact("2026-10-28", "designer", "C1", "lido", 95),       # pendente: custo
                fact("2026-10-28", "designer", "F3", "lido", 80),       # não: 80 e lido
                fact("2026-10-28", "designer", "F4", "inferido", 50), outcome("a1", "F4", "holds"),  # tem desfecho
                fact("2026-10-28", "designer", "F5", "inferido", 50), check("a1", "F5", "holds")]    # o Haiku não basta
        self.assertEqual(self.agg(rows)["pending"], 4)

    def test_since_filters_by_the_fact_date_and_an_outcome_arriving_later_still_counts(self):
        rows = [fact("2026-10-20", "designer", "F1", "lido", 95), outcome("a1", "F1", "fails", day="2026-10-30"),
                fact("2026-10-29", "designer", "F2", "lido", 95, agent_id="a2"),
                outcome("a2", "F2", "holds", day="2026-10-30"),
                capture("designer", 1, day="2026-10-20"), capture("designer", 1, day="2026-10-29"),
                audit(0.5, day="2026-10-20"), audit(0.25, day="2026-10-29"),
                fact("2026-10-29", "designer", "F3", "inferido", 50, agent_id="a2"),
                outcome("a2", "F3", "holds", day="2026-10-20")]  # o desfecho anterior à data não some
        agg = self.agg(rows, since="2026-10-25")
        row = self.line(agg, "designer", "fato", "90+", "lido")
        self.assertEqual((row["facts"], row["holds"], row["fails"]), (1, 1, 0))
        self.assertEqual(agg["captures"]["designer"]["read"], 1)
        self.assertEqual((agg["audits"]["n"], agg["audits"]["cost"]), (1, 0.25))
        self.assertEqual(agg["pending"], 0)

    def test_since_keeps_the_first_fact_even_when_a_later_duplicate_is_inside_the_range(self):
        rows = [fact("2026-10-20", "designer", "F1", "lido", 95), fact("2026-10-29", "designer", "F1", "lido", 95)]
        self.assertEqual(self.agg(rows, since="2026-10-25")["rows"], {})

    def test_table_example_and_dashes(self):
        rows = [fact("2026-10-28", "designer", "F1", "lido", 90), check("a1", "F1", "holds", cite="off"),
                fact("2026-10-28", "designer", "F2", "lido", 100), check("a1", "F2", "fails"),
                fact("2026-10-28", "designer", "C1", "inferido", 60), capture("designer", 3, 1, unmarked=False),
                capture("collector", 0, 0, unmarked=True), audit(0.0123, error="timeout", ignored=2)]
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.summarize.print_facts(self.agg(rows))
        text = out.getvalue()
        print("\n" + text)
        header = next(l for l in text.splitlines() if l.startswith("papel"))
        for column in ("classe", "faixa", "tipo", "fatos", "com desfecho", "holds %", "confiança média", "distância",
                       "unknown", "rebaixados", "cite off %"):
            self.assertIn(column, header)
        lido = next(l for l in text.splitlines() if "lido" in l and "90+" in l)
        for expected in ("50.0%", "95.0", "+45.0", "50.0%"):
            self.assertIn(expected, lido)
        custo = next(l for l in text.splitlines() if "custo" in l)
        self.assertEqual(custo.count("—"), 4)  # holds %, confiança, distância e cite off %: nenhum com desfecho ou check
        self.assertIn("Por papel: collector: lidas 1, sem marca 1, malformadas 0; designer: lidas 1, sem marca 0, malformadas 1",
                      text)
        self.assertIn("Auditorias: rodaram 1, custo US$ 0.0123, falharam 1, ignorados 2, rebaixados 0", text)
        self.assertIn("Pendentes: 1 sem desfecho (inferido, abaixo de 80 ou custo) — python3 scripts/fact_outcome.py --pending --workstream all", text)

    def test_table_shows_the_downgraded_per_row_and_in_the_audits_line(self):
        rows = [fact("2026-10-28", "designer", "F1", "lido", 95), downgraded("a1", "F1"),
                fact("2026-10-28", "designer", "F2", "lido", 95), downgraded("a1", "F2", "fails"),
                fact("2026-10-28", "designer", "C1", "inferido", 60), audit(0.01, downgraded=2)]
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.summarize.print_facts(self.agg(rows))
        lines = out.getvalue().splitlines()
        header = next(l for l in lines if l.startswith("papel")).split()
        lido = next(l for l in lines if "lido" in l and "90+" in l).split()
        custo = next(l for l in lines if "custo" in l and "inferido" in l).split()
        # as colunas de uma palavra só, contadas do fim: ... unknown rebaixados "cite off %"
        self.assertEqual((lido[-3], lido[-2]), ("2", "2"))  # o rebaixado também é `unknown`
        self.assertEqual((custo[-3], custo[-2]), ("0", "0"))
        self.assertIn("rebaixados", header)
        self.assertIn("ignorados 0, rebaixados 2", out.getvalue())

    def run_main(self, rows, *flags, raw=""):
        with tempfile.TemporaryDirectory() as tmp:
            with open(os.path.join(tmp, "measurements.jsonl"), "w", encoding="utf-8") as f:
                f.write("\n".join(json.dumps(r) for r in rows) + "\n" + raw)
            out = io.StringIO()
            with mock.patch.object(sys, "argv", ["summarize", "--dir", tmp, *flags]), contextlib.redirect_stdout(out):
                code = self.summarize.main()
            return code, out.getvalue()

    def test_command_line_prints_the_facts_and_the_rest_of_the_report_is_not_touched(self):
        rows = [fact("2026-10-28", "designer", "F1", "lido", 90), check("a1", "F1", "holds"), capture("designer")]
        code, text = self.run_main(rows, "--facts")
        self.assertEqual(code, 0)
        self.assertIn("holds %", text)
        self.assertNotIn("Subagentes", text)
        code, text = self.run_main(rows)
        self.assertNotIn("holds %", text)

    def test_json_has_the_same_aggregation(self):
        rows = [fact("2026-10-28", "designer", "F1", "lido", 90), check("a1", "F1", "holds", cite="off"),
                capture("designer", 1, 2, unmarked=True), audit(0.5, error="x", ignored=1),
                fact("2026-10-28", "designer", "C1", "inferido", 50)]
        code, text = self.run_main(rows, "--facts", "--json")
        data = json.loads(text)
        self.assertEqual(code, 0)
        first = data["rows"][0]
        self.assertEqual((first["agent"], first["class"], first["band"], first["kind"], first["facts"],
                          first["with_outcome"], first["holds_pct"], first["mean_confidence"], first["distance"],
                          first["unknown"], first["cite_off_pct"]),
                         ("designer", "fato", "90+", "lido", 1, 1, 100.0, 90.0, -10.0, 0, 100.0))
        self.assertEqual(data["rows"][1]["holds_pct"], None)
        self.assertEqual(data["captures"], [{"agent": "designer", "read": 1, "unmarked": 1, "malformed": 2}])
        self.assertEqual(data["audits"], {"n": 1, "cost": 0.5, "failed": 1, "ignored": 1, "downgraded": 0})
        self.assertEqual(first["downgraded"], 0)
        self.assertEqual(data["pending"], 1)

    def test_json_carries_the_downgraded_of_the_rows_and_of_the_audits(self):
        rows = [fact("2026-10-28", "designer", "F1", "lido", 95), downgraded("a1", "F1"), audit(0.1, downgraded=1)]
        code, text = self.run_main(rows, "--facts", "--json")
        data = json.loads(text)
        self.assertEqual((code, data["rows"][0]["downgraded"], data["audits"]["downgraded"]), (0, 1, 1))

    def test_log_without_fact_events_says_so_and_exits_zero(self):
        code, text = self.run_main(ROWS, "--facts")
        self.assertEqual(code, 0)
        self.assertEqual(text.strip().splitlines()[-1], "nenhum fato marcado ainda")
        self.assertNotIn("holds %", text)
        code, text = self.run_main(ROWS, "--facts", "--json")
        self.assertEqual((code, json.loads(text)["rows"]), (0, []))

    def test_since_after_every_fact_also_says_there_is_none(self):
        code, text = self.run_main([fact("2026-10-20", "designer", "F1", "lido", 90)], "--facts", "--since", "2026-10-25")
        self.assertEqual(code, 0)
        self.assertIn("nenhum fato marcado ainda", text)

    def test_log_with_only_captures_says_none_and_still_shows_the_unmarked_line(self):
        code, text = self.run_main([capture("designer", 0, 0, unmarked=True)], "--facts")
        self.assertIn("nenhum fato marcado ainda", text)
        self.assertIn("Por papel: designer: lidas 1, sem marca 1, malformadas 0", text)
        self.assertNotIn("Pendentes", text)

    def test_since_and_the_header_in_the_command_line(self):
        rows = [fact("2026-10-20", "designer", "F1", "lido", 90), outcome("a1", "F1", "holds", day="2026-10-30"),
                fact("2026-10-29", "designer", "F2", "lido", 90, agent_id="a2")]
        code, text = self.run_main(rows, "--facts", "--since", "2026-10-25", raw="não é json\n")
        self.assertIn("desde 2026-10-25", text)
        self.assertIn("ilegível", text)
        lido = next(l for l in text.splitlines() if "lido" in l and "90+" in l)
        self.assertNotIn("100", lido)  # o F1 de antes da data ficou fora

    def test_facts_does_not_combine_with_the_other_views(self):
        for flags in (("--by-workstream",), ("--denials",)):
            with self.assertRaises(SystemExit) as raised, contextlib.redirect_stderr(io.StringIO()):
                self.run_main([], "--facts", *flags)
            self.assertEqual(raised.exception.code, 2)

    def test_missing_log_still_exits_two(self):
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.object(sys, "argv", ["summarize", "--dir", tmp, "--facts"]), \
                    contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(self.summarize.main(), 2)


# --- --by-task e --pending (PLN-012/P2, D4) --------------------------------------------------------------------------
#
# O caso da sessão 08e6c812 de 2026-11-03, no workstream tools-kit: os 5 turnos e os 2 subagentes como o hook gravou
# (`.measurements/measurements.jsonl` do brza-workspace), com os campos novos do D2 onde o P1 os grava; e os 3 eventos
# `task` que o P3 vai gravar, montados à mão na forma do D1, um por commit de worklog da sessão em workstream/tools-kit:
# 74fbe0f (22:07:39) e 539b744 (22:16:03), da abertura do PLN-012, e afaf8c0 (22:32:31), da instalação do grok.

SID = "08e6c812-8bb3-42fc-a47f-403fdb472ccd"
OPENING = "work/worklogs/2026-11-03-pln-012-abertura-da-produtividade-por-tarefa.md"
GROK = "work/worklogs/2026-11-03-instalacao-do-grok-build.md"
MACHINE = {"pid": 44452, "from": "env", "cpu_self_s": 0.84, "cpu_children_s": 6.13, "rss_mb": 357, "footprint_mb": 209}


def at(hhmmss, day="2026-11-03"):
    return f"{day}T{hhmmss}-03:00"


def tturn(end, duration, cost, session=SID, workstream="tools-kit", **extra):
    return {"event": "turn", "timestamp": end if "T" in end else at(end), "session_id": session, "duration_s": duration,
            "estimated_cost_usd": cost, "workstream": workstream, "model": "claude-opus-5-5", **extra}


def tsub(end, duration, cost, session=SID, workstream="tools-kit"):
    return {"event": "subagent", "timestamp": end if "T" in end else at(end), "session_id": session,
            "duration_s": duration, "estimated_cost_usd": cost, "workstream": workstream, "agent_type": "designer"}


def task(stamp, worklogs, commit, session=SID, workstream="tools-kit", until=None, **extra):
    return {"event": "task", "timestamp": stamp if "T" in stamp else at(stamp), "session_id": session,
            "bridge_session_id": None, "chat_id": "claude_2ead5f2f_c37e_41a1_8ef4_a299af008379",
            "session_name": "tools-kit-produtividade" if session == SID else None, "pid": 44452,
            "cwd": "/Users/andepb/Projects/brza-workspace/.worktrees/tools-kit", "workstream": workstream,
            "worklogs": list(worklogs), "commit": commit, "until": until, **extra}


def run(end, duration, cpu, rss, session=SID, workstream="tools-kit"):
    return {"event": "run", "timestamp": end if "T" in end else at(end), "session_id": session, "chat_id": None,
            "mode": "tab", "cwd": "/x", "workstream": workstream, "command": "pnpm test", "code": 0,
            "duration_s": duration, "cpu_s": cpu, "max_rss_mb": rss}


SESSION = [
    {"event": "command", "timestamp": at("21:59:33"), "session_id": SID, "command": "plan-runner"},
    # T1, 21:59:33–22:17:30: as duas rodadas esperaram 459 s e 166 s [PLN-012-design F7]
    tturn("22:17:30", 1077, 2.35, waits_s=[459, 166], machine=MACHINE,
          prompt_head="<command-message>plan-runner</command-message> <command-name>/plan-runner</command-name> "),
    task("22:07:39", [OPENING], "74fbe0f"),
    task("22:16:03", [OPENING], "539b744"),
    tturn("22:32:45", 87, 0.43),  # T2, 22:31:18–22:32:45, a linha antiga: sem waits_s, prompt_head nem machine
    task("22:32:31", [GROK], "afaf8c0"),
    tturn("22:32:51", 5, 0.07, waits_s=[], prompt_head="> Grok CLI da xAI  esse"),  # T3, de 22:32:46
    tturn("22:35:19", 59, 0.38, waits_s=[],  # T4, de 22:34:20
          prompt_head="precisamos urgentemente adicionar o `grok` no fluxo do `brza sessions` e do `MCP"),
    tturn("22:42:21", 39, 0.2, waits_s=[], prompt_head="siga com o `PLN-012`"),  # T5, de 22:41:42
    tsub("22:53:35", 678, 2.87),  # começa às 22:42:17, dentro do T5
    tsub("23:01:45", 257, 1.18),  # começa às 22:57:28, fora dos 5 turnos e depois do último marco
]


def by_worklog(result):
    return {t["worklog"]: t for t in result["tasks"]}


class ByTaskTest(unittest.TestCase):
    def setUp(self):
        self.summarize = load()

    def measure(self, rows, **kwargs):
        """O resumo com um `task` de outra sessão à meia-noite à frente, o deploy: as linhas que terminam antes do
        primeiro `task` ficam de fora (R4.1), e o caso sintético não depende do turno que contém o commit."""
        deploy = task("2026-11-03T00:00:00-03:00", ["work/worklogs/2026-11-02-deploy.md"], "d0", session="deploy")
        result = self.summarize.aggregate_by_task([deploy] + rows, **kwargs)
        result["unmeasured"] = [u for u in result["unmeasured"] if u["commit"] != "d0"]
        return result

    def test_the_session_of_2026_11_03_in_three_tasks(self):
        result = self.measure(SESSION)
        tasks = by_worklog(result)
        self.assertEqual(set(tasks), {OPENING, GROK})
        opening = tasks[OPENING]
        self.assertEqual((opening["agent_s"], opening["anderson_s"], opening["pause_s"], opening["elapsed_s"]),
                         (452, 625, 0, 1077))
        self.assertEqual((opening["turns"], opening["sessions"], opening["commits"]), (1, 1, 2))
        self.assertAlmostEqual(opening["cost_turns"], 2.35)
        self.assertEqual(opening["cost_subagents"], 0)
        self.assertEqual((opening["cpu_self_s"], opening["cpu_children_s"], opening["rss_max_mb"]), (0.84, 6.13, 357))
        self.assertEqual((opening["workstream"], opening["day"], opening["task"]),
                         ("tools-kit", "2026-11-03", "pln-012-abertura-da-produtividade-por-tarefa"))
        grok = tasks[GROK]
        self.assertEqual((grok["turns"], grok["agent_s"], grok["anderson_s"], grok["elapsed_s"]), (1, 87, 0, 87))
        self.assertAlmostEqual(grok["cost_turns"], 0.43)
        self.assertEqual(grok["machine_turns"], 0)  # a linha antiga, sem machine, fica sem CPU
        self.assertIsNone(grok["cpu_self_s"])
        self.assertEqual(result["unmeasured"], [])

    def test_the_idle_time_and_the_turns_without_task_of_the_session(self):
        (session,) = self.measure(SESSION)["sessions"]
        self.assertEqual((session["session_id"], session["name"], session["workstream"]),
                         (SID, "tools-kit-produtividade", "tools-kit"))
        # do fim do T1 (22:17:30) ao começo do T2 (22:31:18), 13,8 min; e 1 s do fim do T2 ao começo do T3
        self.assertEqual(session["idle_s"], 828 + 1)
        self.assertEqual(round(828 / 60, 1), 13.8)
        self.assertEqual(session["no_task_turns"], 3)  # T3 a T5
        self.assertAlmostEqual(session["no_task_cost_turns"], 0.07 + 0.38 + 0.2)
        self.assertAlmostEqual(session["no_task_cost_subagents"], 2.87 + 1.18)  # um no T5, outro depois do marco
        self.assertEqual(session["no_task_since"], "2026-11-03T22:32:46-03:00")

    def test_segments_and_measure_are_module_functions_for_brza_note(self):
        segments = self.summarize.task_segments(SESSION)
        closed = [s for s in segments if s["task"]]
        self.assertEqual([s["task"]["commit"] for s in closed], ["74fbe0f", "539b744", "afaf8c0"])
        self.assertEqual([len([r for r in s["rows"] if r["event"] == "turn"]) for s in closed], [1, 0, 1])
        (open_,) = [s for s in segments if not s["task"]]
        self.assertEqual([r["timestamp"] for r in open_["rows"] if r["event"] == "turn"],
                         [at("22:32:51"), at("22:35:19"), at("22:42:21")])
        measure = self.summarize.task_measure(closed[0]["rows"])
        self.assertEqual((measure["agent_s"], measure["anderson_s"], measure["pause_s"]), (452, 625, 0))

    def test_a_wait_of_fifty_minutes_is_thirty_of_anderson_and_twenty_of_pause(self):
        rows = [tturn("10:00:00", 3600, 1.0, waits_s=[3000]), task("10:01:00", [OPENING], "a1")]
        t = by_worklog(self.measure(rows))[OPENING]
        self.assertEqual((t["agent_s"], t["anderson_s"], t["pause_s"], t["elapsed_s"]), (600, 1800, 1200, 3600))
        # o mesmo para o intervalo entre dois turnos do segmento
        rows = [tturn("09:00:00", 60, 0.1), tturn("09:51:00", 60, 0.1), task("09:52:00", [OPENING], "a1")]
        t = by_worklog(self.measure(rows))[OPENING]
        self.assertEqual((t["agent_s"], t["anderson_s"], t["pause_s"], t["elapsed_s"]), (120, 1800, 1200, 3120))

    def test_two_worklogs_in_one_commit_split_the_segment_in_halves(self):
        rows = [tturn("10:00:00", 100, 1.0, machine=MACHINE), task("10:01:00", [OPENING, GROK], "a1")]
        tasks = by_worklog(self.measure(rows))
        for path in (OPENING, GROK):
            self.assertAlmostEqual(tasks[path]["cost_turns"], 0.5)
            self.assertEqual((tasks[path]["elapsed_s"], tasks[path]["shared_with"]), (50, 1))
            self.assertAlmostEqual(tasks[path]["cpu_children_s"], 6.13 / 2)

    def test_a_task_without_session_or_without_turns_is_unmeasured(self):
        rows = [tturn("10:00:00", 100, 1.0), task("10:01:00", [OPENING], "a1"),
                task("10:05:00", [GROK], "b2", session=None),
                task("10:06:00", ["work/worklogs/2026-11-03-outra.md"], "c3", session="sem-turno")]
        result = self.measure(rows)
        self.assertEqual(set(by_worklog(result)), {OPENING})
        self.assertEqual([(u["worklog"], u["commit"], u["reason"]) for u in result["unmeasured"]],
                         [(GROK, "b2", "sem sessão"), ("work/worklogs/2026-11-03-outra.md", "c3", "sessão sem turno")])

    def test_until_moves_the_mark(self):
        rows = [tturn("10:05:00", 300, 1.0), tturn("10:25:00", 300, 2.0),
                task("10:30:00", [OPENING], "a1", until=at("10:10:00"))]
        result = self.measure(rows)
        self.assertEqual(by_worklog(result)[OPENING]["turns"], 1)
        self.assertAlmostEqual(by_worklog(result)[OPENING]["cost_turns"], 1.0)
        self.assertEqual(result["sessions"][0]["no_task_turns"], 1)
        rows[-1]["until"] = "10:10"  # HH:MM, do dia do commit
        self.assertEqual(by_worklog(self.measure(rows))[OPENING]["turns"], 1)

    def test_a_turn_before_the_first_task_stays_out(self):
        rows = [tturn("08:00:00", 60, 5.0), tturn("08:30:00", 60, 7.0, session="outra"),
                tturn("10:05:00", 600, 1.0), task("10:00:00", [OPENING], "a1")]
        result = self.summarize.aggregate_by_task(rows)
        t = by_worklog(result)[OPENING]
        self.assertEqual((t["turns"], t["cost_turns"]), (1, 1.0))  # o turno que termina depois do 1º task entra
        self.assertEqual([s["session_id"] for s in result["sessions"]], [SID])

    def test_the_audit_turn_stays_out(self):
        rows = [tturn("10:00:00", 60, 1.0), tturn("10:00:30", 20, 0.01, fact_audit=True), task("10:01:00", [OPENING], "a1")]
        t = by_worklog(self.measure(rows))[OPENING]
        self.assertEqual((t["turns"], t["cost_turns"]), (1, 1.0))

    def test_subagent_and_run_go_to_the_turn_that_contains_their_start_or_to_the_segment_of_the_marks(self):
        rows = [tturn("10:10:00", 600, 1.0), task("10:11:00", [OPENING], "a1"),
                tsub("10:20:00", 900, 0.5),  # começa 10:05, dentro do turno → abertura
                run("10:30:00", 60, 120.0, 900),  # começa 10:29, fora de turno, entre os marcos 10:11 e 10:41 → grok
                run("10:09:00", 60, 30.0, 1800),  # começa 10:08, dentro do turno → abertura
                tturn("10:40:00", 60, 0.2), task("10:41:00", [GROK], "b2"),
                tsub("10:45:00", 600, 0.3)]  # começa 10:35, fora de turno, entre os marcos 10:11 e 10:41 → grok
        result = self.measure(rows)
        tasks = by_worklog(result)
        self.assertAlmostEqual(tasks[OPENING]["cost_subagents"], 0.5)
        self.assertEqual((tasks[OPENING]["runs"], tasks[OPENING]["run_cpu_s"], tasks[OPENING]["run_max_rss_mb"]),
                         (1, 30.0, 1800))
        self.assertEqual((tasks[GROK]["runs"], tasks[GROK]["run_cpu_s"]), (1, 120.0))
        self.assertAlmostEqual(tasks[GROK]["cost_subagents"], 0.3)
        self.assertEqual(result["sessions"][0]["no_task_turns"], 0)

    def test_a_worklog_committed_in_two_sessions_sums_both(self):
        rows = [tturn("10:00:00", 100, 1.0), tturn("10:00:00", 200, 2.0, session="s2"),
                task("10:01:00", [OPENING], "a1"), task("10:02:00", [OPENING], "b2", session="s2")]
        t = by_worklog(self.measure(rows))[OPENING]
        self.assertEqual((t["sessions"], t["turns"], t["elapsed_s"]), (2, 2, 300))
        self.assertEqual([s["session_id"] for s in t["segments"]], [SID, "s2"])

    def test_workstream_and_since_filter_the_tasks(self):
        rows = [tturn("10:00:00", 100, 1.0), task("10:01:00", [OPENING], "a1"),
                tturn("2026-11-05T10:00:00-03:00", 100, 1.0, session="s2", workstream="studio"),
                task("2026-11-05T10:01:00-03:00", ["work/worklogs/2026-11-05-x.md"], "b2", session="s2",
                     workstream="studio")]
        self.assertEqual(set(by_worklog(self.measure(rows, workstream="studio"))), {"work/worklogs/2026-11-05-x.md"})
        self.assertEqual(set(by_worklog(self.measure(rows, since="2026-11-04"))), {"work/worklogs/2026-11-05-x.md"})

    def test_pending_lists_the_turns_after_the_last_mark_and_the_stale_sessions(self):
        rows = SESSION + [tturn("22:20:00", 60, 1.0, session="aaaaaaaa-1"), tturn("22:25:00", 60, 0.5, session="aaaaaaaa-1"),
                          tturn("2026-11-04T01:30:00-03:00", 60, 9.0, session="bbbbbbbb-2"),  # parada há 30 min
                          tturn("22:25:00", 60, 9.0, session="cccccccc-3", workstream="studio")]  # outro workstream
        text = self.summarize.pending_text(rows, SID, now=self.summarize.parse_time("2026-11-04T02:00:00-03:00"))
        lines = text.splitlines()
        self.assertEqual(lines[0], "este commit fecha 3 turnos desde 22:32 (corrido 10 min, US$ 0,65 dos turnos e "
                                   "US$ 4,05 de 2 subagentes):")
        self.assertEqual(lines[1:4], ["  22:32 > Grok CLI da xAI  esse",
                                      "  22:34 precisamos urgentemente adicionar o `grok` no fluxo do `brza sessions` e "
                                      "do `MCP",
                                      "  22:41 siga com o `PLN-012`"])
        self.assertEqual(lines[4], "sem tarefa no workstream tools-kit: 2 turnos em 1 sessão parada há mais de 2 h "
                                   "(US$ 1,50)")
        self.assertEqual(len(lines), 5)

    def test_pending_resolves_the_workstream_of_a_stale_session_from_any_of_its_rows(self):
        """O último turno da outra sessão sem `workstream` não a tira do aviso: vale o último workstream que ela teve."""
        rows = SESSION + [task("2026-11-04T00:10:00-03:00", [GROK], "o1", session="dddddddd-4"),
                          tturn("2026-11-04T00:20:00-03:00", 60, 2.0, session="dddddddd-4", workstream=None)]
        text = self.summarize.pending_text(rows, SID, now=self.summarize.parse_time("2026-11-04T04:00:00-03:00"))
        self.assertIn("sem tarefa no workstream tools-kit: 1 turno em 1 sessão parada há mais de 2 h (US$ 2,00)", text)

    def test_the_workstream_of_a_session_is_the_last_row_and_only_without_rows_the_last_task(self):
        rows = [tturn("10:00:00", 60, 1.0, session="s9", workstream="ws_antigo"),
                task("10:01:00", [OPENING], "n1", session="s9", workstream="ws_antigo"),
                tturn("10:30:00", 60, 1.0, session="s9", workstream="ws_novo")]
        segments = [seg for seg in self.summarize.task_segments(rows) if seg["session_id"] == "s9"]
        self.assertEqual(self.summarize.session_workstream(segments), "ws_novo")
        self.assertEqual(self.summarize.session_workstream(segments[:1]), "ws_antigo")

    def test_the_sessions_table_takes_the_workstream_from_the_task_when_the_rows_have_none(self):
        rows = [tturn("10:00:00", 60, 1.0, session="s8", workstream=None),
                task("10:01:00", [OPENING], "n2", session="s8", workstream="tools-kit")]
        result = self.measure(rows, workstream="tools-kit")
        self.assertEqual([(s["session_id"], s["workstream"]) for s in result["sessions"]], [("s8", "tools-kit")])

    def test_a_task_with_an_impossible_until_marks_at_its_timestamp(self):
        for until in ("25:00", "10:99"):
            row = task("10:01:00", [OPENING], "n3", until=until)
            self.assertEqual(self.summarize.task_mark(row), self.summarize.parse_time(at("10:01:00")))
            self.measure([tturn("10:00:00", 60, 1.0), row])

    def test_pending_with_nothing_open_says_so(self):
        rows = [tturn("10:00:00", 100, 1.0), task("10:01:00", [OPENING], "a1")]
        self.assertEqual(self.summarize.pending_text(rows, SID, now=self.summarize.parse_time(at("11:00:00"))),
                         "nada a fechar")

    def test_human_duration(self):
        human = self.summarize.human_duration
        self.assertEqual([human(s) for s in (42, 575, 3600, 4320)], ["42 s", "10 min", "1 h", "1 h 12 min"])

    def run_main(self, rows, *flags):
        with tempfile.TemporaryDirectory() as tmp:
            with open(os.path.join(tmp, "measurements.jsonl"), "w", encoding="utf-8") as f:
                f.write("\n".join(json.dumps(r) for r in rows) + "\n")
            out = io.StringIO()
            with mock.patch.object(sys, "argv", ["summarize", "--dir", tmp, *flags]), contextlib.redirect_stdout(out):
                code = self.summarize.main()
            return code, out.getvalue()

    def test_command_line_prints_the_three_tables_and_the_json(self):
        rows = SESSION + [task("23:10:00", [GROK], "z9", session=None)]
        code, text = self.run_main(rows, "--by-task")
        self.assertEqual(code, 0)
        for expected in ("Tarefas, por workstream e dia", "pln-012-abertura-da-produtividade-por-tarefa",
                         "instalacao-do-grok-build", "Tarefas não medidas", "sem sessão",
                         "Sessões: ociosa e sem tarefa", "tools-kit-produtividade", "0:14", "0:18"):
            self.assertIn(expected, text)
        self.assertNotIn("Subagentes, por agente", text)
        code, text = self.run_main(rows, "--by-task", "--json")
        data = json.loads(text)
        self.assertEqual((code, set(data)), (0, {"tasks", "unmeasured", "sessions"}))
        opening = next(t for t in data["tasks"] if t["worklog"] == OPENING)
        self.assertEqual([(s["session_id"], s["turns"]) for s in opening["segments"]], [(SID, 1), (SID, 0)])

    def test_command_line_pending_exits_zero(self):
        code, text = self.run_main(SESSION, "--by-task", "--pending", "--session", "nenhuma")
        self.assertEqual((code, text.strip()), (0, "nada a fechar"))
        with self.assertRaises(SystemExit), contextlib.redirect_stderr(io.StringIO()):
            self.run_main(SESSION, "--by-task", "--pending")  # sem --session
        with self.assertRaises(SystemExit), contextlib.redirect_stderr(io.StringIO()):
            self.run_main(SESSION, "--by-task", "--facts")

    def test_log_without_task_events_says_so(self):
        code, text = self.run_main([tturn("10:00:00", 100, 1.0)], "--by-task")
        self.assertEqual(code, 0)
        self.assertIn("nenhum evento task", text)

    def test_the_other_views_ignore_task_and_run(self):
        extra = [task("22:16:03", [OPENING], "539b744"), run("22:30:00", 10, 1.0, 100)]
        base = self.summarize.to_json(self.summarize.aggregate(ROWS))
        self.assertEqual(self.summarize.to_json(self.summarize.aggregate(ROWS + extra)), base)
        self.assertEqual(self.summarize.to_json_by_workstream(self.summarize.aggregate_by_workstream(ROWS + extra)),
                         self.summarize.to_json_by_workstream(self.summarize.aggregate_by_workstream(ROWS)))
        facts = [fact("2026-10-28", "reviewer", "f1", "claim", "high"), check("a1", "f1", "holds")]
        self.assertEqual(self.summarize.to_json_facts(self.summarize.aggregate_facts(ROWS + facts + extra)),
                         self.summarize.to_json_facts(self.summarize.aggregate_facts(ROWS + facts)))


class RootTest(unittest.TestCase):
    """Sem a BRZA_WORKSPACE e sem a MEASURE_DIR, a raiz sai do git, e não de `scripts/..`, que num worktree é ele mesmo."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        with open(SCRIPT, encoding="utf-8") as f:
            source = f.read()
        self.main, self.tree = repo_fixture.make(self.tmp.name, {"scripts/summarize_measurements.py": source,
                                                                 ".gitignore": ".measurements/\n"})
        self.summarize = load()

    def test_the_root_of_a_worktree_is_the_master_checkout(self):
        self.assertEqual(os.path.realpath(self.summarize.workspace_root(os.path.join(self.tree, "scripts"), {})),
                         self.main)

    def test_the_variables_win_over_the_git(self):
        scripts = os.path.join(self.tree, "scripts")
        self.assertEqual(self.summarize.workspace_root(scripts, {"BRZA_WORKSPACE": "/x/y"}), "/x/y")
        self.assertEqual(os.path.normpath(self.summarize.workspace_root(scripts, {"MEASURE_DIR": "/z"})), self.tree)

    def test_a_failing_git_falls_back_to_the_directory_above_scripts(self):
        outside = os.path.join(self.tmp.name, "fora", "scripts")
        os.makedirs(outside)
        self.assertEqual(os.path.normpath(self.summarize.workspace_root(outside, {})), os.path.dirname(outside))
        with mock.patch.object(self.summarize.subprocess, "run", side_effect=OSError("sem git")):
            self.assertEqual(os.path.normpath(self.summarize.workspace_root(self.tree + "/scripts", {})), self.tree)

    def test_the_script_run_from_the_worktree_reads_the_log_of_the_master(self):
        for directory, rows in ((self.main, [fact("2026-10-28", "designer", "F1", "lido", 90)]),
                                (self.tree, [turn("2026-10-28", "studio", 1.0)])):  # o do worktree é o velho
            os.makedirs(os.path.join(directory, ".measurements"))
            with open(os.path.join(directory, ".measurements", "measurements.jsonl"), "w", encoding="utf-8") as f:
                f.writelines(json.dumps(row) + "\n" for row in rows)
        done = subprocess.run([sys.executable, os.path.join(self.tree, "scripts", "summarize_measurements.py"),
                               "--facts", "--json"], cwd=self.tree, env=repo_fixture.clean_env(),
                              capture_output=True, text=True)
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertIn('"designer"', done.stdout)


if __name__ == "__main__":
    unittest.main()
