"""Testes do measure.py sobre o transcript do Claude Code e a tabela de preço.

Os transcripts são montados pelo próprio teste, em diretório temporário.

    python3 -m unittest discover -s .claude/hooks/tests
"""

import importlib.util
import io
import json
import os
import sys
import tempfile
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
HOOKS = os.path.normpath(os.path.join(HERE, "..", "..", "core", ".claude", "hooks"))


def load_measure(directory):
    os.environ["MEASURE_DIR"] = directory
    spec = importlib.util.spec_from_file_location("measure", os.path.join(HOOKS, "measure.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.settle = lambda path, **_: None  # o transcript do teste não cresce
    return module


def lines_of(path):
    with open(path, encoding="utf-8") as f:
        return f.read().splitlines()


class HookTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        # O registro da máquina de teste: um arquivo que não existe, e nunca o ~/.config real.
        os.environ["BRZA_REGISTRY"] = os.path.join(self.tmp.name, "config", "workspaces.toml")
        os.environ.pop("BRZA_WORKSPACE", None)
        self.measure = load_measure(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()
        os.environ.pop("MEASURE_DIR", None)
        os.environ.pop("BRZA_REGISTRY", None)

    def logged(self):
        return [json.loads(line) for line in lines_of(self.measure.LOG)]

    def run_hook(self, payload, mode="turn"):
        with mock.patch.object(sys, "argv", ["measure.py", mode]), \
                mock.patch.object(sys, "stdin", io.StringIO(json.dumps(payload))):
            with self.assertRaises(SystemExit):
                self.measure.main()


class DocstringTest(HookTest):
    def test_the_docstring_names_the_guard_that_writes_the_guard_line_and_not_the_one_that_left(self):  # A8
        doc = self.measure.__doc__
        self.assertIn("`compact_guard.py` grava no mesmo log a linha `guard`", doc)
        self.assertNotIn("resume_guard", doc)
        self.assertTrue(os.path.isfile(os.path.join(HOOKS, "compact_guard.py")))
        self.assertFalse(os.path.exists(os.path.join(HOOKS, "resume_guard.py")))


class PriceTest(HookTest):
    def test_claude_price_by_id_and_not_by_family(self):
        self.assertEqual(self.measure.price_of("claude-haiku-4-5-20251001")["input"], 1.0)
        self.assertEqual(self.measure.price_of("claude-opus-5-5-high")["input"], 4.0)  # com o sufixo de esforço
        self.assertEqual(self.measure.price_of("claude-haiku-5-5")["input"], 0.10)  # não herda o preço da 4.5
        self.assertIsNone(self.measure.price_of("claude-haiku-6"))  # versão nova não herda o preço da 5.5
        self.assertIsNone(self.measure.price_of("claude-opus-6"))
        self.assertIsNone(self.measure.price_of("haiku"))  # o alias pedido não é o modelo que rodou

    def test_haiku_5_5_charges_each_request_by_prompt_size(self):
        price = self.measure.price_of("claude-haiku-5-5")
        short = {"input": 0, "cache_read": 99_000, "cache_write": 1_000, "cache_write_5m": 1_000, "output": 1_000_000}
        long = dict(short, cache_read=99_001)  # um token a mais passa dos 100 mil, e o pedido inteiro muda de tabela
        self.assertAlmostEqual(self.measure.request_cost(price, short), (99_000 * 0.01 + 1_000 * 0.125 + 1e6 * 0.50) / 1e6)
        self.assertAlmostEqual(self.measure.request_cost(price, long), (99_001 * 0.05 + 1_000 * 0.625 + 1e6 * 2.50) / 1e6)

    def test_turn_that_switches_model_prices_each_request_by_its_own(self):
        def reply(request, model):
            return json.dumps({"type": "assistant", "timestamp": "2026-10-21T00:00:00Z", "requestId": request,
                               "message": {"model": model, "content": [],
                                           "usage": {"input_tokens": 1_000_000, "output_tokens": 0}}})
        lines = [json.dumps({"type": "user", "timestamp": "2026-10-21T00:00:00Z", "message": {"content": "oi"}}),
                 reply("r1", "claude-fable-5-1"), reply("r2", "claude-fable-5-1"), reply("r3", "claude-opus-5-5")]
        measured, _ = self.measure.measure(lines)
        self.assertEqual(measured["model"], "claude-opus-5-5")  # o último, como antes
        self.assertEqual(measured["estimated_cost_usd"], 10.0 + 10.0 + 4.0)  # e não 3 × 4,0 pelo preço do Opus
        self.assertEqual(measured["models"], {"claude-fable-5-1": {"api_calls": 2, "estimated_cost_usd": 20.0},
                                              "claude-opus-5-5": {"api_calls": 1, "estimated_cost_usd": 4.0}})

    def test_turn_with_one_unpriced_request_has_no_cost(self):
        lines = [json.dumps({"type": "assistant", "requestId": r, "message": {
            "model": m, "content": [], "usage": {"input_tokens": 1000}}})
            for r, m in (("r1", "claude-opus-5-5"), ("r2", "claude-opus-6"))]
        measured, _ = self.measure.measure(lines)
        self.assertIsNone(measured["estimated_cost_usd"])
        self.assertIsNone(measured["models"]["claude-opus-6"]["estimated_cost_usd"])

    def test_single_model_turn_does_not_write_the_split(self):
        lines = [json.dumps({"type": "assistant", "requestId": "r1", "message": {
            "model": "claude-opus-5-5", "content": [], "usage": {"input_tokens": 1000}}})]
        measured, _ = self.measure.measure(lines)
        self.assertNotIn("models", measured)


class ClaudeTranscriptTest(HookTest):
    def transcript(self):
        """Dois pedidos: o primeiro chega em dois pedaços, e o segundo lê o contexto maior."""
        def answer(request, read, write, output):
            return {"type": "assistant", "requestId": request, "timestamp": "2026-10-20T10:00:00Z",
                    "message": {"model": "claude-opus-5-5", "content": [],
                                "usage": {"input_tokens": 10, "cache_read_input_tokens": read,
                                          "cache_creation_input_tokens": write, "output_tokens": output}}}
        entries = [{"type": "user", "timestamp": "2026-10-20T09:59:58Z", "message": {"content": "oi"}},
                   answer("r1", 1000, 200, 1), answer("r1", 1000, 200, 50),
                   answer("r2", 250_000, 300, 80)]
        path = os.path.join(self.tmp.name, "session.jsonl")
        with open(path, "w", encoding="utf-8") as stream:
            stream.write("\n".join(json.dumps(entry) for entry in entries) + "\n")
        return path

    def test_max_context_is_the_fullest_call(self):
        measured, _ = self.measure.measure(lines_of(self.transcript()))
        self.assertEqual(measured["api_calls"], 2)
        self.assertEqual(measured["max_context"], 10 + 250_000 + 300)

    def test_turn_writes_the_account_by_the_config_directory(self):
        path = self.transcript()
        cfg = os.path.join(self.tmp.name, ".claude-adm")
        os.makedirs(os.path.join(cfg, "plugins"), exist_ok=True)
        with open(os.path.join(cfg, "plugins", "installed_plugins.json"), "w", encoding="utf-8") as f:
            json.dump({"plugins": {"outro@x": [{"scope": "user", "version": "1.2.3"}]}}, f)
        with mock.patch.dict(os.environ, {"CLAUDE_CONFIG_DIR": cfg + "/"}):
            self.run_hook({"hook_event_name": "Stop", "session_id": "s", "transcript_path": path}, mode="turn")
            self.assertEqual(self.measure.plugin_version("outro:git-runner", self.tmp.name), "1.2.3")
        with mock.patch.dict(os.environ, {"CLAUDE_CONFIG_DIR": "   "}, clear=False):
            self.assertEqual(self.measure.account(), ".claude")
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("CLAUDE_CONFIG_DIR", None)
            self.assertEqual(self.measure.account(), ".claude")
        (row,) = self.logged()
        self.assertEqual((row["event"], row["account"], row["max_context"]), ("turn", ".claude-adm", 250_310))

    def test_turn_of_the_audit_session_carries_the_flag_and_the_others_do_not(self):
        path = self.transcript()
        payload = {"hook_event_name": "Stop", "session_id": "s", "transcript_path": path}
        with mock.patch.dict(os.environ, {"BRZA_FACT_AUDIT": "1"}):
            self.run_hook(payload, mode="turn")
        (row,) = self.logged()
        self.assertIs(row["fact_audit"], True)
        os.remove(self.measure.LOG)
        os.remove(self.measure.STATE)
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("BRZA_FACT_AUDIT", None)
            self.run_hook(payload, mode="turn")
        (row,) = self.logged()
        self.assertNotIn("fact_audit", row)

    def test_lines_carry_the_claude_ai_link_that_clear_keeps(self):
        path = self.transcript()
        link = {"CLAUDE_CODE_BRIDGE_SESSION_ID": "session_01Y9ppkrW9R8FobmitY6B4Kr"}
        with mock.patch.dict(os.environ, link):
            self.run_hook({"hook_event_name": "Stop", "session_id": "s", "transcript_path": path}, mode="turn")
            self.run_hook({"session_id": "s", "tool_input": {"skill": "outro:git-runner"}}, mode="skill")
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("CLAUDE_CODE_BRIDGE_SESSION_ID", None)
            self.run_hook({"session_id": "s", "tool_name": "mcp__firebase__firebase_get_project"}, mode="mcp")
        turn, skill, mcp = self.logged()
        self.assertEqual(turn["bridge_session_id"], "session_01Y9ppkrW9R8FobmitY6B4Kr")
        self.assertEqual(skill["bridge_session_id"], "session_01Y9ppkrW9R8FobmitY6B4Kr")
        self.assertIsNone(mcp["bridge_session_id"])  # sessão sem controle remoto


def at(seconds):
    """O horário do transcript, `seconds` depois de 2026-11-03T21:59:33Z."""
    from datetime import datetime, timedelta, timezone
    base = datetime(2026, 11, 3, 21, 59, 33, tzinfo=timezone.utc)
    return (base + timedelta(seconds=seconds)).isoformat().replace("+00:00", "Z")


def asks(when, ident, request="r1"):
    return {"type": "assistant", "timestamp": at(when), "requestId": request,
            "message": {"model": "claude-opus-5-5", "usage": {"input_tokens": 10},
                        "content": [{"type": "tool_use", "id": ident, "name": "AskUserQuestion",
                                     "input": {"questions": [{"question": "Qual?", "options": [
                                         {"label": "A (Recomendado)"}, {"label": "B"}]}]}}]}}


def answers(when, ident):
    return {"type": "user", "timestamp": at(when), "toolUseResult": {"answers": {"Qual?": "A (Recomendado)"}},
            "message": {"content": [{"type": "tool_result", "tool_use_id": ident, "content": "ok"}]}}


def says(when, request):
    return {"type": "assistant", "timestamp": at(when), "requestId": request,
            "message": {"model": "claude-opus-5-5", "usage": {"input_tokens": 10}, "content": []}}


def prompt(when, text, **extra):
    return {"type": "user", "timestamp": at(when), "message": {"content": text}, **extra}


def side(when, kind="queue-operation"):
    """Uma linha que o Claude Code escreve junto do pedido — a fila, o hook —, e que não é a conversa."""
    if kind == "queue-operation":
        return {"type": kind, "operation": "enqueue", "timestamp": at(when)}
    return {"type": "attachment", "timestamp": at(when), "attachment": {"type": "hook_success"}}


class WaitsAndHeadTest(HookTest):
    """D2: a espera de Anderson dentro do turno e o começo do pedido."""

    LONG = "siga com o `PLN-012`\n" + "x" * 100

    def segment(self):
        return [side(-1), prompt(0, self.LONG), asks(5, "q1"), answers(464, "q1"), says(470, "r2"),
                prompt(500, "<local-command-caveat>Caveat</local-command-caveat>", isMeta=True),
                side(619), side(619.5, "attachment"),
                prompt(620, "e agora o segundo pedido"), says(630, "r3")]

    def test_the_round_and_the_interrupted_turn_are_waits_and_the_meta_prompt_is_not(self):
        measured, seen = self.measure.measure([json.dumps(e) for e in self.segment()])
        self.assertEqual(seen["waits_s"], [459, 150])  # a fila, o hook e o caveat isMeta não encurtam a espera: de 470 a 620
        self.assertEqual(measured["duration_s"], 630)
        self.assertNotIn("waits_s", measured)  # o subagente usa só o primeiro dicionário, e não muda

    def test_the_head_is_the_first_prompt_cut_at_80_without_line_breaks(self):
        _, seen = self.measure.measure([json.dumps(e) for e in self.segment()])
        self.assertEqual(seen["prompt_head"], ("siga com o `PLN-012` " + "x" * 100)[:80])
        self.assertEqual(len(seen["prompt_head"]), 80)
        self.assertNotIn("\n", seen["prompt_head"])

    def test_the_head_skips_a_local_command_and_the_command_typed_alone_is_the_head(self):
        rename = prompt(0, "<command-name>/rename</command-name>\n  <command-message>rename</command-message>\n"
                           "  <command-args>pln012-p1</command-args>")
        stdout = prompt(0.05, "<local-command-stdout>Session renamed to: pln012-p1</local-command-stdout>")
        lines = [rename, stdout, prompt(0.1, "Execute o P1"), says(9, "r1")]
        _, seen = self.measure.measure([json.dumps(e) for e in lines])
        self.assertEqual(seen["prompt_head"], "Execute o P1")
        self.assertEqual(seen["waits_s"], [])  # o pedido colado no comando não espera
        _, seen = self.measure.measure([json.dumps(e) for e in (rename, stdout, says(1, "r1"))])
        self.assertEqual(seen["prompt_head"], "/rename pln012-p1")

    def test_interruption_marker_and_task_notification_are_not_prompts(self):
        lines = [prompt(0, "primeiro"), says(10, "r1"),
                 prompt(40, [{"type": "text", "text": "[Request interrupted by user]"}]),
                 prompt(100, "<task-notification>\n<task-id>x</task-id></task-notification>",
                        origin={"kind": "task-notification"}),
                 says(110, "r2"), prompt(300, "segundo", origin={"kind": "human"}), says(310, "r3")]
        _, seen = self.measure.measure([json.dumps(e) for e in lines])
        self.assertEqual(seen["waits_s"], [190])  # do fim do agente (110) ao pedido de Anderson (300)
        self.assertEqual(seen["prompt_head"], "primeiro")

    def test_a_slash_skill_with_the_message_before_the_name_is_a_command(self):
        skill = prompt(0, "<command-message>plan-runner</command-message>\n<command-name>/plan-runner</command-name>\n"
                          "<command-args>abra o plano</command-args>")
        lines = [skill, prompt(1, "abra o PLN-012"), says(9, "r1")]
        _, seen = self.measure.measure([json.dumps(e) for e in lines])
        self.assertEqual(seen["prompt_head"], "abra o PLN-012")
        _, seen = self.measure.measure([json.dumps(e) for e in (skill, says(9, "r1"))])
        self.assertEqual(seen["prompt_head"], "/plan-runner abra o plano")

    def test_a_command_tag_quoted_in_a_normal_prompt_is_not_a_command(self):
        quoted = "Como a tag <command-name>/help</command-name> é gravada?"
        _, seen = self.measure.measure([json.dumps(e) for e in (prompt(0, quoted), says(9, "r1"))])
        self.assertEqual(seen["prompt_head"], quoted[:80])

    def test_the_compaction_summary_is_not_a_prompt(self):
        summary = prompt(0, "This session is being continued from a previous conversation that ran out of context.",
                         isCompactSummary=True, isVisibleInTranscriptOnly=True)
        caveat = prompt(1, "<local-command-caveat>Caveat</local-command-caveat>", isMeta=True)
        compact = prompt(1, "<command-name>/compact</command-name>\n<command-args></command-args>")
        stdout = prompt(2, "<local-command-stdout>Compacted</local-command-stdout>")
        lines = [summary, caveat, compact, stdout, prompt(3, "siga com o P2"), says(9, "r1")]
        _, seen = self.measure.measure([json.dumps(e) for e in lines])
        self.assertEqual(seen["prompt_head"], "siga com o P2")

    def test_bash_mode_output_is_not_a_prompt_and_the_input_shows_as_a_bang(self):
        lines = [prompt(0, "<bash-input>brza-app install</bash-input>"), prompt(1, "<bash-stdout>ok</bash-stdout>"),
                 prompt(2, "<bash-stderr>aviso</bash-stderr>"), says(9, "r1")]
        _, seen = self.measure.measure([json.dumps(e) for e in lines])
        self.assertEqual((seen["prompt_head"], seen["waits_s"]), ("! brza-app install", []))

    def test_a_wait_never_starts_before_the_segment_clock(self):
        # a forma real: o caveat abre o trecho em 1000, o `<bash-input>` vem com data de 120 s antes, e o pedido seguinte
        lines = [prompt(880, [{"type": "tool_result", "tool_use_id": "t0", "content": "ok"}]),
                 prompt(1000, "<local-command-caveat>Caveat</local-command-caveat>", isMeta=True),
                 prompt(880.5, "<bash-input>ls</bash-input>"), prompt(880.6, "<bash-stdout>x</bash-stdout>"),
                 prompt(1010, "agora outro pedido"), says(1033, "r1")]
        measured, seen = self.measure.measure([json.dumps(e) for e in lines])
        self.assertLessEqual(sum(seen["waits_s"]), measured["duration_s"])
        self.assertEqual(seen["waits_s"], [10])
        self.assertEqual(seen["prompt_head"], "agora outro pedido")  # o `! ls` só seria o head sozinho

    def test_the_bash_input_is_the_head_only_when_there_is_no_written_prompt(self):
        git = prompt(0, "<bash-input>git status</bash-input>")
        _, seen = self.measure.measure([json.dumps(e) for e in (git, prompt(1, "agora o P2"), says(9, "r1"))])
        self.assertEqual(seen["prompt_head"], "agora o P2")
        _, seen = self.measure.measure([json.dumps(e) for e in (git, says(9, "r1"))])
        self.assertEqual(seen["prompt_head"], "! git status")

    def test_the_meta_caveat_is_not_the_previous_line_of_the_conversation(self):
        lines = [prompt(0, "pedido"), says(10, "r1"), prompt(40, "[Request interrupted by user]"),
                 prompt(1000.1, "<local-command-caveat>Caveat</local-command-caveat>", isMeta=True),
                 prompt(1000, "<command-name>/model</command-name>\n<command-args>opus</command-args>"),
                 prompt(1200, "outro pedido"), says(1210, "r2")]
        _, seen = self.measure.measure([json.dumps(e) for e in lines])
        # o /model espera 960 desde o aviso de interrupção (40), e o pedido seguinte 200 desde o /model (1000)
        self.assertEqual(seen["waits_s"], [960, 200])

    def test_a_peer_message_is_not_a_prompt(self):
        lines = [prompt(0, "primeiro"), says(10, "r1"), prompt(50, "mensagem de outra sessão", origin={"kind": "peer"}),
                 prompt(80, "segundo"), says(90, "r2")]
        _, seen = self.measure.measure([json.dumps(e) for e in lines])
        self.assertEqual(seen["waits_s"], [30])  # só o pedido de Anderson espera, e a mensagem é fala, não pedido
        lines = [prompt(0, "mensagem de outra sessão", origin={"kind": "peer"}), prompt(1, "pedido real"), says(9, "r1")]
        _, seen = self.measure.measure([json.dumps(e) for e in lines])
        self.assertEqual((seen["prompt_head"], seen["waits_s"]), ("pedido real", []))

    def test_a_system_reminder_is_not_a_prompt(self):
        reminder = '<system-reminder>\nThe user named this session "tools-kit".</system-reminder>'
        lines = [prompt(0, "primeiro"), says(10, "r1"), prompt(50, reminder), prompt(80, "segundo"), says(90, "r2")]
        _, seen = self.measure.measure([json.dumps(e) for e in lines])
        self.assertEqual(seen["waits_s"], [30])  # o lembrete é linha `user`: a espera conta do 50, não do fim do agente
        lines = [prompt(0, reminder), prompt(1, "pedido real"), says(9, "r1")]
        _, seen = self.measure.measure([json.dumps(e) for e in lines])
        self.assertEqual(seen["prompt_head"], "pedido real")
        self.assertEqual(seen["waits_s"], [])

    def test_a_refused_question_still_waited(self):
        refused = answers(64, "q1")
        refused["message"]["content"][0]["is_error"] = True
        lines = [prompt(0, "pergunta"), asks(4, "q1"), refused, says(70, "r2")]
        _, seen = self.measure.measure([json.dumps(e) for e in lines])
        self.assertEqual(seen["waits_s"], [60])

    def test_segment_without_a_prompt_has_no_head(self):
        _, seen = self.measure.measure([json.dumps(says(0, "r1"))])
        self.assertEqual((seen["waits_s"], seen["prompt_head"]), ([], None))


class FakeRusage:
    """O `proc_pid_rusage` falso: devolve as leituras em ordem, ou levanta."""

    def __init__(self, *readings):
        self.readings = list(readings)
        self.pids = []

    def __call__(self, pid):
        self.pids.append(pid)
        reading = self.readings.pop(0)
        if isinstance(reading, Exception):
            raise reading
        return reading


def reading(cpu_self, cpu_children, rss_mb=357, footprint_mb=209, start=1000):
    return {"cpu_self_s": cpu_self, "cpu_children_s": cpu_children, "rss_bytes": rss_mb * 2 ** 20,
            "footprint_bytes": footprint_mb * 2 ** 20, "start": start}


class MachineTest(HookTest):
    """D2: a CPU e o RSS do `claude` no turno, pelo `proc_pid_rusage`, com o delta guardado no `state.json`."""

    def setUp(self):
        super().setUp()
        self.path = os.path.join(self.tmp.name, "session.jsonl")
        self.env = mock.patch.dict(os.environ, {"CLAUDE_PID": "44452"})
        self.env.start()
        self.addCleanup(self.env.stop)

    def add_turn(self, n):
        with open(self.path, "a", encoding="utf-8") as f:
            for entry in (prompt(100 * n, f"pedido {n}"), says(100 * n + 10, f"r{n}")):
                f.write(json.dumps(entry) + "\n")

    def stop(self):
        self.run_hook({"hook_event_name": "Stop", "session_id": "s", "transcript_path": self.path}, mode="turn")

    def state(self):
        with open(self.measure.STATE, encoding="utf-8") as f:
            return json.load(f)

    def test_two_turns_of_the_same_pid_give_the_delta(self):
        fake = FakeRusage(reading(0.84, 6.13), reading(1.5, 8.5, rss_mb=400, footprint_mb=250))
        with mock.patch.object(self.measure, "proc_pid_rusage", fake):
            self.add_turn(1)
            self.stop()
            self.add_turn(2)
            self.stop()
        first, second = self.logged()
        self.assertEqual(first["machine"], {"pid": 44452, "from": "env", "cpu_self_s": 0.84, "cpu_children_s": 6.13,
                                            "rss_mb": 357, "footprint_mb": 209})  # o primeiro mede desde o começo
        self.assertEqual(second["machine"], {"pid": 44452, "from": "env", "cpu_self_s": 0.66, "cpu_children_s": 2.37,
                                             "rss_mb": 400, "footprint_mb": 250})
        self.assertEqual(fake.pids, [44452, 44452])
        self.assertEqual(self.state()["machine:44452"], {"start": 1000, "cpu_self_s": 1.5, "cpu_children_s": 8.5})  # float exato
        self.assertEqual((first["waits_s"], first["prompt_head"]), ([], "pedido 1"))

    def test_a_new_process_with_the_same_pid_measures_from_its_start(self):
        fake = FakeRusage(reading(5.0, 9.0), reading(0.5, 0.25, start=2000))
        with mock.patch.object(self.measure, "proc_pid_rusage", fake):
            self.add_turn(1)
            self.stop()
            self.add_turn(2)
            self.stop()
        _, second = self.logged()
        self.assertEqual((second["machine"]["cpu_self_s"], second["machine"]["cpu_children_s"]), (0.5, 0.25))

    def test_a_negative_delta_of_the_same_process_is_zero_not_the_accumulated(self):
        fake = FakeRusage(reading(100.0001, 5.0), reading(100.00009, 5.0))
        with mock.patch.object(self.measure, "proc_pid_rusage", fake):
            self.add_turn(1)
            self.stop()
            self.add_turn(2)
            self.stop()
        _, second = self.logged()
        self.assertEqual((second["machine"]["cpu_self_s"], second["machine"]["cpu_children_s"]), (0.0, 0.0))

    def test_without_claude_pid_the_pid_comes_from_the_parent_chain(self):
        os.environ.pop("CLAUDE_PID")
        chain = {100: (90, "/bin/sh"), 90: (80, "/Users/andepb/.local/bin/claude"), 80: (1, "/Applications/X")}
        with mock.patch.object(self.measure.os, "getppid", return_value=100), \
                mock.patch.object(self.measure, "ps_entry", side_effect=lambda pid: chain[pid]), \
                mock.patch.object(self.measure, "proc_pid_rusage", FakeRusage(reading(1.0, 2.0))) as fake:
            self.add_turn(1)
            self.stop()
        (row,) = self.logged()
        self.assertEqual((row["machine"]["pid"], row["machine"]["from"]), (90, "ppid"))
        self.assertEqual(fake.pids, [90])

    def test_the_parent_chain_stops_at_six_levels_and_gives_null(self):
        os.environ.pop("CLAUDE_PID")
        seen = []

        def entry(pid):
            seen.append(pid)
            return pid + 1, "/bin/zsh"
        with mock.patch.object(self.measure.os, "getppid", return_value=100), \
                mock.patch.object(self.measure, "ps_entry", side_effect=entry):
            self.assertEqual(self.measure.claude_pid(), (None, None))
        self.assertEqual(seen, [100, 101, 102, 103, 104, 105])

    def test_a_reading_that_fails_gives_null_and_the_line_is_written(self):
        with mock.patch.object(self.measure, "proc_pid_rusage", FakeRusage(OSError("sem libproc"))):
            self.add_turn(1)
            self.stop()
        (row,) = self.logged()
        self.assertIsNone(row["machine"])
        self.assertEqual((row["event"], row["prompt_head"], row["api_calls"]), ("turn", "pedido 1", 1))
        self.assertNotIn("machine:44452", self.state())
        self.assertFalse(any(r["event"] == "hook_error" for r in self.logged()))

    def test_the_ps_that_fails_gives_null_and_the_line_is_written(self):
        os.environ.pop("CLAUDE_PID")
        with mock.patch.object(self.measure, "ps_entry", side_effect=OSError("sem ps")):
            self.add_turn(1)
            self.stop()
        (row,) = self.logged()
        self.assertIsNone(row["machine"])

    def test_turn_without_api_call_does_not_read_the_machine(self):
        with open(self.path, "w", encoding="utf-8") as f:
            f.write(json.dumps(prompt(0, "/clear")) + "\n")
        fake = FakeRusage()
        with mock.patch.object(self.measure, "proc_pid_rusage", fake):
            self.stop()
        self.assertEqual(fake.pids, [])

    def test_the_subagent_line_does_not_change(self):
        agent = os.path.join(self.tmp.name, "agent-a1.jsonl")
        with open(agent, "w", encoding="utf-8") as f:
            for entry in (prompt(0, "tarefa"), says(10, "r1")):
                f.write(json.dumps(entry) + "\n")
        fake = FakeRusage()
        with mock.patch.object(self.measure, "proc_pid_rusage", fake):
            self.run_hook({"session_id": "s", "agent_type": "implementer", "agent_id": "a1",
                           "agent_transcript_path": agent}, mode="subagent")
        (row,) = self.logged()
        self.assertEqual(row["event"], "subagent")
        for key in ("waits_s", "prompt_head", "machine"):
            self.assertNotIn(key, row)
        self.assertEqual(fake.pids, [])


@unittest.skipUnless(sys.platform == "darwin", "o proc_pid_rusage é do macOS")
class RealRusageTest(HookTest):
    """A leitura de verdade, pelo `ctypes`: confere o struct e a unidade do `mach_timebase_info`."""

    def test_reads_own_cpu_and_rss_and_counts_a_reaped_child(self):
        import subprocess
        before = self.measure.proc_pid_rusage(os.getpid())
        subprocess.run([sys.executable, "-c", "import time\nt=time.process_time()\nwhile time.process_time()-t<0.4: pass"],
                       check=True)
        after = self.measure.proc_pid_rusage(os.getpid())
        self.assertGreater(before["cpu_self_s"], 0)
        self.assertGreater(before["rss_bytes"], 1 * 2 ** 20)
        self.assertGreater(before["footprint_bytes"], 0)
        self.assertGreaterEqual(after["cpu_children_s"] - before["cpu_children_s"], 0.3)
        self.assertLess(after["cpu_children_s"] - before["cpu_children_s"], 5)  # a unidade convertida, e não em ticks
        self.assertEqual(before["start"], after["start"])

    def test_a_pid_that_does_not_exist_raises(self):
        with self.assertRaises(OSError):
            self.measure.proc_pid_rusage(99_999_999)


if __name__ == "__main__":
    unittest.main()
