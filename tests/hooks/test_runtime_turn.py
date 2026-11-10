"""Testes do escritor da linha `turn` do Grok e do Codex.

O log fica no MEASURE_DIR. O registro da máquina é um arquivo que não existe, e nada aqui lê ~/.grok nem ~/.codex.

    python3 -m unittest tests.hooks.test_runtime_turn
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


def load_runtime_turn(directory):
    os.environ["MEASURE_DIR"] = directory
    os.environ.pop("BRZA_WORKSPACE", None)
    sys.modules.pop("measure", None)  # o LOG é resolvido na importação, e cada teste tem o seu diretório
    if HOOKS not in sys.path:
        sys.path.insert(0, HOOKS)
    spec = importlib.util.spec_from_file_location("runtime_turn", os.path.join(HOOKS, "runtime_turn.py"))
    module = importlib.util.module_from_spec(spec)
    sys.modules["runtime_turn"] = module
    spec.loader.exec_module(module)
    return module


def write_json(path, payload):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as stream:
        json.dump(payload, stream)


class HookTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        os.environ["BRZA_REGISTRY"] = os.path.join(self.tmp.name, "config", "workspaces.toml")
        for key in ("GROK_SESSION_ID", "GROK_HOOK_EVENT", "CODEX_SESSION_ID"):
            os.environ.pop(key, None)
        self.rt = load_runtime_turn(self.tmp.name)
        self.rt.measure.now = lambda: "2026-11-09T09:00:00-03:00"
        self.rt.measure.workstream_of = lambda cwd: "studio"

    def tearDown(self):
        os.environ.pop("MEASURE_DIR", None)
        os.environ.pop("BRZA_REGISTRY", None)

    def logged(self):
        if not os.path.exists(self.rt.measure.LOG):
            return []
        with open(self.rt.measure.LOG, encoding="utf-8") as stream:
            return [json.loads(line) for line in stream.read().splitlines() if line]

    def turns(self):
        return [row for row in self.logged() if row.get("event") == "turn"]

    def log_bytes(self):
        with open(self.rt.measure.LOG, "rb") as stream:
            return stream.read()

    def run_hook(self, payload, mode):
        with mock.patch.object(sys, "argv", ["runtime_turn.py", mode]), \
                mock.patch.object(sys, "stdin", io.StringIO(json.dumps(payload))):
            with self.assertRaises(SystemExit) as caught:
                self.rt.main()
        self.assertEqual(caught.exception.code, 0)


def grok_turn(number, input_tokens, cached, write, output, ticks):
    return {"turnNumber": number, "inputTokens": input_tokens, "cachedReadTokens": cached,
            "cacheCreationTokens": write, "outputTokens": output, "costUsdTicks": ticks}


class GrokTurnTest(HookTest):
    def test_parent_turns_are_logged_once_without_the_session_object_or_the_child(self):
        sid = "01parent"
        cwd = os.path.join(self.tmp.name, "work")
        os.makedirs(cwd)
        parent = os.path.join(self.tmp.name, "sessions", "encoded", sid)
        child = os.path.join(parent, "subagents", "kid")
        turns = [grok_turn(1, 1000, 400, 15, 8, 15_000_000_000), grok_turn(2, 250, 50, 0, 4, 25_000_000_000),
                 {"inputTokens": 9, "cachedReadTokens": 1, "outputTokens": 1, "costUsdTicks": 10_000_000_000}]
        child_turn = grok_turn(9, 40, 10, 1, 1, 99_000_000_000)
        session_ticks = 15_000_000_000 + 25_000_000_000 + 99_000_000_000
        write_json(os.path.join(parent, "usage.json"), {
            "sessionId": sid,
            "session": {"inputTokens": 9000, "cachedReadTokens": 8000, "outputTokens": 100, "costUsdTicks": session_ticks},
            "turns": turns,
        })
        write_json(os.path.join(child, "usage.json"), {"sessionId": "kid", "turns": [child_turn]})
        open(os.path.join(parent, "updates.jsonl"), "w").close()
        open(os.path.join(child, "updates.jsonl"), "w").close()
        payload = {"hook_event_name": "Stop", "session_id": sid, "sessionId": sid, "cwd": cwd,
                   "transcript_path": os.path.join(parent, "updates.jsonl")}
        with mock.patch.dict(os.environ, {"GROK_SESSION_ID": sid}):
            self.run_hook(payload, "grok")
            before = self.log_bytes()
            self.run_hook({**payload, "reason": "shutdown"}, "grok")
            self.run_hook({**payload, "transcript_path": os.path.join(child, "updates.jsonl")}, "grok")
            self.run_hook({"hook_event_name": "Stop", "session_id": "kid", "sessionId": "kid", "cwd": cwd,
                           "transcript_path": os.path.join(child, "updates.jsonl")}, "grok")
        self.assertEqual(self.log_bytes(), before)
        rows = self.turns()
        self.assertEqual([row["turn_number"] for row in rows], [1, 2])
        self.assertEqual([row["estimated_cost_usd"] for row in rows], [15_000_000_000 / 1e10, 25_000_000_000 / 1e10])
        self.assertNotEqual(sum(row["estimated_cost_usd"] for row in rows), session_ticks / 1e10)
        self.assertEqual(rows[0]["tokens"]["input"], 1000 - 400)
        self.assertEqual(rows[0]["tokens"]["cache_read"], 400)
        self.assertEqual(rows[0]["tokens"]["cache_write"], 15)
        self.assertEqual(rows[0]["tokens"]["output"], 8)
        self.assertEqual(rows[1]["tokens"], {"input": 250 - 50, "cache_read": 50, "cache_write": 0, "output": 4})
        for row in rows:
            self.assertEqual(row["session_id"], sid)
            self.assertEqual(row["runtime"], "grok")
            self.assertEqual(row["event"], "turn")
            self.assertIsNone(row["machine"])
            self.assertEqual(row["timestamp"], "2026-11-09T09:00:00-03:00")
            self.assertEqual(row["cwd"], cwd)
            self.assertEqual(row["workstream"], "studio")
            self.assertNotIn("fact_audit", row)
            self.assertEqual(set(row["tokens"]), {"input", "cache_read", "cache_write", "output"})
        self.assertFalse(os.path.exists(self.rt.measure.STATE))

    def test_payload_session_id_wins_when_grok_session_id_differs(self):
        sid = "do-payload"
        parent = os.path.join(self.tmp.name, "sessions", "encoded", sid)
        write_json(os.path.join(parent, "usage.json"), {
            "sessionId": sid, "turns": [grok_turn(1, 10, 3, 0, 1, 10_000_000_000)],
        })
        open(os.path.join(parent, "updates.jsonl"), "w").close()
        payload = {"hook_event_name": "Stop", "session_id": sid, "cwd": self.tmp.name,
                   "transcriptPath": os.path.join(parent, "updates.jsonl")}
        with mock.patch.dict(os.environ, {"GROK_SESSION_ID": "do-ambiente"}):
            self.run_hook(payload, "grok")
        row, = self.turns()
        self.assertEqual(row["session_id"], sid)

    def test_session_id_alias_is_used_when_the_snake_case_key_is_absent(self):
        sid = "camel"
        parent = os.path.join(self.tmp.name, "sessions", "encoded", sid)
        write_json(os.path.join(parent, "usage.json"), {
            "sessionId": sid, "turns": [grok_turn(1, 8, 2, 1, 1, 20_000_000_000)],
        })
        open(os.path.join(parent, "updates.jsonl"), "w").close()
        with mock.patch.dict(os.environ, {"GROK_SESSION_ID": "outro"}):
            self.run_hook({"hook_event_name": "Stop", "sessionId": sid, "cwd": self.tmp.name,
                           "transcript_path": os.path.join(parent, "updates.jsonl")}, "grok")
        row, = self.turns()
        self.assertEqual(row["session_id"], sid)
        self.assertEqual(row["estimated_cost_usd"], 20_000_000_000 / 1e10)

    def test_subagent_stop_writes_nothing(self):
        sid = "filho"
        child = os.path.join(self.tmp.name, "sessions", "encoded", "pai", "subagents", sid)
        write_json(os.path.join(child, "usage.json"), {
            "sessionId": sid, "turns": [grok_turn(1, 10, 0, 0, 1, 10_000_000_000)],
        })
        transcript = os.path.join(child, "updates.jsonl")
        open(transcript, "w").close()
        payload = {"hook_event_name": "SubagentStop", "session_id": sid, "cwd": self.tmp.name,
                   "transcript_path": transcript}
        self.run_hook(payload, "grok")
        with mock.patch.dict(os.environ, {"GROK_HOOK_EVENT": "SubagentStop"}):
            self.run_hook({**payload, "hook_event_name": "Stop"}, "grok")
        self.assertEqual(self.turns(), [])
        self.assertFalse(os.path.exists(self.rt.measure.STATE))

    def test_without_session_id_writes_nothing_even_with_the_env_var(self):
        sid = "env-only"
        parent = os.path.join(self.tmp.name, "sessions", "encoded", sid)
        write_json(os.path.join(parent, "usage.json"), {
            "sessionId": sid, "turns": [grok_turn(1, 10, 0, 0, 1, 10_000_000_000)],
        })
        open(os.path.join(parent, "updates.jsonl"), "w").close()
        with mock.patch.dict(os.environ, {"GROK_SESSION_ID": sid}):
            self.run_hook({"hook_event_name": "Stop", "cwd": self.tmp.name,
                           "transcript_path": os.path.join(parent, "updates.jsonl")}, "grok")
        self.assertEqual(self.logged(), [])

    def test_a_usage_file_from_another_session_is_not_logged(self):
        parent = os.path.join(self.tmp.name, "sessions", "encoded", "pai")
        write_json(os.path.join(parent, "usage.json"), {
            "sessionId": "pai", "turns": [grok_turn(1, 10, 0, 0, 1, 10_000_000_000)],
        })
        open(os.path.join(parent, "updates.jsonl"), "w").close()
        self.run_hook({"hook_event_name": "Stop", "session_id": "outro", "cwd": self.tmp.name,
                       "transcript_path": os.path.join(parent, "updates.jsonl")}, "grok")
        self.assertEqual(self.turns(), [])

    def test_workstream_failure_is_null(self):
        sid = "ws"
        parent = os.path.join(self.tmp.name, "sessions", "encoded", sid)
        write_json(os.path.join(parent, "usage.json"), {
            "sessionId": sid, "turns": [grok_turn(1, 4, 1, 0, 1, 10_000_000_000)],
        })
        open(os.path.join(parent, "updates.jsonl"), "w").close()

        def boom(cwd):
            raise RuntimeError("git")

        self.rt.measure.workstream_of = boom
        self.run_hook({"hook_event_name": "Stop", "session_id": sid, "cwd": self.tmp.name,
                       "transcript_path": os.path.join(parent, "updates.jsonl")}, "grok")
        self.assertIsNone(self.turns()[0]["workstream"])

    def test_invalid_stdin_exits_zero_and_writes_no_turn(self):
        with mock.patch.object(sys, "argv", ["runtime_turn.py", "grok"]), \
                mock.patch.object(sys, "stdin", io.StringIO("{")):
            with self.assertRaises(SystemExit) as caught:
                self.rt.main()
        self.assertEqual(caught.exception.code, 0)
        rows = self.logged()
        self.assertFalse(any(row.get("event") == "turn" for row in rows))
        self.assertEqual(rows[0]["event"], "hook_error")
        self.assertEqual(rows[0]["mode"], "grok")
        self.assertIn("error", rows[0])
        self.assertNotIn("payload", rows[0])


class GrokUsageFileTest(HookTest):
    def test_read_drops_the_session_object_and_a_turn_without_number(self):
        path = os.path.join(self.tmp.name, "usage.json")
        write_json(path, {"sessionId": "s", "session": {"costUsdTicks": 99_000_000_000, "inputTokens": 5},
                          "turns": [grok_turn(1, 10, 4, 2, 3, 30_000_000_000),
                                    {"inputTokens": 1, "costUsdTicks": 10_000_000_000},
                                    {"turnNumber": 2, "inputTokens": 8, "outputTokens": 1}]})
        parsed = self.rt.read_grok_usage(path)
        self.assertEqual(set(parsed), {"session_id", "turns"})
        self.assertEqual(parsed["session_id"], "s")
        self.assertEqual(parsed["turns"], [
            {"turn_number": 1, "input": 6, "cache_read": 4, "cache_write": 2, "output": 3, "cost_usd": 3.0},
            {"turn_number": 2, "input": 8, "cache_read": 0, "cache_write": 0, "output": 1, "cost_usd": None},
        ])

    def test_find_stays_one_level_down_and_picks_the_file_whose_session_id_matches(self):
        root = os.path.join(self.tmp.name, "sessions")
        sid = "sid"
        real = os.path.join(root, "zzz", sid, "usage.json")
        decoy = os.path.join(root, "aaa", sid, "usage.json")
        nested = os.path.join(root, "aaa", sid, "subagents", sid, "usage.json")
        shallow = os.path.join(root, sid, "usage.json")
        write_json(real, {"sessionId": sid, "turns": [grok_turn(1, 10, 0, 0, 1, 10_000_000_000)]})
        write_json(decoy, {"sessionId": "outra", "turns": [grok_turn(1, 10, 0, 0, 1, 50_000_000_000_000)]})
        write_json(nested, {"sessionId": sid, "turns": [grok_turn(1, 10, 0, 0, 1, 80_000_000_000_000)]})
        write_json(shallow, {"sessionId": sid, "turns": [grok_turn(1, 10, 0, 0, 1, 70_000_000_000_000)]})
        found = self.rt.find_grok_usage(sid, root)
        self.assertEqual(os.path.realpath(found), os.path.realpath(real))
        self.assertEqual(self.rt.read_grok_usage(found)["turns"][0]["cost_usd"], 1.0)
        self.assertIsNone(self.rt.find_grok_usage(sid, os.path.join(root, "ausente")))
        self.assertIsNone(self.rt.find_grok_usage("../sid", root))


def codex_record(response_id, usage):
    return {"type": "token_usage_record", "payload": {
        "response_id": response_id, "usage": usage,
        "turn_token_usage": {"input_tokens": 99999, "cached_input_tokens": 1, "cache_write_input_tokens": 1,
                             "output_tokens": 1, "reasoning_output_tokens": 1, "total_tokens": 9},
        "thread_token_usage": {"input_tokens": 88888, "cached_input_tokens": 2, "output_tokens": 2,
                               "reasoning_output_tokens": 2, "total_tokens": 8},
    }}


def write_rollout(path, rows):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as stream:
        for row in rows:
            stream.write(row if isinstance(row, str) else json.dumps(row))
            stream.write("\n")


class CodexTurnTest(HookTest):
    def usage(self, input_tokens, cached, write, output):
        return {"input_tokens": input_tokens, "cached_input_tokens": cached, "cache_write_input_tokens": write,
                "output_tokens": output, "reasoning_output_tokens": 3, "total_tokens": 200}

    def test_two_usage_deltas_are_logged_once_and_the_second_stop_does_not_duplicate(self):
        path = os.path.join(self.tmp.name, "rollout.jsonl")
        first = self.usage(100, 40, 5, 7)
        second = self.usage(30, 10, 0, 2)
        write_rollout(path, [
            {"type": "session_meta", "payload": {"thread_source": "user", "id": "thread"}},
            "não é json",
            codex_record("resp-1", first),
            codex_record("resp-2", second),
        ])
        payload = {"hook_event_name": "Stop", "session_id": "codex-sid", "cwd": self.tmp.name,
                   "transcript_path": path}
        self.run_hook(payload, "codex")
        before = self.log_bytes()
        self.run_hook({**payload, "reason": "shutdown"}, "codex")
        self.assertEqual(self.log_bytes(), before)
        rows = self.turns()
        self.assertEqual([row["response_id"] for row in rows], ["resp-1", "resp-2"])
        self.assertEqual([row["turn_number"] for row in rows], ["resp-1", "resp-2"])
        self.assertEqual(rows[0]["tokens"], {"input": 100, "cache_read": 40, "cache_write": 5, "output": 7})
        self.assertEqual(rows[1]["tokens"]["input"], 30)
        self.assertNotEqual(rows[0]["tokens"]["input"], 100 - 40)
        for row in rows:
            self.assertEqual(row["runtime"], "codex")
            self.assertEqual(row["session_id"], "codex-sid")
            self.assertIsNone(row["estimated_cost_usd"])
            self.assertIsNone(row["machine"])
            self.assertEqual(row["workstream"], "studio")
            self.assertNotIn("fact_audit", row)
            self.assertNotIn("reasoning_output_tokens", row)
            self.assertNotIn("total_tokens", row)
            self.assertNotIn("reasoning_output_tokens", row["tokens"])
            self.assertNotIn("total_tokens", row["tokens"])
        self.assertFalse(os.path.exists(self.rt.measure.STATE))

    def test_guardian_review_writes_nothing(self):
        path = os.path.join(self.tmp.name, "rollout.jsonl")
        write_rollout(path, [
            {"type": "session_meta", "payload": {"thread_source": "guardian_review"}},
            codex_record("resp-1", self.usage(100, 40, 5, 7)),
        ])
        self.run_hook({"hook_event_name": "Stop", "session_id": "codex-sid", "cwd": self.tmp.name,
                       "transcript_path": path}, "codex")
        self.assertEqual(self.turns(), [])

    def test_subagent_stop_writes_nothing(self):
        path = os.path.join(self.tmp.name, "rollout.jsonl")
        write_rollout(path, [codex_record("resp-1", self.usage(100, 40, 5, 7))])
        self.run_hook({"hook_event_name": "SubagentStop", "session_id": "codex-sid", "cwd": self.tmp.name,
                       "transcript_path": path}, "codex")
        self.assertEqual(self.logged(), [])

    def test_without_session_id_writes_nothing(self):
        path = os.path.join(self.tmp.name, "rollout.jsonl")
        write_rollout(path, [codex_record("resp-1", self.usage(100, 40, 5, 7))])
        self.run_hook({"hook_event_name": "Stop", "cwd": self.tmp.name, "transcript_path": path}, "codex")
        self.assertEqual(self.logged(), [])

    def test_without_transcript_path_writes_nothing(self):
        self.run_hook({"hook_event_name": "Stop", "session_id": "codex-sid", "cwd": self.tmp.name}, "codex")
        self.assertEqual(self.logged(), [])


if __name__ == "__main__":
    unittest.main()
