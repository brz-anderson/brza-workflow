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


if __name__ == "__main__":
    unittest.main()
