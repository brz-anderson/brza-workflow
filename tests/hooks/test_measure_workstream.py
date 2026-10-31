"""Testes do `workstream` em toda linha, do evento `memory` e das `rounds` do turno, com repositórios git temporários.

    python3 -m unittest discover -s .claude/hooks/tests
"""

import json
import os
import subprocess
import unittest

from test_measure import HERE, HookTest, lines_of

FIXTURES = os.path.join(HERE, "fixtures")
IDENTITY = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}


def git(cwd, *args):
    done = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, env={**os.environ, **IDENTITY})
    assert done.returncode == 0, done.stderr
    return done.stdout


def commit_file(repo, relative, text="x\n"):
    path = os.path.join(repo, relative)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)
    git(repo, "add", relative)
    git(repo, "commit", "-m", f"add {relative}")


def write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


def note(name, kind, body="corpo"):
    return f"---\nname: {name}\ndescription: d\nmetadata:\n  type: {kind}\n---\n\n{body}\n"


class RepoTest(HookTest):
    """O workspace (master + worktrees workstream/studio e workstream/tools-kit) e o studio, em /tmp."""

    def setUp(self):
        super().setUp()
        base = os.path.realpath(self.tmp.name)
        self.workspace, self.studio = os.path.join(base, "workspace"), os.path.join(base, "studio")
        for repo in (self.workspace, self.studio):
            os.makedirs(repo)
            git(repo, "init", "-q", "-b", "master")
            commit_file(repo, "README.md")
        git(self.workspace, "branch", "workstream/studio")
        git(self.workspace, "branch", "workstream/tools-kit")
        self.trees = {}
        for name in ("studio", "tools-kit"):
            self.trees[name] = os.path.join(self.workspace, ".worktrees", name)
            git(self.workspace, "worktree", "add", "-q", self.trees[name], f"workstream/{name}")
        commit_file(self.trees["studio"], "plans/PLN-007-chat/README.md")
        self.measure.WORKSPACE, self.measure.STUDIO = self.workspace, self.studio
        self.measure._WORKSTREAMS.clear()

    def studio_tree(self, branch):
        path = os.path.join(os.path.dirname(self.studio), "studio-" + branch.replace("/", "-"))
        git(self.studio, "worktree", "add", "-q", "-b", branch, path)
        return path

    def memory_events(self):
        if not os.path.exists(self.measure.LOG):
            return []
        return [r for r in self.logged() if r["event"] == "memory"]

    def stop(self, cwd, **extra):
        self.run_hook({"session_id": "s", "cwd": cwd, **extra}, mode="turn")


