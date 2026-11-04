"""Testes do `scripts/summarize_measurements.py` (`--by-workstream` e `--facts`), sobre um log montado pelo teste em
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
    {"event": "subagent", "timestamp": "2026-10-28T12:00:00-03:00", "workstream": "studio", "agent_type": "fixfy:reviewer",
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
                fact("2026-10-28", "fixfy:collector", "F1", "lido", 99, agent_id="a9")]
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
                capture("fixfy:designer", 1, 2), capture("collector", 3)]
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
