"""Testes do fact_audit.py: o modo `stop` (a captura das marcas de confiança na devolução e nos arquivos do subagente, e o
disparo da auditoria) e o modo `run` (a sessão `claude -p` da auditoria, trocada por um espião).

Os transcripts são montados pelo próprio teste, em diretório temporário.

    python3 -m unittest discover -s .claude/hooks/tests
"""

import importlib.util
import io
import json
import os
import stat
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
HOOKS = os.path.normpath(os.path.join(HERE, "..", "..", "core", ".claude", "hooks"))


def load_fact_audit(directory):
    os.environ["MEASURE_DIR"] = directory
    spec = importlib.util.spec_from_file_location("fact_audit", os.path.join(HOOKS, "fact_audit.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.measure.settle = lambda path, **_: None  # o transcript do teste não cresce
    return module


def assistant(*blocks):
    return json.dumps({"type": "assistant", "timestamp": "2026-10-31T00:00:00Z", "requestId": "r",
                       "message": {"role": "assistant", "content": list(blocks)}})


def text(value):
    return {"type": "text", "text": value}


def write_tool(path, content):
    return {"type": "tool_use", "id": "t", "name": "Write", "input": {"file_path": path, "content": content}}


def handback(message):
    return {"type": "tool_use", "id": "h", "name": "SubagentHandback", "input": {"message": message}}


REAL_TRANSCRIPT = ("/Users/andepb/.claude/projects/-Users-andepb-Projects-brza-workspace--worktrees-workspace/"
                   "746b7a51-6fa4-47d9-95ef-35afb7fcc654/subagents/agent-a4e396c649819d3fd.jsonl")


def user(value):
    return json.dumps({"type": "user", "message": {"role": "user", "content": value}})


class Base(unittest.TestCase):
    mock_popen = True

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.addCleanup(os.environ.pop, "MEASURE_DIR", None)
        self.addCleanup(os.environ.pop, "BRZA_FACT_AUDIT", None)
        os.environ.pop("BRZA_FACT_AUDIT", None)
        self.fa = load_fact_audit(self.tmp.name)
        # O `stop` solta o `run` de verdade, e o `run` chama o `claude` de verdade: por padrão o Popen é um mock, e o
        # workstream_of, que chama o git pelo mesmo módulo, é um stub. Só quem testa o processo real o desliga.
        self.fa.measure.workstream_of = lambda cwd: "workspace"
        if self.mock_popen:
            self.popen_patcher = mock.patch.object(self.fa.subprocess, "Popen")
            self.popen = self.popen_patcher.start()
            self.addCleanup(self.popen_patcher.stop)
        self.transcript = os.path.join(self.tmp.name, "agent-a1.jsonl")
        self.payload = {"session_id": "s1", "cwd": self.tmp.name, "agent_id": "a1", "agent_type": "designer",
                        "agent_transcript_path": self.transcript}

    def append(self, *lines):
        with open(self.transcript, "a", encoding="utf-8") as f:
            f.write("".join(line + "\n" for line in lines))

    def run_hook(self, payload=None, mode="stop"):
        payload = self.payload if payload is None else payload
        with mock.patch.object(sys, "argv", ["fact_audit.py", mode]), \
                mock.patch.object(sys, "stdin", io.StringIO(json.dumps(payload))):
            with self.assertRaises(SystemExit) as raised:
                self.fa.main()
        self.assertEqual(raised.exception.code, 0)

    def logged(self):
        try:
            with open(self.fa.measure.LOG, encoding="utf-8") as f:
                return [json.loads(line) for line in f.read().splitlines()]
        except FileNotFoundError:
            return []

    def events(self, name):
        return [e for e in self.logged() if e["event"] == name]



class FactAuditTest(Base):
    def test_marks_in_the_reply_and_in_a_write_become_facts_with_their_source(self):
        self.append(
            user("desenhe"),
            assistant(text("vou gravar"), write_tool("/x/rascunho.md",
                      "# Rascunho\n- o timeout é 30 [F1 · lido · 90]\n- vale `x` [F2 · doc · 75]\n")),
            assistant(text("Resumo\n  - o preço é 0,10 [F3 · rodado · 100]  \n- talvez [C1 · inferido · 40] sirva")))
        self.run_hook()
        facts = self.events("fact")
        self.assertEqual([(f["fact_id"], f["kind"], f["confidence"], f["source"]) for f in facts],
                         [("F1", "lido", 90, "/x/rascunho.md"), ("F2", "doc", 75, "/x/rascunho.md"),
                          ("F3", "rodado", 100, "reply"), ("C1", "inferido", 40, "reply")])
        self.assertEqual(facts[0]["text"], "- o timeout é 30 [F1 · lido · 90]")
        self.assertEqual(facts[2]["text"], "- o preço é 0,10 [F3 · rodado · 100]")  # sem espaços nas pontas
        first = facts[0]
        self.assertEqual((first["session_id"], first["agent_id"], first["agent_type"]), ("s1", "a1", "designer"))
        self.assertIn("timestamp", first)
        self.assertIn("workstream", first)
        self.assertIsInstance(first["confidence"], int)
        captures = self.events("fact_capture")
        self.assertEqual(len(captures), 1)
        c = captures[0]
        self.assertEqual((c["facts"], c["malformed"], c["segment"], c["agent_id"], c["agent_type"]),
                         (4, 0, "first", "a1", "designer"))
        self.assertFalse(c.get("unmarked"))

    def test_subagent_handback_is_the_reply(self):
        self.append(user("x"), assistant(handback("## Devolução\n- o timeout é 30 [F1 · lido · 90]\n- custo [C1 · inferido · 40]")))
        self.run_hook()
        self.assertEqual([(f["fact_id"], f["source"]) for f in self.events("fact")], [("F1", "reply"), ("C1", "reply")])
        self.assertFalse(self.events("fact_capture")[0].get("unmarked"))

    def test_handback_wins_over_an_earlier_text_in_the_segment(self):
        self.append(user("x"), assistant(text("rascunho [F1 · lido · 90]")),
                    assistant(handback("final [F2 · doc · 80]")))
        self.run_hook()
        self.assertEqual([f["fact_id"] for f in self.events("fact")], ["F2"])

    def test_last_handback_wins_and_a_later_text_does_not_replace_it(self):
        self.append(user("x"), assistant(handback("a [F1 · lido · 90]")), assistant(handback("b [F2 · lido · 90]")),
                    assistant(text("depois [F3 · lido · 90]")))
        self.run_hook()
        self.assertEqual([f["fact_id"] for f in self.events("fact")], ["F2"])

    def test_text_is_the_fallback_without_handback(self):
        self.append(user("x"), assistant(text("a [F1 · lido · 90]")), assistant(text("b [F2 · lido · 90]")))
        self.run_hook()
        self.assertEqual([f["fact_id"] for f in self.events("fact")], ["F2"])

    def test_handback_without_marks_is_unmarked(self):
        self.append(user("x"), assistant(handback("sem marca")))
        self.run_hook()
        self.assertTrue(self.events("fact_capture")[0]["unmarked"])

    def test_real_transcript_before_p2_is_unmarked(self):
        if not os.path.exists(REAL_TRANSCRIPT):
            self.skipTest("transcript real ausente")
        self.run_hook({**self.payload, "agent_transcript_path": REAL_TRANSCRIPT})
        self.assertEqual(self.events("fact"), [])
        capture = self.events("fact_capture")[0]
        self.assertIs(capture["unmarked"], True)
        self.assertEqual(capture["segment"], "first")

    def test_text_of_a_fact_stops_at_300_characters(self):
        self.append(user("x"), assistant(text("a" * 400 + " [F1 · lido · 90]")))
        self.run_hook()
        self.assertEqual(len(self.events("fact")[0]["text"]), 300)

    def test_only_the_last_text_message_of_the_segment_is_read(self):
        self.append(user("x"), assistant(text("rascunho [F1 · lido · 90]")),
                    assistant({"type": "tool_use", "id": "t2", "name": "Bash", "input": {"command": "ls"}}),
                    assistant(text("final [F2 · lido · 80]")),
                    assistant({"type": "tool_use", "id": "t3", "name": "Bash", "input": {"command": "ls"}}))
        self.run_hook()
        self.assertEqual([f["fact_id"] for f in self.events("fact")], ["F2"])

    def test_repeated_id_between_write_and_reply_is_one_fact(self):
        self.append(user("x"), assistant(write_tool("/x/a.md", "[F1 · lido · 90] no arquivo")),
                    assistant(text("de novo [F1 · lido · 60] e [F2 · doc · 70]")))
        self.run_hook()
        facts = self.events("fact")
        self.assertEqual([(f["fact_id"], f["source"], f["confidence"]) for f in facts],
                         [("F1", "/x/a.md", 90), ("F2", "reply", 70)])  # a primeira ocorrência fica
        self.assertEqual(self.events("fact_capture")[0]["facts"], 2)

    def test_resume_records_only_new_ids_and_marks_the_segment(self):
        self.append(user("x"), assistant(text("[F1 · lido · 90]")))
        self.run_hook()
        self.append(user("continue"), assistant(text("[F1 · lido · 90] e [F2 · rodado · 95]")))
        self.run_hook()
        self.assertEqual([f["fact_id"] for f in self.events("fact")], ["F1", "F2"])
        captures = self.events("fact_capture")
        self.assertEqual([(c["segment"], c["facts"]) for c in captures], [("first", 1), ("resume", 1)])
        self.assertFalse(captures[1].get("unmarked"))  # a devolução tinha marcas, só que já vistas

    def test_empty_segment_writes_nothing_and_does_not_advance(self):
        self.append(user("x"), assistant(text("[F1 · lido · 90]")))
        self.run_hook()
        before = len(self.logged())
        state = self.read_state()
        self.run_hook()  # nada novo no transcript
        self.assertEqual(len(self.logged()), before)
        self.assertEqual(self.read_state(), state)

    def read_state(self):
        with open(os.path.join(self.fa.measure.DIR, "fact-audit-state.json"), encoding="utf-8") as f:
            return json.load(f)

    def test_state_keeps_the_offset_and_the_seen_ids_by_transcript(self):
        self.append(user("x"), assistant(text("[F1 · lido · 90] [C2 · doc · 50]")))
        self.run_hook()
        entry = self.read_state()[self.transcript]
        self.assertEqual(entry["offset"], os.path.getsize(self.transcript))
        self.assertEqual(sorted(entry["seen"]), ["C2", "F1"])

    def test_other_agent_types_write_nothing(self):
        self.append(user("x"), assistant(text("[F1 · lido · 90]")))
        for kind in ("implementer", "reviewer", None):
            self.run_hook({**self.payload, "agent_type": kind})
        self.assertEqual(self.logged(), [])
        self.assertFalse(os.path.exists(os.path.join(self.fa.measure.DIR, "fact-audit-state.json")))

    def test_agent_type_falls_back_to_the_meta_file(self):
        self.append(user("x"), assistant(text("[F1 · lido · 90]")))
        with open(self.transcript.replace(".jsonl", ".meta.json"), "w", encoding="utf-8") as f:
            json.dump({"agentType": "collector"}, f)
        self.run_hook({k: v for k, v in self.payload.items() if k != "agent_type"})
        facts = self.events("fact")
        self.assertEqual([(f["fact_id"], f["agent_type"]) for f in facts], [("F1", "collector")])

    def test_collector_is_audited(self):
        self.append(user("x"), assistant(text("[C1 · lido · 90]")))
        self.run_hook({**self.payload, "agent_type": "collector"})
        self.assertEqual(self.events("fact")[0]["agent_type"], "collector")

    def test_env_flag_of_the_audit_itself_writes_nothing(self):
        self.append(user("x"), assistant(text("[F1 · lido · 90]")))
        os.environ["BRZA_FACT_AUDIT"] = "1"
        self.run_hook()
        self.assertEqual(self.logged(), [])

    def test_malformed_marks_are_counted(self):
        self.append(user("x"), assistant(text(
            "a [F4 · lido] b [F5 · visto · 90] c [F6 · lido · 120] d [F7 · lido · 90]")))
        self.run_hook()
        self.assertEqual([f["fact_id"] for f in self.events("fact")], ["F7"])
        c = self.events("fact_capture")[0]
        self.assertEqual((c["facts"], c["malformed"]), (1, 3))

    def test_tolerant_marks_of_a_real_collector_are_valid_and_the_grouped_references_are_ignored(self):
        # as 10 marcas do collector de 2026-10-31 que a expressão antiga contava como malformadas
        self.append(user("x"), assistant(text("\n".join([
            "- a rolagem [F3 · lido · 80; a causa da rolagem é inferência sobre o layout]",
            "- o grep [F6 · rodado (grep) · 85]",
            "- herança [F9 · lido · 85; \"não herda\" é inferência sobre o pai]",
            "- outro grep [F10 · rodado (grep) · 90]",
            "- premissa [F20 · lido · 80; premissa: `innerText` de um nó oculto]",
            "- o \"some\" [F21 · lido · 78, o \"some\" é inferência sob o foco]",
            "- listagem [F26 · rodado (ls/head) · 90]",
            "- junto [F3, F25 · lido · 80-90]",
            "- faixa [F7-F9 · lido · 85-95]",
            "- faixa longa [F18\u2013F19 · lido · 90-95]"]))))
        self.run_hook()
        facts = self.events("fact")
        self.assertEqual([(f["fact_id"], f["kind"], f["confidence"]) for f in facts],
                         [("F3", "lido", 80), ("F6", "rodado", 85), ("F9", "lido", 85), ("F10", "rodado", 90),
                          ("F20", "lido", 80), ("F21", "lido", 78), ("F26", "rodado", 90)])
        self.assertEqual(facts[0]["text"], "- a rolagem [F3 · lido · 80; a causa da rolagem é inferência sobre o layout]")
        c = self.events("fact_capture")[0]
        self.assertEqual((c["facts"], c["malformed"]), (7, 0))
        self.assertNotIn("unmarked", c)

    def test_a_grouped_reference_alone_is_neither_a_fact_nor_malformed(self):
        self.append(user("x"), assistant(text("[F3, F25 · lido · 80-90] [F7-F9 · lido · 85-95] [C1 - C2 · doc · 70]")))
        self.run_hook()
        self.assertEqual(self.events("fact"), [])
        c = self.events("fact_capture")[0]
        self.assertEqual((c["facts"], c["malformed"]), (0, 0))

    def test_tolerance_does_not_leak_into_the_still_malformed_marks(self):
        self.append(user("x"), assistant(text(
            "[F4 · lido] [F5 · visto · 90] [F6 · lido · 120] [F7 · lido · 80-90] [F8 · lido · 90 ok] "
            "[F9 · rodado (grep · 90] [F11 · lido · 1000; x] [F12 · lido · ] [F13 · lido · 90]")))
        self.run_hook()
        self.assertEqual([f["fact_id"] for f in self.events("fact")], ["F13"])
        self.assertEqual(self.events("fact_capture")[0]["malformed"], 8)

    def test_confidence_bounds(self):
        self.append(user("x"), assistant(text("[F1 · lido · 0] [F2 · lido · 100] [F3 · lido · 101] [F4 · lido · 1000]")))
        self.run_hook()
        self.assertEqual([(f["fact_id"], f["confidence"]) for f in self.events("fact")], [("F1", 0), ("F2", 100)])
        self.assertEqual(self.events("fact_capture")[0]["malformed"], 2)

    def test_malformed_mark_in_a_write_is_counted_too(self):
        self.append(user("x"), assistant(write_tool("/x/a.md", "[F1 · lido]\n[F2 · lido · 90]")), assistant(text("ok")))
        self.run_hook()
        c = self.events("fact_capture")[0]
        self.assertEqual((c["facts"], c["malformed"]), (1, 1))

    def test_reply_without_marks_is_unmarked(self):
        self.append(user("x"), assistant(text("Nada marcado, e um [link](x) e [F] e [Fx]")))
        self.run_hook()
        self.assertEqual(self.events("fact"), [])
        c = self.events("fact_capture")[0]
        self.assertEqual((c["facts"], c["malformed"], c["unmarked"], c["segment"]), (0, 0, True, "first"))

    def test_forced_exception_becomes_hook_error_and_exits_zero(self):
        self.append(user("x"), assistant(text("[F1 · lido · 90]")))
        self.fa.measure.read_from = mock.Mock(side_effect=RuntimeError("boom"))
        self.run_hook()
        errors = self.events("hook_error")
        self.assertEqual(len(errors), 1)
        self.assertEqual(errors[0]["mode"], "fact_audit:stop")
        self.assertIn("boom", errors[0]["error"])
        self.assertEqual(self.events("fact"), [])

    def test_invalid_payload_and_unknown_mode_become_hook_error(self):
        with mock.patch.object(sys, "argv", ["fact_audit.py", "stop"]), mock.patch.object(sys, "stdin", io.StringIO("{")):
            with self.assertRaises(SystemExit) as raised:
                self.fa.main()
        self.assertEqual(raised.exception.code, 0)
        self.run_hook(mode="nada")
        self.assertEqual([e["mode"] for e in self.events("hook_error")], ["fact_audit:stop", "fact_audit:nada"])

    def test_missing_transcript_writes_nothing(self):
        self.run_hook()
        self.assertEqual(self.logged(), [])


def tool_result(tool_use_id, content):
    return json.dumps({"type": "user", "message": {"role": "user", "content": [
        {"type": "tool_result", "tool_use_id": tool_use_id, "content": content}]}})


def bash_tool(tool_use_id, command):
    return {"type": "tool_use", "id": tool_use_id, "name": "Bash", "input": {"command": command}}


def make_spy(directory, stdout, exit_code=0, sleep=0):
    """O `claude` trocado: grava os argumentos, o cwd, o ambiente e o arquivo da rodada, dorme e imprime o JSON fixo."""
    script = os.path.join(directory, "spy-claude")
    config = os.path.join(directory, "spy-config.json")
    with open(config, "w", encoding="utf-8") as f:
        json.dump({"stdout": stdout, "exit": exit_code, "sleep": sleep, "record": os.path.join(directory, "spy.json")}, f)
    with open(script, "w", encoding="utf-8") as f:
        f.write(f"""#!{sys.executable}
import json, os, re, sys, time
cfg = json.load(open({config!r}))
args = sys.argv[1:]
seen = None
for token in re.findall(r"\\S+\\.json", args[-1]):
    if os.path.isfile(token):
        seen = json.load(open(token))
json.dump({{"args": args, "cwd": os.getcwd(), "audit": os.environ.get("BRZA_FACT_AUDIT"), "file": seen}},
          open(cfg["record"], "w"))
time.sleep(cfg["sleep"])
sys.stdout.write(cfg["stdout"])
sys.exit(cfg["exit"])
""")
    os.chmod(script, os.stat(script).st_mode | stat.S_IXUSR)
    return script


LINES = "\n".join([
    json.dumps({"id": "F1", "verdict": "holds", "cite": "ok", "why": "a linha confere"}),
    json.dumps({"id": "F2", "verdict": "holds", "cite": "off", "why": "verdadeiro, mas a linha citada é outra"}),
    json.dumps({"id": "F3", "verdict": "fails", "cite": "ok", "why": "a saída gravada diz outra coisa"})])
RESULT_OK = {"type": "result", "subtype": "success", "is_error": False, "result": LINES, "total_cost_usd": 0.0056,
             "num_turns": 4, "duration_ms": 9600}


class StopDispatchTest(Base):
    def audit_files(self):
        directory = os.path.join(self.fa.measure.DIR, "fact-audit")
        return sorted(os.listdir(directory)) if os.path.isdir(directory) else []

    def read_audit(self, name):
        with open(os.path.join(self.fa.measure.DIR, "fact-audit", name), encoding="utf-8") as f:
            return json.load(f)

    def test_auditable_facts_write_the_file_and_start_the_process_without_waiting(self):
        self.append(user("x"), assistant(text(
            "a [F1 · lido · 90]\nb [F2 · doc · 75]\nc [F3 · rodado · 100]\nd [F4 · inferido · 40]\ne [C1 · lido · 60]")))
        with mock.patch.object(self.fa.subprocess, "Popen") as popen:
            self.run_hook()
        self.assertEqual(self.audit_files(), ["a1-0.json"])
        path = os.path.join(self.fa.measure.DIR, "fact-audit", "a1-0.json")
        popen.assert_called_once_with([sys.executable, self.fa.__file__, "run", path], start_new_session=True,
                                      stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.assertEqual(popen.return_value.method_calls, [])  # não espera o processo
        data = self.read_audit("a1-0.json")
        self.assertEqual((data["agent_id"], data["agent_type"], data["session_id"], data["cwd"]),
                         ("a1", "designer", "s1", self.tmp.name))
        self.assertIn("workstream", data)
        self.assertEqual([(f["id"], f["kind"], f["confidence"], f["source"]) for f in data["facts"]],
                         [("F1", "lido", 90, "reply"), ("F2", "doc", 75, "reply"), ("F3", "rodado", 100, "reply")])
        self.assertEqual(data["facts"][0]["text"], "a [F1 · lido · 90]")
        self.assertEqual(data["runs"], [])  # sem Bash no trecho

    def test_the_file_name_carries_the_offset_where_the_segment_starts(self):
        self.append(user("x"), assistant(text("[F1 · lido · 90]")))
        with mock.patch.object(self.fa.subprocess, "Popen"):
            self.run_hook()
            offset = os.path.getsize(self.transcript)
            self.append(user("y"), assistant(text("[F2 · lido · 80]")))
            self.run_hook()
        self.assertEqual(self.audit_files(), ["a1-0.json", f"a1-{offset}.json"])
        self.assertEqual([f["id"] for f in self.read_audit(f"a1-{offset}.json")["facts"]], ["F2"])

    def test_only_inferred_and_cost_marks_start_nothing(self):
        self.append(user("x"), assistant(text("a [F1 · inferido · 40] b [C1 · rodado · 90] c [C2 · lido · 70]")))
        with mock.patch.object(self.fa.subprocess, "Popen") as popen:
            self.run_hook()
        popen.assert_not_called()
        self.assertEqual(self.audit_files(), [])
        self.assertEqual(len(self.events("fact")), 3)  # a captura segue igual

    def test_no_mark_and_other_agent_types_start_nothing(self):
        self.append(user("x"), assistant(text("sem marca")))
        with mock.patch.object(self.fa.subprocess, "Popen") as popen:
            self.run_hook()
            self.run_hook({**self.payload, "agent_type": "reviewer"})
        popen.assert_not_called()
        self.assertEqual(self.audit_files(), [])

    def test_a_seen_fact_does_not_start_a_second_audit(self):
        self.append(user("x"), assistant(text("[F1 · lido · 90]")))
        with mock.patch.object(self.fa.subprocess, "Popen") as popen:
            self.run_hook()
            self.append(user("y"), assistant(text("[F1 · lido · 90]")))
            self.run_hook()
        self.assertEqual(popen.call_count, 1)

    def test_runs_carry_each_bash_with_the_result_matched_by_the_id(self):
        self.append(
            user("x"),
            assistant(text("rodando"), bash_tool("b1", "echo um"), bash_tool("b2", "echo dois")),
            tool_result("b2", [{"type": "text", "text": "dois\n"}]),
            tool_result("b1", "um\n"),
            assistant(bash_tool("b3", "yes"), {"type": "tool_use", "id": "r1", "name": "Read", "input": {}}),
            tool_result("b3", "z" * 5000),
            assistant(bash_tool("b4", "sleep 1")),  # sem resultado no trecho
            assistant(text("custo [F1 · rodado · 100]")))
        with mock.patch.object(self.fa.subprocess, "Popen"):
            self.run_hook()
        runs = self.read_audit("a1-0.json")["runs"]
        self.assertEqual([(r["command"], r["output"]) for r in runs[:2]], [("echo um", "um\n"), ("echo dois", "dois\n")])
        self.assertEqual((runs[2]["command"], len(runs[2]["output"])), ("yes", 4000))
        self.assertEqual(len(runs), 4)
        self.assertEqual(runs[3]["output"], "")

    def test_real_process_is_not_waited_for(self):
        self.popen_patcher.stop()
        spy = make_spy(self.tmp.name, stdout=json.dumps(RESULT_OK), sleep=2)
        self.append(user("x"), assistant(text("[F1 · lido · 90]")))
        with mock.patch.dict(os.environ, {"BRZA_FACT_AUDIT_CLAUDE": spy}):
            started = time.monotonic()
            self.run_hook()
            took = time.monotonic() - started
            deadline = time.monotonic() + 15
            while not self.events("fact_audit") and time.monotonic() < deadline:
                time.sleep(0.1)  # o processo solto termina sozinho, e só então o diretório do teste some
        self.assertLess(took, 1.0)
        self.assertEqual(len(self.events("fact_audit")), 1)


class RunTest(Base):
    mock_popen = False

    def setUp(self):
        super().setUp()
        self.directory = os.path.join(self.fa.measure.DIR, "fact-audit")
        os.makedirs(self.directory)
        self.cwd = os.path.join(self.tmp.name, "work")
        os.makedirs(self.cwd)
        self.path = os.path.join(self.directory, "a1-0.json")
        facts = [{"id": "F1", "kind": "lido", "confidence": 90, "text": "t1 [F1 · lido · 90]", "source": "reply"},
                 {"id": "F2", "kind": "doc", "confidence": 70, "text": "t2 [F2 · doc · 70]", "source": "reply"},
                 {"id": "F3", "kind": "rodado", "confidence": 100, "text": "t3 [F3 · rodado · 100]", "source": "reply"}]
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump({"agent_id": "a1", "agent_type": "designer", "session_id": "s1", "workstream": "workspace",
                       "cwd": self.cwd, "facts": facts, "runs": [{"command": "echo 1", "output": "1\n"}]}, f)

    def spy(self, stdout, **kwargs):
        script = make_spy(self.tmp.name, stdout, **kwargs)
        patcher = mock.patch.dict(os.environ, {"BRZA_FACT_AUDIT_CLAUDE": script})
        patcher.start()
        self.addCleanup(patcher.stop)

    def run_audit(self):
        with mock.patch.object(sys, "argv", ["fact_audit.py", "run", self.path]):
            with self.assertRaises(SystemExit) as raised:
                self.fa.main()
        self.assertEqual(raised.exception.code, 0)

    def recorded(self):
        with open(os.path.join(self.tmp.name, "spy.json"), encoding="utf-8") as f:
            return json.load(f)

    def test_success_writes_one_check_per_line_and_the_round_with_the_cost(self):
        self.spy(json.dumps(RESULT_OK))
        self.run_audit()
        checks = self.events("fact_check")
        self.assertEqual([(c["fact_id"], c["verdict"], c["cite"]) for c in checks],
                         [("F1", "holds", "ok"), ("F2", "holds", "off"), ("F3", "fails", "ok")])
        first = checks[0]
        self.assertEqual((first["agent_id"], first["agent_type"], first["session_id"], first["workstream"],
                          first["why"]), ("a1", "designer", "s1", "workspace", "a linha confere"))
        self.assertIn("timestamp", first)
        (audit,) = self.events("fact_audit")
        self.assertEqual((audit["agent_id"], audit["agent_type"], audit["workstream"], audit["facts"], audit["checked"],
                          audit["ignored"], audit["cost_usd"], audit["num_turns"], audit["subtype"], audit["error"]),
                         ("a1", "designer", "workspace", 3, 3, 0, 0.0056, 4, "success", None))
        self.assertIsInstance(audit["duration_s"], float)

    def test_the_spy_gets_the_command_the_cwd_and_the_env_and_the_file_is_deleted_after(self):
        self.spy(json.dumps(RESULT_OK))
        self.run_audit()
        seen = self.recorded()
        args = seen["args"]
        self.assertEqual(args[:1], ["-p"])
        self.assertEqual(args[args.index("--model") + 1], "haiku")
        self.assertEqual(args[args.index("--tools") + 1], "Read,Grep,Glob,WebFetch")
        self.assertEqual(args[args.index("--add-dir") + 1], self.directory)
        self.assertEqual(args[args.index("--output-format") + 1], "json")
        self.assertEqual(args[args.index("--max-budget-usd") + 1], "0.25")
        self.assertEqual(args[-1], self.fa.PROMPT.replace("{arquivo}", self.path))
        self.assertEqual(os.path.realpath(seen["cwd"]), os.path.realpath(self.cwd))
        self.assertEqual(seen["audit"], "1")
        self.assertEqual(seen["file"]["agent_id"], "a1")  # o arquivo existia quando o espião rodou
        self.assertFalse(os.path.exists(self.path))

    def test_budget_is_twelve_thousandths_per_fact_with_a_floor_of_25_cents(self):
        for count, expected in ((3, "0.25"), (20, "0.25"), (22, "0.26"), (24, "0.29"), (50, "0.60")):
            with self.subTest(facts=count):
                facts = [{"id": f"F{i}", "kind": "lido", "confidence": 90, "text": f"t{i}", "source": "reply"}
                         for i in range(1, count + 1)]
                with open(self.path, "w", encoding="utf-8") as f:
                    json.dump({"agent_id": "a1", "agent_type": "designer", "session_id": "s1", "workstream": "workspace",
                               "cwd": self.cwd, "facts": facts, "runs": []}, f)
                self.spy(json.dumps(RESULT_OK))
                self.run_audit()
                args = self.recorded()["args"]
                self.assertEqual(args[args.index("--max-budget-usd") + 1], expected)
        self.assertEqual((self.fa.BUDGET_PER_FACT, self.fa.BUDGET_MIN), (0.012, 0.25))

    def test_prompt_states_the_criteria(self):
        for word in ("holds", "fails", "unknown", "cite", "off", "rodado", "lido", "doc", "runs", "{arquivo}"):
            self.assertIn(word, self.fa.PROMPT)

    def test_invalid_lines_are_ignored_and_counted(self):
        lines = "\n".join([
            json.dumps({"id": "F1", "verdict": "holds", "cite": "ok", "why": "x" * 400}),
            "```json",
            "isto não é JSON",
            json.dumps({"id": "F9", "verdict": "holds", "cite": "ok", "why": "fora do arquivo"}),
            json.dumps({"id": "F2", "verdict": "talvez", "cite": "ok", "why": "vocabulário"}),
            json.dumps({"id": "F2", "verdict": "holds", "cite": "quase", "why": "vocabulário"}),
            json.dumps(["F3"]),
            json.dumps({"id": "F3", "verdict": "unknown", "cite": "ok", "why": "sem evidência"}),
            "```"])
        self.spy(json.dumps({**RESULT_OK, "result": lines}))
        self.run_audit()
        self.assertEqual([c["fact_id"] for c in self.events("fact_check")], ["F1", "F3"])
        self.assertEqual(len(self.events("fact_check")[0]["why"]), 300)
        (audit,) = self.events("fact_audit")
        self.assertEqual((audit["facts"], audit["checked"], audit["ignored"], audit["error"]), (3, 2, 5, None))

    def test_budget_error_with_exit_one_still_writes_the_round_with_the_cost(self):
        out = {"type": "result", "subtype": "error_max_budget_usd", "is_error": True, "result": None,
               "total_cost_usd": 0.2612, "num_turns": 9, "duration_ms": 50000}
        self.spy(json.dumps(out), exit_code=1)
        self.run_audit()
        self.assertEqual(self.events("fact_check"), [])
        (audit,) = self.events("fact_audit")
        self.assertEqual((audit["error"], audit["subtype"], audit["cost_usd"], audit["checked"], audit["facts"]),
                         ("error_max_budget_usd", "error_max_budget_usd", 0.2612, 0, 3))
        self.assertFalse(os.path.exists(self.path))

    def test_exit_nonzero_with_a_result_still_records_the_checks_and_the_error(self):
        self.spy(json.dumps({**RESULT_OK, "subtype": "error_max_turns", "is_error": True}), exit_code=1)
        self.run_audit()
        self.assertEqual(len(self.events("fact_check")), 3)
        self.assertEqual(self.events("fact_audit")[0]["error"], "error_max_turns")

    def test_invalid_json_output_is_an_error(self):
        self.spy("não é json")
        self.run_audit()
        (audit,) = self.events("fact_audit")
        self.assertEqual((audit["error"], audit["checked"], audit["cost_usd"], audit["facts"]), ("invalid_json", 0, None, 3))
        self.assertFalse(os.path.exists(self.path))

    def test_missing_binary_is_an_error_and_exits_zero(self):
        with mock.patch.dict(os.environ, {"BRZA_FACT_AUDIT_CLAUDE": os.path.join(self.tmp.name, "nao-existe")}):
            self.run_audit()
        (audit,) = self.events("fact_audit")
        self.assertEqual((audit["error"], audit["checked"], audit["facts"]), ("binary_not_found", 0, 3))
        self.assertFalse(os.path.exists(self.path))

    def test_no_binary_on_the_path_is_an_error(self):
        with mock.patch.dict(os.environ, {"PATH": self.tmp.name}):
            os.environ.pop("BRZA_FACT_AUDIT_CLAUDE", None)
            self.run_audit()
        self.assertEqual(self.events("fact_audit")[0]["error"], "binary_not_found")

    def test_timeout_is_an_error_and_exits_zero(self):
        self.spy(json.dumps(RESULT_OK), sleep=30)
        self.fa.RUN_TIMEOUT = 1
        started = time.monotonic()
        self.run_audit()
        self.assertLess(time.monotonic() - started, 10)
        (audit,) = self.events("fact_audit")
        self.assertEqual((audit["error"], audit["checked"], audit["facts"]), ("timeout", 0, 3))
        self.assertFalse(os.path.exists(self.path))

    def test_unreadable_file_is_an_error_and_never_raises(self):
        os.remove(self.path)
        self.run_audit()
        (audit,) = self.events("fact_audit")
        self.assertIsNotNone(audit["error"])

    def test_exception_in_the_write_of_a_check_becomes_the_error_of_the_round(self):
        self.spy(json.dumps(RESULT_OK))
        real = self.fa.measure.write

        def flaky(line):
            if line["event"] == "fact_check":
                raise OSError("disco cheio")
            real(line)
        self.fa.measure.write = flaky
        self.run_audit()
        (audit,) = self.events("fact_audit")
        self.assertIn("disco cheio", audit["error"])
        self.assertFalse(os.path.exists(self.path))


class SettingsTest(unittest.TestCase):
    @unittest.skip("P3: lê o settings.json do workspace, que não vem; o settings.hooks.json nasce no P3")
    def test_subagent_stop_has_measure_then_fact_audit(self):
        path = os.path.join(os.path.dirname(os.path.dirname(HERE)), "settings.json")
        with open(path, encoding="utf-8") as f:
            settings = json.load(f)
        (group,) = settings["hooks"]["SubagentStop"]
        commands = [(h["command"], h["timeout"]) for h in group["hooks"]]
        self.assertEqual(len(commands), 2)
        self.assertIn('measure.py" subagent || true', commands[0][0])
        self.assertEqual(commands[1], ('${BRZA_PYTHON:-python3} "${BRZA_WORKSPACE:-$CLAUDE_PROJECT_DIR}'
                                       '/.claude/hooks/fact_audit.py" stop || true', 10))


if __name__ == "__main__":
    unittest.main()