class WorkstreamTest(RepoTest):
    def test_workspace_worktree_gives_the_branch_name_even_from_a_subdirectory(self):
        os.makedirs(os.path.join(self.trees["tools-kit"], "work", "notes"))
        self.assertEqual(self.measure.workstream_of(self.trees["tools-kit"]), "tools-kit")
        self.assertEqual(self.measure.workstream_of(os.path.join(self.trees["tools-kit"], "work", "notes")), "tools-kit")

    def test_main_checkout_on_master_is_master(self):
        self.assertEqual(self.measure.workstream_of(self.workspace), "master")

    def test_workspace_branch_that_is_not_a_workstream_has_none(self):
        git(self.workspace, "checkout", "-q", "-b", "scratch")
        self.assertIsNone(self.measure.workstream_of(self.workspace))

    def test_studio_branch_with_the_plan_number_gives_the_workstream_of_the_plan(self):
        tree = self.studio_tree("feat/pln-007-chat-preview")
        self.assertEqual(self.measure.workstream_of(tree), "studio")
        self.assertEqual(self.measure.resolve_workstream(tree)[1], self.trees["studio"])

    def test_studio_plan_is_found_on_origin_only_when_no_local_branch_has_it(self):
        commit_file(self.trees["tools-kit"], "plans/PLN-009-kit/README.md")
        git(self.workspace, "update-ref", "refs/remotes/origin/workstream/tools-kit", "workstream/tools-kit")
        git(self.workspace, "update-ref", "refs/remotes/origin/workstream/ghost", "workstream/tools-kit")
        self.assertEqual(self.measure.workstream_of_plan("009"), "tools-kit")  # local primeiro
        git(self.workspace, "worktree", "remove", "--force", self.trees["tools-kit"])
        git(self.workspace, "branch", "-q", "-D", "workstream/tools-kit")
        self.assertIsNone(self.measure.workstream_of_plan("009"))  # dois em origin: ambíguo, como o brza-note recusa
        git(self.workspace, "update-ref", "-d", "refs/remotes/origin/workstream/ghost")
        self.assertEqual(self.measure.workstream_of_plan("009"), "tools-kit")

    def test_studio_without_plan_in_branch_or_with_unknown_plan_has_none(self):
        self.assertIsNone(self.measure.workstream_of(self.studio))  # master do studio
        self.assertIsNone(self.measure.workstream_of(self.studio_tree("fix/pln-999-nada")))  # plano sem workstream

    def test_other_places_have_none_and_git_failure_does_not_break(self):
        other = os.path.join(os.path.realpath(self.tmp.name), "other")
        os.makedirs(other)
        git(other, "init", "-q")
        self.assertIsNone(self.measure.workstream_of(other))
        self.assertIsNone(self.measure.workstream_of(os.path.join(other, "ausente")))
        self.assertIsNone(self.measure.workstream_of(None))
        self.measure.git = lambda *a, **k: None
        self.measure._WORKSTREAMS.clear()
        self.assertIsNone(self.measure.workstream_of(self.trees["studio"]))

    def test_result_is_cached_by_cwd(self):
        calls = []
        real = self.measure.git
        self.measure.git = lambda *a, **k: calls.append(a) or real(*a, **k)
        self.measure.workstream_of(self.trees["studio"])
        n = len(calls)
        self.measure.workstream_of(self.trees["studio"])
        self.assertEqual(len(calls), n)

    def test_every_event_line_carries_the_workstream(self):
        cwd, session = self.trees["studio"], os.path.join(self.tmp.name, "t.jsonl")
        write(session, json.dumps({"type": "assistant", "requestId": "r", "message": {
            "model": "claude-opus-5-5", "content": [], "usage": {"input_tokens": 1}}}) + "\n")
        base = {"session_id": "s", "cwd": cwd}
        self.run_hook({**base, "transcript_path": session}, mode="turn")
        self.run_hook({**base, "tool_input": {"skill": "x"}}, mode="skill")
        self.run_hook({**base, "command_name": "x"}, mode="command")
        self.run_hook({**base, "error": "rate_limit"}, mode="failure")
        self.run_hook({**base, "tool_name": "mcp__a__b"}, mode="mcp")
        self.run_hook({**base, "agent_type": "collector", "agent_id": "a1"}, mode="subagent")  # sem transcript: linha de erro
        rows = self.logged()
        self.assertEqual({r["event"] for r in rows}, {"turn", "skill", "command", "failure", "mcp", "subagent"})
        self.assertEqual({r["workstream"] for r in rows}, {"studio"})
        self.run_hook({"session_id": "s", "tool_name": "mcp__a__b"}, mode="mcp")  # sem cwd
        self.assertIsNone(self.logged()[-1]["workstream"])


class MemoryTest(RepoTest):
    def setUp(self):
        super().setUp()
        self.tree = self.trees["tools-kit"]
        self.session_dir = os.path.join(self.tree, "memory")
        self.role_dir = os.path.join(self.tree, ".claude", "agent-memory", "reviewer")

    def test_frontmatter_reads_metadata_type_or_loose_type(self):
        self.assertEqual(self.measure.frontmatter(note("a", "feedback")), {"name": "a", "type": "feedback"})
        self.assertEqual(self.measure.frontmatter("---\nname: b\ntype: 'user'\n---\nx"), {"name": "b", "type": "user"})
        self.assertEqual(self.measure.frontmatter("sem frontmatter"), {})

    def test_first_sight_only_records_the_snapshot(self):
        write(os.path.join(self.session_dir, "MEMORY.md"), "- [a](a.md)\n")
        write(os.path.join(self.session_dir, "a.md"), note("a", "user"))
        self.stop(self.tree)
        self.assertEqual(self.memory_events(), [])
        with open(self.measure.SNAPSHOT, encoding="utf-8") as f:
            snapshot = json.load(f)
        files = snapshot["worktrees"][self.tree][self.session_dir]
        self.assertEqual(set(files), {os.path.join(self.session_dir, "MEMORY.md"), os.path.join(self.session_dir, "a.md")})
        self.assertEqual(len(files[os.path.join(self.session_dir, "a.md")]["sha1"]), 40)
        self.stop(self.tree)  # nada mudou
        self.assertEqual(self.memory_events(), [])

    def test_created_updated_and_deleted_per_owner(self):
        self.stop(self.tree)  # o worktree entra no retrato, sem memória
        write(os.path.join(self.session_dir, "MEMORY.md"), "- [a](a.md)\n")
        write(os.path.join(self.session_dir, "a.md"), note("a", "feedback"))
        write(os.path.join(self.role_dir, "b.md"), note("b", "project"))
        self.stop(self.tree, session_id="s1")
        created = {(e["owner"], e["file"]): e for e in self.memory_events()}
        self.assertEqual(set(created), {("session", "memory/MEMORY.md"), ("session", "memory/a.md"),
                                        ("reviewer", ".claude/agent-memory/reviewer/b.md")})
        self.assertEqual({e["action"] for e in created.values()}, {"created"})
        a = created[("session", "memory/a.md")]
        self.assertEqual((a["name"], a["type"], a["workstream"], a["session_id"]), ("a", "feedback", "tools-kit", "s1"))
        self.assertEqual(created[("session", "memory/MEMORY.md")]["type"], "index")

        write(os.path.join(self.session_dir, "a.md"), note("a", "feedback", "corpo novo"))
        os.remove(os.path.join(self.role_dir, "b.md"))
        before = len(self.memory_events())
        self.run_hook({"session_id": "s1", "cwd": self.tree, "agent_id": "ag1", "agent_type": "reviewer"}, mode="subagent")
        new = self.memory_events()[before:]
        self.assertEqual({(e["action"], e["owner"], e["file"].rsplit("/", 1)[1], e["type"]) for e in new},
                         {("updated", "session", "a.md", "feedback"), ("deleted", "reviewer", "b.md", "project")})
        self.assertEqual({e["agent_id"] for e in new}, {"ag1"})
        self.stop(self.tree)
        self.assertEqual(len(self.memory_events()), before + 2)  # sem mudança, sem evento

    def test_role_directory_that_appears_after_the_baseline_counts_as_created(self):
        write(os.path.join(self.session_dir, "a.md"), note("a", "user"))
        self.stop(self.tree)
        write(os.path.join(self.role_dir, "novo.md"), note("novo", "reference"))
        self.stop(self.tree)
        (event,) = self.memory_events()
        self.assertEqual((event["action"], event["owner"], event["type"]), ("created", "reviewer", "reference"))

    def test_deleted_directory_deletes_its_files(self):
        write(os.path.join(self.role_dir, "b.md"), note("b", "project"))
        self.stop(self.tree)
        os.remove(os.path.join(self.role_dir, "b.md"))
        os.rmdir(self.role_dir)
        self.stop(self.tree)
        (event,) = self.memory_events()
        self.assertEqual((event["action"], event["owner"]), ("deleted", "reviewer"))

    def test_studio_session_uses_the_worktree_of_the_plan_workstream(self):
        tree = self.studio_tree("feat/pln-007-chat")
        self.stop(tree)
        write(os.path.join(self.trees["studio"], "memory", "c.md"), note("c", "project"))
        self.stop(tree)
        (event,) = self.memory_events()
        self.assertEqual((event["workstream"], event["owner"], event["file"]), ("studio", "session", "memory/c.md"))

    def test_no_memory_for_master_unknown_cwd_or_studio_without_worktree(self):
        write(os.path.join(self.workspace, "memory", "m.md"), note("m", "user"))
        self.stop(self.workspace)
        write(os.path.join(self.workspace, "memory", "m2.md"), note("m2", "user"))
        self.stop(self.workspace)
        tree = self.studio_tree("feat/pln-007-chat")
        git(self.workspace, "worktree", "remove", "--force", self.trees["studio"])
        self.stop(tree)
        self.assertFalse(os.path.exists(self.measure.SNAPSHOT))
        self.assertEqual(self.memory_events(), [])

    def test_git_failure_does_not_break_the_hook(self):
        self.measure.git = lambda *a, **k: None
        self.stop(self.tree)
        self.assertFalse(os.path.exists(self.measure.SNAPSHOT))  # sem workstream, sem retrato e sem hook_error
        self.assertEqual([r for r in self.logged() if r["event"] == "hook_error"] if os.path.exists(self.measure.LOG) else [], [])


class ReadFromTest(HookTest):
    def test_line_with_a_loose_unicode_separator_stays_whole(self):
        path = os.path.join(self.tmp.name, "t.jsonl")
        with open(path, "w", encoding="utf-8") as f:
            f.write(json.dumps({"a": "x\u2028y\u0085z"}, ensure_ascii=False) + "\n" + json.dumps({"b": 1}) + "\n{meia")
        lines, end = self.measure.read_from(path, 0)
        self.assertEqual([json.loads(line) for line in lines], [{"a": "x\u2028y\u0085z"}, {"b": 1}])
        self.assertEqual(end, os.path.getsize(path) - len("{meia"))


class RoundsTest(HookTest):
    def fixture(self):
        return lines_of(os.path.join(FIXTURES, "ask_user_question.jsonl"))

    def test_round_counts_from_the_real_shapes(self):
        measured, seen = self.measure.measure(self.fixture())
        # 9 perguntas respondidas (a recusada não conta): 3 recomendadas, 3 outra opção, 3 texto livre
        self.assertEqual(seen["rounds"], {"questions": 9, "recommended": 3, "other_option": 3, "free_text": 3})

    def test_structured_field_wins_and_text_is_the_fallback(self):
        q = {"questions": [{"question": "P?", "multiSelect": False, "options": [{"label": "A (Recomendado)"}, {"label": "B"}]}]}
        use = {"type": "assistant", "requestId": "r", "message": {"model": "m", "content": [
            {"type": "tool_use", "id": "t1", "name": "AskUserQuestion", "input": q}], "usage": {}}}
        text = 'The user answered: "P?"="B". Read the answers carefully.'
        both = {"type": "user", "toolUseResult": {"answers": {"P?": "A (Recomendado)"}},
                "message": {"content": [{"type": "tool_result", "tool_use_id": "t1", "content": text}]}}
        only_text = {k: v for k, v in both.items() if k != "toolUseResult"}
        self.assertEqual(self.measure.measure([json.dumps(use), json.dumps(both)])[1]["rounds"]["recommended"], 1)
        self.assertEqual(self.measure.measure([json.dumps(use), json.dumps(only_text)])[1]["rounds"]["other_option"], 1)

    def test_classify_multi_select(self):
        labels = {"A (Recomendado)", "B (Recomendado)", "C", "D, com vírgula"}
        c = self.measure.classify_answer
        self.assertEqual(c("A (Recomendado),B (Recomendado)", labels, True), "recommended")
        self.assertEqual(c("A (Recomendado),C", labels, True), "other_option")
        self.assertEqual(c("D, com vírgula", labels, True), "other_option")
        self.assertEqual(c("A (Recomendado),D, com vírgula", labels, True), "other_option")
        self.assertEqual(c("A (Recomendado),algo meu", labels, True), "free_text")
        self.assertEqual(c(["C", "texto"], labels, True), "free_text")
        self.assertEqual(c("A, B", labels, False), "free_text")  # seleção única só casa o rótulo inteiro
        self.assertIsNone(c("", labels, True))
        self.assertIsNone(c(None, labels, False))

    def test_turn_writes_rounds_even_with_none(self):
        path = os.path.join(self.tmp.name, "t.jsonl")
        with open(path, "w", encoding="utf-8") as f:
            f.write("\n".join(self.fixture()) + "\n")
        self.run_hook({"session_id": "s", "transcript_path": path}, mode="turn")
        (row,) = self.logged()
        self.assertEqual(row["rounds"]["questions"], 9)
        empty = os.path.join(self.tmp.name, "e.jsonl")
        with open(empty, "w", encoding="utf-8") as f:
            f.write(json.dumps({"type": "assistant", "requestId": "r", "message": {"model": "m", "content": [], "usage": {}}}) + "\n")
        self.run_hook({"session_id": "s2", "transcript_path": empty}, mode="turn")
        self.assertEqual(self.logged()[-1]["rounds"], {"questions": 0, "recommended": 0, "other_option": 0, "free_text": 0})


if __name__ == "__main__":
    unittest.main()
